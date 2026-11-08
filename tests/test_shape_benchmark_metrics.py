"""Unit tests for the P0-4.6 metric and probe modules.

These run offline: no source cache, no network. The invariants that matter are the ones the
benchmark's honesty rests on — participant-level resampling, sign conventions, and probes
that are genuinely valid closed rings rather than self-intersecting artefacts. One test audits
the committed d5/d5b artefacts against each other; it skips when they have not been generated.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

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

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "experiments" / "shape_perception_benchmark" / "results"


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


# --------------------------------------------------------------------------- grouped repeats


def sweep_design(
    seed: int = 13,
    n_pairs: int = 60,
    angles: tuple[float, ...] = (0.0, 30.0, 90.0, 150.0),
    n_observers: int = 6,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    """One row per (rated pair, angle) — the shape benchmark's rotation-sweep design.

    The signal lives at the *stimulus* level; the angles only add noise. Two arms differ in how
    much they keep that stimulus signal under rotation.
    """
    rng = np.random.default_rng(seed)
    similarity = rng.uniform(0.0, 1.0, n_pairs)
    panel = np.repeat(similarity[:, None], n_observers, axis=1)
    panel += rng.normal(0.0, 0.05, panel.shape)
    n_rows = n_pairs * len(angles)
    distances = {
        name: (1.0 - np.tile(similarity, len(angles))) + rng.normal(0.0, noise, n_rows)
        for name, noise in (("strong", 0.03), ("weak", 0.35))
    }
    ratings = np.tile(panel, (len(angles), 1))
    groups = np.tile(np.arange(n_pairs), len(angles))
    conditions = np.repeat(np.asarray(angles), n_pairs)
    return distances, ratings, groups, conditions


def test_grouped_bootstrap_widens_the_interval_that_double_counted_stimuli() -> None:
    """Rows of one stimulus are repeated measures, not independent observations."""
    distances, ratings, groups, _ = sweep_design()
    naive = metrics.bootstrap_two_level(distances["strong"], ratings, n_boot=400, seed=4)
    grouped = metrics.bootstrap_grouped_two_level(
        distances, ratings, groups, n_boot=400, seed=4
    )
    entry = grouped["per_arm"]["strong"]
    naive_width = naive["spearman_ci_high"] - naive["spearman_ci_low"]
    grouped_width = entry["spearman_ci_high"] - entry["spearman_ci_low"]
    assert grouped_width > naive_width
    assert grouped["n_clusters"] == 60
    assert grouped["rows_per_cluster"] == 4
    assert grouped["n_rows"] == 240
    assert grouped["scheme"] == "clusters(stimuli)+observers, condition factor fixed"
    assert "pre-declared design set" in grouped["condition_factor"]
    assert entry["valid_replicates"] == 400


def test_grouped_bootstrap_scores_every_arm_on_the_same_replicate() -> None:
    """One shared replicate stream: a duplicated arm must differ from its twin by exactly zero."""
    distances, ratings, groups, conditions = sweep_design()
    distances = dict(distances, twin=distances["strong"])
    kwargs = dict(n_boot=400, seed=4)
    result = metrics.bootstrap_grouped_two_level(distances, ratings, groups, **kwargs)
    delta = result["differences"]["strong|weak"]
    # strong tracks the panel more closely, so it sits further from zero on the *negative*
    # distance-vs-rating scale; as agreement the same gap reads positive
    assert delta["mean"] < 0.0
    assert delta["excludes_zero"]
    assert delta["n_valid_replicates"] == 400
    twin = result["differences"]["strong|twin"]
    assert twin["mean"] == 0.0
    assert twin["std"] == 0.0
    assert not twin["excludes_zero"]
    assert result == metrics.bootstrap_grouped_two_level(distances, ratings, groups, **kwargs)
    assert result != metrics.bootstrap_grouped_two_level(
        distances, ratings, groups, condition_ids=conditions, resample_conditions=True, **kwargs
    )


def test_grouped_bootstrap_treats_the_condition_as_random_when_asked() -> None:
    """Angles that differ in difficulty must add variance once the angle set itself is sampled."""
    rng = np.random.default_rng(29)
    n_pairs, angles = 45, (0.0, 90.0)
    similarity = rng.uniform(0.0, 1.0, n_pairs)
    panel = np.repeat(similarity[:, None], 5, axis=1)
    distance = np.concatenate(
        [
            1.0 - similarity + rng.normal(0.0, 0.02, n_pairs),
            1.0 - similarity + rng.normal(0.0, 0.55, n_pairs),
        ]
    )
    ratings = np.tile(panel, (len(angles), 1))
    groups = np.tile(np.arange(n_pairs), len(angles))
    conditions = np.repeat(np.asarray(angles), n_pairs)
    kwargs = dict(n_boot=400, seed=8)
    fixed = metrics.bootstrap_grouped_two_level({"a": distance}, ratings, groups, **kwargs)
    randomised = metrics.bootstrap_grouped_two_level(
        {"a": distance}, ratings, groups, condition_ids=conditions, resample_conditions=True,
        **kwargs,
    )

    fixed_width = (
        fixed["per_arm"]["a"]["spearman_ci_high"] - fixed["per_arm"]["a"]["spearman_ci_low"]
    )
    random_width = (
        randomised["per_arm"]["a"]["spearman_ci_high"]
        - randomised["per_arm"]["a"]["spearman_ci_low"]
    )
    assert randomised["scheme"] == "clusters(stimuli)+conditions+observers, all three resampled"
    assert "drawn with replacement" in randomised["condition_factor"]
    assert random_width > fixed_width


def test_grouped_bootstrap_accepts_a_ragged_design() -> None:
    """Unequal repeat counts are legal; the report must then refuse a single rows_per_cluster."""
    groups = np.array([0, 0, 0, 1, 1, 2])
    similarity = np.array([0.9, 0.55, 0.2])
    panel = np.repeat(similarity[:, None], 3, axis=1)
    ratings = panel[groups] + np.linspace(0.0, 0.01, len(groups))[:, None]
    distance = 1.0 - similarity[groups] + np.array([0.0, 0.1, -0.1, 0.05, -0.05, 0.02])
    result = metrics.bootstrap_grouped_two_level({"a": distance}, ratings, groups, n_boot=40)
    assert result["rows_per_cluster"] is None
    assert (result["cluster_size_min"], result["cluster_size_max"]) == (1, 3)
    assert result["n_clusters"] == 3
    # three clusters is tiny: a replicate can draw only the one-row cluster, which has no
    # variance and is dropped, so the valid count is at most n_boot rather than equal to it
    assert 35 <= result["per_arm"]["a"]["valid_replicates"] <= 40


def test_grouped_bootstrap_refuses_a_replicate_with_no_variance() -> None:
    groups = np.array([0, 0, 1, 1])
    with pytest.raises(ValueError, match="no variance to resample"):
        metrics.bootstrap_grouped_two_level(
            {"a": np.ones(4)}, np.ones((4, 2)), groups, n_boot=10
        )


def test_grouped_bootstrap_rejects_misaligned_and_non_crossed_inputs() -> None:
    groups = np.array([0, 0, 1, 1])
    rows = np.ones((4, 2))
    distance = {"a": np.arange(4.0)}
    with pytest.raises(ValueError, match="rating rows must align"):
        metrics.bootstrap_grouped_two_level(distance, np.ones((5, 2)), groups, n_boot=5)
    with pytest.raises(ValueError, match="same rows"):
        metrics.bootstrap_grouped_two_level({"a": np.arange(3.0)}, rows, groups, n_boot=5)
    with pytest.raises(ValueError, match="resample_conditions needs condition_ids"):
        metrics.bootstrap_grouped_two_level(
            distance, rows, groups, resample_conditions=True, n_boot=5
        )
    with pytest.raises(ValueError, match="condition_ids must align"):
        metrics.bootstrap_grouped_two_level(
            distance,
            rows,
            groups,
            condition_ids=np.array([0.0, 1.0, 2.0]),
            resample_conditions=True,
            n_boot=5,
        )
    with pytest.raises(ValueError, match="not crossed"):
        metrics.bootstrap_grouped_two_level(
            distance,
            rows,
            groups,
            condition_ids=np.array([0.0, 0.0, 1.0, 1.0]),
            resample_conditions=True,
            n_boot=5,
        )
    with pytest.raises(ValueError, match="incomplete"):
        metrics.bootstrap_grouped_two_level(
            distance,
            rows,
            groups,
            condition_ids=np.array([0.0, 1.0, 0.0, 2.0]),
            resample_conditions=True,
            n_boot=5,
        )


@pytest.mark.skipif(
    not (RESULTS / "d5_grouped_metrics.json").exists(),
    reason="the d5b re-analysis has not been run in this checkout",
)
def test_d5b_artefacts_agree_with_the_committed_d5_values() -> None:
    """A re-analysis that moves no number must also leave no committed file touched."""
    grouped = json.loads((RESULTS / "d5_grouped_metrics.json").read_text(encoding="utf-8"))
    committed = json.loads((RESULTS / "d5_rotation_metrics.json").read_text(encoding="utf-8"))
    assert grouped["reproduction"]["passed"] is True
    assert grouped["reproduction"]["max_abs_deviation_vs_committed"] < 1e-6

    source = committed["agreement_pooled_by_arm"]
    assert set(grouped["by_arm"]) == set(source)
    assert (grouped["n_clusters"], grouped["rows_per_cluster"]) == (250, 7)
    assert grouped["n_rows"] == 250 * len(grouped["angles_deg"])
    for arm, entry in grouped["by_arm"].items():
        assert entry["agreement_pooled"] == pytest.approx(source[arm]["agreement_pooled"])
        assert entry["naive_ci_low"] == pytest.approx(source[arm]["ci_low"], abs=1e-6)
        assert entry["naive_ci_high"] == pytest.approx(source[arm]["ci_high"], abs=1e-6)
        assert entry["grouped_ci_width"] > entry["naive_ci_width"]
        assert entry["point_estimate_changed"] is False

    arms = len(grouped["by_arm"])
    assert len(grouped["paired_differences"]) == arms * (arms - 1) // 2
    assert grouped["audit_relation"]["d5_outputs_modified"] is False
    for item in grouped["audit_relation"]["read_only_sources"]:
        path = REPO / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert path.stat().st_size == item["bytes"]
    for key, entry in grouped["paired_differences"].items():
        first, second = key.split("|", 1)
        ordering = (
            grouped["by_arm"][first]["agreement_pooled"]
            - grouped["by_arm"][second]["agreement_pooled"]
        )
        delta = entry["grouped_fixed_angle"]["delta_agreement"]
        assert np.sign(delta) == np.sign(ordering), key

    with (RESULTS / "d5_grouped_results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    csv_sha = grouped["audit_relation"]["read_only_sources"][0]["sha256"]
    assert rows and all(row["d5_csv_sha256"] == csv_sha for row in rows)
    assert {row["scheme"] for row in rows} == {
        "naive_rows_as_independent",
        "grouped_pairs_fixed_angle",
        "grouped_pairs_angle_randomised",
    }
    intervals = [row for row in rows if row["record_type"] == "arm_interval"]
    assert len(intervals) == 3 * arms
    assert all(float(row["ci_width"]) > 0.0 for row in rows)


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