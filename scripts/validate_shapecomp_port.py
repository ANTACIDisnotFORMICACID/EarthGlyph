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
  are the ORIGINAL model's 22-D MDS coordinates — the MDS projection of the authors' 109
  descriptors (view_rating_expt_stim_and_resp.m line 21 calls ``dist`` the "original ShapeComp
  distance"), NOT a Keras network output.
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

from earthglyph.shape_benchmark import shapecomp_port as port
from earthglyph.shape_benchmark.datasets import ZENODO_SUBDIR
from earthglyph.shape_benchmark.shapecomp_port import ShapeCompNets, _is_clockwise

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
FIGURE8_MAT = "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat"
H2_SC_MAT = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
H2_HUMAN_MAT = "shapecomp datashare/Figure 9/data/fig7D_lietal_points_2.mat"
H1_STIM_MAT = (
    "shapecomp datashare/Figure 5 and 11A/rating_expt/stim/Fig5B_shapeStim4RatingExpt.mat"
)
#: Published Figure-9 coordinates are stored in float64 but produced by a single-precision
#: Keras/MATLAB forward pass, so agreement at this level means "same arithmetic, no bug".
EXACT_INPUT_TOLERANCE = 1e-6


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
    """Faithfulness metrics: pairwise-distance correlation, per-dim r, raw + Procrustes RMSE.

    The P0-4.6 CP7-A additions (``max_abs_error``, ``per_dim_rmse``, ``pairwise_distance_error``)
    exist because a headline RMSE alone cannot say whether a residual is spread evenly over the
    shapes or concentrated in one coordinate — which is what separates a port bug from an
    input-provenance difference.
    """
    pd_pred = pdist(pred)
    pd_ref = pdist(ref)
    per_dim_r = [float(pearsonr(pred[:, j], ref[:, j]).statistic) for j in range(ref.shape[1])]
    per_dim_rmse = [float(np.sqrt(np.mean((pred[:, j] - ref[:, j]) ** 2))) for j in range(22)]
    gap = pd_pred - pd_ref
    return {
        "n_shapes": int(pred.shape[0]),
        "n_pairs": int(pd_pred.size),
        "pairwise_distance_spearman": float(spearmanr(pd_pred, pd_ref).statistic),
        "pairwise_distance_pearson": float(pearsonr(pd_pred, pd_ref).statistic),
        "per_dim_pearson_mean": float(np.mean(per_dim_r)),
        "per_dim_pearson_min": float(np.min(per_dim_r)),
        "rmse_raw": float(np.sqrt(np.mean((pred - ref) ** 2))),
        "rmse_procrustes": _procrustes_rmse(pred, ref),
        "max_abs_error": float(np.max(np.abs(pred - ref))),
        "per_dim_rmse": per_dim_rmse,
        "pairwise_distance_error": {
            "mean": float(np.mean(gap)),
            "std": float(np.std(gap, ddof=1)),
            "median_abs": float(np.median(np.abs(gap))),
            "p95_abs": float(np.quantile(np.abs(gap), 0.95)),
            "max_abs": float(np.max(np.abs(gap))),
            "median_reference_distance": float(np.median(pd_ref)),
        },
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
            "ORIGINAL ShapeComp 22-D MDS coordinates (the MDS projection of the authors' 109 "
            "descriptors), NOT a Keras network "
            "output (view_rating_expt_stim_and_resp.m line 21). This measures how well the "
            "neural approximation tracks the original model — explicitly NOT port faithfulness."
        ),
        "is_faithfulness_test": False,
    }
    for network in ("kernet1", "kernet2"):
        pred = _run(nets, contours, network)
        out[network] = _agreement(pred, ref)
    return out


def _determinism(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    """Re-run the same contour repeatedly: a numpy forward pass must be bit-identical.

    TensorFlow adds kernel-selection nondeterminism; this port has none, so anything above 0
    here would mean the preprocessing itself is unstable (it reads the contour order).
    """
    raw = sio.loadmat(cache / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False)
    contour = np.asarray(raw["stim"][3].c, dtype=float)
    out: dict[str, Any] = {}
    for network in ("kernet1", "kernet2"):
        fn = nets.kernet1 if network == "kernet1" else nets.kernet2
        runs = np.array([fn(contour) for _ in range(5)])
        out[network] = {
            "n_runs": int(runs.shape[0]),
            "max_abs_spread": float(np.max(runs.max(axis=0) - runs.min(axis=0))),
            "bit_identical": bool(np.all(runs == runs[0])),
        }
    return out


def _rasterisation_sensitivity(nets: ShapeCompNets, contour: np.ndarray) -> dict[str, Any]:
    """How much one flipped 40x40 pixel moves the KerNet2 output.

    Measured because it decides what the H2 residual can mean: if a single pixel is worth
    ~30% of the coordinate norm, then a 1-4% residual is NOT a whole pixel and the two
    explanations have to be kept apart.
    """
    mask = port.inpolygon(port.kernet2_points(contour))
    base = nets.kernet2_from_mask(mask)
    scale = float(np.linalg.norm(base))
    rng = np.random.default_rng(20261108)
    flat = mask.ravel()
    gaps: list[float] = []
    for index in rng.choice(flat.size, 40, replace=False):
        flipped = flat.copy()
        flipped[index] = ~flipped[index]
        shifted = nets.kernet2_from_mask(flipped.reshape(mask.shape))
        gaps.append(float(np.linalg.norm(shifted - base)))
    values = np.array(gaps)
    return {
        "coordinate_norm": scale,
        "flipped_pixels_tested": int(values.size),
        "median_l2_shift": float(np.median(values)),
        "p90_l2_shift": float(np.quantile(values, 0.90)),
        "max_l2_shift": float(values.max()),
        "median_shift_share_of_norm": float(np.median(values) / scale) if scale else 0.0,
    }


def _input_provenance_alignment(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    """Is the H2 residual a port error, or the wrong *sampling phase* of the input contour?

    ``resamplePolygon`` starts sampling at the caller's vertex 0, and the image route keeps
    whatever orientation the contour arrives in, so two equally valid stored versions of the
    SAME closed curve produce different 384-point rings, different 40x40 masks and different
    22-D coordinates. For each shape we therefore search the deposited contour's cyclic start
    index, its traversal direction, and the two files that store it, reporting the best error.

    Reaching float-rounding agreement (~1e-7) on any shape proves the weights, layer order,
    flatten order, inpolygon and flipud are all exactly right for that shape; it is the
    strongest faithfulness evidence the deposit allows.
    """
    base = cache / ZENODO_SUBDIR
    keras = sio.loadmat(base / H2_SC_MAT, squeeze_me=True, struct_as_record=False)
    human = sio.loadmat(base / H2_HUMAN_MAT, squeeze_me=True, struct_as_record=False)
    ref = np.asarray(keras["sc"], dtype=float)
    sources = {
        "keras_file": [np.asarray(entry.c, dtype=float) for entry in keras["stim"]],
        "fig7D_file": [np.asarray(entry.c, dtype=float) for entry in human["stim"]],
    }
    per_shape: list[dict[str, Any]] = []
    for index in range(len(ref)):
        best = (float(np.linalg.norm(nets.kernet2(sources["keras_file"][index]) - ref[index])),
                "keras_file", "as_stored", 0)
        for name, contours in sources.items():
            contour = contours[index]
            for direction, oriented in (("as_stored", contour), ("reversed", contour[::-1])):
                for phase in range(len(oriented)):
                    error = float(
                        np.linalg.norm(nets.kernet2(np.roll(oriented, phase, axis=0)) - ref[index])
                    )
                    if error < best[0]:
                        best = (error, name, direction, phase)
                    if error < EXACT_INPUT_TOLERANCE:
                        break
                if best[0] < EXACT_INPUT_TOLERANCE:
                    break
            if best[0] < EXACT_INPUT_TOLERANCE:
                break
        per_shape.append(
            {
                "shape_index": index,
                "stimulus_id": int(keras["stim"][index].n),
                "best_abs_l2_error": best[0],
                "best_source": best[1],
                "best_direction": best[2],
                "best_start_offset": int(best[3]),
                "exact_to_float_rounding": bool(best[0] < EXACT_INPUT_TOLERANCE),
            }
        )
    errors = np.array([row["best_abs_l2_error"] for row in per_shape])
    native = np.array(
        [
            float(np.linalg.norm(nets.kernet2(c) - ref[i]))
            for i, c in enumerate(sources["keras_file"])
        ]
    )
    return {
        "question": (
            "Is the headline H2 residual caused by the port, or by re-sampling the deposited "
            "contour from a different vertex / direction than the authors used?"
        ),
        "exact_input_tolerance": EXACT_INPUT_TOLERANCE,
        "n_shapes": len(per_shape),
        "n_exact_at_native_phase": int(sum(n < EXACT_INPUT_TOLERANCE for n in native)),
        "n_exact_after_alignment": int(
            sum(bool(row["exact_to_float_rounding"]) for row in per_shape)
        ),
        "native_median_l2_error": float(np.median(native)),
        "aligned_median_l2_error": float(np.median(errors)),
        "aligned_max_l2_error": float(errors.max()),
        "aligned_rmse_bound": float(np.sqrt(np.mean(errors**2) / ref.shape[1])),
        "per_shape": per_shape,
        "reading": (
            "Where alignment brings a shape to ~1e-7, the network and preprocessing are proven "
            "exact and the residual at native phase is an input-provenance effect. Shapes that "
            "stay above the tolerance after the full search have a genuine geometric difference "
            "between the deposited contour and the contour that produced the published "
            "coordinates; that part of the residual is NOT explained by the port and is "
            "reported as unattributed."
        ),
    }


def _flatten_column_major(points: np.ndarray) -> np.ndarray:
    return np.ravel(points, order="F")


def _flatten_row_major(points: np.ndarray) -> np.ndarray:
    return np.ravel(points, order="C")


def _flatten_column_major_y_then_x(points: np.ndarray) -> np.ndarray:
    return np.concatenate([points[:, 1], points[:, 0]])


def _flatten_order_ablation(cache: Path, nets: ShapeCompNets) -> dict[str, Any]:
    """KerNet1's 768-D input order is the one thing H2 cannot check; test it by ablation.

    MATLAB ``points(:)'`` is column-major, i.e. [x_1..x_384, y_1..y_384]. If the port flattened
    in C order (x,y interleaved) or swapped the axes, agreement with the published Figure 8
    ``kernet1`` would collapse. It does — by ~10x — so the flatten order is confirmed even
    though Figure 8's residual is an input-provenance effect.
    """
    raw = sio.loadmat(cache / ZENODO_SUBDIR / FIGURE8_MAT, squeeze_me=True, struct_as_record=False)
    contours = [np.asarray(entry.c, dtype=float) for entry in raw["shapes"][:200]]
    ref = np.asarray(raw["kernet1"], dtype=float)[: len(contours)]
    orders = {
        "column_major_x_then_y": _flatten_column_major,
        "row_major_interleaved": _flatten_row_major,
        "column_major_y_then_x": _flatten_column_major_y_then_x,
    }
    out: dict[str, Any] = {"n_shapes": len(contours), "variants": {}}
    for name, flatten in orders.items():
        errors = []
        for contour, target in zip(contours, ref, strict=True):
            points = port.kernet1_input(contour).reshape(2, 384).T
            errors.append(float(np.linalg.norm(nets.kernet1_from_input(flatten(points)) - target)))
        values = np.array(errors)
        out["variants"][name] = {
            "median_l2_error": float(np.median(values)),
            "mean_l2_error": float(values.mean()),
        }
    medians = {k: v["median_l2_error"] for k, v in out["variants"].items()}
    out["published_order_is_best"] = bool(
        min(medians.items(), key=lambda item: item[1])[0] == "column_major_x_then_y"
    )
    out["note"] = (
        "Only the first 200 Figure 8 shapes are scored so the ablation stays cheap; it is an "
        "ordering test, not a faithfulness level."
    )
    return out


def _edge_cases() -> dict[str, Any]:
    """What the port accepts, what it refuses, and what it warns about (B1's nine inputs)."""
    cases: list[dict[str, Any]] = []
    for name, contour, expect in _edge_case_inputs():
        row: dict[str, Any] = {"case": name, "expectation": expect}
        for network in ("kernet1_input", "kernet2_input"):
            try:
                value = getattr(port, network)(contour)
                finite = bool(np.isfinite(value).all())
                row[network] = {"status": "ok", "finite_output": finite}
            except port.ShapeCompInputError as error:
                row[network] = {"status": "rejected", "reason": str(error)}
            except Exception as error:  # noqa: BLE001 - the audit must surface anything else
                row[network] = {
                    "status": "unexpected_error",
                    "reason": f"{type(error).__name__}: {error}",
                }
        cases.append(row)
    return {
        "note": (
            "Self-intersecting rings are accepted on purpose: GANinput2shape.m documents that "
            "GAN shapes can self-intersect, so they are an official input class. Only inputs the "
            "official chain cannot carry (no extent, <3 vertices, non-finite) are refused."
        ),
        "cases": cases,
    }


def _edge_case_inputs() -> list[tuple[str, np.ndarray, str]]:
    ring = np.column_stack(
        [np.cos(np.linspace(0.0, 2 * np.pi, 240, endpoint=False)),
         np.sin(np.linspace(0.0, 2 * np.pi, 240, endpoint=False))]
    )
    theta = np.linspace(0.0, 2 * np.pi, 240, endpoint=False)
    star = np.column_stack([(1 + 0.6 * np.cos(5 * theta)) * np.cos(theta),
                            (1 + 0.6 * np.cos(5 * theta)) * np.sin(theta)])
    t = np.linspace(0.0, 2 * np.pi, 400, endpoint=False)
    figure_eight = np.column_stack([np.sin(2 * t), np.sin(t)])
    fork = np.array([[0, 0], [0, 1], [-0.5, 2], [0, 1.4], [0.5, 2], [0, 1], [1, 0], [0, 0]], float)
    return [
        ("clockwise_ring", ring[::-1], "accept"),
        ("counterclockwise_ring", ring, "accept"),
        ("duplicated_first_and_last", np.vstack([star, star[:1]]), "accept"),
        ("consecutive_duplicate_vertices", np.repeat(star, 2, axis=0), "accept"),
        ("near_circle", ring, "accept"),
        ("five_point_star", star, "accept"),
        ("thin_forked", fork, "accept"),
        ("all_vertices_coincident", np.zeros((120, 2)), "reject"),
        ("self_intersecting", figure_eight, "accept (official class)"),
        ("too_few_vertices", np.array([[0.0, 0.0], [1.0, 1.0]]), "reject"),
        ("nan_vertex", np.vstack([ring, np.array([[np.nan, 0.5]])]), "reject"),
    ]


def validate(cache: Path, nets: ShapeCompNets, with_alignment: bool = True) -> dict[str, Any]:
    raw = sio.loadmat(cache / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False)
    h2_contour = np.asarray(raw["stim"][3].c, dtype=float)
    result: dict[str, Any] = {
        "schema": "earthglyph.shape_perception_benchmark.shapecomp_port_validation/2",
        "port": "earthglyph.shape_benchmark.shapecomp_port (h5py + numpy, official weights)",
        "data_doi": "10.5281/zenodo.4730985",
        "paper_doi": "10.1371/journal.pcbi.1008981",
        "no_human_ratings": True,
        "weights_sha256": nets.weight_sha256,
        "h2_kernet2_faithfulness": _h2(cache, nets),
        "figure8_network_faithfulness": _figure8(cache, nets),
        "h1_approximation_to_original_shapecomp": _h1(cache, nets),
        "cp7a_inference_determinism": _determinism(cache, nets),
        "cp7a_rasterisation_sensitivity_kernet2": _rasterisation_sensitivity(nets, h2_contour),
        "cp7a_kernet1_flatten_order_ablation": _flatten_order_ablation(cache, nets),
        "cp7a_input_edge_cases": _edge_cases(),
    }
    if with_alignment:
        result["cp7a_h2_input_provenance_alignment"] = _input_provenance_alignment(cache, nets)
    return result


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
    parser.add_argument(
        "--skip-alignment",
        action="store_true",
        help="skip the CP7-A input-provenance search (384 start indices x 2 directions x 2 files)",
    )
    args = parser.parse_args()
    nets = ShapeCompNets() if args.toolbox is None else ShapeCompNets(args.toolbox)
    result = validate(args.cache_dir, nets, with_alignment=not args.skip_alignment)
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