"""A pose fitter whose only job is to make a picture readable — never a score.

Checkpoint C's figures had an unfairness in them that was easy to miss: E0 returns a
per-candidate rotation and cyclic shift, so its Top-k panels were drawn with the
candidate *already fitted onto* the query, while E1/E2/E3 return no pose at all and
were drawn as-returned.  A reader comparing how well the red and green rings overlap
was therefore comparing E0's objective against three other arms' accident of
orientation.

This module fixes the display by fitting one pose for every candidate of every arm
with the same rule, and by being deliberately unrelated to any arm:

* the cost is a plain **symmetric chamfer distance** between two point sets.  It
  uses no Fourier descriptor, no shape context, no turning event and no
  normalisation any of E0..E3 owns, so a good display pose cannot be read as
  "arm X's own metric liked this candidate".
* the search is **rotation only**, on a fixed angular grid.  Scale is not fitted:
  the shared standardizer already puts every contour at centroid-origin and RMS
  radius 1, so fitting another scale would resize panels relative to each other and
  break the "same size, same coordinate scale" rule the review sheet depends on.
  Aspect ratio is never touched, because a rotation is a rigid motion.

What this changes is the *drawing*.  ``scripts/blind_perceptual_review.py`` re-ranks
nothing, and the scores and ranks in ``results.csv`` are untouched by it: the pose is
computed after the ranking, from the ids that ranking already produced.  Every figure
written from it is labelled ``display_aligned`` and says so on the page.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable
from typing import Any

import numpy as np

from earthglyph.retrieval.perceptual_methods import check_contour

__all__ = ["align_for_display", "cache_stats", "reset_cache"]

COARSE_STEP_DEG = 3.0
FINE_STEP_DEG = 0.25
CACHE_KEY_VERSION = "display-align-v1"


def _chamfer(query: np.ndarray, candidate: np.ndarray) -> float:
    """Mean nearest-neighbour distance in both directions (a symmetric chamfer).

    Symmetric because a one-way version is happy to park a small shape inside a big
    one; the two-way mean is the cheapest thing that punishes both "you do not cover
    the query" and "you stick out everywhere".
    """
    from scipy.spatial.distance import cdist

    matrix = cdist(query, candidate)
    return float(0.5 * (matrix.min(axis=1).mean() + matrix.min(axis=0).mean()))


def _rotation(angle_deg: float) -> np.ndarray:
    theta = np.radians(angle_deg)
    cos, sin = np.cos(theta), np.sin(theta)
    return np.array([[cos, -sin], [sin, cos]], dtype=float)


def _sweep(
    query: np.ndarray,
    candidate: np.ndarray,
    angles: np.ndarray,
) -> tuple[float, float]:
    """Cheapest ``(angle, chamfer)`` of the grid; ties go to the smallest |angle|."""
    best_angle = float(angles[0])
    best_cost = np.inf
    for angle in angles:
        cost = _chamfer(query, candidate @ _rotation(float(angle)).T)
        if cost < best_cost - 1e-15:
            best_cost, best_angle = cost, float(angle)
    return best_angle, float(best_cost)


def align_for_display(
    query: Any,
    candidate: Any,
    *,
    coarse_step_deg: float = COARSE_STEP_DEG,
    fine_step_deg: float = FINE_STEP_DEG,
    key: Hashable | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Rotate ``candidate`` onto ``query`` for display; return the posed contour.

    Two passes: a full turn on ``coarse_step_deg``, then a local refinement of one
    coarse cell on ``fine_step_deg``.  Deterministic — no randomness, and the same
    inputs always produce the same angle — which is what lets a review sheet be
    rebuilt from a commit.

    ``key`` caches the fit.  The cache is keyed by the caller's own id pair *and* by
    a hash of the two contours, so a stale entry cannot be served after the dataset
    changes underneath it; ``cache_stats()`` reports hits so a figure log can say how
    much of its time was fitting poses and how much was drawing.
    """
    left = check_contour(query, "query")
    right = check_contour(candidate, "candidate")
    cache_key: str | None = None
    if key is not None:
        digest = hashlib.sha256(
            CACHE_KEY_VERSION.encode()
            + np.ascontiguousarray(left).tobytes()
            + np.ascontiguousarray(right).tobytes()
            + repr(key).encode()
        ).hexdigest()
        cache_key = f"{digest}|{coarse_step_deg}|{fine_step_deg}"
        hit = _CACHE.get(cache_key)
        if hit is not None:
            _stats["hits"] += 1
            return right @ _rotation(hit["theta_deg"]).T, dict(hit)
        _stats["misses"] += 1

    before = _chamfer(left, right)
    coarse_angles = np.arange(0.0, 360.0, coarse_step_deg)
    angle, after = _sweep(left, right, coarse_angles)
    # one coarse cell either side, so the refinement cannot jump to a neighbour's basin
    refine = np.arange(angle - coarse_step_deg, angle + coarse_step_deg + 1e-9, fine_step_deg)
    angle, after = _sweep(left, right, refine)
    angle %= 360.0
    info = {
        "theta_deg": float(angle),
        "chamfer_before": before,
        "chamfer_after": after,
        "coarse_step_deg": float(coarse_step_deg),
        "fine_step_deg": float(fine_step_deg),
        "cost": "symmetric chamfer, rotation-only, display only",
    }
    if cache_key is not None:
        _CACHE[cache_key] = info
    return right @ _rotation(angle).T, info


_CACHE: dict[str, dict[str, Any]] = {}
_stats = {"hits": 0, "misses": 0}


def cache_stats() -> dict[str, int]:
    """Cache hits and misses since the last :func:`reset_cache`."""
    return dict(_stats)


def reset_cache() -> None:
    """Drop the display cache and its counters (used by the tests)."""
    _CACHE.clear()
    for name in list(_stats):
        _stats[name] = 0
