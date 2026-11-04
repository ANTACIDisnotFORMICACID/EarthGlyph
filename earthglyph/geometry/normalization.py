"""Translation- and scale-invariant contour normalization.

This is the shared normalization primitive: preprocessing, the future retrieval
stage and the web frontend must all use the same definition
(docs/RETRIEVAL_DESIGN.md §2 and §6), so it lives in one place and is
re-exported as ``earthglyph.geometry.normalize_contour``.
"""

from __future__ import annotations

import numpy as np


def normalize_contour(points: np.ndarray) -> np.ndarray:
    """Translate a closed contour to its centroid and scale by RMS radius.

    Makes the contour translation- and scale-invariant, which is the first
    normalization step before computing rotation-tolerant shape features.

    Args:
        points: Array of shape (N, 2) with the contour's x/y coordinates.
            The contour is treated as open here; closure is the caller's job.

    Returns:
        Array of shape (N, 2), centered at the origin with unit RMS radius.

    Raises:
        ValueError: If fewer than 3 points are given, coordinates are not 2D,
            or the contour is degenerate (zero RMS radius).
    """
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("points must have shape (N, 2)")
    if pts.shape[0] < 3:
        raise ValueError("a contour needs at least 3 points")

    centered = pts - pts.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum(centered**2, axis=1))))
    if rms <= 1e-12:
        raise ValueError("degenerate contour: zero RMS radius")
    return centered / rms
