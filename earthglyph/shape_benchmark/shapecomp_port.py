"""Faithful Python port of the official ShapeComp neural approximations KerNet1/KerNet2.

The authors' published Keras weights (Keras 2.1.2 ``.h5``) are read with h5py and run
through a numpy forward pass — **no TensorFlow, no retraining, no GPU**. Only the MATLAB
*preprocessing* is translated; the network weights are the official ones, used verbatim.
This is the official neural approximation executed in Python, NOT a self-written
descriptor port, and it is validated against the authors' published 22-D coordinates.

Translated from the manually downloaded toolbox ``shapeComp_17_12_2024/`` (gitignored;
the upstream repo carries only a "please cite" notice, no formal redistribution licence):

* ``ShapeComp_src/getShapeComp22netkeras.m``      -> KerNet1 contour contract (768-D)
* ``ShapeComp_src/getShapeComp22netimagekeras.m`` -> KerNet2 image contract (40x40x1)
* ``misc/geom2d`` (BSD-3, INRA/D. Legland): ``centroid.m``, ``resamplePolygon.m``,
  ``resamplePolyline.m``, ``parametrize.m``

Cite: Morgenstern et al. (2021) PLoS Comput Biol 10.1371/journal.pcbi.1008981;
Carbajal Chavez et al. PsyArXiv. The official input contract (clockwise, leftmost start,
384 points, [0,1] normalisation, column-major flatten) is preserved exactly and is
deliberately NOT replaced by EarthGlyph's 128-point contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import shapely

DEFAULT_TOOLBOX = Path(__file__).resolve().parents[2] / "shapeComp_17_12_2024"
_KERNET1_H5 = "net/keras/shape2sc22a.h5"
_KERNET2_H5 = "net/keras/im2sc22d.h5"
_IMAGE_SIZE = 40


def _centroid(pts: np.ndarray) -> np.ndarray:
    """geom2d ``centroid.m`` with no mass: the mean of the points (not area centroid)."""
    return pts.mean(axis=0)


def _parametrize(poly: np.ndarray) -> np.ndarray:
    """geom2d ``parametrize.m``: cumulative Euclidean edge length, first vertex at 0."""
    delta = np.diff(poly, axis=0)
    steps = np.hypot(delta[:, 0], delta[:, 1])
    return np.concatenate(([0.0], np.cumsum(steps)))


def _resample_polyline(poly: np.ndarray, n: int) -> np.ndarray:
    """geom2d ``resamplePolyline.m``: n points equally spaced by arc length, linear interp.

    Replicates the MATLAB ``find(s<=pos,'last')`` / ``find(s>=pos,'first')`` neighbour
    search and the ``Lmax*1e-12`` guard against coincident neighbours.
    """
    s = _parametrize(poly)
    total = float(s[-1])
    pos = np.linspace(0.0, total, n)
    out = np.empty((n, poly.shape[1]), dtype=float)
    last = len(s) - 1
    for i, p in enumerate(pos):
        ind0 = max(int(np.searchsorted(s, p, side="right")) - 1, 0)
        ind1 = min(int(np.searchsorted(s, p, side="left")), last)
        if ind0 == ind1:
            out[i] = poly[ind0]
            continue
        l0 = p - s[ind0]
        l1 = s[ind1] - p
        if (l0 + l1) > total * 1e-12:
            out[i] = (poly[ind0] * l1 + poly[ind1] * l0) / (l0 + l1)
        else:
            out[i] = poly[ind0]
    return out


def _resample_polygon(poly: np.ndarray, n: int) -> np.ndarray:
    """geom2d ``resamplePolygon.m``: seal the ring, resample to n+1, drop the duplicate."""
    closed = np.vstack([poly, poly[:1]])
    return _resample_polyline(closed, n + 1)[:-1]


def _adjust_contour_pos(contour: np.ndarray) -> np.ndarray:
    """Inline ``adjustContourPos``: shift min to 0, then divide by the new max -> [0,1]."""
    low = float(contour.min())
    high = float(contour.max())
    return (contour - low) / (high - low)


def _is_clockwise(pts: np.ndarray) -> int:
    """Inline ``clockwise(P)`` with ``P = pts'``: 1 clockwise, -1 anti-clockwise.

    Judged from the turn at the first topmost (max-y) vertex, exactly as the MATLAB.
    """
    x = pts[:, 0]
    y = pts[:, 1]
    n = len(pts)
    c = int(np.argmax(y))
    a = (c - 1) % n
    b = (c + 1) % n
    xa, ya = x[a], y[a]
    xb, yb = x[b], y[b]
    xc, yc = x[c], y[c]
    if xa <= xc:
        if xb >= xc:
            return 1
        if xa == xc:
            return -1
        ka = (yc - ya) / (xc - xa)
        kb = (yc - yb) / (xc - xb)
        return -1 if ka > kb else 1
    if xb <= xc:
        return -1
    ka = (yc - ya) / (xc - xa)
    kb = (yc - yb) / (xc - xb)
    return -1 if ka > kb else 1


def kernet1_input(contour: np.ndarray) -> np.ndarray:
    """``getShapeComp22netkeras.m`` preprocessing -> the 768-D network input.

    clockwise -> centre by mean -> [0,1] -> resample 1000 -> drop last (999) ->
    start at leftmost x -> [0,1] -> seal -> resample 384 -> [0,1] -> column-major flatten.
    MATLAB ``points(:)'`` is column-major, so the vector is [x_1..x_384, y_1..y_384].
    """
    pts = np.asarray(contour, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(f"contour must be (N,2), got {pts.shape}")
    if _is_clockwise(pts) == -1:
        pts = pts[::-1]
    pts = pts - _centroid(pts)
    p1 = _resample_polygon(_adjust_contour_pos(pts), 1000)[:-1]
    start = int(np.argmin(p1[:, 0]))
    points = _adjust_contour_pos(np.roll(p1, -start, axis=0))
    points = np.vstack([points, points[:1]])
    points = _adjust_contour_pos(_resample_polygon(points, 384))
    return np.ravel(points, order="F")


def kernet2_input(contour: np.ndarray) -> np.ndarray:
    """``getShapeComp22netimagekeras.m`` preprocessing -> the 40x40x1 binary image.

    centre by mean -> [0,1] -> resample 1000 -> resample 384 -> [0,1] -> rasterise with
    ``inpolygon`` on a linspace(0,1,40) meshgrid -> ``flipud``. No clockwise/leftmost step
    (those lines are commented out in the source). ``shapely.covers`` matches MATLAB
    ``inpolygon``'s inside-or-on-boundary semantics.
    """
    pts = np.asarray(contour, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(f"contour must be (N,2), got {pts.shape}")
    pts = pts - _centroid(pts)
    p1 = _resample_polygon(_adjust_contour_pos(pts), 1000)
    points = _adjust_contour_pos(_resample_polygon(p1, 384))
    lv = np.linspace(0.0, 1.0, _IMAGE_SIZE)
    gx, gy = np.meshgrid(lv, lv)
    grid_x = np.ravel(gx, order="F")
    grid_y = np.ravel(gy, order="F")
    polygon = shapely.Polygon(points)
    inside = shapely.covers(polygon, shapely.points(grid_x, grid_y))
    mask = np.flipud(np.asarray(inside, dtype=bool).reshape((_IMAGE_SIZE, _IMAGE_SIZE), order="F"))
    return mask.astype(np.float32)[..., None]


@dataclass(frozen=True)
class _Dense:
    kernel: np.ndarray
    bias: np.ndarray
    relu: bool


@dataclass(frozen=True)
class _Conv:
    kernel: np.ndarray
    bias: np.ndarray


def _dense_forward(x: np.ndarray, layer: _Dense) -> np.ndarray:
    y: np.ndarray = x @ layer.kernel + layer.bias
    out: np.ndarray = np.maximum(y, 0.0) if layer.relu else y
    return out


def _conv2d_same(x: np.ndarray, layer: _Conv) -> np.ndarray:
    """Keras Conv2D, channels_last, 3x3 stride-1 'same', ReLU (cross-correlation)."""
    kh, kw = layer.kernel.shape[0], layer.kernel.shape[1]
    pad = np.pad(x, ((kh // 2, kh // 2), (kw // 2, kw // 2), (0, 0)), mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(pad, (kh, kw), axis=(0, 1))
    acc: np.ndarray = np.einsum("hwcij,ijco->hwo", windows, layer.kernel, optimize=True)
    out: np.ndarray = np.maximum(acc + layer.bias, 0.0)
    return out


def _maxpool2d(x: np.ndarray, pool: int = 2) -> np.ndarray:
    h, w, c = x.shape
    return x.reshape(h // pool, pool, w // pool, pool, c).max(axis=(1, 3))


def _read_dense_layers(handle: h5py.File, names: list[str]) -> list[_Dense]:
    config = json.loads(handle.attrs["model_config"])["config"]
    activation = {
        layer["config"]["name"]: layer["config"].get("activation", "linear")
        for layer in config
        if layer["class_name"] == "Dense"
    }
    weights = handle["model_weights"]
    layers: list[_Dense] = []
    for name in names:
        group = weights[name][name]
        layers.append(
            _Dense(
                kernel=np.asarray(group["kernel:0"], dtype=np.float64),
                bias=np.asarray(group["bias:0"], dtype=np.float64),
                relu=activation[name] == "relu",
            )
        )
    return layers


def _read_conv_layers(handle: h5py.File, names: list[str]) -> list[_Conv]:
    weights = handle["model_weights"]
    layers: list[_Conv] = []
    for name in names:
        group = weights[name][name]
        layers.append(
            _Conv(
                kernel=np.asarray(group["kernel:0"], dtype=np.float64),
                bias=np.asarray(group["bias:0"], dtype=np.float64),
            )
        )
    return layers


class ShapeCompNets:
    """Load the official KerNet1/KerNet2 weights once and score closed contours.

    Parameters
    ----------
    toolbox:
        Path to the downloaded ``shapeComp_17_12_2024`` directory. Defaults to the
        repo-root sibling of this package. The directory is gitignored; only this port
        and its validation results are committed.
    """

    def __init__(self, toolbox: Path = DEFAULT_TOOLBOX) -> None:
        toolbox = Path(toolbox)
        with h5py.File(toolbox / _KERNET1_H5, "r") as handle:
            self._k1_dense = _read_dense_layers(
                handle, [f"dense_{i}" for i in range(1, 8)]
            )
        with h5py.File(toolbox / _KERNET2_H5, "r") as handle:
            self._k2_conv = _read_conv_layers(handle, ["conv2d_4", "conv2d_5", "conv2d_6"])
            self._k2_dense = _read_dense_layers(handle, ["dense_3", "dense_4"])

    def kernet1(self, contour: np.ndarray) -> np.ndarray:
        """Contour (N,2) -> official KerNet1 22-D ShapeComp coordinates."""
        x = kernet1_input(contour)
        for layer in self._k1_dense:
            x = _dense_forward(x, layer)
        return np.asarray(x, dtype=float)

    def kernet2(self, contour: np.ndarray) -> np.ndarray:
        """Contour (N,2) -> official KerNet2 22-D ShapeComp coordinates (via 40x40 image)."""
        x = kernet2_input(contour).astype(np.float64)
        for conv in self._k2_conv:
            x = _conv2d_same(x, conv)
            x = _maxpool2d(x)
        x = np.ravel(x, order="C")
        for layer in self._k2_dense:
            x = _dense_forward(x, layer)
        return np.asarray(x, dtype=float)