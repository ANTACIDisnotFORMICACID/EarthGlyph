#!/usr/bin/env python
"""CLI: register incoming human sketch exports into anonymous P0-6 sessions
(protocol: experiments/p0_6_human_queries/PROTOCOL.md, frozen before collection).

This script ONLY registers and validates structure. It never runs the
retriever, never scores anything, and never generates sketch data — the
forbidden shortcuts (algorithm-drawn "participants", AI scores posing as human
judgement) have no code path here.

What it does
------------
1. Validate each JSON in the inbox structurally: a points list of >= 3 finite
   (x, y) pairs, a boolean ``closed``. Extraction refusals (self-intersection,
   open stroke, ...) are NOT collected here — they are measured results of the
   evaluation stage and must stay that way.
2. Deduplicate by content sha256: re-exports of the same file are skipped.
3. Map the participant pseudonym (file prefix ``<pseudonym>-...``) to an
   anonymous ``PXX`` via the local key file ``sessions/key_participants.json``
   (gitignored — the mapping never enters the repository).
4. Store the file as ``sessions/<PXX>/<query_id>.json`` with
   ``query_id = PXX-q<NN>`` and record device/date/final-or-practice in
   ``sessions/<PXX>/session.json``. Structurally invalid files are registered
   as ``invalid`` and kept verbatim.

Example
-------
python scripts/collect_p06_queries.py --inbox exports/incoming \
    --sessions-root experiments/p0_6_human_queries/sessions --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

KEY_FILENAME = "key_participants.json"
GITIGNORE_ENTRY = "experiments/p0_6_human_queries/sessions/key_*.json"


def load_key(path: Path) -> dict[str, str]:
    if path.is_file():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return {}


def save_key(path: Path, key: dict[str, str]) -> None:
    path.write_text(json.dumps(key, indent=1, ensure_ascii=False), "utf-8")
    gitignore = Path(".gitignore")
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    if GITIGNORE_ENTRY not in lines:
        lines.append(GITIGNORE_ENTRY)
        gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_structure(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "top level is not an object"
    points = payload.get("points")
    if not isinstance(points, list) or len(points) < 3:
        return "points must be a list of >= 3 pairs"
    for pair in points:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return "each point must be an [x, y] pair"
        if not all(isinstance(v, (int, float)) and v == v and abs(v) != float("inf")
                   for v in pair):
            return "coordinates must be finite numbers"
    if not isinstance(payload.get("closed"), bool):
        return "closed must be a boolean"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, required=True,
                        help="directory of sketch_pad exports, files named "
                        "<pseudonym>-<whatever>.json")
    parser.add_argument("--sessions-root", type=Path,
                        default=Path("experiments/p0_6_human_queries/sessions"))
    parser.add_argument("--device", default="mouse",
                        help="device type recorded for the whole inbox batch")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    inbox = sorted(args.inbox.glob("*.json"))
    if not inbox:
        print(f"no JSON exports found in {args.inbox} — nothing collected")
        return 0

    key_path = args.sessions_root / KEY_FILENAME
    key = load_key(key_path)
    counter = sum(1 for value in key.values() if value.startswith("P")) + 1
    seen_sha: dict[str, str] = {}
    for session_dir in args.sessions_root.glob("P*"):
        manifest_path = session_dir / "session.json"
        if manifest_path.is_file():
            for record in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]:
                seen_sha[record["sha256"]] = record["query_id"]

    registrations: list[dict[str, Any]] = []
    for path in inbox:
        pseudonym = path.name.split("-", 1)[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        error = validate_structure(payload)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha in seen_sha:
            registrations.append({"file": path.name, "status": "duplicate_of",
                                  "of": seen_sha[sha]})
            continue
        if pseudonym not in key:
            key[pseudonym] = f"P{counter:02d}"
            counter += 1
        anon = key[pseudonym]
        session_dir = args.sessions_root / anon
        if not args.dry_run:  # dry-run must not create directories either
            session_dir.mkdir(parents=True, exist_ok=True)
        session_manifest_path = session_dir / "session.json"
        if session_manifest_path.is_file():
            session = json.loads(session_manifest_path.read_text(encoding="utf-8"))
        else:
            session = {"participant": anon, "device": args.device, "files": []}
        if error is not None:
            registrations.append({"file": path.name, "status": "invalid", "reason": error,
                                  "participant": anon})
            session["files"].append({"original": path.name, "sha256": sha,
                                     "status": "invalid", "reason": error})
        else:
            query_id = f"{anon}-q{len(session['files']) + 1:02d}"
            target = session_dir / f"{query_id}.json"
            if not args.dry_run:
                target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            # retake policy: a file is the declared final only when the participant
            # names it so ("<pseudonym>-final-...") or the export says final=true
            final = "-final-" in path.name or bool(payload.get("final", False))
            session["files"].append({"query_id": query_id, "original": path.name,
                                     "sha256": sha, "status": "registered",
                                     "final": final})
            registrations.append({"file": path.name, "status": "registered",
                                  "query_id": query_id, "final": final,
                                  "participant": anon})
        if not args.dry_run:
            session_manifest_path.write_text(
                json.dumps(session, indent=1, ensure_ascii=False), "utf-8"
            )
        seen_sha[sha] = registrations[-1].get("query_id", path.name)

    if not args.dry_run:
        save_key(key_path, key)
    for record in registrations:
        print(record)
    print(f"{len(registrations)} file(s) processed"
          f"{' (dry run — nothing written)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
