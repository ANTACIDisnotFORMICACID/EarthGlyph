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
    d6  rotation WRAPPERS for the official port (naive / R0 24-step / R1 36-72-step /
        R2 canonicalisation / R3 bilateral multi-direction) versus the EarthGlyph arms, under
        a frozen random-angle design on both goals at once: H1 human agreement (grouped
        bootstrap) and MPEG-7 T0/T1/T2 discriminative retrieval (T2 primary)
    d6-correct  statistics-only correction of the d6 retrieval headline: rotated cells and
        0-degree control cells reported as separate views, Precision@K renamed and a real
        Recall@K added. Reads the frozen d6 tables, re-runs no inference, writes a new
        versioned artefact and leaves every input byte-identical
    cp8  the independent validation run: scores the 24 queries and 120 angles frozen by
        CP8-B (checkpoint8/independent_validation_design.json) with the six arms that file
        names, against the whole MPEG-7 archive, and writes checkpoint8/. The gate re-derives
        the design from its seed before the first search, so a sample that moved cannot be
        scored quietly

Every stage writes real distances and timings. S0 appears only where the authors published
official coordinates; it is never fabricated for MPEG-7.

Usage:
    python scripts/run_shape_benchmark.py --stage d1
    python scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3
    python scripts/run_shape_benchmark.py --stage d6 --methods E0 E1 E3 S0K1 S0K2
    python scripts/run_shape_benchmark.py --stage cp8
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import rankdata

from earthglyph.retrieval import perceptual_features as e1
from earthglyph.retrieval import perceptual_methods as pm
from earthglyph.retrieval import shape_context_baseline as e3
from earthglyph.shape_benchmark import independent_validation as iv
from earthglyph.shape_benchmark import metrics
from earthglyph.shape_benchmark import rotation_methods as rm
from earthglyph.shape_benchmark import shapecomp_port as port
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
    _load_shapecomp_nets,
    _scorer_for,
    kernet_rotation_frames,
    pair_distance,
    search_shape,
)
from earthglyph.shape_benchmark.retrieval import (
    KERNET_ROTATION_STEPS as ROTATION_STEPS,
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


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read one stored tidy table back as strings; callers coerce with the stage's own helpers."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


D5_GROUPED_SCHEMA = "earthglyph.shape_perception_benchmark/d5_grouped/2"


def _repo_relative(path: Path) -> str:
    return path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.as_posix()


def _superseded_d5b_record(previous: Path, current_schema: str) -> dict[str, Any]:
    """Describe the artefact this stage replaces, and keep that description across re-runs."""
    if not previous.exists():
        return {"present": False}
    prior = read_json(previous)
    if not isinstance(prior, dict):
        return {"present": False}
    if prior.get("schema") == current_schema:
        # The file on disk is this stage's own output, not a superseded study. Re-recording its
        # hash would drop the only link to the /1 artefact, so carry the first run's record.
        relation = prior.get("audit_relation")
        carried = relation.get("superseded_file") if isinstance(relation, dict) else None
        return carried if isinstance(carried, dict) else {"present": False}
    return {
        "present": True,
        "path": _repo_relative(previous),
        "sha256": _file_sha256(previous),
        "bytes": previous.stat().st_size,
        "schema": prior.get("schema"),
        "relation": (
            "this stage replaces that file. No magnitude changes: the point estimates, "
            "intervals and widths are re-derived from the same stored CSV with the same seed, "
            "and the only difference is that each paired contrast now publishes both readings "
            "under direction-named fields, so any number quoted from the superseded file is "
            "still present here as agreement_first_minus_second"
        ),
    }


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
        return metrics.paired_difference(block["differences"][key])

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

    superseded = _superseded_d5b_record(RESULTS / "d5_grouped_metrics.json", D5_GROUPED_SCHEMA)

    summary = {
        "schema": D5_GROUPED_SCHEMA,
        "study": "CP7-B re-analysis of the d5 rotation-stress agreement (H1)",
        "paired_difference_direction": metrics.PAIRED_DIFFERENCE_DIRECTION,
        "paired_difference_convention": metrics.PAIRED_DIFFERENCE_CONVENTION,
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
            "superseded_file": superseded,
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
                "delta_direction": "",
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
                "point_or_delta": block["agreement_second_minus_first"],
                "delta_direction": block["direction"],
                "ci_low": block["ci_low_second_minus_first"],
                "ci_high": block["ci_high_second_minus_first"],
                "ci_width": block["ci_high_second_minus_first"]
                - block["ci_low_second_minus_first"],
                "excludes_zero": block["excludes_zero"],
                "n_valid_replicates": block["n_valid_replicates"],
                "sampling_unit_rows": fixed["n_clusters"],
                "d5_csv_sha256": source_sha,
            })
    return rows


# --------------------------------------------------------------------------- D6

#: CP7-C Task 3 + Task 4, frozen BEFORE the run. Twelve real MPEG-7 queries spanning every
#: shape family the brief names, five stress angles each, three candidate conditions. Redrawing
#: an angle or swapping a query after seeing a failure would make the headline unfalsifiable, so
#: the design is written into the artefact and never edited in place.
D6_QUERIES: tuple[tuple[str, str], ...] = (
    ("ray-1", "star-like (spokes)"),
    ("octopus-1", "star-like / natural (radiating limbs)"),
    ("Heart-1", "heart"),
    ("horseshoe-1", "crescent (concave U)"),
    ("fork-1", "forked"),
    ("tree-1", "forked / natural"),
    ("bat-1", "elongated (thin branches)"),
    ("sea_snake-1", "elongated"),
    ("Comma-1", "elongated curve"),
    ("HCircle-1", "near-circle"),
    ("device2-1", "near-circle (isotropic second moments)"),
    ("brick-01", "symmetric multi-solution (rectangle)"),
)
#: One on-grid angle (a multiple of the 15-degree step, never 0), two grid MIDPOINTS — the case
#: CP6 showed the 24-step grid cannot land on — and two angles that are neither.
D6_ANGLE_SLOTS: tuple[str, ...] = ("on_grid", "mid_grid", "mid_grid", "random", "random")
D6_CONDITIONS: tuple[str, ...] = ("T0", "T1", "T2")
#: Task 4 names T2 — exact target AND rigid near-duplicates removed — as this stage's primary.
D6_PRIMARY_CONDITION = "T2"
#: An "arbitrary" angle must sit this far from both the 15-degree grid and its midpoints,
#: otherwise the slot labels would not mean what they claim.
D6_OFF_GRID_MARGIN_DEG = 2.0
#: Rated H1 pairs re-scored through the public ``pair_distance`` path to prove the cached route
#: used by (a) is the same arithmetic CP6 documented.
D6_VERIFICATION_PAIRS = 20
D6_STRATEGIES: tuple[str, ...] = ("naive", "R0", "R1_36", "R1_72", "R2", "R3")
#: The arm list behind the committed ``d6_*`` artefacts: four EarthGlyph reference arms plus every
#: wrapper on every official network. A narrower list is a different experiment that would overwrite
#: the frozen table, so ``run_d6`` refuses it unless the caller opts in explicitly.
D6_FROZEN_METHODS: tuple[str, ...] = (*EARTHGLYPH_METHODS, *SHAPECOMP_RUN_METHODS)
D6_ARTEFACTS = (
    "d6_retrieval_results.csv",
    "d6_agreement_results.csv",
    "d6_top10_hits.csv",
    "d6_rotation_metrics.json",
)
#: The committed retrieval table is ``/1``, and its headline means included the 0-degree control
#: cells. From CP8-A on the writer emits ``/2`` (two views, separately counted) and
#: ``d6_corrected/1`` recomputes the same two views from the frozen per-cell table without running
#: any inference. ``/1`` stays on disk as the audited history it is.
D6_STORED_SCHEMA = "earthglyph.shape_perception_benchmark/d6/1"
D6_WRITER_SCHEMA = "earthglyph.shape_perception_benchmark/d6/2"
D6_CORRECTED_SCHEMA = "earthglyph.shape_perception_benchmark/d6_corrected/1"
D6_CORRECTED_ARTEFACT = "d6_rotation_metrics_corrected.json"


@dataclass(frozen=True)
class D6Arm:
    """One arm of the rotation-method comparison: an encoder plus its rotation wrapper."""

    label: str
    method: str
    strategy: str | None = None
    """``rotation_methods`` wrapper name, for the official-network arms."""
    orientation: str | None = None
    """E3's own orientation policy: ``sample`` is its R0 analogue, ``canonical`` its R2."""

    @property
    def is_port(self) -> bool:
        return self.strategy is not None

    def gallery(self, plain: Gallery, canonical: Gallery) -> Gallery:
        return canonical if self.orientation == "canonical" else plain

    def search(self, query: BenchmarkShape, gallery: Gallery, top_k: int) -> Any:
        return search_shape(
            query, gallery, self.method, top_k=top_k, rotation_strategy=self.strategy
        )

    def description(self) -> str:
        if self.is_port:
            return rm.describe(str(self.strategy))
        if self.method == "E3":
            return f"Shape Context with E3's own orientation policy {self.orientation!r}"
        return f"EarthGlyph arm {self.method}, rotation handled by construction"


def _d6_arms(methods: list[str]) -> list[D6Arm]:
    """The arm table: every EarthGlyph reference plus every wrapper on every official network.

    Nothing is dropped for cost reasons — an arm that skipped the hard cases would not be
    comparable with the arms that ran.
    """
    arms = [
        D6Arm("E0", "E0"),
        D6Arm("E1", "E1"),
        D6Arm("E3/sample", "E3", orientation="sample"),
        D6Arm("E3/canonical", "E3", orientation="canonical"),
    ]
    arms = [arm for arm in arms if arm.method in methods]
    for network in SHAPECOMP_RUN_METHODS:
        if network not in methods:
            continue
        for strategy in D6_STRATEGIES:
            arms.append(D6Arm(f"{network}/{strategy}", network, strategy=strategy))
    if not arms:
        raise ValueError("--methods selected no d6 arm")
    return arms


def _d6_frozen_labels() -> list[str]:
    return [arm.label for arm in _d6_arms(list(D6_FROZEN_METHODS))]


def _require_frozen_d6_arms(
    labels: list[str], artefacts_present: bool, allow_subset: bool
) -> None:
    """Refuse to narrow the frozen 16-arm table unless the caller says so on purpose.

    ``--methods`` defaults to the EarthGlyph trio, which is right for d1-d5 and wrong for d6: a
    bare ``--stage d6`` would rewrite the committed artefacts as a four-arm table and leave no
    trace that the wrapper comparison had been dropped.
    """
    if labels == _d6_frozen_labels() or allow_subset or not artefacts_present:
        return
    raise SystemExit(
        "--stage d6 would overwrite the frozen 16-arm artefacts "
        f"{', '.join(D6_ARTEFACTS)} with a {len(labels)}-arm table "
        f"({', '.join(labels)}). Reproduce the committed study with "
        f"--methods {' '.join(D6_FROZEN_METHODS)}; if the narrower table is what you want, "
        "say so with --allow-d6-arm-subset."
    )


def _d6_off_grid_angle(rng: np.random.Generator, step: float) -> float:
    """A pose at least ``D6_OFF_GRID_MARGIN_DEG`` from the grid AND from its midpoints."""
    for _ in range(1000):
        deg = float(rng.uniform(0.0, 360.0))
        for offset in (0.0, step / 2.0):
            residue = min(abs((deg - offset) % step), step - abs((deg - offset) % step))
            if residue <= D6_OFF_GRID_MARGIN_DEG:
                break
        else:
            return deg
    raise RuntimeError("could not draw an off-grid angle")


def _d6_angles(seed: int = SEED) -> list[dict[str, Any]]:
    """The frozen 12 x 5 stress-angle design, drawn from one deterministic rng stream."""
    rng = np.random.default_rng(seed)
    step = 360.0 / float(ROTATION_STEPS)
    rows: list[dict[str, Any]] = []
    for stem, family in D6_QUERIES:
        for slot in D6_ANGLE_SLOTS:
            if slot == "on_grid":
                deg = float(int(rng.integers(1, ROTATION_STEPS)) * step)
            elif slot == "mid_grid":
                deg = float(int(rng.integers(0, ROTATION_STEPS)) * step + step / 2.0)
            else:
                deg = _d6_off_grid_angle(rng, step)
            rows.append({"stem": stem, "family": family, "slot": slot, "angle_deg": deg})
    return rows


def _d6_rank(distances: np.ndarray, gallery: Gallery, drop: Sequence[str]) -> np.ndarray:
    """Rank the kept candidates with the tie-break ``search_shape`` itself uses."""
    excluded = set(drop)
    indices = np.flatnonzero(
        np.array([shape.shape_id not in excluded for shape in gallery.shapes])
    )
    if not indices.size:
        raise ValueError("a candidate condition dropped the entire gallery")
    return indices[np.asarray(pm.rank_order(distances[indices]), dtype=int)]


def _d6_condition_row(
    arm: D6Arm,
    condition: str,
    stem: str,
    angle_row: dict[str, Any],
    distances: np.ndarray,
    gallery: Gallery,
    target_index: int,
    query_category: str,
    drop: Sequence[str],
    ref_order: np.ndarray,
    k: int,
    elapsed: float,
) -> dict[str, Any]:
    """Every Task 4 metric for one (arm, condition, query, angle) cell."""
    order = _d6_rank(distances, gallery, drop)
    positions = {int(index): rank for rank, index in enumerate(order, start=1)}
    categories = [str(gallery.shapes[int(index)].category) for index in order]
    relevant = [1 if category == query_category else 0 for category in categories]
    target_rank = positions.get(int(target_index))
    first_relevant = next((rank for rank, flag in enumerate(relevant, start=1) if flag), None)
    top1 = gallery.shapes[int(order[0])].shape_id
    wrong = np.array(
        [
            float(distances[int(index)])
            for index in range(len(gallery))
            if str(gallery.shapes[int(index)].category) != query_category
            and np.isfinite(distances[int(index)])
        ]
    )
    displacement = metrics.rank_displacement(ref_order, order)
    return {
        "arm": arm.label,
        "method": arm.method,
        "rotation_strategy": arm.strategy,
        "orientation": arm.orientation,
        "condition": condition,
        "query_id": f"mpeg7_{stem}",
        "family": angle_row["family"],
        "slot": angle_row["slot"],
        "angle_deg": angle_row["angle_deg"],
        "n_kept": int(len(order)),
        "n_same_class_kept": int(sum(relevant)),
        "top1": top1,
        "top1_is_target": int(top1 == gallery.shapes[int(target_index)].shape_id),
        "top1_is_same_class": int(categories[0] == query_category),
        "top1_matches_unrotated": int(top1 == gallery.shapes[int(ref_order[0])].shape_id),
        "target_rank": target_rank,
        "self_distance": float(distances[int(target_index)]),
        "best_wrong_class_distance": float(wrong.min()) if wrong.size else None,
        "class_recall_at_k": metrics.class_recall_at_k(categories, query_category, k),
        "first_relevant_rank": first_relevant,
        "ndcg_at_k": metrics.ndcg_at_k(relevant, k) if first_relevant else None,
        "top_k_overlap_vs_unrotated": metrics.top_k_overlap(ref_order, order, k),
        "mean_abs_rank_shift": displacement["mean_abs_shift"],
        "max_abs_rank_shift": float(displacement["max_abs_shift"]),
        "spearman_between_rankings": displacement["spearman_between_rankings"],
        "elapsed_seconds": elapsed,
    }


def _d6_mean(values: list[Any]) -> float | None:
    finite = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    return float(np.mean(finite)) if finite else None


# The numeric columns of one stored cell, and the subset that counts rather than measures.
D6_CELL_FLOAT_FIELDS: tuple[str, ...] = (
    "angle_deg",
    "n_kept",
    "n_same_class_kept",
    "top1_is_target",
    "top1_is_same_class",
    "top1_matches_unrotated",
    "target_rank",
    "self_distance",
    "best_wrong_class_distance",
    "class_recall_at_k",
    "first_relevant_rank",
    "ndcg_at_k",
    "top_k_overlap_vs_unrotated",
    "mean_abs_rank_shift",
    "max_abs_rank_shift",
    "spearman_between_rankings",
    "elapsed_seconds",
)
D6_CELL_INT_FIELDS: tuple[str, ...] = (
    "n_kept",
    "n_same_class_kept",
    "top1_is_target",
    "top1_is_same_class",
    "top1_matches_unrotated",
    "target_rank",
    "first_relevant_rank",
)


def _d6_cell(row: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce one cell back to measurable types, whether it came from memory or from the CSV.

    ``csv.DictReader`` hands back strings, so the correction stage and the writer must normalise
    through the same door or the two would average different types.
    """
    out: dict[str, Any] = dict(row)
    for field in D6_CELL_FLOAT_FIELDS:
        value = out.get(field)
        out[field] = (
            None if value is None or str(value).strip() in ("", "None", "nan") else float(value)
        )
    for field in D6_CELL_INT_FIELDS:
        if out.get(field) is not None:
            out[field] = int(round(float(out[field])))
    return out


def _d6_same_class_hits(cell: dict[str, Any], k: int) -> int:
    """Same-class candidates inside the Top-K, recovered from the stored Precision@K column.

    The frozen cell table stores the ratio rather than the count. Multiplying back is exact for
    this design -- every condition keeps hundreds of candidates, so the denominator is always K --
    and it is checked, not assumed: the ratio must reproduce under integer counts, and
    tests/test_shape_benchmark_d6_correction.py re-derives the same counts for all 1,152 T2 cells
    from the per-rank rows in ``d6_top10_hits.csv``.
    """
    returned = min(k, int(cell["n_kept"]))
    if returned == 0:
        return 0
    hits = int(round(float(cell["class_recall_at_k"]) * returned))
    if hits / returned != float(cell["class_recall_at_k"]):
        raise ValueError(
            f"stored class_recall_at_k={cell['class_recall_at_k']!r} is not a count over "
            f"{returned} returned candidates; refusing to guess the numerator"
        )
    return hits


def _d6_summarise_cells(
    cells: list[dict[str, Any]],
    k: int,
    view: str,
    condition: str,
    n_rotated: int,
    n_control: int,
) -> dict[str, Any]:
    """Headline for one (view, condition): every statistic over exactly the cells it may use."""
    ranks = [int(row["target_rank"]) for row in cells if row["target_rank"]]
    hits = [_d6_same_class_hits(row, k) for row in cells]
    denominators = [int(row["n_same_class_kept"]) for row in cells]
    recall = [h / d for h, d in zip(hits, denominators, strict=True) if d > 0]
    ndcg_scored = [row["ndcg_at_k"] for row in cells if row["ndcg_at_k"] is not None]
    out: dict[str, Any] = {
        "view": view,
        "cell_filter": (
            'slot == "control"' if view == metrics.CONTROL_VIEW else 'slot != "control"'
        ),
        "n_cells": len(cells),
        "n_rotated_cells": n_rotated,
        "n_control_cells": n_control,
        "exact_target_present": condition == "T0",
        "top1_accuracy": _d6_mean([row["top1_is_target"] for row in cells]),
        "top1_same_class_accuracy": _d6_mean([row["top1_is_same_class"] for row in cells]),
        "top1_stability_vs_unrotated": _d6_mean(
            [row["top1_matches_unrotated"] for row in cells]
        ),
        "target_rank_mean": _d6_mean(ranks),
        "target_rank_median": float(np.median(ranks)) if ranks else None,
        "target_rank_worst": int(max(ranks)) if ranks else None,
        "self_distance_mean": _d6_mean([row["self_distance"] for row in cells]),
        "self_distance_max": float(max(row["self_distance"] for row in cells)),
        "attraction_gap_mean": _d6_mean(
            [
                row["best_wrong_class_distance"] - row["self_distance"]
                for row in cells
                if row["best_wrong_class_distance"] is not None
            ]
        ),
        f"precision_at_{k}": _d6_mean([row["class_recall_at_k"] for row in cells]),
        f"recall_at_{k}": float(np.mean(recall)) if recall else None,
        f"recall_at_{k}_n_scored": len(recall),
        f"recall_at_{k}_undefined_cells": len(cells) - len(recall),
        "n_relevant_retained_mean": _d6_mean(denominators),
        "n_relevant_retained_min": min(denominators),
        "n_relevant_retained_max": max(denominators),
        "class_mrr": metrics.mean_reciprocal_rank([row["first_relevant_rank"] for row in cells]),
        f"ndcg_at_{k}": _d6_mean([row["ndcg_at_k"] for row in cells]),
        f"ndcg_at_{k}_n_scored": len(ndcg_scored),
        f"top_{k}_overlap_vs_unrotated": _d6_mean(
            [row["top_k_overlap_vs_unrotated"] for row in cells]
        ),
        "top_k_overlap_min": float(min(row["top_k_overlap_vs_unrotated"] for row in cells)),
        "mean_abs_rank_shift": _d6_mean([row["mean_abs_rank_shift"] for row in cells]),
        "max_abs_rank_shift": float(max(row["max_abs_rank_shift"] for row in cells)),
        "seconds_per_query_mean": _d6_mean([row["elapsed_seconds"] for row in cells]),
        "seconds_per_query_max": float(max(row["elapsed_seconds"] for row in cells)),
    }
    if view == metrics.CONTROL_VIEW:
        out["note"] = (
            "unrotated poses only. top1_stability_vs_unrotated is 1.0 here by construction, so "
            "this view is a drift check on the pipeline and never a rotation measurement."
        )
    return out


def _d6_summarise(rows: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """Collapse the tidy cells into per-view, per-condition headlines.

    ``/1`` of this table averaged the 0-degree control rows into the rotated ones, which is what
    made a pose-blind arm look twice as rotation-stable as it is. The two views are computed
    separately from here on; nothing is deleted, and each block states how many cells it used.
    """
    cells = [_d6_cell(row) for row in rows]
    cells_by_view = metrics.split_rotation_and_control(cells)
    counts = {
        view: {
            condition: sum(1 for row in view_rows if row["condition"] == condition)
            for condition in D6_CONDITIONS
        }
        for view, view_rows in cells_by_view.items()
    }
    out: dict[str, Any] = {}
    for view in metrics.VIEWS:
        per_condition: dict[str, Any] = {}
        for condition in D6_CONDITIONS:
            view_cells = [row for row in cells_by_view[view] if row["condition"] == condition]
            if not view_cells:
                continue
            per_condition[condition] = _d6_summarise_cells(
                view_cells,
                k,
                view,
                condition,
                n_rotated=counts[metrics.ROTATION_VIEW][condition],
                n_control=counts[metrics.CONTROL_VIEW][condition],
            )
        out[view] = per_condition
    return out


def _d6_orientation_diagnostics(gallery: Gallery, queries: list[BenchmarkShape]) -> dict[str, Any]:
    """Line up the three orientation conventions EarthGlyph now owns on the same contours.

    ``rotation_methods`` (this stage's R2, contour point moments), E1's ``principal_frame``
    (AREA moments) and E3's ``canonical_frame`` (point covariance with a different tolerance)
    are NOT the same rule. Listing them side by side is what keeps a claim of
    "canonicalisation" checkable instead of becoming a label.
    """
    contours = [shape.display_points for shape in gallery.shapes] + [
        shape.display_points for shape in queries
    ]
    statuses: dict[str, int] = {}
    margins: list[float] = []
    ratios: list[float] = []
    e3_degenerate = 0
    e3_ambiguous = 0
    disagreement: list[float] = []
    for contour in contours:
        frame = rm.canonical_frame(contour)
        statuses[frame.status] = statuses.get(frame.status, 0) + 1
        margins.append(abs(frame.sign_margin))
        ratios.append(frame.axis_ratio)
        _, provenance = e3.canonical_frame(contour)
        e3_degenerate += int(bool(provenance["axes_degenerate"]))
        e3_ambiguous += int(bool(provenance["sign_ambiguous"]))
        if frame.status != "isotropic-fallback" and not provenance["axes_degenerate"]:
            difference = abs(frame.angle_deg - float(provenance["axis_deg"])) % 360.0
            disagreement.append(min(difference, 360.0 - difference))
    return {
        "n_contours": len(contours),
        "rotation_methods_status_counts": statuses,
        "isotropy_max": rm.ISOTROPY_MAX,
        "sign_tie_tolerance": rm.SIGN_TIE_TOLERANCE,
        "axis_ratio_min": float(min(ratios)),
        "axis_ratio_median": float(np.median(ratios)),
        "sign_margin_median": float(np.median(margins)),
        "sign_margin_min": float(min(margins)),
        "e3_axes_degenerate": e3_degenerate,
        "e3_sign_ambiguous": e3_ambiguous,
        "angle_disagreement_vs_e3": {
            "n_compared": len(disagreement),
            "median_deg": float(np.median(disagreement)) if disagreement else None,
            "max_deg": float(max(disagreement)) if disagreement else None,
        },
        "note": (
            "Statuses are the R2 wrapper's own evidence: 'isotropic-fallback' means no principal "
            "axis exists to align to, so R2 silently leaves that shape in its stored pose and is "
            "rotation-blind on it; 'sign-tie' means an axis exists but its 180-degree flip is "
            "decided by floating-point noise. E3's two counters use E3's own thresholds, which "
            "are NOT the same rule, and the disagreement column quantifies how far apart the two "
            "conventions actually put the same contour."
        ),
    }


def _d6_image_axis_check(queries: list[BenchmarkShape]) -> dict[str, Any]:
    """Does R2's contour-space convention survive KerNet2's rasterisation and ``flipud``?

    D59 made the raster single-pixel-flip cost 18.5% of the embedding norm, so an axis
    convention decided on contours has to be checked where the image network actually looks.
    The major axis is pinned to +x precisely because ``flipud`` leaves that direction alone.
    """
    deviations: dict[str, float] = {}
    for shape in queries:
        canonical, frame = rm.canonicalise(shape.display_points)
        mask = port.kernet2_input(canonical)[..., 0]
        rows, columns = np.nonzero(mask)
        if len(rows) < 3:
            deviations[shape.shape_id] = float("nan")
            continue
        # Pixel row 0 is the TOP after flipud, so math-convention y is -row.
        points = np.stack([columns.astype(float), -rows.astype(float)], axis=1)
        deviations[shape.shape_id] = abs(rm.canonical_frame(points).angle_deg)
    finite = [value for value in deviations.values() if np.isfinite(value)]
    return {
        "deviation_deg_by_query": deviations,
        "max_abs_deviation_deg": float(max(finite)) if finite else None,
        "note": (
            "Angle of the RASTERISED mask's major axis after R2 canonicalised the CONTOUR. Small "
            "values mean one axis convention serves both networks; a large value means the 40x40 "
            "grid re-introduces an orientation the contour decision did not predict."
        ),
    }


def run_d6(methods: list[str], top_k: int, allow_arm_subset: bool = False) -> dict[str, Any]:
    """Compare rotation WRAPPERS for the official port under random rotation, then decide.

    Two studies, one frozen design, shared ground truths, never mixed:

    * **(a) human agreement** — one member of each rated H1 pair is turned by a stress angle and
      every wrapper must still recover the published dissimilarity. Intervals use the CP7-B
      grouped bootstrap: the 250 rated pairs are the sampling unit, not the 1750 angle rows.
    * **(b) discriminative retrieval** — the rotated query is ranked against the D4 stratified
      gallery under T0 (exact present), T1 (exact removed) and T2 (exact + rigid near-duplicates
      removed). T2 is the primary condition. Class labels are relevance proxies, not perception.

    Goal A (perceptual agreement) and goal B (retrieval under unknown rotation) are reported
    separately, so a high Spearman cannot cover a collapsed Top-10 and a perfect self-match
    cannot cover a lost human ordering.
    """
    arms = _d6_arms(methods)
    _require_frozen_d6_arms(
        [arm.label for arm in arms],
        any((RESULTS / name).exists() for name in D6_ARTEFACTS),
        allow_arm_subset,
    )
    design = _d6_angles()

    shapes, _ = load_mpeg7(CACHE)
    per_class: dict[str, list[BenchmarkShape]] = {}
    for shape in shapes:
        per_class.setdefault(str(shape.category), []).append(shape)
    subset = [
        members[i]
        for _, members in sorted(per_class.items())
        for i in range(min(D4_GALLERY_PER_CLASS, len(members)))
    ]
    precompute: dict[str, float] = {}
    started = time.perf_counter()
    gallery = Gallery(subset, name="mpeg7_stratified")
    precompute["gallery_mpeg7"] = time.perf_counter() - started
    started = time.perf_counter()
    canonical_gallery = Gallery(
        subset,
        name="mpeg7_stratified_canonical",
        e3_params={"orientation": "canonical"},
        e1_normalizer=gallery.e1_normalizer,
    )
    precompute["gallery_mpeg7_e3_canonical"] = time.perf_counter() - started
    by_stem = {shape.shape_id.removeprefix("mpeg7_"): shape for shape in subset}
    missing = [stem for stem, _ in D6_QUERIES if stem not in by_stem]
    if missing:
        raise RuntimeError(f"frozen d6 queries absent from the stratified gallery: {missing}")

    # One pose-embedding cache per network: a 1750-row sweep then costs one pass per
    # (shape, pose, wrapper) instead of one per row.
    pose_cache: dict[tuple[str, str, str, float, str], np.ndarray] = {}

    def embed(network: str, strategy: str, shape: BenchmarkShape, degrees: float, role: str):
        key = (network, strategy, shape.shape_id, float(degrees), role)
        if key not in pose_cache:
            contour = rotate(shape.display_points, degrees) if degrees else shape.display_points
            pose_cache[key] = rm.pose_embedding(
                _scorer_for(_RUN_NETWORK[network]), contour, strategy, role
            )
        return pose_cache[key]

    for network in SHAPECOMP_RUN_METHODS:
        if network not in methods:
            continue
        started = time.perf_counter()
        for strategy in D6_STRATEGIES:
            gallery.pose_embeddings(_RUN_NETWORK[network], strategy)
        precompute[f"gallery_pose_embeddings_{network}"] = time.perf_counter() - started

    # ---------------------------------------------------------------- (b) retrieval
    retrieval_rows: list[dict[str, Any]] = []
    hit_rows: list[dict[str, Any]] = []
    angles_by_stem: dict[str, list[dict[str, Any]]] = {}
    for row in design:
        angles_by_stem.setdefault(str(row["stem"]), []).append(row)
    for stem, angle_rows in sorted(angles_by_stem.items()):
        base = by_stem[stem]
        target_index = int(gallery.index_of(base.shape_id))
        query_category = str(base.category)
        # E0 defines the rigid near-duplicate set, once per query and identically for every arm
        # (as in D2), so no arm is handed a candidate list chosen around its own weaknesses.
        e0_full = search_shape(base, gallery, "E0", top_k=len(gallery))
        near = [
            gallery.shapes[int(index)].shape_id
            for index in e0_full.order
            if e0_full.distances[int(index)] < T2_THRESHOLD
            and gallery.shapes[int(index)].shape_id != base.shape_id
        ]
        drops = {"T0": [], "T1": [base.shape_id], "T2": [base.shape_id, *near]}
        print(f"  {stem} ({query_category}) near-duplicates={len(near)}", flush=True)
        for arm in arms:
            gal = arm.gallery(gallery, canonical_gallery)
            reference = arm.search(base, gal, len(gal))
            ref_orders = {
                condition: _d6_rank(reference.distances, gal, drop)
                for condition, drop in drops.items()
            }
            poses: list[tuple[str, dict[str, Any], BenchmarkShape]] = [
                ("unrotated", {"family": "", "slot": "control", "angle_deg": 0.0}, base)
            ] + [
                (f"rotate_{row['angle_deg']:g}", row, _rotated(base, float(row["angle_deg"])))
                for row in angle_rows
            ]
            for pose_label, angle_row, query in poses:
                result = arm.search(query, gal, len(gal))
                for condition, drop in drops.items():
                    retrieval_rows.append(
                        _d6_condition_row(
                            arm,
                            condition,
                            stem,
                            angle_row,
                            result.distances,
                            gal,
                            target_index,
                            query_category,
                            drop,
                            ref_orders[condition],
                            top_k,
                            result.elapsed_seconds,
                        )
                    )
                for rank, index in enumerate(
                    _d6_rank(result.distances, gal, drops[D6_PRIMARY_CONDITION])[:top_k], start=1
                ):
                    hit_rows.append(
                        {
                            "query_id": base.shape_id,
                            "family": angle_rows[0]["family"],
                            "arm": arm.label,
                            "condition": D6_PRIMARY_CONDITION,
                            "pose": pose_label,
                            "rank": rank,
                            "shape_id": gal.shapes[int(index)].shape_id,
                            "category": gal.shapes[int(index)].category,
                            "distance": float(result.distances[int(index)]),
                            "is_same_class": int(
                                str(gal.shapes[int(index)].category) == query_category
                            ),
                        }
                    )
            rotated = [
                row
                for row in retrieval_rows
                if row["arm"] == arm.label
                and row["condition"] == D6_PRIMARY_CONDITION
                and row["slot"] != "control"
            ]
            # Under T2 the exact target is gone by construction, so the worst case that means
            # anything here is the pose that pushed the first same-class candidate furthest down
            # while disturbing the head of the list most.
            worst = max(rotated, key=lambda row: (row["first_relevant_rank"] or 10_000))
            print(
                f"    {arm.label:16s} {D6_PRIMARY_CONDITION} worst first-same-class rank="
                f"{worst['first_relevant_rank']} at {worst['angle_deg']:g}deg "
                f"overlap={worst['top_k_overlap_vs_unrotated']:.2f} "
                f"{worst['elapsed_seconds']:.2f}s/query",
                flush=True,
            )
    retrieval_by_arm = {
        arm.label: _d6_summarise([row for row in retrieval_rows if row["arm"] == arm.label], top_k)
        for arm in arms
    }

    # ---------------------------------------------------------------- (a) agreement
    h1 = load_h1(CACHE)
    h1_gallery = Gallery(list(h1.shapes.values()), official_coords=h1.official_coords, name="h1")
    ratings = h1.pair_matrix()
    mean_rating = ratings.mean(axis=1)
    stress_angles = [angle for angle in D5_ANGLES if angle != 0.0]
    slot = {pair.pair_id: index for index, pair in enumerate(h1.pairs)}
    agreement_rows: list[dict[str, Any]] = []
    distances_by_arm: dict[str, np.ndarray] = {}
    curves: dict[str, dict[str, float]] = {}
    for arm in arms:
        values = np.empty(len(stress_angles) * len(h1.pairs))
        per_angle: dict[str, float] = {}
        for index_angle, phi in enumerate(stress_angles):
            for pair in h1.pairs:
                left = h1.shapes[pair.left]
                right = h1.shapes[pair.right]
                if arm.is_port:
                    strategy = rm.resolve_strategy(str(arm.strategy))
                    rows_left = embed(arm.method, strategy.name, left, 0.0, "query")
                    rows_right = embed(arm.method, strategy.name, right, float(phi), "gallery")
                    distance = float(rm.pose_pair_distance(rows_left, rows_right))
                else:
                    distance = pair_distance(
                        left,
                        _rotated(right, float(phi)),
                        arm.method,
                        normalizer=h1_gallery.e1_normalizer,
                        orientation=arm.orientation,
                    )
                values[index_angle * len(h1.pairs) + slot[pair.pair_id]] = distance
                agreement_rows.append(
                    {
                        "study": "agreement",
                        "arm": arm.label,
                        "method": arm.method,
                        "rotation_strategy": arm.strategy,
                        "orientation": arm.orientation,
                        "stress_deg": phi,
                        "pair_id": pair.pair_id,
                        "model_distance": distance,
                        "human_mean_similarity": float(mean_rating[slot[pair.pair_id]]),
                    }
                )
            block = values[index_angle * len(h1.pairs) : (index_angle + 1) * len(h1.pairs)]
            per_angle[f"rotate_{phi:g}"] = -float(metrics.correlate(block, mean_rating).spearman)
        distances_by_arm[arm.label] = values
        curves[arm.label] = per_angle
        line = " ".join(f"{key}={value:+.3f}" for key, value in per_angle.items())
        print(f"  (a) {arm.label:16s} {line}", flush=True)

    # The cached sweep and the public pairwise path must be the same arithmetic, not a re-run of
    # it: a deterministic sample of rated pairs goes through ``pair_distance`` instead.
    sample_rng = np.random.default_rng(SEED)
    checked = [
        h1.pairs[int(index)]
        for index in sample_rng.choice(len(h1.pairs), size=D6_VERIFICATION_PAIRS, replace=False)
    ]
    verification: list[dict[str, Any]] = []
    for arm in arms:
        if not arm.is_port:
            continue
        deviations = [
            abs(
                pair_distance(
                    h1.shapes[pair.left],
                    _rotated(h1.shapes[pair.right], float(phi)),
                    arm.method,
                    rotation_strategy=arm.strategy,
                )
                - distances_by_arm[arm.label][
                    stress_angles.index(phi) * len(h1.pairs) + slot[pair.pair_id]
                ]
            )
            for pair in checked
            for phi in stress_angles[:2]
        ]
        verification.append(
            {
                "arm": arm.label,
                "n_checked": len(deviations),
                "max_abs_deviation": float(max(deviations)),
            }
        )
    max_deviation = max((item["max_abs_deviation"] for item in verification), default=0.0)

    pair_ids = np.array([slot[pair.pair_id] for pair in h1.pairs], dtype=int)
    group_ids = np.tile(pair_ids, len(stress_angles))
    condition_ids = np.repeat(np.asarray(stress_angles, dtype=float), len(h1.pairs))
    stress_matrix = np.tile(ratings, (len(stress_angles), 1))
    grouped = metrics.bootstrap_grouped_two_level(
        distances_by_arm, stress_matrix, group_ids, n_boot=N_BOOT, seed=SEED
    )
    grouped_randomised = metrics.bootstrap_grouped_two_level(
        distances_by_arm,
        stress_matrix,
        group_ids,
        condition_ids=condition_ids,
        resample_conditions=True,
        n_boot=N_BOOT,
        seed=SEED,
    )
    agreement_by_arm: dict[str, Any] = {}
    for arm in arms:
        block = grouped["per_arm"][arm.label]
        randomised = grouped_randomised["per_arm"][arm.label]
        pooled = metrics.correlate(distances_by_arm[arm.label], stress_matrix.mean(axis=1))
        agreement_by_arm[arm.label] = {
            "agreement_pooled": -float(pooled.spearman),
            "ci_low_grouped": -float(block["spearman_ci_high"]),
            "ci_high_grouped": -float(block["spearman_ci_low"]),
            "ci_low_angle_randomised": -float(randomised["spearman_ci_high"]),
            "ci_high_angle_randomised": -float(randomised["spearman_ci_low"]),
            "n_rows": int(len(distances_by_arm[arm.label])),
            "curve_by_angle": curves[arm.label],
        }

    queries = [by_stem[stem] for stem, _ in D6_QUERIES]
    summary: dict[str, Any] = {
        "schema": D6_WRITER_SCHEMA,
        "seed": SEED,
        "n_boot": N_BOOT,
        "design": {
            "n_queries": len(D6_QUERIES),
            "queries": [
                {"stem": stem, "family": family, "shape_id": f"mpeg7_{stem}"}
                for stem, family in D6_QUERIES
            ],
            "angles_per_query": len(D6_ANGLE_SLOTS),
            "angle_slots": list(D6_ANGLE_SLOTS),
            "n_query_angle_rows": len(design),
            "conditions": list(D6_CONDITIONS),
            "primary_condition": D6_PRIMARY_CONDITION,
            "t2_threshold_e0": T2_THRESHOLD,
            "top_k": top_k,
            "rotation_step_deg": 360.0 / float(ROTATION_STEPS),
            "frozen_before_run": True,
            "note": (
                "Counts and families were declared before the run. No angle was redrawn and no "
                "query swapped after seeing a result: an arm that skipped hard cases would not be "
                "comparable with the arms that ran, so every cell runs for every arm. The "
                "unrotated pose is carried as a control row (slot='control'), and from /2 the "
                "summary reports it as its own view instead of averaging it into the rotated "
                "headline: the control cells ask an unrotated question, so folding them in made a "
                "pose-blind arm look as rotation-stable as the wrappers."
            ),
        },
        "arms": [
            {
                "label": arm.label,
                "method": arm.method,
                "rotation_strategy": arm.strategy,
                "orientation": arm.orientation,
                "contract": arm.description(),
            }
            for arm in arms
        ],
        "angles": design,
        "precompute_seconds": precompute,
        "retrieval_by_arm": retrieval_by_arm,
        "agreement_by_arm": agreement_by_arm,
        "agreement_bootstrap": {
            "sampling_unit": (
                "rated stimulus pair: each H1 pair contributes all of its stress angles as one "
                "cluster, so 250 pairs are resampled and never the 1750 rows"
            ),
            "scheme_fixed_angle": grouped["scheme"],
            "scheme_angle_randomised": grouped_randomised["scheme"],
            "n_clusters": grouped["n_clusters"],
            "rows_per_cluster": grouped["rows_per_cluster"],
            "n_rows": grouped["n_rows"],
            "condition_factor": grouped["condition_factor"],
        },
        # Stored raw on purpose: the bootstrap delta for key "A|B" already is
        # agreement(B) - agreement(A), so re-wrapping it in metrics.paired_difference() here would
        # make the writer emit a shape the committed d6 table does not have.
        "paired_differences": grouped["differences"],
        "paired_differences_angle_randomised": grouped_randomised["differences"],
        "verification_cached_vs_pair_distance": verification,
        "max_abs_deviation_cached_vs_public": max_deviation,
        "orientation_diagnostics": _d6_orientation_diagnostics(gallery, queries),
        "image_axis_convention_check": _d6_image_axis_check(queries),
        "goal_separation": (
            "(a) measures whether a representation still tracks HUMAN dissimilarity once the "
            "stimulus is turned; (b) measures whether the HEAD of a real retrieval list survives "
            "an unknown rotation. Neither implies the other and the report keeps them apart: a "
            "high Spearman cannot excuse a collapsed Top-10, and rank-1 self-matching cannot "
            "excuse losing the human ordering."
        ),
        "rows_written": {"retrieval": len(retrieval_rows), "agreement": len(agreement_rows)},
    }
    write_csv(RESULTS / "d6_retrieval_results.csv", retrieval_rows)
    write_csv(RESULTS / "d6_agreement_results.csv", agreement_rows)
    write_csv(RESULTS / "d6_top10_hits.csv", hit_rows)
    write_json(RESULTS / "d6_rotation_metrics.json", summary)
    return summary


# ------------------------------------------------------------------- CP8-A statistics correction

D6_CORRECTION_FIELDS: tuple[str, ...] = (
    "top1_accuracy",
    "top1_same_class_accuracy",
    "top1_stability_vs_unrotated",
    "target_rank_mean",
    "target_rank_median",
    "target_rank_worst",
    "self_distance_mean",
    "self_distance_max",
    "attraction_gap_mean",
    "class_mrr",
    "mean_abs_rank_shift",
    "max_abs_rank_shift",
    "seconds_per_query_mean",
    "seconds_per_query_max",
)
#: Stored name -> corrected name, for the one field whose label was wrong rather than its mean.
D6_RENAMED_FIELDS: dict[str, str] = {"class_recall_at_k": "precision_at_10"}
#: Two views of one comparison, ranked by the metric a reader is most likely to quote.
D6_RANKING_METRICS: tuple[str, ...] = (
    "top1_same_class_accuracy",
    "top1_stability_vs_unrotated",
    "precision_at_10",
    "recall_at_10",
    "class_mrr",
    "top_10_overlap_vs_unrotated",
    "seconds_per_query_mean",
)


def _d6_pose_label(cell: Mapping[str, Any]) -> str:
    """Reproduce the ``pose`` label the frozen Top-10 table used for this cell.

    The hits table names a pose with ``%.6g``, which is why 162.14262526406276 is stored as
    ``rotate_162.143``. Getting this wrong would silently drop cells from the cross-check, so the
    join is asserted cell by cell, not assumed.
    """
    if cell["slot"] == "control":
        return "unrotated"
    return f"rotate_{float(cell['angle_deg']):.6g}"


def _d6_independent_hit_check(
    cells: list[dict[str, Any]], hits_rows: list[dict[str, str]], k: int
) -> dict[str, Any]:
    """Recount the Top-K same-class candidates straight from the stored per-rank rows.

    This is the second witness the correction asks for: the summary is recomputed from the tidy
    cell table, and the cell table's numerator is re-derived here from a THIRD table (one row per
    rank), so a wrong mean cannot be checked only against the code that wrote it.
    """
    raw: dict[tuple[str, str, str], int] = {}
    for row in hits_rows:
        key = (row["arm"], row["query_id"], row["pose"])
        raw[key] = raw.get(key, 0) + int(row["is_same_class"] == "1")
    stored_keys = {(row["arm"], row["query_id"], row["pose"]) for row in hits_rows}
    mismatches: list[dict[str, Any]] = []
    unjoinable: list[str] = []
    zero_hit = 0
    for cell in cells:
        key = (cell["arm"], cell["query_id"], _d6_pose_label(cell))
        if key not in stored_keys:
            unjoinable.append("|".join(key))
            continue
        counted = raw[key]
        if counted == 0:
            zero_hit += 1
        derived = _d6_same_class_hits(cell, k)
        if counted != derived:
            mismatches.append({"cell": "|".join(key), "from_ranks": counted, "from_ratio": derived})
    return {
        "source": "d6_top10_hits.csv, one row per rank, counted by is_same_class",
        "n_cells_checked": len(cells) - len(unjoinable),
        "n_unjoinable": len(unjoinable),
        "unjoinable_cells": unjoinable[:20],
        "n_numerator_mismatches": len(mismatches),
        "mismatches": mismatches[:20],
        "n_zero_hit_cells": zero_hit,
        "note": (
            "The stored per-rank table covers the primary condition only, which is where the "
            "headline is quoted from; T0 and T1 keep the ratio-based recompute."
        ),
    }


def _d6_rank_arms(values: Mapping[str, float], higher_is_better: bool) -> list[str]:
    """Order arms by one metric. Getting the direction backwards inverts the whole verdict."""
    return sorted(values, key=lambda arm: values[arm], reverse=higher_is_better)


def correct_d6_rotation_statistics(top_k: int = 10) -> dict[str, Any]:
    """Recompute the D6 retrieval headline from the frozen per-cell table, without any inference.

    Two defects are corrected here and both are statistics-only:

    1. ``d6/1`` averaged the 12 zero-degree control cells into the 60 rotated cells of every
       (arm, condition) block, so a pose-blind wrapper was scored on 12 cells that ask it no
       rotation question at all.
    2. the column named ``class_recall_at_k`` has always been Precision@K. The corrected table
       renames it and adds a real Recall@K whose denominator is each query's own retained-relevant
       count (2, 3 or 4 under T2), so it is never a fixed 10.

    Nothing is re-run, deleted or overwritten: the inputs are hashed, the originals stay in place,
    and the output is a versioned document with a field-by-field before/after table.
    """
    import platform
    import subprocess

    cells = [_d6_cell(row) for row in read_csv(RESULTS / "d6_retrieval_results.csv")]
    hits_rows = read_csv(RESULTS / "d6_top10_hits.csv")
    stored = read_json(RESULTS / "d6_rotation_metrics.json")
    stored_arms = [arm["label"] for arm in stored["arms"]]
    corrected = {
        arm: _d6_summarise([row for row in cells if row["arm"] == arm], top_k)
        for arm in stored_arms
    }
    views_by_cell = metrics.split_rotation_and_control(cells)
    n_rotated = len(views_by_cell[metrics.ROTATION_VIEW])
    n_control = len(views_by_cell[metrics.CONTROL_VIEW])

    changes: dict[str, Any] = {}
    unchanged_counts: dict[str, Any] = {}
    material: list[dict[str, Any]] = []
    for arm in stored_arms:
        per_condition: dict[str, Any] = {}
        untouched: dict[str, list[str]] = {}
        for condition, block in stored["retrieval_by_arm"][arm].items():
            rotated_block = corrected[arm][metrics.ROTATION_VIEW][condition]
            moved: dict[str, Any] = {}
            kept: list[str] = []
            for field, before in block.items():
                if field in ("n_cells", "exact_target_present"):
                    continue
                after_field = D6_RENAMED_FIELDS.get(field, field)
                after = rotated_block.get(after_field)
                if before is None or after is None:
                    if before != after:
                        raise ValueError(f"{arm}/{condition}/{field}: cannot compare {before!r}")
                    kept.append(field)
                    continue
                delta = float(after) - float(before)
                if abs(delta) <= 1e-12:
                    kept.append(field)
                    continue
                entry: dict[str, Any] = {
                    "stored_field": field,
                    "corrected_field": after_field,
                    "before": float(before),
                    "after": float(after),
                    "delta": delta,
                    "control_only_value": (
                        corrected[arm][metrics.CONTROL_VIEW][condition][after_field]
                    ),
                }
                moved[field] = entry
                if abs(delta) >= 0.05:
                    material.append({"arm": arm, "condition": condition, **entry})
            added = sorted(
                set(rotated_block)
                - set(block)
                - {"view", "cell_filter", "n_rotated_cells", "n_control_cells", "note"}
            )
            dropped = sorted(set(block) - set(rotated_block) - {"n_cells", "exact_target_present"})
            per_condition[condition] = {
                "changed": moved,
                "n_changed": len(moved),
                "unchanged_fields": kept,
                "renamed_fields": {
                    before_field: after_field
                    for before_field, after_field in D6_RENAMED_FIELDS.items()
                    if before_field in block
                },
                "added_fields": {field: rotated_block[field] for field in added},
                "dropped_field_names": dropped,
            }
            untouched[condition] = len(kept)
        changes[arm] = per_condition
        unchanged_counts[arm] = untouched
    material.sort(key=lambda item: abs(item["delta"]), reverse=True)

    ranking_shift: dict[str, Any] = {}
    for condition in D6_CONDITIONS:
        per_metric: dict[str, Any] = {}
        for field in D6_RANKING_METRICS:
            stored_field = next(
                (name for name, new in D6_RENAMED_FIELDS.items() if new == field), field
            )
            after = {
                arm: corrected[arm][metrics.ROTATION_VIEW][condition][field]
                for arm in stored_arms
            }
            before = {
                arm: stored["retrieval_by_arm"][arm][condition].get(stored_field)
                for arm in stored_arms
            }
            if any(value is None for value in after.values()):
                raise ValueError(
                    f"the corrected rotation view has no {field} for every arm under {condition}; "
                    "refusing to rank an incomplete table"
                )
            # Cost is the one column where "best" is the smallest number; everything else is
            # higher-is-better, and getting that backwards would invert the whole verdict.
            higher_is_better = field != "seconds_per_query_mean"
            order_after = _d6_rank_arms(after, higher_is_better)
            entry: dict[str, Any] = {
                "direction": "higher_is_better" if higher_is_better else "lower_is_better",
                "stored_field": stored_field,
                "order_corrected": order_after,
                "top1_corrected": order_after[0],
            }
            if any(value is None for value in before.values()):
                # a metric the /1 table never published (true Recall@10) has no ranking to compare
                entry["available_in_stored"] = False
            else:
                before_values = {arm: float(value) for arm, value in before.items()}
                order_before = _d6_rank_arms(before_values, higher_is_better)
                rank_before = {arm: index for index, arm in enumerate(order_before, start=1)}
                rank_after = {arm: index for index, arm in enumerate(order_after, start=1)}
                entry.update(
                    {
                        "available_in_stored": True,
                        "order_stored": order_before,
                        "position_change": {
                            arm: rank_after[arm] - rank_before[arm] for arm in stored_arms
                        },
                        "n_arms_moved": sum(
                            1 for arm in stored_arms if rank_before[arm] != rank_after[arm]
                        ),
                        "top1_stored": order_before[0],
                        "max_position_change": max(
                            abs(rank_after[arm] - rank_before[arm]) for arm in stored_arms
                        ),
                    }
                )
            per_metric[field] = entry
        ranking_shift[condition] = per_metric

    t2_cells = [row for row in cells if row["condition"] == D6_PRIMARY_CONDITION]
    hit_check = _d6_independent_hit_check(t2_cells, hits_rows, top_k)
    if hit_check["n_numerator_mismatches"] or hit_check["n_unjoinable"]:
        raise ValueError(
            "the frozen per-rank table does not agree with the frozen per-cell table; refusing to "
            f"publish a correction on top of an unresolved disagreement ({hit_check})"
        )

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"

    def file_record(name: str) -> dict[str, Any]:
        path = RESULTS / name
        return {
            "path": _repo_relative(path),
            "sha256": _file_sha256(path),
            "bytes": path.stat().st_size,
        }

    payload: dict[str, Any] = {
        "schema": D6_CORRECTED_SCHEMA,
        "task": "P0-4.6 CP8-A",
        "seed": SEED,
        "top_k": top_k,
        "supersedes_only": ["retrieval_by_arm"],
        "audit": {
            "reason": (
                "d6/1 aggregated every (arm, condition) block over all 72 stored cells, including "
                "the 12 unrotated control rows, and published a Precision@10 under the name "
                "class_recall_at_k. Both are reading errors baked into the summary; neither is a "
                "new experiment."
            ),
            "method": (
                "statistics-only recompute from the stored per-cell table. No ShapeComp inference "
                "was re-run, no query or angle changed, no 0-degree row deleted, and every arm is "
                "filtered by the same rule."
            ),
            "command": "python scripts/run_shape_benchmark.py --stage d6-correct",
            "generated_by_python": platform.python_version(),
            "numpy": np.__version__,
            "git_commit": commit,
            "original_file": {
                **file_record("d6_rotation_metrics.json"),
                "schema": stored["schema"],
            },
            "corrected_file": {
                "path": _repo_relative(RESULTS / D6_CORRECTED_ARTEFACT),
                "schema": D6_CORRECTED_SCHEMA,
                # A file cannot contain the hash of its own bytes, so the self-hash covers the
                # numbers a reader quotes; the whole-document sha256 is recorded in notes.md after
                # the run, and a test re-derives this one from the artefact on disk.
                "content_sha256": hashlib.sha256(
                    json.dumps(corrected, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "content_sha256_scope": "retrieval_by_arm, canonical JSON, sorted keys",
            },
            "inputs": {
                "d6_retrieval_results.csv": {
                    **file_record("d6_retrieval_results.csv"),
                    "n_rows": len(cells),
                },
                "d6_top10_hits.csv": {
                    **file_record("d6_top10_hits.csv"),
                    "n_rows": len(hits_rows),
                },
            },
            "history_untouched": [
                "d6_rotation_metrics.json",
                "d6_retrieval_results.csv",
                "d6_top10_hits.csv",
                "d6_agreement_results.csv",
            ],
        },
        "views": {
            "definitions": {
                metrics.ROTATION_VIEW: (
                    "the headline. Cells whose query was actually turned, so every rotation "
                    "statistic is measured on poses that can fail."
                ),
                metrics.CONTROL_VIEW: (
                    "diagnostic only. The 0-degree poses, reported apart because a control cell "
                    "asks an unrotated question: it can show the pipeline is wired correctly, and "
                    "it cannot show that a wrapper survives a turn."
                ),
            },
            "cell_filter": {
                metrics.ROTATION_VIEW: 'slot != "control"',
                metrics.CONTROL_VIEW: 'slot == "control"',
            },
            "n_rotated_cells_per_arm_condition": {
                arm: {
                    condition: len(
                        [
                            row
                            for row in cells
                            if row["arm"] == arm
                            and row["condition"] == condition
                            and row["slot"] != "control"
                        ]
                    )
                    for condition in D6_CONDITIONS
                }
                for arm in stored_arms
            },
            "n_control_cells_per_arm_condition": {
                arm: {
                    condition: len(
                        [
                            row
                            for row in cells
                            if row["arm"] == arm
                            and row["condition"] == condition
                            and row["slot"] == "control"
                        ]
                    )
                    for condition in D6_CONDITIONS
                }
                for arm in stored_arms
            },
            "n_stored_cells_total": len(cells),
            "n_rotated_rows_total": n_rotated,
            "n_control_rows_total": n_control,
            "arithmetic": (
                f"{len(stored['design']['queries'])} queries x "
                f"{len(stored['design']['angle_slots'])} rotated slots = "
                f"{n_rotated // len(stored_arms) // len(D6_CONDITIONS)} rotated cells per "
                f"(arm, condition), plus {n_control // len(stored_arms) // len(D6_CONDITIONS)} "
                "0-degree controls = the 72 rows the /1 block averaged"
            ),
        },
        "metric_definitions": {
            f"precision_at_{top_k}": (
                f"same-class share OF THE RETURNED Top-{top_k}: hits / min(k, n_returned). This is "
                "the number /1 published as class_recall_at_k, unchanged, correctly named"
            ),
            f"recall_at_{top_k}": (
                f"share OF THE RETAINED RELEVANT CANDIDATES that reached the Top-{top_k}: "
                "hits / n_same_class_kept for that query and condition. The denominator is per "
                "query (2, 3 or 4 under T2 here), never a constant"
            ),
            "recall_undefined": (
                f"a cell with n_same_class_kept == 0 has no relevant candidate left to find, so "
                f"recall_at_{top_k} is undefined; such cells are counted in "
                f"recall_at_{top_k}_undefined_cells and excluded from the mean instead of being "
                "written down as a 0.0 failure"
            ),
            "ndcg": (
                f"unchanged arithmetic; ndcg_at_{top_k}_n_scored now states how many cells "
                "contributed, because NDCG@K already skipped cells that found nothing relevant"
            ),
            "class_relevance": (
                "MPEG-7 category labels are a relevance proxy for automated metrics only. They are "
                "not human similarity judgements and are never presented as such."
            ),
        },
        "independent_check_from_raw_ranks": hit_check,
        "per_field_changes": changes,
        "n_unchanged_fields_per_arm_condition": unchanged_counts,
        "material_changes": material,
        "material_change_threshold": 0.05,
        "arm_rankings_before_vs_after": ranking_shift,
        "retrieval_by_arm": corrected,
        "agreement_side_unchanged": {
            "fields": [
                "agreement_by_arm",
                "agreement_bootstrap",
                "paired_differences",
                "paired_differences_angle_randomised",
            ],
            "why": (
                "the H1 agreement study is a different table: 250 rated pairs x 7 stress angles = "
                "1,750 rows with a grouped bootstrap over pairs, and it never contained a 0-degree "
                "control row. Its sampling unit and denominators are untouched by this correction."
            ),
            "source": _repo_relative(RESULTS / "d6_rotation_metrics.json"),
            "source_sha256": _file_sha256(RESULTS / "d6_rotation_metrics.json"),
        },
    }
    write_json(RESULTS / D6_CORRECTED_ARTEFACT, payload)
    print(
        f"  {len(cells)} cells -> {n_rotated} rotated + {n_control} control rows; "
        f"{len(material)} material changes over {len(stored_arms)} arms",
        flush=True,
    )
    return payload


# ---------------------------------------------------------------- CP8-C independent validation

CP8_OUT = BENCH / "checkpoint8"
CP8_DESIGN_PATH = CP8_OUT / "independent_validation_design.json"
#: A partial sample can only ever land here. The directory is chosen by the code from the query
#: count, not by a caller remembering a flag, so a smoke run cannot be mistaken for the table.
CP8_SMOKE_DIR = CP8_OUT / "_smoke"
CP8_RESULT_SCHEMA = "earthglyph.shape_perception_benchmark/independent_validation_results/1"
CP8_ARTEFACTS = (
    "cp8_retrieval_results.csv",
    "cp8_top10_hits.csv",
    "cp8_exclusions.csv",
    "independent_metrics.json",
)
MANIFEST = BENCH / "dataset_manifest.json"

#: The numeric columns of one CP8 cell, split by the type they must come back as. A cell table is
#: the only place the headline is derived from, so reading it back has to be unambiguous.
CP8_CELL_FLOAT_FIELDS: tuple[str, ...] = (
    "angle_deg",
    "self_distance",
    "best_wrong_class_distance",
    "precision_at_10",
    "recall_at_10",
    "ndcg_at_10",
    "top_10_overlap_vs_unrotated",
    "mean_abs_rank_shift",
    "max_abs_rank_shift",
    "spearman_between_rankings",
    "elapsed_seconds",
)
CP8_CELL_INT_FIELDS: tuple[str, ...] = (
    "n_kept",
    "n_same_class_kept",
    "same_class_hits_at_10",
    "top1_is_target",
    "top1_is_same_class",
    "top1_matches_unrotated",
    "target_rank",
    "first_relevant_rank",
)

#: Headline name -> the per-cell value it is a mean of. Only mean-type statistics get an interval:
#: a median or a maximum over cells does not decompose per query, so resampling queries would not
#: be measuring the same quantity the point estimate reports.
CP8_CI_STATISTICS: dict[str, Callable[[Mapping[str, Any]], float | None]] = {
    "top1_accuracy": lambda row: row["top1_is_target"],
    "top1_same_class_accuracy": lambda row: row["top1_is_same_class"],
    "top1_stability_vs_unrotated": lambda row: row["top1_matches_unrotated"],
    "target_rank_mean": lambda row: row["target_rank"],
    "self_distance_mean": lambda row: row["self_distance"],
    "attraction_gap_mean": lambda row: (
        None
        if row["best_wrong_class_distance"] is None
        else float(row["best_wrong_class_distance"]) - float(row["self_distance"])
    ),
    "precision_at_10": lambda row: row["precision_at_10"],
    "recall_at_10": lambda row: row["recall_at_10"],
    "ndcg_at_10": lambda row: row["ndcg_at_10"],
    "class_mrr": lambda row: (
        1.0 / int(row["first_relevant_rank"]) if row["first_relevant_rank"] else 0.0
    ),
    "top_10_overlap_vs_unrotated": lambda row: row["top_10_overlap_vs_unrotated"],
    "mean_abs_rank_shift": lambda row: row["mean_abs_rank_shift"],
    "seconds_per_query_mean": lambda row: row["elapsed_seconds"],
}

#: Metric -> whether a larger value is the better arm. Quoting the wrong direction flips a verdict.
CP8_RANKING_DIRECTIONS: dict[str, bool] = {
    "top1_same_class_accuracy": True,
    "top1_stability_vs_unrotated": True,
    "precision_at_10": True,
    "recall_at_10": True,
    "ndcg_at_10": True,
    "class_mrr": True,
    "top_10_overlap_vs_unrotated": True,
    "mean_abs_rank_shift": False,
    "target_rank_mean": False,
    "seconds_per_query_mean": False,
}


def _cp8_gate(design_path: Path = CP8_DESIGN_PATH) -> dict[str, Any]:
    """Load the frozen CP8-B design, re-deriving it from the seed instead of trusting the file.

    "Independent" is only a claim if the sample cannot have been edited between freezing and
    scoring. Every check below therefore recomputes what the stored file says it froze: the content
    hash, the 120 angles, the arm labels, the cell arithmetic. A mismatch is a stop, not a warning.
    """
    if not design_path.exists():
        raise SystemExit(
            f"no frozen design at {_repo_relative(design_path)}. CP8-C scores a sample, it does "
            "not invent one: run python scripts/design_checkpoint8_validation.py first, before "
            "any retrieval."
        )
    design = read_json(design_path)
    problems: list[str] = []

    if design.get("schema") != iv.CP8_SCHEMA:
        problems.append(f"schema {design.get('schema')!r} is not the frozen {iv.CP8_SCHEMA!r}")
    if design.get("seed") != iv.CP8_SEED:
        problems.append(f"seed {design.get('seed')!r} is not the frozen {iv.CP8_SEED}")
    queries = list(design.get("queries", []))
    stored_hash = str(design.get("content_sha256"))
    derived_hash = iv.design_content_sha256(design)
    if stored_hash != derived_hash:
        problems.append(f"content_sha256 {stored_hash} != re-derived {derived_hash}")
    stored_rows = list(design.get("angle_design", {}).get("rows", []))
    redrawn = iv.draw_angles(np.random.default_rng(iv.CP8_SEED), len(queries))
    if [(int(a["query_index"]), str(a["slot"]), float(a["angle_deg"])) for a in stored_rows] != [
        (int(a["query_index"]), str(a["slot"]), float(a["angle_deg"])) for a in redrawn
    ]:
        problems.append(
            f"the {len(stored_rows)} stored angles are not what seed {iv.CP8_SEED} draws "
            f"({len(redrawn)} rows) -- an angle was redrawn after the freeze"
        )
    labels = list(design.get("arms", {}).get("labels", []))
    if labels != list(iv.CP8_ARMS):
        problems.append(f"arms {labels} are not the frozen {list(iv.CP8_ARMS)}")

    slots = len(design.get("angle_design", {}).get("slots_per_query", []))
    arithmetic = design.get("cell_arithmetic", {})
    for key, expected in (
        ("n_rotated_cells_per_arm_condition", len(queries) * slots),
        ("n_control_cells_per_arm_condition", len(queries)),
        ("n_cells_per_arm_condition", len(queries) * (slots + 1)),
    ):
        if arithmetic.get(key) != expected:
            problems.append(f"cell_arithmetic.{key}={arithmetic.get(key)!r} != {expected}")
    if problems:
        raise SystemExit(
            "the design on disk is not the design CP8-B froze:\n  "
            + "\n  ".join(problems)
            + "\nRefusing to score it: results from a sample that moved are not the promised test."
        )
    return design


def _cp8_arms(labels: Sequence[str]) -> list[D6Arm]:
    """Map the design's six labels onto the frozen D6 arm table, so no arm is invented here."""
    frozen = {arm.label: arm for arm in _d6_arms(list(D6_FROZEN_METHODS))}
    unknown = [label for label in labels if label not in frozen]
    if unknown:
        raise SystemExit(
            f"CP8 arm labels that are not frozen D6 arms: {unknown}. A new method inside what is "
            "meant to be an independent test of existing ones is a different study."
        )
    return [frozen[label] for label in labels]


def _cp8_toolbox(arms: Sequence[D6Arm]) -> dict[str, Any]:
    """Report the official-weights dependency, and stop if a needed arm cannot run.

    The port arms read the gitignored official toolbox. Silently dropping them would publish a
    table that looks like the promised six-arm comparison and is not, so the absence is an error.
    """
    needed = sorted({arm.method for arm in arms if arm.method in SHAPECOMP_RUN_METHODS})
    if not needed:
        return {"needed_networks": [], "status": "no arm in this table needs the official weights"}
    try:
        nets = _load_shapecomp_nets()
    except (OSError, ImportError) as error:
        blocked = [arm.label for arm in arms if arm.method in needed]
        raise SystemExit(
            f"the official toolbox is unavailable ({error!r}), so these frozen CP8 arms cannot "
            f"run: {blocked}. They are reported as blocked, not dropped: a narrower table would "
            "look like the promised comparison and not be one. Put the gitignored "
            "shapeComp_17_12_2024 directory back, or run a study that says it covers fewer arms."
        ) from error
    return {
        "needed_networks": needed,
        "toolbox": str(port.DEFAULT_TOOLBOX),
        "weights_sha256": dict(nets.weight_sha256),
        "status": "official weights loaded read-only; nothing here modifies them",
    }


def _cp8_cluster_values(
    cells: Sequence[Mapping[str, Any]],
    extract: Callable[[Mapping[str, Any]], float | None],
) -> dict[str, list[float]]:
    """Group one statistic by its original query, dropping cells where it is undefined."""
    grouped: dict[str, list[float]] = {}
    for cell in cells:
        value = extract(cell)
        if value is None or not np.isfinite(float(value)):
            continue
        grouped.setdefault(str(cell["query_id"]), []).append(float(value))
    return grouped


def _cp8_cluster_ci(
    values_by_query: Mapping[str, Sequence[float]], *, n_boot: int, seed: int
) -> dict[str, Any]:
    """Percentile interval for a mean whose sampling unit is the query, never the cell.

    Five rotated cells share one shape, so treating 120 cells as 120 independent draws would
    narrow every interval by about sqrt(5) and invent significance the design does not support.
    """
    if not values_by_query:
        return {"estimate": None, "n_clusters": 0, "n_cells": 0, "note": "no defined cell"}
    keys = sorted(values_by_query)
    arrays = [np.asarray(values_by_query[key], dtype=float) for key in keys]
    pooled = np.concatenate(arrays)
    rng = np.random.default_rng(seed)
    replicates = np.empty(n_boot, dtype=float)
    for index in range(n_boot):
        drawn = rng.integers(0, len(arrays), len(arrays))
        replicates[index] = float(np.mean(np.concatenate([arrays[int(i)] for i in drawn])))
    low, high = np.percentile(replicates, [2.5, 97.5])
    return {
        "estimate": float(pooled.mean()),
        "ci_low": float(low),
        "ci_high": float(high),
        "n_boot": n_boot,
        "bootstrap_seed": seed,
        "sampling_unit": "original query, with all of its cells moving together",
        "n_clusters": len(keys),
        "n_cells": int(pooled.size),
        "cluster_size_min": int(min(array.size for array in arrays)),
        "cluster_size_max": int(max(array.size for array in arrays)),
    }


def _cp8_cell(row: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce one cell to measurable types, whether it came from memory or from the CSV."""
    out: dict[str, Any] = dict(row)
    for field in CP8_CELL_FLOAT_FIELDS:
        value = out.get(field)
        out[field] = (
            None if value is None or str(value).strip() in ("", "None", "nan") else float(value)
        )
    for field in CP8_CELL_INT_FIELDS:
        value = out.get(field)
        out[field] = (
            None if value is None or str(value).strip() in ("", "None", "nan")
            else int(round(float(value)))
        )
    return out


def _cp8_condition_row(
    arm: D6Arm,
    condition: str,
    entry: Mapping[str, Any],
    angle_row: Mapping[str, Any],
    distances: np.ndarray,
    gallery: Gallery,
    target_index: int,
    drop: Sequence[str],
    ref_order: np.ndarray,
    elapsed: float,
    details: Mapping[str, Any],
    k: int = iv.CP8_TOP_K,
) -> dict[str, Any]:
    """Every headline statistic for one (arm, condition, query, pose) cell, correctly named.

    This is the CP8-A convention applied to a new table from the start: rotated cells and control
    cells are distinguished by ``slot``, and the same-class ratio is published as
    ``precision_at_10`` next to a real ``recall_at_10`` whose denominator is this query's own
    retained relevant count -- not a fixed 10, and not the legacy misnomer.
    """
    query_category = str(entry["class"])
    order = _d6_rank(distances, gallery, drop)
    positions = {int(index): rank for rank, index in enumerate(order, start=1)}
    categories = [str(gallery.shapes[int(index)].category) for index in order]
    relevant = [1 if category == query_category else 0 for category in categories]
    n_relevant = int(sum(relevant))
    hits = metrics.same_class_hits_in_top_k(categories, query_category, k)
    target_rank = positions.get(int(target_index))
    first_relevant = next((rank for rank, flag in enumerate(relevant, start=1) if flag), None)
    top1 = gallery.shapes[int(order[0])].shape_id
    wrong = np.array(
        [
            float(distances[int(index)])
            for index in range(len(gallery))
            if str(gallery.shapes[int(index)].category) != query_category
            and np.isfinite(distances[int(index)])
        ]
    )
    displacement = metrics.rank_displacement(ref_order, order)
    return {
        "arm": arm.label,
        "method": arm.method,
        "rotation_strategy": arm.strategy,
        "orientation": arm.orientation,
        "condition": condition,
        "query_id": str(entry["shape_id"]),
        "family": str(entry["family"]),
        "query_class": query_category,
        "slot": str(angle_row["slot"]),
        "angle_deg": float(angle_row["angle_deg"]),
        "n_kept": int(len(order)),
        "n_same_class_kept": n_relevant,
        f"same_class_hits_at_{k}": hits,
        "top1": top1,
        "top1_is_target": int(top1 == gallery.shapes[int(target_index)].shape_id),
        "top1_is_same_class": int(categories[0] == query_category),
        "top1_matches_unrotated": int(top1 == gallery.shapes[int(ref_order[0])].shape_id),
        "target_rank": target_rank,
        "self_distance": float(distances[int(target_index)]),
        "best_wrong_class_distance": float(wrong.min()) if wrong.size else None,
        f"precision_at_{k}": metrics.precision_at_k(categories, query_category, k),
        f"recall_at_{k}": metrics.recall_at_k(categories, query_category, n_relevant, k),
        "first_relevant_rank": first_relevant,
        f"ndcg_at_{k}": metrics.ndcg_at_k(relevant, k) if first_relevant else None,
        f"top_{k}_overlap_vs_unrotated": metrics.top_k_overlap(ref_order, order, k),
        "mean_abs_rank_shift": displacement["mean_abs_shift"],
        "max_abs_rank_shift": float(displacement["max_abs_shift"]),
        "spearman_between_rankings": displacement["spearman_between_rankings"],
        "forward_passes": details.get("forward_passes"),
        "query_poses": details.get("query_poses"),
        "elapsed_seconds": elapsed,
    }


def _cp8_summarise_cells(
    cells: list[dict[str, Any]],
    view: str,
    condition: str,
    n_rotated: int,
    n_control: int,
    *,
    k: int = iv.CP8_TOP_K,
    n_boot: int = iv.CP8_N_BOOT,
    seed: int = iv.CP8_SEED,
    with_ci: bool = True,
) -> dict[str, Any]:
    """Headline for one (view, condition), over exactly the cells that view may use."""
    ranks = [int(row["target_rank"]) for row in cells if row["target_rank"]]
    recall = [row[f"recall_at_{k}"] for row in cells if row[f"recall_at_{k}"] is not None]
    ndcg = [row[f"ndcg_at_{k}"] for row in cells if row[f"ndcg_at_{k}"] is not None]
    denominators = [int(row["n_same_class_kept"]) for row in cells]
    gaps = [
        float(row["best_wrong_class_distance"]) - float(row["self_distance"])
        for row in cells
        if row["best_wrong_class_distance"] is not None
    ]
    seconds = [float(row["elapsed_seconds"]) for row in cells]
    out: dict[str, Any] = {
        "view": view,
        "cell_filter": 'slot == "control"' if view == metrics.CONTROL_VIEW else 'slot != "control"',
        "n_cells": len(cells),
        "n_rotated_cells": n_rotated,
        "n_control_cells": n_control,
        "exact_target_present": condition == iv.CP8_CHECK_CONDITION,
        "top1_accuracy": _d6_mean([row["top1_is_target"] for row in cells]),
        "top1_same_class_accuracy": _d6_mean([row["top1_is_same_class"] for row in cells]),
        "top1_stability_vs_unrotated": _d6_mean([row["top1_matches_unrotated"] for row in cells]),
        "target_rank_mean": _d6_mean(ranks),
        "target_rank_median": float(np.median(ranks)) if ranks else None,
        "target_rank_worst": int(max(ranks)) if ranks else None,
        "n_target_rank_undefined": len(cells) - len(ranks),
        "self_distance_mean": _d6_mean([row["self_distance"] for row in cells]),
        "self_distance_max": float(max(row["self_distance"] for row in cells)),
        "attraction_gap_mean": _d6_mean(gaps),
        f"precision_at_{k}": _d6_mean([row[f"precision_at_{k}"] for row in cells]),
        f"recall_at_{k}": float(np.mean(recall)) if recall else None,
        f"recall_at_{k}_n_scored": len(recall),
        f"recall_at_{k}_undefined_cells": len(cells) - len(recall),
        "n_relevant_retained_mean": _d6_mean(denominators),
        "n_relevant_retained_min": min(denominators),
        "n_relevant_retained_max": max(denominators),
        "class_mrr": metrics.mean_reciprocal_rank([row["first_relevant_rank"] for row in cells]),
        f"ndcg_at_{k}": _d6_mean([row[f"ndcg_at_{k}"] for row in cells]),
        f"ndcg_at_{k}_n_scored": len(ndcg),
        f"top_{k}_overlap_vs_unrotated": _d6_mean(
            [row[f"top_{k}_overlap_vs_unrotated"] for row in cells]
        ),
        f"top_{k}_overlap_min": float(min(row[f"top_{k}_overlap_vs_unrotated"] for row in cells)),
        "mean_abs_rank_shift": _d6_mean([row["mean_abs_rank_shift"] for row in cells]),
        "max_abs_rank_shift": float(max(row["max_abs_rank_shift"] for row in cells)),
        "seconds_per_query_mean": _d6_mean(seconds),
        "seconds_per_query_p95": float(np.percentile(seconds, 95)),
        "seconds_per_query_max": float(max(seconds)),
    }
    if view == metrics.CONTROL_VIEW:
        out["note"] = (
            "unrotated poses only. top1_stability_vs_unrotated is 1.0 here by construction, so "
            "this view is a wiring check on the pipeline and never a rotation measurement."
        )
    elif with_ci:
        out["query_clustered_ci"] = {
            name: _cp8_cluster_ci(_cp8_cluster_values(cells, extract), n_boot=n_boot, seed=seed)
            for name, extract in CP8_CI_STATISTICS.items()
        }
    return out


def _cp8_summarise(
    rows: list[dict[str, Any]],
    *,
    k: int = iv.CP8_TOP_K,
    n_boot: int = iv.CP8_N_BOOT,
    seed: int = iv.CP8_SEED,
) -> dict[str, Any]:
    """Collapse the tidy cells into per-view, per-condition headlines. Two views, never pooled."""
    cells = [_cp8_cell(row) for row in rows]
    by_view = metrics.split_rotation_and_control(cells, control_slot=iv.CP8_CONTROL_SLOT)
    conditions = [iv.CP8_PRIMARY_CONDITION, iv.CP8_CHECK_CONDITION]
    counts = {
        view: {
            condition: sum(1 for row in view_rows if row["condition"] == condition)
            for condition in conditions
        }
        for view, view_rows in by_view.items()
    }
    out: dict[str, Any] = {}
    for view in metrics.VIEWS:
        per_condition: dict[str, Any] = {}
        for condition in conditions:
            view_cells = [row for row in by_view[view] if row["condition"] == condition]
            if not view_cells:
                continue
            per_condition[condition] = _cp8_summarise_cells(
                view_cells,
                view,
                condition,
                n_rotated=counts[metrics.ROTATION_VIEW][condition],
                n_control=counts[metrics.CONTROL_VIEW][condition],
                k=k,
                n_boot=n_boot,
                seed=seed,
            )
        out[view] = per_condition
    return out


def _cp8_slot_view(rows: list[dict[str, Any]], *, k: int = iv.CP8_TOP_K) -> dict[str, Any]:
    """Rotation behaviour split by angle slot: what an on-grid pose hides from a coarse wrapper."""
    out: dict[str, Any] = {}
    for arm_label in dict.fromkeys(str(row["arm"]) for row in rows):
        by_slot: dict[str, Any] = {}
        for slot in iv.CP8_ANGLE_SLOTS:
            cells = [
                _cp8_cell(row)
                for row in rows
                if row["arm"] == arm_label
                and row["condition"] == iv.CP8_PRIMARY_CONDITION
                and row["slot"] == slot
            ]
            if not cells:
                continue
            by_slot[slot] = {
                "n_cells": len(cells),
                "top1_same_class_accuracy": _d6_mean(
                    [row["top1_is_same_class"] for row in cells]
                ),
                "top1_stability_vs_unrotated": _d6_mean(
                    [row["top1_matches_unrotated"] for row in cells]
                ),
                f"top_{k}_overlap_vs_unrotated": _d6_mean(
                    [row[f"top_{k}_overlap_vs_unrotated"] for row in cells]
                ),
                "mean_abs_rank_shift": _d6_mean([row["mean_abs_rank_shift"] for row in cells]),
                "first_relevant_rank_mean": _d6_mean(
                    [row["first_relevant_rank"] for row in cells]
                ),
            }
        out[arm_label] = by_slot
    return out


def _cp8_rankings(
    retrieval_by_arm: Mapping[str, Mapping[str, Mapping[str, dict[str, Any]]]],
) -> dict[str, Any]:
    """Rank the arms by each headline statistic, so no single metric can be quoted as the winner."""
    headline = {
        arm: blocks[metrics.ROTATION_VIEW][iv.CP8_PRIMARY_CONDITION]
        for arm, blocks in retrieval_by_arm.items()
    }
    out: dict[str, Any] = {}
    for metric_name, higher_is_better in CP8_RANKING_DIRECTIONS.items():
        values = {
            arm: float(block[metric_name])
            for arm, block in headline.items()
            if block.get(metric_name) is not None
        }
        out[metric_name] = {
            "higher_is_better": higher_is_better,
            "order": _d6_rank_arms(values, higher_is_better),
            "values": values,
        }
    return out


def run_cp8(top_k: int = 10, limit_queries: int = 0) -> dict[str, Any]:
    """Score the frozen CP8-B sample with the six frozen arms -- and change nothing else.

    Nothing here picks a query, an angle, a gallery member or a threshold after seeing a result:
    the design file is re-derived from its seed before the first search runs. The library is the
    whole MPEG-7 archive because 22 of the 24 frozen queries are not in the 5-per-class D4 gallery
    that D6 used; the choice is recorded in provenance rather than passed off as the same gallery.
    """
    import platform
    import subprocess

    if top_k != iv.CP8_TOP_K:
        raise SystemExit(
            f"--top-k {top_k} is not the frozen CP8 top_k {iv.CP8_TOP_K}: the design promised one "
            "list length, and scoring another is a different study."
        )
    design = _cp8_gate()
    arms = _cp8_arms(list(design["arms"]["labels"]))
    weights = _cp8_toolbox(arms)

    smoke = 0 < limit_queries < len(design["queries"])
    out_dir = CP8_SMOKE_DIR if smoke else CP8_OUT
    queries = list(design["queries"])[: limit_queries or len(design["queries"])]
    if smoke:
        print(
            f"  SMOKE RUN: {len(queries)} of {len(design['queries'])} queries -> "
            f"{_repo_relative(out_dir)}; this is plumbing, not a result, and it is never published "
            "in checkpoint8/",
            flush=True,
        )

    shapes, excluded_images = load_mpeg7(CACHE)
    by_stem = {shape.shape_id.removeprefix("mpeg7_"): shape for shape in shapes}
    missing = [entry["stem"] for entry in queries if entry["stem"] not in by_stem]
    if missing:
        raise SystemExit(f"frozen CP8 queries absent from the archive: {missing}")
    archive = CACHE / "MPEG7dataset.zip"
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    frozen_archive_sha = design["provenance"]["dataset"]["archive_sha256"]
    if archive_sha != frozen_archive_sha:
        raise SystemExit(
            f"the MPEG-7 archive on disk hashes to {archive_sha}, not the {frozen_archive_sha} "
            "the design was frozen against. A different dataset build is a different study."
        )
    redrew = iv.select_queries(shapes)
    if [(row["stem"], row["class"], row["family"]) for row in redrew] != [
        (row["stem"], row["class"], row["family"]) for row in design["queries"]
    ]:
        raise SystemExit(
            "the selection rules now derive a different 24 shapes than the design froze. The "
            "sample moved, so this is no longer the promised independent test."
        )

    gallery_started = time.perf_counter()
    gallery = Gallery(shapes, name="mpeg7_full")
    precompute = {"gallery_mpeg7_full": time.perf_counter() - gallery_started}
    for network in SHAPECOMP_RUN_METHODS:
        if not any(arm.method == network for arm in arms):
            continue
        started = time.perf_counter()
        for strategy in ("R3", "R2"):
            gallery.pose_embeddings(_RUN_NETWORK[network], strategy)
        precompute[f"gallery_pose_embeddings_{network}"] = time.perf_counter() - started

    angles_by_index: dict[int, list[dict[str, Any]]] = {}
    for row in design["angle_design"]["rows"]:
        angles_by_index.setdefault(int(row["query_index"]), []).append(row)

    run_started = time.perf_counter()
    cells: list[dict[str, Any]] = []
    hit_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    orders: dict[str, np.ndarray] = {}
    conditions = [iv.CP8_PRIMARY_CONDITION, iv.CP8_CHECK_CONDITION]
    for index, entry in enumerate(queries):
        base = by_stem[entry["stem"]]
        target_index = int(gallery.index_of(base.shape_id))
        query_category = str(base.category)
        if query_category != str(entry["class"]):
            raise SystemExit(
                f"{entry['stem']} is class {query_category} in the archive on disk but "
                f"{entry['class']} in the design -- the dataset changed under a frozen sample"
            )
        e0_full = search_shape(base, gallery, "E0", top_k=len(gallery))
        near = [
            gallery.shapes[int(position)].shape_id
            for position in e0_full.order
            if e0_full.distances[int(position)] < T2_THRESHOLD
            and gallery.shapes[int(position)].shape_id != base.shape_id
        ]
        drops = {
            iv.CP8_CHECK_CONDITION: [],
            iv.CP8_PRIMARY_CONDITION: [base.shape_id, *near],
        }
        exclusion_rows.append(
            {
                "query_id": base.shape_id,
                "family": entry["family"],
                "query_class": query_category,
                "n_gallery": len(gallery),
                "n_near_duplicates": len(near),
                "near_duplicate_ids": json.dumps(near),
                "n_kept_t0": len(gallery) - len(drops[iv.CP8_CHECK_CONDITION]),
                "n_kept_t2": len(gallery) - len(drops[iv.CP8_PRIMARY_CONDITION]),
                "n_same_class_in_gallery": sum(
                    1 for shape in gallery.shapes if str(shape.category) == query_category
                ),
                "rule": (
                    f"E0 aligned distance < {T2_THRESHOLD}, defined once per query for all arms"
                ),
            }
        )
        angle_rows = angles_by_index.get(index, [])
        if len(angle_rows) != len(design["angle_design"]["slots_per_query"]):
            raise SystemExit(f"design angle rows for query {index} are incomplete")
        for arm in arms:
            reference = arm.search(base, gallery, len(gallery))
            ref_orders = {
                condition: _d6_rank(reference.distances, gallery, drop)
                for condition, drop in drops.items()
            }
            poses: list[tuple[str, Mapping[str, Any], BenchmarkShape, Any]] = [
                (
                    "unrotated",
                    {"slot": iv.CP8_CONTROL_SLOT, "angle_deg": 0.0},
                    base,
                    reference,
                )
            ] + [
                (
                    f"rotate_{row['angle_deg']:g}",
                    row,
                    _rotated(base, float(row["angle_deg"])),
                    None,
                )
                for row in angle_rows
            ]
            for pose_label, angle_row, query, precomputed in poses:
                result = precomputed if precomputed is not None else arm.search(
                    query, gallery, len(gallery)
                )
                for condition, drop in drops.items():
                    cells.append(
                        _cp8_condition_row(
                            arm,
                            condition,
                            entry,
                            angle_row,
                            result.distances,
                            gallery,
                            target_index,
                            drop,
                            ref_orders[condition],
                            result.elapsed_seconds,
                            result.details,
                            top_k,
                        )
                    )
                    if condition == iv.CP8_PRIMARY_CONDITION and not smoke:
                        orders[f"{arm.label}|{base.shape_id}|{pose_label}"] = _d6_rank(
                            result.distances, gallery, drop
                        )
                    for rank, position in enumerate(
                        _d6_rank(result.distances, gallery, drop)[:top_k], start=1
                    ):
                        hit_rows.append(
                            {
                                "query_id": base.shape_id,
                                "family": entry["family"],
                                "arm": arm.label,
                                "condition": condition,
                                "pose": pose_label,
                                "slot": angle_row["slot"],
                                "angle_deg": angle_row["angle_deg"],
                                "rank": rank,
                                "shape_id": gallery.shapes[int(position)].shape_id,
                                "category": gallery.shapes[int(position)].category,
                                "distance": float(result.distances[int(position)]),
                                "is_same_class": int(
                                    str(gallery.shapes[int(position)].category) == query_category
                                ),
                            }
                        )
        print(
            f"  {entry['stem']:14s} {query_category:14s} near-duplicates={len(near):2d} "
            f"cells={len(cells):5d} elapsed={time.perf_counter() - run_started:7.1f}s",
            flush=True,
        )

    started_run = time.perf_counter()
    retrieval_by_arm = {
        arm.label: _cp8_summarise([row for row in cells if row["arm"] == arm.label])
        for arm in arms
    }
    rotated_t2 = [
        row
        for row in cells
        if row["condition"] == iv.CP8_PRIMARY_CONDITION and row["slot"] != iv.CP8_CONTROL_SLOT
    ]
    undefined = sorted({str(row["slot"]) for row in rotated_t2} - set(iv.CP8_ANGLE_SLOTS))
    if undefined:
        raise SystemExit(f"cells carry slots the frozen design never declared: {undefined}")

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    mpeg7_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["mpeg7"]

    summary: dict[str, Any] = {
        "schema": CP8_RESULT_SCHEMA,
        "task": "P0-4.6 CP8-C",
        "objective": (
            "test the frozen rotation wrappers on a sample that never entered any earlier grid, "
            "after CP8-A made the existing statistics trustworthy"
        ),
        "design": {
            "path": _repo_relative(CP8_DESIGN_PATH),
            "sha256": _file_sha256(CP8_DESIGN_PATH),
            "content_sha256": design["content_sha256"],
            "seed": design["seed"],
            "n_queries": len(queries),
            "n_rotated_angles_per_query": len(design["angle_design"]["slots_per_query"]),
            "n_control_cells_per_arm_condition": len(queries),
            "conditions": conditions,
            "primary_condition": iv.CP8_PRIMARY_CONDITION,
            "check_condition": iv.CP8_CHECK_CONDITION,
            "t2_threshold_e0": T2_THRESHOLD,
            "top_k": top_k,
            "rotation_step_deg": design["angle_design"]["grid"]["step_deg"],
            "frozen_before_run": True,
            "gate": (
                "the runner re-derived the content hash, all 120 angles from the seed, the arm "
                "labels and the cell arithmetic before scoring anything, and aborts on any "
                "mismatch: an independent test is only independent if the sample cannot have "
                "moved after the freeze"
            ),
        },
        "provenance": {
            "command": (
                "python scripts/run_shape_benchmark.py --stage cp8"
                + (f" --limit-queries {limit_queries}" if limit_queries else "")
            ),
            "generated_by_python": platform.python_version(),
            "numpy": np.__version__,
            "git_commit": commit,
            "dataset": {
                "name": "MPEG-7 CE-Shape-1 (S1)",
                "url": mpeg7_manifest["url"],
                "archive_sha256": archive_sha,
                "archive_sha256_gate": (
                    "the run refuses to start unless the archive on disk is the build the design "
                    "was frozen against"
                ),
                "stimulus_count": mpeg7_manifest["stimulus_count"],
                "class_count": mpeg7_manifest["class_count"],
                "images_excluded_by_contour_loader": len(excluded_images),
            },
            "gallery": {
                "name": gallery.name,
                "n_shapes": len(gallery),
                "n_classes": len({str(shape.category) for shape in gallery.shapes}),
                "why": (
                    "the whole archive, not the 5-per-class D4 gallery: only 2 of the 24 frozen "
                    "queries are in that subset, and T0 needs the exact target present. A larger "
                    "library is the harder test, and no candidate was chosen after seeing a result."
                ),
                "differs_from_d6": True,
            },
            "official_toolbox": weights,
            "precompute_seconds": precompute,
            "summary_seconds": time.perf_counter() - started_run,
            "smoke_run": {
                "limited_to_queries": len(queries) if smoke else None,
                "published": not smoke,
                "note": (
                    "a limited run writes only to checkpoint8/_smoke, so a partial sample can "
                    "never be read as the promised 24-query table"
                ),
            },
        },
        "arms": [
            {
                "label": arm.label,
                "method": arm.method,
                "rotation_strategy": arm.strategy,
                "orientation": arm.orientation,
                "contract": arm.description(),
                "blocked": False,
            }
            for arm in arms
        ],
        "metric_definitions": design["statistics"]["metric_conventions"],
        "coverage_audit": design["coverage_audit"],
        "retrieval_by_arm": retrieval_by_arm,
        "rotation_by_angle_slot": _cp8_slot_view(cells),
        "arm_rankings_primary": _cp8_rankings(retrieval_by_arm),
        "human_perception": {
            "new_ratings_collected": 0,
            "status": (
                "CP8-C collects no human ratings and therefore adds no independent perceptual "
                "evidence. The only perceptual study remains the H1 agreement table, which scores "
                "the 250 rated pairs -- a different stimulus set from these 24 MPEG-7 shapes."
            ),
            "reference": {
                "path": _repo_relative(RESULTS / "d6_rotation_metrics.json"),
                "sha256": _file_sha256(RESULTS / "d6_rotation_metrics.json"),
                "schema": read_json(RESULTS / "d6_rotation_metrics.json")["schema"],
            },
            "category_labels_are": "a relevance proxy, not a similarity judgement",
        },
        "limits": [
            *iv.INDEPENDENCE_LIMITS,
            (
                "the gallery is the full archive while D6 scored a 5-per-class subset, so a CP8 "
                "number is not comparable digit-for-digit with a D6 number -- only the direction "
                "and the ranking are quoted across the two"
            ),
            (
                "24 queries is the promised sample, not a large one: intervals are clustered by "
                "query and stay wide, which is the honest reading rather than a defect"
            ),
        ],
        "rows_written": {
            "cells": len(cells),
            "top10_hits": len(hit_rows),
            "exclusions": len(exclusion_rows),
            "full_rankings_stored": len(orders),
        },
    }

    write_csv(out_dir / "cp8_retrieval_results.csv", cells)
    write_csv(out_dir / "cp8_top10_hits.csv", hit_rows)
    write_csv(out_dir / "cp8_exclusions.csv", exclusion_rows)
    if orders:
        write_rankings(out_dir / "cp8_full_rankings_T2.npz", orders)
        summary["files"] = {
            "cp8_full_rankings_T2.npz": {
                "sha256": _file_sha256(out_dir / "cp8_full_rankings_T2.npz"),
                "bytes": (out_dir / "cp8_full_rankings_T2.npz").stat().st_size,
                "note": (
                    "full T2 ranking per (arm, query, pose), so every headline can be recomputed "
                    "without running an encoder again"
                ),
            }
        }
    write_json(out_dir / "independent_metrics.json", summary)
    print(
        f"  {len(queries)} queries x {len(arms)} arms -> {len(cells)} cells, "
        f"{len(hit_rows)} hit rows, {_repo_relative(out_dir)}",
        flush=True,
    )
    return summary


def write_summary() -> None:
    """Combine whatever stages have run into one headline table with full provenance."""
    import platform
    import subprocess

    stages = {name: RESULTS / f"{name}_metrics.json" for name in ("d2", "d3", "d4")}
    stages["d1"] = RESULTS / "human_metrics.json"
    stages["d5"] = RESULTS / "d5_rotation_metrics.json"
    stages["d5b"] = RESULTS / "d5_grouped_metrics.json"
    stages["d6"] = RESULTS / "d6_rotation_metrics.json"
    stages["d6c"] = RESULTS / D6_CORRECTED_ARTEFACT
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
                    "mean_precision_at_10": float(
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
                "precision@10 -- stored as class_recall_at_10 in d2_metrics.json, which is "
                "Precision@K under a wrong name, not a mistake in the number -- uses MPEG-7 object "
                "CATEGORIES, not human similarity. T2/T3 are the conditions of interest; T0/T1 are "
                "controls."
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
            "paired_difference_direction": d5b["paired_difference_direction"],
            "paired_difference_convention": d5b["paired_difference_convention"],
            "paired_differences": d5b["paired_differences"],
            "differences_supported": d5b["differences_supported"],
            "assumptions": d5b["assumptions"],
            "audit_relation": d5b["audit_relation"],
        }
    if "d6" in present:
        d6 = present["d6"]
        headline["rotation_wrappers_agreement"] = {
            "study": (
                "goal A: one member of every rated H1 pair turned by a frozen random stress "
                "angle; the wrapper decides the pose, the human ground truth is shared"
            ),
            "metric": (
                "agreement with the published human dissimilarity, restated positive; interval "
                "resampled by rated pair (CP7-B scheme), never by angle row"
            ),
            "sampling_unit": d6["agreement_bootstrap"]["sampling_unit"],
            "by_arm": d6["agreement_by_arm"],
            "design": d6["design"],
            "verification_cached_vs_pair_distance": d6[
                "verification_cached_vs_pair_distance"
            ],
        }
        if "d6c" not in present:
            raise SystemExit(
                "d6_rotation_metrics.json is present but the CP8-A correction is not: the /1 "
                "headline mixes the 12 unrotated control cells into the 60 rotated ones and "
                "publishes Precision@10 as class_recall_at_k, so it must not be rolled up as it "
                "stands. Run: python scripts/run_shape_benchmark.py --stage d6-correct"
            )
        d6c = present["d6c"]
        primary = d6c["top_k"]
        headline["rotation_wrappers_retrieval"] = {
            "study": (
                "goal B: the rotated MPEG-7 query ranked against the stratified gallery under "
                "T0 (exact present), T1 (exact removed) and T2 (exact + rigid near-duplicates "
                "removed). T2 is the primary condition"
            ),
            "metric": (
                f"T2 Precision@{primary}, true Recall@{primary}, class MRR, NDCG@{primary} and "
                f"Top-{primary} overlap with the unrotated ranking, measured over the ROTATED "
                "cells only; the 0-degree controls are published as their own view"
            ),
            "statistics_correction": {
                "schema": d6c["schema"],
                "corrected_from": d6c["audit"]["original_file"],
                "reason": d6c["audit"]["reason"],
                "command": d6c["audit"]["command"],
                "views": d6c["views"]["definitions"],
                "metric_definitions": d6c["metric_definitions"],
                "before_vs_after_rankings": {
                    "file": _repo_relative(RESULTS / D6_CORRECTED_ARTEFACT),
                    "key": "arm_rankings_before_vs_after",
                },
            },
            "primary_condition": d6["design"]["primary_condition"],
            "by_arm": {
                arm: values[metrics.ROTATION_VIEW][d6["design"]["primary_condition"]]
                for arm, values in d6c["retrieval_by_arm"].items()
            },
            "unrotated_control_by_arm": {
                arm: values[metrics.CONTROL_VIEW][d6["design"]["primary_condition"]]
                for arm, values in d6c["retrieval_by_arm"].items()
            },
            "all_conditions": d6c["retrieval_by_arm"],
            "precompute_seconds": d6["precompute_seconds"],
            "orientation_diagnostics": d6["orientation_diagnostics"],
            "image_axis_convention_check": d6["image_axis_convention_check"],
        }

    payload = {
        # /2: the rollup started carrying the d6 stage and the direction-named d5b block.
        # /3: the d6 retrieval headline now comes from the CP8-A corrected two-view table, and the
        # one field that was mis-named everywhere (Precision@K published as "recall") is renamed.
        "schema": "earthglyph.shape_perception_benchmark/3",
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
            "python scripts/run_shape_benchmark.py --stage d6 --methods E0 E1 E3 S0K1 S0K2",
            "# d6-correct recomputes the d6 retrieval headline from the stored per-cell table -- "
            "rotated cells and 0-degree controls as separate views, Precision@K renamed, Recall@K "
            "added. It re-runs no inference and rewrites none of the files it reads.",
            "python scripts/run_shape_benchmark.py --stage d6-correct",
            "python scripts/plot_shape_benchmark.py --figure all",
        ],
        "arm_availability": {
            "E0": "all stages",
            "E1": "all stages (EarthGlyph's own 91-D descriptors, NOT ShapeComp)",
            "E3": "all stages",
            "S0": (
                "H1/H2 pairwise only, from the authors' published 22-D coordinates "
                "(the MDS projection of the ORIGINAL 109-descriptor ShapeComp space). "
                "Outputs only: cannot score "
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
        "metric_name_corrections": {
            "rule": (
                "a rename changes no measurement. It only stops a reader drawing a recall "
                "conclusion from a precision number, which is what the old label invited."
            ),
            "precision_at_k": "relevant share OF THE RETURNED list: hits / min(k, n_returned)",
            "recall_at_k": (
                "relevant share OF WHAT COULD STILL BE FOUND for that query and condition: "
                "hits / retained relevant candidates, a per-query denominator"
            ),
            "frozen_artefacts_still_using_the_old_name": {
                "d2_metrics.json": "queries[*][condition][method].class_recall_at_10",
                "d6_rotation_metrics.json (/1)": "retrieval_by_arm[*][condition].class_recall_at_k",
                "d6_retrieval_results.csv": "column class_recall_at_k",
            },
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
        choices=["d1", "d2", "d3", "d4", "d5", "d5b", "d6", "d6-correct", "cp8", "summary"],
    )
    parser.add_argument("--methods", nargs="+", default=list(EARTHGLYPH_METHODS),
                        choices=["E0", "E1", "E3", "S0", "S0K1", "S0K2"])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--allow-d6-arm-subset",
        action="store_true",
        help=(
            "let --stage d6 write a narrower arm table than the frozen 16-arm comparison "
            f"(default methods are {', '.join(EARTHGLYPH_METHODS)}, the frozen d6 set is "
            f"{', '.join(D6_FROZEN_METHODS)})"
        ),
    )
    parser.add_argument(
        "--limit-queries",
        type=int,
        default=0,
        help=(
            "with --stage cp8, score only the first N frozen queries as a plumbing smoke test; "
            f"a partial table can only ever be written to {_repo_relative(CP8_SMOKE_DIR)}"
        ),
    )
    args = parser.parse_args()
    if "cp8" in args.stage and set(args.methods) != set(EARTHGLYPH_METHODS):
        raise SystemExit(
            "--stage cp8 takes its six arms from the frozen design file, so --methods would be "
            "ignored here. Refusing the combination instead of silently dropping it."
        )
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
        elif stage == "d6":
            run_d6(args.methods, args.top_k, args.allow_d6_arm_subset)
        elif stage == "d6-correct":
            correct_d6_rotation_statistics(args.top_k)
        elif stage == "cp8":
            run_cp8(args.top_k, args.limit_queries)
        elif stage == "summary":
            write_summary()
        else:
            raise ValueError(f"stage {stage} is not wired into main()")
        print(f"=== {stage} done in {time.perf_counter() - started:.1f}s ===", flush=True)


if __name__ == "__main__":
    main()