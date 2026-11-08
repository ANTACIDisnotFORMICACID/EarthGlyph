"""Unit tests for the P0-4.6 metric and probe modules.

These run offline: no source cache, no network. The invariants that matter are the ones the
benchmark's honesty rests on — participant-level resampling, sign conventions, and probes
that are genuinely valid closed rings rather than self-intersecting artefacts.
"""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Polygon

from earthglyph.shape_benchmark import metrics
from earthglyph.shape_benchmark.probes import (
    PROBE_BUILDERS,
    TRANSFORMS,
    build_structure_probe,
    make_probe,
    mirror,
    rotate,
    shift_start,
)

# --------------------------------------------------------------------------- correlations


def test_correlate_sign_carries_direction() -> None:
    distance = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    similarity = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    result = metrics.correlate(distance, similarity)
    assert result.spearman == pytest.approx(-1.0)
    assert result.n == 5
    assert metrics.correlate(distance, -similarity).spearman == pytest.approx(1.0)


def test_correlate_rejects_mismatched_and_degenerate_inputs() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        metrics.correlate(np.arange(4.0), np.arange(3.0))
    with pytest.raises(ValueError, match="at least 3"):
        metrics.correlate(np.array([1.0, 2.0]), np.array([1.0, 2.0]))


def test_two_level_bootstrap_is_wider_when_observers_disagree() -> None:
    """One dissenting observer must widen the interval; a pairs-only bootstrap cannot see it."""
    rng = np.random.default_rng(7)
    n_pairs, n_observers = 120, 8
    truth = rng.uniform(0.0, 1.0, n_pairs)
    distance = truth + rng.normal(0.0, 0.05, n_pairs)
    ratings = np.repeat(truth[:, None], n_observers, axis=1)
    ratings += rng.normal(0.0, 0.02, ratings.shape)
    # observer 0 answers a different question entirely
    ratings[:, 0] = rng.uniform(0.0, 1.0, n_pairs)

    pairs_only = metrics.bootstrap_correlation(distance, ratings.mean(axis=1), n_boot=400, seed=11)
    two_level = metrics.bootstrap_two_level(distance, ratings, n_boot=400, seed=11)
    pairs_width = pairs_only["spearman_ci_high"] - pairs_only["spearman_ci_low"]
    two_width = two_level["spearman_ci_high"] - two_level["spearman_ci_low"]
    assert two_width > pairs_width
    assert two_level["scheme"] == "pairs+observers"
    assert two_level["valid_replicates"] == 400


def test_bootstrap_is_reproducible_for_a_fixed_seed() -> None:
    rng = np.random.default_rng(3)
    x = rng.uniform(size=60)
    y = x + rng.normal(0.0, 0.2, 60)
    first = metrics.bootstrap_correlation(x, y, n_boot=200, seed=99)
    second = metrics.bootstrap_correlation(x, y, n_boot=200, seed=99)
    assert first == second


def test_paired_difference_detects_a_gap_the_marginal_intervals_miss() -> None:
    """Two arms on the same pairs: the difference is paired, so it can resolve a small gap."""
    rng = np.random.default_rng(17)
    n_pairs, n_observers = 150, 6
    similarity = rng.uniform(0.0, 1.0, n_pairs)
    dissimilarity = 1.0 - similarity
    good = dissimilarity + rng.normal(0.0, 0.05, n_pairs)
    weak = dissimilarity + rng.normal(0.0, 0.45, n_pairs)
    ratings = np.repeat(similarity[:, None], n_observers, axis=1)
    ratings += rng.normal(0.0, 0.02, ratings.shape)

    result = metrics.bootstrap_agreement_difference(good, weak, ratings, n_boot=400, seed=5)
    assert result["observed_difference"] > 0.0
    assert result["excludes_zero"]
    assert result["share_positive"] > 0.95

    null = metrics.bootstrap_agreement_difference(good, good, ratings, n_boot=400, seed=5)
    assert null["observed_difference"] == pytest.approx(0.0)
    assert not null["excludes_zero"]


def test_paired_difference_rejects_misaligned_inputs() -> None:
    with pytest.raises(ValueError, match="must align"):
        metrics.bootstrap_agreement_difference(
            np.arange(5.0), np.arange(4.0), np.ones((5, 3)), n_boot=10
        )


# --------------------------------------------------------------------------- observers


def test_observer_agreement_is_one_for_identical_observers() -> None:
    column = np.linspace(0.0, 1.0, 40)
    ratings = np.repeat(column[:, None], 5, axis=1)
    result = metrics.observer_agreement(ratings)
    assert result["pairwise_summary"]["n"] == 10
    assert result["pairwise_summary"]["median"] == pytest.approx(1.0)
    assert result["leave_one_out_summary"]["median"] == pytest.approx(1.0)


def test_observer_agreement_leave_one_out_excludes_self() -> None:
    """An observer correlated with a mean containing themselves would be inflated."""
    rng = np.random.default_rng(5)
    ratings = rng.uniform(size=(50, 4))
    result = metrics.observer_agreement(ratings)
    for row in result["leave_one_out"]:
        observer = int(row["observer"])
        others = np.delete(ratings, observer, axis=1).mean(axis=1)
        expected = metrics.correlate(others, ratings[:, observer]).spearman
        assert float(row["spearman"]) == pytest.approx(expected)


def test_observer_agreement_needs_two_observers() -> None:
    with pytest.raises(ValueError, match="at least two observers"):
        metrics.observer_agreement(np.ones((10, 1)))


def test_observer_summary_reports_the_spread() -> None:
    rows = [{"spearman": v} for v in (-0.4, -0.6, -0.8)]
    summary = metrics.observer_summary(rows)
    assert summary["n_observers"] == 3
    assert summary["spearman_min"] == pytest.approx(-0.8)
    assert summary["spearman_median"] == pytest.approx(-0.6)
    assert summary["spearman_max"] == pytest.approx(-0.4)


# --------------------------------------------------------------------------- retrieval


def test_class_recall_at_k_counts_only_the_query_class() -> None:
    categories = ["heart", "heart", "bat", None, "heart"]
    assert metrics.class_recall_at_k(categories, "heart", 3) == pytest.approx(2 / 3)
    assert metrics.class_recall_at_k(categories, "heart", 5) == pytest.approx(3 / 5)
    assert metrics.class_recall_at_k([], "heart", 5) == 0.0
    with pytest.raises(ValueError, match="query category"):
        metrics.class_recall_at_k(categories, None, 3)


def test_top_k_overlap_is_jaccard_not_intersection_size() -> None:
    a = np.array([0, 1, 2, 3])
    b = np.array([0, 1, 4, 5])
    assert metrics.top_k_overlap(a, b, 2) == pytest.approx(1.0)
    assert metrics.top_k_overlap(a, b, 4) == pytest.approx(2 / 6)
    assert metrics.top_k_overlap(a, a, 4) == pytest.approx(1.0)


def test_rank_shift_reports_absence_as_none() -> None:
    a = np.array([5, 6, 7])
    b = np.array([7, 5, 6])
    assert metrics.rank_shift(a, b, 5) == 1
    assert metrics.rank_shift(a, b, 7) == -2
    assert metrics.rank_shift(a, b, 99) is None


# --------------------------------------------------------------------------- probes


@pytest.mark.parametrize("name", sorted(PROBE_BUILDERS))
def test_every_probe_is_a_valid_closed_ring(name: str) -> None:
    shape = make_probe(name)
    assert shape.points.shape == (128, 2)
    assert Polygon(shape.points).is_valid
    assert shape.source == "synthetic_probe"


@pytest.mark.parametrize("kind", ["star", "heart", "crescent", "forked"])
def test_structure_probe_has_three_variants_and_three_distractors(kind: str) -> None:
    probe = build_structure_probe(kind)
    assert len(probe.structural_variants) == 3
    assert len(probe.gross_distractors) == 3
    ids = [s.shape_id for s in [probe.query, *probe.structural_variants, *probe.gross_distractors]]
    assert len(set(ids)) == len(ids)
    for shape in [*probe.structural_variants, *probe.gross_distractors]:
        assert Polygon(shape.points).is_valid, shape.shape_id


def test_natural_probe_requires_a_real_source_outline() -> None:
    with pytest.raises(ValueError, match="real source outline"):
        build_structure_probe("natural")
    source = make_probe("blob", seed=2)
    probe = build_structure_probe("natural", natural=source)
    assert probe.query.shape_id == f"probe_natural_{source.shape_id}"
    assert np.array_equal(probe.query.points, source.points)


# --------------------------------------------------------------------------- transforms


def test_rotation_and_start_shift_preserve_the_point_set() -> None:
    points = make_probe("star").points
    rotated = rotate(points, 37.0)
    assert np.allclose(np.linalg.norm(rotated, axis=1), np.linalg.norm(points, axis=1))
    shifted = shift_start(points, 0.37)
    assert np.allclose(np.sort(shifted, axis=0), np.sort(points, axis=0))


def test_mirror_reverses_winding() -> None:
    points = make_probe("heart").points
    assert Polygon(points).exterior.is_ccw
    assert not Polygon(mirror(points)).exterior.is_ccw


def test_transform_sweep_covers_the_required_families() -> None:
    assert set(TRANSFORMS) == {
        "rotate_37", "rotate_90", "rotate_191",
        "scale_up", "scale_down", "translate",
        "start_shift", "deform_local", "mirror",
    }
    points = make_probe("forked").points
    for name, transform in TRANSFORMS.items():
        moved = np.asarray(transform(points), dtype=float)
        assert moved.shape == points.shape, name
        assert np.isfinite(moved).all(), name