#!/usr/bin/env python
"""CLI: P0-3 sampling-resolution study — how much geometry does N points keep?

What this measures and what it does not
---------------------------------------
**Measured**: geometric fidelity of a uniform arc-length resampling against the
*original* Overture boundary, stability of the Fourier / geometry features
across sample counts, and the engineering cost of each choice.
**Not measured**: retrieval quality. No query sketch exists in this stage; the
retrieval effect of a higher N belongs to P0-4 / P0-5 / P0-6.

Resampling always starts from the source exterior ring recovered through
``earthglyph.data.prepare.build_from_source`` — the stored 128-point contours are
never interpolated to 256, which would only measure the interpolator.

Outputs (under --out-dir)
-------------------------
``sampling_metrics.csv``       one row per candidate x N, all metrics
``sampling_comparison.png``    6 real shapes: original / 64 / 128 / 256 at shared scale
``sampling_zoom.png``          worst-deviation zooms for the complex shapes
``sampling_error_scatter.png`` error distribution over all 839 candidates
``sampling_summary.json``      aggregates, selection rules, cost model, provenance
``sampling_metric_validation.csv/json``
                               written by ``--validate-exact``: the cloud
                               deviation estimates compared against exact GEOS
                               Hausdorff distances for the same ring pairs

Example
-------
python scripts/analyze_sampling.py --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
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

from probe_shared import draw_geometry, short_id  # noqa: E402

from earthglyph.data.prepare import (  # noqa: E402
    build_from_source,
    load_contours,
    stack_contours,
)
from earthglyph.features.fourier import fourier_descriptor  # noqa: E402
from earthglyph.features.geometry_stats import shape_metrics  # noqa: E402
from earthglyph.geometry import AeqdProjection, build_contour, exterior_ring  # noqa: E402
from earthglyph.geometry.fidelity import (  # noqa: E402
    DEFAULT_DENSIFY_DIVISOR,
    contour_fidelity,
    exterior_only,
    max_deviation_point,
)

SAMPLE_POINTS: tuple[int, ...] = (64, 128, 256)
REFERENCE_POINTS = 128  # the released dataset value everything is compared to
STABILITY_HARMONICS = 12  # usable at N=64 (Nyquist limit 31) and above
QUERY_STRIDE = 7  # deterministic query subset for neighbour-agreement
NEIGHBOUR_K = 10
VALIDATION_CSV = "sampling_metric_validation.csv"
VALIDATION_JSON = "sampling_metric_validation.json"
VALIDATION_REFINED_DIVISOR = 64  # 8x finer than the study's cloud, as second reference

# Study thresholds for the adaptive-sampling question. Chosen here, stated here,
# and *not* presented as validated product requirements.
IOU_TARGET = 0.95
HAUSDORFF_NORM_TARGET = 0.05

PROBE_LABEL = (
    "EarthGlyph P0-3 sampling study - REAL Overture Maps water outlines "
    "(geometric fidelity, NOT a retrieval result, no query sketch involved)"
)

CATEGORIES: tuple[str, ...] = (
    "round_lake",
    "crescent",
    "slender",
    "dendritic_reservoir",
    "deep_concave",
    "multi_bay",
)
ZOOM_CATEGORIES: tuple[str, ...] = ("dendritic_reservoir", "deep_concave", "multi_bay")


def source_rings(
    raw_path: Path, contours: pd.DataFrame, n_points: int
) -> tuple[dict[str, np.ndarray], AeqdProjection, dict[str, Any]]:
    """Exterior ring in projected metres for every indexed candidate.

    Raises when the dataset and the rebuilt source disagree on candidate ids: the
    study must run on exactly the released candidates.
    """
    build = build_from_source(raw_path, n_points=n_points)
    rings = {
        candidate.candidate_id: np.asarray(exterior_ring(candidate.polygon_metric), dtype=float)
        for candidate in build.candidates
    }
    stored = set(map(str, contours["candidate_id"]))
    if stored != set(rings):
        missing = sorted(stored - set(rings))[:5]
        extra = sorted(set(rings) - stored)[:5]
        raise ValueError(
            f"source rebuild does not match the released dataset (missing {missing}, extra {extra})"
        )
    meta: dict[str, Any] = {
        "projection": build.projection.describe(),
        "raw_file": build.meta.get("file", {}),
        "raw_release": build.meta.get("release"),
        "raw_source": build.meta.get("source"),
        "raw_bbox": build.meta.get("bbox"),
        "raw_rows": build.raw_rows,
    }
    return rings, build.projection, meta


def hull_profile(polygon: Any) -> dict[str, float]:
    """Convex-hull diagnostics of an exterior-only polygon.

    ``hull_compactness`` separates a moon from a notch: a crescent's hull is
    disc-like (close to 1), an L-shaped pond's hull is rectangular (around 0.6),
    even though both have exactly one dominant hull deficit.
    """
    hull = polygon.convex_hull
    hull_area = float(hull.area)
    deficit = hull.difference(polygon)
    parts = (
        list(deficit.geoms)
        if deficit.geom_type == "MultiPolygon"
        else ([deficit] if deficit.geom_type == "Polygon" else [])
    )
    areas = [float(part.area) for part in parts if float(part.area) > 0.0]
    total = sum(areas)
    hull_perimeter = float(hull.length)
    return {
        "solidity_source": float(polygon.area) / hull_area if hull_area > 0 else 0.0,
        "hull_deficits": float(len(areas)),
        "dominant_defect_share": (max(areas) / total) if total > 0 else 0.0,
        "hull_compactness": (
            4.0 * np.pi * hull_area / hull_perimeter**2 if hull_perimeter > 0 else 0.0
        ),
    }


def _crescent(metrics: dict[str, float], attrs: dict[str, Any]) -> bool:
    return (
        attrs["area_ha"] >= 0.5
        and 0.55 <= metrics["solidity_source"] <= 0.90
        and metrics["dominant_defect_share"] >= 0.95
        and metrics["hull_deficits"] <= 3
    )


def _crescent_with_arc(metrics: dict[str, float], attrs: dict[str, Any]) -> bool:
    return _crescent(metrics, attrs) and metrics["hull_compactness"] >= 0.80


#: One entry per mandated shape category. ``stages`` is a relaxation ladder: the
#: first stage whose pool still has an unused candidate wins, and the stage that
#: actually fired is recorded in the output, so a loosened rule is visible
#: instead of a silently missing panel. ``key`` ranks the pool, ``reverse`` picks
#: the last element of the ranking.
CATEGORY_SPECS: dict[str, dict[str, Any]] = {
    "round_lake": {
        "key": lambda m, a: m["compactness"],
        "reverse": True,
        "stages": [
            (
                "max exterior compactness; area >= 2 ha, solidity >= 0.90, "
                "aspect <= 1.4, hull compactness >= 0.80",
                lambda m, a: (
                    a["area_ha"] >= 2.0
                    and m["solidity_source"] >= 0.90
                    and m["aspect_ratio"] <= 1.4
                    and m["hull_compactness"] >= 0.80
                ),
            ),
            (
                "relaxed: dropped the hull-compactness test",
                lambda m, a: (
                    a["area_ha"] >= 2.0
                    and m["solidity_source"] >= 0.90
                    and m["aspect_ratio"] <= 1.4
                ),
            ),
            ("fallback: area >= 0.5 ha", lambda m, a: a["area_ha"] >= 0.5),
        ],
    },
    "crescent": {
        "key": lambda m, a: m["solidity_source"],
        "reverse": False,
        "stages": [
            ("min solidity; one dominant hull deficit on a disc-like hull", _crescent_with_arc),
            ("relaxed: dropped the disc-like-hull test", _crescent),
            (
                "fallback: single dominant deficit",
                lambda m, a: (
                    a["area_ha"] >= 0.5
                    and m["dominant_defect_share"] >= 0.95
                    and m["hull_deficits"] >= 1
                ),
            ),
        ],
    },
    "slender": {
        "key": lambda m, a: m["aspect_ratio"],
        "reverse": True,
        "stages": [
            ("max aspect ratio; area >= 1 ha", lambda m, a: a["area_ha"] >= 1.0),
            ("fallback: area >= 0.2 ha", lambda m, a: a["area_ha"] >= 0.2),
        ],
    },
    "dendritic_reservoir": {
        "key": lambda m, a: m["compactness"],
        "reverse": False,
        "stages": [
            (
                "min compactness among reservoirs; area >= 50 ha",
                lambda m, a: a["area_ha"] >= 50.0 and a["subtype"] == "reservoir",
            ),
            (
                "relaxed: reservoir of any area",
                lambda m, a: a["subtype"] == "reservoir" and a["area_ha"] >= 1.0,
            ),
            ("fallback: min compactness over all candidates", lambda m, a: True),
        ],
    },
    "deep_concave": {
        "key": lambda m, a: m["solidity_source"],
        "reverse": False,
        "stages": [
            ("min solidity; area >= 5 ha", lambda m, a: a["area_ha"] >= 5.0),
            ("fallback: area >= 1 ha", lambda m, a: a["area_ha"] >= 1.0),
        ],
    },
    "multi_bay": {
        "key": lambda m, a: (m["hull_deficits"], m["exterior_area_m2"]),
        "reverse": True,
        "stages": [
            (
                "most convex-hull deficits (>= 4); area >= 10 ha",
                lambda m, a: a["area_ha"] >= 10.0 and m["hull_deficits"] >= 4,
            ),
            (
                "relaxed: >= 3 deficits, area >= 2 ha",
                lambda m, a: a["area_ha"] >= 2.0 and m["hull_deficits"] >= 3,
            ),
            ("fallback: area >= 1 ha", lambda m, a: a["area_ha"] >= 1.0),
        ],
    },
}


def select_representatives(
    rings: dict[str, np.ndarray], contours: pd.DataFrame
) -> dict[str, dict[str, Any]]:
    """Deterministic pick of one real outline per mandated shape category.

    Rules run over the metric source polygons in ``CATEGORIES`` order and a
    candidate already used cannot be reused, so the panels stay different shapes.
    No randomness, no manual cherry-picking, and the stage that fired is written
    out for every panel.
    """
    frame = contours.set_index("candidate_id")
    profile: dict[str, dict[str, float]] = {}
    attrs: dict[str, dict[str, Any]] = {}
    for candidate_id, ring in rings.items():
        polygon = exterior_only(_polygon_of(ring))
        metrics = shape_metrics(ring)
        profile[candidate_id] = {
            **metrics.as_dict(),
            **hull_profile(polygon),
            "exterior_area_m2": float(polygon.area),
        }
        attrs[candidate_id] = {
            "area_ha": (
                float(frame.at[candidate_id, "area_ha"])
                if candidate_id in frame.index
                else float(polygon.area) / 1e4
            ),
            "subtype": (
                str(frame.at[candidate_id, "subtype"]) if candidate_id in frame.index else ""
            ),
        }

    picked: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for category in CATEGORIES:
        spec = CATEGORY_SPECS[category]
        entry: dict[str, Any] = {}
        for stage, (label, keep) in enumerate(spec["stages"], start=1):
            pool = [
                candidate_id
                for candidate_id in sorted(profile)
                if candidate_id not in used
                and candidate_id in frame.index
                and keep(profile[candidate_id], attrs[candidate_id])
            ]
            if not pool:
                continue
            ranked = sorted(
                pool, key=lambda cid: (spec["key"](profile[cid], attrs[cid]), cid)
            )
            candidate_id = ranked[-1] if spec["reverse"] else ranked[0]
            used.add(candidate_id)
            entry = {
                "candidate_id": candidate_id,
                "source_id": str(frame.at[candidate_id, "source_id"]),
                "subtype": attrs[candidate_id]["subtype"],
                "area_ha": round(attrs[candidate_id]["area_ha"], 4),
                "pool_size": len(pool),
                "rule": label,
                "stage": stage,
                "n_stages": len(spec["stages"]),
                "metrics": {k: round(v, 6) for k, v in profile[candidate_id].items()},
            }
            break
        if entry:
            picked[category] = entry
    return picked


def selection_rules() -> dict[str, list[str]]:
    """The full ladder per category, so the summary states what was asked for."""
    return {
        category: [label for label, _ in spec["stages"]]
        for category, spec in CATEGORY_SPECS.items()
    }


def _polygon_of(ring: np.ndarray) -> Any:
    """Polygon from a closed exterior ring as returned by ``exterior_ring``."""
    import shapely

    return shapely.Polygon(ring)


def measure(
    rings: dict[str, np.ndarray],
    contours: pd.DataFrame,
    stored_shapes: np.ndarray,
    sample_points: tuple[int, ...],
    projection: AeqdProjection,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fidelity + feature table for every candidate at every N."""
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    round_trip_max = 0.0
    failures: list[dict[str, str]] = []

    position_of = {
        str(candidate_id): int(position)
        for position, candidate_id in enumerate(contours["candidate_id"])
    }
    for position, (candidate_id, ring) in enumerate(sorted(rings.items())):
        row_position = position_of[candidate_id]
        record = contours.iloc[row_position]
        if REFERENCE_POINTS in sample_points:
            # The released dataset must be exactly reproducible from the source
            # ring; a non-zero gap would mean the index and the raw data diverge.
            reference = build_contour(ring, REFERENCE_POINTS).points
            round_trip_max = max(
                round_trip_max, float(np.abs(reference - stored_shapes[row_position]).max())
            )
        for n_points in sample_points:
            try:
                contour = build_contour(ring, n_points)
                fidelity = contour_fidelity(ring, contour.metric_points)
                descriptor = fourier_descriptor(contour.points, STABILITY_HARMONICS)
                metrics = shape_metrics(contour.points)
            except Exception as exc:  # recorded with its id, never silently dropped
                failures.append(
                    {
                        "candidate_id": candidate_id,
                        "n_points": str(n_points),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(
                    f"  FAILED {candidate_id} at N={n_points}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            row: dict[str, Any] = {
                "candidate_id": candidate_id,
                "source_id": str(record["source_id"]),
                "subtype": str(record["subtype"]),
                "area_ha": float(record["area_ha"]),
                "n_holes": int(record["n_holes"]),
                "lon": float(record["lon"]),
                "lat": float(record["lat"]),
                "n_source_vertices": int(ring.shape[0] - 1),
                "n_points": n_points,
            }
            row.update(fidelity.as_dict())
            row.update(
                {
                    f"fd_{index:02d}": float(value)
                    for index, value in enumerate(descriptor, start=1)
                }
            )
            row.update(
                {f"stat_{name}": float(value) for name, value in metrics.as_dict().items()}
            )
            rows.append(row)
        if (position + 1) % 200 == 0:
            print(f"  measured {position + 1}/{len(rings)} candidates", flush=True)

    table = pd.DataFrame(rows)
    stats = {
        "candidates": len(rings),
        "rows": len(table),
        "seconds": round(time.perf_counter() - started, 2),
        "failed_measurements": failures,
        "stored_contour_round_trip_max_abs_error": round_trip_max,
        "round_trip_tolerance": 1e-9,
        "round_trip_ok": round_trip_max <= 1e-9,
        "projection": projection.describe(),
    }
    return table, stats


def aligned_pivots(
    table: pd.DataFrame, columns: list[str]
) -> tuple[dict[int, pd.DataFrame], list[str]]:
    """Per-N blocks over ``columns``, restricted to candidates measured at every N.

    A candidate whose measurement failed at one N is excluded from the pairwise
    comparisons and *listed* — comparisons must never silently mix row counts.
    """
    blocks = {
        int(n_points): table[table["n_points"] == n_points].set_index("candidate_id")[columns]
        for n_points in sorted(table["n_points"].unique())
    }
    common = set.intersection(*(set(block.index) for block in blocks.values()))
    every = set().union(*(set(block.index) for block in blocks.values()))
    ordered = sorted(common)
    return {n: block.loc[ordered] for n, block in blocks.items()}, sorted(every - common)


def feature_stability(table: pd.DataFrame) -> dict[str, Any]:
    """Cross-N feature differences computed with one identical descriptor."""
    fd_columns = [f"fd_{index:02d}" for index in range(1, STABILITY_HARMONICS + 1)]
    pivot, dropped = aligned_pivots(table, fd_columns)
    reference = pivot[REFERENCE_POINTS].to_numpy()
    out: dict[str, Any] = {
        "descriptor": f"fourier amplitudes, {STABILITY_HARMONICS} harmonics",
        "aligned_candidates": int(pivot[REFERENCE_POINTS].shape[0]),
        "excluded_candidates": dropped,
    }
    for n_points, block in pivot.items():
        values = block.to_numpy()
        difference = np.linalg.norm(values - reference, axis=1)
        base = np.linalg.norm(reference, axis=1)
        relative = difference / base
        cos = 1.0 - (values * reference).sum(axis=1) / (
            np.linalg.norm(values, axis=1) * base + 1e-300
        )
        out[f"n{n_points}_vs_reference"] = {
            "abs_l2_percentiles": {
                key: round(float(np.percentile(difference, q)), 6)
                for key, q in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100))
            },
            "relative_l2_percentiles": {
                key: round(float(np.percentile(relative, q)), 6)
                for key, q in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100))
            },
            "cosine_distance_p99": float(np.percentile(cos, 99)),
        }
    geometry_columns = [column for column in table.columns if column.startswith("stat_")]
    geometry_block, _ = aligned_pivots(table, geometry_columns)
    reference_geometry = geometry_block[REFERENCE_POINTS].to_numpy()
    for n_points, block in geometry_block.items():
        delta = (block.to_numpy() - reference_geometry) / (np.abs(reference_geometry) + 1e-12)
        out[f"n{n_points}_geometry_rel_delta_p99"] = {
            column: round(float(np.nanpercentile(np.abs(delta[:, index]), 99)), 6)
            for index, column in enumerate(geometry_columns)
        }
        out[f"n{n_points}_geometry_rel_delta_max"] = {
            column: round(float(np.nanmax(np.abs(delta[:, index]))), 6)
            for index, column in enumerate(geometry_columns)
        }
    out["neighbour_agreement"] = _neighbour_agreement(pivot)
    return out


def _neighbour_agreement(pivot: dict[int, pd.DataFrame]) -> dict[str, Any]:
    """Cross-N descriptor stability measured in the reference feature space.

    Two deterministic questions, both answered with the *same* descriptor
    definition and the L2 distance used by the planned coarse filter:

    ``top_k_overlap``  do the ``NEIGHBOUR_K`` nearest reference candidates of an
    N-sampled shape stay the same as those of its 128-point version?
    ``self_rank``      when a shape sampled at N queries the 128-point library,
    what rank does its *own* reference descriptor reach?

    This is representation stability, not a retrieval benchmark: the queries are
    stored real candidates, not hand-drawn sketches (P0-6).
    """
    ids = sorted(pivot[REFERENCE_POINTS].index)
    reference_matrix = pivot[REFERENCE_POINTS].loc[ids].to_numpy()
    queries = list(range(0, len(ids), QUERY_STRIDE))

    def ranked_library(query_row: np.ndarray) -> list[int]:
        """Library positions ordered by distance to one query descriptor."""
        distances = np.linalg.norm(reference_matrix - query_row, axis=1)
        return [int(position) for position in np.argsort(distances)]

    reference_sets = {
        position: set(ranked_library(reference_matrix[position])[:NEIGHBOUR_K])
        for position in queries
    }
    out: dict[str, Any] = {
        "queries": len(queries),
        "library": len(ids),
        "k": NEIGHBOUR_K,
        "distance": "L2 to the N=128 reference library",
    }
    for n_points, block in pivot.items():
        matrix = block.loc[ids].to_numpy()
        overlaps: list[float] = []
        self_ranks: list[int] = []
        for position in queries:
            ranked = ranked_library(matrix[position])
            overlaps.append(
                len(reference_sets[position] & set(ranked[:NEIGHBOUR_K])) / NEIGHBOUR_K
            )
            self_ranks.append(ranked.index(position) + 1)
        key = f"n{n_points}"
        out[f"{key}_top{NEIGHBOUR_K}_overlap_with_reference"] = {
            "mean": round(float(np.mean(overlaps)), 4),
            "p10": round(float(np.percentile(overlaps, 10)), 4),
            "min": round(float(np.min(overlaps)), 4),
        }
        out[f"{key}_own_reference_rank"] = {
            "median": float(np.median(self_ranks)),
            "p90": float(np.percentile(self_ranks, 90)),
            "worst": int(np.max(self_ranks)),
            "top1_share": round(float(np.mean([rank == 1 for rank in self_ranks])), 4),
        }
    return out


def adaptive_evidence(table: pd.DataFrame) -> dict[str, Any]:
    """Smallest tested N meeting the study's fidelity targets, per candidate.

    Candidates missing a measurement at any tested N are reported separately;
    the counts below are over the aligned set only.
    """
    columns = ["region_iou", "hausdorff_norm", "subtype", "area_ha"]
    pivots, dropped = aligned_pivots(table, columns)
    order = sorted(pivots)
    ids = list(pivots[order[0]].index)
    passes = {
        n_points: (
            (pivots[n_points]["region_iou"].to_numpy() >= IOU_TARGET)
            & (pivots[n_points]["hausdorff_norm"].to_numpy() <= HAUSDORFF_NORM_TARGET)
        )
        for n_points in order
    }
    chosen: list[int | None] = []
    for row in range(len(ids)):
        chosen.append(next((n for n in order if bool(passes[n][row])), None))
    counts = {
        f"n{n_points}": int(sum(1 for value in chosen if value == n_points)) for n_points in order
    }
    counts["none_of_the_tested_values"] = int(sum(1 for value in chosen if value is None))
    reference = pivots[REFERENCE_POINTS]
    subtype_of = reference["subtype"].to_numpy()
    per_subtype: dict[str, dict[str, int]] = {}
    for index, group in enumerate(subtype_of):
        value = chosen[index]
        key = f"n{value}" if value is not None else "none_of_the_tested_values"
        bucket = per_subtype.setdefault(str(group), {})
        bucket[key] = bucket.get(key, 0) + 1
    hausdorff = reference["hausdorff_norm"].to_numpy()
    iou = reference["region_iou"].to_numpy()
    areas = reference["area_ha"].to_numpy()
    worst_positions = np.argsort(-hausdorff)[:10]
    return {
        "targets": {"region_iou": IOU_TARGET, "hausdorff_norm": HAUSDORFF_NORM_TARGET},
        "aligned_candidates": len(ids),
        "excluded_candidates": dropped,
        "smallest_passing_n": counts,
        "smallest_passing_n_by_subtype": dict(sorted(per_subtype.items())),
        "candidates_failing_at_every_tested_n": counts["none_of_the_tested_values"],
        "worst_hausdorff_norm_at_reference": [
            {
                "candidate_id": ids[int(position)],
                "subtype": str(subtype_of[position]),
                "area_ha": round(float(areas[position]), 4),
                "hausdorff_norm": round(float(hausdorff[position]), 4),
                "region_iou": round(float(iou[position]), 4),
            }
            for position in worst_positions
        ],
    }


def engineering_cost(
    table: pd.DataFrame,
    contours: pd.DataFrame,
    stored_shapes: np.ndarray,
) -> dict[str, Any]:
    """Measured storage and compute cost per sample count, plus the scale model.

    Every number here is a *cost*, timed on this machine, not a quality claim.
    The re-rank proxy is a symmetric Chamfer distance between two contours; the
    real P0-4 aligner (cyclic shift + complex correlation) is not implemented in
    this stage.
    """
    from scipy.spatial import cKDTree

    from earthglyph.data.index import IndexConfig, feature_groups

    columns, _ = feature_groups(IndexConfig(), REFERENCE_POINTS)
    feature_dim = len(columns)
    bytes_per_coordinate = 4  # float32
    candidates = [len(contours), 5_000, 50_000]
    storage = {
        "per_candidate_bytes": {
            f"contour_n{n_points}": n_points * 2 * bytes_per_coordinate
            for n_points in SAMPLE_POINTS
        },
        "per_candidate_bytes_features": feature_dim * bytes_per_coordinate,
        "totals": {
            f"candidates_{count}": {
                f"n{n_points}": {
                    "contours_mib": round(count * n_points * 2 * bytes_per_coordinate / 2**20, 2),
                    "features_mib": round(count * feature_dim * bytes_per_coordinate / 2**20, 2),
                }
                for n_points in SAMPLE_POINTS
            }
            for count in candidates
        },
    }

    def timeit(repeats: int, call: Any) -> float:
        started = time.perf_counter()
        for _ in range(repeats):
            call()
        return (time.perf_counter() - started) / repeats

    reference_block = (
        table[table["n_points"] == REFERENCE_POINTS]
        .sort_values("candidate_id")
        .reset_index(drop=True)
    )
    geometry_names = ("compactness", "solidity", "aspect_ratio", "diameter_fill")
    features = np.concatenate(
        [
            reference_block[[f"fd_{i:02d}" for i in range(1, STABILITY_HARMONICS + 1)]].to_numpy(),
            reference_block[[f"stat_{name}" for name in geometry_names]].to_numpy(),
        ],
        axis=1,
    ).astype(np.float32)

    scan_seconds = timeit(20, lambda: np.linalg.norm(features - features[0], axis=1))
    coarse_scan = {
        "measured_candidates": int(features.shape[0]),
        "feature_dim": int(features.shape[1]),
        "full_scan_seconds": round(scan_seconds, 6),
        "per_candidate_microseconds": round(scan_seconds / features.shape[0] * 1e6, 3),
        "projected_50k_full_scan_seconds": round(scan_seconds / features.shape[0] * 50_000, 3),
        "note": (
            "single-thread NumPy L2 over the study descriptor "
            f"({STABILITY_HARMONICS} harmonics + 4 geometry columns); "
            "the released index vector is wider, cost scales with width"
        ),
    }

    reference = stored_shapes[0]
    rerank: dict[str, Any] = {}
    for n_points in SAMPLE_POINTS:
        other = arc_points(reference, n_points)
        pair_seconds = timeit(
            200,
            lambda a=reference, b=other: symmetric_chamfer(a, b, cKDTree),
        )
        rerank[f"n{n_points}"] = {
            "per_pair_microseconds": round(pair_seconds * 1e6, 2),
            "top100_seconds": round(pair_seconds * 100, 4),
        }

    feature_seconds = timeit(
        3,
        lambda: fourier_descriptor(reference, STABILITY_HARMONICS),
    )
    return {
        "storage": storage,
        "coarse_scan": coarse_scan,
        "rerank_proxy_symmetric_chamfer": rerank,
        "single_descriptor_microseconds": round(feature_seconds * 1e6, 2),
        "caveat": (
            f"timings are this machine and this run (feature bytes assume the released "
            f"index width of {feature_dim} columns); scan cost grows linearly in candidates"
        ),
    }


def arc_points(points: np.ndarray, n_points: int) -> np.ndarray:
    """Resample a normalized contour to ``n_points`` for the cost proxy only."""
    ring = np.vstack([points, points[:1]])
    return np.asarray(build_contour(ring, n_points).points, dtype=float)


def symmetric_chamfer(a: np.ndarray, b: np.ndarray, tree_factory: Any) -> float:
    """Mean nearest-neighbour distance in both directions (re-rank proxy)."""
    da = tree_factory(b).query(a, k=1)[0]
    db = tree_factory(a).query(b, k=1)[0]
    return float(0.5 * (da.mean() + db.mean()))


def figure_comparison(
    picked: dict[str, dict[str, Any]],
    rings: dict[str, np.ndarray],
    table: pd.DataFrame,
    out_path: Path,
) -> dict[str, Any]:
    """Original boundary vs 64 / 128 / 256 samples at one shared world scale."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    categories = [c for c in CATEGORIES if c in picked]
    fig, axes = plt.subplots(
        len(categories), 4, figsize=(11.0, 2.7 * len(categories)), squeeze=False
    )
    for row, category in enumerate(categories):
        candidate_id = picked[category]["candidate_id"]
        ring = rings[candidate_id]
        minx, miny = ring[:-1, 0].min(), ring[:-1, 1].min()
        maxx, maxy = ring[:-1, 0].max(), ring[:-1, 1].max()
        pad = 0.06 * max(maxx - minx, maxy - miny)
        limits = (minx - pad, maxx + pad, miny - pad, maxy + pad)
        panels: list[dict[str, Any]] = []
        for column in range(4):
            ax = axes[row][column]
            ax.set_xlim(limits[0], limits[1])
            ax.set_ylim(limits[2], limits[3])
            ax.set_aspect("equal")
            ax.set_xticks([])
            ax.set_yticks([])
            if column == 0:
                source = exterior_only(_polygon_of(ring))
                draw_geometry(ax, source, facecolor="#dce9f4", edgecolor="#1f3f56", lw=0.7)
                ax.set_title(
                    f"original boundary\n{ring.shape[0] - 1} vertices",
                    fontsize=7.0,
                )
                panels.append({"panel": "original", "n_source_vertices": int(ring.shape[0] - 1)})
            else:
                n_points = SAMPLE_POINTS[column - 1]
                contour = build_contour(ring, n_points)
                metric = contour.metric_points
                closed = np.vstack([metric, metric[:1]])
                ax.plot(closed[:, 0], closed[:, 1], color="#c0392b", lw=0.9)
                ax.plot(metric[:, 0], metric[:, 1], "o", ms=1.6, color="#c0392b")
                ax.plot(ring[:, 0], ring[:, 1], "-", color="#1f3f56", lw=0.35, alpha=0.55)
                row_data = table[
                    (table["candidate_id"] == candidate_id) & (table["n_points"] == n_points)
                ].iloc[0]
                ax.set_title(
                    f"N={n_points}\nHausdorff {row_data['hausdorff_m']:.0f} m "
                    f"({row_data['hausdorff_norm']:.3f} R_eq) - IoU {row_data['region_iou']:.3f}",
                    fontsize=7.0,
                )
                panels.append(
                    {
                        "panel": f"n{n_points}",
                        "hausdorff_m": round(float(row_data["hausdorff_m"]), 1),
                        "hausdorff_norm": round(float(row_data["hausdorff_norm"]), 4),
                        "region_iou": round(float(row_data["region_iou"]), 4),
                        "perimeter_retention": round(float(row_data["perimeter_retention"]), 4),
                        "self_intersected": bool(row_data["self_intersected"]),
                    }
                )
        axes[row][0].set_ylabel(
            f"{category}\n{short_id(candidate_id, 24)}\n"
            f"{picked[category]['subtype']} - {picked[category]['area_ha']:.3g} ha",
            fontsize=7.5,
        )
        picked[category]["panels"] = panels
    fig.suptitle(PROBE_LABEL, fontsize=9.5)
    fig.tight_layout(rect=(0.06, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name, "categories": categories}


def figure_zoom(
    picked: dict[str, dict[str, Any]],
    rings: dict[str, np.ndarray],
    table: pd.DataFrame,
    projection: AeqdProjection,
    out_path: Path,
) -> dict[str, Any]:
    """Zoom on the largest deviation for the complex shapes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    categories = [c for c in ZOOM_CATEGORIES if c in picked]
    fig, axes = plt.subplots(
        len(categories), 4, figsize=(11.0, 2.9 * len(categories)), squeeze=False
    )
    entries: list[dict[str, Any]] = []
    for row, category in enumerate(categories):
        candidate_id = picked[category]["candidate_id"]
        ring = rings[candidate_id]
        x, y, distance = max_deviation_point(ring, build_contour(ring, 256).metric_points)
        extent = max(float(np.ptp(ring[:, 0])), float(np.ptp(ring[:, 1])))
        half = max(1.6 * distance, 0.03 * extent)
        limits = (x - half, x + half, y - half, y + half)
        for column in range(4):
            ax = axes[row][column]
            ax.set_xlim(limits[0], limits[1])
            ax.set_ylim(limits[2], limits[3])
            ax.set_aspect("equal")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.plot(ring[:, 0], ring[:, 1], color="#1f3f56", lw=0.8, alpha=0.9)
            if column > 0:
                n_points = SAMPLE_POINTS[column - 1]
                metric = build_contour(ring, n_points).metric_points
                closed = np.vstack([metric, metric[:1]])
                ax.plot(closed[:, 0], closed[:, 1], color="#c0392b", lw=0.9)
                ax.plot(metric[:, 0], metric[:, 1], "o", ms=1.8, color="#c0392b")
                row_data = table[
                    (table["candidate_id"] == candidate_id) & (table["n_points"] == n_points)
                ].iloc[0]
                ax.set_title(
                    f"N={n_points} - local gap {row_data['hausdorff_m']:.0f} m", fontsize=7.5
                )
            else:
                ax.plot([x], [y], "*", ms=9, color="#d35400")
                ax.set_title("original boundary (* = worst gap)", fontsize=7.5)
        axes[row][0].set_ylabel(f"{category}\n{short_id(candidate_id, 24)}", fontsize=7.5)
        lon, lat = projection.to_lonlat(np.array([[x, y]]))[0]
        entries.append(
            {
                "category": category,
                "candidate_id": candidate_id,
                "subtype": picked[category]["subtype"],
                "area_ha": picked[category]["area_ha"],
                "worst_deviation_m_at_n256": round(distance, 1),
                "zoom_center_lonlat": [round(float(lon), 6), round(float(lat), 6)],
            }
        )
    fig.suptitle(f"Local zoom on the worst-deviation region - {PROBE_LABEL}", fontsize=9.0)
    fig.tight_layout(rect=(0.06, 0, 1, 0.95))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name, "entries": entries}


def figure_scatter(table: pd.DataFrame, out_path: Path) -> dict[str, Any]:
    """Error distributions over all candidates instead of a single mean."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.6))
    colors = {64: "#2e86c1", 128: "#117864", 256: "#c0392b"}
    for n_points in SAMPLE_POINTS:
        block = table[table["n_points"] == n_points]
        axes[0].scatter(
            np.log10(block["area_ha"].to_numpy()),
            block["hausdorff_norm"].to_numpy(),
            s=6,
            alpha=0.55,
            color=colors[n_points],
            label=f"N={n_points}",
        )
        axes[1].scatter(
            block["perimeter_retention"].to_numpy(),
            block["region_iou"].to_numpy(),
            s=6,
            alpha=0.55,
            color=colors[n_points],
        )
    axes[0].set_yscale("log")
    axes[0].axhline(HAUSDORFF_NORM_TARGET, color="k", ls="--", lw=0.7)
    axes[0].set_xlabel("log10 area (ha)")
    axes[0].set_ylabel("Hausdorff / equal-area radius")
    axes[0].set_title(f"a) boundary error vs size (dashed = {HAUSDORFF_NORM_TARGET})", fontsize=8)
    axes[1].axhline(IOU_TARGET, color="k", ls="--", lw=0.7)
    axes[1].set_xlabel("perimeter retention (polyline / source)")
    axes[1].set_ylabel("region IoU vs source exterior")
    axes[1].set_title(f"b) area agreement (dashed = {IOU_TARGET})", fontsize=8)
    block = table[table["n_points"] == REFERENCE_POINTS]
    axes[2].hist(block["region_iou"].to_numpy(), bins=40, color="#117864")
    axes[2].axvline(IOU_TARGET, color="k", ls="--", lw=0.7)
    axes[2].set_xlabel(f"region IoU at N={REFERENCE_POINTS}")
    axes[2].set_ylabel("candidates")
    axes[2].set_title("c) IoU distribution at the released N", fontsize=8)
    axes[0].legend(fontsize=7)
    fig.suptitle(PROBE_LABEL, fontsize=9.5)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"file": out_path.name}


def failure_cases(table: pd.DataFrame) -> dict[str, Any]:
    """Every candidate whose reconstruction self-intersects, by sample count.

    Reported by id with its real source metrics: these are the shapes the P0-4
    re-rank has to survive, and the honest counter-evidence to a mean IoU of 0.99.
    """
    out: dict[str, Any] = {"self_intersecting_by_n": {}}
    for n_points in sorted(table["n_points"].unique()):
        block = table[table["n_points"] == n_points]
        out["self_intersecting_by_n"][f"n{int(n_points)}"] = int(block["self_intersected"].sum())
    at_reference = table[(table["n_points"] == REFERENCE_POINTS) & table["self_intersected"]]
    out["self_intersecting_at_reference"] = [
        {
            "candidate_id": str(row["candidate_id"]),
            "source_id": str(row["source_id"]),
            "subtype": str(row["subtype"]),
            "area_ha": round(float(row["area_ha"]), 3),
            "n_source_vertices": int(row["n_source_vertices"]),
            "region_iou": round(float(row["region_iou"]), 4),
            "hausdorff_norm": round(float(row["hausdorff_norm"]), 4),
            "perimeter_retention": round(float(row["perimeter_retention"]), 4),
            "validity_reason": str(row["validity_reason"]),
            "lon": round(float(row["lon"]), 6),
            "lat": round(float(row["lat"]), 6),
        }
        for _, row in at_reference.sort_values("region_iou").iterrows()
    ]
    thin = table[table["n_points"] == REFERENCE_POINTS].nsmallest(
        10, "perimeter_retention"
    )
    out["worst_perimeter_retention_at_reference"] = [
        {
            "candidate_id": str(row["candidate_id"]),
            "subtype": str(row["subtype"]),
            "area_ha": round(float(row["area_ha"]), 3),
            "perimeter_retention": round(float(row["perimeter_retention"]), 4),
            "max_shortcut_ratio": round(float(row["max_shortcut_ratio"]), 4),
            "n_source_vertices": int(row["n_source_vertices"]),
        }
        for _, row in thin.iterrows()
    ]
    return out


def aggregate(table: pd.DataFrame) -> dict[str, Any]:
    """Per-N percentiles and counts."""
    out: dict[str, Any] = {}
    for n_points in SAMPLE_POINTS:
        block = table[table["n_points"] == n_points]
        out[f"n{n_points}"] = {
            "candidates": int(len(block)),
            "hausdorff_m": {
                key: round(float(np.percentile(block["hausdorff_m"], q)), 2)
                for key, q in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100))
            },
            "hausdorff_norm": {
                key: round(float(np.percentile(block["hausdorff_norm"], q)), 4)
                for key, q in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100))
            },
            "chamfer_norm": {
                key: round(float(np.percentile(block["chamfer_norm"], q)), 4)
                for key, q in (("p50", 50), ("p90", 90), ("max", 100))
            },
            "region_iou": {
                "min": round(float(block["region_iou"].min()), 4),
                "p10": round(float(np.percentile(block["region_iou"], 10)), 4),
                "p50": round(float(np.percentile(block["region_iou"], 50)), 4),
                "mean": round(float(block["region_iou"].mean()), 4),
            },
            "area_rel_error_p90_abs": round(
                float(np.percentile(np.abs(block["area_rel_error"]), 90)), 4
            ),
            "perimeter_retention_p10": round(
                float(np.percentile(block["perimeter_retention"], 10)), 4
            ),
            "max_shortcut_ratio_p90": round(
                float(np.percentile(block["max_shortcut_ratio"], 90)), 4
            ),
            "chord_dispersion_max": round(float(block["chord_dispersion"].max()), 4),
            "self_intersecting_polylines": int(block["self_intersected"].sum()),
            "source_rings_needing_repair": int((~block["source_ring_valid"]).sum()),
        }
    return out


def validate_cloud_metrics(
    rings: dict[str, np.ndarray], sample_points: tuple[int, ...]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Check the cloud-based deviation numbers against independent distances.

    ``contour_fidelity`` densifies both curves, so its Hausdorff value is an
    approximation whose documented error scale is one cloud step
    (``step / densify_divisor``). Two references are used: GEOS
    ``hausdorff_distance`` on the same two closed polylines, and the same cloud
    method run 8x finer. Without this check the study would report a distance it
    never verified against a real one — and the sampled contour is an *open*
    array, so it has to be closed before either comparison or the missing
    closing chord dominates the result.
    """
    import shapely

    rows: list[dict[str, Any]] = []
    for candidate_id, ring in sorted(rings.items()):
        for n_points in sample_points:
            contour = build_contour(ring, n_points)
            sampled = np.vstack([contour.metric_points, contour.metric_points[:1]])
            metrics = contour_fidelity(ring, contour.metric_points)
            refined = contour_fidelity(
                ring,
                contour.metric_points,
                densify_divisor=VALIDATION_REFINED_DIVISOR,
                cloud_cap=1_000_000,
            )
            geos = float(
                shapely.hausdorff_distance(shapely.LineString(ring), shapely.LineString(sampled))
            )
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "n_points": n_points,
                    "hausdorff_cloud_m": metrics.hausdorff_m,
                    "hausdorff_geos_m": geos,
                    "hausdorff_refined_m": refined.hausdorff_m,
                    "gap_vs_geos_m": metrics.hausdorff_m - geos,
                    "gap_vs_refined_m": metrics.hausdorff_m - refined.hausdorff_m,
                    "cloud_step_m": metrics.step_m / DEFAULT_DENSIFY_DIVISOR,
                }
            )
    frame = pd.DataFrame(rows)
    per_point: dict[str, Any] = {}
    for n_points, block in frame.groupby("n_points"):
        ratio = block["gap_vs_geos_m"].abs() / block["cloud_step_m"]
        per_point[str(int(n_points))] = {
            "pairs": int(len(block)),
            "median_cloud_m": round(float(block["hausdorff_cloud_m"].median()), 6),
            "median_geos_m": round(float(block["hausdorff_geos_m"].median()), 6),
            "median_gap_vs_geos_m": round(float(block["gap_vs_geos_m"].median()), 6),
            "max_abs_gap_vs_geos_m": round(float(block["gap_vs_geos_m"].abs().max()), 6),
            "max_gap_vs_geos_over_cloud_step": round(float(ratio.max()), 6),
            "pairs_over_one_cloud_step_vs_geos": int((ratio > 1.0 + 1e-9).sum()),
            "median_gap_vs_refined_m": round(float(block["gap_vs_refined_m"].median()), 6),
            "max_abs_gap_vs_refined_m": round(float(block["gap_vs_refined_m"].abs().max()), 6),
            "pairs_over_cloud_step_vs_refined": int(
                (block["gap_vs_refined_m"].abs() > block["cloud_step_m"] * 1.125 + 1e-9).sum()
            ),
        }
    summary = {
        "what": "study cloud Hausdorff vs GEOS discrete Hausdorff and an 8x finer cloud",
        "reference": "shapely.hausdorff_distance on the CLOSED curves (vertex-based, "
        "a lower bound of the continuous distance)",
        "expected_gap_scale": "one cloud step = step_m / 8",
        "densify_divisor": DEFAULT_DENSIFY_DIVISOR,
        "refined_divisor": VALIDATION_REFINED_DIVISOR,
        "per_n": per_point,
    }
    return frame, summary


def run_validation(
    rings: dict[str, np.ndarray],
    sample_points: tuple[int, ...],
    out_dir: Path,
    args: argparse.Namespace,
) -> int:
    """``--validate-exact``: write the metric-check artifacts and stop."""
    started = time.perf_counter()
    frame, summary = validate_cloud_metrics(rings, sample_points)
    csv_path = out_dir / VALIDATION_CSV
    frame.to_csv(csv_path, index=False, float_format="%.6g")
    payload = {
        "stage": "P0-3 sampling study - fidelity metric validation",
        "kind": "measurement correctness (NOT a retrieval result)",
        "candidates": len(rings),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "data": {"raw_path": args.raw.as_posix()},
        **summary,
        "reproduce": {
            "command": "python "
            + " ".join(
                shlex.quote(item)
                for item in [
                    "scripts/analyze_sampling.py",
                    "--validate-exact",
                    "--raw",
                    str(args.raw),
                    "--contours",
                    str(args.contours),
                    "--out-dir",
                    str(out_dir),
                ]
            )
        },
    }
    json_path = out_dir / VALIDATION_JSON
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary["per_n"], indent=2))
    print(f"validation written: {json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw/wisconsin_water.parquet"))
    parser.add_argument("--contours", type=Path, default=Path("data/processed/contours.parquet"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_feature_probe"))
    parser.add_argument("--points", default="64,128,256", help="comma list of sample counts")
    parser.add_argument("--limit", type=int, default=0, help="measure only the first K ids")
    parser.add_argument("--skip-figures", action="store_true")
    parser.add_argument(
        "--validate-exact",
        action="store_true",
        help="only compare the cloud deviation metrics against exact GEOS distances",
    )
    args = parser.parse_args(argv)

    sample_points = tuple(int(v) for v in args.points.split(","))
    if REFERENCE_POINTS not in sample_points:
        print(f"--points must include the reference N={REFERENCE_POINTS}", file=sys.stderr)
        return 2
    if min(sample_points) < 2 * STABILITY_HARMONICS + 2:
        print(
            f"smallest N must satisfy N//2-1 >= {STABILITY_HARMONICS} harmonics", file=sys.stderr
        )
        return 2

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    contours = load_contours(args.contours)
    stored_shapes = stack_contours(contours)

    print(f"rebuilding source rings from {args.raw} ...", flush=True)
    rings, projection, source_meta = source_rings(args.raw, contours, REFERENCE_POINTS)
    if args.limit:
        keep = sorted(rings)[: args.limit]
        rings = {candidate_id: rings[candidate_id] for candidate_id in keep}
    print(f"{len(rings)} candidates, sample counts {sample_points}", flush=True)
    if args.validate_exact:
        return run_validation(rings, sample_points, out_dir, args)

    table, measure_stats = measure(rings, contours, stored_shapes, sample_points, projection)
    if measure_stats["failed_measurements"]:
        print(
            f"WARNING {len(measure_stats['failed_measurements'])} measurements failed",
            file=sys.stderr,
        )
    csv_path = out_dir / "sampling_metrics.csv"
    table.to_csv(csv_path, index=False, float_format="%.6g")

    picked = select_representatives(rings, contours)
    summary: dict[str, Any] = {
        "stage": "P0-3 sampling-resolution study",
        "kind": "geometric fidelity + feature stability (NOT a retrieval result)",
        "not_a_retrieval_result": True,
        "query_sketch_used": False,
        "deterministic": True,
        "random_seed": None,
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "data": {
            "raw_path": args.raw.as_posix(),
            "contours_path": args.contours.as_posix(),
            "source_manifest": json.loads(
                (args.contours.parent / "data_manifest.json").read_text(encoding="utf-8")
            )["source"]["release"]
            if (args.contours.parent / "data_manifest.json").is_file()
            else None,
            **source_meta,
        },
        "parameters": {
            "sample_points": list(sample_points),
            "reference_points": REFERENCE_POINTS,
            "stability_harmonics": STABILITY_HARMONICS,
            "iou_target": IOU_TARGET,
            "hausdorff_norm_target": HAUSDORFF_NORM_TARGET,
            "query_stride": QUERY_STRIDE,
            "neighbour_k": NEIGHBOUR_K,
            "resampling": "arc-length uniform, from the ORIGINAL exterior ring",
            "forbidden_shortcut": "no interpolation of stored 128-point contours",
        },
        "measurement": measure_stats,
        "aggregate": aggregate(table),
        "failure_cases": failure_cases(table),
        "feature_stability": feature_stability(table),
        "adaptive_sampling_evidence": adaptive_evidence(table),
        "engineering_cost": engineering_cost(table, contours, stored_shapes),
        "representative_selection": picked,
        "selection_rules": selection_rules(),
        "reproduce": {
            "command": "python "
            + " ".join(
                shlex.quote(item)
                for item in [
                    "scripts/analyze_sampling.py",
                    "--raw",
                    str(args.raw),
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

    if not args.skip_figures:
        summary["figures"] = [
            figure_comparison(picked, rings, table, out_dir / "sampling_comparison.png"),
            figure_zoom(picked, rings, table, projection, out_dir / "sampling_zoom.png"),
            figure_scatter(table, out_dir / "sampling_error_scatter.png"),
        ]
    summary["table"] = {"file": csv_path.name, "rows": int(len(table))}
    (out_dir / "sampling_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary["aggregate"], indent=2))
    print(f"summary written: {out_dir / 'sampling_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
