"""Tests for the CP8-C independent run (P0-4.6 Checkpoint 8).

CP8-B froze a sample; CP8-C scores it. What can go wrong here is not arithmetic -- it is the run
quietly scoring a different study: a query swapped, an angle redrawn, an arm invented, the 0-degree
control pooled back into a rotation headline (the CP8-A defect), a Precision published as a Recall,
or a 24-cluster test described as if it were 120 independent measurements. Each of those has a test
below, and the arithmetic tests recompute by hand or with plain numpy instead of asking the function
that produced the number to agree with itself.

Nothing in this file runs an encoder over the archive; the heavyweight path is exercised by
``--stage cp8`` itself, and its published artefacts are checked in the last section.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from earthglyph.shape_benchmark import metrics
from earthglyph.shape_benchmark.datasets import BenchmarkShape
from earthglyph.shape_benchmark.independent_validation import (
    CP8_ANGLE_SLOTS,
    CP8_ARMS,
    CP8_CONTROL_SLOT,
    CP8_SCHEMA,
    CP8_SEED,
    CP8_TOP_K,
    design_content_sha256,
)
from earthglyph.shape_benchmark.retrieval import Gallery

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "experiments" / "shape_perception_benchmark"
DESIGN_PATH = BENCH / "checkpoint8" / "independent_validation_design.json"
OUT = BENCH / "checkpoint8"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rsb() -> Any:
    return _load("run_shape_benchmark_cp8_run", ROOT / "scripts" / "run_shape_benchmark.py")


@pytest.fixture(scope="module")
def design() -> dict[str, Any]:
    return json.loads(DESIGN_PATH.read_text(encoding="utf-8"))


def _write_design(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "independent_validation_design.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the gate


def test_gate_reads_the_committed_design(rsb: Any, design: dict[str, Any]) -> None:
    checked = rsb._cp8_gate(DESIGN_PATH)
    assert checked["n_queries"] == len(design["queries"]) == 24
    assert len(checked["angle_design"]["rows"]) == 120
    assert checked["arms"]["labels"] == list(CP8_ARMS)


def test_gate_refuses_a_missing_design(rsb: Any, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="design_checkpoint8_validation.py"):
        rsb._cp8_gate(tmp_path / "nope.json")


def test_gate_refuses_a_query_that_moved(rsb: Any, tmp_path: Path) -> None:
    payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    payload["queries"][0]["stem"] = "Heart-1"  # a burned D6 query, and not the frozen pick
    with pytest.raises(SystemExit, match="content_sha256"):
        rsb._cp8_gate(_write_design(tmp_path, payload))


def test_gate_refuses_a_redrawn_angle_even_if_rehashed(rsb: Any, tmp_path: Path) -> None:
    """A rehashed design looks self-consistent, so the angles are checked against the seed too."""
    payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    payload["angle_design"]["rows"][3]["angle_deg"] = 180.0
    payload["content_sha256"] = design_content_sha256(payload)
    with pytest.raises(SystemExit, match="not what seed"):
        rsb._cp8_gate(_write_design(tmp_path, payload))


def test_gate_refuses_a_swapped_arm_even_if_rehashed(rsb: Any, tmp_path: Path) -> None:
    payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    payload["arms"]["labels"] = ["E3/canonical", *payload["arms"]["labels"][1:]]
    payload["content_sha256"] = design_content_sha256(payload)
    with pytest.raises(SystemExit, match="are not the frozen"):
        rsb._cp8_gate(_write_design(tmp_path, payload))


def test_gate_refuses_a_different_seed_or_schema_even_if_rehashed(rsb: Any, tmp_path: Path) -> None:
    for key, value in (("seed", CP8_SEED + 1), ("schema", f"{CP8_SCHEMA}/two")):
        payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
        payload[key] = value
        payload["content_sha256"] = design_content_sha256(payload)
        with pytest.raises(SystemExit, match=key):
            rsb._cp8_gate(_write_design(tmp_path, payload))


def test_gate_refuses_a_cell_count_that_does_not_follow_from_the_sample(
    rsb: Any, tmp_path: Path
) -> None:
    payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    payload["cell_arithmetic"]["n_cells_per_arm_condition"] = 72  # the D6 shape, not this design
    payload["content_sha256"] = design_content_sha256(payload)
    with pytest.raises(SystemExit, match="cell_arithmetic"):
        rsb._cp8_gate(_write_design(tmp_path, payload))


def test_gate_reports_every_problem_at_once(rsb: Any, tmp_path: Path) -> None:
    payload = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    payload["seed"] = 1
    payload["queries"][5]["class"] = "nonsense"
    path = _write_design(tmp_path, payload)
    with pytest.raises(SystemExit) as caught:
        rsb._cp8_gate(path)
    message = str(caught.value)
    assert "seed" in message and "content_sha256" in message


def test_run_refuses_a_top_k_the_design_did_not_promise(rsb: Any) -> None:
    with pytest.raises(SystemExit, match="top_k"):
        rsb.run_cp8(top_k=5)


def test_cli_refuses_methods_alongside_cp8(rsb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """--methods would be ignored by this stage, and an ignored flag reads as a chosen arm set."""
    monkeypatch.setattr(
        sys, "argv", ["run_shape_benchmark.py", "--stage", "cp8", "--methods", "E0"]
    )
    with pytest.raises(SystemExit, match="frozen design"):
        rsb.main()


def test_a_partial_run_cannot_land_in_the_published_directory(rsb: Any) -> None:
    assert rsb.CP8_SMOKE_DIR != rsb.CP8_OUT
    assert rsb.CP8_SMOKE_DIR.name == "_smoke"
    assert rsb.CP8_SMOKE_DIR.parent == rsb.CP8_OUT


def test_arms_are_the_frozen_d6_labels(rsb: Any) -> None:
    arms = rsb._cp8_arms(list(CP8_ARMS))
    assert [arm.label for arm in arms] == list(CP8_ARMS)
    frozen = {arm.label for arm in rsb._d6_arms(list(rsb.D6_FROZEN_METHODS))}
    assert set(CP8_ARMS) <= frozen


def test_gate_refuses_an_invented_arm(rsb: Any) -> None:
    with pytest.raises(SystemExit, match="not frozen D6 arms"):
        rsb._cp8_arms(["E0", "E9/new"])


def test_toolbox_reports_blocked_arms_instead_of_dropping_them(
    rsb: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    arms = rsb._cp8_arms(list(CP8_ARMS))

    def blocked() -> None:
        raise OSError("no toolbox")

    monkeypatch.setattr(rsb, "_load_shapecomp_nets", blocked)
    with pytest.raises(SystemExit, match="blocked"):
        rsb._cp8_toolbox(arms)


# ---------------------------------------------------------------- cell arithmetic, by hand


def _ring(phase: float = 0.0, radius: float = 1.0) -> np.ndarray:
    angles = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False) + phase
    return np.stack([radius * np.cos(angles), radius * np.sin(angles)], axis=1)


def _shape(stem: str, category: str) -> BenchmarkShape:
    return BenchmarkShape(
        shape_id=f"mpeg7_{stem}",
        source="test",
        points=_ring(),
        display_points=_ring(),
        category=category,
    )


@pytest.fixture(scope="module")
def tiny_gallery() -> Gallery:
    shapes = [_shape("target-1", "a")]
    shapes += [_shape(f"sibling-{i}", "a") for i in range(1, 5)]
    shapes += [_shape(f"other-{i}", f"c{i}") for i in range(12)]
    return Gallery(shapes, name="tiny")


def _distances_in_order(gallery: Gallery, order: list[str]) -> np.ndarray:
    """Distances that rank exactly the requested shape_ids, best first."""
    by_id = {shape.shape_id: index for index, shape in enumerate(gallery.shapes)}
    dist = np.full(len(gallery), 1.0)
    for rank, shape_id in enumerate(order):
        dist[by_id[shape_id]] = float(rank)
    for index, shape in enumerate(gallery.shapes):
        if shape.shape_id not in set(order):
            dist[index] = float(len(order) + index)
    return dist


def test_condition_row_separates_precision_from_recall(tiny_gallery: Gallery, rsb: Any) -> None:
    """Four same-class candidates, two of them inside the Top-10 list: 0.2 of the list, 0.5 of the
    class. A single number could not be both, which is the whole point of CP8-A2."""
    arm = rsb._cp8_arms(["E0"])[0]
    order = [
        "mpeg7_target-1",
        "mpeg7_other-1",
        "mpeg7_other-2",
        "mpeg7_sibling-1",
        "mpeg7_other-3",
        "mpeg7_other-4",
        "mpeg7_other-5",
        "mpeg7_other-6",
        "mpeg7_other-7",
        "mpeg7_sibling-2",
        "mpeg7_sibling-3",
    ]
    dist = _distances_in_order(tiny_gallery, order)
    entry = {"shape_id": "mpeg7_target-1", "stem": "target-1", "class": "a", "family": "pointed"}
    ref_order = rsb._d6_rank(dist, tiny_gallery, [])
    row = rsb._cp8_condition_row(
        arm,
        "T0",
        entry,
        {"slot": CP8_CONTROL_SLOT, "angle_deg": 0.0},
        dist,
        tiny_gallery,
        int(tiny_gallery.index_of("mpeg7_target-1")),
        [],
        ref_order,
        0.01,
        {},
    )
    assert row["n_same_class_kept"] == 5
    assert row["same_class_hits_at_10"] == 3
    assert row["precision_at_10"] == pytest.approx(3 / 10)
    assert row["recall_at_10"] == pytest.approx(3 / 5)
    assert row["top1_is_target"] == 1
    assert row["target_rank"] == 1
    assert row["slot"] == CP8_CONTROL_SLOT and row["angle_deg"] == 0.0


def test_condition_row_drops_the_target_under_t2(tiny_gallery: Gallery, rsb: Any) -> None:
    arm = rsb._cp8_arms(["E0"])[0]
    order = [
        "mpeg7_sibling-1",
        "mpeg7_other-1",
        "mpeg7_sibling-2",
        "mpeg7_other-2",
        "mpeg7_other-3",
        "mpeg7_other-4",
        "mpeg7_other-5",
        "mpeg7_sibling-3",
        "mpeg7_other-6",
        "mpeg7_other-7",
    ]
    dist = _distances_in_order(tiny_gallery, order)
    entry = {"shape_id": "mpeg7_target-1", "stem": "target-1", "class": "a", "family": "pointed"}
    # The reference is the same query unrotated, scored under the SAME condition -- which is what
    # the runner passes. A reference built on the unfiltered gallery would not be a set of shared
    # candidates, and rank displacement over two different candidate sets is not a number.
    reference_order = list(order)
    reference_order[2], reference_order[5] = reference_order[5], reference_order[2]
    ref_order = rsb._d6_rank(
        _distances_in_order(tiny_gallery, reference_order),
        tiny_gallery,
        ["mpeg7_target-1", "mpeg7_sibling-4"],
    )
    row = rsb._cp8_condition_row(
        arm,
        "T2",
        entry,
        {"slot": "random", "angle_deg": 137.5},
        dist,
        tiny_gallery,
        int(tiny_gallery.index_of("mpeg7_target-1")),
        ["mpeg7_target-1", "mpeg7_sibling-4"],
        ref_order,
        0.01,
        {},
    )
    assert row["target_rank"] is None  # the exact target is gone by construction
    assert row["top1_is_target"] == 0
    assert row["n_kept"] == len(tiny_gallery) - 2
    assert row["n_same_class_kept"] == 3
    assert row["recall_at_10"] == pytest.approx(3 / 3)
    assert row["mean_abs_rank_shift"] > 0.0  # the two orders really do differ


def test_control_and_rotated_cells_are_never_pooled(rsb: Any) -> None:
    """The CP8-A defect, re-pinned for the new table: 0-degree controls answer an unrotated
    question, so pooling them made a pose-blind arm look twice as rotation-stable as it is."""
    cells = []
    for query in ("mpeg7_a-1", "mpeg7_b-1", "mpeg7_c-1"):
        cells.append(_fake_cell(query=query, slot=CP8_CONTROL_SLOT, angle=0.0, stable=1))
        for index, slot in enumerate(CP8_ANGLE_SLOTS):
            cells.append(_fake_cell(query=query, slot=slot, angle=15.0 + index, stable=0))
    rotated = rsb._cp8_summarise_cells(
        [row for row in cells if row["slot"] != CP8_CONTROL_SLOT],
        metrics.ROTATION_VIEW,
        "T2",
        n_rotated=15,
        n_control=3,
        n_boot=200,
    )
    control = rsb._cp8_summarise_cells(
        [row for row in cells if row["slot"] == CP8_CONTROL_SLOT],
        metrics.CONTROL_VIEW,
        "T2",
        n_rotated=15,
        n_control=3,
    )
    assert rotated["n_cells"] == 15 and rotated["n_control_cells"] == 3
    assert rotated["top1_stability_vs_unrotated"] == 0.0
    assert control["top1_stability_vs_unrotated"] == 1.0
    assert "by construction" in control["note"]
    assert "query_clustered_ci" not in control


def test_a_control_row_at_a_nonzero_angle_is_a_design_change_not_a_filter(rsb: Any) -> None:
    cells = [_fake_cell(query="mpeg7_a-1", slot=CP8_CONTROL_SLOT, angle=0.0, stable=1)]
    cells.append(_fake_cell(query="mpeg7_b-1", slot=CP8_CONTROL_SLOT, angle=33.0, stable=1))
    with pytest.raises(ValueError, match="design change"):
        metrics.split_rotation_and_control(cells, control_slot=CP8_CONTROL_SLOT)


def _fake_cell(*, query: str, slot: str, angle: float, stable: int) -> dict[str, Any]:
    hits = 2 if slot == CP8_CONTROL_SLOT else 1
    return {
        "arm": "E0",
        "condition": "T2",
        "query_id": query,
        "family": "pointed",
        "slot": slot,
        "angle_deg": angle,
        "n_kept": 100,
        "n_same_class_kept": 4,
        "same_class_hits_at_10": hits,
        "top1_is_target": 0,
        "top1_is_same_class": 1,
        "top1_matches_unrotated": stable,
        "target_rank": None,
        "self_distance": 0.2,
        "best_wrong_class_distance": 0.5,
        "precision_at_10": hits / 10,
        "recall_at_10": hits / 4,
        "first_relevant_rank": 1,
        "ndcg_at_10": 0.6,
        "top_10_overlap_vs_unrotated": 0.8,
        "mean_abs_rank_shift": 2.0,
        "max_abs_rank_shift": 9.0,
        "spearman_between_rankings": 0.95,
        "forward_passes": None,
        "query_poses": None,
        "elapsed_seconds": 0.5,
    }


def test_headline_means_match_a_hand_recompute(rsb: Any) -> None:
    cells = []
    for index, query in enumerate(("mpeg7_a-1", "mpeg7_b-1", "mpeg7_c-1")):
        for slot in CP8_ANGLE_SLOTS:
            cell = _fake_cell(query=query, slot=slot, angle=10.0 + index, stable=index % 2)
            cell["precision_at_10"] = (index + 1) / 10
            cell["recall_at_10"] = (index + 1) / 4
            cell["elapsed_seconds"] = 1.0 + index
            cells.append(cell)
    block = rsb._cp8_summarise_cells(
        cells, metrics.ROTATION_VIEW, "T2", n_rotated=15, n_control=3
    )
    assert block["precision_at_10"] == pytest.approx(float(np.mean([0.1, 0.2, 0.3])))
    assert block["recall_at_10"] == pytest.approx(float(np.mean([0.25, 0.5, 0.75])))
    assert block["class_mrr"] == pytest.approx(1.0)
    assert block["seconds_per_query_mean"] == pytest.approx(float(np.mean([1.0, 2.0, 3.0])))
    assert block["seconds_per_query_max"] == 3.0
    assert block["top1_stability_vs_unrotated"] == pytest.approx(1 / 3)
    assert block["n_relevant_retained_min"] == 4 and block["n_relevant_retained_max"] == 4


def test_undefined_recall_and_ndcg_are_counted_not_zeroed(rsb: Any) -> None:
    cells = []
    for query in ("mpeg7_a-1", "mpeg7_b-1"):
        for slot in CP8_ANGLE_SLOTS:
            cell = _fake_cell(query=query, slot=slot, angle=5.0, stable=1)
            cell["recall_at_10"] = 0.5
            cell["ndcg_at_10"] = 0.5
            if query == "mpeg7_a-1":  # nothing same-class survived the T2 filter for this query
                cell["n_same_class_kept"] = 0
                cell["recall_at_10"] = None
                cell["ndcg_at_10"] = None
            cells.append(cell)
    block = rsb._cp8_summarise_cells(cells, metrics.ROTATION_VIEW, "T2", n_rotated=10, n_control=0)
    assert block["recall_at_10_undefined_cells"] == 5
    assert block["recall_at_10_n_scored"] == 5
    assert block["ndcg_at_10_n_scored"] == 5
    # The five undefined cells are absent from the average, not folded in as zero: scoring them as
    # 0 would have reported 0.25 for a query whose class was simply not in the filtered gallery.
    assert block["recall_at_10"] == pytest.approx(0.5)
    assert block["ndcg_at_10"] == pytest.approx(0.5)


def test_target_rank_undefined_is_counted(rsb: Any) -> None:
    cells = [
        _fake_cell(query="mpeg7_a-1", slot=slot, angle=1.0, stable=1) for slot in CP8_ANGLE_SLOTS
    ]
    for cell in cells:
        cell["target_rank"] = None
    block = rsb._cp8_summarise_cells(cells, metrics.ROTATION_VIEW, "T0", n_rotated=5, n_control=0)
    assert block["n_target_rank_undefined"] == 5
    assert block["target_rank_mean"] is None and block["target_rank_median"] is None


def test_cell_coercion_from_csv_strings(rsb: Any) -> None:
    row = {
        "angle_deg": "137.5",
        "recall_at_10": "",
        "ndcg_at_10": "None",
        "target_rank": "nan",
        "n_kept": "1399.0",
        "top1_is_same_class": "1",
        "slot": "random",
    }
    cell = rsb._cp8_cell(row)
    assert cell["angle_deg"] == 137.5
    assert cell["recall_at_10"] is None and cell["ndcg_at_10"] is None
    assert cell["target_rank"] is None
    assert cell["n_kept"] == 1399 and cell["top1_is_same_class"] == 1


# ---------------------------------------------------------------- the clustered interval


def test_interval_estimate_is_the_pooled_mean(rsb: Any) -> None:
    values = {"mpeg7_a-1": [0.1, 0.1, 0.1, 0.1, 0.1], "mpeg7_b-1": [0.9, 0.9, 0.9, 0.9, 0.9]}
    out = rsb._cp8_cluster_ci(values, n_boot=200, seed=CP8_SEED)
    assert out["estimate"] == pytest.approx(0.5)
    assert out["n_clusters"] == 2 and out["n_cells"] == 10
    assert out["cluster_size_min"] == out["cluster_size_max"] == 5
    assert out["ci_low"] <= out["estimate"] <= out["ci_high"]


def test_the_bootstrap_moves_whole_queries_not_cells(rsb: Any) -> None:
    """Values identical inside a query can only vary by resampling queries; a cell-level
    bootstrap would report a point estimate with no spread at all."""
    values = {f"mpeg7_q{i}-1": [float(i) / 5] * 5 for i in range(6)}
    out = rsb._cp8_cluster_ci(values, n_boot=200, seed=CP8_SEED)
    assert out["sampling_unit"].startswith("original query")
    assert out["ci_high"] > out["ci_low"]  # spread comes from the clusters, not from cells

    flat = {f"mpeg7_q{i}-{j}": [float(i) / 5] for i in range(6) for j in range(5)}
    same_mean = rsb._cp8_cluster_ci(flat, n_boot=200, seed=CP8_SEED)
    assert same_mean["estimate"] == pytest.approx(out["estimate"])
    assert same_mean["n_clusters"] == 30


def test_interval_is_reproducible_and_seed_sensitive(rsb: Any) -> None:
    """The interval is a deterministic function of (cells, seed): rerunnable, but a different
    seed has to give a different draw or the seed is not actually steering the resample."""
    values = {
        "mpeg7_a-1": [0.1, 0.0, 0.2],
        "mpeg7_b-1": [0.5, 0.4, 0.6],
        "mpeg7_c-1": [0.8, 0.9, 0.7],
        "mpeg7_d-1": [0.3, 0.2, 0.4],
        "mpeg7_e-1": [1.0, 0.9, 0.8],
        "mpeg7_f-1": [0.0, 0.1, 0.2],
    }
    first = rsb._cp8_cluster_ci(values, n_boot=300, seed=CP8_SEED)
    again = rsb._cp8_cluster_ci(values, n_boot=300, seed=CP8_SEED)
    other = rsb._cp8_cluster_ci(values, n_boot=300, seed=CP8_SEED + 1)
    assert (first["ci_low"], first["ci_high"]) == (again["ci_low"], again["ci_high"])
    assert (first["ci_low"], first["ci_high"]) != (other["ci_low"], other["ci_high"])


def test_a_statistic_with_no_defined_cell_is_reported_as_none(rsb: Any) -> None:
    out = rsb._cp8_cluster_ci({}, n_boot=20, seed=CP8_SEED)
    assert out["estimate"] is None and out["n_clusters"] == 0


def test_cluster_values_group_by_query_and_drop_undefined(rsb: Any) -> None:
    cells = [
        _fake_cell(query="mpeg7_a-1", slot=slot, angle=1.0, stable=1) for slot in CP8_ANGLE_SLOTS
    ]
    cells.append(_fake_cell(query="mpeg7_b-1", slot="random", angle=2.0, stable=1))
    cells[0]["recall_at_10"] = None
    grouped = rsb._cp8_cluster_values(cells, lambda row: row["recall_at_10"])
    assert sorted(grouped) == ["mpeg7_a-1", "mpeg7_b-1"]
    assert len(grouped["mpeg7_a-1"]) == len(CP8_ANGLE_SLOTS) - 1


def test_slot_view_uses_the_declared_slots_only(rsb: Any) -> None:
    cells = []
    for query in ("mpeg7_a-1", "mpeg7_b-1", "mpeg7_c-1"):
        cells.append(_fake_cell(query=query, slot=CP8_CONTROL_SLOT, angle=0.0, stable=1))
        for slot in CP8_ANGLE_SLOTS:
            cells.append(_fake_cell(query=query, slot=slot, angle=12.0, stable=0))
    view = rsb._cp8_slot_view(cells)
    counts = {slot: block["n_cells"] for slot, block in view["E0"].items()}
    assert counts == {"on_grid": 3, "mid_grid": 6, "random": 6}
    assert CP8_CONTROL_SLOT not in view["E0"]


def test_rankings_state_their_direction_and_cover_every_headline_metric(rsb: Any) -> None:
    summary = {}
    for arm, precision in (("E0", 0.1), ("E1", 0.3), ("E3/sample", 0.2)):
        cells = [
            _fake_cell(query=f"mpeg7_q{i}-1", slot=slot, angle=1.0, stable=i % 2)
            for i in range(3)
            for slot in CP8_ANGLE_SLOTS
        ]
        for cell in cells:
            cell["arm"] = arm
            cell["precision_at_10"] = precision
            cell["mean_abs_rank_shift"] = precision  # lower is better here
        block = rsb._cp8_summarise_cells(
            cells, metrics.ROTATION_VIEW, "T2", n_rotated=15, n_control=3, with_ci=False
        )
        summary[arm] = {metrics.ROTATION_VIEW: {"T2": block}}
    out = rsb._cp8_rankings(summary)
    assert out["precision_at_10"]["higher_is_better"] is True
    assert out["precision_at_10"]["order"] == ["E1", "E3/sample", "E0"]
    assert out["mean_abs_rank_shift"]["higher_is_better"] is False
    assert out["mean_abs_rank_shift"]["order"] == ["E0", "E3/sample", "E1"]
    assert set(out) == set(rsb.CP8_RANKING_DIRECTIONS)


# ---------------------------------------------------------------- the published table

#: The four files ``--stage cp8`` writes for the frozen 24-query run. The tests below do not
#: exercise code, they audit that artefact, so in a checkout where the run has not happened they
#: say "not run yet" out loud. A skip with the missing names in the reason -- never a green
#: assertion over a table that is not on disk.
PUBLISHED_ARTEFACTS = (
    "cp8_retrieval_results.csv",
    "cp8_top10_hits.csv",
    "cp8_exclusions.csv",
    "independent_metrics.json",
)
_MISSING = [name for name in PUBLISHED_ARTEFACTS if not (OUT / name).exists()]
needs_published = pytest.mark.skipif(
    bool(_MISSING), reason=f"the published CP8 table is not in this checkout: {_MISSING}"
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def stored_cells() -> list[dict[str, str]]:
    return _read(OUT / "cp8_retrieval_results.csv")


@pytest.fixture(scope="module")
def stored_metrics() -> dict[str, Any]:
    return json.loads((OUT / "independent_metrics.json").read_text(encoding="utf-8"))


@needs_published
def test_published_cells_cover_every_arm_and_both_denominators(
    stored_cells: list[dict[str, str]], design: dict[str, Any]
) -> None:
    n_query = len(design["queries"])
    per_arm = {}
    for arm in CP8_ARMS:
        rows = [row for row in stored_cells if row["arm"] == arm]
        per_arm[arm] = (
            sum(1 for row in rows if row["slot"] != CP8_CONTROL_SLOT),
            sum(1 for row in rows if row["slot"] == CP8_CONTROL_SLOT),
        )
    assert set(per_arm.values()) == {(n_query * 5 * 2, n_query * 2)}
    assert all(row["condition"] in ("T0", "T2") for row in stored_cells)


@needs_published
def test_published_headline_recomputes_from_the_stored_cells(rsb: Any, stored_cells: Any) -> None:
    """The JSON has to follow from the CSV any reader can open, not from state only the run had."""
    metrics_json = json.loads((OUT / "independent_metrics.json").read_text(encoding="utf-8"))
    for arm in CP8_ARMS:
        rebuilt = rsb._cp8_summarise(
            [row for row in stored_cells if row["arm"] == arm],
            n_boot=200,
            seed=CP8_SEED,
        )
        published = metrics_json["retrieval_by_arm"][arm][metrics.ROTATION_VIEW]["T2"]
        rebuilt_block = rebuilt[metrics.ROTATION_VIEW]["T2"]
        for key in (
            "top1_same_class_accuracy",
            "top1_stability_vs_unrotated",
            "precision_at_10",
            "recall_at_10",
            "ndcg_at_10",
            "class_mrr",
            "top_10_overlap_vs_unrotated",
            "mean_abs_rank_shift",
            "seconds_per_query_mean",
        ):
            assert rebuilt_block[key] == pytest.approx(published[key], abs=1e-12), (arm, key)
        ci = published["query_clustered_ci"]["precision_at_10"]
        assert ci["n_clusters"] == 24 and ci["n_cells"] == 120, (arm, ci)


@needs_published
def test_published_control_view_is_a_wiring_check_not_a_stability_claim(
    stored_metrics: dict[str, Any]
) -> None:
    for arm in CP8_ARMS:
        control = stored_metrics["retrieval_by_arm"][arm][metrics.CONTROL_VIEW]["T2"]
        assert control["top1_stability_vs_unrotated"] == 1.0
        assert control["n_cells"] == 24
        assert "query_clustered_ci" not in control


@needs_published
def test_published_headline_is_a_plain_numpy_recompute_of_the_stored_counts(
    stored_cells: list[dict[str, str]], stored_metrics: dict[str, Any]
) -> None:
    """Independent of the summariser: same-class Top-1 and Precision@10 counted straight from
    columns, for every arm, so a wrong denominator anywhere shows up here."""
    for arm in CP8_ARMS:
        rows = [
            row
            for row in stored_cells
            if row["arm"] == arm and row["condition"] == "T2" and row["slot"] != CP8_CONTROL_SLOT
        ]
        assert len(rows) == 120
        same_class = np.array([int(row["top1_is_same_class"]) for row in rows], dtype=float)
        precision = np.array([float(row["precision_at_10"]) for row in rows], dtype=float)
        hits = np.array([int(row["same_class_hits_at_10"]) for row in rows], dtype=float)
        denominators = np.array([int(row["n_same_class_kept"]) for row in rows], dtype=float)
        published = stored_metrics["retrieval_by_arm"][arm][metrics.ROTATION_VIEW]["T2"]
        assert published["top1_same_class_accuracy"] == pytest.approx(same_class.mean())
        assert published["precision_at_10"] == pytest.approx(precision.mean())
        keep = denominators > 0
        assert published["recall_at_10"] == pytest.approx(
            float(np.mean(hits[keep] / denominators[keep]))
        )
        assert published["recall_at_10_undefined_cells"] == int((~keep).sum())


@needs_published
def test_published_exclusions_are_shared_by_all_arms(stored_metrics: dict[str, Any]) -> None:
    """One exclusion list per query, defined by E0 before any arm ran, so no arm can be handed an
    easier candidate set than the arms it is compared with."""
    rows = _read(OUT / "cp8_exclusions.csv")
    assert len(rows) == stored_metrics["design"]["n_queries"]
    for row in rows:
        near = json.loads(row["near_duplicate_ids"])
        assert row["query_id"] not in near  # the exact target is always dropped under T2
        assert int(row["n_kept_t2"]) == int(row["n_gallery"]) - 1 - len(near)
        assert int(row["n_kept_t0"]) == int(row["n_gallery"])
    assert {row["rule"] for row in rows} == {
        f"E0 aligned distance < {stored_metrics['design']['t2_threshold_e0']}, "
        "defined once per query for all arms"
    }


@needs_published
def test_published_hits_table_reproduces_the_cell_numerators() -> None:
    """Three tables, one story: the per-rank rows must count the same hits the ratio claims."""
    cells = _read(OUT / "cp8_retrieval_results.csv")
    hits = _read(OUT / "cp8_top10_hits.csv")
    counted: dict[tuple[str, str, str, str], int] = {}
    for row in hits:
        if int(row["rank"]) <= CP8_TOP_K and row["is_same_class"] == "1":
            key = (row["arm"], row["condition"], row["query_id"], row["pose"])
            counted[key] = counted.get(key, 0) + 1
    checked = 0
    for row in cells:
        if row["condition"] != "T2":
            continue
        angle = float(row["angle_deg"])
        pose = "unrotated" if row["slot"] == CP8_CONTROL_SLOT else f"rotate_{angle:g}"
        key = (row["arm"], row["condition"], row["query_id"], pose)
        assert counted.get(key, 0) == int(row["same_class_hits_at_10"]), key
        checked += 1
    assert checked == 6 * 24 * 6


@needs_published
def test_published_run_binds_the_design_it_promised(stored_metrics: dict[str, Any]) -> None:
    design = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    bound = stored_metrics["design"]
    assert bound["content_sha256"] == design["content_sha256"]
    assert bound["sha256"] == _sha(DESIGN_PATH)
    assert bound["seed"] == CP8_SEED
    assert stored_metrics["provenance"]["dataset"]["archive_sha256"] == design["provenance"][
        "dataset"
    ]["archive_sha256"]
    assert stored_metrics["provenance"]["gallery"]["n_shapes"] == 1400
    assert stored_metrics["provenance"]["smoke_run"]["published"] is True
    assert [arm["label"] for arm in stored_metrics["arms"]] == list(CP8_ARMS)
    assert all(arm["blocked"] is False for arm in stored_metrics["arms"])


@needs_published
def test_published_report_keeps_the_four_questions_apart(stored_metrics: dict[str, Any]) -> None:
    """One headline cannot answer four different questions, so the document keeps them separate."""
    for block in stored_metrics["retrieval_by_arm"].values():
        assert metrics.ROTATION_VIEW in block and metrics.CONTROL_VIEW in block
    assert "human_perception" in stored_metrics
    assert stored_metrics["human_perception"]["new_ratings_collected"] == 0
    assert "category_labels_are" in stored_metrics["human_perception"]
    assert stored_metrics["coverage_audit"]["n_queries_meeting"] == 17
    assert "query_clustered_ci" in stored_metrics["retrieval_by_arm"]["E0"][
        metrics.ROTATION_VIEW
    ]["T2"]
    assert stored_metrics["metric_definitions"]


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
