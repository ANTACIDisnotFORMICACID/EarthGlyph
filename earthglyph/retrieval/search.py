"""Unified retrieval interface over the P0-3 index (P0-4, Task D).

Two stages, one call: a coarse shortlist from the feature index, then the
cyclic-start / rotation re-rank on the stored 128-point contours, ordered by the
actual boundary error. ``method`` selects which stages run, so the three baselines
share one candidate library, one query preparation and one scoring path — that is
the comparison, instead of three programs each tuned on its own.

Everything a hit reports is either measured here or read from the index:

* ``coarse_distance`` — per-feature RMS deviation of the chosen group: a
  shortlist cost, not a shape distance;
* ``align_distance`` — the boundary offset in normalized units (RMS radius = 1),
  so ``0.1`` means "about a tenth of the shape's radius off";
* ``align_theta_deg`` / ``align_shift`` / ``align_mirrored`` — the transform that
  achieved it, which is what makes an overlay honest: the candidate is drawn where
  the matcher actually put it, not where it would look nicer;
* ``candidate_id`` / ``source_id`` / ``lon`` / ``lat`` / ``area_m2`` — the real
  geography, read from ``metadata.parquet`` (the point lies inside the water
  polygon, D12).

There is deliberately no similarity percentage (D11). A small distance inside a
library of look-alikes means "closest available here", and :attr:`SearchResult.warnings`
says so when the best score is weak.

Ordering is deterministic: ties break by library row index, and the row index is
fixed by the index's own contract (``metadata.feature_row``), so the same query
against the same index always returns the same ranking.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from earthglyph.features.fourier import check_fold
from earthglyph.retrieval import coarse as coarse_mod
from earthglyph.retrieval import query as query_mod
from earthglyph.retrieval.alignment import align_library, apply_alignment, spectral
from earthglyph.retrieval.chamfer import chamfer_after_alignment
from earthglyph.retrieval.coarse import Shortlist
from earthglyph.retrieval.hu_baseline import BaselineError, HuRanking, rank_by_hu
from earthglyph.retrieval.query import QueryVector, check_matches_index

METHODS = (
    "hu",
    "fourier",
    "geometry",
    "all",
    "fourier_align",
    "geometry_align",
    "all_align",
    "align",
)
SORT_KEYS = ("align", "chamfer")
# methods that run the geometric re-rank; the others stop at the coarse stage
ALIGNED_METHODS = ("fourier_align", "geometry_align", "all_align", "align")
# ~12 % of the shape radius: beyond this the honest wording is "nearest available"
WEAK_ALIGN_DISTANCE = 0.12
AUDIT_MULTIPLIER = 3


class SearchError(ValueError):
    """Raised when a search cannot be run against the supplied index."""


@dataclass(frozen=True)
class IndexHandle:
    """The index plus the cached transforms a search needs, loaded once."""

    directory: Path
    features: np.ndarray
    shapes: np.ndarray
    metadata: pd.DataFrame
    columns: list[str]
    groups: dict[str, Any]
    n_harmonics: int
    fold: str
    n_points: int
    manifest: dict[str, Any] = field(default_factory=dict)
    _cache: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def n_candidates(self) -> int:
        return int(self.shapes.shape[0])

    @property
    def shapes_hat(self) -> np.ndarray:
        """One FFT of the whole library, reused by every query."""
        if "hat" not in self._cache:
            self._cache["hat"] = spectral(self.shapes)
        return self._cache["hat"]

    @property
    def shapes_energy(self) -> np.ndarray:
        if "energy" not in self._cache:
            self._cache["energy"] = np.mean(np.sum(self.shapes**2, axis=2), axis=1)
        return self._cache["energy"]

    @property
    def feature_dim(self) -> int:
        return int(self.features.shape[1])

    def provenance(self) -> dict[str, Any]:
        source = self.manifest.get("source", {})
        return {
            "index_dir": self.directory.as_posix(),
            "index_built_at_utc": self.manifest.get("built_at_utc"),
            "index_git_commit": self.manifest.get("git_commit"),
            "source_name": source.get("source_name"),
            "source_release": source.get("source_release"),
            "source_bbox": source.get("source_bbox"),
            "n_candidates": self.n_candidates,
            "n_points": self.n_points,
            "n_harmonics": self.n_harmonics,
            "fourier_fold": self.fold,
            "feature_dim": self.feature_dim,
            "output_sha256": {
                name: str(entry.get("sha256"))
                for name, entry in (self.manifest.get("output") or {}).items()
                if isinstance(entry, dict)
            },
        }


def load_index_handle(index_dir: Path | str, *, verify: bool = True) -> IndexHandle:
    """Read ``data/index/`` and re-check its consistency contract before use."""
    from earthglyph.data.index import IndexConfig, load_index

    directory = Path(index_dir)
    manifest_path = directory / "data_manifest.json"
    if not manifest_path.is_file():
        raise SearchError(f"index manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    features, shapes, metadata = load_index(directory)
    index_block = manifest["index"]
    config = manifest["config"]
    handle = IndexHandle(
        directory=directory,
        features=features.astype(np.float64),
        shapes=shapes.astype(np.float64),
        metadata=metadata,
        columns=list(index_block["feature_columns"]),
        groups=dict(index_block["feature_groups"]),
        n_harmonics=int(config["n_harmonics"]),
        fold=str(config["fourier_fold"]),
        n_points=int(index_block["n_points"]),
        manifest=manifest,
    )
    if verify:
        from earthglyph.data.index import check_index

        problems = check_index(
            features,
            shapes,
            metadata,
            handle.columns,
            handle.groups,
            IndexConfig(
                n_harmonics=handle.n_harmonics,
                fold=check_fold(handle.fold),
                dtype=str(config["storage_dtype"]),
            ),
        )
        if problems:
            raise SearchError("index failed its own consistency check: " + "; ".join(problems))
    return handle


@dataclass(frozen=True)
class Hit:
    """One ranked candidate with its scores, transform and geography."""

    rank: int
    row: int
    candidate_id: str
    source_id: str
    subtype: str
    feature_class: str
    lon: float
    lat: float
    area_m2: float
    coarse_distance: float | None = None
    align_distance: float | None = None
    align_shift: int | None = None
    align_theta_deg: float | None = None
    align_mirrored: bool | None = None
    chamfer: float | None = None
    contour: np.ndarray | None = None

    def to_dict(self, *, with_contour: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rank": self.rank,
            "index_row": self.row,
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "subtype": self.subtype,
            "class": self.feature_class,
            "lon": self.lon,
            "lat": self.lat,
            "area_m2": self.area_m2,
            "coarse_distance": self.coarse_distance,
            "align_distance": self.align_distance,
            "align_shift": self.align_shift,
            "align_theta_deg": self.align_theta_deg,
            "align_mirrored": self.align_mirrored,
            "chamfer_distance": self.chamfer,
        }
        if with_contour and self.contour is not None:
            data["aligned_contour"] = [[float(x), float(y)] for x, y in self.contour]
        return data


@dataclass(frozen=True)
class SearchResult:
    """A full search: query record, hits, audit trail, timings and provenance."""

    method: str
    query: dict[str, Any]
    hits: list[Hit]
    pre_dedup: list[Hit]
    shortlist: Shortlist | None
    hu: HuRanking | None
    params: dict[str, Any]
    timings: dict[str, float]
    provenance: dict[str, Any]
    warnings: list[str]

    def top(self, k: int = 1) -> list[Hit]:
        return self.hits[: int(k)]

    def to_records(self, *, with_contour: bool = False) -> list[dict[str, Any]]:
        return [hit.to_dict(with_contour=with_contour) for hit in self.hits]

    def to_dict(self, *, with_contours: bool = False) -> dict[str, Any]:
        return {
            "method": self.method,
            "params": self.params,
            "timings": self.timings,
            "provenance": self.provenance,
            "warnings": list(self.warnings),
            "query": self.query,
            "shortlist": None if self.shortlist is None else self.shortlist.to_dict(10),
            "hu": None if self.hu is None else self.hu.to_dict(10),
            "results": [hit.to_dict(with_contour=with_contours) for hit in self.hits],
            "pre_dedup": [hit.to_dict() for hit in self.pre_dedup],
        }


def check_method(method: str) -> str:
    if method not in METHODS:
        raise SearchError(f"method must be one of {METHODS}, got {method!r}")
    return method


def coarse_group(method: str) -> str | None:
    """Which feature group a method filters on (``None`` = no coarse stage)."""
    return {
        "fourier": "fourier",
        "fourier_align": "fourier",
        "geometry": "geometry",
        "geometry_align": "geometry",
        "all": "all",
        "all_align": "all",
    }.get(method)


def _as_query_vector(points: Any, index: IndexHandle, params: dict[str, Any]) -> QueryVector:
    """Accept a finished vector, an extracted contour, a raw trace, or a path."""
    if isinstance(points, QueryVector):
        check_matches_index(points, index.columns)
        return points
    from earthglyph.geometry.sketch import QueryContour

    if isinstance(points, QueryContour):
        if points.n_points != index.n_points:
            raise SearchError(
                f"query has {points.n_points} samples, index expects {index.n_points}"
            )
        return query_mod.vector_from_contour(
            points, n_harmonics=index.n_harmonics, fold=index.fold
        )
    vector = query_mod.prepare(
        points,
        closed=params["closed"],
        n_points=index.n_points,
        n_harmonics=index.n_harmonics,
        fold=index.fold,
        smoothing=params["smoothing"],
        allow_self_intersection=params["allow_self_intersection"],
        unit=params["unit"],
    )
    check_matches_index(vector, index.columns)
    return vector


def _ranked_order(
    scores: np.ndarray,
    chamfers: np.ndarray,
    *,
    sort_by: str,
    chamfer_top: int | None,
) -> np.ndarray:
    """Ranked row positions. ``sort_by="chamfer"`` re-orders inside the window.

    The cross-check only ever has values for the ``chamfer_top`` rows it was run
    on, so it re-sequences those and leaves the rest in primary-score order —
    which keeps the comparison ("did chamfer change what lands in the top 10?")
    answerable instead of silently mixing two scales over the whole shortlist.
    """
    order = np.argsort(scores, kind="stable")
    if sort_by == "align" or chamfer_top is None:
        return order
    limit = min(int(chamfer_top), order.shape[0])
    window = order[:limit]
    resequenced = window[np.argsort(chamfers[window], kind="stable")]
    return np.concatenate([resequenced, order[limit:]])


def _warn(
    hits: list[Hit],
    *,
    method: str,
    allow_mirror: bool,
    shortlist: Shortlist | None,
    index: IndexHandle,
) -> list[str]:
    notes: list[str] = []
    if not hits:
        return ["no candidates survived the search"]
    best = hits[0]
    if best.align_distance is not None and best.align_distance > WEAK_ALIGN_DISTANCE:
        notes.append(
            f"best boundary offset is {best.align_distance:.3f} of the shape radius: "
            "these are the closest candidates in this library, not verified matches"
        )
    if not allow_mirror:
        notes.append("mirrors disallowed (allow_mirror=False); amplitudes cannot see one (D26/D33)")
    if shortlist is not None and shortlist.size >= index.n_candidates:
        notes.append("shortlist covers the whole library: the coarse filter did not narrow")
    if len({hit.source_id for hit in hits}) < len(hits):
        notes.append("returned results still share a source_id (max_per_source > 1)")
    return notes


def search_by_contour(
    points: Any,
    index: IndexHandle,
    *,
    top_k: int = 10,
    shortlist_size: int = 200,
    method: str = "fourier_align",
    allow_mirror: bool = False,
    smoothing: str | float = "light",
    closed: bool | None = None,
    allow_self_intersection: bool = False,
    unit: str = "canvas",
    coarse_scale: str = "none",
    max_per_source: int = 1,
    sort_by: str = "align",
    chamfer_top: int | None = None,
    densify_factor: int = 4,
) -> SearchResult:
    """Retrieve real candidates for one hand-drawn contour.

    ``points`` may be a raw trace, a ``QueryContour``/``QueryVector``, a sketch JSON
    path or an image path; the extraction contract is
    :mod:`earthglyph.retrieval.query`.

    ``method="align"`` skips the shortlist and re-ranks the *whole* library: that
    is the diagnostic which separates "the matcher liked it but the coarse filter
    never shortlisted it" from "nothing in this library matches" (Task H).
    ``sort_by="chamfer"`` re-sequences the cross-checked window by Chamfer to
    measure whether the cross-check changes the answer at all (Task C4).
    """
    check_method(method)
    if sort_by not in SORT_KEYS:
        raise SearchError(f"sort_by must be one of {SORT_KEYS}, got {sort_by!r}")
    if top_k < 1:
        raise SearchError(f"top_k must be >= 1, got {top_k}")
    if max_per_source < 1:
        raise SearchError(f"max_per_source must be >= 1, got {max_per_source}")
    coarse_mod.check_scale(coarse_scale)
    use_align = method in ALIGNED_METHODS
    if chamfer_top is not None and not use_align:
        raise SearchError("chamfer_top needs a method that re-ranks (…_align or 'align')")
    started = time.perf_counter()
    prepare_params = {
        "smoothing": smoothing,
        "closed": closed,
        "allow_self_intersection": allow_self_intersection,
        "unit": unit,
    }
    vector = _as_query_vector(points, index, prepare_params)
    timings = {"prepare": _lap(started)}

    shortlist: Shortlist | None = None
    hu_ranking: HuRanking | None = None
    group = coarse_group(method)
    coarse_distances: np.ndarray | None = None
    shortlist_distances = np.zeros(0, dtype=float)

    started = time.perf_counter()
    if method == "hu":
        try:
            hu_ranking = rank_by_hu(vector.points, index.shapes)
        except BaselineError as error:
            raise SearchError(str(error)) from error
        rows = hu_ranking.order
        coarse_distances = hu_ranking.distances[rows]
    elif group is None:  # "align": full-library diagnostic, no shortlist
        rows = np.arange(index.n_candidates)
    else:
        columns = coarse_mod.group_columns(index.groups, group, index.feature_dim)
        reference = coarse_mod.column_reference(index.features, columns, coarse_scale)
        shortlist = coarse_mod.rank(
            vector.features[columns],
            index.features[:, columns],
            group=group,
            scale=coarse_scale,
            reference=reference,
            shortlist_size=min(int(shortlist_size), index.n_candidates),
        )
        rows = shortlist.indices
        shortlist_distances = shortlist.distances
        coarse_distances = shortlist_distances
    coarse_seconds = _lap(started)

    n_rows = rows.shape[0]
    align_distances = np.zeros(n_rows, dtype=float)
    shifts = np.zeros(n_rows, dtype=int)
    thetas = np.zeros(n_rows, dtype=float)
    mirrored = np.zeros(n_rows, dtype=bool)
    rerank_seconds = 0.0
    if use_align:
        started = time.perf_counter()
        align_distances, shifts, thetas, mirrored = align_library(
            vector.points,
            index.shapes[rows],
            allow_mirror=allow_mirror,
            query_hat=spectral(vector.points),
            library_hat=index.shapes_hat[rows],
            library_energy=index.shapes_energy[rows],
        )
        rerank_seconds = _lap(started)
        primary = align_distances
    elif method == "hu":
        primary = np.asarray(coarse_distances, dtype=float)
    else:
        primary = shortlist_distances

    chamfers = np.full(n_rows, np.inf)
    chamfer_seconds = 0.0
    if chamfer_top is not None:
        started = time.perf_counter()
        window = np.argsort(align_distances, kind="stable")[: min(int(chamfer_top), n_rows)]
        for position in window:
            chamfers[position] = chamfer_after_alignment(
                vector.points,
                index.shapes[int(rows[position])],
                shift=int(shifts[position]),
                theta_rad=float(thetas[position]),
                mirrored=bool(mirrored[position]),
                densify_factor=densify_factor,
            )
        chamfer_seconds = _lap(started)

    order = _ranked_order(primary, chamfers, sort_by=sort_by, chamfer_top=chamfer_top)
    source_ids = index.metadata["source_id"].to_numpy(object)[rows]
    candidate_ids = index.metadata["candidate_id"].to_numpy(object)[rows]

    def hit_at(position: int, rank: int, *, with_contour: bool) -> Hit:
        row = int(rows[position])
        contour = None
        if with_contour and use_align:
            contour = apply_alignment(
                index.shapes[row],
                shift=int(shifts[position]),
                theta_rad=float(thetas[position]),
                mirrored=bool(mirrored[position]),
            )
        return Hit(
            rank=rank,
            row=row,
            candidate_id=str(candidate_ids[position]),
            source_id=str(source_ids[position]),
            coarse_distance=None
            if coarse_distances is None
            else float(coarse_distances[position]),
            align_distance=float(align_distances[position]) if use_align else None,
            align_shift=int(shifts[position]) if use_align else None,
            align_theta_deg=float(np.rad2deg(thetas[position])) if use_align else None,
            align_mirrored=bool(mirrored[position]) if use_align else None,
            chamfer=None if not np.isfinite(chamfers[position]) else float(chamfers[position]),
            contour=contour,
            **_metadata_row(index.metadata, row),
        )

    kept: list[Hit] = []
    counts: dict[str, int] = {}
    for position in order:
        source = str(source_ids[position])
        counts[source] = counts.get(source, 0) + 1
        if counts[source] <= max_per_source:
            kept.append(hit_at(int(position), len(kept) + 1, with_contour=True))
        if len(kept) >= top_k:
            break
    audit_limit = min(order.shape[0], max(AUDIT_MULTIPLIER * top_k, top_k))
    pre_dedup = [
        hit_at(int(position), slot + 1, with_contour=False)
        for slot, position in enumerate(order[:audit_limit])
    ]

    timings.update(
        {
            "coarse": coarse_seconds,
            "rerank": rerank_seconds,
            "chamfer": chamfer_seconds,
            "total": round(
                timings["prepare"] + coarse_seconds + rerank_seconds + chamfer_seconds, 6
            ),
        }
    )
    return SearchResult(
        method=method,
        query=vector.to_dict(),
        hits=kept,
        pre_dedup=pre_dedup,
        shortlist=shortlist,
        hu=hu_ranking,
        params={
            "top_k": int(top_k),
            "shortlist_size": int(shortlist_size),
            "method": method,
            "allow_mirror": bool(allow_mirror),
            "smoothing": str(smoothing),
            "closed": closed,
            "allow_self_intersection": bool(allow_self_intersection),
            "coarse_group": group,
            "coarse_scale": coarse_scale,
            "max_per_source": int(max_per_source),
            "sort_by": sort_by,
            "chamfer_top": chamfer_top,
            "densify_factor": int(densify_factor),
            "n_points": index.n_points,
            "n_harmonics": index.n_harmonics,
            "fourier_fold": index.fold,
        },
        timings=timings,
        provenance=index.provenance(),
        warnings=_warn(
            kept, method=method, allow_mirror=allow_mirror, shortlist=shortlist, index=index
        ),
    )


def search_by_image(
    image: Any,
    index: IndexHandle,
    **kwargs: Any,
) -> SearchResult:
    """Same search, starting from a black-on-white sketch image or its path."""
    from earthglyph.geometry.sketch import contour_from_sketch

    contour = contour_from_sketch(
        image,
        n_points=index.n_points,
        smoothing=kwargs.pop("smoothing", "light"),
        allow_self_intersection=kwargs.pop("allow_self_intersection", False),
        closed=kwargs.pop("closed", None),
    )
    vector = query_mod.vector_from_contour(
        contour, n_harmonics=index.n_harmonics, fold=index.fold
    )
    return search_by_contour(vector, index, **kwargs)


def _metadata_row(metadata: pd.DataFrame, row: int) -> dict[str, Any]:
    record = metadata.iloc[row].to_dict()
    return {
        "subtype": str(record["subtype"]),
        "feature_class": str(record["class"]),
        "lon": float(record["lon"]),
        "lat": float(record["lat"]),
        "area_m2": float(record["area_m2"]),
    }


def _lap(started: float) -> float:
    return round(time.perf_counter() - started, 6)
