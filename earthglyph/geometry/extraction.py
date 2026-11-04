"""Polygon → contour-source extraction (P0-2).

Deals with the real-world state of Overture water polygons: invalid or
self-intersecting geometries, MultiPolygons that must become separate
candidates (D4), holes, and degenerate rings. Extraction is purely
geometric — semantic rules (subtype, minimum area, duplicates) live in
:mod:`earthglyph.geometry.filtering`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

POLYGONAL_TYPES = ("Polygon", "MultiPolygon")


@dataclass(frozen=True)
class RepairInfo:
    """What :func:`repair_geometry` did, for the manifest and audit trail."""

    repaired: bool = False
    reason: str | None = None
    result_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repaired": self.repaired,
            "reason": self.reason,
            "result_type": self.result_type,
        }


NO_REPAIR = RepairInfo()


def repair_geometry(geometry: Any) -> tuple[Any, RepairInfo]:
    """Repair a geometry only when it is actually invalid.

    ``make_valid`` is not applied unconditionally: valid input is returned
    untouched so the stored contour is provably the source shape. The returned
    ``RepairInfo`` records the validity reason and the repaired geometry type,
    because a repair can legitimately turn a Polygon into a MultiPolygon or a
    GeometryCollection (whose polygonal parts are then kept).
    """
    import shapely

    if geometry is None or shapely.is_empty(geometry) or shapely.is_valid(geometry):
        return geometry, NO_REPAIR
    reason = str(shapely.is_valid_reason(geometry))
    fixed = shapely.make_valid(geometry)
    return fixed, RepairInfo(
        repaired=True, reason=reason, result_type=str(fixed.geom_type)
    )


def polygonal_parts(geometry: Any) -> list[Any]:
    """Flatten a geometry into its non-empty Polygon parts.

    MultiPolygons are split into independent candidates (D4); a
    GeometryCollection produced by ``make_valid`` contributes only its
    polygonal parts. Non-polygonal input yields an empty list.
    """
    if geometry is None:
        return []
    kind = geometry.geom_type
    if kind == "Polygon":
        return [] if geometry.is_empty else [geometry]
    if kind in ("MultiPolygon", "GeometryCollection"):
        parts: list[Any] = []
        for sub in geometry.geoms:
            parts.extend(polygonal_parts(sub))
        return parts
    return []


def is_usable_polygon(polygon: Any) -> bool:
    """A polygon whose exterior ring can carry a contour (>= 3 distinct points)."""
    if polygon is None or polygon.geom_type != "Polygon" or polygon.is_empty:
        return False
    return len(polygon.exterior.coords) >= 4


def exterior_ring(polygon: Any) -> np.ndarray:
    """Closed exterior ring as an (M, 2) array; first vertex repeated last."""
    return np.asarray(polygon.exterior.coords, dtype=float)[:, :2]


def hole_count(polygon: Any) -> int:
    """Number of interior rings. Holes are counted, never merged into the outline."""
    return len(polygon.interiors)


@dataclass(frozen=True)
class FeaturePart:
    """One candidate outline extracted from a source feature."""

    polygon: Any
    part_index: int
    n_parts: int
    repair: RepairInfo


def split_feature(geometry: Any) -> list[FeaturePart]:
    """Repair (if needed) and split a feature geometry into candidate parts.

    Returns an empty list when nothing polygonal survives, which the caller
    records as an ``unrepairable_after_make_valid`` rejection.
    """
    fixed, repair = repair_geometry(geometry)
    polys = [p for p in polygonal_parts(fixed) if is_usable_polygon(p)]
    n_parts = len(polys)
    return [
        FeaturePart(polygon=p, part_index=i, n_parts=n_parts, repair=repair)
        for i, p in enumerate(polys)
    ]
