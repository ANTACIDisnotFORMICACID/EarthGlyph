"""End-to-end tests for the P0-6 scoring aggregator (protocol §5-§6).

Every run is a SYNTHETIC TEST: sessions, ratings and the index are generated
fixtures — none of this is human evidence, and the scorer output is asserted
to carry the synthetic marker.

The module fixture builds the full pipeline once (10 participants x 6 finals
on a real 4-candidate index through the frozen sheet generator); each test
forks the judgement directory and fills/corrupts the rating CSVs. Covered
edge cases (brief C4): complete legal data, judge missed ratings, duplicate
ratings, wrong candidate mapping, unknown query_id, illegal verdict /
most_similar, identity misalignment, extraction refusal, exactly 50%,
slightly above 50%, too few participants, too few finals, dataset drift
after sheet generation, and independent recomputation of the key statistics.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("geopandas")

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import make_p06_judgement_sheets as sheets  # noqa: E402
import score_p06_judgements as score  # noqa: E402

from earthglyph.data.index import build_index  # noqa: E402
from earthglyph.data.prepare import CONTOURS_FILENAME, prepare_contours  # noqa: E402

LON0, LAT0 = -89.5, 44.5
BOX_A = (LON0, LAT0, 0.010, 0.008)
BOX_B = (LON0 + 0.02, LAT0, 0.012, 0.006)
TEST_SEED = 20261121
N_PARTICIPANTS = 10
N_FINALS = 6


def _box(lon: float, lat: float, width: float, height: float) -> shapely.Polygon:
    return shapely.Polygon(
        [(lon, lat), (lon + width, lat), (lon + width, lat + height),
         (lon, lat + height)]
    )


def _box_trace(lon: float, lat: float, width: float, height: float,
               per_side: int = 10) -> list[list[float]]:
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


def _bowtie_trace() -> list[list[float]]:
    """A materially self-crossing closed trace — refused by the extractor."""
    return [[0.0, 0.0], [0.02, 0.02], [0.02, 0.0], [0.0, 0.02], [0.0, 0.0]]


@pytest.fixture(scope="module")
def index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("p06_scoring_index")
    boxes = {
        "a-lake": BOX_A,
        "b-lake": BOX_B,
        "c-lake": (LON0, LAT0 + 0.02, 0.008, 0.010),
        "d-lake": (LON0 + 0.02, LAT0 + 0.02, 0.011, 0.009),
    }
    raw = tmp / "synthetic.parquet"
    gpd.GeoDataFrame(
        {
            "id": list(boxes),
            "subtype": pd.array(["lake"] * len(boxes), dtype="string"),
            "class": pd.array(["lake"] * len(boxes), dtype="string"),
            "is_intermittent": pd.array([None] * len(boxes), dtype="boolean"),
            "is_salt": pd.array([False] * len(boxes), dtype="boolean"),
            "geometry": [_box(*args) for args in boxes.values()],
        },
        crs="EPSG:4326",
    ).to_parquet(raw)
    (tmp / (raw.name + ".meta.json")).write_text(
        json.dumps({
            "source": "synthetic unit-test fixture (not real geography)",
            "dataset": "test fixture",
            "release": "test",
            "download_time_utc": "2026-11-21T00:00:00+00:00",
            "fetch_method": "fixture",
            "bbox": {"min_lon": LON0 - 0.05, "min_lat": LAT0 - 0.05,
                     "max_lon": LON0 + 0.05, "max_lat": LAT0 + 0.05},
        }),
        encoding="utf-8",
    )
    processed = tmp / "processed"
    prepare_contours(raw, processed, force=True)
    build_index(processed / CONTOURS_FILENAME, tmp / "index", force=True)
    return tmp / "index"


def _write_session(root: Path, participant: str, finals: int,
                   queries: list[list[list[float]]] | None = None) -> None:
    directory = root / participant
    directory.mkdir(parents=True)
    files = []
    # globally unique traces: participants must not share content sha256
    participant_number = int(participant[1:])
    for index in range(1, finals + 1):
        query_id = f"{participant}-q{index:02d}"
        trace = queries[index - 1] if queries is not None else _box_trace(
            BOX_A[0] + (participant_number * 100 + index) * 0.001,
            BOX_A[1], *BOX_A[2:])
        content = json.dumps({"points": trace, "closed": True})
        (directory / f"{query_id}.json").write_text(content, encoding="utf-8")
        files.append({
            "query_id": query_id,
            "sha256": hashlib.sha256(content.encode(encoding="utf-8")).hexdigest(),
            "status": "registered",
            "final": True,
        })
    (directory / "session.json").write_text(
        json.dumps({"participant": participant, "device": "mouse",
                    "files": files}),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def dataset(index_dir: Path,
            tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One full 10x6 collection + frozen-parameter sheet generation."""
    tmp = tmp_path_factory.mktemp("p06_scoring_dataset")
    sessions = tmp / "sessions"
    for number in range(1, N_PARTICIPANTS + 1):
        _write_session(sessions, f"P{number:02d}", N_FINALS)
    judgement = tmp / "judgement"
    assert sheets.main([
        "--sessions-root", str(sessions),
        "--index", str(index_dir),
        "--out-dir", str(judgement),
        "--judges", "2",
        "--seed", str(TEST_SEED),
    ]) == 0
    return {"sessions": sessions, "judgement": judgement,
            "index": index_dir}


def _fork(dataset: dict[str, Path], tmp_path: Path) -> dict[str, Path]:
    # copy BOTH trees: corruption tests mutate sessions (manifest drift,
    # key tampering), and the module fixture is shared across all tests
    sessions = tmp_path / "sessions"
    judgement = tmp_path / "judgement"
    shutil.copytree(dataset["sessions"], sessions)
    shutil.copytree(dataset["judgement"], judgement)
    return {"sessions": sessions, "judgement": judgement,
            "index": dataset["index"]}


def _read_sheet(path: Path) -> list[list[str]]:
    return list(csv.reader(path.open(encoding="utf-8")))


def _write_sheet(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _fill_self(judgement: Path, similar_queries: set[str]) -> None:
    """Drawer marks option A similar on chosen queries; everything else
    not_similar with most_similar none."""
    for path in sorted(judgement.glob("sheet_self_*.csv")):
        rows = _read_sheet(path)
        filled = [rows[0]]
        for row in rows[1:]:
            query_id, option = row[1], row[2]
            if query_id in similar_queries and option == "A":
                filled.append([row[0], query_id, option, "similar", "A", ""])
            else:
                filled.append([row[0], query_id, option, "not_similar",
                               "none" if option == "A" else "", ""])
        _write_sheet(path, filled)


def _fill_judges(judgement: Path, similar_queries: set[str],
                 matching_judge: str | None = "judge_01") -> None:
    """One judge mirrors the drawer exactly; the other judge rates everything
    not_similar."""
    for path in sorted(judgement.glob("sheet_judge_*.csv")):
        rows = _read_sheet(path)
        mirror = path.stem == f"sheet_{matching_judge}"
        filled = [rows[0]]
        for row in rows[1:]:
            query_id, option = row[1], row[2]
            if mirror and query_id in similar_queries and option == "A":
                filled.append([row[0], query_id, option, "similar", "", ""])
            else:
                filled.append([row[0], query_id, option, "not_similar", "", ""])
        _write_sheet(path, filled)


def _all_query_ids(sessions: Path) -> list[str]:
    return sorted(
        record["query_id"]
        for manifest in sessions.glob("P*/session.json")
        for record in json.loads(manifest.read_text(encoding="utf-8"))["files"]
        if record.get("status") == "registered" and record.get("final")
    )


def _run(fork: dict[str, Path], tmp_path: Path, extra: list[str] | None = None,
         capsys: pytest.CaptureFixture[str] | None = None) -> tuple[int, dict[str, Any], str]:
    argv = ["--sessions-root", str(fork["sessions"]),
            "--judgement-dir", str(fork["judgement"]),
            "--report", str(tmp_path / "scoring_report.json"),
            "--synthetic"]
    if extra:
        argv += extra
    code = score.main(argv)
    report = json.loads((tmp_path / "scoring_report.json").read_text(
        encoding="utf-8"))
    out = capsys.readouterr().out if capsys is not None else ""
    return code, report, out


def test_complete_legal_data_is_go(dataset: dict[str, Path],
                                   tmp_path: Path,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    similar = set(queries[:31])  # 31/60 = 51.7% > 50%
    _fill_self(fork["judgement"], similar)
    _fill_judges(fork["judgement"], similar)

    code, report, out = _run(fork, tmp_path, capsys=capsys)

    assert code == 0
    assert report["synthetic"] is True
    assert "SYNTHETIC TEST" in out
    assert report["verdict"] == "GO"
    assert report["gate"] == {"min_participants": 10, "min_finals": 6,
                              "participants": 10, "short_participants": [],
                              "passed": True}
    calibers = report["main_criterion"]["calibers"]
    for name in ("registered_finals", "sheeted", "complete_data"):
        assert calibers[name]["denominator"] == 60
        assert calibers[name]["similar"] == 31
    assert calibers["registered_finals"]["ratio"] == pytest.approx(31 / 60)
    assert report["refusal"]["refusal_rate"] == 0.0
    assert report["attribution"]["status"] == "not_computed"
    assert len(report["participants"]) == 10
    per_p = {row["participant"]: row for row in report["participants"]}
    # P01 owns the first six sorted query ids — all similar
    assert per_p["P01"]["self_similar"] == 6
    assert per_p["P01"]["ratio_registered"] == 1.0
    # P10 owns the last six — none similar
    assert per_p["P10"]["self_similar"] == 0
    judges = {row["rater"]: row for row in report["judges"]}
    assert judges["judge_01"]["similar_queries"] == 31
    assert judges["judge_02"]["similar_queries"] == 0
    agreement = report["agreement"]["per_judge"]
    assert agreement["judge_01"]["common_pairs"] == 240
    assert agreement["judge_01"]["agreement"] == 1.0
    assert agreement["judge_02"]["agreement"] == pytest.approx(209 / 240)
    assert report["data_quality"]["self_coverage"]["P01"] == {
        "sheeted": 6, "fully_rated": 6}
    assert report["errors"] == []


def test_exactly_50_percent_is_no_go(dataset: dict[str, Path],
                                     tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    similar = set(queries[:30])  # exactly 30/60 = 50%
    _fill_self(fork["judgement"], similar)
    _fill_judges(fork["judgement"], similar)

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert report["verdict"] == "NO-GO"
    strict = report["main_criterion"]["calibers"]["registered_finals"]
    assert strict["similar"] == 30 and strict["denominator"] == 60
    assert strict["ratio"] == 0.5


def test_slightly_above_50_percent_is_go(dataset: dict[str, Path],
                                         tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))

    code, report, _ = _run(fork, tmp_path)

    assert code == 0
    assert report["verdict"] == "GO"
    strict = report["main_criterion"]["calibers"]["registered_finals"]
    assert strict["ratio"] > 0.5


def test_judge_missed_ratings_do_not_break_the_main_criterion(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    similar = set(queries[:31])
    _fill_self(fork["judgement"], similar)
    _fill_judges(fork["judgement"], similar)
    judge_path = fork["judgement"] / "sheet_judge_02.csv"
    rows = _read_sheet(judge_path)
    _write_sheet(judge_path, rows[:100])  # judge_02 abandons mid-sheet

    code, report, _ = _run(fork, tmp_path)

    assert code == 0
    assert report["verdict"] == "GO"  # main criterion uses drawer self-ratings
    judges = {row["rater"]: row for row in report["judges"]}
    assert judges["judge_02"]["coverage"] < 1.0
    assert judges["judge_02"]["queries_rated"] < 60
    agreement = report["agreement"]["per_judge"]
    assert agreement["judge_02"]["common_pairs"] < 240
    assert report["errors"] == []


def test_duplicate_rating_rejected(dataset: dict[str, Path],
                                    tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    path = fork["judgement"] / "sheet_self_P01.csv"
    rows = _read_sheet(path)
    _write_sheet(path, rows + [rows[1]])

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("duplicate rating" in error for error in report["errors"])


def test_wrong_candidate_mapping_rejected(dataset: dict[str, Path],
                                          tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    key_path = fork["sessions"] / "key_options.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    query_id = queries[0]
    key[query_id]["B"] = dict(key[query_id]["A"])  # two options, one candidate
    key_path.write_text(json.dumps(key), encoding="utf-8")

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("map to the same candidate" in error for error in report["errors"])
    assert any("sha256 differs" in error for error in report["errors"])


def test_unknown_query_id_rejected(dataset: dict[str, Path],
                                   tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    path = fork["judgement"] / "sheet_judge_01.csv"
    rows = _read_sheet(path)
    _write_sheet(path, rows + [["judge_01", "P01-q99", "A", "similar", "", ""]])

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("unknown query_id P01-q99" in error for error in report["errors"])


def test_illegal_verdict_and_pick_rejected(dataset: dict[str, Path],
                                           tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    self_path = fork["judgement"] / "sheet_self_P01.csv"
    rows = _read_sheet(self_path)
    rows[1][3] = "great"
    rows[2][4] = "Z"
    _write_sheet(self_path, rows)

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("illegal verdict" in error for error in report["errors"])
    assert any("illegal most_similar" in error for error in report["errors"])


def test_self_rater_cannot_rate_another_participant(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    path = fork["judgement"] / "sheet_self_P01.csv"
    rows = _read_sheet(path)
    _write_sheet(path, rows + [["self_P01", "P02-q01", "A", "similar", "", ""]])

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("identity misalignment" in error for error in report["errors"])


def test_dataset_drift_after_sheet_generation_rejected(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    manifest_path = fork["sessions"] / "P01" / "session.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"query_id": "P01-q07", "sha256": "x" * 64,
                              "status": "registered", "final": True})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code, report, _ = _run(fork, tmp_path)

    assert code == 1
    assert any("dataset changed" in error for error in report["errors"])


def test_extraction_refusal_counts_as_failure_under_strict_caliber(
        index_dir: Path, tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _write_session(sessions, "P01", finals=2,
                   queries=[_box_trace(*BOX_A), _bowtie_trace()])
    judgement = tmp_path / "judgement"
    assert sheets.main([
        "--sessions-root", str(sessions),
        "--index", str(index_dir),
        "--out-dir", str(judgement),
        "--judges", "1",
        "--seed", str(TEST_SEED),
        "--min-participants", "1", "--min-finals", "1",
    ]) == 0
    refusals = list(csv.reader((judgement / "refusals.csv").open(
        encoding="utf-8")))
    assert len(refusals) == 2  # header + the bowtie query
    _fill_self(judgement, {"P01-q01"})  # the sheeted box query
    _fill_judges(judgement, {"P01-q01"})

    code, report, _ = _run({"sessions": sessions, "judgement": judgement},
                           tmp_path, extra=["--min-participants", "1",
                                            "--min-finals", "1"])

    calibers = report["main_criterion"]["calibers"]
    # registered finals: 2 (box sheeted + bowtie refused) — refusal is a failure
    assert calibers["registered_finals"] == {
        "denominator": 2, "similar": 1, "ratio": 0.5}
    # sheeted: the refused query leaves the denominator — 1/1
    assert calibers["sheeted"] == {"denominator": 1, "similar": 1, "ratio": 1.0}
    assert calibers["complete_data"] == {
        "denominator": 1, "similar": 1, "ratio": 1.0}
    assert report["refusal"]["refused_queries"] == ["P01-q02"]
    assert report["refusal"]["refusal_rate"] == 0.5
    # strict caliber says 50% — exactly at the line means NO-GO
    assert code == 1
    assert report["verdict"] == "NO-GO"


def test_too_few_participants_is_not_ready(index_dir: Path,
                                           tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _write_session(sessions, "P01", finals=6)
    _write_session(sessions, "P02", finals=6)
    judgement = tmp_path / "judgement"
    assert sheets.main([
        "--sessions-root", str(sessions),
        "--index", str(index_dir),
        "--out-dir", str(judgement),
        "--judges", "1",
        "--seed", str(TEST_SEED),
        "--min-participants", "2", "--min-finals", "6",
    ]) == 0
    queries = _all_query_ids(sessions)
    _fill_self(judgement, set(queries))
    _fill_judges(judgement, set(queries))

    code, report, _ = _run({"sessions": sessions, "judgement": judgement},
                           tmp_path)  # default gate: 10 participants required

    assert code == 1
    assert report["verdict"] == "NOT READY"
    assert report["gate"]["participants"] == 2
    assert report["gate"]["passed"] is False
    # the ratio is still reported — evidence is not hidden behind the gate
    strict = report["main_criterion"]["calibers"]["registered_finals"]
    assert strict["similar"] == 12 and strict["denominator"] == 12


def test_too_few_finals_per_participant_is_not_ready(
        index_dir: Path, tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _write_session(sessions, "P01", finals=6)
    _write_session(sessions, "P02", finals=5)  # one short of the frozen 6
    judgement = tmp_path / "judgement"
    assert sheets.main([
        "--sessions-root", str(sessions),
        "--index", str(index_dir),
        "--out-dir", str(judgement),
        "--judges", "1",
        "--seed", str(TEST_SEED),
        "--min-participants", "2", "--min-finals", "5",
    ]) == 0
    queries = _all_query_ids(sessions)
    _fill_self(judgement, set(queries))
    _fill_judges(judgement, set(queries))

    code, report, _ = _run({"sessions": sessions, "judgement": judgement},
                           tmp_path)  # default gate: >=6 finals each

    assert code == 1
    assert report["verdict"] == "NOT READY"
    assert report["gate"]["short_participants"] == ["P02(5)"]


def test_independent_recomputation_from_raw_csvs(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    """Cross-check the report numbers by recounting directly from the CSVs —
    a path that shares no code with the scorer."""
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    similar = set(queries[:31])
    _fill_self(fork["judgement"], similar)
    _fill_judges(fork["judgement"], similar)

    code, report, _ = _run(fork, tmp_path)

    assert code == 0
    similar_queries: set[str] = set()
    total_rows = 0
    judge_pairs: dict[tuple[str, str, str], str] = {}
    self_pairs: dict[tuple[str, str, str], str] = {}
    for path in sorted(fork["judgement"].glob("sheet_*.csv")):
        rows = list(csv.reader(path.open(encoding="utf-8")))[1:]
        for row in rows:
            total_rows += 1
            if row[0].startswith("self_"):
                self_pairs[(row[0], row[1], row[2])] = row[3]
                if row[3] == "similar":
                    similar_queries.add(row[1])
            else:
                judge_pairs[(row[0], row[1], row[2])] = row[3]
    calibers = report["main_criterion"]["calibers"]
    assert len(similar_queries) == calibers["registered_finals"]["similar"]
    assert len(queries) == calibers["registered_finals"]["denominator"]
    assert total_rows == 3 * 60 * 4  # 3 raters x 60 queries x 4 options
    judge01 = {pair[1:]: verdict for pair, verdict in judge_pairs.items()
               if pair[0] == "judge_01"}
    self_by_pair = {pair[1:]: verdict for pair, verdict in self_pairs.items()}
    common = [pair for pair in judge01 if pair in self_by_pair]
    matches = sum(1 for pair in common
                  if judge01[pair] == self_by_pair[pair])
    agreement = report["agreement"]["per_judge"]["judge_01"]
    assert agreement["common_pairs"] == len(common)
    assert agreement["matching_pairs"] == matches


def test_target_attribution_when_targets_registered(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    key = json.loads((fork["sessions"] / "key_options.json").read_text(
        encoding="utf-8"))
    manifest = json.loads((fork["judgement"] / "manifest.json").read_text(
        encoding="utf-8"))
    targets = tmp_path / "targets.csv"
    with targets.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "candidate_id"])
        for query_id in queries[:30]:
            top1_row = manifest["queries"][query_id]["top1_row"]
            entry = next(e for e in key[query_id].values()
                         if e["row"] == top1_row)
            writer.writerow([query_id, entry["candidate_id"]])
        for query_id in queries[30:]:
            writer.writerow([query_id, "not-in-top-10"])

    code, report, _ = _run(fork, tmp_path, extra=["--targets", str(targets)])

    assert code == 0
    assert report["attribution"] == {
        "status": "computed",
        "counts": {"target_top1": 30, "target_in_top10": 0,
                   "target_not_in_top10": 30, "unknown": 0},
    }


def test_report_contains_no_candidate_identities(
        dataset: dict[str, Path], tmp_path: Path) -> None:
    """Privacy: the publishable report must not leak the blind-review key."""
    fork = _fork(dataset, tmp_path)
    queries = _all_query_ids(fork["sessions"])
    _fill_self(fork["judgement"], set(queries[:31]))
    _fill_judges(fork["judgement"], set(queries[:31]))
    key = json.loads((fork["sessions"] / "key_options.json").read_text(
        encoding="utf-8"))
    candidate_ids = {str(entry["candidate_id"])
                     for options in key.values() for entry in options.values()}

    _, report, _ = _run(fork, tmp_path)
    report_text = json.dumps(report)
    assert not any(cid in report_text for cid in candidate_ids)
    assert "row" not in report_text
