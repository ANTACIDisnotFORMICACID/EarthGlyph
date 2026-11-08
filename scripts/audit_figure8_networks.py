"""Audit the official network outputs published in Zenodo record 4730985, Figure 8.

``Figure8NetComparison.mat`` holds the authors' own MatNet / KerNet1 / KerNet2 coordinates
for 1000 held-out GAN shapes *and the shapes themselves*. This script recomputes the
Figure 8D correlations from those raw arrays instead of reading them off the published
scatter plot, and checks what the deposit does and does not license us to claim.

These 1000 shapes carry **no human ratings**. Everything produced here is model-vs-model
agreement and must never be reported as human validation.

Usage:
    python scripts/audit_figure8_networks.py [--cache-dir PATH]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io as sio
from scipy.spatial import cKDTree
from scipy.spatial.distance import pdist
from scipy.stats import pearsonr, spearmanr

from earthglyph.geometry.sketch import SketchError
from earthglyph.shape_benchmark.datasets import ZENODO_SUBDIR, load_h1, standardize

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
FIGURE8_MAT = "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat"
FIGURE8_SCRIPT = "shapecomp datashare/Figure 8/Figure8.m"
NETWORKS = ("matnet", "kernet1", "kernet2")

# Quoted from the paper for comparison only. These are NOT results computed here.
PAPER_REPORTED_R = {"matnet": 0.93, "kernet1": 0.94, "kernet2": 0.91}


def _correlations(reference: np.ndarray, coords: dict[str, np.ndarray]) -> dict[str, Any]:
    base = pdist(reference)
    out: dict[str, Any] = {}
    for name in NETWORKS:
        other = pdist(coords[name])
        out[name] = {
            "pearson_r": float(pearsonr(base, other).statistic),
            "spearman_rho": float(spearmanr(base, other).statistic),
            "n_pairs": int(base.size),
            "paper_reported_r_quoted_not_computed": PAPER_REPORTED_R[name],
        }
    return out


def _stimulus_contract(contours: np.ndarray) -> dict[str, Any]:
    """Compare the deposited contours against the paper's stated network input contract."""
    low = contours.min(axis=1)
    high = contours.max(axis=1)
    start_is_min_x = contours[:, :, 0].argmin(axis=1) == 0
    return {
        "n_shapes": int(contours.shape[0]),
        "contour_shape": list(contours.shape[1:]),
        "paper_contract": (
            "384x2 (x,y); per coordinate subtract the absolute minimum then divide by the "
            "resulting absolute maximum (range 0-1); first point is the minimum-x point"
        ),
        "per_shape_min_mean": low.mean(axis=0).tolist(),
        "per_shape_max_mean": high.mean(axis=0).tolist(),
        "fraction_already_min_max_normalised": float(
            np.mean((low == 0.0).all(axis=1) & (high == 1.0).all(axis=1))
        ),
        "fraction_starting_at_min_x": float(start_is_min_x.mean()),
        "verdict": (
            "The deposited `c` arrays are RAW GAN contours, not the normalised network input. "
            "The published coordinates were produced from a normalised version that is not "
            "in this member, so any EarthGlyph arm scored on these contours differs from the "
            "official route in preprocessing as well as in descriptors."
        ),
    }


def _h1_overlap(
    cache: Path, usable: np.ndarray, sc22: np.ndarray, n_excluded: int
) -> dict[str, Any]:
    """Are any of the 1000 Figure 8 shapes among the 500 human-rated H1 stimuli?"""
    h1 = load_h1(cache)
    rated = np.stack([shape.points for shape in h1.shapes.values()])
    rated_sc = np.stack([h1.official_coords[s] for s in h1.shapes])
    d_contour, _ = cKDTree(usable.reshape(len(usable), -1)).query(
        rated.reshape(len(rated), -1)
    )
    d_sc, _ = cKDTree(sc22).query(rated_sc)
    exact = int((d_contour < 1e-9).sum())
    return {
        "n_rated_h1_shapes": len(rated),
        "n_figure8_shapes": int(len(sc22)),
        "n_figure8_shapes_comparable_in_contour_space": int(len(usable)),
        "n_excluded_self_intersecting": n_excluded,
        "exact_contour_matches": exact,
        "contour_nn_distance_min": float(d_contour.min()),
        "contour_nn_distance_median": float(np.median(d_contour)),
        "sc22_nn_distance_min": float(d_sc.min()),
        "sc22_nn_distance_median": float(np.median(d_sc)),
        "comparison_space": (
            "contours on both sides pass through standardize() onto the shared 128-point "
            "contract, so identical published shapes match exactly; the SC check uses all "
            "1000 published coordinate rows and needs no contour loading"
        ),
        "verdict": (
            "DISJOINT: no Figure 8 shape is an H1 rated stimulus"
            if exact == 0 and d_sc.min() > 1e-6
            else "OVERLAP: at least one H1 stimulus also appears in Figure 8"
        ),
    }


def audit(cache: Path) -> dict[str, Any]:
    path = cache / ZENODO_SUBDIR / FIGURE8_MAT
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing; run scripts/fetch_shape_benchmark_sources.py first"
        )
    raw = sio.loadmat(path, simplify_cells=True)
    sc = np.asarray(raw["SC"], dtype=float)
    coords = {name: np.asarray(raw[name], dtype=float) for name in NETWORKS}
    entries = raw["shapes"]
    contours = np.stack([np.asarray(entry["c"], dtype=float) for entry in entries])
    latents = np.stack([np.asarray(entry["lat"], dtype=float) for entry in entries])

    usable: list[np.ndarray] = []
    excluded: list[dict[str, Any]] = []
    for index, contour in enumerate(contours):
        try:
            usable.append(standardize(contour))
        except SketchError as error:
            excluded.append({"index": index, "reason": str(error)})
    usable_array = np.stack(usable)

    variance = sc.var(axis=0)
    script = (cache / ZENODO_SUBDIR / FIGURE8_SCRIPT).read_text(encoding="utf-8")
    uses_full_sc = "pdist(SC)" in script

    return {
        "schema": "earthglyph.shape_perception_benchmark.figure8_network_audit/1",
        "source": {
            "member": FIGURE8_MAT,
            "data_doi": "10.5281/zenodo.4730985",
            "paper_doi": "10.1371/journal.pcbi.1008981",
            "license": "CC-BY-4.0",
            "bytes": path.stat().st_size,
            "plot_script": FIGURE8_SCRIPT,
            "plot_script_calls_pdist_on_full_SC": uses_full_sc,
        },
        "array_inventory": {
            "SC": {"shape": list(sc.shape), "dtype": str(raw["SC"].dtype)},
            **{
                name: {"shape": list(coords[name].shape), "dtype": str(raw[name].dtype)}
                for name in NETWORKS
            },
            "shapes": {"n": len(entries), "fields": sorted(entries[0].keys())},
            "contours": {"shape": list(contours.shape)},
            "gan_latents": {"shape": list(latents.shape)},
        },
        "sc_dimensionality": {
            "n_columns": int(sc.shape[1]),
            "network_output_columns": int(coords["kernet1"].shape[1]),
            "variance_share_first_22": float(variance[:22].sum() / variance.sum()),
            "per_column_std_first_5": variance[:5] ** 0.5,
            "finding": (
                "SC is a 40-D MDS solution, NOT the 22-D ShapeComp space used elsewhere in "
                "the paper. Its first 22 columns retain only ~88.8% of this solution's "
                "variance, against the 95.05% the paper reports for the animal-silhouette "
                "space, so they are not verified to be the canonical ShapeComp coordinates. "
                "The authors' Figure8.m calls pdist(SC) on all 40 columns, so the published "
                "Figure 8D correlations are 40-D-reference correlations."
            ),
        },
        "network_vs_shapecomp": {
            "reference_SC_all_40_columns_as_in_Figure8m": _correlations(sc, coords),
            "reference_SC_first_22_columns": _correlations(sc[:, :22], coords),
            "target_semantics": (
                "MODEL vs MODEL. Pairwise-distance agreement between each official network's "
                "22-D output and the official MDS coordinates. No human ratings are involved."
            ),
            "train_test_status": (
                "The paper states the networks were trained on 800,000 of 950,000 GAN shapes "
                "and evaluated on 1000 untrained shapes; Figure 8D is that held-out comparison. "
                "Held-out from NETWORK training, but the MDS reference space itself was fitted "
                "on animal silhouettes, and these shapes have no human ratings."
            ),
        },
        "stimulus_input_contract": _stimulus_contract(contours),
        "shared_contract_compatibility": {
            "n_loadable": len(usable),
            "n_excluded": len(excluded),
            "excluded": excluded,
            "note": (
                "standardize() refuses self-intersecting rings rather than silently repairing "
                "them, so these two GAN shapes cannot enter an E0/E1/E3 gallery as-is. They "
                "still have published official coordinates."
            ),
        },
        "overlap_with_human_rated_h1": _h1_overlap(
            cache, usable_array, sc[:, :22], len(excluded)
        ),
        "usability": {
            "official_coordinates_available_for_retrieval": True,
            "n_shapes_with_coordinates_and_contours": len(entries),
            "human_ratings_available": False,
            "can_score_arbitrary_new_shapes": False,
            "reason_cannot_score_new_shapes": (
                "These are published outputs for a fixed 1000-shape set. Scoring a new shape "
                "still needs the unobtainable 109-descriptor engine or the network weights."
            ),
            "preprocessing_confound": (
                "Official coordinates came from min-max-normalised 384-point contours with a "
                "minimum-x start point; E0/E1/E3 use the shared 128-point centred/unit-RMS/CCW "
                "contract. A ranking disagreement on this gallery cannot be attributed to the "
                "descriptor alone."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = audit(args.cache_dir)
    out = args.out or args.cache_dir.parent / "results" / "figure8_network_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=_jsonify) + "\n",
                   encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)}")
    full = result["network_vs_shapecomp"]["reference_SC_all_40_columns_as_in_Figure8m"]
    for name in NETWORKS:
        print(f"  {name:8s} r={full[name]['pearson_r']:.4f} "
              f"(paper quotes {PAPER_REPORTED_R[name]:.2f})")
    print(f"  {result['overlap_with_human_rated_h1']['verdict']}")


def _jsonify(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serialisable: {type(value)}")


if __name__ == "__main__":
    main()