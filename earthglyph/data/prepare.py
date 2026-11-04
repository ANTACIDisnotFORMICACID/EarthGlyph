"""Build the standardized contour dataset from a real GeoParquet extract (P0-2).

Pipeline (docs/RETRIEVAL_DESIGN.md §1–2):

    raw GeoParquet → repair (only when invalid) → split MultiPolygon parts
                   → subtype / area / duplicate filtering
                   → local metric projection (D16)
                   → arc-length resample to N points → normalize (D7)
                   → data/processed/contours.parquet + data_manifest.json

Everything is deterministic: no randomness, no learned components, and the
manifest records the source release, file checksum, encoding-independent
content digest, projection, cleaning parameters and environment, so the exact
dataset can be rebuilt from the raw extract with one command.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from earthglyph.data.download import sha256_of
from earthglyph.geometry import (
    REASON_DEGENERATE_RING,
    REASON_DUPLICATE,
    REASON_MIN_AREA,
    REASON_SUBTYPE,
    REASON_UNREPAIRABLE,
    AeqdProjection,
    ContourError,
    FilterConfig,
    RejectionLog,
    build_contour,
    extends_beyond_frame,
    exterior_ring,
    geometry_rejection_reason,
    hole_count,
    resolve_duplicates,
    split_feature,
    subtype_allowed,
)

CONTOURS_FILENAME = "contours.parquet"
MANIFEST_FILENAME = "data_manifest.json"
REJECTED_FILENAME = "rejected_samples.parquet"
PENDING_FILENAME = "pending_analysis.parquet"
MANIFEST_SCHEMA_VERSION = "1.0"


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        same = bool(value == value)
    except (TypeError, ValueError):
        return None
    if not same:
        return None
    return str(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        same = bool(value == value)
    except (TypeError, ValueError):
        return None
    if not same:
        return None
    try:
        return bool(value)
    except (TypeError, ValueError):
        return None


def _column(frame: pd.DataFrame, name: str) -> list[Any]:
    if name in frame.columns:
        return list(frame[name])
    return [None] * len(frame)


def content_digest(ids: list[Any], geometries: list[Any]) -> dict[str, Any]:
    """Encoding-independent digest of the source content (D17).

    Two files that hold the same features in a different Parquet page layout
    hash differently; this digest hashes ``id → WKB`` instead, sorted, so it is
    stable across readers and chunking strategies.
    """
    import shapely

    arr = np.empty(len(geometries), dtype=object)
    arr[:] = list(geometries)
    wkbs = shapely.to_wkb(arr)
    pairs = sorted(
        (
            str(feature_id),
            "<null>" if wkb is None else bytes(wkb).hex(),
        )
        for feature_id, wkb in zip(ids, wkbs, strict=True)
    )
    digest = hashlib.sha256()
    for feature_id, wkb_hex in pairs:
        digest.update(feature_id.encode("utf-8"))
        digest.update(b"\t")
        digest.update(wkb_hex.encode("ascii"))
        digest.update(b"\n")
    return {
        "algorithm": "sha256 over sorted lines '<id>\\t<wkb-hex>' (missing geometry -> '<null>')",
        "rows": len(pairs),
        "value": digest.hexdigest(),
    }


@dataclass
class _PartCandidate:
    """A part that passed geometry checks; dedup runs before it is emitted."""

    candidate_id: str
    source_id: str
    part_index: int
    n_parts: int
    polygon_metric: Any
    polygon_geo: Any
    record: dict[str, Any]


@dataclass
class PrepareResult:
    manifest: dict[str, Any]
    records: list[dict[str, Any]] = field(default_factory=list)
    rejections: RejectionLog = field(default_factory=RejectionLog)


def _read_source(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    import geopandas as gpd

    gdf = gpd.read_parquet(path)
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    meta: dict[str, Any] = {}
    if sidecar.is_file():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    return gdf, meta


def _frame_from_meta(meta: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = meta.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        return (
            float(bbox["min_lon"]),
            float(bbox["min_lat"]),
            float(bbox["max_lon"]),
            float(bbox["max_lat"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def build_candidates(
    gdf: pd.DataFrame,
    *,
    projection: AeqdProjection,
    config: FilterConfig,
    n_points: int,
    frame: tuple[float, float, float, float] | None = None,
) -> tuple[list[_PartCandidate], RejectionLog, dict[str, int]]:
    """Run extraction, filtering, projection and normalization over all rows."""
    geoms = list(gdf.geometry)
    ids = _column(gdf, "id")
    subtypes = _column(gdf, "subtype")
    classes = _column(gdf, "class")
    intermittent = _column(gdf, "is_intermittent")
    salt = _column(gdf, "is_salt")

    rejections = RejectionLog()
    candidates: list[_PartCandidate] = []
    feature_reasons: Counter[str] = Counter()
    part_reasons: Counter[str] = Counter()

    for row, (geometry, feature_id) in enumerate(zip(geoms, ids, strict=True)):
        source_id = _str_or_none(feature_id) or f"<row {row}>"
        subtype = _str_or_none(subtypes[row])
        klass = _str_or_none(classes[row])

        reason = geometry_rejection_reason(geometry)
        if reason is not None:
            feature_reasons[reason] += 1
            kind = getattr(geometry, "geom_type", None)
            rejections.add(
                reason,
                source_id,
                detail=None if kind is None else str(kind),
                subtype=subtype,
                geometry=None if kind is None else geometry,
            )
            continue

        if not subtype_allowed(subtypes[row], config.subtypes):
            feature_reasons[REASON_SUBTYPE] += 1
            rejections.add(
                REASON_SUBTYPE,
                source_id,
                detail=f"subtype={subtype!r}",
                subtype=subtype,
                feature_class=klass,
                geometry=geometry,
            )
            continue

        parts = split_feature(geometry)
        if not parts:
            feature_reasons[REASON_UNREPAIRABLE] += 1
            rejections.add(
                REASON_UNREPAIRABLE,
                source_id,
                detail=str(geometry.geom_type),
                subtype=subtype,
                geometry=geometry,
            )
            continue

        for part in parts:
            metric = projection.project_geometry(part.polygon)
            area_m2 = float(metric.area)
            if area_m2 < config.min_area_m2:
                part_reasons[REASON_MIN_AREA] += 1
                rejections.add(
                    REASON_MIN_AREA,
                    source_id,
                    detail=f"area={area_m2:.1f} m2 < {config.min_area_m2:.1f} m2",
                    subtype=subtype,
                    area_m2=area_m2,
                    geometry=part.polygon,
                )
                continue

            try:
                contour = build_contour(exterior_ring(metric), n_points)
            except ContourError as exc:
                part_reasons[REASON_DEGENERATE_RING] += 1
                rejections.add(
                    REASON_DEGENERATE_RING,
                    source_id,
                    detail=str(exc),
                    subtype=subtype,
                    area_m2=area_m2,
                    geometry=part.polygon,
                )
                continue

            point = part.polygon.representative_point()
            lon, lat = float(point.x), float(point.y)
            min_lon, min_lat, max_lon, max_lat = part.polygon.bounds
            candidate_id = (
                source_id if part.n_parts == 1 else f"{source_id}-part{part.part_index}"
            )
            record: dict[str, Any] = {
                "candidate_id": candidate_id,
                "source_id": source_id,
                "part_index": int(part.part_index),
                "n_parts": int(part.n_parts),
                "subtype": subtype,
                "class": klass,
                "lon": lon,
                "lat": lat,
                "area_m2": area_m2,
                "area_ha": area_m2 / 1e4,
                "perimeter_m": contour.perimeter_m,
                "n_holes": int(hole_count(part.polygon)),
                "n_points": int(contour.points.shape[0]),
                "rms_radius_m": contour.rms_radius_m,
                "original_winding": contour.original_winding,
                "extends_beyond_frame": (
                    extends_beyond_frame((min_lon, min_lat, max_lon, max_lat), frame)
                    if frame is not None
                    else False
                ),
                "was_repaired": bool(part.repair.repaired),
                "repair_reason": part.repair.reason,
                "is_intermittent": _bool_or_none(intermittent[row]),
                "is_salt": _bool_or_none(salt[row]),
                "bbox_lonlat": [min_lon, min_lat, max_lon, max_lat],
                "contour": contour.points.reshape(-1).tolist(),
            }
            candidates.append(
                _PartCandidate(
                    candidate_id=candidate_id,
                    source_id=source_id,
                    part_index=part.part_index,
                    n_parts=part.n_parts,
                    polygon_metric=metric,
                    polygon_geo=part.polygon,
                    record=record,
                )
            )

    if config.dedupe and len(candidates) > 1:
        polygons = [c.polygon_metric for c in candidates]
        dropped_indices = resolve_duplicates(polygons, config.max_iou)
        for dropped_index in sorted(dropped_indices):
            kept_index, iou = dropped_indices[dropped_index]
            dropped, kept = candidates[dropped_index], candidates[kept_index]
            part_reasons[REASON_DUPLICATE] += 1
            rejections.add(
                REASON_DUPLICATE,
                dropped.source_id,
                detail=(
                    f"iou={iou:.4f} with kept candidate {kept.candidate_id}"
                    f" (area {kept.record['area_m2']:.1f} m2)"
                ),
                subtype=dropped.record["subtype"],
                area_m2=dropped.record["area_m2"],
                geometry=dropped.polygon_geo,
            )
        candidates = [
            c for index, c in enumerate(candidates) if index not in dropped_indices
        ]

    stats = {
        "rejected_features_total": sum(feature_reasons.values()),
        "rejected_parts_total": sum(part_reasons.values()),
        **{f"feature_{k}": v for k, v in feature_reasons.items()},
        **{f"part_{k}": v for k, v in part_reasons.items()},
    }
    return candidates, rejections, stats


def _contour_schema(n_points: int) -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("candidate_id", pa.string()),
            ("source_id", pa.string()),
            ("part_index", pa.int32()),
            ("n_parts", pa.int32()),
            ("subtype", pa.string()),
            ("class", pa.string()),
            ("lon", pa.float64()),
            ("lat", pa.float64()),
            ("area_m2", pa.float64()),
            ("area_ha", pa.float64()),
            ("perimeter_m", pa.float64()),
            ("n_holes", pa.int32()),
            ("n_points", pa.int32()),
            ("rms_radius_m", pa.float64()),
            ("original_winding", pa.string()),
            ("extends_beyond_frame", pa.bool_()),
            ("was_repaired", pa.bool_()),
            ("repair_reason", pa.string()),
            ("is_intermittent", pa.bool_()),
            ("is_salt", pa.bool_()),
            ("bbox_lonlat", pa.list_(pa.float64(), 4)),
            ("contour", pa.list_(pa.float64(), 2 * n_points)),
        ]
    )


def _rejected_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("reason", pa.string()),
            ("source_id", pa.string()),
            ("subtype", pa.string()),
            ("area_m2", pa.float64()),
            ("detail", pa.string()),
            ("wkb", pa.binary()),
        ]
    )


def write_contours(records: list[dict[str, Any]], out_path: Path, n_points: int) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not records:
        columns: dict[str, Any] = {name: [] for name in _contour_schema(n_points).names}
    else:
        columns = {name: [rec[name] for rec in records] for name in records[0]}
    table = pa.Table.from_pydict(columns, schema=_contour_schema(n_points))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd")


def write_rejected_samples(
    rejections: RejectionLog, out_path: Path, limit_per_reason: int
) -> dict[str, Any]:
    """Save a deterministic sample of rejected shapes for visual inspection."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    rows: dict[str, list[Any]] = {name: [] for name in _rejected_schema().names}
    kept_per_reason: Counter[str] = Counter()
    for item in rejections.items:
        if kept_per_reason[item.reason] >= limit_per_reason:
            continue
        geometry = item.geometry
        rows["reason"].append(item.reason)
        rows["source_id"].append(item.source_id)
        rows["subtype"].append(item.subtype)
        rows["area_m2"].append(item.area_m2)
        rows["detail"].append(item.detail)
        rows["wkb"].append(None if geometry is None else shapely.to_wkb(geometry))
        kept_per_reason[item.reason] += 1

    table = pa.Table.from_pydict(rows, schema=_rejected_schema())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd")
    return {
        "path": out_path.as_posix(),
        "rows": len(rows["reason"]),
        "size_bytes": out_path.stat().st_size,
        "sha256": sha256_of(out_path),
        "limit_per_reason": limit_per_reason,
        "saved_per_reason": dict(sorted(kept_per_reason.items())),
        "note": "inspection sample only - full per-reason counts are in the manifest",
    }


def write_pending_analysis(
    rejections: RejectionLog, projection: AeqdProjection, out_path: Path
) -> dict[str, Any]:
    """Persist polygonal features outside the subtype allowlist (D6).

    These are *not* searchable candidates: they are kept as pending-analysis
    data (rivers, canals, human-made basins, untyped ``water``) so a later
    phase can decide about them without re-reading the raw extract.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    schema = pa.schema(
        [
            ("source_id", pa.string()),
            ("subtype", pa.string()),
            ("class", pa.string()),
            ("area_m2", pa.float64()),
            ("n_parts", pa.int32()),
            ("wkb", pa.binary()),
        ]
    )
    rows: dict[str, list[Any]] = {name: [] for name in schema.names}
    subtype_counts: Counter[str] = Counter()
    for item in rejections.of_reason(REASON_SUBTYPE):
        if item.geometry is None:
            continue
        parts = split_feature(item.geometry)
        if not parts:
            continue
        area_m2 = sum(float(projection.project_geometry(p.polygon).area) for p in parts)
        rows["source_id"].append(item.source_id)
        rows["subtype"].append(item.subtype)
        rows["class"].append(item.feature_class)
        rows["area_m2"].append(area_m2)
        rows["n_parts"].append(len(parts))
        rows["wkb"].append(shapely.to_wkb(item.geometry))
        subtype_counts[item.subtype or "<null>"] += 1

    table = pa.Table.from_pydict(rows, schema=schema)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd")
    return {
        "path": out_path.as_posix(),
        "rows": len(rows["source_id"]),
        "size_bytes": out_path.stat().st_size,
        "sha256": sha256_of(out_path),
        "by_subtype": dict(sorted(subtype_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "note": "kept for later analysis only; not part of the searchable candidate set",
    }


def load_contours(path: Path | str) -> pd.DataFrame:
    """Read ``contours.parquet`` back as a DataFrame."""
    return pd.read_parquet(Path(path))


def stack_contours(frame: pd.DataFrame) -> np.ndarray:
    """Stack the flat ``contour`` column into an (n, N, 2) float array."""
    if len(frame) == 0:
        return np.zeros((0, 0, 2), dtype=float)
    flat = np.asarray(
        [np.asarray(values, dtype=float) for values in frame["contour"]], dtype=float
    )
    n_points = flat.shape[1] // 2
    return flat.reshape(len(frame), n_points, 2)


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
        return None  # e.g. a repository without any commit yet
    return out.stdout.strip() or None


def _environment() -> dict[str, str]:
    import shapely

    versions: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "shapely": shapely.__version__,
        "geos": ".".join(str(v) for v in shapely.geos_version),
    }
    for name in ("pyarrow", "geopandas", "pyproj", "duckdb"):
        try:
            mod = __import__(name)
        except ImportError:
            continue
        versions[name] = str(getattr(mod, "__version__", "unknown"))
    try:
        import pyproj

        versions["proj"] = pyproj.proj_version_str
    except ImportError:
        pass
    return versions


def _area_percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    qs = np.percentile(arr, [0, 1, 50, 95, 99, 100])
    keys = ["min", "p1", "median", "p95", "p99", "max"]
    return {k: float(v) for k, v in zip(keys, qs, strict=True)}


def prepare_contours(
    input_path: Path | str,
    out_dir: Path | str,
    *,
    n_points: int = 128,
    config: FilterConfig | None = None,
    rejected_sample_limit: int = 200,
    force: bool = False,
    build_command: str | None = None,
    write_files: bool = True,
) -> PrepareResult:
    """Build ``contours.parquet`` + ``data_manifest.json`` from a raw extract."""
    import pyarrow.parquet as pq

    input_path = Path(input_path)
    out_dir = Path(out_dir)
    config = config or FilterConfig()
    if n_points < 3:
        raise ValueError(f"n_points must be >= 3, got {n_points}")
    if not input_path.is_file():
        raise FileNotFoundError(f"input GeoParquet not found: {input_path}")

    contours_path = out_dir / CONTOURS_FILENAME
    manifest_path = out_dir / MANIFEST_FILENAME
    if contours_path.exists() and not force:
        raise FileExistsError(
            f"{contours_path} already exists; pass --force to rebuild it"
        )

    gdf, meta = _read_source(input_path)
    raw_rows = int(len(gdf))
    frame = _frame_from_meta(meta)
    bounds = [float(v) for v in gdf.geometry.total_bounds]
    projection = AeqdProjection.from_bounds(
        (bounds[0], bounds[1], bounds[2], bounds[3])
    )

    candidates, rejections, stats = build_candidates(
        gdf,
        projection=projection,
        config=config,
        n_points=n_points,
        frame=frame,
    )
    records = [c.record for c in candidates]

    ids = [rec["candidate_id"] for rec in records]
    duplicates = [cid for cid, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"candidate_id must be unique, got duplicates: {duplicates[:5]}")

    subtype_counts = Counter(rec["subtype"] or "<null>" for rec in records)
    areas_ha = [rec["area_ha"] for rec in records]

    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dataset": "EarthGlyph standardized contours (P0-2 preprocessing output)",
        "stage": "P0-2 geometry cleaning + contour extraction (NOT a retrieval result)",
        "deterministic": True,
        "random_seed": None,
        "source": {
            "name": meta.get("source"),
            "dataset": meta.get("dataset"),
            "release": meta.get("release"),
            "download_time_utc": meta.get("download_time_utc"),
            "fetch_method": meta.get("fetch_method"),
            "bbox": meta.get("bbox"),
            "input_path": input_path.as_posix(),
            "input_size_bytes": input_path.stat().st_size,
            "input_sha256": sha256_of(input_path),
            "input_content_digest": content_digest(_column(gdf, "id"), list(gdf.geometry)),
            "input_crs": str(gdf.crs) if gdf.crs is not None else None,
            "input_columns": [str(c) for c in gdf.columns],
        },
        "projection": {
            **projection.describe(),
            "center_from": "centre of the data bounds",
            "data_bounds_lonlat": bounds,
        },
        "normalization": {
            "n_points": n_points,
            "sampling": "uniform along exterior-ring arc length (not vertex index)",
            "boundary": "exterior ring only; holes counted in n_holes, not merged",
            "winding": "counter-clockwise in projected metres",
            "centering": "centroid of the resampled points",
            "scaling": "unit RMS radius (earthglyph.geometry.normalize_contour)",
            "rotation_alignment": "not applied - retrieval stage (D8)",
            "mirror": "not applied - retrieval stage (D9)",
            "contour_layout": "row-major flat [x0, y0, x1, y1, ...], 2 * n_points floats",
            "coordinate_frame": "normalized units (metres divided by RMS radius)",
        },
        "cleaning": config.to_dict(),
        "counts": {
            "raw_rows": raw_rows,
            "raw_geometry_types": {
                str(t): int(c)
                for t, c in gdf.geometry.geom_type.value_counts(dropna=False).items()
            },
            "rejected_features_total": stats["rejected_features_total"],
            "rejected_features_by_reason": {
                k.removeprefix("feature_"): v
                for k, v in stats.items()
                if k.startswith("feature_")
            },
            "features_reaching_part_stage": raw_rows - stats["rejected_features_total"],
            "parts_extracted": stats["rejected_parts_total"] + len(records),
            "rejected_parts_by_reason": {
                k.removeprefix("part_"): v for k, v in stats.items() if k.startswith("part_")
            },
            "candidates": len(records),
            "features_with_candidates": len({rec["source_id"] for rec in records}),
            "candidate_by_subtype": dict(sorted(subtype_counts.items(), key=lambda kv: -kv[1])),
            "candidates_extending_beyond_frame": int(
                sum(bool(rec["extends_beyond_frame"]) for rec in records)
            ),
            "candidates_repaired": int(sum(bool(rec["was_repaired"]) for rec in records)),
            "candidates_with_holes": int(sum(rec["n_holes"] > 0 for rec in records)),
            "candidates_from_multipolygon": int(sum(rec["n_parts"] > 1 for rec in records)),
        },
        "candidate_stats": {
            "area_ha_percentiles": _area_percentiles(areas_ha),
            "n_holes_distribution": dict(
                sorted(Counter(rec["n_holes"] for rec in records).items())
            ),
            "winding_distribution": dict(
                sorted(Counter(rec["original_winding"] for rec in records).items())
            ),
            "intermittent_true": int(sum(rec["is_intermittent"] is True for rec in records)),
            "salt_true": int(sum(rec["is_salt"] is True for rec in records)),
        },
        "environment": _environment(),
        "git_commit": _git_commit(),
        "reproduce": {
            "command": build_command
            or (
                f"python scripts/prepare_polygons.py --input {input_path.as_posix()} "
                f"--out-dir {out_dir.as_posix()} --force"
            ),
            "notes": "raw GeoParquet is never modified; rebuilding overwrites data/processed only",
        },
    }

    if write_files:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_contours(records, contours_path, n_points)
        manifest["output"] = {
            "contours": {
                "path": contours_path.as_posix(),
                "rows": len(records),
                "size_bytes": contours_path.stat().st_size,
                "sha256": sha256_of(contours_path),
                "parquet_row_groups": pq.read_metadata(contours_path).num_row_groups,
            },
            "rejected_samples": write_rejected_samples(
                rejections, out_dir / REJECTED_FILENAME, rejected_sample_limit
            ),
            "pending_analysis": write_pending_analysis(
                rejections, projection, out_dir / PENDING_FILENAME
            ),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        manifest["manifest_path"] = manifest_path.as_posix()

    return PrepareResult(manifest=manifest, records=records, rejections=rejections)
