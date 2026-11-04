"""Tests for the auxiliary geometry statistics (P0-3, Task B + Task G).

Two things are asserted for every feature: its closed-form value on shapes where
the answer is known analytically, and its invariance group. The negative result
is explicit — these statistics are invariant to mirroring, which is exactly why
they cannot separate a shape from its mirror image.

The rotated-rectangle tests also cover the reason the module computes the hull
itself instead of calling ``shapely.minimum_rotated_rectangle``: GEOS was
observed to collapse a valid 40-vertex hull to a zero-area sliver on the real
dataset (docs/DECISIONS.md, P0-3 sampling study).
"""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from earthglyph.features.fourier import FeatureError
from earthglyph.features.geometry_stats import (
    COLUMN_ASPECT_RATIO,
    COLUMN_COMPACTNESS,
    COLUMN_DIAMETER_FILL,
    COLUMN_SOLIDITY,
    FEATURE_COLUMNS,
    GEOMETRY_SCHEMA_VERSION,
    ShapeMetrics,
    batch_shape_metrics,
    convex_hull_area,
    convex_hull_points,
    geometry_feature_definition,
    max_point_distance,
    polygon_area,
    rectangle_aspect_ratio,
    rotated_rectangle_extents,
    shape_metrics,
)
from earthglyph.geometry import arc_length_resample, build_contour, normalize_contour, open_ring


def closed(points: list[tuple[float, float]]) -> np.ndarray:
    arr = np.asarray(points, dtype=float)
    return np.vstack([arr, arr[:1]])


def square(side: float = 10.0) -> np.ndarray:
    half = side / 2.0
    return closed([(-half, -half), (half, -half), (half, half), (-half, half)])


def rectangle(long: float, short: float) -> np.ndarray:
    return closed(
        [
            (-long / 2, -short / 2),
            (long / 2, -short / 2),
            (long / 2, short / 2),
            (-long / 2, short / 2),
        ]
    )


def disc(radius: float = 50.0, n: int = 4096) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return closed(np.column_stack([radius * np.cos(angle), radius * np.sin(angle)]))


def rotate(points: np.ndarray, theta: float) -> np.ndarray:
    cosine, sine = np.cos(theta), np.sin(theta)
    return points @ np.array([[cosine, -sine], [sine, cosine]]).T


# --- analytic values --------------------------------------------------------


def test_square_matches_the_closed_forms() -> None:
    metrics = shape_metrics(square(10.0))
    assert metrics.compactness == pytest.approx(np.pi / 4, rel=1e-9)
    assert metrics.solidity == pytest.approx(1.0, rel=1e-12)
    assert metrics.aspect_ratio == pytest.approx(1.0, rel=1e-9)
    assert metrics.diameter_fill == pytest.approx(2.0 / np.pi, rel=1e-9)


def test_rectangle_aspect_ratio_is_long_over_short() -> None:
    metrics = shape_metrics(rectangle(70.0, 10.0))
    assert metrics.aspect_ratio == pytest.approx(7.0, rel=1e-9)
    assert metrics.solidity == pytest.approx(1.0, rel=1e-12)
    # 4*pi*A/P^2 with A=700, P=160: an elongated rectangle is far from the disc.
    assert metrics.compactness == pytest.approx(4.0 * np.pi * 700.0 / 160.0**2, rel=1e-9)


def test_disc_is_almost_a_unit_disc_for_every_feature() -> None:
    metrics = shape_metrics(disc())
    assert metrics.compactness == pytest.approx(1.0, rel=1e-3)
    assert metrics.solidity == pytest.approx(1.0, rel=1e-6)
    assert metrics.aspect_ratio == pytest.approx(1.0, rel=1e-3)
    assert metrics.diameter_fill == pytest.approx(1.0, rel=1e-3)


def test_a_concave_shape_loses_solidity() -> None:
    crescent = closed(
        [
            (-50.0, 0.0),
            (0.0, 45.0),
            (50.0, 0.0),
            (0.0, 8.0),
        ]
    )
    metrics = shape_metrics(crescent)
    assert metrics.solidity < 0.9
    assert metrics.compactness < 1.0


def test_diameter_is_the_largest_point_distance() -> None:
    points = rectangle(80.0, 60.0)
    assert max_point_distance(points) == pytest.approx(np.hypot(80.0, 60.0), rel=1e-12)


def test_polygon_area_is_unsigned() -> None:
    assert polygon_area(square(10.0)) == pytest.approx(100.0, rel=1e-12)
    assert polygon_area(square(10.0)[::-1]) == pytest.approx(100.0, rel=1e-12)


# --- invariance group -------------------------------------------------------


@pytest.mark.parametrize(
    "transform",
    [
        pytest.param(lambda p: p + np.array([4000.0, -2500.0]), id="translation"),
        pytest.param(lambda p: p * 3.7, id="uniform-scale"),
        pytest.param(lambda p: rotate(p, 1.234), id="rotation"),
        pytest.param(lambda p: p * np.array([1.0, -1.0]), id="mirroring"),
        pytest.param(lambda p: p[::-1], id="direction-reversal"),
    ],
)
def test_all_four_statistics_share_the_invariance_group(transform) -> None:
    contour = normalize_contour(arc_length_resample(open_ring(disc(30.0)), 256))
    arrow = normalize_contour(
        arc_length_resample(
            open_ring(
                closed(
                    [
                        (0.0, 0.0),
                        (60.0, 8.0),
                        (58.0, 30.0),
                        (120.0, 40.0),
                        (60.0, 55.0),
                        (62.0, 80.0),
                        (0.0, 60.0),
                        (-8.0, 30.0),
                    ]
                )
            ),
            256,
        )
    )
    for shape in (contour, arrow):
        base = shape_metrics(shape).as_vector()
        moved = shape_metrics(transform(shape)).as_vector()
        # The invariance is exact in theory; a translation three orders of
        # magnitude larger than the shape costs float precision, so the
        # tolerance is relative and the residual is checked to be tiny.
        assert np.allclose(moved, base, rtol=1e-6, atol=1e-12)
        assert float(np.max(np.abs(moved - base))) < 1e-6


def test_statistics_cannot_separate_a_shape_from_its_mirror() -> None:
    """Documented limitation, not a bug: D9/D10 re-ranking has to handle mirrors."""
    contour = build_contour(disc(20.0), 128).points
    mirrored = contour * np.array([1.0, -1.0])
    assert np.allclose(
        shape_metrics(mirrored).as_vector(), shape_metrics(contour).as_vector(), atol=1e-12
    )


# --- basis: measured on the standardized contour ----------------------------


def test_basis_is_the_sampled_contour_not_the_raw_ring() -> None:
    """Same definition, different resolution: the gap is the sampling error.

    For a curved boundary the 128-point contour is a polygonal approximation,
    so the statistics differ slightly from the dense source ring — the
    consequence the module docstring records instead of hiding. The same is true
    of corners, which is measured rather than assumed.
    """
    dense = disc(40.0, n=4096)
    sampled = build_contour(dense, 128).points
    dense_metrics = shape_metrics(dense)
    sampled_metrics = shape_metrics(sampled)
    assert dense_metrics.compactness != sampled_metrics.compactness
    assert abs(dense_metrics.compactness - sampled_metrics.compactness) < 0.01
    assert abs(dense_metrics.solidity - sampled_metrics.solidity) < 0.01

    corners = closed(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 70.0), (40.0, 70.0), (40.0, 30.0), (0.0, 30.0)]
    )
    exact = shape_metrics(corners)
    resampled = shape_metrics(build_contour(corners, 128).points)
    # Even a polygon is not preserved: uniform arc-length spacing does not land
    # on the vertices, so each corner it steps over is cut. The residual is the
    # sampling error, measured here instead of assumed away.
    assert abs(resampled.compactness - exact.compactness) < 0.02
    assert resampled.compactness > exact.compactness
    assert abs(resampled.solidity - exact.solidity) < 0.02


def test_holes_do_not_enter_the_outline_metrics() -> None:
    """D21: the contour is exterior-ring only, so an island changes nothing."""
    outer = [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)]
    without_hole = shapely.Polygon(outer)
    with_hole = shapely.Polygon(outer, [[(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0)]])
    a = shape_metrics(np.asarray(without_hole.exterior.coords))
    b = shape_metrics(np.asarray(with_hole.exterior.coords))
    assert a.as_vector() == pytest.approx(b.as_vector())
    assert with_hole.area < without_hole.area


# --- degenerate input -------------------------------------------------------


def test_rejects_non_finite() -> None:
    points = square()
    points[2, 0] = np.inf
    with pytest.raises(FeatureError, match="non-finite"):
        shape_metrics(points)


def test_rejects_wrong_shape() -> None:
    with pytest.raises(FeatureError, match="shape"):
        shape_metrics(np.zeros((5, 3)))


def test_rejects_fewer_than_three_points() -> None:
    with pytest.raises(FeatureError, match="at least 3"):
        shape_metrics(np.array([[0.0, 0.0], [1.0, 1.0]]))


def test_rejects_zero_area_ring() -> None:
    with pytest.raises(FeatureError, match="zero area"):
        shape_metrics(closed([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]))


def test_rejects_collinear_hull() -> None:
    with pytest.raises(FeatureError, match="collinear|zero area"):
        convex_hull_area(np.column_stack([np.linspace(0.0, 5.0, 8), np.zeros(8)]))


def test_batch_matches_single_row_by_row() -> None:
    contours = np.stack(
        [
            normalize_contour(arc_length_resample(open_ring(disc(20.0)), 128)),
            normalize_contour(arc_length_resample(open_ring(rectangle(90.0, 12.0)), 128)),
        ]
    )
    batch = batch_shape_metrics(contours)
    assert batch.shape == (2, len(FEATURE_COLUMNS))
    for row, contour in enumerate(contours):
        assert np.allclose(batch[row], shape_metrics(contour).as_vector(), atol=0.0)


def test_batch_rejects_a_bad_stack() -> None:
    with pytest.raises(FeatureError, match="contours"):
        batch_shape_metrics(np.zeros((3, 4)))


# --- convex hull and minimum-area rectangle --------------------------------


def test_hull_ignores_interior_points() -> None:
    points = np.array(
        [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [5.0, 5.0], [4.0, 3.0]]
    )
    hull = convex_hull_points(points)
    assert len(hull) == 4
    assert convex_hull_area(points) == pytest.approx(100.0, rel=1e-12)


def test_hull_deduplicates_repeated_vertices() -> None:
    points = np.array([[0.0, 0.0], [0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 0.0]])
    assert convex_hull_area(points) == pytest.approx(6.0, rel=1e-12)


def test_min_area_rectangle_of_a_rotated_square() -> None:
    long_side, short_side = rotated_rectangle_extents(rotate(square(10.0), 0.6))
    assert long_side == pytest.approx(10.0, rel=1e-9)
    assert short_side == pytest.approx(10.0, rel=1e-9)


def test_min_area_rectangle_beats_or_matches_geos() -> None:
    """Own rotating-calipers result is the true minimum; GEOS can only tie it.

    The minimum-area comparison is one-sided on purpose: on the real dataset
    GEOS produced a degenerate sliver for a valid 40-vertex hull, so asserting
    equality would have failed the released pipeline. The invariant that must
    always hold is ``area(own) <= area(geos)``.
    """
    rng = np.random.default_rng(20261105)
    relative_gap = []
    for _ in range(60):
        points = rng.normal(scale=100.0, size=(int(rng.integers(4, 60)), 2))
        long_side, short_side = rotated_rectangle_extents(points)
        own_area = long_side * short_side
        hull = shapely.multipoints([shapely.Point(p) for p in points]).convex_hull
        coords = np.asarray(hull.minimum_rotated_rectangle.exterior.coords, dtype=float)
        geos_area = float(shapely.Polygon(coords).area)
        assert own_area <= geos_area * (1.0 + 1e-9) + 1e-9
        if geos_area > 1e-12:
            relative_gap.append(abs(own_area - geos_area) / geos_area)
    assert max(relative_gap) < 1e-6


def test_aspect_ratio_of_a_needle_is_finite_and_large() -> None:
    needle = closed([(0.0, 0.0), (1000.0, 0.0), (1000.0, 1.0), (0.0, 1.0)])
    assert rectangle_aspect_ratio(needle) == pytest.approx(1000.0, rel=1e-9)


# --- schema -----------------------------------------------------------------


def test_vector_order_is_the_recorded_column_order() -> None:
    metrics = ShapeMetrics(0.5, 0.6, 7.0, 0.3)
    assert list(metrics.as_dict()) == list(FEATURE_COLUMNS)
    assert metrics.as_vector().tolist() == [0.5, 0.6, 7.0, 0.3]
    assert FEATURE_COLUMNS == (
        COLUMN_COMPACTNESS,
        COLUMN_SOLIDITY,
        COLUMN_ASPECT_RATIO,
        COLUMN_DIAMETER_FILL,
    )


def test_definition_states_formulas_basis_and_limits() -> None:
    definition = geometry_feature_definition()
    assert definition["schema_version"] == GEOMETRY_SCHEMA_VERSION
    assert set(definition["columns"]) == set(FEATURE_COLUMNS)
    assert set(definition["formulas"]) == set(FEATURE_COLUMNS)
    assert "sampled contour" in definition["basis"]
    assert "mirroring" in definition["invariances"]
    assert "sampling resolution" in " ".join(definition["not_invariant_to"])
    assert "silently dropped" in definition["degenerate_policy"]
    assert any("holes" in note for note in definition["notes"])
