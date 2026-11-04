#!/usr/bin/env python
"""CLI: render the P0-2 geometry-cleaning inspection figures from REAL data.

Every panel is drawn from ``data/processed/contours.parquet``,
``data/processed/rejected_samples.parquet`` and the untouched raw Overture
extract. These are DATA PREPROCESSING results - they are explicitly not
retrieval results and contain no query sketch.

Figures
-------
A ``before_after.png``     raw source geometry vs the stored normalized contour
B ``candidate_gallery.png`` diversity preview of cleaned candidates
C ``rejected_examples.png`` filtered features with their rejection reason
D ``complex_contours.png``  dendritic / crescent / holed / MultiPolygon / slender

Example
-------
python scripts/visualize_geometry.py \
    --contours data/processed/contours.parquet \
    --out-dir experiments/p0_geometry_probe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe_shared import (  # noqa: E402
    draw_contour as _draw_contour,
)
from probe_shared import (
    draw_geometry as _draw_geometry,
)
from probe_shared import (
    projection_from_manifest as _projection,
)
from probe_shared import (
    stratified_pick as _stratified_pick,
)

from earthglyph.data.prepare import load_contours, stack_contours  # noqa: E402
from earthglyph.geometry import AeqdProjection, split_feature  # noqa: E402

PROBE_LABEL = (
    "EarthGlyph P0-2 geometry cleaning - REAL Overture Maps water features "
    "(data preprocessing result, NOT a retrieval result)"
)
PROBE_LABEL_SHORT = "REAL Overture water - P0-2 preprocessing result, NOT retrieval"
CIRCLE_COMPLEXITY = 4.0 * np.pi  # perimeter^2 / area for a perfect disc
def _raw_geometries(raw_path: Path) -> dict[str, Any]:
    import geopandas as gpd

    gdf = gpd.read_parquet(raw_path)
    return {str(i): g for i, g in zip(gdf["id"], gdf.geometry, strict=True)}


def _source_part(raw_geoms: dict[str, Any], source_id: str, part_index: int) -> Any:
    """Recover the exact source polygon a candidate was built from."""
    geom = raw_geoms.get(source_id)
    if geom is None:
        return None
    parts = split_feature(geom)
    if part_index >= len(parts):
        return None
    return parts[part_index].polygon
def _shape_metrics(geom_metric: Any, row: pd.Series) -> dict[str, float]:
    """Dimensionless shape descriptors used to pick the figure-D cases."""
    area = float(geom_metric.area)
    perimeter = float(geom_metric.length)
    hull = geom_metric.convex_hull
    rect = geom_metric.minimum_rotated_rectangle
    rect_xy = np.asarray(rect.exterior.coords)[:4]
    edges = np.linalg.norm(np.diff(np.vstack([rect_xy, rect_xy[:1]]), axis=0), axis=1)
    long_side = float(np.max(edges))
    short_side = float(np.min(edges))
    deficit = hull.difference(geom_metric)
    defects = (
        list(deficit.geoms)
        if deficit.geom_type == "MultiPolygon"
        else ([deficit] if deficit.geom_type == "Polygon" else [])
    )
    deficit_area = sum(float(d.area) for d in defects)
    dominant = max((float(d.area) for d in defects), default=0.0)
    return {
        "area_ha": area / 1e4,
        "perimeter_m": perimeter,
        "complexity": perimeter**2 / area if area > 0 else float("inf"),
        "solidity": area / float(hull.area) if hull.area > 0 else 0.0,
        "aspect_ratio": long_side / short_side if short_side > 0 else float("inf"),
        "hull_defects": float(len(defects)),
        "dominant_defect_share": dominant / deficit_area if deficit_area > 0 else 0.0,
        "n_holes": int(row["n_holes"]),
        "n_parts": int(row["n_parts"]),
    }


def figure_before_after(
    contours: pd.DataFrame,
    stacked: np.ndarray,
    raw_geoms: dict[str, Any],
    projection: AeqdProjection,
    out_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """A: raw source outline next to the stored 128-point normalized contour."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    picked = _pick_representative(contours, k=8)
    n = len(picked)
    fig, axes = plt.subplots(n, 2, figsize=(7.2, 2.5 * n))
    axes = np.atleast_2d(axes)
    entries: list[dict[str, Any]] = []
    for r, pos in enumerate(picked):
        row = contours.iloc[pos]
        raw = _source_part(raw_geoms, str(row["source_id"]), int(row["part_index"]))
        ax_raw, ax_norm = axes[r]
        if raw is None:
            ax_raw.text(0.5, 0.5, "source geometry unavailable", ha="center", fontsize=6)
            ax_raw.set_xticks([])
            ax_raw.set_yticks([])
        else:
            _draw_geometry(ax_raw, projection.project_geometry(raw))
        _draw_contour(ax_norm, stacked[pos])
        label = (
            f"{row['candidate_id']}\n{row['subtype']} | {float(row['area_ha']):.2f} ha | "
            f"{int(row['n_holes'])} holes | part {int(row['part_index']) + 1}/"
            f"{int(row['n_parts'])} | ({row['lat']:.4f},{row['lon']:.4f})"
        )
        ax_raw.set_title(
            "raw source polygon (aeqd metres)" if r == 0 else "", fontsize=6.5
        )
        ax_norm.set_title(
            "stored normalized contour (128 pts)" if r == 0 else "", fontsize=6.5
        )
        ax_raw.set_ylabel(label, fontsize=5.6, rotation=0, ha="right", va="center")
        entries.append(
            {
                "candidate_id": str(row["candidate_id"]),
                "source_id": str(row["source_id"]),
                "subtype": str(row["subtype"]),
                "area_ha": float(row["area_ha"]),
                "n_holes": int(row["n_holes"]),
                "n_parts": int(row["n_parts"]),
                "lon": float(row["lon"]),
                "lat": float(row["lat"]),
            }
        )
    fig.suptitle(
        f"A. Cleaning before/after - {n} real candidates\n{PROBE_LABEL_SHORT}\n"
        f"source: Overture base/water release {manifest['source']['release']}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.06, 0, 1, 0.93))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {
        "file": out_path.name,
        "panels": n,
        "selection": (
            "area-stratified across all candidates, plus the most holed and a "
            "MultiPolygon part"
        ),
        "entries": entries,
    }


def _pick_representative(contours: pd.DataFrame, k: int) -> list[int]:
    """Deterministic spread over size, forced to include interesting shapes."""
    order = contours["area_m2"].to_numpy().argsort()
    picks = _stratified_pick(order, max(k - 2, 1))
    most_holes = int(contours["n_holes"].to_numpy().argmax())
    multi = contours.index[contours["n_parts"] > 1]
    extras = [most_holes]
    if len(multi) > 0:
        extras.append(int(multi[0]))
    for pos in extras:
        if pos not in picks:
            picks.append(pos)
    return picks[:k]


def figure_candidate_gallery(
    contours: pd.DataFrame, stacked: np.ndarray, out_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    """B: diversity preview of cleaned candidates (normalized contours only)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    count = min(36, len(contours))
    order = contours["area_m2"].to_numpy().argsort()
    picks = _stratified_pick(order, count)
    cols = 6
    rows = int(np.ceil(len(picks) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(2.3 * cols, 2.5 * rows))
    axes = np.atleast_2d(axes)
    entries: list[dict[str, Any]] = []
    for i, ax in enumerate(axes.flat):
        if i >= len(picks):
            ax.axis("off")
            continue
        pos = picks[i]
        row = contours.iloc[pos]
        _draw_contour(ax, stacked[pos])
        cid = str(row["candidate_id"])
        short = cid if len(cid) <= 20 else cid[:19] + "..."
        flags = []
        if int(row["n_holes"]) > 0:
            flags.append(f"{int(row['n_holes'])} holes")
        if int(row["n_parts"]) > 1:
            flags.append(f"part {int(row['part_index']) + 1}/{int(row['n_parts'])}")
        flag_txt = (" | " + " | ".join(flags)) if flags else ""
        ax.set_title(
            f"{row['subtype']} | {float(row['area_ha']):.3g} ha{flag_txt}\n{short}",
            fontsize=5.6,
        )
        entries.append(
            {
                "candidate_id": cid,
                "subtype": str(row["subtype"]),
                "area_ha": float(row["area_ha"]),
                "n_holes": int(row["n_holes"]),
                "n_parts": int(row["n_parts"]),
            }
        )
    subtypes = {str(s): int(n) for s, n in contours["subtype"].value_counts().items()}
    fig.suptitle(
        f"B. Candidate diversity - {len(picks)} of {len(contours)} cleaned real contours "
        f"(area-stratified, by_subtype={subtypes})\n{PROBE_LABEL}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {
        "file": out_path.name,
        "panels": len(picks),
        "selection": (
            "one candidate per equal slice of the area-sorted list "
            "(deterministic, no seed)"
        ),
        "entries": entries,
    }


def figure_rejected_examples(
    rejected: pd.DataFrame,
    contours: pd.DataFrame,
    projection: AeqdProjection,
    out_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """C: what the filters removed, with reasons, plus the size distribution."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shapely

    counts = manifest["counts"]
    reasons = sorted(rejected["reason"].unique())
    per_reason = 4
    rows = len(reasons)
    fig = plt.figure(figsize=(13.0, 2.6 * rows + 2.6))
    gs = fig.add_gridspec(rows + 1, per_reason + 1, height_ratios=[1.0] * rows + [1.1])

    entries: dict[str, Any] = {}
    for r, reason in enumerate(reasons):
        block = rejected[rejected["reason"] == reason]
        picks = _stratified_pick(np.arange(len(block)), min(per_reason, len(block)))
        total = _reason_total(counts, reason)
        for c, local in enumerate(picks):
            ax = fig.add_subplot(gs[r, c])
            item = block.iloc[local]
            geom = None if item["wkb"] is None else shapely.from_wkb(item["wkb"])
            if geom is None:
                ax.text(0.5, 0.5, "no geometry", ha="center", va="center", fontsize=6)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                _draw_geometry(
                    ax,
                    projection.project_geometry(geom),
                    facecolor="#e6c9a8",
                    edgecolor="#7a4b1f",
                )
            area = item["area_m2"]
            area_txt = "n/a" if area is None or pd.isna(area) else f"{float(area) / 1e4:.4f} ha"
            detail = str(item["detail"])
            if len(detail) > 46:
                detail = detail[:45] + "..."
            ax.set_title(
                f"{reason}\nsubtype={item['subtype']} | {area_txt}\n"
                f"id {str(item['source_id'])[:18]}",
                fontsize=5.4,
            )
            ax.set_xlabel(detail, fontsize=4.8)
        for c in range(len(picks), per_reason):
            fig.add_subplot(gs[r, c]).axis("off")
        label_ax = fig.add_subplot(gs[r, per_reason])
        label_ax.axis("off")
        label_ax.text(
            0.0,
            0.5,
            f"{reason}\n\n{total} rejected\n({len(block)} sampled here)\n\n"
            f"{_reason_explanation(reason)}",
            fontsize=6.4,
            va="center",
        )
        entries[reason] = {
            "total_rejected": total,
            "panels_shown": len(picks),
            "source_ids": [str(block.iloc[i]["source_id"]) for i in picks],
        }

    hist_ax = fig.add_subplot(gs[rows, :])
    kept = contours["area_ha"].to_numpy(dtype=float)
    dropped_areas = np.asarray(
        [
            float(v) / 1e4
            for v in rejected.loc[rejected["reason"] == "area_below_minimum", "area_m2"]
            if v is not None and not pd.isna(v)
        ],
        dtype=float,
    )
    bins = np.logspace(np.log10(0.05), np.log10(1e4), 48).tolist()
    if kept.size:
        hist_ax.hist(
            kept, bins=bins, alpha=0.75, label=f"kept candidates ({kept.size})",
            color="#4a7fa5",
        )
    if dropped_areas.size:
        hist_ax.hist(
            dropped_areas,
            bins=bins,
            alpha=0.75,
            label=f"rejected: area_below_minimum ({dropped_areas.size} sampled)",
            color="#b07a3a",
        )
    hist_ax.axvline(float(manifest["cleaning"]["min_area_ha"]), color="crimson", ls="--", lw=1.2)
    hist_ax.text(
        float(manifest["cleaning"]["min_area_ha"]) * 1.15,
        hist_ax.get_ylim()[1] * 0.85,
        f"min area = {manifest['cleaning']['min_area_ha']} ha "
        "(CLI parameter --min-area-ha, not a hard rule)",
        color="crimson",
        fontsize=6.5,
    )
    hist_ax.set_xscale("log")
    hist_ax.set_xlabel("area (ha, log scale)", fontsize=7)
    hist_ax.set_ylabel("count", fontsize=7)
    hist_ax.tick_params(labelsize=6)
    hist_ax.legend(fontsize=6.5)
    hist_ax.set_title(
        "Kept vs rejected area distribution - is the 0.1 ha floor over-filtering?",
        fontsize=7.5,
    )

    fig.suptitle(
        f"C. Rejected features and reasons (raw rows {counts['raw_rows']} -> "
        f"candidates {counts['candidates']})\n{PROBE_LABEL}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.945))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {
        "file": out_path.name,
        "panels": sum(int(v["panels_shown"]) for v in entries.values()) + 1,
        "reasons_present_in_saved_samples": entries,
        "reasons_with_zero_real_occurrences": _zero_reasons(counts),
        "note": (
            "full per-reason counts come from data_manifest.json; the parquet holds "
            "a capped sample"
        ),
    }


def _reason_total(counts: dict[str, Any], reason: str) -> int:
    for table in ("rejected_features_by_reason", "rejected_parts_by_reason"):
        value = counts.get(table, {}).get(reason)
        if value is not None:
            return int(value)
    return 0


def _reason_explanation(reason: str) -> str:
    return {
        "wrong_type": "Point/LineString/etc: not a closed outline, cannot become a contour.",
        "subtype_not_in_allowlist": "Kept in pending_analysis.parquet, not deleted (D6).",
        "area_below_minimum": "Below the 0.1 ha experiment floor; parameter, not a hard rule.",
        "empty_geometry": "Zero-vertex geometry, nothing to sample.",
        "null_geometry": "No geometry in the source row.",
        "unrepairable_after_make_valid": (
            "make_valid returned no polygonal part (e.g. a sliver line)."
        ),
        "degenerate_ring": "Fewer than 3 distinct vertices after closing-point removal.",
        "duplicate_high_overlap": "IoU > max_iou with a larger kept candidate.",
    }.get(reason, "See earthglyph/geometry/filtering.py for the rule definition.")


def _zero_reasons(counts: dict[str, Any]) -> dict[str, int]:
    known = (
        "wrong_type",
        "subtype_not_in_allowlist",
        "empty_geometry",
        "null_geometry",
        "unrepairable_after_make_valid",
        "area_below_minimum",
        "degenerate_ring",
        "duplicate_high_overlap",
    )
    out: dict[str, int] = {}
    for reason in known:
        if _reason_total(counts, reason) == 0:
            out[reason] = 0
    return out


def figure_complex_contours(
    contours: pd.DataFrame,
    stacked: np.ndarray,
    raw_geoms: dict[str, Any],
    projection: AeqdProjection,
    out_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """D: the hard shapes - dendritic, crescent, holed, MultiPolygon, slender."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics: list[dict[str, float]] = []
    for pos in range(len(contours)):
        row = contours.iloc[pos]
        raw = _source_part(raw_geoms, str(row["source_id"]), int(row["part_index"]))
        if raw is None:
            continue
        metrics.append(_shape_metrics(projection.project_geometry(raw), row) | {"pos": pos})
    frame = pd.DataFrame(metrics)

    crescent_band = frame[
        (frame["hull_defects"] == 1)
        & (frame["dominant_defect_share"] > 0.95)
        & (frame["solidity"] >= 0.6)
        & (frame["solidity"] <= 0.9)
        & (frame["complexity"] < 60.0)
        & (frame["area_ha"] >= 0.25)
    ]
    simple_parts = frame[frame["n_parts"] > 1]
    modest = frame[frame["complexity"] < 100.0]
    cases: list[tuple[str, str, int]] = [
        (
            "dendritic",
            "highest perimeter^2/area - branching shoreline; 128 points under-sample it",
            int(frame.loc[frame["complexity"].idxmax(), "pos"]),
        ),
        (
            "crescent / oxbow",
            "deepest single concave bay on a compact body - a real crescent, kept intact",
            int(crescent_band.loc[(1.0 - crescent_band["solidity"]).idxmax(), "pos"]),
        ),
        (
            "with holes",
            "most interior rings - only the exterior boundary is stored, holes counted in metadata",
            int(frame.loc[frame["n_holes"].idxmax(), "pos"]),
        ),
        (
            "MultiPolygon part",
            "a simple part of a split feature; parent source_id + part_index preserved",
            int(simple_parts.loc[simple_parts["complexity"].idxmin(), "pos"]),
        ),
        (
            "slender but valid",
            "highest aspect ratio with complexity < 100 - kept on purpose (no aspect-ratio filter)",
            int(modest.loc[modest["aspect_ratio"].idxmax(), "pos"]),
        ),
    ]

    seen: set[int] = set()
    unique_cases: list[tuple[str, str, int]] = []
    for name, why, pos in cases:
        if pos in seen:
            continue
        seen.add(pos)
        unique_cases.append((name, why, pos))

    fig, axes = plt.subplots(len(unique_cases), 2, figsize=(8.0, 2.7 * len(unique_cases)))
    axes = np.atleast_2d(axes)
    entries: list[dict[str, Any]] = []
    for r, (name, why, pos) in enumerate(unique_cases):
        row = contours.iloc[pos]
        m = frame[frame["pos"] == pos].iloc[0]
        raw = _source_part(raw_geoms, str(row["source_id"]), int(row["part_index"]))
        ax_raw, ax_norm = axes[r]
        _draw_geometry(ax_raw, projection.project_geometry(raw))
        _draw_contour(ax_norm, stacked[pos])
        ax_raw.set_title(f"{name}: raw source polygon (aeqd metres)", fontsize=6.5)
        ax_norm.set_title(f"{name}: normalized 128-point contour", fontsize=6.5)
        ax_raw.set_ylabel(
            f"{str(row['candidate_id'])[:26]}\n{row['subtype']} | {float(m['area_ha']):.1f} ha\n"
            f"complexity {float(m['complexity']):.1f} (disc {CIRCLE_COMPLEXITY:.1f})\n"
            f"solidity {float(m['solidity']):.3f} | aspect {float(m['aspect_ratio']):.1f}\n"
            f"holes {int(m['n_holes'])} | parts {int(m['n_parts'])}",
            fontsize=5.4,
            rotation=0,
            ha="right",
            va="center",
        )
        ax_norm.set_xlabel(why, fontsize=5.2)
        entries.append(
            {
                "case": name,
                "candidate_id": str(row["candidate_id"]),
                "source_id": str(row["source_id"]),
                "subtype": str(row["subtype"]),
                "area_ha": float(m["area_ha"]),
                "perimeter_m": float(m["perimeter_m"]),
                "complexity_perimeter2_over_area": float(m["complexity"]),
                "solidity": float(m["solidity"]),
                "aspect_ratio": float(m["aspect_ratio"]),
                "n_holes": int(m["n_holes"]),
                "n_parts": int(m["n_parts"]),
                "why_selected": why,
            }
        )
    fig.suptitle(
        f"D. Complex real outlines after cleaning ({len(unique_cases)} cases)\n{PROBE_LABEL}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.11, 0, 1, 0.945))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {
        "file": out_path.name,
        "panels": len(unique_cases),
        "metric_definitions": {
            "complexity": "perimeter^2 / area; 4*pi = 12.57 for a disc, higher = more branching",
            "solidity": "area / convex_hull.area; low = concave (crescent, oxbow)",
            "aspect_ratio": "long / short side of the minimum rotated rectangle",
            "hull_defects": "number of convex-hull deficit polygons (concave bays)",
            "dominant_defect_share": (
                "largest bay / total hull deficit; ~1.0 = one clean crescent bay"
            ),
            "crescent_rule": (
                "hull_defects==1 and dominant_defect_share>0.95 and "
                "0.6<=solidity<=0.9 and complexity<60 and area>=0.25 ha, deepest bay wins"
            ),
        },
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contours", type=Path, default=Path("data/processed/contours.parquet"))
    parser.add_argument(
        "--rejected", type=Path, default=Path("data/processed/rejected_samples.parquet")
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/processed/data_manifest.json")
    )
    parser.add_argument(
        "--raw", type=Path, default=Path("data/raw/wisconsin_water.parquet")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_geometry_probe"))
    args = parser.parse_args(argv)

    for path in (args.contours, args.rejected, args.manifest, args.raw):
        if not path.exists():
            print(f"missing input: {path}", file=sys.stderr)
            return 1

    contours = load_contours(args.contours)
    stacked = stack_contours(contours)
    rejected = pd.read_parquet(args.rejected)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    projection = _projection(manifest)
    raw_geoms = _raw_geometries(args.raw)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "stage": "P0-2 geometry cleaning / contour extraction",
        "kind": "data preprocessing inspection",
        "not_a_retrieval_result": True,
        "query_sketch_used": None,
        "deterministic": True,
        "random_seed": None,
        "inputs": {
            "contours": args.contours.as_posix(),
            "rejected_samples": args.rejected.as_posix(),
            "manifest": args.manifest.as_posix(),
            "raw_source": args.raw.as_posix(),
        },
        "source": manifest["source"],
        "counts": manifest["counts"],
        "cleaning": manifest["cleaning"],
        "projection": manifest["projection"],
        "n_points": manifest["normalization"]["n_points"],
        "figures": {},
    }

    summary["figures"]["A_before_after"] = figure_before_after(
        contours,
        stacked,
        raw_geoms,
        projection,
        args.out_dir / "before_after.png",
        manifest,
    )
    summary["figures"]["B_candidate_gallery"] = figure_candidate_gallery(
        contours, stacked, args.out_dir / "candidate_gallery.png", manifest
    )
    summary["figures"]["C_rejected_examples"] = figure_rejected_examples(
        rejected, contours, projection, args.out_dir / "rejected_examples.png", manifest
    )
    summary["figures"]["D_complex_contours"] = figure_complex_contours(
        contours,
        stacked,
        raw_geoms,
        projection,
        args.out_dir / "complex_contours.png",
        manifest,
    )

    out_json = args.out_dir / "summary.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, fig in summary["figures"].items():
        print(f"  {name}: {fig['file']} ({fig['panels']} panels)")
    print(f"summary written: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
