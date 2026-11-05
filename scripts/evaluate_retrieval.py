#!/usr/bin/env python
"""CLI: the three P0-4 evaluation layers, with failure attribution (Tasks G and H).

Layers are reported separately because they answer different questions and must
never be averaged together (docs/EXPERIMENTS.md, task brief §G):

G1 correctness  synthetic perturbations of *real* library contours whose answer is
                known (the source row itself). Measures the pipeline, not drawing.
G2 robustness   irregular traces — jitter, local bump/dent, aspect change, sparse
                sampling, inaccurate closing — at three smoothing levels.
G3 hand-like    the archetype traces and PNGs. No ground truth exists for these;
                they are reported as distances plus figures for a human to judge.

Every G1/G2 row carries the numbers behind its label, so the label can be
re-derived or disputed: the rank in the coarse ordering, the rank after re-ranking
all 839 candidates, the rank in the returned list, and before de-duplication.
``full_library_rank`` is the diagnostic the brief asks for when a query fails: it
separates "the coarse filter dropped it" from "nothing in this library is close".

Example
-------
python scripts/evaluate_retrieval.py --index data/index \
    --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline --top 10 --shortlist 200 \
    --ablation --sweep 100,200,300
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_shared import short_id  # noqa: E402

from earthglyph.features.fourier import harmonic_energy_fractions  # noqa: E402
from earthglyph.features.geometry_stats import shape_metrics  # noqa: E402
from earthglyph.geometry.resampling import open_ring, ring_perimeter  # noqa: E402
from earthglyph.geometry.sketch import (  # noqa: E402
    SMOOTHING_SIGMA,
    SketchError,
    contour_from_sketch,
    load_sketch_json,
    smooth_cyclic,
)
from earthglyph.retrieval import coarse as coarse_mod  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    IndexHandle,
    SearchError,
    load_index_handle,
    search_by_contour,
)

# G1 perturbations: the answer is known, so a miss is an algorithm bug.
CORRECTNESS = ("self", "rot", "scale", "shift", "reverse", "sparse", "mirror")
# G2 deformations: the answer is known but the shape really did change.
ROBUSTNESS = ("jag", "island", "bay", "aspect", "gap")
LAYERS = {"G1_correctness": CORRECTNESS, "G2_robustness": ROBUSTNESS}
# A re-rank distance above this is reported as "no close candidate", matching the
# note the search interface itself emits (search.WEAK_ALIGN_DISTANCE).
WEAK_DISTANCE = 0.12
TOP_K = 10


def _position(row: int, rows: list[int]) -> int | None:
    """1-based position of ``row`` in ``rows``, or None when it is absent."""
    return rows.index(row) + 1 if row in rows else None


def coarse_rank(
    index: IndexHandle, query_vector: np.ndarray, row: int, *, group: str, scale: str
) -> int:
    """Where the true row sits in the *whole-library* coarse ordering (1-based)."""
    columns = coarse_mod.group_columns(index.groups, group, index.features.shape[1])
    shortlist = coarse_mod.rank(
        query_vector[columns], index.features[:, columns], group=group, scale=scale
    )
    return int(np.where(shortlist.indices == row)[0][0]) + 1


def evaluate(
    path: Path,
    index: IndexHandle,
    *,
    truth: int | None,
    smoothing: str,
    shortlist_size: int,
    top_k: int = TOP_K,
    allow_mirror: bool = False,
    allow_self_intersection: bool = False,
) -> dict[str, Any]:
    """One query, one setting: the returned ranking plus the diagnostics behind it."""
    record: dict[str, Any] = {
        "query": path.name,
        "smoothing": smoothing,
        "allow_mirror": allow_mirror,
        "truth_row": truth,
    }
    try:
        result = search_by_contour(
            path,
            index,
            top_k=top_k,
            shortlist_size=shortlist_size,
            smoothing=smoothing,
            allow_mirror=allow_mirror,
            allow_self_intersection=allow_self_intersection,
            chamfer_top=min(top_k, 10),
        )
    except (SketchError, SearchError) as error:
        # Layer A: nothing downstream ever saw this input.
        record.update({"label": "A_extraction", "refusal": str(error)})
        return record

    hits = [hit.row for hit in result.hits]
    pre_dedup = [hit.row for hit in result.pre_dedup]
    query_contour = np.asarray(result.query["query_contour"]["points"], dtype=float)
    record.update(
        {
            "n_raw_points": result.query["query_contour"]["n_raw_points"],
            "end_gap_rel": round(float(result.query["query_contour"]["closure"]["gap_rel"]), 4),
            "top1_row": hits[0] if hits else None,
            "top1_align": round(result.hits[0].align_distance, 5) if hits else None,
            "weak_note": any("not verified matches" in note for note in result.warnings),
        }
    )
    if truth is None:
        # G3 has no ground truth: report what was returned, judge the overlays.
        record.update(
            {
                "label": "G3_no_ground_truth",
                "top_rows": hits,
                "top_align": [round(hit.align_distance, 5) for hit in result.hits],
            }
        )
        return record

    full = search_by_contour(
        path,
        index,
        top_k=index.n_candidates,
        method="align",
        smoothing=smoothing,
        allow_mirror=allow_mirror,
        # every candidate once, in re-rank order: de-duplication would hide the
        # true row behind a sibling and the diagnostic would read as a coverage gap
        max_per_source=index.n_candidates,
    )
    full_rows = [hit.row for hit in full.hits]
    rank_final = _position(truth, hits)
    rank_pre = _position(truth, pre_dedup)
    rank_full = _position(truth, full_rows)
    rank_coarse = coarse_rank(
        index,
        np.asarray(result.query["query_features"], dtype=float),
        truth,
        group=str(result.params["coarse_group"]),
        scale=str(result.params["coarse_scale"]),
    )
    align_of_truth = next(hit.align_distance for hit in full.hits if hit.row == truth)
    record.update(
        {
            "rank_final": rank_final,
            "rank_pre_dedup": rank_pre,
            "rank_coarse": rank_coarse,
            "rank_full_library": rank_full,
            "align_distance_of_truth": round(align_of_truth, 5),
            "in_shortlist": rank_coarse <= shortlist_size,
            "label": attribute(
                rank_final=rank_final,
                rank_pre=rank_pre,
                rank_coarse=rank_coarse,
                rank_full=rank_full,
                shortlist_size=shortlist_size,
                top_k=top_k,
            ),
        }
    )
    # The query contour vs the stored source contour, independent of any ranking:
    # how much of the shape the extraction itself kept.
    fidelity = align_pair(query_contour, index.shapes[truth], allow_mirror=allow_mirror)
    record["query_vs_source_align"] = round(fidelity, 5)
    return record


def align_pair(a: np.ndarray, b: np.ndarray, *, allow_mirror: bool) -> float:
    from earthglyph.retrieval.alignment import align

    return float(align(a, b, allow_mirror=allow_mirror).distance)


def attribute(
    *,
    rank_final: int | None,
    rank_pre: int | None,
    rank_coarse: int,
    rank_full: int,
    shortlist_size: int,
    top_k: int,
) -> str:
    """Task H's four classes, decided by measured ranks only.

    ``C_dedup_or_truncation``, ``B_coarse_filter`` and ``C_rerank_order`` are
    separated by the three ranks: a true row that sits in the pre-de-duplication
    window but not in the returned list was lost to de-duplication or truncation;
    one whose whole-library coarse position is past the shortlist was lost to the
    coarse filter; one that the coarse filter did keep but the re-rank ordered
    outside the top-k was lost to the re-rank. ``D_coverage_or_deformation`` is the
    case where even re-ranking all 839 candidates leaves the true row outside the
    top-k — the library genuinely has ``top_k`` closer shapes, or the deformation
    moved the query past them, and no shortlist size would have fixed that.
    """
    if rank_final is not None and rank_final <= top_k:
        return "ok"
    if rank_pre is not None and rank_pre <= top_k:
        return "C_dedup_or_truncation"
    if rank_coarse > shortlist_size and rank_full <= top_k:
        return "B_coarse_filter"
    if rank_full <= top_k:
        return "C_rerank_order"
    return "D_coverage_or_deformation"


# --- smoothing ablation (Task A3) --------------------------------------------


def ablation_table(index: IndexHandle, *, n_harmonics: int = 16) -> list[dict[str, Any]]:
    """What each smoothing level costs, measured on every candidate in the library.

    Deliberately *not* a "looks better" judgement. Three measured costs: the share
    of each contour's own non-DC spectral energy that sits in the upper half of the
    descriptor band (a low-pass does not remove much of that share, because the
    band is already a small part of the total — which is why the absolute shares,
    not only their ratio, are reported); the change in compactness, which a real
    notch moves; and the change in perimeter, which is where a dendritic
    reservoir actually lives. A level that shortens perimeters is erasing shape,
    whatever it does to jitter.
    """
    reference = index.shapes
    # per contour, because the energy fraction is normalized by that contour's own
    # non-DC energy — a batch call would have no single denominator to use
    base_energy = np.stack(
        [harmonic_energy_fractions(shape, n_harmonics) for shape in reference]
    )
    base_rings = [open_ring(shape) for shape in reference]
    base_compact = np.array([shape_metrics(ring).compactness for ring in base_rings])
    base_perim = np.array([ring_perimeter(ring) for ring in base_rings])
    detail = (n_harmonics - 1, n_harmonics // 2 - 1)  # cumulative indices, high then low
    base_detail = base_energy[:, detail[0]] - base_energy[:, detail[1]]
    rows: list[dict[str, Any]] = []
    for level, sigma in SMOOTHING_SIGMA.items():
        smoothed = np.stack([smooth_cyclic(shape, sigma=sigma) for shape in reference])
        energy = np.stack(
            [harmonic_energy_fractions(shape, n_harmonics) for shape in smoothed]
        )
        level_detail = energy[:, detail[0]] - energy[:, detail[1]]
        ratio = level_detail / np.maximum(base_detail, 1e-12)
        rings = [open_ring(shape) for shape in smoothed]
        compact = np.abs(
            np.array([shape_metrics(ring).compactness for ring in rings]) - base_compact
        )
        perim = np.array([ring_perimeter(ring) for ring in rings])
        perimeter = np.abs(perim - base_perim) / np.maximum(base_perim, 1e-12)
        rows.append(
            {
                "smoothing": level,
                "sigma": sigma,
                "n_candidates": int(reference.shape[0]),
                "n_harmonics": n_harmonics,
                "detail_band": f"k={n_harmonics // 2 + 1}..{n_harmonics}",
                "base_mean_detail_share": round(float(np.mean(base_detail)), 6),
                "mean_detail_share": round(float(np.mean(level_detail)), 6),
                "mean_detail_share_ratio": round(float(np.mean(ratio)), 6),
                "min_detail_share_ratio": round(float(np.min(ratio)), 6),
                "mean_compactness_change": round(float(np.mean(compact)), 6),
                "max_compactness_change": round(float(np.max(compact)), 6),
                "mean_perimeter_change": round(float(np.mean(perimeter)), 6),
                "max_perimeter_change": round(float(np.max(perimeter)), 6),
            }
        )
    return rows


# --- aggregation -------------------------------------------------------------


def summarize(
    rows: list[dict[str, Any]],
    table: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Per-family counts, rank distributions and labels. No averaged cross-layer score."""
    out: dict[str, Any] = {"params": params, "layers": {}}
    for layer, families in LAYERS.items():
        by_family: dict[str, Any] = {}
        for family in families:
            subset = [
                row
                for row in rows
                if row.get("layer") == layer and row.get("perturbation") == family
            ]
            if not subset:
                continue
            ranks = [row.get("rank_final") for row in subset if row.get("rank_final") is not None]
            counts: dict[str, int] = {}
            for row in subset:
                counts[row["label"]] = counts.get(row["label"], 0) + 1
            by_family[family] = {
                "n": len(subset),
                "top1_rate": round(sum(1 for r in ranks if r == 1) / len(subset), 4),
                "topk_rate": round(len(ranks) / len(subset), 4),
                "median_rank": float(np.median(ranks)) if ranks else None,
                "max_rank": max(ranks) if ranks else None,
                "median_align_of_truth": _median(subset, "align_distance_of_truth"),
                "labels": counts,
            }
        out["layers"][layer] = by_family
    out["labels_overall"] = _label_counts(rows)
    out["smoothing_ablation"] = table
    out["hand_like"] = [
        {
            "query": row["query"],
            "smoothing": row["smoothing"],
            "top1_row": row.get("top1_row"),
            "top1_align": row.get("top1_align"),
            "weak_note": row.get("weak_note"),
            "label": row["label"],
            "refusal": row.get("refusal"),
        }
        for row in rows
        if row.get("layer") == "G3_hand_like"
    ]
    return out


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    return round(float(np.median(values)), 5) if values else None


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    return dict(sorted(counts.items()))


def shortlist_sweep(
    index: IndexHandle,
    manifest: dict[str, Any],
    queries_dir: Path,
    *,
    smoothing: str,
    sizes: tuple[int, ...],
    top_k: int,
) -> dict[str, Any]:
    """Does a bigger coarse window change the answer? Run the pipeline, don't infer it.

    Each probe is searched with ``method="fourier"`` — the coarse filter alone, no
    geometric re-rank — once per shortlist size, so the only variable is how many
    candidates the shortlist is allowed to hold. The list is still de-duplicated to
    one candidate per ``source_id``, which is why these numbers are not the raw
    whole-library coarse ranks in ``results.csv``: a query whose true row is coarse
    rank 1 can still sit behind a sibling of the same source. Reported per layer
    and family, because "Top-300 fixes it" is only true for rows the coarse filter
    actually dropped.
    """
    per_size: dict[str, Any] = {}
    for size in sizes:
        n_seen: dict[str, int] = {}
        n_top1: dict[str, int] = {}
        n_topk: dict[str, int] = {}
        for entry in manifest["queries"]:
            if entry["family"] != "probe":
                continue
            layer = "G1" if entry["perturbation"] in CORRECTNESS else "G2"
            key = f"{layer}/{entry['perturbation']}"
            path = queries_dir / entry["file"]
            try:
                result = search_by_contour(
                    path,
                    index,
                    top_k=top_k,
                    shortlist_size=size,
                    method="fourier",  # coarse filter only, no re-rank
                    smoothing=smoothing,
                )
            except (SketchError, SearchError):
                continue
            position = _position(int(entry["source_row"]), [hit.row for hit in result.hits])
            n_seen[key] = n_seen.get(key, 0) + 1
            n_top1[key] = n_top1.get(key, 0) + (1 if position == 1 else 0)
            n_topk[key] = n_topk.get(key, 0) + (1 if position is not None else 0)
        per_size[str(size)] = {
            key: {
                "n": n_seen[key],
                "top1_rate": round(n_top1[key] / n_seen[key], 4),
                f"top{top_k}_rate": round(n_topk[key] / n_seen[key], 4),
            }
            for key in sorted(n_seen)
        }
    return {"smoothing": smoothing, "top_k": top_k, "by_shortlist": per_size}


# --- failure figure ----------------------------------------------------------


def _wrap(text: str, width: int = 26) -> str:
    return "\n".join(textwrap.wrap(text, width=width))


def failure_figure(
    rows: list[dict[str, Any]],
    index: IndexHandle,
    out_path: Path,
    *,
    queries_dir: Path,
    limit: int = 12,
) -> dict[str, Any]:
    """Query contour against the true source contour, for every non-ok label.

    The pair is what makes an attribution checkable: a B/C row whose two contours
    overlay tightly is a pipeline loss, while a D row whose query really is a
    different shape is not — and the figure shows which is which.
    """
    from visualize_retrieval import _figure_backend, draw_pair

    bad_all = [row for row in rows if row["label"] not in ("ok", "G3_no_ground_truth")]
    # One panel per distinct case, not one per smoothing repeat: repeating the same
    # refusal six times would fill the figure and teach the reader nothing, while
    # every row of the case stays in results.csv.
    seen: set[tuple[str, str]] = set()
    bad: list[dict[str, Any]] = []
    for row in bad_all:
        key = (str(row["label"]), str(row["query"]))
        if key in seen:
            continue
        seen.add(key)
        bad.append(row)
        if len(bad) >= limit:
            break
    plt = _figure_backend()
    fig, axes = plt.subplots(len(bad), 3, figsize=(9.0, 2.3 * max(len(bad), 1)), squeeze=False)
    for slot, row in enumerate(bad):
        truth = row.get("truth_row")
        path = queries_dir / row["query"]
        try:
            contour = contour_from_sketch(
                path, n_points=index.n_points, smoothing=row["smoothing"]
            )
        except (SketchError, SearchError):
            contour = None
        if contour is not None:
            query = contour.points
        elif path.suffix == ".json":
            # the stroke as drawn, in canvas units — what the extractor was handed
            query, _ = load_sketch_json(path)
        else:
            query = np.zeros((index.n_points, 2))
        draw_pair(axes[slot][0], query, None, fill_query=True)
        axes[slot][0].set_title(
            _wrap(
                f"{row['label']} — {row['query']} ({row['smoothing']})"
                + ("" if contour is not None else " raw stroke, refused")
            ),
            fontsize=6,
        )
        if contour is None and isinstance(truth, int):
            draw_pair(axes[slot][1], index.shapes[truth], None)
            axes[slot][1].set_title(
                _wrap(f"true row {truth}: {short_id(row['query'])}"), fontsize=5.6
            )
            axes[slot][2].axis("off")
            axes[slot][2].text(
                0.0, 0.5, _wrap(str(row.get("refusal", "")), 40), fontsize=5.6, va="center"
            )
            continue
        if isinstance(truth, int):
            draw_pair(axes[slot][1], query, index.shapes[truth])
            axes[slot][1].set_title(
                _wrap(
                    f"true row {truth}: align {row.get('query_vs_source_align')} "
                    f"coarse#{row.get('rank_coarse')} full#{row.get('rank_full_library')} "
                    f"returned#{row.get('rank_final')}"
                ),
                fontsize=5.6,
            )
            top = search_by_contour(
                path,
                index,
                top_k=3,
                smoothing=row["smoothing"],
                allow_mirror=bool(row["allow_mirror"]),
            )
            draw_pair(axes[slot][2], query, index.shapes[top.hits[0].row])
            axes[slot][2].set_title(
                _wrap(
                    f"returned #1 row {top.hits[0].row} d={top.hits[0].align_distance:.4f} "
                    f"{short_id(top.hits[0].candidate_id)}"
                ),
                fontsize=5.6,
            )
        else:
            for column in (1, 2):
                axes[slot][column].axis("off")
            axes[slot][1].text(
                0.0, 0.5, _wrap(str(row.get("refusal", "")), 40), fontsize=5.6, va="center"
            )
    fig.suptitle(
        f"H — failure cases by class: query, the contour it should have found, and what "
        f"was returned instead ({len(bad)} of {len(bad_all)} non-ok rows shown)",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {
        "figure": out_path.name,
        "n_shown": len(bad),
        "n_non_ok_rows": len(bad_all),
        "labels": [row["label"] for row in bad_all],
    }


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--top", type=int, default=TOP_K)
    parser.add_argument("--shortlist", type=int, default=200)
    parser.add_argument("--smoothing", default="light", help="level used for G1 and G3")
    parser.add_argument(
        "--ablation", action="store_true", help="also measure none/light/medium on the library"
    )
    parser.add_argument(
        "--sweep",
        default="100,200,300",
        help="comma-separated coarse shortlist sizes to compare (empty to skip)",
    )
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args(argv)

    index = load_index_handle(args.index)
    manifest = json.loads((args.queries / "manifest.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for entry in manifest["queries"]:
        family = entry["family"]
        perturbation = entry["perturbation"]
        path = args.queries / entry["file"]
        truth = entry.get("source_row")
        if family == "probe" and perturbation in CORRECTNESS:
            layer, levels = "G1_correctness", [args.smoothing]
        elif family == "probe" and perturbation in ROBUSTNESS:
            layer, levels = "G2_robustness", list(SMOOTHING_SIGMA)
        else:
            continue
        for level in levels:
            record = evaluate(
                path,
                index,
                truth=int(truth) if truth is not None else None,
                smoothing=level,
                shortlist_size=args.shortlist,
                top_k=args.top,
            )
            if perturbation == "mirror":
                # The mirror probes are the switch's own test: run both settings
                # and keep them as two rows, so "allowed" and "disallowed" are
                # compared rather than assumed.
                allowed = evaluate(
                    path,
                    index,
                    truth=int(truth) if truth is not None else None,
                    smoothing=level,
                    shortlist_size=args.shortlist,
                    top_k=args.top,
                    allow_mirror=True,
                )
                record["mirror_allowed_rank"] = allowed.get("rank_final")
                record["mirror_allowed_align_of_truth"] = allowed.get("align_distance_of_truth")
            record.update({"layer": layer, "perturbation": perturbation, "params": entry["params"]})
            rows.append(record)
        print(f"{layer} {path.name}: {rows[-1]['label']}")

    for entry in manifest["queries"]:
        if entry["family"] != "sketch":
            continue
        paths = [args.queries / entry["file"]]
        # The same trace as a raster, when the manifest recorded one: an image that
        # must be refused is a result, not a skipped row.
        png = args.queries / entry["file"].replace(".json", ".png")
        if png.name in manifest.get("png_queries", []):
            paths.append(png)
        for path in paths:
            record = evaluate(
                path,
                index,
                truth=None,
                smoothing=args.smoothing,
                shortlist_size=args.shortlist,
                top_k=args.top,
                allow_self_intersection=bool(entry.get("allow_self_intersection")),
            )
            record.update(
                {"layer": "G3_hand_like", "perturbation": entry["perturbation"], "file": path.name}
            )
            rows.append(record)
            print(f"G3_hand_like {path.name}: {record['label']}")
    for name in manifest.get("png_queries", []):
        # Images with no matching trace entry — the two refusal cases.
        path = args.queries / name
        if any(row.get("file") == name for row in rows):
            continue
        record = evaluate(
            path,
            index,
            truth=None,
            smoothing=args.smoothing,
            shortlist_size=args.shortlist,
            top_k=args.top,
        )
        record.update({"layer": "G3_hand_like", "perturbation": "image", "file": name})
        rows.append(record)
        print(f"G3_hand_like {name}: {record['label']}")

    table = ablation_table(index) if args.ablation else []
    sweep = shortlist_sweep(
        index,
        manifest,
        args.queries,
        smoothing=args.smoothing,
        sizes=tuple(int(x) for x in args.sweep.split(",") if x.strip()),
        top_k=args.top,
    )
    summary = summarize(rows, table, {
        "top_k": args.top,
        "shortlist_size": args.shortlist,
        "smoothing_for_g1_g3": args.smoothing,
        "query_manifest_seed": manifest.get("seed"),
        "n_queries": len(manifest["queries"]),
        "index": index.provenance(),
        "weak_distance": WEAK_DISTANCE,
    })
    summary["shortlist_sweep"] = sweep

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "results.csv"
    _write_csv(rows, csv_path)
    json_path = args.out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), "utf-8")
    print(f"wrote {csv_path} ({len(rows)} rows) and {json_path}")

    if not args.no_figures:
        from visualize_retrieval import ablation_figure

        if table:
            figure = args.out_dir / "smoothing_ablation.png"
            ablation_figure(table, figure)
            print(f"wrote {figure}")
        failures = failure_figure(
            rows, index, args.out_dir / "failure_cases.png", queries_dir=args.queries
        )
        summary["failure_figure"] = failures
        json_path.write_text(
            json.dumps(summary, indent=1, ensure_ascii=False, default=str), "utf-8"
        )
        print(f"wrote {args.out_dir / 'failure_cases.png'}")
    return 0


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """One flat row per evaluation; nested values are JSON-encoded, not dropped."""
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
