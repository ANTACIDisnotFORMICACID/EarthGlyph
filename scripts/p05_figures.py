#!/usr/bin/env python
"""C2: draw the P0-5 strength curves and real failure overlays from saved results.

Two artefacts under ``results/figures/``:

- ``strength_curves.png`` — G1 bars and G2 curves, read only from
  ``results/summary_by_strength.json`` (a file that cannot have moved: it is
  regenerated from results.csv by ``evaluate_p05.py --aggregate-only`` and
  verified by ``recompute_p05_metrics.py``).
- ``failure_overlays.png`` — real query-vs-truth overlays, one per attribution
  class that fired in the run, drawn from the query JSON files and the index
  shapes actually used by the retriever. A class with zero rows (both
  C_rerank_order and C_dedup_or_truncation) is stated in the notes instead of
  being invented here.

Usage:
    python scripts/p05_figures.py --results experiments/p0_perturbation_sweep/results \
        --queries experiments/p0_perturbation_sweep/queries --index data/index
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.retrieval.search import load_index_handle, search_by_contour  # noqa: E402
from scripts.visualize_retrieval import draw_pair  # noqa: E402

QUERY_COLOR = "#c0392b"


def strength_curves(summary: dict, out_path: Path) -> None:
    g1 = summary["by_strength"]["G1"]
    g2 = summary["by_strength"]["G2"]
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6))

    # G1: one bar per variant, production Top-1; coarse-only as a marker
    names, top1, top1_p0, top10 = [], [], [], []
    for perturbation in sorted(g1):
        for strength, cell in sorted(g1[perturbation].items()):
            names.append(strength)
            top1.append(cell["top1"])
            top1_p0.append(cell["top1_p0"])
            top10.append(cell["top10"])
    ax = axes[0]
    positions = np.arange(len(names))
    ax.bar(positions, top1, color="#2c7fb8", label="P1 production Top-1")
    ax.scatter(positions, top1_p0, color="#d95f0e", marker="_", s=340,
               label="P0 coarse-only Top-1", zorder=3)
    ax.plot(positions, top10, color="#737373", lw=0.9, ls="--", marker=".",
            label="P1 Top-10")
    ax.set_xticks(positions, names, rotation=45, ha="right", fontsize=7.5)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.set_title("G1 invariances and sparsity (n=118 each)")
    ax.legend(fontsize=7.5, loc="lower left")

    # G2: Top-1 / Top-10 against strength for every layer
    for column, title in ((1, "G2 deformation strength — Top-1"),
                          (2, "G2 deformation strength — Top-10")):
        ax = axes[column]
        metric = "top1" if column == 1 else "top10"
        for perturbation in sorted(g2):
            cells = sorted(g2[perturbation].items())
            xs = list(range(len(cells)))
            ys = [cell[metric] for _, cell in cells]
            ax.plot(xs, ys, marker="o", ms=3.5, lw=1.2, label=perturbation)
            for x, (strength, cell) in zip(xs, cells, strict=True):
                ax.annotate(strength.split("-", 1)[-1], (x, cell[metric]),
                            fontsize=6, xytext=(0, -9), textcoords="offset points",
                            ha="center", color="#404040")
        ax.set_xticks(range(max(len(g2[p]) for p in g2)),
                      [f"s{i}" for i in range(max(len(g2[p]) for p in g2))])
        ax.set_ylim(0, 1.05)
        ax.set_xlabel("strength index (labels under each point)")
        ax.set_title(title)
        ax.legend(fontsize=7.5)

    index_info = summary["params"]["index"]
    fig.suptitle(
        "P0-5 perturbation sweep — production fourier_align (top_k=10, "
        f"shortlist=200), index {index_info['index_git_commit'][:12]} "
        f"shapes sha256 {index_info['output_sha256']['shapes'][:12]}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def prepared_query(path: Path, index: object) -> np.ndarray:
    result = search_by_contour(path, index, top_k=1, method="fourier_align")
    return np.asarray(result.query["query_contour"]["points"], dtype=float)


def failure_overlays(
    rows: pd.DataFrame, queries_dir: Path, index: object, shapes: np.ndarray,
    out_path: Path,
) -> None:
    """One real overlay per attribution class that fired, plus an ok contrast.

    Selection is deterministic: the alphabetically first query of each class
    inside the named strength. Nothing is swapped after looking at distances.
    """
    picks = [
        ("A_extraction", "gap-0.2-tail0.15",
         "A: refused at extraction\n(closure gap > limit) — never searched"),
        ("B_coarse_filter", "sparse-16",
         "B: coarse put truth 297th\n(shortlist 200), full align ranks it 2nd"),
        ("D_coverage_or_deformation", "aspect-1.4",
         "D: aspect-1.4 —\nre-ranked beyond top-10"),
        ("D_coverage_or_deformation", "finger-0.45",
         "D: finger-0.45 —\nre-ranked beyond top-10"),
        ("D_coverage_or_deformation", "mirror",
         "D: mirror query — no-mirror re-rank\ndrops truth (mirror-allowed rank = 1)"),
        ("ok", "bay-0.18",
         "ok: matched, re-ranked rank 1\n— contrast panel"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(12.5, 8.6))
    notes: list[str] = []
    for ax, (label, strength, caption) in zip(axes.ravel(), picks, strict=True):
        block = rows[(rows["label"] == label) & (rows["strength"] == strength)]
        if block.empty:
            ax.set_axis_off()
            ax.set_title(f"{caption}\n(no row in this class/strength)", fontsize=8)
            continue
        row = block.sort_values("query").iloc[0]
        query_path = queries_dir / str(row["query"])
        payload = json.loads(query_path.read_text(encoding="utf-8"))
        truth = int(row["source_row"])
        if label == "A_extraction":
            query = np.asarray(payload["points"], dtype=float)
            notes.append(f"{row['query']}: {row['refusal']}")
        else:
            query = prepared_query(query_path, index)
        draw_pair(ax, query, shapes[truth])
        ax.set_title(caption, fontsize=8.5)
        note = (
            f"{row['query']}  truth row {truth}  "
            f"query-vs-source align {row['query_vs_source_align']}"
            if label != "A_extraction"
            else f"{row['query']}  truth row {truth} (refusal)"
        )
        ax.text(0.02, -0.02, note, transform=ax.transAxes, fontsize=6.5,
                color="#404040")
    fig.suptitle(
        "P0-5 failure attribution overlays — red = query, blue = truth source "
        "(index shapes, N=128). C_rerank_order and C_dedup_or_truncation fired "
        "0 times in 3658 queries.",
        fontsize=9,
    )
    if notes:
        fig.text(0.01, 0.005, "\n".join(notes), fontsize=6, color="#404040")
    fig.tight_layout(rect=(0, 0.02, 1, 0.94))
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path,
                        default=Path("experiments/p0_perturbation_sweep/results"))
    parser.add_argument("--queries", type=Path,
                        default=Path("experiments/p0_perturbation_sweep/queries"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    args = parser.parse_args(argv)

    figures = args.results / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    summary = json.loads(
        (args.results / "summary_by_strength.json").read_text(encoding="utf-8")
    )
    strength_curves(summary, figures / "strength_curves.png")
    print(f"wrote {figures / 'strength_curves.png'}")

    rows = pd.read_csv(args.results / "results.csv")
    index = load_index_handle(args.index)
    failure_overlays(rows, args.queries, index, index.shapes,
                     figures / "failure_overlays.png")
    print(f"wrote {figures / 'failure_overlays.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
