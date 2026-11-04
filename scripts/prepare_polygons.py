#!/usr/bin/env python
"""CLI: build the standardized contour dataset from a raw GeoParquet (P0-2).

Examples
--------
python scripts/prepare_polygons.py --input data/raw/wisconsin_water.parquet \
    --out-dir data/processed --force

python scripts/prepare_polygons.py --min-area-ha 0.5 --subtypes lake,reservoir \
    --out-dir data/processed_experiment --force

The raw extract is read-only. Outputs are ``contours.parquet``,
``rejected_samples.parquet`` and ``data_manifest.json`` under --out-dir; an
existing dataset is only overwritten with --force.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.prepare import prepare_contours
from earthglyph.geometry import DEFAULT_N_POINTS, DEFAULT_SUBTYPES, FilterConfig


def parse_subtypes(text: str) -> tuple[str, ...] | None:
    """``all`` (or empty) disables the allowlist; otherwise a comma list."""
    cleaned = text.strip()
    if cleaned.lower() in {"all", "*", ""}:
        return None
    return tuple(part.strip() for part in cleaned.split(",") if part.strip())


def build_config(args: argparse.Namespace) -> FilterConfig:
    return FilterConfig(
        subtypes=parse_subtypes(args.subtypes),
        min_area_m2=args.min_area_ha * 1e4,
        max_iou=args.max_iou,
        dedupe=not args.no_dedupe,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/wisconsin_water.parquet"),
        help="raw GeoParquet extract (read-only)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/processed"),
        help="directory for contours.parquet / data_manifest.json",
    )
    parser.add_argument(
        "--points",
        type=int,
        default=DEFAULT_N_POINTS,
        help=f"contour sample count (default {DEFAULT_N_POINTS})",
    )
    parser.add_argument(
        "--min-area-ha",
        type=float,
        default=0.1,
        help="minimum projected area in hectares (experiment parameter)",
    )
    parser.add_argument(
        "--subtypes",
        default=",".join(DEFAULT_SUBTYPES),
        help="comma-separated subtype allowlist, or 'all' to keep every subtype",
    )
    parser.add_argument(
        "--max-iou",
        type=float,
        default=0.95,
        help="IoU above which two candidates count as duplicates",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="keep highly overlapping candidates instead of dropping them",
    )
    parser.add_argument(
        "--rejected-sample-limit",
        type=int,
        default=200,
        help="rejected shapes saved per reason for visual inspection",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing dataset in --out-dir",
    )
    args = parser.parse_args(argv)

    if args.points < 3:
        print("--points must be at least 3", file=sys.stderr)
        return 2
    if args.min_area_ha < 0:
        print("--min-area-ha must be >= 0", file=sys.stderr)
        return 2
    if not 0.0 < args.max_iou <= 1.0:
        print("--max-iou must be within (0, 1]", file=sys.stderr)
        return 2

    command = "python " + " ".join(shlex.quote(a) for a in [sys.argv[0], *(argv or sys.argv[1:])])
    try:
        result = prepare_contours(
            args.input,
            args.out_dir,
            n_points=args.points,
            config=build_config(args),
            rejected_sample_limit=args.rejected_sample_limit,
            force=args.force,
            build_command=command,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"prepare failed: {exc}", file=sys.stderr)
        return 1

    manifest = result.manifest
    counts = manifest["counts"]
    print(f"OK  input={manifest['source']['input_path']}")
    print(f"    release={manifest['source']['release']}  raw_rows={counts['raw_rows']}")
    print(f"    candidates={counts['candidates']}  "
          f"features={counts['features_with_candidates']}")
    print("    rejected features by reason:")
    for reason, n in counts["rejected_features_by_reason"].items():
        print(f"      {reason}: {n}")
    print("    rejected parts by reason:")
    for reason, n in counts["rejected_parts_by_reason"].items():
        print(f"      {reason}: {n}")
    if not counts["rejected_parts_by_reason"]:
        print("      (none)")
    output = manifest.get("output")
    if output:
        print(f"    contours={output['contours']['path']} "
              f"({output['contours']['size_bytes']} bytes)")
        print(f"    sha256={output['contours']['sha256']}")
        print(f"    manifest={manifest['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
