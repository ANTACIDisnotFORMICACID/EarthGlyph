#!/usr/bin/env python
"""CLI: download a real Overture Maps extract for a bbox (P0-1).

Examples
--------
python scripts/download_data.py --bbox=-90,44,-89,45 \
    --output data/raw/wisconsin_water.parquet

python scripts/download_data.py --bbox=-90,44,-89,45 \
    --output data/raw/wisconsin_water.parquet \
    --release 2026-10-21.0 --force

Re-running with the same bbox reuses an existing valid file (idempotent);
pass --force to re-download. A sidecar <output>.meta.json records provenance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.download import DownloadError, download_theme, parse_bbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        required=True,
        help="min_lon,min_lat,max_lon,max_lat (e.g. -90,44,-89,45)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="output GeoParquet path under data/raw/",
    )
    parser.add_argument(
        "--release",
        default=None,
        help="Overture release (e.g. 2026-10-21.0); default: latest",
    )
    parser.add_argument(
        "--theme",
        default="water",
        help="base theme to download (P0 uses water only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if the output file already exists and is valid",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="download timeout in seconds",
    )
    parser.add_argument(
        "--fetch-method",
        choices=("duckdb", "cli"),
        default="duckdb",
        help="duckdb (default): STAC index + HTTPS row-group range reads. "
             "cli: official overturemaps download command.",
    )
    args = parser.parse_args(argv)

    try:
        bbox = parse_bbox(args.bbox)
    except ValueError as exc:
        print(f"invalid --bbox: {exc}", file=sys.stderr)
        return 2

    try:
        meta = download_theme(
            bbox,
            args.output,
            theme=args.theme,
            release=args.release,
            force=args.force,
            timeout_s=args.timeout,
            fetch_method=args.fetch_method,
        )
    except (DownloadError, ValueError, NotImplementedError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK  release={meta['release']}  rows={meta['file']['rows']}")
    print(f"    file={meta['file']['path']} ({meta['file']['size_bytes']} bytes)")
    print(f"    sha256={meta['file']['sha256']}")
    print(f"    fetch_method={meta['fetch_method']}")
    print(f"    sidecar={args.output}.meta.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
