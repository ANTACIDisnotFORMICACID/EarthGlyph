"""Candidate filtering with per-reason accounting (P0-2).

Every rule is a parameter, not a hardcoded law: the subtype allowlist, the
minimum area and the duplicate-overlap threshold all come from
:class:`FilterConfig` so an experiment can re-run with different values and
the manifest records which ones were used.

Note on bounding-box edges: the download bbox is an *intersection* query, so a
feature touching the frame is not necessarily clipped. Measured on the real
Wisconsin extract, 0 of 1,984 usable polygons have an exterior vertex or edge
lying on the frame, and the 17 features whose bbox exceeds the frame are whole
geometries (the largest is a Mississippi river pool west of the frame, not a
cut-off lake). Frame contact is therefore reported as an informational flag
(:func:`extends_beyond_frame`) instead of being used to delete candidates —
see docs/DECISIONS.md D15 (revised).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_SUBTYPES: tuple[str, ...] = ("lake", "pond", "reservoir")
DEFAULT_MIN_AREA_M2 = 1000.0  # 0.1 ha — experiment parameter, see FilterConfig
DEFAULT_MAX_IOU = 0.95
EPS_DEG = 1e-9

REASON_NULL_GEOMETRY = "null_geometry"
REASON_EMPTY_GEOMETRY = "empty_geometry"
REASON_WRONG_TYPE = "wrong_type"
REASON_UNREPAIRABLE = "unrepairable_after_make_valid"
REASON_SUBTYPE = "subtype_not_in_allowlist"
REASON_MIN_AREA = "area_below_minimum"
REASON_DEGENERATE_RING = "degenerate_ring_after_resampling"
REASON_DUPLICATE = "duplicate_high_overlap"


@dataclass(frozen=True)
class FilterConfig:
    """Cleaning parameters recorded verbatim in ``data_manifest.json``."""

    subtypes: tuple[str, ...] | None = DEFAULT_SUBTYPES
    min_area_m2: float = DEFAULT_MIN_AREA_M2
    max_iou: float = DEFAULT_MAX_IOU
    dedupe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtype_allowlist": list(self.subtypes) if self.subtypes is not None else None,
            "min_area_m2": self.min_area_m2,
            "min_area_ha": round(self.min_area_m2 / 1e4, 6),
            "max_iou": self.max_iou,
            "dedupe": self.dedupe,
            "frame_clip_filter": (
                "not applied - bbox contact is not clipping evidence (D15 revised)"
            ),
        }


@dataclass(frozen=True)
class Rejection:
    """One rejected feature, kept for the count table and the case figures."""

    reason: str
    source_id: str
    detail: str | None = None
    subtype: str | None = None
    feature_class: str | None = None
    area_m2: float | None = None
    geometry: Any = None


class RejectionLog:
    """Per-reason counts plus the rejected shapes themselves."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._items: list[Rejection] = []

    def add(
        self,
        reason: str,
        source_id: str,
        *,
        detail: str | None = None,
        subtype: str | None = None,
        feature_class: str | None = None,
        area_m2: float | None = None,
        geometry: Any = None,
    ) -> Rejection:
        item = Rejection(
            reason=reason,
            source_id=source_id,
            detail=detail,
            subtype=subtype,
            feature_class=feature_class,
            area_m2=area_m2,
            geometry=geometry,
        )
        self._counts[reason] += 1
        self._items.append(item)
        return item

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    @property
    def items(self) -> list[Rejection]:
        return list(self._items)

    def counts(self) -> dict[str, int]:
        return dict(sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def of_reason(self, reason: str) -> list[Rejection]:
        return [item for item in self._items if item.reason == reason]


def geometry_rejection_reason(geometry: Any) -> str | None:
    """Reason a raw geometry cannot be processed, or ``None`` if it can."""
    import shapely

    if geometry is None or (isinstance(geometry, float) and geometry != geometry):
        return REASON_NULL_GEOMETRY
    if shapely.is_empty(geometry):
        return REASON_EMPTY_GEOMETRY
    if geometry.geom_type not in ("Polygon", "MultiPolygon"):
        return REASON_WRONG_TYPE
    return None


def subtype_tokens(value: Any) -> tuple[str, ...]:
    """Normalize the ``subtype``/``class`` field to comparable tokens.

    Nulls arrive as ``None`` in memory and as ``float('nan')`` after a Parquet
    round-trip; Overture has used both scalar and list encodings (D14).
    """
    if value is None:
        return ()
    if isinstance(value, float) and value != value:
        return ()
    if isinstance(value, (list, tuple, set, frozenset, np.ndarray)):
        return tuple(str(v) for v in value)
    return (str(value),)


def subtype_allowed(value: Any, allowed: Sequence[str] | None) -> bool:
    """``allowed=None`` means "keep everything"."""
    if allowed is None:
        return True
    tokens = subtype_tokens(value)
    if not tokens:
        return False
    return any(token in set(allowed) for token in tokens)


def extends_beyond_frame(
    bounds: Sequence[float], frame: Sequence[float], eps: float = EPS_DEG
) -> bool:
    """True when a feature bbox reaches outside the download frame.

    Informational only: Overture returns whole features for a bbox query, so
    this does not imply the geometry was cut (see module docstring).
    """
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bounds)
    f_min_lon, f_min_lat, f_max_lon, f_max_lat = (float(v) for v in frame)
    return (
        min_lon < f_min_lon - eps
        or min_lat < f_min_lat - eps
        or max_lon > f_max_lon + eps
        or max_lat > f_max_lat + eps
    )


def duplicate_pairs(polygons: Sequence[Any], max_iou: float) -> list[tuple[int, int, float]]:
    """Index pairs of polygons whose IoU exceeds ``max_iou``."""
    import shapely

    if len(polygons) < 2:
        return []
    tree = shapely.STRtree(list(polygons))
    pairs: list[tuple[int, int, float]] = []
    for i, poly in enumerate(polygons):
        for j in tree.query(poly, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            other = polygons[j]
            intersection = poly.intersection(other).area
            if intersection <= 0.0:
                continue
            union = poly.area + other.area - intersection
            iou = intersection / union if union > 0.0 else 0.0
            if iou > max_iou:
                pairs.append((i, j, iou))
    return pairs


def duplicate_clusters(pairs: Iterable[tuple[int, int, float]], n: int) -> list[list[int]]:
    """Group indices linked by high-overlap pairs (union-find, sorted output)."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i, j, _iou in pairs:
        union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = [sorted(g) for g in groups.values() if len(g) > 1]
    clusters.sort(key=lambda g: g[0])
    return clusters


def resolve_duplicates(
    polygons: Sequence[Any], max_iou: float
) -> dict[int, tuple[int, float]]:
    """Pick which members of each near-duplicate cluster to drop.

    Keeps the largest-area polygon of each cluster (ties broken by index) and
    returns ``{dropped_index: (kept_index, iou_with_kept)}``.
    """
    pairs = duplicate_pairs(polygons, max_iou)
    dropped: dict[int, tuple[int, float]] = {}
    for cluster in duplicate_clusters(pairs, len(polygons)):
        areas = [polygons[i].area for i in cluster]
        keep = cluster[int(np.argmax(areas))]
        for i in cluster:
            if i == keep:
                continue
            intersection = polygons[keep].intersection(polygons[i]).area
            union = polygons[keep].area + polygons[i].area - intersection
            iou = intersection / union if union > 0.0 else 0.0
            dropped[i] = (keep, iou)
    return dropped
