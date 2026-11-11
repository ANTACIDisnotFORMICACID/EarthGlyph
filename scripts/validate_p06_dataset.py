#!/usr/bin/env python
"""Pre-flight gate for the P0-6 human-query dataset (protocol §7, frozen).

Read-only. Must pass before `make_p06_judgement_sheets.py` or any retrieval
evaluation may run on collected sessions. Checks:

1. at least ``--min-participants`` distinct anonymous PXX sessions;
2. each session has at least ``--min-finals`` files declared final
   (``-final-`` in the original name or ``final: true`` at registration);
3. no duplicate content sha256 across the whole dataset (a drawing registered
   twice under different participants would inflate the count silently);
4. every registered file still passes the structural validation and its
   on-disk sha256 matches the manifest record;
5. the pseudonym key file must be git-ignored — if git tracks it, refuse.

Usage:
    python scripts/validate_p06_dataset.py \
        --sessions-root experiments/p0_6_human_queries/sessions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect_p06_queries import validate_structure  # noqa: E402

MIN_PARTICIPANTS = 10
MIN_FINALS = 6
KEY_FILENAME = "key_participants.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_tracks(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        capture_output=True,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path,
                        default=Path("experiments/p0_6_human_queries/sessions"))
    parser.add_argument("--min-participants", type=int, default=MIN_PARTICIPANTS)
    parser.add_argument("--min-finals", type=int, default=MIN_FINALS)
    args = parser.parse_args(argv)

    problems: list[str] = []
    root = args.sessions_root
    if not root.is_dir():
        print(f"NOT READY: sessions root {root} does not exist — nothing collected")
        return 1

    key_file = root / KEY_FILENAME
    if key_file.is_file() and git_tracks(key_file):
        problems.append(
            f"{key_file} is tracked by git — the pseudonym mapping must never "
            "enter the repository (gitignore it and re-register)"
        )

    seen_sha: dict[str, str] = {}
    table: list[dict[str, Any]] = []
    for session_dir in sorted(root.glob("P*")):
        manifest_path = session_dir / "session.json"
        if not manifest_path.is_file():
            continue
        session = json.loads(manifest_path.read_text(encoding="utf-8"))
        finals = 0
        invalid = 0
        practice = 0
        for record in session["files"]:
            if record.get("status") == "invalid":
                invalid += 1
                continue
            query_path = session_dir / f"{record['query_id']}.json"
            if not query_path.is_file():
                problems.append(f"{record['query_id']}: registered but file missing")
                continue
            payload = json.loads(query_path.read_text(encoding="utf-8"))
            error = validate_structure(payload)
            if error is not None:
                problems.append(f"{record['query_id']}: fails structure ({error})")
            digest = file_sha256(query_path)
            if digest != record["sha256"]:
                problems.append(
                    f"{record['query_id']}: on-disk sha256 differs from the "
                    "registration record — the file changed after collection"
                )
            owner = seen_sha.get(digest)
            if owner is not None and owner != record["query_id"]:
                problems.append(
                    f"{record['query_id']}: duplicate content of {owner} "
                    "(same sha256 registered twice)"
                )
            seen_sha[digest] = record["query_id"]
            if record.get("final"):
                finals += 1
            else:
                practice += 1
        table.append({"participant": session["participant"], "finals": finals,
                      "practice": practice, "invalid": invalid})

    if not table:
        print("NOT READY: no sessions registered yet (empty sessions directory)")
        return 1

    short = [row for row in table if row["finals"] < args.min_finals]
    if len(table) < args.min_participants:
        problems.append(
            f"only {len(table)} participant(s) — protocol requires "
            f">= {args.min_participants} distinct humans"
        )
    if short:
        names = ", ".join(f"{row['participant']}({row['finals']})" for row in short)
        problems.append(
            f"participants below {args.min_finals} finals: {names}"
        )

    total_finals = sum(row["finals"] for row in table)
    for row in table:
        print(f"  {row['participant']}: finals={row['finals']} "
              f"practice={row['practice']} invalid={row['invalid']}")
    print(f"participants={len(table)}  finals={total_finals}  "
          f"unique_contents={len(seen_sha)}")

    if problems:
        print("\nNOT READY:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"\nREADY: >= {args.min_participants} participants x >= "
          f"{args.min_finals} finals, no duplicate content, all files verified "
          "against their registration records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
