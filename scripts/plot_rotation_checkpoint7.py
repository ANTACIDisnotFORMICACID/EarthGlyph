"""Draw the P0-4.6 Checkpoint 7-C/7-D rotation figures from stored results.

Nothing here re-ranks, re-scores or re-samples. Every order, distance, correlation and rank in
these four PNGs is read out of ``results/d6_*.{csv,json}``, which ``run_shape_benchmark.py --stage
d6`` wrote from the frozen design. The only geometry computed here is a *drawing*: contours are
centred, RMS-normalised and shown at one common scale, in their PUBLISHED pose. No panel is
rotated, aligned or re-fitted to make a method look better, and each figure asserts that the order
it drew is the order the CSV recorded.

Figures (all written to ``checkpoint7/``):

    rotation_methods_overview.png  — one query, one gallery, one frozen angle, every arm's REAL
                                     Top-5, with candidate ids, real ranks and distances
    rotation_rank_stability.png    — how each arm's ranking moves when the query is turned
    rotation_human_agreement.png   — Goal A alone: agreement with the human ratings, never mixed
                                     with the retrieval metrics of Goal B
    rotation_failure_cases.png     — the worst T2 misses, with causes labelled only where the data
                                     supports a label; everything else says "cause undetermined"

Usage:
    python scripts/plot_rotation_checkpoint7.py
    python scripts/plot_rotation_checkpoint7.py --figure overview
"""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
from plot_shape_benchmark import (  # noqa: E402
    RESULTS,
    draw,
    figure_limit,
    read_csv,
    read_json,
    short,
)

from earthglyph.shape_benchmark.datasets import BenchmarkShape  # noqa: E402
from earthglyph.shape_benchmark.probes import rotate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "shape_perception_benchmark" / "checkpoint7"
HITS = RESULTS / "d6_top10_hits.csv"
CELLS = RESULTS / "d6_retrieval_results.csv"
METRICS = RESULTS / "d6_rotation_metrics.json"

#: Every arm the frozen stage ran, in the order the figure lists them. Deliberately ALL of them:
#: a figure that dropped the arms it liked less would be the same cherry-picking the brief forbids
#: in the metrics ("不能为了使某个方法更快而让它少检索某些难例").
ARM_ROWS = [
    "E0",
    "E1",
    "E3/canonical",
    "E3/sample",
    "S0K1/naive",
    "S0K1/R0",
    "S0K1/R1_36",
    "S0K1/R1_72",
    "S0K1/R2",
    "S0K1/R3",
    "S0K2/naive",
    "S0K2/R0",
    "S0K2/R1_36",
    "S0K2/R1_72",
    "S0K2/R2",
    "S0K2/R3",
]
ARM_GROUPS = {
    "E0": "EarthGlyph geometric alignment",
    "E1": "EarthGlyph 91-D (ours, not ShapeComp)",
    "E3/canonical": "EarthGlyph Shape Context, canonical frame",
    "E3/sample": "EarthGlyph Shape Context, rotation search",
}
TOP_IN_OVERVIEW = 5
#: The query whose row is drawn. Chosen by a rule stated before looking at anything: the frozen
#: ``D6_QUERIES`` list is ordered by family, and this is its heart-shaped member, the family where
#: a 180-degree flip is visible to the eye. No arm's result influenced the pick.
OVERVIEW_QUERY = "mpeg7_Heart-1"
#: The on-grid slot. ``on_grid`` keeps the picture comparable with the 24-step search that CP6
#: inherited; the mid-grid and random slots are plotted in the stability figure instead.
OVERVIEW_SLOT = "on_grid"
ROLE_COLOUR = {"query": "#111111", "hit": "#1f6f8b", "miss": "#b03030", "target": "#2e7d32"}

FAMILIES_WITHOUT_AN_AXIS = {"near-circle", "near-circle (isotropic second moments)"}


def arm_title(arm: str) -> str:
    if arm in ARM_GROUPS:
        return ARM_GROUPS[arm]
    network, strategy = arm.split("/")
    labels = {
        "naive": "no wrapper (stored pose)",
        "R0": "R0 query-side 24 poses",
        "R1_36": "R1 query-side 36 poses",
        "R1_72": "R1 query-side 72 poses",
        "R2": "R2 bilateral canonical frame",
        "R3": "R3 24 poses both sides",
    }
    return f"{network} + {labels[strategy]}"


def points_of(shape: BenchmarkShape) -> np.ndarray:
    """Centred, RMS-normalised PUBLISHED pose. A drawing convention, never a scoring one."""
    pts = np.asarray(shape.display_points, dtype=float)
    pts = pts - pts.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum(pts**2, axis=1))))
    return pts / max(rms, 1e-9)


def parse_pose(pose: str) -> float:
    """The stored pose label as degrees. ``unrotated`` is the control row, i.e. zero."""
    if pose == "unrotated":
        return 0.0
    return float(pose.removeprefix("rotate_"))


def numeric(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.array([float(row[key]) for row in rows], dtype=float)


def caption(text: str, width: int = 205) -> str:
    """matplotlib never wraps a ``fig.text``, so the long provenance lines are wrapped here."""
    return "\n".join(textwrap.wrap(" ".join(text.split()), width))


# --------------------------------------------------------------------------- overview


def plot_methods_overview(index: dict[str, BenchmarkShape]) -> Path:
    hits = [r for r in read_csv(HITS) if r["query_id"] == OVERVIEW_QUERY and r["condition"] == "T2"]
    poses = sorted({r["pose"] for r in hits} - {"unrotated"})
    by_arm: dict[str, list[dict[str, str]]] = {}
    for row in hits:
        by_arm.setdefault(row["arm"], []).append(row)
    arms = [arm for arm in ARM_ROWS if arm in by_arm]
    if not arms:
        raise RuntimeError(f"no d6 hits stored for {OVERVIEW_QUERY!r}")

    # One angle for every arm, so the row-to-row difference is the METHOD and nothing else.
    slot_rows = [
        r
        for r in read_csv(CELLS)
        if r["query_id"] == OVERVIEW_QUERY and r["slot"] == OVERVIEW_SLOT
    ]
    angle = float(slot_rows[0]["angle_deg"]) if slot_rows else 0.0
    # same stored-rounding caveat as the failure figure: the hits file writes rotate_162.999
    chosen = next(p for p in poses if abs(parse_pose(p) - angle) < 1e-3)

    query = index[OVERVIEW_QUERY]
    columns = 2 + TOP_IN_OVERVIEW
    fig, axes = plt.subplots(
        len(arms), columns, figsize=(2.05 * columns, 2.0 * len(arms)), squeeze=False
    )
    limits = figure_limit(
        [points_of(query)]
        + [points_of(index[r["shape_id"]]) for r in hits if r["pose"] == chosen]
    )

    for position, arm in enumerate(arms):
        rows = sorted(
            (r for r in by_arm[arm] if r["pose"] == chosen), key=lambda r: int(r["rank"])
        )
        # The assertion is the figure's licence to exist: the panels are drawn in stored rank
        # order, and the stored distances are non-decreasing in that order.
        distances = numeric(rows, "distance")
        if np.any(np.diff(distances) < -1e-12):
            raise RuntimeError(f"{arm}: stored Top-K is not sorted by distance")
        if [int(r["rank"]) for r in rows] != list(range(1, len(rows) + 1)):
            raise RuntimeError(f"{arm}: stored ranks are not 1..k")

        left = axes[position][0]
        draw(left, points_of(query), colour=ROLE_COLOUR["query"], limit=limits, fill=True)
        left.set_title("query, published pose", fontsize=6.5, pad=2)
        turned = rotate(points_of(query), angle)
        second = axes[position][1]
        draw(second, turned, colour=ROLE_COLOUR["query"], limit=limits, fill=True)
        second.set_title(f"query, +{angle:g} deg (what was searched)", fontsize=6.5, pad=2)

        for column in range(TOP_IN_OVERVIEW):
            ax = axes[position][column + 2]
            row = rows[column]
            shape = index[row["shape_id"]]
            same = row["is_same_class"] == "1"
            draw(
                ax,
                points_of(shape),
                colour=ROLE_COLOUR["hit" if same else "miss"],
                limit=limits,
                fill=same,
            )
            ax.set_title(
                f"rank {row['rank']}  {short(row['shape_id'])}",
                fontsize=6.5,
                color=ROLE_COLOUR["hit" if same else "miss"],
                pad=2,
            )
            ax.set_xlabel(
                f"MPEG-7/{short(shape.category)}  d={float(row['distance']):.3f}  "
                f"{'same class' if same else 'OTHER class'}",
                fontsize=5.2,
                color="#555555",
                labelpad=1,
            )
        for column in range(columns):
            axes[position][column].set_ylabel(
                arm_title(arm) if column == 0 else "",
                fontsize=6.0,
                rotation=0,
                ha="right",
                va="center",
            )

    fig.suptitle(
        f"P0-4.6 CP7-C overview - {short(OVERVIEW_QUERY)} against the MPEG-7 gallery, condition T2 "
        f"(exact target + rigid near-duplicates removed), one frozen angle {angle:g} deg\n"
        f"all {len(arms)} arms, real stored Top-{TOP_IN_OVERVIEW}; ranks/distances come from "
        "d6_top10_hits.csv and nothing is re-ranked here. Panels share one display scale and keep "
        "the PUBLISHED pose, so the drawing cannot change an order.",
        fontsize=8.5,
    )
    fig.text(
        0.5,
        0.005,
        caption(
            "green/blue = same MPEG-7 category (NOT an independent perceptual class), red = a "
            "candidate from another category. 'rank' is the rank the arm actually returned under "
            "this arm's own rotation policy."
        ),
        ha="center",
        va="bottom",
        fontsize=6.5,
        color="#555555",
    )
    fig.tight_layout(rect=(0.16, 0.045, 1.0, 0.945))
    path = OUT / "rotation_methods_overview.png"
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- rank stability


def plot_rank_stability() -> Path:
    """Goal B alone: how each arm's ranking behaves when the query is turned.

    Quantities with different units never share an axis, and the per-arm panels are horizontal so
    the 16 arm names sit on a y axis instead of spilling into the panel below. Goal A (human
    agreement) is only ever plotted next to Goal B, never averaged into it.
    """
    cells = [r for r in read_csv(CELLS) if r["condition"] == "T2"]
    metrics = read_json(METRICS)
    by_arm = metrics["retrieval_by_arm"]
    arms = [arm for arm in ARM_ROWS if arm in by_arm]

    fig = plt.figure(figsize=(14.0, 13.4))
    grid = fig.add_gridspec(
        4, 2, height_ratios=[1.15, 1.0, 1.0, 1.05], hspace=0.66, wspace=0.34
    )
    order = np.arange(len(arms))

    # (a) how deep the first same-class answer sits, for every arm and every stored cell. Under T2
    # the exact target is REMOVED from the gallery, so ``target_rank`` is empty by design and the
    # honest column is ``first_relevant_rank``. Nothing is averaged away here: the band at rank 1
    # and the long tail are both measurements, and a dot is one cell, not one query.
    slots = ("control", "on_grid", "mid_grid", "random")
    slot_marker = {"control": "s", "on_grid": "o", "mid_grid": "^", "random": "D"}
    slot_colour = {"control": "#4a4a4a", "on_grid": "#1f6f8b", "mid_grid": "#c47f1f",
                   "random": "#2e7d32"}
    ax = fig.add_subplot(grid[0, :])
    for index, arm in enumerate(arms):
        rows = [r for r in cells if r["arm"] == arm]
        for slot_index, slot in enumerate(slots):
            sub = [r for r in rows if r["slot"] == slot]
            if not sub:
                continue
            ranks = np.maximum(numeric(sub, "first_relevant_rank"), 1.0)
            # the small x offset only separates the four frozen slots inside one arm and spreads
            # identical ranks; it carries no measurement meaning
            offset = np.linspace(-0.020, 0.020, len(ranks)) if len(ranks) > 1 else np.zeros(1)
            ax.scatter(
                index + (slot_index - 1.5) * 0.13 + offset,
                ranks,
                s=13,
                marker=slot_marker[slot],
                alpha=0.8,
                color=slot_colour[slot],
                linewidths=0.0,
                label=slot if index == 0 else None,
            )
        rotated = np.maximum(
            numeric([r for r in rows if r["slot"] != "control"], "first_relevant_rank"), 1.0
        )
        ax.hlines(
            float(np.median(rotated)), index - 0.33, index + 0.33, color="black", linewidth=1.1
        )
    ax.set_yscale("log")
    ax.set_ylim(0.9, 500)
    ax.set_xticks(order, arms, fontsize=6.4, rotation=35, ha="right")
    ax.set_ylabel("rank of the first same-class candidate (log)")
    ax.set_title(
        "(a) one dot per stored cell, all 16 arms; the black bar is the median over the rotated "
        "cells (lower is better)",
        fontsize=9.5,
        loc="left",
    )
    ax.legend(fontsize=6.6, ncol=4, loc="upper right", frameon=False)
    ax.grid(alpha=0.25, axis="y", which="both")

    # (b) the R1 question asked directly: does the frozen angle being ON the search grid, on its
    # MIDPOINT, or an ARBITRARY value change how deep the first same-class answer sits?
    ax = fig.add_subplot(grid[1, 0])
    table = np.full((len(arms), len(slots)), np.nan)
    for i, arm in enumerate(arms):
        for j, slot in enumerate(slots):
            sub = [r for r in cells if r["arm"] == arm and r["slot"] == slot]
            if sub:
                table[i, j] = float(
                    np.mean(np.maximum(numeric(sub, "first_relevant_rank"), 1.0))
                )
    finite = table[np.isfinite(table)]
    ax.imshow(
        np.ma.masked_invalid(table),
        cmap="viridis_r",
        norm=LogNorm(vmin=1.0, vmax=float(np.max(finite))),
        aspect="auto",
    )
    for i in range(len(arms)):
        for j in range(len(slots)):
            if np.isfinite(table[i, j]):
                ax.text(
                    j,
                    i,
                    f"{table[i, j]:.1f}",
                    ha="center",
                    va="center",
                    fontsize=5.4,
                    color="white",
                )
    ax.set_xticks(np.arange(len(slots)), slots, fontsize=6.6)
    ax.xaxis.set_ticks_position("bottom")
    ax.xaxis.set_label_position("bottom")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.set_title(
        "(b) mean first same-class rank per frozen angle slot (log colour; lower is better)",
        fontsize=9.5,
        loc="left",
    )

    # (c) self-consistency of the answer under rotation
    ax = fig.add_subplot(grid[1, 1])
    stability = [by_arm[arm]["T2"]["top1_stability_vs_unrotated"] for arm in arms]
    overlap = [by_arm[arm]["T2"]["top_10_overlap_vs_unrotated"] for arm in arms]
    ax.barh(order - 0.2, stability, height=0.4, color="#1f6f8b", label="Top-1 same as unrotated")
    ax.barh(order + 0.2, overlap, height=0.4, color="#c47f1f", label="Top-10 Jaccard vs unrotated")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.set_xlim(0.0, 1.5)
    ax.invert_yaxis()
    ax.set_title("(c) does the answer survive the turn?", fontsize=9.5, loc="left")
    ax.legend(fontsize=6.2, loc="center right", frameon=False)
    ax.grid(alpha=0.25, axis="x")

    # (d) how far rows actually move
    ax = fig.add_subplot(grid[2, 0])
    mean_shift = np.maximum([by_arm[arm]["T2"]["mean_abs_rank_shift"] for arm in arms], 1e-3)
    worst_shift = np.maximum([by_arm[arm]["T2"]["max_abs_rank_shift"] for arm in arms], 1e-3)
    ax.semilogx(mean_shift, order - 0.2, "o", markersize=4.0, color="#1f6f8b")
    ax.semilogx(worst_shift, order + 0.2, "x", markersize=5.0, color="#b03030")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.set_xlim(1e-3, 3e3)
    ax.invert_yaxis()
    ax.set_xlabel("absolute rank shift vs the same arm unrotated (log)")
    ax.set_title(
        "(d) how far the rows move: o = mean over cells, x = worst single query",
        fontsize=9.5,
        loc="left",
    )
    ax.grid(alpha=0.25, which="both")

    # (e) the price of each policy
    ax = fig.add_subplot(grid[2, 1])
    seconds = [by_arm[arm]["T2"]["seconds_per_query_mean"] for arm in arms]
    ax.semilogx(seconds, order, "s-", markersize=3.8, color="#4a4a4a")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.set_yticklabels([])
    ax.set_xlim(1e-3, 5e1)
    ax.invert_yaxis()
    ax.set_xlabel("seconds per query (log)")
    ax.set_title(
        "(e) measured query cost; the gallery-side precompute is separate, arms in (d)'s order",
        fontsize=9.5,
        loc="left",
    )
    ax.grid(alpha=0.25)

    # (f) the honest warning: which wrapper wins depends on which metric is asked
    ax = fig.add_subplot(grid[3, 0])
    height = 0.26
    primary = [by_arm[arm]["T2"]["top1_same_class_accuracy"] for arm in arms]
    recall = [by_arm[arm]["T2"]["class_recall_at_k"] for arm in arms]
    mrr = [by_arm[arm]["T2"]["class_mrr"] for arm in arms]
    ax.barh(order - height, primary, height=height, color="#1f6f8b", label="Top-1 same class")
    ax.barh(order, recall, height=height, color="#c47f1f", label="class Recall@10")
    ax.barh(order + height, mrr, height=height, color="#2e7d32", label="class MRR")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.set_xlim(0.0, 1.5)
    ax.invert_yaxis()
    ax.set_title("(f) T2 quality is not one number", fontsize=9.5, loc="left")
    ax.legend(fontsize=6.2, loc="center right", frameon=False)
    ax.grid(alpha=0.25, axis="x")

    # (g) the two goals, one row per arm, never merged into one score
    ax = fig.add_subplot(grid[3, 1])
    agreement = metrics["agreement_by_arm"]
    human = [agreement[arm]["agreement_pooled"] for arm in arms]
    # A scatter of the same two measurements buries 11 arms in one corner, so the arm names move to
    # the y axis and the two goals become two dots joined by the gap between them. The line is a
    # difference, not an average: no number in this benchmark combines Goal A and Goal B.
    ax.hlines(order, primary, human, color="#999999", linewidth=1.6, alpha=0.8)
    ax.scatter(primary, order, s=30, c="#1f6f8b", edgecolor="black", linewidth=0.4, zorder=3,
               label="Goal B: T2 Top-1 same-class under random rotation")
    ax.scatter(human, order, s=30, c="#c47f1f", edgecolor="black", linewidth=0.4, zorder=3,
               label="Goal A: agreement with the human ratings (pooled)")
    ax.set_yticks(order, arms, fontsize=6.2)
    ax.invert_yaxis()
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("two measurements of the same arm, reported side by side")
    ax.set_title("(g) Goal A and Goal B are never averaged", fontsize=9.5, loc="left")
    ax.legend(fontsize=6.2, loc="lower right", frameon=False)
    ax.grid(alpha=0.25, axis="x")

    fig.suptitle(
        "P0-4.6 CP7-C - behaviour under random query rotation",
        fontsize=12,
        y=0.995,
    )
    fig.subplots_adjust(top=0.955, bottom=0.045)
    precompute = metrics["precompute_seconds"]
    fig.text(
        0.5,
        0.004,
        caption(
            "(a)-(f) are Goal B retrieval measurements; Goal A (human agreement) appears only in "
            "(g) and is never averaged into any of them. "
            f"12 queries x {metrics['design']['angles_per_query']} frozen angles each "
            f"({', '.join(metrics['design']['angle_slots'])}), condition T2 = exact target and "
            f"rigid near-duplicates removed. Counts declared before the run "
            f"(frozen_before_run={metrics['design']['frozen_before_run']}); no cell was dropped "
            "for any arm, so a faster arm never skipped a hard case. "
            "In (a)/(b) the four frozen slots are the angle categories: control is the unrotated "
            "reference, on_grid is an exact multiple of the 24-direction search grid (15 deg), "
            "mid_grid is that grid plus 7.5 deg, and random is an arbitrary off-grid draw kept at "
            "least 2 deg from both the grid and its midpoints. "
            f"Gallery-side precompute, one-off: pose embeddings "
            f"{precompute['gallery_pose_embeddings_S0K1']:.1f}s KerNet1 / "
            f"{precompute['gallery_pose_embeddings_S0K2']:.1f}s KerNet2. "
            "In (g) the join is the difference between the two goals, never an average of them. "
            "MPEG-7 category is a label, not an independent perceptual similarity (D71)."
        ),
        ha="center",
        va="bottom",
        fontsize=6.8,
        color="#555555",
    )
    path = OUT / "rotation_rank_stability.png"
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- human agreement


def plot_human_agreement() -> Path:
    metrics = read_json(METRICS)
    agreement = metrics["agreement_by_arm"]
    bootstrap = metrics["agreement_bootstrap"]
    arms = [arm for arm in ARM_ROWS if arm in agreement]
    # one row order and one colour per arm for all three panels: 16 curves cannot be keyed by the
    # default 10-colour cycle, and an unlabelled repeat of colours would be unreadable
    rank_order = np.argsort([agreement[arm]["agreement_pooled"] for arm in arms])
    listed = [arms[index] for index in rank_order]
    turbo = matplotlib.colormaps["turbo"]
    colour_of = {arm: turbo(position / (len(listed) - 1)) for position, arm in enumerate(listed)}

    fig = plt.figure(figsize=(14.6, 8.4))
    grid = fig.add_gridspec(2, 3, width_ratios=[1.0, 1.0, 0.13], hspace=0.46, wspace=0.28)

    ax = fig.add_subplot(grid[:, 0])
    for position, arm in enumerate(listed):
        row = agreement[arm]
        ax.errorbar(
            row["agreement_pooled"],
            position,
            xerr=[
                [row["agreement_pooled"] - row["ci_low_grouped"]],
                [row["ci_high_grouped"] - row["agreement_pooled"]],
            ],
            fmt="o",
            markersize=4.5,
            color=colour_of[arm],
            capsize=2.4,
            linewidth=1.2,
        )
        ax.text(
            row["ci_high_grouped"] + 0.008,
            position,
            f"{row['agreement_pooled']:.3f}",
            fontsize=6.0,
            va="center",
        )
    ax.set_yticks(np.arange(len(listed)), listed, fontsize=6.2)
    ax.axvline(0.0, color="#999999", linewidth=0.8)
    ax.set_xlabel("Spearman rho between the arm's DISTANCE and the human SIMILARITY rating")
    ax.set_title("(a) Goal A only: prediction of the human ratings", fontsize=9, loc="left")
    ax.grid(alpha=0.25, axis="x")

    ax = fig.add_subplot(grid[0, 1])
    for arm in listed:
        row = agreement[arm]
        angles = [parse_pose(key) for key in sorted(row["curve_by_angle"])]
        values = [row["curve_by_angle"][key] for key in sorted(row["curve_by_angle"])]
        ax.plot(angles, values, "-o", markersize=2.6, linewidth=1.0, color=colour_of[arm])
    ax.set_xlabel("rotation angle of one member of the rated pair (deg)")
    ax.set_ylabel("agreement rho")
    ax.set_title(
        "(b) agreement against angle. A FLAT line means the arm's distance does not move\n"
        "when the picture is turned; the two no-wrapper arms swing across zero",
        fontsize=9,
        loc="left",
    )
    ax.grid(alpha=0.25)

    ax = fig.add_subplot(grid[1, 1])
    spread = [
        max(agreement[arm]["curve_by_angle"].values())
        - min(agreement[arm]["curve_by_angle"].values())
        for arm in listed
    ]
    position = np.arange(len(listed))
    ax.barh(position, spread, color=[colour_of[arm] for arm in listed])
    ax.set_yticks(position, listed, fontsize=6.2)
    ax.set_xlabel("max - min agreement across the seven stress angles")
    ax.set_title("(c) how much the answer depends on the angle", fontsize=9, loc="left")
    ax.grid(alpha=0.25, axis="x")

    # the key repeats the row order of (a)/(c), so any panel can be read off any other
    axk = fig.add_subplot(grid[:, 2])
    axk.barh(position, np.ones(len(listed)), height=0.92, color=[colour_of[arm] for arm in listed])
    axk.set_yticks(position, listed, fontsize=5.8)
    axk.set_xticks([])
    axk.set_ylim(-0.5, len(listed) - 0.5)
    axk.set_title("arm", fontsize=8)

    fig.suptitle(
        "P0-4.6 CP7-C - agreement with HUMAN similarity ratings (Goal A, reported separately)",
        fontsize=11,
        y=0.985,
    )
    fig.text(
        0.5,
        0.006,
        caption(
            f"Statistical unit: {bootstrap['sampling_unit']}. "
            f"Headline intervals: {bootstrap['scheme_fixed_angle']}. "
            f"Sensitivity: {bootstrap['scheme_angle_randomised']} "
            f"(condition factor: {bootstrap['condition_factor']}). "
            "These numbers use the published human ratings; rotating a stimulus does not create "
            "new human data, so (a)-(c) measure invariance of the model, not human judgements of "
            "rotated shapes (D63)."
        ),
        ha="center",
        va="bottom",
        fontsize=6.6,
        color="#555555",
    )
    path = OUT / "rotation_human_agreement.png"
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- failure cases


def cause_of(arm: str, family: str, unrotated_rank: float, turned_rank: float) -> tuple[str, str]:
    """Label a failure only where a measurement supports the label; otherwise say it is unknown."""
    wrapped = "/" in arm and not arm.endswith("/naive")
    uses_a_frame = arm.endswith("/R2")
    if not np.isfinite(unrotated_rank):
        return ("cause undetermined - no unrotated control stored (open item)", "#666666")
    if unrotated_rank <= 2.0 and turned_rank > 20.0:
        return (
            "measured: first same-class answer sat in the first 2 unrotated and falls past 20 once "
            "the query is turned",
            "#b03030",
        )
    if uses_a_frame and family in FAMILIES_WITHOUT_AN_AXIS:
        return (
            "measured: no second-moment axis exists for this contour (F1 isotropic fallback)",
            "#c47f1f",
        )
    if not wrapped and turned_rank > 10.0:
        return ("measured: arm searches no rotation at all", "#b03030")
    return ("cause undetermined - not guessed (open item)", "#666666")


def plot_failure_cases(index: dict[str, BenchmarkShape]) -> Path:
    cells = [r for r in read_csv(CELLS) if r["condition"] == "T2"]
    hits = read_csv(HITS)
    unrotated = {
        (row["arm"], row["query_id"]): float(row["first_relevant_rank"])
        for row in cells
        if row["slot"] == "control"
    }
    rotated = [row for row in cells if row["slot"] != "control"]
    worst = sorted(rotated, key=lambda row: -float(row["first_relevant_rank"]))[:8]

    columns = 5
    fig, axes = plt.subplots(
        len(worst), columns, figsize=(2.3 * columns, 2.2 * len(worst)), squeeze=False
    )
    for position, row in enumerate(worst):
        arm = row["arm"]
        query_id = row["query_id"]
        angle = float(row["angle_deg"])
        deep = float(row["first_relevant_rank"])
        query = index[query_id]
        limit = figure_limit([points_of(query)])
        ax = axes[position][0]
        draw(
            ax,
            rotate(points_of(query), angle),
            colour=ROLE_COLOUR["query"],
            limit=limit,
            fill=True,
        )
        ax.set_title(f"query {short(query_id)} +{angle:g} deg", fontsize=6.4, pad=2)

        # ``d6_top10_hits.csv`` writes the pose rounded (rotate_162.999) while
        # ``d6_retrieval_results.csv`` keeps the full float, so an exact comparison silently drops
        # cells; 1e-3 deg is below the stored rounding and far above any real angle difference.
        rows = sorted(
            (
                h
                for h in hits
                if h["arm"] == arm
                and h["query_id"] == query_id
                and h["pose"] != "unrotated"
                and abs(parse_pose(h["pose"]) - angle) < 1e-3
            ),
            key=lambda h: int(h["rank"]),
        )
        for column in range(1, 4):
            ax = axes[position][column]
            if column - 1 >= len(rows):
                ax.axis("off")
                continue
            hit = rows[column - 1]
            shape = index[hit["shape_id"]]
            same = hit["is_same_class"] == "1"
            draw(
                ax,
                points_of(shape),
                colour=ROLE_COLOUR["hit" if same else "miss"],
                limit=limit,
                fill=same,
            )
            ax.set_title(
                f"rank {hit['rank']}  {short(hit['shape_id'])}",
                fontsize=6.4,
                color=ROLE_COLOUR["hit" if same else "miss"],
                pad=2,
            )
            ax.set_xlabel(
                f"MPEG-7/{short(shape.category)}  d={float(hit['distance']):.3f}",
                fontsize=5.4,
                color="#555555",
                labelpad=1,
            )
        # The last panel is what the arm SHOULD have returned early: the shallowest same-class
        # candidate inside the stored Top-10, or an explicit statement that there is none.
        ax = axes[position][4]
        same_class = [h for h in rows if h["is_same_class"] == "1"]
        if same_class:
            hit = same_class[0]
            shape = index[hit["shape_id"]]
            draw(ax, points_of(shape), colour=ROLE_COLOUR["target"], limit=limit, fill=True)
            ax.set_title(
                f"rank {hit['rank']}  {short(hit['shape_id'])}",
                fontsize=6.4,
                color=ROLE_COLOUR["target"],
                pad=2,
            )
            ax.set_xlabel(
                f"MPEG-7/{short(shape.category)}  same class, {len(same_class)} in Top-10",
                fontsize=5.4,
                color="#555555",
                labelpad=1,
            )
        else:
            ax.text(
                0.5,
                0.5,
                f"no same-class shape\nin the stored Top-10\n(first one is rank {deep:g})",
                ha="center",
                va="center",
                fontsize=5.8,
                color="#b03030",
            )
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title("nothing to draw here", fontsize=6.4, color="#b03030", pad=2)
        base = unrotated.get((arm, query_id), float("nan"))
        text, colour = cause_of(arm, row["family"], base, deep)
        axes[position][0].set_ylabel(
            f"{arm_title(arm)}\nfirst same-class rank {deep:g} (unrotated {base:g})\n{text}",
            fontsize=5.4,
            rotation=0,
            ha="right",
            va="center",
            color=colour,
        )

    fig.suptitle(
        "P0-4.6 CP7-C - the eight worst T2 cells under random rotation, exactly as stored",
        fontsize=11,
    )
    fig.text(
        0.5,
        0.006,
        caption(
            "Selection rule: sort every arm x query x rotated-slot cell by first-same-class rank "
            "and take the eight largest. No cell was chosen by eye. T2 removes the exact target "
            "and its rigid near-duplicates, so 'rank' here counts same-class answers, and a cause "
            "is printed only when a stored measurement supports it - everything else is labelled "
            "undetermined rather than explained."
        ),
        ha="center",
        va="bottom",
        fontsize=6.6,
        color="#555555",
    )
    fig.tight_layout(rect=(0.30, 0.025, 1.0, 0.955))
    path = OUT / "rotation_failure_cases.png"
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- entry point


FIGURES = {
    "overview": lambda index: plot_methods_overview(index),
    "stability": lambda index: plot_rank_stability(),
    "agreement": lambda index: plot_human_agreement(),
    "failures": lambda index: plot_failure_cases(index),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--figure", default="all", choices=["all", *FIGURES], help="which PNG to write"
    )
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    index: dict[str, BenchmarkShape] = {}
    wanted = list(FIGURES) if args.figure == "all" else [args.figure]
    needs_index = {"overview", "failures"} & set(wanted)
    if needs_index:
        from plot_shape_benchmark import shape_index

        index = shape_index()
        loaded: dict[str, Any] = {shape.shape_id: shape for shape in index.values()}
        missing = [
            row["shape_id"]
            for row in read_csv(HITS)
            if (args.figure == "all" or args.figure in needs_index)
            and row["shape_id"] not in loaded
        ]
        if missing:
            raise RuntimeError(f"{len(missing)} ranked shapes are not in the loaded gallery")
    for name in wanted:
        written = FIGURES[name](index)
        size = written.stat().st_size
        if size < 10_000:
            raise RuntimeError(f"{written} is suspiciously small ({size} bytes)")
        print(f"wrote {written} ({size:,} bytes)")


if __name__ == "__main__":
    main()
