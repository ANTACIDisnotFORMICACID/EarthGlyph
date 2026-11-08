"""Tests for the official ShapeComp numpy port and its actually-run retrieval arms (P0-4.6).

Preprocessing-contract tests are self-contained (no weights, no cache). Weight loading,
faithfulness against the authors' published coordinates, and the S0K1/S0K2 retrieval arms
need the gitignored official toolbox, so they skip when it (or h5py) is absent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

import scipy.io as sio  # noqa: E402
from scipy.stats import pearsonr  # noqa: E402

from earthglyph.shape_benchmark import shapecomp_port as port  # noqa: E402
from earthglyph.shape_benchmark.datasets import (  # noqa: E402
    ZENODO_SUBDIR,
    BenchmarkShape,
    standardize,
)
from earthglyph.shape_benchmark.probes import rotate  # noqa: E402
from earthglyph.shape_benchmark.retrieval import (  # noqa: E402
    KERNET_ROTATION_STEPS,
    Gallery,
    kernet_rotation_frames,
    pair_distance,
    search_shape,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
H2_SC_MAT = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
HAS_TOOLBOX = (port.DEFAULT_TOOLBOX / "net" / "keras" / "shape2sc22a.h5").exists()
HAS_H2 = (CACHE / ZENODO_SUBDIR / H2_SC_MAT).exists()


def polygon(n: int = 200, *, lobes: int = 0) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = 1.0 + 0.3 * np.cos(lobes * theta) if lobes else np.full(n, 1.0)
    return np.column_stack([r * np.cos(theta), r * np.sin(theta)])


def asymmetric(n: int = 300) -> np.ndarray:
    """A lopsided outline: no rotational symmetry, so rotation genuinely moves the coords."""
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = 1.0 + 0.28 * np.cos(theta) + 0.12 * np.sin(2 * theta) + 0.2 * np.cos(3 * theta + 0.5)
    return np.column_stack([r * np.cos(theta), r * np.sin(theta)])


def _shape(points: np.ndarray, shape_id: str) -> BenchmarkShape:
    return BenchmarkShape(shape_id, "synthetic", standardize(points), points)


def test_kernet1_input_is_768d_unit_range_column_major() -> None:
    vector = port.kernet1_input(polygon(lobes=4))
    assert vector.shape == (768,)
    assert np.isfinite(vector).all()
    assert vector.min() >= 0.0
    assert vector.max() <= 1.0


def test_kernet2_input_is_40x40_binary_silhouette() -> None:
    image = port.kernet2_input(polygon(lobes=4))
    assert image.shape == (40, 40, 1)
    assert set(np.unique(image).tolist()) <= {0.0, 1.0}
    assert image.sum() > 0


def test_kernet_inputs_reject_non_contour() -> None:
    with pytest.raises(ValueError, match="contour must be"):
        port.kernet1_input(np.zeros(10))
    with pytest.raises(ValueError, match="contour must be"):
        port.kernet2_input(np.zeros((10, 3)))


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
def test_nets_load_official_weights_and_score_finite_22d() -> None:
    nets = port.ShapeCompNets()
    contour = polygon(lobes=4)
    for fn in (nets.kernet1, nets.kernet2):
        coords = fn(contour)
        assert coords.shape == (22,)
        assert np.isfinite(coords).all()


@pytest.mark.skipif(not (HAS_TOOLBOX and HAS_H2), reason="toolbox or Zenodo cache absent")
def test_kernet2_reproduces_published_h2_coordinates() -> None:
    """The headline faithfulness check: H2's published coords are the KerNet2 route."""
    raw = sio.loadmat(
        CACHE / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False
    )
    nets = port.ShapeCompNets()
    pred = np.array([nets.kernet2(np.asarray(entry.c, dtype=float)) for entry in raw["stim"]])
    ref = np.asarray(raw["sc"], dtype=float)
    per_dim = [pearsonr(pred[:, j], ref[:, j]).statistic for j in range(ref.shape[1])]
    assert float(np.mean(per_dim)) > 0.99


def _gallery() -> Gallery:
    shapes = [
        BenchmarkShape("circle", "synthetic", standardize(polygon()), polygon()),
        BenchmarkShape("star", "synthetic", standardize(polygon(lobes=5)), polygon(lobes=5)),
        BenchmarkShape("blob", "synthetic", standardize(polygon(lobes=3)), polygon(lobes=3)),
    ]
    return Gallery(shapes, name="port-test")


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_run_arms_find_exact_match_at_rank_one(method: str) -> None:
    gallery = _gallery()
    result = search_shape(gallery.shapes[1], gallery, method, top_k=3)
    assert result.hits[0].shape_id == "star"
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert result.details["kind"] == "official_network_actually_run"


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_run_arms_respect_exclusion(method: str) -> None:
    gallery = _gallery()
    result = search_shape(gallery.shapes[0], gallery, method, top_k=3, exclude=["circle"])
    assert "circle" not in [hit.shape_id for hit in result.hits]
    assert not np.isfinite(result.distances[gallery.index_of("circle")])
    assert result.rank_of("circle", gallery) is None


# ------------------------------------------------------------------ rotation search (d5)


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("network", ["kernet1", "kernet2"])
def test_rotation_frames_row_zero_is_the_plain_score(network: str) -> None:
    """The grid's 0-degree row equals the un-rotated network output, so row 0 == naive score."""
    contour = asymmetric()
    frames = kernet_rotation_frames(network, contour)
    assert frames.shape == (KERNET_ROTATION_STEPS, 22)
    nets = port.ShapeCompNets()
    fn = nets.kernet1 if network == "kernet1" else nets.kernet2
    assert np.allclose(frames[0], fn(contour))
    assert np.isfinite(frames).all()


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method,network", [("S0K1", "kernet1"), ("S0K2", "kernet2")])
def test_rotation_search_recovers_distance_to_a_rotated_copy(method: str, network: str) -> None:
    """A rotated copy of the same shape should be nearly equidistant once the grid is searched."""
    shape = _shape(asymmetric(), "query")
    rotated = _shape(rotate(asymmetric(), 137.0), "query_rot137")
    naive = pair_distance(shape, rotated, method)
    searched = pair_distance(shape, rotated, method, rotation_search=True)
    identical = pair_distance(shape, _shape(asymmetric(), "query_again"), method)
    assert searched < naive  # the search strictly shrinks the rotation gap
    assert searched < 0.5 * naive or searched < identical + 0.15
    # The cached frames scorer used by run_d5 must equal the public API exactly.
    left_frames = kernet_rotation_frames(network, shape.display_points)
    right = kernet_rotation_frames(network, rotated.display_points)[0]
    cached = float(np.min(np.linalg.norm(left_frames - right, axis=1)))
    assert cached == pytest.approx(searched, abs=1e-12)


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
@pytest.mark.parametrize("method", ["S0K1", "S0K2"])
def test_rotation_search_ranks_rotated_query_to_its_own_class(method: str) -> None:
    """Rotation-search makes an off-grid-rotated query recover its exact match at rank 1."""
    base = asymmetric()
    distractors = [
        _shape(polygon(lobes=5), "star"),
        _shape(polygon(lobes=3), "tri"),
        _shape(polygon(200), "circle"),
    ]
    gallery = Gallery([_shape(base, "target"), *distractors], name="rot-test")
    query = _shape(rotate(base, 37.0), "query_rot37")
    searched = search_shape(query, gallery, method, top_k=4, rotation_search=True)
    assert searched.hits[0].shape_id == "target"
    assert searched.details["rotation_search"] is True
    assert searched.details["rotations_searched"] == KERNET_ROTATION_STEPS
    # Invariance: the searched distance to the target is far below the naive distance to it.
    naive = search_shape(query, gallery, method, top_k=4)
    target_idx = gallery.index_of("target")
    assert searched.distances[target_idx] < naive.distances[target_idx]