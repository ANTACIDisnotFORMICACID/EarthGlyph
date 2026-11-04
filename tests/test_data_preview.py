"""Offline tests for the data-probe preview selection (P0-1).

Synthetic geometries in ``tmp_path`` only — they check the sampling and
rendering helpers (clip-artifact exclusion, deterministic stratification,
hole winding), not retrieval quality.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import preview_data as pv  # noqa: E402

FRAME = (-90.0, 44.0, -89.0, 45.0)


def bbox_of(geom) -> dict:
    xmin, ymin, xmax, ymax = geom.bounds
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def make_gdf(n: int = 20, with_frame_touch: bool = True) -> gpd.GeoDataFrame:
    geoms, ids, subtypes, bbox = [], [], [], []
    for i in range(n):
        lon = -89.9 + i * 0.02
        size = 0.001 * (i + 1)  # steadily increasing area
        g = shapely.box(lon, 44.1, lon + size, 44.1 + size)
        geoms.append(g)
        ids.append(f"synthetic-{i:02d}")
        subtypes.append(["lake", "pond", "reservoir"][i % 3])
        bbox.append(bbox_of(g))
    if with_frame_touch:
        clipped = shapely.box(-90.0, 44.2, -89.7, 44.5)  # west edge == frame
        geoms.append(clipped)
        ids.append("synthetic-frame-clip")
        subtypes.append("water")
        bbox.append(bbox_of(clipped))
    return gpd.GeoDataFrame(
        {"id": ids, "subtype": subtypes, "bbox": bbox},
        geometry=geoms,
        crs="EPSG:4326",
    )


def test_touching_frame_detects_edge_contact():
    assert pv._touching_frame({"xmin": -90.0, "ymin": 44.2, "xmax": -89.7, "ymax": 44.5}, FRAME)
    assert pv._touching_frame({"xmin": -89.5, "ymin": 44.0, "xmax": -89.2, "ymax": 44.3}, FRAME)
    assert not pv._touching_frame({"xmin": -89.5, "ymin": 44.2, "xmax": -89.2, "ymax": 44.3}, FRAME)


def test_touching_frame_uses_epsilon_not_exact_equality():
    near = {"xmin": -89.9999999, "ymin": 44.2, "xmax": -89.7, "ymax": 44.5}
    assert pv._touching_frame(near, FRAME)
    assert not pv._touching_frame(near, FRAME, eps=0.0)


def test_select_samples_excludes_frame_clips_and_is_deterministic():
    gdf = make_gdf()
    picked = pv.select_samples(gdf, FRAME, 8, seed=42)
    ids = list(picked["id"])
    assert "synthetic-frame-clip" not in ids
    assert len(ids) == 8
    again = pv.select_samples(gdf, FRAME, 8, seed=42)
    assert list(again["id"]) == ids


def test_select_samples_spans_sizes():
    picked = pv.select_samples(make_gdf(), FRAME, 8, seed=7)
    areas = np.array([g.area for g in picked.geometry.to_crs(
        "+proj=aeqd +lat_0=44.5 +lon_0=-89.5 +ellps=WGS84 +units=m +no_defs"
    )])
    assert areas.max() / areas.min() > 5  # sample spans small ponds to large lakes
    assert picked["subtype"].nunique() > 1


def test_select_samples_returns_empty_when_everything_touches_frame():
    g = shapely.box(-90.0, 44.0, -89.0, 45.0)  # == frame
    gdf = gpd.GeoDataFrame(
        {"id": ["whole"], "subtype": ["water"], "bbox": [bbox_of(g)]},
        geometry=[g],
        crs="EPSG:4326",
    )
    assert len(pv.select_samples(gdf, FRAME, 4, seed=1)) == 0


def test_select_samples_skips_invalid_and_open_geometries():
    gdf = make_gdf(5, with_frame_touch=False)
    gdf.loc[len(gdf)] = {
        "id": "line",
        "subtype": "stream",
        "bbox": bbox_of(shapely.LineString([(-89.5, 44.5), (-89.4, 44.6)])),
        "geometry": shapely.LineString([(-89.5, 44.5), (-89.4, 44.6)]),
    }
    picked = pv.select_samples(gdf, FRAME, 5, seed=3)
    assert "line" not in list(picked["id"])


def test_geom_paths_reverses_hole_winding():
    shell = [(-89.5, 44.5), (-89.4, 44.5), (-89.4, 44.6), (-89.5, 44.6)]
    hole = [(-89.47, 44.52), (-89.45, 44.52), (-89.45, 44.55), (-89.47, 44.55)]
    poly = shapely.Polygon(shell, [hole])
    parts = pv._geom_paths(poly)
    assert len(parts) == 2
    assert parts[0].vertices.tolist() == np.asarray(poly.exterior.coords).tolist()
    assert parts[1].vertices.tolist() == np.asarray(poly.interiors[0].coords)[::-1].tolist()
    # exterior and hole must wind in opposite directions so the nonzero fill
    # rule renders the hole empty
    assert np.sign(_signed_area(parts[0].vertices)) != np.sign(_signed_area(parts[1].vertices))


def test_geom_paths_handles_multipolygon():
    multi = shapely.MultiPolygon(
        [shapely.Polygon([(-89.5, 44.5), (-89.4, 44.5), (-89.4, 44.6)]),
         shapely.Polygon([(-89.3, 44.5), (-89.2, 44.5), (-89.2, 44.6)])]
    )
    assert len(pv._geom_paths(multi)) == 2


def test_project_preserves_metric_scale():
    """0.1 deg of latitude ~ 11.1 km and 0.1 deg of longitude at lat 44.5 is
    ~ 7.9 km: lon/lat must not be read as a planar square."""
    g = shapely.box(-89.5, 44.5, -89.4, 44.6)
    (projected,) = pv._project([g], -89.45, 44.55)
    minx, miny, maxx, maxy = projected.bounds
    height = maxy - miny
    width = maxx - minx
    assert 10_900 < height < 11_200
    assert 7_700 < width < 8_100
    assert width < height  # naive lon/lat would render this as a perfect square


def test_primary_name_only_accepts_struct_like_values():
    assert pv._primary_name({"primary": "Rock Lake"}) == "Rock Lake"
    assert pv._primary_name(None) == ""
    assert pv._primary_name("Rock Lake") == ""


def _signed_area(coords: np.ndarray) -> float:
    x, y = coords[:, 0], coords[:, 1]
    return float(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
