"""Behavioural tests for E3, the shape-context baseline.

The shared contract is what these tests hold against: inputs are 128-point
standardized closed CCW rings (built here with the project's own standardizer, so a
test cannot drift away from what the gallery actually stores), smaller score means
more similar, ties break by library row order, and every method scores the full
library.

The interesting assertions are the ones that check a *claim in POLICY* rather than a
number, because the value of this module to the experiment is that its stated
invariances are true of the code.  Where a claim is only true approximately, the test
asserts the approximate form and says why.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthglyph.data.perceptual_gallery import circle, regular_polygon, standardize, star
from earthglyph.retrieval import perceptual_methods
from earthglyph.retrieval import shape_context_baseline as sc
from earthglyph.retrieval.alignment import mirror_points

N = 128


def rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(float(angle_deg))
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ rotation.T, dtype=float)


def asymmetric_blob() -> np.ndarray:
    """A closed outline with no reflective or rotational symmetry at all.

    Needed by the rotation tests: a symmetric shape has many equally good
    orientations, so it cannot show whether a reported rotation was *recovered*.
    Built as a raw polygon and then put through the real standardizer.
    """
    angle = np.linspace(0.0, 2.0 * np.pi, 512, endpoint=False)
    radius = 1.0 + 0.30 * np.cos(angle) + 0.18 * np.cos(2.0 * angle) + 0.12 * np.sin(3.0 * angle)
    return standardize(np.column_stack([radius * np.cos(angle), radius * np.sin(angle)]))


STAR = standardize(star(5, 0.42))
SOFT_STAR = standardize(star(5, 0.42, tip_round=0.25))
JITTERED_STAR = standardize(star(5, 0.42, arm_jitter=[0.06, -0.05, 0.04, -0.07, 0.03]))
CIRCLE = standardize(circle())
SQUARE = standardize(regular_polygon(4))
BLOB = asymmetric_blob()


# --- the contract itself -------------------------------------------------------


def test_distance_is_zero_only_for_identical_context_sets() -> None:
    """(a) The same contour against itself is exactly 0, not merely small."""
    for contour in (STAR, CIRCLE, BLOB, SQUARE):
        score, detail = sc.distance(contour, contour)
        assert score == pytest.approx(0.0, abs=1e-12)
        assert detail["matched_contexts"] == sc.CONTEXT_POINTS
        assert detail["orientation_mode"] == "sample"


def test_translated_and_rescaled_input_is_unaffected_before_standardizing() -> None:
    """(b) Relative positions + mean-pairwise-distance scaling make the descriptor
    translation- and scale-invariant on its own, so a non-standardized rigid copy of
    a query scores 0 against it rather than merely a small number."""
    moved = BLOB * 3.7 + np.array([12.5, -7.0])
    assert sc.distance(BLOB, moved)[0] == pytest.approx(0.0, abs=1e-12)
    # The same pair through the standardizer must give the same answer: one pipeline.
    assert sc.distance(standardize(moved), standardize(BLOB))[0] == pytest.approx(
        0.0, abs=1e-12
    )
    # And a scale change alone does not reorder a library.
    library = np.stack([CIRCLE, SOFT_STAR, SQUARE])
    plain = perceptual_methods.rank_order(sc.distance_library(BLOB, library))
    rescaled = perceptual_methods.rank_order(sc.distance_library(BLOB, library * 5.0 + 3.0))
    assert list(plain) == list(rescaled)


def test_score_library_matches_the_pairwise_distance_and_reports_its_policy() -> None:
    """The runner must not need a second code path: row i of score_library is
    distance(query, library[i])."""
    library = np.stack([CIRCLE, STAR, BLOB, SOFT_STAR])
    scores, meta = sc.score_library(BLOB, library)
    assert scores.shape == (4,)
    assert np.isfinite(scores).all()
    pairwise = [sc.distance(BLOB, row)[0] for row in library]
    np.testing.assert_allclose(scores, np.asarray(pairwise), rtol=1e-12, atol=1e-12)
    assert meta["policy"]["id"] == "E3"
    assert [detail["row"] for detail in meta["transforms"]] == [0, 1, 2, 3]
    assert meta["transforms"][0]["score"] == pytest.approx(scores[0])
    assert meta["elapsed_seconds"] >= 0.0
    assert meta["seconds_per_candidate"] >= 0.0


def test_dispatch_through_the_shared_contract_accepts_a_library_of_arrays() -> None:
    """E3 must be reachable exactly like E0: same names, same call, same validation."""
    assert perceptual_methods.METHOD_MODULES["E3"] == "earthglyph.retrieval.shape_context_baseline"
    for name in ("POLICY", "describe_policy", "distance", "distance_library", "score_library"):
        assert hasattr(sc, name), name
    library = [CIRCLE, STAR, BLOB]
    scores, meta = perceptual_methods.score_library("E3", STAR, library)
    assert scores.shape == (3,)
    assert scores[1] == pytest.approx(0.0, abs=1e-12)
    assert meta["policy"]["params"]["orientation"] == "sample"


def test_library_mixing_sample_counts_is_refused() -> None:
    """The contract forbids comparing rings sampled differently."""
    ragged = [CIRCLE, CIRCLE[:100]]
    with pytest.raises(perceptual_methods.PerceptualMethodError):
        sc.score_library(STAR, ragged)


# --- orientation ---------------------------------------------------------------


def test_grid_aligned_rotation_is_recovered_exactly() -> None:
    """(c) When the rotation sits on the search grid, E3 finds it and costs nothing:
    the two context sets coincide, so the identity matching has zero cost."""
    for angle in (0.0, 75.0, 90.0, 315.0):
        score, detail = sc.distance(BLOB, rotate(BLOB, angle))
        assert score == pytest.approx(0.0, abs=1e-12)
        assert detail["rotation_deg"] == pytest.approx(angle % 360.0, abs=1e-9)
        assert detail["candidate_rotation_deg"] == pytest.approx((-angle) % 360.0, abs=1e-9)
        assert detail["rotations_searched"] == sc.ROTATION_STEPS


def test_arbitrary_rotation_is_recovered_within_half_a_grid_step() -> None:
    """(c, continued) Between grid points the winner is the nearest step, and the
    residual cost is far below the cost of a different shape — the honest statement
    of "rotation invariant up to half a step"."""
    half_step = 360.0 / sc.ROTATION_STEPS / 2.0
    score, detail = sc.distance(BLOB, rotate(BLOB, 37.0))
    assert abs(detail["rotation_deg"] - 37.0) <= half_step + 1e-9
    assert detail["rotation_deg"] % (360.0 / sc.ROTATION_STEPS) == pytest.approx(0.0, abs=1e-9)
    assert score < 0.15  # measured 0.0845; the same blob against a circle is 0.3409
    assert score < sc.distance(BLOB, CIRCLE)[0]


def test_sample_mode_does_not_claim_more_than_it_delivers() -> None:
    """The default policy text must describe a search, not an invariance."""
    rotation = sc.describe_policy()["invariance"]["rotation"]
    assert "searched, not assumed" in rotation
    assert "half a grid step" in rotation
    canonical = sc.describe_policy(orientation="canonical")["invariance"]["rotation"]
    assert "principal axes" in canonical
    assert canonical != rotation


def test_canonical_mode_removes_an_arbitrary_rotation_of_an_asymmetric_shape() -> None:
    """The cheap mode, and where it is exact: the principal frame is identifiable
    here, so any rotation disappears for one assignment per candidate."""
    for angle in (3.0, 37.0, 211.0):
        score, detail = sc.distance(BLOB, rotate(BLOB, angle), orientation="canonical")
        assert score == pytest.approx(0.0, abs=1e-9)
        assert detail["rotations_searched"] == 1
        assert detail["rotation_deg"] == 0.0
        assert detail["candidate_axes_degenerate"] is False
        assert detail["candidate_sign_ambiguous"] is False


def test_canonical_frame_resolves_the_eigenvector_sign_ambiguity() -> None:
    """``eigh`` gives an arbitrary sign per axis, so the 180-degree flip of a shape
    would otherwise be silently a different descriptor.  The third-moment rule makes
    the canonical frame identical for a shape and its flipped copy."""
    reference, note = sc.canonical_frame(BLOB)
    assert note["anisotropy"] > sc.ANISOTROPY_TOLERANCE
    for angle in (37.0, 180.0, 180.0 + 37.0, 311.0):
        aligned, _ = sc.canonical_frame(rotate(BLOB, angle))
        np.testing.assert_allclose(aligned, reference, atol=1e-12)


def test_canonical_mode_reports_the_ambiguities_it_cannot_resolve() -> None:
    """A circle and a symmetric star have (near-)isotropic covariance, so no
    direction is the principal one; a circle's odd moments vanish too.  The code has
    to say so instead of returning a confident frame."""
    _, circle_note = sc.canonical_frame(CIRCLE)
    assert circle_note["axes_degenerate"] is True
    assert circle_note["sign_ambiguous"] is True
    _, star_note = sc.canonical_frame(STAR)
    assert star_note["axes_degenerate"] is True
    _, blob_note = sc.canonical_frame(BLOB)
    assert blob_note["axes_degenerate"] is False
    assert blob_note["sign_ambiguous"] is False
    # The same shape with the opposite handedness must not be forced onto a frame it
    # cannot identify: the mirrored blob is a different shape, and canonical mode
    # scores it as one.
    score, _ = sc.distance(BLOB, mirror_points(rotate(BLOB, 40.0)), orientation="canonical")
    assert score > 0.0


# --- discrimination (the point of the experiment) ------------------------------


def test_descriptor_distinguishes_corners_from_smooth_outline() -> None:
    """(d) A cornered perturbation of a star has to stay closer to the star than a
    circle is, which is the whole reason E3 is in the comparison: E0's mean boundary
    offset can be fooled by a smooth shape that sits well."""
    for perturbed, label in ((SOFT_STAR, "rounded tips"), (JITTERED_STAR, "uneven arms")):
        near = sc.distance(STAR, perturbed)[0]
        far = sc.distance(STAR, CIRCLE)[0]
        assert near < far, label
        assert near > 0.0, label
    # A different corner count is further away than the same one perturbed.
    assert sc.distance(STAR, SOFT_STAR)[0] < sc.distance(STAR, SQUARE)[0]


# --- guards -------------------------------------------------------------------


def test_descriptor_matches_the_paper_definition_on_a_square() -> None:
    """A golden check of the histogram itself, re-derived from the definition rather
    than from this module's helpers: for four corners the relative vectors, the mean
    pairwise distance over *distinct* pairs and the log-spaced radial bins are all
    exact, so a mismatch here would mean the descriptor is not the one POLICY
    describes (this is what pins the R = mean pairwise distance claim)."""
    corners = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    radial, angular = sc.RADIAL_BINS, sc.ANGULAR_BINS
    delta = corners[None, :, :] - corners[:, None, :]
    distances = np.sqrt((delta * delta).sum(axis=2))
    off_diagonal = ~np.eye(4, dtype=bool)
    mean_distance = float(distances[off_diagonal].sum() / off_diagonal.sum())
    edges = np.exp(
        np.linspace(
            np.log(mean_distance * sc.R_MIN_FACTOR),
            np.log(mean_distance * sc.R_MAX_FACTOR),
            radial + 1,
        )
    )
    expected_raw = np.zeros((4, radial * angular))
    for row in range(4):
        for column in range(4):
            if row == column:
                continue  # the paper excludes the point itself
            radius = distances[row, column]
            angle = float(np.arctan2(delta[row, column, 1], delta[row, column, 0]))
            r_bin = min(max(int(np.searchsorted(edges, radius, side="right") - 1), 0), radial - 1)
            wrapped = (angle + np.pi) * angular / (2.0 * np.pi)
            a_bin = min(max(int(np.floor(wrapped)), 0), angular - 1)
            expected_raw[row, r_bin * angular + a_bin] += 1.0
    assert expected_raw.sum() == 4.0 * 3.0  # every row describes three other points
    expected = expected_raw / expected_raw.sum(axis=1, keepdims=True)
    np.testing.assert_allclose(sc.shape_contexts(corners, context_points=4), expected)


def test_empty_histograms_cannot_produce_nan() -> None:
    """(e) The chi-square denominator is a 0/0 waiting to happen for a bin neither
    shape occupies; the epsilon guard added to the paper's formula has to keep the
    cost finite, including for an all-zero context stack."""
    zeros = np.zeros((6, sc.RADIAL_BINS * sc.ANGULAR_BINS))
    costs = sc.chi_square_costs(zeros, zeros)
    assert np.isfinite(costs).all()
    assert costs.max() == 0.0
    score, matched = sc.context_match_cost(zeros, zeros)
    assert score == 0.0 and matched == 6
    # Mixed empty and full stays finite too.
    full = sc.shape_contexts(STAR)
    mixed = sc.chi_square_costs(np.vstack([zeros, full[:2]]), full)
    assert np.isfinite(mixed).all()
    assert np.all(mixed[:6] >= 0.0)


def test_degenerate_point_set_is_refused_not_scored() -> None:
    """A contour with no extent has no mean pairwise distance to normalise by, so
    there is nothing to describe; returning a plausible number would be a lie."""
    with pytest.raises(sc.ShapeContextError):
        sc.shape_contexts(np.zeros((8, 2)))


def test_parameter_domain_is_enforced() -> None:
    for bad in (
        {"orientation": "rotate"},
        {"epsilon": 0.0},
        {"r_min_factor": 2.0, "r_max_factor": 1.0},
        {"radial_bins": 0},
        {"rotation_steps": -3},
    ):
        with pytest.raises(sc.ShapeContextError):
            sc.resolve_params(**bad)
    with pytest.raises(sc.ShapeContextError):
        sc.shape_contexts(STAR, context_points=N + 1)
    with pytest.raises(sc.ShapeContextError):
        sc.distance(STAR, CIRCLE[:100])


def test_unknown_keywords_are_recorded_instead_of_vanishing() -> None:
    """A runner passing another method's knobs must not have them silently change
    this one's policy — they are echoed as ignored_params."""
    grid = sc.resolve_params(radial_bin=4, orientation="canonical")
    assert grid.ignored == ("radial_bin",)
    assert grid.as_dict()["ignored_params"] == ["radial_bin"]
    assert grid.orientation == "canonical"


# --- mirror policy -------------------------------------------------------------


def test_mirror_flag_is_a_switch_not_an_assumption() -> None:
    """(f) With allow_mirror off a mirrored candidate is a different shape; with it
    on the mirrored pass wins and is reported, and the direct cost is kept."""
    mirrored = mirror_points(BLOB)
    off, detail_off = sc.distance(BLOB, mirrored, allow_mirror=False)
    on, detail_on = sc.distance(BLOB, mirrored, allow_mirror=True)
    assert off > 0.1
    assert detail_off["mirrored"] is False
    assert detail_off["mirror_cost"] is None
    assert on < off - 0.1
    assert detail_on["mirrored"] is True
    assert detail_on["mirror_cost"] == pytest.approx(on)
    assert detail_on["direct_cost"] == pytest.approx(off)
    # allow_mirror can never make a score worse, mirrored candidate or not.
    plain = sc.distance(BLOB, rotate(BLOB, 75.0), allow_mirror=False)[0]
    plain_mirror = sc.distance(BLOB, rotate(BLOB, 75.0), allow_mirror=True)[0]
    assert plain_mirror <= plain + 1e-12


def test_mirror_policy_reaches_the_full_library_path() -> None:
    mirrored_library = np.stack([CIRCLE, mirror_points(BLOB), STAR])
    scores_off, meta_off = sc.score_library(BLOB, mirrored_library, allow_mirror=False)
    scores_on, meta_on = sc.score_library(BLOB, mirrored_library, allow_mirror=True)
    # allow_mirror can only lower a score: it adds comparisons, it never removes one.
    assert np.all(scores_on <= scores_off + 1e-12)
    assert scores_on[1] < scores_off[1] - 0.1  # the mirror candidate, obviously
    assert meta_off["transforms"][1]["mirrored"] is False
    assert meta_on["transforms"][1]["mirrored"] is True
    # The unrelated rows barely move (measured <0.1%): the mirrored pass is a real
    # second comparison, not a re-scoring, so the switch cannot silently reorder the
    # whole gallery.
    assert abs(scores_on[0] - scores_off[0]) < 0.01 * scores_off[0]
    assert abs(scores_on[2] - scores_off[2]) < 0.01 * scores_off[2]


# --- symmetry and permutation --------------------------------------------------


def test_score_is_symmetric_within_a_stated_tolerance() -> None:
    """(g) Not exactly symmetric: the assignment part is (transposing the cost matrix
    cannot change the optimum), but the query-side rotation grid and the fact that the
    log-polar bin edges are fixed to the coordinate frame — not carried along with a
    rotation — both leave a residual.  Small enough to rank with, documented here
    rather than claimed away."""
    for first, second in ((STAR, CIRCLE), (BLOB, SQUARE), (BLOB, SOFT_STAR), (CIRCLE, SQUARE)):
        forward = sc.distance(first, second)[0]
        backward = sc.distance(second, first)[0]
        assert abs(forward - backward) <= 0.05
        assert abs(forward - backward) <= 0.05 * max(forward, backward)


def test_reordering_samples_leaves_the_score_unchanged() -> None:
    """The descriptor is a set of histograms matched by an assignment, so any
    permutation of a contour's samples — a reversed walk included — is invisible to it
    *when every sample participates*.  At the default 64-of-128 subsampling a reversed
    ring is sampled at the opposite phase, which is a genuinely different point set:
    the residual there is sampling phase, not direction sensitivity.  Both halves are
    asserted because POLICY says both."""
    for points in (STAR, BLOB, CIRCLE):
        reversed_score = sc.distance(points, points[::-1], context_points=N)[0]
        rolled_score = sc.distance(points, np.roll(points, 17, axis=0), context_points=N)[0]
        assert reversed_score == pytest.approx(0.0, abs=1e-12)
        assert rolled_score == pytest.approx(0.0, abs=1e-12)
    default_residual = sc.distance(STAR, STAR[::-1])[0]
    assert 0.0 < default_residual < sc.distance(STAR, CIRCLE)[0]


# --- full-library ranking ------------------------------------------------------


def test_planted_match_ranks_first_and_ties_break_by_row_order() -> None:
    """(h) The rule the comparison depends on: the rotated/scaled copy of the query
    wins, and equally-scored rows keep their library order so the table is
    reproducible."""
    twin_a = CIRCLE.copy()
    twin_b = CIRCLE.copy()
    # 45 deg is on the 15 deg search grid, so the planted copy has to score exactly 0.
    planted = rotate(BLOB, 45.0)
    library = np.stack([twin_a, SQUARE, planted, SOFT_STAR, twin_b, STAR])
    scores, meta = sc.score_library(BLOB, library, allow_mirror=True)
    order = perceptual_methods.rank_order(scores)
    assert order[0] == 2
    assert scores[2] == pytest.approx(0.0, abs=1e-9)
    # The two identical circles tie exactly; the stable sort must keep row 0 first.
    assert scores[0] == scores[4]
    assert list(order).index(0) < list(order).index(4)
    assert [detail["row"] for detail in meta["transforms"]] == list(range(6))


def test_canonical_mode_ranks_the_same_planted_match_first() -> None:
    library = np.stack([CIRCLE, STAR, SOFT_STAR, rotate(BLOB, 13.0), SQUARE])
    scores = sc.distance_library(BLOB, library, orientation="canonical")
    assert perceptual_methods.rank_order(scores)[0] == 3
    assert scores[3] == pytest.approx(0.0, abs=1e-9)


def test_every_candidate_is_scored_not_shortlisted() -> None:
    """The contract's no-coarse-window rule: no row may be skipped or pinned."""
    library = np.stack([STAR] * 3 + [CIRCLE] * 3)
    scores = sc.distance_library(STAR, library)
    assert scores.shape == (6,)
    assert np.all(scores[:3] == 0.0)
    assert np.all(scores[3:] > 0.0)


# --- POLICY truthfulness -------------------------------------------------------


def test_policy_defaults_match_the_code() -> None:
    """The numbers printed next to the experiment's results are this module's
    constants, not a description someone typed in once."""
    params = sc.describe_policy()["params"]
    assert params["radial_bins"] == sc.RADIAL_BINS == 5
    assert params["angular_bins"] == sc.ANGULAR_BINS == 15
    assert params["log_polar_bins"] == 75
    assert params["context_points"] == sc.CONTEXT_POINTS
    assert params["rotation_steps"] == sc.ROTATION_STEPS == 24
    assert params["rotation_step_deg"] == 15.0
    assert params["r_min_factor"] == 0.125  # the "[R/8, 2R]" in the prose
    assert params["r_max_factor"] == 2.0
    assert params["orientation"] == "sample"
    ring = standardize(circle(samples=2048))
    assert sc.shape_contexts(ring).shape == (sc.CONTEXT_POINTS, 75)
    assert np.allclose(sc.shape_contexts(ring).sum(axis=1), 1.0)


def test_policy_does_not_present_opencv_contrib_as_the_implementation() -> None:
    """The claim in POLICY is that the library extractor is unavailable here, so
    check it against the install rather than trusting the sentence."""
    text = sc.POLICY["implementation"]
    assert "cv2.createShapeContextDistanceExtractor" in text
    assert "OpenCV contrib is not used" in text
    cv2 = pytest.importorskip("cv2")
    assert not hasattr(cv2, "createShapeContextDistanceExtractor")


def test_policy_states_the_deviations_from_the_paper() -> None:
    """Anything this code does differently must be in the list, or the method id is
    a false claim."""
    deviations = " ".join(sc.POLICY["deviations_from_the_paper"])
    for required in ("curvature", "epsilon", "divided", "clipped", "5x15"):
        assert required in deviations
    assert sc.EPSILON > 0.0
    guardless = sc.shape_contexts(STAR)
    assert sc.chi_square_costs(guardless, guardless, epsilon=sc.EPSILON).shape == (64, 64)
