"""Tests for the rotation wrappers of the official port arms (P0-4.6 Checkpoint 7-C).

The wrapper arithmetic is testable without the official weights because the scorer is injected:
a recording scorer proves how many poses a strategy searches and in which order. Only the
equivalence claims that involve the real networks (R0 == CP6's rotation_search, and the
no-oracle-leakage check through ``search_shape``) need the gitignored toolbox and skip without it.

The properties under test are the ones the checkpoint stands on:

* the pose decision is a function of ONE contour only — never of the gallery, the target or a
  class label;
* degenerate axes and front-to-back symmetry are reported, not silently "fixed";
* the strategies differ in cost as advertised, and the cost is what the run reports;
* the added wrapper cannot be applied to an arm that does not have it.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthglyph.retrieval import shape_context_baseline as e3
from earthglyph.shape_benchmark import metrics
from earthglyph.shape_benchmark import rotation_methods as rm
from earthglyph.shape_benchmark import shapecomp_port as port
from earthglyph.shape_benchmark.datasets import BenchmarkShape, standardize
from earthglyph.shape_benchmark.probes import rotate
from earthglyph.shape_benchmark.retrieval import (
    KERNET_ROTATION_STEPS,
    Gallery,
    kernet_rotation_frames,
    pair_distance,
    search_shape,
)

HAS_TOOLBOX = (port.DEFAULT_TOOLBOX / "net" / "keras" / "shape2sc22a.h5").exists()


# ---------------------------------------------------------------- synthetic contours
def circle(n: int = 200) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([np.cos(theta), np.sin(theta)])


def egg(n: int = 240) -> np.ndarray:
    """One-lobe polar ring ``r = 1 + 0.3 cos(t)``.

    Deliberately kept as a degeneracy probe: for this family the point-set variances along x and
    y are equal by construction (the bulge adds exactly as much variance to one axis as the
    centring takes away), so the POINT-covariance rule of R2 calls it isotropic even though the
    outline is visibly not a circle. E1's AREA-moment frame does orient it, which is one of the
    reasons the two conventions are compared rather than merged.
    """
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = 1.0 + 0.3 * np.cos(theta)
    return np.column_stack([r * np.cos(theta), r * np.sin(theta)])


def elongated(n: int = 240) -> np.ndarray:
    """Anisotropic AND front-to-back asymmetric: the case R2 is meant to orient."""
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([2.0 * np.cos(theta) * (1.0 + 0.3 * np.cos(theta)), 0.7 * np.sin(theta)])


def crescent_arc(n: int = 240) -> np.ndarray:
    """Elongated, and symmetric under a half turn: an axis exists, but no third moment decides
    which end of it is the front, so the sign of the frame is left to floating-point noise."""
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([2.0 * np.cos(theta), 0.7 * np.sin(theta) + 0.35 * np.cos(theta)])


def star5(subdivisions: int = 12) -> np.ndarray:
    """A five-point star: 5-fold rotation symmetry, so its covariance is a circle."""
    vertices = [
        (1.0 if index % 2 == 0 else 0.4)
        * np.array([np.cos(2.0 * np.pi * index / 10.0), np.sin(2.0 * np.pi * index / 10.0)])
        for index in range(10)
    ]
    edges = []
    for first, second in zip(vertices, [*vertices[1:], vertices[0]], strict=True):
        t = np.linspace(0.0, 1.0, subdivisions, endpoint=False)[:, None]
        edges.append(first + t * (second - first))
    return np.vstack(edges)


def regular_polygon(sides: int = 6, per_edge: int = 20) -> np.ndarray:
    vertices = [
        np.array([np.cos(2.0 * np.pi * index / sides), np.sin(2.0 * np.pi * index / sides)])
        for index in range(sides)
    ]
    edges = []
    for first, second in zip(vertices, [*vertices[1:], vertices[0]], strict=True):
        t = np.linspace(0.0, 1.0, per_edge, endpoint=False)[:, None]
        edges.append(first + t * (second - first))
    return np.vstack(edges)


def ellipse(n: int = 240) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([2.0 * np.cos(theta), 0.6 * np.sin(theta)])


def parabola_arc(n: int = 241) -> np.ndarray:
    """Symmetric left-right (major-axis skew exactly zero) but not top-bottom.

    ``x`` is centred on zero exactly, so the third moment along the major axis cancels in pairs
    instead of leaving a discretisation residue that could outvote the intended decision. This is
    the shape the major axis cannot orient but the minor axis can.
    """
    x = (np.arange(n) - (n - 1) / 2.0) * (4.0 / float(n))
    return np.column_stack([x, 0.3 * x**2])


class RecordingScorer:
    """A fake 22-D encoder that remembers every contour it was asked to score."""

    def __init__(self) -> None:
        self.seen: list[np.ndarray] = []

    def __call__(self, contour: np.ndarray) -> np.ndarray:
        pts = np.asarray(contour, dtype=float)
        self.seen.append(pts)
        # Deterministic and rotation-sensitive: the first 22 ring coordinates.
        return np.resize(pts.ravel(), 22)

    def angles_of(self, reference: np.ndarray, step: float = 0.25) -> list[float]:
        """For each recorded pose, the turn on a ``step``-degree grid that reproduces it."""
        ref = np.asarray(reference, dtype=float)
        grid = np.arange(0.0, 360.0, step)
        turned = np.stack([rotate(ref, float(deg)) for deg in grid])
        out = []
        for pts in self.seen:
            score = np.sum((turned - pts) ** 2, axis=(1, 2))
            out.append(float(grid[int(np.argmin(score))]))
        return out


def _shapes() -> dict[str, np.ndarray]:
    return {
        "elongated": elongated(),
        "egg": egg(),
        "circle": circle(),
        "star": star5(),
        "ellipse": ellipse(),
        "parabola": parabola_arc(),
    }


def _gallery(name: str = "wrapper") -> Gallery:
    return Gallery(
        [
            BenchmarkShape(key, "synthetic", standardize(contour), contour)
            for key, contour in _shapes().items()
        ],
        name=name,
    )


# ---------------------------------------------------------------- strategy table
def test_strategy_table_matches_the_checkpoint_definitions() -> None:
    assert set(rm.ROTATION_STRATEGIES) == {"naive", "R0", "R1_36", "R1_72", "R2", "R3"}
    naive = rm.resolve_strategy("naive")
    assert (naive.steps, naive.canonical, naive.bilateral) == (0, False, False)
    assert naive.step_deg == 0.0
    r0 = rm.resolve_strategy("R0")
    assert r0.steps == KERNET_ROTATION_STEPS == e3.ROTATION_STEPS == 24
    assert r0.step_deg == pytest.approx(15.0)
    assert rm.resolve_strategy("R1_36").step_deg == pytest.approx(10.0)
    assert rm.resolve_strategy("R1_72").step_deg == pytest.approx(5.0)
    assert (rm.resolve_strategy("R2").steps, rm.resolve_strategy("R2").canonical) == (0, True)
    r3 = rm.resolve_strategy("R3")
    assert (r3.steps, r3.canonical, r3.bilateral) == (24, False, True)
    assert "canonicalisation" in rm.describe("R2")
    assert "grid" in rm.describe("R0")


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown rotation strategy"):
        rm.resolve_strategy("R4")


def test_pose_row_counts_are_the_advertised_costs() -> None:
    """Cost is part of the claim, so it is checked against the table the run reports."""
    expected = {
        "naive": (1, 1),
        "R0": (24, 1),
        "R1_36": (36, 1),
        "R1_72": (72, 1),
        "R2": (1, 1),
        "R3": (24, 24),
    }
    for name, (query_rows, gallery_rows) in expected.items():
        strategy = rm.resolve_strategy(name)
        assert rm.rows_for(strategy, "query") == query_rows, name
        assert rm.rows_for(strategy, "gallery") == gallery_rows, name


# ---------------------------------------------------------------- invalid input
@pytest.mark.parametrize(
    "bad",
    [
        np.zeros(10),
        np.zeros((10, 3)),
        np.zeros((2, 2)),
        np.array([[0.0, 0.0], [1.0, 1.0], [np.nan, 0.0]]),
        np.array([[0.0, 0.0], [1.0, np.inf], [2.0, 0.0]]),
    ],
    ids=["1d", "three-columns", "two-points", "nan", "inf"],
)
def test_contours_that_cannot_define_an_orientation_are_rejected(bad: np.ndarray) -> None:
    with pytest.raises(rm.RotationInputError):
        rm.canonical_frame(bad)
    with pytest.raises(rm.RotationInputError):
        rm.canonicalise(bad)
    with pytest.raises(rm.RotationInputError):
        rm.centroid(bad)
    with pytest.raises(rm.RotationInputError):
        rm.pose_embedding(RecordingScorer(), bad, "R0")


def test_a_collapsed_contour_is_rejected_instead_of_giving_a_zero_vector() -> None:
    collapsed = np.zeros((50, 2))
    with pytest.raises(rm.RotationInputError, match="zero variance"):
        rm.canonical_frame(collapsed)
    with pytest.raises(rm.RotationInputError):
        rm.pose_embedding(RecordingScorer(), collapsed, "R2")


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("strategy", ["naive", "R0", "R1_72", "R2", "R3"])
def test_the_official_chain_rejects_a_degenerate_contour_on_every_strategy(
    strategy: str,
) -> None:
    """No wrapper path can return a silent zero vector or NaN for a collapsed contour.

    R2 rejects it in the orientation rule (no axis can be computed), the grid strategies reject it
    in the port's own input guard. Both are explicit ``ValueError`` subclasses, and neither is
    allowed to fall through to a finite-looking answer.
    """
    scorer = port.ShapeCompNets().kernet1
    with pytest.raises(ValueError, match="degenerate|zero variance"):
        rm.pose_embedding(scorer, np.zeros((50, 2)), strategy, "query")



def test_pose_embedding_returns_finite_22d_for_every_strategy() -> None:
    scorer = RecordingScorer()
    for name in rm.ROTATION_STRATEGIES:
        rows = rm.pose_embedding(scorer, egg(), name, "query")
        assert rows.shape == (rm.rows_for(rm.resolve_strategy(name), "query"), 22), name
        assert np.isfinite(rows).all()


# ---------------------------------------------------------------- the axis convention
def test_star_regular_polygon_and_circle_are_isotropic_fallbacks() -> None:
    for contour, label in [
        (star5(), "five-point star"),
        (regular_polygon(6), "regular hexagon"),
        (regular_polygon(4), "square"),
        (circle(), "circle"),
        (egg(), "one-lobe ring (point-variances equal by construction)"),
    ]:
        frame = rm.canonical_frame(contour)
        assert frame.status == "isotropic-fallback", label
        assert frame.axis_ratio >= rm.ISOTROPY_MAX, label
        assert frame.angle_deg == 0.0, label
        assert not frame.canonicalised, label


def test_fallback_leaves_a_near_circle_in_its_stored_pose() -> None:
    """The documented consequence of the fallback: R2 is rotation-blind on a near-circle."""
    contour = circle()
    turned = rotate(contour, 37.0)
    assert np.array_equal(rm.canonicalise(contour)[0], contour)
    assert np.array_equal(rm.canonicalise(turned)[0], turned)


def test_front_to_back_symmetric_contour_reports_a_sign_tie() -> None:
    for contour, label in [(ellipse(), "ellipse"), (crescent_arc(), "arc symmetric in x")]:
        frame = rm.canonical_frame(contour)
        assert frame.status == "sign-tie", label
        assert abs(frame.sign_margin) < rm.SIGN_TIE_TOLERANCE, label
    assert rm.canonical_frame(ellipse()).axis_ratio == pytest.approx((0.6 / 2.0) ** 2, abs=1e-3)


def test_asymmetric_contour_is_decided_by_the_major_axis() -> None:
    frame = rm.canonical_frame(elongated())
    assert frame.status == "ok"
    assert frame.axis_ratio < 0.5
    assert abs(frame.sign_margin) >= rm.SIGN_TIE_TOLERANCE
    assert frame.canonicalised


def test_minor_axis_rule_fires_when_the_major_axis_cannot_decide() -> None:
    frame = rm.canonical_frame(parabola_arc())
    assert frame.status == "minor"
    assert abs(frame.sign_margin) >= rm.SIGN_TIE_TOLERANCE
    assert frame.canonicalised


def test_canonicalisation_is_rotation_covariant_and_deterministic() -> None:
    """The property the whole R2 arm rests on: turning first or canonicalising first agrees."""
    contour = elongated()
    reference, frame = rm.canonicalise(contour)
    assert frame.status == "ok"
    for deg in [0.0, 7.5, 15.0, 37.0, 90.0, 135.0, 180.0, 191.0, 260.5, 333.0]:
        turned, _ = rm.canonicalise(rotate(contour, deg))
        assert np.allclose(turned, reference, atol=1e-9), deg


def test_a_sign_tie_frame_is_not_rotation_consistent() -> None:
    """The honest limitation behind the ``sign-tie`` status, measured rather than asserted away.

    With no third moment to decide which end of the axis is the front, the flip is made by
    floating-point residue, so two poses of the same shape can canonicalise half a turn apart.
    This is why ``sign-tie`` is reported instead of quietly counted as a canonicalisation.
    """
    contour = crescent_arc()
    assert rm.canonical_frame(contour).status == "sign-tie"
    reference, _ = rm.canonicalise(contour)
    jumps = 0
    for deg in np.arange(0.0, 360.0, 15.0):
        turned, _ = rm.canonicalise(rotate(contour, float(deg)))
        if not np.allclose(turned, reference, atol=1e-6):
            jumps += 1
    assert jumps > 0


def test_repeated_canonicalisation_is_idempotent() -> None:
    once, frame = rm.canonicalise(rotate(elongated(), 120.0))
    assert frame.status == "ok"
    twice, again = rm.canonicalise(once)
    assert np.allclose(twice, once, atol=1e-9)
    assert again.angle_deg == pytest.approx(0.0, abs=1e-6)
    assert rm.canonical_frame(twice).angle_deg == pytest.approx(0.0, abs=1e-6)


def test_the_sign_rule_points_the_major_axis_at_the_bulk_of_the_shape() -> None:
    """Mirror the shape and the decided direction must invert by exactly a half turn."""
    forward = rm.canonical_frame(elongated())
    assert forward.status == "ok"
    assert abs(forward.sign_margin) > 0.05
    mirrored = rm.canonical_frame(np.column_stack([-elongated()[:, 0], elongated()[:, 1]]))
    assert mirrored.status == "ok"
    assert abs(mirrored.sign_margin) > 0.05
    difference = abs(forward.angle_deg - mirrored.angle_deg) % 360.0
    assert min(difference, 360.0 - difference) == pytest.approx(180.0, abs=1e-6)


def test_the_frame_decision_survives_a_start_point_move() -> None:
    """Same ring, different first sample: a start-point artefact must not change the pose.

    Rolling reorders the samples, so the comparison is on the point SET and on the decision, not
    on the array element order.
    """
    contour = elongated()
    reference, base = rm.canonicalise(contour)
    assert base.status == "ok"
    for shift in (1, 7, 53, 119):
        rolled = np.roll(contour, shift, axis=0)
        turned, frame = rm.canonicalise(rolled)
        assert frame.status == base.status, shift
        assert frame.angle_deg == pytest.approx(base.angle_deg, abs=1e-9), shift
        # Same point SET in a different order: compare by correspondence, not by element order.
        pairs = np.linalg.norm(turned[:, None, :] - reference[None, :, :], axis=2)
        gap = float(max(pairs.min(axis=1).max(), pairs.min(axis=0).max()))
        assert gap == pytest.approx(0.0, abs=1e-9), shift


# ---------------------------------------------------------------- pose arithmetic
def test_r0_grid_is_the_shared_rotation_grid_and_naive_is_the_identity() -> None:
    scorer = RecordingScorer()
    contour = egg()
    rm.pose_embedding(scorer, contour, "naive", "query")
    assert len(scorer.seen) == 1
    assert np.array_equal(scorer.seen[0], contour)

    scorer = RecordingScorer()
    rows = rm.pose_embedding(scorer, contour, "R0", "query")
    assert rows.shape == (24, 22)
    grid = e3.rotation_grid_deg(24)
    assert len(grid) == 24
    for angle, pts in zip(grid, scorer.seen, strict=True):
        assert np.allclose(pts, rotate(contour, float(angle)), atol=1e-12)

    scorer = RecordingScorer()
    rm.pose_embedding(scorer, contour, "R1_72", "query")
    assert len(scorer.seen) == 72
    assert np.allclose(scorer.seen[1], rotate(contour, 5.0), atol=1e-12)


def test_r2_searches_no_grid_and_canonicalises_in_contour_space() -> None:
    scorer = RecordingScorer()
    contour = rotate(elongated(), 137.0)
    assert rm.canonical_frame(contour).status == "ok"
    rows = rm.pose_embedding(scorer, contour, "R2", "query")
    assert rows.shape == (1, 22)
    assert len(scorer.seen) == 1
    assert np.allclose(scorer.seen[0], rm.canonicalise(contour)[0], atol=1e-12)
    # Not the stored pose: the wrapper really did turn the contour before scoring it.
    assert not np.allclose(scorer.seen[0], contour)
    # One contour, one decision: the role label cannot change the pose.
    scorer_gallery = RecordingScorer()
    rows_gallery = rm.pose_embedding(scorer_gallery, contour, "R2", "gallery")
    assert np.allclose(rows_gallery, rows, atol=1e-12)


def test_r3_adds_gallery_poses_without_changing_the_query_grid() -> None:
    """R3 differs from R0 only on the gallery side; the two query grids are the same 24 poses."""
    contour = rotate(egg(), 47.0)
    scorer_r0 = RecordingScorer()
    query_r0 = rm.pose_embedding(scorer_r0, contour, "R0", "query")
    scorer_r3 = RecordingScorer()
    query_r3 = rm.pose_embedding(scorer_r3, contour, "R3", "query")
    assert np.allclose(query_r3, query_r0, atol=1e-12)
    assert len(scorer_r3.seen) == 24

    gallery_rows = rm.pose_embedding(RecordingScorer(), contour, "R3", "gallery")
    assert gallery_rows.shape == (24, 22)
    stored = rm.pose_embedding(RecordingScorer(), contour, "R0", "gallery")
    assert stored.shape == (1, 22)
    # The R3 gallery stack contains the stored pose among its 24 poses.
    assert np.isclose(np.linalg.norm(gallery_rows - stored[0], axis=1).min(), 0.0, atol=1e-12)


def test_pose_pair_distance_is_the_min_over_pose_pairs() -> None:
    query = np.array([[0.0, 1.0] * 11, [2.0, 3.0] * 11, [4.0, 5.0] * 11])
    gallery = np.stack(
        [
            np.full(22, 9.0),
            np.array([2.0, 3.0] * 11),
            np.array([0.0, 7.0] * 11),
        ]
    )
    brute = min(
        float(np.linalg.norm(row - candidate))
        for row in query
        for candidate in gallery
    )
    assert rm.pose_pair_distance(query, gallery) == pytest.approx(brute)
    # A single row on either side is accepted, which is naive / R0-gallery / R2.
    assert rm.pose_pair_distance(query[0], gallery) == pytest.approx(
        float(np.linalg.norm(gallery - query[0], axis=1).min())
    )
    assert rm.pose_pair_distance(query, gallery[1]) == pytest.approx(0.0)


def test_gallery_distances_matches_brute_force_and_checks_its_input_shape() -> None:
    rng = np.random.default_rng(7)
    gallery = rng.normal(size=(5, 3, 22))
    query = rng.normal(size=(4, 22))
    expected = np.array(
        [
            min(
                float(np.linalg.norm(row - candidate))
                for row in query
                for candidate in gallery[index]
            )
            for index in range(len(gallery))
        ]
    )
    assert np.allclose(rm.gallery_distances(query, gallery), expected)
    single = rm.gallery_distances(query[:1], gallery)
    assert single.shape == (len(gallery),)
    assert np.allclose(
        single,
        [
            float(np.linalg.norm(gallery[index] - query[0], axis=1).min())
            for index in range(len(gallery))
        ],
    )
    with pytest.raises(ValueError, match=r"must be \(n, k, 22\)"):
        rm.gallery_distances(query, gallery[:, 0])
    with pytest.raises(ValueError, match="different coordinate dimensions"):
        rm.gallery_distances(rng.normal(size=(2, 12)), gallery)


def test_gallery_distances_is_independent_of_query_row_order() -> None:
    rng = np.random.default_rng(11)
    gallery = rng.normal(size=(6, 2, 22))
    query = rng.normal(size=(3, 22))
    assert np.allclose(
        rm.gallery_distances(query, gallery), rm.gallery_distances(query[::-1], gallery)
    )


# ---------------------------------------------------------------- metrics helpers (CP7-C)
def test_mean_reciprocal_rank_treats_an_absent_target_as_zero() -> None:
    assert metrics.mean_reciprocal_rank([1, 2, None]) == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert metrics.mean_reciprocal_rank([4]) == pytest.approx(0.25)
    with pytest.raises(ValueError, match="at least one query"):
        metrics.mean_reciprocal_rank([])


def test_ndcg_at_k_is_one_for_a_perfect_ranking_and_penalises_a_deep_relevant() -> None:
    assert metrics.ndcg_at_k(np.array([1, 1, 0, 0, 0]), 2) == pytest.approx(1.0)
    assert metrics.ndcg_at_k(np.array([0, 0, 1, 1, 0]), 4) < metrics.ndcg_at_k(
        np.array([1, 1, 0, 0, 0]), 4
    )
    assert metrics.ndcg_at_k(np.array([1, 0, 0, 0, 0]), 1) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="no relevant candidate"):
        metrics.ndcg_at_k(np.zeros(5, dtype=int), 3)
    with pytest.raises(ValueError, match="binary"):
        metrics.ndcg_at_k(np.array([1, 2, 0]), 2)


def test_rank_displacement_handles_gapped_gallery_indices() -> None:
    """Regression: a T1/T2 ranking drops a candidate, so its gallery indices have a gap.

    Reading positions off ``range(len(order))`` instead of off the member ids either crashes or,
    worse, compares the wrong candidates.
    """
    reference = np.array([7, 2, 349, 0])
    out = metrics.rank_displacement(reference, np.array([7, 2, 349, 0]))
    assert out["mean_abs_shift"] == 0.0
    assert out["max_abs_shift"] == 0.0
    assert out["n_compared"] == 4.0
    # Member 0 moves 3->0, member 2 moves 1->3, member 7 moves 0->1, member 349 stays at 2.
    drift = metrics.rank_displacement(reference, np.array([0, 7, 349, 2]))
    assert drift["mean_abs_shift"] == pytest.approx((3.0 + 2.0 + 1.0 + 0.0) / 4.0)
    assert drift["max_abs_shift"] == 3.0
    assert drift["spearman_between_rankings"] < 1.0
    # Reversing a gapped ranking moves every member and is a perfect monotone inversion.
    reverse = metrics.rank_displacement(reference, reference[::-1])
    assert reverse["mean_abs_shift"] == pytest.approx(2.0)
    assert reverse["max_abs_shift"] == 3.0
    assert reverse["spearman_between_rankings"] == pytest.approx(-1.0)


def test_rank_displacement_requires_the_same_candidates() -> None:
    with pytest.raises(ValueError, match="same candidates"):
        metrics.rank_displacement(np.array([0, 1, 2]), np.array([0, 1, 3]))
    with pytest.raises(ValueError, match="same candidates"):
        metrics.rank_displacement(np.array([0, 1, 2]), np.array([0, 1]))


# ---------------------------------------------------------------- arm plumbing
def test_rotation_strategy_is_refused_for_arms_that_do_not_have_a_wrapper() -> None:
    gallery = _gallery()
    shapes = list(gallery.shapes)
    for method in ("E0", "E1", "E3"):
        with pytest.raises(ValueError, match="rotation_strategy"):
            search_shape(shapes[0], gallery, method, rotation_strategy="R0")
        with pytest.raises(ValueError, match="rotation_strategy"):
            pair_distance(shapes[0], shapes[1], method, rotation_strategy="R0")


def test_orientation_is_refused_for_arms_that_do_not_own_a_policy() -> None:
    shapes = list(_gallery().shapes)
    with pytest.raises(ValueError, match="orientation is a policy of E1 and E3 only"):
        pair_distance(shapes[0], shapes[1], "E0", orientation="canonical")


# ---------------------------------------------------------------- real networks
@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("network", ["kernet1", "kernet2"])
def test_r0_wrapper_reproduces_the_cp6_rotation_search_exactly(network: str) -> None:
    """R0 through the new door must be the CP6 arithmetic, not a near-miss of it."""
    contour = rotate(egg(), 37.0)
    scorer = getattr(port.ShapeCompNets(), network)
    rows = rm.pose_embedding(scorer, contour, "R0", "query")
    frames = kernet_rotation_frames(network, contour, KERNET_ROTATION_STEPS)
    assert rows.shape == (24, 22)
    assert np.allclose(rows, frames, rtol=0.0, atol=1e-12)
    # naive is row zero of the same grid.
    single = rm.pose_embedding(scorer, contour, "naive", "query")
    assert np.allclose(single, frames[:1], rtol=0.0, atol=1e-12)


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_pair_distance_r0_equals_rotation_search(method: str) -> None:
    contours = _shapes()
    left = BenchmarkShape(
        "l", "synthetic", standardize(contours["elongated"]), rotate(contours["elongated"], 100.0)
    )
    right = BenchmarkShape("r", "synthetic", standardize(contours["circle"]), contours["circle"])
    wrapper = pair_distance(left, right, method, rotation_strategy="R0")
    legacy = pair_distance(left, right, method, rotation_search=True)
    assert wrapper == pytest.approx(legacy, abs=1e-12)
    # A grid min can never be further than the stored-pose distance.
    assert wrapper <= pair_distance(left, right, method) + 1e-12
    finer = pair_distance(left, right, method, rotation_strategy="R1_72")
    assert finer <= wrapper + 1e-12


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_search_wrapper_costs_match_rows_for_and_report_the_contract(method: str) -> None:
    gallery = _gallery("cost")
    contours = _shapes()
    query = BenchmarkShape(
        "q", "synthetic", standardize(contours["elongated"]), rotate(contours["elongated"], 47.0)
    )
    for name in ("naive", "R0", "R1_36", "R1_72", "R2", "R3"):
        strategy = rm.resolve_strategy(name)
        result = search_shape(query, gallery, method, top_k=len(gallery), rotation_strategy=name)
        assert result.details["kind"] == "official_network_with_rotation_wrapper", name
        assert result.details["rotation_strategy"] == name, name
        assert result.details["query_poses"] == rm.rows_for(strategy, "query"), name
        assert result.details["gallery_poses"] == rm.rows_for(strategy, "gallery"), name
        assert result.details["rotation_step_deg"] == strategy.step_deg, name
        assert result.details["forward_passes"] == rm.rows_for(
            strategy, "query"
        ) + rm.rows_for(strategy, "gallery"), name
        assert np.isfinite(result.distances).all(), name
        # Multi-direction poses belong to a shape; they never multiply the candidate objects.
        assert len(result.order) == len(gallery)
        assert len(np.unique(result.order)) == len(gallery)


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_pose_choice_never_consults_the_gallery(method: str) -> None:
    """No-oracle-leakage test: a shared candidate keeps the same distance in any gallery.

    If a wrapper picked a pose with the target (or the rest of the gallery) in mind, shrinking
    the gallery would change the distance to the shapes that stayed.
    """
    gallery = _gallery("wide")
    contours = _shapes()
    probe = rotate(contours["elongated"], 191.0)
    query = BenchmarkShape("probe", "synthetic", standardize(contours["elongated"]), probe)
    for name in ("naive", "R0", "R1_72", "R2", "R3"):
        wide = search_shape(query, gallery, method, top_k=len(gallery), rotation_strategy=name)
        small = Gallery(list(gallery.shapes[:2]) + [gallery.shapes[4]], name="narrow")
        narrow = search_shape(query, small, method, top_k=3, rotation_strategy=name)
        for shape in small.shapes:
            shared = int(gallery.index_of(shape.shape_id))
            assert float(wide.distances[shared]) == pytest.approx(
                float(narrow.distances[int(small.index_of(shape.shape_id))]), abs=1e-12
            ), f"{name}/{shape.shape_id}"


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_wrapper_is_deterministic_across_repeats(method: str) -> None:
    gallery = _gallery("repeat")
    contours = _shapes()
    query = BenchmarkShape(
        "q", "synthetic", standardize(contours["ellipse"]), rotate(contours["ellipse"], 260.5)
    )
    for name in ("R0", "R2", "R3"):
        first = search_shape(query, gallery, method, top_k=len(gallery), rotation_strategy=name)
        second = search_shape(query, gallery, method, top_k=len(gallery), rotation_strategy=name)
        assert np.array_equal(first.distances, second.distances), name
        assert np.array_equal(first.order, second.order), name


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
def test_gallery_pose_embeddings_are_cached_per_strategy() -> None:
    gallery = Gallery(
        [
            BenchmarkShape("egg", "synthetic", standardize(egg()), egg()),
            BenchmarkShape("star", "synthetic", standardize(star5()), star5()),
        ],
        name="cache",
    )
    for network in ("kernet1", "kernet2"):
        naive = gallery.pose_embeddings(network, "naive")
        assert naive.shape == (2, 1, 22)
        assert np.allclose(naive, gallery.kernet_coords(network)[:, None, :], atol=1e-12)
        assert np.allclose(gallery.pose_embeddings(network, "R0"), naive, atol=1e-12)
        r3 = gallery.pose_embeddings(network, "R3")
        assert r3.shape == (2, 24, 22)
        assert gallery.pose_embeddings(network, "R3") is r3
        # The gallery object count is unchanged by the extra poses.
        assert len(gallery) == 2
