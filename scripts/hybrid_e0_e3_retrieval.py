#!/usr/bin/env python3
"""D3 — does an E0 coarse filter plus a Shape Context re-rank keep Full-E3's answers?

Checkpoint C measured Full-E3 at about 8 s per query on T2 and about 40 s per query on
T3, and E0 at a fraction of a second.  The obvious engineering answer is a two-stage
retriever: E0 decides *which* candidates reach E3, E3 decides *their* order.  This
script tests that answer instead of assuming it, and the brief sets the rules:

* **E0 contributes no score.**  Inside the window the final order is sorted by the E3
  distance alone; E0's distance is used only to choose the window and is reported as
  evidence afterwards.  E3 ties break by library row index, the same rule Checkpoint C
  published (D42), so E0's order cannot leak in through a tie either.
* **every arm gets the same E3.**  Same ``orientation="sample"``, same 24-step grid,
  same descriptor parameters as C, whether the candidate set is the whole library or a
  20-row window.  Nothing about E3's mathematics is bent to make the hybrid fast; the
  only change is how many candidates it is asked about.
* **the precompute is charged, not hidden.**  The hybrid scores from cached candidate
  descriptors; the build time and the byte count are recorded next to the query time,
  and the cached path is checked against the uncached one on the whole library so a
  speed claim cannot rest on a different algorithm.
* **what the filter throws away is itemised.**  Whenever a candidate Full-E3 put in its
  Top-10 never reaches the re-ranker, its id, its E0 rank and the query go into a
  ledger.  A coverage number without that list would hide which queries fail.

Arms compared, per setting (T2 and T3):

``E0_only``     the coarse stage alone — what the pipeline costs if E3 is skipped.
``Full-E3``     the reference: E3 over every row, the ranking C published.
``E0->E3@W``    E3 over E0's top-W rows (W = 20, 50, 100, 200); the tail keeps E0 order.

Quality is measured twice, because the two numbers answer different questions:
*agreement with Full-E3* says what the cut costs in answers, *labels.json metrics*
(NDCG@10 / MRR / hit@k, imported from the C runner so the definitions cannot drift)
say whether either ranking is any good at all.

T3 needs a caveat that is easy to lose: the 839 real landforms carry **no relevance
labels**, so a landform in the Top-10 is not a false positive.  For T3 the labelled
numbers mean "did the labelled variants keep their place once landforms joined the
library", and nothing more.

This is an experimental retriever living in ``scripts/``.  The production default
(``earthglyph.retrieval.search.search_by_contour``, E0 with ``shortlist_size=200``) is
not touched by this file; whether to adopt a two-stage design is the user's call after
reading the numbers.

Usage::

    python scripts/hybrid_e0_e3_retrieval.py                    # both settings
    python scripts/hybrid_e0_e3_retrieval.py --settings T2      # one setting
    python scripts/hybrid_e0_e3_retrieval.py --windows 50,200   # subset of windows
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from diagnose_perceptual_arms import Frozen  # noqa: E402
from evaluate_perceptual_methods import (  # noqa: E402
    GALLERY_LABEL,
    INDEX_LABEL,
    metric_block,
)
from visualize_retrieval import _figure_backend  # noqa: E402  (selects the Agg backend)

from earthglyph.retrieval import perceptual_methods  # noqa: E402
from earthglyph.retrieval import shape_context_baseline as e3  # noqa: E402

WINDOWS = (20, 50, 100, 200)
K_AGREEMENT = 10
K_REPORT = 10
E3_ORIENTATION = "sample"
QUERY_TINT = "#a4262c"
GALLERY_TINT = "#2f3b4c"
BAD = "#a4262c"


# ---------------------------------------------------------------- the three passes


def e0_pass(frozen: Frozen, setting: str, query_id: str) -> dict[str, Any]:
    """E0 over the whole library.  The cut is decided here and nowhere else."""
    library = frozen.library(setting, query_id)
    query = np.asarray(frozen.item(query_id), dtype=float)
    started = time.perf_counter()
    distances, _ = perceptual_methods.score_library(
        "E0", query, list(library.rows), allow_mirror=False, params={}
    )
    elapsed = time.perf_counter() - started
    order = perceptual_methods.rank_order(distances)
    return {
        "library": library,
        "query": query,
        "distances": distances,
        "rows_in_e0_order": [int(index) for index in order],
        "ids": [library.ids[int(index)] for index in order],
        "seconds": elapsed,
    }


def full_e3_pass(base: dict[str, Any]) -> dict[str, Any]:
    """E3 over every row, through the call shape Checkpoint C used — the reference."""
    library = base["library"]
    started = time.perf_counter()
    distances, info = e3.score_library(
        base["query"], library.rows, allow_mirror=False, orientation=E3_ORIENTATION
    )
    elapsed = time.perf_counter() - started
    order = perceptual_methods.rank_order(distances)
    return {
        "distances": distances,
        "ids": [library.ids[int(index)] for index in order],
        "rows_in_e3_order": [int(index) for index in order],
        "seconds": elapsed,
        "seconds_per_candidate": info["seconds_per_candidate"],
    }


def hybrid_pass(base: dict[str, Any], window: int, cache: Any) -> dict[str, Any]:
    """Re-rank E0's top-``window`` rows with E3; the tail keeps E0's order.

    The shortlist is sorted back into library row order before it is scored, so a tie
    inside the window breaks by row index exactly as it does in the full-library call —
    sorting the shortlist by E0 rank instead would smuggle E0 back in through ties.
    The cache is built once per query by the caller and shared by every window, which
    is why each window arm is charged the whole build: a deployed service would pay it
    once for the one window it actually uses.
    """
    library = base["library"]
    shortlist = sorted(base["rows_in_e0_order"][: min(window, len(library))])
    inside_set = set(shortlist)
    started = time.perf_counter()
    distances, _ = e3.score_library_from_descriptors(
        base["query"], cache, shortlist, orientation=E3_ORIENTATION
    )
    rerank_seconds = time.perf_counter() - started
    inside = [shortlist[int(position)] for position in perceptual_methods.rank_order(distances)]
    tail = [row for row in base["rows_in_e0_order"] if row not in inside_set]
    ranked_rows = inside + tail
    return {
        "window": len(shortlist),
        "ids": [library.ids[row] for row in ranked_rows],
        "distance_by_row": dict(zip(shortlist, [float(value) for value in distances], strict=True)),
        "ids_in_window": {library.ids[row] for row in shortlist},
        "rerank_seconds": rerank_seconds,
    }


def cache_equivalence(base: dict[str, Any], full: dict[str, Any], cache: Any) -> dict[str, Any]:
    """Cached full-library scoring must reproduce the uncached distances exactly.

    If ``identical`` is false the cache is a different algorithm and every hybrid
    number in this file is void, so the check runs before anything is reported.
    """
    distances, _ = e3.score_library_from_descriptors(
        base["query"], cache, orientation=E3_ORIENTATION
    )
    difference = np.abs(distances - full["distances"])
    order_cached = perceptual_methods.rank_order(distances)
    return {
        "rows": int(distances.shape[0]),
        "max_abs_difference": float(difference.max()),
        "identical": bool(np.array_equal(distances, full["distances"])),
        "rankings_identical": bool(
            [int(index) for index in order_cached] == full["rows_in_e3_order"]
        ),
    }


def uncached_window_cost(base: dict[str, Any], window: int) -> dict[str, float]:
    """What the same window costs without a descriptor cache, measured not estimated."""
    library = base["library"]
    shortlist = sorted(base["rows_in_e0_order"][: min(window, len(library))])
    rows = [library.rows[row] for row in shortlist]
    started = time.perf_counter()
    e3.score_library(base["query"], rows, allow_mirror=False, orientation=E3_ORIENTATION)
    return {
        "uncached_seconds": time.perf_counter() - started,
        "rows": float(len(shortlist)),
    }


# ---------------------------------------------------------------- metrics


def label_metrics(
    frozen: Frozen, query_id: str, ids: list[str], n_library: int
) -> dict[str, float]:
    """C's own metric block, over the arm's full ranking."""
    relevant = frozen.relevant(query_id)
    vector = np.array([1.0 if item in relevant else 0.0 for item in ids], dtype=float)
    return metric_block(vector, len(relevant), n_library)


def agreement(hybrid_ids: list[str], reference_ids: list[str], k: int) -> float:
    """Share of the reference Top-k that the arm also puts in its own Top-k."""
    reference = reference_ids[:k]
    shown = set(hybrid_ids[:k])
    return sum(1 for item in reference if item in shown) / float(len(reference))


def arm_names(windows: tuple[int, ...]) -> list[str]:
    return ["E0_only", "Full-E3"] + [f"E0->E3@{window}" for window in windows]


def run_query(
    frozen: Frozen, setting: str, query_id: str, windows: tuple[int, ...]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Every arm for one query, plus the ledger rows the cut produced."""
    base = e0_pass(frozen, setting, query_id)
    full = full_e3_pass(base)
    library = base["library"]
    n_library = len(library)
    relevant = frozen.relevant(query_id)
    e0_rank = {item: rank + 1 for rank, item in enumerate(base["ids"])}
    e3_rank = {item: rank + 1 for rank, item in enumerate(full["ids"])}

    arms: dict[str, dict[str, Any]] = {}
    arms["E0_only"] = {
        "window": 0,
        "ids": base["ids"],
        "distance_by_id": {
            item: float(base["distances"][row]) for row, item in enumerate(library.ids)
        },
        "kind": "e0_alignment",
        "seconds": base["seconds"],
        "coarse_seconds": base["seconds"],
        "rerank_seconds": 0.0,
    }
    arms["Full-E3"] = {
        "window": n_library,
        "ids": full["ids"],
        "distance_by_id": {
            item: float(full["distances"][row]) for row, item in enumerate(library.ids)
        },
        "kind": "e3_shape_context",
        "seconds": full["seconds"],
        "coarse_seconds": base["seconds"],
        "rerank_seconds": full["seconds"],
    }

    ledger: list[dict[str, Any]] = []
    cache_started = time.perf_counter()
    cache = e3.build_library_descriptors(list(library.rows), orientation=E3_ORIENTATION)
    cache_seconds = time.perf_counter() - cache_started
    for window in windows:
        hybrid = hybrid_pass(base, window, cache)
        name = f"E0->E3@{window}"
        arms[name] = {
            "window": hybrid["window"],
            "requested_window": window,
            "ids": hybrid["ids"],
            "distance_by_id": {
                library.ids[row]: value for row, value in hybrid["distance_by_row"].items()
            },
            "kind": "e3_shape_context",
            "seconds": cache_seconds + hybrid["rerank_seconds"],
            "coarse_seconds": base["seconds"],
            "rerank_seconds": hybrid["rerank_seconds"],
            "cache_build_seconds": cache_seconds,
            "cache_reported_build_seconds": cache.build_seconds,
            "cache_bytes": cache.bytes_used,
            "cache_rows": cache.rows,
        }
        missing = [
            item
            for item in full["ids"][:K_AGREEMENT]
            if item not in hybrid["ids_in_window"]
        ]
        arms[name]["top10_reached_e3"] = K_AGREEMENT - len(missing)
        for item in missing:
            meta = frozen.meta(item)
            ledger.append(
                {
                    "setting": setting,
                    "query_id": query_id,
                    "window": window,
                    "candidate_id": item,
                    "e0_rank": e0_rank.get(item),
                    "full_e3_rank": e3_rank.get(item),
                    "family": meta["family"],
                    "role": meta["role"],
                    "relevant": item in relevant,
                    "provenance": GALLERY_LABEL if item in frozen.gallery else INDEX_LABEL,
                    "note": meta["note"],
                }
            )
    cache_block = {
        "rows": cache.rows,
        "bytes": cache.bytes_used,
        "build_seconds": cache_seconds,
        "build_seconds_reported": cache.build_seconds,
        "equivalence": cache_equivalence(base, full, cache),
    }

    for name in arm_names(windows):
        arm = arms[name]
        arm["metrics"] = label_metrics(frozen, query_id, arm["ids"], n_library)
        arm["agreement_top5"] = agreement(arm["ids"], full["ids"], 5)
        arm["agreement_top10"] = agreement(arm["ids"], full["ids"], K_AGREEMENT)
    return (
        {
            "query_id": query_id,
            "family": frozen.meta(query_id)["family"],
            "library_rows": n_library,
            "n_relevant": len(relevant),
            "cache": cache_block,
            "arms": arms,
        },
        ledger,
    )


def run_setting(
    frozen: Frozen, setting: str, windows: tuple[int, ...], probe_window: int
) -> dict[str, Any]:
    """One setting end to end: every query, every arm, plus the two integrity probes."""
    queries: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    equivalence: dict[str, Any] = {}
    cache_cost: dict[str, Any] = {}
    for position, query_id in enumerate(frozen.queries):
        entry, rows = run_query(frozen, setting, query_id, windows)
        queries.append(entry)
        ledger.extend(rows)
        if position == 0:
            probe_name = f"E0->E3@{probe_window}"
            if probe_name not in entry["arms"]:
                probe_name = f"E0->E3@{windows[-1]}"
            arm = entry["arms"][probe_name]
            base = e0_pass(frozen, setting, query_id)
            uncached = uncached_window_cost(base, int(arm["window"]))
            equivalence = {"query_id": query_id, **entry["cache"]["equivalence"]}
            cache_cost = {
                "query_id": query_id,
                "window_probe": arm["window"],
                "cached_rerank_seconds": arm["rerank_seconds"],
                "cache_build_seconds": entry["cache"]["build_seconds"],
                **uncached,
                "cache_bytes": entry["cache"]["bytes"],
                "cache_rows": entry["cache"]["rows"],
                "seconds_saved_by_caching": uncached["uncached_seconds"] - arm["rerank_seconds"],
            }
    return {
        "setting": setting,
        "queries": queries,
        "excluded_ledger": ledger,
        "cache_equivalence": equivalence,
        "cache_cost_probe": cache_cost,
    }


def summarise(bundle: dict[str, Any], windows: tuple[int, ...]) -> dict[str, Any]:
    """Average every per-query number, and keep the per-query values beside it."""
    out: dict[str, Any] = {}
    for name in arm_names(windows):
        entries = bundle["queries"]
        blocks = [entry["arms"][name]["metrics"] for entry in entries]
        means = {
            key: float(np.mean([block[key] for block in blocks]))
            for key in blocks[0]
            if key != "library_rows"
        }
        arm: dict[str, Any] = {
            "n_queries": len(entries),
            "labels": means,
            "agreement_with_full_e3": {
                "top5": float(np.mean([e["arms"][name]["agreement_top5"] for e in entries])),
                "top10": float(
                    np.mean([e["arms"][name]["agreement_top10"] for e in entries])
                ),
            },
            "seconds": {
                "mean_query": float(np.mean([e["arms"][name]["seconds"] for e in entries])),
                "total_pass": float(sum(e["arms"][name]["seconds"] for e in entries)),
                "coarse_e0_mean": float(
                    np.mean([e["arms"][name]["coarse_seconds"] for e in entries])
                ),
                "rerank_e3_mean": float(
                    np.mean([e["arms"][name]["rerank_seconds"] for e in entries])
                ),
            },
            "per_query": {
                "query_id": [entry["query_id"] for entry in entries],
                "ndcg_at_10": [round(block["ndcg@10"], 6) for block in blocks],
                "mrr": [round(block["mrr"], 6) for block in blocks],
                "first_relevant_rank": [block["first_relevant_rank"] for block in blocks],
                "agreement_top10": [e["arms"][name]["agreement_top10"] for e in entries],
                "query_seconds": [round(e["arms"][name]["seconds"], 4) for e in entries],
            },
        }
        if name.startswith("E0->E3@"):
            arm["seconds"]["cache_build_mean"] = float(
                np.mean([e["arms"][name]["cache_build_seconds"] for e in entries])
            )
            arm["cache"] = {
                "bytes_per_query": int(entries[0]["arms"][name]["cache_bytes"]),
                "rows_cached": int(entries[0]["arms"][name]["cache_rows"]),
                "precompute_charged_in_query_time": True,
                "why_per_query": (
                    "labels.json froze a different library for each query (T2 removes "
                    "that query's target and its declared near-duplicates), so a cache "
                    "keyed by library is rebuilt once per query here"
                ),
            }
            arm["window"] = {
                "requested": int(entries[0]["arms"][name]["requested_window"]),
                "actually_scored": int(entries[0]["arms"][name]["window"]),
                "note": (
                    "the window is capped at the library size; on T2 a 200-wide window "
                    "scores every row, so it is Full-E3 with extra steps"
                ),
            }
            arm["top10_reached_e3_mean"] = float(
                np.mean([e["arms"][name]["top10_reached_e3"] for e in entries])
            )
        out[name] = arm
    return out


# ---------------------------------------------------------------- outputs


def csv_rows(bundle: dict[str, Any], windows: tuple[int, ...]) -> list[dict[str, Any]]:
    """One row per (query, arm, rank) for the reported Top-10."""
    frozen_families = bundle["_families"]
    frozen_roles = bundle["_roles"]
    relevant_by_query = bundle["_relevant"]
    out: list[dict[str, Any]] = []
    for entry in bundle["queries"]:
        arms = entry["arms"]
        e0_rank = {item: rank + 1 for rank, item in enumerate(arms["E0_only"]["ids"])}
        e3_rank = {item: rank + 1 for rank, item in enumerate(arms["Full-E3"]["ids"])}
        for name in arm_names(windows):
            arm = arms[name]
            for rank, item in enumerate(arm["ids"][:K_REPORT], start=1):
                distance = arm["distance_by_id"].get(item)
                out.append(
                    {
                        "setting": bundle["setting"],
                        "query_id": entry["query_id"],
                        "query_family": entry["family"],
                        "arm": name,
                        "window": arm["window"],
                        "rank": rank,
                        "candidate_id": item,
                        "distance": "" if distance is None else f"{distance:.9g}",
                        "distance_kind": arm["kind"],
                        "scored_by_e3": int(distance is not None),
                        "e0_rank": e0_rank.get(item, ""),
                        "full_e3_rank": e3_rank.get(item, ""),
                        "family": frozen_families.get(item, ""),
                        "role": frozen_roles.get(item, ""),
                        "relevant": int(item in relevant_by_query[entry["query_id"]]),
                        "in_full_e3_top10": int(item in arms["Full-E3"]["ids"][:10]),
                    }
                )
    return out


# ---------------------------------------------------------------- figure


def draw_window_comparison(
    bundles: dict[str, dict[str, Any]], windows: tuple[int, ...], out: Path, dpi: int
) -> dict[str, Any]:
    """Quality and cost against the window, one panel per question."""
    plt = _figure_backend()
    names = [f"E0->E3@{window}" for window in windows]
    tints = {"T2": GALLERY_TINT, "T3": BAD}

    def values(setting: str, getter: Any) -> list[float]:
        summary = bundles[setting]["_summary"]
        return [float(getter(summary, name)) for name in names]

    panels = (
        (
            "Top-10 agreement with Full-E3",
            "share of Full-E3's Top-10 the hybrid also shows in its Top-10",
            lambda summary, name: summary[name]["agreement_with_full_e3"]["top10"],
            0.0,
            1.05,
            False,
        ),
        (
            "labelled NDCG@10",
            "read against labels.json, not against Full-E3",
            lambda summary, name: summary[name]["labels"]["ndcg@10"],
            0.0,
            1.05,
            False,
        ),
        (
            "mean query seconds (log)",
            "E0 coarse + E3 re-rank, descriptor-cache build included",
            lambda summary, name: summary[name]["seconds"]["mean_query"],
            0.005,
            200.0,
            True,
        ),
        (
            "Full-E3 Top-10 never reaching E3",
            "how many of the reference's Top-10 the cut throws away",
            lambda summary, name: (
                10.0 - summary[name]["top10_reached_e3_mean"]
                if name.startswith("E0->E3@")
                else None
            ),
            0.0,
            10.0,
            False,
        ),
    )
    fig, axes = plt.subplots(1, len(panels), figsize=(4.7 * len(panels), 4.2))
    for axis, (title, caption, getter, lo, hi, log_x) in zip(axes, panels, strict=True):
        for setting in bundles:
            summary = bundles[setting]["_summary"]
            axis.plot(
                list(windows),
                values(setting, getter),
                marker="o",
                color=tints[setting],
                label=f"{setting} hybrid",
            )
            for reference_name, style, alpha in (("Full-E3", ":", 0.7), ("E0_only", "--", 0.3)):
                reference = getter(summary, reference_name)
                if reference is not None:
                    axis.axhline(
                        float(reference), linestyle=style, color=tints[setting], alpha=alpha
                    )
        axis.set_xscale("log" if log_x else "linear")
        if log_x:
            # the windows are the only ticks that mean anything here; log minor ticks
            # would otherwise print "3 x 10^1" between 20 and 50.
            axis.xaxis.set_minor_formatter(plt.NullFormatter())
        axis.set_ylim(lo, hi)
        axis.set_xticks(list(windows))
        axis.set_xticklabels([str(window) for window in windows])
        axis.set_xlabel("E0 window (candidates reaching E3)")
        axis.set_title(title, fontsize=10)
        axis.grid(alpha=0.25)
        axis.set_ylabel(caption, fontsize=7.5)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower right", fontsize=8, ncol=len(bundles))
    fig.suptitle(
        "Checkpoint D3 - E0 coarse + E3 re-rank: what the cut costs, per window",
        fontsize=12,
    )
    fig.text(
        0.5,
        0.02,
        "E3's parameters and rotation grid are identical in every arm; E0 only chooses "
        "which candidates E3 is asked about and contributes no score to the ordering "
        "(ties break by library row index, the rule Checkpoint C published).  Dotted "
        "lines are the Full-E3 value of the same metric, dashed lines the E0-only value.  "
        "Query seconds include the descriptor-cache build, which is per query here because "
        "labels.json froze a different library for each query.",
        ha="center",
        fontsize=7.5,
    )
    fig.tight_layout(rect=(0.0, 0.085, 1.0, 0.93))
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out),
        "panels": [panel[0] for panel in panels],
        "windows": list(windows),
        "settings": list(bundles),
    }


# ---------------------------------------------------------------- main


def redraw_from_json(metrics_path: Path, out_dir: Path, dpi: int) -> dict[str, Any]:
    """Re-render only the window figure from an archived metrics file.

    The figure is a pure function of the per-setting ``summary`` blocks, so a cosmetic
    fix to it does not need the 38-minute experiment re-run — but the CSV and JSON must
    not be touched by this path, and the windows come from the file rather than the
    command line so the two cannot drift apart.
    """
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    bundles = {
        setting: {"_summary": block["summary"]}
        for setting, block in payload["settings"].items()
    }
    first = next(iter(bundles.values()))["_summary"]
    windows = tuple(
        sorted(int(name.split("@")[1]) for name in first if name.startswith("E0->E3@"))
    )
    out = out_dir / "hybrid_window_comparison.png"
    return draw_window_comparison(bundles, windows, out, dpi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gallery-dir", type=Path, default=Path("experiments/p0_perceptual_probe")
    )
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("experiments/p0_perceptual_probe/checkpoint_d")
    )
    parser.add_argument("--settings", default="T2,T3")
    parser.add_argument("--windows", default="20,50,100,200")
    parser.add_argument("--probe-window", type=int, default=200)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument(
        "--redraw-from-json",
        type=Path,
        default=None,
        help="re-render only hybrid_window_comparison.png from an archived metrics file",
    )
    args = parser.parse_args(argv)

    settings = [item.strip() for item in args.settings.split(",") if item.strip()]
    windows = tuple(int(item) for item in args.windows.split(",") if item.strip())
    if list(windows) != sorted(windows):
        raise SystemExit("--windows must be ascending; the tail depends on it")
    if args.redraw_from_json is not None:
        info = redraw_from_json(args.redraw_from_json, args.out_dir, args.dpi)
        print(f"redrew {info['path']} from {args.redraw_from_json} (no scoring re-run)")
        return 0
    results_path = args.results or (args.gallery_dir / "results.csv")
    frozen = Frozen(args.gallery_dir, args.index, results_path)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"hybrid E0->E3 over {len(frozen.queries)} queries, settings {settings}")
    print(
        f"windows {list(windows)}, E3 orientation {E3_ORIENTATION!r}, "
        f"rotation steps {e3.ROTATION_STEPS}"
    )
    started = time.perf_counter()
    bundles: dict[str, dict[str, Any]] = {}
    for setting in settings:
        bundle = run_setting(frozen, setting, windows, args.probe_window)
        bundle["_summary"] = summarise(bundle, windows)
        seen = {
            item
            for entry in bundle["queries"]
            for arm in entry["arms"].values()
            for item in arm["ids"][:K_REPORT]
        }
        bundle["_families"] = {item: frozen.meta(item)["family"] for item in seen}
        bundle["_roles"] = {item: frozen.meta(item)["role"] for item in seen}
        bundle["_relevant"] = {
            entry["query_id"]: frozen.relevant(entry["query_id"])
            for entry in bundle["queries"]
        }
        bundles[setting] = bundle
        print(f"[{setting}] done at {time.perf_counter() - started:.1f} s")
        _print_summary(setting, bundle, windows)

    payload = {
        "checkpoint": "D3",
        "question": (
            "does an E0 coarse filter plus Shape Context re-ranking keep the answers "
            "Full-E3 would have found, and what does it cost"
        ),
        "produced_by": "scripts/hybrid_e0_e3_retrieval.py",
        "inputs": {
            "gallery_dir": str(args.gallery_dir),
            "index": str(args.index),
            "results_csv": str(results_path),
            "queries": len(frozen.queries),
            "query_ids": list(frozen.queries),
        },
        "contract": {
            "e0_contributes_no_score": (
                "inside the window the order is sorted by E3 distance alone and ties "
                "break by library row index, so E0 decides membership only"
            ),
            "e3_identical_across_arms": {
                "orientation": E3_ORIENTATION,
                "rotation_steps": e3.ROTATION_STEPS,
                "params": e3.resolve_params(orientation=E3_ORIENTATION).as_dict(),
            },
            "precompute_charged": (
                "the descriptor cache is rebuilt per query because labels.json froze a "
                "per-query library; its build seconds and bytes appear in every hybrid "
                "arm's timing block instead of being subtracted from the claim"
            ),
            "production_default_unchanged": True,
            "this_is_a_new_experiment_not_a_re_report": (
                "distances are recomputed here; C's results.csv is read only to locate "
                "the frozen library and labels, and C's own files are not written to"
            ),
        },
        "cache_equivalence": {
            setting: bundle["cache_equivalence"] for setting, bundle in bundles.items()
        },
        "cache_cost_probe": {
            setting: bundle["cache_cost_probe"] for setting, bundle in bundles.items()
        },
        "wall_seconds": round(time.perf_counter() - started, 3),
        "settings": {
            setting: {
                "library_rows": {
                    entry["query_id"]: entry["library_rows"] for entry in bundle["queries"]
                },
                "n_relevant": {
                    entry["query_id"]: entry["n_relevant"] for entry in bundle["queries"]
                },
                "summary": bundle["_summary"],
                "excluded_ledger": bundle["excluded_ledger"],
            }
            for setting, bundle in bundles.items()
        },
    }
    metrics_path = out_dir / "hybrid_retrieval_metrics.json"
    metrics_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {metrics_path}")

    rows: list[dict[str, Any]] = []
    for bundle in bundles.values():
        rows.extend(csv_rows(bundle, windows))
    csv_path = out_dir / "hybrid_retrieval_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} rows)")

    figure_path = out_dir / "hybrid_window_comparison.png"
    figure = draw_window_comparison(bundles, windows, figure_path, args.dpi)
    print(f"wrote {figure['path']}")
    return 0


def _print_summary(setting: str, bundle: dict[str, Any], windows: tuple[int, ...]) -> None:
    summary = bundle["_summary"]
    print(f"\n== {setting} ==")
    print(
        f"{'arm':<14}{'ndcg@10':>9}{'mrr':>8}{'agr5':>7}{'agr10':>7}"
        f"{'sec/q':>9}{'coarse':>9}{'top10 in':>9}"
    )
    for name in arm_names(windows):
        arm = summary[name]
        reached = (
            f"{arm['top10_reached_e3_mean']:>8.2f}"
            if name.startswith("E0->E3@")
            else f"{'-':>8}"
        )
        print(
            f"{name:<14}{arm['labels']['ndcg@10']:>9.3f}{arm['labels']['mrr']:>8.3f}"
            f"{arm['agreement_with_full_e3']['top5']:>7.3f}"
            f"{arm['agreement_with_full_e3']['top10']:>7.3f}"
            f"{arm['seconds']['mean_query']:>9.3f}{arm['seconds']['coarse_e0_mean']:>9.4f}"
            f"{reached:>9}"
        )
    print(f"Full-E3 Top-10 candidates excluded by the cut: {len(bundle['excluded_ledger'])} rows")
    print(f"cache equivalence: {bundle['cache_equivalence']}")
    print(f"cache cost probe:  {bundle['cache_cost_probe']}")


if __name__ == "__main__":
    raise SystemExit(main())
