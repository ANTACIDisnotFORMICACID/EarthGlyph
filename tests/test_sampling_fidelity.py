"""Tests for resampling correctness and contour-fidelity metrics (Task C/G).

The sampling study compares N=64 / 128 / 256, so the primitives it measures have
to be provably correct on their own: the resampler must place points at equal
arc length, the fidelity metrics must reproduce analytic values on shapes where
the answer is known, and an extreme outline must never be dropped silently.

Everything here is geometric correctness. Retrieval quality is not measurable
until P0-4 and is not claimed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely

from earthglyph.geometry import arc_length_resample, build_contour, exterior_ring, open_ring
from earthglyph.geometry.fidelity import (
    FidelityError,
    chord_lengths,
    contour_fidelity,
    exterior_only,
    region_iou,
    ring_polygon,
    summarize_metrics,
)
from earthglyph.geometry.resampling import ContourError, ring_perimeter, signed_area

REPO = Path(__file__).resolve().parents[1]
REAL_RAW = REPO / "data" / "raw" / "wisconsin_water.parquet"
requires_real_data = pytest.mark.skipif(
    not REAL_RAW.is_file(), reason="real extract not present on this machine"
)

N_OPTIONS = (64, 128, 256)


def closed(points: np.ndarray) -> np.ndarray:
    return np.vstack([points, points[:1]])


def circle_ring(radius: float, n: int) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return closed(np.column_stack([radius * np.cos(angle), radius * np.sin(angle)]))


def square_ring(side: float = 100.0) -> np.ndarray:
    half = side / 2.0
    return closed([(-half, -half), (half, -half), (half, half), (-half, half)])


def needle_ring(length: float = 1000.0, width: float = 1.5) -> np.ndarray:
    return closed([(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)])


def dendritic_ring(n_arms: int = 14, outer: float = 400.0, inner: float = 120.0) -> np.ndarray:
    """A star-shaped outline with narrow bays: the hard case for coarse sampling."""
    points = []
    steps = n_arms * 2
    for index in range(steps):
        radius = outer if index % 2 == 0 else inner
        angle = 2.0 * math.pi * index / steps
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return closed(np.asarray(points, dtype=float))


def notched_ring() -> np.ndarray:
    """A rectangle with a deep rectangular bay cut into one side."""
    return closed(
        [
            (-100.0, -60.0),
            (100.0, -60.0),
            (100.0, 60.0),
            (-100.0, 60.0),
            (-100.0, 20.0),
            (-20.0, 20.0),
            (-20.0, -20.0),
            (-100.0, -20.0),
        ]
    )


def geos_hausdorff(source: np.ndarray, sampled: np.ndarray) -> float:
    """GEOS Hausdorff between two *closed* curves, computed from their vertices.

    GEOS evaluates the discrete (vertex-based) distance, so this is a lower bound
    of the continuous curve distance; it is the reference my cloud estimate has to
    agree with whenever the extremum sits on a vertex, as it does for the polygon
    pairs used here.
    """
    return float(
        shapely.hausdorff_distance(shapely.LineString(source), shapely.LineString(sampled))
    )


# --- the resampler at the three studied sample counts -----------------------


@pytest.mark.parametrize("n_points", N_OPTIONS)
def test_resample_returns_exactly_n_finite_equidistant_points(n_points: int) -> None:
    source = circle_ring(250.0, 2048)
    resampled = arc_length_resample(open_ring(source), n_points)
    assert resampled.shape == (n_points, 2)
    assert np.all(np.isfinite(resampled))
    chords = chord_lengths(closed(resampled))
    assert np.max(np.abs(chords - chords.mean()) / chords.mean()) < 1e-9


@pytest.mark.parametrize("n_points", N_OPTIONS)
def test_resampled_points_lie_on_the_source_boundary(n_points: int) -> None:
    """Resampling never invents geometry: every sample sits on the source ring."""
    for source in (square_ring(), dendritic_ring(), notched_ring()):
        polygon = shapely.Polygon(source)
        resampled = arc_length_resample(open_ring(source), n_points)
        distance = [polygon.exterior.distance(shapely.Point(point)) for point in resampled]
        assert max(distance) < 1e-9


def test_closed_and_open_input_resample_identically() -> None:
    source = dendritic_ring()
    assert np.allclose(
        arc_length_resample(open_ring(source), 128), arc_length_resample(source, 128)
    )


def test_resampling_is_deterministic() -> None:
    source = dendritic_ring()
    first = arc_length_resample(open_ring(source), 256)
    second = arc_length_resample(open_ring(source), 256)
    assert np.array_equal(first, second)


@pytest.mark.parametrize("n_points", N_OPTIONS)
def test_build_contour_canonicalizes_winding_and_scale(n_points: int) -> None:
    for source in (circle_ring(90.0, 512), square_ring(), needle_ring(), dendritic_ring()):
        contour = build_contour(source, n_points)
        assert contour.points.shape == (n_points, 2)
        assert np.all(np.isfinite(contour.points))
        assert np.max(np.abs(contour.points.mean(axis=0))) < 1e-12
        rms = float(np.sqrt(np.mean(np.sum(contour.points**2, axis=1))))
        assert rms == pytest.approx(1.0, rel=1e-12)
        assert signed_area(contour.points) > 0.0  # CCW canonical
        assert contour.perimeter_m == pytest.approx(ring_perimeter(open_ring(source)), rel=1e-6)
        # only ``points`` is normalized; ``metric_points`` keeps the metre frame
        assert contour.rms_radius_m > 1.0
        assert np.allclose(contour.metric_points.mean(axis=0), contour.centroid_m, atol=1e-9)


@pytest.mark.parametrize("n_points", N_OPTIONS)
def test_extreme_shapes_are_never_silently_lost(n_points: int) -> None:
    """The shapes the study cares about must still produce a contour at any N."""
    for source in (needle_ring(), dendritic_ring(), circle_ring(0.5, 64)):
        contour = build_contour(source, n_points)
        assert contour.points.shape == (n_points, 2)
        assert abs(signed_area(contour.points)) > 0.0
        assert np.all(np.isfinite(contour.points))


def test_degenerate_rings_raise_instead_of_returning_noise() -> None:
    line = closed([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)])
    with pytest.raises(ContourError, match="no area"):
        build_contour(line, 128)
    with pytest.raises(ContourError, match="at least 3"):
        build_contour(np.array([[0.0, 0.0], [1.0, 1.0]]), 16)
    with pytest.raises(ContourError, match="zero perimeter"):
        arc_length_resample(np.array([[2.0, 3.0]] * 4), 16)


# --- fidelity: identical curves --------------------------------------------


def test_identical_rings_are_perfect() -> None:
    source = circle_ring(120.0, 512)
    metrics = contour_fidelity(source, source)
    assert metrics.region_iou == pytest.approx(1.0, abs=1e-12)
    assert metrics.hausdorff_m == pytest.approx(0.0, abs=1e-9)
    assert metrics.chamfer_m == pytest.approx(0.0, abs=1e-9)
    assert metrics.area_rel_error == pytest.approx(0.0, abs=1e-12)
    assert metrics.perimeter_retention == pytest.approx(1.0, rel=1e-12)
    assert metrics.self_intersected is False
    assert metrics.source_ring_valid is True


def test_fidelity_of_a_square_at_its_own_corners_is_exact() -> None:
    source = square_ring()
    metrics = contour_fidelity(source, source)
    assert metrics.step_m == pytest.approx(100.0, rel=1e-12)
    assert metrics.max_shortcut_ratio == pytest.approx(0.0, abs=1e-12)
    assert metrics.chord_dispersion == pytest.approx(0.0, abs=1e-12)


# --- fidelity: analytic values ---------------------------------------------


def test_inscribed_polygon_matches_the_analytic_error() -> None:
    """A 16-gon inscribed in a circle has closed-form area, perimeter and deviation."""
    radius = 500.0
    source = circle_ring(radius, 8192)
    sampled = closed(arc_length_resample(open_ring(source), 16))
    metrics = contour_fidelity(source, sampled, densify_divisor=64)

    n = 16
    expected_area_ratio = n * math.sin(2.0 * math.pi / n) / (2.0 * math.pi)
    expected_perimeter_ratio = n * math.sin(math.pi / n) / math.pi
    expected_deviation = radius * (1.0 - math.cos(math.pi / n))

    assert metrics.area_rel_error == pytest.approx(expected_area_ratio - 1.0, abs=1e-4)
    assert metrics.perimeter_retention == pytest.approx(expected_perimeter_ratio, abs=1e-4)
    # inscribed, so the intersection is the polygon itself
    assert metrics.region_iou == pytest.approx(expected_area_ratio, abs=2e-3)
    # a chord is shorter than the arc it replaces by exactly 1 - sin(pi/n)/(pi/n)
    assert metrics.max_shortcut_ratio == pytest.approx(1.0 - expected_perimeter_ratio, abs=1e-4)
    assert metrics.chord_dispersion == pytest.approx(0.0, abs=1e-9)
    # the curve distance is a cloud approximation; its error scale is the cloud step
    assert metrics.hausdorff_m == pytest.approx(expected_deviation, abs=metrics.step_m / 64)


def test_error_shrinks_as_sampling_gets_finer() -> None:
    source = dendritic_ring()
    coarse = contour_fidelity(source, closed(arc_length_resample(open_ring(source), 64)))
    medium = contour_fidelity(source, closed(arc_length_resample(open_ring(source), 128)))
    fine = contour_fidelity(source, closed(arc_length_resample(open_ring(source), 256)))
    assert coarse.hausdorff_m > medium.hausdorff_m > fine.hausdorff_m > 0.0
    assert coarse.chamfer_m > medium.chamfer_m > fine.chamfer_m > 0.0
    assert coarse.region_iou < medium.region_iou < fine.region_iou
    assert coarse.perimeter_retention < medium.perimeter_retention < fine.perimeter_retention
    # a chord that spans a convex vertex loses area, one that spans a concave
    # vertex fills the bay and gains area; this star is notch-dominated, so its
    # signed area error is positive and shrinks towards zero as N grows
    assert coarse.area_rel_error > medium.area_rel_error > fine.area_rel_error > 0.0


def test_arguments_are_not_interchangeable_because_the_source_defines_truth() -> None:
    """The cloud step is ``source perimeter / sample count``, so order matters.

    The maximum (Hausdorff) is a property of the curve pair and survives a swap;
    the mean (Chamfer) is evaluated on differently densified clouds and does not.
    Every study row is therefore recorded as ``(source, sampled)`` and the signed
    area error is read as "reconstruction minus truth".
    """
    source = dendritic_ring()
    sampled = closed(arc_length_resample(open_ring(source), 64))
    forward = contour_fidelity(source, sampled)
    backward = contour_fidelity(sampled, source)
    assert forward.hausdorff_m == pytest.approx(backward.hausdorff_m, rel=1e-6)
    assert forward.chamfer_m != pytest.approx(backward.chamfer_m, rel=1e-2)
    assert forward.region_iou == pytest.approx(backward.region_iou, abs=1e-12)
    # the signed area error divides by whichever ring is the truth, so a swap
    # flips its sign and changes its scale by the area ratio
    assert forward.area_rel_error > 0.0 > backward.area_rel_error


def test_normalized_metrics_are_scale_invariant() -> None:
    source = circle_ring(100.0, 4096)
    sampled = closed(arc_length_resample(open_ring(source), 24))
    base = contour_fidelity(source, sampled)
    scaled = contour_fidelity(source * 7.0, sampled * 7.0)
    assert scaled.equal_area_radius_m == pytest.approx(base.equal_area_radius_m * 7.0, rel=1e-9)
    assert scaled.hausdorff_norm == pytest.approx(base.hausdorff_norm, rel=1e-9)
    assert scaled.chamfer_norm == pytest.approx(base.chamfer_norm, rel=1e-9)
    assert scaled.region_iou == pytest.approx(base.region_iou, rel=1e-9)
    assert scaled.area_rel_error == pytest.approx(base.area_rel_error, rel=1e-9)
    assert scaled.hausdorff_m == pytest.approx(base.hausdorff_m * 7.0, rel=1e-9)


def test_hausdorff_is_symmetric_not_one_directional() -> None:
    """A small square inside a big one: the far source corner must be counted."""
    big = square_ring(100.0)
    small = closed([(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)])
    metrics = contour_fidelity(big, small)
    # corner to corner along the diagonal: (50 - 10) * sqrt(2), not the 40 that a
    # one-directional measure would stop at
    assert metrics.hausdorff_m == pytest.approx(40.0 * math.sqrt(2.0), abs=1e-6)
    assert metrics.region_iou == pytest.approx(400.0 / 10000.0, abs=1e-9)
    assert metrics.area_rel_error == pytest.approx(0.04 - 1.0, abs=1e-9)


def test_cloud_estimate_converges_as_the_cloud_is_refined() -> None:
    """The reported deviation is a cloud approximation with a bounded error.

    Both curves are densified at ``step / densify_divisor``, so the estimate can
    sit either side of the continuous distance by about one cloud step. Refining
    the cloud 8x must move it by no more than the two steps combined — otherwise
    the study would be reporting its own discretisation rather than geometry.
    """
    cases = [
        (circle_ring(300.0, 64), 32),
        (dendritic_ring(), 64),
        (notched_ring(), 8),
        (circle_ring(100.0, 4096), 24),
    ]
    for source, n_points in cases:
        sampled = closed(arc_length_resample(open_ring(source), n_points))
        study = contour_fidelity(source, sampled)
        refined = contour_fidelity(source, sampled, densify_divisor=64, cloud_cap=1_000_000)
        step = study.step_m / 8.0
        assert abs(study.hausdorff_m - refined.hausdorff_m) <= 1.125 * step + 1e-9
        assert abs(study.chamfer_m - refined.chamfer_m) <= 1.125 * step + 1e-9
        # the region terms never touch the clouds
        assert refined.region_iou == study.region_iou


def test_cloud_cap_bounds_added_points_and_widens_the_step() -> None:
    """Cost control: the cap limits the *added* points, it never deletes vertices."""
    source = circle_ring(300.0, 64)
    sampled = closed(arc_length_resample(open_ring(source), 32))
    capped = contour_fidelity(source, sampled, cloud_cap=20)
    assert capped.n_source_cloud <= source.shape[0] + 20
    assert capped.n_sampled_cloud <= sampled.shape[0] + 20
    # a coarser cloud degrades the estimate, but stays inside its own larger step
    refined = contour_fidelity(source, sampled, densify_divisor=64, cloud_cap=1_000_000)
    assert capped.hausdorff_m - refined.hausdorff_m <= capped.source_perimeter_m / 20.0 + 1e-9
    # the region terms never touch the clouds
    assert capped.region_iou == contour_fidelity(source, sampled).region_iou


def test_a_curve_must_be_closed_before_it_is_compared() -> None:
    """Real-data lesson: the stored contour is an *open* array.

    ``contour_fidelity`` closes both rings internally, so an open and a closed
    array measure identically. A direct curve comparison does not: leaving the
    closing chord out of the 64-point version of one real reservoir outline moved
    its Hausdorff from 520 m to 610 m, which is what the first validation run
    exposed before any conclusion could be drawn from it.
    """
    source = circle_ring(500.0, 4096)
    opened = arc_length_resample(open_ring(source), 5)
    sealed = closed(opened)
    for metric in ("hausdorff_m", "chamfer_m", "region_iou"):
        first = getattr(contour_fidelity(source, opened), metric)
        second = getattr(contour_fidelity(source, sealed), metric)
        assert first == pytest.approx(second, abs=1e-12), metric
    # five samples leave a 72-degree gap; the open curve is a different shape
    assert geos_hausdorff(source, opened) == pytest.approx(
        1000.0 * math.sin(math.pi / 10.0), rel=2e-3
    )
    assert geos_hausdorff(source, sealed) == pytest.approx(
        500.0 * (1.0 - math.cos(math.pi / 5.0)), rel=2e-3
    )


# --- self-intersection reporting -------------------------------------------


def test_self_intersecting_reconstruction_is_reported_not_repaired_away() -> None:
    bowtie = closed([(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)])
    metrics = contour_fidelity(square_ring(100.0), bowtie)
    assert metrics.self_intersected is True
    assert metrics.validity_reason is not None
    assert "Self-intersection" in metrics.validity_reason
    assert metrics.source_ring_valid is True
    polygon, reason = ring_polygon(bowtie)
    assert reason is not None
    assert polygon.area == pytest.approx(5000.0, rel=1e-9)  # two triangles, repaired


def test_self_intersecting_source_is_attributed_to_the_source() -> None:
    """A crossed chord must be blamed on the right ring, not silently on both."""
    bowtie = closed([(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)])
    metrics = contour_fidelity(bowtie, square_ring(100.0))
    assert metrics.source_ring_valid is False
    assert metrics.self_intersected is False  # the reconstruction itself is clean


def test_clean_ring_has_no_validity_complaint() -> None:
    polygon, reason = ring_polygon(square_ring())
    assert reason is None
    assert polygon.area == pytest.approx(10000.0, rel=1e-12)


def test_coarse_sampling_across_a_bay_stays_measurable() -> None:
    """A deep bay is where coarse chords misbehave; every metric stays defined."""
    source = notched_ring()
    previous_iou = 0.0
    for n_points in (4, 8, 16, 32, 64, 128):
        sampled = closed(arc_length_resample(open_ring(source), n_points))
        metrics = contour_fidelity(source, sampled)
        assert np.isfinite(metrics.hausdorff_m)
        assert np.isfinite(metrics.chamfer_m)
        assert 0.0 <= metrics.region_iou <= 1.0
        assert metrics.region_iou > previous_iou
        assert metrics.self_intersected is False  # a bay this wide survives N=4
        previous_iou = metrics.region_iou


# --- helpers ---------------------------------------------------------------


def test_chord_lengths_close_the_ring() -> None:
    chords = chord_lengths(square_ring(100.0))
    assert chords.shape == (4,)
    assert np.allclose(chords, 100.0)


def test_exterior_only_drops_holes_and_keeps_the_largest_part() -> None:
    outer = [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)]
    with_hole = shapely.Polygon(outer, [[(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0)]])
    assert exterior_only(with_hole).area == pytest.approx(10000.0, rel=1e-12)
    multi = shapely.union_all(
        [shapely.Polygon(outer), shapely.Polygon([(200.0, 0.0), (210.0, 0.0), (210.0, 10.0)])]
    )
    assert exterior_only(multi).area == pytest.approx(10000.0, rel=1e-12)


def test_region_iou_values() -> None:
    a = shapely.Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    b = shapely.Polygon([(5.0, 0.0), (15.0, 0.0), (15.0, 10.0), (5.0, 10.0)])
    disjoint = shapely.Polygon([(100.0, 0.0), (110.0, 0.0), (110.0, 10.0), (100.0, 10.0)])
    assert region_iou(a, a) == pytest.approx(1.0)
    assert region_iou(a, b) == pytest.approx(1.0 / 3.0)
    assert region_iou(a, disjoint) == pytest.approx(0.0)


def test_summarize_metrics_reports_distribution_not_just_a_mean() -> None:
    rows: list[dict[str, Any]] = [
        {"score": float(value), "text": "ignore me"} for value in range(1, 11)
    ]
    rows.append({"score": None, "text": "also ignored"})
    summary = summarize_metrics(rows)
    assert set(summary) == {"score"}
    stats = summary["score"]
    assert stats["min"] == 1.0
    assert stats["max"] == 10.0
    assert stats["mean"] == pytest.approx(5.5)
    assert stats["p50"] == pytest.approx(5.5, abs=0.6)
    assert stats["p90"] <= 10.0
    assert stats["p99"] <= 10.0
    assert summarize_metrics([]) == {}


def test_fidelity_rejects_illegal_input() -> None:
    source = square_ring()
    with pytest.raises(FidelityError, match="non-finite"):
        contour_fidelity(source, np.array([[0.0, 0.0], [1.0, np.nan], [2.0, 1.0]]))
    with pytest.raises(FidelityError, match="at least 3"):
        contour_fidelity(source, np.array([[0.0, 0.0], [1.0, 1.0]]))
    with pytest.raises(FidelityError, match="shape"):
        contour_fidelity(source, np.zeros((4, 3)))
    with pytest.raises(FidelityError, match="zero perimeter"):
        contour_fidelity(np.zeros((5, 2)), source)


def test_normalized_contour_survives_the_float32_index_round_trip() -> None:
    """The index stores float32 contours; reading them back must not shift shape."""
    contour = build_contour(dendritic_ring(), 128)
    round_tripped = np.asarray(contour.points, dtype=np.float32).astype(float)
    assert np.max(np.abs(round_tripped - contour.points)) < 1e-5
    assert contour_fidelity(contour.points, round_tripped).region_iou > 0.9999


# --- real data (skipped when the extract is absent) -------------------------


@pytest.fixture(scope="module")
def real_rings() -> list[tuple[str, np.ndarray]]:
    from earthglyph.data.prepare import build_from_source

    build = build_from_source(REAL_RAW, n_points=128)
    return [
        (
            candidate.candidate_id,
            np.asarray(exterior_ring(candidate.polygon_metric), dtype=float),
        )
        for candidate in list(build.candidates)[::40]  # deterministic 21-candidate subset
    ]


@requires_real_data
def test_real_candidates_are_measurable_at_every_studied_n(real_rings: list) -> None:
    """No real Wisconsin outline is lost or non-finite at 64 / 128 / 256.

    Resampling always restarts from the source ring; the released 128-point
    contours are never interpolated upwards.
    """
    assert len(real_rings) == 21
    for candidate_id, ring in real_rings:
        assert np.all(np.isfinite(ring)), candidate_id
        ious: list[float] = []
        for n_points in N_OPTIONS:
            contour = build_contour(ring, n_points)
            assert contour.points.shape == (n_points, 2)
            metrics = contour_fidelity(ring, contour.metric_points)
            assert np.isfinite(metrics.hausdorff_m), (candidate_id, n_points)
            assert np.isfinite(metrics.chamfer_m), (candidate_id, n_points)
            assert 0.0 < metrics.region_iou <= 1.0, (candidate_id, n_points)
            assert 0.0 < metrics.perimeter_retention <= 1.0, (candidate_id, n_points)
            assert metrics.source_ring_valid is True, f"{candidate_id}: released ring must be valid"
            ious.append(metrics.region_iou)
        assert ious[0] <= ious[1] <= ious[2], f"{candidate_id}: IoU regressed with N"
