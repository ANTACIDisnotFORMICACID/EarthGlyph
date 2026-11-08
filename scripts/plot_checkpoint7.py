"""Draw the P0-4.6 Checkpoint 7 figures.

CP7-A emits one figure, ``shapecomp_port_diagnostics.png``, which answers a single question with
committed evidence: *is the Python port the official network, and if a residual remains, whose is
it?* Every panel is built from ``results/shapecomp_port_validation.json`` (schema /2); the two
panels the JSON stores only as summaries recompute from the same documented seed and are checked
against the stored values, so the figure cannot drift from the result file.

No human rating appears anywhere in this figure: it is model-vs-model against the authors' own
published 22-D coordinates.

Usage:
    python scripts/plot_checkpoint7.py --figure port-diagnostics
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import scipy.io as sio  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402

from earthglyph.shape_benchmark import shapecomp_port as port  # noqa: E402
from earthglyph.shape_benchmark.datasets import ZENODO_SUBDIR  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "experiments" / "shape_perception_benchmark"
CACHE = BENCH / "cache"
RESULTS = BENCH / "results"
OUT = BENCH / "checkpoint7"
VALIDATION = RESULTS / "shapecomp_port_validation.json"
H2_SC_MAT = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _h2_pair_panel(ax: Any, validation: dict[str, Any]) -> None:
    """Official pairwise distance vs the port's, on the 15 H2 shapes (105 pairs)."""
    raw = sio.loadmat(
        CACHE / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False
    )
    ref = np.asarray(raw["sc"], dtype=float)
    nets = port.ShapeCompNets()
    pred = np.array([nets.kernet2(np.asarray(e.c, dtype=float)) for e in raw["stim"]])
    i, j = np.triu_indices(len(ref), 1)
    d_ref = np.linalg.norm(ref[i] - ref[j], axis=1)
    d_pred = np.linalg.norm(pred[i] - pred[j], axis=1)
    ax.scatter(d_ref, d_pred, s=14, c="#1f6fb4", alpha=0.8, linewidths=0)
    lo, hi = float(min(d_ref.min(), d_pred.min())), float(max(d_ref.max(), d_pred.max()))
    ax.plot([lo, hi], [lo, hi], color="0.4", lw=1.0, ls="--")
    head = validation["h2_kernet2_faithfulness"]["headline_faithfulness"]
    err = np.abs(d_pred - d_ref)
    ax.set_xlabel("official published 22-D distance")
    ax.set_ylabel("Python port 22-D distance")
    ax.set_title(
        f"(a) KerNet2 route, H2: {len(d_ref)} pairs\n"
        f"Spearman {head['pairwise_distance_spearman']:.5f}   "
        f"RMSE {head['rmse_raw']:.5f}\n"
        f"pair error median {np.median(err):.4f}  p95 {np.quantile(err, 0.95):.4f}"
    )


def _per_dim_panel(ax: Any, validation: dict[str, Any]) -> None:
    head = validation["h2_kernet2_faithfulness"]["headline_faithfulness"]
    values = np.array(head["per_dim_rmse"])
    ax.bar(np.arange(len(values)), values, color="#4c9be8")
    ax.axhline(float(values.mean()), color="0.3", ls="--", lw=1.0)
    ax.set_xlabel("ShapeComp dimension")
    ax.set_ylabel("RMSE vs published")
    ax.set_title(f"(b) per-dimension RMSE (mean {values.mean():.4f}, max {values.max():.4f})")


def _alignment_panel(ax: Any, validation: dict[str, Any]) -> None:
    align = validation["cp7a_h2_input_provenance_alignment"]
    rows = align["per_shape"]
    raw = sio.loadmat(
        CACHE / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False
    )
    ref = np.asarray(raw["sc"], dtype=float)
    nets = port.ShapeCompNets()
    native = np.array(
        [
            float(np.linalg.norm(nets.kernet2(np.asarray(e.c, dtype=float)) - ref[k]))
            for k, e in enumerate(raw["stim"])
        ]
    )
    assert abs(float(np.median(native)) - align["native_median_l2_error"]) < 1e-12
    aligned = np.array([row["best_abs_l2_error"] for row in rows])
    x = np.arange(len(rows))
    ax.bar(x - 0.2, native, width=0.38, color="#c9c9c9", label="as-stored (native phase)")
    ax.bar(
        x + 0.2,
        aligned,
        width=0.38,
        color=["#1a7f37" if r["exact_to_float_rounding"] else "#b03030" for r in rows],
        label="best over start / direction / file",
    )
    ax.axhline(align["exact_input_tolerance"], color="k", ls=":", lw=1.0)
    ax.text(
        len(rows) - 0.4,
        align["exact_input_tolerance"] * 1.4,
        "float-rounding",
        ha="right",
        fontsize=8,
    )
    ax.set_yscale("log")
    ax.set_ylim(1e-8, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels([str(r["stimulus_id"]) for r in rows], fontsize=7)
    ax.set_xlabel("H2 stimulus id")
    ax.set_ylabel("22-D L2 error (log)")
    n_exact = int(sum(r["exact_to_float_rounding"] for r in rows))
    ax.set_title(
        f"(c) input provenance: {n_exact}/{len(rows)} reach ~1e-7 once aligned\n"
        f"{len(rows) - n_exact} red stay 4.8e-3..1.8e-2 -> residual NOT attributed to the port",
        fontsize=9,
    )
    ax.legend(fontsize=7, loc="upper center", ncols=1, frameon=False)


def _pixel_panel(ax: Any, validation: dict[str, Any]) -> None:
    stored = validation["cp7a_rasterisation_sensitivity_kernet2"]
    raw = sio.loadmat(
        CACHE / ZENODO_SUBDIR / H2_SC_MAT, squeeze_me=True, struct_as_record=False
    )
    contour = np.asarray(raw["stim"][3].c, dtype=float)
    nets = port.ShapeCompNets()
    mask = port.inpolygon(port.kernet2_points(contour))
    base = nets.kernet2_from_mask(mask)
    scale = float(np.linalg.norm(base))
    rng = np.random.default_rng(20261108)
    flat = mask.ravel()
    shifts = []
    for index in rng.choice(flat.size, int(stored["flipped_pixels_tested"]), replace=False):
        flipped = flat.copy()
        flipped[index] = ~flipped[index]
        shifted = nets.kernet2_from_mask(flipped.reshape(mask.shape))
        shifts.append(float(np.linalg.norm(shifted - base)))
    values = np.array(shifts)
    # Recompute must reproduce the committed summary, or the figure is lying.
    assert abs(float(np.median(values)) - stored["median_l2_shift"]) < 1e-9
    assert abs(float(values.max()) - stored["max_l2_shift"]) < 1e-9
    ax.hist(values, bins=12, color="#f0883e")
    ax.axvline(stored["median_l2_shift"], color="k", ls="--", lw=1.0)
    ax.axvline(scale, color="#b03030", ls="-", lw=1.2)
    top = ax.get_ylim()[1]
    ax.text(
        scale * 0.97,
        top * 0.94,
        f"whole embedding {scale:.2f}",
        color="#b03030",
        fontsize=8,
        ha="right",
    )
    ax.text(
        stored["median_l2_shift"] * 1.02,
        top * 0.94,
        f"median {stored['median_l2_shift']:.3f}",
        fontsize=8,
    )
    ax.set_xlabel("L2 shift of the 22-D output")
    ax.set_ylabel("single-pixel flips")
    ax.set_title(
        f"(d) rasterisation is near-discontinuous\n"
        f"1 flipped pixel = {100 * stored['median_shift_share_of_norm']:.1f}% of the embedding",
        fontsize=9,
    )


def _flatten_panel(ax: Any, validation: dict[str, Any]) -> None:
    variants = validation["cp7a_kernet1_flatten_order_ablation"]["variants"]
    names = list(variants)
    medians = [variants[n]["median_l2_error"] for n in names]
    colours = ["#1a7f37" if n == "column_major_x_then_y" else "#b03030" for n in names]
    ax.barh(range(len(names)), medians, color=colours)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("median L2 error vs published kernet1 (Figure 8, 200 shapes)")
    ax.set_title(
        "(e) KerNet1: only the 768-D ORDER is pinned\n"
        "column-major wins ~10x; its 0.0597 RMSE stays partly unattributed",
        fontsize=9,
    )


def _edge_panel(ax: Any, validation: dict[str, Any]) -> None:
    cases = validation["cp7a_input_edge_cases"]["cases"]
    ax.axis("off")
    lines = []
    for row in cases:
        k1, k2 = row["kernet1_input"], row["kernet2_input"]
        mark = "accepted" if k1["status"] == "ok" else "REFUSED"
        detail = "" if k1["status"] == "ok" else f"  ({k1['reason']})"
        same = "" if k2["status"] == k1["status"] else f"  k2: {k2['status']}"
        lines.append(f"{row['case']:<34s} {mark}{detail}{same}")
    ax.text(
        0.0,
        0.98,
        "(f) input contract (both routes)\n" + "\n".join(lines),
        fontsize=7.0,
        va="top",
        family="monospace",
    )


def plot_port_diagnostics() -> Path:
    validation = read_json(VALIDATION)
    OUT.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(15.0, 9.5))
    grid = GridSpec(2, 3, figure=figure, width_ratios=[1.0, 1.0, 1.15], hspace=0.38, wspace=0.28)
    _h2_pair_panel(figure.add_subplot(grid[0, 0]), validation)
    _per_dim_panel(figure.add_subplot(grid[0, 1]), validation)
    _alignment_panel(figure.add_subplot(grid[0, 2]), validation)
    _pixel_panel(figure.add_subplot(grid[1, 0]), validation)
    _flatten_panel(figure.add_subplot(grid[1, 1]), validation)
    _edge_panel(figure.add_subplot(grid[1, 2]), validation)
    figure.suptitle(
        "ShapeComp Python port diagnostics — model-vs-model against the authors' published "
        "22-D coordinates (no human ratings)",
        fontsize=11,
    )
    path = OUT / "shapecomp_port_diagnostics.png"
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure", default="port-diagnostics", choices=["port-diagnostics"])
    args = parser.parse_args()
    if args.figure == "port-diagnostics":
        print(f"wrote {plot_port_diagnostics()}")


if __name__ == "__main__":
    main()
