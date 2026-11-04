"""Geometry processing: extraction, projection, resampling, filtering.

Implements the P0-2 preprocessing half of docs/RETRIEVAL_DESIGN.md §1–2:
real polygons → repaired, metric, uniformly resampled and normalized contours.

Rotation alignment, cyclic-start matching and mirroring are **not** done here —
they belong to the retrieval stage (D8/D9), so preprocessing keeps each
candidate's true orientation.
"""

from __future__ import annotations

from earthglyph.geometry.extraction import (
    POLYGONAL_TYPES,
    FeaturePart,
    RepairInfo,
    exterior_ring,
    hole_count,
    is_usable_polygon,
    polygonal_parts,
    repair_geometry,
    split_feature,
)
from earthglyph.geometry.filtering import (
    DEFAULT_MAX_IOU,
    DEFAULT_MIN_AREA_M2,
    DEFAULT_SUBTYPES,
    EPS_DEG,
    REASON_DEGENERATE_RING,
    REASON_DUPLICATE,
    REASON_EMPTY_GEOMETRY,
    REASON_MIN_AREA,
    REASON_NULL_GEOMETRY,
    REASON_SUBTYPE,
    REASON_UNREPAIRABLE,
    REASON_WRONG_TYPE,
    FilterConfig,
    Rejection,
    RejectionLog,
    duplicate_clusters,
    duplicate_pairs,
    extends_beyond_frame,
    geometry_rejection_reason,
    resolve_duplicates,
    subtype_allowed,
    subtype_tokens,
)
from earthglyph.geometry.normalization import normalize_contour
from earthglyph.geometry.projection import PROJ_TEMPLATE, AeqdProjection
from earthglyph.geometry.resampling import (
    DEFAULT_N_POINTS,
    ContourError,
    ResampledContour,
    arc_length_resample,
    build_contour,
    ensure_ccw,
    open_ring,
    ring_perimeter,
    signed_area,
    winding_of,
)

__all__ = [
    "AeqdProjection",
    "ContourError",
    "DEFAULT_MAX_IOU",
    "DEFAULT_MIN_AREA_M2",
    "DEFAULT_N_POINTS",
    "DEFAULT_SUBTYPES",
    "EPS_DEG",
    "FeaturePart",
    "FilterConfig",
    "POLYGONAL_TYPES",
    "PROJ_TEMPLATE",
    "REASON_DEGENERATE_RING",
    "REASON_DUPLICATE",
    "REASON_EMPTY_GEOMETRY",
    "REASON_MIN_AREA",
    "REASON_NULL_GEOMETRY",
    "REASON_SUBTYPE",
    "REASON_UNREPAIRABLE",
    "REASON_WRONG_TYPE",
    "Rejection",
    "RejectionLog",
    "RepairInfo",
    "ResampledContour",
    "arc_length_resample",
    "build_contour",
    "duplicate_clusters",
    "duplicate_pairs",
    "ensure_ccw",
    "extends_beyond_frame",
    "exterior_ring",
    "geometry_rejection_reason",
    "hole_count",
    "is_usable_polygon",
    "normalize_contour",
    "open_ring",
    "polygonal_parts",
    "repair_geometry",
    "resolve_duplicates",
    "ring_perimeter",
    "signed_area",
    "split_feature",
    "subtype_allowed",
    "subtype_tokens",
    "winding_of",
]
