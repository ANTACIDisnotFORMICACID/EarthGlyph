"""Tests for the CP8-A statistics correction (P0-4.6 Checkpoint 8).

Two reading errors were baked into the committed D6 retrieval headline: every ``(arm, condition)``
block averaged all 72 stored cells, including the 12 unrotated 0-degree controls, and a Precision@K
was published under the name ``class_recall_at_k``. The first made a pose-blind wrapper look twice
as rotation-stable as it is, so the correction is pinned here three ways over: the denominators, an
independent recompute straight from the stored tidy tables, and the per-field audit trail.

Nothing in this file re-runs inference. Where a number is checked it is re-derived from the raw
rows with plain numpy, never with the function that wrote it.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from earthglyph.shape_benchmark import metrics

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_shape_benchmark.py"
RESULTS = ROOT / "experiments" / "shape_perception_benchmark" / "results"
CELLS = RESULTS / "d6_retrieval_results.csv"
HITS = RESULTS / "d6_top10_hits.csv"
STORED = RESULTS / "d6_rotation_metrics.json"
CORRECTED = RESULTS / "d6_rotation_metrics_corrected.json"

CONDITIONS = ("T0", "T1", "T2")
N_QUERY = 12
N_ROTATED_SLOTS = 5
N_ROTATED_CELLS = N_QUERY * N_ROTATED_SLOTS
N_CONTROL_CELLS = N_QUERY
N_STORED_CELLS = N_ROTATED_CELLS + N_CONTROL_CELLS
TOP_K = 10

pytestmark = pytest.mark.skipif(
    not (CORRECTED.exists() and CELLS.exists() and STORED.exists()),
    reason="the CP8-A correction has not been run in this checkout",
)


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _value(row: dict[str, str], field: str) -> float | None:
    raw = (row[field] or "").strip()
    if raw in ("", "None", "nan"):
        return None
    return float(raw)


def _mean(values: list[float | None]) -> float:
    finite = [v for v in values if v is not None and np.isfinite(v)]
    assert finite, "no finite values to average"
    return float(np.mean(finite))


def _pose(row: dict[str, str]) -> str:
    if row["slot"] == "control":
        return "unrotated"
    return f"rotate_{float(row['angle_deg']):.6g}"


@pytest.fixture(scope="module")
def runner() -> Any:
    return _load("run_shape_benchmark", SCRIPT)


@pytest.fixture(scope="module")
def cells() -> list[dict[str, str]]:
    return _read(CELLS)


@pytest.fixture(scope="module")
def stored() -> dict[str, Any]:
    return _json(STORED)


@pytest.fixture(scope="module")
def corrected() -> dict[str, Any]:
    return _json(CORRECTED)


@pytest.fixture(scope="module")
def arms(stored: dict[str, Any]) -> list[str]:
    return [arm["label"] for arm in stored["arms"]]


# -------------------------------------------------------------------------- the denominators


def test_every_arm_and_condition_stores_sixty_rotated_cells_and_twelve_controls(
    cells: list[dict[str, str]], arms: list[str]
) -> None:
    """12 queries x 5 non-zero angles are the rotation study; 12 x 1 zero pose is the control."""
    for arm in arms:
        for condition in CONDITIONS:
            block = [r for r in cells if r["arm"] == arm and r["condition"] == condition]
            rotated = [r for r in block if r["slot"] != "control"]
            control = [r for r in block if r["slot"] == "control"]
            assert len(block) == N_STORED_CELLS, f"{arm}/{condition} stored cells"
            assert len(rotated) == N_ROTATED_CELLS, f"{arm}/{condition} rotated cells"
            assert len(control) == N_CONTROL_CELLS, f"{arm}/{condition} control cells"
            assert len({r["query_id"] for r in block}) == N_QUERY
            assert {float(r["angle_deg"]) for r in control} == {0.0}
            assert all(float(r["angle_deg"]) != 0.0 for r in rotated)


def test_the_expected_denominators_come_from_the_frozen_design_not_from_a_constant(
    stored: dict[str, Any], cells: list[dict[str, str]]
) -> None:
    """If the design moves, this moves with it, and the pinned 60/12/72 then fails loudly."""
    design = stored["design"]
    assert len(design["queries"]) == N_QUERY
    assert len(design["angle_slots"]) == N_ROTATED_SLOTS
    assert len({r["query_id"] for r in cells}) == design["n_queries"]
    per_query = {
        query_id: len(
            {
                r["angle_deg"]
                for r in cells
                if r["query_id"] == query_id and r["slot"] != "control"
            }
        )
        for query_id in {r["query_id"] for r in cells}
    }
    assert set(per_query.values()) == {N_ROTATED_SLOTS}
    assert design["n_query_angle_rows"] == N_ROTATED_CELLS
    assert design["angles_per_query"] == N_ROTATED_SLOTS
    # the whole stored table is the design PLUS one unrotated control per query -- that extra 12
    # is what /1 averaged into the headline
    assert design["n_query_angle_rows"] + design["n_queries"] == N_STORED_CELLS


def test_view_counts_are_stated_inside_every_corrected_block(corrected: dict[str, Any]) -> None:
    for arm, views in corrected["retrieval_by_arm"].items():
        for view, blocks in views.items():
            for condition, block in blocks.items():
                expected = N_ROTATED_CELLS if view == metrics.ROTATION_VIEW else N_CONTROL_CELLS
                assert block["n_cells"] == expected, f"{arm}/{view}/{condition}"
                assert block["n_rotated_cells"] == N_ROTATED_CELLS
                assert block["n_control_cells"] == N_CONTROL_CELLS
                assert block["view"] == view


def test_a_control_row_that_is_no_longer_unrotated_is_refused(
    runner: Any, cells: list[dict[str, str]]
) -> None:
    """The drift the brief asks to catch: turning the control does not silently become a filter."""
    drift = [dict(row) for row in cells if row["arm"] == "E0" and row["condition"] == "T2"]
    for row in drift:
        if row["slot"] == "control":
            row["angle_deg"] = "37.5"
    with pytest.raises(ValueError, match="design change"):
        runner._d6_summarise([runner._d6_cell(row) for row in drift], TOP_K)


def test_a_rotated_row_parked_at_zero_degrees_is_refused(
    runner: Any, cells: list[dict[str, str]]
) -> None:
    drift = [dict(row) for row in cells if row["arm"] == "E0" and row["condition"] == "T2"]
    for row in drift:
        if row["slot"] == "on_grid":
            row["angle_deg"] = "0.0"
    with pytest.raises(ValueError, match="design change"):
        runner._d6_summarise([runner._d6_cell(row) for row in drift], TOP_K)


def test_the_split_keeps_every_row_exactly_once(cells: list[dict[str, str]]) -> None:
    normalised = [{"slot": row["slot"], "angle_deg": float(row["angle_deg"])} for row in cells]
    views = metrics.split_rotation_and_control(normalised)
    assert len(views[metrics.ROTATION_VIEW]) + len(views[metrics.CONTROL_VIEW]) == len(cells)
    assert len(views[metrics.ROTATION_VIEW]) == 16 * len(CONDITIONS) * N_ROTATED_CELLS


# ------------------------------------------------------------------- independent recompute


def test_corrected_headlines_recompute_from_the_raw_cells_with_plain_numpy(
    cells: list[dict[str, str]], corrected: dict[str, Any], arms: list[str]
) -> None:
    """No call into the code under test: means, medians and maxima rebuilt from the tidy table."""
    for arm in arms:
        for condition in CONDITIONS:
            block = [
                r
                for r in cells
                if r["arm"] == arm and r["condition"] == condition and r["slot"] != "control"
            ]
            view = corrected["retrieval_by_arm"][arm][metrics.ROTATION_VIEW][condition]
            assert view["top1_same_class_accuracy"] == pytest.approx(
                _mean([_value(r, "top1_is_same_class") for r in block]), abs=1e-9
            )
            assert view["top1_stability_vs_unrotated"] == pytest.approx(
                _mean([_value(r, "top1_matches_unrotated") for r in block]), abs=1e-9
            )
            assert view[f"precision_at_{TOP_K}"] == pytest.approx(
                _mean([_value(r, "class_recall_at_k") for r in block]), abs=1e-9
            )
            hits = [
                int(round(float(r["class_recall_at_k"]) * min(TOP_K, int(r["n_kept"]))))
                for r in block
            ]
            assert view[f"recall_at_{TOP_K}"] == pytest.approx(
                _mean([h / int(r["n_same_class_kept"]) for h, r in zip(hits, block, strict=True)]),
                abs=1e-9,
            )
            assert view[f"ndcg_at_{TOP_K}"] == pytest.approx(
                _mean([_value(r, "ndcg_at_k") for r in block]), abs=1e-9
            )
            assert view[f"top_{TOP_K}_overlap_vs_unrotated"] == pytest.approx(
                _mean([_value(r, "top_k_overlap_vs_unrotated") for r in block]), abs=1e-9
            )
            first_relevant = [_value(r, "first_relevant_rank") for r in block]
            assert view["class_mrr"] == pytest.approx(
                sum(1.0 / rank for rank in first_relevant if rank) / len(block), abs=1e-9
            )
            assert view["seconds_per_query_mean"] == pytest.approx(
                _mean([_value(r, "elapsed_seconds") for r in block]), abs=1e-9
            )
            assert view["seconds_per_query_max"] == pytest.approx(
                max(float(r["elapsed_seconds"]) for r in block), abs=1e-9
            )
            assert view["top_k_overlap_min"] == pytest.approx(
                min(float(r["top_k_overlap_vs_unrotated"]) for r in block), abs=1e-9
            )
            assert view["max_abs_rank_shift"] == pytest.approx(
                max(float(r["max_abs_rank_shift"]) for r in block), abs=1e-9
            )


def test_the_five_quoted_checkpoint_values_are_the_rotation_only_numbers(
    corrected: dict[str, Any]
) -> None:
    """The numbers the checkpoint prose leans on, re-derived rather than trusted."""
    view = corrected["retrieval_by_arm"]
    assert view["E1"][metrics.ROTATION_VIEW]["T2"]["top1_same_class_accuracy"] == pytest.approx(
        0.83333, abs=1e-4
    )
    assert view["E3/sample"][metrics.ROTATION_VIEW]["T2"][
        "top1_stability_vs_unrotated"
    ] == pytest.approx(0.71667, abs=1e-4)
    assert view["S0K2/naive"][metrics.ROTATION_VIEW]["T2"][
        "top1_same_class_accuracy"
    ] == pytest.approx(0.15000, abs=1e-4)
    assert view["S0K2/naive"][metrics.ROTATION_VIEW]["T2"][
        "top1_stability_vs_unrotated"
    ] == pytest.approx(0.05000, abs=1e-4)
    assert view["S0K2/R2"][metrics.ROTATION_VIEW]["T2"][
        "top1_stability_vs_unrotated"
    ] == pytest.approx(0.96667, abs=1e-4)


def test_the_pooled_seventy_two_cell_numbers_are_what_the_old_headline_published(
    cells: list[dict[str, str]], stored: dict[str, Any], arms: list[str]
) -> None:
    """The /1 blocks are reproduced from all 72 cells, so the difference is only the filter."""
    for arm in arms:
        for condition in CONDITIONS:
            block = [r for r in cells if r["arm"] == arm and r["condition"] == condition]
            old = stored["retrieval_by_arm"][arm][condition]
            assert old["n_cells"] == len(block) == N_STORED_CELLS
            assert old["top1_stability_vs_unrotated"] == pytest.approx(
                _mean([_value(r, "top1_matches_unrotated") for r in block]), abs=1e-9
            )
            assert old["class_recall_at_k"] == pytest.approx(
                _mean([_value(r, "class_recall_at_k") for r in block]), abs=1e-9
            )


def test_precision_and_recall_are_recounted_from_the_per_rank_rows(
    cells: list[dict[str, str]], corrected: dict[str, Any]
) -> None:
    """A third table, one row per rank, must give the same Top-K counts as the ratio column."""
    hits: dict[tuple[str, str, str], int] = {}
    for row in _read(HITS):
        if row["condition"] != "T2":
            continue
        key = (row["arm"], row["query_id"], row["pose"])
        hits[key] = hits.get(key, 0) + int(row["is_same_class"] == "1")
    for cell in cells:
        if cell["condition"] != "T2":
            continue
        key = (cell["arm"], cell["query_id"], _pose(cell))
        assert key in hits, f"no stored ranks for {key}"
        returned = min(TOP_K, int(cell["n_kept"]))
        assert abs(hits[key] / returned - float(cell["class_recall_at_k"])) < 1e-12
    for arm in {row["arm"] for row in cells}:
        block = [
            r
            for r in cells
            if r["arm"] == arm and r["condition"] == "T2" and r["slot"] != "control"
        ]
        view = corrected["retrieval_by_arm"][arm][metrics.ROTATION_VIEW]["T2"]
        counted = [hits[(r["arm"], r["query_id"], _pose(r))] for r in block]
        assert view[f"precision_at_{TOP_K}"] == pytest.approx(
            _mean([c / min(TOP_K, int(r["n_kept"])) for c, r in zip(counted, block, strict=True)]),
            abs=1e-9,
        )
        assert view[f"recall_at_{TOP_K}"] == pytest.approx(
            _mean(
                [c / int(r["n_same_class_kept"]) for c, r in zip(counted, block, strict=True)]
            ),
            abs=1e-9,
        )


def test_recall_uses_a_per_query_denominator_and_precision_does_not(
    corrected: dict[str, Any], cells: list[dict[str, str]]
) -> None:
    view = corrected["retrieval_by_arm"]["E1"][metrics.ROTATION_VIEW]["T2"]
    assert view["n_relevant_retained_min"] < view["n_relevant_retained_max"]
    assert view["n_relevant_retained_min"] == min(
        int(r["n_same_class_kept"])
        for r in cells
        if r["arm"] == "E1" and r["condition"] == "T2" and r["slot"] != "control"
    )
    assert view[f"recall_at_{TOP_K}_undefined_cells"] == 0
    assert view[f"recall_at_{TOP_K}_n_scored"] == N_ROTATED_CELLS
    assert view[f"recall_at_{TOP_K}"] != pytest.approx(view[f"precision_at_{TOP_K}"])


def test_recall_of_a_query_with_nothing_left_to_find_is_undefined_not_zero() -> None:
    categories = ["heart", "bat", "bat"]
    assert metrics.recall_at_k(categories, "heart", 0, 3) is None
    assert metrics.precision_at_k(categories, "heart", 3) == pytest.approx(1 / 3)
    assert metrics.recall_at_k(categories, "heart", 1, 3) == pytest.approx(1.0)
    assert metrics.recall_at_k(categories, "heart", 4, 3) == pytest.approx(0.25)


def test_precision_of_a_short_library_is_over_what_was_returned() -> None:
    categories = ["heart", "bat", "bat"]
    assert metrics.precision_at_k(categories, "heart", 10) == pytest.approx(1 / 3)
    assert metrics.precision_at_k([], "heart", 10) == 0.0
    assert metrics.same_class_hits_in_top_k(categories, "heart", 2) == 1
    with pytest.raises(ValueError, match="k must be positive"):
        metrics.precision_at_k(categories, "heart", 0)
    with pytest.raises(ValueError, match="query category"):
        metrics.recall_at_k(categories, None, 1, 3)
    with pytest.raises(ValueError, match="negative"):
        metrics.recall_at_k(categories, "heart", -1, 3)


def test_the_legacy_column_is_precision_under_a_wrong_name_and_the_rename_is_arithmetic_free() -> (
    None
):
    categories = ["heart", "heart", "bat", None, "heart"]
    for k in (1, 2, 3, 5, 10):
        assert metrics.class_recall_at_k(categories, "heart", k) == pytest.approx(
            metrics.precision_at_k(categories, "heart", k)
        )


# ------------------------------------------------------------------------- the audit trail


def test_the_correction_records_the_exact_bytes_it_read(corrected: dict[str, Any]) -> None:
    audit = corrected["audit"]
    assert corrected["schema"] == "earthglyph.shape_perception_benchmark/d6_corrected/1"
    assert corrected["supersedes_only"] == ["retrieval_by_arm"]
    assert audit["original_file"]["schema"] == "earthglyph.shape_perception_benchmark/d6/1"
    assert audit["original_file"]["sha256"] == _sha(STORED)
    assert audit["corrected_file"]["schema"] == corrected["schema"]
    document = json.dumps(corrected["retrieval_by_arm"], sort_keys=True).encode("utf-8")
    assert audit["corrected_file"]["content_sha256"] == hashlib.sha256(document).hexdigest()
    for name, record in audit["inputs"].items():
        path = RESULTS / name
        assert record["sha256"] == _sha(path), name
        assert record["bytes"] == path.stat().st_size, name
    assert audit["inputs"]["d6_retrieval_results.csv"]["n_rows"] == 16 * 3 * N_STORED_CELLS
    assert "inference" in audit["method"]
    assert audit["command"] == "python scripts/run_shape_benchmark.py --stage d6-correct"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_history_files_are_declared_untouched_and_the_agreement_side_is_not_rewritten(
    corrected: dict[str, Any], stored: dict[str, Any]
) -> None:
    assert set(corrected["audit"]["history_untouched"]) == {
        "d6_rotation_metrics.json",
        "d6_retrieval_results.csv",
        "d6_top10_hits.csv",
        "d6_agreement_results.csv",
    }
    side = corrected["agreement_side_unchanged"]
    assert set(side["fields"]) <= set(stored)
    for field in side["fields"]:
        assert field not in corrected, f"{field} must stay only in the file that owns it"
    # the H1 agreement study is 250 pairs x 7 angles with a grouped bootstrap, not these 72 cells
    assert "1,750" in side["why"] or "1750" in side["why"]
    assert side["source_sha256"] == _sha(STORED)


def test_per_field_changes_cover_every_field_of_the_stored_block(
    corrected: dict[str, Any], stored: dict[str, Any], arms: list[str]
) -> None:
    skipped = {"n_cells", "exact_target_present"}
    for arm in arms:
        for condition, block in stored["retrieval_by_arm"][arm].items():
            entry = corrected["per_field_changes"][arm][condition]
            assert set(entry["changed"]) | set(entry["unchanged_fields"]) == set(block) - skipped
            assert entry["n_changed"] == len(entry["changed"])
            assert not set(entry["changed"]) & set(entry["unchanged_fields"])
            for field, change in entry["changed"].items():
                assert change["before"] == pytest.approx(block[field])
                after_field = change["corrected_field"]
                assert change["after"] == pytest.approx(
                    corrected["retrieval_by_arm"][arm][metrics.ROTATION_VIEW][condition][after_field]
                )
                assert change["control_only_value"] == pytest.approx(
                    corrected["retrieval_by_arm"][arm][metrics.CONTROL_VIEW][condition][after_field]
                )
                assert change["delta"] == pytest.approx(change["after"] - change["before"])
            assert entry["renamed_fields"] == {"class_recall_at_k": f"precision_at_{TOP_K}"}
            assert f"recall_at_{TOP_K}" in entry["added_fields"]


def test_the_pooled_control_rows_changed_the_naive_arms_most(corrected: dict[str, Any]) -> None:
    """The point of the fix: name the arms whose headline the controls were carrying."""
    view = corrected["retrieval_by_arm"]
    for arm in ("S0K1/naive", "S0K2/naive"):
        rotated = view[arm][metrics.ROTATION_VIEW]["T2"]["top1_stability_vs_unrotated"]
        control = view[arm][metrics.CONTROL_VIEW]["T2"]["top1_stability_vs_unrotated"]
        assert rotated < 0.11 and control == 1.0
    assert corrected["material_changes"], "the correction must not claim nothing moved"
    worst = corrected["material_changes"][0]
    assert abs(worst["delta"]) == max(abs(item["delta"]) for item in corrected["material_changes"])


def test_the_summariser_accepts_native_rows_as_well_as_stored_strings(
    runner: Any, cells: list[dict[str, str]]
) -> None:
    """``run_d6`` hands it in-memory ints and floats; a long run must not die at the summary."""
    native = [
        {
            **{key: value for key, value in row.items()},
            "angle_deg": float(row["angle_deg"]),
            "n_kept": int(row["n_kept"]),
            "n_same_class_kept": int(row["n_same_class_kept"]),
            "top1_is_same_class": int(row["top1_is_same_class"]),
            "top1_matches_unrotated": int(row["top1_matches_unrotated"]),
            "target_rank": None if row["target_rank"] == "" else int(row["target_rank"]),
            "self_distance": float(row["self_distance"]),
            "best_wrong_class_distance": (
                None if row["best_wrong_class_distance"] == ""
                else float(row["best_wrong_class_distance"])
            ),
            "class_recall_at_k": float(row["class_recall_at_k"]),
            "first_relevant_rank": (
                None if row["first_relevant_rank"] == "" else int(row["first_relevant_rank"])
            ),
            "ndcg_at_k": None if row["ndcg_at_k"] == "" else float(row["ndcg_at_k"]),
            "top_k_overlap_vs_unrotated": float(row["top_k_overlap_vs_unrotated"]),
            "mean_abs_rank_shift": float(row["mean_abs_rank_shift"]),
            "max_abs_rank_shift": float(row["max_abs_rank_shift"]),
            "elapsed_seconds": float(row["elapsed_seconds"]),
        }
        for row in cells
        if row["arm"] == "E1" and row["condition"] == "T2"
    ]
    from_native = runner._d6_summarise(native, TOP_K)
    from_strings = runner._d6_summarise(
        [row for row in cells if row["arm"] == "E1" and row["condition"] == "T2"], TOP_K
    )
    for view in metrics.VIEWS:
        block_native = from_native[view]["T2"]
        block_string = from_strings[view]["T2"]
        assert set(block_native) == set(block_string)
        for field, value in block_string.items():
            if isinstance(value, float):
                assert block_native[field] == pytest.approx(value, abs=1e-12), field
            else:
                assert block_native[field] == value, field
    assert from_native[metrics.ROTATION_VIEW]["T2"]["n_cells"] == N_ROTATED_CELLS


def test_corrected_table_still_matches_the_summariser_in_this_checkout(
    runner: Any, cells: list[dict[str, str]], corrected: dict[str, Any], arms: list[str]
) -> None:
    """Writer drift: if _d6_summarise changes without regenerating the artefact, this fails."""
    for arm in arms:
        rows = [row for row in cells if row["arm"] == arm]
        rebuilt = runner._d6_summarise([runner._d6_cell(row) for row in rows], TOP_K)
        published = corrected["retrieval_by_arm"][arm]
        assert set(rebuilt) == set(published) == set(metrics.VIEWS)
        for view in metrics.VIEWS:
            assert set(rebuilt[view]) == set(published[view])
            for condition in published[view]:
                a, b = rebuilt[view][condition], published[view][condition]
                assert set(a) == set(b)
                for field, value in b.items():
                    if isinstance(value, float):
                        assert a[field] == pytest.approx(value, abs=1e-12), f"{arm}/{view}/{field}"
                    else:
                        assert a[field] == value, f"{arm}/{view}/{field}"
