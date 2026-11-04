"""Feature index build and verification (P0-3, Task D).

Layout of ``data/index/``::

    features.npy        float32 (n_candidates, feature_dim)   coarse-filter vector
    shapes.npy          float32 (n_candidates, n_points, 2)   standardized contour
    metadata.parquet    one row per candidate, same order     geography + provenance
    data_manifest.json  schema, source, parameters, checksums, reproduce command

Row order is the contract: ``features[i]``, ``shapes[i]`` and ``metadata`` row
``i`` all describe ``candidate_id == metadata.candidate_id[i]``, and that id
resolves back to a real location (``lon`` / ``lat`` is a point inside the source
polygon, D12/D21).

The feature vector concatenates two independent groups, recorded with their
offsets so either can be dropped in an ablation without re-decoding:

    [ fourier | geometry ]

Storage dtype is float32 — the format a browser would download — while the
features are computed in float64; :func:`check_index` plus the round-trip report
in the manifest make the precision loss measured rather than assumed.

A candidate that cannot be processed **fails the build** with its id in the
message. There is no partial-index path: silently shrinking the index is exactly
how the extreme dendritic and slender outlines — the shapes the sampling study
cares about — would disappear.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from earthglyph.data.download import sha256_of
from earthglyph.data.prepare import load_contours, stack_contours
from earthglyph.features.fourier import (
    DEFAULT_FOLD,
    DEFAULT_N_HARMONICS,
    FeatureError,
    Fold,
    batch_fourier_descriptors,
    descriptor_columns,
    feature_definition,
)
from earthglyph.features.geometry_stats import (
    FEATURE_COLUMNS as GEOMETRY_COLUMNS,
)
from earthglyph.features.geometry_stats import (
    batch_shape_metrics,
    geometry_feature_definition,
)

INDEX_SCHEMA_VERSION = "1.0"
FEATURES_FILENAME = "features.npy"
SHAPES_FILENAME = "shapes.npy"
METADATA_FILENAME = "metadata.parquet"
SOURCE_MANIFEST_FILENAME = "data_manifest.json"
INDEX_MANIFEST_FILENAME = "data_manifest.json"  # same name, different directory

GROUP_FOURIER = "fourier"
GROUP_GEOMETRY = "geometry"
FEATURE_DTYPES = ("float32", "float64")

METADATA_COLUMNS: tuple[str, ...] = (
    "candidate_id",
    "source_id",
    "part_index",
    "n_parts",
    "subtype",
    "class",
    "lon",
    "lat",
    "area_m2",
    "area_ha",
    "perimeter_m",
    "n_holes",
    "n_points",
    "rms_radius_m",
    "original_winding",
    "was_repaired",
    "extends_beyond_frame",
    "is_intermittent",
    "is_salt",
    "bbox_lonlat",
)


@dataclass(frozen=True)
class IndexConfig:
    """Everything that changes the bytes of the index; recorded verbatim."""

    n_harmonics: int = DEFAULT_N_HARMONICS
    fold: Fold = DEFAULT_FOLD
    dtype: str = "float32"

    def __post_init__(self) -> None:
        if self.n_harmonics < 1:
            raise ValueError(f"n_harmonics must be >= 1, got {self.n_harmonics}")
        if self.fold not in ("positive", "symmetric"):
            raise ValueError(f"unknown fold {self.fold!r}")
        if self.dtype not in FEATURE_DTYPES:
            raise ValueError(f"dtype must be one of {FEATURE_DTYPES}, got {self.dtype!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_harmonics": self.n_harmonics,
            "fourier_fold": self.fold,
            "storage_dtype": self.dtype,
        }


@dataclass
class BuiltIndex:
    features: np.ndarray
    shapes: np.ndarray
    metadata: pd.DataFrame
    manifest: dict[str, Any]
    columns: list[str]
    groups: dict[str, Any]
    feature_seconds: float

    @property
    def n_candidates(self) -> int:
        return int(self.metadata.shape[0])


def feature_groups(config: IndexConfig, n_points: int) -> tuple[list[str], dict[str, Any]]:
    """Column names and group offsets of the coarse feature vector."""
    limit = n_points // 2 - 1
    if config.n_harmonics > limit:
        raise ValueError(
            f"n_harmonics={config.n_harmonics} exceeds the Nyquist limit ({limit}) "
            f"for {n_points}-point contours"
        )
    fourier = descriptor_columns(config.n_harmonics, config.fold)
    geometry = list(GEOMETRY_COLUMNS)
    groups = {
        GROUP_FOURIER: {"offset": 0, "width": len(fourier), "columns": fourier},
        GROUP_GEOMETRY: {
            "offset": len(fourier),
            "width": len(geometry),
            "columns": geometry,
        },
    }
    return fourier + geometry, groups


def _failed_rows(
    shapes: np.ndarray,
    candidate_ids: pd.Series,
    config: IndexConfig,
    error: FeatureError,
) -> ValueError:
    """Re-extract row by row to name the candidates the batch path choked on."""
    offending: list[tuple[str, str]] = []
    for index, candidate_id in enumerate(candidate_ids):
        row = shapes[index : index + 1]
        try:
            batch_fourier_descriptors(row, config.n_harmonics, fold=config.fold)
            batch_shape_metrics(row)
        except FeatureError as row_error:
            offending.append((str(candidate_id), str(row_error)))
    listed = ", ".join(candidate for candidate, _ in offending[:5]) or str(error)
    return ValueError(
        f"feature extraction failed for {len(offending) or 1} candidate(s): {listed}"
    )


def compute_features(
    contours: pd.DataFrame,
    shapes: np.ndarray,
    config: IndexConfig,
) -> tuple[np.ndarray, list[str], dict[str, Any], float]:
    """Build the (n, dim) coarse feature matrix in the contour row order."""
    if shapes.ndim != 3 or shapes.shape[2] != 2:
        raise ValueError(f"shapes must be (n, N, 2), got {shapes.shape}")
    columns, groups = feature_groups(config, int(shapes.shape[1]))
    started = time.perf_counter()
    try:
        fourier = batch_fourier_descriptors(shapes, config.n_harmonics, fold=config.fold)
        geometry = batch_shape_metrics(shapes)
    except FeatureError as error:
        raise _failed_rows(shapes, contours["candidate_id"], config, error) from error
    elapsed = time.perf_counter() - started

    features = np.concatenate([fourier, geometry], axis=1)
    if not np.all(np.isfinite(features)):
        bad = np.flatnonzero(~np.isfinite(features).all(axis=1))
        ids = [str(contours["candidate_id"].iloc[index]) for index in bad[:5]]
        raise ValueError(f"non-finite feature values in candidates: {', '.join(ids)}")
    return features, columns, groups, elapsed


def _environment() -> dict[str, str]:
    import shapely

    versions = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "shapely": shapely.__version__,
    }
    for name in ("pyarrow", "scipy"):
        try:
            module = __import__(name)
        except ImportError:
            continue
        versions[name] = str(getattr(module, "__version__", "unknown"))
    return versions


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def build_index(
    contours_path: Path | str,
    out_dir: Path | str,
    *,
    config: IndexConfig | None = None,
    force: bool = False,
    build_command: str | None = None,
    write_files: bool = True,
) -> BuiltIndex:
    """Turn ``data/processed/contours.parquet`` into a static feature index."""
    import pyarrow.parquet as pq

    config = config or IndexConfig()
    contours_path = Path(contours_path)
    out_dir = Path(out_dir)
    if not contours_path.is_file():
        raise FileNotFoundError(f"contours dataset not found: {contours_path}")

    if out_dir.resolve() == contours_path.parent.resolve():
        raise ValueError("out-dir must differ from the contour dataset directory")
    source_manifest_path = contours_path.parent / SOURCE_MANIFEST_FILENAME
    if not source_manifest_path.is_file():
        raise FileNotFoundError(f"source manifest not found: {source_manifest_path}")
    source_manifest: dict[str, Any] = json.loads(
        source_manifest_path.read_text(encoding="utf-8")
    )

    features_path = out_dir / FEATURES_FILENAME
    shapes_path = out_dir / SHAPES_FILENAME
    metadata_path = out_dir / METADATA_FILENAME
    manifest_path = out_dir / INDEX_MANIFEST_FILENAME
    existing = [path for path in (features_path, shapes_path, metadata_path) if path.exists()]
    if existing and not force:
        raise FileExistsError(
            f"{existing[0]} already exists; pass --force to rebuild the index"
        )

    contours = load_contours(contours_path)
    if len(contours) == 0:
        raise ValueError("contours dataset is empty; nothing to index")
    missing = [column for column in METADATA_COLUMNS if column not in contours.columns]
    if missing:
        raise ValueError(f"contours dataset is missing columns: {missing}")

    shapes = stack_contours(contours)
    features, columns, groups, feature_seconds = compute_features(contours, shapes, config)

    metadata = contours.loc[:, list(METADATA_COLUMNS)].copy().reset_index(drop=True)
    metadata["feature_row"] = np.arange(len(metadata), dtype=np.int32)

    stored_features = features.astype(config.dtype, copy=False)
    stored_shapes = shapes.astype(config.dtype, copy=False)

    violations = check_index(stored_features, stored_shapes, metadata, columns, groups, config)
    if violations:
        raise ValueError("index consistency check failed: " + "; ".join(violations))

    round_trip = {
        "features_max_abs_round_trip_error": float(
            np.abs(stored_features.astype(np.float64) - features).max()
        ),
        "shapes_max_abs_round_trip_error": float(
            np.abs(stored_shapes.astype(np.float64) - shapes).max()
        ),
    }

    manifest: dict[str, Any] = {
        "manifest_schema_version": INDEX_SCHEMA_VERSION,
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dataset": "EarthGlyph shape-feature index (P0-3 coarse-filter and re-rank input)",
        "stage": "P0-3 feature extraction + index build (NOT a retrieval result)",
        "not_a_retrieval_result": True,
        "query_sketch_used": False,
        "deterministic": True,
        "random_seed": None,
        "source": {
            "contours_path": contours_path.as_posix(),
            "contours_rows": int(len(contours)),
            "contours_size_bytes": contours_path.stat().st_size,
            "contours_sha256": sha256_of(contours_path),
            "source_manifest_path": source_manifest_path.as_posix(),
            "source_manifest_sha256": sha256_of(source_manifest_path),
            "upstream_dataset": source_manifest.get("dataset"),
            "source_name": source_manifest["source"].get("name"),
            "source_release": source_manifest["source"].get("release"),
            "source_bbox": source_manifest["source"].get("bbox"),
            "source_input_path": source_manifest["source"].get("input_path"),
            "source_input_sha256": source_manifest["source"].get("input_sha256"),
            "source_content_digest": source_manifest["source"].get("input_content_digest"),
            "projection": source_manifest.get("projection"),
            "cleaning": source_manifest.get("cleaning"),
            "normalization": source_manifest.get("normalization"),
        },
        "counts": {
            "input_candidates": int(len(contours)),
            "indexed_candidates": int(stored_features.shape[0]),
            "skipped_candidates": 0,
            "candidate_by_subtype": {
                str(key): int(value)
                for key, value in metadata["subtype"].value_counts().items()
            },
        },
        "index": {
            "n_points": int(stored_shapes.shape[1]),
            "feature_dim": int(stored_features.shape[1]),
            "feature_columns": columns,
            "feature_groups": groups,
            "row_order_contract": (
                "features[i], shapes[i] and metadata row i describe the same "
                "candidate_id; metadata.feature_row records the expectation"
            ),
            "storage": {
                "dtype": config.dtype,
                "byte_order": "little-endian IEEE-754",
                "layout": "C-order (row-major), no padding",
                "features_shape": list(stored_features.shape),
                "shapes_shape": list(stored_shapes.shape),
                "decode": (
                    "numpy .npy header carries dtype+shape; equivalently "
                    "np.frombuffer(bytes, dtype='<f4').reshape(shape)"
                ),
            },
            "round_trip": round_trip,
        },
        "features": {
            "fourier": feature_definition(config.n_harmonics, config.fold),
            "geometry": geometry_feature_definition(),
            "fusion": "none - the two groups are concatenated, not weighted",
        },
        "config": config.to_dict(),
        "build": {
            "feature_seconds": round(feature_seconds, 3),
            "command": build_command
            or (
                f"python scripts/build_index.py --contours {contours_path.as_posix()} "
                f"--out-dir {out_dir.as_posix()} --force"
            ),
            "input_dataset_command": source_manifest.get("reproduce", {}).get("command"),
        },
        "environment": _environment(),
        "git_commit": _git_commit(),
        "traceability": {
            "candidate_id_rule": (
                "candidate_id == source_id for single-part features, else "
                "source_id + '-part' + part_index (D21)"
            ),
            "geolocation": (
                "metadata.lon/lat is a shapely representative_point of the source "
                "part, so it lies inside the water polygon (D12)"
            ),
        },
    }

    if write_files:
        out_dir.mkdir(parents=True, exist_ok=True)
        np.save(features_path, stored_features)
        np.save(shapes_path, stored_shapes)
        metadata.to_parquet(metadata_path, compression="zstd")
        outputs: dict[str, Any] = {}
        for name, path in (
            ("features", features_path),
            ("shapes", shapes_path),
            ("metadata", metadata_path),
        ):
            outputs[name] = {
                "path": path.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_of(path),
            }
        outputs["metadata"]["parquet_rows"] = int(pq.read_metadata(metadata_path).num_rows)
        manifest["output"] = outputs
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        manifest["manifest_path"] = manifest_path.as_posix()

    return BuiltIndex(
        features=stored_features,
        shapes=stored_shapes,
        metadata=metadata,
        manifest=manifest,
        columns=columns,
        groups=groups,
        feature_seconds=feature_seconds,
    )


def load_index(index_dir: Path | str) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Read ``(features, shapes, metadata)`` back from an index directory."""
    directory = Path(index_dir)
    features = np.load(directory / FEATURES_FILENAME)
    shapes = np.load(directory / SHAPES_FILENAME)
    metadata = pd.read_parquet(directory / METADATA_FILENAME)
    return features, shapes, metadata


def check_index(
    features: np.ndarray,
    shapes: np.ndarray,
    metadata: pd.DataFrame,
    columns: Sequence[str],
    groups: dict[str, Any],
    config: IndexConfig,
) -> list[str]:
    """Index consistency checks; an empty list means the index is valid.

    Messages are returned instead of raised so a build can report every problem
    at once and tests can assert on the specific failure.
    """
    problems: list[str] = []
    n = features.shape[0] if features.ndim == 2 else -1

    if features.ndim != 2:
        problems.append(f"features must be 2-D, got ndim={features.ndim}")
    elif features.shape[1] != len(columns):
        problems.append(f"feature width {features.shape[1]} != {len(columns)} columns")
    if shapes.ndim != 3 or shapes.shape[2] != 2:
        problems.append(f"shapes must be (n, N, 2), got {shapes.shape}")
    if shapes.shape[0] != n:
        problems.append(f"candidate count mismatch: features {n} vs shapes {shapes.shape[0]}")
    if len(metadata) != n:
        problems.append(f"candidate count mismatch: features {n} vs metadata {len(metadata)}")

    ids = metadata["candidate_id"]
    duplicated = ids[ids.duplicated(keep=False)]
    if not duplicated.empty:
        problems.append(f"duplicate candidate_id: {sorted(set(duplicated))[:5]}")
    if "feature_row" in metadata.columns and not np.array_equal(
        metadata["feature_row"].to_numpy(), np.arange(len(metadata), dtype=np.int32)
    ):
        problems.append("metadata.feature_row is not the row index")

    if not np.all(np.isfinite(features)):
        problems.append("features contain non-finite values")
    if not np.all(np.isfinite(shapes)):
        problems.append("shapes contain non-finite values")
    if features.dtype != np.dtype(config.dtype):
        problems.append(f"features dtype {features.dtype} != configured {config.dtype}")

    offset = 0
    for name, group in groups.items():
        width = int(group["width"])
        if len(group["columns"]) != width:
            problems.append(f"group {name}: {len(group['columns'])} names for width {width}")
        offset += width
    if offset != features.shape[1]:
        problems.append(f"group widths sum {offset} != feature_dim {features.shape[1]}")

    if shapes.shape[0] and shapes.shape[1] >= 3:
        double = shapes.astype(np.float64)
        centroids = double.mean(axis=1)
        rms = np.sqrt(np.mean(np.sum((double - centroids[:, None, :]) ** 2, axis=2), axis=1))
        if float(np.abs(centroids).max()) > 1e-3:
            problems.append("stored contours are not centroid-centered")
        if float(np.abs(rms - 1.0).max()) > 1e-3:
            problems.append("stored contours are not unit RMS radius")

    lon = metadata["lon"].to_numpy(dtype=float)
    lat = metadata["lat"].to_numpy(dtype=float)
    if not (np.all(np.isfinite(lon)) and np.all(np.isfinite(lat))):
        problems.append("metadata contains non-finite coordinates")
    else:
        if not (np.all(lon >= -180.0) and np.all(lon <= 180.0)):
            problems.append("longitude outside [-180, 180]")
        if not (np.all(lat >= -90.0) and np.all(lat <= 90.0)):
            problems.append("latitude outside [-90, 90]")
        bounds = np.asarray(metadata["bbox_lonlat"].to_list(), dtype=float)
        inside = (
            (bounds[:, 0] <= lon)
            & (lon <= bounds[:, 2])
            & (bounds[:, 1] <= lat)
            & (lat <= bounds[:, 3])
        )
        if not inside.all():
            problems.append("representative point outside its own candidate bbox")

    areas = metadata["area_m2"].to_numpy(dtype=float)
    if np.any(~np.isfinite(areas)) or np.any(areas <= 0.0):
        problems.append("area_m2 must be finite and positive")
    if np.any(metadata["n_points"].to_numpy() != shapes.shape[1]):
        problems.append("metadata.n_points disagrees with the stored contour width")
    return problems
