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
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gallery-dir",
        type=Path,
        default=Path("experiments/p0_perceptual_probe"),
    )
    parser.add_argument("--control-only", action="store_true")
    args = parser.parse_args(argv)
    items, _labels = _load(args.gallery_dir)
    info = control_gallery_figure(items, args.gallery_dir / "control_gallery.png")
    print(json.dumps(info, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
