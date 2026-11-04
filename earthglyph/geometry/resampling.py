"""Arc-length resampling and normalization of closed contours (P0-2).

Contract (docs/RETRIEVAL_DESIGN.md §2): a stored contour is a fixed number of
points sampled uniformly *by arc length* along the exterior ring of a polygon,
translated to its centroid and scaled to unit RMS radius.

Deliberately **not** done here: rotation alignment, cyclic-start matching and
mirroring. Those are retrieval concerns (D8/D9); applying them in
preprocessing would hide each candidate's real orientation and make mirror
pairs indistinguishable before any matching happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from earthglyph.geometry.normalization import normalize_contour

DEFAULT_N_POINTS = 128

# Vertices closer than this (in the working units) are treated as duplicates.
_DUP_EPS = 1e-12


class ContourError(ValueError):
    """Raised when a ring cannot be turned into a usable contour."""


@dataclass(frozen=True)
class ResampledContour:
    """A standardized contour plus the metric facts needed for provenance."""

    points: np.ndarray  # (N, 2) centroid-centered, unit RMS radius
    metric_points: np.ndarray  # (N, 2) resampled ring in metres
    centroid_m: tuple[float, float]
    rms_radius_m: float
    perimeter_m: float
    original_winding: str  # winding of the source ring before normalization

    def to_dict(self) -> dict[str, Any]:
        return {
            "centroid_m": list(self.centroid_m),
            "rms_radius_m": self.rms_radius_m,
            "perimeter_m": self.perimeter_m,
            "original_winding": self.original_winding,
            "n_points": int(self.points.shape[0]),
        }


def open_ring(ring: Any) -> np.ndarray:
    """Drop the duplicated closing vertex and any repeated consecutive vertices.

    Input is an (M, 2) closed or open sequence; output is an open (M', 2)
    sequence with M' >= 3 distinct points. Index-based sampling is never used
    downstream, so removing duplicate vertices does not distort arc lengths.
    """
    arr = np.asarray(ring, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ContourError(f"ring must have shape (M, 2), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ContourError("ring contains non-finite coordinates")
    if arr.shape[0] >= 2 and np.allclose(arr[0], arr[-1], rtol=0.0, atol=_DUP_EPS):
        arr = arr[:-1]
    if arr.shape[0] >= 2:
        moved = np.abs(np.diff(arr, axis=0)).max(axis=1) > _DUP_EPS
        arr = arr[np.concatenate(([True], moved))]
    if arr.shape[0] < 3:
        raise ContourError(
            f"a contour needs at least 3 distinct points, got {arr.shape[0]}"
        )
    return arr


def signed_area(ring_open: np.ndarray) -> float:
    """Shoelace area of an open ring; positive when counter-clockwise."""
    x, y = ring_open[:, 0], ring_open[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def ensure_ccw(ring_open: np.ndarray) -> np.ndarray:
    """Normalize traversal direction to counter-clockwise.

    Reversal only changes the walking direction of the same outline; it is not
    a mirror operation (D9).
    """
    return ring_open if signed_area(ring_open) >= 0.0 else ring_open[::-1]


def winding_of(ring_open: np.ndarray) -> str:
    return "ccw" if signed_area(ring_open) >= 0.0 else "cw"


def ring_perimeter(ring_open: np.ndarray) -> float:
    """Perimeter of the closed outline described by an open ring."""
    closed = np.vstack([ring_open, ring_open[:1]])
    return float(np.linalg.norm(np.diff(closed, axis=0), axis=1).sum())


def arc_length_resample(ring_open: np.ndarray, n: int = DEFAULT_N_POINTS) -> np.ndarray:
    """Resample an open ring to ``n`` points equally spaced along its perimeter.

    Sample positions come from cumulative arc length, not from vertex indices,
    so an outline with a few long edges is sampled as densely as one with many
    short edges. The first sample sits on the ring's first vertex; cyclic-start
    alignment is left to the retrieval stage.
    """
    if n < 3:
        raise ContourError(f"n must be at least 3, got {n}")
    closed = np.vstack([ring_open, ring_open[:1]])
    segments = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(segments)))
    total = float(cumulative[-1])
    if not np.isfinite(total) or total <= 0.0:
        raise ContourError("ring has zero perimeter")
    targets = np.arange(n, dtype=float) * (total / n)
    x = np.interp(targets, cumulative, closed[:, 0])
    y = np.interp(targets, cumulative, closed[:, 1])
    return np.column_stack((x, y))


def build_contour(ring: Any, n: int = DEFAULT_N_POINTS) -> ResampledContour:
    """Full preprocessing chain for one exterior ring given in metres.

    dedupe closing point → validate → normalize winding → arc-length resample
    → center → RMS-scale (reusing :func:`normalize_contour`).
    """
    opened = open_ring(ring)
    winding = winding_of(opened)
    ordered = ensure_ccw(opened)
    perimeter = ring_perimeter(ordered)
    area = abs(signed_area(ordered))
    if area <= _DUP_EPS * max(perimeter, 1.0) ** 2:
        raise ContourError("ring encloses no area")
    metric = arc_length_resample(ordered, n)
    points = normalize_contour(metric)
    centroid = metric.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum((metric - centroid) ** 2, axis=1))))
    return ResampledContour(
        points=points,
        metric_points=metric,
        centroid_m=(float(centroid[0]), float(centroid[1])),
        rms_radius_m=rms,
        perimeter_m=perimeter,
        original_winding=winding,
    )
