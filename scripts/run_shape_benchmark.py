"""Run the P0-4.6 independent shape-perception benchmark.

Stages:
    d1  human-rating prediction on H1 (250 rated pairs) and H2 (105 auxiliary pairs)
    d2  exact-target-removal retrieval on MPEG-7 (T0/T1/T2/T3)
    d3  structure stress probes (star / heart / crescent / forked / natural)
    d4  rotation, scale, translation, start-point, deformation and mirror stability
    d5  rotation-search capability added to the S0K port, compared to E0/E1/E3 under
        identical rotation stress (human agreement + retrieval stability)
    d5b re-analysis of d5 (a): the rated stimulus pair, not the 1750 angle-rows, is the
        sampling unit; reads the committed d5 outputs and writes new grouped artefacts

Every stage writes real distances and timings. S0 appears only where the authors published
official coordinates; it is never fabricated for MPEG-7.

Usage:
    python scripts/run_shape_benchmark.py --stage d1
    python scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import rankdata

from earthglyph.retrieval import perceptual_features as e1
from earthglyph.shape_benchmark import metrics
from earthglyph.shape_benchmark.datasets import (
    BenchmarkShape,
    load_h1,
    load_h2,
    load_mpeg7,
)
from earthglyph.shape_benchmark.probes import (
    TRANSFORMS,
    build_structure_probe,
    make_probe,
    rotate,
)
from earthglyph.shape_benchmark.retrieval import (
    _RUN_NETWORK,
    EARTHGLYPH_METHODS,
    SHAPECOMP_RUN_METHODS,
    Gallery,
    kernet_rotation_frames,
    pair_distance,
    search_shape,
)

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "experiments" / "shape_perception_benchmark"
CACHE = BENCH / "cache"
RESULTS = BENCH / "results"
SEED = 20261107
N_BOOT = 2000
# Rigid near-duplicate cutoff for T2, in E0's aligned-distance units.
T2_THRESHOLD = 0.05
D2_QUERIES = ["Heart-1", "fork-1", "bat-1", "tree-1", "apple-1", "butterfly-1"]
D4_TARGETS = ["Heart-1", "fork-1", "bat-1"]
D4_GALLERY_PER_CLASS = 5
# P0-4.6 d5 rotation-study: stress angles that mix on-grid multiples of 15 (15/45/90/135/270)
# with off-grid angles (37/191) the 24-step grid cannot land exactly on, plus 0 as the control.
D5_ANGLES = [0.0, 15.0, 37.0, 45.0, 90.0, 135.0, 191.0, 270.0]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_rankings(path: Path, orders: dict[str, np.ndarray]) -> None:
    """Full rankings, right-padded with -1 so galleries of different sizes fit one array."""
    keys = list(orders)
    lengths = np.array([len(orders[key]) for key in keys], dtype=np.int32)
    width = max((len(order) for order in orders.values()), default=0)
    padded = np.full((len(keys), width), -1, dtype=np.int32)
    for row, key in enumerate(keys):
        padded[row, : len(orders[key])] = orders[key]
    np.savez_compressed(path, keys=np.array(keys), orders=padded, lengths=lengths)


# --------------------------------------------------------------------------- D1


def _agreement(
    observed: metrics.Correlation, boot: dict[str, Any], *, flip: bool
) -> dict[str, Any]:
    """Restate a correlation so positive always means agreement with human dissimilarity."""
    sign = -1.0 if flip else 1.0
    low = float(boot["spearman_ci_high"] if flip else boot["spearman_ci_low"])
    high = float(boot["spearman_ci_low"] if flip else boot["spearman_ci_high"])
    return {
        "spearman": sign * observed.spearman,
        "pearson": sign * observed.pearson,
        "ci_low": sign * low,
        "ci_high": sign * high,
        "bootstrap_scheme": boot["scheme"],
    }


def run_d1(methods: list[str]) -> dict[str, Any]:
    """Predict the published human ratings from each arm's distance on the rated pairs."""
    h1 = load_h1(CACHE)
    h2 = load_h2(CACHE)
    gallery = Gallery(list(h1.shapes.values()), official_coords=h1.official_coords, name="h1")

    rows: list[dict[str, Any]] = []
    distances: dict[str, np.ndarray] = {}
    timings: dict[str, float] = {}
    for method in methods:
        started = time.perf_counter()
        values = np.empty(len(h1.pairs))
        for i, pair in enumerate(h1.pairs):
            if method == "S0":
                values[i] = pair.official_dist
            else:
                values[i] = pair_distance(
                    h1.shapes[pair.left],
                    h1.shapes[pair.right],
                    method,
                    normalizer=gallery.e1_normalizer,
                )
        distances[method] = values
        timings[method] = time.perf_counter() - started
        print(f"  H1 {method}: {len(values)} pairs in {timings[method]:.1f}s", flush=True)

    ratings = h1.pair_matrix()
    mean_rating = ratings.mean(axis=1)
    for i, pair in enumerate(h1.pairs):
        row: dict[str, Any] = {
            "dataset": "H1",
            "pair_id": pair.pair_id,
            "left_id": pair.left,
            "right_id": pair.right,
            "human_mean_similarity": float(mean_rating[i]),
            "human_sd_across_observers": float(ratings[i].std(ddof=1)),
            "human_min": float(ratings[i].min()),
            "human_max": float(ratings[i].max()),
            "published_official_dist": pair.official_dist,
        }
        for method in methods:
            row[f"dist_{method}"] = float(distances[method][i])
        rows.append(row)

    # H2 is auxiliary: its human target is a digitised arrangement, not raw ratings.
    h2_human = h2.human_pair_distance()
    h2_gallery = Gallery(
        [h2.shapes[s] for s in h2.shape_ids], official_coords=h2.official_coords, name="h2"
    )
    h2_pairs = [
        (i, j) for i in range(len(h2.shape_ids)) for j in range(i + 1, len(h2.shape_ids))
    ]
    h2_distances: dict[str, np.ndarray] = {}
    for method in methods:
        started = time.perf_counter()
        if method == "S0":
            values = h2.official_pair_distance()
        else:
            values = np.array(
                [
                    pair_distance(
                        h2.shapes[h2.shape_ids[i]],
                        h2.shapes[h2.shape_ids[j]],
                        method,
                        normalizer=h2_gallery.e1_normalizer,
                    )
                    for i, j in h2_pairs
                ]
            )
        h2_distances[method] = values
        timings[f"H2_{method}"] = time.perf_counter() - started
    for k, (i, j) in enumerate(h2_pairs):
        row = {
            "dataset": "H2",
            "pair_id": k,
            "left_id": h2.shape_ids[i],
            "right_id": h2.shape_ids[j],
            "human_arrangement_distance_normalised": float(h2_human[k]),
        }
        for method in methods:
            row[f"dist_{method}"] = float(h2_distances[method][k])
        rows.append(row)

    write_csv(RESULTS / "pairwise_predictions.csv", rows)

    summary: dict[str, Any] = {
        "seed": SEED,
        "n_boot": N_BOOT,
        "direction_note": (
            "H1 ratings are SIMILARITY (higher = more similar), so a good model gives a "
            "NEGATIVE distance-rating correlation. H2's target is a DISTANCE in a digitised "
            "human arrangement, so a good model gives a POSITIVE correlation. The two are "
            "never pooled."
        ),
        "H1": {"n_pairs": len(h1.pairs), "n_observers": h1.n_observers, "methods": {}},
        "H2": {
            "n_pairs": len(h2_pairs),
            "n_shapes": len(h2.shape_ids),
            "target": "digitised published human MDS arrangement (auxiliary, not raw ratings)",
            "methods": {},
        },
        "timings_seconds": timings,
    }
    for method in methods:
        d = distances[method]
        observed = metrics.correlate(d, mean_rating)
        per_observer = metrics.per_observer_correlations(d, ratings)
        boot_pairs = metrics.bootstrap_correlation(d, mean_rating, n_boot=N_BOOT, seed=SEED)
        boot_two = metrics.bootstrap_two_level(d, ratings, n_boot=N_BOOT, seed=SEED)
        summary["H1"]["methods"][method] = {
            "vs_mean_rating": observed.as_dict(),
            "agreement_pairs_bootstrap": _agreement(observed, boot_pairs, flip=True),
            "agreement_two_level": _agreement(observed, boot_two, flip=True),
            "bootstrap_pairs": boot_pairs,
            "bootstrap_two_level": boot_two,
            "per_observer": per_observer,
            "observer_summary": metrics.observer_summary(per_observer),
        }
        h2_observed = metrics.correlate(h2_distances[method], h2_human)
        h2_boot = metrics.bootstrap_correlation(
            h2_distances[method], h2_human, n_boot=N_BOOT, seed=SEED
        )
        summary["H2"]["methods"][method] = {
            "vs_human_arrangement": h2_observed.as_dict(),
            "agreement_pairs_bootstrap": _agreement(h2_observed, h2_boot, flip=False),
            "bootstrap_pairs": h2_boot,
        }
        print(
            f"  {method}: H1 agreement={-observed.spearman:+.4f} "
            f"[{-boot_two['spearman_ci_high']:+.3f}, {-boot_two['spearman_ci_low']:+.3f}] "
            f"H2 agreement={h2_observed.spearman:+.4f}",
            flush=True,
        )
    summary["H1"]["ranking_by_agreement"] = sorted(
        methods,
        key=lambda m: -float(summary["H1"]["methods"][m]["agreement_two_level"]["spearman"]),
    )
    summary["agreement_convention"] = (
        "Agreement re-states every correlation as distance-vs-human-DISSIMILARITY, so a larger "
        "positive value is always better and H1/H2 are comparable in sign (still not pooled: "
        "the pairs, observers and target semantics differ)."
    )
    summary["H1"]["observer_agreement"] = metrics.observer_agreement(ratings)
    summary["H1"]["paired_differences"] = {
        f"{a}-{b}": metrics.bootstrap_agreement_difference(
            distances[a], distances[b], ratings, n_boot=N_BOOT, seed=SEED
        )
        for a, b in itertools.combinations(methods, 2)
    }

    # Failure cases are selected by a stated rule over ALL 250 pairs, never by eye.
    human_pct = _percentile(-mean_rating)
    failure_rows: list[dict[str, Any]] = []
    for i, pair in enumerate(h1.pairs):
        row = {
            "pair_id": pair.pair_id,
            "left_id": pair.left,
            "right_id": pair.right,
            "human_mean_similarity": float(mean_rating[i]),
            "human_sd_across_observers": float(ratings[i].std(ddof=1)),
            "human_dissimilarity_percentile": float(human_pct[i]),
        }
        for method in methods:
            pct = _percentile(distances[method])
            row[f"dist_percentile_{method}"] = float(pct[i])
            row[f"disagreement_{method}"] = float(pct[i] - human_pct[i])
        failure_rows.append(row)
    reference = "E3" if "E3" in methods else methods[0]
    failure_rows.sort(key=lambda r: -abs(float(r[f"disagreement_{reference}"])))
    summary["H1"]["failure_case_rule"] = (
        f"All {len(failure_rows)} pairs ranked by |distance percentile - human dissimilarity "
        f"percentile| for {reference}; the figure shows the top 6. Positive = the model calls "
        "the pair farther apart than humans do; negative = closer than humans do. "
        "failure_cases.csv keeps every pair, so nothing is hidden by the cut."
    )
    write_csv(RESULTS / "failure_cases.csv", failure_rows)
    write_json(RESULTS / "human_metrics.json", summary)
    return summary


def _percentile(values: np.ndarray) -> np.ndarray:
    """Rank in [0, 1], average ties — a scale-free position, not a distance."""
    ranks: np.ndarray = np.asarray(rankdata(values, method="average"), dtype=float)
    return (ranks - 1.0) / max(len(ranks) - 1, 1)


# --------------------------------------------------------------------------- D2


@dataclass
class RetrievalRow:
    experiment: str
    condition: str
    query_id: str
    method: str
    rank: int
    shape_id: str
    category: str | None
    distance: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "condition": self.condition,
            "query_id": self.query_id,
            "method": self.method,
            "rank": self.rank,
            "shape_id": self.shape_id,
            "category": self.category if self.category is not None else "",
            "distance": self.distance,
        }


def run_d2(methods: list[str], top_k: int) -> dict[str, Any]:
    shapes, excluded_images = load_mpeg7(CACHE)
    gallery = Gallery(shapes, name="mpeg7")
    by_stem = {s.shape_id.removeprefix("mpeg7_"): s for s in shapes}
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    full_orders: dict[str, np.ndarray] = {}
    normalizer_drift: dict[str, Any] = {}
    summary: dict[str, Any] = {
        "gallery_size": len(shapes),
        "gallery_classes": len({s.category for s in shapes}),
        "excluded_images": [{"name": e.name, "reason": e.reason} for e in excluded_images],
        "t2_threshold_e0": T2_THRESHOLD,
        "e1_normalizer": gallery.e1_normalizer.fitted_on,
        "queries": {},
    }

    for stem in D2_QUERIES:
        query = by_stem.get(stem)
        if query is None:
            print(f"  !! query {stem} not in gallery, skipping", flush=True)
            continue
        # E0 defines the rigid near-duplicate set used by T2, identically for every arm.
        e0 = search_shape(query, gallery, "E0", top_k=len(gallery))
        near = [
            gallery.shapes[int(i)].shape_id
            for i in e0.order
            if e0.distances[int(i)] < T2_THRESHOLD
            and gallery.shapes[int(i)].shape_id != query.shape_id
        ]
        for shape_id in near:
            exclusions.append(
                {
                    "query_id": query.shape_id,
                    "condition": "T2",
                    "shape_id": shape_id,
                    "reason": f"E0 aligned distance < {T2_THRESHOLD}",
                    "e0_distance": float(e0.distances[gallery.index_of(shape_id)]),
                }
            )
        distractors = [
            make_probe("circle"),
            make_probe("ellipse"),
            make_probe("simple_curve", seed=11),
        ]
        distractors = [
            BenchmarkShape(
                shape_id=f"{query.shape_id}__t3_{d.shape_id}",
                source="synthetic_distractor",
                points=d.points,
                display_points=d.display_points,
            )
            for d in distractors
        ]
        t3_gallery = Gallery(
            shapes + distractors, name="mpeg7+t3", e1_normalizer=gallery.e1_normalizer
        )
        refit = e1.fit_normalizer(
            t3_gallery.e1_features,
            method="minmax",
            names=e1.feature_names(),
            fitted_on="mpeg7+t3 refit (sensitivity only, not used for ranking)",
        )
        normalizer_drift[stem] = {
            "n_descriptors": int(refit.n_descriptors),
            "n_changed": int(
                np.count_nonzero(
                    ~np.isclose(refit.center, gallery.e1_normalizer.center)
                    | ~np.isclose(refit.scale, gallery.e1_normalizer.scale)
                )
            ),
            "max_relative_scale_shift": float(
                np.max(
                    np.abs(refit.scale - gallery.e1_normalizer.scale)
                    / np.maximum(np.abs(gallery.e1_normalizer.scale), 1e-12)
                )
            ),
            "note": (
                "T3 inherits the T0/T1/T2 normalizer so adding distractors is the only variable. "
                "This row quantifies what a refit would have changed instead."
            ),
        }

        conditions = {
            "T0": (gallery, []),
            "T1": (gallery, [query.shape_id]),
            "T2": (gallery, [query.shape_id, *near]),
            "T3": (t3_gallery, [query.shape_id, *near]),
        }
        per_query: dict[str, Any] = {"category": query.category, "t2_near_duplicates": near}
        for condition, (gal, drop) in conditions.items():
            per_condition: dict[str, Any] = {}
            for method in methods:
                result = search_shape(query, gal, method, top_k=top_k, exclude=drop)
                key = f"{stem}|{condition}|{method}"
                full_orders[key] = result.order
                for hit in result.hits:
                    rows.append(
                        RetrievalRow(
                            "d2", condition, query.shape_id, method, hit.rank,
                            hit.shape_id, hit.category, hit.distance,
                        ).as_dict()
                    )
                categories = [gal.shapes[int(i)].category for i in result.order]
                recall = (
                    metrics.class_recall_at_k(categories, query.category, top_k)
                    if query.category
                    else None
                )
                distractor_hits = [
                    h.shape_id for h in result.hits if h.source == "synthetic_distractor"
                ]
                per_condition[method] = {
                    "candidates": int(len(result.order)),
                    "class_recall_at_10": recall,
                    "top1": result.hits[0].shape_id if result.hits else None,
                    "top1_category": result.hits[0].category if result.hits else None,
                    "synthetic_distractors_in_top10": distractor_hits,
                    "elapsed_seconds": result.elapsed_seconds,
                    "self_rank": result.rank_of(query.shape_id, gal),
                }
            per_query[condition] = per_condition
            print(
                f"  {stem} {condition}: "
                + ", ".join(
                    f"{m} recall={per_condition[m]['class_recall_at_10']}"
                    f" ({per_condition[m]['elapsed_seconds']:.1f}s)"
                    for m in methods
                ),
                flush=True,
            )
        summary["queries"][stem] = per_query

    write_csv(RESULTS / "retrieval_results.csv", rows)
    write_csv(RESULTS / "excluded_candidates.csv", exclusions)
    write_rankings(RESULTS / "d2_full_rankings.npz", full_orders)
    summary["e1_normalizer_drift_if_refitted"] = normalizer_drift
    write_json(RESULTS / "d2_metrics.json", summary)
    return summary


# --------------------------------------------------------------------------- D3


def run_d3(methods: list[str], top_k: int) -> dict[str, Any]:
    shapes, _ = load_mpeg7(CACHE)
    by_stem = {s.shape_id.removeprefix("mpeg7_"): s for s in shapes}
    natural_source = by_stem.get("bat-1")
    base = Gallery(shapes, name="mpeg7")
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "note": (
            "Designed structural probes. The expected ordering (structural variant above gross "
            "distractor) is a geometric hypothesis, NOT validated human truth."
        ),
        "e1_normalizer": base.e1_normalizer.fitted_on,
        "e1_normalizer_policy": (
            "Every probe gallery inherits the plain MPEG-7 normalizer, so E1 distances are "
            "comparable across probe families and the added probes are the only variable."
        ),
        "natural_family_asymmetry": (
            "For 'natural' the query is a real MPEG-7 outline that stays in the gallery, so "
            "rank 1 is the exact original. For the four synthetic families the query itself is "
            "absent and only its three variants are present."
        ),
        "probes": {},
    }

    for kind in ["star", "heart", "crescent", "forked", "natural"]:
        probe = build_structure_probe(kind, natural=natural_source)
        candidates = shapes + probe.structural_variants + probe.gross_distractors
        gallery = Gallery(
            candidates, name=f"mpeg7+{kind}", e1_normalizer=base.e1_normalizer
        )
        variant_ids = {v.shape_id for v in probe.structural_variants}
        distractor_ids = {d.shape_id for d in probe.gross_distractors}
        per_probe: dict[str, Any] = {}
        for method in methods:
            result = search_shape(probe.query, gallery, method, top_k=top_k)
            for hit in result.hits:
                role = (
                    "structural_variant" if hit.shape_id in variant_ids
                    else "gross_distractor" if hit.shape_id in distractor_ids
                    else "gallery"
                )
                row = RetrievalRow(
                    "d3", kind, probe.query.shape_id, method, hit.rank,
                    hit.shape_id, hit.category, hit.distance,
                ).as_dict()
                row["role"] = role
                rows.append(row)
            variant_ranks = [
                result.rank_of(v.shape_id, gallery) for v in probe.structural_variants
            ]
            distractor_ranks = [
                result.rank_of(d.shape_id, gallery) for d in probe.gross_distractors
            ]
            best_variant = min((r for r in variant_ranks if r is not None), default=None)
            best_distractor = min((r for r in distractor_ranks if r is not None), default=None)
            per_probe[method] = {
                "structural_variant_ranks": variant_ranks,
                "gross_distractor_ranks": distractor_ranks,
                "best_structural_variant_rank": best_variant,
                "best_gross_distractor_rank": best_distractor,
                "structure_beats_gross": (
                    best_variant is not None
                    and best_distractor is not None
                    and best_variant < best_distractor
                ),
                "variants_in_top10": sum(
                    1 for r in variant_ranks if r is not None and r <= top_k
                ),
                "distractors_in_top10": sum(
                    1 for r in distractor_ranks if r is not None and r <= top_k
                ),
                "elapsed_seconds": result.elapsed_seconds,
            }
        summary["probes"][kind] = per_probe
        print(
            f"  {kind}: "
            + ", ".join(
                f"{method} variant={per_probe[method]['best_structural_variant_rank']}"
                f" distractor={per_probe[method]['best_gross_distractor_rank']}"
                f" ok={per_probe[method]['structure_beats_gross']}"
                for method in methods
            ),
            flush=True,
        )
    write_csv(RESULTS / "structure_probe_results.csv", rows)
    write_json(RESULTS / "d3_metrics.json", summary)
    return summary


# --------------------------------------------------------------------------- D4


def run_d4(methods: list[str], top_k: int) -> dict[str, Any]:
    shapes, _ = load_mpeg7(CACHE)
    per_class: dict[str, list[BenchmarkShape]] = {}
    for shape in shapes:
        per_class.setdefault(str(shape.category), []).append(shape)
    subset = [
        members[i]
        for _, members in sorted(per_class.items())
        for i in range(min(D4_GALLERY_PER_CLASS, len(members)))
    ]
    gallery = Gallery(subset, name="mpeg7_stratified")
    by_stem = {s.shape_id.removeprefix("mpeg7_"): s for s in subset}
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "gallery_size": len(subset),
        "gallery_per_class": D4_GALLERY_PER_CLASS,
        "note": (
            "A stratified subset is used for the transform sweep so every arm scores the same "
            "candidate set at tractable cost. Stability is measured against the untransformed "
            "query on this same gallery. E1's normalizer is fitted on this subset, so its "
            "absolute distances are not comparable to D2's; only within-D4 ranks are used."
        ),
        "e1_normalizer": gallery.e1_normalizer.fitted_on,
        "targets": {},
    }
    for stem in D4_TARGETS:
        base = by_stem.get(stem)
        if base is None:
            print(f"  !! {stem} not in stratified subset, skipping", flush=True)
            continue
        per_target: dict[str, Any] = {}
        baseline = {
            method: search_shape(base, gallery, method, top_k=top_k) for method in methods
        }
        for name, transform in TRANSFORMS.items():
            points = np.asarray(transform(base.points), dtype=float)
            transformed = BenchmarkShape(
                shape_id=f"{base.shape_id}__{name}",
                source="transform",
                points=points,
                display_points=points,
            )
            per_transform: dict[str, Any] = {}
            for method in methods:
                result = search_shape(transformed, gallery, method, top_k=top_k)
                ref = baseline[method]
                original_rank = result.rank_of(base.shape_id, gallery)
                for hit in result.hits:
                    rows.append(
                        RetrievalRow(
                            "d4", name, transformed.shape_id, method, hit.rank,
                            hit.shape_id, hit.category, hit.distance,
                        ).as_dict()
                    )
                per_transform[method] = {
                    "original_target_rank": original_rank,
                    "top1": result.hits[0].shape_id if result.hits else None,
                    "top_k_overlap_with_untransformed": metrics.top_k_overlap(
                        ref.order, result.order, top_k
                    ),
                    "elapsed_seconds": result.elapsed_seconds,
                }
            per_target[name] = per_transform
            print(
                f"  {stem} {name}: "
                + ", ".join(
                    f"{m} rank={per_transform[m]['original_target_rank']}"
                    f" overlap={per_transform[m]['top_k_overlap_with_untransformed']:.2f}"
                    for m in methods
                ),
                flush=True,
            )
        summary["targets"][stem] = per_target
    write_csv(RESULTS / "transform_results.csv", rows)
    write_json(RESULTS / "d4_metrics.json", summary)
    return summary


# --------------------------------------------------------------------------- D5


def _rotated(shape: BenchmarkShape, degrees: float) -> BenchmarkShape:
    """A copy of ``shape`` turned by ``degrees``, with BOTH contour contracts rotated.

    E0/E1/E3 read ``points`` (the shared 128-point ring); the S0K arms read
    ``display_points`` (the original published contour). Rotating both keeps every arm
    facing the identical rotated stimulus, which is the whole point of a same-condition test.
    """
    return replace(
        shape,
        points=rotate(shape.points, degrees),
        display_points=rotate(shape.display_points, degrees),
    )


def _d5_arms(methods: list[str]) -> list[tuple[str, str, bool]]:
    """(label, method, rotation_search) triples that put every arm on the same footing.

    S0K arms appear twice (naive and with the added rotation search); E0/E1/E3 appear once
    because rotation is already handled by construction (E0/E1) or by their own E3 sample
    grid, so the flag is inert for them.
    """
    arms: list[tuple[str, str, bool]] = []
    for method in methods:
        if method in SHAPECOMP_RUN_METHODS:
            arms.append((method, method, False))
            arms.append((f"{method}+rot", method, True))
        else:
            arms.append((method, method, False))
    return arms


def run_d5(methods: list[str], top_k: int) -> dict[str, Any]:
    """Add rotation-search to the ShapeComp port and compare it under identical rotation.

    Two same-condition studies, with rotation as the only variable:

    * **(a) rotation-stress human agreement (H1)** — one member of every rated pair is
      turned by each stress angle; each arm must recover the human dissimilarity it was
      scored on regardless of that rotation. Ground truth is the published ratings, shared
      by all arms.
    * **(b) rotation-stress retrieval (MPEG-7 stratified)** — the query is turned by each
      angle and ranked against the D4 subset; stability is the Top-10 overlap with the
      untransformed ranking and the target's own rank.

    The S0K+rot score is the same arithmetic ``pair_distance``/``search_shape`` perform, just
    cached: a query-side grid min over the network's rotated frames, with the official input
    contract untouched.
    """
    from earthglyph.shape_benchmark.retrieval import KERNET_ROTATION_STEPS

    arms = _d5_arms([m for m in methods if m != "S0"])
    angles = [a for a in D5_ANGLES]

    # --- shared cache for the S0K network frames (row 0 is the plain no-search score)
    frames_cache: dict[tuple[str, str], np.ndarray] = {}
    coord_cache: dict[tuple[str, str, float], np.ndarray] = {}

    def frames(network: str, shape: BenchmarkShape) -> np.ndarray:
        key = (network, shape.shape_id)
        if key not in frames_cache:
            frames_cache[key] = kernet_rotation_frames(network, shape.display_points)
        return frames_cache[key]

    def coord(network: str, shape: BenchmarkShape, degrees: float) -> np.ndarray:
        key = (network, shape.shape_id, degrees)
        if key not in coord_cache:
            rotated = _rotated(shape, degrees)
            coord_cache[key] = kernet_rotation_frames(network, rotated.display_points)[0]
        return coord_cache[key]

    def s0k_pair_distance(
        left: BenchmarkShape, right: BenchmarkShape, method: str, rotation_search: bool, phi: float
    ) -> float:
        left_frames = frames(_RUN_NETWORK[method], left)
        right_coord = coord(_RUN_NETWORK[method], right, phi)
        deltas = np.linalg.norm(left_frames - right_coord, axis=1)
        return float(deltas.min()) if rotation_search else float(deltas[0])

    # ---------------------------------------------------------------- (a) H1 agreement
    h1 = load_h1(CACHE)
    h1_gallery = Gallery(list(h1.shapes.values()), official_coords=h1.official_coords, name="h1")
    ratings = h1.pair_matrix()
    mean_rating = ratings.mean(axis=1)

    curve: dict[str, dict[str, float]] = {label: {} for label, _, _ in arms}
    pooled_dists: dict[str, list[float]] = {label: [] for label, _, _ in arms}
    a_rows: list[dict[str, Any]] = []
    for label, method, rot in arms:
        for phi in angles:
            values = np.empty(len(h1.pairs))
            for i, pair in enumerate(h1.pairs):
                left = h1.shapes[pair.left]
                right = h1.shapes[pair.right]
                if method in SHAPECOMP_RUN_METHODS:
                    values[i] = s0k_pair_distance(left, right, method, rot, phi)
                else:
                    values[i] = pair_distance(
                        left,
                        _rotated(right, phi),
                        method,
                        normalizer=h1_gallery.e1_normalizer,
                    )
                a_rows.append(
                    {
                        "study": "agreement",
                        "arm": label,
                        "method": method,
                        "rotation_search": rot,
                        "stress_deg": phi,
                        "pair_id": pair.pair_id,
                        "model_distance": float(values[i]),
                        "human_mean_similarity": float(mean_rating[i]),
                    }
                )
            obs = metrics.correlate(values, mean_rating)
            curve[label][f"rotate_{phi:g}"] = -float(obs.spearman)
            if phi != 0.0:
                pooled_dists[label].extend(values.tolist())
        line = " ".join(f"{k}={v:+.3f}" for k, v in curve[label].items())
        print(f"  (a) {label}: {line}", flush=True)

    # Pooled agreement over every rotated pair (rotation stress averaged across the sweep),
    # with a two-level bootstrap so pairs AND observers are resampled.
    stress_matrix = np.tile(ratings, (len(angles) - 1, 1))
    headline_a: dict[str, Any] = {}
    for label, _, _ in arms:
        d = np.array(pooled_dists[label], dtype=float)
        obs = metrics.correlate(d, stress_matrix.mean(axis=1))
        boot = metrics.bootstrap_two_level(d, stress_matrix, n_boot=N_BOOT, seed=SEED)
        headline_a[label] = {
            "agreement_pooled": -float(obs.spearman),
            "ci_low": -float(boot["spearman_ci_high"]),
            "ci_high": -float(boot["spearman_ci_low"]),
            "n_rows": int(len(d)),
        }

    # ---------------------------------------------------------------- (b) MPEG-7 retrieval
    shapes, _ = load_mpeg7(CACHE)
    per_class: dict[str, list[BenchmarkShape]] = {}
    for shape in shapes:
        per_class.setdefault(str(shape.category), []).append(shape)
    subset = [
        members[i]
        for _, members in sorted(per_class.items())
        for i in range(min(D4_GALLERY_PER_CLASS, len(members)))
    ]
    retrieval_gallery = Gallery(subset, name="mpeg7_stratified")
    by_stem = {s.shape_id.removeprefix("mpeg7_"): s for s in subset}
    b_rows: list[dict[str, Any]] = []
    # overlap[(label, angle)] collects the Top-10 overlap vs the untransformed query, one per
    # target stem; target_rank[(label, stem, angle)] records where the exact target landed.
    overlap: dict[tuple[str, float], list[float]] = {}
    target_rank: dict[tuple[str, str, float], int | None] = {}
    for stem in D4_TARGETS:
        base = by_stem.get(stem)
        if base is None:
            print(f"  !! {stem} not in stratified subset, skipping", flush=True)
            continue
        untransformed = {
            label: search_shape(base, retrieval_gallery, method, top_k=top_k, rotation_search=rot)
            for label, method, rot in arms
        }
        for phi in angles:
            query = _rotated(base, phi)
            for label, method, rot in arms:
                result = search_shape(
                    query, retrieval_gallery, method, top_k=top_k, rotation_search=rot
                )
                ref = untransformed[label]
                original_rank = result.rank_of(base.shape_id, retrieval_gallery)
                k_overlap = metrics.top_k_overlap(ref.order, result.order, top_k)
                overlap.setdefault((label, phi), []).append(k_overlap)
                target_rank[(label, stem, phi)] = original_rank
                for hit in result.hits:
                    b_rows.append(
                        RetrievalRow(
                            "d5", f"rotate_{phi:g}", query.shape_id, label, hit.rank,
                            hit.shape_id, hit.category, hit.distance,
                        ).as_dict()
                    )
            print(
                f"  (b) {stem} rotate_{phi:g}: "
                + ", ".join(
                    f"{label} r={target_rank[(label, stem, phi)]}" for label, _, _ in arms
                ),
                flush=True,
            )
    # Per-arm Top-10 overlap vs the untransformed query, averaged over the D4 targets, per
    # angle; and the mean over the rotated (non-control) angles as the headline robustness.
    overlap_curve: dict[str, dict[str, float]] = {}
    robustness: dict[str, dict[str, float]] = {}
    for label, _, _ in arms:
        per_angle = {f"rotate_{phi:g}": float(np.mean(overlap[(label, phi)])) for phi in angles}
        overlap_curve[label] = per_angle
        rotated_vals = [per_angle[f"rotate_{phi:g}"] for phi in angles if phi != 0.0]
        control = per_angle["rotate_0"]
        robustness[label] = {
            "mean_overlap_rotated": float(np.mean(rotated_vals)),
            "worst_overlap_rotated": float(np.min(rotated_vals)),
            "unrotated_control_overlap": control,
        }

    summary = {
        "seed": SEED,
        "n_boot": N_BOOT,
        "angles_deg": angles,
        "rotation_step_deg": 360.0 / float(KERNET_ROTATION_STEPS),
        "rotations_searched": KERNET_ROTATION_STEPS,
        "note": (
            "Rotation is the only variable. (a) turns one member of each rated H1 pair; a "
            "rotation-robust arm keeps its agreement with the published human dissimilarity. "
            "(b) turns each retrieval query against the D4 stratified gallery; a robust arm "
            "keeps the Top-10 it returned unrotated. S0K+rot wraps the official network in the "
            "query-side grid search; the official input contract is unchanged and the network "
            "weights are the authors' own."
        ),
        "agreement_curve_by_arm": curve,
        "agreement_pooled_by_arm": headline_a,
        "retrieval_overlap_curve_by_arm": overlap_curve,
        "retrieval_robustness_by_arm": robustness,
        "retrieval_target_rank": {
            f"{label}|{stem}@{phi:g}": value for (label, stem, phi), value in target_rank.items()
        },
        "rows_written": {"agreement": len(a_rows), "retrieval": len(b_rows)},
    }
    write_csv(RESULTS / "d5_rotation_results.csv", a_rows + b_rows)
    write_json(RESULTS / "d5_rotation_metrics.json", summary)
    return summary


def _file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_d5_grouped(n_boot: int = N_BOOT, seed: int = SEED) -> dict[str, Any]:
    """CP7-B: re-infer the D5 rotation sweep with the *stimulus pair* as the sampling unit.

    ``run_d5`` stacked its seven rotated angles into 1750 rows and handed them to
    ``bootstrap_two_level``, which resamples rows as though each were an independent stimulus.
    They are not independent: 1750 rows are 250 rated pairs seen seven times, judged by one
    shared rating panel, and the angles are a pre-declared design set rather than a sample.
    This stage keeps every committed point estimate as a descriptive number and re-derives only
    the uncertainty — whole pairs are resampled (all angles of a drawn pair move together), the
    observer panel is resampled at its own level, and one shared replicate stream scores all
    seven arms so the arm-vs-arm differences are properly paired.

    Nothing is re-ranked or re-scored here: the distances are read back from
    ``d5_rotation_results.csv`` and the stage refuses to continue unless they reproduce the
    committed pooled values exactly. The committed files are only ever read, so this is a new
    version with an explicit audit relation, not an overwrite.
    """
    src_csv = RESULTS / "d5_rotation_results.csv"
    src_json = RESULTS / "d5_rotation_metrics.json"
    if not src_csv.exists() or not src_json.exists():
        raise RuntimeError(
            "d5b re-analyses the committed d5 outputs; run "
            "`python scripts/run_shape_benchmark.py --stage d5 --methods E0 E1 E3 S0K1 S0K2` first"
        )
    source = read_json(src_json)
    committed = source["agreement_pooled_by_arm"]
    arms = list(committed)
    angles = [float(a) for a in source["angles_deg"] if float(a) != 0.0]

    h1 = load_h1(CACHE)
    ratings = h1.pair_matrix()
    slot = {pair.pair_id: index for index, pair in enumerate(h1.pairs)}
    with src_csv.open(newline="", encoding="utf-8") as handle:
        agreement_rows = [
            row
            for row in csv.DictReader(handle)
            if row["study"] == "agreement" and float(row["stress_deg"]) != 0.0
        ]

    dist: dict[str, np.ndarray] = {}
    group_ids: np.ndarray | None = None
    condition_ids: np.ndarray | None = None
    expected = len(angles) * len(h1.pairs)
    for label in arms:
        ordered = sorted(
            (row for row in agreement_rows if row["arm"] == label),
            key=lambda row: (float(row["stress_deg"]), slot[int(row["pair_id"])]),
        )
        if len(ordered) != expected:
            raise RuntimeError(f"d5b: arm {label} has {len(ordered)} rotated rows, want {expected}")
        groups = np.array([slot[int(row["pair_id"])] for row in ordered])
        conditions = np.array([float(row["stress_deg"]) for row in ordered])
        if group_ids is None:
            group_ids, condition_ids = groups, conditions
        elif not (
            np.array_equal(groups, group_ids) and np.array_equal(conditions, condition_ids)
        ):
            raise RuntimeError(f"d5b: arm {label} does not cover the same rows as {arms[0]}")
        dist[label] = np.array([float(row["model_distance"]) for row in ordered])

    # --- reproduction gate: the committed numbers must come out of the committed rows
    stress_matrix = np.tile(ratings, (len(angles), 1))
    mean_pooled = stress_matrix.mean(axis=1)
    naive: dict[str, dict[str, float]] = {}
    deviations: list[float] = []
    for label in arms:
        ref = committed[label]
        obs = metrics.correlate(dist[label], mean_pooled)
        boot = metrics.bootstrap_two_level(dist[label], stress_matrix, n_boot=n_boot, seed=seed)
        point = -float(obs.spearman)
        low = -float(boot["spearman_ci_high"])
        high = -float(boot["spearman_ci_low"])
        naive[label] = {"agreement_pooled": point, "ci_low": low, "ci_high": high}
        deviations.extend(
            abs(got - want)
            for got, want in (
                (point, ref["agreement_pooled"]),
                (low, ref["ci_low"]),
                (high, ref["ci_high"]),
            )
        )
    max_deviation = max(deviations)
    if max_deviation > 1e-6:
        raise RuntimeError(
            f"d5b could not reproduce the committed d5 values (max deviation {max_deviation:.3e}); "
            "the stored CSV and JSON disagree, so this re-analysis is not comparable"
        )

    # the rating panel is tiled to the same 1750 rows as the distances; group_ids says which of
    # the 250 rated pairs each row really belongs to, and that is the unit being resampled
    fixed = metrics.bootstrap_grouped_two_level(
        dist, stress_matrix, group_ids, n_boot=n_boot, seed=seed
    )
    randomised = metrics.bootstrap_grouped_two_level(
        dist,
        stress_matrix,
        group_ids,
        condition_ids=condition_ids,
        resample_conditions=True,
        n_boot=n_boot,
        seed=seed,
    )

    def interval(block: dict[str, Any], label: str) -> dict[str, Any]:
        entry = block["per_arm"][label]
        return {
            "ci_low": -float(entry["spearman_ci_high"]),
            "ci_high": -float(entry["spearman_ci_low"]),
            "boot_mean": -float(entry["spearman_boot_mean"]),
            "boot_std": float(entry["spearman_boot_std"]),
            "valid_replicates": int(entry["valid_replicates"]),
        }

    def delta(block: dict[str, Any], key: str) -> dict[str, Any]:
        raw = block["differences"][key]
        return {
            "delta_agreement": -float(raw["mean"]),
            "std": float(raw["std"]),
            "ci_low": -float(raw["ci_high"]),
            "ci_high": -float(raw["ci_low"]),
            "excludes_zero": bool(raw["excludes_zero"]),
            "n_valid_replicates": int(raw["n_valid_replicates"]),
        }

    per_arm: dict[str, Any] = {}
    for label in arms:
        grouped = interval(fixed, label)
        sensitivity = interval(randomised, label)
        width_naive = naive[label]["ci_high"] - naive[label]["ci_low"]
        width_grouped = grouped["ci_high"] - grouped["ci_low"]
        per_arm[label] = {
            "agreement_pooled": naive[label]["agreement_pooled"],
            "naive_ci_low": naive[label]["ci_low"],
            "naive_ci_high": naive[label]["ci_high"],
            "naive_ci_width": width_naive,
            "grouped_ci_low": grouped["ci_low"],
            "grouped_ci_high": grouped["ci_high"],
            "grouped_ci_width": width_grouped,
            "width_ratio_grouped_over_naive": width_grouped / width_naive,
            "grouped_boot_mean": grouped["boot_mean"],
            "grouped_boot_std": grouped["boot_std"],
            "angle_randomised_ci_low": sensitivity["ci_low"],
            "angle_randomised_ci_high": sensitivity["ci_high"],
            "angle_randomised_ci_width": sensitivity["ci_high"] - sensitivity["ci_low"],
            "point_estimate_changed": False,
        }

    differences: dict[str, Any] = {}
    for first, second in itertools.combinations(arms, 2):
        key = f"{first}|{second}"
        if key not in fixed["differences"]:
            continue
        differences[key] = {
            "grouped_fixed_angle": delta(fixed, key),
            "angle_randomised": delta(randomised, key),
            "naive_intervals_overlap": bool(
                naive[first]["ci_low"] <= naive[second]["ci_high"]
                and naive[second]["ci_low"] <= naive[first]["ci_high"]
            ),
        }

    summary = {
        "schema": "earthglyph.shape_perception_benchmark/d5_grouped/1",
        "study": "CP7-B re-analysis of the d5 rotation-stress agreement (H1)",
        "sampling_unit": (
            f"the rated stimulus pair ({fixed['n_clusters']} pairs), not the row; "
            f"{fixed['n_rows']} rows = {fixed['n_clusters']} pairs x {len(angles)} rotated angles "
            "and resampling rows independently understates the interval"
        ),
        "angle_factor": fixed["condition_factor"],
        "angle_factor_sensitivity": randomised["condition_factor"],
        "metric": "Spearman of model distance vs human similarity, restated positive",
        "reproduction": {
            "n_boot": n_boot,
            "seed": seed,
            "max_abs_deviation_vs_committed": max_deviation,
            "tolerance": 1e-6,
            "passed": True,
            "note": (
                "the naive (row-as-independent) bootstrap re-run from the stored CSV reproduces "
                "every committed d5 point estimate and CI, so the grouped numbers below are "
                "directly comparable to them"
            ),
        },
        "audit_relation": {
            "read_only_sources": [
                {
                    "path": path.relative_to(REPO).as_posix(),
                    "sha256": _file_sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path in (src_csv, src_json)
            ],
            "written_by_this_stage": [
                (RESULTS / "d5_grouped_results.csv").relative_to(REPO).as_posix(),
                (RESULTS / "d5_grouped_metrics.json").relative_to(REPO).as_posix(),
            ],
            "d5_outputs_modified": False,
        },
        "assumptions": [
            (
                "rotating one member of a pair does not change how the *human observers* rated "
                "that pair, so the published rating is reused for every angle; this is an "
                "assumption carried over from d5, not new human data — no rotated pair was ever "
                "re-rated"
            ),
            (
                "the seven rotated angles are a fixed design set (headline); treating them as a "
                "random sample of angles widens the intervals further and is reported as a "
                "sensitivity, not as the headline"
            ),
            (
                "the rating panel is exchangeable across stimuli, so one observer draw is applied "
                "per replicate for every arm"
            ),
        ],
        "n_boot": n_boot,
        "seed": seed,
        "n_clusters": fixed["n_clusters"],
        "rows_per_cluster": fixed["rows_per_cluster"],
        "n_rows": fixed["n_rows"],
        "angles_deg": angles,
        "by_arm": per_arm,
        "paired_differences": differences,
        "differences_supported": sorted(
            key
            for key, entry in differences.items()
            if entry["grouped_fixed_angle"]["excludes_zero"]
        ),
    }
    write_csv(RESULTS / "d5_grouped_results.csv", _d5_grouped_rows(summary, fixed, randomised))
    write_json(RESULTS / "d5_grouped_metrics.json", summary)
    print(
        "  reproduction max abs deviation vs committed d5 = "
        f"{max_deviation:.3e} (tolerance 1e-6)",
        flush=True,
    )
    for label in arms:
        entry = per_arm[label]
        print(
            f"  {label}: {entry['agreement_pooled']:+.4f} "
            f"naive [{entry['naive_ci_low']:+.4f},{entry['naive_ci_high']:+.4f}] "
            f"grouped [{entry['grouped_ci_low']:+.4f},{entry['grouped_ci_high']:+.4f}] "
            f"x{entry['width_ratio_grouped_over_naive']:.2f} wide",
            flush=True,
        )
    return summary


def _d5_grouped_rows(
    summary: dict[str, Any],
    fixed: dict[str, Any],
    randomised: dict[str, Any],
) -> list[dict[str, Any]]:
    """Tidy table for d5_grouped_results.csv: one row per interval, arm-level then paired."""
    source_sha = summary["audit_relation"]["read_only_sources"][0]["sha256"]
    rows: list[dict[str, Any]] = []
    for label, entry in summary["by_arm"].items():
        schemes = (
            ("naive_rows_as_independent", entry["naive_ci_low"], entry["naive_ci_high"],
             fixed["n_rows"]),
            ("grouped_pairs_fixed_angle", entry["grouped_ci_low"], entry["grouped_ci_high"],
             fixed["n_clusters"]),
            ("grouped_pairs_angle_randomised", entry["angle_randomised_ci_low"],
             entry["angle_randomised_ci_high"], randomised["n_clusters"]),
        )
        for scheme, low, high, unit_rows in schemes:
            rows.append({
                "record_type": "arm_interval",
                "scheme": scheme,
                "arm": label,
                "other_arm": "",
                "point_or_delta": entry["agreement_pooled"],
                "ci_low": low,
                "ci_high": high,
                "ci_width": high - low,
                "excludes_zero": "",
                "n_valid_replicates": "",
                "sampling_unit_rows": unit_rows,
                "d5_csv_sha256": source_sha,
            })
    for key, entry in summary["paired_differences"].items():
        first, second = key.split("|", 1)
        for scheme, block in (
            ("grouped_pairs_fixed_angle", entry["grouped_fixed_angle"]),
            ("grouped_pairs_angle_randomised", entry["angle_randomised"]),
        ):
            rows.append({
                "record_type": "paired_difference",
                "scheme": scheme,
                "arm": first,
                "other_arm": second,
                "point_or_delta": block["delta_agreement"],
                "ci_low": block["ci_low"],
                "ci_high": block["ci_high"],
                "ci_width": block["ci_high"] - block["ci_low"],
                "excludes_zero": block["excludes_zero"],
                "n_valid_replicates": block["n_valid_replicates"],
                "sampling_unit_rows": fixed["n_clusters"],
                "d5_csv_sha256": source_sha,
            })
    return rows


def write_summary() -> None:
    """Combine whatever stages have run into one headline table with full provenance."""
    import platform
    import subprocess

    stages = {name: RESULTS / f"{name}_metrics.json" for name in ("d2", "d3", "d4")}
    stages["d1"] = RESULTS / "human_metrics.json"
    stages["d5"] = RESULTS / "d5_rotation_metrics.json"
    stages["d5b"] = RESULTS / "d5_grouped_metrics.json"
    present = {name: read_json(path) for name, path in stages.items() if path.exists()}
    manifest_path = BENCH / "dataset_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"

    headline: dict[str, Any] = {}
    if "d1" in present:
        headline["human_rating_prediction"] = {
            "dataset": "H1 (250 pairs x 14 observers, Zenodo 4730985)",
            "metric": "Spearman, restated so positive = agreement with human dissimilarity",
            "ci": "two-level bootstrap, pairs AND observers, 2000 replicates",
            "by_method": {
                method: {
                    "agreement": values["agreement_two_level"]["spearman"],
                    "ci_low": values["agreement_two_level"]["ci_low"],
                    "ci_high": values["agreement_two_level"]["ci_high"],
                    "per_observer_median_abs": abs(
                        values["observer_summary"]["spearman_median"]
                    ),
                }
                for method, values in present["d1"]["H1"]["methods"].items()
            },
            "single_observer_noise_ceiling_median": (
                present["d1"]["H1"]["observer_agreement"]["leave_one_out_summary"]["median"]
            ),
            "paired_differences": {
                name: {
                    "observed": values["observed_difference"],
                    "ci_low": values["spearman_ci_low"],
                    "ci_high": values["spearman_ci_high"],
                    "excludes_zero": values["excludes_zero"],
                }
                for name, values in present["d1"]["H1"]["paired_differences"].items()
            },
            "auxiliary_H2_by_method": {
                method: values["agreement_pairs_bootstrap"]["spearman"]
                for method, values in present["d1"]["H2"]["methods"].items()
            },
        }
    if "d2" in present:
        d2 = present["d2"]
        conditions = ("T0", "T1", "T2", "T3")
        by_condition: dict[str, Any] = {}
        for condition in conditions:
            arms = {}
            for method in (*EARTHGLYPH_METHODS, *SHAPECOMP_RUN_METHODS):
                rows = [
                    (query["category"], query[condition][method])
                    for query in d2["queries"].values()
                    if condition in query and method in query[condition]
                ]
                if not rows:
                    continue
                arms[method] = {
                    "n_queries": len(rows),
                    "mean_class_recall_at_10": float(
                        np.mean([row["class_recall_at_10"] for _, row in rows])
                    ),
                    "top1_same_category_count": sum(
                        row["top1_category"] == category for category, row in rows
                    ),
                    "queries_with_synthetic_distractor_in_top10": sum(
                        bool(row["synthetic_distractors_in_top10"]) for _, row in rows
                    ),
                    "mean_candidates": float(np.mean([row["candidates"] for _, row in rows])),
                    "mean_query_seconds": float(
                        np.mean([row["elapsed_seconds"] for _, row in rows])
                    ),
                }
            by_condition[condition] = arms
        headline["target_removal_retrieval"] = {
            "gallery": f"MPEG-7 CE-Shape-1, {d2['gallery_size']} shapes / "
            f"{d2['gallery_classes']} categories",
            "caveat": (
                "class_recall@10 uses MPEG-7 object CATEGORIES, not human similarity. "
                "T2/T3 are the conditions of interest; T0/T1 are controls."
            ),
            "conditions": {
                "T0": "nothing removed (self-rank control)",
                "T1": "exact query image removed",
                "T2": "query plus E0-defined rigid near-duplicates removed",
                "T3": "T2 plus structurally similar wrong-category hard distractors added",
            },
            "e1_normalizer": d2["e1_normalizer"],
            "e1_normalizer_policy": (
                "The T3 gallery inherits the plain MPEG-7 E1 normalizer, so adding "
                "distractors is the only variable. e1_normalizer_drift_if_refitted in "
                "d2_metrics.json records what a refit would have changed."
            ),
            "by_condition": by_condition,
        }
    if "d3" in present:
        headline["structure_probes"] = {
            "caveat": "constructed roles, NOT validated human truth",
            "by_probe": {
                kind: {
                    method: {
                        "best_variant_rank": values["best_structural_variant_rank"],
                        "best_distractor_rank": values["best_gross_distractor_rank"],
                        "structure_beats_gross": values["structure_beats_gross"],
                    }
                    for method, values in arms.items()
                }
                for kind, arms in present["d3"]["probes"].items()
            },
        }
    if "d4" in present:
        stability: dict[str, dict[str, float]] = {}
        for target in present["d4"]["targets"].values():
            for name, arms in target.items():
                for method, values in arms.items():
                    bucket = stability.setdefault(method, {})
                    overlap = float(values["top_k_overlap_with_untransformed"])
                    bucket[f"{name}_top10_overlap"] = overlap
        headline["transform_stability"] = {
            "gallery_size": present["d4"]["gallery_size"],
            "metric": "Jaccard overlap of Top-10 with the untransformed query, per transform",
            "by_method": stability,
        }
    if "d5" in present:
        d5 = present["d5"]
        headline["rotation_stress_agreement"] = {
            "study": "one member of every rated H1 pair turned by each stress angle (a)",
            "metric": (
                "agreement with the published human dissimilarity, restated positive; rotation "
                "is the only variable and the human ground truth is shared by all arms"
            ),
            "grid": f"{d5['rotations_searched']} steps x {d5['rotation_step_deg']:.1f} deg",
            "by_arm": d5["agreement_pooled_by_arm"],
            "curve_by_arm": d5["agreement_curve_by_arm"],
        }
        headline["rotation_stress_retrieval"] = {
            "study": "each D4 query turned by each stress angle against the stratified gallery (b)",
            "metric": "mean Top-10 overlap with the untransformed ranking over the rotated angles",
            "by_arm": d5["retrieval_robustness_by_arm"],
        }
    if "d5b" in present:
        d5b = present["d5b"]
        headline["rotation_stress_agreement_grouped"] = {
            "study": (
                "CP7-B re-analysis of rotation_stress_agreement: identical point estimates, "
                "uncertainty resampled by rated stimulus pair instead of by row"
            ),
            "why": (
                "d5 stacked 7 rotated angles onto each of 250 rated pairs and resampled the "
                "resulting 1750 rows independently, which counts one stimulus seven times as "
                "seven independent observations and makes every interval too narrow"
            ),
            "sampling_unit": d5b["sampling_unit"],
            "angle_factor": d5b["angle_factor"],
            "angle_factor_sensitivity": d5b["angle_factor_sensitivity"],
            "reproduction_of_committed_values": d5b["reproduction"],
            "ci": (
                "grouped two-level bootstrap (pairs + observers) with a shared replicate stream "
                "across arms; the angle-randomised interval is a sensitivity, not the headline"
            ),
            "by_arm": d5b["by_arm"],
            "paired_differences": d5b["paired_differences"],
            "differences_supported": d5b["differences_supported"],
            "assumptions": d5b["assumptions"],
            "audit_relation": d5b["audit_relation"],
        }

    payload = {
        "schema": "earthglyph.shape_perception_benchmark/1",
        "task": "P0-4.6",
        "git_commit": commit,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "seed": SEED,
        "n_boot": N_BOOT,
        "stages_present": sorted(present),
        "reproduce": [
            "python scripts/fetch_shape_benchmark_sources.py",
            "# S0K1/S0K2 additionally need the official toolbox (gitignored, no redistribution "
            "licence) at shapeComp_17_12_2024/ and: pip install -e .[shapecomp]",
            "python scripts/validate_shapecomp_port.py",
            "python scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2",
            "python scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2",
            "python scripts/run_shape_benchmark.py --stage d5 --methods E0 E1 E3 S0K1 S0K2",
            "# d5b re-analyses d5 (a) by stimulus pair; it reads the files above and never "
            "rewrites them",
            "python scripts/run_shape_benchmark.py --stage d5b",
            "python scripts/plot_shape_benchmark.py --figure all",
        ],
        "arm_availability": {
            "E0": "all stages",
            "E1": "all stages (EarthGlyph's own 91-D descriptors, NOT ShapeComp)",
            "E3": "all stages",
            "S0": (
                "H1/H2 pairwise only, from the authors' published 22-D coordinates "
                "(the ORIGINAL 91-descriptor ShapeComp MDS space). Outputs only: cannot score "
                "MPEG-7 or any new shape, so S0 is absent from every retrieval stage."
            ),
            "S0K1": (
                "all stages. Official KerNet1 weights ACTUALLY RUN locally via the numpy port "
                "(h5py, no TensorFlow), on the original contour under the official input "
                "contract. Can score MPEG-7 and new shapes. Validated vs published KerNet1 "
                "outputs (Figure8 pairwise Spearman ~0.98). Not rotation/mirror invariant."
            ),
            "S0K2": (
                "all stages. Official KerNet2 weights ACTUALLY RUN locally via the numpy port. "
                "Validated essentially exact vs published KerNet2 outputs (H2 per-dim r ~0.998, "
                "pairwise Spearman ~0.999). Can score MPEG-7 and new shapes. Not rotation/mirror "
                "invariant."
            ),
        },
        "headline": headline,
        "data_provenance": {
            "h1_h2": {
                key: manifest.get("h1_h2", {}).get(key)
                for key in ("record", "doi", "url", "archive_bytes", "archive_md5_published",
                            "license", "transfer")
            },
            "mpeg7": {
                key: manifest.get("mpeg7", {}).get(key)
                for key in ("url", "bytes", "sha256", "stimulus_count", "class_count")
            },
        },
        "detail_files": {name: path.relative_to(REPO).as_posix() for name, path in stages.items()},
    }
    write_json(RESULTS / "metrics.json", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        nargs="+",
        required=True,
        choices=["d1", "d2", "d3", "d4", "d5", "d5b", "summary"],
    )
    parser.add_argument("--methods", nargs="+", default=list(EARTHGLYPH_METHODS),
                        choices=["E0", "E1", "E3", "S0", "S0K1", "S0K2"])
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    for stage in args.stage:
        print(f"=== {stage} ===", flush=True)
        started = time.perf_counter()
        if stage == "d1":
            run_d1(args.methods + (["S0"] if "S0" not in args.methods else []))
        elif stage == "d2":
            run_d2(args.methods, args.top_k)
        elif stage == "d3":
            run_d3(args.methods, args.top_k)
        elif stage == "d4":
            run_d4(args.methods, args.top_k)
        elif stage == "d5":
            run_d5(args.methods, args.top_k)
        elif stage == "d5b":
            run_d5_grouped()
        else:
            write_summary()
        print(f"=== {stage} done in {time.perf_counter() - started:.1f}s ===", flush=True)


if __name__ == "__main__":
    main()