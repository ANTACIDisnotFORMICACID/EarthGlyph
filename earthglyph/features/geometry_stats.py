"""Physically meaningful shape statistics used beside the Fourier descriptor.

Four features, each dimensionless and each with a closed-form definition:

``compactness``
    ``4*pi*A / P^2`` — the isoperimetric quotient. Exactly 1 for a disc, drops
    as the boundary gets longer for a given area (dendritic, crenulated).
``solidity``
    ``A / A_hull`` — how much of the convex hull the shape fills. Low for
    crescents and deeply concave outlines, near 1 for discs and blobs.
``aspect_ratio``
    long side / short side of the minimum-area rotated rectangle. 1 for a
    square-ish blob, large for slender lakes. The rectangle and the hull above
    come from one NumPy rotating-calipers computation (see
    :func:`rotated_rectangle_extents` for why GEOS is not used).
``diameter_fill``
    ``4*A / (pi*D^2)`` with ``D`` the largest distance between two contour
    points. Unlike ``solidity`` this measures against the enclosing *circle*,
    so it stays low for shapes that are long but not necessarily concave.

Basis — stated explicitly because it matters for comparability: all four are
computed on the **standardized sampled contour** (its exterior ring closed onto
itself), not on the raw source geometry. A hand-drawn query only ever yields a
sampled ring, so query and candidate must be measured the same way. Two
consequences are recorded rather than hidden: holes (islands) are excluded,
which differs from the P0-2 figure metric ``perimeter^2 / area`` that used the
full boundary (see ``experiments/p0_geometry_probe/notes.md``); and the values
carry the same sampling error as the contour itself, which
``earthglyph.geometry.fidelity`` quantifies.

Invariances: every feature here is invariant to translation and uniform scale,
and to rotation as well — ``compactness`` and ``solidity`` are invariants of the
point set, and the rotated rectangle / pairwise diameter are recomputed in the
shape's own frame. They are *not* invariant to mirroring in any special sense
(they do not change under mirroring at all, which is why they cannot separate a
shape from its mirror image — that is a re-ranking concern, D9/D10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from earthglyph.features.fourier import FeatureError
from earthglyph.geometry.resampling import ring_perimeter, signed_area

GEOMETRY_SCHEMA_VERSION = "1.0"

COLUMN_COMPACTNESS = "compactness"
COLUMN_SOLIDITY = "solidity"
COLUMN_ASPECT_RATIO = "aspect_ratio"
COLUMN_DIAMETER_FILL = "diameter_fill"

FEATURE_COLUMNS: tuple[str, ...] = (
    COLUMN_COMPACTNESS,
    COLUMN_SOLIDITY,
    COLUMN_ASPECT_RATIO,
    COLUMN_DIAMETER_FILL,
)

_DEGENERATE_EPS = 1e-12


@dataclass(frozen=True)
class ShapeMetrics:
    """One row of geometry statistics, kept separate from the Fourier group."""

    compactness: float
    solidity: float
    aspect_ratio: float
    diameter_fill: float

    def as_vector(self) -> np.ndarray:
        return np.array(
            [self.compactness, self.solidity, self.aspect_ratio, self.diameter_fill],
            dtype=float,
        )

    def as_dict(self) -> dict[str, float]:
        return dict(zip(FEATURE_COLUMNS, self.as_vector().tolist(), strict=True))


def _closed(ring: np.ndarray) -> np.ndarray:
    """Validate an (M, 2) ring and append the closing vertex."""
    arr = np.asarray(ring, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise FeatureError(f"ring must have shape (M, 2), got {arr.shape}")
    if arr.shape[0] < 3:
        raise FeatureError(f"a ring needs at least 3 points, got {arr.shape[0]}")
    if not np.all(np.isfinite(arr)):
        raise FeatureError("ring contains non-finite coordinates")
    if np.allclose(arr[0], arr[-1], rtol=0.0, atol=_DEGENERATE_EPS):
        arr = arr[:-1]
    return np.vstack([arr, arr[:1]])


def polygon_area(ring: np.ndarray) -> float:
    """Unsigned area enclosed by a closed or open ring sequence."""
    return abs(signed_area(_closed(ring)[:-1]))


def max_point_distance(ring: np.ndarray) -> float:
    """Diameter: the largest distance between two ring points (exact, O(M^2))."""
    arr = _closed(ring)[:-1]
    diff = arr[:, None, :] - arr[None, :, :]
    dist = np.sqrt((diff**2).sum(axis=2))
    return float(dist.max())


def convex_hull_points(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise convex hull of a point set, closing vertex omitted.

    Andrew's monotone chain, computed here rather than through GEOS because the
    rotated rectangle below is derived from the same hull and must be exactly
    consistent with it. Collinear vertices are dropped.
    """
    arr = np.unique(np.asarray(points, dtype=float), axis=0)
    if arr.shape[0] < 3:
        raise FeatureError(f"fewer than 3 distinct points to hull ({arr.shape[0]})")

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    rows = [tuple(row) for row in arr]
    lower: list[tuple[float, float]] = []
    for point in rows:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(rows):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    hull = np.array(lower[:-1] + upper[:-1], dtype=float)
    if hull.shape[0] < 3:
        raise FeatureError("point set is collinear: no convex hull area")
    return hull


def convex_hull_area(points: np.ndarray) -> float:
    return abs(signed_area(convex_hull_points(points)))


def rotated_rectangle_extents(points: np.ndarray) -> tuple[float, float]:
    """Side lengths ``(long, short)`` of the minimum-area enclosing rectangle.

    The minimum-area rectangle of a point set has one side collinear with a
    convex-hull edge, so the calipers test each hull direction and keep the
    smallest enclosing band. Computed in NumPy on purpose: GEOS's
    ``minimum_rotated_rectangle`` was observed to collapse a valid 40-vertex
    hull to a zero-width sliver (area 6e-18), which would silently poison the
    ``aspect_ratio`` column at some sample counts and not others.
    """
    hull = convex_hull_points(points)
    best: tuple[float, float] | None = None
    for index in range(hull.shape[0]):
        edge = hull[(index + 1) % hull.shape[0]] - hull[index]
        length = float(np.hypot(edge[0], edge[1]))
        if length <= _DEGENERATE_EPS:
            continue
        along = hull @ (edge / length)
        across = hull @ np.array([-edge[1] / length, edge[0] / length])
        extents = (float(along.max() - along.min()), float(across.max() - across.min()))
        if best is None or extents[0] * extents[1] < best[0] * best[1]:
            best = extents
    if best is None:
        raise FeatureError("degenerate ring: convex hull has no edges")
    return max(best), min(best)


def rectangle_aspect_ratio(ring: np.ndarray) -> float:
    """Long/short side of the minimum-area rotated rectangle around the ring."""
    long_side, short_side = rotated_rectangle_extents(_closed(ring)[:-1])
    if short_side <= _DEGENERATE_EPS:
        raise FeatureError("degenerate ring: rotated rectangle has no width")
    return long_side / short_side


def shape_metrics(ring: np.ndarray) -> ShapeMetrics:
    """Compute the four geometry statistics of one contour ring.

    Raises:
        FeatureError: on non-finite input, fewer than 3 distinct points, or a
            ring that encloses no area / has no width. Callers must count and
            report these — never drop a candidate silently.
    """
    points = _closed(ring)[:-1]
    area = polygon_area(points)
    perimeter = ring_perimeter(points)
    if area <= _DEGENERATE_EPS or perimeter <= _DEGENERATE_EPS:
        raise FeatureError("degenerate ring: zero area or zero perimeter")
    hull_area = convex_hull_area(points)
    if hull_area <= _DEGENERATE_EPS:
        raise FeatureError("degenerate ring: convex hull has no area")
    diameter = max_point_distance(points)
    if diameter <= _DEGENERATE_EPS:
        raise FeatureError("degenerate ring: zero diameter")

    return ShapeMetrics(
        compactness=4.0 * np.pi * area / perimeter**2,
        solidity=area / hull_area,
        aspect_ratio=rectangle_aspect_ratio(points),
        diameter_fill=4.0 * area / (np.pi * diameter**2),
    )


def batch_shape_metrics(rings: Any) -> np.ndarray:
    """(M, len(FEATURE_COLUMNS)) geometry statistics for a stack of rings."""
    arr = np.asarray(rings, dtype=float)
    if arr.ndim != 3 or arr.shape[2] != 2:
        raise FeatureError(f"expected (M, N, 2) contours, got shape {arr.shape}")
    return np.stack([shape_metrics(row).as_vector() for row in arr])


def geometry_feature_definition() -> dict[str, Any]:
    """Specification recorded in the index manifest (formulas + invariances)."""
    return {
        "schema_version": GEOMETRY_SCHEMA_VERSION,
        "name": "dimensionless geometry statistics",
        "basis": (
            "computed on the standardized sampled contour ring (exterior only, "
            "closed onto itself); the same definition is applied to query "
            "sketches so query and candidate stay comparable"
        ),
        "columns": list(FEATURE_COLUMNS),
        "formulas": {
            COLUMN_COMPACTNESS: "4*pi*area / perimeter^2 (1 for a disc)",
            COLUMN_SOLIDITY: "area / convex_hull_area (1 for a convex shape)",
            COLUMN_ASPECT_RATIO: (
                "long_side / short_side of the minimum-area enclosing rectangle "
                "(own rotating-calipers hull computation, not GEOS)"
            ),
            COLUMN_DIAMETER_FILL: "4*area / (pi*diameter^2), diameter = max point distance",
        },
        "invariances": ["translation", "uniform scale", "rotation", "mirroring"],
        "not_invariant_to": ["sampling resolution (N points)", "self-intersection repair"],
        "degenerate_policy": (
            "zero area / zero perimeter / zero hull / zero rectangle width raise "
            "FeatureError and are counted, never silently dropped"
        ),
        "notes": [
            "holes (islands) are excluded: the contour is exterior-ring only (D21)",
            "no fusion weights are applied here; the groups stay separable for ablation",
        ],
    }
