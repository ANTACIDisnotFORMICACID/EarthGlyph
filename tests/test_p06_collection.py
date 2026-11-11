"""Tests for the P0-6 human-query collector and dataset pre-flight gate.

These cover the honest-data guardrails only: structural validation, pseudonym
mapping, dedup, the final-declaration rule, and the ready/no-ready verdicts.
No test ever generates a plausible human sketch dataset — fixtures are minimal
coordinate lists used purely to drive the registration machinery.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_p06_queries as collect  # noqa: E402
import validate_p06_dataset as gate  # noqa: E402


def payload(points=None, closed=True, **extra):
    return {
        "points": points or [[0.0, 0.0], [1.0, 0.5], [0.5, 1.0]],
        "closed": closed,
        **extra,
    }


def write_inbox(inbox: Path, name: str, points=None, **extra) -> Path:
    path = inbox / name
    path.write_text(json.dumps(payload(points, **extra)), encoding="utf-8")
    return path


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # save_key edits ./.gitignore relative to cwd
    directory = tmp_path / "inbox"
    directory.mkdir()
    return directory


def run_collect(args) -> int:
    return collect.main(args)


def session_files(root: Path) -> dict:
    return {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in root.glob("P*/session.json")
    }


class TestValidateStructure:
    def test_valid_payload_passes(self):
        assert collect.validate_structure(payload()) is None

    def test_rejections(self):
        assert collect.validate_structure("nope") is not None
        assert collect.validate_structure(payload(points=[[0, 0]])) is not None
        assert collect.validate_structure(
            payload(points=[[0, 0], [1, 1], [float("nan"), 2]])
        ) is not None
        assert collect.validate_structure(
            payload(points=[[0, 0], [1, 1], [float("inf"), 2]])
        ) is not None
        assert collect.validate_structure(payload(points=[[0, 0], [1, 1], [2]])) is not None
        assert collect.validate_structure(payload(closed="yes")) is not None


class TestRegistration:
    def test_pseudonyms_map_to_sequential_anonymous_ids(self, inbox, tmp_path):
        write_inbox(inbox, "alpha-lake.json", points=[[0.0, 0.0], [1.0, 0.5], [0.5, 1.0]])
        write_inbox(inbox, "beta-pond.json", points=[[2.0, 2.0], [3.0, 2.5], [2.5, 3.0]])
        run_collect(["--inbox", str(inbox), "--sessions-root", str(tmp_path / "s")])
        key = json.loads((tmp_path / "s" / collect.KEY_FILENAME).read_text())
        assert key == {"alpha": "P01", "beta": "P02"}
        files = session_files(tmp_path / "s")
        assert set(files) == {"P01", "P02"}

    def test_same_pseudonym_maps_to_same_id(self, inbox, tmp_path):
        write_inbox(inbox, "alpha-lake.json", points=[[0.0, 0.0], [1.0, 0.5], [0.5, 1.0]])
        write_inbox(inbox, "alpha-pond.json", points=[[2.0, 2.0], [3.0, 2.5], [2.5, 3.0]])
        run_collect(["--inbox", str(inbox), "--sessions-root", str(tmp_path / "s")])
        assert list(session_files(tmp_path / "s")) == ["P01"]

    def test_identical_content_registered_once(self, inbox, tmp_path):
        write_inbox(inbox, "alpha-lake.json")
        write_inbox(inbox, "beta-lake.json")  # same minimal points -> same bytes
        run_collect(["--inbox", str(inbox), "--sessions-root", str(tmp_path / "s")])
        files = session_files(tmp_path / "s")
        registered = [f for f in files["P01"]["files"] if f["status"] == "registered"]
        assert len(registered) == 1
        assert "P02" not in files  # a duplicate never registers its pseudonym

    def test_invalid_file_kept_but_marked(self, inbox, tmp_path):
        path = inbox / "alpha-bad.json"
        path.write_text(json.dumps({"points": [[0.0, 0.0]]}), encoding="utf-8")
        run_collect(["--inbox", str(inbox), "--sessions-root", str(tmp_path / "s")])
        record = session_files(tmp_path / "s")["P01"]["files"][0]
        assert record["status"] == "invalid"
        assert "reason" in record

    def test_final_declaration_rules(self, inbox, tmp_path):
        write_inbox(inbox, "alpha-final-lake.json",     # declared in filename
                    points=[[0.0, 0.0], [1.0, 0.5], [0.5, 1.0]])
        write_inbox(inbox, "alpha-pond.json",           # declared in payload
                    points=[[2.0, 2.0], [3.0, 2.5], [2.5, 3.0]], final=True)
        write_inbox(inbox, "alpha-river.json",          # practice by default
                    points=[[4.0, 4.0], [5.0, 4.5], [4.5, 5.0]])
        run_collect(["--inbox", str(inbox), "--sessions-root", str(tmp_path / "s")])
        finals = {
            f["original"]: f.get("final")
            for f in session_files(tmp_path / "s")["P01"]["files"]
            if f["status"] == "registered"
        }
        assert finals == {
            "alpha-final-lake.json": True,
            "alpha-pond.json": True,
            "alpha-river.json": False,
        }

    def test_dry_run_writes_nothing(self, inbox, tmp_path):
        write_inbox(inbox, "alpha-lake.json")
        run_collect(["--inbox", str(inbox), "--sessions-root",
                     str(tmp_path / "s"), "--dry-run"])
        assert not (tmp_path / "s").exists()

    def test_empty_inbox_is_a_no_op(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert run_collect(["--inbox", str(empty),
                            "--sessions-root", str(tmp_path / "s")]) == 0
        assert "nothing collected" in capsys.readouterr().out


def build_session(root: Path, participant: str, finals: int, practice: int = 0,
                  *, duplicate_of: dict | None = None) -> None:
    """Write a minimal but well-formed session directory for the gate tests.

    Every query gets a unique ``note`` field so contents never collide by
    accident; ``duplicate_of`` overrides the body to forge an exact duplicate.
    """
    directory = root / participant
    directory.mkdir(parents=True)
    files = []
    for index in range(1, finals + practice + 1):
        query_id = f"{participant}-q{index:02d}"
        body = (payload(points=[[0.0, index], [1.0, index], [0.5, index + 1.0]],
                        closed=True, note=participant)
                if duplicate_of is None else duplicate_of)
        content = json.dumps(body)
        (directory / f"{query_id}.json").write_text(content, encoding="utf-8")
        files.append({
            "query_id": query_id,
            "original": f"{participant.lower()}-{index}.json",
            "sha256": hashlib.sha256(
                content.encode(encoding="utf-8")).hexdigest(),
            "status": "registered",
            "final": index <= finals,
        })
    (directory / "session.json").write_text(
        json.dumps({"participant": participant, "device": "mouse", "files": files}),
        encoding="utf-8",
    )


class TestPreflightGate:
    def test_empty_root_is_not_ready(self, tmp_path, capsys):
        assert gate.main(["--sessions-root", str(tmp_path)]) == 1
        assert "NOT READY" in capsys.readouterr().out

    def test_below_thresholds_lists_participants(self, tmp_path, capsys):
        build_session(tmp_path, "P01", finals=2)
        build_session(tmp_path, "P02", finals=1)
        assert gate.main(["--sessions-root", str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "only 2 participant(s)" in out
        assert "P01(2)" in out and "P02(1)" in out

    def test_ready_dataset_passes(self, tmp_path, capsys):
        for number in range(1, 11):
            build_session(tmp_path, f"P{number:02d}", finals=6)
        assert gate.main(["--sessions-root", str(tmp_path)]) == 0
        assert "READY" in capsys.readouterr().out

    def test_duplicate_content_across_participants_fails(self, tmp_path, capsys):
        shared = payload(points=[[0.0, 3.0], [1.0, 3.0], [0.5, 4.0]])
        for number in range(1, 11):
            build_session(tmp_path, f"P{number:02d}", finals=6)
        build_session(tmp_path, "P11", finals=7, duplicate_of=shared)
        assert gate.main(["--sessions-root", str(tmp_path)]) == 1
        assert "duplicate content" in capsys.readouterr().out

    def test_missing_registered_file_fails(self, tmp_path, capsys):
        for number in range(1, 11):
            build_session(tmp_path, f"P{number:02d}", finals=6)
        (tmp_path / "P01" / "P01-q01.json").unlink()
        assert gate.main(["--sessions-root", str(tmp_path)]) == 1
        assert "registered but file missing" in capsys.readouterr().out

    def test_tampered_file_fails_sha_check(self, tmp_path, capsys):
        for number in range(1, 11):
            build_session(tmp_path, f"P{number:02d}", finals=6)
        target = tmp_path / "P03" / "P03-q02.json"
        target.write_text(json.dumps(payload(points=[[9.0, 9.0], [9.0, 8.0], [8.0, 8.0]])),
                          encoding="utf-8")
        assert gate.main(["--sessions-root", str(tmp_path)]) == 1
        assert "sha256 differs" in capsys.readouterr().out
