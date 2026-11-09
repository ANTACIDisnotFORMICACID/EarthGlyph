"""Tests for the CP8 figure script -- a picture has to obey the same rules as the table.

A figure is how a result gets believed, so these tests pin the ways a drawing could lie:

* publishing a ``--limit-queries`` plumbing table as if it were the 24-query result;
* drawing over a design file that no longer hashes to the sample the run scored;
* drawing a table whose cell arithmetic is not the promised 24 x 5 + 24;
* letting two arms be searched at two different angles in a side-by-side row;
* looking a stored panel up by an angle the hits table never held, so a whole arm draws nothing;
* drawing panels in an order the CSV did not record;
* picking "the worst failures" by a rule that differs per arm, or naming a cause the stored
  columns do not support;
* letting the unrotated control cells into a rotation panel.

No PNG is rendered here. The figures are made by ``python scripts/plot_checkpoint8.py``.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "experiments" / "shape_perception_benchmark"
DESIGN_PATH = BENCH / "checkpoint8" / "independent_validation_design.json"
CONTROL = "control"


@pytest.fixture(scope="module")
def plot() -> Any:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "plot_checkpoint8_test", ROOT / "scripts" / "plot_checkpoint8.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def design() -> dict[str, Any]:
    return json.loads(DESIGN_PATH.read_text(encoding="utf-8"))


def _cells(design: dict[str, Any]) -> list[dict[str, str]]:
    """The cell table a clean run stores: five frozen angles and one control per query and arm."""
    by_query: dict[int, list[dict[str, Any]]] = {}
    for row in design["angle_design"]["rows"]:
        by_query.setdefault(int(row["query_index"]), []).append(row)
    rows = []
    for arm in design["arms"]["labels"]:
        for index, entry in enumerate(design["queries"]):
            poses = [*by_query[index], {"slot": CONTROL, "angle_deg": 0.0}]
            for pose in poses:
                for condition in ("T0", "T2"):
                    rows.append(
                        {
                            "arm": arm,
                            "condition": condition,
                            "query_id": entry["shape_id"],
                            "slot": pose["slot"],
                            "angle_deg": f"{pose['angle_deg']:g}",
                            "n_kept": "1397",
                            "n_same_class_kept": "3",
                            "same_class_hits_at_10": "1",
                            "top1_is_same_class": "1",
                            "top1_matches_unrotated": "1",
                            "top_10_overlap_vs_unrotated": "0.4",
                            "precision_at_10": "0.1",
                            "recall_at_10": "0.33",
                            "mean_abs_rank_shift": "120",
                        }
                    )
    return rows


def _summary(design: dict[str, Any], cells: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "design": {
            "n_queries": len(design["queries"]),
            "content_sha256": design["content_sha256"],
        },
        "provenance": {"smoke_run": {"published": True, "limited_to_queries": None}},
        "arms": [{"label": label} for label in design["arms"]["labels"]],
        "rows_written": {"cells": len(cells)},
        "retrieval_by_arm": {},
    }


def _publish(
    plot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cells: list[dict[str, str]],
    summary: dict[str, Any],
) -> None:
    """Point the figure module at a checkpoint8 directory holding exactly these files."""
    design_file = tmp_path / "independent_validation_design.json"
    design_file.write_text(DESIGN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    for name, rows in (
        ("cp8_retrieval_results.csv", cells),
        ("cp8_top10_hits.csv", cells[:1]),
        ("cp8_exclusions.csv", cells[:1]),
    ):
        with (tmp_path / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    metrics_file = tmp_path / "independent_metrics.json"
    metrics_file.write_text(json.dumps(summary), encoding="utf-8")
    for attribute, path in (
        ("OUT", tmp_path),
        ("DESIGN", design_file),
        ("CELLS", tmp_path / "cp8_retrieval_results.csv"),
        ("HITS", tmp_path / "cp8_top10_hits.csv"),
        ("EXCLUSIONS", tmp_path / "cp8_exclusions.csv"),
        ("METRICS", metrics_file),
    ):
        monkeypatch.setattr(plot, attribute, path)


# --------------------------------------------------------------------------- the guard


def test_the_guard_refuses_a_missing_table(
    plot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attribute, name in (
        ("DESIGN", "independent_validation_design.json"),
        ("CELLS", "cp8_retrieval_results.csv"),
        ("HITS", "cp8_top10_hits.csv"),
        ("EXCLUSIONS", "cp8_exclusions.csv"),
        ("METRICS", "independent_metrics.json"),
    ):
        monkeypatch.setattr(plot, attribute, tmp_path / name)
    with pytest.raises(SystemExit, match="run .*--stage cp8.* first"):
        plot.require_published_table()


def test_the_guard_refuses_a_smoke_table(
    plot: Any,
    design: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-query plumbing check describes a partial sample, so it can never become the picture."""
    cells = _cells(design)[: 6 * 6 * 2]
    summary = _summary(design, cells)
    summary["provenance"]["smoke_run"] = {"published": False, "limited_to_queries": 1}
    summary["design"]["n_queries"] = 1
    _publish(plot, monkeypatch, tmp_path, cells, summary)
    with pytest.raises(SystemExit, match="smoke run"):
        plot.require_published_table()


def test_the_guard_refuses_a_design_that_no_longer_hashes_to_the_run(
    plot: Any, design: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = _cells(design)
    summary = _summary(design, cells)
    summary["design"]["content_sha256"] = "0" * 64
    _publish(plot, monkeypatch, tmp_path, cells, summary)
    with pytest.raises(SystemExit, match="the sample moved"):
        plot.require_published_table()


def test_the_guard_refuses_a_cell_count_that_is_not_the_frozen_sample(
    plot: Any, design: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the random slots would leave a smaller test that still prints 24 queries."""
    cells = [row for row in _cells(design) if row["slot"] != "random"]
    _publish(plot, monkeypatch, tmp_path, cells, _summary(design, cells))
    with pytest.raises(SystemExit, match="not the frozen one"):
        plot.require_published_table()


def test_the_guard_accepts_the_arithmetic_the_design_promises(
    plot: Any, design: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = _cells(design)
    summary = _summary(design, cells)
    _publish(plot, monkeypatch, tmp_path, cells, summary)
    assert plot.require_published_table() == summary


# --------------------------------------------------------------------------- selection rules


def test_pose_label_is_the_string_the_runner_wrote(plot: Any, design: dict[str, Any]) -> None:
    """A picture looks up hits by the pose COLUMN, so the label must be rebuilt as it was written.

    Off-grid angles are full-precision floats in the design and the cell table, but the runner
    writes the hits table's ``pose`` with ``%g``, which keeps six significant digits. Parsing that
    label back to a float therefore does NOT give the cell's angle again (229.58513465711746
    becomes 229.585), so matching on the parsed number drops exactly the rows whose angle the
    design drew randomly -- the kind of missing panel a figure can hide. Reformatting the cell's
    angle with the same ``%g`` rule the runner used is an exact match.
    """
    angles = [float(row["angle_deg"]) for row in design["angle_design"]["rows"]]
    lossy = 0
    for angle in angles:
        stored_pose = f"rotate_{angle:g}"
        assert plot.pose_label(float(f"{angle}")) == stored_pose
        if float(stored_pose.removeprefix("rotate_")) != angle:
            lossy += 1
    assert lossy, "some frozen angle must be one %g cannot round-trip, or the guard proves nothing"
    assert plot.pose_label(0.0) == "unrotated"


def test_one_angle_per_query_and_slot_or_the_rows_are_not_comparable(
    plot: Any, design: dict[str, Any]
) -> None:
    cells = _cells(design)
    query = str(design["queries"][0]["shape_id"])
    angle, rows = plot._query_angle_slot(cells, query, "on_grid")
    assert len(rows) == len(design["arms"]["labels"])
    assert {float(row["angle_deg"]) for row in rows} == {angle}

    smuggled = [
        *cells,
        {**cells[0], "query_id": query, "slot": "on_grid", "angle_deg": "11", "condition": "T2"},
    ]
    with pytest.raises(SystemExit, match="not one shared angle"):
        plot._query_angle_slot(smuggled, query, "on_grid")


def test_worst_cell_uses_one_rule_for_every_arm(plot: Any, design: dict[str, Any]) -> None:
    cells = _cells(design)
    target = str(design["queries"][3]["shape_id"])
    for row in cells:
        if row["arm"] == "E1" and row["query_id"] == target:
            row["precision_at_10"] = "0.0"
            row["mean_abs_rank_shift"] = "400"
    chosen = plot.worst_cell(cells, "E1")
    assert chosen["precision_at_10"] == "0.0" and chosen["query_id"] == target
    assert plot.worst_cell(cells, "E0")["precision_at_10"] == "0.1"


def test_worst_cell_ties_break_on_id_and_angle(plot: Any, design: dict[str, Any]) -> None:
    cells = _cells(design)
    first = plot.worst_cell(cells, "E0")
    again = plot.worst_cell(list(reversed(cells)), "E0")
    assert (first["query_id"], first["angle_deg"]) == (again["query_id"], again["angle_deg"])
    assert first["slot"] != CONTROL
    with pytest.raises(SystemExit, match="no stored rotated cells"):
        plot.worst_cell([row for row in cells if row["arm"] != "E0"], "E0")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (
            {"n_same_class_kept": "0", "same_class_hits_at_10": "0", "top1_is_same_class": "0"},
            "no same-class candidate",
        ),
        (
            {"n_same_class_kept": "3", "same_class_hits_at_10": "0", "top1_is_same_class": "0"},
            "reached the Top-10",
        ),
        (
            {"n_same_class_kept": "3", "same_class_hits_at_10": "2", "top1_is_same_class": "0"},
            "a wrong class ranked first",
        ),
        (
            {"n_same_class_kept": "3", "same_class_hits_at_10": "2", "top1_is_same_class": "1"},
            "cause undetermined",
        ),
    ],
)
def test_a_cause_is_named_only_when_the_columns_support_it(
    plot: Any, cell: dict[str, str], expected: str
) -> None:
    assert expected in plot.cause_label(cell)


def test_rotated_cells_never_include_the_control_view(plot: Any, design: dict[str, Any]) -> None:
    cells = _cells(design)
    rotated = plot.rotated_cells(cells)
    assert all(row["slot"] != CONTROL for row in rotated)
    assert len(rotated) == len(design["arms"]["labels"]) * len(design["queries"]) * 5


def test_rotation_panels_pair_a_cell_column_with_a_real_headline_name(plot: Any) -> None:
    """The one place a mean line can silently vanish: averaging a column the headline renamed."""
    assert [(field, block) for field, block, _, _ in plot.ROTATION_PANELS] == [
        ("top1_matches_unrotated", "top1_stability_vs_unrotated"),
        ("top_10_overlap_vs_unrotated", "top_10_overlap_vs_unrotated"),
        ("precision_at_10", "precision_at_10"),
        ("mean_abs_rank_shift", "mean_abs_rank_shift"),
    ]


def test_cells_of_refuses_a_table_that_disagrees_with_its_own_record(
    plot: Any, design: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = _cells(design)
    summary = _summary(design, cells)
    _publish(plot, monkeypatch, tmp_path, cells, summary)
    summary["rows_written"]["cells"] = len(cells) + 1
    with pytest.raises(SystemExit, match="one of the two was rewritten"):
        plot.cells_of(summary)


# --------------------------------------------------------------------------- the drawn order


def _hit(rank: int, distance: float, shape_id: str) -> dict[str, str]:
    return {
        "rank": str(rank),
        "distance": f"{distance:.4f}",
        "shape_id": shape_id,
        "is_same_class": "1" if rank <= 2 else "0",
    }


def _axes(columns: int) -> Any:
    _figure, axes = plt.subplots(1, columns, figsize=(2.0 * columns, 2.0), squeeze=False)
    return axes


def _ring() -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, 24, endpoint=False)
    return np.column_stack([np.cos(angle), np.sin(angle)])


def _shape(shape_id: str, category: str) -> Any:
    from earthglyph.shape_benchmark.datasets import BenchmarkShape

    return BenchmarkShape(
        shape_id=shape_id,
        source="MPEG7",
        points=_ring(),
        display_points=_ring(),
        category=category,
    )


def test_overview_row_refuses_a_rank_list_that_is_not_1_to_k(plot: Any) -> None:
    index = {f"mpeg7_s{i}": _shape(f"mpeg7_s{i}", "a") for i in range(1, 8)}
    index["mpeg7_q"] = _shape("mpeg7_q", "a")
    rows = [_hit(rank, float(rank), f"mpeg7_s{rank}") for rank in (1, 2, 3, 4, 6, 7)]
    with pytest.raises(SystemExit, match="ranks are not 1"):
        plot._overview_row(
            _axes(7), 0, "E0", rows, index["mpeg7_q"], 15.0, index, 1.2, top=5
        )


def test_overview_row_refuses_distances_that_do_not_follow_the_stored_ranks(plot: Any) -> None:
    index = {f"mpeg7_s{i}": _shape(f"mpeg7_s{i}", "a") for i in range(1, 8)}
    index["mpeg7_q"] = _shape("mpeg7_q", "a")
    rows = [_hit(rank, 1.0 if rank == 3 else float(rank), f"mpeg7_s{rank}") for rank in range(1, 8)]
    with pytest.raises(SystemExit, match="not sorted by distance"):
        plot._overview_row(
            _axes(7), 0, "E0", rows, index["mpeg7_q"], 15.0, index, 1.2, top=5
        )


def test_overview_row_draws_the_stored_order_when_the_table_is_consistent(plot: Any) -> None:
    index = {f"mpeg7_s{i}": _shape(f"mpeg7_s{i}", "a") for i in range(1, 8)}
    index["mpeg7_q"] = _shape("mpeg7_q", "a")
    rows = [_hit(rank, float(rank), f"mpeg7_s{rank}") for rank in range(1, 8)]
    axes = _axes(7)
    plot._overview_row(axes, 0, "E0", rows, index["mpeg7_q"], 15.0, index, 1.2, top=5)
    titles = [axes[0][column].get_title() for column in range(2, 7)]
    assert [title.split()[1] for title in titles] == ["1", "2", "3", "4", "5"]
    assert "published pose" in axes[0][0].get_title()
    assert "+15 deg" in axes[0][1].get_title()
    plt.close("all")
