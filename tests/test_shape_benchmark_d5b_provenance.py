"""Tests for the supersession record written by ``--stage d5b`` (CP7 follow-up).

The d5b stage re-derives its own artefact, so a second run overwrites a file it wrote itself.
Recording *that* file as the superseded one would replace the only pointer to the `/1` study and
silently break the audit relation the schema bump exists to create, so the record has to survive
re-runs. Tested against the writer directly instead of by re-running the stage.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_shape_benchmark.py"
RESULTS = ROOT / "experiments" / "shape_perception_benchmark" / "results"


@pytest.fixture(scope="module")
def runner() -> Any:
    spec = importlib.util.spec_from_file_location("run_shape_benchmark", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_nothing_is_superseded_before_the_first_run(runner: Any, tmp_path: Path) -> None:
    assert runner._superseded_d5b_record(tmp_path / "absent.json", runner.D5_GROUPED_SCHEMA) == {
        "present": False
    }


def test_a_different_schema_is_recorded_with_its_hash_and_size(runner: Any, tmp_path: Path) -> None:
    prior = tmp_path / "d5_grouped_metrics.json"
    _write(prior, {"schema": "earthglyph.shape_perception_benchmark/d5_grouped/1"})
    record = runner._superseded_d5b_record(prior, runner.D5_GROUPED_SCHEMA)
    assert record["present"] is True
    assert record["schema"].endswith("d5_grouped/1")
    assert record["sha256"] == hashlib.sha256(prior.read_bytes()).hexdigest()
    assert record["bytes"] == prior.stat().st_size
    assert record["path"].endswith("d5_grouped_metrics.json")
    assert "agreement_first_minus_second" in record["relation"]


def test_rerunning_the_stage_carries_the_original_supersession_forward(
    runner: Any, tmp_path: Path
) -> None:
    prior = tmp_path / "d5_grouped_metrics.json"
    first = {
        "sha256": "a" * 64,
        "bytes": 21056,
        "schema": "earthglyph.shape_perception_benchmark/d5_grouped/1",
        "present": True,
    }
    current = {"schema": runner.D5_GROUPED_SCHEMA, "audit_relation": {"superseded_file": first}}
    _write(prior, current)
    assert runner._superseded_d5b_record(prior, runner.D5_GROUPED_SCHEMA) == first


def test_a_current_schema_file_without_a_record_reports_nothing_superseded(
    runner: Any, tmp_path: Path
) -> None:
    prior = tmp_path / "d5_grouped_metrics.json"
    _write(prior, {"schema": runner.D5_GROUPED_SCHEMA})
    assert runner._superseded_d5b_record(prior, runner.D5_GROUPED_SCHEMA) == {"present": False}


def test_unreadable_payload_is_not_treated_as_a_superseded_study(
    runner: Any, tmp_path: Path
) -> None:
    prior = tmp_path / "d5_grouped_metrics.json"
    prior.write_text("[1, 2, 3]", encoding="utf-8")
    assert runner._superseded_d5b_record(prior, runner.D5_GROUPED_SCHEMA) == {"present": False}


@pytest.mark.skipif(
    not (RESULTS / "d5_grouped_metrics.json").exists(),
    reason="the CP7-B re-analysis has not been run in this checkout",
)
def test_committed_artefact_still_points_at_the_d5_grouped_1_file(runner: Any) -> None:
    """The real `/2` file re-read by the writer must not lose its link to the `/1` file."""
    committed = RESULTS / "d5_grouped_metrics.json"
    stored = json.loads(committed.read_text(encoding="utf-8"))
    assert stored["schema"] == runner.D5_GROUPED_SCHEMA
    again = runner._superseded_d5b_record(committed, runner.D5_GROUPED_SCHEMA)
    assert again == stored["audit_relation"]["superseded_file"]
    assert again["schema"].endswith("d5_grouped/1")
    assert again["sha256"] != hashlib.sha256(committed.read_bytes()).hexdigest()
