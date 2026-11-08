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
from earthglyph.shape_benchmark.datasets import ZENODO_SUBDIR, BenchmarkShape, standardize  # noqa: E402
from earthglyph.shape_benchmark.retrieval import Gallery, search_shape  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
H2_SC_MAT = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
HAS_TOOLBOX = (port.DEFAULT_TOOLBOX / "net" / "keras" / "shape2sc22a.h5").exists()
HAS_H2 = (CACHE / ZENODO_SUBDIR / H2_SC_MAT).exists()


def polygon(n: int = 200, *, lobes: int = 0) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = 1.0 + 0.3 * np.cos(lobes * theta) if lobes else np.full(n, 1.0)
    return np.column_stack([r * np.cos(theta), r * np.sin(theta)])


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