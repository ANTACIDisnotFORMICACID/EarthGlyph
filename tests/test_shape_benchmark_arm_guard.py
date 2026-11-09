"""Tests for the arm-set guard in the benchmark runner (P0-4.6 Checkpoint 7 follow-up).

``--stage d6`` with the default ``--methods`` used to rewrite the committed 16-arm artefacts as a
four-arm table and leave no trace that the wrapper comparison had been dropped. The guard is the
only thing standing between a forgotten flag and a rewritten result, so it is tested directly
rather than through a 60-minute run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_shape_benchmark.py"
RESULTS = ROOT / "experiments" / "shape_perception_benchmark" / "results"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> Any:
    return _load("run_shape_benchmark", SCRIPT)


def _labels(runner: Any, methods: list[str]) -> list[str]:
    return [arm.label for arm in runner._d6_arms(methods)]


def test_frozen_methods_are_the_default_plus_both_networks(runner: Any) -> None:
    assert list(runner.D6_FROZEN_METHODS) == [
        *runner.EARTHGLYPH_METHODS,
        *runner.SHAPECOMP_RUN_METHODS,
    ]


def test_default_methods_produce_the_narrow_table_the_guard_blocks(runner: Any) -> None:
    """The reported footgun: the CLI default is four arms, the committed table has sixteen."""
    assert len(_labels(runner, list(runner.EARTHGLYPH_METHODS))) == 4
    assert len(runner._d6_frozen_labels()) == 16


def test_guard_accepts_the_frozen_arm_set(runner: Any) -> None:
    runner._require_frozen_d6_arms(runner._d6_frozen_labels(), True, False)


def test_guard_refuses_to_narrow_an_existing_table(runner: Any) -> None:
    with pytest.raises(SystemExit) as raised:
        runner._require_frozen_d6_arms(
            _labels(runner, list(runner.EARTHGLYPH_METHODS)), True, False
        )
    message = str(raised.value)
    assert "16-arm" in message
    assert "--methods E0 E1 E3 S0K1 S0K2" in message
    assert "--allow-d6-arm-subset" in message
    for name in runner.D6_ARTEFACTS:
        assert name in message


def test_guard_steps_aside_when_told_to_or_when_nothing_exists_yet(runner: Any) -> None:
    narrow = _labels(runner, list(runner.EARTHGLYPH_METHODS))
    runner._require_frozen_d6_arms(narrow, True, True)
    runner._require_frozen_d6_arms(narrow, False, False)


def test_guard_message_names_the_arm_count_being_lost(runner: Any) -> None:
    with pytest.raises(SystemExit) as raised:
        runner._require_frozen_d6_arms(_labels(runner, ["E0", "E1", "E3", "S0K1"]), True, False)
    assert "with a 10-arm table" in str(raised.value)


@pytest.mark.skipif(
    not (RESULTS / "d6_rotation_metrics.json").exists(),
    reason="the CP7-C study has not been run in this checkout",
)
def test_frozen_labels_match_the_committed_arm_order(runner: Any) -> None:
    """The guard's idea of 'frozen' is the artefact's, not a hand-copied list."""
    committed = json.loads((RESULTS / "d6_rotation_metrics.json").read_text(encoding="utf-8"))
    assert runner._d6_frozen_labels() == [arm["label"] for arm in committed["arms"]]
