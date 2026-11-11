"""Tests for the CP8-B independent validation design (P0-4.6 Checkpoint 8, Task D).

Checkpoint 7 burned twelve queries and five angles per arm, so any "the wrappers generalise" claim
rests on the shapes it was tuned against. This design is the opposite: 24 MPEG-7 instances that
never entered a scored cell, five angles each, drawn by rule from one seed and committed **before**
retrieval runs. The tests therefore pin the *promise*, not a performance number:

* the query set is disjoint from every stem a scored cell used, and re-derivable from the seed;
* the angles satisfy the slot arithmetic they are labelled with, including "random";
* the two views stay separate (120 rotated cells, 24 controls, never pooled into 144);
* the coverage audit is pure arithmetic over stored descriptors, so a shortfall cannot be edited
  away by loosening a threshold -- near_circle is recorded as 0/4 and a test pins that.

Nothing here scores a shape or ranks a candidate, because nothing here may depend on a result.
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

from earthglyph.shape_benchmark.datasets import BenchmarkShape, load_mpeg7
from earthglyph.shape_benchmark.independent_validation import (
    BURNED_QUERY_STEMS,
    CP8_ANGLE_SLOTS,
    CP8_ARMS,
    CP8_CONTROL_SLOT,
    CP8_OFF_GRID_MARGIN_DEG,
    CP8_QUERIES_PER_FAMILY,
    CP8_ROTATION_STEPS,
    CP8_SEED,
    FAMILIES,
    FAMILY_CRITERIA,
    INDEPENDENCE_LIMITS,
    _off_grid,
    angle_grid,
    audit_coverage,
    build_design,
    contour_descriptors,
    design_content_sha256,
    draw_angles,
    select_queries,
)

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "experiments" / "shape_perception_benchmark"
CACHE = BENCH / "cache"
DESIGN_PATH = BENCH / "checkpoint8" / "independent_validation_design.json"
MANIFEST = BENCH / "dataset_manifest.json"
SCRIPT = ROOT / "scripts" / "design_checkpoint8_validation.py"
CELLS = BENCH / "results" / "d6_retrieval_results.csv"

N_FAMILIES = 6
N_QUERIES = 24
N_ROTATED = 5
N_ROTATED_CELLS = N_QUERIES * N_ROTATED
N_CONTROL_CELLS = N_QUERIES
STEP_DEG = 360.0 / 24
TOLERANCE = 1e-9


def ring(lobes: int = 0, radius: float = 1.0, n: int = 128) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = radius * (1.0 + 0.3 * np.cos(lobes * theta)) if lobes else np.full(n, radius)
    points = np.column_stack([r * np.cos(theta), r * np.sin(theta)])
    centered = points - points.mean(axis=0)
    return centered / float(np.sqrt(np.mean(np.sum(centered**2, axis=1))))


def fake_shape(stem: str, category: str) -> BenchmarkShape:
    points = ring(lobes=3)
    return BenchmarkShape(
        shape_id=f"mpeg7_{stem}",
        source="synthetic",
        points=points,
        display_points=points,
        category=category,
    )


ALL_CLASSES = tuple(sorted({name for family in FAMILIES for name in family.classes}))


def fake_pool() -> list[BenchmarkShape]:
    """Five synthetic instances per class the design needs -- enough for any family to draw."""
    return [
        fake_shape(f"{name}-{index}", name) for name in ALL_CLASSES for index in range(1, 6)
    ]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def design_module() -> Any:
    return _load("cp8_design_script", SCRIPT)


@pytest.fixture(scope="module")
def runner_module() -> Any:
    return _load("cp8_runner_for_burned_stems", ROOT / "scripts" / "run_shape_benchmark.py")


@pytest.fixture(scope="module")
def committed_design() -> dict[str, Any]:
    if not DESIGN_PATH.exists():
        pytest.skip("the CP8-B design has not been frozen in this checkout")
    return json.loads(DESIGN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mpeg7_shapes() -> list[Any]:
    if not (CACHE / "MPEG7dataset.zip").exists():
        pytest.skip("source cache not fetched")
    pytest.importorskip("PIL")  # load_mpeg7 decodes the GIFs with Pillow
    shapes, _ = load_mpeg7(CACHE)
    return shapes


# ------------------------------------------------------------------ the rules as written


def test_six_families_span_every_shape_type_the_brief_names() -> None:
    """The brief lists six shape families; a design that quietly drops one is not a design."""
    assert [family.key for family in FAMILIES] == [
        "pointed",
        "heart",
        "curved",
        "forked",
        "near_circle",
        "irregular_natural",
    ]
    assert {family.label_zh for family in FAMILIES} == {
        "尖角",
        "心形",
        "弯曲",
        "分叉",
        "接近圆形",
        "不规则自然剪影",
    }
    for family in FAMILIES:
        assert family.why_these_classes and family.measurable_expectation and family.gap
        assert family.classes, family.key
    assert set(FAMILY_CRITERIA) == {family.key for family in FAMILIES}


def test_query_count_clears_the_twenty_floor_by_rule() -> None:
    """Four per family either way: one instance of each of four classes, or four of one class."""
    total = 0
    for family in FAMILIES:
        count = 1 if family.per_class else CP8_QUERIES_PER_FAMILY
        assert count * len(family.classes) == CP8_QUERIES_PER_FAMILY, family.key
        total += count * len(family.classes)
    assert total == N_FAMILIES * CP8_QUERIES_PER_FAMILY == N_QUERIES
    assert N_QUERIES >= 20, "the brief asks for at least 20 new queries"


def test_angle_slots_are_one_grid_two_midpoints_two_random_plus_a_control() -> None:
    """The D6 slot mix, unchanged: a different mix would make the two studies uncomparable."""
    assert CP8_ANGLE_SLOTS == ("on_grid", "mid_grid", "mid_grid", "random", "random")
    assert CP8_ANGLE_SLOTS.count("on_grid") == 1
    assert CP8_ANGLE_SLOTS.count("mid_grid") == 2
    assert CP8_ANGLE_SLOTS.count("random") == 2
    assert CP8_CONTROL_SLOT == "control"
    assert CP8_CONTROL_SLOT not in CP8_ANGLE_SLOTS
    assert CP8_ROTATION_STEPS == 24


def test_grid_arithmetic_is_stated_not_implied() -> None:
    grid = angle_grid()
    assert grid["step_deg"] == pytest.approx(STEP_DEG)
    assert "multiple of the step" in grid["on_grid"]
    assert "half a step" in grid["mid_grid"]
    assert f"{CP8_OFF_GRID_MARGIN_DEG} deg" in grid["random"]


def test_draw_angles_is_reproducible_from_the_seed_only() -> None:
    first = draw_angles(np.random.default_rng(CP8_SEED), N_QUERIES)
    second = draw_angles(np.random.default_rng(CP8_SEED), N_QUERIES)
    assert first == second
    other_seed = draw_angles(np.random.default_rng(CP8_SEED + 1), N_QUERIES)
    assert other_seed != first


def _residue(deg: float, offset: float) -> float:
    """Distance from ``deg`` to the ``offset``-shifted grid, the way the drawer defines it."""
    value = abs((deg - offset) % STEP_DEG)
    return min(value, STEP_DEG - value)


def test_every_drawn_angle_satisfies_its_slot() -> None:
    rows = draw_angles(np.random.default_rng(CP8_SEED), N_QUERIES)
    assert len(rows) == N_ROTATED_CELLS
    for row in rows:
        deg = row["angle_deg"]
        assert 0.0 < deg < 360.0, row
        grid = _residue(deg, 0.0)
        midpoint = _residue(deg, STEP_DEG / 2.0)
        if row["slot"] == "on_grid":
            assert grid < TOLERANCE, row
        elif row["slot"] == "mid_grid":
            assert midpoint < TOLERANCE, row
        else:
            assert min(grid, midpoint) > CP8_OFF_GRID_MARGIN_DEG, row


def test_slot_mix_per_query_survives_the_draw() -> None:
    rows = draw_angles(np.random.default_rng(CP8_SEED), N_QUERIES)
    for index in range(N_QUERIES):
        slots = [row["slot"] for row in rows if row["query_index"] == index]
        assert slots == ["on_grid", "mid_grid", "mid_grid", "random", "random"]


def test_off_grid_draw_refuses_a_grid_angle_in_disguise() -> None:
    """An "arbitrary" angle one degree off a grid line is a grid angle; it must be rejected."""

    class StubRng:
        def __init__(self, values: list[float]) -> None:
            self.values = values
            self.calls = 0

        def uniform(self, _low: float, high: float) -> float:
            value = self.values[min(self.calls, len(self.values) - 1)]
            self.calls += 1
            return value

    stub = StubRng([16.0, 7.5, 100.0])
    assert _off_grid(stub, STEP_DEG) == pytest.approx(100.0)
    assert stub.calls == 3


def test_off_grid_draw_gives_up_loudly_rather_than_returning_a_bad_angle() -> None:
    class StubRng:
        def uniform(self, _low: float, _high: float) -> float:
            return 15.5

    with pytest.raises(RuntimeError, match="off-grid"):
        _off_grid(StubRng(), STEP_DEG)


# ------------------------------------------------------------------ the descriptors


def test_descriptors_are_invariant_to_rotation_and_scale() -> None:
    """Family evidence must not depend on the pose it is measured in, or it proves nothing."""
    base = ring(lobes=3)
    reference = contour_descriptors(base)
    angle = np.deg2rad(37.0)
    rotated = base @ np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    for key, value in contour_descriptors(rotated).items():
        assert value == pytest.approx(reference[key], rel=1e-9), key
    scaled = contour_descriptors(base * 3.7)
    for key in ("circularity", "solidity", "elongation", "deep_gap_runs", "deepest_gap"):
        assert scaled[key] == pytest.approx(reference[key], rel=1e-9), key
    assert scaled["area"] == pytest.approx(reference["area"] * 3.7**2, rel=1e-9)


def test_descriptors_distinguish_a_disc_from_a_three_lobed_outline() -> None:
    disc = contour_descriptors(ring())
    three_lobe = contour_descriptors(ring(lobes=3))
    assert disc["deep_gap_runs"] == 0.0
    assert disc["circularity"] > 0.95
    assert three_lobe["deep_gap_runs"] == 3.0
    assert three_lobe["solidity"] < disc["solidity"]
    assert three_lobe["circularity"] < disc["circularity"]


def test_descriptors_refuse_input_that_is_not_the_contour_contract() -> None:
    with pytest.raises(ValueError, match="128 points"):
        contour_descriptors(ring(n=64))
    with pytest.raises(ValueError, match=r"\(n, 2\)"):
        contour_descriptors(np.zeros((128, 3)))
    with pytest.raises(ValueError, match=r"\(n, 2\)"):
        contour_descriptors(np.zeros(128))


# ------------------------------------------------------------------ selection by rule


def test_select_queries_refuses_a_missing_class() -> None:
    shapes = [s for s in fake_pool() if "pencil" not in s.shape_id]
    with pytest.raises(ValueError, match="pencil"):
        select_queries(shapes)


def test_select_queries_refuses_an_underfilled_pool() -> None:
    """Three spare hearts cannot supply the four the single-class family asks for."""
    shapes = [s for s in fake_pool() if not s.shape_id.startswith("mpeg7_Heart-")]
    shapes += [fake_shape(f"Heart-{i}", "Heart") for i in (2, 3, 4)]
    with pytest.raises(ValueError, match="only 3 unused shapes for 4"):
        select_queries(shapes)


def test_select_queries_never_reuses_a_burned_stem() -> None:
    shapes = fake_pool() + [
        fake_shape(stem, stem.rpartition("-")[0]) for stem in BURNED_QUERY_STEMS
    ]
    chosen = select_queries(shapes)
    stems = {row["stem"] for row in chosen}
    assert not stems & set(BURNED_QUERY_STEMS)
    assert len(stems) == N_QUERIES


def test_selection_is_reproducible_and_disjoint_across_families() -> None:
    shapes = fake_pool()
    first = select_queries(shapes)
    assert first == select_queries(shapes)
    assert len({row["stem"] for row in first}) == N_QUERIES
    for family in FAMILIES:
        rows = [row for row in first if row["family"] == family.key]
        assert len(rows) == CP8_QUERIES_PER_FAMILY


def test_burned_stems_are_exactly_the_runners_scored_queries(runner_module: Any) -> None:
    """The list cannot drift from the study it protects against without a red build."""
    burned = {stem.removeprefix("mpeg7_") for stem, _ in runner_module.D6_QUERIES} | {
        stem for stem in runner_module.D2_QUERIES
    }
    assert burned == set(BURNED_QUERY_STEMS)


def test_arms_include_the_brief_minimum_and_no_more() -> None:
    assert CP8_ARMS == ("E0", "E1", "E3/sample", "S0K1/R3", "S0K2/R3", "S0K2/R2")
    assert set(CP8_ARMS) >= {"E0", "E1", "E3/sample", "S0K1/R3", "S0K2/R3"}


def test_independence_limits_name_the_three_things_this_cannot_show() -> None:
    text = "\n".join(INDEPENDENCE_LIMITS)
    assert "No new human ratings" in text
    assert "gallery is not" in text
    assert "object labels" in text
    assert "coverage_audit" in text


# ------------------------------------------------------------------ the coverage audit


def test_audit_counts_are_pure_arithmetic_over_stored_descriptors() -> None:
    queries = [
        {
            "stem": f"{name}-{i}",
            "family": family.key,
            "descriptors": {
                "circularity": 0.9,
                "solidity": 0.9,
                "elongation": 0.9,
                "deep_gap_runs": 1.0,
            },
        }
        for family in FAMILIES
        for i in range(CP8_QUERIES_PER_FAMILY)
        for name in family.classes[:1]
    ]
    audit = audit_coverage(queries)
    assert audit["n_queries"] == len(queries)
    assert audit["per_family"]["pointed"]["n_meeting"] == 0  # solidity 0.9 > 0.85
    assert audit["per_family"]["heart"]["n_meeting"] == CP8_QUERIES_PER_FAMILY
    assert audit["per_family"]["near_circle"]["n_meeting"] == CP8_QUERIES_PER_FAMILY
    assert audit["per_family"]["curved"]["n_meeting"] == 0  # elongation 0.9 > 0.65
    assert audit["per_family"]["forked"]["n_meeting"] == 0  # needs >= 2 gaps
    assert all(row["violations"] for row in audit["per_query"] if not row["meets_expectation"])


def test_audit_flags_only_the_bound_that_actually_failed() -> None:
    queries = [
        {
            "stem": "watch-1",
            "family": "near_circle",
            "descriptors": {"circularity": 0.36, "solidity": 0.8, "elongation": 0.24},
        }
    ]
    row = audit_coverage(queries)["per_query"][0]
    assert row["meets_expectation"] is False
    assert {v["metric"] for v in row["violations"]} == {"circularity", "elongation"}


# ------------------------------------------------------------------ the frozen artefact


def test_committed_design_matches_the_frozen_rules(
    committed_design: dict[str, Any], mpeg7_shapes: list[Any]
) -> None:
    """Re-deriving the design from the dataset must reproduce the committed hash exactly."""
    rebuilt = build_design(mpeg7_shapes)
    assert design_content_sha256(rebuilt) == committed_design["content_sha256"]
    assert rebuilt["queries"] == committed_design["queries"]
    assert rebuilt["angle_design"]["rows"] == committed_design["angle_design"]["rows"]
    assert rebuilt["coverage_audit"] == committed_design["coverage_audit"]


def test_committed_design_counts_and_arithmetic(committed_design: dict[str, Any]) -> None:
    assert committed_design["n_queries"] == N_QUERIES
    assert committed_design["seed"] == CP8_SEED
    conditions = committed_design["conditions"]
    assert conditions["primary"] == "T2"
    assert conditions["correctness_check"] == "T0"
    assert set(conditions["definitions"]) == {"T0", "T1", "T2"}
    assert "rigid near-duplicates" in conditions["definitions"]["T2"]
    cells = committed_design["cell_arithmetic"]
    assert cells["n_rotated_cells_per_arm_condition"] == N_ROTATED_CELLS
    assert cells["n_control_cells_per_arm_condition"] == N_CONTROL_CELLS
    assert cells["n_cells_per_arm_condition"] == N_ROTATED_CELLS + N_CONTROL_CELLS
    assert "never pooled" in cells["note"]
    assert committed_design["arms"]["labels"] == list(CP8_ARMS)
    statistics = committed_design["statistics"]
    assert statistics["n_boot"] == 2000
    assert "original query" in statistics["statistical_unit"]
    assert len(statistics["report_separately"]) == 3, "category / stability / cost, reported apart"
    assert any("Precision@K" in line for line in statistics["metric_conventions"])


def test_every_cp8_arm_is_a_frozen_d6_arm(committed_design: dict[str, Any]) -> None:
    """Six arms are a *selection* from the sixteen already run, not six new implementations."""
    if not CELLS.exists():
        pytest.skip("the frozen D6 retrieval table is not in this checkout")
    with CELLS.open(newline="", encoding="utf-8") as handle:
        stored_arms = {row["arm"] for row in csv.DictReader(handle)}
    assert set(committed_design["arms"]["labels"]) <= stored_arms
    absent = stored_arms - set(CP8_ARMS)
    assert len(stored_arms) == 16 and len(absent) == 10
    assert {"E3/canonical", "S0K1/naive", "S0K2/naive", "S0K1/R0", "S0K2/R0"} <= absent
    assert {a for a in absent if "R1_" in a} == {
        "S0K1/R1_36",
        "S0K1/R1_72",
        "S0K2/R1_36",
        "S0K2/R1_72",
    }
    assert not [a for a in absent if a.endswith("/R3")], "both R3 wrappers are selected"


def test_committed_queries_are_disjoint_from_every_burned_stem(
    committed_design: dict[str, Any], runner_module: Any
) -> None:
    stems = [row["stem"] for row in committed_design["queries"]]
    assert len(stems) == len(set(stems)) == N_QUERIES
    burned = {stem.removeprefix("mpeg7_") for stem, _ in runner_module.D6_QUERIES} | {
        stem for stem in runner_module.D2_QUERIES
    }
    assert not set(stems) & burned
    assert not set(stems) & set(BURNED_QUERY_STEMS)


def test_committed_angles_satisfy_their_slots(committed_design: dict[str, Any]) -> None:
    rows = committed_design["angle_design"]["rows"]
    assert len(rows) == N_ROTATED_CELLS
    assert {row["slot"] for row in rows} == {"on_grid", "mid_grid", "random"}
    for row in rows:
        deg = row["angle_deg"]
        assert 0.0 < deg < 360.0
        if row["slot"] == "random":
            assert min(_residue(deg, 0.0), _residue(deg, STEP_DEG / 2.0)) > CP8_OFF_GRID_MARGIN_DEG


def test_committed_audit_recomputes_from_the_stored_descriptors(
    committed_design: dict[str, Any]
) -> None:
    """The audit only views the descriptors already in the file, so it cannot contradict them."""
    assert audit_coverage(committed_design["queries"]) == committed_design["coverage_audit"]


def test_committed_audit_records_the_near_circle_shortfall(
    committed_design: dict[str, Any]
) -> None:
    """Pinned so a threshold cannot be loosened later to make the family look covered."""
    audit = committed_design["coverage_audit"]
    assert audit["per_family"]["near_circle"]["n_meeting"] == 0
    assert audit["per_family"]["near_circle"]["failing_stems"] == [
        "HCircle-8",
        "watch-11",
        "device2-6",
        "apple-14",
    ]
    assert audit["per_family"]["heart"]["n_meeting"] == 4
    assert audit["per_family"]["forked"]["n_meeting"] == 4
    assert audit["per_family"]["irregular_natural"]["n_meeting"] == 4
    assert audit["n_queries_meeting"] == 17
    assert audit["n_queries"] == N_QUERIES


def test_committed_design_is_a_design_not_a_result(committed_design: dict[str, Any]) -> None:
    """No distance, rank or hit may be recorded here -- the file exists before retrieval runs."""

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return {key for item in node.items() for key in {item[0]} | keys(item[1])}
        if isinstance(node, list):
            return {key for item in node for key in keys(item)}
        return set()

    names = keys(committed_design)
    assert not {name.lower() for name in names} & {
        "distances",
        "rankings",
        "hits",
        "target_rank",
        "ndcg_at_k",
        "mrr",
        "precision_at_k",
        "recall_at_k",
        "elapsed_seconds",
        "scores",
    }
    assert "coverage_audit" in names and "angle_design" in names


def test_committed_provenance_matches_the_manifest(committed_design: dict[str, Any]) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    block = committed_design["provenance"]["dataset"]
    assert block["archive_sha256"] == manifest["mpeg7"]["sha256"]
    assert block["archive_sha256_verified_on_disk"] == manifest["mpeg7"]["sha256"]
    assert committed_design["provenance"]["seed"] == CP8_SEED
    archive = CACHE / "MPEG7dataset.zip"
    if archive.exists():
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == block["archive_sha256"]


# ------------------------------------------------------------------ the freeze mechanics


def test_freeze_writes_once_and_then_reports_unchanged(
    design_module: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    design = {"seed": CP8_SEED}
    digest = "abc123"
    design_path = tmp_path / "design.json"
    design_module.verify_or_write(design, digest, "24 queries", design_path=design_path)
    assert "frozen 24 queries" in capsys.readouterr().out
    design_module.verify_or_write(design, digest, "24 queries", design_path=design_path)
    assert "already frozen" in capsys.readouterr().out
    assert design_path.read_text(encoding="utf-8") == (
        json.dumps({**design, "content_sha256": digest}, indent=2, sort_keys=True) + "\n"
    )


def test_freeze_refuses_to_rewrite_a_different_design(
    design_module: Any, tmp_path: Path
) -> None:
    design_path = tmp_path / "design.json"
    design_path.write_text(json.dumps({"content_sha256": "old"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        design_module.verify_or_write(
            {"seed": CP8_SEED}, "new", "24 queries", design_path=design_path
        )
    assert "DESIGN DRIFT" in str(raised.value)
    assert "new study" in str(raised.value)
    assert json.loads(design_path.read_text(encoding="utf-8"))["content_sha256"] == "old"


def test_check_refuses_when_no_design_exists(design_module: Any, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no design"):
        design_module.verify_or_write(
            {"seed": CP8_SEED}, "digest", "24 queries", design_path=tmp_path / "missing.json",
            check_only=True,
        )


def test_a_different_dataset_build_is_refused(design_module: Any, tmp_path: Path) -> None:
    archive = tmp_path / "MPEG7dataset.zip"
    archive.write_bytes(b"not the frozen build")
    actual = hashlib.sha256(b"not the frozen build").hexdigest()
    assert design_module.check_dataset(archive, actual) == actual
    with pytest.raises(SystemExit, match="different study"):
        design_module.check_dataset(archive, "0" * 64)
    assert design_module.check_dataset(tmp_path / "absent.zip", "0" * 64) is None


def test_the_frozen_design_would_survive_a_second_invocation(
    design_module: Any, tmp_path: Path
) -> None:
    """Writing the real committed design twice is a no-op, which is what 'frozen' has to mean."""
    if not DESIGN_PATH.exists():
        pytest.skip("the CP8-B design has not been frozen in this checkout")
    design = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    digest = design_content_sha256(design)
    assert digest == design["content_sha256"]
    target = tmp_path / "design.json"
    design_module.verify_or_write(design, digest, "24 queries", design_path=target)
    assert json.loads(target.read_text(encoding="utf-8"))["content_sha256"] == digest
