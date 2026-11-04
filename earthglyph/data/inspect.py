"""Schema and quality inspection for downloaded GeoParquet data (P0-1).

Pure inspection: nothing here repairs or filters the data. The summary is
meant to answer, for a real Overture extract: what fields exist, how many
features have usable closed contours, and what anomalies are present.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

USABLE_GEOM_TYPES = ("Polygon", "MultiPolygon")


def _category_key(value: Any) -> str:
    """Normalize a categorical field (may be str or list in Overture schemas).

    Nulls reach us as ``None`` in memory but as ``float('nan')`` after a
    Parquet round-trip, so both count as missing.
    """
    if value is None:
        return "<null>"
    if isinstance(value, float) and value != value:
        return "<null>"
    if isinstance(value, (list, tuple, set, frozenset, np.ndarray)):
        if len(value) == 0:
            return "<empty-list>"
        return "|".join(str(v) for v in sorted(str(x) for x in value))
    return str(value)


def summarize_geoparquet(path: Path | str) -> dict[str, Any]:
    """Read a GeoParquet file and return a structured quality summary."""
    import geopandas as gpd
    import shapely

    path = Path(path)
    gdf = gpd.read_parquet(path)
    geom = gdf.geometry

    geom_types = {
        str(t): int((geom.geom_type == t).sum())
        for t in geom.geom_type.dropna().unique()
    }
    null_geom = int(geom.isna().sum())

    valid_mask = shapely.is_valid(geom.array)
    empty_mask = shapely.is_empty(geom.array)
    invalid_mask = ~valid_mask & ~geom.isna().array

    def dist(col: str, mask: Any = None) -> dict[str, int]:
        if col not in gdf.columns:
            return {}
        counts: dict[str, int] = {}
        series = gdf[col] if mask is None else gdf.loc[mask, col]
        for value in series:
            key = _category_key(value)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    null_counts = {
        col: int(gdf[col].isna().sum()) for col in gdf.columns if col != geom.name
    }

    id_dupes = 0
    if "id" in gdf.columns:
        id_dupes = int(gdf["id"].duplicated().sum())

    # Area (m^2) in a local azimuthal equidistant projection centered on the
    # data — lon/lat must not be treated as planar metres/degrees directly.
    areas_m2: list[float] = []
    usable_idx = geom.geom_type.isin(USABLE_GEOM_TYPES) & valid_mask & ~empty_mask
    if int(usable_idx.sum()) > 0:
        bounds = np.array(geom.total_bounds, dtype=float)
        lon0 = (bounds[0] + bounds[2]) / 2.0
        lat0 = (bounds[1] + bounds[3]) / 2.0
        from pyproj import Transformer

        tr = Transformer.from_proj(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +ellps=WGS84 +units=m +no_defs",
            always_xy=True,
        )
        def _to_meters(xy: np.ndarray) -> np.ndarray:
            x, y = tr.transform(xy[:, 0], xy[:, 1])
            return np.column_stack((x, y))

        for g in geom[usable_idx]:
            areas_m2.append(shapely.transform(g, _to_meters).area)

    summary: dict[str, Any] = {
        "file": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
        },
        "rows": int(len(gdf)),
        "columns": {str(c): str(t) for c, t in gdf.dtypes.items()},
        "crs": str(gdf.crs) if gdf.crs is not None else None,
        "geometry": {
            "column": geom.name,
            "type_counts": geom_types,
            "null_geometries": null_geom,
            "empty_geometries": int(empty_mask.sum()),
            "invalid_geometries": int(invalid_mask.sum()),
            "invalid_examples": [
                str(shapely.is_valid_reason(geom[i]))
                for i in np.flatnonzero(np.asarray(invalid_mask))[:10]
            ],
        },
        "subtype_distribution": dist("subtype"),
        "class_distribution": dist("class"),
        "subtype_distribution_of_usable_closed": dist("subtype", usable_idx),
        "null_counts_by_column": null_counts,
        "duplicate_ids": id_dupes,
        "usable_closed_contours": {
            "definition": "Polygon/MultiPolygon AND valid AND non-empty",
            "count": int(usable_idx.sum()),
            "share_of_rows": round(float(usable_idx.mean()), 4) if len(gdf) else 0.0,
        },
        "areas_m2_of_usable": _area_stats(areas_m2),
    }
    return summary


def _area_stats(areas: list[float]) -> dict[str, Any]:
    if not areas:
        return {"count": 0}
    arr = np.asarray(areas, dtype=float)
    qs = np.percentile(arr, [0, 1, 5, 50, 95, 99, 100])
    buckets = {
        "< 0.01 ha (100 m2)": int((arr < 1e2).sum()),
        "0.01-1 ha": int(((arr >= 1e2) & (arr < 1e4)).sum()),
        "1-100 ha": int(((arr >= 1e4) & (arr < 1e6)).sum()),
        "100 ha-10 km2": int(((arr >= 1e6) & (arr < 1e7)).sum()),
        "> 10 km2": int((arr >= 1e7).sum()),
    }
    return {
        "count": int(arr.size),
        "min": float(qs[0]),
        "p1": float(qs[1]),
        "p5": float(qs[2]),
        "median": float(qs[3]),
        "p95": float(qs[4]),
        "p99": float(qs[5]),
        "max": float(qs[6]),
        "size_buckets": buckets,
    }


def write_summary_json(summary: dict[str, Any], out_path: Path | str) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
