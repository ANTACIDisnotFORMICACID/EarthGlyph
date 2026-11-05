"""Behavioural tests for E1, the ShapeComp-*inspired* descriptor battery.

Three things are held to account here.

*The provenance line.*  This arm is inspired by Morgenstern et al. 2021 and is not
their model; the tests check that the code says so and that the descriptor count the
policy quotes is the count the code emits (measured, never typed).

*The two-stage scale policy.*  Stage 2 is the part that decides whether a descriptor
with range 4 means more than one with range 0.001, so the normalizer's own arithmetic
is tested directly — including the case that silently breaks a distance: a corpus
column that is constant.

*The invariances, per mechanism.*  POLICY claims exactness for four of the five
transformations and a *conditional* claim for the fifth, because one block of the
vector is rotation covariant and is measured in a principal-axis frame that is
undefined for a near-isotropic contour.  The tests therefore run the invariance
matrix twice: once on a chiral, anisotropic contour (where every claim must be exact)
and once on a regular pentagon (where the fallback really does cost distance, and the
number is stated instead of hidden).

Tolerances are written as relative residuals against the untransformed distance,
because the absolute scale depends on the fitted corpus.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from earthglyph.data.perceptual_gallery import (
    circle,
    crescent,
    irregular,
    regular_polygon,
    standardize,
    star,
)
from earthglyph.retrieval import perceptual_features as e1
from earthglyph.retrieval import perceptual_methods
from earthglyph.retrieval.alignment import mirror_points

N = 128
#: the six emitted Hu invariants plus a scale-free geometric check, as absolute limits
EXACT = 1e-9


def rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = math.radians(float(angle_deg))
    matrix = np.array(
        [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
    )
    return np.asarray(points @ matrix.T, dtype=float)


@pytest.fixture(scope="module")
def corpus() -> dict[str, np.ndarray]:
    return {
        "star5": standardize(star()),
        "star6": standardize(star(points_count=6)),
        "chiral": standardize(irregular(3)),
        "pentagon": standardize(regular_polygon(5)),
        "circle": standardize(circle()),
        "crescent": standardize(crescent()),
    }


@pytest.fixture(scope="module")
def normalizer(corpus: dict[str, np.ndarray]) -> e1.FeatureNormalizer:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    return e1.fit_normalizer(
        e1.features_for_library(rows), names=list(e1.feature_names()), fitted_on="test corpus"
    )


def residuals(
    query: np.ndarray,
    candidate: np.ndarray,
    normalizer: e1.FeatureNormalizer,
    **kwargs: object,
) -> dict[str, float]:
    """Relative change of the distance under each transformation of the candidate.

    Returns the empty-ish key ``"identity"`` as 0.0 so the caller can see the matrix
    has all its rows.
    """
    reference = e1.distance(query, candidate, normalizer=normalizer, **kwargs)[0]
    transforms = {
        "identity": candidate,
        "start_point": np.roll(candidate, 13, axis=0),
        "traversal_direction": candidate[::-1],
        "translation": candidate + np.array([0.3, -1.1]),
        "uniform_scale": candidate * 2.5,
        "rotation": rotate(candidate, 40.0),
        "rotation_and_start": np.roll(rotate(candidate, 40.0), 7, axis=0),
    }
    out: dict[str, float] = {}
    for name, contour in transforms.items():
        value = e1.distance(query, contour, normalizer=normalizer, **kwargs)[0]
        out[name] = abs(value - reference) / max(reference, 1e-12)
    return out


# --- provenance and the descriptor vector --------------------------------------


def test_descriptor_count_is_measured_not_typed() -> None:
    names = list(e1.feature_names())
    counts = e1.descriptor_counts()
    assert counts["total"] == len(names) == 91
    assert sum(counts[group] for group in e1.GROUPS) == counts["total"]
    assert e1.POLICY["descriptor_counts"] == counts


def test_policy_declares_itself_inspired_not_shapecomp() -> None:
    """The naming constraint is a correctness constraint, so it is asserted here.

    A results table that printed "ShapeComp" for this arm would misattribute a
    published human-similarity correlation to code that shares none of its corpus,
    its 22-D space or its network weights.
    """
    policy = e1.describe_policy()
    assert "inspired" in policy["name"].lower()
    assert "not the shapecomp model" in policy["name"].lower()
    provenance = policy["provenance"].lower()
    # every thing this arm is NOT has to be named, so a reader cannot infer a claim
    for present in ("25,712", "mds", "22-d", "matnet", "kernet", "silhouettes", "matlab"):
        assert present in provenance, present
    assert "do not cite as 'the shapecomp model'" in provenance
    doc = e1.__doc__ or ""
    assert "inspired by" in doc.lower()
    assert "must never be cited" in doc
    # the purchased-stock-image provenance is the reason, and it is stated once here
    assert "shutterstock" in doc.lower()
    assert "25712" not in json.dumps(policy)  # no corpus constant this arm does not have
    assert policy["new_code_in_this_checkpoint"] is True


def test_groups_partition_the_vector(corpus: dict[str, np.ndarray]) -> None:
    query = corpus["star5"]
    every = list(e1.descriptor_values(query).values())
    joined: list[float] = []
    for group in e1.GROUPS:
        block = list(e1.descriptor_values(query, groups=(group,)).values())
        assert len(block) == e1.descriptor_counts()[group], group
        assert np.all(np.isfinite(block)), group
        joined.extend(block)
    assert np.allclose(joined, every, atol=1e-12)


def test_unknown_group_and_empty_selection_are_refused(corpus: dict[str, np.ndarray]) -> None:
    with pytest.raises(e1.PerceptualFeatureError, match="unknown group"):
        e1.check_groups(("nope",))
    with pytest.raises(e1.PerceptualFeatureError):
        e1.check_groups(())
    with pytest.raises(e1.PerceptualFeatureError, match="distance_kind"):
        e1.check_distance_kind("chebyshev")
    with pytest.raises(e1.PerceptualFeatureError, match="orientation"):
        e1.check_orientation("align")


# --- stage-2 scale policy ------------------------------------------------------


def test_minmax_maps_the_corpus_onto_unit_columns(corpus: dict[str, np.ndarray]) -> None:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    matrix = e1.features_for_library(rows)
    fitted = e1.fit_normalizer(matrix, names=list(e1.feature_names()))
    scaled = fitted.transform(matrix)
    # every non-degenerate column spans exactly [0, 1]; degenerate ones stay flat
    for index, name in enumerate(fitted.names):
        column = scaled[:, index]
        if name in fitted.degenerate:
            assert np.allclose(column, column[0]), name
        else:
            assert column.min() == pytest.approx(0.0, abs=1e-12), name
            assert column.max() == pytest.approx(1.0, abs=1e-12), name


def test_robust_method_uses_median_and_iqr(corpus: dict[str, np.ndarray]) -> None:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    matrix = e1.features_for_library(rows)
    robust = e1.fit_normalizer(matrix, method="robust", names=list(e1.feature_names()))
    median = np.median(matrix, axis=0)
    inter = np.percentile(matrix, 75.0, axis=0) - np.percentile(matrix, 25.0, axis=0)
    assert np.allclose(robust.center, median)
    for index in range(matrix.shape[1]):
        if inter[index] > 0.0:
            assert robust.scale[index] == pytest.approx(inter[index])
        else:
            # a constant column must divide by 1, never by 0
            assert robust.scale[index] == pytest.approx(1.0)
            assert robust.names[index] in robust.degenerate


def test_normalizer_refuses_bad_corpuses_and_wrong_widths() -> None:
    with pytest.raises(e1.PerceptualFeatureError, match="method"):
        e1.fit_normalizer(np.zeros((3, 2)), method="std")
    with pytest.raises(e1.PerceptualFeatureError, match="at least 2"):
        e1.fit_normalizer(np.zeros((1, 2)))
    bad = np.ones((3, 2))
    bad[0, 0] = np.nan
    with pytest.raises(e1.PerceptualFeatureError, match="non-finite"):
        e1.fit_normalizer(bad)
    with pytest.raises(e1.PerceptualFeatureError, match="names"):
        e1.fit_normalizer(np.zeros((3, 2)), names=("a", "b", "c"))
    fitted = e1.fit_normalizer(np.array([[0.0, 1.0], [2.0, 1.0], [4.0, 1.0]]))
    with pytest.raises(e1.PerceptualFeatureError, match="descriptors"):
        fitted.transform(np.zeros((2, 5)))


def test_transform_preserves_dimensionality(normalizer: e1.FeatureNormalizer) -> None:
    """A ``(d,)`` vector must come back ``(d,)``.

    It used to come back ``(1, d)``, and ``distance()`` then handed ``np.dot`` two
    2-D arrays: every scored pair raised ``shapes (1, 91) and (1, 91) not aligned``.
    A corpus-fitted normalizer is the configuration the experiment runs on, so the
    crash sat on the only path that matters.
    """
    width = normalizer.n_descriptors
    assert normalizer.transform(np.zeros(width)).shape == (width,)
    assert normalizer.transform(np.zeros((3, width))).shape == (3, width)


def test_distance_and_score_library_both_accept_a_corpus_normalizer(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    self_row = sorted(corpus).index("star5")
    scores, records = e1.score_library(corpus["star5"], rows, normalizer=normalizer)
    pairwise = np.array(
        [
            e1.distance(corpus["star5"], row, normalizer=normalizer)[0]
            for row in rows
        ]
    )
    assert np.allclose(scores, pairwise)
    assert normalizer.n_descriptors == 91
    # the library is alphabetical, so the query's own row is not row 0
    assert scores[self_row] == pytest.approx(0.0, abs=1e-12)


# --- what the scores mean ------------------------------------------------------


def test_identity_is_zero_and_distance_is_symmetric(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    for name, contour in corpus.items():
        assert e1.distance(contour, contour, normalizer=normalizer)[0] == pytest.approx(
            0.0, abs=1e-12
        ), name
    left, right = corpus["star5"], corpus["crescent"]
    assert e1.distance(left, right, normalizer=normalizer)[0] == pytest.approx(
        e1.distance(right, left, normalizer=normalizer)[0], rel=1e-12
    )


def test_raw_units_are_reported_as_not_comparable(
    corpus: dict[str, np.ndarray]
) -> None:
    """``distance()`` without a normalizer says so in the record it returns."""
    score, info = e1.distance(corpus["star5"], corpus["pentagon"])
    assert "raw descriptor units" in info["normalizer"]
    assert info["n_descriptors"] == 91
    assert info["mirrored"] is False
    _, fitted = e1.distance(corpus["star5"], corpus["pentagon"], groups=("global",))
    assert fitted["groups"] == ["global"]


def test_cityblock_is_a_larger_than_euclidean_alternative(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    left, right = corpus["star5"], corpus["crescent"]
    euclid = e1.distance(left, right, normalizer=normalizer)[0]
    block = e1.distance(left, right, normalizer=normalizer, distance_kind="cityblock")[0]
    assert block >= euclid > 0.0
    assert block != pytest.approx(euclid)


def test_self_fit_is_reported_not_silent(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    _scores, auto = e1.score_library(corpus["star5"], rows)
    assert auto["normalizer"]["self_fit"] is True
    assert auto["normalizer"]["supplied_by_caller"] is False
    assert "library it scored" in auto["normalizer"]["fitted_on"]
    assert "weaker claim" in auto["normalizer"]["claim"]
    _scores, given = e1.score_library(corpus["star5"], rows, normalizer=normalizer)
    assert given["normalizer"]["self_fit"] is False
    assert given["normalizer"]["fitted_on"] == "test corpus"
    assert "corpus-fitted scale supplied" in given["normalizer"]["claim"]


# --- invariances ---------------------------------------------------------------


def test_invariances_are_exact_for_an_anisotropic_contour(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    """Every claimed invariance holds to floating point when the frame is defined.

    ``chiral`` is an ``irregular`` contour: not mirror-symmetric, not isotropic, so
    the principal-axis frame that carries the one covariant block is identifiable.
    Translation, uniform scale, start point and traversal direction are exact here
    (and exact even for the isotropic cases, because the fallback frame is the input
    frame, chosen deterministically as angle 0).
    """
    table = residuals(corpus["star5"], corpus["chiral"], normalizer)
    for key in ("identity", "start_point", "traversal_direction", "translation",
                "uniform_scale", "rotation", "rotation_and_start"):
        assert table[key] < EXACT, (key, table[key])


def test_isotropic_contours_take_the_fallback_frame_and_say_so(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    """The one honest hole in the rotation claim, quantified rather than hidden.

    A regular pentagon has equal principal second moments (ratio 0.9994 here), so
    there is no axis to align to: the frame falls back to the input frame and the
    tangent-angle block rotates with the contour.  Measured residual, rotation 14% of
    the distance while translation, scale and start point stay at 0.9% (one sample of
    a 128-bin histogram crossing a bin edge, which is what binning costs).  POLICY
    states both numbers instead of claiming invariance.
    """
    frame = e1.principal_frame(e1.prepare(corpus["pentagon"]))
    assert frame.status == "isotropic-fallback"
    assert frame.angle_rad == 0.0
    assert frame.axis_ratio >= e1.ORIENTATION_ISOTROPY_MAX
    table = residuals(corpus["star5"], corpus["pentagon"], normalizer)
    assert table["rotation"] > 0.05
    assert table["rotation"] < 0.30
    for key in ("translation", "uniform_scale", "start_point", "traversal_direction"):
        assert table[key] < 0.02, (key, table[key])
    circle_frame = e1.principal_frame(e1.prepare(corpus["circle"]))
    assert circle_frame.status == "isotropic-fallback"


def test_the_canonical_frame_is_what_buys_rotation_invariance(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    """``orientation="raw"`` leaves the covariant block in the input frame on purpose.

    Same query, same candidate, same corpus: the raw arm moves 3% under a rotation
    while the canonical arm does not move at all.  That difference is the measurement
    behind the choice of mechanism, and it is cheap enough that no angular search is
    needed to get invariance.
    """
    raw = residuals(corpus["star5"], corpus["chiral"], normalizer, orientation="raw")
    canonical = residuals(corpus["star5"], corpus["chiral"], normalizer)
    assert raw["rotation"] > 1e-3
    assert canonical["rotation"] < EXACT
    assert raw["translation"] < EXACT  # the frame is not what translation breaks


def test_mirror_switch_is_a_real_equivalence_test(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    """``allow_mirror`` may only lower a score, and it reports when it did.

    The moments block is mirror-even by construction (the seventh, reflection-odd
    invariant is omitted), so the tangent histogram is the only part that can move —
    and for a chiral contour the mirrored candidate must score 0 once the switch is
    allowed.
    """
    chiral = corpus["chiral"]
    reflected = mirror_points(chiral)
    off = e1.distance(chiral, reflected, normalizer=normalizer, allow_mirror=False)[0]
    on, info = e1.distance(chiral, reflected, normalizer=normalizer, allow_mirror=True)
    assert off > 0.1
    assert on == pytest.approx(0.0, abs=1e-9)
    assert info["mirrored"] is True
    assert info["mirror_distance"] == pytest.approx(on)
    # the block that is mirror-blind really is: only the tangent histogram moves
    flipped = np.asarray(chiral) * e1._MIRROR_SIGN  # y -> -y
    for group in ("global", "moments", "fourier"):
        assert np.allclose(
            list(e1.descriptor_values(chiral, groups=(group,)).values()),
            list(e1.descriptor_values(flipped, groups=(group,)).values()),
            atol=1e-9,
        ), group
    assert not np.allclose(
        list(e1.descriptor_values(chiral, groups=("histogram",)).values()),
        list(e1.descriptor_values(flipped, groups=("histogram",)).values()),
        atol=1e-6,
    )


# --- the two blocks that carry the "perceptual" claim --------------------------


def test_curvature_events_use_the_circle_as_the_ruler(
    corpus: dict[str, np.ndarray]
) -> None:
    """The curvature shares are normalised by what a *circle* does at the same window.

    A disc must report no corner events at any window, and a star must report some at
    every window.  Without the circle reference a single threshold would mean one
    thing at 1/32 of the perimeter and another at 1/8, which is how a
    curvature-at-scale battery becomes uninterpretable.
    """
    for index, fraction in enumerate(e1.CURVATURE_WINDOW_FRACTIONS):
        label = f"{index:02d}_w{int(round(1.0 / fraction))}"
        disk = e1.descriptor_values(corpus["circle"], groups=("curvature",))
        star_values = e1.descriptor_values(corpus["star5"], groups=("curvature",))
        assert disk[f"curv{label}_convex_share"] == 0.0
        assert disk[f"curv{label}_concave_share"] == 0.0
        assert star_values[f"curv{label}_convex_share"] > 0.0
        assert star_values[f"curv{label}_concave_share"] > 0.0
        assert disk[f"curv{label}_max"] < e1.CURVATURE_CONVEX_MIN
    psi = e1.signed_turning(corpus["circle"], 4)
    assert np.allclose(psi, 1.0, atol=0.05)  # a disc turns exactly like a disc


def test_hu_invariants_match_the_closed_form_for_a_disc(
    corpus: dict[str, np.ndarray],
) -> None:
    """Analytic cross-check of the exact polygon integration.

    For a uniform disc the normalised second moments are eta20 = eta02 = 1/(4*pi) and
    every third-order eta vanishes, so I1 = 1/(2*pi) whatever the radius and I2..I6 are
    zero.  A sign error, an index swap or a lost normalisation in
    ``raw_moments``/``hu_invariants`` all show up here, in absolute units, with no
    reference implementation needed.

    I2 is the one column this corpus cannot check exactly: a 128-gon is not a disc, its
    (eta20 - eta02) is the polygon's real four-fold anisotropy (~1e-8, not round-off),
    and squaring it puts the log form near -14.  So I2 is asserted small rather than
    zero, and the exactness assertions sit on I1 and I3..I6.
    """
    values = e1.descriptor_values(corpus["circle"], groups=("moments",))
    vector = np.array(list(values.values()))
    expected = math.log10(1.0 / (2.0 * math.pi))
    assert vector[0] == pytest.approx(expected, abs=1e-3)
    assert vector[1] < -10.0, values["mom_hu_2"]
    # exact zeros in theory; the signed-log form reports an under-epsilon magnitude as
    # 0.0 rather than as +-20, which is the convention hu_invariants documents.
    for index in (2, 3, 4, 5):
        assert vector[index] == pytest.approx(0.0, abs=1e-12), f"I{index + 1}"


def hu_seventh(ring: np.ndarray) -> float:
    """I7 priced by the closed form ``hu_invariants`` documents, raw (not logged)."""
    moments = e1.central_moments(ring, 3)
    area = float(moments[0, 0])
    orders = np.arange(4)[:, None] + np.arange(4)[None, :]
    eta = moments / area ** (1.0 + 0.5 * orders)
    e30, e03, e21, e12 = float(eta[3, 0]), float(eta[0, 3]), float(eta[2, 1]), float(eta[1, 2])
    a_plus, b_plus = e30 + e12, e21 + e03
    a_minus, b_minus = e30 - 3.0 * e12, 3.0 * e21 - e03
    return float(
        b_minus * a_plus * (a_plus**2 - 3.0 * b_plus**2)
        - a_minus * b_plus * (3.0 * a_plus**2 - b_plus**2)
    )


def hu_six(ring: np.ndarray) -> np.ndarray:
    """The six emitted invariants for one contour, through the module's own path."""
    moments = e1.central_moments(ring, 3)
    return e1.hu_invariants(moments, float(moments[0, 0]))


def signed_log(value: float, *, with_floor: bool = True) -> float:
    """The project's signed-log form, with and without the under-epsilon rule."""
    if value == 0.0:
        return 0.0
    if with_floor and abs(value) < e1._HU_LOG_EPSILON:
        return 0.0
    return math.copysign(1.0, value) * math.log10(abs(value) + e1._HU_LOG_EPSILON)


def rotation_spread(values: list[float]) -> float:
    return max(values) - min(values)


def test_seventh_hu_invariant_is_noise_and_is_dropped(
    corpus: dict[str, np.ndarray],
) -> None:
    """The omission POLICY documents, reproduced as the measurement it quotes.

    Two independent failures, one per kind of contour.

    *A mirror-symmetric curve whose samples are not.*  ``crescent()`` reflects onto
    itself as a curve, but 128 equal-arc-length samples starting at an arbitrary point
    do not, and ``I7`` reports ``-2.5e-8`` for it — eleven decades above the
    under-epsilon floor, so the floor does not hide it and the arm would emit ``+7.6``
    and call a symmetric shape chiral.  Denser sampling shrinks it (``1.5e-7`` at 64
    points, ``5.3e-10`` at 1024), which is what identifies it as the sampler's number
    rather than the shape's.

    *A contour whose samples are symmetric.*  ``I7`` then falls to ``1e-30``
    (five-point star), ``1e-64`` (six-point) and ``1e-32`` (pentagon) — pure round-off,
    and its *sign* changes with the rotation angle.  The floor reports those as 0.0,
    so the emitted value is stable by luck; without the floor the same unrotated shape
    spans 59.9-129.2 log10 units across the 14 angles below, which is the whole range
    of the descriptor and more.

    ``I1``-``I6`` over the same 14 rotations move by less than 3.0e-7 (worst column is
    the disc's ``I2``, the log of a genuinely tiny anisotropy).  A descriptor that
    answers "how chiral?" with a number owned by the sampler and the frame would put
    that term into every distance after stage-2 normalisation, so it is not emitted.
    """
    angles = tuple(np.arange(0.0, 360.0, 45.0)) + (7.3, 23.9, 51.2, 88.1, 137.5, 271.4)

    crescent_seventh = hu_seventh(corpus["crescent"])
    assert abs(crescent_seventh) > 1e-9, crescent_seventh
    assert hu_seventh(mirror_points(corpus["crescent"])) == pytest.approx(
        -crescent_seventh, rel=1e-6
    )  # reflection-odd, as POLICY claims for the seventh and only the seventh
    assert abs(hu_seventh(standardize(crescent(), n_points=1024))) < abs(
        crescent_seventh
    ) / 20.0
    assert abs(hu_seventh(standardize(crescent(), n_points=64))) > abs(crescent_seventh)

    for name in ("star5", "star6", "pentagon"):
        raws = [hu_seventh(rotate(corpus[name], float(angle))) for angle in angles]
        assert max(abs(value) for value in raws) < e1._HU_LOG_EPSILON, name
        assert len({(value > 0.0) - (value < 0.0) for value in raws}) > 1, name
        assert all(signed_log(value) == 0.0 for value in raws), name
        assert rotation_spread([signed_log(value, with_floor=False) for value in raws]) > 20.0

    for name, contour in corpus.items():
        six = np.stack([hu_six(rotate(contour, float(angle))) for angle in angles])
        assert float(np.max(six.max(axis=0) - six.min(axis=0))) < 1e-6, name

    emitted = list(e1.descriptor_values(corpus["star5"], groups=("moments",)))
    assert emitted == [f"mom_hu_{index + 1}" for index in range(6)]
    assert e1.HU_INVARIANTS == ("I1", "I2", "I3", "I4", "I5", "I6")


# --- ablation ------------------------------------------------------------------


def test_ablation_is_a_parameter_not_a_code_edit(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    """Every group can be turned off, and the groups disagree with each other.

    The fused distance cannot be reproduced by any single group.  The arms do not all
    hand back the same ranking either: measured on this corpus every arm still puts the
    identical contour first, but the fourier arm ranks the pentagon second where the
    global arm ranks the six-point star second — that disagreement is what the ablation
    is for, since POLICY states that the 42-column histogram block outweighs the
    10-column global block by dimension count alone.  Arm magnitudes (mean distance
    1.15 to 2.64) are *not* comparable across arms: each self-fits its own columns, so
    only the ranking within an arm means anything.
    """
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    query = corpus["star5"]
    self_row = sorted(corpus).index("star5")
    single: dict[str, np.ndarray] = {}
    for group in e1.GROUPS:
        scores, records = e1.score_library(query, rows, groups=(group,))
        assert records["applied_groups"] == [group]
        assert records["descriptors"]["count"] == e1.descriptor_counts()[group]
        assert np.isfinite(scores).all()
        assert scores[self_row] == pytest.approx(0.0, abs=1e-12), group
        single[group] = scores
    fused, records = e1.score_library(query, rows)
    assert records["descriptors"]["per_group"] == {
        **{group: e1.descriptor_counts()[group] for group in e1.GROUPS},
        "total": 91,
    }
    for group in e1.GROUPS:
        assert not np.allclose(fused, single[group])
    assert set(single) == set(e1.GROUPS)
    orders = {
        group: tuple(perceptual_methods.rank_order(values))
        for group, values in single.items()
    }
    assert len(set(orders.values())) > 1, orders
    for group, values in single.items():
        assert int(values.argmin()) == self_row, group


def test_rankings_are_deterministic_and_dispatched(
    corpus: dict[str, np.ndarray], normalizer: e1.FeatureNormalizer
) -> None:
    rows = np.stack([corpus[name] for name in sorted(corpus)])
    first, _ = perceptual_methods.score_library(
        "E1", corpus["star5"], rows, params={"normalizer": normalizer}
    )
    second, metadata = perceptual_methods.score_library("E1", corpus["star5"], rows)
    assert np.array_equal(first, e1.distance_library(corpus["star5"], rows, normalizer=normalizer))
    order = perceptual_methods.rank_order(first)
    assert np.array_equal(order, perceptual_methods.rank_order(first))
    assert metadata["policy"]["id"] == "E1"
    assert order[0] == sorted(corpus).index("star5")


# --- the orientation frame: what it buys, and where it gives up -----------------


def test_orientation_statuses_names_every_shape_that_has_no_axis() -> None:
    """The fallback is not the circle's peculiarity; every symmetric member hits it.

    ``ORIENTATION_ISOTROPY_MAX`` is a threshold on ``lambda2/lambda1``, and any shape
    with 3-fold rotational symmetry or better has equal principal moments, so the
    regular polygons and the stars of this project's gallery are all in the fallback
    set - which is the opposite of an edge case.  The asymmetric members keep a real
    frame, and their ratios are what makes them decidable.
    """
    shapes = {
        "circle": standardize(circle()),
        "pentagon": standardize(regular_polygon(5)),
        "star5": standardize(star()),
        "star6": standardize(star(points_count=6)),
        "crescent": standardize(crescent()),
        "chiral": standardize(irregular(3)),
    }
    rows = np.stack(list(shapes.values()))
    reports = e1.orientation_statuses(rows)
    assert [report["row"] for report in reports] == list(range(len(shapes)))
    statuses = dict(zip(shapes, [r["orientation_status"] for r in reports], strict=True))
    for name in ("circle", "pentagon", "star5", "star6"):
        assert statuses[name] == "isotropic-fallback", name
    for name in ("crescent", "chiral"):
        assert statuses[name] == "canonical", name
    ratios = dict(zip(shapes, [r["moment_axis_ratio"] for r in reports], strict=True))
    assert ratios["star5"] >= e1.ORIENTATION_ISOTROPY_MAX
    assert ratios["chiral"] < 0.8 and ratios["crescent"] < 0.4
    assert all("orientation_angle_deg" in report for report in reports)
    # orientation="raw" never claims a frame was found
    raw = e1.orientation_statuses(rows, orientation="raw")
    assert {report["orientation_status"] for report in raw} == {"raw"}


def test_the_frame_buys_invariance_where_an_axis_exists_and_nothing_where_it_does_not() -> None:
    """One pair per case, measured both ways, so POLICY cannot over- or under-claim.

    For the chiral blob the canonical frame is the whole mechanism: the same query
    against its own 37-degree rotation goes from 1.3434 in ``orientation="raw"`` to
    exactly 0.0000.  For ``star(5)`` the two numbers are the same within noise
    (4.8047 canonical, 4.6945 raw) because there is no axis to align to, so the frame
    silently degrades to "raw" and the 42-column tangent block loses invariance.  That
    is stated in POLICY["known_limitations"], and this test is the measurement behind
    it - the arm is not broken, the shape has no principal axis.
    """
    disk = standardize(circle())
    cres = standardize(crescent())
    pent = standardize(regular_polygon(5))
    star5 = standardize(star())
    chiral = standardize(irregular(3))
    star_rows = np.stack([rotate(star5, 37.0), disk, cres, pent, chiral])
    chiral_rows = np.stack([rotate(chiral, 37.0), disk, pent, star5, cres])
    canonical_star, _ = e1.score_library(star5, star_rows)
    raw_star, _ = e1.score_library(star5, star_rows, orientation="raw")
    canonical_chiral, _ = e1.score_library(chiral, chiral_rows)
    raw_chiral, _ = e1.score_library(chiral, chiral_rows, orientation="raw")
    assert canonical_chiral[0] == pytest.approx(0.0, abs=1e-12)
    assert raw_chiral[0] > 0.5, raw_chiral[0]
    assert canonical_star[0] > 3.0, canonical_star[0]
    assert abs(canonical_star[0] - raw_star[0]) < 0.5, (canonical_star[0], raw_star[0])
    assert int(canonical_star.argmin()) == 0
    assert "isotropic_shapes_lose_rotation_invariance" in e1.POLICY["known_limitations"]
