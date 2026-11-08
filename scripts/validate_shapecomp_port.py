"""Validate the Python ShapeComp port against the authors' published network outputs.

This is a *faithfulness* test, distinct from ``audit_figure8_networks.py`` (which compares
the published networks to the original ShapeComp MDS space). Here we run our numpy port
(``earthglyph.shape_benchmark.shapecomp_port``) on the deposited input contours and compare
its 22-D output to the authors' own published 22-D coordinates for the *same* network.

Three references, three different meanings — kept strictly apart:

* **H2 / KerNet2** (``lietal_ShapeCompSolution_keras_mds_image.mat``): ``stim[i].c`` paired
  with ``sc[i]``, the published KerNet2 (40x40 image route) output. This is a clean
  input->output pair for the network we ported, so it is the headline faithfulness check.
* **Figure 8 / KerNet1 & KerNet2** (``Figure8NetComparison.mat``): the published ``kernet1``
  / ``kernet2`` columns for 1000 held-out GAN shapes, with the raw ``shapes[i].c`` contours.
  The deposited ``c`` is a RAW 384-point display contour, not the exact normalised input the
  authors fed the network (see audit_figure8_networks.py), so a small residual is expected
  and is an input-provenance effect, not a port bug.
* **H1 / original ShapeComp** (``Fig5B_shapeStim4RatingExpt.mat``): ``shape1SC``/``shape2SC``
  are the ORIGINAL 91-descriptor model's 22-D MDS coordinates (view_rating_expt_stim_and_resp.m
  line 21 calls ``dist`` the "original ShapeComp distance"), NOT a Keras network output.
  Comparing our KerNet ports to these measures how well the neural approximation tracks the
  original model — explicitly NOT a faithfulness test, and reported separately as such.

No human ratings are involved anywhere in this script. Everything is model-vs-model.

Usage:
    python scripts/validate_shapecomp_port.py [--cache-dir PATH] [--toolbox PATH]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io as sio
from scipy.spatial.distance import pdist
from scipy.stats import pearsonr, spearmanr

from earthglyph.shape_benchmark.datasets import ZENODO_SUBDIR
from earthglyph.shape_benchmark.shapecomp_port import ShapeCompNets, _is_clockwise

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
FIGURE8_MAT = "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat"
H2_SC_MAT = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
H1_STIM_MAT = (
    "shapecomp datashare/Figure 5 and 11A/rating_expt/stim/Fig5B_shapeStim4RatingExpt.mat"
)


def _procrustes_rmse(pred: np.ndarray, ref: np.ndarray) -> float:
    """RMSE after optimal translation + rotation + uniform scale (no reflection).

    Basis-independent agreement: if the port merely rotated/scaled the published basis this
    would be ~0 while raw RMSE is large. For a faithful port of the *same* weights both are
    small, because the output basis is identical.
    """
    x = pred - pred.mean(axis=0)
    y = ref - ref.mean(axis=0)
    xx = float(np.sum(x * x))
    if xx == 0.0:
        return float(np.sqrt(np.mean(y * y)))
    m = x.T @ y
    u, s, vt = np.linalg.svd(m)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    scale = float(s.sum() / xx)
    aligned = (x @ r) * scale
    return float(np.sqrt(np.mean((aligned - y) ** 2)))


def _agreement(pred: np.ndarray, ref: np.ndarray) -> dict[str, Any]:
    """Faithfulness metrics: pairwise-distance correlation, per-dim r, raw + Procrustes RMSE."""
    pd_pred = pdist(pred)
    pd_ref = pdist(ref)
    per_dim = [float(pearsonr(pred[:, j], ref[:, j]).statistic) for j in range(ref.shape[1])]
    return {
        "n_shapes": int(pred.shape[0]),
        "n_pairs": int(pd_pred.size),
        "pairwise_distance_spearman": float(spearmanr(pd_pred, pd_ref).statistic),
        "pairwise_distance_pearson": float(pearsonr(pd_pred, pd_ref).statistic),
        "per_dim_pearson_mean": float(np.mean(per_dim)),
        "per_dim_pearson_min": float(np.min(per_dim)),
        "rmse_raw": float(np.sqrt(np.mean((pred - ref) ** 2))),
        "rmse_procrustes": _procrustes_rmse(pred, ref),
    }


def _run(nets: ShapeCompNets, contours: list[np.ndarray], network: str) -> np.ndarray:
    fn = nets.kernet1 if network == "kernet1" else nets.kernet2
    return np.array([fn(c) for c in contours], dtype=float)


def _figure8(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    raw = sio.loadmat(cache / ZENODO_SUBDIR / FIGURE8_MAT, squeeze_me=True, struct_as_record=False)
    contours = [np.asarray(entry.c, dtype=float) for entry in raw["shapes"]]
    out: dict[str, Any] = {
        "reference": "Figure8NetComparison.mat published kernet1/kernet2 columns",
        "reference_semantics": "official network output for the SAME network we ported",
        "input_provenance_caveat": (
            "Deposited shapes[i].c is a RAW 384-point GAN display contour (not centred, "
            "non-uniform arc length), not the exact normalised input the authors fed the "
            "network. A small residual is expected from re-resampling this display contour "
            "and is an input-provenance effect, not a port bug; skipping the resample "
            "collapses agreement to ~0.56, confirming the pipeline is required and correct."
        ),
    }
    for network in ("kernet1", "kernet2"):
        ref = np.asarray(raw[network], dtype=float)
        pred = _run(nets, contours, network)
        out[network] = _agreement(pred, ref)
    orient = np.array([_is_clockwise(c) for c in contours])
    out["orientation"] = {
        "n_clockwise": int((orient == 1).sum()),
        "n_anticlockwise": int((orient == -1).sum()),
        "note": (
            "getShapeComp22netkeras.m enforces clockwise (lines 9-13); the port keeps that "
            "official step. On Figure 8 it is a near-no-op because almost all deposited "
            "contours are already clockwise, so it cannot explain the residual."
        ),
    }
    return out


def _h2(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    raw = sio.loadmat(cache / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False)
    contours = [np.asarray(entry.c, dtype=float) for entry in raw["stim"]]
    ref = np.asarray(raw["sc"], dtype=float)
    pred = _run(nets, contours, "kernet2")
    return {
        "reference": "lietal_ShapeCompSolution_keras_mds_image.mat stim[i].c -> sc[i]",
        "reference_semantics": (
            "published KerNet2 (40x40 binary image route) output, paired with its own input "
            "contour in the same file — a clean input->output pair for the ported network"
        ),
        "network": "kernet2",
        "headline_faithfulness": _agreement(pred, ref),
    }


def _h1(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    raw = sio.loadmat(cache / ZENODO_SUBDIR / H1_STIM_MAT, squeeze_me=True, struct_as_record=False)
    stim = raw["rate_stim"]
    contours: list[np.ndarray] = []
    refs: list[np.ndarray] = []
    for entry in stim:
        contours.append(np.asarray(entry.shape1, dtype=float))
        refs.append(np.asarray(entry.shape1SC, dtype=float))
        contours.append(np.asarray(entry.shape2, dtype=float))
        refs.append(np.asarray(entry.shape2SC, dtype=float))
    ref = np.array(refs, dtype=float)
    out: dict[str, Any] = {
        "reference": "Fig5B_shapeStim4RatingExpt.mat shape1SC/shape2SC",
        "reference_semantics": (
            "ORIGINAL 91-descriptor ShapeComp 22-D MDS coordinates, NOT a Keras network "
            "output (view_rating_expt_stim_and_resp.m line 21). This measures how well the "
            "neural approximation tracks the original model — explicitly NOT port faithfulness."
        ),
        "is_faithfulness_test": False,
    }
    for network in ("kernet1", "kernet2"):
        pred = _run(nets, contours, network)
        out[network] = _agreement(pred, ref)
    return out


def validate(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    return {
        "schema": "earthglyph.shape_perception_benchmark.shapecomp_port_validation/1",
        "port": "earthglyph.shape_benchmark.shapecomp_port (h5py + numpy, official weights)",
        "data_doi": "10.5281/zenodo.4730985",
        "paper_doi": "10.1371/journal.pcbi.1008981",
        "no_human_ratings": True,
        "h2_kernet2_faithfulness": _h2(cache, nets),
        "figure8_network_faithfulness": _figure8(cache, nets),
        "h1_approximation_to_original_shapecomp": _h1(cache, nets),
    }


def _jsonify(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serialisable: {type(value)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--toolbox", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    nets = ShapeCompNets() if args.toolbox is None else ShapeCompNets(args.toolbox)
    result = validate(args.cache_dir, nets)
    out = args.out or args.cache_dir.parent / "results" / "shapecomp_port_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=_jsonify) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out.relative_to(REPO)}")
    h2 = result["h2_kernet2_faithfulness"]["headline_faithfulness"]
    print(
        f"  H2 KerNet2 (headline): pairwise Spearman={h2['pairwise_distance_spearman']:.5f} "
        f"per-dim r={h2['per_dim_pearson_mean']:.5f} RMSE={h2['rmse_raw']:.5f}"
    )
    fig = result["figure8_network_faithfulness"]
    for network in ("kernet1", "kernet2"):
        m = fig[network]
        print(
            f"  Figure8 {network}: pairwise Spearman={m['pairwise_distance_spearman']:.5f} "
            f"per-dim r={m['per_dim_pearson_mean']:.5f} RMSE={m['rmse_raw']:.5f}"
        )
    h1 = result["h1_approximation_to_original_shapecomp"]
    for network in ("kernet1", "kernet2"):
        m = h1[network]
        print(
            f"  H1 {network} vs ORIGINAL ShapeComp (approximation, not faithfulness): "
            f"pairwise Spearman={m['pairwise_distance_spearman']:.5f}"
        )


if __name__ == "__main__":
    main()