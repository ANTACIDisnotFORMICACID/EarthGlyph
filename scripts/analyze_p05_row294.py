#!/usr/bin/env python
"""CLI: B4 — does a higher sampling count fix the row-294-class failures?

Row 294 (a forked reservoir) failed 22/30 P0-4 probes with rank 23-502 even
under whole-library re-ranking. This script measures, per candidate sampling
count N in {128, 256, 512}:

1. **Geometric fidelity** of every forked-family source (18 candidates from the
   frozen P0-5 sample) against its *original* Overture boundary via the P0-3
   primitives (``build_contour`` + ``contour_fidelity``): region IoU, perimeter
   retention (a dendritic shape losing thin branches loses perimeter), and the
   normalized Hausdorff deviation.
2. **Retrieval rank of row 294** under three arms (P1 production
   ``fourier_align`` top-10, P0 coarse-only top-10, P2 whole-library ``align``)
   for all 49 row-294 probes (the 31 frozen P0-5 variants + P0-4's 18), with
   the index rebuilt per N. The candidate set, its order, the feature pipeline
   and the query traces are identical across N; only the sampling count moves,
   and each query is re-extracted at its index's own contract (the search API
   reads ``index.n_points``), so sampling contracts are never mixed.

The production index (``data/index``, N=128) is only ever read; the per-N
rebuilds land under the experiment directory. No threshold, candidate or query
is dropped to make the numbers look better.

Example
-------
python scripts/analyze_p05_row294.py --raw data/raw/wisconsin_water.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from make_p05_samples import FORKED_SOURCE_OVERRIDE  # noqa: E402

from earthglyph.data.index import IndexConfig, build_index  # noqa: E402
from earthglyph.data.prepare import build_from_source  # noqa: E402
from earthglyph.geometry import build_contour, exterior_ring  # noqa: E402
from earthglyph.geometry.fidelity import contour_fidelity  # noqa: E402
from earthglyph.geometry.sketch import SketchError  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    load_index_handle,
    search_by_contour,
)

SAMPLE_POINTS = (128, 256, 512)
ROW_294 = 294
P05_QUERIES = Path("experiments/p0_perturbation_sweep/queries")
P04_QUERIES = Path("experiments/p0_retrieval_baseline/queries")


def original_rings(
    raw_path: Path, contours: pd.DataFrame
) -> dict[str, np.ndarray]:
    """Full-resolution projected boundary for exactly the released candidates."""
    build = build_from_source(raw_path, n_points=128)
    rings = {
        candidate.candidate_id: np.asarray(
            exterior_ring(candidate.polygon_metric), dtype=float
        )
        for candidate in build.candidates
    }
    stored = set(map(str, contours["candidate_id"]))
    if stored != set(rings):
        raise SystemExit("source rebuild does not match the released candidate set")
    return rings


def fidelity_table(
    rings: dict[str, np.ndarray],
    contours: pd.DataFrame,
    forked_ids: list[str],
) -> pd.DataFrame:
    rows = []
    for candidate_id in forked_ids:
        ring = rings[candidate_id]
        for n_points in SAMPLE_POINTS:
            contour = build_contour(ring, n_points)
            fidelity = contour_fidelity(ring, contour.metric_points)
            rows.append(
                {"candidate_id": candidate_id, "n_points": n_points,
                 **{key: round(float(value), 6)
                    for key, value in fidelity.as_dict().items()
                    if isinstance(value, (int, float, np.integer, np.floating))
                    and not isinstance(value, bool)}}
            )
    return pd.DataFrame(rows)


def build_index_at_n(
    rings: dict[str, np.ndarray],
    contours: pd.DataFrame,
    n_points: int,
    out_root: Path,
    *,
    raw_path: Path,
) -> tuple[Path, float]:
    """Same candidates, same order, same feature pipeline — only N moves."""
    out_dir = out_root / f"index_n{n_points}"
    processed = out_root / f"processed_n{n_points}"
    processed.mkdir(parents=True, exist_ok=True)
    rebuilt = contours.copy()
    rebuilt["n_points"] = n_points
    new_contours = []
    for candidate_id in contours["candidate_id"]:
        contour = build_contour(rings[str(candidate_id)], n_points)
        new_contours.append(np.asarray(contour.points, dtype=float).ravel())
    rebuilt["contour"] = new_contours
    contours_path = processed / "contours.parquet"
    rebuilt.to_parquet(contours_path, index=False)

    # build_index traces provenance through the processed-dataset manifest. Copy the
    # released one and amend it honestly: only the contour/n_points columns were
    # rebuilt from the same source rings; everything else is verbatim.
    source_manifest = json.loads(
        Path("data/processed/data_manifest.json").read_text(encoding="utf-8")
    )
    source_manifest["b4_rebuild"] = {
        "purpose": "B4 row-294 sampling-fidelity control (experiments/p0_sampling_fidelity_294)",
        "rebuild_command": f"python scripts/analyze_p05_row294.py --raw {raw_path}",
        "what_changed": [
            f"contour column rebuilt at n_points={n_points} via "
            "earthglyph.geometry.build_contour from the same source exterior rings",
            f"n_points set to {n_points}",
        ],
        "what_is_unchanged": "candidate set, candidate order, all metadata columns, "
        "filtering decisions, projection and cleaning",
        "not_a_release": "this processed dataset is an experiment-local control, "
        "not a replacement for data/processed",
    }
    (processed / "data_manifest.json").write_text(
        json.dumps(source_manifest, indent=1, ensure_ascii=False), "utf-8"
    )

    started = time.perf_counter()
    build_index(
        contours_path,
        out_dir,
        config=IndexConfig(n_harmonics=16, fold="positive", dtype="float32"),
        force=True,
        build_command=(
            f"python scripts/analyze_p05_row294.py (rebuild at n_points={n_points})"
        ),
    )
    return out_dir, time.perf_counter() - started


def _position(truth: int, rows: list[int]) -> int | None:
    try:
        return rows.index(truth) + 1
    except ValueError:
        return None


def rank_row294(
    index: Any,
    truth_row: int,
    query_files: list[Path],
) -> list[dict[str, Any]]:
    rows = []
    for path in query_files:
        record: dict[str, Any] = {"query": path.name}
        try:
            production = search_by_contour(path, index, top_k=10, method="fourier_align")
        except SketchError as error:
            record.update({"label": "A_extraction", "refusal": str(error)})
            rows.append(record)
            continue
        coarse_only = search_by_contour(path, index, top_k=10, method="fourier")
        full = search_by_contour(
            path, index, top_k=index.n_candidates, method="align",
            max_per_source=index.n_candidates,
        )
        record.update(
            {
                "rank_p1": _position(truth_row, [hit.row for hit in production.hits]),
                "rank_p0": _position(truth_row, [hit.row for hit in coarse_only.hits]),
                "rank_full": _position(truth_row, [hit.row for hit in full.hits]),
                "align_of_truth": round(
                    next(hit.align_distance for hit in full.hits if hit.row == truth_row), 5
                ),
                "total_ms": round(production.timings["total"] * 1000, 2),
            }
        )
        rows.append(record)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw/wisconsin_water.parquet"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("experiments/p0_perturbation_sweep/samples/sample_sources.csv"),
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("experiments/p0_sampling_fidelity_294")
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    contours = pd.read_parquet("data/processed/contours.parquet")
    sample = pd.read_csv(args.samples)
    forked = sample[sample["family"] == "forked"]
    forked_ids = [str(index_metadata) for index_metadata in forked["candidate_id"]]

    print("recovering original boundaries ...", flush=True)
    rings = original_rings(args.raw, contours)

    fidelity = fidelity_table(rings, contours, forked_ids)
    fidelity_path = args.out_dir / "fidelity_by_n.csv"
    fidelity.to_csv(fidelity_path, index=False)
    print(f"wrote {fidelity_path} ({len(fidelity)} rows)")

    print("building per-N indexes (256, 512) ...", flush=True)
    index_dirs: dict[int, Path] = {128: args.index}
    build_seconds: dict[int, float] = {128: 0.0}
    for n_points in (256, 512):
        index_dir, seconds = build_index_at_n(
            rings, contours, n_points, args.out_dir, raw_path=args.raw
        )
        index_dirs[n_points] = index_dir
        build_seconds[n_points] = round(seconds, 1)
        print(f"  index_n{n_points}: {seconds:.1f}s")

    r294_id = str(contours.iloc[ROW_294]["candidate_id"])
    if not r294_id.startswith(FORKED_SOURCE_OVERRIDE[:12]):
        raise SystemExit(f"row {ROW_294} is not the declared forked source: {r294_id}")

    query_files = sorted(P05_QUERIES.glob("*-r294.json")) + sorted(
        P04_QUERIES.glob("*r294*.json")
    )
    print(f"running {len(query_files)} row-294 probes against "
          f"{len(index_dirs)} indexes ...", flush=True)
    rank_rows: list[dict[str, Any]] = []
    for n_points in SAMPLE_POINTS:
        index = load_index_handle(index_dirs[n_points])
        truth_row = int(
            index.metadata.index[
                index.metadata["candidate_id"] == r294_id
            ][0]
        )
        for record in rank_row294(index, truth_row, query_files):
            record["n_points"] = n_points
            record["truth_row"] = truth_row
            rank_rows.append(record)

    ranks = pd.DataFrame(rank_rows)
    ranks_path = args.out_dir / "rank_by_n.csv"
    ranks.to_csv(ranks_path, index=False)

    summary: dict[str, Any] = {
        "row_294": {"candidate_id": r294_id, "class": "reservoir",
                    "declared_override_source": FORKED_SOURCE_OVERRIDE},
        "sample_points": list(SAMPLE_POINTS),
        "build_seconds": build_seconds,
        "n_queries_per_n": len(query_files),
        "query_sources": {
            "p0_5": "experiments/p0_perturbation_sweep/queries/*-r294.json",
            "p0_4": "experiments/p0_retrieval_baseline/queries/*r294*.json",
        },
        "index_size_bytes": {
            str(n): (index_dirs[n] / "shapes.npy").stat().st_size for n in SAMPLE_POINTS
        },
    }
    by_n: dict[str, Any] = {}
    for n_points in SAMPLE_POINTS:
        block = ranks[ranks["n_points"] == n_points]

        def rate(column: str, bound: int, block: pd.DataFrame = block) -> float:
            # a NaN rank means "truth not in the returned list" — a failure that
            # must stay in the denominator, exactly like a refusal does
            return round(float((block[column] <= bound).mean()), 4)

        evaluated = block[block["rank_p1"].notna()] if "rank_p1" in block else block
        by_n[str(n_points)] = {
            "n_probes": int(len(block)),
            "n_refusals": int((block["label"] == "A_extraction").sum())
            if "label" in block else 0,
            "n_top10_miss_p1": int(block["rank_p1"].isna().sum())
            if "rank_p1" in block else None,
            "top1_p1": rate("rank_p1", 1),
            "top10_p1": rate("rank_p1", 10),
            "top10_p0": rate("rank_p0", 10),
            "top10_full": rate("rank_full", 10),
            "median_rank_p1_found_only": float(block["rank_p1"].median())
            if "rank_p1" in block else None,
            "median_rank_full_found_only": float(block["rank_full"].median())
            if "rank_full" in block else None,
            "median_align_of_truth": float(block["align_of_truth"].median())
            if "align_of_truth" in block else None,
            "median_total_ms": float(evaluated["total_ms"].median())
            if len(evaluated) else None,
        }
    summary["by_n"] = by_n
    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False), "utf-8")
    print(f"wrote {ranks_path} and {summary_path}")
    print(json.dumps(by_n, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
