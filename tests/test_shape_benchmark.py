"""Tests for the independent shape-perception benchmark (P0-4.6).

Retrieval and Range-reader tests are self-contained. Dataset tests need the downloaded
cache and skip when it is absent, so the suite stays runnable without network access.
"""

from __future__ import annotations

import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from earthglyph.shape_benchmark import remote_zip
from earthglyph.shape_benchmark.datasets import (
    BenchmarkShape,
    load_h1,
    load_h2,
    load_mpeg7,
    standardize,
)
from earthglyph.shape_benchmark.retrieval import Gallery, pair_distance, search_shape

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"


def polygon(n: int = 64, *, lobes: int = 0, radius: float = 1.0) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = radius * (1.0 + 0.3 * np.cos(lobes * theta)) if lobes else np.full(n, radius)
    return np.column_stack([r * np.cos(theta), r * np.sin(theta)])


def shape(shape_id: str, points: np.ndarray, category: str | None = None) -> BenchmarkShape:
    return BenchmarkShape(
        shape_id=shape_id,
        source="synthetic",
        points=standardize(points),
        display_points=points - points.mean(axis=0),
        category=category,
    )


# --------------------------------------------------------------------------- Range reader


class _RangeHandler(BaseHTTPRequestHandler):
    payload: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        header = self.headers.get("Range")
        if header is None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
            return
        start_text, end_text = header.replace("bytes=", "").split("-")
        start, end = int(start_text), int(end_text)
        chunk = self.payload[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def range_server() -> object:
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", b"hello shape benchmark")
        archive.writestr("nested/data.bin", bytes(range(256)) * 40)
        # A large member that is never read: fetching it would defeat the point.
        archive.writestr("bulk/unread.bin", b"\x00" * (1 << 20))
    _RangeHandler.payload = buffer.getvalue()
    server = HTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/archive.zip"
    finally:
        server.shutdown()
        server.server_close()


def test_remote_zip_reads_members_without_full_download(range_server: object) -> None:
    url = str(range_server)
    with remote_zip.RemoteZip(url, chunk=64) as archive:
        assert archive.read("hello.txt") == b"hello shape benchmark"
        assert archive.read("nested/data.bin") == bytes(range(256)) * 40
        assert set(archive.names()) == {"hello.txt", "nested/data.bin", "bulk/unread.bin"}
        assert archive.archive_size > (1 << 20)
        assert archive.bytes_fetched < archive.archive_size // 2
        assert archive.request_count >= 1


def test_range_file_seek_and_tell(range_server: object) -> None:
    with remote_zip.HttpRangeFile(str(range_server), chunk=32) as handle:
        handle.seek(10)
        assert handle.tell() == 10
        assert len(handle.read(5)) == 5
        handle.seek(-4, 2)
        assert handle.tell() == handle.size - 4
        assert len(handle.read()) == 4
        assert handle.read() == b""
        with pytest.raises(ValueError):
            handle.seek(-1)


# --------------------------------------------------------------------------- standardize


def test_standardize_closes_non_uniform_published_polygon() -> None:
    # A closed ring whose wrap chord is many median steps long, like the Zenodo contours.
    points = polygon(384)
    points = np.vstack([points[:200], points[210:]])
    result = standardize(points)
    assert result.shape == (128, 2)
    assert np.isfinite(result).all()
    assert np.sqrt((result**2).sum() / len(result)) == pytest.approx(1.0, abs=1e-6)


def test_standardize_is_translation_and_scale_invariant() -> None:
    base = polygon(96, lobes=5)
    a = standardize(base)
    b = standardize(base * 3.7 + np.array([12.0, -4.0]))
    assert np.allclose(a, b, atol=1e-6)


# --------------------------------------------------------------------------- retrieval


def densify(vertices: np.ndarray, per_edge: int = 16) -> np.ndarray:
    closed = np.vstack([vertices, vertices[:1]])
    segments = [
        np.linspace(a, b, per_edge, endpoint=False)
        for a, b in zip(closed[:-1], closed[1:], strict=True)
    ]
    return np.vstack(segments)


def make_gallery() -> Gallery:
    shapes = [
        shape("circle", polygon(96), "round"),
        shape("star", polygon(96, lobes=5), "spiky"),
        shape("blob", polygon(96, lobes=3), "round"),
        shape("square", densify(np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]])), "angular"),
    ]
    return Gallery(shapes, name="test")


@pytest.mark.parametrize("method", ["E0", "E1", "E3"])
def test_search_shape_finds_exact_match_at_rank_one(method: str) -> None:
    gallery = make_gallery()
    query = gallery.shapes[1]
    result = search_shape(query, gallery, method, top_k=3)
    assert result.hits[0].shape_id == "star"
    assert result.hits[0].rank == 1
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert len(result.order) == len(gallery)
    assert result.elapsed_seconds > 0.0


@pytest.mark.parametrize("method", ["E0", "E1", "E3"])
def test_exclusion_removes_query_from_candidates(method: str) -> None:
    gallery = make_gallery()
    result = search_shape(gallery.shapes[0], gallery, method, top_k=10, exclude=["circle"])
    assert "circle" not in [hit.shape_id for hit in result.hits]
    assert len(result.order) == len(gallery) - 1
    assert not np.isfinite(result.distances[gallery.index_of("circle")])
    assert result.rank_of("circle", gallery) is None
    assert result.rank_of("star", gallery) is not None


def test_search_shape_rejects_unknown_method_and_empty_exclusion() -> None:
    gallery = make_gallery()
    with pytest.raises(ValueError, match="unknown method"):
        search_shape(gallery.shapes[0], gallery, "E9")
    with pytest.raises(ValueError, match="every gallery shape was excluded"):
        search_shape(gallery.shapes[0], gallery, "E0", exclude=[s.shape_id for s in gallery.shapes])


def test_s0_requires_published_coordinates() -> None:
    gallery = make_gallery()
    with pytest.raises(ValueError, match="published official 22-D coordinates"):
        search_shape(gallery.shapes[0], gallery, "S0")


def test_s0_uses_published_coordinates_when_present() -> None:
    shapes = [shape("a", polygon(96)), shape("b", polygon(96, lobes=5))]
    coords = {"a": np.zeros(22), "b": np.ones(22) * 0.5, "q": np.zeros(22)}
    gallery = Gallery(shapes, official_coords=coords, name="s0")
    query = BenchmarkShape("q", "H1", standardize(polygon(96)), polygon(96))
    result = search_shape(query, gallery, "S0", top_k=2)
    assert result.hits[0].shape_id == "a"
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-9)
    assert result.hits[1].distance == pytest.approx(float(np.sqrt(22) * 0.5), rel=1e-9)


def test_pair_distance_requires_normalizer_for_e1() -> None:
    gallery = make_gallery()
    with pytest.raises(ValueError, match="corpus normalizer"):
        pair_distance(gallery.shapes[0], gallery.shapes[1], "E1")
    value = pair_distance(
        gallery.shapes[0], gallery.shapes[1], "E1", normalizer=gallery.e1_normalizer
    )
    assert value > 0.0


def test_gallery_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate shape_id"):
        Gallery([shape("same", polygon(64)), shape("same", polygon(64, lobes=3))])


# --------------------------------------------------------------------------- real datasets


@pytest.mark.skipif(not (CACHE / "MPEG7dataset.zip").exists(), reason="source cache not fetched")
def test_mpeg7_gallery_loads_with_exclusion_ledger() -> None:
    shapes, excluded = load_mpeg7(CACHE)
    assert len(shapes) == 1398
    assert {Path(item.name).name for item in excluded} == {"pocket-11.gif", "pocket-12.gif"}
    assert len({s.shape_id for s in shapes}) == len(shapes)
    assert len({s.category for s in shapes}) == 70
    assert all(s.points.shape == (128, 2) for s in shapes)


@pytest.mark.skipif(not (CACHE / "zenodo4730985").exists(), reason="source cache not fetched")
def test_h1_has_complete_ratings_and_official_distance_identity() -> None:
    data = load_h1(CACHE)
    assert len(data.pairs) == 250
    assert len(data.shapes) == 500
    assert data.n_observers == 14
    matrix = data.pair_matrix()
    assert matrix.shape == (250, 14)
    assert not np.isnan(matrix).any()
    assert data.provenance["official_distance_identity_max_residual"] < 1e-12


@pytest.mark.skipif(not (CACHE / "zenodo4730985").exists(), reason="source cache not fetched")
def test_h2_pairs_by_id_and_reproduces_published_correlation() -> None:
    from scipy.stats import pearsonr

    data = load_h2(CACHE)
    assert len(data.shape_ids) == 15
    assert len(data.human_pair_distance()) == 105
    correlation = pearsonr(data.official_pair_distance(), data.human_pair_distance()).statistic
    # Figure9.m reports r = 0.78 for this comparison.
    assert correlation == pytest.approx(0.778, abs=0.01)