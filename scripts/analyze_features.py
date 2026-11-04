#!/usr/bin/env python
"""CLI: P0-3 feature study — harmonic count, what amplitudes discard, library shape.

Three questions, all answered on the real released candidates:

1. ``n_harmonics`` — does 12 / 16 / 24 change the *geometry* of the feature
   space (energy capture, spread, collisions, neighbour structure)? This selects
   the index width. It is not a retrieval accuracy measurement: there is no
   ground truth and no query sketch at this stage.
2. What do amplitude-only descriptors lose? Every contour is rebuilt from its
   own amplitude spectrum with the phases zeroed. The impostor shares the
   descriptor with the original by construction, so the measured gap is exactly
   the information the coarse feature throws away.
3. Candidate-library scale and diversity: the real distributions of size and
   shape statistics, and the near-duplicate structure of the library.

Outputs (under --out-dir)
-------------------------
``features_summary.json``        all blocks below, with provenance
``near_duplicate_pairs.csv``     every pair inside the duplicate thresholds
``harmonic_ablation.png``        capture and feature-space geometry vs H
``candidate_distribution.png``   real distributions, not a headline number
``phase_loss_examples.png``      true contour beside its amplitude-only impostor

Example
-------
python scripts/analyze_features.py --contours data/processed/contours.parquet \
    --out-dir experiments/p0_feature_probe
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shlex
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe_shared import draw_contour, short_id, stratified_pick  # noqa: E402

from earthglyph.data.prepare import load_contours, stack_contours  # noqa: E402
from earthglyph.features.fourier import (  # noqa: E402
    as_complex_sequence,
    batch_harmonic_amplitudes,
    harmonic_energy_fractions,
)
from earthglyph.features.geometry_stats import FEATURE_COLUMNS, batch_shape_metrics  # noqa: E402
from earthglyph.geometry.fidelity import contour_fidelity  # noqa: E402

HARMONIC_OPTIONS: tuple[int, ...] = (12, 16, 24)
REFERENCE_HARMONICS = 16
QUERY_STRIDE = 7
NEIGHBOUR_K = 10
DUPLICATE_FEATURE_TAU = 0.02  # L2 on the index descriptor
DUPLICATE_SPECTRUM_TAU = 0.03  # L2 on the full positive-half spectrum
DUPLICATE_AREA_TAU = 0.02  # relative metric-area difference
COLLISION_EPSILON = 0.02

PROBE_LABEL = (
    "EarthGlyph P0-3 feature study - REAL Overture Maps water outlines "
    "(feature-space geometry, NOT retrieval accuracy; no query sketch involved)"
)


def assert_normalized(shapes: np.ndarray) -> np.ndarray:
    """The pipeline stores RMS-normalized contours; verify instead of assuming."""
    radius = np.sqrt((shapes**2).sum(axis=2).mean(axis=1))
    if not np.allclose(radius, 1.0, atol=1e-3):
        raise ValueError(
            f"stored contours are not RMS-normalized (radius "
            f"{radius.min():.4f}..{radius.max():.4f}); the dataset is not what this "
            "study expects"
        )
    return shapes


def descriptors(shapes: np.ndarray, n_harmonics: int) -> np.ndarray:
    return batch_harmonic_amplitudes(shapes, n_harmonics)


def full_spectrum(shapes: np.ndarray) -> np.ndarray:
    """Amplitudes of every usable positive harmonic (1..N//2-1)."""
    return batch_harmonic_amplitudes(shapes, shapes.shape[1] // 2 - 1)


def _complex_stack(shapes: np.ndarray) -> np.ndarray:
    """(M, N) complex sequences, each row normalized exactly as the library does.

    ``as_complex_sequence`` only accepts a single contour, so the batch path
    loops; at 839 rows the cost is negligible and the definition stays shared.
    """
    return np.stack([as_complex_sequence(row) for row in shapes])


def energy_capture(shapes: np.ndarray) -> dict[int, np.ndarray]:
    """Per-candidate share of non-DC spectral energy inside the first H harmonics.

    Same denominator as ``fourier.harmonic_energy_fractions`` (all k = 1..N-1),
    and the two paths are cross-checked below so the vectorized version cannot
    drift from the definition.
    """
    z = _complex_stack(shapes)
    power = np.abs(np.fft.fft(z, axis=1)) ** 2
    total = np.maximum(power[:, 1:].sum(axis=1), 1e-300)
    return {h: power[:, 1 : h + 1].sum(axis=1) / total for h in HARMONIC_OPTIONS}


def check_energy_capture(shapes: np.ndarray) -> float:
    """Max |vectorized - per-contour definition| over a deterministic subset."""
    capture = energy_capture(shapes)
    worst = 0.0
    for position in range(0, shapes.shape[0], 97):
        reference = harmonic_energy_fractions(shapes[position], max(HARMONIC_OPTIONS))
        for h in HARMONIC_OPTIONS:
            worst = max(worst, abs(float(reference[h - 1]) - float(capture[h][position])))
    return worst


def pairwise_distances(features: np.ndarray) -> np.ndarray:
    diff = features[:, None, :] - features[None, :, :]
    return np.sqrt((diff**2).sum(axis=2))


def feature_space_geometry(dist: np.ndarray) -> dict[str, Any]:
    """Spread and separability of one feature matrix, from distances only."""
    off = dist[np.triu_indices_from(dist, k=1)]
    n = dist.shape[0]
    nearest = np.array([dist[i, np.arange(n) != i].min() for i in range(n)])
    return {
        "pairwise_l2": {
            key: round(float(np.percentile(off, q)), 5)
            for key, q in (("p10", 10), ("p50", 50), ("p90", 90))
        },
        "pairwise_l2_mean": round(float(off.mean()), 5),
        "nearest_neighbour_l2": {
            "median": round(float(np.median(nearest)), 5),
            "p10": round(float(np.percentile(nearest, 10)), 5),
            "min": round(float(nearest.min()), 6),
        },
        "spread_ratio_mean_over_min": round(float(off.mean() / max(nearest.min(), 1e-12)), 1),
        f"pairs_below_{COLLISION_EPSILON}": int((off < COLLISION_EPSILON).sum()),
        "pairs_below_0.05": int((off < 0.05).sum()),
    }


def matrix_agreement(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Do two feature matrices order the same pair distances the same way?"""

    def offdiag(matrix: np.ndarray) -> np.ndarray:
        return matrix[np.triu_indices_from(matrix, k=1)]

    xa, xb = offdiag(a), offdiag(b)
    return {
        "pearson": round(float(np.corrcoef(xa, xb)[0, 1]), 6),
        "spearman": round(
            float(
                np.corrcoef(
                    pd.Series(xa).rank().to_numpy(), pd.Series(xb).rank().to_numpy()
                )[0, 1]
            ),
            6,
        ),
        "mean_relative_change": round(float(np.mean(np.abs(xa - xb) / xa)), 5),
    }


def neighbour_sets(dist: np.ndarray, k: int = NEIGHBOUR_K) -> dict[int, frozenset[int]]:
    """Deterministic query subset (every QUERY_STRIDE-th row) and its top-k set."""
    out: dict[int, frozenset[int]] = {}
    for query in range(0, dist.shape[0], QUERY_STRIDE):
        ranked = [int(other) for other in np.argsort(dist[query]) if other != query]
        out[query] = frozenset(ranked[:k])
    return out


def neighbour_agreement(
    base: dict[int, frozenset[int]], other: dict[int, frozenset[int]]
) -> dict[str, Any]:
    overlaps = [len(base[query] & other[query]) / len(base[query]) for query in base]
    return {
        "queries": len(overlaps),
        "k": NEIGHBOUR_K,
        "mean_overlap": round(float(np.mean(overlaps)), 4),
        "p10_overlap": round(float(np.percentile(overlaps, 10)), 4),
        "min_overlap": round(float(np.min(overlaps)), 4),
    }


def ablation(shapes: np.ndarray) -> dict[str, Any]:
    """Compare the harmonic options with one identical descriptor definition."""
    capture = energy_capture(shapes)
    dists = {h: pairwise_distances(descriptors(shapes, h)) for h in HARMONIC_OPTIONS}
    spaces = {f"h{h}": feature_space_geometry(dists[h]) for h in HARMONIC_OPTIONS}
    sets = {f"h{h}": neighbour_sets(dists[h]) for h in HARMONIC_OPTIONS}
    base = f"h{REFERENCE_HARMONICS}"
    return {
        "reference_harmonics": REFERENCE_HARMONICS,
        "energy_capture": {
            f"h{h}": {
                key: round(float(np.percentile(capture[h], q)), 5)
                for key, q in (("min", 0), ("p10", 10), ("p50", 50), ("max", 100))
            }
            for h in HARMONIC_OPTIONS
        },
        "energy_capture_definition_check_max_abs_error": check_energy_capture(shapes),
        "feature_space": spaces,
        "pairwise_distance_agreement": {
            f"h{a}_vs_h{b}": matrix_agreement(dists[a], dists[b])
            for a, b in ((12, 16), (16, 24), (12, 24))
        },
        "neighbour_agreement": {
            f"h{h}_vs_{base}": neighbour_agreement(sets[base], sets[f"h{h}"])
            for h in HARMONIC_OPTIONS
        },
        "timing": _timing(shapes),
        "not_measured": (
            "retrieval accuracy: no ground-truth pairs and no hand-drawn queries "
            "exist yet, so a wider descriptor is only justified by the geometry above"
        ),
    }


def _timing(shapes: np.ndarray) -> dict[str, Any]:
    started = time.perf_counter()
    for h in HARMONIC_OPTIONS:
        descriptors(shapes, h)
    batch = (time.perf_counter() - started) / len(HARMONIC_OPTIONS)
    features = descriptors(shapes, REFERENCE_HARMONICS)
    started = time.perf_counter()
    _ = np.linalg.norm(features - features[0], axis=1)
    return {
        "descriptor_batch_seconds": round(batch, 4),
        "one_full_l2_scan_seconds": round(time.perf_counter() - started, 5),
        "note": "this machine, this run; scan cost is linear in candidates",
    }


def phase_zeroed_reconstruction(shapes: np.ndarray) -> np.ndarray:
    """Contour rebuilt from its own amplitude spectrum with every phase set to 0.

    ``|Z'_k| == |Z_k|`` holds exactly, so the impostor shares the descriptor with
    the original while being a different curve. That is the deterministic proof
    that an amplitude-only feature cannot pin a shape down on its own.
    """
    z = _complex_stack(shapes)
    spectrum = np.fft.fft(z, axis=1)
    spectrum[:, 0] = 0.0  # DC dropped, exactly as the descriptor does
    rebuilt = np.fft.ifft(np.abs(spectrum), axis=1)
    return np.stack([rebuilt.real, rebuilt.imag], axis=2)


def _closed(points: np.ndarray) -> np.ndarray:
    return np.vstack([points, points[0]])


def phase_impostor_frame(
    shapes: np.ndarray, impostors: np.ndarray, contours: pd.DataFrame
) -> pd.DataFrame:
    """One row per candidate: real contour vs the impostor sharing its descriptor."""
    rows = []
    for position in range(shapes.shape[0]):
        metrics = contour_fidelity(_closed(shapes[position]), _closed(impostors[position]))
        rows.append(
            {
                "candidate_id": str(contours["candidate_id"].iloc[position]),
                "source_id": str(contours["source_id"].iloc[position]),
                "subtype": str(contours["subtype"].iloc[position]),
                "area_ha": float(contours["area_ha"].iloc[position]),
                "lon": float(contours["lon"].iloc[position]),
                "lat": float(contours["lat"].iloc[position]),
                "region_iou": metrics.region_iou,
                "hausdorff_norm": metrics.hausdorff_norm,
                "chamfer_norm": metrics.chamfer_norm,
                "area_rel_error": metrics.area_rel_error,
            }
        )
    return pd.DataFrame(rows)


def phase_loss(frame: pd.DataFrame) -> dict[str, Any]:
    """Measured gap between each real contour and its amplitude-only impostor."""
    return {
        "candidates": int(len(frame)),
        "descriptor_identical_by_construction": True,
        "region_iou": {
            key: round(float(np.percentile(frame["region_iou"], q)), 4)
            for key, q in (("min", 0), ("p10", 10), ("p50", 50), ("p90", 90))
        },
        "hausdorff_norm": {
            key: round(float(np.percentile(frame["hausdorff_norm"], q)), 4)
            for key, q in (("p50", 50), ("p90", 90), ("max", 100))
        },
        "chamfer_norm_median": round(float(frame["chamfer_norm"].median()), 4),
        "share_with_iou_below_0.9": round(float((frame["region_iou"] < 0.9).mean()), 4),
        "worst_impostors": [
            {
                "candidate_id": row["candidate_id"],
                "subtype": row["subtype"],
                "area_ha": round(row["area_ha"], 4),
                "region_iou": round(row["region_iou"], 4),
                "hausdorff_norm": round(row["hausdorff_norm"], 4),
            }
            for row in frame.nsmallest(6, "region_iou").to_dict("records")
        ],
        "reading": (
            "a coarse amplitude-only match can therefore never be the final answer: "
            "phase (or an explicit geometric re-rank) is what separates these impostors"
        ),
    }


def near_duplicates(
    contours: pd.DataFrame, index_features: np.ndarray, spectrum: np.ndarray
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Pairs that collide in the coarse feature *and* stay collidable at full spectrum.

    Three independent signals must agree: the index descriptor, the complete
    positive-half amplitude spectrum, and the real metric area. Rotation and
    cyclic start are still unaligned, so this understates confusability rather
    than overstating it; confirming a duplicate belongs to the P0-4 re-rank.
    """
    n = index_features.shape[0]
    feature_dist = pairwise_distances(index_features)
    spec_dist = pairwise_distances(spectrum)
    area = contours["area_m2"].to_numpy(dtype=float)
    rel_area = np.abs(area[:, None] - area[None, :]) / np.maximum(area[:, None], 1.0)
    mask = (
        np.triu(np.ones((n, n), dtype=bool), k=1)
        & (feature_dist < DUPLICATE_FEATURE_TAU)
        & (spec_dist < DUPLICATE_SPECTRUM_TAU)
        & (rel_area < DUPLICATE_AREA_TAU)
    )
    left, right = np.nonzero(mask)
    ids = [str(value) for value in contours["candidate_id"]]
    subtypes = [str(value) for value in contours["subtype"]]
    pairs = pd.DataFrame(
        {
            "candidate_a": [ids[i] for i in left],
            "candidate_b": [ids[i] for i in right],
            "subtype_a": [subtypes[i] for i in left],
            "subtype_b": [subtypes[i] for i in right],
            "feature_l2": np.round(feature_dist[left, right], 6),
            "spectrum_l2": np.round(spec_dist[left, right], 6),
            "area_rel_diff": np.round(rel_area[left, right], 6),
            "area_ha_a": np.round(area[left] / 1e4, 4),
            "area_ha_b": np.round(area[right] / 1e4, 4),
            "lon_a": np.round(contours["lon"].to_numpy()[left], 6),
            "lat_a": np.round(contours["lat"].to_numpy()[left], 6),
        }
    )
    parent = list(range(n))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i, j in zip(left, right, strict=True):
        root_i, root_j = find(int(i)), find(int(j))
        if root_i != root_j:
            parent[max(root_i, root_j)] = min(root_i, root_j)
    sizes: dict[int, int] = {}
    for node in range(n):
        root = find(node)
        sizes[root] = sizes.get(root, 0) + 1
    clusters = sorted((size for size in sizes.values() if size > 1), reverse=True)
    nearest = np.array([feature_dist[i, np.arange(n) != i].min() for i in range(n)])
    return pairs, {
        "thresholds": {
            "index_feature_l2": DUPLICATE_FEATURE_TAU,
            "full_spectrum_l2": DUPLICATE_SPECTRUM_TAU,
            "relative_area": DUPLICATE_AREA_TAU,
        },
        "pairs": int(len(pairs)),
        "candidates_in_any_pair": len(set(left.tolist()) | set(right.tolist())),
        "clusters": len(clusters),
        "largest_cluster": clusters[0] if clusters else 0,
        "cluster_size_histogram": {
            str(s): clusters.count(s) for s in sorted(set(clusters), reverse=True)
        },
        "own_nearest_neighbour_l2": {
            "median": round(float(np.median(nearest)), 5),
            "p10": round(float(np.percentile(nearest, 10)), 5),
            "min": round(float(nearest.min()), 6),
        },
        "caveat": (
            "rotation and cyclic start are unaligned, so this is a lower bound on "
            "how confusable the library is; duplicate decisions belong to P0-4"
        ),
    }


def library_review(contours: pd.DataFrame, shapes: np.ndarray) -> dict[str, Any]:
    """Diversity of the candidate library as distributions, not a headline count."""
    stats = batch_shape_metrics(shapes)
    log_area = np.log10(contours["area_ha"].to_numpy(dtype=float))
    return {
        "candidates": int(len(contours)),
        "subtypes": {str(k): int(v) for k, v in contours["subtype"].value_counts().items()},
        "classes": {str(k): int(v) for k, v in contours["class"].value_counts().items()},
        "area_ha": {
            key: round(float(np.percentile(contours["area_ha"], q)), 4)
            for key, q in (("min", 0), ("p10", 10), ("p50", 50), ("p90", 90), ("max", 100))
        },
        "area_orders_of_magnitude": round(float(log_area.max() - log_area.min()), 2),
        "geometry_stats": {
            column: {
                key: round(float(np.percentile(stats[:, index], q)), 4)
                for key, q in (("p5", 5), ("p50", 50), ("p95", 95))
            }
            for index, column in enumerate(FEATURE_COLUMNS)
        },
        "per_subtype": {
            str(subtype): {
                "count": int(len(block)),
                "area_ha": {
                    key: round(float(np.percentile(block["area_ha"], q)), 4)
                    for key, q in (("min", 0), ("p50", 50), ("max", 100))
                },
                "compactness_median": round(
                    float(np.median(stats[contours["subtype"].to_numpy() == subtype][:, 0])), 4
                ),
            }
            for subtype, block in contours.groupby("subtype")
        },
        "with_holes": int((contours["n_holes"] > 0).sum()),
        "multi_part_sources": int((contours["n_parts"] > 1).sum()),
        "repaired": int(contours["was_repaired"].astype(bool).sum()),
        "extends_beyond_frame": int(contours["extends_beyond_frame"].astype(bool).sum()),
        "coverage_note": (
            "one 1x1 degree frame (bbox -90,44,-89,45) inside a single state; "
            "the library is geographically narrow, so a shape that is rare here "
            "may be common elsewhere (Type B risk, see failure_type_fields)"
        ),
    }


def failure_type_fields(contours: pd.DataFrame, duplicates: dict[str, Any]) -> dict[str, Any]:
    """Fields that let P0-4/P0-6 separate an algorithm failure from a data gap.

    Recorded as *prepared instrumentation*. Nothing here decides a cause today:
    Type A (the algorithm cannot represent the shape) and Type B (the shape is
    absent from the library) are only distinguishable once real queries exist.
    """
    return {
        "per_query_fields_planned": [
            "query_sketch_id",
            "query_source (hand-drawn | synthetic-perturbation)",
            "topk_candidate_ids",
            "topk_feature_l2",
            "topk_aligned_geometric_score (P0-4 re-rank)",
            "human_label (relevant / partially / not relevant)",
        ],
        "library_fields_available_now": [
            "candidate_id",
            "source_id",
            "subtype",
            "area_ha",
            "compactness / solidity / aspect_ratio / diameter_fill",
            "lon / lat (so a wrong match can be located on a map)",
        ],
        "type_b_signal": {
            "definition": (
                "no library candidate reaches an aligned geometric score above the "
                "acceptance threshold, even at the widest descriptor tested"
            ),
            "supporting_counts_available": {
                "candidates": int(len(contours)),
                "subtypes": sorted(contours["subtype"].unique().tolist()),
                "distinct_subtype_count": int(contours["subtype"].nunique()),
                "near_duplicate_pairs": int(duplicates["pairs"]),
            },
        },
        "type_a_signal": {
            "definition": (
                "a library candidate that visually matches the query ranks poorly "
                "under the descriptor, i.e. the failure is in the feature or the "
                "distance, not in the data"
            ),
            "known_limits_measured_here": [
                "amplitude-only descriptor ignores phase (quantified in what_amplitudes_discard)",
                "rotation and cyclic start unaligned until the P0-4 re-rank",
                "no mirror handling beyond CCW winding normalization",
            ],
        },
        "status": "prepared, not concluded",
    }


def figure_ablation(result: dict[str, Any], out_path: Path) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    keys = [f"h{h}" for h in HARMONIC_OPTIONS]
    fig, axes = plt.subplots(1, 3, figsize=(11.6, 3.7))
    axes[0].plot(
        HARMONIC_OPTIONS,
        [result["energy_capture"][k]["p50"] for k in keys],
        "o-",
        color="#117864",
        label="median",
    )
    axes[0].plot(
        HARMONIC_OPTIONS,
        [result["energy_capture"][k]["min"] for k in keys],
        "s--",
        color="#c0392b",
        label="worst candidate",
    )
    axes[0].set_xlabel("n_harmonics")
    axes[0].set_ylabel("share of non-DC spectral energy")
    axes[0].set_title("a) energy capture saturates", fontsize=8.5)
    axes[0].legend(fontsize=7)
    axes[1].plot(
        HARMONIC_OPTIONS,
        [result["feature_space"][k]["nearest_neighbour_l2"]["median"] for k in keys],
        "o-",
        color="#2e86c1",
    )
    axes[1].plot(
        HARMONIC_OPTIONS,
        [result["feature_space"][k]["nearest_neighbour_l2"]["min"] for k in keys],
        "s--",
        color="#c0392b",
    )
    axes[1].set_xlabel("n_harmonics")
    axes[1].set_ylabel("L2 distance")
    axes[1].set_title("b) how tightly the library packs", fontsize=8.5)
    axes[1].legend(["median nearest neighbour", "closest pair"], fontsize=7)
    axes[2].bar(
        [str(h) for h in HARMONIC_OPTIONS],
        [result["feature_space"][k][f"pairs_below_{COLLISION_EPSILON}"] for k in keys],
        color="#b7950b",
    )
    axes[2].set_xlabel("n_harmonics")
    axes[2].set_ylabel(f"pairs closer than {COLLISION_EPSILON}")
    axes[2].set_title("c) coarse-filter collisions", fontsize=8.5)
    for ax in axes:
        ax.tick_params(labelsize=7)
    fig.suptitle(
        f"{PROBE_LABEL}\nfeature-space geometry only: no ground truth exists yet, "
        "so this is not a retrieval-accuracy comparison",
        fontsize=9.0,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name}


def figure_distribution(
    contours: pd.DataFrame,
    stats: np.ndarray,
    features: np.ndarray,
    nearest: np.ndarray,
    out_path: Path,
) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 4, figsize=(13.2, 5.8))
    axes[0][0].hist(np.log10(contours["area_ha"].to_numpy(dtype=float)), bins=40, color="#2e86c1")
    axes[0][0].set_xlabel("log10 area (ha)")
    axes[0][0].set_title("a) size spread", fontsize=8.5)
    for index, column in enumerate(FEATURE_COLUMNS):
        ax = axes.flat[index + 1]
        ax.hist(stats[:, index], bins=40, color="#117864")
        ax.set_xlabel(column)
        ax.set_title(f"{chr(ord('b') + index)}) {column}", fontsize=8.5)
    scatter = axes.flat[5]
    colors = {"pond": "#2e86c1", "lake": "#117864", "reservoir": "#c0392b"}
    for subtype, color in colors.items():
        mask = contours["subtype"].to_numpy() == subtype
        scatter.scatter(
            features[mask, 0], features[mask, 1], s=7, alpha=0.6, color=color, label=subtype
        )
    scatter.set_xlabel("|Z1|")
    scatter.set_ylabel("|Z2|")
    scatter.set_title("f) first two harmonics by subtype", fontsize=8.5)
    scatter.legend(fontsize=6.5)
    axes.flat[6].hist(nearest, bins=40, color="#b7950b")
    axes.flat[6].set_xlabel("nearest-neighbour descriptor L2")
    axes.flat[6].set_title("g) duplicate pressure", fontsize=8.5)
    axes.flat[7].hist(contours["n_holes"].to_numpy(dtype=float), bins=20, color="#7d6608")
    axes.flat[7].set_xlabel("holes (islands) per candidate")
    axes.flat[7].set_title("h) excluded from the outline (D21)", fontsize=8.5)
    for ax in axes.flat:
        ax.tick_params(labelsize=7)
    fig.suptitle(
        f"Real candidate library - {len(contours)} outlines, "
        f"{contours['subtype'].nunique()} subtypes - {PROBE_LABEL}",
        fontsize=9.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name}


def figure_phase_loss(
    contours: pd.DataFrame,
    shapes: np.ndarray,
    impostors: np.ndarray,
    frame: pd.DataFrame,
    out_path: Path,
) -> dict[str, Any]:
    """True contour beside the impostor that shares its descriptor."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    iou = frame["region_iou"].to_numpy(dtype=float)
    worst = [int(position) for position in np.argsort(iou)[:4]]
    spread = [
        position for position in stratified_pick(np.arange(shapes.shape[0]), 8)
        if position not in worst
    ][:4]
    picks = worst + spread
    fig, axes = plt.subplots(2, len(picks), figsize=(2.6 * len(picks), 5.6), squeeze=False)
    entries = []
    for column, position in enumerate(picks):
        caption = contours.iloc[position]
        for row, (points, kind) in enumerate(
            (
                (shapes[position], "true contour"),
                (impostors[position], "same |Z_k|, all phases = 0"),
            )
        ):
            ax = axes[row][column]
            draw_contour(
                ax,
                points,
                color="#28556e" if row == 0 else "#c0392b",
                face="#dce9f4" if row == 0 else "#f6ddd8",
            )
            if row == 0:
                ax.set_title(
                    f"{caption['subtype']} {caption['area_ha']:.3g} ha\n"
                    f"{short_id(str(caption['candidate_id']), 18)}",
                    fontsize=6.5,
                )
            ax.set_xlabel(kind, fontsize=6.0)
        axes[1][column].set_ylabel(f"IoU {iou[position]:.3f}", fontsize=7.0)
        entries.append(
            {
                "candidate_id": str(caption["candidate_id"]),
                "source_id": str(caption["source_id"]),
                "subtype": str(caption["subtype"]),
                "area_ha": round(float(caption["area_ha"]), 4),
                "lon": round(float(caption["lon"]), 6),
                "lat": round(float(caption["lat"]), 6),
                "impostor_region_iou": round(float(iou[position]), 4),
                "selected_by": "worst IoU" if position in worst else "area-stratified",
            }
        )
    fig.suptitle(
        f"Both rows have an identical amplitude descriptor - {PROBE_LABEL}",
        fontsize=9.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name, "entries": entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contours", type=Path, default=Path("data/processed/contours.parquet"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_feature_probe"))
    parser.add_argument("--skip-figures", action="store_true")
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    contours = load_contours(args.contours)
    shapes = assert_normalized(stack_contours(contours))
    print(f"{len(contours)} candidates, {shapes.shape[1]} points each", flush=True)

    result = ablation(shapes)
    index_features = descriptors(shapes, REFERENCE_HARMONICS)
    impostors = phase_zeroed_reconstruction(shapes)
    impostor_frame = phase_impostor_frame(shapes, impostors, contours)
    loss = phase_loss(impostor_frame)
    stats = batch_shape_metrics(shapes)
    spectrum = full_spectrum(shapes)
    pairs, duplicates = near_duplicates(contours, index_features, spectrum)
    index_distance = pairwise_distances(index_features)
    nearest = np.array(
        [
            index_distance[i, np.arange(index_distance.shape[0]) != i].min()
            for i in range(len(contours))
        ]
    )
    manifest_path = args.contours.parent / "data_manifest.json"
    source_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )

    summary: dict[str, Any] = {
        "stage": "P0-3 feature and candidate-library study",
        "kind": "feature-space geometry + library diversity (NOT retrieval accuracy)",
        "not_a_retrieval_result": True,
        "query_sketch_used": False,
        "deterministic": True,
        "random_seed": None,
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "data": {
            "contours_path": args.contours.as_posix(),
            "candidates": int(len(contours)),
            "n_points": int(shapes.shape[1]),
            "source": source_manifest.get("source"),
            "contours_sha256": (
                (source_manifest.get("outputs", {}).get("contours", {}) or {}).get("sha256")
            ),
        },
        "parameters": {
            "harmonic_options": list(HARMONIC_OPTIONS),
            "reference_harmonics": REFERENCE_HARMONICS,
            "fold": "positive",
            "per_sample_normalization": True,
            "query_stride": QUERY_STRIDE,
            "neighbour_k": NEIGHBOUR_K,
            "collision_epsilon": COLLISION_EPSILON,
        },
        "harmonic_ablation": result,
        "what_amplitudes_discard": loss,
        "near_duplicates": duplicates,
        "library_review": library_review(contours, shapes),
        "failure_type_fields": failure_type_fields(contours, duplicates),
        "reproduce": {
            "command": "python "
            + " ".join(
                shlex.quote(item)
                for item in [
                    "scripts/analyze_features.py",
                    "--contours",
                    str(args.contours),
                    "--out-dir",
                    str(out_dir),
                ]
            )
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }

    pairs_path = out_dir / "near_duplicate_pairs.csv"
    pairs.to_csv(pairs_path, index=False)
    summary["near_duplicate_pairs_file"] = {"file": pairs_path.name, "rows": int(len(pairs))}

    if not args.skip_figures:
        summary["figures"] = [
            figure_ablation(result, out_dir / "harmonic_ablation.png"),
            figure_distribution(
                contours, stats, index_features, nearest, out_dir / "candidate_distribution.png"
            ),
            figure_phase_loss(
                contours, shapes, impostors, impostor_frame, out_dir / "phase_loss_examples.png"
            ),
        ]

    (out_dir / "features_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result["energy_capture"], indent=2))
    print(json.dumps(result["feature_space"], indent=2))
    print(json.dumps(result["pairwise_distance_agreement"], indent=2))
    print(json.dumps(result["neighbour_agreement"], indent=2))
    print(json.dumps(duplicates, indent=2))
    print(f"summary written: {out_dir / 'features_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
