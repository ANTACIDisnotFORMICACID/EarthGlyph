#!/usr/bin/env python
"""C1: independently recompute the P0-5 summary metrics from the saved CSVs alone.

No retriever code, no evaluate_p05 imports — only pandas/numpy over
``results/results.csv`` and ``results/shapecomp_arm.csv``. The bootstrap is
re-implemented here (source-clustered, same seed and same draw scheme) rather
than re-imported, so a match with ``results/summary_by_strength.json`` is real
independent evidence, not a tautology.

Conventions (must match the design):
- a NaN rank means "truth not in the returned list" and stays in the
  denominator as a failure, exactly like a refusal — nothing is dropna'd;
- ``rank_p0_coarse_top10`` counts every row that has a valid coarse rank
  (D-class rows included); refusals have none and count as failures;
- ``median_rank`` / ``p90_rank`` are over found rows only and are reported as
  such.

Usage:
    python scripts/recompute_p05_metrics.py \
        --results experiments/p0_perturbation_sweep/results
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

BOOTSTRAP_B = 1000
BOOTSTRAP_SEED = 20261111
TOL = 5e-4
RANK_COLUMNS = (
    "rank_p0_coarse_top10", "rank_final", "rank_pre_dedup", "rank_coarse",
    "rank_full_library", "rank_chamfer_d_row", "mirror_allowed_rank",
)


def load_rows(csv_path: Path) -> list[dict[str, object]]:
    table = pd.read_csv(csv_path)
    rows: list[dict[str, object]] = []
    for record in table.to_dict("records"):
        for column in RANK_COLUMNS:
            value = record[column]
            record[column] = None if pd.isna(value) else int(value)
        for column in ("align_distance_of_truth", "query_vs_source_align"):
            value = record[column]
            record[column] = None if pd.isna(value) else float(value)
        record["refusal"] = None if pd.isna(record["refusal"]) else str(record["refusal"])
        record["source_row"] = int(record["source_row"])
        for column in ("timings", "timings_warm"):
            record[column] = (
                ast.literal_eval(record[column]) if pd.notna(record[column]) else None
            )
        rows.append(record)
    return rows


def top1(rows: list[dict[str, object]], column: str) -> float:
    return float(np.mean([row.get(column) == 1 for row in rows]))


def top10(rows: list[dict[str, object]], column: str) -> float:
    return float(np.mean([row.get(column) is not None and row[column] <= 10
                          for row in rows]))  # type: ignore[operator]


def mrr(rows: list[dict[str, object]], column: str) -> float:
    return float(np.mean([
        1.0 / row[column] if row.get(column) is not None else 0.0 for row in rows
    ]))


def cell(subset: list[dict[str, object]]) -> dict[str, object]:
    evaluated = [row for row in subset if row.get("rank_final") is not None]
    refusals = [row for row in subset if row.get("label") == "A_extraction"]
    ranks = [int(row["rank_final"]) for row in evaluated]
    ranks_p0 = [int(row["rank_p0_coarse_top10"]) for row in subset
                if row.get("rank_p0_coarse_top10") is not None]
    labels: dict[str, int] = {}
    for row in subset:
        labels[str(row.get("label"))] = labels.get(str(row.get("label")), 0) + 1
    totals = [
        row["timings"]["total"] for row in evaluated
        if isinstance(row.get("timings"), dict)
    ]
    return {
        "n": len(subset),
        "refusals": len(refusals),
        "top1_p0": round(sum(1 for r in ranks_p0 if r == 1) / len(subset), 4),
        "top1": round(sum(1 for r in ranks if r == 1) / len(subset), 4),
        "top10": round(sum(1 for r in ranks if r <= 10) / len(subset), 4),
        "mrr": round(sum(1.0 / r for r in ranks) / len(subset), 4),
        "median_rank": float(np.median(ranks)) if ranks else None,
        "p90_rank": float(np.percentile(ranks, 90)) if ranks else None,
        "labels": labels,
        "median_total_ms": round(float(np.median(totals)) * 1000, 3) if totals else None,
    }


def bootstrap_ci(
    rows: list[dict[str, object]],
    metric: Callable[[list[dict[str, object]]], float],
    *,
    n_boot: int = BOOTSTRAP_B,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    """Source-clustered bootstrap, same draw scheme as the run: resample
    sources with ``rng.integers`` so the draws reproduce bit-for-bit."""
    by_source: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        by_source.setdefault(int(row["source_row"]), []).append(row)
    sources = sorted(by_source)
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(sources), len(sources))
        subset = [row for i in pick for row in by_source[sources[int(i)]]]
        stats.append(metric(subset))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return {
        "point": round(metric(rows), 4),
        "ci95_low": round(float(lo), 4),
        "ci95_high": round(float(hi), 4),
        "n_sources": len(sources),
        "n_boot": n_boot,
        "seed": seed,
    }


def diff_section(name: str, mine: object, theirs: object) -> list[str]:
    problems: list[str] = []
    if isinstance(mine, dict) and isinstance(theirs, dict):
        for key in sorted(set(mine) | set(theirs)):
            problems += diff_section(f"{name}.{key}", mine.get(key), theirs.get(key))
    elif isinstance(mine, float) and isinstance(theirs, (int, float)):
        if abs(mine - float(theirs)) > TOL:
            problems.append(f"{name}: recomputed {mine} vs summary {theirs}")
    elif isinstance(mine, int) and isinstance(theirs, (int, float)):
        if abs(mine - float(theirs)) > 0:
            problems.append(f"{name}: recomputed {mine} vs summary {theirs}")
    elif mine != theirs:
        problems.append(f"{name}: recomputed {mine!r} vs summary {theirs!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path, default=Path("experiments/p0_perturbation_sweep/results")
    )
    args = parser.parse_args(argv)

    rows = load_rows(args.results / "results.csv")
    shapecomp = pd.read_csv(args.results / "shapecomp_arm.csv")
    with (args.results / "summary_by_strength.json").open(encoding="utf-8") as handle:
        summary = json.load(handle)

    problems: list[str] = []

    # 1. overall labels
    labels: dict[str, int] = {}
    for row in rows:
        labels[str(row["label"])] = labels.get(str(row["label"]), 0) + 1
    problems += diff_section("labels_overall", labels, summary["labels_overall"])

    # 2. per-strength cells
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = (str(row["layer"]), str(row["perturbation"]), str(row["strength"]))
        grouped.setdefault(key, []).append(row)
    by_strength: dict[str, dict[str, dict[str, object]]] = {}
    for (layer, perturbation, strength), subset in sorted(grouped.items()):
        by_strength.setdefault(layer, {}).setdefault(perturbation, {})[strength] = cell(subset)
    problems += diff_section("by_strength", by_strength, summary["by_strength"])

    # 3. per-family cells
    family_of = {
        int(record["row"]): str(record["family"])
        for record in pd.read_csv(
            "experiments/p0_perturbation_sweep/samples/sample_sources.csv"
        ).to_dict("records")
    }
    by_family: dict[str, dict[str, object]] = {}
    for family in sorted(set(family_of.values())):
        subset = [row for row in rows
                  if family_of.get(int(row["source_row"])) == family]  # type: ignore[arg-type]
        if subset:
            by_family[family] = cell(subset)
    problems += diff_section("by_shape_family", by_family, summary["by_shape_family"])

    # 4. overall bootstrap metrics, reimplemented from the raw table
    def _top1(subset: list[dict[str, object]]) -> float:
        return top1(subset, "rank_final")

    def _top10(subset: list[dict[str, object]]) -> float:
        return top10(subset, "rank_final")

    def _mrr(subset: list[dict[str, object]]) -> float:
        return mrr(subset, "rank_final")

    for metric, fn in (("top1", _top1), ("top10", _top10), ("mrr", _mrr)):
        problems += diff_section(
            f"bootstrap_overall.{metric}", bootstrap_ci(rows, fn),
            summary["bootstrap_overall"][metric],
        )

    # 5. per-family bootstrap CIs
    for family in by_family:
        subset = [row for row in rows
                  if family_of.get(int(row["source_row"])) == family]  # type: ignore[arg-type]
        for metric, fn in (("top1", _top1), ("top10", _top10), ("mrr", _mrr)):
            problems += diff_section(
                f"bootstrap_by_family.{family}.{metric}", bootstrap_ci(subset, fn),
                summary["bootstrap_by_family"][family][metric],
            )

    # 6. paired delta P1 - P0 on the same queries, same clustering
    def delta_top1(subset: list[dict[str, object]]) -> float:
        p1 = top1(subset, "rank_final")
        p0 = top1(subset, "rank_p0_coarse_top10")
        return p1 - p0

    problems += diff_section(
        "paired_delta_top1.delta_top1_p1_minus_p0", bootstrap_ci(rows, delta_top1),
        summary["paired_delta_top1"]["delta_top1_p1_minus_p0"],
    )

    # 7. shapecomp arm
    sc_rows: list[dict[str, object]] = []
    for record in shapecomp.to_dict("records"):
        rank = record["shapecomp_rank"]
        sc_rows.append({
            "shapecomp_rank": None if pd.isna(rank) else int(rank),
            "refusal": None if pd.isna(record["refusal"]) else str(record["refusal"]),
        })
    sc_ranks = [int(row["shapecomp_rank"]) for row in sc_rows
                if row["shapecomp_rank"] is not None]
    sc = {
        "n": len(sc_rows),
        "refusals": sum(1 for row in sc_rows if row["shapecomp_rank"] is None),
        "top1": round(sum(1 for r in sc_ranks if r == 1) / len(sc_rows), 4),
        "top10": round(sum(1 for r in sc_ranks if r <= 10) / len(sc_rows), 4),
        "mrr": round(sum(1.0 / r for r in sc_ranks) / len(sc_rows), 4),
        "median_rank": float(np.median(sc_ranks)) if sc_ranks else None,
    }
    problems += diff_section("shapecomp_arm", sc, summary["shapecomp_arm"])

    # 8. cold vs warm timing split (the summary only stores cold medians)
    cold = np.array([
        row["timings"]["total"] for row in rows if isinstance(row.get("timings"), dict)
    ])
    warm = np.array([
        row["timings_warm"]["total"] for row in rows
        if isinstance(row.get("timings_warm"), dict)
    ])
    timings = {
        "cold_median_ms": round(float(np.median(cold)) * 1000, 3),
        "warm_median_ms": round(float(np.median(warm)) * 1000, 3),
        "cold_p90_ms": round(float(np.percentile(cold, 90)) * 1000, 3),
        "warm_p90_ms": round(float(np.percentile(warm, 90)) * 1000, 3),
        "first_query_total_ms": round(float(cold[0]) * 1000, 3),
    }

    # mirror policy: production no-mirror vs the allow_mirror=True diagnostic
    mirror = [row for row in rows if row["perturbation"] == "mirror"]
    mirror_policy = {
        "n": len(mirror),
        "top1_no_mirror": round(top1(mirror, "rank_final"), 4),
        "top10_no_mirror": round(top10(mirror, "rank_final"), 4),
        "top1_mirror_allowed": round(top1(mirror, "mirror_allowed_rank"), 4),
        "top10_mirror_allowed": round(top10(mirror, "mirror_allowed_rank"), 4),
    }

    print(json.dumps(
        {"timings_cold_vs_warm": timings, "mirror_policy": mirror_policy}, indent=1
    ))

    if problems:
        print(f"\nMISMATCH ({len(problems)}):", file=sys.stderr)
        for item in problems[:40]:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("\nPASS: every recomputed metric matches summary_by_strength.json "
          f"(tol {TOL}); bootstrap draws reproduced bit-for-bit from the same seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
