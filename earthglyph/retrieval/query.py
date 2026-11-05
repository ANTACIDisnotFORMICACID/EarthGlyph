"""Query-side preparation: a trace or image into the vectors both stages read (Task D).

One function owns the contract that the offline evaluation and the browser must
agree on: a query is standardized with the *same* ``n_points``, described with the
*same* harmonic count, fold and ``per_sample`` rule, and its geometry statistics
come from the *same* post-resampling contour as the index's. If any of those drift
apart, the coarse distances stop being distances — the P0-3 ablation measured a
24.7 % ``compactness`` shift between a 64- and a 128-point sampling of the same
curve, which is larger than most real shape differences in the library.

Feature columns are returned in the index's own order (``fourier`` block first,
then ``geometry``) and :func:`check_matches_index` compares them against a
manifest, so a mismatch fails the search instead of silently ranking against the
wrong columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from earthglyph.features.fourier import (
    DEFAULT_FOLD,
    DEFAULT_N_HARMONICS,
    check_fold,
    check_harmonics,
    descriptor_columns,
    harmonic_amplitudes,
)
from earthglyph.features.geometry_stats import FEATURE_COLUMNS as GEOMETRY_COLUMNS
from earthglyph.features.geometry_stats import batch_shape_metrics
from earthglyph.geometry import DEFAULT_N_POINTS
from earthglyph.geometry.sketch import QueryContour, contour_from_sketch


class QueryError(ValueError):
    """Raised when a query cannot be turned into comparable features."""


@dataclass(frozen=True)
class QueryVector:
    """Everything the two retrieval stages need about one query."""

    contour: QueryContour
    fourier: np.ndarray
    geometry: np.ndarray
    features: np.ndarray
    columns: list[str] = field(default_factory=list)

    @property
    def points(self) -> np.ndarray:
        return self.contour.points

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_columns": list(self.columns),
            "query_features": [float(value) for value in self.features],
            "query_fourier": [float(value) for value in self.fourier],
            "query_geometry": [float(value) for value in self.geometry],
            "query_contour": self.contour.to_dict(),
        }


def feature_columns(n_harmonics: int, fold: str) -> list[str]:
    """Column names in index order: the Fourier block, then the geometry block."""
    return [*descriptor_columns(n_harmonics, fold), *GEOMETRY_COLUMNS]


def vectors_for(
    contour: np.ndarray, *, n_harmonics: int, fold: str
) -> tuple[np.ndarray, np.ndarray]:
    """``(fourier_amplitudes, geometry_statistics)`` of one standardized contour."""
    array = np.asarray(contour, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2 or array.shape[0] < 3:
        raise QueryError(f"contour must be (N, 2) with N >= 3, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise QueryError("contour contains non-finite coordinates")
    check_harmonics(array.shape[0], n_harmonics)
    fourier = harmonic_amplitudes(array, n_harmonics, fold=check_fold(fold))
    geometry = np.asarray(batch_shape_metrics(array[None, ...])[0], dtype=float)
    return np.asarray(fourier, dtype=float), geometry


def vector_from_contour(
    contour: QueryContour, *, n_harmonics: int, fold: str
) -> QueryVector:
    """Describe a contour that extraction already produced, without re-sampling it.

    Re-running the trace chain on a standardized ``N``-point contour would move the
    arc-length grid by a fraction of a chord — a real, measured effect
    (docs/RETRIEVAL_DESIGN.md §9.3) that must not be applied twice.
    """
    fourier, geometry = vectors_for(contour.points, n_harmonics=n_harmonics, fold=fold)
    return QueryVector(
        contour=contour,
        fourier=fourier,
        geometry=geometry,
        features=np.concatenate([fourier, geometry]),
        columns=feature_columns(n_harmonics, fold),
    )


def prepare(
    target: Any,
    *,
    closed: bool | None = None,
    n_points: int = DEFAULT_N_POINTS,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    fold: str = DEFAULT_FOLD,
    smoothing: str | float = "light",
    allow_self_intersection: bool = False,
    unit: str = "canvas",
) -> QueryVector:
    """Extract a contour from a trace, JSON file or PNG and describe it.

    ``target`` is a point array, a ``{"points": ..., "closed": ...}`` mapping or a
    path (``.json`` / image). The extraction rules — and the refusals, which are
    the point of the module — live in :mod:`earthglyph.geometry.sketch`.
    """
    contour = contour_from_sketch(
        target,
        closed=closed,
        n_points=n_points,
        smoothing=smoothing,
        allow_self_intersection=allow_self_intersection,
        unit=unit,
    )
    return vector_from_contour(contour, n_harmonics=n_harmonics, fold=fold)


def check_matches_index(query: QueryVector, index_columns: list[str]) -> None:
    """Fail loudly if the query was described under a different contract."""
    if query.columns != list(index_columns):
        raise QueryError(
            "query descriptor does not match the index: "
            f"{query.columns[:3]}... vs {list(index_columns)[:3]}... "
            "(align n_points / n_harmonics / fold with the index manifest)"
        )
