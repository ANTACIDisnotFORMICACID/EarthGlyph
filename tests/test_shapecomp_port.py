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
H2_HUMAN_MAT = "shapecomp datashare/Figure 9/data/fig7D_lietal_points_2.mat"
FIGURE8_MAT = "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat"
HAS_TOOLBOX = (port.DEFAULT_TOOLBOX / "net" / "keras" / "shape2sc22a.h5").exists()
HAS_H2 = (CACHE / ZENODO_SUBDIR / H2_SC_MAT).exists()
HAS_F8 = (CACHE / ZENODO_SUBDIR / FIGURE8_MAT).exists()
#: Agreement that can only come from the same arithmetic: float-rounding, not approximation.
EXACT_INPUT_TOLERANCE = 1e-6


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


# ------------------------------------------------- CP7-A input contract and faithfulness

#: The contour classes the official chain is documented to carry.
LEGAL_CONTOURS = [
    "clockwise_ring",
    "counterclockwise_ring",
    "duplicated_first_and_last",
    "consecutive_duplicate_vertices",
    "near_circle",
    "five_point_star",
    "thin_forked",
    "self_intersecting",
]
#: The contour classes that would make the official chain emit NaN or an all-zero input.
ILLEGAL_CONTOURS = ["all_vertices_coincident", "too_few_vertices", "nan_vertex"]

#: (shape index, deposited file, traversal, cyclic start offset) that reach float-rounding
#: agreement with the published KerNet2 coordinates -- the CP7-A exactness evidence.
H2_EXACT_ALIGNMENTS = [
    (0, "keras_file", "as_stored", 0),
    (1, "keras_file", "as_stored", 0),
    (2, "fig7D_file", "as_stored", 3),
    (6, "fig7D_file", "as_stored", 0),
    (11, "keras_file", "as_stored", 0),
    (12, "keras_file", "as_stored", 0),
    (13, "keras_file", "as_stored", 1),
    (14, "keras_file", "as_stored", 1),
]


def edge_contours() -> dict[str, np.ndarray]:
    theta = np.linspace(0.0, 2.0 * np.pi, 240, endpoint=False)
    ring = np.column_stack([np.cos(theta), np.sin(theta)])
    star = np.column_stack(
        [(1.0 + 0.6 * np.cos(5 * theta)) * np.cos(theta),
         (1.0 + 0.6 * np.cos(5 * theta)) * np.sin(theta)]
    )
    t = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    return {
        "clockwise_ring": ring[::-1],
        "counterclockwise_ring": ring,
        "duplicated_first_and_last": np.vstack([star, star[:1]]),
        "consecutive_duplicate_vertices": np.repeat(star, 2, axis=0),
        "near_circle": ring,
        "five_point_star": star,
        "thin_forked": np.array(
            [[0, 0], [0, 1], [-0.5, 2], [0, 1.4], [0.5, 2], [0, 1], [1, 0], [0, 0]], float
        ),
        "all_vertices_coincident": np.zeros((120, 2)),
        "self_intersecting": np.column_stack([np.sin(2 * t), np.sin(t)]),
        "too_few_vertices": np.array([[0.0, 0.0], [1.0, 1.0]]),
        "nan_vertex": np.vstack([ring, np.array([[np.nan, 0.5]])]),
    }


def _matlab_resample_polyline_loop(poly: np.ndarray, n: int) -> np.ndarray:
    """The literal transcription of ``resamplePolyline.m`` lines 35-61, loops and all."""
    s = port._parametrize(poly)
    total = float(s[-1])
    out = np.zeros((n, 2))
    for i, p in enumerate(np.linspace(0.0, total, n)):
        ind0 = int(np.nonzero(s <= p)[0][-1])  # find(s <= pos, 1, 'last')
        ind1 = int(np.nonzero(s >= p)[0][0])  # find(s >= pos, 1, 'first')
        if ind0 == ind1:
            out[i] = poly[ind0]
            continue
        l0, l1 = p - s[ind0], s[ind1] - p
        out[i] = (
            poly[ind0]
            if l0 + l1 <= total * 1e-12
            else (poly[ind0] * l1 + poly[ind1] * l0) / (l0 + l1)
        )
    return out


def test_vectorised_resample_is_bit_equal_to_the_matlab_loop() -> None:
    """The speed-up must not change a single sample, so compare against the loop itself."""
    rng = np.random.default_rng(7)
    rows = equal = 0
    for _ in range(40):
        poly = rng.normal(size=(int(rng.integers(3, 300)), 2))
        if rng.random() < 0.5:
            poly = np.repeat(poly, 2, axis=0)  # duplicated vertices -> ties in s
        if rng.random() < 0.3:
            poly = np.vstack([poly, poly[:1]])  # already-sealed rings
        ring = np.vstack([poly, poly[:1]])
        n = int(rng.integers(30, 400))
        rows += n
        vectorised = port._resample_polyline(ring, n)
        equal += int(np.array_equal(_matlab_resample_polyline_loop(ring, n), vectorised))
    assert equal == 40 and rows > 5000


@pytest.mark.parametrize("name", LEGAL_CONTOURS)
def test_legal_contour_classes_reach_both_network_inputs(name: str) -> None:
    """Legal inputs produce finite, non-empty network inputs -- never a silent zero vector."""
    contour = edge_contours()[name]
    vector = port.kernet1_input(contour)
    image = port.kernet2_input(contour)
    assert vector.shape == (768,) and image.shape == (40, 40, 1)
    assert np.isfinite(vector).all() and np.isfinite(image).all()
    assert vector.max() > 0.0
    assert image.sum() > 0.0


@pytest.mark.parametrize("name", ILLEGAL_CONTOURS)
def test_illegal_contour_classes_are_refused_not_silently_degraded(name: str) -> None:
    """Refusal must be explicit: no zero vector, no NaN, and no unexplained error type."""
    contour = edge_contours()[name]
    for build in (port.kernet1_input, port.kernet2_input):
        with pytest.raises(port.ShapeCompInputError):
            build(contour)


def test_input_refusal_is_a_value_error_subclass() -> None:
    """Callers that only catch ValueError still see the refusal (no new exception hierarchy)."""
    assert issubclass(port.ShapeCompInputError, ValueError)


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
def test_legal_contours_give_finite_nonzero_22d_on_both_nets() -> None:
    nets = port.ShapeCompNets()
    for name in LEGAL_CONTOURS:
        contour = edge_contours()[name]
        for fn in (nets.kernet1, nets.kernet2):
            coords = fn(contour)
            assert coords.shape == (22,)
            assert np.isfinite(coords).all(), name
            assert float(np.linalg.norm(coords)) > 0.0, name


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
def test_forward_pass_is_bit_identical_across_repeats() -> None:
    """No dropout at inference, no hidden state: the same input must give the same bits."""
    nets = port.ShapeCompNets()
    for name, contour in edge_contours().items():
        if name in ILLEGAL_CONTOURS:
            continue
        for fn in (nets.kernet1, nets.kernet2):
            runs = np.array([fn(contour) for _ in range(3)])
            assert np.unique(runs, axis=0).shape[0] == 1, name


@pytest.mark.skipif(not HAS_TOOLBOX, reason="official toolbox not downloaded")
def test_one_raster_pixel_flips_move_kernet2_coordinates_substantially() -> None:
    """KerNet2's 40x40 input is near-discontinuous: locked in because every re-rasterisation
    (including rotating a contour before rasterising) inherits this error."""
    nets = port.ShapeCompNets()
    mask = port.inpolygon(port.kernet2_points(polygon(lobes=4)))
    base = nets.kernet2_from_mask(mask)
    scale = float(np.linalg.norm(base))
    rng = np.random.default_rng(20261108)
    flat = mask.ravel()
    shifts = []
    for index in rng.choice(flat.size, 24, replace=False):
        flipped = flat.copy()
        flipped[index] = ~flipped[index]
        shifted = nets.kernet2_from_mask(flipped.reshape(mask.shape))
        shifts.append(float(np.linalg.norm(shifted - base)))
    assert scale > 0.0
    assert float(np.median(shifts)) / scale > 0.05


def _h2_deposits() -> tuple[dict[str, list[np.ndarray]], np.ndarray]:
    base = CACHE / ZENODO_SUBDIR
    keras = sio.loadmat(base / H2_SC_MAT, squeeze_me=True, struct_as_record=False)
    human = sio.loadmat(base / H2_HUMAN_MAT, squeeze_me=True, struct_as_record=False)
    return (
        {
            "keras_file": [np.asarray(e.c, dtype=float) for e in keras["stim"]],
            "fig7D_file": [np.asarray(e.c, dtype=float) for e in human["stim"]],
        },
        np.asarray(keras["sc"], dtype=float),
    )


@pytest.mark.skipif(not (HAS_TOOLBOX and HAS_H2), reason="toolbox or Zenodo H2 cache absent")
def test_aligned_h2_inputs_reproduce_published_coordinates_to_float_rounding() -> None:
    """The strongest faithfulness claim the deposit allows: same input bits -> same output bits.

    Where the deposited contour is rolled to the sampling phase the authors used, the Python
    KerNet2 output matches the published 22-D coordinates to ~1e-7, which pins the weight
    loading, conv/pool/ReLU order, C-order flatten, ``inpolygon`` and ``flipud`` together.
    """
    sources, ref = _h2_deposits()
    nets = port.ShapeCompNets()
    for index, name, direction, phase in H2_EXACT_ALIGNMENTS:
        contour = sources[name][index]
        ring = contour if direction == "as_stored" else contour[::-1]
        error = float(
            np.linalg.norm(nets.kernet2(np.roll(ring, phase, axis=0)) - ref[index])
        )
        assert error < EXACT_INPUT_TOLERANCE, (index, name, direction, phase, error)
    assert len(H2_EXACT_ALIGNMENTS) >= 8


@pytest.mark.skipif(not (HAS_TOOLBOX and HAS_H2), reason="toolbox or Zenodo H2 cache absent")
def test_unaligned_h2_residuals_are_reported_as_unattributed_not_explained() -> None:
    """The other seven shapes stay far above float rounding under a bounded phase search, so
    their residual is *not* claimed as a preprocessing match: it is an input-provenance gap."""
    sources, ref = _h2_deposits()
    nets = port.ShapeCompNets()
    exact = {row[0] for row in H2_EXACT_ALIGNMENTS}
    for index in range(len(ref)):
        if index in exact:
            continue
        best = float("inf")
        for name in sources:
            contour = sources[name][index]
            for ring in (contour, contour[::-1]):
                for phase in range(0, len(ring), 12):
                    rolled = np.roll(ring, phase, axis=0)
                    best = min(best, float(np.linalg.norm(nets.kernet2(rolled) - ref[index])))
        assert best > 100 * EXACT_INPUT_TOLERANCE, (index, best)


@pytest.mark.skipif(
    not (HAS_TOOLBOX and HAS_H2 and HAS_F8), reason="toolbox or Zenodo H2/Figure 8 cache absent"
)
def test_inpolygon_matches_geos_covers_on_official_contours() -> None:
    """The hand-translated MATLAB primitive must agree with GEOS pixel-for-pixel on real rings,
    self-intersecting ones included, so replacing shapely changed no rasterisation."""
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import Polygon

    sources, _ = _h2_deposits()
    raw = sio.loadmat(
        CACHE / ZENODO_SUBDIR / FIGURE8_MAT, squeeze_me=True, struct_as_record=False
    )
    contours = list(sources["keras_file"]) + [
        np.asarray(entry.c, dtype=float) for entry in raw["shapes"][:60]
    ]
    gx, gy = port._grid_points()
    grid = shapely.points(gx, gy)
    n_invalid = 0
    for contour in contours:
        ring = port.kernet2_points(contour)
        polygon_geom = Polygon(ring)
        n_invalid += int(not polygon_geom.is_valid)
        geos = np.asarray(shapely.covers(polygon_geom, grid)).reshape(40, 40, order="F")
        assert int((np.flipud(geos) != port.inpolygon(ring)).sum()) == 0
    assert n_invalid > 0, "Figure 8 was expected to contain at least one self-intersecting ring"