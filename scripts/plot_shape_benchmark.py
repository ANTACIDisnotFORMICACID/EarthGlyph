"""Draw the P0-4.6 figures from stored results.

This script re-ranks nothing. Every panel's order comes from a CSV written by
``run_shape_benchmark.py``; the only geometry computed here is a *display* pose, fitted with
the arm-independent symmetric-chamfer rotation fitter in ``earthglyph.retrieval.display_alignment``.
That is why ``comparison_*.png`` (fitted pose) and ``comparison_*_original_pose.png`` (published
pose) show the same shapes in the same order.

Figures:
    overview   perception_benchmark_overview.png   — D2/T2, query + each arm's Top-3
    comparison comparison_<kind>.png               — D3, each arm's Top-10, fitted display pose
               comparison_<kind>_original_pose.png — same ranks, published pose
    human      human_similarity_scatter.png, human_pairwise_agreement.png,
               human_similarity_failure_cases.png
    rotation   rotation_stress.png                    — D5, agreement + overlap vs angle

Usage:
    python scripts/plot_shape_benchmark.py --figure all
    python scripts/plot_shape_benchmark.py --figure comparison --kind star
"""

from __future__ import annotations

import argparse
import csv
import json
import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402

from earthglyph.retrieval.display_alignment import align_for_display  # noqa: E402
from earthglyph.shape_benchmark.datasets import (  # noqa: E402
    BenchmarkShape,
    load_h1,
    load_h2,
    load_mpeg7,
)
from earthglyph.shape_benchmark.probes import (  # noqa: E402
    build_structure_probe,
    make_probe,
)

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "experiments" / "shape_perception_benchmark"
CACHE = BENCH / "cache"
RESULTS = BENCH / "results"
FIGURES = BENCH / "figures"
QUERIES = BENCH / "queries"
KINDS = ["star", "heart", "crescent", "forked", "natural"]
ARM_ORDER = ["E0", "E1", "E3", "S0", "S0K1", "S0K2"]
ARM_TITLES = {
    "E0": "E0  geometric alignment",
    "E1": "E1  EarthGlyph ShapeComp-inspired (91-D, ours)",
    "E3": "E3  Shape Context + rotation search",
    "S0": "S0  official ShapeComp 22-D (published coordinates)",
    "S0K1": "S0K1  official KerNet1 (actually run, numpy port)",
    "S0K2": "S0K2  official KerNet2 (actually run, numpy port)",
}


def arms_in(rows: list[dict[str, str]]) -> list[str]:
    """Arms present in a result table, in canonical order — figures adapt to what was run."""
    present = {r["method"] for r in rows}
    return [a for a in ARM_ORDER if a in present]
ROLE_COLOUR = {
    "query": "#1f4e9c",
    "structural_variant": "#1a7f37",
    "gross_distractor": "#b3261e",
    "gallery": "#444444",
}
DPI = 140


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def shape_index() -> dict[str, BenchmarkShape]:
    """Every outline the figures can name, rebuilt from the same deterministic loaders."""
    shapes, _ = load_mpeg7(CACHE)
    index = {s.shape_id: s for s in shapes}
    index.update(load_h1(CACHE).shapes)
    index.update(load_h2(CACHE).shapes)
    natural = index.get("mpeg7_bat-1")
    for kind in KINDS:
        probe = build_structure_probe(kind, natural=natural)
        index[probe.query.shape_id] = probe.query
        for shape in probe.structural_variants + probe.gross_distractors:
            index[shape.shape_id] = shape
    for stem in ["Heart-1", "fork-1", "bat-1", "tree-1", "apple-1", "butterfly-1"]:
        query_id = f"mpeg7_{stem}"
        if query_id not in index:
            continue
        for probe_name, kwargs in [("circle", {}), ("ellipse", {}), ("simple_curve", {"seed": 11})]:
            distractor = make_probe(probe_name, **kwargs)
            shape_id = f"{query_id}__t3_{distractor.shape_id}"
            index[shape_id] = BenchmarkShape(
                shape_id=shape_id,
                source="synthetic_distractor",
                points=distractor.points,
                display_points=distractor.display_points,
            )
    return index


def _rms(points: np.ndarray) -> float:
    centred = points - points.mean(axis=0)
    return float(np.sqrt(np.mean(np.sum(centred**2, axis=1))))


def posed(
    shape: BenchmarkShape, query: BenchmarkShape, mode: str
) -> np.ndarray:
    """Return the contour to draw. ``mode`` never touches the stored ranking."""
    if mode == "original":
        points = np.asarray(shape.display_points, dtype=float)
        points = points - points.mean(axis=0)
        return points / max(_rms(points), 1e-9)
    if mode == "aligned":
        fitted, _ = align_for_display(
            query.points, shape.points, key=(query.shape_id, shape.shape_id)
        )
        return np.asarray(fitted, dtype=float)
    return np.asarray(shape.points, dtype=float)


def draw(
    ax: Axes,
    points: np.ndarray,
    *,
    colour: str,
    limit: float,
    fill: bool = False,
) -> None:
    ring = np.vstack([points, points[:1]])
    ax.plot(ring[:, 0], ring[:, 1], color=colour, linewidth=1.1)
    if fill:
        ax.fill(ring[:, 0], ring[:, 1], color=colour, alpha=0.10)
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#cccccc")


def short(shape_id: str) -> str:
    name = shape_id.removeprefix("mpeg7_").removeprefix("probe_")
    name = name.removeprefix("natural_mpeg7_").removeprefix("natural_")
    if "__" in name:
        token = name.split("__")[-1]
        return token.removeprefix("distractor_probe_").removeprefix("distractor_")
    return name


def source_tag(shape: BenchmarkShape) -> str:
    if shape.source == "MPEG7":
        return f"MPEG-7 / {shape.category}"
    return shape.source


def panel_label(ax: Axes, rank: int | str, shape: BenchmarkShape, role: str) -> None:
    ax.set_title(f"{rank}  {short(shape.shape_id)}", fontsize=7, color=ROLE_COLOUR[role], pad=2)
    ax.set_xlabel(source_tag(shape), fontsize=5.5, color="#666666", labelpad=1)


def figure_limit(points: list[np.ndarray]) -> float:
    extent = max(float(np.abs(p).max()) for p in points)
    return extent * 1.08


# --------------------------------------------------------------------------- overview


def plot_overview(index: dict[str, BenchmarkShape], condition: str = "T2") -> Path:
    rows = [
        r for r in read_csv(RESULTS / "retrieval_results.csv")
        if r["experiment"] == "d2" and r["condition"] == condition
    ]
    query_ids = list(dict.fromkeys(r["query_id"] for r in rows))
    arms = arms_in(rows)
    top3: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row["query_id"], row["method"])
        top3.setdefault(key, []).append(row)
    for key in top3:
        top3[key] = sorted(top3[key], key=lambda r: int(r["rank"]))[:3]

    n_cols = 1 + 3 * len(arms)
    fig = plt.figure(figsize=(1.55 * n_cols, 1.75 * len(query_ids) + 1.1))
    grid = GridSpec(
        len(query_ids), n_cols, figure=fig,
        left=0.02, right=0.98, top=0.88, bottom=0.02, wspace=0.08, hspace=0.42,
    )
    drawn: list[np.ndarray] = []
    cells: list[tuple[int, int, str, str, BenchmarkShape]] = []
    for row_i, query_id in enumerate(query_ids):
        query = index[query_id]
        cells.append((row_i, 0, "query", query_id, query))
        drawn.append(posed(query, query, "aligned"))
        for arm_i, arm in enumerate(arms):
            for rank_i, hit in enumerate(top3.get((query_id, arm), [])):
                shape = index[hit["shape_id"]]
                cells.append((row_i, 1 + arm_i * 3 + rank_i, hit["rank"], hit["shape_id"], shape))
                drawn.append(posed(shape, query, "aligned"))
    limit = figure_limit(drawn)
    for row_i, col_i, rank, _shape_id, shape in cells:
        ax = fig.add_subplot(grid[row_i, col_i])
        query = index[query_ids[row_i]]
        role = "query" if col_i == 0 else "gallery"
        draw(ax, posed(shape, query, "aligned"), colour=ROLE_COLOUR[role], limit=limit, fill=True)
        panel_label(ax, rank, shape, role)
    for arm_i, arm in enumerate(arms):
        ax = fig.add_subplot(grid[0, 1 + arm_i * 3: 1 + arm_i * 3 + 3])
        ax.axis("off")
        ax.set_title(ARM_TITLES[arm], fontsize=8, pad=14)
    fig.suptitle(
        f"Shape-perception benchmark — MPEG-7 gallery, condition {condition} "
        "(query and its rigid near-duplicates removed)\n"
        "Same gallery for every arm. Labels are rank + id + category, not scores. "
        "Display pose fitted with an arm-independent chamfer rotation; ranks are unchanged.\n"
        "S0K1/S0K2 run the official KerNet weights locally (numpy port) and can score MPEG-7; "
        "S0 (published coordinates) is absent here because no MPEG-7 coordinates were published.",
        fontsize=8.5, y=0.995,
    )
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / "perception_benchmark_overview.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- comparison


def plot_comparison(index: dict[str, BenchmarkShape], kind: str, mode: str) -> Path:
    rows = [
        r for r in read_csv(RESULTS / "structure_probe_results.csv")
        if r["experiment"] == "d3" and r["condition"] == kind
    ]
    if not rows:
        raise ValueError(f"no D3 rows for {kind}; run --stage d3 first")
    arms = arms_in(rows)
    query_id = rows[0]["query_id"]
    query = index[query_id]
    probe = build_structure_probe(kind, natural=index.get("mpeg7_bat-1"))
    key_shapes = [query, *probe.structural_variants, *probe.gross_distractors]
    key_roles = ["query"] + ["structural_variant"] * 3 + ["gross_distractor"] * 3

    n_cols = 11
    fig = plt.figure(figsize=(1.55 * n_cols, 1.85 * (len(arms) + 1) + 1.2))
    grid = GridSpec(
        len(arms) + 1, n_cols, figure=fig,
        left=0.035, right=0.99, top=0.87, bottom=0.02, wspace=0.08, hspace=0.55,
    )
    drawn = [posed(s, query, mode) for s in key_shapes]
    hits_by_arm: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        hits_by_arm.setdefault(row["method"], []).append(row)
    for arm in arms:
        for hit in sorted(hits_by_arm.get(arm, []), key=lambda r: int(r["rank"])):
            drawn.append(posed(index[hit["shape_id"]], query, mode))
    limit = figure_limit(drawn)

    for col, (shape, role) in enumerate(zip(key_shapes, key_roles, strict=True)):
        ax = fig.add_subplot(grid[0, col])
        draw(ax, posed(shape, query, mode), colour=ROLE_COLOUR[role], limit=limit, fill=True)
        token = short(shape.shape_id)
        if col == 0:
            title = "QUERY"
        elif role == "structural_variant":
            title = f"variant\n{token}"
        else:
            title = f"distractor\n{token}"
        ax.set_title(title, fontsize=6.5, color=ROLE_COLOUR[role], pad=2)
        ax.set_xlabel(source_tag(shape), fontsize=5.5, color="#666666", labelpad=1)
    for col in range(len(key_shapes), n_cols):
        fig.add_subplot(grid[0, col]).axis("off")

    for arm_i, arm in enumerate(arms):
        ax = fig.add_subplot(grid[arm_i + 1, 0])
        draw(ax, posed(query, query, mode), colour=ROLE_COLOUR["query"], limit=limit, fill=True)
        panel_label(ax, "query", query, "query")
        hits = sorted(hits_by_arm.get(arm, []), key=lambda r: int(r["rank"]))[:10]
        for col, hit in enumerate(hits, start=1):
            shape = index[hit["shape_id"]]
            role = hit.get("role", "gallery")
            ax = fig.add_subplot(grid[arm_i + 1, col])
            draw(ax, posed(shape, query, mode), colour=ROLE_COLOUR[role], limit=limit, fill=True)
            panel_label(ax, hit["rank"], shape, role)
        for col in range(len(hits) + 1, n_cols):
            fig.add_subplot(grid[arm_i + 1, col]).axis("off")
        ax = fig.add_subplot(grid[arm_i + 1, :])
        ax.axis("off")
        ax.set_ylabel(ARM_TITLES[arm], fontsize=8, rotation=90, labelpad=28)

    pose_note = (
        "published pose (rescaled to a common panel size, orientation untouched)"
        if mode == "original"
        else "one arm-independent chamfer rotation fitted per candidate, display only"
    )
    n_gallery = (
        sum(1 for s in index.values() if s.source == "MPEG7")
        + len(probe.structural_variants)
        + len(probe.gross_distractors)
    )
    fig.suptitle(
        f"D3 structure probe — {kind}.  Each arm's Top-10 from the same "
        f"{n_gallery}-shape gallery (MPEG-7 + 3 variants + 3 distractors).\n"
        f"Pose: {pose_note}. Green = structural variant, red = gross distractor, "
        "grey = MPEG-7 gallery item.\n"
        "The variant/distractor roles are constructed by design, NOT validated human truth. "
        "Both pose figures show identical ranks.",
        fontsize=8.5, y=0.995,
    )
    FIGURES.mkdir(parents=True, exist_ok=True)
    suffix = "_original_pose" if mode == "original" else ""
    path = FIGURES / f"comparison_{kind}{suffix}.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- human


def plot_human_scatter(metrics: dict[str, Any], predictions: list[dict[str, str]]) -> Path:
    h1 = [r for r in predictions if r["dataset"] == "H1"]
    rating = np.array([float(r["human_mean_similarity"]) for r in h1])
    arms = [a for a in ARM_ORDER if f"dist_{a}" in h1[0]]
    n_rows = max(1, (len(arms) + 1) // 2)
    fig, axes = plt.subplots(n_rows, 2, figsize=(10.5, 4.6 * n_rows))
    for ax, arm in zip(axes.flat, arms, strict=False):
        distance = np.array([float(r[f"dist_{arm}"]) for r in h1])
        stats = metrics["H1"]["methods"][arm]
        agreement = stats["agreement_two_level"]
        ax.scatter(rating, distance, s=13, alpha=0.6, color=ROLE_COLOUR["query"], edgecolors="none")
        slope, intercept = np.polyfit(rating, distance, 1)
        xs = np.linspace(rating.min(), rating.max(), 50)
        ax.plot(xs, slope * xs + intercept, color="#b3261e", linewidth=1.0)
        observed = stats["vs_mean_rating"]
        ax.set_title(
            f"{ARM_TITLES[arm]}\n"
            f"agreement rho = {agreement['spearman']:.3f} "
            f"[{agreement['ci_low']:.3f}, {agreement['ci_high']:.3f}]  "
            f"Pearson r = {abs(observed['pearson']):.3f}",
            fontsize=8.5,
        )
        ax.set_xlabel("human mean similarity (14 observers, published units)", fontsize=7.5)
        ax.set_ylabel(f"{arm} distance (own units)", fontsize=7.5)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, linewidth=0.5)
    for ax in list(axes.flat)[len(arms):]:
        ax.axis("off")
    ceiling = metrics["H1"]["observer_agreement"]["leave_one_out_summary"]["median"]
    fig.suptitle(
        "D1 — predicting 250 published human similarity ratings (H1, Zenodo 4730985, CC-BY-4.0)\n"
        "Every pair is plotted; none were selected. Higher human rating = more similar, so a "
        "good model's distance falls: agreement rho is the sign-flipped Spearman.\n"
        f"CI = two-level bootstrap (pairs AND observers, 2000 replicates, seed 20261107). "
        f"Single-observer noise ceiling (leave-one-out median) = {ceiling:.3f}.\n"
        "S0 uses the authors' published original-ShapeComp 22-D coordinates (outputs only). "
        "S0K1/S0K2 actually run the official KerNet weights locally via the numpy port. "
        "E1 is EarthGlyph's own 91-D descriptor set, NOT ShapeComp.",
        fontsize=8.5, y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.925))
    path = FIGURES / "human_similarity_scatter.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_human_agreement(metrics: dict[str, Any]) -> Path:
    agreement = metrics["H1"]["observer_agreement"]
    pairwise = np.array([float(r["spearman"]) for r in agreement["pairwise"]])
    loo = np.array([float(r["spearman"]) for r in agreement["leave_one_out"]])
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.8))

    ax = axes[0]
    rng = np.random.default_rng(20261107)
    ax.scatter(
        pairwise, rng.uniform(-0.18, 0.18, len(pairwise)),
        s=16, alpha=0.6, color="#888888", edgecolors="none",
        label=f"observer vs observer (n={len(pairwise)})",
    )
    ax.scatter(
        loo, rng.uniform(0.82, 1.18, len(loo)),
        s=26, alpha=0.8, color=ROLE_COLOUR["query"], edgecolors="none",
        label=f"observer vs mean of other 13 (n={len(loo)})",
    )
    ax.axvline(float(np.median(loo)), color=ROLE_COLOUR["query"], linestyle="--", linewidth=1.0)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["pairwise", "leave-one-out"], fontsize=8)
    ax.set_ylim(-0.6, 1.6)
    ax.set_xlabel("Spearman rho between human observers", fontsize=8)
    ax.set_title(
        f"Human consistency on the same 250 pairs\n"
        f"pairwise median {np.median(pairwise):.3f}, "
        f"leave-one-out median {np.median(loo):.3f}",
        fontsize=9,
    )
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.25, linewidth=0.5, axis="x")

    ax = axes[1]
    arms = [a for a in ARM_ORDER if a in metrics["H1"]["methods"]]
    for i, arm in enumerate(arms):
        values = np.array(
            [abs(float(r["spearman"])) for r in metrics["H1"]["methods"][arm]["per_observer"]]
        )
        ax.scatter(
            np.full(len(values), i) + rng.uniform(-0.13, 0.13, len(values)), values,
            s=22, alpha=0.75, color=ROLE_COLOUR["query"], edgecolors="none",
        )
        ax.plot([i - 0.22, i + 0.22], [np.median(values)] * 2, color="#b3261e", linewidth=1.6)
    ax.axhspan(
        float(loo.min()), float(loo.max()), color="#1a7f37", alpha=0.12,
        label="single-observer leave-one-out range",
    )
    ax.axhline(float(np.median(loo)), color="#1a7f37", linestyle="--", linewidth=1.0)
    ax.set_xticks(range(len(arms)))
    ax.set_xticklabels(arms, fontsize=9)
    ax.set_ylabel("|Spearman| vs one observer's own ratings", fontsize=8)
    ax.set_title("Each arm against each individual observer (14 dots per arm)", fontsize=9)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(alpha=0.25, linewidth=0.5, axis="y")

    fig.suptitle(
        "D1 — participant-level agreement. Ratings are never treated as 3500 independent draws.\n"
        "A model has no observation noise, so it can exceed a single observer's leave-one-out "
        "score; the green band is context, not a hard ceiling.",
        fontsize=8.5, y=1.02,
    )
    fig.tight_layout()
    path = FIGURES / "human_pairwise_agreement.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_human_failures(
    index: dict[str, BenchmarkShape], metrics: dict[str, Any], failures: list[dict[str, str]]
) -> Path:
    selected = failures[:6]
    fig = plt.figure(figsize=(8.6, 1.95 * len(selected) + 1.0))
    grid = GridSpec(
        len(selected), 3, figure=fig, width_ratios=[1, 1, 1.7],
        left=0.02, right=0.98, top=0.88, bottom=0.02, wspace=0.12, hspace=0.30,
    )
    drawn: list[np.ndarray] = []
    for row in selected:
        for key in ("left_id", "right_id"):
            shape = index[row[key]]
            drawn.append(posed(shape, shape, "original"))
    limit = figure_limit(drawn)
    for row_i, row in enumerate(selected):
        left, right = index[row["left_id"]], index[row["right_id"]]
        for col, (side, shape) in enumerate([("A", left), ("B", right)]):
            ax = fig.add_subplot(grid[row_i, col])
            draw(
                ax, posed(shape, shape, "original"),
                colour=ROLE_COLOUR["query"], limit=limit, fill=True,
            )
            panel_label(ax, side, shape, "query")
        ax = fig.add_subplot(grid[row_i, 2])
        ax.axis("off")
        lines = [
            f"pair {row['pair_id']}   human mean similarity "
            f"{float(row['human_mean_similarity']):.3f} "
            f"(sd {float(row['human_sd_across_observers']):.3f})",
            f"human dissimilarity percentile {float(row['human_dissimilarity_percentile']):.3f}",
        ]
        for arm in ARM_ORDER:
            key = f"disagreement_{arm}"
            if key in row:
                lines.append(
                    f"{arm}: distance percentile {float(row[f'dist_percentile_{arm}']):.3f}"
                    f"   disagreement {float(row[key]):+.3f}"
                )
        ax.text(0.0, 0.95, "\n".join(lines), fontsize=7.2, va="top", family="monospace")
    rule = textwrap.fill(metrics["H1"]["failure_case_rule"], width=118)
    fig.suptitle(
        "D1 — the six pairs where E3 disagrees most with the human mean.\n"
        + rule + "\n"
        "Percentiles are scale-free ranks within the 250 pairs, not distances. "
        "Contours are drawn in their published pose.",
        fontsize=8.0, y=0.995,
    )
    path = FIGURES / "human_similarity_failure_cases.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- rotation stress


def plot_rotation_stress(metrics: dict[str, Any]) -> Path:
    """D5: agreement and retrieval overlap vs rotation-stress angle, every arm on one footing.

    Reads only ``d5_rotation_metrics.json``; no re-ranking. Rotation is the single variable,
    the human ratings (panel a) and the untransformed Top-10 (panel b) are shared references.
    """
    angles = [float(a) for a in metrics["angles_deg"]]
    curve_a = metrics["agreement_curve_by_arm"]
    curve_b = metrics["retrieval_overlap_curve_by_arm"]
    order = list(curve_a)
    palette = {
        "E0": "#1f4e9c", "E1": "#1a7f37", "E3": "#e07b00",
        "S0K1": "#b3261e", "S0K1+rot": "#b3261e",
        "S0K2": "#6f42c1", "S0K2+rot": "#6f42c1",
    }
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.0))

    for label in order:
        values = [curve_a[label][f"rotate_{a:g}"] for a in angles]
        style = "--" if label.endswith("+rot") else "-"
        axes[0].plot(angles, values, style, marker="o", ms=4, lw=1.6,
                      color=palette.get(label, "#444444"), label=label)
        values_b = [curve_b[label][f"rotate_{a:g}"] for a in angles]
        axes[1].plot(angles, values_b, style, marker="s", ms=4, lw=1.6,
                      color=palette.get(label, "#444444"), label=label)

    step = metrics.get("rotation_step_deg", 15.0)
    axes[0].set_title(
        "(a)  agreement with human dissimilarity vs rotation stress\n"
        "one member of every rated H1 pair turned by the angle; flat = rotation-robust",
        fontsize=8.5,
    )
    axes[0].set_xlabel("rotation stress applied to one stimulus (deg)", fontsize=8)
    axes[0].set_ylabel("agreement rho (positive = better)", fontsize=8)
    axes[1].set_title(
        "(b)  Top-10 retrieval stability vs rotation stress\n"
        "query turned by the angle; overlap with its own untransformed Top-10",
        fontsize=8.5,
    )
    axes[1].set_xlabel("rotation stress applied to the query (deg)", fontsize=8)
    axes[1].set_ylabel("Jaccard overlap with untransformed Top-10", fontsize=8)
    for ax in axes:
        ax.tick_params(labelsize=7.5)
        ax.grid(alpha=0.25, linewidth=0.5)
        ax.set_ylim(0, 1.0)
        ax.legend(fontsize=7, loc="lower left", ncol=2)
    fig.suptitle(
        "D5 — rotation-search added to the ShapeComp port, compared under identical rotation.\n"
        "E0/E1 are rotation-invariant by construction, E3 already searches a rotation grid; "
        "S0K1/S0K2 are the official KerNet weights run locally (naive, solid) vs the same "
        f"weights wrapped in a query-side {metrics.get('rotations_searched', 24)}-step grid "
        f"(dashed, {step:.0f} deg).\n"
        "On-grid angles (multiples of 15) and off-grid 37/191 are both swept. "
        "The official input contract and the authors' weights are unchanged by the wrapper.",
        fontsize=8.4, y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    path = FIGURES / "rotation_stress.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- queries


def write_query_contours(index: dict[str, BenchmarkShape]) -> Path:
    """Persist every query outline used by D2/D3/D4 so a reader can inspect the input."""
    ids: list[str] = []
    for name, column in [
        ("retrieval_results.csv", "query_id"),
        ("structure_probe_results.csv", "query_id"),
        ("transform_results.csv", "query_id"),
    ]:
        path = RESULTS / name
        if path.exists():
            ids.extend(r[column] for r in read_csv(path))
    QUERIES.mkdir(parents=True, exist_ok=True)
    path = QUERIES / "query_contours.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "source", "category", "point_index", "x", "y"])
        for query_id in dict.fromkeys(ids):
            base_id = query_id.split("__")[0]
            shape = index.get(query_id) or index.get(base_id)
            if shape is None:
                continue
            for i, (x, y) in enumerate(np.asarray(shape.points, dtype=float)):
                writer.writerow(
                    [query_id, shape.source, shape.category or "", i, f"{x:.8f}", f"{y:.8f}"]
                )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--figure", required=True,
        choices=["overview", "comparison", "human", "rotation", "all"],
    )
    parser.add_argument("--kind", choices=KINDS, default=None)
    parser.add_argument("--condition", default="T2")
    args = parser.parse_args()
    FIGURES.mkdir(parents=True, exist_ok=True)
    index = shape_index()
    written: list[Path] = []

    if args.figure in ("overview", "all"):
        written.append(plot_overview(index, args.condition))
    if args.figure in ("comparison", "all"):
        for kind in [args.kind] if args.kind else KINDS:
            written.append(plot_comparison(index, kind, "aligned"))
            written.append(plot_comparison(index, kind, "original"))
    if args.figure in ("human", "all"):
        metrics = read_json(RESULTS / "human_metrics.json")
        predictions = read_csv(RESULTS / "pairwise_predictions.csv")
        failures = read_csv(RESULTS / "failure_cases.csv")
        written.append(plot_human_scatter(metrics, predictions))
        written.append(plot_human_agreement(metrics))
        written.append(plot_human_failures(index, metrics, failures))
    if args.figure in ("rotation", "all"):
        d5_path = RESULTS / "d5_rotation_metrics.json"
        if d5_path.exists():
            written.append(plot_rotation_stress(read_json(d5_path)))
    if args.figure in ("overview", "comparison", "all"):
        written.append(write_query_contours(index))
    for path in written:
        print(f"wrote {path.relative_to(REPO)}  ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()