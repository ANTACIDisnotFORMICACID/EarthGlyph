"""Tests for the D2 unblinding step — that the verdict cannot outrun the evidence.

``scripts/visual_review_report.py`` is the only place where the sealed
``blind_mapping.json`` is joined to marks that were written down beforehand, so the
properties worth locking are the ones a reviewer would otherwise have to take on trust:
the scope is the scope the docstring declares, the join checks the mapping against the
template instead of assuming they agree, no row carries a distance or a relevance tag,
every mark is labelled ``AI_visual_review`` rather than human, the derived flags are the
stated rules, and the committed CSV/JSON still follow from the sealed score table.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("matplotlib")  # every test here renders figures

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "visual_review_report.py"
CHECKPOINT_D = ROOT / "experiments" / "p0_perceptual_probe" / "checkpoint_d"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("visual_review_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report() -> Any:
    return _load()


@pytest.fixture(scope="module")
def blind(report: Any) -> tuple[Any, Any, Any]:
    """(template, mapping cells, mapping meta) from the committed D1 artefacts."""
    template = report.load_template(CHECKPOINT_D / "visual_review_template.csv")
    cells, meta = report.load_mapping(CHECKPOINT_D / "blind_mapping.json")
    return template, cells, meta


# ------------------------------------------------------------------ the sealed table


def test_every_group_is_four_marks_between_zero_and_three(report: Any) -> None:
    for key, digits in report.SCORES.items():
        assert len(digits) == 4, key
        assert set(digits) <= set("0123"), key
        assert len(key.split("|")) == 3, key


def test_scope_is_what_the_docstring_declares(report: Any) -> None:
    """90 T2 groups + 35 T3 groups; anything else would quietly change the denominators."""
    t2 = {key for key in report.SCORES if key.startswith("T2|")}
    t3 = {key for key in report.SCORES if key.startswith("T3|")}
    assert len(t2) == 18 * 5 and len(t3) == 7 * 5
    assert {key.split("|")[1] for key in t3} == set(report.T3_QUERIES)
    assert report.VIEW == "display_aligned"


def test_scored_groups_exist_in_the_blind_material(report: Any, blind: Any) -> None:
    template, cells, meta = blind
    for key in report.SCORES:
        assert key in template, key
        assert key in cells, key
        assert meta[key]["query_id"], key
        assert meta[key]["family"], key


def test_t3_covers_one_representative_query_per_family(report: Any, blind: Any) -> None:
    _, _, meta = blind
    families = [
        meta[key]["family"]
        for key in sorted(report.SCORES)
        if key.startswith("T3|") and key.endswith("|1")
    ]
    assert len(families) == len(set(families)) == len(report.T3_QUERIES)


def test_hard_groups_are_real_scored_groups(report: Any) -> None:
    for key in report.HARD_GROUPS:
        assert key in report.SCORES, key


def test_override_reasons_point_at_cells_that_exist(report: Any) -> None:
    for key in report.OVERRIDE_REASON:
        group, letter = key[:-1], key[-1]
        assert letter in report.LETTERS, key
        assert group in report.SCORES, key


# ------------------------------------------------------------------ the join checks


def test_rows_carry_no_geometric_or_label_evidence(report: Any, blind: Any) -> None:
    """The marks are the input; a distance or REL column here would mean a second source."""
    rows = report.score_rows(*blind)
    assert set(rows[0]) == {
        "setting", "view", "query_code", "query_id", "rank", "blind_label", "arm",
        "candidate_code", "candidate_id", "family", "score_0_3", "key_structure_preserved",
        "obvious_error", "low_confidence", "reason", "reviewer", "status",
    }
    assert not [name for name in rows[0] if "distance" in name or "rel" in name.lower()]
    assert len(rows) == 4 * len(report.SCORES)


def test_every_cell_is_labelled_ai_review_not_human(report: Any, blind: Any) -> None:
    rows = report.score_rows(*blind)
    assert {row["reviewer"] for row in rows} == {"AI_visual_review"}
    assert {row["status"] for row in rows} == {"ai_scored"}


def test_mapping_and_template_are_reconciled_not_assumed(report: Any, blind: Any) -> None:
    """Every letter is checked: a silent mismatch would re-key the whole review."""
    template, cells, meta = blind
    key = next(
        group
        for group in sorted(report.SCORES)
        if len({cells[group][each]["candidate_code"] for each in "ABCD"}) > 1
    )
    for letter in "ABCD":
        tampered = {k: dict(v) for k, v in template.items()}
        other = next(
            code
            for code in {cells[key][each]["candidate_code"] for each in "ABCD"}
            if tampered[key][letter] != code
        )
        tampered[key][letter] = other
        with pytest.raises(SystemExit, match="disagree"):
            report.score_rows(tampered, cells, meta)


def test_a_group_outside_the_blind_material_is_rejected(
    report: Any, blind: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "SCORES", {"T2|Q99|1": "3333"})
    with pytest.raises(SystemExit, match="not a group in the blind template"):
        report.score_rows(*blind)


def test_a_malformed_score_string_is_rejected(
    report: Any, blind: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "SCORES", {"T2|Q01|1": "3334"})
    with pytest.raises(SystemExit, match="not four of 0..3"):
        report.score_rows(*blind)


# ------------------------------------------------------------------ derived flags


def _one_group_blind(report: Any, codes: dict[str, str]) -> tuple[Any, Any, Any]:
    key = "T2|Q01|1"
    template = {key: codes}
    cells = {
        key: {
            letter: {
                "arm": arm,
                "candidate_id": f"id-{letter}",
                "candidate_code": codes[letter],
            }
            for letter, arm in zip("ABCD", report.ARMS, strict=True)
        }
    }
    meta = {key: {"query_id": "syn-blob-query", "family": "blob"}}
    return template, cells, meta


def test_flat_marks_only_read_as_low_confidence_when_shapes_differ(
    report: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four identical marks on one candidate is a real tie, not an unsure reviewer."""
    monkeypatch.setattr(report, "SCORES", {"T2|Q01|1": "3333"})
    same = report.score_rows(*_one_group_blind(report, dict.fromkeys("ABCD", "C0001")))
    assert {row["low_confidence"] for row in same} == {0}

    codes = dict(zip("ABCD", ("C0001", "C0002", "C0003", "C0004"), strict=True))
    distinct = report.score_rows(*_one_group_blind(report, codes))
    assert {row["low_confidence"] for row in distinct} == {1}


def test_the_declared_hard_list_adds_no_hand_set_flag(report: Any, blind: Any) -> None:
    """low_confidence must stay mechanical, and this is the artefact that proves it.

    The list was written before the key was opened.  If any listed group needed the list
    - i.e. the automatic rule would have left it unflagged - the flag column would be
    partly hand-set and every low-confidence share in the summary would inherit that.
    """
    rows = report.score_rows(*blind)
    audit = report.hard_groups_audit(rows)
    assert audit["declared"] == len(report.HARD_GROUPS)
    assert audit["flagged_only_by_the_list"] == []
    assert audit["also_flagged_by_the_rule"] == audit["declared"]
    flagged = {
        f"{row['setting']}|{row['query_code']}|{row['rank']}"
        for row in rows
        if row["low_confidence"]
    }
    assert set(report.HARD_GROUPS) <= flagged


def test_audit_would_notice_a_hand_only_flag(report: Any) -> None:
    """The audit is not vacuous: spread marks over distinct codes trip it."""
    def group_rows(key: str, *, spread: bool) -> list[dict[str, Any]]:
        setting, query_code, rank = key.split("|")
        return [
            {
                "setting": setting,
                "query_code": query_code,
                "rank": rank,
                "candidate_code": f"C000{index}",
                "score_0_3": index if spread else 3,
            }
            for index in range(4)
        ]

    hard = list(report.HARD_GROUPS)
    rows = [row for key in hard for row in group_rows(key, spread=key == hard[0])]
    assert report.hard_groups_audit(rows)["flagged_only_by_the_list"] == [hard[0]]


def test_audit_reports_a_declared_group_that_is_not_in_the_data(report: Any) -> None:
    """A stale entry in the list is a defect, not a silent no-op."""
    rows = [
        {
            "setting": "T2",
            "query_code": "Q17",
            "rank": "3",
            "candidate_code": f"C000{index}",
            "score_0_3": 3,
        }
        for index in range(4)
    ]
    audit = report.hard_groups_audit(rows)
    assert set(audit["flagged_only_by_the_list"]) == set(report.HARD_GROUPS)
    assert audit["also_flagged_by_the_rule"] == 0


def test_flag_columns_are_the_stated_rules(report: Any, blind: Any) -> None:
    rows = report.score_rows(*blind)
    assert all(row["key_structure_preserved"] == int(row["score_0_3"] >= 2) for row in rows)
    assert all(row["obvious_error"] == int(row["score_0_3"] <= 1) for row in rows)


def test_reason_follows_the_mark_not_the_arm(report: Any, blind: Any) -> None:
    """A generic sentence is chosen by score alone, so two arms with the same mark read alike."""
    for row in report.score_rows(*blind):
        cell = f"{row['setting']}|{row['query_code']}|{row['rank']}{row['blind_label']}"
        specific = report.OVERRIDE_REASON.get(cell)
        assert row["reason"] == (specific or report.BASE_REASON[row["score_0_3"]])


# ------------------------------------------------------------------ metrics


def test_ndcg_rewards_the_arm_that_orders_its_own_good_shapes_first(report: Any) -> None:
    assert report.ndcg_at_five([3, 2, 1, 0, 0]) == 1.0
    assert report.ndcg_at_five([0, 0, 1, 2, 3]) < 1.0
    assert report.ndcg_at_five([2, 2, 2, 2, 2]) == 1.0


def test_ndcg_of_an_all_zero_group_is_pinned_to_one(report: Any) -> None:
    """Documented convention: no ideal ordering exists, so rank order cannot be blamed.

    Worth a test because it inflates a hopeless arm; the notes must not read NDCG alone.
    """
    assert report.ndcg_at_five([0, 0, 0, 0, 0]) == 1.0


def _synthetic_rows(report: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for arm, marks in (("E0", [3, 1, 1, 1, 1]), ("E1", [1, 1, 1, 1, 3])):
        for rank, value in enumerate(marks, start=1):
            out.append(
                {
                    "setting": "T2",
                    "query_code": "Q01",
                    "arm": arm,
                    "rank": rank,
                    "score_0_3": value,
                    "family": "blob",
                    "obvious_error": int(value <= 1),
                    "key_structure_preserved": int(value >= 2),
                    "low_confidence": 0,
                }
            )
    return out


def test_summarise_matches_a_hand_computation(report: Any) -> None:
    table = report.summarise(_synthetic_rows(report))
    assert table["E0"]["mean_top1_visual"] == 3.0
    assert table["E1"]["mean_top1_visual"] == 1.0
    assert table["E0"]["mean_top5_visual"] == round(7 / 5, 4)
    assert table["E1"]["mean_top5_visual"] == round(7 / 5, 4)
    assert table["E0"]["ndcg_at_5_from_visual_scores"] == 1.0
    assert table["E1"]["ndcg_at_5_from_visual_scores"] < 1.0
    assert table["E0"]["obvious_error_share"] == 0.8
    assert table["E0"]["n_queries"] == 1 and table["E0"]["n_cells"] == 5


def test_by_family_best_is_the_argmax_of_mean_top5(report: Any) -> None:
    table = report.by_family(_synthetic_rows(report))
    entry = table["blob"]
    assert entry["best"] == max(
        (arm for arm in report.ARMS if arm in entry), key=lambda arm: entry[arm]
    )
    assert entry["E0"] == entry["E1"]  # equal means, so the winner is a tie-break


def test_per_query_blocks_read_in_the_arm_s_own_order(report: Any, blind: Any) -> None:
    rows = report.score_rows(*blind)
    for (setting, query_code, arm), marks in report.per_query_blocks(rows).items():
        group = [
            row
            for row in rows
            if (row["setting"], row["query_code"], row["arm"]) == (setting, query_code, arm)
        ]
        assert marks == [row["score_0_3"] for row in sorted(group, key=lambda r: int(r["rank"]))]


# ------------------------------------------------------------------ committed artefacts


def test_committed_scores_csv_is_the_sealed_table_unblinded(report: Any, blind: Any) -> None:
    path = CHECKPOINT_D / "visual_review_scores.csv"
    assert path.exists(), "run scripts/visual_review_report.py"
    with path.open(encoding="utf-8", newline="") as handle:
        committed = list(csv.DictReader(handle))
    expected = report.score_rows(*blind)
    assert len(committed) == len(expected)
    for before, after in zip(committed, expected, strict=True):
        assert before["score_0_3"] == str(after["score_0_3"])
        assert before["arm"] == after["arm"]
        assert before["candidate_id"] == after["candidate_id"]
        assert before["reason"] == after["reason"]


def test_committed_summary_agrees_with_the_committed_csv(report: Any) -> None:
    summary = json.loads((CHECKPOINT_D / "visual_review_summary.json").read_text("utf-8"))
    with (CHECKPOINT_D / "visual_review_scores.csv").open(encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    for row in rows:
        for name in (
            "score_0_3",
            "rank",
            "key_structure_preserved",
            "obvious_error",
            "low_confidence",
        ):
            row[name] = int(row[name])
    for setting, block in (("T2", summary["T2"]), ("T3", summary["T3_representative"])):
        recomputed = report.summarise([row for row in rows if row["setting"] == setting])
        for arm, metrics in block["per_arm"].items():
            assert recomputed[arm]["mean_top5_visual"] == metrics["mean_top5_visual"], arm
            assert recomputed[arm]["ndcg_at_5_from_visual_scores"] == (
                metrics["ndcg_at_5_from_visual_scores"]
            ), arm
    assert summary["scope"]["cells_scored"] == len(rows)
    assert summary["not_human_subject_data"]
    assert summary["low_confidence_audit"]["flagged_only_by_the_list"] == []


def test_draw_examples_writes_a_png_without_covering_shapes(
    report: Any, blind: Any, tmp_path: Path
) -> None:
    rows = report.score_rows(*blind)
    out = tmp_path / "examples.png"
    info = report.draw_examples(rows, out, dpi=60)
    assert out.exists() and out.stat().st_size > 10_000
    assert len(info["groups"]) == 2 * len(report.ARMS)
