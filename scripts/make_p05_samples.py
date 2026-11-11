#!/usr/bin/env python
"""CLI: freeze the P0-5 stratified sample of real library contours.

Reads ``data/index`` only (metadata + stored 128-point shapes). It never runs a
search and never sees a retrieval result: the sample must be choosable from the
library alone, or the strength curves measured later would be a selection
effect, not a measurement.

Selection rule (frozen, deterministic)
--------------------------------------
1. One candidate per ``source_id``: the part with the largest ``area_m2``
   (tie-break: lexicographically smallest ``candidate_id``). Declared override:
   source ``4b2c4aea-…`` is represented by library row 294 (part 1) instead of
   its largest part, because P0-4 documented that exact contour as the
   forked-reservoir failure case and the sweep must contain it. This is prior
   knowledge from the experiment log, not a result-driven pick.
2. Six shape families, assigned in this fixed order, thresholds measured on
   the representative contour itself:
      near_circular  compactness >= 0.85
      elongated      axis-ratio  >= 3.0
      forked         solidity <= 0.70 and compactness <= 0.15
      deep_concave   solidity <= 0.70
      irregular      detail-band share >= the representatives' 90th percentile
      compact        everything else
3. 20 sources per family, drawn with ``numpy.random.default_rng(20261111)``
   over the family pool sorted by ``candidate_id`` (permutation, take first).
   Shortfalls are reported, never filled from another family.
4. Near-duplicate control: every sampled pair is aligned (mirror not allowed);
   a pair with align distance < 0.05 is a near-duplicate, the lexicographically
   later candidate is dropped and topped up with the next unused candidate from
   the same family's pool (sorted-by-candidate_id order), then the check
   repeats (max 10 passes).

Outputs (under --out-dir)
-------------------------
``sample_sources.csv``       one row per sampled source, with all indices
``selection_report.json``    provenance, thresholds, pool sizes, dropped pairs,
                             effective sample size, command, git commit

Example
-------
python scripts/make_p05_samples.py --index data/index \
    --out experiments/p0_perturbation_sweep/samples
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.features.fourier import harmonic_energy_fractions  # noqa: E402
from earthglyph.features.geometry_stats import shape_metrics  # noqa: E402
from earthglyph.geometry import open_ring  # noqa: E402
from earthglyph.retrieval.alignment import align  # noqa: E402

SEED = 20261111
PER_FAMILY = 20
NEAR_DUP_ALIGN = 0.05
MAX_DEDUP_PASSES = 10
FAMILIES = ("near_circular", "elongated", "forked", "deep_concave", "irregular", "compact")
FORKED_SOURCE_OVERRIDE = "4b2c4aea-4e72-3a7c-9377-b1e862f43e6b"
ROW_294 = 294
COMPACT_MIN = 0.85
ELONG_MIN = 3.0
SOLID_MAX = 0.70
FORKED_COMPACT_MAX = 0.15
DETAIL_Q = 0.90


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def axis_ratio(points: np.ndarray) -> float:
    centered = points - points.mean(axis=0)
    ev = np.linalg.eigvalsh(np.cov(centered.T))
    ev = np.maximum(ev, 1e-12)
    return float(np.sqrt(ev[1] / ev[0]))


def solidity(ring: np.ndarray) -> float:
    poly = Polygon(ring)
    return float(poly.area / max(poly.convex_hull.area, 1e-12))


def detail_share(shape: np.ndarray) -> float:
    energy = harmonic_energy_fractions(np.asarray(shape, dtype=float), 16)
    return float(energy[15] - energy[7])


def measure_library(metadata: pd.DataFrame, shapes: np.ndarray) -> pd.DataFrame:
    rows = []
    for i in range(len(metadata)):
        shape = np.asarray(shapes[i], dtype=float)
        ring = open_ring(shape)
        rows.append(
            {
                "row": i,
                "candidate_id": str(metadata.iloc[i]["candidate_id"]),
                "source_id": str(metadata.iloc[i]["source_id"]),
                "class": str(metadata.iloc[i]["class"]),
                "subtype": str(metadata.iloc[i]["subtype"]),
                "area_m2": float(metadata.iloc[i]["area_m2"]),
                "n_parts": int(metadata.iloc[i]["n_parts"]),
                "compact": round(float(shape_metrics(ring).compactness), 6),
                "elong": round(axis_ratio(shape), 6),
                "solid": round(solidity(ring), 6),
                "detail": round(detail_share(shape), 8),
            }
        )
    return pd.DataFrame(rows)


def pick_representatives(lib: pd.DataFrame) -> pd.DataFrame:
    ordered = lib.sort_values(
        ["source_id", "area_m2", "candidate_id"], ascending=[True, False, True]
    )
    reps = ordered.groupby("source_id", as_index=False).first()
    override = lib[lib["row"] == ROW_294]
    if len(override) and str(override.iloc[0]["source_id"]) == FORKED_SOURCE_OVERRIDE:
        reps = pd.concat(
            [reps[reps["source_id"] != FORKED_SOURCE_OVERRIDE], override]
        ).sort_values("source_id").reset_index(drop=True)
    else:
        raise SystemExit(
            f"row {ROW_294} is not part of source {FORKED_SOURCE_OVERRIDE}; "
            "the declared representative override no longer holds"
        )
    return reps


def assign_family(reps: pd.DataFrame) -> tuple[pd.Series, float]:
    detail_cut = float(reps["detail"].quantile(DETAIL_Q))
    labels = []
    for rec in reps.itertuples():
        if rec.compact >= COMPACT_MIN:
            labels.append("near_circular")
        elif rec.elong >= ELONG_MIN:
            labels.append("elongated")
        elif rec.solid <= SOLID_MAX and rec.compact <= FORKED_COMPACT_MAX:
            labels.append("forked")
        elif rec.solid <= SOLID_MAX:
            labels.append("deep_concave")
        elif rec.detail >= detail_cut:
            labels.append("irregular")
        else:
            labels.append("compact")
    return pd.Series(labels, index=reps.index), detail_cut


def draw_sample(reps: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    picks = []
    for family in FAMILIES:
        pool = reps[reps["family"] == family].sort_values("candidate_id")
        perm = rng.permutation(len(pool))
        picks.append(pool.iloc[perm[:PER_FAMILY]])
    return pd.concat(picks).reset_index(drop=True)


def dedup_sample(
    sampled: pd.DataFrame, reps: pd.DataFrame, shapes: np.ndarray
) -> tuple[pd.DataFrame, list[dict[str, Any]], int]:
    sampled = sampled.copy()
    contours = [np.asarray(shapes[int(row)], dtype=float) for row in sampled["row"]]
    dropped: list[dict[str, Any]] = []
    passes = 0
    while passes < MAX_DEDUP_PASSES:
        passes += 1
        worst: tuple[float, int, int] | None = None
        for i in range(len(sampled)):
            for j in range(i + 1, len(sampled)):
                distance = float(
                    align(contours[i], contours[j], allow_mirror=False).distance
                )
                if distance < NEAR_DUP_ALIGN and (worst is None or distance < worst[0]):
                    worst = (distance, i, j)
        if worst is None:
            break
        distance, i, j = worst
        id_i, id_j = str(sampled.iloc[i]["candidate_id"]), str(sampled.iloc[j]["candidate_id"])
        drop = j if id_j > id_i else i
        dropped.append(
            {
                "align_distance": round(distance, 5),
                "kept_candidate_id": id_j if drop == i else id_i,
                "dropped_candidate_id": str(sampled.iloc[drop]["candidate_id"]),
                "dropped_family": str(sampled.iloc[drop]["family"]),
            }
        )
        family = dropped[-1]["dropped_family"]
        sampled = sampled.drop(sampled.index[drop]).reset_index(drop=True)
        contours.pop(drop)
        used = set(sampled["candidate_id"])
        pool = reps[reps["family"] == family].sort_values("candidate_id")
        refill = pool[~pool["candidate_id"].isin(used)].head(1)
        if len(refill):
            sampled = pd.concat([sampled, refill], ignore_index=True)
            contours.append(np.asarray(shapes[int(refill.iloc[0]["row"])], dtype=float))
    return sampled, dropped, passes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/p0_perturbation_sweep/samples")
    )
    args = parser.parse_args(argv)

    metadata = pd.read_parquet(args.index / "metadata.parquet")
    shapes = np.load(args.index / "shapes.npy")
    manifest = json.loads((args.index / "data_manifest.json").read_text(encoding="utf-8"))

    lib = measure_library(metadata, shapes)
    reps = pick_representatives(lib)
    labels, detail_cut = assign_family(reps)
    reps = reps.assign(family=labels)

    rng = np.random.default_rng(SEED)
    sampled = draw_sample(reps, rng)
    sampled, dropped, passes = dedup_sample(sampled, reps, shapes)

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / "sample_sources.csv"
    sampled.to_csv(csv_path, index=False)

    counts = sampled["family"].value_counts().to_dict()
    pools: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        pool = reps[reps["family"] == family]
        pools[family] = {
            "pool_size": int(len(pool)),
            "requested": PER_FAMILY,
            "sampled_final": int(counts.get(family, 0)),
            "shortfall": int(max(PER_FAMILY - len(pool), 0)),
        }
    report: dict[str, Any] = {
        "generated_by": "scripts/make_p05_samples.py",
        "command": f"python scripts/make_p05_samples.py --index {args.index} --out {args.out}",
        "seed": SEED,
        "index_manifest_git_commit": manifest.get("git_commit"),
        "git_head_at_generation": git_head(),
        "rules": {
            "representative": "per source_id, largest area_m2 part (tie-break smallest "
            f"candidate_id); source {FORKED_SOURCE_OVERRIDE} overridden to library row "
            f"{ROW_294} (P0-4 documented forked-reservoir failure, declared in advance)",
            "compactness_min_near_circular": COMPACT_MIN,
            "axis_ratio_min_elongated": ELONG_MIN,
            "solidity_max_concave": SOLID_MAX,
            "forked_compactness_max": FORKED_COMPACT_MAX,
            "irregular_detail_cut_on_representatives": round(detail_cut, 8),
            "irregular_detail_quantile": DETAIL_Q,
            "per_family": PER_FAMILY,
            "near_dup_align_max": NEAR_DUP_ALIGN,
            "dedup_passes_used": passes,
            "refill_order": "same family pool, sorted by candidate_id, first unused",
        },
        "library": {
            "n_candidates": int(len(metadata)),
            "n_sources": int(metadata["source_id"].nunique()),
            "measured_quantiles": {
                col: {
                    q: round(float(lib[col].quantile(q)), 5)
                    for q in (0.05, 0.25, 0.5, 0.75, 0.95)
                }
                for col in ("compact", "elong", "solid", "detail")
            },
        },
        "family_pools": pools,
        "near_dup_pairs_dropped": dropped,
        "effective_sample_size": {
            "n_sources": int(len(sampled)),
            "per_family": {f: int(counts.get(f, 0)) for f in FAMILIES},
        },
    }
    report_path = args.out / "selection_report.json"
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), "utf-8")
    print(f"wrote {csv_path} ({len(sampled)} sources) and {report_path}")
    print("effective sample size:", report["effective_sample_size"]["per_family"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
