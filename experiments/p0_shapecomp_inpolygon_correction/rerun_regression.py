"""Regression record: inpolygon horizontal-edge fix vs the pre-fix implementation.

The fix (this commit) makes horizontal edges participate in the boundary test while still
contributing no ray crossings. The pre-fix implementation (verbatim from commit fd7db93,
earthglyph/shape_benchmark/shapecomp_port.py lines 192-232) skipped BOTH for y0 == y1, so
grid pixels lying exactly on a horizontal edge interior could be lost.

This script re-derives, for every contour family the committed experiments fed through
KerNet2, the 40x40 mask and the 22-D KerNet2 coordinates under BOTH implementations and
writes the per-contour deltas to results/regression_results.json. It never overwrites any
frozen experiment artifact: it only reads inputs and writes into its own results directory.

Run:  python experiments/p0_shapecomp_inpolygon_correction/rerun_regression.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from earthglyph.shape_benchmark import shapecomp_port as port  # noqa: E402

_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
_ZENODO = _CACHE / "zenodo4730985"
H2_SC_MAT = _ZENODO / "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
H2_HUMAN_MAT = _ZENODO / "shapecomp datashare/Figure 9/data/fig7D_lietal_points_2.mat"
FIGURE8_MAT = _ZENODO / "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat"
QUERIES_CSV = REPO / "experiments" / "shape_perception_benchmark" / "queries" / "query_contours.csv"
SHAPES_NPY = REPO / "data" / "index" / "shapes.npy"


def _inpolygon_pre_fix(points: np.ndarray) -> np.ndarray:
    """The pre-fix inpolygon, line-for-line from commit fd7db93 (kept for provenance)."""
    grid_x, grid_y = port._grid_points()
    xv = points[:, 0]
    yv = points[:, 1]
    n = len(xv)
    inside = np.zeros(len(grid_x), dtype=bool)
    on_boundary = np.zeros(len(grid_x), dtype=bool)
    for i in range(n):
        j = (i + 1) % n
        x0, y0, x1, y1 = xv[i], yv[i], xv[j], yv[j]
        on_boundary |= (grid_x == x0) & (grid_y == y0)
        if y0 == y1:
            continue
        rising = y0 < y1
        lo, hi = (y0, y1) if rising else (y1, y0)
        sel = (grid_y >= lo) & (grid_y < hi)
        if not sel.any():
            continue
        cross_x = x0 + (grid_y[sel] - y0) * (x1 - x0) / (y1 - y0)
        inside[sel] ^= grid_x[sel] < cross_x
        dx = x1 - x0
        dy = y1 - y0
        cross = (grid_x - x0) * dy - (grid_y - y0) * dx
        on_boundary |= (
            (cross == 0.0)
            & (grid_x >= min(x0, x1))
            & (grid_x <= max(x0, x1))
            & (grid_y >= min(y0, y1))
            & (grid_y <= max(y0, y1))
        )
    return np.flipud(np.reshape(inside | on_boundary, (port._IMAGE_SIZE, port._IMAGE_SIZE), order="F"))


def _load_official() -> dict[str, list[np.ndarray]]:
    import scipy.io as sio

    out: dict[str, list[np.ndarray]] = {}
    if H2_SC_MAT.exists():
        keras = sio.loadmat(H2_SC_MAT, squeeze_me=True, struct_as_record=False)
        out["h2_keras_file"] = [np.asarray(e.c, dtype=float) for e in keras["stim"]]
    if H2_HUMAN_MAT.exists():
        human = sio.loadmat(H2_HUMAN_MAT, squeeze_me=True, struct_as_record=False)
        out["h2_fig7d_file"] = [np.asarray(e.c, dtype=float) for e in human["stim"]]
    if FIGURE8_MAT.exists():
        raw = sio.loadmat(FIGURE8_MAT, squeeze_me=True, struct_as_record=False)
        out["figure8"] = [np.asarray(e.c, dtype=float) for e in raw["shapes"][:60]]
    return out


def _load_queries() -> dict[str, list[np.ndarray]]:
    import pandas as pd

    if not QUERIES_CSV.exists():
        return {}
    df = pd.read_csv(QUERIES_CSV)
    out: dict[str, list[np.ndarray]] = {}
    for query_id, group in df.groupby("query_id", sort=True):
        pts = group.sort_values("point_index")[["x", "y"]].to_numpy(dtype=float)
        out.setdefault(f"queries_{group['source'].iloc[0]}", []).append(pts)
    return out


def _load_gallery() -> dict[str, list[np.ndarray]]:
    if not SHAPES_NPY.exists():
        return {}
    shapes = np.load(SHAPES_NPY)
    return {"gallery_candidates": [shapes[i].astype(float) for i in range(len(shapes))]}


def _compare_one(name: str, contour: np.ndarray, nets: "port.ShapeCompNets | None") -> dict:
    ring = port.kernet2_points(contour)
    mask_old = _inpolygon_pre_fix(ring)
    mask_new = port.inpolygon(ring)
    added = int((mask_new & ~mask_old).sum())
    removed = int((~mask_new & mask_old).sum())
    row: dict = {"contour": name, "pixels_added": added, "pixels_removed": removed}
    if added or removed:
        row["added_ij"] = [
            [int(r), int(c)] for r, c in np.argwhere(mask_new & ~mask_old)
        ]
    if nets is not None and (added or removed):
        emb_old = nets.kernet2_from_mask(mask_old)
        emb_new = nets.kernet2_from_mask(mask_new)
        row["kernet2_l2_delta"] = float(np.linalg.norm(emb_new - emb_old))
        row["kernet2_rel_delta"] = row["kernet2_l2_delta"] / float(np.linalg.norm(emb_old))
    return row


def main() -> None:
    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    nets = None
    try:
        nets = port.ShapeCompNets()
    except Exception as exc:  # noqa: BLE001 - toolbox is optional for the mask-only pass
        print(f"toolbox unavailable, mask-only comparison: {exc}")

    families = {**_load_official(), **_load_queries(), **_load_gallery()}
    report = {
        "pre_fix_source": "commit fd7db93 earthglyph/shape_benchmark/shapecomp_port.py inpolygon",
        "post_fix_source": "working tree inpolygon (horizontal edges join the boundary test)",
        "weight_sha256": nets.weight_sha256 if nets is not None else None,
        "families": {},
    }
    for family, contours in sorted(families.items()):
        rows = [_compare_one(f"{family}[{i}]", c, nets) for i, c in enumerate(contours)]
        changed = [r for r in rows if r["pixels_added"] or r["pixels_removed"]]
        report["families"][family] = {
            "n_contours": len(rows),
            "n_changed_masks": len(changed),
            "total_pixels_added": sum(r["pixels_added"] for r in rows),
            "total_pixels_removed": sum(r["pixels_removed"] for r in rows),
            "changed": changed,
        }
        print(
            f"{family}: {len(rows)} contours, {len(changed)} masks changed, "
            f"+{report['families'][family]['total_pixels_added']} / "
            f"-{report['families'][family]['total_pixels_removed']} px"
        )

    out_path = results_dir / "regression_results.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {out_path}")


if __name__ == "__main__":
    main()
