"""Tests for the retrieval stage: coarse filter, re-rank helpers, baselines (P0-4).

Synthetic contours are used as mathematical inputs only — nothing here claims a
retrieval result, and the in-memory ``tiny_index`` fixture below exists purely so
that ordering rules (source cap, audit trail, parameter validation) can be pinned
on a library built to contain the case. The integration against the real
839-candidate index lives in ``tests/test_retrieval_integration.py``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from earthglyph.features.fourier import FeatureError, harmonic_amplitudes
from earthglyph.features.geometry_stats import FEATURE_COLUMNS, batch_shape_metrics
from earthglyph.geometry import (
    DEFAULT_N_POINTS,
    arc_length_resample,
    ensure_ccw,
    normalize_contour,
    open_ring,
)
from earthglyph.geometry.sketch import SketchError, contour_from_sketch
from earthglyph.retrieval.alignment import align, mirror_points
from earthglyph.retrieval.chamfer import (
    ChamferError,
    chamfer_after_alignment,
    chamfer_distance,
    chamfer_library,
    densify,
)
from earthglyph.retrieval.coarse import (
    CoarseError,
    column_reference,
    group_columns,
    rank,
    shortlist_overlap,
)
from earthglyph.retrieval.hu_baseline import (
    METHODS as HU_METHODS,
)
from earthglyph.retrieval.hu_baseline import (
    BaselineError,
    check_method,
    check_source,
    hu_moments,
    match_shapes,
    rank_by_hu,
    raster_info,
    rasterize,
)
from earthglyph.retrieval.query import (
    QueryError,
    check_matches_index,
    feature_columns,
    prepare,
    vector_from_contour,
    vectors_for,
)
from earthglyph.retrieval.search import IndexHandle, SearchError, search_by_contour

N = DEFAULT_N_POINTS

GROUPS = {
    "fourier": {"offset": 0, "width": 3, "columns": ["fd_01", "fd_02", "fd_03"]},
    "geometry": {"offset": 3, "width": 2, "columns": ["compactness", "solidity"]},
}


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def arc(n: int = N) -> np.ndarray:
    """An asymmetric closed outline (no mirror or rotational symmetry)."""
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = 1.0 + 0.30 * np.cos(t) + 0.17 * np.sin(2 * t + 0.8) + 0.09 * np.cos(3 * t + 1.9)
    return normalize_contour(np.column_stack([radius * np.cos(t), 0.75 * radius * np.sin(t)]))


def rotate(points: np.ndarray, theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.column_stack(
        [points[:, 0] * c - points[:, 1] * s, points[:, 0] * s + points[:, 1] * c]
    )


def mirror(points: np.ndarray) -> np.ndarray:
    return points * np.array([-1.0, 1.0])


def blob(seed: int, n: int = N) -> np.ndarray:
    """A lumpy asymmetric outline: every harmonic 2..7 in both phases, seeded."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    k = np.arange(2, 8)[:, None]
    basis = np.concatenate([np.cos(k * t[None, :]), np.sin(k * t[None, :])])
    coefs = rng.uniform(0.05, 0.25, basis.shape[0])
    coefs = coefs * (0.6 / coefs.sum())  # keeps every radius positive
    radius = 1.0 + 0.2 * np.cos(t) + coefs @ basis
    return normalize_contour(np.column_stack([radius * np.cos(t), radius * np.sin(t)]))


# --- coarse filter ----------------------------------------------------------


def test_group_columns_reads_the_manifest_offsets() -> None:
    assert group_columns(GROUPS, "fourier", 5) == [0, 1, 2]
    assert group_columns(GROUPS, "geometry", 5) == [3, 4]
    assert group_columns(GROUPS, "all", 5) == [0, 1, 2, 3, 4]
    # a manifest from a different build that does not carry the group
    with pytest.raises(CoarseError, match="no feature group"):
        group_columns({"fourier": GROUPS["fourier"]}, "geometry", 5)
    with pytest.raises(CoarseError, match="outside"):
        group_columns({"fourier": {"offset": 0, "width": 9}}, "fourier", 5)
    with pytest.raises(CoarseError, match="group must be"):
        group_columns(GROUPS, "fourier16", 5)


def test_ranking_the_concatenation_is_not_the_same_as_ranking_a_group() -> None:
    """The ablation the brief demands: fusion is a choice with a different answer.

    Column 4 is a statistic with raw magnitudes in the thousands (an area-like
    quantity), so in the concatenated vector one term dwarfs the three harmonic
    amplitudes and the order flips: the candidate whose Fourier block is the
    *worst* match wins. ``scale="std"`` is the fix, and it is opt-in.
    """
    library = np.array(
        [
            [0.90, 0.10, 0.05, 0.5, 1000.0],
            [0.70, 0.30, 0.07, 0.5, 500.0],
            [0.20, 0.80, 0.30, 0.5, 0.0],
        ]
    )
    query = np.array([0.88, 0.12, 0.05, 0.5, 1.0])
    fourier = rank(query[:3], library[:, :3], group="fourier")
    fused = rank(query, library, group="all")
    assert fourier.indices.tolist() == [0, 1, 2]
    assert fused.indices.tolist() == [2, 1, 0]  # the magnitude column decides
    assert fused.distances[2] == pytest.approx(446.766382, rel=1e-6)
    std = rank(query, library, group="all", scale="std")
    assert std.indices.tolist() == [1, 0, 2]
    assert std.distances.tolist() == pytest.approx([0.674209, 1.095193, 1.762288], rel=1e-5)
    # the divisor is applied to the query too, which is the bug this test pins:
    # standardizing one side only would have kept the raw 1000-scale distance
    assert max(std.distances) < 2.0
    assert shortlist_overlap(fourier, fused) == 1.0  # same rows, but for the wrong reason


def test_rank_distances_are_per_feature_rms_and_shortlist_limits() -> None:
    library = np.array([[1.0, 2.0], [3.0, 4.0], [1.0, 4.0]])
    result = rank(np.array([1.0, 2.0]), library, group="all")
    assert result.distances[0] == pytest.approx(0.0)
    assert result.distances.tolist()[1] == pytest.approx(np.sqrt((0**2 + 2**2) / 2))
    assert result.indices.tolist()[0] == 0
    assert result.size == 3
    limited = rank(np.array([1.0, 2.0]), library, group="all", shortlist_size=2)
    assert limited.size == 2
    assert list(limited.distances) == sorted(limited.distances)


def test_rank_breaks_ties_by_row_index_and_is_repeatable() -> None:
    library = np.array([[1.0, 1.0], [2.0, 2.0], [1.0, 1.0], [2.0, 2.0]])
    first = rank(np.array([1.0, 1.0]), library, group="all")
    second = rank(np.array([1.0, 1.0]), library, group="all")
    assert first.indices.tolist() == second.indices.tolist() == [0, 2, 1, 3]


def test_std_scaling_stops_a_tiny_variance_column_from_dominating() -> None:
    library = np.array([[1.0, 0.0], [1.0, 1e-3], [1.0, 2e-3], [1.0, 3e-3]])
    unscaled = column_reference(library, [0, 1], "none")
    std = column_reference(library, [0, 1], "std")
    assert np.all(unscaled == 1.0)
    assert std[1] > 0.0 and std[0] == 1.0  # a constant column stays inert
    assert np.all(std > 0.0) and np.all(np.isfinite(std))
    near_zero = column_reference(np.ones((4, 2)), [0, 1], "std")
    assert np.all(near_zero == 1.0)


def test_rank_validates_its_inputs() -> None:
    library = np.zeros((3, 2))
    with pytest.raises(CoarseError, match="1-D"):
        rank(np.zeros((1, 2)), library)
    with pytest.raises(CoarseError, match="2-D"):
        rank(np.zeros(2), np.zeros((3, 2, 2)))
    with pytest.raises(CoarseError, match="empty"):
        rank(np.zeros(2), np.zeros((0, 2)))
    with pytest.raises(CoarseError, match="1 features, library has 2"):
        rank(np.zeros(1), library)
    with pytest.raises(CoarseError, match="finite"):
        rank(np.array([np.nan, 0.0]), library)
    with pytest.raises(CoarseError, match="finite"):
        rank(np.zeros(2), np.array([[0.0, np.inf], [1.0, 1.0], [2.0, 2.0]]))
    with pytest.raises(CoarseError, match="shortlist_size"):
        rank(np.zeros(2), library, shortlist_size=0)
    with pytest.raises(CoarseError, match="scale must be"):
        rank(np.zeros(2), library, scale="z")
    with pytest.raises(CoarseError, match="scale must be"):
        column_reference(library, [0], "rms")
    with pytest.raises(CoarseError, match="reference width"):
        rank(np.zeros(2), library, reference=np.ones(3))
    with pytest.raises(CoarseError, match="finite and positive"):
        rank(np.zeros(2), library, reference=np.array([1.0, 0.0]))


def test_std_scale_without_a_reference_uses_the_library_it_searches() -> None:
    # column 0 is constant across candidates, so it divides by 1 and stays inert;
    # dividing it by its own (zero) standard deviation would be a NaN
    library = np.array([[5.0, 0.0], [5.0, 2.0]])
    result = rank(np.array([5.0, 1.0]), library, group="all", scale="std")
    assert result.distances.tolist() == pytest.approx([np.sqrt(0.5), np.sqrt(0.5)])
    assert result.indices.tolist() == [0, 1]  # tie breaks by row, not by luck
    assert result.to_dict()["coarse_scale"] == "std"


def test_shortlist_overlap_is_a_set_measure() -> None:
    a = rank(np.array([0.0]), np.array([[0.0], [1.0], [2.0]]), group="all", shortlist_size=2)
    b = rank(np.array([2.0]), np.array([[0.0], [1.0], [2.0]]), group="all", shortlist_size=2)
    assert shortlist_overlap(a, a) == 1.0
    assert shortlist_overlap(a, b) == pytest.approx(1 / 3)


# --- chamfer ----------------------------------------------------------------


def test_densify_adds_points_without_moving_the_originals() -> None:
    contour = arc()
    dense = densify(contour, 4)
    assert dense.shape == (contour.shape[0] * 4, 2)
    # the original vertices survive (every 4th interpolated point)
    assert np.max(np.abs(dense[::4] - contour)) < 1e-12
    assert densify(contour, 1).shape == contour.shape
    with pytest.raises(ChamferError, match="densify factor"):
        densify(contour, 0)


def test_chamfer_is_symmetric_and_zero_only_for_the_same_curve() -> None:
    a, b = arc(), blob(3)
    assert chamfer_distance(a, b) == pytest.approx(chamfer_distance(b, a))
    assert chamfer_distance(a, a) < 1e-9
    assert chamfer_distance(a, b) > 0.01
    assert chamfer_distance(a, b, squared=True) > 0.0


def test_chamfer_measures_relative_pose_not_absolute_position() -> None:
    """A set distance is invariant only to a motion applied to *both* curves.

    The naive version of this test — "rotate one curve, expect zero" — is wrong,
    and the measured values are the correction: at unit RMS radius a 0.4 rad turn
    of one curve costs ``0.1348`` and a mirror costs ``0.2067``, while re-indexing
    the start of the same closed polyline costs exactly ``0.0``.
    """
    a = arc()
    theta = 0.4
    both = (rotate(a, theta), rotate(mirror(a), theta))  # same motion, both curves
    assert chamfer_distance(a, mirror(a)) == pytest.approx(
        chamfer_distance(both[0], both[1]), abs=1e-12
    )
    assert chamfer_distance(a, np.roll(a, 17, axis=0)) == 0.0  # start index is invisible
    assert chamfer_distance(a, a[::-1]) < 1e-15  # traversal direction is invisible
    assert chamfer_distance(a, rotate(a, theta)) > 0.1  # one-curve rotation is seen
    assert chamfer_distance(a, mirror(a)) > 0.1  # one-curve reflection is seen


def test_chamfer_sees_a_mirror_but_cannot_adjudicate_one() -> None:
    """Chamfer is not amplitude-blind, yet it still cannot carry the policy (D33).

    It charges a mirror and any other deformation with the same arithmetic and
    returns one number with no branch, so nothing in the result says whether the
    candidate was matched as-is or reflected — and for a near-mirror-symmetric
    outline even the penalty collapses.
    """
    a = arc()
    reflected = mirror(a)
    assert chamfer_distance(a, reflected) > 0.1
    # a shape that is itself mirror-symmetric scores its own mirror as identical
    t = np.linspace(0.0, 2.0 * np.pi, N, endpoint=False)
    radius = 1.0 + 0.3 * np.cos(2 * t)
    symmetric = normalize_contour(np.column_stack([radius * np.cos(t), radius * np.sin(t)]))
    assert chamfer_distance(symmetric, mirror(symmetric)) < 1e-3
    assert align(a, reflected).distance > 0.05  # the re-rank separates them *and* reports why


def test_chamfer_scales_linearly_with_the_shape() -> None:
    a = arc()
    assert chamfer_distance(a * 3.0, blob(3) * 3.0) == pytest.approx(
        3.0 * chamfer_distance(a, blob(3)), rel=1e-6
    )


def test_chamfer_tolerates_a_local_bump_that_the_alignment_penalises() -> None:
    a = arc()
    bumped = a.copy()
    bumped[30:40, 0] += 0.25
    bumpless = chamfer_distance(a, bumped)
    assert align(a, bumped).distance > bumpless
    assert bumpless < 0.05


def test_chamfer_after_alignment_is_read_at_the_pose_the_rerank_chose() -> None:
    """The cross-check scores the aligned pair, which is what the overlay draws.

    Two properties, both measured rather than assumed: the reported ``shift``
    cannot move a point (roll invariance, exact), while the reported ``theta``
    genuinely re-poses the candidate — and that is the reason it is applied at
    all, since it pulls a rotated candidate back towards the query's frame.
    """
    a, b = arc(), blob(5)
    turned = rotate(b, 0.9)
    result = align(a, turned)
    aligned = chamfer_after_alignment(a, turned, shift=result.shift, theta_rad=result.theta_rad)
    assert aligned < chamfer_distance(a, turned)  # the re-rank's pose is the better fit
    assert chamfer_after_alignment(a, b, shift=37, theta_rad=0.0) == pytest.approx(
        chamfer_distance(a, b), abs=1e-12
    )
    mirrored = chamfer_after_alignment(a, turned, shift=0, theta_rad=0.0, mirrored=True)
    # the module's mirror is the x-axis reflection with the CCW walk restored; the
    # test's own helper reflects in y, which is the same shape rotated 180 degrees
    assert mirrored == pytest.approx(chamfer_distance(a, mirror_points(turned)), abs=1e-12)
    assert mirrored != pytest.approx(aligned, abs=1e-6)  # the branch changes the number


def test_chamfer_library_selects_rows_and_validates_the_stack() -> None:
    library = np.stack([arc(), blob(3), blob(4)])
    whole = chamfer_library(arc(), library)
    subset = chamfer_library(arc(), library, indices=[2])
    assert whole.shape == (3,)
    assert whole[0] == pytest.approx(0.0, abs=1e-9)
    assert subset.tolist() == [whole[2]]
    with pytest.raises(ChamferError, match="library must be"):
        chamfer_library(arc(), library[0])
    with pytest.raises(ChamferError, match="non-finite"):
        chamfer_library(np.array([[0.0, np.nan], [1.0, 0.0], [0.0, 1.0]]), library)


# --- Hu baseline ------------------------------------------------------------


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_rasterize_uses_one_uniform_pixel_scale_and_stays_on_the_canvas() -> None:
    contour = arc()
    mask, info = rasterize(contour)
    assert mask.shape == (512, 512) and mask.dtype == np.uint8
    ys, xs = np.nonzero(mask)
    assert xs.min() > 0 and xs.max() < 511 and ys.min() > 0 and ys.max() < 511
    extents = contour.max(axis=0) - contour.min(axis=0)
    assert info["major_axis_px"] / info["minor_axis_px"] == pytest.approx(
        extents.max() / extents.min(), rel=1e-3
    )
    # the drawn pixels, not just the reported numbers, keep the aspect ratio:
    # per-axis stretching is what "same canvas for every shape" must not mean
    drawn = (xs.max() - xs.min()) / (ys.max() - ys.min())
    assert drawn == pytest.approx(extents[0] / extents[1], abs=0.02)


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_rasterize_flips_the_y_axis_between_geometry_and_pixels() -> None:
    """Geometry is y-up, image rows are y-down, so a high shape draws high.

    The circle is centered at ``(0, 3)`` in geometry coordinates, i.e. well above
    the origin that the canvas is centered on: with the flip its pixels land in
    the upper band (rows ``< 256``), without it they would land near row 436.
    """
    t = np.linspace(0.0, 2.0 * np.pi, N, endpoint=False)
    contour = np.column_stack([np.cos(t), 3.0 + np.sin(t)])
    mask, info = rasterize(contour)
    ys, _ = np.nonzero(mask)
    expected_row = 256.0 - info["pixel_scale"] * 3.0
    assert ys.mean() == pytest.approx(expected_row, abs=2.0)
    assert ys.mean() < 200.0


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_hu_baseline_is_invariant_to_rotation_scale_and_mirror() -> None:
    """Why Hu moments are a *baseline*, not the product's matcher (D33)."""
    contour = arc()
    for name, other in {
        "rotated": rotate(contour, 1.2),
        "scaled": normalize_contour(contour * 6.0),
        "mirrored": mirror(contour),
        "shifted": normalize_contour(contour + np.array([3.0, -2.0])),
    }.items():
        assert match_shapes(contour, other) < 1e-3, name
    assert match_shapes(contour, blob(11)) > 1e-3


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_hu_baseline_is_not_invariant_to_a_per_axis_stretch() -> None:
    """The rasterization contract has teeth: stretching axes is a different shape."""
    contour = arc()
    stretched = contour * np.array([1.0, 0.5])
    assert match_shapes(contour, normalize_contour(stretched)) > 1e-3


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_rank_by_hu_orders_the_library_and_records_cost() -> None:
    library = np.stack([arc(), blob(3), blob(4), blob(5)])
    ranking = rank_by_hu(arc(), library)
    assert ranking.order[0] == 0
    assert ranking.distances[ranking.order[0]] < 1e-6
    assert ranking.seconds > 0.0
    assert ranking.seconds_per_candidate == pytest.approx(ranking.seconds / 4, rel=1e-6)
    payload = ranking.to_dict(2)
    assert payload["hu_method"] == "I1" and payload["hu_source"] == "mask"
    assert payload["hu_canvas"] == 512 and len(payload["hu_top_indices"]) == 2
    assert ranking.top(1) == [(0, ranking.distances[0])]


@pytest.mark.skipif(not cv2_available(), reason="needs OpenCV (pip install .[cv])")
def test_contour_source_rasterizes_on_the_same_grid_as_the_mask() -> None:
    contour = arc()
    both = [
        match_shapes(contour, blob(3), source=source) for source in ("mask", "contour")
    ]
    assert all(value > 0.0 for value in both)
    assert not np.isclose(both[0], both[1])


def test_hu_helpers_validate_their_contract() -> None:
    with pytest.raises(BaselineError, match="method must be"):
        check_method("I9")
    with pytest.raises(BaselineError, match="source must be"):
        check_source("stroke")
    assert check_method("I3") == "I3" and check_source("mask") == "mask"
    assert HU_METHODS == ("I1", "I2", "I3")
    with pytest.raises(BaselineError, match="collapses"):
        raster_info(np.zeros((8, 2)))
    with pytest.raises(BaselineError, match="canvas must be"):
        raster_info(arc(), canvas=8)
    with pytest.raises(BaselineError, match="margin"):
        raster_info(arc(), margin=0.7)
    if cv2_available():
        with pytest.raises(BaselineError, match=r"N, 2"):
            rasterize(np.zeros((2, 2)))
        sliver = np.column_stack([np.linspace(-1.0, 1.0, N), np.zeros(N)])
        with pytest.raises(BaselineError, match="sliver"):
            hu_moments(sliver)
        with pytest.raises(BaselineError, match="sliver"):
            rasterize(sliver)
        assert raster_info(sliver)["minor_axis_px"] == 0.0  # reported before refusing


# --- query preparation ------------------------------------------------------


def trace(n: int = 400) -> np.ndarray:
    """A dense, unevenly meaningful stroke: the point count is not 128 on purpose."""
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = 1.0 + 0.30 * np.cos(t) + 0.17 * np.sin(2 * t + 0.8)
    return np.column_stack([radius * np.cos(t), 0.75 * radius * np.sin(t)])


def test_query_features_are_the_index_columns_in_index_order() -> None:
    columns = feature_columns(16, "positive")
    vector = prepare(trace())
    assert vector.columns == columns
    assert columns[:16] == [f"fd_{i:02d}" for i in range(1, 17)]
    assert columns[16:] == list(FEATURE_COLUMNS)
    assert vector.features.shape == (20,)
    assert np.array_equal(vector.features, np.concatenate([vector.fourier, vector.geometry]))
    payload = vector.to_dict()
    assert payload["query_columns"] == columns
    assert len(payload["query_features"]) == 20
    assert "query_contour" in payload


def test_vectors_for_is_the_same_descriptor_the_index_build_used() -> None:
    contour = contour_from_sketch(trace())
    fourier, geometry = vectors_for(contour.points, n_harmonics=16, fold="positive")
    assert np.array_equal(fourier, harmonic_amplitudes(contour.points, 16, fold="positive"))
    assert np.array_equal(geometry, np.asarray(batch_shape_metrics(contour.points[None, ...])[0]))
    vector = vector_from_contour(contour, n_harmonics=16, fold="positive")
    # the extracted contour is described as it stands — not re-traced
    assert np.array_equal(vector.fourier, fourier) and np.array_equal(vector.geometry, geometry)


def test_re_tracing_an_extracted_contour_is_a_real_perturbation() -> None:
    """Why ``vector_from_contour`` exists: the arc-length grid moves when re-sampled.

    Measured, not guessed — ``3.1e-4`` with smoothing off, ``1.3e-3`` with the
    default light smoothing, both far larger than the float noise floor and both
    smaller than the library's own nearest-neighbour spacing, which is why the
    bug is worth preventing rather than tolerating.
    """
    contour = contour_from_sketch(trace())
    once = vector_from_contour(contour, n_harmonics=16, fold="positive")
    retraced_none = prepare(contour.points, smoothing="none")
    retraced_light = prepare(contour.points)
    assert np.max(np.abs(once.features - retraced_none.features)) == pytest.approx(
        3.117e-4, rel=1e-3
    )
    assert np.max(np.abs(once.features - retraced_light.features)) == pytest.approx(
        1.296e-3, rel=1e-3
    )
    assert np.max(np.abs(once.features - retraced_light.features)) > 1e-5


def test_query_vector_is_invariant_to_canvas_size_and_position() -> None:
    base = prepare(trace())
    moved = prepare(trace() * 7.0 + np.array([13.0, -4.0]))
    assert np.max(np.abs(base.features - moved.features)) < 1e-12
    assert np.max(np.abs(base.points - moved.points)) < 1e-12


def test_prepare_is_deterministic() -> None:
    first, second = prepare(trace()), prepare(trace())
    assert np.array_equal(first.features, second.features)
    assert first.contour.to_dict() == second.contour.to_dict()


def test_prepare_passes_the_extraction_refusals_through() -> None:
    with pytest.raises(SketchError, match="open"):
        prepare(np.column_stack([np.linspace(0.0, 1.0, 60), np.zeros(60)]))
    with pytest.raises(QueryError, match="N, 2"):
        vectors_for(np.zeros(8), n_harmonics=16, fold="positive")
    with pytest.raises(QueryError, match="non-finite"):
        vectors_for(
            np.array([[0.0, 0.0], [1.0, 0.0], [np.nan, 1.0]]), n_harmonics=1, fold="positive"
        )
    with pytest.raises(FeatureError, match="harmonic"):
        vectors_for(np.zeros((32, 2)), n_harmonics=16, fold="positive")


def test_check_matches_index_refuses_a_different_contract() -> None:
    vector = prepare(trace())
    check_matches_index(vector, feature_columns(16, "positive"))
    with pytest.raises(QueryError, match="does not match the index"):
        check_matches_index(vector, feature_columns(8, "positive"))
    with pytest.raises(QueryError, match="does not match the index"):
        check_matches_index(vector, feature_columns(16, "symmetric"))


# --- search interface: ordering, source cap, audit trail ---------------------


def tiny_index(
    entries: list[tuple[str, str, np.ndarray]],
    *,
    n_harmonics: int = 2,
    n_points: int = DEFAULT_N_POINTS,
) -> IndexHandle:
    """An in-memory index of hand-picked contours, so ranking rules can be pinned.

    The real 839-candidate index is exercised in ``test_retrieval_integration.py``;
    this handle exists because the source cap needs a library where two rows
    genuinely share a ``source_id`` *and* both rank inside ``top_k`` — a case the
    real library does not produce for any query (see that file). Entries are
    standardized through the library's own chain, because a uniform-in-angle ring
    is not arc-length uniform: fed to the matcher as-is it scores 0.186 against
    its own re-traced query where a real candidate scores 0.010.
    """
    shapes = np.stack(
        [
            normalize_contour(arc_length_resample(ensure_ccw(open_ring(entry[2])), n_points))
            for entry in entries
        ]
    )
    features = np.stack(
        [
            np.concatenate(vectors_for(shape, n_harmonics=n_harmonics, fold="positive"))
            for shape in shapes
        ]
    )
    columns = feature_columns(n_harmonics, "positive")
    return IndexHandle(
        directory=Path("<in-memory>"),
        features=features,
        shapes=shapes,
        metadata=pd.DataFrame(
            {
                "candidate_id": [entry[0] for entry in entries],
                "source_id": [entry[1] for entry in entries],
                "subtype": ["lake"] * len(entries),
                "class": ["lake"] * len(entries),
                "lon": [float(-89.0 - row) for row in range(len(entries))],
                "lat": [float(44.0 + row) for row in range(len(entries))],
                "area_m2": [1e5 * (row + 1) for row in range(len(entries))],
            }
        ),
        columns=columns,
        groups={
            "fourier": {
                "offset": 0,
                "width": n_harmonics,
                "columns": columns[:n_harmonics],
            },
            "geometry": {
                "offset": n_harmonics,
                "width": len(FEATURE_COLUMNS),
                "columns": list(FEATURE_COLUMNS),
            },
        },
        n_harmonics=n_harmonics,
        fold="positive",
        n_points=n_points,
        manifest={"source": {"source_name": "synthetic fixture"}},
    )


def sibling_pair() -> tuple[np.ndarray, np.ndarray]:
    """One contour and a near-duplicate: a second part of the same source."""
    query = blob(11)
    rng = np.random.default_rng(3)
    turned = normalize_contour(rotate(query, 0.3) + rng.normal(0.0, 0.01, query.shape))
    return query, turned


def test_source_cap_drops_the_repeat_and_refills_from_the_next_source() -> None:
    query, sibling = sibling_pair()
    index = tiny_index(
        [
            ("cand-0", "source-A", query),
            ("cand-1", "source-A", sibling),
            ("cand-2", "source-B", blob(12)),
            ("cand-3", "source-C", blob(13)),
            ("cand-4", "source-D", blob(14)),
        ]
    )
    capped = search_by_contour(query, index, top_k=3, max_per_source=1, smoothing="none")
    audit = [hit.row for hit in capped.pre_dedup]
    assert audit[:2] == [0, 1], "the fixture must put the two parts of one source adjacent"
    assert [hit.row for hit in capped.hits] == [row for row in audit if row != 1][:3]
    assert [hit.rank for hit in capped.hits] == [1, 2, 3]
    assert len({hit.source_id for hit in capped.hits}) == 3
    assert not any("share a source_id" in note for note in capped.warnings)
    # the dropped part is not deleted: it stays where it ranked, for the audit
    assert audit[0] == 0 and audit[1] == 1
    assert [hit.rank for hit in capped.pre_dedup] == [1, 2, 3, 4, 5]
    assert capped.pre_dedup[0].align_distance < capped.pre_dedup[1].align_distance


def test_letting_a_source_repeat_changes_the_answer_and_says_so() -> None:
    query, sibling = sibling_pair()
    index = tiny_index(
        [
            ("cand-0", "source-A", query),
            ("cand-1", "source-A", sibling),
            ("cand-2", "source-B", blob(12)),
            ("cand-3", "source-C", blob(13)),
        ]
    )
    loose = search_by_contour(query, index, top_k=3, max_per_source=2, smoothing="none")
    assert [hit.row for hit in loose.hits][:2] == [0, 1]
    assert loose.hits[2].row not in (0, 1)
    assert len({hit.source_id for hit in loose.hits}) == 2
    assert any("share a source_id" in note for note in loose.warnings)
    assert loose.params["max_per_source"] == 2


def test_the_search_interface_validates_its_parameters() -> None:
    index = tiny_index([("cand-0", "source-A", blob(15))])
    query = blob(15)
    with pytest.raises(SearchError, match="top_k"):
        search_by_contour(query, index, top_k=0)
    with pytest.raises(SearchError, match="max_per_source"):
        search_by_contour(query, index, max_per_source=0)
    with pytest.raises(SearchError, match="sort_by"):
        search_by_contour(query, index, sort_by="closest")
    with pytest.raises(SearchError, match="re-rank"):
        search_by_contour(query, index, method="fourier", chamfer_top=5)
    with pytest.raises(SearchError, match="method must be one of"):
        search_by_contour(query, index, method="deep_learning")


def test_hit_rows_and_metadata_are_read_from_the_same_index_row() -> None:
    """The geography a hit reports is the candidate's, not the query's (D12)."""
    query, _ = sibling_pair()
    index = tiny_index(
        [("cand-0", "source-A", blob(16)), ("cand-1", "source-B", blob(17))],
    )
    result = search_by_contour(query, index, top_k=2, smoothing="none")
    for hit in result.hits:
        record = index.metadata.iloc[hit.row]
        assert hit.candidate_id == str(record["candidate_id"])
        assert hit.source_id == str(record["source_id"])
        assert hit.lon == pytest.approx(float(record["lon"]))
        assert hit.lat == pytest.approx(float(record["lat"]))
        assert hit.area_m2 == pytest.approx(float(record["area_m2"]))
    assert [hit.rank for hit in result.pre_dedup] == list(range(1, len(result.pre_dedup) + 1))
