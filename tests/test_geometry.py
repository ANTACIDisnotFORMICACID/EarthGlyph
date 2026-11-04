"""Geometry preprocessing tests (P0-2).

Cover the normalization seed plus the extraction / projection / resampling
primitives. The synthetic shapes here are unit-test fixtures only and are never
written into ``data/processed`` (tests/test_polygon_preparation.py checks the
real dataset for contamination).

Note on invariance: preprocessing must be translation- and scale-invariant
only. Rotation alignment, cyclic-start matching and mirror matching belong to
the retrieval stage (D8/D9), so the tests below assert that a rotated or
mirrored input yields a correspondingly rotated/mirrored contour - not an
identical one.
"""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from earthglyph.geometry import (
    AeqdProjection,
    ContourError,
    arc_length_resample,
    build_contour,
    ensure_ccw,
    exterior_ring,
    hole_count,
    is_usable_polygon,
    normalize_contour,
    open_ring,
    polygonal_parts,
    repair_geometry,
    ring_perimeter,
    signed_area,
    split_feature,
    winding_of,
)

LON0, LAT0 = -89.5, 44.5


def _square(scale: float = 1.0, offset: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    ox, oy = offset
    return (
        np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [1.0, 1.0],
                [0.0, 1.0],
            ]
        )
        * scale
        + np.array([ox, oy])
    )


def _rms_radius(points: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(points**2, axis=1))))


def _rotate(
    points: np.ndarray, theta: float, center: tuple[float, float] = (0.0, 0.0)
) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    rot = np.array([[c, -s], [s, c]])
    return (points - np.asarray(center)) @ rot.T + np.asarray(center)


def _mirror_x(points: np.ndarray) -> np.ndarray:
    return points * np.array([-1.0, 1.0])


def _closed(ring: np.ndarray) -> np.ndarray:
    return np.vstack([ring, ring[:1]])


def _circle(
    n: int = 64, radius: float = 100.0, center: tuple[float, float] = (0.0, 0.0)
) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack(
        (center[0] + radius * np.cos(t), center[1] + radius * np.sin(t))
    )


def _l_shape() -> np.ndarray:
    """Asymmetric outline: distinguishes mirror from reversal."""
    return np.array(
        [
            [0.0, 0.0],
            [400.0, 0.0],
            [400.0, 100.0],
            [250.0, 100.0],
            [250.0, 300.0],
            [0.0, 300.0],
        ]
    )


def _best_cyclic_deviation(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest max-abs deviation between two contours over start shift + direction.

    Preprocessing does not align the cyclic start or the traversal direction, so
    shape comparisons in tests must search over both (this is *not* a rotation
    search - the retrieval stage owns that).
    """
    best = float("inf")
    for candidate in (a, a[::-1]):
        for shift in range(len(candidate)):
            best = min(best, float(np.abs(np.roll(candidate, shift, axis=0) - b).max()))
    return best


# --- normalize_contour (shared primitive) -----------------------------------


def test_output_is_centered_and_unit_rms() -> None:
    out = normalize_contour(_square())
    assert np.allclose(out.mean(axis=0), 0.0, atol=1e-9)
    assert _rms_radius(out) == pytest.approx(1.0, rel=1e-9)


def test_translation_invariance() -> None:
    base = normalize_contour(_square())
    shifted = normalize_contour(_square(offset=(123.0, -456.0)))
    assert np.allclose(base, shifted, atol=1e-9)


def test_scale_invariance() -> None:
    base = normalize_contour(_square())
    scaled = normalize_contour(_square(scale=17.5))
    assert np.allclose(base, scaled, atol=1e-9)


@pytest.mark.parametrize(
    "bad",
    [
        np.array([[0.0, 0.0], [1.0, 1.0]]),  # too few points
        np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 0.0, 0.0]]),  # not 2D
        np.array([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]]),  # degenerate (zero radius)
    ],
)
def test_invalid_inputs_raise(bad: np.ndarray) -> None:
    with pytest.raises(ValueError):
        normalize_contour(bad)


def test_shapely_runtime_available() -> None:
    """Smoke check that the shapely runtime (used by the geometry pipeline) imports."""
    poly = shapely.box(0, 0, 2, 1)
    assert shapely.is_valid(poly)
    assert poly.area == pytest.approx(2.0)


# --- projection -------------------------------------------------------------


def test_projection_roundtrip_and_metric_scale() -> None:
    proj = AeqdProjection(lon0=LON0, lat0=LAT0)
    ring = np.array([[LON0, LAT0], [LON0 + 0.01, LAT0], [LON0 + 0.01, LAT0 + 0.01]])
    meters = proj.to_meters(ring)
    assert np.allclose(meters[0], np.zeros(2), atol=1e-6)
    assert np.allclose(proj.to_lonlat(meters), ring, atol=1e-9)
    # 0.01 deg of latitude is ~1.11 km anywhere; degrees are not metres.
    assert 1000.0 < float(np.linalg.norm(meters[2] - meters[1])) < 1200.0


def test_projection_geometry_area_is_metric() -> None:
    proj = AeqdProjection.from_bounds((-90.0, 44.0, -89.0, 45.0))
    assert (proj.lon0, proj.lat0) == (LON0, LAT0)
    box = shapely.box(LON0, LAT0, LON0 + 0.01, LAT0 + 0.01)
    metric = proj.project_geometry(box)
    assert 8.0e5 < metric.area < 1.0e6  # ~1.11 km north-south x ~0.79 km east-west
    assert shapely.equals_exact(proj.unproject_geometry(metric), box, 1e-9)


def test_projection_rejects_non_planar_arrays() -> None:
    proj = AeqdProjection(lon0=LON0, lat0=LAT0)
    with pytest.raises(ValueError):
        proj.to_meters(np.zeros((4, 3)))
    with pytest.raises(ValueError):
        proj.to_lonlat(np.zeros((2,)))


@pytest.mark.parametrize("lon0,lat0", [(-200.0, 0.0), (0.0, 95.0)])
def test_projection_rejects_out_of_range_center(lon0: float, lat0: float) -> None:
    with pytest.raises(ValueError):
        AeqdProjection(lon0=lon0, lat0=lat0)


# --- extraction -------------------------------------------------------------


def test_valid_geometry_is_not_repaired() -> None:
    poly = shapely.box(0, 0, 1, 1)
    out, info = repair_geometry(poly)
    assert out is poly
    assert info.repaired is False and info.reason is None and info.result_type is None


def test_self_intersecting_polygon_is_repaired_and_typed() -> None:
    bowtie = shapely.Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert not shapely.is_valid(bowtie)
    fixed, info = repair_geometry(bowtie)
    assert info.repaired is True
    assert "Self-intersection" in (info.reason or "")
    assert info.result_type == "MultiPolygon"
    parts = polygonal_parts(fixed)
    assert len(parts) == 2
    assert all(shapely.is_valid(p) for p in parts)
    assert sum(p.area for p in parts) == pytest.approx(2.0)


def test_repair_keeps_only_polygonal_parts() -> None:
    """make_valid may return lines for a collapsed ring: no candidate survives."""
    line_like = shapely.Polygon([(0, 0), (1, 1), (2, 2)])
    fixed, info = repair_geometry(line_like)
    assert info.repaired is True and info.result_type == "MultiLineString"
    assert polygonal_parts(fixed) == []
    assert split_feature(line_like) == []


def test_multipolygon_is_split_with_part_indices() -> None:
    multi = shapely.MultiPolygon([shapely.box(0, 0, 1, 1), shapely.box(5, 5, 6, 6)])
    parts = split_feature(multi)
    assert [(p.part_index, p.n_parts) for p in parts] == [(0, 2), (1, 2)]
    assert all(is_usable_polygon(p.polygon) for p in parts)
    assert split_feature(shapely.box(0, 0, 1, 1))[0].n_parts == 1


def test_non_polygonal_and_empty_inputs_have_no_parts() -> None:
    assert split_feature(shapely.LineString([(0, 0), (1, 1)])) == []
    assert split_feature(shapely.Point(0, 0)) == []
    assert split_feature(None) == []
    assert split_feature(shapely.Polygon()) == []


def test_exterior_ring_and_holes() -> None:
    poly = shapely.Polygon(
        [(0, 0), (4, 0), (4, 4), (0, 4)],
        holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]],
    )
    ring = exterior_ring(poly)
    assert ring.shape == (5, 2)
    assert np.allclose(ring[0], ring[-1])
    assert hole_count(poly) == 1
    assert hole_count(shapely.box(0, 0, 1, 1)) == 0


# --- ring cleaning ----------------------------------------------------------


def test_open_ring_drops_duplicate_closing_point() -> None:
    out = open_ring(_closed(_square()))
    assert out.shape == (4, 2)
    assert not np.allclose(out[0], out[-1])
    assert np.allclose(out, _square())


def test_open_ring_drops_repeated_vertices_but_keeps_shape() -> None:
    ring = np.array(
        [
            [0.0, 0.0],
            [0.0, 0.0],
            [10.0, 0.0],
            [10.0, 0.0],
            [10.0, 10.0],
            [0.0, 10.0],
            [0.0, 0.0],
        ]
    )
    out = open_ring(ring)
    assert out.shape == (4, 2)
    assert ring_perimeter(out) == pytest.approx(40.0)


def test_open_ring_accepts_unclosed_input() -> None:
    assert np.allclose(open_ring(_square()), _square())


@pytest.mark.parametrize(
    "ring",
    [
        np.array([[0.0, 0.0], [1.0, 0.0]]),  # too short
        np.array([[0.0, 0.0]] * 4),  # no distinct points
        np.array([[0.0, 0.0], [1.0, 1.0], [1.0, 1.0]]),  # 2 distinct points
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]),  # not 2D
        np.array([[0.0, 0.0], [1.0, np.nan], [1.0, 1.0]]),  # non-finite
    ],
)
def test_open_ring_rejects_degenerate_sequences(ring: np.ndarray) -> None:
    with pytest.raises(ContourError):
        open_ring(ring)


def test_winding_is_normalized_to_ccw() -> None:
    cw = _square()[::-1]
    assert signed_area(cw) < 0
    assert winding_of(cw) == "cw"
    ccw = ensure_ccw(cw)
    assert signed_area(ccw) > 0
    assert np.allclose(ccw, _square())
    assert winding_of(_square()) == "ccw"


# --- arc-length resampling --------------------------------------------------


def test_resampling_is_uniform_in_arc_length_not_vertex_index() -> None:
    # Vertices clustered on one edge: index-based sampling would bunch points
    # there, arc-length sampling must not.
    ring = np.array(
        [
            [0.0, 0.0],
            [0.25, 0.0],
            [0.5, 0.0],
            [0.75, 0.0],
            [1.0, 0.0],
            [1.0, 1.0],
            [0.0, 1.0],
        ]
    )
    out = arc_length_resample(ring, 16)
    steps = np.linalg.norm(np.diff(out, axis=0), axis=1)
    expected = ring_perimeter(ring) / 16
    assert np.allclose(steps, expected, rtol=1e-9)


def test_resampling_output_count_and_spacing() -> None:
    out = arc_length_resample(_circle(37, radius=250.0), 128)
    assert out.shape == (128, 2)
    steps = np.linalg.norm(np.diff(_closed(out), axis=0), axis=1)[:-1]
    # Chords of a curved outline are slightly shorter than the arcs they span,
    # so spacing is uniform to within that sagitta (index-based sampling on the
    # 37 raw vertices would deviate by orders of magnitude more).
    assert np.allclose(steps, steps.mean(), rtol=1e-2)


def test_resampling_first_point_is_the_ring_start() -> None:
    ring = _l_shape()
    out = arc_length_resample(ring, 64)
    assert np.allclose(out[0], ring[0])


@pytest.mark.parametrize("n", [0, 1, 2])
def test_resampling_rejects_too_few_points(n: int) -> None:
    with pytest.raises(ContourError):
        arc_length_resample(_square(), n)


def test_resampling_rejects_zero_perimeter() -> None:
    with pytest.raises(ContourError):
        arc_length_resample(np.array([[0.0, 0.0]] * 3), 8)


# --- build_contour: the stored representation -------------------------------


def test_build_contour_default_is_128_points_unit_rms() -> None:
    contour = build_contour(_closed(_circle(50, radius=300.0)))
    assert contour.points.shape == (128, 2)
    assert _rms_radius(contour.points) == pytest.approx(1.0, rel=1e-12)
    assert np.allclose(contour.points.mean(axis=0), 0.0, atol=1e-12)
    assert contour.perimeter_m == pytest.approx(2 * np.pi * 300.0, rel=1e-3)
    # a 50-gon inscribed in the circle: samples sit between R*cos(pi/50) and R
    assert contour.rms_radius_m == pytest.approx(300.0, rel=1e-2)


def test_build_contour_preserves_shape_proportions() -> None:
    """No anisotropic stretch: a 3:1 rectangle stays 3:1 after normalization."""
    ring = np.array([[0.0, 0.0], [300.0, 0.0], [300.0, 100.0], [0.0, 100.0]])
    pts = build_contour(_closed(ring)).points
    width = pts[:, 0].max() - pts[:, 0].min()
    height = pts[:, 1].max() - pts[:, 1].min()
    assert width / height == pytest.approx(3.0, rel=1e-6)


def test_build_contour_translation_and_scale_invariance() -> None:
    base = build_contour(_closed(_circle(60, radius=100.0))).points
    moved = build_contour(
        _closed(_circle(60, radius=100.0, center=(4321.0, -987.0)))
    ).points
    scaled = build_contour(_closed(_circle(60, radius=100.0) * 7.25)).points
    assert np.allclose(base, moved, atol=1e-9)
    assert np.allclose(base, scaled, atol=1e-9)


def test_build_contour_rotates_with_the_input() -> None:
    """Preprocessing is not rotation-invariant: structure must be preserved."""
    ring = np.array(
        [[0.0, 0.0], [400.0, 0.0], [400.0, 120.0], [150.0, 260.0], [0.0, 120.0]]
    )
    theta = np.deg2rad(37.0)
    base = build_contour(_closed(ring))
    rotated = build_contour(_closed(_rotate(ring, theta, center=(1234.0, -56.0))))
    assert np.allclose(rotated.points, _rotate(base.points, theta), atol=1e-9)
    assert not np.allclose(rotated.points, base.points, atol=1e-6)
    # rotation preserves every pairwise distance, i.e. the geometric structure
    d0 = np.linalg.norm(base.points[:, None, :] - base.points[None, :, :], axis=2)
    d1 = np.linalg.norm(rotated.points[:, None, :] - rotated.points[None, :, :], axis=2)
    assert np.allclose(d0, d1, atol=1e-9)
    assert rotated.rms_radius_m == pytest.approx(base.rms_radius_m, rel=1e-9)


def test_build_contour_keeps_mirror_distinct() -> None:
    """A mirrored outline must not be mistaken for the original (D9)."""
    ring = _l_shape()
    base = build_contour(_closed(ring)).points
    mirrored = build_contour(_closed(_mirror_x(ring))).points
    # not the original outline under any start shift or traversal direction
    assert _best_cyclic_deviation(mirrored, base) > 1e-6
    assert not np.allclose(base[::-1], mirrored, atol=1e-9)
    # it *is* exactly the reflected outline: mirroring loses no shape information
    aligned = np.roll(_mirror_x(ring)[::-1], 1, axis=0)  # reflected ring, CCW from P0
    realigned = build_contour(_closed(aligned)).points
    assert np.allclose(realigned, _mirror_x(np.roll(base[::-1], 1, axis=0)), atol=1e-9)


def test_build_contour_normalizes_winding_direction() -> None:
    ring = _circle(40, radius=500.0)
    forward = build_contour(_closed(ring))
    backward = build_contour(_closed(ring[::-1]))
    assert forward.original_winding == "ccw"
    assert backward.original_winding == "cw"
    assert np.allclose(forward.points, backward.points, atol=1e-9)


def test_build_contour_does_not_align_the_cyclic_start() -> None:
    """Start-point alignment is a retrieval concern, so it must stay visible."""
    t = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False)
    ring = np.column_stack((500.0 * np.cos(t), 500.0 * np.sin(t)))  # arc-uniform
    base = build_contour(_closed(ring))
    assert np.allclose(base.metric_points, ring, atol=1e-9)  # resampling is exact here
    shifted = build_contour(_closed(np.roll(ring, 9, axis=0))).points
    assert np.allclose(shifted, np.roll(base.points, 9, axis=0), atol=1e-9)
    assert not np.allclose(shifted, base.points, atol=1e-6)
    assert _best_cyclic_deviation(shifted, base.points) < 1e-9


@pytest.mark.parametrize("n", [3, 16, 64, 128, 256])
def test_build_contour_point_counts(n: int) -> None:
    contour = build_contour(_closed(_square(scale=50.0)), n)
    assert contour.points.shape == (n, 2)
    assert contour.metric_points.shape == (n, 2)
    assert _rms_radius(contour.points) == pytest.approx(1.0, rel=1e-9)


@pytest.mark.parametrize(
    "ring",
    [
        np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [0.0, 0.0]]),  # collinear
        np.array([[5.0, 5.0]] * 4),  # single distinct point
        np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 0.0]]),  # zero area
    ],
)
def test_build_contour_rejects_degenerate_rings(ring: np.ndarray) -> None:
    with pytest.raises(ContourError):
        build_contour(ring)


# --- numerical stability ----------------------------------------------------


def test_build_contour_stable_for_huge_offsets() -> None:
    ring = _circle(48, radius=250.0) + np.array([4.0e7, -3.0e7])
    contour = build_contour(_closed(ring))
    reference = build_contour(_closed(_circle(48, radius=250.0)))
    assert np.all(np.isfinite(contour.points))
    assert np.allclose(contour.points, reference.points, atol=1e-6)
    assert contour.rms_radius_m == pytest.approx(reference.rms_radius_m, rel=1e-6)
    assert contour.perimeter_m == pytest.approx(reference.perimeter_m, rel=1e-6)


def test_build_contour_stable_for_tiny_shapes() -> None:
    contour = build_contour(_closed(_circle(48, radius=1.0e-6)))
    reference = build_contour(_closed(_circle(48, radius=1.0)))
    assert np.all(np.isfinite(contour.points))
    assert np.allclose(contour.points, reference.points, atol=1e-6)


def test_build_contour_accepts_float32_input() -> None:
    contour = build_contour(_closed(_circle(33, radius=80.0)).astype(np.float32))
    assert contour.points.dtype == np.float64
    assert _rms_radius(contour.points) == pytest.approx(1.0, rel=1e-9)


def test_build_contour_is_deterministic() -> None:
    ring = _closed(_circle(91, radius=1234.5))
    first = build_contour(ring)
    second = build_contour(ring.copy())
    assert np.array_equal(first.points, second.points)
    assert first.perimeter_m == second.perimeter_m
    assert first.rms_radius_m == second.rms_radius_m
