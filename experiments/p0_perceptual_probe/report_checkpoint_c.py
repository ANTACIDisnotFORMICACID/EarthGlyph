"""Print the Checkpoint C tables straight from the artefacts, so docs quote output not memory.

D48 says a number in a document must be reproducible by a command in the repo.  The metrics
live in ``metrics.json`` and the per-candidate ranking in ``results.csv``; this script turns
those two files into the exact text that goes into ``notes.md`` and ``docs/TASKS.md``, so a
hand-copied table can never drift from the run it came from.  It scores nothing — importing
it changes no measurement.

Usage::

    python experiments/p0_perceptual_probe/report_checkpoint_c.py            # all settings
    python experiments/p0_perceptual_probe/report_checkpoint_c.py --setting T2
    python experiments/p0_perceptual_probe/report_checkpoint_c.py --files     # artefact list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ARMS = ("E0", "E1", "E2", "E3")
KEYS = ("hit@1", "hit@3", "hit@5", "hit@10", "mrr", "ap", "ndcg@10")
SETTINGS = ("T1", "T2", "T3")

SETTING_MEANING = {
    "T1": "query's own family only (exact target present, excluded from the metrics)",
    "T2": "whole controlled gallery (exact target absent)",
    "T3": "gallery + 839 real landform rows (landforms carry no relevance label)",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_metrics(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _arms(table: dict[str, Any]) -> list[str]:
    return [arm for arm in ARMS if arm in table]


def aggregate_table(metrics: dict[str, Any], setting: str) -> str:
    table = metrics["results"][setting]
    lines = [
        f"setting {setting} - {SETTING_MEANING[setting]}",
        "arm   " + "".join(f"{key:>9}" for key in KEYS) + f"{'1stRel':>8}{'sec/q':>9}{'rows':>10}",
    ]
    for arm in _arms(table):
        block = table[arm]
        aggregate = block["aggregate"]
        rows = block["timing"]["n_scored_per_query"]
        span = f"{rows[0]}" if len(set(rows)) == 1 else f"{min(rows)}-{max(rows)}"
        lines.append(
            f"{arm:6}"
            + "".join(f"{aggregate[key]:>9.3f}" for key in KEYS)
            + f"{aggregate['first_relevant_rank']:>8.1f}"
            + f"{block['timing']['seconds_mean_per_query']:>9.2f}{span:>10}"
        )
    return "\n".join(lines)


def label_pressure_table(metrics: dict[str, Any], setting: str) -> str:
    """Who occupies the first k slots, counted by label rather than by score."""
    table = metrics["results"][setting]
    arms = _arms(table)
    n_queries = int(table[arms[0]]["aggregate"]["n_queries"])
    lines = [
        f"setting {setting} - what occupied the first k slots of those {n_queries} queries,"
        " counted by label, not by score",
        "arm   relevant  same-fam-neg  other-fam  landform  top1-lookalike  mean-indist",
    ]
    for arm in _arms(table):
        slots = table[arm]["top_slots"]
        pressure = table[arm]["label_pressure"]
        indistinguishable = pressure["mean_indistinguishable_rows_in_library"]
        lines.append(
            f"{arm:6}{slots['relevant_share']:>9.2f}"
            f"{slots['same_family_hard_negative_share']:>14.2f}"
            f"{slots['other_family_gallery_share']:>11.2f}"
            f"{slots['landform_share']:>10.2f}"
            f"{pressure['top1_is_label_lookalike_share']:>16.2f}"
            + (f"{indistinguishable:>14.1f}" if indistinguishable is not None else f"{'n/a':>14}")
        )
    return "\n".join(lines)


def top1_table(metrics: dict[str, Any], setting: str) -> str:
    """Every query's Top-1 per arm: the row a human eyeballs against the figures."""
    table = metrics["results"][setting]
    arms = _arms(table)
    per_query = {arm: table[arm]["per_query"] for arm in arms}
    query_ids = sorted(set().union(*(set(per_query[arm]) for arm in arms)))
    lines = [
        f"setting {setting} - Top-1 per arm (R=relevant, x=label lookalike, -=label miss)"
        + (
            "; in T1 the query's own row is skipped here, see exact_target_self_rank below"
            if setting == "T1"
            else ""
        )
    ]

    for query_id in query_ids:
        cells = []
        for arm in arms:
            record = per_query[arm].get(query_id)
            if record is None:
                cells.append(f"{arm} (not run)".ljust(27))
                continue
            mark = "-"
            if record["top1_relevant"]:
                mark = "R"
            elif record["top1_is_label_lookalike"]:
                mark = "x"
            cells.append(f"{arm} {mark} {record['top1_id']}".ljust(27))
        lines.append(f"{query_id:24}" + "".join(cells))
    return "\n".join(lines)


def invariance_block(metrics: dict[str, Any]) -> str:
    """T1 only: does the arm put the query's own contour first when it is in the library."""
    table = metrics["results"].get("T1", {})
    lines = [
        "T1 exact_target_self_rank - the query's own contour is a library row; rank 1 means"
        " the arm is invariant on its own input"
    ]
    for arm in _arms(table):
        block = table[arm]["exact_target_self_rank"]
        if block is None:
            lines.append(f"{arm:6} n/a")
            continue
        values = sorted(block["values"])
        lines.append(
            f"{arm:6} rank1 {sum(1 for rank in values if rank == 1):>2}/{len(values):<2}"
            f"  median {values[len(values) // 2]:>3}  max {block['max']:>3}"
        )
    return "\n".join(lines)


def protocol_block(metrics: dict[str, Any]) -> str:
    frozen = metrics["frozen_inputs"]
    lines = [
        "what this report reads, and the inputs it was computed from",
        f"command        {metrics['command']}",
        f"git_commit     {metrics['git_commit']}",
        f"wall_seconds   {metrics['wall_seconds']:.1f}",
        f"gallery_version  {frozen['gallery_version']}",
    ]
    for name, sha in sorted(frozen["sha256"].items()):
        lines.append(f"sha256 {name:20} {sha[:16]}")
    for name, sha in sorted((frozen.get("index_sha256") or {}).items()):
        lines.append(f"sha256 index/{name:14} {sha[:16]}")
    normalizer = metrics.get("e1_normalizer") or {}
    if normalizer:
        lines.append(
            f"e1 normalizer  {normalizer['method']} on {normalizer['n_samples']} rows x "
            f"{normalizer['n_descriptors']} descriptors, orientation="
            f"{normalizer['orientation']}, degenerate={len(normalizer['degenerate_descriptors'])}"
        )
        outside = normalizer.get("out_of_range_on_real_index")
        if outside:
            lines.append(
                f"e1 off-corpus  {outside['cells_outside_fitted_interval_share']:.3f} of "
                f"{outside['descriptor_cells']:,} descriptor cells leave the fitted interval"
                f" (worst {outside['worst_descriptor']})"
            )
    probe = metrics.get("e3_direction_probe")
    if probe:
        lines.append(
            f"e3 direction   {probe['direction_used_by_this_run'].split(':')[0]} fixed;"
            f" reverse order differs on {probe['n_pairs']} pairs: median "
            f"{probe['median_absolute_gap']:.4f} max {probe['max_absolute_gap']:.4f}"
        )
    cost = metrics.get("e3_orientation_cost")
    if cost:
        lines.append(
            "e3 cost        "
            + "  ".join(
                f"{setting}: {block['seconds_sample']:.2f}s sample /"
                f" {block['seconds_canonical']:.2f}s canonical"
                f" (x{block['ratio_sample_over_canonical']:.1f}, {block['library_rows']} rows)"
                for setting, block in sorted(cost.items())
            )
        )
    return "\n".join(lines)


def files_block(directory: Path) -> str:
    names = (
        "results.csv",
        "metrics.json",
        "method_status.json",
        "figures_manifest.json",
        "control_gallery.png",
    )
    lines = ["Checkpoint C artefacts in this directory"]
    for name in names:
        path = directory / name
        if not path.exists():
            lines.append(f"{name:36} MISSING - run the commands in notes.md first")
            continue
        lines.append(f"{name:36} {_sha256(path)[:16]}  {path.stat().st_size:>9,} B")
    figures = sorted(directory.glob("comparison_*.png")) + sorted(
        directory.glob("perception_comparison_overview_*.png")
    )
    for path in figures:
        lines.append(f"{path.name:36} {_sha256(path)[:16]}  {path.stat().st_size:>9,} B")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=HERE)
    parser.add_argument("--setting", default="all", choices=(*SETTINGS, "all"))
    parser.add_argument("--files", action="store_true", help="only the artefact list")
    parser.add_argument("--no-top1", action="store_true")
    args = parser.parse_args(argv)
    if args.files:
        print(files_block(args.dir))
        return 0
    metrics = load_metrics(args.dir / "metrics.json")
    settings = SETTINGS if args.setting == "all" else (args.setting,)
    present = [setting for setting in settings if setting in metrics["results"]]
    if not present:
        print(
            f"metrics.json has none of {settings}; it has "
            f"{sorted(metrics['results'])} - run the evaluation first",
            file=sys.stderr,
        )
        return 2
    print(protocol_block(metrics))
    for setting in present:
        print()
        print(aggregate_table(metrics, setting))
        print()
        print(label_pressure_table(metrics, setting))
        if not args.no_top1:
            print()
            print(top1_table(metrics, setting))
    if "T1" in present:
        print()
        print(invariance_block(metrics))
    print()
    print("claim limits (copied from metrics.json, not paraphrased):")
    for limit in metrics["claim_limits"]:
        print(f"  - {limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
