"""Offline tests for download / verification / provenance logic (P0-1).

No network access: every test either calls the pure helpers directly or
injects a fake fetcher. The parquet files created here live only in ``tmp_path``
and exist to exercise parsing logic — they are synthetic and are never part of
the real dataset under ``data/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip('pyarrow')  # before the optional imports

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from earthglyph.data.download import (  # noqa: E402
    FETCH_LABELS,
    FETCHERS,
    DownloadError,
    download_theme,
    parse_bbox,
    sha256_of,
    verify_parquet,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def write_fake_parquet(path: Path, n_rows: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "id": pa.array([f"synthetic-{i}" for i in range(n_rows)], type=pa.string()),
            "rows": pa.array([n_rows] * n_rows, type=pa.int64()),
        }
    )
    pq.write_table(table, path)
    return path


def make_fetch(n_rows: int, calls: list[tuple] | None = None):
    def _fetch(bbox, release, out_path, timeout_s):
        if calls is not None:
            calls.append((bbox, release, str(out_path), timeout_s))
        write_fake_parquet(Path(out_path), n_rows)

    return _fetch


# --- parse_bbox -------------------------------------------------------------


def test_parse_bbox_accepts_documented_order_and_spaces():
    assert parse_bbox("-90, 44, -89, 45") == (-90.0, 44.0, -89.0, 45.0)


@pytest.mark.parametrize(
    "text",
    [
        "-90,44,-89",                       # too few values
        "-90,44,-89,45,7",                  # too many values
        "-90,44,-89,abc",                   # not a number
        "-190,44,-89,45",                   # longitude out of range
        "-90,44,-89,95",                    # latitude out of range
        "-89,44,-90,45",                    # west >= east
        "-90,45,-89,44",                    # south >= north
        "",
    ],
)
def test_parse_bbox_rejects_bad_input(text: str):
    with pytest.raises(ValueError):
        parse_bbox(text)


# --- verify_parquet / sha256 -------------------------------------------------


def test_verify_parquet_returns_row_count(tmp_path):
    path = write_fake_parquet(tmp_path / "ok.parquet", 3)
    assert verify_parquet(path) == 3


def test_verify_parquet_zero_row_file(tmp_path):
    path = write_fake_parquet(tmp_path / "empty_rows.parquet", 0)
    assert verify_parquet(path) == 0


def test_verify_parquet_missing_file(tmp_path):
    with pytest.raises(DownloadError, match="missing"):
        verify_parquet(tmp_path / "nope.parquet")


def test_verify_parquet_zero_bytes(tmp_path):
    path = tmp_path / "zero.parquet"
    path.write_bytes(b"")
    with pytest.raises(DownloadError, match="empty"):
        verify_parquet(path)


def test_verify_parquet_truncated_or_garbage(tmp_path):
    path = tmp_path / "garbage.parquet"
    path.write_bytes(b"PAR1" + b"not really a parquet body")
    with pytest.raises(DownloadError, match="not a readable Parquet"):
        verify_parquet(path)


def test_sha256_of_known_vector(tmp_path):
    path = tmp_path / "abc.bin"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --- download_theme ----------------------------------------------------------


def test_download_theme_writes_file_and_provenance_sidecar(tmp_path):
    out = tmp_path / "synthetic_water.parquet"
    calls: list[tuple] = []
    bbox = (-90.0, 44.0, -89.0, 45.0)

    meta = download_theme(
        bbox, out, release="2026-10-21.0", fetch=make_fetch(5, calls), force=True
    )

    assert calls == [(bbox, "2026-10-21.0", str(out), 900)]
    assert verify_parquet(out) == 5
    sidecar = Path(str(out) + ".meta.json")
    assert sidecar.is_file()
    on_disk = json.loads(sidecar.read_text(encoding="utf-8"))
    assert on_disk == meta
    assert meta["release"] == "2026-10-21.0"
    assert meta["source"].startswith("Overture Maps")
    assert meta["format"] == "geoparquet"
    assert meta["fetch_method"] == "custom"
    assert meta["bbox"] == {
        "min_lon": -90.0,
        "min_lat": 44.0,
        "max_lon": -89.0,
        "max_lat": 45.0,
        "order": "min_lon,min_lat,max_lon,max_lat",
    }
    assert meta["file"]["rows"] == 5
    assert meta["file"]["size_bytes"] == out.stat().st_size
    assert meta["file"]["sha256"] == sha256_of(out)
    assert "2026-10-21.0" in meta["reproduce"]
    assert "T" in meta["download_time_utc"]


def test_download_theme_reuses_valid_existing_file_without_refetching(tmp_path):
    out = tmp_path / "synthetic_water.parquet"
    calls: list[tuple] = []
    fetch = make_fetch(4, calls)

    def never_called(bbox, release, out_path, timeout_s):  # pragma: no cover - guard
        raise AssertionError("must not re-fetch a valid existing file")

    download_theme((-90, 44, -89, 45), out, release="r1", fetch=fetch)
    assert len(calls) == 1
    meta = download_theme((-90, 44, -89, 45), out, release="r1", fetch=never_called)

    assert meta["note"] == "reused existing file"
    assert meta["file"]["rows"] == 4
    assert meta["release"] == "r1"
    assert meta["file"]["sha256"] == sha256_of(out)


def test_download_theme_force_refetches(tmp_path):
    out = tmp_path / "synthetic_water.parquet"
    calls: list[tuple] = []
    fetch = make_fetch(2, calls)

    download_theme((-90, 44, -89, 45), out, release="r1", fetch=fetch)
    download_theme((-90, 44, -89, 45), out, release="r1", fetch=fetch, force=True)

    assert len(calls) == 2
    assert "note" not in json.loads(Path(str(out) + ".meta.json").read_text(encoding="utf-8"))


def test_download_theme_redownloads_zero_row_existing_file(tmp_path):
    out = tmp_path / "synthetic_water.parquet"
    write_fake_parquet(out, 0)
    calls: list[tuple] = []

    meta = download_theme(
        (-90, 44, -89, 45), out, release="r1", fetch=make_fetch(6, calls)
    )

    assert len(calls) == 1
    assert meta["file"]["rows"] == 6
    assert "note" not in meta


def test_download_theme_redownloads_corrupt_existing_file(tmp_path):
    out = tmp_path / "synthetic_water.parquet"
    out.write_bytes(b"PAR1 garbage")
    calls: list[tuple] = []

    meta = download_theme(
        (-90, 44, -89, 45), out, release="r1", fetch=make_fetch(3, calls)
    )

    assert len(calls) == 1
    assert meta["file"]["rows"] == 3


def test_download_theme_propagates_fetch_failure_without_sidecar(tmp_path):
    out = tmp_path / "synthetic_water.parquet"

    def failing_fetch(bbox, release, out_path, timeout_s):
        raise DownloadError("network unreachable (synthetic)")

    with pytest.raises(DownloadError, match="synthetic"):
        download_theme((-90, 44, -89, 45), out, release="r1", fetch=failing_fetch)

    assert not out.exists()
    assert not Path(str(out) + ".meta.json").exists()


def test_download_theme_creates_missing_parent_directory(tmp_path):
    out = tmp_path / "nested" / "deeper" / "synthetic_water.parquet"

    meta = download_theme((-90, 44, -89, 45), out, release="r1", fetch=make_fetch(1))

    assert out.is_file()
    assert meta["file"]["path"] == str(out)


def test_download_theme_rejects_unknown_fetch_method(tmp_path):
    with pytest.raises(ValueError, match="unknown fetch_method"):
        download_theme(
            (-90, 44, -89, 45),
            tmp_path / "x.parquet",
            release="r1",
            fetch_method="ftp",
        )


def test_download_theme_refuses_non_water_theme(tmp_path):
    """Both fetchers read the water collection: labelling other themes would
    produce false provenance."""
    with pytest.raises(NotImplementedError, match="water theme"):
        download_theme(
            (-90, 44, -89, 45),
            tmp_path / "x.parquet",
            release="r1",
            theme="land",
            fetch=make_fetch(1),
        )


def test_cli_fetch_method_choices_match_library_fetchers():
    """scripts/download_data.py --fetch-method must not drift from FETCHERS."""
    assert set(FETCHERS) == {"duckdb", "cli"}
    assert FETCH_LABELS.keys() == FETCHERS.keys()
    assert FETCH_LABELS["duckdb"] == "duckdb-httpfs"


def test_cli_passes_arguments_through_to_download_theme(tmp_path, monkeypatch):
    import download_data

    captured: dict = {}

    def fake_download_theme(bbox, out_path, **kwargs):
        captured["bbox"] = bbox
        captured.update(kwargs)
        return {
            "release": "r1",
            "fetch_method": FETCH_LABELS[kwargs["fetch_method"]],
            "file": {"rows": 7, "path": str(out_path), "size_bytes": 1, "sha256": "abc"},
        }

    monkeypatch.setattr(download_data, "download_theme", fake_download_theme)
    out = tmp_path / "o.parquet"

    rc = download_data.main(
        [f"--bbox={-90},44,-89,45", f"--output={out}", "--release", "r1",
         "--fetch-method", "cli", "--timeout", "30"]
    )

    assert rc == 0
    assert captured["bbox"] == (-90.0, 44.0, -89.0, 45.0)
    assert captured["fetch_method"] == "cli"
    assert captured["release"] == "r1"
    assert captured["theme"] == "water"
    assert captured["timeout_s"] == 30
    assert captured["force"] is False


def test_cli_reports_invalid_bbox_without_downloading(tmp_path):
    import download_data

    assert download_data.main(["--bbox=-90,44,-89", f"--output={tmp_path}/o.parquet"]) == 2


def test_cli_reports_unsupported_theme_as_failure(tmp_path, monkeypatch):
    import download_data

    def raise_not_implemented(bbox, out_path, **kwargs):
        raise NotImplementedError("only the water theme is implemented")

    monkeypatch.setattr(download_data, "download_theme", raise_not_implemented)
    rc = download_data.main(["--bbox=-90,44,-89,45", f"--output={tmp_path}/o.parquet",
                             "--theme=land"])
    assert rc == 1
