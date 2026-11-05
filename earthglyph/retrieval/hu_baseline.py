"""Hu-moment baseline through OpenCV (P0-4, Task C1).

Included because it is the standard reference a shape-retrieval claim has to beat,
not because it fits this problem: the seven Hu moments are invariant to
translation, scale, rotation **and mirroring** all at once, so they cannot express
the mirror policy the product needs (D33), and they are domain-integrated moments
of a *filled* region, so a dendritic reservoir and a smooth lake of the same area
and gross orientation are hard to separate.

To make the comparison fair, every contour goes through the same rasterization:

* one square canvas (``512`` px) with a fixed margin, so no shape gets a
  resolution advantage;
* **one** pixel scale per shape, applied to x and y alike — stretching the axes
  independently would change the moments and is exactly what "same canvas" must
  not mean;
* the closed contour filled as a polygon (the alternative, a thin stroke, measures
  the boundary line rather than the region and would silently compare a different
  quantity);
* the same ``cv2.matchShapes`` comparator and method for query and candidates.

Because a filled raster quantizes the outline, :func:`raster_info` reports the
minor-axis extent in pixels, and :func:`to_pixels` refuses a contour that lands
thinner than ``MIN_THICKNESS_PX``: such a shape has no interior on this canvas,
and OpenCV would still hand back a plausible-looking number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

CANVAS_SIZE = 512
MARGIN_RATIO = 0.06
MIN_THICKNESS_PX = 2.0
METHODS = ("I1", "I2", "I3")
SOURCES = ("mask", "contour")


class BaselineError(ValueError):
    """Raised when the OpenCV baseline cannot run."""


def _cv2() -> Any:
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - depends on the extras
        raise BaselineError(
            "the Hu-moment baseline needs OpenCV; install it with `pip install .[cv]`"
        ) from error
    return cv2


def check_method(method: str) -> str:
    if method not in METHODS:
        raise BaselineError(f"method must be one of {METHODS}, got {method!r}")
    return method


def check_source(source: str) -> str:
    if source not in SOURCES:
        raise BaselineError(f"source must be one of {SOURCES}, got {source!r}")
    return source


def _as_contour(points: Any, name: str) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2 or array.shape[0] < 3:
        raise BaselineError(f"{name} must be an (N, 2) contour with N >= 3, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise BaselineError(f"{name} contains non-finite coordinates")
    return array


def raster_info(
    points: np.ndarray, *, canvas: int = CANVAS_SIZE, margin: float = MARGIN_RATIO
) -> dict[str, float]:
    """The uniform pixel scale a contour gets, plus what that means in pixels.

    ``half_extent`` is the largest |coordinate| over both axes: one number, so the
    long and short axis are scaled by the same factor. ``minor_axis_px`` is the
    resulting width of the thinner axis — the honest measure of how much detail
    the raster can carry for this shape.
    """
    if int(canvas) < 16:
        raise BaselineError(f"canvas must be >= 16, got {canvas}")
    if not 0.0 <= float(margin) < 0.5:
        raise BaselineError(f"margin must be in [0, 0.5), got {margin}")
    half_extent = float(np.max(np.abs(points)))
    if not half_extent > 0.0:
        raise BaselineError("contour collapses to a point; nothing to rasterize")
    radius = 0.5 * float(canvas) * (1.0 - float(margin))
    scale = radius / half_extent
    extents = np.max(points, axis=0) - np.min(points, axis=0)
    return {
        "canvas": float(canvas),
        "margin": float(margin),
        "half_extent": half_extent,
        "pixel_scale": scale,
        "major_axis_px": float(extents.max() * scale),
        "minor_axis_px": float(extents.min() * scale),
    }


def to_pixels(
    points: np.ndarray, *, canvas: int = CANVAS_SIZE, margin: float = MARGIN_RATIO
) -> np.ndarray:
    """Geometry coordinates to integer pixel coordinates, y flipped.

    One uniform scale for both axes (see :func:`raster_info`) and the geometry
    origin placed at the canvas center, so the same shape always rasterizes the
    same way whichever consumer asks for it.

    A contour thinner than ``MIN_THICKNESS_PX`` after rasterizing is rejected
    rather than measured: ``cv2.fillPoly`` still paints pixels for a sliver, so
    the moments would come back as a *number* describing a one-pixel line — the
    failure mode a baseline comparison must not swallow silently.
    """
    info = raster_info(points, canvas=canvas, margin=margin)
    if info["minor_axis_px"] < MIN_THICKNESS_PX:
        raise BaselineError(
            f"contour rasterizes to a {info['minor_axis_px']:.1f}-pixel sliver "
            f"(need >= {MIN_THICKNESS_PX}); its region moments would be noise"
        )
    center = 0.5 * float(canvas)
    pixel = np.column_stack(
        [center + info["pixel_scale"] * points[:, 0], center - info["pixel_scale"] * points[:, 1]]
    )
    integer = np.rint(pixel).astype(np.int32)
    integer[:, 0] = np.clip(integer[:, 0], 0, int(canvas) - 1)
    integer[:, 1] = np.clip(integer[:, 1], 0, int(canvas) - 1)
    return integer


def rasterize(
    points: Any,
    *,
    canvas: int = CANVAS_SIZE,
    margin: float = MARGIN_RATIO,
) -> tuple[np.ndarray, dict[str, float]]:
    """Fill a closed contour on a square canvas: y-up geometry to y-down pixels.

    The centroid is *not* re-removed here — inputs are already standardized — but
    the drawing is centered on the canvas, so where a shape sits in its own
    coordinate frame cannot affect its moments.
    """
    cv2 = _cv2()
    array = _as_contour(points, "points")
    info = raster_info(array, canvas=canvas, margin=margin)
    mask = np.zeros((int(canvas), int(canvas)), dtype=np.uint8)
    cv2.fillPoly(mask, [to_pixels(array, canvas=canvas, margin=margin)[:, None, :]], 255)
    return mask, info


def hu_moments(
    points: Any, *, canvas: int = CANVAS_SIZE, margin: float = MARGIN_RATIO
) -> np.ndarray:
    """The seven Hu moments of one contour's filled region."""
    cv2 = _cv2()
    mask, _ = rasterize(points, canvas=canvas, margin=margin)
    return np.asarray(cv2.HuMoments(cv2.moments(mask)).flatten()).reshape(7)


def log_hu(points: Any, **kwargs: Any) -> np.ndarray:
    """``sign(h) * log10(|h| + eps)`` form, for reporting the moment vector."""
    values = hu_moments(points, **kwargs)
    return np.sign(values) * np.log10(np.abs(values) + 1e-20)


def _reference(
    points: Any, *, source: str, canvas: int, margin: float
) -> Any:
    """What ``cv2.matchShapes`` receives: a filled mask or the boundary vertices.

    Both live on the same pixel grid, so ``"contour"`` differs from ``"mask"``
    only in whether the region or its outline carries the moments — rounding a
    unit-RMS contour straight to integers would not, it would collapse it.
    """
    array = _as_contour(points, "points")
    if check_source(source) == "mask":
        return rasterize(array, canvas=canvas, margin=margin)[0]
    return to_pixels(array, canvas=canvas, margin=margin)[:, None, :]


def match_shapes(
    a: Any,
    b: Any,
    *,
    method: str = "I1",
    source: str = "mask",
    canvas: int = CANVAS_SIZE,
    margin: float = MARGIN_RATIO,
) -> float:
    """``cv2.matchShapes`` distance between two contours (lower is more similar).

    The value is an internal cost, not a similarity percentage (D11).
    """
    cv2 = _cv2()
    flag = getattr(cv2, f"CONTOURS_MATCH_{check_method(method)}")
    first = _reference(a, source=source, canvas=canvas, margin=margin)
    second = _reference(b, source=source, canvas=canvas, margin=margin)
    distance = float(cv2.matchShapes(first, second, flag, 0.0))
    if not np.isfinite(distance) or distance < 0.0:
        raise BaselineError(f"cv2.matchShapes returned {distance}")
    return distance


@dataclass(frozen=True)
class HuRanking:
    """Full-library Hu ranking with the timing and rasterization contract recorded."""

    order: np.ndarray
    distances: np.ndarray
    seconds: float
    seconds_per_candidate: float
    method: str
    source: str
    canvas: int
    margin: float
    raster: dict[str, float]

    def top(self, k: int) -> list[tuple[int, float]]:
        limit = min(int(k), self.order.shape[0])
        return [(int(self.order[i]), float(self.distances[i])) for i in range(limit)]

    def to_dict(self, k: int | None = None) -> dict[str, Any]:
        limit = self.order.shape[0] if k is None else min(int(k), self.order.shape[0])
        return {
            "hu_method": self.method,
            "hu_source": self.source,
            "hu_canvas": self.canvas,
            "hu_margin": self.margin,
            "hu_seconds": round(self.seconds, 6),
            "hu_seconds_per_candidate": round(self.seconds_per_candidate, 9),
            "hu_top_indices": [int(i) for i in self.order[:limit]],
            "hu_top_distances": [float(d) for d in self.distances[:limit]],
            "hu_minor_axis_px": self.raster["minor_axis_px"],
        }


def rank_by_hu(
    query: Any,
    library: np.ndarray,
    *,
    method: str = "I1",
    source: str = "mask",
    canvas: int = CANVAS_SIZE,
    margin: float = MARGIN_RATIO,
) -> HuRanking:
    """Rank every candidate against one query by Hu moment distance.

    Timed as one wall-clock span: the baseline's cost *is* part of the comparison,
    since the re-rank it is measured against is a batched FFT.
    """
    stack = np.asarray(library, dtype=float)
    if stack.ndim != 3 or stack.shape[2] != 2:
        raise BaselineError(f"library must be (M, N, 2), got {stack.shape}")
    check_method(method)
    check_source(source)
    query_reference = _reference(query, source=source, canvas=canvas, margin=margin)
    cv2 = _cv2()
    flag = getattr(cv2, f"CONTOURS_MATCH_{method}")
    started = time.perf_counter()
    distances = np.empty(stack.shape[0], dtype=float)
    for index in range(stack.shape[0]):
        candidate = _reference(stack[index], source=source, canvas=canvas, margin=margin)
        distances[index] = float(cv2.matchShapes(query_reference, candidate, flag, 0.0))
    elapsed = time.perf_counter() - started
    order = np.argsort(distances, kind="stable")
    return HuRanking(
        order=order.astype(np.int64),
        distances=distances,
        seconds=elapsed,
        seconds_per_candidate=elapsed / max(stack.shape[0], 1),
        method=method,
        source=source,
        canvas=canvas,
        margin=margin,
        raster=raster_info(_as_contour(query, "query"), canvas=canvas, margin=margin),
    )
