"""Tests for the comparison contract itself, and for the E0 adapter.

Checkpoint B's brief has two halves.  One is "add E1/E2/E3"; the other is "leave
E0's default behaviour alone", and neither half is worth much unless the four arms
really are comparable.  So this file tests the parts that are *not* under
experiment: the shared input validation, the tie-break rule that makes a ranking
table reproducible (the D42 rule), the dispatch table, and the claim that E0 is
the P0-4 pipeline called the same way the product calls it.

A test here never asserts that one arm beats another — that is the experiment's
job, and pre-judging it in the contract is how a comparison becomes a demo.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

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
from earthglyph.retrieval import perceptual_e0 as e0
from earthglyph.retrieval import perceptual_methods as contract
from earthglyph.retrieval.alignment import align, mirror_points
from earthglyph.retrieval.search import search_by_contour

ARMS = ("E0", "E1", "E2", "E3")


@pytest.fixture(scope="module")
def library() -> dict[str, np.ndarray]:
    return {
        "star5": standardize(star()),
        "circle": standardize(circle()),
        "crescent": standardize(crescent()),
        "pentagon": standardize(regular_polygon(5)),
        "chiral": standardize(irregular(3)),
    }


def rows_from(names: list[str], library: dict[str, np.ndarray]) -> np.ndarray:
    return np.stack([library[name] for name in names])


def rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(float(angle_deg))
    matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ matrix.T, dtype=float)


# --- input validation ----------------------------------------------------------


def test_check_contour_accepts_a_standardized_ring(library: dict[str, np.ndarray]) -> None:
    array = contract.check_contour(library["star5"])
    assert array.shape == (contract.CONTOUR_POINTS, 2)
    assert array.dtype == np.float64
    # a list of pairs is a contour too; the contract is about content, not container
    assert np.array_equal(contract.check_contour(array.tolist()), array)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (np.zeros((128,)), r"\(n, 2\) array"),
        (np.zeros((128, 3)), r"\(n, 2\) array"),
        (np.zeros((3, 2)), "cannot be a ring"),
        (np.full((128, 2), np.nan), "non-finite"),
        (np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 0.0], [np.inf, 0.0]]), "non-finite"),
    ],
)
def test_check_contour_refuses_shapes_that_are_not_rings(bad: np.ndarray, reason: str) -> None:
    with pytest.raises(contract.PerceptualMethodError, match=reason):
        contract.check_contour(bad, "query")


def test_contour_stack_refuses_mixed_sampling_and_an_empty_library(
    library: dict[str, np.ndarray],
) -> None:
    with pytest.raises(contract.PerceptualMethodError, match="is empty"):
        contract.contour_stack([])
    mixed = [library["star5"], standardize(star(points_count=5), n_points=100)]
    with pytest.raises(contract.PerceptualMethodError, match="mixes sample counts"):
        contract.contour_stack(mixed)
    stacked = contract.contour_stack([library["star5"], library["circle"]])
    assert stacked.shape == (2, contract.CONTOUR_POINTS, 2)


# --- the ranking rule ----------------------------------------------------------


def test_rank_order_breaks_ties_by_library_row_order() -> None:
    """Two identical scores must not swap between runs — that is the D42 rule."""
    distances = np.array([0.5, 0.1, 0.5, 0.2, 0.5])
    order = contract.rank_order(distances)
    assert order.tolist() == [1, 3, 0, 2, 4]
    for _ in range(20):
        assert np.array_equal(contract.rank_order(distances), order)


def test_rank_order_refuses_a_matrix_and_a_nan() -> None:
    with pytest.raises(contract.PerceptualMethodError, match="1-D"):
        contract.rank_order(np.zeros((2, 3)))
    with pytest.raises(contract.PerceptualMethodError, match="2 distances are not finite"):
        contract.rank_order(np.array([0.1, np.nan, 0.2, np.inf]))


# --- the dispatch table --------------------------------------------------------


def test_every_registered_arm_implements_the_same_five_names() -> None:
    for method_id, dotted in contract.METHOD_MODULES.items():
        module = importlib.import_module(dotted)
        assert module.__name__ == dotted
        for name in ("distance", "distance_library", "score_library", "POLICY", "describe_policy"):
            assert hasattr(module, name), f"{method_id} is missing {name}"
        assert module.POLICY["id"] == method_id
        assert module.describe_policy()["id"] == method_id
        assert module.describe_policy() is not module.POLICY


def test_describe_policy_returns_a_fresh_top_level_dict() -> None:
    """Each arm copies the top level; only E3 rebuilds the block it overrides.

    ``POLICY`` is documentation a results table prints, not state a run writes, so the
    guarantee here is deliberately the shallow one: a runner cannot rename an arm by
    writing through the copy.  E3 replaces the rotation prose to match its orientation
    mode and so builds a new ``invariance`` dict; the other arms share the nested block,
    which is why the experiment code reads policies and never annotates them.
    """
    for method_id in ARMS:
        module = importlib.import_module(contract.METHOD_MODULES[method_id])
        policy = module.describe_policy()
        assert policy is not module.POLICY
        policy["id"] = "written by a test"
        assert module.POLICY["id"] == method_id
    e3 = importlib.import_module(contract.METHOD_MODULES["E3"])
    assert e3.describe_policy()["invariance"] is not e3.POLICY["invariance"]


def test_an_unknown_arm_is_named_alongside_the_ones_that_exist() -> None:
    with pytest.raises(contract.PerceptualMethodError, match="unknown method"):
        contract.score_library("E7", np.zeros((128, 2)), [np.zeros((128, 2))])


def test_dispatch_calls_the_module_it_names(library: dict[str, np.ndarray]) -> None:
    names = sorted(library)
    rows = rows_from(names, library)
    query = library["star5"]
    for method_id in ARMS:
        module = importlib.import_module(contract.METHOD_MODULES[method_id])
        dispatched, meta = contract.score_library(method_id, query, rows)
        direct, direct_meta = module.score_library(query, rows)
        assert np.array_equal(dispatched, direct), method_id
        assert meta["policy"]["id"] == method_id
        assert len(meta["transforms"]) == len(names)


# --- E0 stays the P0-4 pipeline ------------------------------------------------


def test_the_e0_adapter_reports_what_its_own_align_call_reports(
    library: dict[str, np.ndarray],
) -> None:
    """Not "similar to": the adapter must add no computation of its own."""
    query, candidate = library["star5"], library["crescent"]
    shipped = align(query, candidate)
    distance, fitted = e0.distance(query, candidate)
    assert distance == pytest.approx(float(shipped.distance), rel=0.0, abs=1e-15)
    assert fitted["shift"] == int(shipped.shift)
    assert fitted["theta_deg"] == pytest.approx(float(np.degrees(shipped.theta_rad)), abs=1e-12)
    assert fitted["mirrored"] is bool(shipped.mirrored)
    assert fitted["ambiguous_rotations"] == int(shipped.alternatives)


def test_the_e0_adapter_declares_itself_old_code() -> None:
    assert e0.POLICY["new_code_in_this_checkpoint"] is False
    assert "shortlist_size=200" in e0.POLICY["coarse_window"]


def test_the_product_search_path_still_defaults_to_the_p0_4_settings() -> None:
    """The brief's "E0 default behaviour unchanged", asserted rather than promised.

    search_by_contour is the product entry point; if Checkpoint B had quietly
    retuned it to make the new arms look good, the defaults below would move.
    """
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(search_by_contour).parameters.items()
    }
    assert defaults["top_k"] == 10
    assert defaults["shortlist_size"] == 200
    assert defaults["method"] == "fourier_align"
    assert defaults["allow_mirror"] is False
    assert defaults["smoothing"] == "light"


def test_e0_mirror_switch_off_by_default_and_on_by_request(library: dict[str, np.ndarray]) -> None:
    chiral = library["chiral"]
    reflected = mirror_points(chiral)
    off, off_fit = e0.distance(chiral, reflected)
    on, on_fit = e0.distance(chiral, reflected, allow_mirror=True)
    assert off_fit["mirrored"] is False and on_fit["mirrored"] is True
    assert on <= off + 1e-12
    assert inspect.signature(e0.distance).parameters["allow_mirror"].default is False


# --- what the four arms agree on before any experiment -------------------------


def test_every_arm_puts_the_query_itself_first(library: dict[str, np.ndarray]) -> None:
    names = sorted(library)
    rows = rows_from(names, library)
    query = library["star5"]
    self_row = names.index("star5")
    for method_id in ARMS:
        scores, _meta = contract.score_library(method_id, query, rows)
        assert np.isfinite(scores).all(), method_id
        assert contract.rank_order(scores)[0] == self_row, method_id


def test_every_arm_prefers_a_rotated_copy_of_itself_over_another_shape(
    library: dict[str, np.ndarray],
) -> None:
    """"Smaller is closer" has to mean this much, or the columns are not distances.

    A 37-degree rotation is inside every arm's own story (E0 solved in closed form, E2
    searched as a cyclic offset, E3 searched on a grid, E1 a descriptor set), so all
    four must put it ahead of four unrelated contours.  Nothing here says how near —
    the next test prices that.
    """
    query = library["star5"]
    turned = rotate(query, 37.0)
    rows = np.stack(
        [turned, library["circle"], library["crescent"], library["pentagon"], library["chiral"]]
    )
    for method_id in ARMS:
        scores, _meta = contract.score_library(method_id, query, rows)
        assert int(scores.argmin()) == 0, (method_id, scores.tolist())
        assert scores[0] < max(scores[1:]), method_id


def test_the_rotated_copy_residual_separates_the_arms_and_matches_their_policies(
    library: dict[str, np.ndarray],
) -> None:
    """The same pair, four numbers, and each one is what its POLICY says it will be.

    Measured on this pair: E0 and E2 give exactly 0.0000 (their rotation handling is
    closed-form or arc-length based), E3 gives 0.1405 (its grid step is 15 degrees, so
    half a step of residual is the price of a *searched* invariance), and E1 gives
    4.8047 — not because E1 lost the rotation but because a 5-fold star has no principal
    axis (lambda2/lambda1 = 0.99869 >= 0.98), so the 42-column tangent block is
    measured against static bin edges.  E1 states exactly that in
    POLICY["known_limitations"]["isotropic_shapes_lose_rotation_invariance"], and the
    assertion below is what stops the claim from being softer than the number.
    """
    query = library["star5"]
    rows = np.stack(
        [
            rotate(query, 37.0),
            library["circle"],
            library["crescent"],
            library["pentagon"],
            library["chiral"],
        ]
    )
    residual: dict[str, float] = {}
    for method_id in ARMS:
        scores, _meta = contract.score_library(method_id, query, rows)
        residual[method_id] = float(scores[0])
    assert residual["E0"] == pytest.approx(0.0, abs=1e-12)
    assert residual["E2"] == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < residual["E3"] < 0.25, residual["E3"]
    assert residual["E1"] > 10.0 * residual["E3"], (residual["E1"], residual["E3"])
    limitation = importlib.import_module(contract.METHOD_MODULES["E1"])
    assert "isotropic_shapes_lose_rotation_invariance" in limitation.POLICY["known_limitations"]


def test_arms_are_reachable_with_a_plain_list_of_rows(library: dict[str, np.ndarray]) -> None:
    """The contract takes any sequence; a runner does not have to pre-stack."""
    query = library["star5"]
    stacked_rows = rows_from(sorted(library), library)
    stacked, _ = contract.score_library("E2", query, stacked_rows)
    as_list, _ = contract.score_library("E2", query, list(stacked_rows))
    assert np.array_equal(stacked, as_list)


@pytest.mark.parametrize("method_id", ARMS)
def test_an_arm_refuses_a_query_that_is_not_a_ring(
    method_id: str, library: dict[str, np.ndarray]
) -> None:
    rows = rows_from(sorted(library), library)
    with pytest.raises(contract.PerceptualMethodError):
        contract.score_library(method_id, np.zeros((2, 2)), rows)


def test_a_policy_statement_covers_every_invariance_the_runner_might_assume() -> None:
    """No arm may be silent about the four things a results table prints."""
    for method_id in ARMS:
        module: Any = importlib.import_module(contract.METHOD_MODULES[method_id])
        invariance = module.POLICY["invariance"]
        for key in ("translation", "uniform_scale", "rotation", "mirror"):
            assert key in invariance, (method_id, key)
            assert isinstance(invariance[key], str) and len(invariance[key]) > 10, (
                method_id,
                key,
            )
