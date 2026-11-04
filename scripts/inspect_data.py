#!/usr/bin/env python
"""CLI: inspect a GeoParquet extract and write a JSON quality summary (P0-1).

Example
-------
python scripts/inspect_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/dataset_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.inspect import summarize_geoparquet, write_summary_json


def _print_human(summary: dict) -> None:
    g = summary["geometry"]
    print(f"file: {summary['file']['path']} ({summary['file']['size_bytes']} bytes)")
    print(f"rows: {summary['rows']}  crs: {'EPSG-ish' if summary['crs'] else 'MISSING'}")
    print(f"columns ({len(summary['columns'])}): {', '.join(summary['columns'])}")
    print(f"geometry types: {g['type_counts']}")
    print(
        f"null geoms: {g['null_geometries']} | empty: {g['empty_geometries']} "
        f"| invalid: {g['invalid_geometries']}"
    )
    if g["invalid_examples"]:
        print(f"invalid examples: {g['invalid_examples'][:5]}")
    print(f"duplicate ids: {summary['duplicate_ids']}")
    print(f"usable closed contours: {summary['usable_closed_contours']}")
    print("subtype distribution:", summary["subtype_distribution"])
    print("class distribution:", summary["class_distribution"])
    a = summary["areas_m2_of_usable"]
    if a.get("count"):
        print(
            f"usable areas m2: min={a['min']:.0f} p5={a['p5']:.0f} median={a['median']:.0f}"
            f" p95={a['p95']:.0f} max={a['max']:.0f}"
        )
        print(f"size buckets: {a['size_buckets']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="GeoParquet file to inspect")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write JSON summary here (also printed to stdout)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    summary = summarize_geoparquet(args.input)
    _print_human(summary)
    if args.output:
        write_summary_json(summary, args.output)
        print(f"\nsummary written: {args.output}")
    else:
        print("\n" + json.dumps(summary, indent=2, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
