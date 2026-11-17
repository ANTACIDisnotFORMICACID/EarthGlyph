"""End-to-end tests for the P0-6 blind judgement sheet generator.

The generator had never been executed before the review round, so its first
real run is these tests. Each guardrail is tied to a concrete finding:

* the §7 pre-flight gate must pass before any material is generated — the
  generator refuses and writes nothing (review finding: gate was not enforced);
* one shuffled CSV per judge, each with its own fixed seed (protocol §5);
* the A-J option key must map every option to ONE consistent candidate: after
  the per-query random permutation, ``row`` and ``candidate_id`` come from the
  same index row — the exact pairing an earlier version got wrong;
* the per-query order seed arithmetic must actually parse ``PXX-qNN`` ids.

Fixtures are synthetic geometry and minimal coordinate lists; nothing here
fabricates human data.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("geopandas")  # before the optional imports

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_p06_queries as collect  # noqa: E402
import make_p06_judgement_sheets as sheets  # noqa: E402

from earthglyph.data.index import build_index, load_index  # noqa: E402
from earthglyph.data.prepare import CONTOURS_FILENAME, prepare_contours  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    load_index_handle,
    search_by_contour,
)

LON0, LAT0 = -89.5, 44.5
BOX_A = (LON0, LAT0, 0.010, 0.008)
BOX_B = (LON0 + 0.02, LAT0, 0.012, 0.006)
TEST_SEED = 20261117
REHEARSAL_GATE = ["--min-participants", "1", "--min-finals", "1"]


def _box(lon: float, lat: float, width: float, height: float) -> shapely.Polygon:
    return shapely.Polygon(
        [
            (lon, lat),
            (lon + width, lat),
            (lon + width, lat + height),
            (lon, lat + height),
        ]
    )


def _box_trace(lon: float, lat: float, width: float, height: float,
               per_side: int = 10) -> list[list[float]]:
    """A closed trace of the box perimeter, first point repeated at the end."""
    corners = [(lon, lat), (lon + width, lat), (lon + width, lat + height),
               (lon, lat + height)]
    points: list[list[float]] = []
    for index in range(4):
        x0, y0 = corners[index]
        x1, y1 = corners[(index + 1) % 4]
        for step in range(per_side):
            fraction = step / per_side
            points.append([x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction])
    points.append(points[0])
    return points


@pytest.fixture(scope="module")
def index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real four-candidate index on disk, built through the production chain."""
    tmp = tmp_path_factory.mktemp("p06_sheets")
    boxes = {
        "a-lake": BOX_A,
        "b-lake": BOX_B,
        "c-lake": (LON0, LAT0 + 0.02, 0.008, 0.010),
        "d-lake": (LON0 + 0.02, LAT0 + 0.02, 0.011, 0.009),
    }
    rows: list[dict[str, Any]] = [
        {"id": name, "subtype": "lake", "geometry": _box(*args)}
        for name, args in boxes.items()
    ]
    raw = tmp / "synthetic.parquet"
    gpd.GeoDataFrame(
        {
            "id": [row["id"] for row in rows],
            "subtype": pd.array([row["subtype"] for row in rows], dtype="string"),
            "class": pd.array([row["subtype"] for row in rows], dtype="string"),
            "is_intermittent": pd.array([None] * len(rows), dtype="boolean"),
            "is_salt": pd.array([False] * len(rows), dtype="boolean"),
            "geometry": [row["geometry"] for row in rows],
        },
        crs="EPSG:4326",
    ).to_parquet(raw)
    (tmp / (raw.name + ".meta.json")).write_text(
        json.dumps(
            {
                "source": "synthetic unit-test fixture (not real geography)",
                "dataset": "test fixture",
                "release": "test",
                "download_time_utc": "2026-11-17T00:00:00+00:00",
                "fetch_method": "fixture",
                "bbox": {"min_lon": LON0 - 0.05, "min_lat": LAT0 - 0.05,
                         "max_lon": LON0 + 0.05, "max_lat": LAT0 + 0.05},
            }
        ),
        encoding="utf-8",
    )
    processed = tmp / "processed"
    prepare_contours(raw, processed, force=True)
    build_index(processed / CONTOURS_FILENAME, tmp / "index", force=True)
    return tmp / "index"


def _write_session(root: Path, participant: str, finals: int) -> None:
    """A minimal well-formed session whose on-disk files match their records."""
    directory = root / participant
    directory.mkdir(parents=True)
    files = []
    for index in range(1, finals + 1):
        query_id = f"{participant}-q{index:02d}"
        body = {"points": _box_trace(BOX_A[0] + index, BOX_A[1], *BOX_A[2:]),
                "closed": True, "note": f"{participant}-{index}"}
        content = json.dumps(body)
        (directory / f"{query_id}.json").write_text(content, encoding="utf-8")
        files.append({
            "query_id": query_id,
            "sha256": hashlib.sha256(content.encode(encoding="utf-8")).hexdigest(),
            "status": "registered",
            "final": True,
        })
    (directory / "session.json").write_text(
        json.dumps({"participant": participant, "device": "mouse", "files": files}),
        encoding="utf-8",
    )


def test_collection_to_sheets_pipeline(index_dir: Path, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # the collector edits ./.gitignore relative to cwd
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "alice-final-lake.json").write_text(
        json.dumps({"points": _box_trace(*BOX_A), "closed": True}), encoding="utf-8")
    (inbox / "alice-practice.json").write_text(
        json.dumps({"points": _box_trace(*BOX_B), "closed": True}), encoding="utf-8")
    sessions = tmp_path / "sessions"
    assert collect.main(["--inbox", str(inbox), "--sessions-root", str(sessions)]) == 0

    out_dir = tmp_path / "judgement"
    assert sheets.main([
        "--sessions-root", str(sessions),
        "--index", str(index_dir),
        "--out-dir", str(out_dir),
        "--judges", "2",
        "--seed", str(TEST_SEED),
        *REHEARSAL_GATE,
    ]) == 0

    # only the declared final is turned into blind material
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["n_final_queries"] == 1
    assert "top1_row" in manifest["queries"]["P01-q01"]
    assert (out_dir / "sheets" / "P01-q01.png").is_file()
    refusals = list(csv.reader((out_dir / "refusals.csv").open(encoding="utf-8")))
    assert refusals == [["query_id", "refusal"]]
    assert manifest["gate"] == {"min_participants": 1, "min_finals": 1}

    # one shuffled CSV per judge, same (query, option) coverage, different orders
    columns = ["judge_id", "query_id", "option", "verdict", "most_similar", "notes"]
    bodies = []
    for judge in (1, 2):
        rows = list(csv.reader(
            (out_dir / f"sheet_judge_{judge:02d}.csv").open(encoding="utf-8")))
        assert rows[0] == columns
        assert all(row[0] == f"judge_{judge:02d}" for row in rows[1:])
        bodies.append([(row[1], row[2]) for row in rows[1:]])
    assert sorted(bodies[0]) == sorted(bodies[1]) == [
        ("P01-q01", letter) for letter in "ABCD"
    ]
    assert bodies[0] != bodies[1], "judges must not share one annotation order"
    assert set(manifest["judge_seeds"]) == {"judge_01", "judge_02"}

    # regression (review finding 1): every option maps ONE candidate — row and
    # candidate_id from the same permuted hit, independently recomputed here
    key = json.loads((sessions / "key_options.json").read_text(encoding="utf-8"))
    _, _, metadata = load_index(index_dir)
    index = load_index_handle(index_dir)
    result = search_by_contour(
        sessions / "P01" / "P01-q01.json", index, top_k=10, method="fourier_align")
    order_seed = (TEST_SEED * 10000 + int("P01-q01"[1:3]) * 100
                  + int("P01-q01"[-2:]))
    order = np.random.default_rng(order_seed).permutation(len(result.hits))
    expected = {
        sheets.OPTIONS[slot]: {
            "row": int(result.hits[int(order[slot])].row),
            "candidate_id": str(result.hits[int(order[slot])].candidate_id),
        }
        for slot in range(len(result.hits))
    }
    assert key == {"P01-q01": expected}
    for entry in expected.values():
        assert metadata.iloc[entry["row"]]["candidate_id"] == entry["candidate_id"]
    assert manifest["queries"]["P01-q01"]["top1_row"] == result.hits[0].row


def test_generator_refuses_below_frozen_gate_thresholds(index_dir: Path,
                                                        tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions", "P01", finals=1)
    out_dir = tmp_path / "judgement"
    with pytest.raises(SystemExit, match="pre-flight gate"):
        sheets.main([
            "--sessions-root", str(tmp_path / "sessions"),
            "--index", str(index_dir),
            "--out-dir", str(out_dir),
        ])
    assert not out_dir.exists()  # nothing may be written by a refused run


def test_generator_refuses_an_empty_sessions_root(index_dir: Path,
                                                  tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="pre-flight gate"):
        sheets.main([
            "--sessions-root", str(tmp_path / "empty"),
            "--index", str(index_dir),
            "--out-dir", str(tmp_path / "judgement"),
        ])
