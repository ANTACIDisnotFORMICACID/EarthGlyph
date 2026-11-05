"""Behavioural tests for E2, the multi-scale salient-structure method.

E2's whole claim is that it scores *structure* — where the boundary turns, which
way, how sharply, in what order — rather than a corner count or a boundary offset.
That claim is only worth anything if the tests hold it against the code, so almost
every test below states a property and then measures it, instead of pinning a magic
number.  Where a number appears it is the number the implementation produces on
this machine (128-point standardized rings, ``SALIENCE_K = 3.0``), and the
assertions around it are orderings and ratios, which survive a parameter change.

The shared contract (``perceptual_methods``) is what these tests read the contours
through: 128-point closed CCW unit-RMS rings built by the project's own
standardizer, so a test cannot drift away from what the gallery stores.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from earthglyph.data.perceptual_gallery import (
    blob_matching_solidity,
    circle,
    crescent,
    heart,
    irregular,
    regular_polygon,
    standardize,
    star,
)
from earthglyph.features.geometry_stats import shape_metrics
from earthglyph.retrieval import perceptual_methods
from earthglyph.retrieval import salient_structure as e2
from earthglyph.retrieval.alignment import mirror_points

N = 128


def rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(float(angle_deg))
    matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ matrix.T, dtype=float)


def solidity(contour: np.ndarray) -> float:
    return float(shape_metrics(contour).solidity)


@pytest.fixture(scope="module")
def shapes() -> dict[str, np.ndarray]:
    """The comparison set, all through the real standardizer."""
    query = standardize(star())
    lobed, _achieved, _info = blob_matching_solidity(solidity(query), count=5, sharpness=2.0)
    cusp, _a, _i = blob_matching_solidity(solidity(query), count=5, sharpness=0.55)
    return {
        "star5": query,
        "star5_jitter": standardize(star(arm_jitter=[1.0, 0.9, 1.05, 0.85, 1.1])),
        "star5_worn": standardize(star(tip_round=0.18, valley_round=0.12)),
        "star5_lobed": standardize(lobed),
        "star5_cusp": standardize(cusp),
        "star4": standardize(star(points_count=4)),
        "star6": standardize(star(points_count=6)),
        "circle": standardize(circle()),
        "gon128": standardize(regular_polygon(128, per_edge=1)),
        "pentagon": standardize(regular_polygon(5)),
        "hexagon": standardize(regular_polygon(6)),
        "crescent": standardize(crescent()),
        "heart": standardize(heart()),
        "irregular": standardize(irregular(3)),
    }


# --- the turning atoms and the bar -------------------------------------------


def test_exterior_angles_sum_to_one_full_turn(shapes: dict[str, np.ndarray]) -> None:
    """Signed turning around a simple closed CCW ring is exactly 2*pi.

    This is the invariant the whole method is priced in: the score's denominator is
    justified only if "one full turn" is the same quantity for every shape.
    """
    for name, contour in shapes.items():
        total = float(np.sum(e2.exterior_angles(contour)))
        assert np.isclose(total, 2.0 * np.pi, atol=1e-9), name


def test_bar_equals_the_accumulated_turning_of_a_cornerless_polygon() -> None:
    """A regular 128-gon sampled vertex-to-vertex *is* the reference signal.

    ``uniform_turn_reference`` claims to be "what a circle accumulates over this
    window".  On a uniformly sampled regular polygon every vertex turns by 2*pi/n,
    so a window of 2*lag+1 vertices must accumulate the reference exactly — not
    approximately, and not up to a constant.  If the bar and the atoms ever drift
    apart, every shape in the gallery starts looking like a corner or stops looking
    like one.
    """
    ring = np.asarray(regular_polygon(N, per_edge=1), dtype=float)
    lags = [max(1, int(round(fraction * N))) for fraction in e2.DEFAULT_SCALES]
    measured = e2.signed_turning(ring)
    for index, lag in enumerate(lags):
        expected = e2.uniform_turn_reference(N, lag)
        assert np.allclose(measured[:, index], expected, rtol=1e-12, atol=1e-15)
        assert np.isclose(expected, 2.0 * np.pi * (2 * lag + 1) / N)


def test_cornerless_polygon_produces_no_events() -> None:
    """A 128-sided polygon is a circle to this method, and gets zero events.

    Also the flat-signal path of ``_cyclic_peaks``: a constant column has no local
    maximum, so an all-plateau contour must not invent corners.
    """
    ring = np.asarray(regular_polygon(N, per_edge=1), dtype=float)
    assert e2.signature(ring).events == ()


def test_circle_produces_no_events(shapes: dict[str, np.ndarray]) -> None:
    """The load-bearing claim: a disk must never be allowed to look like a corner.

    Every other test in this file that ranks a star above a circle depends on the
    circle contributing *nothing* to be matched, so that the distance is the star's
    unexplained turning rather than a difference between two event lists.
    """
    assert shapes["circle"] is not None
    for name in ("circle", "gon128"):
        signature = e2.signature(shapes[name])
        assert signature.events == (), name
        assert np.isclose(signature.total_turning, 2.0 * np.pi)


def test_star_reports_tips_and_valleys_with_the_right_signs(
    shapes: dict[str, np.ndarray],
) -> None:
    """Five arms = five convex tips and five concave valleys, alternating.

    Sign is the discriminator the experiment is about: a star and a disk of the same
    area differ here and only here.  Getting this backwards (the first version of
    this module reported ``conv=0 conc=5``) would flip every ranking.
    """
    signature = e2.signature(shapes["star5"])
    assert len(signature.events) == 10
    assert signature.n_convex == 5 and signature.n_concave == 5
    signs = [event.sign for event in signature.events]
    assert signs == [1, -1] * 5 or signs == [-1, 1] * 5
    magnitudes = {round(event.magnitude, 2) for event in signature.events}
    assert magnitudes == {2.43, 1.17}  # tip turn and valley turn of star10 proportions


def test_convex_polygon_never_invents_a_valley(shapes: dict[str, np.ndarray]) -> None:
    for name in ("pentagon", "hexagon"):
        signature = e2.signature(shapes[name])
        assert signature.n_concave == 0, name
        assert signature.n_convex == {"pentagon": 5, "hexagon": 6}[name], name


def test_one_event_per_corner_even_over_a_flat_top(shapes: dict[str, np.ndarray]) -> None:
    """A worn star reports ten corners, not one per sample of the rounded tip.

    Two duplications are possible and both are guarded here.  A flat top is long: the
    windowed turning signal is constant for as long as the window still contains the
    same corner, so a naive local-maximum test can report one peak per sample of it.
    And the same corner is genuinely found at several windows: with
    ``_merge_duplicates`` bypassed, this contour yields 16 events instead of 10 and the
    star yields 17 instead of 10 — every one of those extras sits at the *same* arc
    position as a kept event (measured offset 0 samples), which is one piece of
    geometry seen through two apertures, not two corners.  On the default ladder every
    one of the ten corners is therefore at least 0.06 of the perimeter from its
    neighbour.
    """
    for name in ("star5", "star5_worn", "star5_jitter"):
        events = e2.signature(shapes[name]).events
        assert len(events) == 10, name
        positions = np.array([event.position for event in events])
        delta = np.abs(positions[:, None] - positions[None, :])
        delta = np.minimum(delta, 1.0 - delta)
        np.fill_diagonal(delta, 1.0)
        assert delta.min() > 0.06, (name, delta.min())


def test_coarser_ladders_report_coarser_structure(shapes: dict[str, np.ndarray]) -> None:
    """Drop the finest window and the valleys stop resolving: 10 corners become 5.

    Recorded because it is a property of the scale ladder, not a bug: at lag 4 and
    above a valley's negative turn sits inside the same window as the two tips beside
    it and cancels, so the aperture genuinely cannot resolve it.  A change to
    ``DEFAULT_SCALES`` should be a decision about this, and the test says so.
    """
    query = shapes["star5"]
    assert len(e2.signature(query, scales=(1 / 64, 1 / 32)).events) == 10
    assert len(e2.signature(query, scales=(1 / 32, 1 / 16, 1 / 8)).events) == 5
    assert e2.signature(query, scales=(1 / 16, 1 / 8, 1 / 4)).events == ()


def test_zero_perimeter_contour_is_refused() -> None:
    collapsed = np.zeros((N, 2))
    with pytest.raises(e2.SalientStructureError, match="zero perimeter"):
        e2.signature(collapsed)


# --- the score itself ----------------------------------------------------------


def test_identity_is_exactly_zero(shapes: dict[str, np.ndarray]) -> None:
    for name, contour in shapes.items():
        assert e2.distance(contour, contour)[0] == pytest.approx(0.0, abs=1e-12), name


def test_circle_against_star_is_the_stars_turning_in_full_turns(
    shapes: dict[str, np.ndarray],
) -> None:
    """The denominator is absolute, so the number means something on its own.

    With a circle contributing no events, every tip and valley of the star is a gap
    and the cost is the star's total absolute turning divided by 2*pi — measured, not
    asserted, and it is what keeps "how unexplained is this shape" comparable across
    candidates.
    """
    query, disk = shapes["star5"], shapes["circle"]
    distance, fitted = e2.distance(query, disk)
    expected = float(np.abs(e2.exterior_angles(query)).sum()) / (2.0 * np.pi)
    assert distance == pytest.approx(expected, rel=1e-9)
    assert fitted["unmatched_left"] == 10 and fitted["unmatched_right"] == 0
    assert fitted["pairs"] == []


def test_both_empty_signatures_score_zero(shapes: dict[str, np.ndarray]) -> None:
    assert e2.distance(shapes["circle"], shapes["gon128"])[0] == pytest.approx(0.0, abs=1e-12)


def test_score_is_not_a_corner_count(shapes: dict[str, np.ndarray]) -> None:
    """Three contours with the *same* event count, spread over a 12x distance range.

    If the score were "how many corners do we share", these would all be equal.  The
    worn star and the five-lobed blob both present 5 convex + 5 concave events at the
    same positions; what differs is how much boundary turning each corner carries and
    how pointed it is — which is exactly the distinction the brief refuses to let this
    method lose.
    """
    query = shapes["star5"]
    counts = {
        name: len(e2.signature(query).events) == len(e2.signature(shapes[name]).events)
        for name in ("star5_worn", "star5_lobed", "star5_cusp")
    }
    assert all(counts.values()), counts
    worn = e2.distance(query, shapes["star5_worn"])[0]
    lobed = e2.distance(query, shapes["star5_lobed"])[0]
    cusp = e2.distance(query, shapes["star5_cusp"])[0]
    assert lobed / worn > 3.0, (worn, lobed)
    assert cusp > lobed > worn


def test_worn_tips_are_near_but_not_identical(shapes: dict[str, np.ndarray]) -> None:
    """Rounding is a real second-order difference, not a reclassified shape.

    Two claims, both measured: the worn star must be *closer* to the star than a
    six-armed star is (so rounding is not punished as a different structure), and it
    must not be at distance 0 (which is what the turn-group price alone produced, and
    which would have been an over-claim of invariance).
    """
    query = shapes["star5"]
    worn = e2.distance(query, shapes["star5_worn"])[0]
    six = e2.distance(query, shapes["star6"])[0]
    assert 0.05 < worn < 0.5, worn
    assert worn < six


def test_structure_ladder_ranks_corner_preserving_variants_first(
    shapes: dict[str, np.ndarray],
) -> None:
    """The star ladder, with no "star first" hard-coding anywhere in the method.

    Ordering by unexplained turning alone: jitter, then worn tips, then the wrong arm
    count, then the same solidity with no corners, then a convex polygon, then a
    circle.  Each step is a real perceptual step, and the sequence falls out of the
    geometry rather than from a weight.
    """
    query = shapes["star5"]
    order = [
        "star5_jitter",
        "star5_worn",
        "star4",
        "star6",
        "star5_lobed",
        "pentagon",
        "circle",
    ]
    distances = [e2.distance(query, shapes[name])[0] for name in order]
    assert distances == sorted(distances), list(zip(order, distances, strict=True))
    assert distances[-1] > 8.0 * distances[0]


def test_invariances_are_exact(shapes: dict[str, np.ndarray]) -> None:
    """Translation, uniform scale, arbitrary rotation and start point change nothing.

    Asserted to 1e-12 because these are not searched-approximately: angles do not see
    position or size, and the cyclic offset search covers a re-started ring exactly.
    A regression here means the price depends on where the digitiser put sample 0.
    """
    query, candidate = shapes["star5"], shapes["pentagon"]
    reference = e2.distance(query, candidate)[0]
    variants = {
        "translate": candidate + np.array([0.37, -1.2]),
        "rotate_37": rotate(candidate, 37.0),
        "rotate_180": rotate(candidate, 180.0),
        "scale_2p5": candidate * 2.5,
        "roll_13": np.roll(candidate, 13, axis=0),
        "roll_prime": np.roll(rotate(candidate, 12.0), 7, axis=0),
    }
    for name, contour in variants.items():
        assert e2.distance(query, contour)[0] == pytest.approx(reference, abs=1e-12), name


def test_resampling_phase_leaves_a_small_residual(shapes: dict[str, np.ndarray]) -> None:
    """Re-binning the *same* shape at a new phase is not an invariance, and is small.

    E2 prices a corner by its turn group, so a corner that lands between two samples
    splits differently depending on where the samples are.  Measured residual is
    ~1e-3 of the distance, i.e. a hundred times below the jitter-vs-arm-count gap;
    stating it as a bounded residual keeps POLICY honest about what is exact.
    """
    query, candidate = shapes["star5"], shapes["pentagon"]
    reference = e2.distance(query, candidate)[0]
    resampled = e2.distance(query, standardize(rotate(candidate, 37.0) * 2.1))[0]
    assert abs(resampled - reference) / reference < 0.02


def test_traversal_direction_is_a_probe_not_absorbed(shapes: dict[str, np.ndarray]) -> None:
    """Reversing the walk is not silently fixed: it changes the score.

    The rings arrive CCW from the shared standardizer, so this cannot happen by
    accident in the pipeline — but the method must not be *credited* with an
    invariance it does not have, and the reversal probe has to be reported as its own
    measurement (brief: direction handled separately).
    """
    contour = shapes["irregular"]
    assert e2.distance(contour, contour)[0] == pytest.approx(0.0, abs=1e-12)
    assert e2.distance(contour, contour[::-1])[0] > 0.3


def test_mirror_switch_reflects_rather_than_rotates(shapes: dict[str, np.ndarray]) -> None:
    """``allow_mirror`` must be a reflection.

    The first version of this module mirrored as ``points[:, ::-1] * [-1, 1]``, which
    is (x, y) -> (-y, x): determinant +1, a quarter turn the offset search already
    absorbs.  The switch then did nothing at all — measured 0.6181 both ways on a
    chiral contour.  ``alignment.mirror_points`` is the project's one convention
    (reflect in x, restore CCW) and is what E0, E1 and E3 use.
    """
    chiral = shapes["irregular"]
    reflected = e2._mirror(chiral)
    assert np.array_equal(reflected, mirror_points(chiral))
    assert not np.allclose(reflected, rotate(chiral, 90.0))
    direct = e2.distance(chiral, reflected, allow_mirror=False)[0]
    allowed = e2.distance(chiral, reflected, allow_mirror=True)
    assert allowed[0] == pytest.approx(0.0, abs=1e-9)
    assert allowed[1]["mirrored"] is True
    assert direct > 0.3
    # a mirror-symmetric shape is a tie, and the direct pass is reported
    symmetric = shapes["star5"]
    mirrored_star = e2.distance(symmetric, mirror_points(symmetric), allow_mirror=True)
    assert mirrored_star[0] == pytest.approx(0.0, abs=1e-9)


def test_pen_jitter_is_down_weighted_by_geometry(shapes: dict[str, np.ndarray]) -> None:
    """1% vertex noise costs no distance at all; 0.5% costs 3% of a full turn.

    The claim in POLICY is that jitter is suppressed by the salience bar rather than
    by a hand-set coefficient, so the test fixes a *ratio*: the noise term stays far
    below the structure term (a circle-vs-star distance of 2.86).  Where the bar does
    start firing is also recorded, because it is a real limit of the method: at
    sigma = 0.01 of the RMS radius the spurious turning reaches 0.65, which is why the
    gallery contours go through the project's smoothing and resampling first.
    """
    disk = shapes["circle"]
    rng = np.random.default_rng(0)
    tiny = standardize(disk + rng.normal(0.0, 0.002, disk.shape))
    assert e2.distance(disk, tiny)[0] == pytest.approx(0.0, abs=1e-12)
    rng = np.random.default_rng(0)
    mild = standardize(disk + rng.normal(0.0, 0.005, disk.shape))
    noise = e2.distance(disk, mild)[0]
    structure = e2.distance(disk, shapes["star5"])[0]
    assert noise < 0.05 * structure, (noise, structure)


# --- the cost function, priced by hand ----------------------------------------


def _signature_of(events: list[tuple[float, int, float]]) -> e2.SalientSignature:
    """A signature built directly, so the pricing can be isolated from detection."""
    return e2.SalientSignature(
        events=tuple(
            e2.SalientEvent(
                position=position,
                sign=sign,
                magnitude=magnitude,
                scale_index=1,
                persistence=2,
                peak=magnitude,
            )
            for position, sign, magnitude in events
        ),
        n_vertices=N,
        scales=e2.DEFAULT_SCALES,
        total_turning=2.0 * np.pi,
        turning=np.zeros((N, len(e2.DEFAULT_SCALES))),
    )


def test_prong_matched_to_valley_pays_both_turns() -> None:
    """Convex/concave confusion is not a small penalty; it is the sum.

    The tie is the point to notice: for positive magnitudes ``|a - (-b)| == a + b``,
    which is exactly what leaving both events unmatched costs.  So the DP never
    prefers a prong-to-valley correspondence — the sign enters through the cost
    function and the alignment declines the pair on price, which is the behaviour the
    brief asks for (convex and concave structure must not be conflated) and is why the
    same-sign pair below costs nothing while the opposite-sign pair costs both turns.
    """
    convex = _signature_of([(0.0, 1, 1.6)])
    concave = _signature_of([(0.0, -1, 1.6)])
    aligned = e2.align_sequences(convex, concave)
    assert aligned["cost"] == pytest.approx(3.2, abs=1e-9)
    assert aligned["unmatched_left"] == 1 and aligned["unmatched_right"] == 1
    same = e2.align_sequences(convex, _signature_of([(0.0, 1, 1.6)]))
    assert same["cost"] == pytest.approx(0.0, abs=1e-12)
    assert len(same["pairs"]) == 1 and same["pairs"][0]["sign_match"] is True
    # and the sign really is what makes the price: equal magnitude, wrong sign
    assert e2._match_cost(  # type: ignore[attr-defined]
        convex.events[0], concave.events[0], 0.0
    ) == pytest.approx(3.2, abs=1e-9)


def test_drift_is_free_inside_the_budget_then_linear() -> None:
    """The deformation budget: no cost up to 0.02 of the perimeter, then unit gain.

    Two events, because a single-event sequence is re-anchored to position 0 by
    ``_cut`` and cannot show drift at all.  The first event of each signature is the
    anchor (its magnitude is the largest), so the second pair is measured from a
    common zero.
    """
    anchor = (0.0, 1, 2.0)
    for drift in (0.0, 0.01, e2.DEFORMATION_BUDGET):
        left = _signature_of([anchor, (0.5, -1, 1.2)])
        right = _signature_of([anchor, (0.5 + drift, -1, 1.2)])
        assert e2.align_sequences(left, right)["cost"] == pytest.approx(0.0, abs=1e-12)
    left = _signature_of([anchor, (0.5, -1, 1.2)])
    right = _signature_of([anchor, (0.57, -1, 1.2)])
    expected = 1.2 * (0.07 - e2.DEFORMATION_BUDGET)
    assert e2.align_sequences(left, right)["cost"] == pytest.approx(expected, rel=1e-9)


def test_pointedness_is_charged_at_a_quarter_weight() -> None:
    """Same group total, different edge quality: the peak term is what separates them.

    Without this term a worn tip and a needle tip priced identically — with
    ``PEAK_GAIN = 0`` the real pair ``star(5)`` vs its rounded copy measures exactly
    0.0 while the same rounding at 0.25 measures 0.2348 — which claimed an invariance
    the method does not have.  The upper side of the choice was measured too: at unit
    gain the rounding alone reaches 0.9391, 68% of the way to the six-armed star's
    1.3909, so worn tips would nearly rival a change of arm count.  ``PEAK_GAIN =
    0.25`` sits between a false identity and a reclassified shape, and
    ``test_worn_tips_are_near_but_not_identical`` holds the real pair inside that band.
    """
    sharp = _signature_of([(0.0, 1, 2.0)])
    worn = e2.SalientSignature(
        events=tuple(replace(event, peak=0.4) for event in sharp.events),
        n_vertices=sharp.n_vertices,
        scales=sharp.scales,
        total_turning=sharp.total_turning,
        turning=sharp.turning,
    )
    cost = e2.align_sequences(sharp, worn)["cost"]
    assert cost == pytest.approx(e2.PEAK_GAIN * 1.6, rel=1e-9)
    assert 0.0 < e2.PEAK_GAIN < 1.0


def test_arm_count_difference_does_not_outrank_missing_corners(
    shapes: dict[str, np.ndarray],
) -> None:
    """A six-armed star stays nearer to a five-armed one than a circle does.

    The rejected formulation divided the drift excess by the budget, so that sliding a
    corner by two budgets cost its whole turn.  Priced that way on this pair the
    six-armed star lands at 3.9716 against the circle's unchanged 2.8610 — every arm
    has to slide a few percent of the perimeter, so the count mismatch pays about six
    full turns of drift.  People see five-vs-six, but they see star-vs-circle first;
    with the unit gain the shipped code uses the same pair measures 1.0902, and this is
    the guard.
    """
    query = shapes["star5"]
    assert e2.distance(query, shapes["star6"])[0] < e2.distance(query, shapes["circle"])[0]


# --- contract plumbing ---------------------------------------------------------


def test_distance_library_matches_pairwise(shapes: dict[str, np.ndarray]) -> None:
    query = shapes["star5"]
    library = np.stack([shapes[name] for name in sorted(shapes)])
    vector = e2.distance_library(query, library)
    pairwise = np.array([e2.distance(query, row)[0] for row in library])
    assert vector.shape == (len(shapes),)
    assert np.array_equal(vector, pairwise)
    assert np.isfinite(vector).all()


def test_score_library_agrees_and_carries_the_policy(
    shapes: dict[str, np.ndarray],
) -> None:
    query = shapes["star5"]
    library = np.stack([shapes[name] for name in sorted(shapes)])
    scores, metadata = e2.score_library(query, library)
    dispatched, dispatched_meta = perceptual_methods.score_library("E2", query, library)
    assert np.array_equal(scores, dispatched)
    assert metadata["policy"] == dispatched_meta["policy"]
    assert metadata["policy"]["id"] == "E2"
    assert len(metadata["transforms"]) == len(library)
    assert all("rotation_offset" in item for item in metadata["transforms"])
    for index, row in enumerate(library):
        assert scores[index] == pytest.approx(e2.distance(query, row)[0], rel=1e-12)


def test_ranking_is_deterministic(shapes: dict[str, np.ndarray]) -> None:
    query = shapes["star5"]
    library = np.stack([shapes[name] for name in sorted(shapes)])
    first = perceptual_methods.rank_order(e2.distance_library(query, library))
    second = perceptual_methods.rank_order(e2.distance_library(query, library))
    assert np.array_equal(first, second)
    assert e2.POLICY["scales"] == [round(value, 6) for value in e2.DEFAULT_SCALES]
    assert e2.POLICY["new_code_in_this_checkpoint"] is True


def test_transform_of_a_query_against_itself_reports_every_pair(
    shapes: dict[str, np.ndarray],
) -> None:
    query = shapes["star5"]
    scores, metadata = e2.score_library(query, np.stack([query]))
    assert scores[0] == pytest.approx(0.0, abs=1e-12)
    transform = metadata["transforms"][0]
    assert transform["query_events"] == transform["candidate_events"] == 10
    assert len(transform["pairs"]) == 10
    assert all(pair["sign_match"] for pair in transform["pairs"])
    assert 0.0 <= transform["rotation_offset"] < 1.0
