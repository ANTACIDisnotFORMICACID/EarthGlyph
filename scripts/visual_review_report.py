#!/usr/bin/env python3
"""Unseal the D2 blind scores and turn them into the visual verdict (task B3).

Read order matters and this script encodes it: the four-digit score strings below were
typed from the blind PNGs (``blind_{family}_{t2,t3}.png``, view ``display_aligned``)
against the rubric pre-registered in ``checkpoint_d/notes.md`` section D1.3, and only
after they were written down does anything here open ``blind_mapping.json``.  Nothing
in this file derives a score from a distance, a ``labels.json`` relevance tag or an
arm name — the mapping is used for exactly one thing, aggregating a fixed score by arm.

Scope actually scored in this pass, declared rather than implied:

* ``T2`` — all 18 queries x ranks 1-5 x four letters = 360 cells;
* ``T3`` — one representative query per family (the first query block printed on each
  T3 family page) x ranks 1-5 x four letters = 140 cells;
* the ``unaligned`` view and the remaining T3 groups are **not** scored, so the
  template keeps ``pending_review`` for them and no metric below claims to cover them.

Two derived columns are rules, not extra observations, and are labelled as such in
``visual_review_summary.json``:

``key_structure_preserved`` = score >= 2, ``obvious_error`` = score <= 1.

``low_confidence`` is the honest one: it is set when four *different* candidates in one
group received the same mark, i.e. where this reviewer could not separate shapes that
the sheets show to be distinct.  ``HARD_GROUPS`` lists the groups expected to be
undecidable; after the join the automatic rule already covers all of them, so that list
is a pre-registration, not a hand-set flag.

Usage::

    python scripts/visual_review_report.py
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

REVIEWER = "AI_visual_review"
VIEW = "display_aligned"
LETTERS = ("A", "B", "C", "D")
ARMS = ("E0", "E1", "E2", "E3")
T3_QUERIES = ("Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q08")

# Declared before the key was opened, as the groups this reviewer expected to be
# undecidable.  After the join every one of them also satisfies the automatic rule
# (flat marks over four different candidates), so the list changes no cell and no
# metric - tests/test_visual_review_report.py pins that, because a hand-maintained
# low-confidence flag is exactly the thing this review must not be able to lean on.
HARD_GROUPS = ("T2|Q10|4", "T2|Q10|5", "T2|Q05|2", "T2|Q05|3", "T3|Q05|1")

BASE_REASON = {
    3: "outline and key structures match; only plausible local deformation",
    2: "overall similar; one key structure is softened or displaced",
    1: "partly similar; a key structure is missing or replaced",
    0: "different shape class; the key structure is absent",
}

# Where the generic sentence would hide the actual observation, name it.
OVERRIDE_REASON = {
    "T2|Q01|1A": "an open band with a concave side where the query is a filled disc",
    "T2|Q01|5A": "a second lobe pinches the outline into a peanut; the query has none",
    "T2|Q02|1D": "the lobes are much wider than the query's; the notch is shallower",
    "T2|Q04|3B": "a straight band: both horns of the crescent are gone",
    "T2|Q04|4B": "a straight band again, no concave bite at all",
    "T2|Q04|5D": "a rounded blob; neither horn survives",
    "T2|Q09|2D": "a thin wavy snake instead of a two-turn zigzag",
    "T2|Q09|3C": "a four-prong star; the zigzag turns are replaced by prongs",
    "T2|Q09|5A": "the long upper stroke survives, the second turn does not",
    "T2|Q12|5B": "the arch is flattened into a lens; the opening under it nearly closes",
    "T2|Q06|1C": "a crescent band; the hub-and-bars structure is absent",
    "T2|Q06|5C": "a crescent band; nothing of the three-branch layout is left",
    "T2|Q11|2B": "a crescent band where the query is a canopy on a trunk",
    "T2|Q11|4C": "a crescent band; the trunk is gone",
    "T2|Q15|5A": "a lightning zigzag; the three rounded prongs are gone",
    "T2|Q15|5C": "a plain triangle; no prong at all",
    "T2|Q17|2D": "a triangle; the query's bumps are gone",
    "T2|Q17|3D": "a band; the query is a filled blob",
    "T2|Q17|4B": "a band; the query is a filled blob",
    "T2|Q10|2A": "an irregular shard; the star's points are not separable",
    "T2|Q13|2C": "a crescent band where the query is a filled ellipse",
    "T3|Q03|1A": "a four-prong blob; the lightning's two turns are gone",
    "T3|Q03|3A": "a trapezoid; the zigzag is not present in any orientation",
    "T3|Q04|2B": "a straight band; both horns gone",
    "T3|Q04|3C": "a straight band; both horns gone",
    "T3|Q04|5B": "a straight band; both horns gone",
    "T3|Q06|1A": "an irregular blob; the hub-and-bars structure is absent",
    "T3|Q06|5A": "an irregular blob; nothing of the three-branch layout is left",
    "T3|Q02|1C": "a bite removed part of the right lobe",
    "T3|Q02|4B": "a bite removed part of a lobe",
    "T3|Q01|1A": "an open band with a concave side where the query is a filled disc",
}

SCORES: dict[str, str] = {
    # ---- T2, blob
    "T2|Q01|1": "0333", "T2|Q01|2": "3333", "T2|Q01|3": "3332", "T2|Q01|4": "2333",
    "T2|Q01|5": "3333", "T2|Q07|1": "3333", "T2|Q07|2": "3333", "T2|Q07|3": "3332",
    "T2|Q07|4": "3332", "T2|Q07|5": "3323", "T2|Q13|1": "3323", "T2|Q13|2": "3303",
    "T2|Q13|3": "3333", "T2|Q13|4": "3333", "T2|Q13|5": "1333",
    # ---- T2, heart
    "T2|Q02|1": "3332", "T2|Q02|2": "3233", "T2|Q02|3": "3333", "T2|Q02|4": "3333",
    "T2|Q02|5": "3333", "T2|Q16|1": "3233", "T2|Q16|2": "3333", "T2|Q16|3": "3333",
    "T2|Q16|4": "3333", "T2|Q16|5": "3333",
    # ---- T2, band
    "T2|Q03|1": "2111", "T2|Q03|2": "1131", "T2|Q03|3": "2111", "T2|Q03|4": "1111",
    "T2|Q03|5": "1111", "T2|Q09|1": "3323", "T2|Q09|2": "3330", "T2|Q09|3": "2100",
    "T2|Q09|4": "2100", "T2|Q09|5": "3110", "T2|Q12|1": "3323", "T2|Q12|2": "3333",
    "T2|Q12|3": "3333", "T2|Q12|4": "3333", "T2|Q12|5": "3133",
    # ---- T2, crescent
    "T2|Q04|1": "3323", "T2|Q04|2": "3223", "T2|Q04|3": "1022", "T2|Q04|4": "3022",
    "T2|Q04|5": "2110", "T2|Q14|1": "3333", "T2|Q14|2": "3333", "T2|Q14|3": "3333",
    "T2|Q14|4": "3333", "T2|Q14|5": "3331",
    # ---- T2, forked
    "T2|Q06|1": "1100", "T2|Q06|2": "1112", "T2|Q06|3": "1201", "T2|Q06|4": "1121",
    "T2|Q06|5": "1202", "T2|Q11|1": "3233", "T2|Q11|2": "1033", "T2|Q11|3": "2021",
    "T2|Q11|4": "2100", "T2|Q11|5": "1110", "T2|Q15|1": "3333", "T2|Q15|2": "1322",
    "T2|Q15|3": "1131", "T2|Q15|4": "3221", "T2|Q15|5": "0101",
    # ---- T2, irregular
    "T2|Q08|1": "3333", "T2|Q08|2": "3122", "T2|Q08|3": "3323", "T2|Q08|4": "2232",
    "T2|Q08|5": "3233", "T2|Q17|1": "3333", "T2|Q17|2": "3330", "T2|Q17|3": "3330",
    "T2|Q17|4": "3033", "T2|Q17|5": "3332",
    # ---- T2, star
    "T2|Q05|1": "3333", "T2|Q05|2": "3333", "T2|Q05|3": "3333", "T2|Q05|4": "3333",
    "T2|Q05|5": "3333", "T2|Q10|1": "2233", "T2|Q10|2": "1322", "T2|Q10|3": "2322",
    "T2|Q10|4": "2222", "T2|Q10|5": "2222", "T2|Q18|1": "3333", "T2|Q18|2": "3323",
    "T2|Q18|3": "3333", "T2|Q18|4": "3323", "T2|Q18|5": "3322",
    # ---- T3, one representative query per family
    "T3|Q01|1": "0333", "T3|Q01|2": "3333", "T3|Q01|3": "3332", "T3|Q01|4": "3333",
    "T3|Q01|5": "3333", "T3|Q02|1": "2313", "T3|Q02|2": "3313", "T3|Q02|3": "3133",
    "T3|Q02|4": "3113", "T3|Q02|5": "3331", "T3|Q03|1": "1111", "T3|Q03|2": "1121",
    "T3|Q03|3": "1111", "T3|Q03|4": "1211", "T3|Q03|5": "1111", "T3|Q04|1": "2332",
    "T3|Q04|2": "2032", "T3|Q04|3": "2302", "T3|Q04|4": "3322", "T3|Q04|5": "2023",
    "T3|Q05|1": "3333", "T3|Q05|2": "3333", "T3|Q05|3": "3333", "T3|Q05|4": "3333",
    "T3|Q05|5": "3333", "T3|Q06|1": "0100", "T3|Q06|2": "0010", "T3|Q06|3": "1211",
    "T3|Q06|4": "0101", "T3|Q06|5": "0100", "T3|Q08|1": "3333", "T3|Q08|2": "3323",
    "T3|Q08|3": "3123", "T3|Q08|4": "3332", "T3|Q08|5": "3333",
}


# ------------------------------------------------------------------ loading


def load_template(path: Path) -> dict[str, dict[str, str]]:
    """group key -> {letter: candidate_code}, for the scored view only."""
    groups: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["view"] != VIEW:
                continue
            key = f"{row['setting']}|{row['query_code']}|{row['rank']}"
            groups.setdefault(key, {})[row["blind_label"]] = row["candidate_code"]
    return groups


def load_mapping(
    path: Path,
) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, dict[str, str]]]:
    """(group key -> letter -> cell, group key -> {query_id, family}).

    ``blind_mapping.json`` is opened only here, and only after ``SCORES`` above was
    written down, so the key can influence aggregation but never a mark.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    cells: dict[str, dict[str, dict[str, str]]] = {}
    meta: dict[str, dict[str, str]] = {}
    for entry in payload["groups"]:
        key = f"{entry['setting']}|{entry['query_code']}|{entry['rank']}"
        cells[key] = {
            letter: {
                "arm": str(cell["arm"]),
                "candidate_id": str(cell["candidate_id"]),
                "candidate_code": str(cell["candidate_code"]),
            }
            for letter, cell in entry["assignment"].items()
        }
        meta[key] = {
            "query_id": str(entry["query_id"]),
            "family": str(entry["query_family_hidden_from_sheet"]),
        }
    return cells, meta


# ------------------------------------------------------------------ rows


def score_rows(
    template: dict[str, dict[str, str]],
    cells: dict[str, dict[str, dict[str, str]]],
    meta: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Join the fixed scores to the sealed key, one row per scored cell."""
    rows: list[dict[str, Any]] = []
    for key in sorted(SCORES):
        digits = SCORES[key]
        if len(digits) != 4 or any(char not in "0123" for char in digits):
            raise SystemExit(f"{key}: score string {digits!r} is not four of 0..3")
        if key not in template:
            raise SystemExit(f"{key}: not a group in the blind template")
        if key not in cells:
            raise SystemExit(f"{key}: not a group in the blind mapping")
        group = template[key]
        if set(group) != set(LETTERS):
            raise SystemExit(f"{key}: template letters {sorted(group)} != {list(LETTERS)}")
        for letter in LETTERS:
            if cells[key][letter]["candidate_code"] != group[letter]:
                raise SystemExit(
                    f"{key}/{letter}: mapping and template disagree on the blind codes"
                )
        identical = len({group[letter] for letter in LETTERS}) == 1
        flat = all(char == digits[0] for char in digits)
        low = int((flat and not identical) or key in HARD_GROUPS)
        setting, query_code, rank = key.split("|")
        for index, letter in enumerate(LETTERS):
            cell = cells[key][letter]
            value = int(digits[index])
            reason = OVERRIDE_REASON.get(f"{key}{letter}") or BASE_REASON[value]
            rows.append(
                {
                    "setting": setting,
                    "view": VIEW,
                    "query_code": query_code,
                    "query_id": meta[key]["query_id"],
                    "rank": rank,
                    "blind_label": letter,
                    "arm": cell["arm"],
                    "candidate_code": group[letter],
                    "candidate_id": cell["candidate_id"],
                    "family": meta[key]["family"],
                    "score_0_3": value,
                    "key_structure_preserved": int(value >= 2),
                    "obvious_error": int(value <= 1),
                    "low_confidence": low,
                    "reason": reason,
                    "reviewer": REVIEWER,
                    "status": "ai_scored",
                }
            )
    return rows


# ------------------------------------------------------------------ metrics


def ndcg_at_five(scores_by_rank: list[int]) -> float:
    """Graded NDCG@5 of one arm's own five marks, in the order it returned them."""
    def dcg(values: list[int]) -> float:
        return sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values))

    ideal = dcg(sorted(scores_by_rank, reverse=True))
    return 1.0 if ideal == 0.0 else dcg(scores_by_rank) / ideal


def per_query_blocks(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[int]]:
    """(setting, query_code, arm) -> the five marks in returned order."""
    blocks: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        blocks.setdefault((row["setting"], row["query_code"], row["arm"]), []).append(row)
    return {
        key: [int(row["score_0_3"]) for row in sorted(block, key=lambda r: int(r["rank"]))]
        for key, block in blocks.items()
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = per_query_blocks(rows)
    out: dict[str, Any] = {}
    for arm in ARMS:
        series = {key: values for key, values in blocks.items() if key[2] == arm}
        if not series:
            continue
        arm_rows = [row for row in rows if row["arm"] == arm]
        top1 = [values[0] for values in series.values()]
        top3 = [values[:3] for values in series.values()]
        top5 = [values[:5] for values in series.values()]
        out[arm] = {
            "n_queries": len(series),
            "n_cells": len(arm_rows),
            "mean_top1_visual": round(sum(top1) / len(top1), 4),
            "mean_top3_visual": round(
                sum(sum(block) / len(block) for block in top3) / len(top3), 4
            ),
            "mean_top5_visual": round(
                sum(sum(block) / len(block) for block in top5) / len(top5), 4
            ),
            "ndcg_at_5_from_visual_scores": round(
                sum(ndcg_at_five(values) for values in top5) / len(top5), 4
            ),
            "share_top1_score3": round(sum(1 for value in top1 if value == 3) / len(top1), 4),
            "obvious_error_share": round(
                sum(row["obvious_error"] for row in arm_rows) / len(arm_rows), 4
            ),
            "key_structure_preserved_share": round(
                sum(row["key_structure_preserved"] for row in arm_rows) / len(arm_rows), 4
            ),
            "low_confidence_share": round(
                sum(row["low_confidence"] for row in arm_rows) / len(arm_rows), 4
            ),
        }
    return out


def by_family(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    families = sorted({row["family"] for row in rows})
    for family in families:
        picked = [row for row in rows if row["family"] == family]
        table = summarise(picked)
        out[family] = {
            arm: block["mean_top5_visual"] for arm, block in table.items()
        }
        out[family]["best"] = max(
            (arm for arm in ARMS if arm in table), key=lambda arm: table[arm]["mean_top5_visual"]
        )
    return out


def hard_groups_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Does the declared hard-group list add any flag the mechanical rule missed?

    Reported rather than assumed: if a listed group were only flagged by the list, the
    ``low_confidence`` column would be partly hand-set and every share built on it would
    inherit that.  Zero such groups is the evidence that it is not.
    """
    only_by_list: list[str] = []
    for key in HARD_GROUPS:
        group = [
            row
            for row in rows
            if f"{row['setting']}|{row['query_code']}|{row['rank']}" == key
        ]
        if not group:
            only_by_list.append(key)
            continue
        flat = len({row["score_0_3"] for row in group}) == 1
        distinct = len({row["candidate_code"] for row in group}) > 1
        if not (flat and distinct):
            only_by_list.append(key)
    return {
        "declared": len(HARD_GROUPS),
        "also_flagged_by_the_rule": len(HARD_GROUPS) - len(only_by_list),
        "flagged_only_by_the_list": only_by_list,
    }


# ------------------------------------------------------------------ figure


def draw_examples(rows: list[dict[str, Any]], out_path: Path, *, dpi: int) -> dict[str, Any]:
    """The best and worst scored group per arm, now that the key is open."""
    from visualize_retrieval import _figure_backend  # noqa: PLC0415

    diagnostics = _load_diagnostics()
    gallery_dir = ROOT / "experiments" / "p0_perceptual_probe"
    frozen = diagnostics.Frozen(gallery_dir, ROOT / "data" / "index", gallery_dir / "results.csv")
    plt = _figure_backend()

    blocks = per_query_blocks(rows)
    lookup = {(r["setting"], r["query_code"], r["arm"], int(r["rank"])): r for r in rows}
    picked: list[tuple[str, str, list[int]]] = []
    for arm in ARMS:
        series = {key: values for key, values in blocks.items() if key[2] == arm}
        order = sorted(series, key=lambda key: (sum(series[key]) / len(series[key]), key))
        picked.append((arm, order[-1], series[order[-1]]))
        picked.append((arm, order[0], series[order[0]]))

    columns = 5
    grid = columns + 1
    fig, axes = plt.subplots(
        len(picked), grid, figsize=(grid * 1.95, len(picked) * 2.0), squeeze=False
    )
    for index, (arm, key, values) in enumerate(picked):
        setting, query_code, _ = key
        cells = [lookup[(setting, query_code, arm, rank)] for rank in range(1, columns + 1)]
        query_id = cells[0]["query_id"]
        for slot, row in enumerate(cells):
            diagnostics._draw_pair(
                axes[index][slot + 1],
                {
                    "query_id": query_id,
                    "candidate_id": row["candidate_id"],
                    "label": f"{row['candidate_id']} visual {row['score_0_3']}",
                    "arm": arm,
                },
                frozen,
            )
        # A real text axis, not set_ylabel on the first shape cell: the label used to be
        # printed straight over the outline it was naming.
        notes = axes[index][0]
        notes.axis("off")
        notes.text(
            0.0,
            0.96,
            _wrap(f"{arm}  {query_code}  mean {round(sum(values) / len(values), 2)}", width=14),
            fontsize=7.2,
            va="top",
            ha="left",
            color="#122033",
            fontweight="bold",
            linespacing=1.4,
        )
    fig.suptitle(
        "Checkpoint D2 - visual review examples: each arm's best and worst scored group",
        fontsize=10.0,
    )
    fig.text(
        0.012,
        0.014,
        _wrap(
            "Top-5 of one arm, in the order that arm returned them, with the blind score this "
            "reviewer gave each cell against the query in the same row.  The scores were typed "
            "from the blind sheets before the key was opened; this figure is the unblinded "
            "reading of those fixed numbers, not a second review.  Dashed green is the shared "
            "display fit and belongs to no arm.",
            width=150,
        ),
        fontsize=6.2,
        color="#40506a",
        linespacing=1.5,
    )
    fig.subplots_adjust(left=0.11, right=0.99, top=0.93, bottom=0.08, wspace=0.1, hspace=0.4)
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "groups": [f"{arm} {key} mean {round(sum(values) / len(values), 2)}"
                   for arm, key, values in picked],
    }


def _wrap(text: str, *, width: int) -> str:
    import textwrap  # noqa: PLC0415

    return "\n".join(textwrap.fill(line, width=width) for line in text.split("\n"))


def _load_diagnostics() -> Any:
    spec = importlib.util.spec_from_file_location(
        "diagnose_perceptual_arms", ROOT / "scripts" / "diagnose_perceptual_arms.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--in-dir", type=Path, default=Path("experiments/p0_perceptual_probe/checkpoint_d")
    )
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--no-figure", action="store_true")
    args = parser.parse_args(argv)

    template = load_template(args.in_dir / "visual_review_template.csv")
    cells, meta = load_mapping(args.in_dir / "blind_mapping.json")
    rows = score_rows(template, cells, meta)

    scored = args.in_dir / "visual_review_scores.csv"
    fieldnames = [
        "setting", "view", "query_code", "query_id", "rank", "blind_label", "arm",
        "candidate_code", "candidate_id", "family", "score_0_3", "key_structure_preserved",
        "obvious_error", "low_confidence", "reason", "reviewer", "status",
    ]
    with scored.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    t2 = [row for row in rows if row["setting"] == "T2"]
    t3 = [row for row in rows if row["setting"] == "T3"]
    summary = {
        "reviewer": REVIEWER,
        "not_human_subject_data": (
            "one model read the blind sheets; this is not a human-subject experiment and "
            "must not be quoted as one"
        ),
        "rubric": "checkpoint_d/notes.md section D1.3, fixed before scoring",
        "read_order": (
            "scores were typed from blind_{family}_{t2,t3}.png pixels, then this file was "
            "written, then blind_mapping.json was opened; no score was revised afterwards"
        ),
        "blinding_caveats": {
            "partial": (
                "the same reviewer had already read the labelled D1 fair pages and the D2 "
                "diagnostic tables, so a Top-1 shape could sometimes be recognised as an "
                "arm's known answer; the letter->arm key was still unknown while marking"
            ),
            "not_scored": [
                "the unaligned view",
                "T3 groups other than the one representative query per family",
            ],
        },
        "derived_columns_are_rules_not_observations": {
            "key_structure_preserved": "score >= 2",
            "obvious_error": "score <= 1",
            "low_confidence": (
                "four different candidates in one group received the same mark, or the "
                "group is listed in HARD_GROUPS"
            ),
        },
        "low_confidence_audit": hard_groups_audit(rows),
        "scope": {
            "groups_scored": len(SCORES),
            "cells_scored": len(rows),
            "t2_cells": len(t2),
            "t3_cells": len(t3),
            "template_display_aligned_cells": len(template) * len(LETTERS),
            "template_cells_both_views": len(template) * len(LETTERS) * 2,
        },
        "T2": {"per_arm": summarise(t2), "best_arm_by_family": by_family(t2)},
        "T3_representative": {"per_arm": summarise(t3), "best_arm_by_family": by_family(t3)},
        "combined": {"per_arm": summarise(rows), "best_arm_by_family": by_family(rows)},
    }
    path = args.in_dir / "visual_review_summary.json"
    if not args.no_figure:
        summary["figure"] = draw_examples(rows, args.in_dir / "visual_review_examples.png",
                                          dpi=args.dpi)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"scored {len(SCORES)} groups / {len(rows)} cells -> {scored}")
    for setting, block in (("T2", summary["T2"]), ("T3", summary["T3_representative"])):
        print(f"\n== {setting} ==")
        print(f"{'arm':<5}{'top1':>7}{'top3':>7}{'top5':>7}{'ndcg5':>8}{'err%':>7}{'lowconf%':>10}")
        for arm, metrics in block["per_arm"].items():
            print(
                f"{arm:<5}{metrics['mean_top1_visual']:>7.3f}{metrics['mean_top3_visual']:>7.3f}"
                f"{metrics['mean_top5_visual']:>7.3f}"
                f"{metrics['ndcg_at_5_from_visual_scores']:>8.3f}"
                f"{100 * metrics['obvious_error_share']:>7.1f}"
                f"{100 * metrics['low_confidence_share']:>10.1f}"
            )
        winners = {
            family: entry["best"] for family, entry in block["best_arm_by_family"].items()
        }
        print("best arm by family:", winners)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
