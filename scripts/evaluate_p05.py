#!/usr/bin/env python
"""CLI: run the frozen P0-5 strength-stratified evaluation.

Arms (DESIGN.md §3, frozen before the first run): P0 coarse-only, P1 production
``fourier_align``, P2 whole-library ``align`` diagnostic (every query), P3
Chamfer re-sequencing on D-class rows, plus a ShapeComp scorer arm on a frozen
every-20th-query subsample. The production algorithm, its defaults and the
index are never touched; every number lands in ``results.csv`` and every
aggregate in this output is recomputable from that file.

Per-query record: truth rank under each arm, the whole-library coarse position,
the attribution label (A_extraction / ok / C_dedup_or_truncation /
B_coarse_filter / C_rerank_order / D_coverage_or_deformation), extraction
refusals with their reason, cold and warm timings, and the mirror query run
under both ``allow_mirror`` settings as two separate rows.

Example
-------
python scripts/evaluate_p05.py --index data/index \
    --queries experiments/p0_perturbation_sweep/queries \
    --out-dir experiments/p0_perturbation_sweep/results
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluate_retrieval import attribute, coarse_rank  # noqa: E402

from earthglyph.geometry.sketch import SketchError  # noqa: E402
from earthglyph.retrieval.alignment import align  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    SearchError,
    load_index_handle,
    search_by_contour,
)

BOOTSTRAP_B = 1000
BOOTSTRAP_SEED = 20261111
SHAPECOMP_EVERY = 20


def _position(truth: int, rows: list[int]) -> int | None:
    try:
        return rows.index(truth) + 1
    except ValueError:
        return None


def evaluate_query(
    path: Path,
    index: Any,
    entry: dict[str, Any],
    *,
    shortlist_size: int,
    top_k: int,
) -> dict[str, Any]:
    """One query through P0/P1/P2 plus fidelity, attribution and timings."""
    truth = int(entry["source_row"])
    record: dict[str, Any] = {
        "query": entry["file"],
        "layer": entry["layer"],
        "perturbation": entry["perturbation"],
        "strength": entry["file"].rsplit("-r", 1)[0],
        "source_row": truth,
        "source_candidate_id": entry.get("source_candidate_id"),
        "query_seed": entry["params"].get("query_seed"),
    }
    try:
        result = search_by_contour(
            path,
            index,
            top_k=top_k,
            shortlist_size=shortlist_size,
            method="fourier_align",
        )
    except (SketchError, SearchError) as error:
        record.update({"label": "A_extraction", "refusal": str(error)})
        return record

    hit_rows = [hit.row for hit in result.hits]
    pre_rows = [hit.row for hit in result.pre_dedup]
    rank_final = _position(truth, hit_rows)
    rank_pre = _position(truth, pre_rows)
    coarse_only = search_by_contour(path, index, top_k=top_k, method="fourier")
    rank_p0 = _position(truth, [hit.row for hit in coarse_only.hits])
    full = search_by_contour(
        path,
        index,
        top_k=index.n_candidates,
        method="align",
        smoothing="light",
        max_per_source=index.n_candidates,
    )
    full_rows = [hit.row for hit in full.hits]
    rank_full = _position(truth, full_rows)
    rank_coarse = coarse_rank(
        index,
        np.asarray(result.query["query_features"], dtype=float),
        truth,
        group=str(result.params["coarse_group"]),
        scale=str(result.params["coarse_scale"]),
    )
    query_contour = np.asarray(result.query["query_contour"]["points"], dtype=float)
    fidelity = float(align(query_contour, index.shapes[truth], allow_mirror=False).distance)
    record.update(
        {
            "rank_p0_coarse_top10": rank_p0,
            "rank_final": rank_final,
            "rank_pre_dedup": rank_pre,
            "rank_coarse": rank_coarse,
            "rank_full_library": rank_full,
            "align_distance_of_truth": round(
                next(
                    hit.align_distance
                    for hit in full.hits
                    if hit.row == truth
                ),
                5,
            ),
            "query_vs_source_align": round(fidelity, 5),
            "in_shortlist": bool(rank_coarse <= shortlist_size),
            "label": attribute(
                rank_final=rank_final,
                rank_pre=rank_pre,
                rank_coarse=rank_coarse,
                rank_full=rank_full,
                shortlist_size=shortlist_size,
                top_k=top_k,
            ),
            "timings": result.timings,
        }
    )
    if record["label"] == "D_coverage_or_deformation":
        chamfered = search_by_contour(
            path,
            index,
            top_k=top_k,
            shortlist_size=shortlist_size,
            method="fourier_align",
            sort_by="chamfer",
            chamfer_top=top_k,
        )
        record["rank_chamfer_d_row"] = _position(truth, [hit.row for hit in chamfered.hits])
    if entry["perturbation"] == "mirror":
        allowed = search_by_contour(
            path,
            index,
            top_k=top_k,
            shortlist_size=shortlist_size,
            method="fourier_align",
            allow_mirror=True,
        )
        record["mirror_allowed_rank"] = _position(truth, [hit.row for hit in allowed.hits])
    return record


class ShapeCompArm:
    """Frozen separate arm: rank the library by official ShapeComp embeddings.

    Distance is the mean cosine distance of the KerNet1 and KerNet2 22-D
    embeddings, computed on the *same extracted query contour* P1 used. Scores
    are comparable only inside this arm, never against align distances.
    """

    def __init__(self, index: Any) -> None:
        from earthglyph.shape_benchmark.shapecomp_port import ShapeCompNets

        self.nets = ShapeCompNets()
        self.candidate_k1 = np.stack([self.nets.kernet1(shape) for shape in index.shapes])
        self.candidate_k2 = np.stack([self.nets.kernet2(shape) for shape in index.shapes])

    def rank(
        self, query_contour: np.ndarray, *, truth: int, top_k: int
    ) -> dict[str, Any]:
        q1 = self.nets.kernet1(query_contour)
        q2 = self.nets.kernet2(query_contour)
        d1 = _cosine_distance(q1, self.candidate_k1)
        d2 = _cosine_distance(q2, self.candidate_k2)
        mean_distance = (d1 + d2) / 2.0
        order = np.argsort(mean_distance, kind="stable")
        rows = [int(i) for i in order[:top_k]]
        return {
            "shapecomp_rank": _position(truth, rows),
            "shapecomp_distance_of_truth": round(float(mean_distance[truth]), 5),
        }


def _cosine_distance(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    q = query / max(float(np.linalg.norm(query)), 1e-12)
    c = candidates / np.maximum(np.linalg.norm(candidates, axis=1, keepdims=True), 1e-12)
    return 1.0 - c @ q


def warm_timings(
    index: Any, manifest: dict[str, Any], queries_dir: Path, *, top_k: int
) -> dict[str, dict[str, float]]:
    """Second pass, P1 only, same process: cache-warm latency per query."""
    warm: dict[str, dict[str, float]] = {}
    for entry in manifest["queries"]:
        try:
            result = search_by_contour(
                queries_dir / entry["file"],
                index,
                top_k=top_k,
                method="fourier_align",
            )
        except (SketchError, SearchError):
            continue
        warm[entry["file"]] = {k: round(v, 6) for k, v in result.timings.items()}
    return warm


def aggregate(rows: list[dict[str, Any]], sample: pd.DataFrame) -> dict[str, Any]:
    """Per layer x perturbation x strength, and per shape family — no averages
    that hide a strength curve."""
    family_of = dict(zip(sample["row"].astype(int), sample["family"], strict=True))
    out: dict[str, Any] = {}
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["layer"]), str(row["perturbation"]), str(row["strength"]))
        grouped.setdefault(key, []).append(row)
    table: dict[str, Any] = {}
    for (layer, perturbation, strength), subset in sorted(grouped.items()):
        table.setdefault(layer, {}).setdefault(perturbation, {})[strength] = _cell(subset)
    out["by_strength"] = table
    by_family: dict[str, Any] = {}
    for family in sorted(set(family_of.values())):
        subset = [r for r in rows if family_of.get(int(r["source_row"])) == family]
        if subset:
            by_family[family] = _cell(subset)
    out["by_shape_family"] = by_family
    out["labels_overall"] = {
        str(label): sum(1 for r in rows if r.get("label") == label)
        for label in sorted({str(r.get("label")) for r in rows})
    }
    return out


def _cell_shapecomp(subset: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [int(r["shapecomp_rank"]) for r in subset if r.get("shapecomp_rank") is not None]
    refusals = [r for r in subset if r.get("shapecomp_rank") is None]
    return {
        "n": len(subset),
        "refusals": len(refusals),
        "top1": round(sum(1 for r in ranks if r == 1) / len(subset), 4),
        "top10": round(sum(1 for r in ranks if r <= 10) / len(subset), 4),
        "mrr": round(sum(1.0 / r for r in ranks) / len(subset), 4),
        "median_rank": float(np.median(ranks)) if ranks else None,
    }


def _cell(subset: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [r for r in subset if r.get("rank_final") is not None]
    refusals = [r for r in subset if r.get("label") == "A_extraction"]
    ranks = [int(r["rank_final"]) for r in evaluated]
    # rank_p0 exists for D-class rows too; restricting the numerator to rows
    # whose final rank was found understated top1_p0 (e.g. mirror 0.356 vs 0.983)
    ranks_p0 = [int(r["rank_p0_coarse_top10"]) for r in subset
                if r.get("rank_p0_coarse_top10") is not None]
    labels: dict[str, int] = {}
    for row in subset:
        labels[row.get("label", "?")] = labels.get(row.get("label", "?"), 0) + 1
    totals = [r.get("timings", {}).get("total") for r in evaluated if r.get("timings")]
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
    rows: list[dict[str, Any]],
    metric: Callable[[list[dict[str, Any]]], float],
    *,
    n_boot: int = BOOTSTRAP_B,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, float]:
    """Source-clustered bootstrap: perturbations of one source are correlated."""
    by_source: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(int(row["source_row"]), []).append(row)
    if not by_source:
        return {
            "point": None, "ci95_low": None, "ci95_high": None,
            "n_sources": 0, "n_boot": n_boot, "seed": seed,
        }
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


def _top1(rows: list[dict[str, Any]]) -> float:
    # a refusal or a missing rank is a failure, never a silent exclusion
    return float(np.mean([r.get("rank_final") == 1 for r in rows]))


def _top10(rows: list[dict[str, Any]]) -> float:
    return float(np.mean([
        r.get("rank_final") is not None and r["rank_final"] <= 10 for r in rows
    ]))


def _mrr(rows: list[dict[str, Any]]) -> float:
    return float(np.mean([
        1.0 / r["rank_final"] if r.get("rank_final") is not None else 0.0 for r in rows
    ]))


def paired_delta(
    rows: list[dict[str, Any]], *, key_p0: str
) -> dict[str, Any]:
    """P1 minus P0 on the same queries, source-clustered."""
    def delta_top1(subset: list[dict[str, Any]]) -> float:
        # refusals and beyond-top-10 rows count as failures on both sides
        p1 = float(np.mean([r.get("rank_final") == 1 for r in subset]))
        p0 = float(np.mean([r.get(key_p0) == 1 for r in subset]))
        return p1 - p0

    return {
        "delta_top1_p1_minus_p0": bootstrap_ci(rows, delta_top1),
    }


RANK_COLUMNS = (
    "rank_p0_coarse_top10", "rank_final", "rank_pre_dedup", "rank_coarse",
    "rank_full_library", "rank_chamfer_d_row", "mirror_allowed_rank",
)
FLOAT_COLUMNS = ("align_distance_of_truth", "query_vs_source_align")


def rows_from_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Rebuild the in-memory row dicts from the saved per-query CSV.

    Used by ``--aggregate-only`` so a summary-side bug can be fixed and the
    summary re-emitted without re-running retrieval: the CSV is the record of
    the run and must not be rewritten by that path.
    """
    table = pd.read_csv(csv_path)
    rows: list[dict[str, Any]] = []
    for record in table.to_dict("records"):
        for column in RANK_COLUMNS:
            value = record.get(column)
            record[column] = None if pd.isna(value) else int(value)
        for column in FLOAT_COLUMNS:
            value = record.get(column)
            record[column] = None if pd.isna(value) else float(value)
        for column in ("timings", "timings_warm"):
            record[column] = (
                ast.literal_eval(record[column]) if pd.notna(record[column]) else None
            )
        record["refusal"] = (
            None if pd.isna(record["refusal"]) else str(record["refusal"])
        )
        record["source_row"] = int(record["source_row"])
        record["query_seed"] = int(record["query_seed"])
        record["in_shortlist"] = bool(record["in_shortlist"])
        rows.append(record)
    return rows


def shapecomp_rows_from_csv(csv_path: Path) -> list[dict[str, Any]]:
    table = pd.read_csv(csv_path)
    rows: list[dict[str, Any]] = []
    for record in table.to_dict("records"):
        rank = record.get("shapecomp_rank")
        record["shapecomp_rank"] = None if pd.isna(rank) else int(rank)
        record["shapecomp_distance_of_truth"] = float(record["shapecomp_distance_of_truth"])
        record["refusal"] = None if pd.isna(record["refusal"]) else str(record["refusal"])
        record["source_row"] = int(record["source_row"])
        rows.append(record)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--queries", type=Path, default=Path("experiments/p0_perturbation_sweep/queries")
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("experiments/p0_perturbation_sweep/results")
    )
    parser.add_argument("--samples", type=Path,
                        default=Path("experiments/p0_perturbation_sweep/samples/sample_sources.csv"))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--shortlist", type=int, default=200)
    parser.add_argument("--shapecomp-every", type=int, default=SHAPECOMP_EVERY,
                        help="frozen subsample stride for the separate ShapeComp arm")
    parser.add_argument("--no-shapecomp", action="store_true")
    parser.add_argument("--no-warm-pass", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="re-emit summary_by_strength.json from the saved "
                             "results.csv without re-running any retrieval")
    parser.add_argument("--limit", type=int, default=None,
                        help="smoke-test only: evaluate the first N manifest entries")
    args = parser.parse_args(argv)

    index = load_index_handle(args.index)
    manifest = json.loads((args.queries / "manifest.json").read_text(encoding="utf-8"))
    sample = pd.read_csv(args.samples)

    shapecomp: ShapeCompArm | None = None
    if not args.no_shapecomp and not args.aggregate_only:
        try:
            shapecomp = ShapeCompArm(index)
        except Exception as error:  # toolbox missing is a reportable result, not a crash
            print(f"ShapeComp arm unavailable: {error}")

    if args.aggregate_only:
        rows = rows_from_csv(args.out_dir / "results.csv")
        sc_path = args.out_dir / "shapecomp_arm.csv"
        shapecomp_rows = (
            shapecomp_rows_from_csv(sc_path) if sc_path.exists() else []
        )
        print(f"aggregate-only: {len(rows)} rows from {args.out_dir / 'results.csv'}")
        entries: list[dict[str, Any]] = []
    else:
        entries = manifest["queries"]
        rows = []
        shapecomp_rows = []
    if args.limit is not None:
        entries = entries[: args.limit]
    started = time.perf_counter()
    for ordinal, entry in enumerate(entries):
        record = evaluate_query(
            args.queries / entry["file"],
            index,
            entry,
            shortlist_size=args.shortlist,
            top_k=args.top,
        )
        if shapecomp is not None and ordinal % args.shapecomp_every == 0:
            if record.get("label") == "A_extraction":
                shapecomp_rows.append({
                    "query": record["query"], "layer": record["layer"],
                    "perturbation": record["perturbation"],
                    "strength": record["strength"], "source_row": record["source_row"],
                    "shapecomp_rank": None, "refusal": record.get("refusal"),
                })
            else:
                query_contour = np.asarray(
                    _query_contour(args.queries / entry["file"], index), dtype=float
                )
                shapecomp_rows.append({
                    "query": record["query"], "layer": record["layer"],
                    "perturbation": record["perturbation"],
                    "strength": record["strength"],
                    "source_row": int(entry["source_row"]),
                    **shapecomp.rank(query_contour, truth=int(entry["source_row"]),
                                     top_k=args.top),
                })
        rows.append(record)
        if (ordinal + 1) % 250 == 0:
            rate = (time.perf_counter() - started) / (ordinal + 1)
            print(f"{ordinal + 1}/{len(entries)} "
                  f"({rate:.2f}s/query, eta {rate * (len(entries) - ordinal - 1) / 60:.0f} min)")

    warm: dict[str, dict[str, float]] = {}
    if not args.no_warm_pass and not args.aggregate_only:
        warm = warm_timings(index, manifest, args.queries, top_k=args.top)
    for row in rows:
        w = warm.get(row["query"])
        if w:
            row["timings_warm"] = w

    for row in rows:  # attach the shape family for cluster statistics
        match = sample[sample["row"] == int(row["source_row"])]
        row["shape_family"] = str(match["family"].iloc[0]) if len(match) else "?"

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "results.csv"
    if not args.aggregate_only:  # aggregate-only must not touch the saved run
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        if shapecomp_rows:
            pd.DataFrame(shapecomp_rows).to_csv(
                args.out_dir / "shapecomp_arm.csv", index=False
            )

    summary = aggregate(rows, sample)
    summary["params"] = {
        "top_k": args.top,
        "shortlist_size": args.shortlist,
        "n_queries": len(rows),
        "index": index.provenance(),
        "design": "experiments/p0_perturbation_sweep/DESIGN.md (frozen)",
    }
    summary["bootstrap_overall"] = {
        "top1": bootstrap_ci(rows, _top1),
        "top10": bootstrap_ci(rows, _top10),
        "mrr": bootstrap_ci(rows, _mrr),
    }
    by_family_ci: dict[str, Any] = {}
    for family in sorted(set(sample["family"])):
        subset = [r for r in rows if r["shape_family"] == family]
        by_family_ci[family] = {
            "top1": bootstrap_ci(subset, _top1),
            "top10": bootstrap_ci(subset, _top10),
            "mrr": bootstrap_ci(subset, _mrr),
        }
    summary["bootstrap_by_family"] = by_family_ci
    summary["paired_delta_top1"] = paired_delta(rows, key_p0="rank_p0_coarse_top10")
    if shapecomp_rows:
        summary["shapecomp_arm"] = _cell_shapecomp(shapecomp_rows)

    summary_path = args.out_dir / "summary_by_strength.json"
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False), "utf-8")
    if args.aggregate_only:
        print(f"wrote {summary_path} from the saved CSV ({len(rows)} rows)")
    else:
        print(f"wrote {csv_path} ({len(rows)} rows), shapecomp_arm.csv "
              f"({len(shapecomp_rows)} rows) and {summary_path}")
    return 0


def _query_contour(path: Path, index: Any) -> np.ndarray:
    result = search_by_contour(path, index, top_k=1, method="fourier_align")
    return np.asarray(result.query["query_contour"]["points"], dtype=float)


if __name__ == "__main__":
    raise SystemExit(main())
