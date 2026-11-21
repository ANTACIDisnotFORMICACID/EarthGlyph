#!/usr/bin/env python
"""CLI: export the real feature index as one self-contained static JSON file
for browser-side retrieval experiments (Phase D / P0-7 feasibility only —
this is NOT product development and NOT a public deployment).

The export is the existing production index verbatim, re-packed:

* ``features``    Float32 (N, 20) feature matrix, row-aligned with ``rows``
* ``shapes``      Float32 (N, 128, 2) standardized contour points
* ``rows``        per-candidate: row index, candidate_id, subtype, lon, lat
* ``provenance``  the index manifest's own traceability: source dataset and
                  release, bbox, candidate counts, feature_columns,
                  n_points/n_harmonics/fold, output sha256 of the index
                  artifacts this export was taken from, index git_commit

Every number comes from the real index — nothing is synthesized. If the
index directory is missing, the script refuses rather than fabricating.

The JSON is written with a stable key order and indent=1 so the file itself
is diff-friendly; its sha256 is printed for the record.

Example
-------
python scripts/export_static_index.py --index data/index \
    --out web/public/static_index.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.index import load_index  # noqa: E402

EXPORT_SCHEMA_VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest_path = args.index / "data_manifest.json"
    if not manifest_path.is_file():
        print(f"ERROR: {manifest_path} not found — a real index is required; "
              "this tool does not fabricate data", file=sys.stderr)
        return 1
    features, shapes, metadata = load_index(args.index)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    for _, record in metadata.iterrows():
        rows.append({
            "row": int(record["feature_row"]),
            "candidate_id": str(record["candidate_id"]),
            "subtype": str(record["subtype"]),
            "lon": float(record["lon"]),
            "lat": float(record["lat"]),
        })
    if len(rows) != features.shape[0] or features.shape[0] != shapes.shape[0]:
        print("ERROR: metadata / features / shapes row counts disagree",
              file=sys.stderr)
        return 1

    payload = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_by": "scripts/export_static_index.py",
        "n_candidates": len(rows),
        "n_points": int(shapes.shape[1]),
        "feature_dim": int(features.shape[1]),
        "rows": rows,
        "features": features.tolist(),
        "shapes": shapes.tolist(),
        "provenance": {
            "index_dir": args.index.as_posix(),
            "index_manifest_schema_version": manifest.get(
                "manifest_schema_version"),
            "index_git_commit": manifest.get("git_commit"),
            "source_name": manifest.get("source", {}).get("source_name"),
            "source_release": manifest.get("source", {}).get("source_release"),
            "source_bbox": manifest.get("source", {}).get("source_bbox"),
            "counts": manifest.get("counts"),
            "feature_columns": manifest.get("index", {}).get("feature_columns"),
            "config": manifest.get("config"),
            "index_output_sha256": manifest.get("output"),
            "note": "verbatim re-pack of the production index; the export "
                    "contains no query results and no synthetic data",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size} bytes, {len(rows)} candidates)")
    print(f"sha256 {digest}")
    print(f"features {features.shape} {features.dtype}, "
          f"shapes {shapes.shape} {shapes.dtype}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
