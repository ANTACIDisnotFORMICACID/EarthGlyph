"""E0 — the P0-4 pipeline exposed through the comparison contract, unchanged.

E0 is *"Fourier coarse filter -> rotation/cyclic-shift alignment -> geometric
distance"*, and it is the incumbent: the product path (``search_by_contour``)
still runs it with its own defaults, and nothing here alters that.  What this
adapter does is answer the narrower question the comparison needs — given a
query and a library of standardized contours, what is the **full-gallery**
alignment ranking?

Two deliberate choices, both so that a difference against E1/E2/E3 is a
difference in the score and not in the plumbing:

* the coarse stage is **bypassed**, not tuned.  ``align`` is already O(n log n)
  per candidate thanks to the closed-form cross-spectrum, so at gallery sizes
  the shortlist buys nothing and would only hide candidates;
* the alignment result is the *same* ``align()`` call the product uses, with the
  same ``allow_mirror`` switch — this module computes nothing new.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from earthglyph.retrieval.alignment import align
from earthglyph.retrieval.perceptual_methods import check_contour, contour_stack

__all__ = ["POLICY", "describe_policy", "distance", "distance_library", "score_library"]

POLICY = {
    "id": "E0",
    "name": "P0-4 baseline: Fourier coarse filter + closed-form rotation/shift alignment",
    "score": "aligned RMS boundary offset of the 128-point contour (smaller is closer)",
    "invariance": {
        "translation": "exact (contours are centroid-centred by the standardizer)",
        "uniform_scale": "exact (unit RMS radius by the standardizer)",
        "rotation": "searched in closed form by the cross spectrum, not assumed",
        "start_point": "searched cyclically in the same closed form",
        "traversal_direction": "NOT invariant: rings are made CCW upstream, a reversal is "
        "a separate invariance probe",
        "mirror": "only when allow_mirror=True; off by default, as in the product path",
    },
    "coarse_window": "bypassed here; the product path still uses shortlist_size=200",
    "cost": "O(n log n) per candidate (one FFT pair + one inverse per comparison)",
    "new_code_in_this_checkpoint": False,
}


def describe_policy() -> dict[str, Any]:
    return dict(POLICY)


def distance(
    query: Any, candidate: Any, *, allow_mirror: bool = False
) -> tuple[float, dict[str, Any]]:
    """Alignment distance between two standardized contours, plus what was fitted."""
    a = check_contour(query, "query")
    b = check_contour(candidate, "candidate")
    result = align(a, b, allow_mirror=allow_mirror)
    return float(result.distance), {
        "shift": int(result.shift),
        "theta_deg": float(np.degrees(result.theta_rad)),
        "mirrored": bool(result.mirrored),
        "ambiguous_rotations": int(result.alternatives),
    }


def distance_library(
    query: Any, library: Any, *, allow_mirror: bool = False
) -> np.ndarray:
    return np.array(
        [distance(query, row, allow_mirror=allow_mirror)[0] for row in library], dtype=float
    )


def score_library(
    query: Any, library: Any, *, allow_mirror: bool = False, **_: Any
) -> tuple[np.ndarray, dict[str, Any]]:
    """ ``(distances, policy)`` for every row of ``library``."""
    rows = contour_stack(library, "library")
    scores = np.empty(rows.shape[0], dtype=float)
    transforms: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        value, fitted = distance(query, row, allow_mirror=allow_mirror)
        scores[index] = value
        transforms.append(fitted)
    return scores, {"policy": describe_policy(), "transforms": transforms}
