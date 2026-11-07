#!/usr/bin/env python
"""Figures for the P0-4.5 perceptual experiment.

Two kinds of picture, and they must not be confused:

* the **controlled gallery** figures show shapes that are not geography. Each one
  is labelled ``Controlled Shape Retrieval — 非真实地理景观``, because a star
  matched to a star proves an algorithm, not a product.
* the **real landform** figures (setting T3) mix the same queries into the 839
  candidate Overture index and are labelled
  ``Real Landform Retrieval — Overture Maps``.

Everything drawn here comes from files on disk: contours are the stored 128-point
rings, ids are the stored ids, and an overlay only uses a transform the matcher
itself computed. Nothing is beautified and no candidate is hand-picked.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate_perceptual_methods import INDEX_LABEL, load_landforms  # noqa: E402
from probe_shared import draw_contour, short_id  # noqa: E402
from visualize_retrieval import (  # noqa: E402
    CANDIDATE_EDGE,
    CANDIDATE_FACE,
    QUERY_LINE,
    _figure_backend,
)

GALLERY_LABEL = "Controlled Shape Retrieval — 非真实地理景观"
REAL_LABEL = "Real Landform Retrieval — Overture Maps"

ROLE_COLOURS = {
    "query": (QUERY_LINE, "#f6e2e0"),
    "variant": (CANDIDATE_EDGE, CANDIDATE_FACE),
    "hard_negative": ("#8a5a00", "#f7e3bd"),
}
METHOD_COLUMNS = ("E0", "E1", "E2", "E3")
ARM_BLURB_KEYS = METHOD_COLUMNS
ROLE_LETTER = {"query": "Q", "variant": "V", "hard_negative": "N"}


def configure_cjk_font() -> None:
    """Let matplotlib fall back to a font that actually has the Chinese glyphs.

    The two labels the brief mandates both contain Chinese, and the default DejaVu
    Sans silently draws empty boxes for them. Order is "whatever this machine has
    first", so the figure is still produced on a box without YaHei.
    """
    from matplotlib import font_manager, rcParams

    wanted = ("Microsoft YaHei", "Noto Sans SC", "SimHei", "PingFang SC", "Yu Gothic")
    available = {font.name for font in font_manager.fontManager.ttflist}
    chains = [name for name in wanted if name in available] + ["DejaVu Sans"]
    rcParams["font.sans-serif"] = chains
    rcParams["axes.unicode_minus"] = False


def _panel(
    ax: Any,
    points: np.ndarray,
    *,
    role: str,
    caption: str,
    overlay: np.ndarray | None = None,
    overlay_colour: str = "#1b7f4b",
    filled: bool = True,
) -> None:
    edge, face = ROLE_COLOURS.get(role, (CANDIDATE_EDGE, CANDIDATE_FACE))
    if filled:
        draw_contour(ax, points, color=edge, face=face)
    else:
        ring = np.vstack([points, points[:1]])
        ax.plot(ring[:, 0], ring[:, 1], color=edge, lw=1.0)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        limit = float(np.abs(points).max()) * 1.15 + 1e-9
        ax.set_xlim(-limit, limit)
        ax.set_ylim(-limit, limit)
    if overlay is not None:
        ring = np.vstack([overlay, overlay[:1]])
        ax.plot(ring[:, 0], ring[:, 1], color=overlay_colour, lw=1.2, zorder=5)
    ax.set_title(caption, fontsize=5.0, color=edge, pad=1.5)


def control_gallery_figure(
    items: list[Any],
    out_path: Path,
    *,
    per_row: int = 14,
    dpi: int = 200,
) -> dict[str, Any]:
    """The whole test set at a glance, one band per structure family.

    This figure exists so a human can falsify the design before any algorithm is
    run on it: if a "hard negative" does not look hard, or a "variant" is obviously
    a different object, then no number produced from these labels means anything.
    Every stored item is drawn — nothing is sampled out for looking bad — in the
    order query, then variants, then hard negatives, so each band reads left to
    right in decreasing structural similarity to its own queries.

    ``sol`` is printed on every panel because the matched-envelope negatives are
    *defined* by having the same solidity as their query; a reader can check that
    claim with their eyes instead of taking the generator's word for it.
    """
    plt = _figure_backend()
    configure_cjk_font()
    families: dict[str, dict[str, list[Any]]] = {}
    for item in items:
        bucket = families.setdefault(
            item.family, {"query": [], "variant": [], "hard_negative": []}
        )
        bucket[item.role].append(item)

    bands: list[tuple[str, list[Any]]] = []
    for family in sorted(families):
        bucket = families[family]
        band = (
            sorted(bucket["query"], key=lambda item: item.item_id)
            + sorted(bucket["variant"], key=lambda item: item.item_id)
            + sorted(bucket["hard_negative"], key=lambda item: item.item_id)
        )
        bands.append((family, band))

    panel_rows = sum(-(-len(band) // per_row) for _, band in bands)
    rows = panel_rows + len(bands)
    fig, axes = plt.subplots(
        rows,
        per_row,
        figsize=(0.72 * per_row + 1.4, 0.66 * rows + 1.9),
        squeeze=False,
        dpi=dpi,
    )
    cursor = 0
    for family, band in bands:
        header_row = axes[cursor]
        for cell in header_row:
            cell.axis("off")
        counts = {"query": 0, "variant": 0, "hard_negative": 0}
        for item in band:
            counts[item.role] += 1
        header_row[0].text(
            0.0,
            0.6,
            f"{family}   ·   {counts['query']} query (red)   ·   "
            f"{counts['variant']} variant (blue)   ·   "
            f"{counts['hard_negative']} hard negative (amber)",
            fontsize=7.5,
            ha="left",
            va="center",
            color="#1b2735",
        )
        cursor += 1
        for offset, item in enumerate(band):
            ax = axes[cursor + offset // per_row][offset % per_row]
            _panel(
                ax,
                item.contour,
                role=item.role,
                caption=(
                    f"{ROLE_LETTER[item.role]} {short_id(item.item_id, 22)}\n"
                    f"{item.origin[:14]}  sol {item.metrics['solidity']:.2f}"
                ),
            )
        cursor += -(-len(band) // per_row)
        # the band's last row is usually partial; its empty cells must not keep
        # tick marks, or they read as shapes the gallery never contains
        for cell in axes[cursor - 1][len(band) % per_row or per_row:]:
            cell.axis("off")
    for cell in axes[cursor:]:
        for handle in cell:
            handle.axis("off")
    fig.suptitle(
        f"{GALLERY_LABEL} — controlled gallery, {len(items)} items in "
        f"{len(bands)} structure families (every stored shape, no sampling)",
        fontsize=11,
        y=0.988,
    )
    fig.text(
        0.006,
        0.011,
        "panels are the stored 128-point contours; ids, origins and solidity are the "
        "stored values. Q=role query, V=variant, N=hard negative. Roles were fixed from "
        "the design table before any retrieval method ran.",
        fontsize=6.5,
        color="#40506a",
    )
    # explicit spacing rather than tight_layout: the family headers are meant to
    # overflow their own cell, and tight_layout spends its time fighting that
    fig.subplots_adjust(
        left=0.012, right=0.994, top=0.958, bottom=0.028, wspace=0.07, hspace=0.46
    )
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "rows": rows,
        "columns": per_row,
        "items": len(items),
        "families": {family: len(band) for family, band in bands},
        "panel_rows": panel_rows,
    }


def _load(out_dir: Path) -> tuple[list[Any], dict[str, Any]]:
    """Read gallery.jsonl back as light objects (used by the standalone CLI)."""
    from types import SimpleNamespace

    records = [
        json.loads(line)
        for line in (out_dir / "gallery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [
        SimpleNamespace(
            item_id=record["item_id"],
            family=record["family"],
            role=record["role"],
            origin=record["origin"],
            contour=np.asarray(record["contour"], dtype=float),
            metrics=record["metrics"],
        )
        for record in records
    ]
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    return items, labels


ARM_BLURB = {
    "E0": "Fourier + closed-form rotation/shift alignment (the P0-4 pipeline)",
    "E1": "ShapeComp-inspired 91 descriptors, corpus-fitted scale, Euclidean",
    "E2": "multi-scale signed-turn events, monotone DP alignment",
    "E3": "Shape Context, log-polar histograms + chi-square assignment",
}
RELEVANT_EDGE = "#12623c"
RELEVANT_FACE = "#d8ecdd"


def load_results(path: Path) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """``(setting, arm, query_id) -> rows in rank order`` straight from results.csv.

    The figures read the CSV and nothing else, so what a reader sees is the same artefact
    the metrics were computed from; a figure that re-scored the shapes could silently
    disagree with the table printed next to it.
    """
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cells.setdefault((row["setting"], row["arm"], row["query_id"]), []).append(row)
    for rows in cells.values():
        rows.sort(key=lambda row: int(row["rank"]))
    return cells


def _number(text: str | None) -> float | None:
    return float(text) if text not in ("", None) else None


def _who(row: dict[str, Any]) -> str:
    """One short line saying what this candidate is, by the label it carries."""
    if row["provenance"] == INDEX_LABEL:
        return "landform (unlabelled)"
    letter = {"query": "Q", "variant": "V", "hard_negative": "N"}.get(row["candidate_role"], "?")
    return f"{letter} {row['candidate_family']}"


def _candidate_lines(row: dict[str, Any], arm: str) -> str:
    """Rank + id, what it is, what the arm paid — three lines, each cell-width short."""
    distance = _number(row["distance"])
    tail = "  REL" if row["relevant"] == "1" else ""
    third = f"d={distance:.4f}{tail}" if distance is not None else f"-{tail}"
    if arm == "E0":
        theta = _number(row["theta_deg"])
        if theta is not None:
            third += f"  th{theta:+.0f} s{row['shift']}"
    if arm == "E2" and row["candidate_events"]:
        third += f"  {row['query_events']}/{row['candidate_events']}ev"
    return f"#{row['rank']} {short_id(row['candidate_id'], 20)}\n{_who(row)}\n{third}"


def _compare_panel(
    ax: Any,
    *,
    candidate: np.ndarray,
    query: np.ndarray,
    arm: str,
    row: dict[str, Any],
    caption: str,
) -> None:
    """One Top-k cell: the candidate as stored, the query overlaid honestly.

    Only E0 returns a transform, so only E0's candidate is re-posed before the overlay
    (solid green ring).  For E1/E2/E3 the two rings are simply both in the standardizer's
    own frame — centroid at the origin, unit RMS radius — and drawing them as if something
    had been fitted would claim a value the arm never returned.
    """
    from earthglyph.retrieval.alignment import apply_alignment

    relevant = row["relevant"] == "1"
    edge = RELEVANT_EDGE if relevant else CANDIDATE_EDGE
    face = RELEVANT_FACE if relevant else CANDIDATE_FACE
    if arm == "E0" and _number(row["theta_deg"]) is not None:
        shown = apply_alignment(
            candidate,
            shift=int(row["shift"]),
            theta_rad=float(np.radians(_number(row["theta_deg"]) or 0.0)),
            mirrored=str(row["mirrored"]).lower() == "true",
        )
    else:
        shown = candidate
    draw_contour(ax, shown, color=edge, face=face)
    ring = np.vstack([query, query[:1]])
    ax.plot(
        ring[:, 0],
        ring[:, 1],
        color="#1b7f4b" if arm == "E0" else "#a4231d",
        lw=1.0,
        linestyle="-" if arm == "E0" else "--",
        zorder=5,
    )
    ax.set_title(caption, fontsize=4.4, color=edge, pad=2.0, linespacing=1.45)


def _query_cell(ax: Any, points: np.ndarray, query_id: str, arm: str) -> None:
    """The left-hand cell of a row: the query itself, tagged with the arm scoring it."""
    draw_contour(ax, points, color="#a4231d", face="#f6e2e0")
    if arm:
        ax.set_ylabel(
            arm, fontsize=7.0, rotation=0, ha="right", va="center", labelpad=13, color="#1b2735"
        )
    ax.set_title(f"QUERY {short_id(query_id, 22)}", fontsize=5.0, color="#a4231d", pad=2.0)


def _footer(fig: Any, extra: str) -> None:
    lines = [
        "solid green ring = the pose E0 fitted;  dashed red ring = the query as standardized, "
        "no transform applied (E1/E2/E3 return no per-candidate pose).",
        "V=variant  N=hard negative  Q=query role;  REL = the frozen label's relevant set.  "
        "d is a dimensionless internal distance, never a similarity percentage.",
        "these are the closest candidates in this library, not verified matches; no human "
        "judged any panel here (blind review is Checkpoint D).",
    ] + ([extra] if extra else [])
    fig.text(0.008, 0.010, "\n".join(lines), fontsize=5.9, color="#40506a", linespacing=1.5)


def comparison_figure(
    family: str,
    query_ids: list[str],
    cells: dict[tuple[str, str, str], list[dict[str, Any]]],
    contours: dict[str, np.ndarray],
    out_path: Path,
    *,
    setting: str,
    arms: tuple[str, ...] = METHOD_COLUMNS,
    top: int = 5,
    dpi: int = 200,
    lookalikes: int = 0,
) -> dict[str, Any]:
    """Every query of one family, every arm, Top-k — the eye-check grid.

    Rows are (query, arm); the leftmost cell is the query and the rest are that arm's Top-k
    candidates in the arm's own order, with the distance it printed.  Nothing is picked by
    eye: rows come from ``results.csv``, contours from the stored 128-point rings, and a row
    whose arm returned no candidate is left blank rather than padded with a nicer shape.
    """
    plt = _figure_backend()
    configure_cjk_font()
    columns = 1 + top
    rows_spec = [
        (query_id, arm, contours[query_id], cells.get((setting, arm, query_id), []))
        for query_id in query_ids
        for arm in arms
    ]
    fig_w = 0.95 * columns + 1.5
    fig_h = 1.02 * len(rows_spec) + 1.5
    fig, axes = plt.subplots(
        len(rows_spec), columns, figsize=(fig_w, fig_h), squeeze=False, dpi=dpi
    )
    for index, (query_id, arm, query, rows) in enumerate(rows_spec):
        _query_cell(axes[index][0], query, query_id, arm)
        for slot in range(top):
            ax = axes[index][slot + 1]
            if slot >= len(rows):
                ax.axis("off")
                continue
            row = rows[slot]
            candidate = contours.get(row["candidate_id"])
            if candidate is None:
                ax.axis("off")
                continue
            _compare_panel(
                ax, candidate=candidate, query=query, arm=arm, row=row,
                caption=_candidate_lines(row, arm),
            )
    label = GALLERY_LABEL if setting != "T3" else REAL_LABEL
    # The header band is sized in inches, not as a fraction: a fixed fraction shrinks as
    # rows are added, which is what let the two-line title land on the first row's captions.
    header_in = 0.92
    footer_in = 0.62
    fig.suptitle(
        f"{label}\nfamily '{family}' · setting {setting} · Top-{top} of "
        f"{' '.join(arms)} against the same query contour",
        fontsize=9.5,
        y=1.0 - 0.04 / fig_h,
        va="top",
        linespacing=1.6,
    )
    _footer(
        fig,
        (
            f"{lookalikes} non-relevant candidate(s) in these libraries are "
            "align-indistinguishable from the query, so a cell can look right and still "
            "count as a miss."
            if lookalikes
            else ""
        ),
    )
    fig.subplots_adjust(
        left=0.048,
        right=0.988,
        top=1.0 - header_in / fig_h,
        bottom=footer_in / fig_h,
        wspace=0.14,
        hspace=0.74,
    )
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "family": family,
        "setting": setting,
        "queries": query_ids,
        "arms": list(arms),
        "top": top,
        "rows": len(rows_spec),
        "panels": len(rows_spec) * columns,
    }


def overview_figure(
    query_ids: list[str],
    cells: dict[tuple[str, str, str], list[dict[str, Any]]],
    contours: dict[str, np.ndarray],
    metrics: dict[str, Any],
    out_path: Path,
    *,
    setting: str,
    arms: tuple[str, ...] = METHOD_COLUMNS,
    dpi: int = 170,
) -> dict[str, Any]:
    """Headline numbers read straight from metrics.json, then every query's Top-1.

    The table is printed from the JSON rather than retyped, so a figure cannot drift from
    the run it came from (D48).  The queries are laid out in two blocks so the picture
    stays a readable aspect ratio instead of a 4 000-pixel scroll.
    """
    plt = _figure_backend()
    configure_cjk_font()
    table = metrics["results"][setting]
    per_block = -(-len(query_ids) // 2)
    width = 1 + len(arms)
    columns = 2 * width
    fig_h = 1.02 * (per_block + 3.4) + 1.1
    fig = plt.figure(figsize=(0.95 * columns + 1.6, fig_h), dpi=dpi)
    grid = fig.add_gridspec(
        per_block + 1,
        columns,
        left=0.055,
        right=0.988,
        top=1.0 - 0.42 / fig_h,
        bottom=0.62 / fig_h,
        wspace=0.16,
        hspace=0.82,
        height_ratios=[3.4] + [1.0] * per_block,
    )
    axis_table = fig.add_subplot(grid[0, :])
    axis_table.axis("off")
    keys = ("hit@1", "hit@3", "hit@5", "mrr", "ap", "ndcg@10")
    lines = [
        f"setting {setting} · every candidate scored by every arm, no coarse window, "
        "ties broken by library row order",
        "arm     " + "".join(f"{key:>9}" for key in keys) + f"{'sec/query':>11}",
    ]
    for arm in arms:
        aggregate = table[arm]["aggregate"]
        lines.append(
            f"{arm:8}"
            + "".join(f"{aggregate[key]:>9.3f}" for key in keys)
            + f"{table[arm]['timing']['seconds_mean_per_query']:>11.3f}"
        )
    lines.append("")
    lines.append("Top-k slot composition, and the label-pressure count:")
    for arm in arms:
        slots = table[arm]["top_slots"]
        lines.append(
            f"  {arm}: hard-neg {slots['same_family_hard_negative_share']:.2f}  "
            f"other-fam {slots['other_family_gallery_share']:.2f}  "
            f"landform {slots['landform_share']:.2f}  "
            f"Top1-lookalike {table[arm]['label_pressure']['top1_is_label_lookalike_share']:.2f}"
        )
    axis_table.text(
        0.0, 1.0, "\n".join(lines), family="monospace", fontsize=6.2, va="top", color="#1b2735"
    )
    label = GALLERY_LABEL if setting != "T3" else REAL_LABEL
    axis_table.set_title(
        f"{label} — P0-4.5 Checkpoint C · {len(query_ids)} frozen queries × "
        f"{' '.join(arms)} · each arm's Top-1",
        fontsize=9.0,
        loc="left",
        pad=11,
    )
    for position in range(per_block):
        for block in (0, 1):
            offset_index = block * per_block + position
            if offset_index >= len(query_ids):
                for column in range(width):
                    fig.add_subplot(grid[position + 1, block * width + column]).axis("off")
                continue
            query_id = query_ids[offset_index]
            for column in range(width):
                ax = fig.add_subplot(grid[position + 1, block * width + column])
                if column == 0:
                    _query_cell(ax, contours[query_id], query_id, "")
                    continue
                arm = arms[column - 1]
                rows = cells.get((setting, arm, query_id), [])
                if not rows or rows[0]["candidate_id"] not in contours:
                    ax.axis("off")
                    continue
                row = rows[0]
                _compare_panel(
                    ax,
                    candidate=contours[row["candidate_id"]],
                    query=contours[query_id],
                    arm=arm,
                    row=row,
                    caption=f"{arm} · " + _candidate_lines(row, arm),
                )
    _footer(fig, "")
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "setting": setting,
        "queries": len(query_ids),
        "arms": list(arms),
        "blocks": 2,
        "panels": len(query_ids) * width,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - provenance is best effort, never fatal
        return None
    return result.stdout.strip() or None


def write_output_manifest(
    out_dir: Path,
    produced: list[Path],
    *,
    inputs: list[Path],
    commands: list[str],
) -> dict[str, Any]:
    """sha256 for every artefact this checkpoint wrote, with the command that wrote it.

    P0-4 had no such file (review finding F4): its products were only addressable through
    git blobs, so "re-run and get the same thing" was a claim rather than a check. Every
    number in the figures and tables is now tied to a named file and a named command.
    """
    manifest = {
        "checkpoint": "C",
        "built_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reproduce": {"git_commit": _git_commit(), "commands": commands},
        "inputs": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in inputs
        },
        "outputs": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in produced
        },
    }
    path = out_dir / "figures_manifest.json"
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gallery-dir",
        type=Path,
        default=Path("experiments/p0_perceptual_probe"),
    )
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_perceptual_probe"))
    parser.add_argument("--results", type=Path, default=None, help="results.csv from the runner")
    parser.add_argument("--metrics", type=Path, default=None, help="metrics.json from the runner")
    parser.add_argument(
        "--settings",
        default="T2",
        help="'all' or a comma list of T1,T2,T3; each setting gets its own figures",
    )
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--families",
        default="all",
        help="'all' or a comma list; every family has one comparison figure",
    )
    parser.add_argument(
        "--control-only",
        action="store_true",
        help="redraw control_gallery.png and stop (it is normally the builder's artefact)",
    )
    parser.add_argument(
        "--with-control",
        action="store_true",
        help="also redraw control_gallery.png in a full figure run",
    )
    parser.add_argument("--no-manifest", action="store_true")
    args = parser.parse_args(argv)
    settings = (
        ["T1", "T2", "T3"]
        if args.settings == "all"
        else [item for item in args.settings.split(",") if item]
    )
    unknown = sorted(set(settings) - {"T1", "T2", "T3"})
    if unknown:
        raise SystemExit(f"--settings must be a subset of T1,T2,T3 (got {unknown})")

    items, labels = _load(args.gallery_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[Path] = []
    commands = [
        "python scripts/prepare_perceptual_dataset.py --offline --force",
        "python scripts/evaluate_perceptual_methods.py --top 10",
        " ".join(["python"] + [str(Path(sys.argv[0]).as_posix())] + list(argv or sys.argv[1:])),
    ]
    if args.control_only or args.with_control:
        # control_gallery.png is the dataset builder's artefact.  Redrawing it here uses the
        # published gallery.jsonl, whose contours are rounded to 6 digits, so the bytes differ
        # from the builder's full-precision drawing — hence opt-in rather than a free side
        # effect of asking for comparison figures.
        gallery_path = args.out_dir / "control_gallery.png"
        info = control_gallery_figure(items, gallery_path)
        produced.append(gallery_path)
        print(json.dumps(info, indent=1, ensure_ascii=False))
    if args.control_only:
        return 0

    results_path = args.results or args.out_dir / "results.csv"
    metrics_path = args.metrics or args.out_dir / "metrics.json"
    cells = load_results(results_path)
    metrics: dict[str, Any] = json.loads(metrics_path.read_text(encoding="utf-8"))
    absent = sorted(set(settings) - set(metrics["results"]))
    if absent:
        raise SystemExit(
            f"{metrics_path} has no {absent}; it has {sorted(metrics['results'])} "
            "- run scripts/evaluate_perceptual_methods.py first"
        )
    contours: dict[str, np.ndarray] = {item.item_id: item.contour for item in items}
    index_loaded = False

    families = sorted({item["family"] for item in labels["queries"].values()})
    if args.families != "all":
        families = [family for family in families if family in args.families.split(",")]
    queries_by_family = {
        family: sorted(
            qid for qid, item in labels["queries"].items() if item["family"] == family
        )
        for family in families
    }
    for setting in settings:
        # One process, one manifest: writing the manifest per setting would leave only the
        # last run's figures listed, and a reader would take that for the whole checkpoint.
        arms = tuple(arm for arm in METHOD_COLUMNS if arm in metrics["results"][setting])
        if setting == "T3" and not index_loaded:
            index_ids, index_rows, _ = load_landforms(args.index)
            contours.update(dict(zip(index_ids, index_rows, strict=True)))
            index_loaded = True
        e0_records = metrics["results"][setting].get("E0", {}).get("per_query", {})
        lookalike_counts = {
            query_id: int(record.get("align_indistinguishable_non_relevant", 0))
            for query_id, record in e0_records.items()
        }
        missing = sorted(set(labels["queries"]) - set(lookalike_counts))
        if missing:
            print(
                f"warning: {setting} E0 per_query has no record for {len(missing)} quer"
                f"{'y' if len(missing) == 1 else 'ies'} ({', '.join(missing[:3])}"
                f"{' ...' if len(missing) > 3 else ''}); their lookalike counts are unknown, "
                "so the lookalike footnote may understate the label-vs-perception trap",
                file=sys.stderr,
            )
        for family in families:
            out_path = args.out_dir / f"comparison_{family}_{setting.lower()}.png"
            info = comparison_figure(
                family,
                queries_by_family[family],
                cells,
                contours,
                out_path,
                setting=setting,
                arms=arms,
                top=args.top,
                lookalikes=sum(
                    lookalike_counts.get(qid, 0) for qid in queries_by_family[family]
                ),
            )
            produced.append(out_path)
            print(json.dumps(info, indent=1, ensure_ascii=False))
        out_path = args.out_dir / f"perception_comparison_overview_{setting.lower()}.png"
        info = overview_figure(
            sorted(labels["queries"]),
            cells,
            contours,
            metrics,
            out_path,
            setting=setting,
            arms=arms,
        )
        produced.append(out_path)
        print(json.dumps(info, indent=1, ensure_ascii=False))

    if not args.no_manifest:
        inputs = [
            args.gallery_dir / name
            for name in ("gallery.jsonl", "labels.json", "dataset_manifest.json")
        ] + [results_path, metrics_path]
        manifest = write_output_manifest(
            args.out_dir, produced, inputs=[path for path in inputs if path.exists()],
            commands=commands,
        )
        print(json.dumps({"manifest_entries": len(manifest["outputs"])}, indent=1))
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
