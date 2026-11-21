#!/usr/bin/env python
"""Generate the extended cross-language golden vectors for the static web
retrieval contract (Phase D / P0-7 feasibility).

The existing contract (web/src/search/golden_contour.json) covered only
``normalize_contour``. The browser-side retrieval path needs the whole chain:

  arc_length_resample -> smooth_cyclic(light) -> normalize_contour
  -> harmonic_amplitudes(16) -> geometry stats -> coarse rank (fourier group)

This script runs the PRODUCTION functions on a fixed synthetic-but-realistic
query trace (a hand-drawn-ish rounded polygon — deterministic, no randomness)
and writes ``web/src/search/golden_retrieval.json`` with every intermediate
array plus the expected coarse shortlist computed against a REAL index. The
TS side must reproduce all of them within the tolerances recorded in the file.

Nothing here fabricates geography: the query is a constructed outline (clearly
labelled), the library side uses the actual production index under
``--index`` (default data/index, 839 real candidates). If the index is absent
the script exits non-zero instead of substituting fake candidates.

Run from the repo root:
    python scripts/make_golden_retrieval_vectors.py [--index data/index]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.index import load_index  # noqa: E402
from earthglyph.features.fourier import harmonic_amplitudes  # noqa: E402
from earthglyph.geometry.normalization import normalize_contour  # noqa: E402
from earthglyph.geometry.resampling import (  # noqa: E402
    arc_length_resample,
    ensure_ccw,
)
from earthglyph.geometry.sketch import smooth_cyclic  # noqa: E402
from earthglyph.retrieval.coarse import rank  # noqa: E402
from earthglyph.retrieval.query import vectors_for  # noqa: E402

GOLDEN_VERSION = "2.0"
N_POINTS = 128
N_HARMONICS = 16
SMOOTHING_SIGMA = 0.8  # "light"
SHORTLIST_SIZE = 10


def golden_query_trace() -> np.ndarray:
    """A deterministic asymmetric blob: rounded polygon with a notch.

    Constructed (not real geography, not a human drawing) purely as contract
    input; the production chain treats it exactly like any user trace.
    """
    angles = np.linspace(0.0, 2.0 * np.pi, 9, endpoint=False)
    radii = np.array([100.0, 62.0, 88.0, 55.0, 92.0, 70.0, 97.0, 58.0, 80.0])
    points = np.column_stack((radii * np.cos(angles) + 300.0,
                              radii * np.sin(angles) + 200.0))
    # super-sample each edge so the trace looks like a pointer path (many
    # samples, non-uniform density) rather than 9 bare vertices
    dense: list[list[float]] = []
    for index in range(points.shape[0]):
        start = points[index]
        end = points[(index + 1) % points.shape[0]]
        for step in range(30):
            fraction = step / 30.0
            dense.append([start[0] + (end[0] - start[0]) * fraction,
                          start[1] + (end[1] - start[1]) * fraction])
    return np.asarray(dense, dtype=float)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out", type=Path,
                        default=Path("web/src/search/golden_retrieval.json"))
    args = parser.parse_args(argv)

    features, shapes, metadata = load_index(args.index)
    manifest = json.loads(
        (args.index / "data_manifest.json").read_text(encoding="utf-8"))

    raw = golden_query_trace()
    ordered = ensure_ccw(raw)
    resampled = arc_length_resample(ordered, N_POINTS)
    smoothed = smooth_cyclic(resampled, sigma=SMOOTHING_SIGMA)
    normalized = normalize_contour(smoothed)
    fourier = harmonic_amplitudes(normalized, N_HARMONICS)
    _, geometry = vectors_for(normalized, n_harmonics=N_HARMONICS, fold="positive")
    query_vector = np.concatenate([fourier, geometry])

    shortlist = rank(query_vector[:N_HARMONICS], features[:, :N_HARMONICS],
                     group="fourier", scale="none",
                     shortlist_size=SHORTLIST_SIZE)

    payload = {
        "contract": "web static retrieval chain == earthglyph production chain",
        "golden_version": GOLDEN_VERSION,
        "input": {
            "query_trace": raw.tolist(),
            "n_points": N_POINTS,
            "n_harmonics": N_HARMONICS,
            "smoothing_sigma": SMOOTHING_SIGMA,
            "note": "constructed contract input, not real geography and not a "
                    "human drawing",
        },
        "expected": {
            "resampled": resampled.tolist(),
            "smoothed": smoothed.tolist(),
            "normalized": normalized.tolist(),
            "fourier_amplitudes": fourier.tolist(),
            "geometry_stats": geometry.tolist(),
            "query_vector": query_vector.tolist(),
        },
        "library": {
            "source_name": manifest.get("source", {}).get("source_name"),
            "source_release": manifest.get("source", {}).get("source_release"),
            "n_candidates": int(features.shape[0]),
            "feature_columns": manifest["index"]["feature_columns"],
            "coarse": {
                "group": "fourier",
                "scale": "none",
                "shortlist_rows": shortlist.indices.tolist(),
                "shortlist_distances": shortlist.distances.tolist(),
                "candidate_ids": [
                    str(metadata.iloc[row]["candidate_id"])
                    for row in shortlist.indices
                ],
                "distance_definition": "sqrt(mean(((lib - query)/1)^2)) over the "
                                       "16 fourier columns, stable argsort, "
                                       "ties by row index",
            },
        },
        "tolerances": {
            "resampled_per_point_abs": 1e-9,
            "smoothed_per_point_abs": 1e-9,
            "normalized_per_point_rel": 1e-9,
            "fourier_per_value_rel": 1e-9,
            "geometry_per_value_rel": 1e-9,
            "coarse_distance_rel": 1e-9,
            "coarse_order": "exact row-sequence equality",
            "note": "the TS side uses the same float64 arithmetic and the same "
                    "operation order where practical; tolerances only absorb "
                    "library summation-order differences",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"query: {raw.shape[0]} raw points -> {N_POINTS} samples, "
          f"{N_HARMONICS} harmonics")
    print(f"shortlist rows: {shortlist.indices.tolist()}")
    print(f"distances: {np.round(shortlist.distances, 6).tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
