"""Unified, geography-free shape retrieval over the EarthGlyph arms plus official outputs.

Every method scores the *same* candidate set. E0/E1/E3 share one preprocessing contract
(128-point centred/unit-RMS/CCW rings). S0 is different in kind: it is the authors'
*published* 22-D ShapeComp coordinates, so it needs no preprocessing and can only score
shapes whose coordinates were published. That asymmetry is reported, never hidden.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from earthglyph.retrieval import perceptual_features as e1
from earthglyph.retrieval import perceptual_methods as pm
from earthglyph.retrieval import shape_context_baseline as e3
from earthglyph.shape_benchmark.datasets import BenchmarkShape

EARTHGLYPH_METHODS = ("E0", "E1", "E3")
ALL_METHODS = ("E0", "E1", "E3", "S0")


@dataclass(frozen=True)
class Hit:
    rank: int
    shape_id: str
    source: str
    category: str | None
    distance: float


@dataclass
class SearchResult:
    query_id: str
    method: str
    gallery_size: int
    distances: np.ndarray
    """Distance to every gallery shape, in gallery order (excluded entries are +inf)."""
    order: np.ndarray
    """Full ranking of gallery indices, best first. Not just the Top-K."""
    hits: list[Hit]
    elapsed_seconds: float
    details: dict[str, Any] = field(default_factory=dict)

    def rank_of(self, shape_id: str, gallery: Gallery) -> int | None:
        index = gallery.index_of(shape_id)
        if index is None or not np.isfinite(self.distances[index]):
            return None
        return int(np.flatnonzero(self.order == index)[0]) + 1


class Gallery:
    """A candidate set with the per-arm corpora precomputed once and shared by all queries."""

    def __init__(
        self,
        shapes: Sequence[BenchmarkShape],
        *,
        official_coords: dict[str, np.ndarray] | None = None,
        e3_params: dict[str, Any] | None = None,
        e1_normalizer: e1.FeatureNormalizer | None = None,
        name: str = "gallery",
    ) -> None:
        if not shapes:
            raise ValueError("gallery is empty")
        self.name = name
        self.shapes = list(shapes)
        self.matrix = np.stack([shape.points for shape in self.shapes])
        self._index = {shape.shape_id: i for i, shape in enumerate(self.shapes)}
        if len(self._index) != len(self.shapes):
            raise ValueError("gallery contains duplicate shape_id values")
        self.official_coords = official_coords
        self.e3_params: dict[str, Any] = dict(e3_params or {})

        features = e1.features_for_library(list(self.matrix))
        # Inheriting keeps "added candidates" the only variable when a condition extends the
        # gallery; refitting would silently rescale every E1 distance at the same time.
        self.e1_normalizer = e1_normalizer or e1.fit_normalizer(
            features,
            method="minmax",
            names=e1.feature_names(),
            fitted_on=f"{name} ({len(self.shapes)} shapes)",
        )
        self.e1_normalizer_inherited = e1_normalizer is not None
        self.e1_features = features
        self.e3_descriptors = e3.build_library_descriptors(list(self.matrix), **self.e3_params)

    def __len__(self) -> int:
        return len(self.shapes)

    def index_of(self, shape_id: str) -> int | None:
        return self._index.get(shape_id)

    def has_official_coords(self) -> bool:
        return self.official_coords is not None and all(
            shape.shape_id in self.official_coords for shape in self.shapes
        )

    def params_for(self, method: str) -> dict[str, Any]:
        if method == "E1":
            return {"normalizer": self.e1_normalizer, "orientation": "canonical"}
        if method == "E3":
            return dict(self.e3_params)
        return {}


def _query_points(query: BenchmarkShape | np.ndarray) -> tuple[np.ndarray, str]:
    if isinstance(query, BenchmarkShape):
        return query.points, query.shape_id
    return np.asarray(query, dtype=float), "query"


def search_shape(
    query: BenchmarkShape | np.ndarray,
    gallery: Gallery,
    method: str,
    top_k: int = 10,
    *,
    allow_mirror: bool = False,
    exclude: Iterable[str] = (),
) -> SearchResult:
    """Rank the whole gallery against ``query`` and return the Top-K plus the full order.

    ``exclude`` removes shape_ids from the candidate set (Part D2's T1/T2/T3). Excluded
    entries stay in ``distances`` as +inf so the array remains gallery-aligned, but they
    are dropped from ``order`` and ``hits``.
    """
    if method not in ALL_METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {ALL_METHODS}")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    points, query_id = _query_points(query)
    excluded = set(exclude)
    mask = np.array([shape.shape_id not in excluded for shape in gallery.shapes])
    if not mask.any():
        raise ValueError("every gallery shape was excluded")

    started = time.perf_counter()
    details: dict[str, Any]
    if method == "S0":
        if not gallery.has_official_coords():
            raise ValueError(
                "S0 needs published official 22-D coordinates for every gallery shape; "
                "they exist only for the H1/H2 stimuli, not for MPEG-7"
            )
        coords = gallery.official_coords
        assert coords is not None
        query_coords = coords.get(query_id)
        if query_coords is None:
            raise ValueError(f"no published official coordinates for query {query_id!r}")
        gallery_coords = np.array([coords[shape.shape_id] for shape in gallery.shapes])
        distances = np.linalg.norm(gallery_coords - query_coords, axis=1)
        details = {
            "kind": "official_precomputed_coordinates",
            "note": "Euclidean distance in the authors' published 22-D ShapeComp space; "
            "not a local execution of ShapeComp",
        }
    elif method == "E3":
        indices = np.flatnonzero(mask)
        distances = np.full(len(gallery), np.inf)
        scores, details = e3.score_library_from_descriptors(
            points, gallery.e3_descriptors, indices, allow_mirror=allow_mirror, **gallery.e3_params
        )
        distances[indices] = scores
    else:
        indices = np.flatnonzero(mask)
        distances = np.full(len(gallery), np.inf)
        scores, details = pm.score_library(
            method,
            points,
            [gallery.matrix[i] for i in indices],
            allow_mirror=allow_mirror,
            params=gallery.params_for(method),
        )
        distances[indices] = scores
    elapsed = time.perf_counter() - started

    finite = np.flatnonzero(np.isfinite(distances))
    order = finite[np.asarray(pm.rank_order(distances[finite]), dtype=int)]
    hits = [
        Hit(
            rank=rank,
            shape_id=gallery.shapes[int(index)].shape_id,
            source=gallery.shapes[int(index)].source,
            category=gallery.shapes[int(index)].category,
            distance=float(distances[int(index)]),
        )
        for rank, index in enumerate(order[:top_k], start=1)
    ]
    return SearchResult(
        query_id=query_id,
        method=method,
        gallery_size=len(gallery),
        distances=distances,
        order=order,
        hits=hits,
        elapsed_seconds=elapsed,
        details=details,
    )


def pair_distance(
    left: BenchmarkShape | np.ndarray,
    right: BenchmarkShape | np.ndarray,
    method: str,
    *,
    normalizer: e1.FeatureNormalizer | None = None,
    allow_mirror: bool = False,
) -> float:
    """Distance between exactly two shapes — the unit of the human-rating experiment.

    Computing only the pairs that were actually rated avoids ranking 500 shapes 250 times.
    """
    left_points, _ = _query_points(left)
    right_points, _ = _query_points(right)
    if method == "S0":
        raise ValueError("use the published coordinates directly for S0 pair distances")
    params: dict[str, Any] = {}
    if method == "E1":
        if normalizer is None:
            raise ValueError("E1 pair distances need a corpus normalizer for comparability")
        params = {"normalizer": normalizer, "orientation": "canonical"}
    scores, _ = pm.score_library(
        method, left_points, [right_points], allow_mirror=allow_mirror, params=params
    )
    return float(scores[0])