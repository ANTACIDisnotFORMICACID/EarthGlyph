"""Symmetric Chamfer cross-check for the geometric re-rank (P0-4, Task C4).

The primary score (:func:`earthglyph.retrieval.alignment.align`) is a
**correspondence** distance: point ``k`` of the query is compared with point
``k+s`` of the candidate, so an arc-length phase mismatch — a hand-drawn bump
that pushes every later sample a few percent along the perimeter — is charged as
a boundary offset. Chamfer instead compares each point with its *nearest* point
on the other curve, which is insensitive to that phase and tolerant of a local
dent, but it also stops checking whether the two curves are traversed in the
same order.

The invariances have to be stated exactly, because the naive claim is wrong:
chamfer is a function of the **relative** pose of the two clouds. Moving both
curves together changes nothing, and neither does re-indexing where a closed
polyline starts or which way it is walked (same point set). Rotating or
reflecting *one* curve does change the number — measured on an asymmetric
contour at unit RMS radius, a 0.4 rad turn costs ``0.135`` and a mirror costs
``0.207``. So chamfer is not "blind" to a mirror the way Fourier amplitudes are
(amplitudes are exactly invariant, D26/D33); what it lacks is a *branch*: it
returns one scalar and cannot say which pose it assumed, so it cannot report a
mirror decision, cannot be minimized over ``θ`` without an outer search, and
charges a mirror and a random deformation with the same arithmetic. A near
mirror-symmetric candidate — common among real lakes — makes even that penalty
disappear. The mirror policy therefore stays in the re-rank (D33).

That trade-off is the question this module exists to answer, and the answer comes
from measurement (``experiments/p0_retrieval_baseline``), not from preference:
the cross-check can be switched on for the top candidates, and if it does not
change which candidates a human would accept, the re-rank stays alone.

Densification interpolates along each of the ``N`` chords before the
nearest-neighbour query, because with only 128 points the measured distance is
bounded below by the chord length and an apparently "closer" match can be an
artifact of coarse sampling (the effect P0-3 had to control for in
``docs/DECISIONS.md`` D30).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

DEFAULT_DENSIFY = 4


class ChamferError(ValueError):
    """Raised when two contours cannot be compared by nearest neighbours."""


def _as_array(points: Any, name: str) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2 or array.shape[0] < 3:
        raise ChamferError(f"{name} must be an (N, 2) contour with N >= 3, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ChamferError(f"{name} contains non-finite coordinates")
    return array


def densify(points: Any, factor: int = DEFAULT_DENSIFY) -> np.ndarray:
    """Insert ``factor - 1`` points on every chord of a closed polyline.

    Linear interpolation along chords: it adds no shape, it only makes the
    nearest-neighbour distance a better approximation of the curve-to-curve
    distance (residual bounded by half a sub-chord).
    """
    if int(factor) < 1:
        raise ChamferError(f"densify factor must be >= 1, got {factor}")
    array = _as_array(points, "points")
    if int(factor) == 1:
        return array.copy()
    closed = np.vstack([array, array[:1]])
    start, end = closed[:-1], closed[1:]
    steps = np.arange(int(factor), dtype=float) / float(factor)
    offsets: np.ndarray = (
        (end[:, None, :] - start[:, None, :]) * steps[None, :, None] + start[:, None, :]
    )
    return offsets.reshape(-1, 2)


def _symmetric_mean(
    forward: np.ndarray, backward: np.ndarray, *, squared: bool
) -> float:
    if squared:
        return float(0.5 * (np.mean(forward**2) + np.mean(backward**2)))
    return float(0.5 * (forward.mean() + backward.mean()))


def chamfer_distance(
    a: Any,
    b: Any,
    *,
    densify_factor: int = DEFAULT_DENSIFY,
    squared: bool = False,
) -> float:
    """Symmetric mean nearest-neighbour distance between two closed contours.

    ``0.5 * (mean over a of dist(.,b) + mean over b of dist(.,a))``, optionally
    on squared distances (which penalizes one far burst of points harder).
    Symmetric by construction, and unchanged by a rigid motion applied to **both**
    contours at once — it measures how the two curves sit relative to each other,
    not where either of them sits on the canvas.
    """
    cloud_a = densify(_as_array(a, "a"), densify_factor)
    cloud_b = densify(_as_array(b, "b"), densify_factor)
    forward = cKDTree(cloud_b).query(cloud_a, k=1)[0]
    backward = cKDTree(cloud_a).query(cloud_b, k=1)[0]
    return _symmetric_mean(forward, backward, squared=squared)


def chamfer_after_alignment(
    query: Any,
    candidate: Any,
    *,
    shift: int,
    theta_rad: float,
    mirrored: bool = False,
    densify_factor: int = DEFAULT_DENSIFY,
    squared: bool = False,
) -> float:
    """Chamfer between a query and a candidate moved by a reported alignment.

    The cyclic ``shift`` cannot change this number (same points, different index),
    but ``theta_rad`` re-poses the candidate and the mirrored branch flips it, so
    the aligned score is generally **not** the raw one. That is the intended use:
    the cross-check is read against the overlay the plot draws, i.e. against the
    pose the re-rank actually chose, and the ``(shift, theta, mirrored)`` triple
    stays auditable.
    """
    from earthglyph.retrieval.alignment import apply_alignment

    aligned = apply_alignment(candidate, shift=shift, theta_rad=theta_rad, mirrored=mirrored)
    return chamfer_distance(query, aligned, densify_factor=densify_factor, squared=squared)


def chamfer_library(
    query: Any,
    library: np.ndarray,
    *,
    indices: Sequence[int] | None = None,
    densify_factor: int = DEFAULT_DENSIFY,
    squared: bool = False,
) -> np.ndarray:
    """Chamfer of one query against selected ``(M, N, 2)`` library rows.

    ``indices`` restricts the cross-check to a shortlist — the intended use is the
    top ``k`` candidates, not the whole library: a KD-tree per candidate costs
    orders of magnitude more than the batched FFT re-rank, which is exactly why
    this is a cross-check and not a first pass.
    """
    stack = np.asarray(library, dtype=float)
    if stack.ndim != 3 or stack.shape[2] != 2:
        raise ChamferError(f"library must be (M, N, 2), got {stack.shape}")
    rows = np.arange(stack.shape[0]) if indices is None else np.asarray(indices, dtype=int)
    cloud = densify(_as_array(query, "query"), densify_factor)
    query_tree = cKDTree(cloud)
    out = np.empty(rows.shape[0], dtype=float)
    for position, row in enumerate(rows):
        candidate = densify(stack[int(row)], densify_factor)
        tree = cKDTree(candidate)
        forward = tree.query(cloud, k=1)[0]
        backward = query_tree.query(candidate, k=1)[0]
        out[position] = _symmetric_mean(forward, backward, squared=squared)
    return out
