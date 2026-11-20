"""Tests for the local one-click search bridge (scripts/serve_sketch_search.py).

The bridge must be a thin wrapper: production defaults frozen, refusals surfaced
as refusals, client-supplied algorithm parameters ignored, loopback HTTP contract
stable. Fixtures are synthetic geometry built through the production index chain;
nothing here fabricates human data or touches the real index.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("geopandas")  # before the optional imports

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import serve_sketch_search as bridge  # noqa: E402

from earthglyph.data.index import build_index  # noqa: E402
from earthglyph.data.prepare import CONTOURS_FILENAME, prepare_contours  # noqa: E402
from earthglyph.retrieval.search import IndexHandle, load_index_handle  # noqa: E402

LON0, LAT0 = -89.5, 44.5


def _box(lon: float, lat: float, width: float, height: float) -> shapely.Polygon:
    return shapely.Polygon(
        [(lon, lat), (lon + width, lat), (lon + width, lat + height), (lon, lat + height)]
    )


@pytest.fixture(scope="module")
def handle(tmp_path_factory: pytest.TempPathFactory) -> IndexHandle:
    tmp = tmp_path_factory.mktemp("sketch_search")
    boxes = {
        "a-lake": (LON0, LAT0, 0.010, 0.008),
        "b-lake": (LON0 + 0.02, LAT0, 0.012, 0.006),
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
        json.dumps(
            {
                "source": "synthetic unit-test fixture (not real geography)",
                "dataset": "test fixture",
                "release": "test",
                "download_time_utc": "2026-11-18T00:00:00+00:00",
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
    return load_index_handle(tmp / "index")


def _box_payload() -> dict[str, Any]:
    """A closed canvas trace of a rectangle, first point repeated at the end."""
    corners = [(100.0, 100.0), (300.0, 100.0), (300.0, 260.0), (100.0, 260.0)]
    points: list[list[float]] = []
    for index in range(4):
        x0, y0 = corners[index]
        x1, y1 = corners[(index + 1) % 4]
        for step in range(10):
            fraction = step / 10
            points.append([x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction])
    points.append(points[0])
    return {"points": points, "closed": True, "unit": "canvas"}


def _open_arc_payload() -> dict[str, Any]:
    """Three quarters of a circle: the endpoint gap is ~1.41 RMS radii."""
    points = [
        [260.0 + 150.0 * np.cos(2 * np.pi * i / 48),
         260.0 + 150.0 * np.sin(2 * np.pi * i / 48)]
        for i in range(37)
    ]
    return {"points": points, "closed": True, "unit": "canvas"}


def test_valid_payload_uses_frozen_production_defaults(handle: IndexHandle) -> None:
    data = bridge.run_search(_box_payload(), handle)
    assert data["ok"] is True
    params = data["params"]
    assert params["method"] == "fourier_align"
    assert params["top_k"] == 10
    assert params["shortlist_size"] == 200
    assert params["allow_mirror"] is False
    assert params["smoothing"] == "light"
    assert params["max_per_source"] == 1
    assert 1 <= len(data["results"]) <= 10
    for hit in data["results"]:
        assert isinstance(hit["candidate_id"], str) and hit["candidate_id"]
        assert np.isfinite(hit["lon"]) and np.isfinite(hit["lat"])
        contour = np.asarray(hit["aligned_contour"], dtype=float)
        assert contour.shape == (128, 2) and np.isfinite(contour).all()
    query_points = np.asarray(data["query"]["query_contour"]["points"], dtype=float)
    assert query_points.shape == (128, 2)


def test_search_receives_the_users_exact_points(
    handle: IndexHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge must hand the drawn trace to production search verbatim.

    No resampling, reordering, or substitution may happen before the search
    call; standardization is production search's own documented job.
    """
    captured: dict[str, Any] = {}
    real_search = bridge.search_by_contour

    def spy(points: Any, index: IndexHandle, **kwargs: Any) -> Any:
        captured["points"] = points
        captured["index"] = index
        captured["kwargs"] = kwargs
        return real_search(points, index, **kwargs)

    monkeypatch.setattr(bridge, "search_by_contour", spy)
    payload = _box_payload()
    payload["extra_client_key"] = "must not reach the algorithm"
    data = bridge.run_search(payload, handle)
    assert data["ok"] is True
    assert captured["points"] is payload["points"]
    assert captured["index"] is handle
    assert captured["kwargs"] == {"closed": True, "unit": "canvas"}


def test_client_cannot_override_algorithm_parameters(handle: IndexHandle) -> None:
    payload = _box_payload()
    payload.update({"method": "hu", "top_k": 1, "allow_mirror": True, "shortlist_size": 5})
    data = bridge.run_search(payload, handle)
    assert data["params"]["method"] == "fourier_align"
    assert data["params"]["top_k"] == 10
    assert data["params"]["allow_mirror"] is False
    assert data["params"]["shortlist_size"] == 200


def test_ranking_is_deterministic(handle: IndexHandle) -> None:
    first = bridge.run_search(_box_payload(), handle)
    second = bridge.run_search(_box_payload(), handle)
    assert first["results"] == second["results"]
    assert first["warnings"] == second["warnings"]


def test_open_stroke_is_refused_not_silently_closed(handle: IndexHandle) -> None:
    with pytest.raises(ValueError):
        bridge.run_search(_open_arc_payload(), handle)


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"closed": True}, {"points": "not-a-list"}, {"points": [[1.0, 2.0]]}],
)
def test_malformed_payloads_raise_value_error(handle: IndexHandle, payload: Any) -> None:
    with pytest.raises(ValueError):
        bridge.run_search(payload, handle)


@pytest.fixture()
def server(handle: IndexHandle) -> Any:
    httpd = ThreadingHTTPServer((bridge.LOOPBACK, 0), bridge.build_handler(handle))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(server: ThreadingHTTPServer, method: str, path: str,
             body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any] | bytes]:
    conn = http.client.HTTPConnection(bridge.LOOPBACK, server.server_address[1], timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        if resp.getheader("Content-Type", "").startswith("application/json"):
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw
    finally:
        conn.close()


def test_http_serves_the_pad_with_the_search_button(server: ThreadingHTTPServer) -> None:
    status, body = _request(server, "GET", "/")
    assert status == 200
    assert isinstance(body, bytes)
    assert b'id="search"' in body and b'id="overlay"' in body


def test_http_search_round_trip(server: ThreadingHTTPServer) -> None:
    body = json.dumps(_box_payload()).encode("utf-8")
    status, data = _request(server, "POST", "/search", body,
                            {"Content-Type": "application/json"})
    assert status == 200
    assert isinstance(data, dict) and data["ok"] is True
    assert data["results"]


def test_http_refusal_is_400_with_reason(server: ThreadingHTTPServer) -> None:
    body = json.dumps(_open_arc_payload()).encode("utf-8")
    status, data = _request(server, "POST", "/search", body,
                            {"Content-Type": "application/json"})
    assert status == 400
    assert isinstance(data, dict) and data["ok"] is False
    assert data["error"]


def test_http_invalid_json_is_400(server: ThreadingHTTPServer) -> None:
    status, data = _request(server, "POST", "/search", b"{not json",
                            {"Content-Type": "application/json"})
    assert status == 400
    assert isinstance(data, dict) and data["ok"] is False


def test_http_unknown_routes_are_404(server: ThreadingHTTPServer) -> None:
    status, _ = _request(server, "GET", "/nope")
    assert status == 404
    status, _ = _request(server, "POST", "/nope", b"{}")
    assert status == 404


def test_http_body_cap_is_enforced(server: ThreadingHTTPServer) -> None:
    conn = http.client.HTTPConnection(bridge.LOOPBACK, server.server_address[1], timeout=30)
    try:
        conn.putrequest("POST", "/search")
        conn.putheader("Content-Length", str(bridge.MAX_BODY_BYTES + 1))
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is False
    finally:
        conn.close()

# --- M1 multi-stroke adapter (schema v2) --------------------------------------


def _box_strokes_payload() -> dict[str, Any]:
    """The same rectangle as _box_payload, split into its four sides.

    Adjacent strokes share their corner point exactly, so a correct assembly
    must reproduce the single-stroke baseline bit-for-bit.
    """
    points = _box_payload()["points"]
    strokes = []
    for side in range(4):
        chunk = points[side * 10:(side + 1) * 10]
        chunk.append(points[(side + 1) * 10 % 40])
        strokes.append({"points": chunk})
    return {"schema_version": 2, "mode": "contour", "unit": "canvas", "strokes": strokes}


def _two_loops_payload() -> dict[str, Any]:
    def loop(cx: float) -> list[list[float]]:
        pts = [[cx + 60.0 * np.cos(2 * np.pi * i / 48),
                260.0 + 60.0 * np.sin(2 * np.pi * i / 48)] for i in range(49)]
        pts[-1] = list(pts[0])
        return pts

    return {"schema_version": 2, "mode": "contour", "unit": "canvas",
            "strokes": [{"points": loop(160.0)}, {"points": loop(360.0)}]}


def _ambiguous_payload() -> dict[str, Any]:
    """Same verified construction as tests/test_multistroke_assembly.py:
    four nested lanes with fanned endpoints -> exactly 2 valid assemblies."""
    px, py, qx, qy = 150.0, 260.0, 430.0, 260.0
    off_p = [(0.0, 2.0), (2.0, 0.0), (0.0, -2.0), (-2.0, 0.0)]
    off_q = [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)]
    strokes = []
    for k, bulge in enumerate((128.0, 40.0, -40.0, -128.0)):
        p0 = (px + off_p[k][0], py + off_p[k][1])
        p1 = (qx + off_q[k][0], qy + off_q[k][1])
        pts = [[p0[0] + (p1[0] - p0[0]) * (i / 32),
                p0[1] + (p1[1] - p0[1]) * (i / 32)
                + 4 * bulge * (i / 32) * (1 - i / 32)] for i in range(33)]
        strokes.append({"points": pts})
    return {"schema_version": 2, "mode": "contour", "unit": "canvas",
            "strokes": strokes}


def test_multistroke_box_matches_single_stroke_baseline(handle: IndexHandle) -> None:
    baseline = bridge.run_search(_box_payload(), handle)
    multi = bridge.run_search(_box_strokes_payload(), handle)
    assert multi["ok"] is True
    assert multi["results"] == baseline["results"]
    assert multi["query"]["query_contour"]["points"] == \
        baseline["query"]["query_contour"]["points"]
    assembly = multi["assembly"]
    assert assembly["status"] == "ok"
    assert len(assembly["junctions"]) == 4
    assert all(j["correction"] <= 1e-9 for j in assembly["junctions"])
    assert assembly["contour_points"] is not None


def test_multistroke_keeps_frozen_production_defaults(handle: IndexHandle) -> None:
    data = bridge.run_search(_box_strokes_payload(), handle)
    params = data["params"]
    assert params["method"] == "fourier_align"
    assert params["top_k"] == 10
    assert params["shortlist_size"] == 200
    assert params["allow_mirror"] is False
    assert params["smoothing"] == "light"


def test_multistroke_failure_never_calls_search(
    handle: IndexHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("production search must not run for refused assemblies")

    monkeypatch.setattr(bridge, "search_by_contour", boom)
    for payload in (_two_loops_payload(), _ambiguous_payload()):
        with pytest.raises(ValueError):
            bridge.run_search(payload, handle)


def test_multistroke_ambiguous_is_refused_with_reason(handle: IndexHandle) -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        bridge.run_search(_ambiguous_payload(), handle)


def test_v2_single_stroke_matches_old_format(handle: IndexHandle) -> None:
    old = bridge.run_search(_box_payload(), handle)
    payload = {"schema_version": 2, "mode": "contour", "unit": "canvas",
               "strokes": [{"points": _box_payload()["points"]}]}
    new = bridge.run_search(payload, handle)
    assert new["results"] == old["results"]


def test_old_points_path_never_enters_assembly(
    handle: IndexHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("legacy points path must not touch the assembly module")

    monkeypatch.setattr(bridge, "assemble_contour", boom)
    data = bridge.run_search(_box_payload(), handle)
    assert data["ok"] is True
    assert "assembly" not in data


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "strokes": [{"points": [[0, 0], [1, 1]]}]},
        {"schema_version": 2, "strokes": []},
        {"schema_version": 2, "strokes": [{"points": [[0, 0]]}]},
        {"schema_version": 2, "strokes": [{"points": [[0, 0], [1, 1]]}],
         "points": [[0, 0], [1, 1]]},
        {"schema_version": 2, "mode": "road",
         "strokes": [{"points": [[0, 0], [1, 1]]}]},
    ],
)
def test_malformed_v2_payloads_raise_value_error(
    handle: IndexHandle, payload: Any
) -> None:
    with pytest.raises(ValueError):
        bridge.run_search(payload, handle)


def test_http_multistroke_round_trip(server: ThreadingHTTPServer) -> None:
    body = json.dumps(_box_strokes_payload()).encode("utf-8")
    status, data = _request(server, "POST", "/search", body,
                            {"Content-Type": "application/json"})
    assert status == 200
    assert isinstance(data, dict) and data["ok"] is True
    assert data["results"]
    assert data["assembly"]["status"] == "ok"
    assert len(data["assembly"]["junctions"]) == 4


def test_http_multistroke_refusal_is_400_with_reason(server: ThreadingHTTPServer) -> None:
    body = json.dumps(_two_loops_payload()).encode("utf-8")
    status, data = _request(server, "POST", "/search", body,
                            {"Content-Type": "application/json"})
    assert status == 400
    assert isinstance(data, dict) and data["ok"] is False
    assert data["error"]


def test_http_multistroke_ambiguous_is_400(server: ThreadingHTTPServer) -> None:
    body = json.dumps(_ambiguous_payload()).encode("utf-8")
    status, data = _request(server, "POST", "/search", body,
                            {"Content-Type": "application/json"})
    assert status == 400
    assert isinstance(data, dict) and data["ok"] is False
    assert "ambiguous" in data["error"]
