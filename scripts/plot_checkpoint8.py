"""Draw the P0-4.6 Checkpoint 8 figures from stored results.

Nothing here re-ranks, re-scores, re-samples or re-encodes. Every order, distance, ratio, rank and
interval in these PNGs is read out of ``checkpoint8/*.{csv,json}`` and
``results/d6_rotation_metrics{,_corrected}.json``, which the frozen stages wrote. The only geometry
computed here is a *drawing*: contours are centred, RMS-normalised and shown at one common scale in
their PUBLISHED pose, and a turned query is turned by the angle the design stored -- never by a
fit that would make a method look better. Each figure asserts that what it drew is what the table
recorded, and every query and angle shown is chosen by a rule fixed in the frozen design, not by
what came back.

Figures (all written to ``checkpoint8/``):

    corrected_metrics_comparison.png     — Goal 1 alone: what the 0-degree control cells did to the
                                           D6 headline (stored 72-cell pooling vs the corrected
                                           60-cell rotation-only view vs the 12 controls on their
                                           own), and the legacy ``class_recall_at_k`` shown next to
                                           the two metrics it conflated
    independent_retrieval_overview.png   — the same frozen query, the same frozen angle, every arm's
                                           REAL Top-5 with ids and distances
    independent_top1_by_family.png       — that overview widened over the sample by rule: the first
                                           frozen query of each family, Top-1 only
    rotation_vs_quality.png             — every rotated cell plotted against its own angle; the
                                           control cells are not in the picture
    arm_comparison_with_intervals.png    — the headline with its 24-cluster bootstrap interval, so a
                                           ranking is read as a ranking and not as a decimal
    independent_failure_cases.png        — each arm's worst stored miss, cause labelled only where
                                           the stored columns support it

Usage:
    python scripts/plot_checkpoint8.py
    python scripts/plot_checkpoint8.py --figure overview
"""

from __future__ import annotations

import argparse
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from plot_rotation_checkpoint7 import (  # noqa: E402
    arm_title,
    caption,
    numeric,
    points_of,
)
from plot_shape_benchmark import (  # noqa: E402
    RESULTS,
    draw,
    figure_limit,
    read_csv,
    read_json,
    shape_index,
    short,
)

from earthglyph.shape_benchmark import independent_validation as iv  # noqa: E402
from earthglyph.shape_benchmark.datasets import BenchmarkShape  # noqa: E402
from earthglyph.shape_benchmark.probes import rotate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "shape_perception_benchmark" / "checkpoint8"
DESIGN = OUT / "independent_validation_design.json"
CELLS = OUT / "cp8_retrieval_results.csv"
HITS = OUT / "cp8_top10_hits.csv"
EXCLUSIONS = OUT / "cp8_exclusions.csv"
METRICS = OUT / "independent_metrics.json"
D6_STORED = RESULTS / "d6_rotation_metrics.json"
D6_CORRECTED = RESULTS / "d6_rotation_metrics_corrected.json"

#: The condition every CP8 figure is drawn from. T2 is the primary condition the design promised;
#: T0 exists in the same table as a correctness check and is quoted in the captions, never mixed
#: into a T2 panel.
CONDITION = iv.CP8_PRIMARY_CONDITION
TOP_IN_OVERVIEW = 5
DPI = 150
ROLE_COLOUR = {"query": "#111111", "hit": "#1f6f8b", "miss": "#b03030", "target": "#2e7d32"}
ARM_COLOUR = {
    "E0": "#7b3294",
    "E1": "#1f4e9c",
    "E3/sample": "#1a7f37",
    "S0K1/R3": "#c77f00",
    "S0K2/R3": "#b03030",
    "S0K2/R2": "#4f6d7a",
}
SLOT_MARKER = {"on_grid": "^", "mid_grid": "o", "random": "s"}

FIGURES = (
    "corrected",
    "overview",
    "top1_by_family",
    "rotation",
    "intervals",
    "failures",
)


# --------------------------------------------------------------------------- the guard


def require_published_table() -> dict[str, Any]:
    """Refuse to draw anything that is not the frozen 24-query run.

    A figure is how a result gets believed, so the picture carries the same gate the table does:
    the run must say it published (a ``--limit-queries`` smoke never does), the cell arithmetic must
    be the one the design promised, and the design file on disk must still hash to the content the
    run says it scored. A partial run therefore cannot become a figure by forgetting a flag.
    """
    missing = [p.name for p in (DESIGN, CELLS, HITS, EXCLUSIONS, METRICS) if not p.exists()]
    if missing:
        raise SystemExit(
            f"checkpoint8 is missing {missing}; run "
            "`python scripts/run_shape_benchmark.py --stage cp8` first -- a figure drawn from a "
            "half-written table would be read as the real thing"
        )
    summary = read_json(METRICS)
    if not summary["provenance"]["smoke_run"]["published"]:
        raise SystemExit(
            f"{OUT.name}/independent_metrics.json is a smoke run "
            f"({summary['provenance']['smoke_run']['limited_to_queries']} queries). Its numbers "
            "describe a partial sample, so no figure may be made from them."
        )
    design = read_json(DESIGN)
    content = iv.design_content_sha256(design)
    for label, value in (
        ("the design file itself", design["content_sha256"]),
        ("the run's provenance", summary["design"]["content_sha256"]),
    ):
        if value != content:
            raise SystemExit(
                f"{label} hashes to {value}, not to the {content} recomputed from "
                f"{DESIGN.name}: the sample moved under the figure"
            )
    rows = read_csv(CELLS)
    n_queries = len(design["queries"])
    per_arm = {
        arm: (
            sum(
                1
                for row in rows
                if row["arm"] == arm
                and row["condition"] == CONDITION
                and row["slot"] != iv.CP8_CONTROL_SLOT
            ),
            sum(
                1
                for row in rows
                if row["arm"] == arm
                and row["condition"] == CONDITION
                and row["slot"] == iv.CP8_CONTROL_SLOT
            ),
        )
        for arm in design["arms"]["labels"]
    }
    if set(per_arm.values()) != {(n_queries * 5, n_queries)}:
        raise SystemExit(
            f"the stored table gives {per_arm} rotated/control cells per arm at {CONDITION}, not "
            f"{(n_queries * 5, n_queries)}: the figure would be drawn over a sample that is not "
            "the frozen one"
        )
    return summary


def arms_of(summary: dict[str, Any]) -> list[str]:
    """Every arm the run scored, in the order the design froze. A figure drops none of them."""
    return [str(entry["label"]) for entry in summary["arms"]]


def headline(summary: dict[str, Any], arm: str, view: str, metric: str) -> float | None:
    block = summary["retrieval_by_arm"][arm][view][CONDITION]
    value = block[metric]
    return None if value is None else float(value)


def cells_of(summary: dict[str, Any]) -> list[dict[str, str]]:
    rows = read_csv(CELLS)
    if len(rows) != summary["rows_written"]["cells"]:
        raise SystemExit(
            f"{CELLS.name} has {len(rows)} rows but the metrics file recorded "
            f"{summary['rows_written']['cells']}: one of the two was rewritten"
        )
    return rows


def pose_label(angle: float) -> str:
    """The pose string the runner wrote for this angle, rebuilt the same way it was written.

    ``cp8_top10_hits.csv`` stores the angle inside a label formatted with ``%g``, so comparing a
    parsed float back to the cell table would be exact only by luck: the on-grid and mid-grid
    angles are clean decimals, while an off-grid draw is a full-precision float and would round to
    six significant digits. Matching the label instead of the number has no tolerance to argue
    about, and a wrong-angle panel would silently draw another pose's Top-K.
    """
    return "unrotated" if angle == 0.0 else f"rotate_{angle:g}"


def rotated_cells(rows: list[dict[str, str]], arm: str | None = None) -> list[dict[str, str]]:
    """The cells that answer a rotation question. A control cell is never in this list."""
    return [
        row
        for row in rows
        if row["condition"] == CONDITION
        and row["slot"] != iv.CP8_CONTROL_SLOT
        and (arm is None or row["arm"] == arm)
    ]


# --------------------------------------------------------------------------- 1. the correction


def pose_hits(
    rows: list[dict[str, str]], arm: str, query_id: str, pose: str, *, top: int
) -> list[dict[str, str]]:
    """One pose's stored Top-K, with a repeated angle collapsed only if it is the same search.

    The pose label is not unique per cell: the frozen slot sampler draws a query's five angles
    independently, so ``lizzard-3`` received ``mid_grid`` 127.5 deg twice and every arm has two
    cells -- and two Top-10 blocks -- under one label. Those blocks are the same measurement, and
    a figure that drew twenty panels or averaged two ranks would be inventing a second pose. So:
    ranks must be exactly 1..top, and where a rank was stored more than once its candidate id and
    distance must agree, or the figure stops.
    """
    block = [
        row
        for row in rows
        if row["arm"] == arm
        and row["query_id"] == query_id
        and row["condition"] == CONDITION
        and row["pose"] == pose
    ]
    by_rank: dict[int, list[dict[str, str]]] = {}
    for row in block:
        by_rank.setdefault(int(row["rank"]), []).append(row)
    if sorted(by_rank) != list(range(1, top + 1)):
        raise SystemExit(
            f"{arm}/{query_id} at {pose}: stored ranks are {sorted(by_rank)}, not 1..{top}"
        )
    out: list[dict[str, str]] = []
    for rank in range(1, top + 1):
        group = by_rank[rank]
        if any(
            (row["shape_id"], row["distance"]) != (group[0]["shape_id"], group[0]["distance"])
            for row in group
        ):
            raise SystemExit(
                f"{arm}/{query_id} at {pose}: rank {rank} was stored as two different searches"
            )
        out.append(group[0])
    return out


def plot_corrected_metrics() -> Path:
    """Goal 1, on its own: the same run, the same cells, three different denominators.

    ``d6_rotation_metrics.json`` averaged every cell whose arm ran, including the 12 rows at 0
    degrees. The corrected file splits them: ``rotation_only`` is 12 queries x 5 turned angles = 60
    cells, ``unrotated_control`` is the 12 rows that never turned. The third bar is the point of
    the figure -- where a control-only number and a rotation number differ, the pooled headline was
    reporting the mixture.
    """
    stored = read_json(D6_STORED)
    corrected = read_json(D6_CORRECTED)
    arms = [str(arm) for arm in corrected["retrieval_by_arm"]]
    if set(arms) != set(stored["retrieval_by_arm"]):
        raise SystemExit("the corrected file names arms the stored file never ran")
    pooled = {arm: stored["retrieval_by_arm"][arm][CONDITION] for arm in arms}
    rotation = {
        arm: corrected["retrieval_by_arm"][arm]["rotation_only"][CONDITION] for arm in arms
    }
    control = {
        arm: corrected["retrieval_by_arm"][arm]["unrotated_control"][CONDITION] for arm in arms
    }
    # The licence to put the three bars side by side: nothing was re-run between the two files.
    for arm in arms:
        if pooled[arm]["n_cells"] != 72 or rotation[arm]["n_cells"] != 60:
            raise SystemExit(
                f"{arm}: stored {pooled[arm]['n_cells']} cells, corrected "
                f"{rotation[arm]['n_cells']} -- the two files are not the same run split two ways"
            )
        if control[arm]["n_cells"] != 12:
            raise SystemExit(f"{arm}: {control[arm]['n_cells']} control cells, expected 12")

    panels = [
        ("top1_same_class_accuracy", "top1_same_class_accuracy", "Top-1 same class"),
        ("top1_stability_vs_unrotated", "top1_stability_vs_unrotated", "Top-1 stable vs unrotated"),
        (
            "class_recall_at_k",
            "precision_at_10",
            "same-class share of the Top-10\n(= Precision@10)",
        ),
        ("ndcg_at_10", "ndcg_at_10", "NDCG@10"),
        ("class_mrr", "class_mrr", "class MRR"),
    ]
    figure, axes = plt.subplots(2, 3, figsize=(16.2, 8.4))
    positions = np.arange(len(arms))
    width = 0.27
    for ax, (before_key, after_key, title) in zip(
        axes.ravel()[: len(panels)], panels, strict=True
    ):
        before = [float(pooled[arm][before_key]) for arm in arms]
        after = [float(rotation[arm][after_key]) for arm in arms]
        only = [float(control[arm][after_key]) for arm in arms]
        ax.bar(positions - width, before, width, color="#9aa0a6", label="stored: 72 cells pooled")
        ax.bar(positions, after, width, color="#1f4e9c", label="corrected: 60 rotated cells")
        ax.bar(
            positions + width,
            only,
            width,
            color="#f0c000",
            label="the 12 control cells alone",
        )
        for x, (left, right) in enumerate(zip(before, after, strict=True)):
            if abs(left - right) >= float(corrected["material_change_threshold"]):
                ax.annotate(
                    f"{right - left:+.2f}",
                    (x, max(left, right) + 0.02),
                    ha="center",
                    fontsize=5.6,
                    color="#b03030",
                )
        ax.set_xticks(positions)
        ax.set_xticklabels(arms, rotation=90, fontsize=6)
        ax.set_title(title, fontsize=8.5, pad=4)
        ax.set_ylim(0, 1.14)
        ax.grid(axis="y", color="#dddddd", linewidth=0.5)
        ax.tick_params(labelsize=6.5)

    # Recall@10 has no "before": the legacy field was the precision, and a real recall over each
    # query's own retained relevant count is new in CP8-A.
    ax = axes.flat[5]
    recall = [rotation[arm]["recall_at_10"] for arm in arms]
    undefined = [rotation[arm]["recall_at_10_undefined_cells"] for arm in arms]
    ax.bar(positions, recall, 0.55, color="#1a7f37")
    for x, (value, gaps) in enumerate(zip(recall, undefined, strict=True)):
        ax.annotate(
            f"{value:.2f}" if value is not None else "n/a",
            (x, (value or 0.0) + 0.02),
            ha="center",
            fontsize=5.8,
        )
        if gaps:
            ax.annotate(f"{gaps} undef", (x, 0.03), ha="center", fontsize=4.8, color="#b03030")
    ax.set_xticks(positions)
    ax.set_xticklabels(arms, rotation=90, fontsize=6)
    ax.set_title(
        "Recall@10 (hits / that query's own retained same-class count)\nnever existed before the "
        "split -- class_recall_at_k was the precision",
        fontsize=8,
        pad=4,
    )
    ax.set_ylim(0, 1.14)
    ax.grid(axis="y", color="#dddddd", linewidth=0.5)
    ax.tick_params(labelsize=6.5)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="upper center", ncol=3, fontsize=7.5, frameon=False)
    source = corrected["audit"]
    views = corrected["views"]
    n_rot = views["n_rotated_cells_per_arm_condition"][arms[0]][CONDITION]
    n_ctl = views["n_control_cells_per_arm_condition"][arms[0]][CONDITION]
    figure.text(
        0.5,
        0.955,
        "P0-4.6 CP8-A  what the 0-degree control cells did to the D6 headline (condition T2)",
        ha="center",
        fontsize=12,
        weight="bold",
    )
    figure.text(
        0.5,
        0.008,
        caption(
            f"Both files score the SAME stored cells: {_repo_name(D6_STORED)} pooled 72 rows per "
            f"arm-condition into its headline, and {_repo_name(D6_CORRECTED)} splits them by the "
            f"stored ``slot`` column into {n_rot} rotated cells and {n_ctl} controls. No retrieval "
            f"was re-run and no historical row was rewritten (audit.reason: {source['reason']}). "
            f"Red labels mark |change| >= {corrected['material_change_threshold']}. In the "
            "stability panel the yellow column is 1.00 for every arm by construction -- an "
            "unrotated query always reproduces its own Top-1 -- which is exactly why pooling those "
            "12 rows into a 72-cell mean made a pose-blind arm look rotation-stable. Recall@10 is "
            "undefined where the T2 filter left no same-class candidate to find, and those cells "
            "are counted in recall_at_10_undefined_cells rather than scored as a 0.0 failure."
        ),
        ha="center",
        fontsize=6.2,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.055, 1.0, 0.94))
    path = OUT / "corrected_metrics_comparison.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


def _repo_name(path: Path) -> str:
    return path.relative_to(REPO).as_posix().split("experiments/shape_perception_benchmark/")[-1]


def write_corrected_metrics() -> Path:
    """``corrected_metrics.json`` as a view, not a second source of truth.

    The authority for the corrected D6 numbers stays ``results/d6_rotation_metrics_corrected.json``
    -- the task asked for the corrected headline inside checkpoint8, and the way to have both
    without two files that can disagree is to copy the values and bind the copy to the original's
    hash.
    """
    import hashlib
    import json

    source = read_json(D6_CORRECTED)
    payload = {
        "schema": "earthglyph.shape_perception_benchmark/corrected_metrics_view/1",
        "role": (
            "a checkpoint8 view of the CP8-A correction. The authoritative record is the file "
            "hashed below; this one adds the naming audit and the checkpoint-level reading."
        ),
        "authority": {
            "path": _repo_name(D6_CORRECTED),
            "bytes": D6_CORRECTED.stat().st_size,
            "sha256": hashlib.sha256(D6_CORRECTED.read_bytes()).hexdigest(),
            "supersedes_only": source["supersedes_only"],
            "history_untouched": source["audit"]["history_untouched"],
        },
        "reason": source["audit"]["reason"],
        "recompute_command": source["audit"]["command"],
        "views": source["views"],
        "metric_definitions": source["metric_definitions"],
        "material_change_threshold": source["material_change_threshold"],
        "n_material_changes": len(source["material_changes"]),
        "material_changes": source["material_changes"],
        "arm_rankings_before_vs_after": source["arm_rankings_before_vs_after"],
        "independent_check_from_raw_ranks": source["independent_check_from_raw_ranks"],
        "corrected_headline_T2_rotation_only": {
            arm: {
                key: block[key]
                for key in (
                    "top1_accuracy",
                    "top1_same_class_accuracy",
                    "top1_stability_vs_unrotated",
                    "precision_at_10",
                    "recall_at_10",
                    "recall_at_10_n_scored",
                    "recall_at_10_undefined_cells",
                    "ndcg_at_10",
                    "class_mrr",
                    "top_10_overlap_vs_unrotated",
                    "mean_abs_rank_shift",
                    "target_rank_mean",
                    "seconds_per_query_mean",
                    "n_cells",
                )
            }
            for arm, block in (
                (arm, source["retrieval_by_arm"][arm]["rotation_only"]["T2"])
                for arm in source["retrieval_by_arm"]
            )
        },
        "naming_audit_B4": {
            "legacy_field": "class_recall_at_k",
            "what_it_measured": (
                "same-class share of the returned Top-K, i.e. Precision@K -- the numerator was "
                "right, the name was a recall word on a precision formula"
            ),
            "renamed_to": ["precision_at_10", "recall_at_10"],
            "numbers_recomputed": False,
            "consequence": (
                "no method got faster, more accurate or more stable because of the rename; a "
                "reader who took class_recall_at_k for recall was reading a harder question than "
                "the one that had been answered"
            ),
            "legacy_value_still_means": "precision_at_10, unchanged, over the same cells",
        },
    }
    path = OUT / "corrected_metrics.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 2. the overview


def _overview_row(
    axes: Any,
    position: int,
    arm: str,
    rows: list[dict[str, str]],
    query: BenchmarkShape,
    angle: float,
    index: dict[str, BenchmarkShape],
    limits: float,
    *,
    top: int,
) -> None:
    """One arm's row: the query as published, the query as searched, then its stored Top-K."""
    ordered = sorted(rows, key=lambda row: int(row["rank"]))
    # The assertion that lets the row be drawn at all: the panels come out in stored rank order and
    # the stored distances are non-decreasing in that order.
    if [int(row["rank"]) for row in ordered] != list(range(1, len(ordered) + 1)):
        raise SystemExit(f"{arm}: stored ranks are not 1..k")
    distances = numeric(ordered, "distance")
    if np.any(np.diff(distances) < -1e-12):
        raise SystemExit(f"{arm}: stored Top-K is not sorted by distance")
    left = axes[position][0]
    draw(left, points_of(query), colour=ROLE_COLOUR["query"], limit=limits, fill=True)
    left.set_title("query, published pose", fontsize=6.5, pad=2)
    turned = axes[position][1]
    draw(
        turned,
        rotate(points_of(query), angle),
        colour=ROLE_COLOUR["query"],
        limit=limits,
        fill=True,
    )
    turned.set_title(f"query, +{angle:g} deg (what was searched)", fontsize=6.5, pad=2)
    for column in range(top):
        ax = axes[position][column + 2]
        row = ordered[column]
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
    for column in range(top + 2):
        # A row is ~2.1 inches tall, so an unwrapped arm name like
        # "EarthGlyph 91-D (ours, not ShapeComp) rotation search" runs past its own row and reads
        # as one long smear against the neighbour. Wrapped, each row labels only itself.
        axes[position][column].set_ylabel(
            textwrap.fill(arm_title(arm), 20) if column == 0 else "", fontsize=6.8
        )
        axes[position][column].set_xticks([])
        axes[position][column].set_yticks([])


def _query_angle_slot(
    rows: list[dict[str, str]], query_id: str, slot: str
) -> tuple[float, list[dict[str, str]]]:
    """The one angle every arm saw for (query, slot) -- or the figure is not a comparison."""
    cells = [
        row
        for row in rows
        if row["query_id"] == query_id
        and row["slot"] == slot
        and row["condition"] == CONDITION
    ]
    angles = {float(row["angle_deg"]) for row in cells}
    if len(angles) != 1:
        raise SystemExit(
            f"{query_id}/{slot}: the arms were searched at {sorted(angles)}, not one shared angle"
        )
    return angles.pop(), cells


def plot_independent_overview(index: dict[str, BenchmarkShape]) -> Path:
    """The promised side-by-side: one frozen query, one frozen angle, every arm's real Top-5.

    The query is ``design.queries[0]`` -- the first shape the frozen seed drew, in the family order
    fixed in code before any retrieval ran -- and the angle is its ``on_grid`` slot from the same
    frozen draw. Neither pick looked at a result, which is the only reason the row-to-row difference
    is a difference between methods.
    """
    design = read_json(DESIGN)
    summary = require_published_table()
    rows = read_csv(HITS)
    cells = read_csv(CELLS)
    query_id = str(design["queries"][0]["shape_id"])
    query_class = str(design["queries"][0]["class"])
    angle, _ = _query_angle_slot(cells, query_id, "on_grid")
    arms = arms_of(summary)
    hits: dict[str, list[dict[str, str]]] = {}
    for arm in arms:
        block = pose_hits(rows, arm, query_id, pose_label(angle), top=iv.CP8_TOP_K)
        # T2 deleted this query's own shape and its near-duplicates, so the query can never appear
        # in its own result list. A hit row that is the query would be a leak, not a match.
        if any(row["shape_id"] == query_id for row in block):
            raise SystemExit(f"{arm}: the query came back in its own T2 Top-K")
        hits[arm] = block

    columns = 2 + TOP_IN_OVERVIEW
    figure, axes = plt.subplots(
        len(arms), columns, figsize=(2.15 * columns, 2.1 * len(arms)), squeeze=False
    )
    limits = figure_limit(
        [points_of(index[query_id])]
        + [points_of(index[row["shape_id"]]) for arm in arms for row in hits[arm]]
    )
    for position, arm in enumerate(arms):
        _overview_row(
            axes, position, arm, hits[arm], index[query_id], angle, index, limits,
            top=TOP_IN_OVERVIEW,
        )
    figure.suptitle(
        f"P0-4.6 CP8-C  independent query {short(query_id)} ({query_class}), "
        f"turned +{angle:g} deg -- gallery: the whole MPEG-7 archive, T2",
        fontsize=12,
        weight="bold",
    )
    figure.text(
        0.5,
        0.006,
        caption(
            f"Query and angle come from the frozen design ({_repo_name(DESIGN)}, content_sha256 "
            f"{design['content_sha256'][:16]}…): the first shape the seed drew, at its on_grid "
            f"slot, so the row-to-row difference is the method and nothing else. Every panel is a "
            f"real candidate from {_repo_name(HITS)} -- id, class and distance as stored, nothing "
            "invented, nothing re-ranked. Blue = same MPEG-7 class as the query, red = another "
            "class, and the query's own shape and its E0 near-duplicates were deleted from the "
            "gallery before any arm searched it (rule and ids in cp8_exclusions.csv, identical for "
            "all six arms). One shared centring / RMS scale for the whole figure; contours are "
            "drawn in their published pose, and the only rotation shown is the angle the design "
            "stored. Class labels are a relevance proxy, not a human similarity judgement."
        ),
        ha="center",
        fontsize=6.4,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.045, 1.0, 0.955))
    path = OUT / "independent_retrieval_overview.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


def plot_top1_by_family(index: dict[str, BenchmarkShape]) -> Path:
    """The overview widened by rule: the first frozen query of each family, Top-1 only.

    Six families x six arms at one angle each, so a single row of the picture cannot stand for the
    sample. The query per family is the first entry the design lists for that family -- again a
    position in a frozen table, never a result.
    """
    design = read_json(DESIGN)
    summary = require_published_table()
    rows = read_csv(HITS)
    cells = read_csv(CELLS)
    arms = arms_of(summary)
    picks: list[tuple[str, str]] = []
    for family in dict.fromkeys(str(entry["family"]) for entry in design["queries"]):
        entry = next(e for e in design["queries"] if str(e["family"]) == family)
        picks.append((str(entry["shape_id"]), family))
    columns = 1 + len(arms)
    figure, axes = plt.subplots(
        len(picks), columns, figsize=(2.15 * columns, 2.25 * len(picks)), squeeze=False
    )
    for position, (query_id, family) in enumerate(picks):
        angle, _ = _query_angle_slot(cells, query_id, "on_grid")
        pose = pose_label(angle)
        panels = [points_of(index[query_id])]
        firsts: dict[str, dict[str, str]] = {}
        for arm in arms:
            firsts[arm] = pose_hits(rows, arm, query_id, pose, top=iv.CP8_TOP_K)[0]
            panels.append(points_of(index[firsts[arm]["shape_id"]]))
        limits = figure_limit(panels)
        ax = axes[position][0]
        draw(
            ax,
            rotate(points_of(index[query_id]), angle),
            colour=ROLE_COLOUR["query"],
            limit=limits,
            fill=True,
        )
        ax.set_title(f"{short(query_id)} +{angle:g} deg", fontsize=6.8, pad=2)
        ax.set_xlabel(
            f"query ({index[query_id].category})", fontsize=5.4, color="#555555", labelpad=1
        )
        for column, arm in enumerate(arms, start=1):
            hit = firsts[arm]
            shape = index[hit["shape_id"]]
            same = hit["is_same_class"] == "1"
            panel = axes[position][column]
            draw(
                panel,
                points_of(shape),
                colour=ROLE_COLOUR["hit" if same else "miss"],
                limit=limits,
                fill=same,
            )
            panel.set_title(
                f"{arm_title(arm)}\n{short(hit['shape_id'])}", fontsize=6.0, pad=2,
                color=ROLE_COLOUR["hit" if same else "miss"],
            )
            panel.set_xlabel(
                f"MPEG-7/{short(shape.category)}  d={float(hit['distance']):.3f}\n"
                f"{'same class' if same else 'OTHER class'}",
                fontsize=5.2,
                color="#555555",
                labelpad=1,
            )
        for column in range(columns):
            axes[position][column].set_ylabel(family if column == 0 else "", fontsize=7.2)
    figure.suptitle(
        "P0-4.6 CP8-C  the first frozen query of each family, turned to its on_grid angle: Top-1",
        fontsize=12,
        weight="bold",
    )
    figure.text(
        0.5,
        0.006,
        caption(
            "One row per shape family, one column per arm, every cell a real stored rank-1 result "
            "from cp8_top10_hits.csv. The query in each row is the first one the frozen design "
            "lists for that family, so no row was chosen because it flatters or embarrasses a "
            "method. This is the overview figure widened to the sample; it is still not the "
            "headline -- the headline is the 24-query mean with its interval in "
            "arm_comparison_with_intervals.png, and one class label is not a human judgement."
        ),
        ha="center",
        fontsize=6.4,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.035, 1.0, 0.955))
    path = OUT / "independent_top1_by_family.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


# --------------------------------------------------------------------------- 3. angle vs quality


#: (the per-cell column, the headline it averages into, the panel title, the better direction).
#: The cell records ``top1_matches_unrotated`` and the headline calls the same quantity
#: ``top1_stability_vs_unrotated``; drawing a dashed mean onto the wrong key would silently plot
#: nothing, so the pair is stated here rather than guessed from one name.
ROTATION_PANELS = (
    (
        "top1_matches_unrotated",
        "top1_stability_vs_unrotated",
        "Top-1 still the unrotated Top-1",
        True,
    ),
    (
        "top_10_overlap_vs_unrotated",
        "top_10_overlap_vs_unrotated",
        "Top-10 overlap vs unrotated",
        True,
    ),
    ("precision_at_10", "precision_at_10", "Precision@10 (same class / returned list)", True),
    ("mean_abs_rank_shift", "mean_abs_rank_shift", "mean |rank shift| vs unrotated", False),
)


def plot_rotation_vs_quality(summary: dict[str, Any]) -> Path:
    """Every rotated cell against the angle that produced it, per arm.

    Each query was turned five times by the frozen draw, so an angle carries no more than five
    cells and no arm's points are a function of angle alone. The per-arm dashed line is the stored
    rotation-only mean -- the number the headline quotes -- and the control cells (0 degrees) are
    deliberately absent: they are a wiring check, and putting them at x=0 would let the reader
    average them in by eye, which is the CP8-A defect drawn as a picture.
    """
    rows = rotated_cells(cells_of(summary))
    arms = arms_of(summary)
    figure, axes = plt.subplots(2, 2, figsize=(12.4, 8.2))
    for ax, (field, block_field, title, higher) in zip(axes.flat, ROTATION_PANELS, strict=True):
        for arm in arms:
            block = [row for row in rows if row["arm"] == arm]
            x = numeric(block, "angle_deg")
            y = numeric(block, field)
            ax.scatter(x, y, s=14, alpha=0.55, color=ARM_COLOUR[arm], marker="o", linewidths=0)
            mean = headline(summary, arm, "rotation_only", block_field)
            if mean is not None:
                ax.axhline(mean, color=ARM_COLOUR[arm], linestyle="--", linewidth=1.0, alpha=0.9)
        reading = "higher is better" if higher else "lower is better"
        ax.set_title(f"{title}   ({reading})", fontsize=9)
        ax.set_xlabel("stored query rotation angle, degrees", fontsize=7.5)
        ax.set_ylim(min(0.0, ax.get_ylim()[0]), max(1.05, ax.get_ylim()[1]))
        ax.set_xticks(np.arange(0, 361, 45))
        ax.grid(color="#e8e8e8", linewidth=0.5)
        ax.tick_params(labelsize=7)
    handles = [
        plt.Line2D([], [], marker="o", linestyle="", color=colour, markersize=5)
        for colour in (ARM_COLOUR[arm] for arm in arms)
    ]
    figure.legend(handles, arms, loc="upper center", ncol=len(arms), fontsize=7.5, frameon=False)
    figure.suptitle(
        f"P0-4.6 CP8-C  rotation vs quality on the independent sample "
        f"({summary['design']['n_queries']} queries, {CONDITION})",
        fontsize=12.5,
        weight="bold",
    )
    figure.text(
        0.5,
        0.008,
        caption(
            f"Dots are {len(rows)} stored rotated cells from {_repo_name(CELLS)} (one per arm x "
            f"query x angle); the dashed line is that arm's rotation-only mean as the metrics file "
            "records it. No cell at 0 degrees is plotted. A pose-blind arm can hold a perfectly "
            "flat overlap line while its accuracy sits near zero -- which is why the four panels "
            "are read as a set and not as one winner, and why the control view never counts as "
            "stability evidence. Angles come "
            "from the frozen draw: on-grid multiples of 15 deg, mid-grid multiples of 15 plus 7.5, "
            "and off-grid randoms, mixed identically for every arm."
        ),
        ha="center",
        fontsize=6.4,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.055, 1.0, 0.945))
    path = OUT / "rotation_vs_quality.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


# ------------------------------------------------------------------------ 4. the honest ranking


INTERVAL_PANELS = (
    ("top1_same_class_accuracy", "category retrieval: Top-1 same class", True),
    ("top1_stability_vs_unrotated", "rotation stability: Top-1 unchanged", True),
    ("precision_at_10", "list quality: Precision@10", True),
    ("seconds_per_query_mean", "engineering cost: seconds per search", False),
)


def plot_intervals(summary: dict[str, Any]) -> Path:
    """Point estimate, 24-cluster interval, and the control value that is not rotation evidence.

    The interval resamples whole queries with all five of their cells, so it is wide: 24 clusters is
    the promised sample, not a large one. Overlapping intervals are a result, not a defect -- and
    the hollow marker is the same arm's unrotated control, which sits at 1.0 for stability by
    construction and therefore cannot be quoted as evidence that a wrapper survives a turn.
    """
    arms = arms_of(summary)
    n_queries = int(summary["design"]["n_queries"])
    n_boot = int(
        summary["retrieval_by_arm"][arms[0]]["rotation_only"][CONDITION]["query_clustered_ci"][
            INTERVAL_PANELS[0][0]
        ]["n_boot"]
    )
    figure, axes = plt.subplots(2, 2, figsize=(12.8, 8.4))
    positions = np.arange(len(arms))
    for ax, (field, title, higher) in zip(axes.flat, INTERVAL_PANELS, strict=True):
        log_axis = field == "seconds_per_query_mean"
        for position, arm in enumerate(arms):
            block = summary["retrieval_by_arm"][arm]["rotation_only"][CONDITION]
            interval = block["query_clustered_ci"][field]
            value = block[field]
            if value is None:
                continue
            colour = ARM_COLOUR[arm]
            ax.errorbar(
                position,
                float(value),
                yerr=[
                    [max(0.0, float(value) - float(interval["ci_low"]))],
                    [max(0.0, float(interval["ci_high"]) - float(value))],
                ],
                fmt="o",
                color=colour,
                ecolor=colour,
                elinewidth=1.6,
                capsize=4,
                markersize=6,
            )
            control = headline(summary, arm, "unrotated_control", field)
            if control is not None:
                ax.plot(
                    position, float(control), marker="o", mfc="none", mec="#999999", ms=8, mew=1.2
                )
        ax.set_yscale("log") if log_axis else ax.set_ylim(0.0, 1.12)
        ax.set_xticks(positions)
        ax.set_xticklabels(arms, rotation=20, ha="right", fontsize=7)
        reading = "higher is better" if higher else "lower is better"
        ax.set_title(f"{title}   ({reading})", fontsize=9)
        ax.grid(axis="y", color="#e8e8e8", linewidth=0.5)
        ax.tick_params(labelsize=7)
    figure.suptitle(
        f"P0-4.6 CP8-C  six frozen arms on {summary['design']['n_queries']} unseen queries "
        f"({CONDITION}, rotation-only view, {len(arms) * n_queries * 5} cells)",
        fontsize=12.5,
        weight="bold",
    )
    figure.text(
        0.5,
        0.008,
        caption(
            f"Filled markers are the stored rotation-only means over {n_queries} queries x 5 "
            f"angles per arm; whiskers are the {n_boot}-draw percentile bootstrap clustered by the "
            f"original query ({n_queries} clusters, so the intervals are wide and overlapping is a "
            "finding, not a defect). Hollow grey markers are the same arm's unrotated control view "
            "-- a wiring check at 0 degrees, never a rotation measurement. No single column "
            "decides a winner: category retrieval, rotation stability and engineering cost are "
            "three questions, and none of them is human perception, which CP8 measured no new "
            "ratings for."
        ),
        ha="center",
        fontsize=6.4,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.055, 1.0, 0.945))
    path = OUT / "arm_comparison_with_intervals.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


# --------------------------------------------------------------------------- 5. the misses


def worst_cell(rows: list[dict[str, str]], arm: str) -> dict[str, str]:
    """The arm's own worst stored miss, by a rule written before the numbers were looked at.

    Lowest Precision@10 first; ties broken by the largest full-ranking shift, then by query id and
    angle so the choice is reproducible. It is the same rule for every arm, which is what keeps a
    figure that "shows the failures" from becoming a figure that shows one method's failures.
    """
    block = rotated_cells(rows, arm)
    if not block:
        raise SystemExit(f"{arm}: no stored rotated cells")

    def key(row: dict[str, str]) -> tuple[float, float, str, float]:
        precision = float(row["precision_at_10"]) if row["precision_at_10"] else 0.0
        shift = -float(row["mean_abs_rank_shift"])
        return (precision, shift, row["query_id"], float(row["angle_deg"]))

    return min(block, key=key)


def cause_label(cell: Mapping[str, str]) -> str:
    """A cause the stored columns actually support, or the words that say we do not know one."""
    if int(cell["n_same_class_kept"]) == 0:
        return "cause the table supports: the T2 filter left no same-class candidate for this query"
    if int(cell["same_class_hits_at_10"]) == 0:
        return "cause the table supports: no same-class shape reached the Top-10 at all"
    if int(cell["top1_is_same_class"]) == 0:
        return "cause the table supports: a wrong class ranked first, though the class did reach"
    return "cause undetermined: the head of the list is on-class and the stored columns say no more"


def failure_note(arm: str, cell: Mapping[str, str], query: BenchmarkShape) -> str:
    """The row's numbers, all of them read out of the stored cell, plus the evidence-bound cause."""
    recall = cell["recall_at_10"]
    return (
        f"{arm_title(arm)}\n"
        f"worst {CONDITION} miss: {short(cell['query_id'])} (class {query.category}) "
        f"at +{float(cell['angle_deg']):g} deg\n"
        f"Precision@10 = {float(cell['precision_at_10']):.1f}  "
        f"Recall@10 = {recall if recall else 'undefined'}\n"
        f"same-class hits = {cell['same_class_hits_at_10']} of "
        f"{cell['n_same_class_kept']} retained candidates\n"
        "Top-10 overlap with its own unrotated run = "
        f"{float(cell['top_10_overlap_vs_unrotated']):.2f}\n"
        f"mean |rank shift| = {float(cell['mean_abs_rank_shift']):.0f} of "
        f"{int(cell['n_kept'])} candidates\n"
        + cause_label(cell)
    )


def plot_failures(index: dict[str, BenchmarkShape], summary: dict[str, Any]) -> Path:
    """Each arm's worst miss, drawn the same way the success figures are drawn.

    The right-hand column is the arm's own stored evidence, not a caption written after seeing the
    picture: every number in it is a column of ``cp8_retrieval_results.csv`` for that one cell.
    """
    rows = read_csv(CELLS)
    hits = read_csv(HITS)
    arms = arms_of(summary)
    worst = {arm: worst_cell(rows, arm) for arm in arms}
    columns = 3 + TOP_IN_OVERVIEW
    figure, axes = plt.subplots(
        len(arms), columns, figsize=(2.15 * (columns - 1) + 3.1, 2.2 * len(arms)), squeeze=False
    )
    blocks: dict[str, list[dict[str, str]]] = {}
    for arm in arms:
        cell = worst[arm]
        blocks[arm] = pose_hits(
            hits, arm, cell["query_id"], pose_label(float(cell["angle_deg"])),
            top=iv.CP8_TOP_K,
        )
    panels = [points_of(index[worst[arm]["query_id"]]) for arm in arms]
    panels.extend(
        points_of(index[row["shape_id"]]) for arm in arms for row in blocks[arm]
    )
    limits = figure_limit(panels)
    for position, arm in enumerate(arms):
        cell = worst[arm]
        _overview_row(
            axes, position, arm, blocks[arm], index[cell["query_id"]], float(cell["angle_deg"]),
            index, limits, top=TOP_IN_OVERVIEW,
        )
        note = axes[position][columns - 1]
        note.axis("off")
        note.text(
            0.02,
            0.95,
            failure_note(arm, cell, index[cell["query_id"]]),
            fontsize=5.9,
            color="#333333",
            va="top",
            ha="left",
            linespacing=1.5,
        )
    figure.suptitle(
        "P0-4.6 CP8-C  failure cases: each arm's worst stored miss, one rule, one scale",
        fontsize=12,
        weight="bold",
    )
    figure.text(
        0.5,
        0.006,
        caption(
            f"Selected from all {len(rotated_cells(rows))} stored rotated cells by one rule "
            "applied identically to the six arms (lowest Precision@10, then largest full-ranking "
            "shift, "
            "then query id and angle) -- so the figure shows where each method fails, not where "
            "one of them does. Every panel is a real stored Top-K row with its distance. The "
            "deleted target is not drawn, because T2 removes it by design; its stored distance to "
            "the query and the best wrong-class distance stay in cp8_retrieval_results.csv, so any "
            "claim about why a miss happened needs those columns. Where they do not support a "
            "cause, the row says the cause is undetermined rather than guessing one."
        ),
        ha="center",
        fontsize=6.4,
        color="#555555",
    )
    figure.tight_layout(rect=(0.0, 0.045, 1.0, 0.95))
    path = OUT / "independent_failure_cases.png"
    figure.savefig(path, dpi=DPI)
    plt.close(figure)
    return path


# --------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--figure", default="all", choices=("all", *FIGURES))
    args = parser.parse_args()
    wanted = FIGURES if args.figure == "all" else (args.figure,)
    if args.figure == "corrected":
        # The correction figure reads the D6 tables only, so it can be drawn without the CP8 run.
        print(f"  corrected -> {plot_corrected_metrics()}")
        print(f"  corrected -> {write_corrected_metrics()}")
        return
    summary = require_published_table()
    index = shape_index() if {"overview", "top1_by_family", "failures"} & set(wanted) else {}
    for figure in wanted:
        if figure == "corrected":
            path = plot_corrected_metrics()
            written = write_corrected_metrics()
            print(f"  {figure} -> {path}")
            print(f"  {figure} -> {written}")
            continue
        if figure == "overview":
            path = plot_independent_overview(index)
        elif figure == "top1_by_family":
            path = plot_top1_by_family(index)
        elif figure == "rotation":
            path = plot_rotation_vs_quality(summary)
        elif figure == "intervals":
            path = plot_intervals(summary)
        else:
            path = plot_failures(index, summary)
        print(f"  {figure} -> {path}")


if __name__ == "__main__":
    main()
