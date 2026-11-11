#!/usr/bin/env python
"""CLI: generate the frozen P0-5 synthetic perturbation queries.

One probe per (sampled source, variant) — 118 sources x 31 variants = 3658
traces, written in the same JSON format P0-4 used, so the same extraction path
reads them. The variant table and all parameter values are frozen in
``experiments/p0_perturbation_sweep/DESIGN.md`` and must not change after any
retrieval has run.

Every randomised variant gets its own recorded seed
(``20261111*1000 + source_row*100 + variant_index``), so each trace is
reproducible byte-for-byte. These are synthetic perturbations of real library
contours — **not** hand drawings; the manifest marks every entry ``family=
"probe"``.

Example
-------
python scripts/make_p05_queries.py --index data/index \
    --samples experiments/p0_perturbation_sweep/samples/sample_sources.csv \
    --out experiments/p0_perturbation_sweep/queries --seed 20261111
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from make_retrieval_queries import (  # noqa: E402
    arc_length_resample,
    as_trace,
    local_deform,
    mirror,
    open_end,
    turn,
    write_query,
)

from earthglyph.geometry import open_ring  # noqa: E402

TRACE_POINTS = 90  # same trace length as P0-4's pen


def variant_table() -> list[dict[str, Any]]:
    """The frozen 31 variants, in the order that fixes variant_index."""
    table: list[dict[str, Any]] = []

    def add(tag: str, layer: str, perturbation: str, params: dict[str, Any]) -> None:
        table.append({"tag": tag, "layer": layer, "perturbation": perturbation,
                      "params": params})

    # G1 correctness (6)
    add("self", "G1", "self", {})
    add("rot-37", "G1", "rot", {"theta_deg": 37.0})
    add("scale-3.7x", "G1", "scale", {"factor": 3.7})
    add("shift", "G1", "shift", {"dx": 40.0, "dy": -25.0})
    add("reverse", "G1", "reverse", {})
    add("mirror", "G1", "mirror", {})
    # sparse strength curve (3)
    for n in (64, 32, 16):
        add(f"sparse-{n}", "G1", "sparse", {"n": n})
    # G2 robustness (22)
    for amp in (0.02, 0.05, 0.10):
        add(f"jag-{amp:g}", "G2", "jag", {"amplitude": amp})
    for sigma in (0.25, 0.55, 0.90):
        add(f"uneven-{sigma:g}", "G2", "uneven", {"sigma": sigma})
    for share, ramp in ((0.06, 0.0), (0.06, 0.15), (0.20, 0.15), (0.35, 0.15)):
        name = f"gap-{share:g}" + (f"-tail{ramp:g}" if ramp else "")
        add(name, "G2", "gap", {"share": share, "ramp": ramp})
    for height in (0.10, 0.18, 0.30):
        add(f"island-{height:g}", "G2", "island", {"height": height})
    for height in (0.10, 0.18, 0.30):
        add(f"bay-{height:g}", "G2", "bay", {"height": height})
    for height in (0.30, 0.45):
        add(f"finger-{height:g}", "G2", "finger", {"width": 0.15, "height": height})
    add("notch-0.45", "G2", "notch", {"width": 0.12, "height": 0.45})
    for factor in (1.15, 1.25, 1.40):
        add(f"aspect-{factor:g}", "G2", "aspect", {"factor": factor})
    return table


def build_points(
    stored: np.ndarray, variant: dict[str, Any], *, seed: int
) -> np.ndarray:
    family, params = variant["perturbation"], variant["params"]
    points = np.asarray(stored, dtype=float)
    if family == "self":
        pass
    elif family == "rot":
        points = turn(points, np.deg2rad(float(params["theta_deg"])))
    elif family == "scale":
        points = points * float(params["factor"])
    elif family == "shift":
        points = points + np.array([float(params["dx"]), float(params["dy"])])
    elif family == "reverse":
        points = points[::-1]
    elif family == "mirror":
        points = mirror(points)
    elif family == "sparse":
        points = arc_length_resample(open_ring(points), int(params["n"]))
    elif family == "jag":
        points = as_trace(points, seed=seed, wobble_amplitude=float(params["amplitude"]))
    elif family == "uneven":
        from make_retrieval_queries import resample_uneven

        points = resample_uneven(
            open_ring(points), seed=seed, n=TRACE_POINTS, sigma=float(params["sigma"])
        )
    elif family == "gap":
        traced = as_trace(points, seed=seed)
        points = open_end(
            traced, share=float(params["share"]), ramp=float(params["ramp"])
        )
    elif family == "island":
        points = local_deform(points, centre_angle=0.9, width=0.35,
                              height=float(params["height"]))
    elif family == "bay":
        points = local_deform(points, centre_angle=2.4, width=0.3,
                              height=float(params["height"]))
    elif family == "finger":
        points = local_deform(points, centre_angle=0.9, width=float(params["width"]),
                              height=float(params["height"]))
    elif family == "notch":
        points = local_deform(points, centre_angle=2.4, width=float(params["width"]),
                              height=float(params["height"]))
    elif family == "aspect":
        factor = float(params["factor"])
        points = points * np.array([factor, 1.0 / factor])
    else:
        raise ValueError(f"unknown variant family {family!r}")
    return points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--samples", type=Path,
        default=Path("experiments/p0_perturbation_sweep/samples/sample_sources.csv"),
    )
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/p0_perturbation_sweep/queries")
    )
    parser.add_argument("--seed", type=int, default=20261111)
    args = parser.parse_args(argv)

    from earthglyph.retrieval.search import load_index_handle

    index = load_index_handle(args.index)
    samples = pd.read_csv(args.samples)
    table = variant_table()
    assert len(table) == 31, f"variant table drifted: {len(table)} entries"

    args.out.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for source in samples.itertuples():
        stored = np.asarray(index.shapes[int(source.row)], dtype=float)
        for variant_index, variant in enumerate(table):
            seed = args.seed * 1000 + int(source.row) * 100 + variant_index
            points = build_points(stored, variant, seed=seed)
            record = {
                "name": f"{variant['tag']}-r{int(source.row)}",
                "family": "probe",
                "perturbation": variant["perturbation"],
                "layer": variant["layer"],
                "params": {**variant["params"], "query_seed": seed},
                "source_row": int(source.row),
                "source_candidate_id": str(source.candidate_id),
                "points": points,
                "closed": True,
            }
            entries.append(write_query(record, args.out))

    manifest = {
        "generated_by": "scripts/make_p05_queries.py",
        "command": f"python scripts/make_p05_queries.py --index {args.index} "
        f"--samples {args.samples} --out {args.out} --seed {args.seed}",
        "seed": args.seed,
        "design": "experiments/p0_perturbation_sweep/DESIGN.md (frozen before any run)",
        "n_sources": int(len(samples)),
        "variants_per_source": len(table),
        "trace_points": TRACE_POINTS,
        "n_points": index.n_points,
        "index": index.provenance(),
        "families": {
            "probe": "synthetic perturbation of a real library contour — NOT a hand "
            "drawing; mirror is evaluated under both allow_mirror settings and "
            "reported separately",
        },
        "queries": entries,
        "png_queries": [],
    }
    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")

    names = [entry["file"] for entry in entries]
    if len(set(names)) != len(names):
        raise SystemExit("duplicate query names — variant tags collided")
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["layer"]] = counts.get(entry["layer"], 0) + 1
    print(f"wrote {len(entries)} queries ({counts}) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
