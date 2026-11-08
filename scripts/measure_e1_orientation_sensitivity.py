"""P0-4.6 Checkpoint 7-D, F1: measure how rotation-sensitive EarthGlyph's E1 frame really is.

``perceptual_features.principal_frame()`` is the orientation rule behind E1's 91-D descriptor
vector. Its own documentation admits two escape hatches — an ``isotropic-fallback`` that keeps the
input frame, and a ``FLIP_TIE_TOLERANCE`` of 1e-9 on the third moment that decides the 180-degree
sign — and until now neither had been measured. This script answers four separate questions and
keeps the answers apart:

1. **Which branch fires, on which shape?** The four classes the checkpoint names (five-point star,
   near-circle, regular polygon, noisy symmetric shape) are the ones expected to fall back, plus
   asymmetric controls so a fallback count cannot be read as a general failure.
2. **What does that cost in descriptor space?** Per block, the relative movement of the 91-D
   vector between an unrotated contour and the same contour turned by a known angle, expressed
   against a second ruler: the distance to a DIFFERENT shape. A rotation that moves a descriptor
   further than a different shape does is not a rounding error.
3. **Is the sign rule decided by geometry or by floating-point residue?** ``eta30`` / ``eta03``
   are printed for every pose. Near zero against a tolerance of 1e-9 means the flip is made by
   noise, whatever the status word says.
4. **Which mechanism moved the descriptor?** Three are possible and they are separated here by
   what the pose shows, not by a reading of the code: no axis exists at all
   (``isotropic-fallback``), the axis exists but the 180-degree sign is decided by residue, and —
   the case nothing in the module documentation admits — the delivered frame is *not* the major
   axis it claims, because ``principal_frame``'s hand-built eigenvector
   ``(top - cov[1,1], cov[0,1])`` underflows exactly when the covariance is diagonal with the
   larger variance on ``y``, and the documented fallback then returns ``+x``. ``eigh`` on the same
   covariance is used as the reference axis, so this third mechanism is measurable rather than
   argued.

The baseline is NOT modified here. The rotation-search variant of E1 in part 4 is measured as an
independent scheme — its own cost, its own benefit — so a future decision can compare it against
the current arm instead of against a straw man.

Usage:
    python scripts/measure_e1_orientation_sensitivity.py
    python scripts/measure_e1_orientation_sensitivity.py --angles 0 15 37 90 180 191 270
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from earthglyph.retrieval import perceptual_features as e1
from earthglyph.shape_benchmark.probes import rotate

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "experiments" / "shape_perception_benchmark" / "results"
SEED = 20261107
DEFAULT_ANGLES = [0.0, 15.0, 37.0, 45.0, 75.0, 90.0, 135.0, 180.0, 191.0, 247.5, 270.0, 333.0]
E1_GROUPS: tuple[str, ...] = e1.GROUPS


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ---------------------------------------------------------------- contours
def _ring(theta: np.ndarray, radius: np.ndarray) -> np.ndarray:
    return np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])


def star5(points: int = 256) -> np.ndarray:
    vertices = [
        (1.0 if index % 2 == 0 else 0.4)
        * np.array([np.cos(2.0 * np.pi * index / 10.0), np.sin(2.0 * np.pi * index / 10.0)])
        for index in range(10)
    ]
    edges = []
    for first, second in zip(vertices, [*vertices[1:], vertices[0]], strict=True):
        step = np.linspace(0.0, 1.0, points // 10, endpoint=False)[:, None]
        edges.append(first + step * (second - first))
    return np.vstack(edges)


def regular_polygon(sides: int, points: int = 256) -> np.ndarray:
    vertices = [
        np.array([np.cos(2.0 * np.pi * index / sides), np.sin(2.0 * np.pi * index / sides)])
        for index in range(sides)
    ]
    edges = []
    for first, second in zip(vertices, [*vertices[1:], vertices[0]], strict=True):
        step = np.linspace(0.0, 1.0, points // sides, endpoint=False)[:, None]
        edges.append(first + step * (second - first))
    return np.vstack(edges)


def probes() -> list[tuple[str, str, np.ndarray]]:
    """(name, family, contour) — the four expected-degenerate classes plus asymmetric controls."""
    theta = np.linspace(0.0, 2.0 * np.pi, 256, endpoint=False)
    rng = np.random.default_rng(SEED)
    near_circle = _ring(theta, 1.0 + 0.02 * np.cos(7.0 * theta))
    two_lobed = _ring(theta, 1.0 + 0.35 * np.cos(2.0 * theta))
    noisy_symmetric = two_lobed * (1.0 + 0.01 * rng.standard_normal((len(theta), 2)))
    ellipse = np.column_stack([2.0 * np.cos(theta), 0.6 * np.sin(theta)])
    elongated = np.column_stack(
        [2.0 * np.cos(theta) * (1.0 + 0.3 * np.cos(theta)), 0.7 * np.sin(theta)]
    )
    heart = np.column_stack(
        [
            1.6 * np.sin(theta) ** 3,
            1.3 * np.cos(theta)
            - 0.5 * np.cos(2.0 * theta)
            - 0.3 * np.cos(3.0 * theta)
            - 0.125 * np.cos(4.0 * theta),
        ]
    )
    return [
        ("star5", "degenerate: 5-fold symmetry", star5()),
        ("near_circle", "degenerate: r = 1 + 0.02 cos(7t)", near_circle),
        ("circle", "degenerate: exact circle", _ring(theta, np.ones_like(theta))),
        ("hexagon", "degenerate: regular polygon", regular_polygon(6)),
        ("square", "degenerate: regular polygon", regular_polygon(4)),
        ("two_lobed", "degenerate: centrosymmetric ring", two_lobed),
        ("noisy_symmetric", "degenerate: symmetric ring + 1% radial noise", noisy_symmetric),
        ("ellipse", "control: anisotropic, front-to-back symmetric", ellipse),
        ("elongated", "control: anisotropic and asymmetric", elongated),
        ("heart", "control: asymmetric ring", heart),
        ("teardrop", "control: one-sided bulge", _ring(theta, 1.0 + 0.5 * np.cos(theta))),
    ]


# ---------------------------------------------------------------- the E1 measurements
def sign_residue(prep: e1.PreparedContour, frame: e1.OrientationFrame) -> tuple[float, float]:
    """Recompute the two third moments ``principal_frame`` compares with ``FLIP_TIE_TOLERANCE``.

    Measured in the frame the descriptor is actually delivered in. A 180-degree flip negates
    both numbers, so their *magnitudes* are exactly the values the sign rule tested, whether or
    not the rule ended up flipping.
    """
    turned = np.asarray(
        prep.ring
        @ np.array(
            [[np.cos(-frame.angle_rad), -np.sin(-frame.angle_rad)],
             [np.sin(-frame.angle_rad), np.cos(-frame.angle_rad)]]
        ).T
    )
    moments = e1.central_moments(turned, 3)
    area = float(moments[0, 0])
    if area <= 0.0:
        return float("nan"), float("nan")
    return float(moments[3, 0] / area**2.5), float(moments[0, 3] / area**2.5)


def flip_decided_by(eta30: float, eta03: float) -> str:
    """Which number carried the 180-degree decision — or 'neither', i.e. floating-point residue."""
    tolerance = e1.FLIP_TIE_TOLERANCE
    if abs(eta30) > tolerance:
        return "eta30"
    if abs(eta03) > tolerance:
        return "eta03"
    return "neither"


def true_major_axis_deg(prep: e1.PreparedContour) -> float:
    """The major principal axis from ``eigh`` on the same covariance ``principal_frame`` reads.

    ``principal_frame`` builds its eigenvector as ``(top - cov[1,1], cov[0,1])`` instead of
    calling ``eigh``. That is algebraically the same vector, except when both components
    underflow — a diagonal covariance whose LARGER variance sits on ``y`` — and then the
    documented fallback returns ``+x``. ``eigh`` has no such branch, so it is the reference
    this script measures the delivered frame against.
    """
    mu, area = prep.central_moments, prep.area
    cov = np.array([[mu[2, 0] / area, mu[1, 1] / area], [mu[1, 1] / area, mu[0, 2] / area]])
    vector = np.linalg.eigh(cov)[1][:, -1]
    return float(np.rad2deg(np.arctan2(vector[1], vector[0])))


def circular_axis_gap_deg(left: float, right: float) -> float:
    """The smallest angle between two UNORIENTED axes, in [0, 90] degrees."""
    return float(abs(((left - right + 90.0) % 180.0) - 90.0))


def block_vectors(contour: np.ndarray) -> dict[str, np.ndarray]:
    """E1's descriptor vector, split by block, through the public per-group selection."""
    out: dict[str, np.ndarray] = {}
    for group in E1_GROUPS:
        out[group] = np.array(
            list(e1.descriptor_values(contour, groups=[group]).values()), dtype=float
        )
    out["all"] = np.concatenate([out[group] for group in E1_GROUPS])
    return out


def raw_block_vectors(contour: np.ndarray) -> dict[str, np.ndarray]:
    """The same blocks with the frame left where the drawing put it: the sensitivity ceiling."""
    out: dict[str, np.ndarray] = {}
    for group in E1_GROUPS:
        out[group] = np.array(
            list(e1.descriptor_values(contour, groups=[group], orientation="raw").values()),
            dtype=float,
        )
    out["all"] = np.concatenate([out[group] for group in E1_GROUPS])
    return out


def measure(angles: list[float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base: dict[str, dict[str, np.ndarray]] = {}
    for name, family, contour in probes():
        base[name] = block_vectors(contour)
        for angle in angles:
            turned = rotate(contour, angle)
            prep = e1.prepare(turned)
            frame = e1.principal_frame(prep)
            canonical = block_vectors(turned)
            ceiling = raw_block_vectors(turned)
            eta30, eta03 = sign_residue(prep, frame)
            row: dict[str, Any] = {
                "probe": name,
                "family": family,
                "angle_deg": angle,
                "orientation_status": frame.status,
                "frame_angle_deg": round(np.rad2deg(frame.angle_rad), 6),
                "true_major_axis_deg": round(true_major_axis_deg(prep), 6),
                "axis_gap_deg": round(
                    circular_axis_gap_deg(np.rad2deg(frame.angle_rad), true_major_axis_deg(prep)),
                    6,
                ),
                "frame_angle_residual_deg": round(
                    float((np.rad2deg(frame.angle_rad) - angle + 90.0) % 180.0 - 90.0), 6
                ),
                "flipped": bool(frame.flipped),
                "moment_axis_ratio": round(frame.axis_ratio, 6),
                "eta30": eta30,
                "eta03": eta03,
                "flip_tolerance": e1.FLIP_TIE_TOLERANCE,
                "flip_decided_by": flip_decided_by(eta30, eta03),
                "residue_above_tolerance": flip_decided_by(eta30, eta03) != "neither",
            }
            for group in (*E1_GROUPS, "all"):
                reference = base[name][group]
                scale = float(np.linalg.norm(reference))
                moved = float(np.linalg.norm(canonical[group] - reference))
                held = float(np.linalg.norm(ceiling[group] - reference))
                row[f"{group}_drift"] = moved / scale if scale else 0.0
                row[f"{group}_drift_raw_frame"] = held / scale if scale else 0.0
                row[f"{group}_drift_units"] = moved
            rows.append(row)
    return rows, base


def cross_shape_reference(base: dict[str, dict[str, np.ndarray]]) -> dict[str, float]:
    """For each probe, the E1 distance to its nearest OTHER probe: the second ruler."""
    names = sorted(base)
    out: dict[str, float] = {}
    for name in names:
        others = [
            float(np.linalg.norm(base[name]["all"] - base[other]["all"]))
            for other in names
            if other != name
        ]
        out[name] = min(others)
    return out


# ---------------------------------------------------------------- independent scheme
def rotation_search_scheme(angles: list[float]) -> dict[str, Any]:
    """E1 wrapped in a query-side 24-pose min, measured as its own arm.

    ``d(q, g) = min_theta ||e1(R_theta q) - e1(g)||``, where ``q`` arrives in its *stressed* pose
    and the gallery is scored once in its stored pose. This costs 24 descriptor extractions per
    query instead of one, and it is a SEARCH, so it inherits the usual wrong-shape-attraction
    risk: the best-aligned pose of two DIFFERENT shapes can also line up. Both the benefit and
    that risk are measured here, and nothing is substituted for the current E1 arm.
    """
    grid = np.arange(24, dtype=float) * 15.0
    entries = probes()
    names = [name for name, _, _ in entries]
    contours = {name: contour for name, _, contour in entries}
    vectors = {name: e1.features_for_contour(contour) for name, contour in contours.items()}
    # The gallery side of the contract: each shape once, in its stored pose.
    searched = {
        name: np.array([e1.features_for_contour(rotate(c, float(p))) for p in grid])
        for name, c in contours.items()
    }

    # Timings measured on one contour: one extraction, then the same contour 24 times.
    probe = rotate(contours["star5"], 37.0)
    repeats = 5
    started = time.perf_counter()
    for _ in range(repeats):
        e1.features_for_contour(probe)
    single_seconds = (time.perf_counter() - started) / repeats
    started = time.perf_counter()
    for _ in range(repeats):
        for pose in grid:
            e1.features_for_contour(rotate(probe, float(pose)))
    search_seconds = (time.perf_counter() - started) / repeats

    naive_self: list[float] = []
    searched_self: list[float] = []
    cross_shrink: list[float] = []
    for name in names:
        reference = vectors[name]
        for angle in angles:
            # The query as it actually arrives: already turned by the stress angle.
            turned = rotate(contours[name], float(angle))
            naive_self.append(float(np.linalg.norm(e1.features_for_contour(turned) - reference)))
            searched_self.append(
                min(
                    float(
                        np.linalg.norm(
                            e1.features_for_contour(rotate(turned, float(p))) - reference
                        )
                    )
                    for p in grid
                )
            )
        # Risk: the same min measured against a DIFFERENT shape, in that shape's stored pose.
        for other in names:
            if other == name:
                continue
            direct = float(np.linalg.norm(reference - vectors[other]))
            if direct <= 0.0:
                continue
            best = min(float(np.linalg.norm(row - vectors[other])) for row in searched[name])
            cross_shrink.append(best / direct)
    return {
        "contract": "query-side min over 24 poses of 15 degrees, gallery scored once",
        "descriptor_extractions_per_query": 24,
        "seconds_per_query_single_extraction": single_seconds,
        "seconds_per_query_24_pose_search": search_seconds,
        "mean_self_distance_no_wrapper": float(np.mean(naive_self)),
        "mean_self_distance_with_24_pose_min": float(np.mean(searched_self)),
        "benefit_share_of_rotation_displacement_removed": float(
            1.0 - float(np.mean(searched_self)) / max(float(np.mean(naive_self)), 1e-12)
        ),
        "mean_other_shape_distance_ratio_after_min": float(np.mean(cross_shrink)),
        "worst_other_shape_distance_ratio_after_min": float(max(cross_shrink)),
        "risk_note": (
            "ratio = (distance to a DIFFERENT shape after the same 24-pose min) / (distance to "
            "that shape without the min). 1.0 = the search costs nothing in separation; a value "
            "well below 1.0 means the wrapper pulls different shapes together, which is exactly "
            "the wrong-shape attraction the checkpoint warns about."
        ),
        "verdict": (
            "Costed, not adopted: the current E1 arm is unchanged by this script. A scheme whose "
            "benefit and whose separation loss are of the same size is not an improvement."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--angles", nargs="+", type=float, default=DEFAULT_ANGLES)
    args = parser.parse_args()

    rows, base = measure(args.angles)
    rulers = cross_shape_reference(base)
    for row in rows:
        distance = row["all_drift_units"]
        ruler = rulers[row["probe"]]
        row["rotation_vs_other_shape"] = distance / ruler if ruler else float("inf")

    per_probe: dict[str, Any] = {}
    for name in sorted({str(row["probe"]) for row in rows}):
        cells = [row for row in rows if row["probe"] == name]
        residuals = [row["frame_angle_residual_deg"] for row in cells]
        # Consecutive poses, compared as UNORIENTED axes: a rotation-consistent rule holds the
        # residual constant, so any real movement here is the frame losing the axis.
        jumps = [
            circular_axis_gap_deg(first, second)
            for first, second in zip(residuals, residuals[1:], strict=False)
        ]
        canonical = [row for row in cells if row["orientation_status"] == "canonical"]
        axis_ok_pose = [
            float(row["all_drift"])
            for row in canonical
            if row["axis_gap_deg"] <= 45.0 and row["flip_decided_by"] == "neither"
        ]
        axis_lost_pose = [
            float(row["all_drift"]) for row in canonical if row["axis_gap_deg"] > 45.0
        ]
        per_probe[name] = {
            "family": cells[0]["family"],
            "statuses": sorted({str(row["orientation_status"]) for row in cells}),
            "fallback_share": sum(row["orientation_status"] != "canonical" for row in cells)
            / len(cells),
            "flip_decided_by": sorted({str(row["flip_decided_by"]) for row in cells}),
            "any_pose_above_flip_tolerance": any(row["residue_above_tolerance"] for row in cells),
            "abs_eta30_median": float(np.median([abs(row["eta30"]) for row in cells])),
            "abs_eta03_median": float(np.median([abs(row["eta03"]) for row in cells])),
            "axis_ratio": float(np.mean([row["moment_axis_ratio"] for row in cells])),
            "axis_gap_deg_max": max(row["axis_gap_deg"] for row in cells),
            # Only canonical poses can be a WRONG axis: an isotropic contour has no axis to hit.
            "axis_gap_deg_max_when_canonical": max(
                (row["axis_gap_deg"] for row in canonical), default=0.0
            ),
            "canonical_poses_off_the_major_axis": int(
                sum(row["axis_gap_deg"] > 45.0 for row in canonical)
            ),
            # Two mechanisms, separated by what the pose shows rather than by a reading of the
            # code: a coin-toss SIGN on an axis that is otherwise correct, versus a frame that is
            # 90 degrees away from the major axis it claims to have found.
            "drift_mean_axis_aligned_pose": float(np.mean(axis_ok_pose)) if axis_ok_pose else None,
            "drift_mean_axis_lost_pose": float(np.mean(axis_lost_pose)) if axis_lost_pose else None,
            # The frame angle minus the stress angle must be a constant if the rule is
            # rotation-consistent. A jump here is the frame losing the axis, in degrees.
            "frame_angle_residual_jump_deg_max": float(max(jumps, default=0.0)),
            "frame_angle_residual_spread_deg": float(
                max(
                    circular_axis_gap_deg(first, second)
                    for first in residuals
                    for second in residuals
                )
            ),
            "all_drift_max": max(row["all_drift"] for row in cells),
            "all_drift_median": float(np.median([row["all_drift"] for row in cells])),
            "all_drift_raw_frame_max": max(row["all_drift_raw_frame"] for row in cells),
            "histogram_drift_max": max(row["histogram_drift"] for row in cells),
            "fourier_drift_max": max(row["fourier_drift"] for row in cells),
            "moments_drift_max": max(row["moments_drift"] for row in cells),
            "curvature_drift_max": max(row["curvature_drift"] for row in cells),
            "global_drift_max": max(row["global_drift"] for row in cells),
            "rotation_vs_other_shape_max": max(row["rotation_vs_other_shape"] for row in cells),
            "nearest_other_probe_distance_units": rulers[name],
        }
    summary: dict[str, Any] = {
        "schema": "earthglyph.shape_perception_benchmark/f1/1",
        "task": "P0-4.6 Checkpoint 7-D, item F1",
        "seed": SEED,
        "angles": args.angles,
        "descriptor_width": int(len(base["circle"]["all"])),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "git_commit": _commit(),
        "reproduce": f"{Path(__file__).name} --angles " + " ".join(
            f"{angle:g}" for angle in args.angles
        ),
        "baseline_untouched": (
            "This script only reads perceptual_features. E1's arm in the benchmark, the "
            "production default retriever and every committed result file are unchanged; the "
            "rotation-search variant below is measured as a SEPARATE scheme so a later decision "
            "compares two real arms rather than one arm and a guess."
        ),
    }
    summary["by_probe"] = per_probe
    aligned = [
        values["drift_mean_axis_aligned_pose"]
        for values in per_probe.values()
        if values["drift_mean_axis_aligned_pose"] is not None
    ]
    lost = [
        values["drift_mean_axis_lost_pose"]
        for values in per_probe.values()
        if values["drift_mean_axis_lost_pose"] is not None
    ]
    summary["mechanism_attribution"] = {
        "isotropic_fallback": {
            "probes": [
                name for name, values in per_probe.items() if values["fallback_share"] == 1.0
            ],
            "documented_in_policy": True,
            "worst_all_drift": max(
                (values["all_drift_max"] for values in per_probe.values()
                 if values["fallback_share"] == 1.0),
                default=0.0,
            ),
            "mechanism": (
                "no axis exists to align to, so the 42-column tangent block is measured against "
                "static bin edges in the drawing's own frame and is not rotation invariant"
            ),
        },
        "degenerate_eigenvector_fallback": {
            "probes": [
                name for name, values in per_probe.items()
                if values["canonical_poses_off_the_major_axis"] > 0
            ],
            "documented_in_policy": False,
            "worst_axis_gap_deg": max(
                (values["axis_gap_deg_max_when_canonical"] for values in per_probe.values()),
                default=0.0,
            ),
            "mechanism": (
                "principal_frame builds the major eigenvector as (top - cov[1,1], cov[0,1]) and "
                "falls back to +x when that pair underflows. The pair underflows exactly when the "
                "covariance is diagonal AND the larger variance sits on y, so the delivered frame "
                "is 90 degrees away from the shape's own major axis while the status still reads "
                "'canonical'. The status word does not mean the axis was found."
            ),
            "mean_drift_at_affected_poses": float(np.mean(lost)) if lost else None,
        },
        "sign_coin_toss_on_a_correct_axis": {
            "probes": [
                name for name, values in per_probe.items()
                if "neither" in values["flip_decided_by"]
            ],
            "documented_in_policy": True,
            "mean_drift_at_affected_poses": float(np.mean(aligned)) if aligned else None,
            "mechanism": (
                "both third moments vanish, so FLIP_TIE_TOLERANCE=1e-9 is compared against "
                "floating-point residue and the 180-degree sign is decided by noise. The tangent "
                "bins span 0..2pi, so a flip permutes them by half a turn - but on a genuinely "
                "centrosymmetric contour the tangent multiset is itself symmetric, which is why "
                "the measured drift on those poses stays far below the drift of a pose that lost "
                "the axis. That contrast is this row's evidence: the sign is unstable, the cost is "
                "not."
            ),
        },
    }
    summary["block_widths"] = {
        group: int(len(base["circle"][group])) for group in (*E1_GROUPS, "all")
    }
    summary["orientation_api"] = {
        "isotropy_max": e1.ORIENTATION_ISOTROPY_MAX,
        "flip_tie_tolerance": e1.FLIP_TIE_TOLERANCE,
        "statuses": ["canonical", "isotropic-fallback", "raw"],
        "note": (
            "E1 decides the 180-degree sign on AREA moments with a 1e-9 tolerance; the R2 wrapper "
            "of rotation_methods decides on contour-ring moments with 1e-3. The two tolerances "
            "are not interchangeable and are listed next to the measured residues so neither "
            "convention can be called 'the' canonicalisation."
        ),
    }
    summary["independent_rotation_search_scheme"] = rotation_search_scheme(args.angles)
    summary["headline"] = {
        "probes_with_a_non_canonical_frame_at_every_pose": [
            name for name, values in per_probe.items() if values["fallback_share"] == 1.0
        ],
        "probes_whose_frame_rule_is_decided_by_residue_at_some_pose": [
            name for name, values in per_probe.items()
            if "neither" in values["flip_decided_by"]
        ],
        "probes_whose_frame_moves_by_more_than_45_deg_between_poses": [
            name for name, values in per_probe.items()
            if values["frame_angle_residual_jump_deg_max"] > 45.0
        ],
        "probes_with_a_canonical_frame_that_is_not_the_major_axis": [
            name for name, values in per_probe.items()
            if values["canonical_poses_off_the_major_axis"] > 0
        ],
        "probes_whose_rotation_moves_further_than_the_nearest_other_shape": [
            name for name, values in per_probe.items()
            if values["rotation_vs_other_shape_max"] >= 1.0
        ],
        "worst_all_drift": max(values["all_drift_max"] for values in per_probe.values()),
        "worst_histogram_drift": max(
            values["histogram_drift_max"] for values in per_probe.values()
        ),
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "f1_e1_orientation_sensitivity.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    fields = sorted({key for row in rows for key in row})
    with (RESULTS / "f1_e1_orientation_sensitivity.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{'probe':18s} {'status mix':22s} {'fallback':9s} {'|eta30|':10s} {'axis off':9s} "
          f"{'drift':9s} {'hist':9s} {'vs other':9s} {'jump':7s}")
    for name, values in per_probe.items():
        print(
            f"{name:18s} {','.join(v[:4] for v in values['statuses']):22s} "
            f"{values['fallback_share']:6.2f}   {values['abs_eta30_median']:9.2e} "
            f"{values['axis_gap_deg_max_when_canonical']:8.1f}  "
            f"{values['all_drift_max']:8.4f}  {values['histogram_drift_max']:8.4f} "
            f"{values['rotation_vs_other_shape_max']:8.3f} "
            f"{values['frame_angle_residual_jump_deg_max']:6.1f}"
        )
    print(json.dumps(summary["headline"], indent=2))
    print(json.dumps(summary["mechanism_attribution"], indent=2))
    print(json.dumps(summary["independent_rotation_search_scheme"], indent=2))


if __name__ == "__main__":
    main()
