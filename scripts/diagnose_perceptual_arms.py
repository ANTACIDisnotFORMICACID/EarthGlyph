#!/usr/bin/env python3
"""Why the four arms behave as they do, per query and per source line (Checkpoint D2).

Checkpoint C reported *what* each arm ranked.  Checkpoint D has to say *why*, and the
brief is explicit that guessing is not allowed: no "E2 is weak at notches" until a
specific query, a specific candidate and a specific term of the cost function are on
the table, and no "E2's rank-4 is a bug" before the cause has been located.

So every number here is traced to one of three places:

* the frozen ``results.csv`` — the rankings are read, never recomputed, so D2 cannot
  disagree with C by accident;
* the arm modules themselves, called with the same parameters the C runner used
  (``fit_gallery_normalizer`` / ``arm_params`` are imported from that runner rather
  than re-derived here, which is what keeps the two checkpoints comparable);
* ``labels.json`` + ``gallery.jsonl`` for family, role and the ``note`` text.

What each part answers, keyed to the brief:

``material`` — a gallery-level check that has to be read first: how many members are
        byte-identical contours, how many of those pairs ``labels.json`` declares, and
        whether a query's own library still contains a copy of itself.  A distance of
        0.0 only means "the arm cannot tell these apart" if the two contours differ.
``e0``  C1 — is E0 already good enough on simple shapes, which deformations break it,
        does it miss notches, does it return a near-identical boundary with different
        structure.  E0 is the incumbent, so "old" is not evidence against it and the
        part reports where it wins as well as where it loses.
``e1``  C2 — why heart queries come back rounded, what each of the five feature groups
        contributes when it is removed (ablation with the queries and the gallery
        frozen), and how far the corpus-fitted normalizer travels on real landforms.
        No weight is hand-tuned; dropping a group is the only intervention.
``e2``  C3 — the T1 rank-4 first, located rather than declared: the query's salient
        sequence, the tie set at distance 0.0, and the tie-break rule.  Then the T2
        weakness: event census, the price the DP actually pays for a dropped corner,
        whether a dropped corner is cheaper than an honest match, whether matched pairs
        link a convex corner to a concave one, and what the coarse scales delete.
``e3``  C4 — the full E3-vs-E0 per-query comparison, wins *and* losses, plus where the
        wall-clock goes (descriptor build, the rotation grid, the assignment).
``rotation``
        the invariance probe: rotate the *query* and see whether the ranking of the
        same frozen target moves.  E3 is included on a small subset only, because its
        cost is measured, not assumed.

The figure it writes (``diagnostic_cases.png``) is for the eye, not for the argument:
each panel draws the exact shapes the tables name, in the shared display frame D1
established, so a reader can check the diagnosis against the picture.

Scores and ranks of Checkpoint C are never modified: this script writes only into
``experiments/p0_perceptual_probe/checkpoint_d/``.

Usage::

    python scripts/diagnose_perceptual_arms.py                     # all parts
    python scripts/diagnose_perceptual_arms.py --parts e2,rotation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_perceptual_methods import (  # noqa: E402
    Item,
    Library,
    arm_params,
    build_library,
    fit_gallery_normalizer,
    load_gallery,
    load_landforms,
    out_of_range_report,
)
from visualize_retrieval import _figure_backend  # noqa: E402  (selects the Agg backend)

from earthglyph.retrieval import perceptual_features as e1  # noqa: E402
from earthglyph.retrieval import perceptual_methods  # noqa: E402
from earthglyph.retrieval import salient_structure as e2  # noqa: E402
from earthglyph.retrieval import shape_context_baseline as e3  # noqa: E402
from earthglyph.retrieval.display_alignment import align_for_display  # noqa: E402

ARMS = ("E0", "E1", "E2", "E3")
ROTATION_ANGLES = (0.0, 15.0, 30.0, 37.0, 45.0, 60.0, 72.0, 90.0, 180.0)
E3_ROTATION_ANGLES = (0.0, 37.0, 90.0, 180.0)
QUERY = "#a4262c"
CANDIDATE = "#2f3b4c"
CANDIDATE_FILL = "#cfd9e6"
GOOD = "#12623c"
BAD = "#a4262c"


# ---------------------------------------------------------------- loading


def load_results(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class Frozen:
    """Everything D2 reads, held in one place so no part can drift from another."""

    def __init__(self, gallery_dir: Path, index_dir: Path, results_path: Path) -> None:
        self.gallery: dict[str, Item] = load_gallery(gallery_dir)
        self.labels = json.loads((gallery_dir / "labels.json").read_text(encoding="utf-8"))
        self.notes = _read_notes(gallery_dir)
        self.origin_refs = _read_origin_refs(gallery_dir)
        self.results = load_results(results_path)
        self.index_ids, self.index_rows, self.index_meta = load_landforms(index_dir)
        self.index_position = {item_id: n for n, item_id in enumerate(self.index_ids)}
        self.normalizer = fit_gallery_normalizer(list(self.gallery.values()))
        self.queries = sorted(self.labels["queries"])
        self.rows: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        for record in self.results:
            key = (record["setting"], record["arm"], record["query_id"])
            self.rows.setdefault(key, []).append(record)
        for block in self.rows.values():
            block.sort(key=lambda row: int(row["rank"]))

    def top(self, setting: str, arm: str, query_id: str, k: int) -> list[dict[str, str]]:
        return self.rows.get((setting, arm, query_id), [])[:k]

    def item(self, item_id: str) -> np.ndarray:
        """The ring exactly as the library scored it — gallery rows and index rows alike."""
        if item_id in self.gallery:
            return self.gallery[item_id].contour
        return np.asarray(self.index_rows[self.index_position[item_id]], dtype=float)

    def meta(self, item_id: str) -> dict[str, Any]:
        item = self.gallery.get(item_id)
        if item is None:
            return {"family": "real_landform", "role": "unlabelled_real_shape", "note": ""}
        return {"family": item.family, "role": item.role, "note": self.notes.get(item_id, "")}

    def library(self, setting: str, query_id: str) -> Library:
        return build_library(
            setting, query_id, self.labels, self.gallery, self.index_ids, self.index_rows
        )

    def relevant(self, query_id: str) -> set[str]:
        return set(self.labels["queries"][query_id]["relevant_variants"])


def _read_notes(gallery_dir: Path) -> dict[str, str]:
    """The generator's own one-line description of each shape, keyed by id."""
    notes: dict[str, str] = {}
    for line in (gallery_dir / "gallery.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            notes[str(record["item_id"])] = str(record.get("note", ""))
    return notes


def _read_origin_refs(gallery_dir: Path) -> dict[str, str]:
    """Which source asset each gallery row was cut from, keyed by id.

    ``Item`` does not carry this, but ``gallery.jsonl`` does, and it is the generator's
    own admission of reuse: a QuickDraw ``key_id`` appearing three times under three
    families says the same thing as a contour hash, from the other side of the pipeline.
    """
    refs: dict[str, str] = {}
    for line in (gallery_dir / "gallery.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            refs[str(record["item_id"])] = str(record.get("origin_ref", ""))
    return refs


# ---------------------------------------------------------------- helpers


def rank_of(target: str, ids: list[str]) -> int:
    """1-based position of ``target`` in a ranked id list, or 999 if absent."""
    return ids.index(target) + 1 if target in ids else 999


def hit_and_first(relevant: set[str], ids: list[str]) -> dict[str, Any]:
    hits = [index + 1 for index, item in enumerate(ids) if item in relevant]
    return {
        "top1_relevant": bool(hits and hits[0] == 1),
        "first_relevant_rank": hits[0] if hits else None,
        "relevant_in_top5": sum(1 for rank in hits if rank <= 5),
    }


def score(
    frozen: Frozen, arm: str, query: np.ndarray, rows: list[np.ndarray], **extra: Any
) -> np.ndarray:
    """Distances under exactly the parameters the C runner passed, plus explicit extras.

    ``arm_params`` is imported from that runner rather than re-derived, so a D2
    re-ranking cannot disagree with a C ranking by having quietly used a different
    E1 normalizer or a different E3 orientation mode.
    """
    params = dict(arm_params(arm, normalizer=frozen.normalizer, e3_orientation="sample"))
    params.update(extra)
    distances, _ = perceptual_methods.score_library(arm, query, rows, params=params)
    return np.asarray(distances, dtype=float)


def ranked_ids(
    frozen: Frozen, arm: str, query: np.ndarray, library: Library, **extra: Any
) -> list[str]:
    order = perceptual_methods.rank_order(score(frozen, arm, query, library.rows, **extra))
    return [library.ids[int(position)] for position in order]


# ---------------------------------------------------------------- part e0


def contour_digests(frozen: Frozen) -> dict[str, str]:
    """id -> hash of its point array, rounded to 9 dp.

    Three arms in this run produce rows at distance exactly 0.0, or several families at
    one identical distance.  Before any of them is read as "this arm cannot see the
    structure", the geometry has to answer whether the two shapes differ at all; the
    digest also lets a table count distinct shape comparisons instead of ids, so one
    contour filed under six families is not read as six findings.
    """
    digests: dict[str, str] = {}
    for item_id, item in frozen.gallery.items():
        digests[item_id] = hashlib.sha256(
            np.round(np.asarray(item.contour, dtype=float), 9).tobytes()
        ).hexdigest()
    return digests


def part_e0(frozen: Frozen, setting: str) -> dict[str, Any]:
    """C1: what the incumbent is actually good at, and the one thing it cannot see."""
    near = float(frozen.labels["near_max_align"])
    # rows whose candidate is the same contour as the query are a gallery property, not
    # an E0 blind spot, so they get labelled before they can be misread as one.
    digests = contour_digests(frozen)
    per_query: list[dict[str, Any]] = []
    blind_cases: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        family = frozen.labels["queries"][query_id]["family"]
        relevant = frozen.relevant(query_id)
        block = frozen.top(setting, "E0", query_id, 10)
        top1 = block[0] if block else {}
        distances = [float(row["distance"]) for row in block]
        # the worst deformation E0 still ranks first: the relevant variant it likes
        # least among the ones it put in the top five.
        tolerated = [
            float(row["distance"])
            for row in block[:5]
            if row["candidate_id"] in relevant and float(row["distance"]) > 0.0
        ]
        entry = {
            "query_id": query_id,
            "family": family,
            "top1_candidate": top1.get("candidate_id"),
            "top1_distance": float(top1["distance"]) if top1 else None,
            "top1_relevant": bool(top1) and top1.get("relevant") == "1",
            "theta_deg": float(top1["theta_deg"]) if top1 and top1["theta_deg"] else 0.0,
            "shift": int(float(top1["shift"])) if top1 and top1["shift"] else 0,
            "top5_distance_spread": (
                round(max(distances[:5]) - min(distances[:5]), 6) if len(distances) >= 5 else None
            ),
            "worst_relevant_in_top5": round(max(tolerated), 6) if tolerated else None,
            "lookalikes_below_threshold": 0,
        }
        # "near-identical boundary, different structure": every candidate E0 calls
        # indistinguishable from the query, split by whether the labels agree.
        library = frozen.library(setting, query_id)
        distances_all = score(frozen, "E0", frozen.item(query_id), library.rows)
        close = [
            (library.ids[int(position)], float(value))
            for position, value in enumerate(distances_all)
            if float(value) <= near
        ]
        entry["lookalikes_below_threshold"] = len(close)
        entry["lookalikes_relevant"] = sum(1 for item, _ in close if item in relevant)
        entry["lookalikes_not_relevant"] = len(close) - entry["lookalikes_relevant"]
        for item_id, value in close:
            if item_id in relevant:
                continue
            blind_cases.append(
                {
                    "query_id": query_id,
                    "candidate_id": item_id,
                    "distance": round(value, 8),
                    "candidate_family": frozen.meta(item_id)["family"],
                    "candidate_role": frozen.meta(item_id)["role"],
                    "same_contour_as_query": digests[query_id] == digests[item_id],
                    "note": frozen.meta(item_id)["note"],
                }
            )
        per_query.append(entry)

    simple = [row for row in per_query if row["family"] in ("band", "blob", "irregular")]
    return {
        "near_max_align": near,
        "per_query": per_query,
        "top1_relevant_rate": round(
            statistics.fmean(1.0 if row["top1_relevant"] else 0.0 for row in per_query), 4
        ),
        "simple_family_top1_relevant_rate": round(
            statistics.fmean(1.0 if row["top1_relevant"] else 0.0 for row in simple), 4
        )
        if simple
        else None,
        "n_simple_queries": len(simple),
        "indistinguishable_but_not_relevant": sorted(
            blind_cases, key=lambda row: row["distance"]
        )[:20],
        "count_indistinguishable_non_relevant": len(blind_cases),
        "count_indistinguishable_relevant": sum(
            1 for row in per_query for _ in range(row["lookalikes_relevant"])
        ),
        # the split that decides how much of the table above is evidence about E0
        "same_contour_as_query": sum(1 for row in blind_cases if row["same_contour_as_query"]),
        "distinct_contours": sum(1 for row in blind_cases if not row["same_contour_as_query"]),
        # six ids may be one shape: count the comparisons a reader could actually learn from
        "distinct_shape_comparisons": len(
            {(row["query_id"], digests[row["candidate_id"]]) for row in blind_cases}
        ),
        "distinct_comparisons_on_a_different_contour": len(
            {
                (row["query_id"], digests[row["candidate_id"]])
                for row in blind_cases
                if not row["same_contour_as_query"]
            }
        ),
    }


# ---------------------------------------------------------------- material audit


def identical_contour_groups(frozen: Frozen) -> list[list[str]]:
    """Gallery ids whose point array is the same shape, rounded to 9 dp.

    Split out of ``part_material`` because the arm tables need it too: a query and a
    "hard negative" that share a contour cannot be separated by any metric, so those
    rows must be counted as a material property before they are read as an algorithm's
    blind spot.
    """
    buckets: dict[str, list[str]] = {}
    for item_id, digest in contour_digests(frozen).items():
        buckets.setdefault(digest, []).append(item_id)
    return sorted(sorted(ids) for ids in buckets.values() if len(ids) > 1)


def part_material(frozen: Frozen, setting: str) -> dict[str, Any]:
    """Are the gallery's "hard negatives" actually different shapes from the query?

    Every arm-level table in this run compares a query against a candidate the labels
    call another family.  That comparison only carries information if the two contours
    differ, so the geometry is checked before any arm number is read.
    """
    groups = identical_contour_groups(frozen)
    declared = {
        frozenset((entry["left"], entry["right"]))
        for entry in frozen.labels["near_duplicate_pairs"]
    }
    pairs = [
        (ids[left], ids[right])
        for ids in groups
        for left in range(len(ids))
        for right in range(left + 1, len(ids))
    ]
    undeclared = sorted(pair for pair in pairs if frozenset(pair) not in declared)
    leakage: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        ring = np.round(np.asarray(frozen.item(query_id), dtype=float), 9)
        library = frozen.library(setting, query_id)
        hits = [
            candidate_id
            for candidate_id, row in zip(library.ids, library.rows, strict=True)
            if np.array_equal(np.round(np.asarray(row, dtype=float), 9), ring)
        ]
        if hits:
            leakage.append({"query_id": query_id, "identical_rows_in_library": hits})
    affected = sorted({item_id for ids in groups for item_id in ids})
    # an independent second read: the provenance column says which source asset each row
    # was cut from, so a match there is the generator admitting the reuse in its own words
    by_ref: dict[str, list[str]] = {}
    for item_id in sorted(frozen.gallery):
        by_ref.setdefault(frozen.origin_refs.get(item_id, ""), []).append(item_id)
    ref_groups = sorted(sorted(ids) for ids in by_ref.values() if len(ids) > 1)
    return {
        "setting": setting,
        "gallery_members": len(frozen.gallery),
        "identical_contour_groups": len(groups),
        "members_in_a_group": len(affected),
        "groups": [
            {
                "ids": ids,
                "labels": [
                    f"{item_id} [{frozen.meta(item_id)['family']}/{frozen.meta(item_id)['role']}]"
                    for item_id in ids
                ],
                "families_present": sorted({frozen.meta(item_id)["family"] for item_id in ids}),
                "roles_present": sorted({frozen.meta(item_id)["role"] for item_id in ids}),
            }
            for ids in groups
        ],
        "pairs_total": len(pairs),
        "pairs_declared_in_labels": len(pairs) - len(undeclared),
        "pairs_not_declared": [list(pair) for pair in undeclared],
        "query_duplicates_left_in_library": leakage,
        # the two reads are built from different fields (geometry hash vs provenance);
        # if they disagree, one of them is wrong, and the audit says so out loud
        "origin_ref_groups": len(ref_groups),
        "origin_ref_members": sum(len(ids) for ids in ref_groups),
        "geometric_and_provenance_reads_agree": ref_groups == groups,
        "provenance_of_groups": [
            {"group": ids, "origin_ref": frozen.origin_refs.get(ids[0], "")}
            for ids in groups
        ],
        "reading": (
            "a distance of 0.0 between two ids in one of these groups is the gallery "
            "reusing one contour under several family labels, not an arm failing to see a "
            "structural difference; the generator reuses the same recipe for all the "
            "syn-circle* hard negatives and the same QuickDraw row for qd-circle / "
            "qd-blob-circle / qd-band-circle / qd-circle-query, so the intended inner "
            "structure never reaches the point array"
        ),
    }


# ---------------------------------------------------------------- part e1


def part_e1(frozen: Frozen, setting: str) -> dict[str, Any]:
    """C2: the rounded-heart question, a group ablation, and the normalizer's reach."""
    groups = list(e1_groups())
    per_query: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        if frozen.labels["queries"][query_id]["family"] != "heart":
            continue
        ring = frozen.item(query_id)
        library = frozen.library(setting, query_id)
        ids = ranked_ids(frozen, "E1", ring, library)
        relevant = frozen.relevant(query_id)
        top3 = [
            {
                "candidate_id": item,
                "family": frozen.meta(item)["family"],
                "role": frozen.meta(item)["role"],
                "note": frozen.meta(item)["note"],
                "relevant": item in relevant,
            }
            for item in ids[:3]
        ]
        # where the *pointed* variants land, and what the winning candidate scores
        # per group: the group whose distance is smallest is the one doing the work.
        pointed = [item for item in relevant if "point" in item or "deep" in item]
        per_query.append(
            {
                "query_id": query_id,
                "top3": top3,
                "first_relevant_rank": rank_of(next(iter(sorted(relevant))), ids),
                "pointed_variant_ranks": {
                    item: rank_of(item, ids) for item in sorted(pointed)[:4]
                },
                "group_distances_for_top1": _group_split(frozen, ring, ids[0]),
            }
        )

    ablation = _e1_ablation(frozen, setting, groups)
    return {
        "heart_queries": per_query,
        "descriptor_counts": {group: _group_width(group) for group in groups},
        "ablation": ablation,
        "normalizer_shift_on_landforms": out_of_range_report(frozen.normalizer, frozen.index_rows),
        "normalizer_shift_on_gallery": _gallery_shift(frozen),
    }


def e1_groups() -> tuple[str, ...]:
    return e1.GROUPS


def _group_width(group: str) -> int:
    return len(e1.feature_names((group,), orientation="canonical"))


def _normalizer_for(frozen: Frozen, groups: tuple[str, ...]) -> e1.FeatureNormalizer:
    """The corpus-fitted scale restricted to a group selection.

    The fitted transform is per-column minmax, so slicing its center/scale/lo/hi is
    arithmetically the same thing as refitting on the retained columns — it is *not*
    a second normalization and it introduces no new scale.  Passing the full 91-column
    normalizer alongside a shorter group selection is rejected outright by
    ``_check_normalizer``, which is the right behaviour: the alternative would be
    silently comparing descriptors at two different scales.
    """
    wanted = set(e1.feature_names(groups, orientation="canonical"))
    keep = [index for index, name in enumerate(frozen.normalizer.names) if name in wanted]
    source = frozen.normalizer
    return e1.FeatureNormalizer(
        method=source.method,
        center=source.center[keep],
        scale=source.scale[keep],
        lo=source.lo[keep],
        hi=source.hi[keep],
        names=tuple(source.names[index] for index in keep),
        n_samples=source.n_samples,
        degenerate=tuple(
            name for name in source.degenerate if name in {source.names[i] for i in keep}
        ),
        fitted_on=f"{source.fitted_on}, columns subset for group ablation",
    )


def _group_split(frozen: Frozen, ring: np.ndarray, candidate_id: str) -> dict[str, float]:
    """Per-group share of the normalized distance, from one full 91-dim transform.

    The normalizer is fitted on all 91 descriptors, so the honest way to ask "which
    group decided this rank" is to transform the whole vector once and slice it — not
    to refit or rescale a single group, which would be a different metric.
    """
    left = frozen.normalizer.transform(e1.features_for_contour(ring, orientation="canonical"))
    right = frozen.normalizer.transform(
        e1.features_for_contour(frozen.item(candidate_id), orientation="canonical")
    )
    delta = left - right
    out: dict[str, float] = {}
    offset = 0
    for group in e1_groups():
        width = _group_width(group)
        out[group] = round(float(np.linalg.norm(delta[offset : offset + width])), 4)
        offset += width
    out["total"] = round(float(np.linalg.norm(delta)), 4)
    return out


def _gallery_shift(frozen: Frozen) -> dict[str, Any]:
    """The same out-of-range check on the corpus the normalizer was fitted on."""
    matrix = e1.features_for_library(
        [item.contour for item in frozen.gallery.values()], orientation="canonical"
    )
    below = int(np.count_nonzero(matrix < frozen.normalizer.lo))
    above = int(np.count_nonzero(matrix > frozen.normalizer.hi))
    return {
        "rows_scored": int(matrix.shape[0]),
        "descriptor_cells": int(matrix.size),
        "cells_outside_fitted_interval": below + above,
    }


def _e1_ablation(frozen: Frozen, setting: str, groups: list[str]) -> dict[str, Any]:
    """Drop-one-group and single-group rankings with the queries and gallery frozen."""
    configs = [("full", tuple(groups))]
    configs += [(f"drop_{group}", tuple(g for g in groups if g != group)) for group in groups]
    configs += [(group, (group,)) for group in groups]

    rows: dict[str, dict[str, Any]] = {}
    for name, selected in configs:
        hits1 = 0
        firsts: list[int] = []
        flips: list[str] = []
        for query_id in frozen.queries:
            library = frozen.library(setting, query_id)
            distances = score(
                frozen,
                "E1",
                frozen.item(query_id),
                library.rows,
                groups=selected,
                normalizer=_normalizer_for(frozen, selected),
            )
            order = perceptual_methods.rank_order(distances)
            ids = [library.ids[int(position)] for position in order]
            relevant = frozen.relevant(query_id)
            hits = [index + 1 for index, item in enumerate(ids[:10]) if item in relevant]
            if ids and ids[0] in relevant:
                hits1 += 1
            firsts.append(hits[0] if hits else 11)
            if ids and ids[0] not in relevant:
                flips.append(f"{query_id}->{ids[0]}")
        rows[name] = {
            "n_descriptors": int(
                len(e1.feature_names(selected, orientation="canonical"))
            ),
            "hit@1": round(hits1 / len(frozen.queries), 4),
            "mean_first_relevant_rank": round(statistics.fmean(firsts), 3),
            "wrong_top1_queries": flips[:6],
        }
    baseline = rows["full"]["hit@1"]
    for block in rows.values():
        block["hit@1_delta_vs_full"] = round(block["hit@1"] - baseline, 4)
    return rows


# ---------------------------------------------------------------- part e2


def part_e2(frozen: Frozen, setting: str) -> dict[str, Any]:
    """C3: the T1 rank-4 located, then the T2 weakness traced to a cost term."""
    return {
        "constants": {
            "DEFAULT_SCALES": list(e2.DEFAULT_SCALES),
            "SALIENCE_K": e2.SALIENCE_K,
            "MIN_SEPARATION": e2.MIN_SEPARATION,
            "GAP_COST": e2.GAP_COST,
            "DEFORMATION_BUDGET": e2.DEFORMATION_BUDGET,
            "PEAK_GAIN": e2.PEAK_GAIN,
            "normalisation": "cost / 2*pi",
        },
        "t1_self_rank": _e2_self_rank(frozen),
        "zero_distance_top_hits": _e2_zero_distance_top1(frozen, setting),
        "event_census": _e2_census(frozen),
        "gap_price": _e2_gap_price(frozen),
        "structure_loss_cases": _e2_structure_loss(frozen, setting),
        "deletion_vs_match": _e2_deletion_vs_match(frozen, setting),
        "scale_filtering": _e2_scales(frozen),
    }


def _e2_self_rank(frozen: Frozen) -> dict[str, Any]:
    """Find the query whose exact target is not rank 1 and show why, row by row."""
    out: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        block = frozen.top("T1", "E2", query_id, 6)
        ranks = {int(row["rank"]): row["candidate_id"] for row in block}
        if ranks.get(1) == query_id:
            continue
        library = frozen.library("T1", query_id)
        distances = score(frozen, "E2", frozen.item(query_id), library.rows)
        self_distance = float(distances[library.ids.index(query_id)])
        tied = [
            (library.ids[int(position)], float(value))
            for position, value in enumerate(distances)
            if abs(float(value) - self_distance) < 1e-12
        ]
        signature = e2.signature(frozen.item(query_id))
        out.append(
            {
                "query_id": query_id,
                "observed_self_rank": min(
                    (int(row["rank"]) for row in block if row["candidate_id"] == query_id),
                    default=None,
                ),
                "self_distance": round(self_distance, 12),
                "self_distance_is_zero": abs(self_distance) < 1e-15,
                "query_signature": signature.summary(),
                "tie_set": [
                    {
                        "candidate_id": item,
                        "distance": round(value, 12),
                        "events": len(e2.signature(frozen.item(item)).events),
                        "family": frozen.meta(item)["family"],
                        "role": frozen.meta(item)["role"],
                        "library_row": library.ids.index(item),
                    }
                    for item, value in tied
                ],
                "tie_break_rule": "np.argsort(kind='stable') -> first row wins (D42)",
                "verdict": _self_rank_verdict(len(tied), abs(self_distance) < 1e-15),
            }
        )
    return {"queries_off_rank_one": out, "n_queries_off_rank_one": len(out)}


def _self_rank_verdict(ties: int, zero: bool) -> str:
    if zero and ties > 1:
        return (
            "not an implementation error: an empty salient sequence aligns with an empty "
            "salient sequence at cost 0.0 by definition, so every shape with no measurable "
            f"turning sits in one tie of {ties} rows and the stable tie-break, not the "
            "score, decides who is first"
        )
    return "needs reading: no zero-distance tie explains this rank"


def _e2_zero_distance_top1(frozen: Frozen, setting: str) -> dict[str, Any]:
    """The same empty-sequence degeneracy, but where it costs a Top-1 rather than a self-rank.

    ``t1_self_rank`` only looks at the query matching itself.  A query with no measurable
    turning also matches *every other* shape with no measurable turning at exactly 0.0,
    and those are different shape classes to the eye — which is how E2 can put a loose
    band first for a filled circle and call the distance perfect.
    """
    groups: list[dict[str, Any]] = []
    digests = contour_digests(frozen)
    for query_id in frozen.queries:
        rows = frozen.top(setting, "E2", query_id, 5)
        zeros = [row for row in rows if abs(float(row["distance"])) < 1e-12]
        if not zeros:
            continue
        query_events = len(e2.signature(frozen.item(query_id)).events)
        candidates = [
            {
                "rank": int(row["rank"]),
                "candidate_id": row["candidate_id"],
                "events": len(e2.signature(frozen.item(row["candidate_id"])).events),
                "family": frozen.meta(row["candidate_id"])["family"],
                "role": frozen.meta(row["candidate_id"])["role"],
                "relevant": row["relevant"] == "1",
                "same_contour_as_query": digests[query_id] == digests[row["candidate_id"]],
            }
            for row in zeros
        ]
        groups.append(
            {
                "query_id": query_id,
                "query_family": frozen.meta(query_id)["family"],
                "query_events": query_events,
                "ranks_at_zero": [item["rank"] for item in candidates],
                "candidates": candidates,
                "all_candidates_eventless": all(item["events"] == 0 for item in candidates),
                "n_same_contour": sum(1 for item in candidates if item["same_contour_as_query"]),
            }
        )
    return {
        "setting": setting,
        "n_queries_with_zero_distance_top_hits": len(groups),
        "zero_hits_that_are_the_same_contour": sum(
            group["n_same_contour"] for group in groups
        ),
        "zero_hits_on_a_genuinely_different_contour": sum(
            1
            for group in groups
            for item in group["candidates"]
            if not item["same_contour_as_query"]
        ),
        "groups": groups,
        "reading": (
            "distance 0.0 here is a property of two empty salient-event sequences aligning "
            "at cost 0, not a claim that the two outlines agree; where "
            "all_candidates_eventless is true the metric has simply run out of signal, and "
            "where same_contour_as_query is true the gallery handed the arm its own "
            "contour back under a different id, which no metric could score differently"
        ),
    }


def _e2_census(frozen: Frozen) -> dict[str, Any]:
    counts = [len(e2.signature(ring).events) for ring in _all_rings(frozen)]
    histogram: dict[str, int] = {}
    for value in counts:
        histogram[str(value)] = histogram.get(str(value), 0) + 1
    queries = [len(e2.signature(frozen.item(q)).events) for q in frozen.queries]
    return {
        "shapes_census": len(counts),
        "event_count_histogram": dict(sorted(histogram.items(), key=lambda kv: int(kv[0]))),
        "share_zero_event": round(sum(1 for c in counts if c == 0) / len(counts), 4),
        "query_event_counts": dict(zip(frozen.queries, queries, strict=True)),
    }


def _all_rings(frozen: Frozen) -> list[np.ndarray]:
    """Gallery contours plus the index rows — both are already the 128-point contract."""
    return [item.contour for item in frozen.gallery.values()] + [
        np.asarray(row, dtype=float) for row in frozen.index_rows
    ]


def _e2_gap_price(frozen: Frozen) -> dict[str, Any]:
    """What the DP actually pays to drop a corner, in the units the score uses."""

    def signature(events: tuple[Any, ...]) -> Any:
        return e2.SalientSignature(
            events=events,
            n_vertices=128,
            scales=e2.DEFAULT_SCALES,
            total_turning=2 * math.pi,
            turning=np.zeros((128, len(e2.DEFAULT_SCALES))),
        )

    def event(sign: int, magnitude: float) -> Any:
        return e2.SalientEvent(
            position=0.0,
            sign=sign,
            magnitude=magnitude,
            scale_index=2,
            persistence=3,
            peak=magnitude,
        )

    rows: dict[str, Any] = {}
    for magnitude in (0.3, 0.6, 1.0, 1.6):
        left = signature((event(+1, magnitude),))
        right = signature(())
        aligned = e2.align_sequences(left, right)
        rows[f"drop_a_{magnitude}_rad_convex_corner"] = {
            "cost": round(aligned["cost"], 6),
            "distance_over_2pi": round(aligned["cost"] / (2 * math.pi), 6),
        }
    # the same money for a corner that exists but is the wrong sign
    flipped = e2.align_sequences(signature((event(+1, 1.0),)), signature((event(-1, 1.0),)))
    rows["match_convex_to_concave_1rad"] = {
        "cost": round(flipped["cost"], 6),
        "distance_over_2pi": round(flipped["cost"] / (2 * math.pi), 6),
        "note": "turning term adds, so a prong read as a valley pays both turns",
    }
    rows["reference"] = {
        "near_max_align": frozen.labels["near_max_align"],
        "comment": (
            "a dropped 1.0 rad corner costs 0.159 in the same units E0 calls "
            "'indistinguishable' at the near-duplicate bar above, so the penalty is not tiny "
            "in absolute terms; the question is whether the notches in this gallery actually "
            "reach 1.0 rad"
        ),
    }
    return rows


def _e2_structure_loss(frozen: Frozen, setting: str) -> list[dict[str, Any]]:
    """The named failures: heart matched without its notch, crescent matched to a band."""
    cases: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        family = frozen.labels["queries"][query_id]["family"]
        if family not in ("heart", "crescent"):
            continue
        ring = frozen.item(query_id)
        query_signature = e2.signature(ring)
        for row in frozen.top(setting, "E2", query_id, 3):
            candidate = frozen.item(row["candidate_id"])
            candidate_signature = e2.signature(candidate)
            aligned = e2.align_sequences(query_signature, candidate_signature)
            signs = _pair_signs(aligned)
            cases.append(
                {
                    "query_id": query_id,
                    "query_family": family,
                    "rank": int(row["rank"]),
                    "candidate_id": row["candidate_id"],
                    "candidate_family": frozen.meta(row["candidate_id"])["family"],
                    "candidate_note": frozen.meta(row["candidate_id"])["note"],
                    "distance": float(row["distance"]),
                    "query_events": len(query_signature.events),
                    "query_concave": query_signature.n_concave,
                    "query_convex": query_signature.n_convex,
                    "candidate_events": len(candidate_signature.events),
                    "candidate_concave": candidate_signature.n_concave,
                    "candidate_convex": candidate_signature.n_convex,
                    "pairs": aligned["pairs"],
                    "unmatched_query_events": aligned["unmatched_left"],
                    "unmatched_candidate_events": aligned["unmatched_right"],
                    "opposite_sign_pairs": signs["opposite"],
                    "drift_beyond_budget": signs["drifted"],
                }
            )
    return cases


def _e2_deletion_vs_match(frozen: Frozen, setting: str) -> dict[str, Any]:
    """Is E2 cheaper when it throws a corner away than when it matches one honestly?

    ``GAP_COST`` prices a deletion at the dropped event's own turning, while a matched
    pair pays magnitude and position difference even at zero drift.  If the first is
    usually smaller, E2's preference for deleting is a property of the constants and not
    of these particular shapes.  Distances are read from C's Top-10; only the alignment
    is re-derived, so this explains a ranking without producing a new one.
    """
    groups: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        relevant = frozen.relevant(query_id)
        query_signature = e2.signature(frozen.item(query_id))
        lost: dict[str, Any] | None = None
        kept: dict[str, Any] | None = None
        for row in frozen.top(setting, "E2", query_id, 10):
            if int(row["rank"]) > 10:
                break
            candidate_id = row["candidate_id"]
            aligned = e2.align_sequences(query_signature, e2.signature(frozen.item(candidate_id)))
            dropped = aligned["unmatched_left"]
            entry = {
                "rank": int(row["rank"]),
                "candidate_id": candidate_id,
                "distance": float(row["distance"]),
                "relevant": candidate_id in relevant,
                "query_events_dropped": dropped,
                "candidate_events_dropped": aligned["unmatched_right"],
                "pair_costs": [round(float(pair["cost"]), 4) for pair in aligned["pairs"]],
                "pair_drift": [round(float(pair["drift"]), 4) for pair in aligned["pairs"]],
            }
            if dropped and (lost is None or entry["distance"] < lost["distance"]):
                lost = entry
            if (
                not dropped
                and not entry["candidate_events_dropped"]
                and entry["relevant"]
                and (kept is None or entry["distance"] < kept["distance"])
            ):
                kept = entry
        if lost is None or kept is None:
            continue
        groups.append(
            {
                "query_id": query_id,
                "query_family": frozen.meta(query_id)["family"],
                "query_events": len(query_signature.events),
                "cheapest_with_a_dropped_event": lost,
                "cheapest_fully_matched_relevant": kept,
                "margin": round(kept["distance"] - lost["distance"], 5),
                "deletion_wins": lost["distance"] < kept["distance"],
            }
        )
    return {
        "setting": setting,
        "scope": "C's stored Top-10 only; alignments re-derived, distances read",
        "queries_compared": len(groups),
        "deletion_wins": sum(1 for group in groups if group["deletion_wins"]),
        "groups": groups,
        "gap_cost_reading": (
            "a dropped query event costs its own signed turning, so the DP compares "
            "|magnitude of the lost corner| against the sum of the pair costs it would "
            "have paid to match it; deletion wins whenever that comparison is cheap"
        ),
    }


def _pair_signs(aligned: dict[str, Any]) -> dict[str, Any]:
    """Is the DP's chosen correspondence sane?  A prong matched to a valley is not.

    ``_trace`` already records ``sign_match`` and ``drift`` per pair, so this reads
    the alignment the score actually used instead of re-deriving it from the events.
    """
    opposite = sum(1 for pair in aligned["pairs"] if not pair["sign_match"])
    drifted = sum(1 for pair in aligned["pairs"] if pair["drift"] > e2.DEFORMATION_BUDGET)
    return {"opposite": opposite, "drifted": drifted}


def _e2_scales(frozen: Frozen) -> dict[str, Any]:
    """How many events each single window reports, on one query per interesting family."""
    out: dict[str, Any] = {}
    wanted = ("heart", "crescent", "star", "blob", "forked")
    seen: set[str] = set()
    for query_id in frozen.queries:
        family = frozen.labels["queries"][query_id]["family"]
        if family not in wanted or family in seen:
            continue
        seen.add(family)
        ring = frozen.item(query_id)
        full = e2.signature(ring)
        per_scale = {
            f"{scale:.4f}": len(e2.signature(ring, scales=(scale,)).events)
            for scale in e2.DEFAULT_SCALES
        }
        out[query_id] = {
            "family": family,
            "events_all_scales": len(full.events),
            "events_per_single_scale": per_scale,
            "signs": [int(event.sign) for event in full.events][:12],
            "scale_index_of_each_event": [event.scale_index for event in full.events][:12],
            "persistence_of_each_event": [event.persistence for event in full.events][:12],
        }
    return out


# ---------------------------------------------------------------- part e3


def first_relevant_rank(
    frozen: Frozen, setting: str, arm: str, query_id: str
) -> int | None:
    """The rank of the first row the frozen labels call relevant, read from results.csv."""
    for row in frozen.top(setting, arm, query_id, 10):
        if row["relevant"] == "1":
            return int(row["rank"])
    return None


def part_e3(frozen: Frozen, setting: str) -> dict[str, Any]:
    """C4: the whole comparison, not only the wins, plus where the seconds go."""
    per_query: list[dict[str, Any]] = []
    for query_id in frozen.queries:
        family = frozen.labels["queries"][query_id]["family"]
        relevant = frozen.relevant(query_id)
        e0_first = first_relevant_rank(frozen, setting, "E0", query_id)
        e3_first = first_relevant_rank(frozen, setting, "E3", query_id)
        per_query.append(
            {
                "query_id": query_id,
                "family": family,
                "e0_first_relevant": e0_first,
                "e3_first_relevant": e3_first,
                "delta": _delta(e0_first, e3_first),
                "direction": _direction(e0_first, e3_first),
                "e0_top1": frozen.top(setting, "E0", query_id, 1)[0]["candidate_id"],
                "e3_top1": frozen.top(setting, "E3", query_id, 1)[0]["candidate_id"],
                "e0_top1_relevant": frozen.top(setting, "E0", query_id, 1)[0]["relevant"] == "1",
                "e3_top1_relevant": frozen.top(setting, "E3", query_id, 1)[0]["relevant"] == "1",
                "n_relevant": len(relevant),
            }
        )

    wins = [row for row in per_query if row["direction"] == "E3 better"]
    losses = [row for row in per_query if row["direction"] == "E0 better"]
    by_family: dict[str, dict[str, int]] = {}
    for row in per_query:
        block = by_family.setdefault(row["family"], {"better_e3": 0, "better_e0": 0, "tie": 0})
        key = {"E3 better": "better_e3", "E0 better": "better_e0", "tie": "tie"}[row["direction"]]
        block[key] += 1
    return {
        "per_query": per_query,
        "e3_better": len(wins),
        "e0_better": len(losses),
        "ties": sum(1 for row in per_query if row["direction"] == "tie"),
        "by_family": by_family,
        "where_e3_loses": losses,
        "timing_breakdown": _e3_timing(frozen, setting),
    }


def _delta(e0: int | None, e3: int | None) -> float | None:
    if e0 is None and e3 is None:
        return None
    return round(float((e0 or 11) - (e3 or 11)), 3)


def _direction(e0: int | None, e3: int | None) -> str:
    if e0 is None and e3 is None:
        return "tie"
    left, right = (e0 or 11), (e3 or 11)
    if right < left:
        return "E3 better"
    if right > left:
        return "E0 better"
    return "tie"


def _e3_timing(frozen: Frozen, setting: str) -> dict[str, Any]:
    """Split one E3 query into descriptor build, rotation grid and assignment."""
    query_id = frozen.queries[0]
    library = frozen.library(setting, query_id)
    ring = frozen.item(query_id)

    started = time.perf_counter()
    contexts = e3.shape_contexts(ring)
    build_seconds = time.perf_counter() - started

    _, full = e3.score_library(ring, library.rows, orientation="sample")
    _, canonical = e3.score_library(ring, library.rows, orientation="canonical")
    _, one_step = e3.score_library(ring, library.rows, orientation="sample", rotation_steps=1)

    # ``transforms`` is one detail dict per library row, and each already carries the
    # rotation the grid settled on — so rotation stability is read off the run that
    # produced the ranking, not off a second experiment.
    rotations = [float(detail["rotation_deg"]) for detail in full["transforms"]]
    searched = {int(detail["rotations_searched"]) for detail in full["transforms"]}

    return {
        "query_id": query_id,
        "library_rows": len(library.ids),
        "context_build_seconds_for_one_contour": round(build_seconds, 6),
        "context_shape": list(contexts.shape),
        "sample_full_grid_seconds": full["elapsed_seconds"],
        "canonical_seconds": canonical["elapsed_seconds"],
        "sample_one_rotation_seconds": one_step["elapsed_seconds"],
        "rotation_grid_share": round(
            1.0 - one_step["elapsed_seconds"] / max(full["elapsed_seconds"], 1e-9), 4
        ),
        "seconds_per_candidate": full["seconds_per_candidate"],
        "rotation_steps": e3.ROTATION_STEPS,
        "rotations_searched_per_candidate": sorted(searched),
        "chi_square_cells_per_pair": int(e3.CONTEXT_POINTS * e3.RADIAL_BINS * e3.ANGULAR_BINS),
        "winning_rotation_deg": {
            "min": round(min(rotations), 3),
            "median": round(statistics.median(rotations), 3),
            "max": round(max(rotations), 3),
            "distinct": len({round(value, 3) for value in rotations}),
        },
        "note": (
            "sample mode runs the grid and one linear_sum_assignment per candidate per "
            "rotation step; canonical mode runs both once. The ratio below is the measured "
            "cost of not assuming an orientation convention, and rotation_grid_share is "
            "the measured cost of the grid itself against the same one-step run."
        ),
        "sample_over_canonical": round(full["elapsed_seconds"] / canonical["elapsed_seconds"], 2),
    }


# ---------------------------------------------------------------- rotation


def part_rotation(frozen: Frozen, setting: str, e3_queries: list[str]) -> dict[str, Any]:
    """Rotate the query itself and watch the frozen target move in the ranking."""
    out: dict[str, Any] = {}
    for arm in ARMS:
        if arm == "E3" and not e3_queries:
            continue
        rows: list[dict[str, Any]] = []
        for query_id in frozen.queries:
            if arm == "E3" and query_id not in e3_queries:
                continue
            relevant = frozen.relevant(query_id)
            library = frozen.library(setting, query_id)
            base = frozen.item(query_id)
            ranks: dict[str, int] = {}
            top1s: set[str] = set()
            angles = E3_ROTATION_ANGLES if arm == "E3" else ROTATION_ANGLES
            for angle in angles:
                # a centred, unit-RMS ring stays centred and unit-RMS under rotation, so
                # the probe rotates it directly rather than re-standardizing and resampling
                rotated = _rotate(base, angle)
                ids = ranked_ids(frozen, arm, rotated, library)
                hits = [index + 1 for index, item in enumerate(ids[:10]) if item in relevant]
                ranks[f"{angle:g}"] = hits[0] if hits else 11
                top1s.add(ids[0])
            values = list(ranks.values())
            rows.append(
                {
                    "query_id": query_id,
                    "family": frozen.labels["queries"][query_id]["family"],
                    "first_relevant_rank_by_angle": ranks,
                    "rank_spread": max(values) - min(values),
                    "distinct_top1": len(top1s),
                }
            )
        spreads = [row["rank_spread"] for row in rows]
        out[arm] = {
            "per_query": rows,
            "mean_rank_spread": round(statistics.fmean(spreads), 3),
            "max_rank_spread": max(spreads),
            "queries_whose_top1_changed": sum(1 for row in rows if row["distinct_top1"] > 1),
            "n_queries": len(rows),
            "angles_deg": list(E3_ROTATION_ANGLES if arm == "E3" else ROTATION_ANGLES),
        }
    return out


def _rotate(ring: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = math.radians(angle_deg)
    matrix = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    return ring @ matrix.T


# ---------------------------------------------------------------- figure


def draw_cases(frozen: Frozen, data: dict[str, Any], out_path: Path, *, dpi: int) -> dict[str, Any]:
    """Four panels, each drawing the exact shapes a table above names.

    One row per arm, four shape cells plus one text cell.  The text column is a real
    axis rather than a figure-level annotation so the caption cannot run off the edge
    of the canvas, which is what clipped every panel in the first draft of this figure.
    """
    plt = _figure_backend()

    panel = _panel_plan(frozen, data)
    shape_columns = max(len(block["cases"]) for block in panel)
    columns = shape_columns + 1
    fig, axes = plt.subplots(
        len(panel),
        columns,
        figsize=(columns * 2.05, len(panel) * 2.15),
        squeeze=False,
    )
    for index, block in enumerate(panel):
        for slot, case in enumerate(block["cases"]):
            _draw_pair(axes[index][slot], case, frozen)
        for slot in range(len(block["cases"]), shape_columns):
            axes[index][slot].axis("off")
        notes = axes[index][shape_columns]
        notes.axis("off")
        notes.text(
            0.0,
            0.98,
            _wrap(block["title"], width=30),
            fontsize=7.6,
            va="top",
            ha="left",
            color="#122033",
            fontweight="bold",
        )
        notes.text(
            0.0,
            0.78,
            _wrap(block["caption"], width=34),
            fontsize=6.2,
            va="top",
            ha="left",
            color="#22334a",
            linespacing=1.45,
        )
    fig.suptitle(
        "Checkpoint D2 - diagnostic cases: every shape below is named in the tables",
        fontsize=10.0,
    )
    fig.text(
        0.012,
        0.015,
        _wrap(
            "red ring = the query as stored; blue fill = the candidate as retrieved; dashed "
            "green = the query re-posed onto the candidate by the shared display aligner "
            "(earthglyph.retrieval.display_alignment), which belongs to no arm and changed no "
            "score. Both shapes are drawn in one shared frame at one scale; nothing is "
            "resampled, smoothed or resized. Captions give the distance the arm reported.",
            width=150,
        ),
        fontsize=6.2,
        color="#40506a",
        linespacing=1.5,
    )
    fig.subplots_adjust(left=0.02, right=0.99, top=0.9, bottom=0.075, wspace=0.08, hspace=0.3)
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {"path": str(out_path), "panels": [block["title"] for block in panel]}


def _panel_plan(frozen: Frozen, data: dict[str, Any]) -> list[dict[str, Any]]:
    material = data.get("material", {"groups": [], "query_duplicates_left_in_library": []})
    # the material panel draws a pair per duplicate: the query with the copy left in its
    # own library, then two ids from the widest group so the cross-family reuse is visible
    material_cases: list[dict[str, Any]] = [
        {
            "query_id": block["query_id"],
            "candidate_id": hit,
            "label": f"same contour | {hit}",
            "arm": "gallery",
        }
        for block in material["query_duplicates_left_in_library"]
        for hit in block["identical_rows_in_library"][:2]
    ]
    if material["groups"]:
        widest = max(material["groups"], key=lambda group: len(group["families_present"]))
        for pair in list(zip(widest["ids"], widest["ids"][1:], strict=False))[:2]:
            material_cases.append(
                {
                    "query_id": pair[0],
                    "candidate_id": pair[1],
                    "label": f"one contour, {len(widest['families_present'])} families | "
                    f"{pair[1]}",
                    "arm": "gallery",
                }
            )
    e0_seen: set[tuple[str, str]] = set()
    e0_cases = []
    digests = contour_digests(frozen)
    for case in data["e0"]["indistinguishable_but_not_relevant"]:
        # a pair the gallery already duplicated is not evidence about E0, and six ids of
        # one contour would draw six identical cells; keep one cell per real comparison
        if case["same_contour_as_query"]:
            continue
        key = (case["query_id"], case["candidate_id"])
        shape_key = (key[0], digests[key[1]])
        if shape_key in e0_seen:
            continue
        e0_seen.add(shape_key)
        e0_cases.append(
            {
                "query_id": case["query_id"],
                "candidate_id": case["candidate_id"],
                "label": f"E0 d={case['distance']:.2e} | {case['candidate_id']}",
                "arm": "E0",
            }
        )
    e0_cases = e0_cases[:4]
    e1_cases = [
        {
            "query_id": case["query_id"],
            "candidate_id": case["top3"][0]["candidate_id"],
            "label": f"E1 top1 | {case['top3'][0]['candidate_id']}",
            "arm": "E1",
        }
        for case in data["e1"]["heart_queries"][:4]
    ]
    e2_cases = [
        {
            "query_id": case["query_id"],
            "candidate_id": case["candidate_id"],
            "label": (
                f"E2 r{case['rank']} d={case['distance']:.3f} | "
                f"concave {case['query_concave']}->{case['candidate_concave']}"
            ),
            "arm": "E2",
        }
        for case in data["e2"]["structure_loss_cases"][:4]
    ]
    e3_cases = [
        {
            "query_id": case["query_id"],
            "candidate_id": case["e3_top1"],
            "label": (
                f"E3 first-rel {case['e3_first_relevant']} vs E0 "
                f"{case['e0_first_relevant']} | {case['e3_top1']}"
            ),
            "arm": "E3",
        }
        for case in data["e3"]["where_e3_loses"]
    ] + [
        {
            "query_id": case["query_id"],
            "candidate_id": case["e3_top1"],
            "label": (
                f"E3 first-rel {case['e3_first_relevant']} vs E0 "
                f"{case['e0_first_relevant']} | {case['e3_top1']}"
            ),
            "arm": "E3",
        }
        for case in data["e3"]["per_query"]
        if case["direction"] == "E3 better"
    ]
    panels = []
    if material_cases:
        panels.append(
            {
                "title": "Gallery first: one contour, several ids",
                "cases": material_cases,
                "caption": (
                    f"{material['identical_contour_groups']} groups of gallery members share a "
                    f"byte-identical contour ({material['members_in_a_group']} of "
                    f"{material['gallery_members']} members), confirmed independently by the "
                    "contour hash and by the origin_ref column. Every arm-level table below has "
                    "to be read against this: a distance of 0.0 between two of these ids is the "
                    "material, not an algorithm missing a structural difference."
                ),
            }
        )
    panels.extend([
        {
            "title": "E0 same boundary, different structure",
            "cases": e0_cases,
            "caption": (
                "E0's closed-form alignment can drive the distance below the "
                f"indistinguishable bar ({frozen.labels['near_max_align']}) for a candidate the "
                "labels call a hard negative. Only pairs on genuinely different contours are "
                f"drawn here: {data['e0']['count_indistinguishable_non_relevant']} such rows "
                f"exist, {data['e0']['same_contour_as_query']} of them are the duplicate ids in "
                "the panel above, and the rest collapse to "
                f"{data['e0']['distinct_comparisons_on_a_different_contour']} distinct shape "
                "comparisons."
            ),
        },
        {
            "title": "E1 heart -> rounded",
            "cases": e1_cases,
            "caption": (
                "The heart queries' Top-1, with the per-group distances printed by the "
                "ablation table. Which group is smallest is which group decided the rank."
            ),
        },
        {
            "title": "E2 lost notch / lost crescent",
            "cases": e2_cases,
            "caption": (
                "Query and candidate event counts side by side: when the candidate has fewer "
                "concave events than the query, the DP paid the gap price and still won."
            ),
        },
        {
            "title": "E3 vs E0 - losses first, then wins",
            "cases": e3_cases,
            "caption": (
                "The first cell is the one query where E0 reaches a relevant variant earlier "
                "than Shape Context does; the rest are where E3 wins. Both directions are "
                "drawn because a table of wins alone cannot support a universal claim."
            ),
        },
    ])
    return panels


def _draw_pair(axis: Any, case: dict[str, Any], frozen: Frozen) -> None:
    query = frozen.item(case["query_id"])
    candidate = frozen.item(case["candidate_id"])
    frame = max(float(np.abs(query).max()), float(np.abs(candidate).max())) * 1.08
    posed, _ = align_for_display(query, candidate)
    axis.fill(query[:, 0], query[:, 1], facecolor="#f6dede", edgecolor=QUERY, linewidth=0.9)
    axis.fill(
        candidate[:, 0], candidate[:, 1], facecolor=CANDIDATE_FILL, edgecolor=CANDIDATE,
        linewidth=0.9,
    )
    axis.plot(
        posed[:, 0], posed[:, 1], linestyle="--", color=GOOD, linewidth=0.9, alpha=0.9
    )
    axis.set_xlim(-frame, frame)
    axis.set_ylim(-frame, frame)
    axis.set_aspect("equal")
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_title(_wrap(case["label"], width=34), fontsize=5.6, color="#22334a")
    for spine in axis.spines.values():
        spine.set_color("#b6c2d2")


def _wrap(text: str, *, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.fill(line, width=width) for line in text.split("\n"))


# ---------------------------------------------------------------- tables


def print_tables(data: dict[str, Any]) -> None:
    """Every number the documents are allowed to quote comes out of here.

    Sections print only if their part ran, so `--parts e2` does not pretend to have
    measured E0.
    """
    if "material" in data:
        _print_material(data["material"])
    if "e0" in data:
        _print_e0(data["e0"])
    if "e1" in data:
        _print_e1(data["e1"])
    if "e2" in data:
        _print_e2(data["e2"])
    if "e3" in data:
        _print_e3(data["e3"])
    if "rotation" in data:
        _print_rotation(data["rotation"])


def _print_material(material: dict[str, Any]) -> None:
    print("\n== gallery geometry (material audit, read before any arm table) ==")
    print(
        f"{material['members_in_a_group']} of {material['gallery_members']} gallery members "
        f"sit in {material['identical_contour_groups']} byte-identical contour groups"
    )
    print(
        f"identical pairs: {material['pairs_total']}, declared in labels.json "
        f"{material['pairs_declared_in_labels']}, undeclared {len(material['pairs_not_declared'])}"
    )
    for group in material["groups"]:
        print(f"   {' | '.join(group['labels'])}")
    for block in material["query_duplicates_left_in_library"]:
        print(
            f"   LEAK {block['query_id']}: the {material['setting']} library for this query "
            f"still holds an identical contour -> {block['identical_rows_in_library']}"
        )
    print(f"   reading: {material['reading']}")


def _print_e0(e0: dict[str, Any]) -> None:
    print("\n== E0 (C1) ==")
    print(f"{'query':<22}{'fam':<11}{'top1 d':>10}{'rel':>5}{'lookalikes':>12}{'not-rel':>9}")
    for row in e0["per_query"]:
        print(
            f"{row['query_id']:<22}{row['family']:<11}"
            f"{row['top1_distance']:>10.4f}{str(row['top1_relevant']):>5}"
            f"{row['lookalikes_below_threshold']:>12}{row['lookalikes_not_relevant']:>9}"
        )
    print(
        f"top1 relevant rate {e0['top1_relevant_rate']} | "
        f"simple-shape rate {e0['simple_family_top1_relevant_rate']} "
        f"(n={e0['n_simple_queries']}) | "
        f"indistinguishable-but-unlabelled {e0['count_indistinguishable_non_relevant']}"
    )
    print(
        f"   of those: {e0['same_contour_as_query']} rows are the same contour under another "
        f"id (a gallery property), {e0['distinct_contours']} involve a different contour; "
        f"the {e0['count_indistinguishable_non_relevant']} rows collapse to "
        f"{e0['distinct_shape_comparisons']} distinct shape comparisons, "
        f"{e0['distinct_comparisons_on_a_different_contour']} of them on shapes that differ"
    )
    print("worst of them (closest distance first):")
    for row in e0["indistinguishable_but_not_relevant"][:8]:
        print(
            f"   {row['query_id']} ~ {row['candidate_id']} "
            f"d={row['distance']:.3e} [{row['candidate_family']}/{row['candidate_role']}] "
            f"{'SAME CONTOUR AS QUERY  ' if row['same_contour_as_query'] else ''}"
            f"{row['note']}"
        )


def _print_e1(e1: dict[str, Any]) -> None:
    print("\n== E1 heart queries (C2) ==")
    for row in e1["heart_queries"]:
        top = row["top3"][0]
        print(
            f"{row['query_id']}: top1 {top['candidate_id']} "
            f"[{top['family']}/{top['role']}{' REL' if top['relevant'] else ''}] "
            f"{top['note']}"
        )
        print(f"   group split {row['group_distances_for_top1']}")
        print(f"   pointed variants {row['pointed_variant_ranks']}")

    print("\n== E1 ablation (C2, queries and gallery frozen) ==")
    print(f"{'config':<20}{'k':>5}{'hit@1':>8}{'delta':>8}{'mean 1st rel':>14}")
    for name, block in e1["ablation"].items():
        print(
            f"{name:<20}{block['n_descriptors']:>5}{block['hit@1']:>8.3f}"
            f"{block['hit@1_delta_vs_full']:>8.3f}"
            f"{block['mean_first_relevant_rank']:>14.2f}"
        )
    shift = e1["normalizer_shift_on_landforms"]
    gallery = e1["normalizer_shift_on_gallery"]
    print(
        f"normalizer on {shift['rows_scored']} landforms: {shift['cells_below_fitted_min']} below"
        f" / {shift['cells_above_fitted_max']} above of {shift['descriptor_cells']} cells "
        f"({shift['cells_outside_fitted_interval_share']:.4%}), worst descriptor "
        f"{shift['worst_descriptor']} at {shift['worst_descriptor_outside_share']:.4%}"
    )
    print(
        f"same check on the fitted corpus itself: {gallery['rows_scored']} rows, "
        f"{gallery['cells_outside_fitted_interval']} cells outside "
        f"(of {gallery['descriptor_cells']})"
    )


def _print_e2(e2: dict[str, Any]) -> None:
    print("\n== E2 self-rank (C3) ==")
    for row in e2["t1_self_rank"]["queries_off_rank_one"]:
        print(
            f"{row['query_id']}: rank {row['observed_self_rank']}, "
            f"self distance {row['self_distance']}, "
            f"events {row['query_signature']['events']} "
            f"-> tie set of {len(row['tie_set'])} rows at the same distance"
        )
        for tie in row["tie_set"]:
            print(
                f"   row {tie['library_row']:>3} {tie['candidate_id']:<24}"
                f"d={tie['distance']:<14.12g} events={tie['events']} "
                f"[{tie['family']}/{tie['role']}]"
            )
        print(f"   verdict: {row['verdict']}")
    zero = e2["zero_distance_top_hits"]
    print(
        f"\n-- Top hits at distance exactly 0.0 ({zero['setting']}): "
        f"{zero['n_queries_with_zero_distance_top_hits']} queries, "
        f"{zero['zero_hits_that_are_the_same_contour']} of the hits are the query's own "
        f"contour under another id, "
        f"{zero['zero_hits_on_a_genuinely_different_contour']} are different shapes --"
    )
    for group in zero["groups"]:
        print(
            f"   {group['query_id']} (events {group['query_events']}) "
            f"ranks {group['ranks_at_zero']} all-eventless={group['all_candidates_eventless']}"
        )
        for item in group["candidates"]:
            print(
                f"      rank {item['rank']} {item['candidate_id']:<24}"
                f"events={item['events']} [{item['family']}/{item['role']}]"
                f"{' REL' if item['relevant'] else ''}"
                f"{'  SAME CONTOUR AS QUERY' if item['same_contour_as_query'] else ''}"
            )
    census = e2["event_census"]
    print(f"event histogram over {census['shapes_census']} shapes: "
          f"{census['event_count_histogram']}")
    print(f"share with zero events: {census['share_zero_event']}")
    print("gap price (what dropping a corner actually costs):")
    for name, block in e2["gap_price"].items():
        if name == "reference":
            continue
        print(f"   {name}: cost {block['cost']} -> distance {block['distance_over_2pi']}")

    print("\n== E2 structure loss on heart/crescent (C3) ==")
    print(
        f"{'query':<20}{'rk':>3}{'candidate':<26}{'d':>8}"
        f"{'q cvx/cc':>10}{'c cvx/cc':>10}{'opp':>5}{'drift':>6}"
    )
    for row in e2["structure_loss_cases"]:
        q_shape = f"{row['query_convex']}/{row['query_concave']}"
        c_shape = f"{row['candidate_convex']}/{row['candidate_concave']}"
        print(
            f"{row['query_id']:<20}{row['rank']:>3}{row['candidate_id']:<26}"
            f"{row['distance']:>8.3f}{q_shape:>10}{c_shape:>10}"
            f"{row['opposite_sign_pairs']:>5}{row['drift_beyond_budget']:>6}"
        )

    compare = e2["deletion_vs_match"]
    print(
        f"\n== E2 deletion vs honest match ({compare['scope']}) =="
    )
    print(
        f"queries compared {compare['queries_compared']}, deletion cheaper on "
        f"{compare['deletion_wins']}"
    )
    print(
        f"{'query':<22}{'lost d':>8}{'kept d':>8}{'margin':>8}{'lost cand':>26}"
    )
    for group in compare["groups"]:
        lost = group["cheapest_with_a_dropped_event"]
        kept = group["cheapest_fully_matched_relevant"]
        print(
            f"{group['query_id']:<22}{lost['distance']:>8.3f}{kept['distance']:>8.3f}"
            f"{group['margin']:>8.3f}{lost['candidate_id']:>26}"
            f"{'  <- deletion wins' if group['deletion_wins'] else ''}"
        )

    print("\n== E2 per-window event counts (C3) ==")
    for item_id, block in e2["scale_filtering"].items():
        print(
            f"{item_id} [{block['family']}]: all scales {block['events_all_scales']}, "
            f"single window {block['events_per_single_scale']}"
        )


def _print_e3(e3: dict[str, Any]) -> None:
    print("\n== E3 vs E0 (C4) ==")
    print(f"{'query':<22}{'fam':<11}{'E0':>5}{'E3':>5}{'dir':>12}")
    for row in e3["per_query"]:
        print(
            f"{row['query_id']:<22}{row['family']:<11}"
            f"{str(row['e0_first_relevant']):>5}{str(row['e3_first_relevant']):>5}"
            f"{row['direction']:>12}"
        )
    print(
        f"E3 better {e3['e3_better']} / E0 better {e3['e0_better']} / "
        f"ties {e3['ties']}; by family {e3['by_family']}"
    )
    timing = e3["timing_breakdown"]
    print(
        f"E3 timing on {timing['query_id']} over {timing['library_rows']} rows: "
        f"sample {timing['sample_full_grid_seconds']} s, "
        f"canonical {timing['canonical_seconds']} s, one rotation step "
        f"{timing['sample_one_rotation_seconds']} s "
        f"(grid share {timing['rotation_grid_share']}, ratio "
        f"{timing['sample_over_canonical']}x, per candidate "
        f"{timing['seconds_per_candidate']} s)"
    )
    print(f"   winning rotation: {timing['winning_rotation_deg']}")


def _print_rotation(rotation: dict[str, Any]) -> None:
    print("\n== rotation probe ==")
    for arm, block in rotation.items():
        print(
            f"{arm}: mean rank spread {block['mean_rank_spread']}, max "
            f"{block['max_rank_spread']}, top1 changed on "
            f"{block['queries_whose_top1_changed']}/{block['n_queries']} queries "
            f"at angles {block['angles_deg']}"
        )
        for row in block["per_query"]:
            if row["rank_spread"] or row["distinct_top1"] > 1:
                print(
                    f"   {arm} {row['query_id']} [{row['family']}]: "
                    f"spread {row['rank_spread']}, distinct top1 "
                    f"{row['distinct_top1']} {row['first_relevant_rank_by_angle']}"
                )


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--gallery-dir", type=Path, default=Path("experiments/p0_perceptual_probe")
    )
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--out-dir", type=Path, default=Path("experiments/p0_perceptual_probe/checkpoint_d")
    )
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--settings", default="T2")
    parser.add_argument("--parts", default="material,e0,e1,e2,e3,rotation")
    parser.add_argument(
        "--e3-rotation-queries",
        default=3,
        type=int,
        help="how many queries E3's rotation probe runs on; it is timed, not assumed",
    )
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument(
        "--from-json",
        action="store_true",
        help="redraw the figure from an existing arm_diagnostics.json; recomputes nothing",
    )
    args = parser.parse_args(argv)

    parts = [part for part in args.parts.split(",") if part]
    settings = [value for value in args.settings.split(",")]
    if len(settings) != 1:
        raise SystemExit("D2 diagnoses one setting at a time so the tables stay comparable")
    setting = settings[0]

    results_path = args.results or (args.gallery_dir / "results.csv")
    frozen = Frozen(args.gallery_dir, args.index, results_path)

    data: dict[str, Any] = {
        "checkpoint": "D2",
        "setting": setting,
        "inputs": {
            "results_csv": str(results_path),
            "queries": len(frozen.queries),
            "gallery_members": len(frozen.gallery),
            "landform_rows": len(frozen.index_ids),
        },
        "frozen_rankings_read_not_recomputed": True,
        "elapsed_seconds": {},
    }
    runners = {
        "material": lambda: part_material(frozen, setting),
        "e0": lambda: part_e0(frozen, setting),
        "e1": lambda: part_e1(frozen, setting),
        "e2": lambda: part_e2(frozen, setting),
        "e3": lambda: part_e3(frozen, setting),
        "rotation": lambda: part_rotation(
            frozen, setting, frozen.queries[: max(0, args.e3_rotation_queries)]
        ),
    }
    for name in parts:
        if name not in runners:
            raise SystemExit(f"unknown part {name!r}; choose from {sorted(runners)}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / "arm_diagnostics.json"
    if args.from_json:
        if not out_path.exists():
            raise SystemExit(f"--from-json needs an existing {out_path}")
        data = json.loads(out_path.read_text(encoding="utf-8"))
        print(f"redrawing the figure from {out_path}; nothing recomputed", flush=True)
    else:
        for name in parts:
            started = time.perf_counter()
            data[name] = runners[name]()
            data["elapsed_seconds"][name] = round(time.perf_counter() - started, 2)
            print(f"[{name}] done in {data['elapsed_seconds'][name]} s", flush=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out_path}")

    if {"e0", "e1", "e2", "e3"} <= set(data):
        figure = draw_cases(frozen, data, args.out_dir / "diagnostic_cases.png", dpi=args.dpi)
        print(f"wrote {figure['path']}")
        if not args.from_json:
            data["figure"] = figure
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print_tables(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
