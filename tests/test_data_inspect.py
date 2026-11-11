"""Offline tests for the GeoParquet inspection logic (P0-1).

Fixtures are synthetic one-file datasets written under ``tmp_path`` so the
summarizer can be exercised without network access and without touching the
real data in ``data/``. The shapes are deliberately not real lakes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip('geopandas')  # before the optional imports

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import shapely  # noqa: E402

from earthglyph.data.inspect import (  # noqa: E402
    _area_stats,
    _category_key,
    summarize_geoparquet,
    write_summary_json,
)

LON0, LAT0 = -89.5, 44.5  # inside the P0-1 Wisconsin probe area


def square(lon: float, lat: float, span: float) -> shapely.Polygon:
    return shapely.box(lon, lat, lon + span, lat + span)


def bowtie() -> shapely.Polygon:
    """Self-intersecting ring: the classic invalid geometry."""
    return shapely.Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])


@pytest.fixture()
def mixed_parquet(tmp_path: Path) -> Path:
    geoms = [
        square(LON0, LAT0, 0.01),                       # 0 small valid polygon
        square(LON0 + 0.05, LAT0, 0.03),                # 1 larger valid polygon
        bowtie(),                                       # 2 invalid
        shapely.MultiPolygon([square(LON0, LAT0 + 0.1, 0.02),
                             square(LON0 + 0.04, LAT0 + 0.1, 0.01)]),  # 3 multipoly
        shapely.Polygon(),                              # 4 empty
        None,                                           # 5 missing geometry
        square(LON0, LAT0, 0.01),                       # 6 duplicate id + duplicate shape
        shapely.LineString([(LON0, LAT0), (LON0 + 0.01, LAT0 + 0.01)]),  # 7 line
    ]
    gdf = gpd.GeoDataFrame(
        {
            "id": ["w1", "w2", "w3", "w4", "w5", "w6", "w1", "w7"],
            "subtype": ["lake", "pond", "river", "lake", "canal", None, "lake", "stream"],
            "class": ["lake", "pond", "river", "lake", "canal", "water", "lake", "stream"],
            "names": ["Rock Lake", None, None, "Twin Lakes", None, None, "Rock Lake", None],
        },
        geometry=geoms,
        crs="EPSG:4326",
    )
    path = tmp_path / "synthetic_mixed.parquet"
    gdf.to_parquet(path)
    return path


def test_rows_columns_and_crs(mixed_parquet):
    s = summarize_geoparquet(mixed_parquet)
    assert s["rows"] == 8
    assert s["file"]["size_bytes"] == mixed_parquet.stat().st_size
    assert set(s["columns"]) >= {"id", "subtype", "class", "geometry"}
    assert s["crs"] and "4326" in s["crs"]


def test_geometry_type_and_health_counts(mixed_parquet):
    s = summarize_geoparquet(mixed_parquet)["geometry"]
    assert s["column"] == "geometry"
    assert s["type_counts"] == {"Polygon": 5, "MultiPolygon": 1, "LineString": 1}
    assert s["null_geometries"] == 1
    assert s["empty_geometries"] == 1
    assert s["invalid_geometries"] == 1  # the bowtie only; null is not counted
    assert s["invalid_examples"] and "Self-intersection" in s["invalid_examples"][0]


def test_usable_closed_contours_definition(mixed_parquet):
    s = summarize_geoparquet(mixed_parquet)
    usable = s["usable_closed_contours"]
    assert usable["definition"] == "Polygon/MultiPolygon AND valid AND non-empty"
    # rows 0,1,3,6 qualify; bowtie (invalid), empty, null and LineString do not.
    assert usable["count"] == 4
    assert usable["share_of_rows"] == pytest.approx(4 / 8, abs=1e-4)
    # rows 0 (lake), 1 (pond), 3 (lake), 6 (lake)
    assert s["subtype_distribution_of_usable_closed"] == {"lake": 3, "pond": 1}


def test_duplicate_ids_and_null_counts(mixed_parquet):
    s = summarize_geoparquet(mixed_parquet)
    assert s["duplicate_ids"] == 1
    assert s["null_counts_by_column"]["subtype"] == 1
    assert s["null_counts_by_column"]["names"] == 5
    assert "geometry" not in s["null_counts_by_column"]


def test_distributions_are_sorted_desc(mixed_parquet):
    dist = summarize_geoparquet(mixed_parquet)["subtype_distribution"]
    counts = list(dist.values())
    assert counts == sorted(counts, reverse=True)
    assert dist["stream"] == 1
    assert dist["<null>"] == 1


def test_areas_use_metric_projection_not_lonlat_degrees(mixed_parquet):
    s = summarize_geoparquet(mixed_parquet)
    areas = s["areas_m2_of_usable"]
    assert areas["count"] == 4
    # A 0.01 x 0.01 degree square is 1e-4 in raw lon/lat units but roughly
    # 8.8e5 m2 on the ground; asserting the metre scale is the point.
    assert 5e5 < areas["min"] < 2e6
    assert areas["max"] > areas["min"]
    assert areas["size_buckets"]["0.01-1 ha"] == 0
    assert sum(areas["size_buckets"].values()) == 4


def test_area_median_between_extremes(mixed_parquet):
    areas = summarize_geoparquet(mixed_parquet)["areas_m2_of_usable"]
    assert areas["min"] <= areas["p5"] <= areas["median"] <= areas["p95"] <= areas["max"]


def test_summary_has_no_unusable_rows_area_section(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": ["l1"], "subtype": ["stream"]},
        geometry=[shapely.LineString([(0, 0), (1, 1)])],
        crs="EPSG:4326",
    )
    path = tmp_path / "synthetic_lines_only.parquet"
    gdf.to_parquet(path)

    s = summarize_geoparquet(path)
    assert s["usable_closed_contours"]["count"] == 0
    assert s["areas_m2_of_usable"] == {"count": 0}


def test_write_summary_json_roundtrip(mixed_parquet, tmp_path):
    summary = summarize_geoparquet(mixed_parquet)
    out = tmp_path / "nested" / "summary.json"
    write_summary_json(summary, out)
    assert json.loads(out.read_text(encoding="utf-8")) == summary


def test_accepts_path_as_string(mixed_parquet):
    assert summarize_geoparquet(str(mixed_parquet))["rows"] == 8


# --- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "<null>"),
        ("lake", "lake"),
        ([], "<empty-list>"),
        (["b", "a"], "a|b"),
        (("water",), "water"),
        (np.array(["y", "x"]), "x|y"),
        (3, "3"),
        (np.str_("pond"), "pond"),
    ],
)
def test_category_key_normalisation(value, expected):
    assert _category_key(value) == expected


def test_area_stats_empty():
    assert _area_stats([]) == {"count": 0}


def test_area_stats_buckets_are_contiguous():
    arr = [50.0, 500.0, 5e4, 5e6, 5e7]
    stats = _area_stats(arr)
    assert stats["count"] == 5
    assert stats["min"] == 50.0 and stats["max"] == 5e7
    assert stats["size_buckets"] == {
        "< 0.01 ha (100 m2)": 1,
        "0.01-1 ha": 1,
        "1-100 ha": 1,
        "100 ha-10 km2": 1,
        "> 10 km2": 1,
    }
