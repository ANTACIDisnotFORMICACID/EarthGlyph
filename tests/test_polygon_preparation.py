"""End-to-end tests for the P0-2 polygon preparation pipeline.

Synthetic fixtures below are unit-test inputs written to ``tmp_path`` only. The
real Overture extract is checked separately, including a guard that the shipped
``data/processed/contours.parquet`` holds nothing but real source features.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from earthglyph.data.download import sha256_of
from earthglyph.data.prepare import (
    CONTOURS_FILENAME,
    MANIFEST_FILENAME,
    PENDING_FILENAME,
    REJECTED_FILENAME,
    content_digest,
    load_contours,
    prepare_contours,
    stack_contours,
)
from earthglyph.geometry import (
    AeqdProjection,
    FilterConfig,
    build_contour,
    exterior_ring,
)

REPO = Path(__file__).resolve().parents[1]
REAL_RAW = REPO / "data" / "raw" / "wisconsin_water.parquet"
REAL_CONTOURS = REPO / "data" / "processed" / CONTOURS_FILENAME
REAL_MANIFEST = REPO / "data" / "processed" / MANIFEST_FILENAME

FRAME = (-90.0, 44.0, -89.0, 45.0)
LON0, LAT0 = -89.5, 44.5

requires_real_data = pytest.mark.skipif(
    not (REAL_RAW.is_file() and REAL_CONTOURS.is_file() and REAL_MANIFEST.is_file()),
    reason="real Overture extract / built dataset not present",
)


def _box(lon: float, lat: float, dlon: float, dlat: float) -> Any:
    return shapely.box(lon, lat, lon + dlon, lat + dlat)


def _row(feature_id: str, subtype: str | None, klass: str, geometry: Any) -> dict[str, Any]:
    return {"id": feature_id, "subtype": subtype, "class": klass, "geometry": geometry}


def _synthetic_rows() -> list[dict[str, Any]]:
    """One feature per rejection path plus the accepted shapes.

    These are fabricated geometries used to exercise the pipeline; they must
    never reach ``data/processed``.
    """
    bowtie = shapely.Polygon(
        [
            (LON0 - 0.3, LAT0 - 0.3),
            (LON0 - 0.29, LAT0 - 0.29),
            (LON0 - 0.29, LAT0 - 0.3),
            (LON0 - 0.3, LAT0 - 0.29),
        ]
    )
    holed = shapely.Polygon(
        [
            (LON0 + 0.30, LAT0 - 0.30),
            (LON0 + 0.32, LAT0 - 0.30),
            (LON0 + 0.32, LAT0 - 0.28),
            (LON0 + 0.30, LAT0 - 0.28),
        ],
        holes=[
            [
                (LON0 + 0.305, LAT0 - 0.295),
                (LON0 + 0.315, LAT0 - 0.295),
                (LON0 + 0.315, LAT0 - 0.285),
                (LON0 + 0.305, LAT0 - 0.285),
            ]
        ],
    )
    return [
        _row("lake-ok", "lake", "lake", _box(LON0, LAT0, 0.01, 0.01)),
        _row("pond-tiny", "pond", "pond", _box(LON0 + 0.1, LAT0, 0.0002, 0.0002)),
        _row(
            "river-line",
            "river",
            "river",
            shapely.LineString([(LON0, LAT0), (LON0 + 0.05, LAT0 + 0.05)]),
        ),
        _row("spring-point", "water", "water", shapely.Point(LON0, LAT0 + 0.2)),
        _row("stream-poly", "stream", "stream", _box(LON0 - 0.1, LAT0 + 0.1, 0.004, 0.004)),
        _row("untyped-poly", None, "water", _box(LON0 - 0.1, LAT0 + 0.2, 0.004, 0.004)),
        _row(
            "multi-lake",
            "lake",
            "lake",
            shapely.MultiPolygon(
                [
                    _box(LON0 + 0.2, LAT0 + 0.2, 0.006, 0.006),
                    _box(LON0 + 0.25, LAT0 + 0.25, 0.004, 0.004),
                ]
            ),
        ),
        _row("bowtie-lake", "lake", "lake", bowtie),
        _row("holed-lake", "lake", "lake", holed),
        _row("dup-a", "pond", "pond", _box(LON0 - 0.2, LAT0 + 0.3, 0.005, 0.005)),
        _row("dup-b", "pond", "pond", _box(LON0 - 0.2 + 1e-5, LAT0 + 0.3, 0.00495, 0.00495)),
        _row(
            "collapsed-lake",
            "lake",
            "lake",
            shapely.Polygon(
                [(LON0, LAT0 + 0.4), (LON0 + 0.01, LAT0 + 0.4), (LON0 + 0.02, LAT0 + 0.4)]
            ),
        ),
        _row("empty-poly", "lake", "lake", shapely.Polygon()),
        _row("null-geom", "pond", "pond", None),
        _row("frame-lake", "lake", "lake", _box(FRAME[0] - 0.004, LAT0, 0.008, 0.008)),
    ]


def _write_synthetic(tmp_path: Path, name: str = "synthetic.parquet") -> Path:
    rows = _synthetic_rows()
    frame = gpd.GeoDataFrame(
        {
            "id": [r["id"] for r in rows],
            "subtype": pd.array([r["subtype"] for r in rows], dtype="string"),
            "class": pd.array([r["class"] for r in rows], dtype="string"),
            "is_intermittent": pd.array([None] * len(rows), dtype="boolean"),
            "is_salt": pd.array([False] * len(rows), dtype="boolean"),
            "geometry": [r["geometry"] for r in rows],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / name
    frame.to_parquet(path)
    (tmp_path / (name + ".meta.json")).write_text(
        json.dumps(
            {
                "source": "synthetic unit-test fixture (not real geography)",
                "dataset": "test fixture",
                "release": "test",
                "download_time_utc": "2026-11-05T00:00:00+00:00",
                "fetch_method": "fixture",
                "bbox": {
                    "min_lon": FRAME[0],
                    "min_lat": FRAME[1],
                    "max_lon": FRAME[2],
                    "max_lat": FRAME[3],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def synthetic_build(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("synthetic")
    raw = _write_synthetic(tmp)
    out = tmp / "processed"
    result = prepare_contours(raw, out, force=True)
    return raw, result.manifest


def _records(manifest_dir: Path) -> pd.DataFrame:
    return load_contours(manifest_dir / CONTOURS_FILENAME)


# --- counts and rejection accounting ----------------------------------------


def test_raw_rows_are_fully_accounted_for(synthetic_build: tuple[Path, dict[str, Any]]) -> None:
    _, manifest = synthetic_build
    counts = manifest["counts"]
    assert counts["raw_rows"] == len(_synthetic_rows()) == 15
    assert (
        counts["rejected_features_total"] + counts["features_reaching_part_stage"]
        == counts["raw_rows"]
    )
    assert (
        counts["candidates"] + sum(counts["rejected_parts_by_reason"].values())
        == counts["parts_extracted"]
    )
    types = counts["raw_geometry_types"]
    assert types["Polygon"] == 11
    assert types["MultiPolygon"] == 1
    assert types["LineString"] == 1
    assert types["Point"] == 1
    assert sum(types.values()) == counts["raw_rows"]


def test_every_rejection_reason_is_counted(synthetic_build: tuple[Path, dict[str, Any]]) -> None:
    _, manifest = synthetic_build
    counts = manifest["counts"]
    assert counts["rejected_features_by_reason"] == {
        "wrong_type": 2,
        "subtype_not_in_allowlist": 2,
        "unrepairable_after_make_valid": 1,
        "empty_geometry": 1,
        "null_geometry": 1,
    }
    assert counts["rejected_parts_by_reason"] == {
        "area_below_minimum": 1,
        "duplicate_high_overlap": 1,
    }
    assert counts["parts_extracted"] == 10
    assert counts["candidates"] == 8
    assert counts["features_with_candidates"] == 6
    assert counts["candidate_by_subtype"] == {"lake": 7, "pond": 1}
    assert counts["candidates_repaired"] == 2
    assert counts["candidates_with_holes"] == 1
    assert counts["candidates_from_multipolygon"] == 4
    assert counts["candidates_extending_beyond_frame"] == 1


# --- traceability and record contents ---------------------------------------


def test_candidate_ids_link_back_to_source_features(
    synthetic_build: tuple[Path, dict[str, Any]]
) -> None:
    _, manifest = synthetic_build
    df = _records(Path(manifest["output"]["contours"]["path"]).parent)
    assert df.candidate_id.is_unique
    single = df[df.n_parts == 1]
    assert set(single.candidate_id) == set(single.source_id)
    assert set(df[df.source_id == "multi-lake"].candidate_id) == {
        "multi-lake-part0",
        "multi-lake-part1",
    }
    assert sorted(df[df.source_id == "bowtie-lake"].part_index) == [0, 1]


def test_repair_is_recorded_with_reason_and_result_type(
    synthetic_build: tuple[Path, dict[str, Any]]
) -> None:
    _, manifest = synthetic_build
    df = _records(Path(manifest["output"]["contours"]["path"]).parent)
    repaired = df[df.source_id == "bowtie-lake"]
    assert len(repaired) == 2
    assert repaired.was_repaired.all()
    assert repaired.repair_reason.str.contains("Self-intersection").all()
    assert not df[df.source_id == "lake-ok"].was_repaired.any()


def test_records_are_traceable_to_lonlat_inside_the_source(
    synthetic_build: tuple[Path, dict[str, Any]]
) -> None:
    raw_path, manifest = synthetic_build
    df = _records(Path(manifest["output"]["contours"]["path"]).parent)
    raw = gpd.read_parquet(raw_path)
    source = {str(i): g for i, g in zip(raw["id"], raw.geometry, strict=True)}
    for row in df.itertuples():
        feature = source[row.source_id]
        part = (
            feature.geoms[row.part_index]
            if feature.geom_type == "MultiPolygon"
            else feature
        )
        assert part.contains(shapely.Point(row.lon, row.lat)), row.candidate_id
        assert feature.contains(shapely.Point(row.lon, row.lat)), row.candidate_id
        assert FRAME[0] - 0.01 <= row.lon <= FRAME[2] + 0.01
        assert row.area_m2 > 0.0
        assert row.n_points == 128
        assert len(row.contour) == 256
        assert row.subtype in {"lake", "pond", "reservoir"}


def test_holes_are_counted_but_not_merged_into_the_outline(
    synthetic_build: tuple[Path, dict[str, Any]]
) -> None:
    raw_path, manifest = synthetic_build
    df = _records(Path(manifest["output"]["contours"]["path"]).parent)
    row = df[df.source_id == "holed-lake"].iloc[0]
    assert row.n_holes == 1
    stored = np.asarray(row.contour, dtype=float).reshape(128, 2)

    raw = gpd.read_parquet(raw_path)
    geometry = raw.loc[raw["id"] == "holed-lake", "geometry"].iloc[0]
    proj = AeqdProjection(
        lon0=manifest["projection"]["lon0"], lat0=manifest["projection"]["lat0"]
    )
    exterior_only = build_contour(exterior_ring(proj.project_geometry(geometry)), 128)
    assert np.allclose(stored, exterior_only.points, atol=1e-12)
    # the outline is the exterior ring; filling the hole would change its area
    assert exterior_only.perimeter_m == pytest.approx(
        proj.project_geometry(geometry).exterior.length, rel=1e-6
    )


def test_contours_are_normalized_and_reconstructible(
    synthetic_build: tuple[Path, dict[str, Any]]
) -> None:
    _, manifest = synthetic_build
    out_dir = Path(manifest["output"]["contours"]["path"]).parent
    contours = stack_contours(_records(out_dir))
    assert contours.shape == (8, 128, 2)
    assert np.all(np.isfinite(contours))
    assert np.allclose(contours.mean(axis=1), 0.0, atol=1e-12)
    rms = np.sqrt(np.mean(np.sum(contours**2, axis=2), axis=1))
    assert np.allclose(rms, 1.0, atol=1e-12)


# --- parameters, not hardcoded laws -----------------------------------------


def test_minimum_area_is_a_parameter(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    strict = prepare_contours(
        raw, tmp_path / "strict", config=FilterConfig(min_area_m2=1000.0), force=True
    )
    relaxed = prepare_contours(
        raw, tmp_path / "relaxed", config=FilterConfig(min_area_m2=0.0), force=True
    )
    assert strict.manifest["counts"]["candidates"] == 8
    assert relaxed.manifest["counts"]["candidates"] == 9
    assert relaxed.manifest["counts"]["rejected_parts_by_reason"].get("area_below_minimum", 0) == 0
    assert relaxed.manifest["cleaning"]["min_area_m2"] == 0.0
    ids = set(_records(tmp_path / "relaxed").candidate_id)
    assert "pond-tiny" in ids


def test_subtype_allowlist_can_be_widened_or_disabled(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    widened_subtypes = ("lake", "pond", "reservoir", "stream")
    widened = prepare_contours(
        raw, tmp_path / "widened", config=FilterConfig(subtypes=widened_subtypes), force=True
    )
    everything = prepare_contours(
        raw, tmp_path / "all", config=FilterConfig(subtypes=None), force=True
    )
    assert "stream-poly" in set(_records(tmp_path / "widened").candidate_id)
    assert widened.manifest["counts"]["candidates"] == 9
    assert everything.manifest["cleaning"]["subtype_allowlist"] is None
    assert everything.manifest["output"]["pending_analysis"]["rows"] == 0
    assert "untyped-poly" in set(_records(tmp_path / "all").candidate_id)


def test_dedupe_can_be_disabled(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    deduped = prepare_contours(raw, tmp_path / "deduped", force=True)
    kept = prepare_contours(raw, tmp_path / "kept", config=FilterConfig(dedupe=False), force=True)
    assert deduped.manifest["counts"]["rejected_parts_by_reason"]["duplicate_high_overlap"] == 1
    assert "duplicate_high_overlap" not in kept.manifest["counts"]["rejected_parts_by_reason"]
    ids = set(_records(tmp_path / "kept").candidate_id)
    assert {"dup-a", "dup-b"} <= ids
    kept_ids = set(_records(tmp_path / "deduped").candidate_id)
    assert "dup-a" in kept_ids and "dup-b" not in kept_ids  # larger of the pair survives


def test_sampling_point_count_is_configurable(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    result = prepare_contours(raw, tmp_path / "out64", n_points=64, force=True)
    df = _records(tmp_path / "out64")
    assert set(df.n_points) == {64}
    assert set(len(c) for c in df.contour) == {128}
    assert stack_contours(df).shape == (len(df), 64, 2)
    assert result.manifest["normalization"]["n_points"] == 64


def test_frame_contact_is_a_flag_not_a_filter(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    prepare_contours(raw, tmp_path / "out", force=True)
    df = _records(tmp_path / "out")
    flagged = df[df.extends_beyond_frame]
    assert list(flagged.candidate_id) == ["frame-lake"]
    assert flagged.area_m2.iloc[0] > 0.0  # kept as a candidate, not deleted


# --- side outputs -----------------------------------------------------------


def test_pending_analysis_keeps_set_aside_features(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    manifest = prepare_contours(raw, tmp_path / "out", force=True).manifest
    pending = pd.read_parquet(tmp_path / "out" / PENDING_FILENAME)
    assert set(pending.source_id) == {"stream-poly", "untyped-poly"}
    assert pending.area_m2.gt(0).all()
    assert all(shapely.from_wkb(wkb).geom_type == "Polygon" for wkb in pending.wkb)
    assert manifest["output"]["pending_analysis"]["rows"] == 2
    candidates = set(_records(tmp_path / "out").source_id)
    assert candidates.isdisjoint(pending.source_id)


def test_rejected_samples_store_real_shapes_with_reasons(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    prepare_contours(raw, tmp_path / "out", force=True)
    rejected = pd.read_parquet(tmp_path / "out" / REJECTED_FILENAME)
    assert set(rejected.reason) == {
        "wrong_type",
        "subtype_not_in_allowlist",
        "unrepairable_after_make_valid",
        "empty_geometry",
        "null_geometry",
        "area_below_minimum",
        "duplicate_high_overlap",
    }
    tiny = rejected[rejected.reason == "area_below_minimum"].iloc[0]
    assert tiny.source_id == "pond-tiny"
    assert 0.0 < tiny.area_m2 < 1000.0
    assert shapely.from_wkb(tiny.wkb).geom_type == "Polygon"
    assert rejected[rejected.reason == "null_geometry"].wkb.isna().all()


def test_rejected_sample_limit_is_per_reason(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    manifest = prepare_contours(raw, tmp_path / "out", rejected_sample_limit=1, force=True).manifest
    saved = manifest["output"]["rejected_samples"]["saved_per_reason"]
    assert set(saved.values()) == {1}
    assert pd.read_parquet(tmp_path / "out" / REJECTED_FILENAME).shape[0] == len(saved)


# --- reproducibility and safety ---------------------------------------------


def test_rebuild_is_byte_identical(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    first = prepare_contours(raw, tmp_path / "a", force=True)
    second = prepare_contours(raw, tmp_path / "b", force=True)
    first_sha = first.manifest["output"]["contours"]["sha256"]
    second_sha = second.manifest["output"]["contours"]["sha256"]
    assert first_sha == second_sha
    assert (
        first.manifest["source"]["input_content_digest"]
        == second.manifest["source"]["input_content_digest"]
    )
    assert first.manifest["counts"] == second.manifest["counts"]
    assert first.manifest["projection"] == second.manifest["projection"]
    volatile = {"built_at_utc", "output", "manifest_path", "reproduce"}
    stable_a = {k: v for k, v in first.manifest.items() if k not in volatile}
    stable_b = {k: v for k, v in second.manifest.items() if k not in volatile}
    assert stable_a == stable_b


def test_content_digest_ignores_file_encoding(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    raw = _write_synthetic(tmp_path)
    copy_path = tmp_path / "copy.parquet"
    shutil.copy(raw.with_suffix(".parquet.meta.json"), copy_path.with_suffix(".parquet.meta.json"))
    table = pq.read_table(raw)
    pq.write_table(table, copy_path, compression="gzip")
    assert sha256_of(raw) != sha256_of(copy_path)
    first = prepare_contours(raw, tmp_path / "a", force=True).manifest
    second = prepare_contours(copy_path, tmp_path / "b", force=True).manifest
    assert (
        first["source"]["input_content_digest"]["value"]
        == second["source"]["input_content_digest"]["value"]
    )
    assert first["counts"] == second["counts"]
    assert first["source"]["input_sha256"] != second["source"]["input_sha256"]


def test_raw_input_is_never_modified(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    before = sha256_of(raw)
    mtime = raw.stat().st_mtime_ns
    prepare_contours(raw, tmp_path / "out", force=True)
    assert sha256_of(raw) == before
    assert raw.stat().st_mtime_ns == mtime


def test_existing_output_requires_force(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    out = tmp_path / "out"
    prepare_contours(raw, out)
    with pytest.raises(FileExistsError):
        prepare_contours(raw, out)
    prepare_contours(raw, out, force=True)


def test_missing_input_and_bad_parameters_raise(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        prepare_contours(tmp_path / "nope.parquet", tmp_path / "out")
    raw = _write_synthetic(tmp_path)
    with pytest.raises(ValueError):
        prepare_contours(raw, tmp_path / "out2", n_points=2)


def test_manifest_records_environment_and_command(tmp_path: Path) -> None:
    raw = _write_synthetic(tmp_path)
    manifest = prepare_contours(
        raw, tmp_path / "out", build_command="python scripts/prepare_polygons.py --force"
    ).manifest
    assert manifest["deterministic"] is True
    assert manifest["random_seed"] is None
    assert manifest["environment"]["python"]
    assert {"numpy", "shapely", "geos", "proj"} <= set(manifest["environment"])
    assert manifest["reproduce"]["command"] == "python scripts/prepare_polygons.py --force"
    assert manifest["normalization"]["rotation_alignment"].startswith("not applied")
    assert manifest["normalization"]["mirror"].startswith("not applied")
    assert manifest["stage"].startswith("P0-2")


# --- real data --------------------------------------------------------------


@requires_real_data
def test_real_dataset_is_traceable_to_overture() -> None:
    raw = gpd.read_parquet(REAL_RAW)
    df = load_contours(REAL_CONTOURS)
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    source = {str(i): g for i, g in zip(raw["id"], raw.geometry, strict=True)}
    assert df.candidate_id.is_unique
    assert set(df.source_id) <= set(source)
    assert set(df.subtype) <= set(manifest["cleaning"]["subtype_allowlist"])
    for row in df.itertuples():
        feature = source[row.source_id]
        assert 0 <= row.part_index < row.n_parts
        part = feature.geoms[row.part_index] if feature.geom_type == "MultiPolygon" else feature
        assert part.contains(shapely.Point(row.lon, row.lat)), row.candidate_id


@requires_real_data
def test_real_manifest_matches_the_shipped_files() -> None:
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    df = load_contours(REAL_CONTOURS)
    assert manifest["output"]["contours"]["rows"] == len(df)
    assert manifest["output"]["contours"]["sha256"] == sha256_of(REAL_CONTOURS)
    assert manifest["source"]["input_sha256"] == sha256_of(REAL_RAW)
    raw = gpd.read_parquet(REAL_RAW)
    digest = content_digest(list(raw["id"]), list(raw.geometry))
    assert digest == manifest["source"]["input_content_digest"]
    assert manifest["counts"]["raw_rows"] == len(raw)
    assert manifest["counts"]["candidates"] == len(df)
    assert set(df.n_points) == {manifest["normalization"]["n_points"]}


@requires_real_data
def test_real_contours_are_normalized_and_match_the_pipeline() -> None:
    raw = gpd.read_parquet(REAL_RAW)
    df = load_contours(REAL_CONTOURS)
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    proj = AeqdProjection(lon0=manifest["projection"]["lon0"], lat0=manifest["projection"]["lat0"])
    contours = stack_contours(df)
    assert contours.shape == (len(df), manifest["normalization"]["n_points"], 2)
    rms = np.sqrt(np.mean(np.sum(contours**2, axis=2), axis=1))
    assert np.allclose(rms, 1.0, atol=1e-9)
    assert np.allclose(contours.mean(axis=1), 0.0, atol=1e-9)

    source = {str(i): g for i, g in zip(raw["id"], raw.geometry, strict=True)}
    sample = df.sample(n=min(40, len(df)), random_state=20261105)
    for row in sample.itertuples():
        feature = source[row.source_id]
        part = feature.geoms[row.part_index] if feature.geom_type == "MultiPolygon" else feature
        metric = proj.project_geometry(part)
        assert metric.area == pytest.approx(row.area_m2, rel=1e-9)
        rebuilt = build_contour(exterior_ring(metric), int(row.n_points)).points
        assert np.allclose(rebuilt, np.asarray(row.contour, float).reshape(-1, 2), atol=1e-12)


@requires_real_data
def test_real_rebuild_reproduces_the_shipped_dataset(tmp_path: Path) -> None:
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    config = FilterConfig(
        subtypes=tuple(manifest["cleaning"]["subtype_allowlist"]),
        min_area_m2=manifest["cleaning"]["min_area_m2"],
        max_iou=manifest["cleaning"]["max_iou"],
        dedupe=manifest["cleaning"]["dedupe"],
    )
    result = prepare_contours(
        REAL_RAW,
        tmp_path / "rebuild",
        n_points=manifest["normalization"]["n_points"],
        config=config,
        force=True,
    )
    assert (
        result.manifest["output"]["contours"]["sha256"]
        == manifest["output"]["contours"]["sha256"]
    )
    assert result.manifest["counts"] == manifest["counts"]
    assert sha256_of(REAL_RAW) == manifest["source"]["input_sha256"]


@requires_real_data
def test_shipped_dataset_contains_no_synthetic_records() -> None:
    raw_ids = {str(i) for i in gpd.read_parquet(REAL_RAW)["id"]}
    df = load_contours(REAL_CONTOURS)
    assert set(df.source_id) <= raw_ids
    assert all(cid.split("-part")[0] in raw_ids for cid in df.candidate_id)
    assert df.source_id.str.len().gt(0).all()
