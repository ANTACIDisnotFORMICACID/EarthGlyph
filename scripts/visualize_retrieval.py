#!/usr/bin/env python
"""CLI: the P0-4 retrieval figures — abstraction audit, real Top-K, baseline compare.

Four figures, all drawn from the REAL 839-candidate index (``data/index``):

A ``extraction_examples.png``      raw trace / extracted outline / 128-point contour
                                   for every query (Task A acceptance)
B ``smoothing_ablation.png``       what none / light / medium each cost the shape,
                                   measured on the whole library (Task A3)
C ``topk_<query>.png``             sketch + 10 real candidates, each overlaid at the
                                   transform the matcher actually chose (Task F)
D ``baselines_<query>.png``        the same query under the three baselines (Task F)

Every panel carries the candidate's real ``candidate_id``, subtype and coordinates,
so a figure can be checked against the source data. Nothing here re-orders or
retouches a result: the ranking is whatever ``earthglyph.retrieval.search`` returned.

Example
-------
python scripts/visualize_retrieval.py \
    --index data/index --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_shared import draw_contour, short_id  # noqa: E402

from earthglyph.features.geometry_stats import FEATURE_COLUMNS as GEOMETRY_COLUMNS  # noqa: E402
from earthglyph.geometry.sketch import (  # noqa: E402
    SketchError,
    contour_from_sketch,
    load_sketch_json,
)
from earthglyph.retrieval.search import (  # noqa: E402
    IndexHandle,
    SearchError,
    SearchResult,
    load_index_handle,
    search_by_contour,
)

QUERY_LINE = "#a4262c"
CANDIDATE_FACE = "#7fb2d9"
CANDIDATE_EDGE = "#28556e"
SMOOTHINGS = ("none", "light", "medium")
BASELINE_METHODS = ("hu", "fourier", "fourier_align")


def _figure_backend() -> Any:
    """Import matplotlib only when a figure is actually written (Agg, no display)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _caption_lines(text: str, width: int = 34) -> str:
    return "\n".join(textwrap.wrap(text, width=width))


# --- A: the abstraction audit -------------------------------------------------


def extraction_records(
    targets: list[Any],
    *,
    index: IndexHandle,
    smoothing: str = "light",
    names: list[str] | None = None,
    allow_self_intersection: bool = False,
) -> list[dict[str, Any]]:
    """Per-query abstraction metrics for *every* input, accepted or refused.

    The table is the audit trail behind figure A: a refusal is recorded with its
    reason instead of dropping the row, so "the tool said no" is as visible as
    "the tool said here is a contour". ``allow_self_intersection`` defaults to the
    search path's own value, so a row cannot be "accepted" here and then refused by
    ``search_by_contour`` for the same reason.
    """
    labels = names if names is not None else [Path(str(t)).stem for t in targets]
    records: list[dict[str, Any]] = []
    for label, target in zip(labels, targets, strict=True):
        try:
            contour = contour_from_sketch(
                target,
                n_points=index.n_points,
                smoothing=smoothing,
                allow_self_intersection=allow_self_intersection,
            )
        except (SketchError, SearchError) as error:
            records.append({"query": label, "refused": str(error), "smoothing": smoothing})
            continue
        summary = contour.to_dict()
        records.append(
            {
                "query": label,
                "source": contour.source,
                "unit": contour.unit,
                "n_raw": int(contour.raw_points.shape[0]),
                "n_outline": int(contour.outline_points.shape[0]),
                "end_gap_rel": round(float(contour.closure["gap_rel"]), 6),
                "auto_closed": bool(contour.closure["auto_closed"]),
                "seam_closed": bool(contour.closure.get("seam_closed")),
                "reject_limit_rel": round(float(contour.closure["reject_limit_rel"]), 6),
                "winding": contour.winding,
                "smoothing": contour.smoothing,
                "smoothing_sigma": contour.smoothing_sigma,
                "outline_perimeter": round(float(summary["outline_perimeter"]), 6),
                "outline_area": round(float(summary["outline_area"]), 6),
                "self_crossings": contour.self_intersection["count"],
                "warnings": list(contour.warnings),
            }
        )
    return records


def extraction_figure(
    queries: list[tuple[str, Any]],
    out_path: Path,
    *,
    index: IndexHandle,
    smoothing: str = "light",
    allow_self_intersection: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rows = queries, columns = raw trace / extracted outline / standardized contour.

    The third column is what retrieval actually sees. Reading the row left to right
    answers the one question Task A asks: was the shape kept, or was it prettified?
    """
    plt = _figure_backend()
    rows = len(queries)
    fig, axes = plt.subplots(rows, 3, figsize=(7.6, 2.5 * rows), squeeze=False)
    records: list[dict[str, Any]] = []
    for row, (name, target) in enumerate(queries):
        try:
            contour = contour_from_sketch(
                target,
                n_points=index.n_points,
                smoothing=smoothing,
                allow_self_intersection=allow_self_intersection,
            )
        except (SketchError, SearchError) as error:
            # A refusal is drawn, not dropped: for a trace the reader sees the very
            # stroke that was rejected and can judge the decision themselves.
            for column in range(3):
                axes[row][column].axis("off")
            axes[row][0].axis("on")
            axes[row][0].set_xticks([])
            axes[row][0].set_yticks([])
            axes[row][0].set_title(f"{name}: REFUSED", fontsize=6.5, color=QUERY_LINE)
            try:
                raw, _ = load_sketch_json(target)
            except Exception:
                raw = None
            suffix = Path(str(target)).suffix.lower()
            if raw is not None and suffix in {".json", ".txt"}:
                draw_raw(axes[row][0], raw, "canvas")
                axes[row][0].set_title(f"{name}: REFUSED stroke", fontsize=6.5, color=QUERY_LINE)
            elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                draw_ink(axes[row][0], Path(str(target)))
                axes[row][0].set_title(f"{name}: REFUSED ink", fontsize=6.5, color=QUERY_LINE)
            else:
                axes[row][0].set_title(f"{name}: REFUSED", fontsize=6.5, color=QUERY_LINE)
            axes[row][1].text(
                0.0, 0.5, _caption_lines(str(error), 30), fontsize=6.0, va="center"
            )
            records.append({"query": name, "refused": str(error), "smoothing": smoothing})
            continue
        if contour.source == "image":
            draw_ink(axes[row][0], Path(str(target)))
            axes[row][0].set_title(f"{name}: raw ink as saved", fontsize=6.5)
        else:
            draw_raw(axes[row][0], contour.raw_points, contour.unit)
            axes[row][0].set_title(f"{name}: raw trace ({contour.source})", fontsize=6.5)
        draw_raw(axes[row][1], contour.outline_points, contour.unit)
        draw_contour(axes[row][2], contour.points)
        axes[row][2].set_title("standardized contour (RMS radius 1)", fontsize=6.5)
        gap = contour.closure["gap_rel"]
        axes[row][1].set_title(
            _caption_lines(
                f"extracted outline ({contour.outline_points.shape[0]} pts, "
                f"end gap {gap:.3f}, {contour.winding})"
            ),
            fontsize=6.0,
        )
        records.append(
            {
                "query": name,
                "source": contour.source,
                "unit": contour.unit,
                "n_raw": int(contour.raw_points.shape[0]),
                "n_outline": int(contour.outline_points.shape[0]),
                "end_gap_rel": round(gap, 6),
                "auto_closed": bool(contour.closure["auto_closed"]),
                "seam_closed": bool(contour.closure.get("seam_closed")),
                "winding": contour.winding,
                "smoothing": contour.smoothing,
                "smoothing_sigma": contour.smoothing_sigma,
                "outline_perimeter": round(float(contour.to_dict()["outline_perimeter"]), 6),
                "outline_area": round(float(contour.to_dict()["outline_area"]), 6),
                "self_crossings": contour.self_intersection["count"],
                "warnings": contour.warnings,
            }
        )
    if rows == 1:
        for col, label in enumerate(("raw input", "extracted outline", "query contour")):
            axes[0][col].set_ylabel(label, fontsize=7)
    fig.suptitle(
        "A — abstraction audit: the drawn stroke, the outline it yields, and the "
        f"contour retrieval sees (smoothing={smoothing})",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.05 if extra else 0.0, 1, 0.95))
    if extra:
        _write_note(fig, extra)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return {"figure": out_path.name, "rows": records}


def draw_raw(ax: Any, points: np.ndarray, unit: str) -> None:
    """Plot a trace in its own canvas/pixel frame, y axis up for pixels."""
    array = np.asarray(points, dtype=float)
    ring = np.vstack([array, array[:1]])
    ax.plot(ring[:, 0], -ring[:, 1], color="#3c4650", lw=0.8)
    ax.plot(array[0, 0], -array[0, 1], "o", ms=3.5, color=QUERY_LINE)
    ax.plot(array[-1, 0], -array[-1, 1], "s", ms=3.5, color="#1c6b4f")
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel(f"{unit} units (y up); red = start, green = end", fontsize=5.5)


def draw_ink(ax: Any, path: Path) -> None:
    """The saved pixels, exactly as stored — for an image query this is the raw input.

    Deliberately not the extracted boundary: showing the boundary twice would hide
    the one thing the reader needs to check, namely what the ink actually looked
    like before the pipeline chose a ring from it.
    """
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"{path}: not a readable image")
    ax.imshow(image, cmap="gray", origin="upper")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel(f"saved pixels {image.shape[1]}x{image.shape[0]}", fontsize=5.5)


# --- B: smoothing ablation on the whole library ------------------------------


def ablation_figure(table: list[dict[str, Any]], out_path: Path) -> dict[str, Any]:
    """Per smoothing level, measured on every library candidate: mean cost and worst cost."""
    plt = _figure_backend()
    fig, axes = plt.subplots(1, 3, figsize=(9.8, 3.1))
    levels = [str(row["smoothing"]) for row in table]
    n_rows = int(table[0]["n_candidates"]) if table else 0
    band = str(table[0]["detail_band"]) if table else "the upper band"
    pos = np.arange(len(levels))
    width = 0.38
    for slot, ax in enumerate((axes[0], axes[1], axes[2])):
        mean_key = (
            "mean_detail_share_ratio",
            "mean_compactness_change",
            "mean_perimeter_change",
        )[slot]
        label = (
            f"share of {band} spectral energy kept"
            if slot == 0
            else ("mean |change| in compactness" if slot == 1 else "mean |change| in perimeter")
        )
        values = [float(row[mean_key]) for row in table]
        worst = [
            float(row["min_detail_share_ratio" if slot == 0 else "max_" + mean_key[5:]])
            for row in table
        ]
        ax.bar(pos - width / 2, values, width, color="#28556e", label="mean over candidates")
        ax.bar(pos + width / 2, worst, width, color="#c9a227", label="worst candidate")
        ax.set_xticks(pos, levels)
        ax.set_title(_caption_lines(label, 26), fontsize=7)
        ax.legend(fontsize=6)
        for value, x in zip(values, pos - width / 2, strict=True):
            ax.text(x, value, f"{value:.3f}", ha="center", va="bottom", fontsize=5.6)
        for value, x in zip(worst, pos + width / 2, strict=True):
            ax.text(x, value, f"{value:.3f}", ha="center", va="bottom", fontsize=5.6)
    fig.suptitle(
        "B — smoothing ablation over every candidate in the real library "
        f"(n={n_rows} contours at 128 points; none / light / medium differ only in sigma)",
        fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return {"figure": out_path.name, "levels": table}


# --- C: real Top-K with honest overlays --------------------------------------


def topk_figure(
    result: SearchResult,
    out_path: Path,
    *,
    query_label: str,
    index: IndexHandle | None = None,
    allow_mirror: bool = False,
    top: int = 10,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sketch plus the returned candidates, each drawn at the matcher's transform."""
    plt = _figure_backend()
    hits = result.hits[:top]
    columns = 5
    rows = 1 + (len(hits) + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(13.0, 2.6 * rows), squeeze=False)
    query = np.asarray(result.query["query_contour"]["points"], dtype=float)
    draw_pair(axes[0][0], query, None, fill_query=True)
    axes[0][0].set_title(
        _caption_lines(f"QUERY — {query_label} ({result.query['query_contour']['source']})", 22),
        fontsize=6.5,
    )
    for slot in range(1, columns):
        axes[0][slot].axis("off")
    axes[0][1].text(
        0.0,
        0.97,
        _caption_lines(_result_note(result), 62),
        fontsize=6.2,
        va="top",
        family="monospace",
    )
    for slot, hit in enumerate(hits):
        ax = axes[1 + slot // columns][slot % columns]
        moved, drawn = _candidate_contour(
            result, hit, index, display_align=True, allow_mirror=allow_mirror, query=query
        )
        draw_pair(ax, query, moved)
        ax.set_title(_caption_lines(_hit_caption(result, hit) + drawn, 22), fontsize=5.8)
    fig.suptitle(
        f"C — method={result.method}: real Top-{len(hits)} for “{query_label}” "
        "(red = the query, filled = the candidate at the transform the matcher chose)",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.05 if extra else 0.0, 1, 0.95))
    if extra:
        _write_note(fig, extra)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"figure": out_path.name, "query": query_label, "results": _rows(result, hits)}


def _candidate_contour(
    result: SearchResult,
    hit: Any,
    index: IndexHandle | None,
    *,
    display_align: bool = False,
    allow_mirror: bool = False,
    query: np.ndarray | None = None,
) -> tuple[np.ndarray, str]:
    """The polygon to draw, plus a caption saying how it was obtained.

    A re-ranked hit carries the contour the matcher itself aligned, so it is drawn
    as returned. A coarse-only hit has no transform, and drawing it in its stored
    rotation would make two rows of a baseline comparison differ by rotation rather
    than by shape — hence ``display_align``, which moves the *drawing* only and
    leaves the printed distance and the ranking untouched.
    """
    if hit.contour is not None:
        return np.asarray(hit.contour, dtype=float), ""
    if index is None:
        raise ValueError("a coarse-only method needs --index to draw the candidate")
    stored = np.asarray(index.shapes[hit.row], dtype=float)
    if not display_align or query is None:
        return stored, " drawn as stored"
    moved, best = _display_align(query, stored, allow_mirror=allow_mirror)
    return moved, (
        f" display-aligned θ={np.rad2deg(best.theta_rad):+.0f}° shift={best.shift} "
        f"mirror={best.mirrored} (drawing only)"
    )


def draw_pair(
    ax: Any, query: np.ndarray, candidate: np.ndarray | None, *, fill_query: bool = False
) -> None:
    """Candidate filled, query drawn on top: the overlay is the visual verdict."""
    if candidate is not None:
        draw_contour(ax, candidate, color=CANDIDATE_EDGE, face=CANDIDATE_FACE)
    if fill_query and candidate is None:
        draw_contour(ax, query, color=QUERY_LINE, face="#f2d7d5")
    ring = np.vstack([query, query[:1]])
    ax.plot(ring[:, 0], ring[:, 1], color=QUERY_LINE, lw=1.1, zorder=4)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def _hit_caption(result: SearchResult, hit: Any) -> str:
    align = (
        f"d={hit.align_distance:.4f} θ={hit.align_theta_deg:+.1f}° shift={hit.align_shift} "
        f"mirror={hit.align_mirrored}"
        if hit.align_distance is not None
        else f"coarse d={hit.coarse_distance:.4f}, no re-rank"
    )
    chamfer = "" if hit.chamfer is None else f" cham={hit.chamfer:.4f}"
    return (
        f"#{hit.rank} {short_id(hit.candidate_id)} {hit.subtype} "
        f"{hit.lon:.4f},{hit.lat:.4f} {hit.area_m2 / 1e4:,.1f} ha — {align}{chamfer}"
    )


def _result_note(result: SearchResult) -> str:
    geometry = result.query.get("query_geometry")
    named = ""
    if geometry is not None:
        named = " | query geometry: " + " ".join(
            f"{name}={float(value):.3f}"
            for name, value in zip(GEOMETRY_COLUMNS, np.asarray(geometry, dtype=float), strict=True)
        )
    return (
        f"params: {json.dumps(result.params, sort_keys=True, default=str)}\n"
        f"provenance: {result.provenance['source_name']} "
        f"{result.provenance['n_candidates']} candidates, "
        f"commit {str(result.provenance['index_git_commit'])[:8]}"
        f"{named}\n"
        f"warnings: {' | '.join(result.warnings) or 'none'}\n"
        f"timings: {json.dumps(result.timings, sort_keys=True)}"
    )


def _rows(result: SearchResult, hits: list[Any]) -> list[dict[str, Any]]:
    return [hit.to_dict() for hit in hits]


# --- D: the three baselines side by side -------------------------------------


def _display_align(
    query: np.ndarray, candidate: np.ndarray, *, allow_mirror: bool
) -> tuple[np.ndarray, Any]:
    """Rotate a coarse-only candidate onto the query **for drawing only**.

    Without this, an ``hu`` or ``fourier`` panel would differ from a
    ``fourier_align`` panel by the candidate's stored rotation as well as by its
    shape, and the reader would score that rotation difference as a worse match.
    The ranking and the printed distances are untouched — only the polygon drawn
    is moved, and the caption says so.
    """
    from earthglyph.retrieval.alignment import align, apply_alignment

    best = align(query, candidate, allow_mirror=allow_mirror)
    moved = apply_alignment(
        candidate, shift=best.shift, theta_rad=best.theta_rad, mirrored=best.mirrored
    )
    return moved, best


def baseline_comparison_figure(
    results: dict[str, SearchResult],
    out_path: Path,
    *,
    query_label: str,
    index: IndexHandle | None = None,
    allow_mirror: bool = False,
    top: int = 5,
) -> dict[str, Any]:
    """Rows = methods, columns = that method's own ranking. Rank labels are printed,
    so a disagreement between methods is visible instead of being averaged away.

    A coarse-only row (``hu``, ``fourier``) returns no transform, so its candidate is
    drawn after a display-only alignment (:func:`_display_align`); every such panel
    is labelled ``display-aligned`` and its distance stays the method's own. Without
    that the rows would differ by stored rotation, not by shape.
    """
    plt = _figure_backend()
    methods = list(results)
    fig, axes = plt.subplots(
        len(methods), top + 1, figsize=(2.3 * (top + 1), 2.5 * len(methods)), squeeze=False
    )
    query = np.asarray(results[methods[0]].query["query_contour"]["points"], dtype=float)
    grid: dict[str, list[dict[str, Any]]] = {}
    for row, method in enumerate(methods):
        result = results[method]
        draw_pair(axes[row][0], query, None, fill_query=True)
        axes[row][0].set_title(
            _caption_lines(f"QUERY\n{method} → same sketch", 22), fontsize=6.0
        )
        slots = []
        for slot, hit in enumerate(result.hits[:top]):
            ax = axes[row][slot + 1]
            moved, drawn = _candidate_contour(
                result, hit, index, display_align=True, allow_mirror=allow_mirror, query=query
            )
            draw_pair(ax, query, moved)
            ax.set_title(_caption_lines(_hit_caption(result, hit) + drawn, 24), fontsize=5.2)
            slots.append(
                {
                    "rank": hit.rank,
                    "row": hit.row,
                    "candidate_id": hit.candidate_id,
                    "subtype": hit.subtype,
                    "lon": hit.lon,
                    "lat": hit.lat,
                    "coarse_distance": hit.coarse_distance,
                    "align_distance": hit.align_distance,
                    "chamfer": hit.chamfer,
                }
            )
        grid[method] = slots
    fig.suptitle(
        f"D — three baselines on one query (“{query_label}”), same library, same "
        "standardization; overlays are the comparison, not the numbers",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"figure": out_path.name, "query": query_label, "methods": grid}


def _write_note(fig: Any, note: dict[str, Any]) -> None:
    text = "\n".join(f"{key}: {value}" for key, value in note.items())
    fig.text(0.01, 0.012, _caption_lines(text, 170), fontsize=5.4, family="monospace")


# --- CLI ---------------------------------------------------------------------


def collect_queries(queries_dir: Path) -> list[tuple[str, Path]]:
    """Every .json / .png under the queries directory, in name order.

    ``manifest.json`` is the generator's own record, not a query, so it is skipped;
    a label keeps its file suffix so ``drawn-crescent.json`` and
    ``drawn-crescent.png`` (the same shape through two input paths) get separate
    figures instead of overwriting each other.
    """
    files = sorted(
        path
        for path in queries_dir.iterdir()
        if path.suffix.lower() in {".json", ".png", ".jpg", ".jpeg"}
        and path.name != "manifest.json"
    )
    return [(f"{path.stem}_{path.suffix.lstrip('.').lower()}", path) for path in files]


def _matches(label: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(label, pattern) for pattern in patterns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--smoothing", default="light")
    parser.add_argument("--allow-mirror", action="store_true")
    parser.add_argument(
        "--allow-self-intersection",
        action="store_true",
        help="match self-crossing strokes as closed curves (the search default refuses them)",
    )
    parser.add_argument("--methods", nargs="*", default=list(BASELINE_METHODS))
    parser.add_argument("--limit", type=int, default=0, help="only the first N inputs")
    parser.add_argument(
        "--extract-rows",
        type=int,
        default=12,
        help="accepted inputs drawn in figure A (every refused one is always drawn)",
    )
    parser.add_argument(
        "--figures-for",
        nargs="*",
        default=["drawn-*"],
        help="labels getting a Top-K and a baseline figure",
    )
    args = parser.parse_args(argv)

    index = load_index_handle(args.index)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    queries = collect_queries(args.queries)
    if args.limit:
        queries = queries[: args.limit]
    if not queries:
        print(f"no .json/.png queries under {args.queries}", file=sys.stderr)
        return 2

    records = extraction_records(
        [path for _, path in queries],
        index=index,
        smoothing=args.smoothing,
        names=[name for name, _ in queries],
        allow_self_intersection=args.allow_self_intersection,
    )
    by_label = {str(row["query"]): row for row in records}
    accepted = [str(row["query"]) for row in records if "refused" not in row]
    refused = [str(row["query"]) for row in records if "refused" in row]

    # Refused inputs always get a panel (that refusal is a result). Among the
    # accepted ones, the labels the figures are for come first, so the hand-drawing
    # queries are the ones a reader sees rather than whatever sorts alphabetically.
    figure_rows = refused + [
        label for label in accepted if _matches(label, args.figures_for)
    ] + [label for label in accepted if not _matches(label, args.figures_for)]
    figure_rows = figure_rows[: len(refused) + args.extract_rows]
    paths = dict(queries)
    manifest: dict[str, Any] = {
        "index": index.provenance(),
        "params": {
            "smoothing": args.smoothing,
            "top": args.top,
            "allow_mirror": args.allow_mirror,
            "allow_self_intersection": args.allow_self_intersection,
            "methods": args.methods,
            "figures_for": args.figures_for,
            "n_inputs": len(queries),
            "n_refused": len(refused),
        },
    }
    manifest["extraction"] = extraction_figure(
        [(label, paths[label]) for label in figure_rows],
        out / "extraction_examples.png",
        index=index,
        smoothing=args.smoothing,
        allow_self_intersection=args.allow_self_intersection,
        extra={
            "inputs": len(queries),
            "accepted": len(accepted),
            "refused": refused,
            "note": "refused rows show the tool's own refusal; metrics for every "
            "input are in figures.json",
        },
    )
    manifest["extraction_all"] = records

    manifest["queries"] = [
        {"query": label, "path": paths[label].as_posix(), "refused": by_label[label]["refused"]}
        for label in refused
    ]
    for label in refused:
        print(f"refused {label}: {by_label[label]['refused']}")

    for name in [label for label in accepted if _matches(label, args.figures_for)]:
        path = paths[name]
        try:
            result = search_by_contour(
                path,
                index,
                top_k=args.top,
                smoothing=args.smoothing,
                allow_mirror=args.allow_mirror,
                allow_self_intersection=args.allow_self_intersection,
                chamfer_top=min(10, args.top),
            )
        except (SketchError, SearchError) as error:
            # The extraction table already applied this same policy, so reaching
            # here means a later stage refused; record it instead of losing the run.
            manifest["queries"].append(
                {"query": name, "path": path.as_posix(), "refused": str(error)}
            )
            print(f"refused {name}: {error}")
            continue
        single = topk_figure(
            result,
            out / f"topk_{name}.png",
            query_label=name,
            index=index,
            allow_mirror=args.allow_mirror,
            top=args.top,
        )
        compared = baseline_comparison_figure(
            {
                method: search_by_contour(
                    path,
                    index,
                    top_k=max(5, min(args.top, 10)),
                    method=method,
                    smoothing=args.smoothing,
                    allow_mirror=args.allow_mirror,
                    allow_self_intersection=args.allow_self_intersection,
                )
                for method in args.methods
            },
            out / f"baselines_{name}.png",
            query_label=name,
            index=index,
            allow_mirror=args.allow_mirror,
            top=5,
        )
        manifest["queries"].append(
            {
                "query": name,
                "path": path.as_posix(),
                "extraction": by_label[name],
                "topk": single,
                "baselines": compared,
                "warnings": result.warnings,
                "provenance": result.provenance,
            }
        )
        print(f"wrote {out / f'topk_{name}.png'} and {out / f'baselines_{name}.png'}")
    (out / "figures.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")
    print(f"wrote {out / 'extraction_examples.png'} and {out / 'figures.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
