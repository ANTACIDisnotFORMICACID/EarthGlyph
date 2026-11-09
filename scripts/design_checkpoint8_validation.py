#!/usr/bin/env python3
"""Freeze (or verify) the CP8-B independent validation design -- it scores nothing.

    python scripts/design_checkpoint8_validation.py            # freeze, once
    python scripts/design_checkpoint8_validation.py --check    # verify, never writes

The file this writes is a promise about a future experiment: which 24 shapes, which 5 angles each,
which 6 arms, which seed. Rewriting it after results exist would turn an independent test into a
recitation, so a differing design on disk is an error rather than an update -- a genuinely new study
needs a new ``CP8_SCHEMA`` and seed, in code, with the old file kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from earthglyph.shape_benchmark.datasets import load_mpeg7  # noqa: E402
from earthglyph.shape_benchmark.independent_validation import (  # noqa: E402
    CP8_SEED,
    build_design,
    design_content_sha256,
)

BENCH = REPO / "experiments" / "shape_perception_benchmark"
OUT_DIR = BENCH / "checkpoint8"
DESIGN = OUT_DIR / "independent_validation_design.json"
CACHE = BENCH / "cache"
MANIFEST = BENCH / "dataset_manifest.json"


def git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()


def check_dataset(archive: Path, expected: str) -> str | None:
    """Return the archive hash on disk, refusing a dataset build that is not the manifest's."""
    actual = hashlib.sha256(archive.read_bytes()).hexdigest() if archive.exists() else None
    if actual is not None and actual != expected:
        raise SystemExit(
            f"the MPEG-7 archive on disk hashes to {actual}, not the manifest's {expected}. "
            "A query set drawn from a different dataset build is a different study."
        )
    return actual


def provenance() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mpeg7 = manifest["mpeg7"]
    actual = check_dataset(CACHE / "MPEG7dataset.zip", mpeg7["sha256"])
    return {
        "command": "python scripts/design_checkpoint8_validation.py",
        "generated_by_python": sys.version.split()[0],
        "numpy": np.__version__,
        "git_commit": git_commit(),
        "seed": CP8_SEED,
        "dataset": {
            "name": "MPEG-7 CE-Shape-1 (S1)",
            "url": mpeg7["url"],
            "archive_sha256": mpeg7["sha256"],
            "archive_sha256_verified_on_disk": actual,
            "stimulus_count": mpeg7["stimulus_count"],
            "class_count": mpeg7["class_count"],
            "contour_contract": "128 points, centroided, unit RMS, CCW",
        },
    }


def verify_or_write(
    design: dict[str, Any],
    digest: str,
    summary: str,
    *,
    design_path: Path = DESIGN,
    check_only: bool = False,
) -> None:
    """Freeze the design exactly once, then only ever verify it.

    A differing design on disk is not an update: it either means a code path changed underneath a
    committed study (so every result tied to it must be re-checked) or that someone re-picked a
    query after seeing a result (so it is a new study and must say so in its schema and seed).
    """
    if design_path.exists():
        stored = json.loads(design_path.read_text(encoding="utf-8")).get("content_sha256")
        if stored != digest:
            raise SystemExit(
                f"DESIGN DRIFT: {design_path} records "
                    f"content_sha256 {stored!r} but the frozen rules "
                f"now derive {digest!r}. If nothing was re-picked, a code path changed under the "
                "design and every stored result tied to it must be re-checked. If something *was* "
                "re-picked, that is a new study: bump CP8_SCHEMA and the seed in "
                "earthglyph/shape_benchmark/independent_validation.py and keep this file."
            )
        print(f"design already frozen and unchanged: {summary}\n  content_sha256 {digest}")
        return
    if check_only:
        raise SystemExit(
            f"no design at {design_path}. Run without --check to freeze it -- before any retrieval."
        )
    payload = dict(design)
    payload["content_sha256"] = digest
    design_path.parent.mkdir(parents=True, exist_ok=True)
    design_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        shown: Path = design_path.relative_to(REPO)
    except ValueError:  # a scratch path outside the repo, e.g. a test tmp dir
        shown = design_path
    print(f"frozen {summary} -> {shown}\n  content_sha256 {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed design against the rules and write nothing",
    )
    args = parser.parse_args()

    shapes, excluded = load_mpeg7(CACHE)
    design = build_design(shapes, provenance=provenance())
    digest = design_content_sha256(design)
    summary = (
        f"{design['n_queries']} queries x {len(design['angle_design']['slots_per_query'])} "
        f"rotated angles + control, {len(design['arms']['labels'])} arms, seed {design['seed']}"
        f"\n  {len(excluded)} archive images excluded by the contour loader (see notes)"
    )
    verify_or_write(design, digest, summary, check_only=args.check)


if __name__ == "__main__":
    main()
