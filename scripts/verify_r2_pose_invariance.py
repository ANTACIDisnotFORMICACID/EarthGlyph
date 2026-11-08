"""Measure, per contour, whether R2's canonicalisation actually removes the rotation.

P0-4.6 Checkpoint 7-C. A flat agreement curve is not by itself evidence that a rotation wrapper
works: dropping the pose information and genuinely un-rotating the shape produce the same flat
line. So the check is made on the coordinates the network returns — the same contour is turned by
three stress poses, scored NAIVE (wrapper off) and R2, and the two are compared against the
unrotated embedding. Machine-precision agreement for R2 with a large movement for naive is the
claim in `docs/DECISIONS.md` D67 and in `notes.md` C5; the numbers are stored, not quoted from a
terminal.

This script only reads. It changes no committed result and no baseline.

    python scripts/verify_r2_pose_invariance.py
"""

from __future__ import annotations

import json
import subprocess

import numpy as np
from run_shape_benchmark import (
    CACHE,
    D4_GALLERY_PER_CLASS,
    D6_QUERIES,
    RESULTS,
    load_mpeg7,
)

from earthglyph.shape_benchmark import retrieval as ret
from earthglyph.shape_benchmark.datasets import BenchmarkShape
from earthglyph.shape_benchmark.probes import rotate
from earthglyph.shape_benchmark.rotation_methods import canonicalise, pose_embedding

OUT = RESULTS / "r2_pose_invariance_check.json"

#: CP6's stress set, unchanged: one off-grid pose, one near-half-turn, one quarter turn.
ANGLES = (0.0, 37.0, 191.0, 270.0)
NETWORKS = {"S0K1": "kernet1", "S0K2": "kernet2"}


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _queries() -> list[BenchmarkShape]:
    """The frozen d6 queries, resolved exactly the way run_d6 resolves them."""
    shapes, _ = load_mpeg7(CACHE)
    per_class: dict[str, list[BenchmarkShape]] = {}
    for shape in shapes:
        per_class.setdefault(str(shape.category), []).append(shape)
    subset = [
        members[i]
        for _, members in sorted(per_class.items())
        for i in range(min(D4_GALLERY_PER_CLASS, len(members)))
    ]
    by_stem = {shape.shape_id.removeprefix("mpeg7_"): shape for shape in subset}
    missing = [stem for stem, _ in D6_QUERIES if stem not in by_stem]
    if missing:
        raise RuntimeError(f"frozen d6 queries absent from the stratified gallery: {missing}")
    return [by_stem[stem] for stem, _ in D6_QUERIES]


def main() -> None:
    queries = _queries()
    out: dict[str, object] = {
        "schema": "earthglyph.shape_perception_benchmark/r2_invariance/1",
        "task": "P0-4.6 Checkpoint 7-C, the R2 coordinate check behind D67",
        "angles": list(ANGLES),
        "git_commit": _commit(),
        "numpy": np.__version__,
        "reproduce": "python scripts/verify_r2_pose_invariance.py",
        "read_only": "scores contours through the port and writes this file; nothing else changes",
        "by_network": {},
    }
    for label, network in NETWORKS.items():
        scorer = ret._scorer_for(network)
        rows: list[dict[str, object]] = []
        for shape in queries:
            contour = shape.display_points
            _, frame = canonicalise(contour)
            naive = np.stack(
                [np.asarray(scorer(rotate(contour, deg)), dtype=float) for deg in ANGLES]
            )
            r2 = np.stack(
                [pose_embedding(scorer, rotate(contour, deg), "R2", "query")[0] for deg in ANGLES]
            )
            rows.append(
                {
                    "shape_id": shape.shape_id,
                    "frame_status": frame.status,
                    "coord_norm_mean": float(np.linalg.norm(naive[0])),
                    "naive_move_max": float(np.max(np.linalg.norm(naive[1:] - naive[0], axis=1))),
                    "r2_move_max": float(np.max(np.linalg.norm(r2[1:] - r2[0], axis=1))),
                }
            )
        moved = [r["naive_move_max"] for r in rows]
        held = [r["r2_move_max"] for r in rows]
        norms = [r["coord_norm_mean"] for r in rows]
        out["by_network"][label] = {
            "queries": rows,
            "naive_move_max": max(moved),
            "naive_move_median": float(np.median(moved)),
            "r2_move_max": max(held),
            "coordinate_norm_median": float(np.median(norms)),
            "worst_r2_move_as_share_of_norm": max(
                r["r2_move_max"] / r["coord_norm_mean"] for r in rows
            ),
            "note": (
                "naive_move_max is how far the 22-D coordinates of ONE contour move when only "
                "its pose changes; r2_move_max is the same quantity with R2 applied. R2 holding "
                "at floating-point residue while naive moves by a large fraction of the "
                "coordinate norm distinguishes true un-rotation from simply discarding the pose."
            ),
        }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for label, block in out["by_network"].items():
        print(
            f"{label}: naive max {block['naive_move_max']:.4f} "
            f"(median {block['naive_move_median']:.4f}), R2 max {block['r2_move_max']:.3e}, "
            f"median coordinate norm {block['coordinate_norm_median']:.4f}"
        )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
