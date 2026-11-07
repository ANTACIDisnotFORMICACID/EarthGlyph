"""Tests for the blinding itself — the part that makes a visual review mean anything.

``scripts/blind_perceptual_review.py`` draws pictures, but the claims worth testing are
the ones a picture cannot show: that the letter assignment is reproducible from the seed,
that it is a permutation rather than a relabelling, that no candidate is invented or
dropped, and that the answer sheet carries no column an arm could be recognised from.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "blind_perceptual_review.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("blind_perceptual_review", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def blind() -> Any:
    return _load_module()


def record(query: str, rank: int, arm: str, candidate: str, setting: str = "T2") -> dict:
    return {
        "setting": setting,
        "query_id": query,
        "rank": rank,
        "arm": arm,
        "candidate_id": candidate,
    }


class TestPermutation:
    def test_every_arm_gets_exactly_one_letter_per_group(self, blind: Any) -> None:
        records = [record("q1", 1, arm, f"c{arm}") for arm in ("E0", "E1", "E2", "E3")]
        blind.blind_cells(records, seed=1)
        letters = sorted(record["blind_label"] for record in records)
        assert letters == ["A", "B", "C", "D"]

    def test_the_seed_reproduces_the_key_and_a_different_seed_does_not(self, blind: Any) -> None:
        def key(seed: int) -> dict:
            records = [record("q1", rank, arm, f"c{rank}{arm}") for rank in (1, 2, 3)
                        for arm in ("E0", "E1", "E2", "E3")]
            return blind.blind_cells(records, seed=seed)

        first, repeat, other = key(7), key(7), key(8)
        assert first == repeat
        # a different seed must actually shuffle, or "randomised" would be decoration
        assert first != other

    def test_groups_are_permuted_independently(self, blind: Any) -> None:
        """One shared permutation for every row would let a column become an arm."""
        records = [
            record("q1", 1, arm, f"c1{arm}") for arm in ("E0", "E1", "E2", "E3")
        ] + [record("q2", 1, arm, f"c2{arm}") for arm in ("E0", "E1", "E2", "E3")]
        assignments = blind.blind_cells(records, seed=20261107)
        keys = sorted(assignments)
        assert keys == [("T2", "q1", 1), ("T2", "q2", 1)]
        # with four arms the chance two independent draws agree is 1/24; over many seeds
        # they must not always agree
        agreements = 0
        for seed in range(40):
            per_seed = [
                record(f"g{group}", 1, arm, f"c{group}{arm}")
                for group in (1, 2)
                for arm in ("E0", "E1", "E2", "E3")
            ]
            made = blind.blind_cells(per_seed, seed=seed)
            agreements += int(made[("T2", "g1", 1)] == made[("T2", "g2", 1)])
        assert agreements < 40

    def test_no_candidate_is_invented_or_dropped(self, blind: Any) -> None:
        records = [record("q1", 1, arm, f"c{arm}") for arm in ("E0", "E1", "E2", "E3")]
        blind.blind_cells(records, seed=3)
        assert sorted(record["candidate_id"] for record in records) == [
            "cE0",
            "cE1",
            "cE2",
            "cE3",
        ]


class TestCodes:
    def test_codes_are_opaque_and_stable(self, blind: Any) -> None:
        records = [
            record("syn-heart-query", 1, "E0", "syn-heart-e3"),
            record("syn-heart-query", 2, "E1", "qd-star-variant-02"),
        ]
        queries, candidates = blind.make_codes(records)
        assert queries["syn-heart-query"] == "Q01"
        assert set(candidates.values()) == {"C0001", "C0002"}
        # assigned on sorted ids, so the code a reader sees never depends on row order
        assert candidates["qd-star-variant-02"] == "C0001"
        assert candidates["syn-heart-e3"] == "C0002"
        # re-running on the same ids must hand back the same codes, or the key would
        # not describe the sheet it shipped with
        assert blind.make_codes(records) == (queries, candidates)

    def test_a_shared_candidate_shares_one_code_across_settings(self, blind: Any) -> None:
        records = [
            record("q1", 1, "E0", "shared", setting="T2"),
            record("q1", 1, "E1", "shared", setting="T3"),
        ]
        _, candidates = blind.make_codes(records)
        assert len(candidates) == 1


class TestCells:
    def test_reads_the_stored_rank_and_refuses_a_disordered_table(self, blind: Any) -> None:
        contours = blind.Contours()
        contours.register("q", np.zeros((128, 2)))
        contours.register("a", np.zeros((128, 2)))
        contours.register("b", np.zeros((128, 2)))
        cells = {("T2", "E0", "q"): [{"rank": "1", "candidate_id": "a"},
                                     {"rank": "2", "candidate_id": "b"}]}
        out = blind.build_cells("T2", "q", cells, contours, top=2)
        assert [r["candidate_id"] for r in out] == ["a", "b"]
        broken = {("T2", "E0", "q"): [{"rank": "2", "candidate_id": "a"},
                                      {"rank": "1", "candidate_id": "b"}]}
        with pytest.raises(SystemExit, match="not in rank order"):
            blind.build_cells("T2", "q", broken, contours, top=2)

    def test_a_missing_cell_is_skipped_not_padded(self, blind: Any) -> None:
        contours = blind.Contours()
        contours.register("q", np.zeros((128, 2)))
        contours.register("a", np.zeros((128, 2)))
        cells = {("T2", "E0", "q"): [{"rank": "1", "candidate_id": "a"}]}
        out = blind.build_cells("T2", "q", cells, contours, top=3)
        assert len(out) == 1
        assert [r["arm"] for r in out] == ["E0"]


class TestFrame:
    def test_one_frame_for_the_whole_setting(self, blind: Any) -> None:
        contours = blind.Contours()
        contours.register("round", np.array([[0.5, 0.0], [0.0, 0.5], [-0.5, 0.0], [0.0, -0.5]]))
        contours.register(
            "spindly", np.array([[2.0, 0.0], [0.0, 0.2], [-2.0, 0.0], [0.0, -0.2]])
        )
        frame = contours.frame_for({"round", "spindly"})
        assert frame > 2.0
        # the same two shapes drawn in a different order must give the same frame
        assert contours.frame_for({"spindly", "round"}) == frame

    def test_a_shape_that_was_never_loaded_is_an_error_not_a_blank_panel(
        self, blind: Any
    ) -> None:
        contours = blind.Contours()
        contours.need("ghost")
        with pytest.raises(SystemExit, match="contours never loaded"):
            contours.frame_for({"ghost"})


class TestAnswerSheet:
    def test_the_template_ships_empty_and_carries_no_arm_column(
        self, blind: Any, tmp_path: Path
    ) -> None:
        records = [record("q1", 1, arm, f"c{arm}") for arm in ("E0", "E1", "E2", "E3")]
        blind.blind_cells(records, seed=5)
        queries, candidates = blind.make_codes(records)
        for r in records:
            r["blind_candidate_code"] = candidates[r["candidate_id"]]
        groups = [
            {
                "setting": "T2",
                "query_id": "q1",
                "rank": 1,
                "family": "blob",
                "by_label": {r["blind_label"]: r for r in records},
            }
        ]
        path = tmp_path / "template.csv"
        written = blind.write_template(path, groups, queries)
        assert written == 8  # four arms x two views
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        leaky = {"arm", "candidate_id", "distance", "relevant", "family", "query_id"}
        assert not leaky & set(header)
        assert set(header) == {
            "setting",
            "view",
            "query_code",
            "rank",
            "blind_label",
            "candidate_code",
            "score_0_3",
            "key_structure_preserved",
            "obvious_error",
            "low_confidence",
            "reason",
            "reviewer",
            "status",
        }
        body = path.read_text(encoding="utf-8").splitlines()[1:]
        assert all(line.endswith(",,,,,,,pending_review") for line in body)
        arms = ("E0", "E1", "E2", "E3")
        assert not any(arm in line for line in body for arm in arms)
