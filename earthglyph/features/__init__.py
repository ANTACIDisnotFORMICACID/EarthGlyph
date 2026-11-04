"""Shape features: coarse descriptors computed from a standardized contour.

Two independent groups, kept separate so a later ablation can switch one off
without touching the other (docs/RETRIEVAL_DESIGN.md §4):

* :mod:`earthglyph.features.fourier` — low-harmonic Fourier amplitude descriptors
* :mod:`earthglyph.features.geometry_stats` — dimensionless geometry statistics

Hu moments are **not** implemented here: they belong to the P0-4 baseline
comparison (``earthglyph.retrieval``) and would be unused code in this stage.

Nothing in this package performs retrieval or fusion weighting — feature
descriptors are coarse-filter inputs only (docs/DECISIONS.md D10).
"""

from __future__ import annotations

from earthglyph.features.fourier import (
    DEFAULT_FOLD,
    DEFAULT_N_HARMONICS,
    FOURIER_SCHEMA_VERSION,
    FeatureError,
    Fold,
    as_complex_sequence,
    batch_fourier_descriptors,
    batch_harmonic_amplitudes,
    check_fold,
    check_harmonics,
    descriptor_columns,
    feature_definition,
    fourier_coefficients,
    fourier_descriptor,
    harmonic_amplitudes,
    harmonic_energy_fractions,
    harmonic_limit,
)
from earthglyph.features.geometry_stats import (
    FEATURE_COLUMNS,
    GEOMETRY_SCHEMA_VERSION,
    ShapeMetrics,
    batch_shape_metrics,
    geometry_feature_definition,
    max_point_distance,
    polygon_area,
    rectangle_aspect_ratio,
    shape_metrics,
)

__all__ = [
    "DEFAULT_FOLD",
    "DEFAULT_N_HARMONICS",
    "FEATURE_COLUMNS",
    "FOURIER_SCHEMA_VERSION",
    "GEOMETRY_SCHEMA_VERSION",
    "FeatureError",
    "Fold",
    "ShapeMetrics",
    "as_complex_sequence",
    "batch_fourier_descriptors",
    "batch_harmonic_amplitudes",
    "batch_shape_metrics",
    "check_fold",
    "check_harmonics",
    "descriptor_columns",
    "feature_definition",
    "fourier_coefficients",
    "fourier_descriptor",
    "geometry_feature_definition",
    "harmonic_amplitudes",
    "harmonic_energy_fractions",
    "harmonic_limit",
    "max_point_distance",
    "polygon_area",
    "rectangle_aspect_ratio",
    "shape_metrics",
]
