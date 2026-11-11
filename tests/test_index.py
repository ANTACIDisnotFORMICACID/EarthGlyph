"""Tests for the P0-3 feature index (Task D + Task G).

Two layers, deliberately separated:

* the synthetic layer exercises the build contract on ``tmp_path`` data — row
  alignment, uniqueness, the consistency checks, determinism, refusal to write
  over an existing index, and every anomaly :func:`check_index` must catch;
* the real-data layer (skipped when the dataset is absent) reads the shipped
  ``data/index`` back and proves it is derived from the released Overture
  extract: every ``candidate_id`` resolves to a real source feature id, the
  recorded digests match the files on disk, and nothing synthetic is indexed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip('geopandas')  # before the optional imports

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402

from earthglyph.data.download import sha256_of  # noqa: E402
from earthglyph.data.index import (  # noqa: E402
    FEATURES_FILENAME,
    INDEX_SCHEMA_VERSION,
    METADATA_FILENAME,
    SHAPES_FILENAME,
    BuiltIndex,
    IndexConfig,
    batch_fourier_descriptors,
    build_index,
    check_index,
    compute_features,
    feature_groups,
    load_index,
)
from earthglyph.data.prepare import (  # noqa: E402
    CONTOURS_FILENAME,
    MANIFEST_FILENAME,
    prepare_contours,
    stack_contours,
)
from earthglyph.features.fourier import DEFAULT_FOLD, DEFAULT_N_HARMONICS  # noqa: E402
from earthglyph.features.geometry_stats import FEATURE_COLUMNS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REAL_RAW = REPO / "data" / "raw" / "wisconsin_water.parquet"
REAL_CONTOURS = REPO / "data" / "processed" / CONTOURS_FILENAME
REAL_MANIFEST = REPO / "data" / "processed" / MANIFEST_FILENAME
REAL_INDEX = REPO / "data" / "index"
REAL_INDEX_MANIFEST = REAL_INDEX / MANIFEST_FILENAME

requires_real_data = pytest.mark.skipif(
    not (
        REAL_RAW.is_file()
        and REAL_CONTOURS.is_file()
        and REAL_MANIFEST.is_file()
        and (REAL_INDEX / FEATURES_FILENAME).is_file()
    ),
    reason="real extract / built index not present on this machine",
)

LON0, LAT0 = -89.5, 44.5
FRAME = (LON0 - 0.05, LAT0 - 0.05, LON0 + 0.05, LAT0 + 0.05)


def _box(lon: float, lat: float, width: float = 0.01, height: float = 0.008) -> shapely.Polygon:
    return shapely.Polygon(
        [
            (lon, lat),
            (lon + width, lat),
            (lon + width, lat + height),
            (lon, lat + height),
        ]
    )


def _row(identifier: str, subtype: str, geometry: Any) -> dict[str, Any]:
    return {"id": identifier, "subtype": subtype, "class": subtype, "geometry": geometry}


def _rows() -> list[dict[str, Any]]:
    return [
        _row("a-lake", "lake", _box(LON0, LAT0)),
        _row(
            "b-pond",
            "pond",
            shapely.buffer(shapely.Point(LON0 + 0.02, LAT0 + 0.02), 0.004, quad_segs=16),
        ),
        # Multipart source: two parts, so two candidates from one feature.
        _row(
            "c-multi",
            "lake",
            shapely.union_all([_box(LON0 - 0.03, LAT0 - 0.03), _box(LON0 + 0.03, LAT0 + 0.02)]),
        ),
        _row("d-reservoir", "reservoir", _box(LON0 - 0.04, LAT0 + 0.03, 0.03, 0.02)),
        # Below the subtype allowlist -> must not reach the index.
        _row("e-river", "river", _box(LON0 + 0.01, LAT0 - 0.04)),
        # Below the minimum area -> rejected, counted, not indexed.
        _row("f-tiny", "pond", _box(LON0 - 0.01, LAT0 + 0.001, 1e-5, 1e-5)),
    ]


def _prepare(tmp: Path) -> Path:
    rows = _rows()
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
    raw = tmp / "synthetic.parquet"
    frame.to_parquet(raw)
    (tmp / (raw.name + ".meta.json")).write_text(
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
    processed = tmp / "processed"
    prepare_contours(raw, processed, force=True)
    return processed


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[BuiltIndex, Path]:
    tmp = tmp_path_factory.mktemp("index")
    processed = _prepare(tmp)
    index = build_index(processed / CONTOURS_FILENAME, tmp / "index", force=True)
    return index, tmp


# --- counts and identity ----------------------------------------------------


def test_index_row_counts_agree(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    n = index.n_candidates
    assert n > 0
    assert index.features.shape[0] == n
    assert index.shapes.shape[0] == n
    assert len(index.metadata) == n
    assert index.manifest["counts"]["indexed_candidates"] == n
    assert index.manifest["counts"]["skipped_candidates"] == 0


def test_only_allowlisted_features_are_indexed(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    ids = set(index.metadata["source_id"])
    assert "e-river" not in ids  # subtype outside the allowlist
    assert "f-tiny" not in ids  # below the minimum area


def test_multipart_source_yields_part_ids(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    parts = index.metadata[index.metadata["source_id"] == "c-multi"]
    assert len(parts) == 2
    assert set(parts["candidate_id"]) == {"c-multi-part0", "c-multi-part1"}


def test_candidate_ids_are_unique(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    ids = index.metadata["candidate_id"]
    assert ids.is_unique


def test_feature_row_column_records_the_contract(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    assert index.metadata["feature_row"].to_numpy().tolist() == list(range(index.n_candidates))


def test_metadata_matches_the_contour_row_order(built: tuple[BuiltIndex, Path]) -> None:
    index, tmp = built
    contours = pd.read_parquet(tmp / "processed" / CONTOURS_FILENAME)
    assert index.metadata["candidate_id"].tolist() == contours["candidate_id"].tolist()


# --- feature content --------------------------------------------------------


def test_feature_layout_is_fourier_then_geometry(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    groups = index.groups
    assert groups["fourier"]["offset"] == 0
    assert groups["fourier"]["width"] == DEFAULT_N_HARMONICS
    assert groups["geometry"]["offset"] == DEFAULT_N_HARMONICS
    assert groups["geometry"]["columns"] == list(FEATURE_COLUMNS)
    assert index.features.shape[1] == DEFAULT_N_HARMONICS + len(FEATURE_COLUMNS)
    assert index.manifest["index"]["feature_dim"] == index.features.shape[1]


def test_stored_features_are_reproducible_from_stored_shapes(
    built: tuple[BuiltIndex, Path]
) -> None:
    """The index must be re-derivable from the contours it ships with."""
    index, _ = built
    shapes = index.shapes.astype(np.float64)
    recomputed = np.concatenate(
        [
            batch_fourier_descriptors(shapes, DEFAULT_N_HARMONICS, fold=DEFAULT_FOLD),
            np.stack([_geometry_row(row) for row in shapes]),
        ],
        axis=1,
    )
    stored = index.features.astype(np.float64)
    assert np.allclose(recomputed, stored, rtol=2e-5, atol=2e-6)


def _geometry_row(points: np.ndarray) -> np.ndarray:
    from earthglyph.features.geometry_stats import shape_metrics

    return shape_metrics(points).as_vector()


def test_features_are_finite_and_non_negative_where_required(
    built: tuple[BuiltIndex, Path]
) -> None:
    index, _ = built
    assert np.all(np.isfinite(index.features))
    assert np.all(index.features[:, :DEFAULT_N_HARMONICS] >= 0.0)


def test_shapes_are_unit_rms_and_centred(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    shapes = index.shapes.astype(np.float64)
    assert np.max(np.abs(shapes.mean(axis=1))) < 1e-3
    centered = shapes - shapes.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(np.sum(centered**2, axis=2), axis=1))
    assert np.max(np.abs(rms - 1.0)) < 1e-3


def test_storage_dtype_is_float32_for_browser_download(
    built: tuple[BuiltIndex, Path]
) -> None:
    index, _ = built
    assert index.features.dtype == np.dtype("float32")
    assert index.shapes.dtype == np.dtype("float32")
    assert index.manifest["index"]["storage"]["dtype"] == "float32"


def test_round_trip_error_is_measured_and_small(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    round_trip = index.manifest["index"]["round_trip"]
    assert round_trip["features_max_abs_round_trip_error"] < 1e-5
    assert round_trip["shapes_max_abs_round_trip_error"] < 1e-5


# --- files, reload, determinism --------------------------------------------


def test_files_are_written_and_reload_identically(
    built: tuple[BuiltIndex, Path]
) -> None:
    index, tmp = built
    directory = tmp / "index"
    for name in (FEATURES_FILENAME, SHAPES_FILENAME, METADATA_FILENAME, MANIFEST_FILENAME):
        assert (directory / name).is_file()
    features, shapes, metadata = load_index(directory)
    assert np.array_equal(features, index.features)
    assert np.array_equal(shapes, index.shapes)
    assert metadata["candidate_id"].tolist() == index.metadata["candidate_id"].tolist()


def test_manifest_records_output_checksums(built: tuple[BuiltIndex, Path]) -> None:
    index, tmp = built
    output = index.manifest["output"]
    for name in ("features", "shapes", "metadata"):
        entry = output[name]
        path = tmp / "index" / Path(entry["path"]).name
        assert entry["sha256"] == sha256_of(path)
        assert entry["size_bytes"] == path.stat().st_size


def test_rebuild_is_byte_identical(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    contours = processed / CONTOURS_FILENAME
    first = build_index(contours, tmp_path / "index_a", force=True)
    second = build_index(contours, tmp_path / "index_b", force=True)
    for name in (FEATURES_FILENAME, SHAPES_FILENAME):
        assert sha256_of(tmp_path / "index_a" / name) == sha256_of(tmp_path / "index_b" / name)
    assert sha256_of(tmp_path / "index_a" / METADATA_FILENAME) == sha256_of(
        tmp_path / "index_b" / METADATA_FILENAME
    )
    assert first.manifest["config"] == second.manifest["config"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    target = tmp_path / "index_dry"
    index = build_index(processed / CONTOURS_FILENAME, target, force=True, write_files=False)
    assert index.n_candidates > 0
    assert not target.exists()


def test_build_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    build_index(processed / CONTOURS_FILENAME, tmp_path / "index", force=True)
    with pytest.raises(FileExistsError, match="--force"):
        build_index(processed / CONTOURS_FILENAME, tmp_path / "index")


def test_build_requires_the_source_manifest(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    (processed / MANIFEST_FILENAME).unlink()
    with pytest.raises(FileNotFoundError, match="manifest"):
        build_index(processed / CONTOURS_FILENAME, tmp_path / "index", force=True)


def test_out_dir_must_differ_from_the_dataset(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    with pytest.raises(ValueError, match="out-dir"):
        build_index(processed / CONTOURS_FILENAME, processed, force=True)


def test_missing_contours_dataset_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="contours"):
        build_index(tmp_path / "nope.parquet", tmp_path / "index")


# --- configuration guards --------------------------------------------------


def test_nyquist_limit_is_enforced_before_any_write(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    with pytest.raises(ValueError, match="Nyquist"):
        build_index(
            processed / CONTOURS_FILENAME,
            tmp_path / "index",
            config=IndexConfig(n_harmonics=64),
            force=True,
        )
    assert not (tmp_path / "index" / FEATURES_FILENAME).exists()


def test_index_config_validates_its_fields() -> None:
    with pytest.raises(ValueError, match="n_harmonics"):
        IndexConfig(n_harmonics=0)
    with pytest.raises(ValueError, match="fold"):
        IndexConfig(fold="mirror")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="dtype"):
        IndexConfig(dtype="float16")
    columns, groups = feature_groups(IndexConfig(n_harmonics=12), 128)
    assert len(columns) == 16
    assert groups["fourier"]["width"] == 12


def test_float64_configuration_is_stored(tmp_path: Path) -> None:
    processed = _prepare(tmp_path)
    index = build_index(
        processed / CONTOURS_FILENAME,
        tmp_path / "index",
        config=IndexConfig(dtype="float64"),
        force=True,
    )
    assert index.features.dtype == np.dtype("float64")
    assert index.manifest["index"]["round_trip"]["features_max_abs_round_trip_error"] == 0.0


# --- consistency checker --------------------------------------------------


def _tamper_cases(index: BuiltIndex) -> list[tuple[str, Any, Any, Any, Any, str]]:
    features = index.features.copy()
    shapes = index.shapes.copy()
    metadata = index.metadata.copy()
    groups = index.groups
    return [
        ("count mismatch", features[:-1], shapes, metadata, groups, "candidate count mismatch"),
        (
            "duplicate id",
            features,
            shapes,
            _dup_metadata(metadata),
            groups,
            "duplicate candidate_id",
        ),
        ("row order broken", features, shapes, _scrambled_rows(metadata), groups, "feature_row"),
        ("non-finite feature", _poison(features, 0, 0), shapes, metadata, groups, "non-finite"),
        ("non-finite shape", features, _poison(shapes, 0, 0, 0), metadata, groups, "non-finite"),
        ("wrong dtype", features.astype(np.float64), shapes, metadata, groups, "dtype"),
        ("wrong feature width", features[:, :-1], shapes, metadata, groups, "columns"),
        ("group width", features, shapes, metadata, _bad_groups(groups), "group widths sum"),
        ("not centred", features, _shift(shapes), metadata, groups, "centroid-centered"),
        ("lon out of range", features, shapes, _bad_lon(metadata), groups, "longitude"),
        ("lat out of range", features, shapes, _bad_lat(metadata), groups, "latitude"),
        ("area not positive", features, shapes, _bad_area(metadata), groups, "area_m2"),
        ("n_points mismatch", features, shapes, _bad_npoints(metadata), groups, "n_points"),
    ]


def _dup_metadata(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out.loc[out.index[1], "candidate_id"] = out.loc[out.index[0], "candidate_id"]
    return out


def _scrambled_rows(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out["feature_row"] = out["feature_row"][::-1].to_numpy()
    return out


def _poison(array: np.ndarray, *index: int) -> np.ndarray:
    out = array.copy()
    out[index] = np.nan
    return out


def _bad_groups(groups: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(groups))
    out["geometry"]["width"] += 1
    return out


def _shift(shapes: np.ndarray) -> np.ndarray:
    out = shapes.copy()
    out += np.float32(5.0)
    return out


def _bad_lon(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out.loc[out.index[0], "lon"] = 190.0
    return out


def _bad_lat(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out.loc[out.index[0], "lat"] = -95.0
    return out


def _bad_area(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out.loc[out.index[0], "area_m2"] = 0.0
    return out


def _bad_npoints(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata.copy()
    out.loc[out.index[0], "n_points"] = 64
    return out


def test_check_index_catches_every_anomaly(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    config = IndexConfig()
    clean = check_index(
        index.features, index.shapes, index.metadata, index.columns, index.groups, config
    )
    assert clean == []
    for label, features, shapes, metadata, groups, expected in _tamper_cases(index):
        problems = check_index(features, shapes, metadata, index.columns, groups, config)
        assert problems, f"{label}: check_index stayed silent"
        matched = any(expected in problem for problem in problems)
        assert matched, f"{label}: expected {expected!r} in {problems}"


def test_a_single_violation_fails_the_build(tmp_path: Path) -> None:
    """No partial index: a broken candidate aborts instead of shrinking the file."""
    processed = _prepare(tmp_path)
    contours = pd.read_parquet(processed / CONTOURS_FILENAME)
    broken = contours.copy()
    values = list(broken["contour"].iloc[0])
    values[0] = float("nan")
    broken.loc[broken.index[0], "contour"] = values
    broken.to_parquet(processed / CONTOURS_FILENAME)
    with pytest.raises((ValueError, Exception), match="non-finite|failed"):
        build_index(processed / CONTOURS_FILENAME, tmp_path / "index", force=True)


# --- manifest completeness -------------------------------------------------


def test_manifest_carries_the_p0_3_required_fields(built: tuple[BuiltIndex, Path]) -> None:
    index, _ = built
    manifest = index.manifest
    assert manifest["manifest_schema_version"] == INDEX_SCHEMA_VERSION
    assert manifest["deterministic"] is True
    assert manifest["random_seed"] is None
    assert manifest["not_a_retrieval_result"] is True
    assert "P0-3" in manifest["stage"]
    source = manifest["source"]
    assert source["contours_sha256"]
    assert source["source_release"]
    assert source["source_bbox"]["min_lon"] < source["source_bbox"]["max_lon"]
    assert manifest["index"]["feature_columns"] == index.columns
    assert manifest["index"]["row_order_contract"]
    assert manifest["features"]["fourier"]["n_harmonics"] == DEFAULT_N_HARMONICS
    assert manifest["features"]["geometry"]["columns"] == list(FEATURE_COLUMNS)
    assert manifest["features"]["fusion"] == "none - the two groups are concatenated, not weighted"
    assert manifest["build"]["command"].startswith("python scripts/build_index.py")
    assert manifest["environment"]["numpy"]
    assert "git_commit" in manifest
    assert manifest["traceability"]["geolocation"]


# --- real data -------------------------------------------------------------


@requires_real_data
def test_real_index_matches_the_released_contours() -> None:
    features, shapes, metadata = load_index(REAL_INDEX)
    contours = pd.read_parquet(REAL_CONTOURS)
    assert len(features) == len(shapes) == len(metadata) == len(contours)
    assert metadata["candidate_id"].tolist() == contours["candidate_id"].tolist()
    assert np.all(np.isfinite(features)) and np.all(np.isfinite(shapes))


@requires_real_data
def test_real_index_provenance_resolves_to_source_features() -> None:
    _, _, metadata = load_index(REAL_INDEX)
    raw_ids = {str(value) for value in pd.read_parquet(REAL_RAW, columns=["id"])["id"]}
    source_ids = {str(value) for value in metadata["source_id"]}
    assert source_ids <= raw_ids
    manifest = json.loads(REAL_INDEX_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["source"]["contours_sha256"] == sha256_of(REAL_CONTOURS)
    assert manifest["source"]["source_input_sha256"] == sha256_of(REAL_RAW)
    assert manifest["source"]["source_release"] == "2026-10-21.0"


@requires_real_data
def test_real_index_holds_no_synthetic_candidates() -> None:
    manifest = json.loads(REAL_INDEX_MANIFEST.read_text(encoding="utf-8"))
    _, shapes, metadata = load_index(REAL_INDEX)
    assert "fixture" not in json.dumps(manifest["source"], ensure_ascii=False)
    assert manifest["source"]["source_name"].startswith("Overture")
    assert manifest["query_sketch_used"] is False
    assert manifest["counts"]["skipped_candidates"] == 0
    assert len(metadata) == manifest["counts"]["input_candidates"] == 839
    assert shapes.shape == (839, 128, 2)


@requires_real_data
def test_real_index_passes_the_consistency_check() -> None:
    manifest = json.loads(REAL_INDEX_MANIFEST.read_text(encoding="utf-8"))
    features, shapes, metadata = load_index(REAL_INDEX)
    columns = manifest["index"]["feature_columns"]
    groups = manifest["index"]["feature_groups"]
    config = IndexConfig(
        n_harmonics=int(manifest["config"]["n_harmonics"]),
        fold=manifest["config"]["fourier_fold"],
        dtype=manifest["config"]["storage_dtype"],
    )
    assert check_index(features, shapes, metadata, columns, groups, config) == []


@requires_real_data
def test_real_contours_rebuild_the_stored_features() -> None:
    """The shipped descriptor is re-derived from the shipped contours, independently."""
    manifest = json.loads(REAL_INDEX_MANIFEST.read_text(encoding="utf-8"))
    n_harmonics = int(manifest["config"]["n_harmonics"])
    fold = manifest["config"]["fourier_fold"]
    contours = pd.read_parquet(REAL_CONTOURS)
    shapes = stack_contours(contours)
    features, _, _, _ = compute_features(
        contours, shapes, IndexConfig(n_harmonics=n_harmonics, fold=fold)
    )
    stored = np.load(REAL_INDEX / FEATURES_FILENAME).astype(np.float64)
    assert np.allclose(features, stored, rtol=1e-5, atol=1e-6)
