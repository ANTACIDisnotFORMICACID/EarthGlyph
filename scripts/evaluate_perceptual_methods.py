#!/usr/bin/env python
"""Checkpoint C — run E0..E3 over the frozen P0-4.5 split and write the ranking tables.

This is the first time any retrieval metric is computed for these four arms, so the
rules that make the numbers comparable are set *here* and printed *with* the results
(``method_status.json``), rather than living in a comment someone can ignore:

* one input, three libraries.  Every arm scores the same 18 query contours against the
  same candidate rows in the same row order, and the row order comes straight from
  ``labels.json`` — never re-sorted here, because ``rank_order`` breaks ties by row and
  a re-sort would silently change the tie-break (D42).
* no window.  ``perceptual_methods.score_library`` scores every candidate; ``n_scored``
  is asserted against the library length and recorded, so "the coarse filter hid it"
  cannot be mistaken for "the feature missed it".
* E1's scale is fitted once on the frozen 179-member gallery and supplied to all three
  settings.  Left to itself E1 refits on the library it is scoring, which makes its
  distances incomparable across settings — the exact defect Checkpoint B recorded.
* E3 is scored as ``d(query, candidate)``.  The arm is measurably asymmetric
  (780-pair census, median 0.0061), so the direction is a parameter of the experiment,
  not a detail.
* mirror off, smoothing none, ``orientation="canonical"`` for E1 and ``"sample"`` for
  E3 (each arm's own documented default), and the cost of E3's default is measured per
  query instead of being estimated in prose.

Nothing here is a human judgement.  ``labels.json`` relevance is *design intent* — who
the generator built to be structurally alike — so every metric in ``metrics.json`` is
reported beside the statement of what it does and does not measure, and setting T3's
real landform candidates carry no relevance label at all (a pond cannot be a member of
the "star" family; it is not a false positive in any human sense).

Run (order of minutes: E3 in ``sample`` mode is the slow arm — roughly 44 s per query on
the 1018-row T3 library, so budget about 15 minutes for the full table):

    python scripts/evaluate_perceptual_methods.py --top 10
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.retrieval import perceptual_features as e1  # noqa: E402
from earthglyph.retrieval import perceptual_methods  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ("T1", "T2", "T3")
ARMS = ("E0", "E1", "E2", "E3")
GALLERY_LABEL = "Controlled Shape Retrieval — 非真实地理景观"
INDEX_LABEL = "Real Landform Retrieval — Overture Maps"
TOP_REPORTED = (1, 3, 5, 10)


class FrozenInputError(RuntimeError):
    """The dataset on disk is not the dataset the labels were frozen against."""


@dataclass(frozen=True)
class Item:
    """One stored gallery member, with the contour exactly as the builder wrote it."""

    item_id: str
    family: str
    role: str
    origin: str
    contour: np.ndarray
    metrics: dict[str, float]

    @property
    def provenance(self) -> str:
        return GALLERY_LABEL


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str | None:
    """HEAD at run time; provenance is best effort and never fatal."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - a missing git must not hide the metrics
        return None
    return result.stdout.strip() or None


def check_frozen_inputs(gallery_dir: Path) -> dict[str, Any]:
    """Refuse to run on a dataset that drifted from the one the labels froze.

    ``gallery.jsonl`` and the query files are gitignored and rebuilt by a script, so the
    only thing standing between "the split described in the notes" and "whatever happens
    to be on this machine" is this comparison.
    """
    manifest = json.loads((gallery_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    recorded = manifest["content_sha256"]
    measured: dict[str, str] = {}
    for name, expected in recorded.items():
        got = sha256_of(gallery_dir / name)
        measured[name] = got
        if got != expected:
            raise FrozenInputError(
                f"{name} sha256 {got[:12]} != the {expected[:12]} recorded in "
                "dataset_manifest.json; rebuild with "
                "scripts/prepare_perceptual_dataset.py --offline --force before running C"
            )
    return {
        "checked_against": "dataset_manifest.json -> content_sha256",
        "sha256": measured,
        "gallery_version": manifest["gallery_version"],
        "totals": manifest["totals"],
        "settings": manifest["settings"],
    }


def load_gallery(gallery_dir: Path) -> dict[str, Item]:
    records = [
        json.loads(line)
        for line in (gallery_dir / "gallery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = {
        str(record["item_id"]): Item(
            item_id=str(record["item_id"]),
            family=str(record["family"]),
            role=str(record["role"]),
            origin=str(record["origin"]),
            contour=np.asarray(record["contour"], dtype=float),
            metrics={str(k): float(v) for k, v in record["metrics"].items()},
        )
        for record in records
    }
    if len(items) != len(records):
        raise FrozenInputError("gallery.jsonl repeats an item_id")
    return items


def load_landforms(index_dir: Path) -> tuple[list[str], list[np.ndarray], list[dict[str, Any]]]:
    """The real 839-candidate index as (ids, contours, provenance) in stored row order."""
    shapes = np.load(index_dir / "shapes.npy")
    import pandas as pd

    metadata = pd.read_parquet(index_dir / "metadata.parquet")
    if shapes.shape[0] != len(metadata):
        raise FrozenInputError(
            f"{index_dir} has {shapes.shape[0]} shape rows but {len(metadata)} metadata rows"
        )
    ids = [str(value) for value in metadata["candidate_id"]]
    rows = [np.asarray(shapes[index], dtype=float) for index in range(shapes.shape[0])]
    provenance = [
        {
            "label": INDEX_LABEL,
            "candidate_id": ids[index],
            "subtype": str(metadata["subtype"].iloc[index]),
            "class": str(metadata["class"].iloc[index]),
            "area_ha": float(metadata["area_ha"].iloc[index]),
            "lon": float(metadata["lon"].iloc[index]),
            "lat": float(metadata["lat"].iloc[index]),
            "n_holes": int(metadata["n_holes"].iloc[index]),
            "source": "data/index (P0-3, git_commit recorded in its manifest)",
        }
        for index in range(len(ids))
    ]
    return ids, rows, provenance


@dataclass(frozen=True)
class Library:
    """One scoring library: ids, contours, and what each row is."""

    ids: list[str]
    rows: list[np.ndarray]
    provenance: list[str]
    families: list[str]
    roles: list[str]

    def __len__(self) -> int:
        return len(self.ids)


def build_library(
    setting: str,
    query_id: str,
    labels: dict[str, Any],
    gallery: dict[str, Item],
    index_ids: Sequence[str],
    index_rows: Sequence[np.ndarray],
) -> Library:
    """Materialise T1/T2/T3 for one query, in the order ``labels.json`` froze.

    T3 appends the real index *after* the gallery rows so a caption can say which block a
    rank came from; the appended rows are the stored index order, never a filtered subset.
    """
    entry = labels["queries"][query_id]
    if setting == "T1":
        ids = list(entry["library_t1"])
    elif setting in ("T2", "T3"):
        ids = list(entry["library_t2_full"])
    else:  # pragma: no cover - argparse restricts the choices
        raise FrozenInputError(f"unknown setting {setting!r}")
    if setting == "T1":
        if query_id not in ids:
            raise FrozenInputError("T1 must contain the exact target")
    elif query_id in ids:
        raise FrozenInputError(f"{setting}: {query_id} must not score against itself")
    rows = [gallery[item].contour for item in ids]
    provenance = [GALLERY_LABEL] * len(ids)
    families = [gallery[item].family for item in ids]
    roles = [gallery[item].role for item in ids]
    if setting == "T3":
        ids = ids + list(index_ids)
        rows = list(rows) + list(index_rows)
        provenance = provenance + [INDEX_LABEL] * len(index_ids)
        families = families + ["real_landform"] * len(index_ids)
        roles = roles + ["unlabelled_real_shape"] * len(index_ids)
    return Library(ids=ids, rows=rows, provenance=provenance, families=families, roles=roles)


def arm_params(
    arm: str,
    *,
    normalizer: e1.FeatureNormalizer | None,
    e3_orientation: str,
) -> dict[str, Any]:
    """Per-arm parameters, spelled out so ``method_status.json`` can show what was passed.

    ``perceptual_methods.score_library`` hands each arm the same call shape, and the arms
    ignore keywords they do not own; passing one shared dict would therefore run, but the
    recorded policy would no longer be the policy that produced the numbers.
    """
    if arm == "E0":
        return {}
    if arm == "E1":
        return {"normalizer": normalizer, "orientation": "canonical"}
    if arm == "E2":
        return {}
    return {"orientation": e3_orientation}


def metric_block(ranks: np.ndarray, n_relevant: int, n_library: int) -> dict[str, float]:
    """Hit rates, MRR, AP and NDCG@10 for one query's binary relevance vector.

    ``ranks`` is 0/1 over the whole library in ranking order, so AP is a true average
    precision over the full ranking (no cut-off) while NDCG is cut at 10 as reported.
    """
    out: dict[str, float] = {"n_relevant": float(n_relevant)}
    for k in TOP_REPORTED:
        out[f"hit@{k}"] = float(bool(np.any(ranks[: min(k, ranks.shape[0])])))
        out[f"p@{k}"] = float(ranks[: min(k, ranks.shape[0])].sum() / k)
    positions = np.flatnonzero(ranks) + 1
    out["mrr"] = float(1.0 / positions[0]) if positions.size else 0.0
    out["ap"] = (
        float(
            sum(
                (count / int(position)) * 1.0
                for count, position in enumerate(positions, start=1)
            )
            / max(n_relevant, 1)
        )
        if n_relevant
        else 0.0
    )
    dcg = float(sum(1.0 / math.log2(int(rank) + 1) for rank in positions[:10]))
    idcg = float(sum(1.0 / math.log2(rank + 2) for rank in range(min(n_relevant, 10))))
    out["ndcg@10"] = float(dcg / idcg) if idcg > 0 else 0.0
    out["first_relevant_rank"] = float(int(positions[0]) if positions.size else n_library)
    out["library_rows"] = float(n_library)
    return out


def summarise(blocks: list[dict[str, float]]) -> dict[str, float]:
    return {
        key: float(np.mean([block[key] for block in blocks]))
        for key in blocks[0]
        if key != "library_rows"
    } | {"n_queries": float(len(blocks))}


def top_slots(
    ranked: np.ndarray,
    *,
    query_family: str,
    relevant: set[str],
    ids: Sequence[str],
    roles: Sequence[str],
    families: Sequence[str],
    k: int,
) -> dict[str, float]:
    """Who actually occupies the Top-k, counted by label rather than by score.

    ``landform_share`` only means anything in T3; the other three settings have no
    landform rows, so it is reported as 0.0 instead of omitted — a hole in a table invites
    a reader to guess its meaning, and this file has too many of those already.
    """
    head = [int(index) for index in ranked[:k]]
    if not head:
        raise ValueError("empty Top-k")
    return {
        "relevant_share": float(sum(1 for index in head if ids[index] in relevant) / k),
        "same_family_hard_negative_share": float(
            sum(
                1
                for index in head
                if roles[index] == "hard_negative" and families[index] == query_family
            )
            / k
        ),
        "other_family_gallery_share": float(
            sum(
                1
                for index in head
                if families[index] not in (query_family, "real_landform")
            )
            / k
        ),
        "landform_share": float(sum(1 for index in head if families[index] == "real_landform") / k),
    }


def score_run(
    arm: str,
    query_id: str,
    query: np.ndarray,
    library: Library,
    params: dict[str, Any],
    *,
    allow_mirror: bool,
    relevant: Iterable[str],
    exclude_from_metrics: Iterable[str] = (),
) -> dict[str, Any]:
    """Score one (arm, query, setting) and return its distances, ranking and metrics.

    Two rankings come out, on purpose.  ``ranked`` is what the arm returned, row order and
    all, and is what ``results.csv`` prints.  ``ranked_scored`` is that ranking with
    ``exclude_from_metrics`` dropped, and it is what the metrics use: in T1 the library
    contains the query's own contour, which every invariant arm puts at rank 1 for distance
    0, so leaving it in would make T1's Top-1 relevance exactly 0.0 by construction and
    invite a reader to report a plumbing fact as an algorithm result.
    """
    started = time.perf_counter()
    distances, details = perceptual_methods.score_library(
        arm, query, list(library.rows), allow_mirror=allow_mirror, params=params
    )
    elapsed = time.perf_counter() - started
    if distances.shape[0] != len(library):
        raise AssertionError(f"{arm} returned {distances.shape[0]} scores for {len(library)} rows")
    if not np.all(np.isfinite(distances)):
        raise AssertionError(f"{arm} produced a non-finite distance for {query_id}")
    ranked = perceptual_methods.rank_order(distances)
    relevant_set = set(relevant)
    drop = set(exclude_from_metrics)
    keep = np.array([library.ids[index] not in drop for index in ranked])
    if not keep.any():
        raise AssertionError(f"{arm}: excluding {sorted(drop)} emptied the library")
    flags = np.array([1 if library.ids[index] in relevant_set else 0 for index in ranked])
    transforms = details.get("transforms") or []
    row_extra: list[dict[str, Any]] = []
    for index in range(len(library)):
        record = transforms[index] if index < len(transforms) else {}
        row_extra.append(
            {
                "theta_deg": record.get("theta_deg"),
                "shift": record.get("shift"),
                "mirrored": record.get("mirrored"),
                "rotation_offset": record.get("rotation_offset"),
                "query_events": record.get("query_events"),
                "candidate_events": record.get("candidate_events"),
                "pairs": record.get("pairs"),
            }
        )
    return {
        "distances": distances,
        "ranked": ranked,
        "ranked_scored": ranked[keep],
        "flags": flags,
        "flags_scored": flags[keep],
        "metrics_rows": int(keep.sum()),
        "seconds": elapsed,
        "reported_seconds": details.get("elapsed_seconds"),
        "seconds_per_candidate": details.get("seconds_per_candidate"),
        "row_extra": row_extra,
        "metrics": metric_block(flags[keep], len(relevant_set), int(keep.sum())),
        "policy": details.get("policy", {}),
        "details_keys": sorted(details),
        "orientation_mode": details.get("orientation_mode"),
        "normalizer": (details.get("normalizer") or {}).get("fitted_on"),
        "normalizer_supplied": (details.get("normalizer") or {}).get("supplied_by_caller"),
    }


CSV_COLUMNS = [
    "setting",
    "arm",
    "query_id",
    "query_family",
    "rank",
    "candidate_id",
    "provenance",
    "candidate_family",
    "candidate_role",
    "relevant",
    "distance",
    "theta_deg",
    "shift",
    "mirrored",
    "query_events",
    "candidate_events",
    "pairs",
    "arm_seconds_for_query",
    "in_top_k",
    "align_indistinguishable_from_query",
]


def result_rows(
    setting: str,
    arm: str,
    query_id: str,
    query_family: str,
    library: Library,
    run: dict[str, Any],
    lookalike: set[str],
    *,
    top: int,
) -> list[dict[str, Any]]:
    """Top-``top`` rows plus every labelled row, so no relevance label is unranked.

    The full ranking is what the arms actually computed and ``n_scored`` proves it; only
    these rows are written out because a 1018-row x 4-arm x 18-query table is 73k lines of
    text that a reader cannot hold in their head, while the labelled subset is the part
    that answers "where did the things that should match end up?".
    """
    ranked = run["ranked"]
    keep = list(range(min(top, ranked.shape[0])))
    # ``flags`` is aligned to ranking positions, not to library rows: flags[i] answers
    # "is the i-th ranked candidate relevant", so indexing it by a row index would mark
    # the wrong rows REL and silently corrupt the relevance column.
    for position in range(top, ranked.shape[0]):
        if run["flags"][position]:
            keep.append(position)
    out: list[dict[str, Any]] = []
    for position in keep:
        index = int(ranked[position])
        extra = run["row_extra"][index]
        out.append(
            {
                "setting": setting,
                "arm": arm,
                "query_id": query_id,
                "query_family": query_family,
                "rank": position + 1,
                "candidate_id": library.ids[index],
                "provenance": library.provenance[index],
                "candidate_family": library.families[index],
                "candidate_role": library.roles[index],
                "relevant": int(bool(run["flags"][position])),
                "distance": float(run["distances"][index]),
                "theta_deg": extra["theta_deg"],
                "shift": extra["shift"],
                "mirrored": extra["mirrored"],
                "query_events": extra["query_events"],
                "candidate_events": extra["candidate_events"],
                "pairs": extra["pairs"],
                "arm_seconds_for_query": round(float(run["seconds"]), 6),
                "in_top_k": int(position < top),
                "align_indistinguishable_from_query": int(
                    library.ids[index] in lookalike and not bool(run["flags"][position])
                ),
            }
        )
    return out


def fit_gallery_normalizer(gallery_items: Sequence[Item]) -> e1.FeatureNormalizer:
    """E1's per-descriptor scale, fitted once on the frozen 179-member corpus.

    Checkpoint B recorded that E1 refits on the library it scores, which moves the same
    query's distances between settings; fitting here is what makes the three T-tables
    comparable, and it also makes the cost visible — a corpus-fitted minmax scale is a
    claim about *this* corpus, so the out-of-range share of the real index is measured
    below rather than assumed away.
    """
    rows = [item.contour for item in gallery_items]
    matrix = e1.features_for_library(rows, orientation="canonical")
    return e1.fit_normalizer(
        matrix,
        method="minmax",
        names=e1.feature_names(orientation="canonical"),
        fitted_on=(
            f"the frozen {len(gallery_items)}-member P0-4.5 gallery "
            "(all roles), once per run, supplied to T1/T2/T3"
        ),
    )


def out_of_range_report(
    normalizer: e1.FeatureNormalizer, index_rows: Sequence[np.ndarray]
) -> dict[str, Any]:
    """How far the real landform descriptors sit outside the fitted corpus interval."""
    matrix = e1.features_for_library(list(index_rows), orientation="canonical")
    below = np.count_nonzero(matrix < normalizer.lo)
    above = np.count_nonzero(matrix > normalizer.hi)
    total = int(matrix.size)
    per_column = np.maximum(
        (normalizer.lo[None, :] > matrix).mean(axis=0),
        (matrix > normalizer.hi[None, :]).mean(axis=0),
    )
    worst = int(np.argmax(per_column))
    return {
        "rows_scored": int(matrix.shape[0]),
        "descriptor_cells": total,
        "cells_below_fitted_min": int(below),
        "cells_above_fitted_max": int(above),
        "cells_outside_fitted_interval_share": float((below + above) / total),
        "worst_descriptor": normalizer.names[worst],
        "worst_descriptor_outside_share": float(per_column[worst]),
        "reading": (
            "a minmax scale fitted on stylised icons does not bound natural coastlines; "
            "the Euclidean distance in E1 is then partly a distance *outside* the fitted "
            "range, which is a property of this corpus choice, not of the descriptors"
        ),
    }


def e3_direction_probe(
    pairs: Sequence[tuple[np.ndarray, np.ndarray]], **params: Any
) -> dict[str, Any]:
    """Measure the asymmetry that fixing d(query, candidate) is there to control."""
    from earthglyph.retrieval import shape_context_baseline as e3

    gaps = [
        abs(float(e3.distance(a, b, **params)[0]) - float(e3.distance(b, a, **params)[0]))
        for a, b in pairs
    ]
    return {
        "n_pairs": len(gaps),
        "params": dict(params),
        "median_absolute_gap": float(np.median(gaps)) if gaps else 0.0,
        "max_absolute_gap": float(np.max(gaps)) if gaps else 0.0,
        "direction_used_by_this_run": "d(query, candidate): the query supplies the "
        "rotated frames, the candidate supplies the fixed frames (score_library's own loop)",
    }


def e3_orientation_cost(
    query: np.ndarray, libraries: dict[str, list[np.ndarray]]
) -> dict[str, Any]:
    """Time E3 in both of its orientation modes, on the libraries this run used."""
    from earthglyph.retrieval import shape_context_baseline as e3

    out: dict[str, Any] = {}
    for setting, rows in libraries.items():
        measured: dict[str, float] = {}
        for mode in ("sample", "canonical"):
            started = time.perf_counter()
            e3.score_library(query, rows, orientation=mode)
            measured[mode] = time.perf_counter() - started
        out[setting] = {
            "library_rows": len(rows),
            "seconds_sample": round(measured["sample"], 4),
            "seconds_canonical": round(measured["canonical"], 4),
            "ratio_sample_over_canonical": round(
                measured["sample"] / max(measured["canonical"], 1e-9), 2
            ),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gallery-dir", type=Path, default=Path("experiments/p0_perceptual_probe"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_perceptual_probe"))
    parser.add_argument("--top", type=int, default=10, help="rows written per query and arm")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--settings", default=",".join(SETTINGS))
    parser.add_argument(
        "--e3-orientation",
        default="sample",
        choices=("sample", "canonical"),
        help="E3's own rotation policy; 'sample' is the arm default and the slow one",
    )
    parser.add_argument("--limit-queries", type=int, default=0, help="smoke test only")
    parser.add_argument(
        "--allow-mirror", action="store_true", help="default off, as in the product path"
    )
    parser.add_argument(
        "--skip-e3-cost",
        action="store_true",
        help="skip the both-modes timing block (it re-scores one query twice)",
    )
    args = parser.parse_args(argv)

    arms = [item for item in args.arms.split(",") if item]
    settings = [item for item in args.settings.split(",") if item]
    if any(arm not in ARMS for arm in arms):
        raise SystemExit(f"--arms must be a subset of {ARMS}")
    if any(setting not in SETTINGS for setting in settings):
        raise SystemExit(f"--settings must be a subset of {SETTINGS}")

    frozen = check_frozen_inputs(args.gallery_dir)
    labels = json.loads((args.gallery_dir / "labels.json").read_text(encoding="utf-8"))
    gallery = load_gallery(args.gallery_dir)
    query_ids = sorted(labels["queries"])
    if args.limit_queries:
        query_ids = query_ids[: args.limit_queries]

    index_sha: dict[str, str] = {}
    index_ids: list[str] = []
    index_rows: list[np.ndarray] = []
    if "T3" in settings:
        index_ids, index_rows, _ = load_landforms(args.index)
        index_sha = {
            name: sha256_of(args.index / name)
            for name in ("shapes.npy", "features.npy", "metadata.parquet")
        }

    normalizer = fit_gallery_normalizer(list(gallery.values()))
    e1_report: dict[str, Any] = {
        "fitted_on": normalizer.fitted_on,
        "method": normalizer.method,
        "n_samples": normalizer.n_samples,
        "n_descriptors": normalizer.n_descriptors,
        "degenerate_descriptors": list(normalizer.degenerate),
        "orientation": "canonical",
        "supplied_to_settings": settings if "E1" in arms else [],
        "out_of_range_on_real_index": (
            out_of_range_report(normalizer, index_rows) if index_rows else None
        ),
    }

    rows: list[dict[str, Any]] = []
    per_cell: dict[str, dict[str, Any]] = {setting: {} for setting in settings}
    # Candidates E0's own alignment calls indistinguishable from the query, minus the ones
    # the frozen labels already count as relevant.  The gallery deliberately parks human
    # circles as *star* hard negatives, so a matcher that returns a circle for a circle
    # query is labelled a miss; without this set the Top-1 column would read as an
    # algorithm failure when it is a label-design property.  Threshold is labels.json's
    # own near_max_align, the same number the builder used to exclude near-duplicates.
    near_align_max = float(labels["near_max_align"])
    lookalikes: dict[str, dict[str, set[str]]] = {setting: {} for setting in settings}
    started_run = time.perf_counter()
    for setting in settings:
        for query_id in query_ids:
            entry = labels["queries"][query_id]
            library = build_library(
                setting, query_id, labels, gallery, index_ids, index_rows
            )
            relevant = set(entry["relevant_variants"])
            missing = relevant - set(library.ids)
            if missing:
                raise FrozenInputError(f"{setting}/{query_id}: labels promise {sorted(missing)}")
            for arm in arms:
                run = score_run(
                    arm,
                    query_id,
                    gallery[query_id].contour,
                    library,
                    arm_params(arm, normalizer=normalizer, e3_orientation=args.e3_orientation),
                    allow_mirror=args.allow_mirror,
                    relevant=relevant,
                    exclude_from_metrics={query_id} if setting == "T1" else (),
                )
                rows.extend(
                    result_rows(
                        setting,
                        arm,
                        query_id,
                        entry["family"],
                        library,
                        run,
                        lookalikes[setting].get(query_id, set()),
                        top=args.top,
                    )
                )
                slot = per_cell[setting].setdefault(
                    arm, {"per_query": {}, "blocks": [], "slots": []}
                )
                if arm == "E0":
                    lookalikes[setting][query_id] = {
                        library.ids[index]
                        for index in range(len(library))
                        if run["distances"][index] <= near_align_max
                    } - relevant - {query_id}
                seen = lookalikes[setting].get(query_id, set())
                top1_id = library.ids[int(run["ranked_scored"][0])]
                top1_full_id = library.ids[int(run["ranked"][0])]
                self_rank = None
                if setting == "T1":
                    self_rank = int(np.where(np.asarray(library.ids) == query_id)[0][0])
                    self_rank = int(np.flatnonzero(run["ranked"] == self_rank)[0]) + 1
                slot["per_query"][query_id] = {
                    "n_scored": int(len(library)),
                    "seconds": round(float(run["seconds"]), 4),
                    "reported_seconds": run["reported_seconds"],
                    "self_rank": self_rank,
                    "top1_id": top1_id,
                    "top1_full_id": top1_full_id,
                    "top1_family": library.families[int(run["ranked_scored"][0])],
                    "top1_relevant": int(bool(run["flags_scored"][0])),
                    "top1_is_label_lookalike": int(
                        top1_id in seen and not bool(run["flags_scored"][0])
                    ),
                    "align_indistinguishable_non_relevant": len(seen),
                    "first_relevant_rank": run["metrics"]["first_relevant_rank"],
                    "hit@1": run["metrics"]["hit@1"],
                    "hit@5": run["metrics"]["hit@5"],
                    "ap": run["metrics"]["ap"],
                    "mirror_used": any(
                        bool(extra["mirrored"]) for extra in run["row_extra"] if extra["mirrored"]
                    ),
                }
                slot["blocks"].append(run["metrics"])
                slot["slots"].append(
                    top_slots(
                        run["ranked_scored"],
                        query_family=entry["family"],
                        relevant=relevant,
                        ids=library.ids,
                        roles=library.roles,
                        families=library.families,
                        k=args.top,
                    )
                )
                print(
                    f"{setting} {arm} {query_id}: {run['seconds']:.2f}s "
                    f"top1={library.ids[int(run['ranked'][0])]} "
                    f"hit@1={run['metrics']['hit@1']:.0f}",
                    file=sys.stderr,
                    flush=True,
                )

    totals: dict[str, Any] = {}
    for setting in settings:
        totals[setting] = {}
        for arm in arms:
            slot = per_cell[setting][arm]
            self_ranks = [
                record["self_rank"] for record in slot["per_query"].values() if record["self_rank"]
            ]
            totals[setting][arm] = {
                "aggregate": summarise(slot["blocks"]),
                "top_slots": {
                    key: float(np.mean([entry[key] for entry in slot["slots"]]))
                    for key in slot["slots"][0]
                },
                "timing": {
                    "seconds_total": round(
                        float(sum(rec["seconds"] for rec in slot["per_query"].values())), 3
                    ),
                    "seconds_mean_per_query": float(
                        np.mean([rec["seconds"] for rec in slot["per_query"].values()])
                    ),
                    "seconds_per_candidate_mean": float(
                        np.mean(
                            [
                                rec["seconds"] / rec["n_scored"]
                                for rec in slot["per_query"].values()
                            ]
                        )
                    ),
                    "n_scored_per_query": sorted(
                        {int(rec["n_scored"]) for rec in slot["per_query"].values()}
                    ),
                },
                "exact_target_self_rank": (
                    {
                        "values": self_ranks,
                        "rank_1_share": float(np.mean([rank == 1 for rank in self_ranks])),
                        "max": int(max(self_ranks)),
                    }
                    if self_ranks
                    else None
                ),
                "per_query": slot["per_query"],
                "label_pressure": {
                    "top1_is_label_lookalike_share": float(
                        np.mean(
                            [
                                rec["top1_is_label_lookalike"]
                                for rec in slot["per_query"].values()
                            ]
                        )
                    ),
                    "mean_indistinguishable_rows_in_library": (
                        float(np.mean([len(lookalikes[setting][qid]) for qid in slot["per_query"]]))
                        if lookalikes[setting]
                        else None
                    ),
                },
                "top1": {
                    "relevant_share": float(
                        np.mean([rec["hit@1"] for rec in slot["per_query"].values()])
                    ),
                    "by_family": {
                        family: {
                            "hit@1": float(
                                np.mean(
                                    [
                                        rec["hit@1"]
                                        for qid, rec in slot["per_query"].items()
                                        if labels["queries"][qid]["family"] == family
                                    ]
                                )
                            ),
                            "n_queries": float(
                                sum(
                                    1
                                    for qid in slot["per_query"]
                                    if labels["queries"][qid]["family"] == family
                                )
                            ),
                        }
                        for family in sorted(
                            {
                                labels["queries"][qid]["family"]
                                for qid in slot["per_query"]
                                if qid in labels["queries"]
                            }
                        )
                    },
                },
            }

    metrics = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(),
        "command": " ".join([Path(sys.argv[0]).as_posix(), *sys.argv[1:]]) or " ".join(sys.argv),
        "wall_seconds": round(time.perf_counter() - started_run, 3),
        "frozen_inputs": {**frozen, "index_sha256": index_sha or None},
        "protocol": {
            "arms": arms,
            "settings": settings,
            "queries": query_ids,
            "top_written": args.top,
            "allow_mirror": args.allow_mirror,
            "tie_break": "stable argsort, ties by library row order (D42)",
            "coarse_window": "none: every candidate is scored by every arm",
            "t1_metric_scope": (
                "T1's library holds the query's own contour, which an invariant arm ranks "
                "first at distance 0; the relevance metrics exclude that one row (otherwise "
                "T1 Top-1 would be 0.0 by construction) while exact_target_self_rank and "
                "results.csv report the full ranking"
            ),
            "contour_points": perceptual_methods.CONTOUR_POINTS,
            "e3_direction": "d(query, candidate)",
            "labels": "labels.json relevance = design intent, frozen before any arm ran",
        },
        "e1_normalizer": e1_report,
        "e3_orientation_cost": None,
        "e3_direction_probe": None,
        "results": totals,
        "claim_limits": [
            "no human judgement is involved anywhere in this file; these are not accuracy "
            "numbers against perceived similarity",
            "labels.json relevance is the generator's design intent (same family, role=variant), "
            "not a human ground truth",
            "T1 is an implementation sanity check: the query's own contour is in the library, so "
            "exact_target_self_rank measures invariance, while its relevance metrics reuse the "
            "family's variants",
            "T3 real landform candidates carry no relevance label: a pond cannot belong to the "
            "star family, so a landform in the Top-k is not a false positive in any human sense. "
            "T3 measures how far the labelled variants survive being mixed with 839 unlabelled "
            "shapes, and nothing more",
            "E1's distances are only comparable inside this one corpus-fitted normalizer; they are "
            "not comparable to a Checkpoint B self-fitted number",
            "E3's score is asymmetric; the run fixes d(query, candidate) and e3_direction_probe "
            "measures what the other order would have cost",
            "timings are this machine's wall clock and will not reproduce elsewhere; the "
            "scores will",
        ],
    }

    if "E3" in arms and not args.skip_e3_cost and query_ids:
        first = query_ids[0]
        probe_rows = [
            gallery[item].contour
            for item in labels["queries"][first]["relevant_variants"][:6]
        ]
        query = gallery[first].contour
        metrics["e3_direction_probe"] = e3_direction_probe(
            list(zip([query] * len(probe_rows), probe_rows, strict=True)),
            orientation=args.e3_orientation,
        )
        if not args.skip_e3_cost:
            cost_libraries = {
                setting: build_library(
                    setting, first, labels, gallery, index_ids, index_rows
                ).rows
                for setting in settings
            }
            metrics["e3_orientation_cost"] = e3_orientation_cost(query, cost_libraries)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (args.out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    import importlib

    arm_status: dict[str, Any] = {}
    for arm in arms:
        module = importlib.import_module(perceptual_methods.METHOD_MODULES[arm])
        passed = arm_params(arm, normalizer=normalizer, e3_orientation=args.e3_orientation)
        arm_status[arm] = {
            "module": perceptual_methods.METHOD_MODULES[arm],
            "policy": module.describe_policy(),
            "params_passed": {
                key: (
                    f"FeatureNormalizer(method={normalizer.method}, "
                    f"n_samples={normalizer.n_samples}, n_descriptors={normalizer.n_descriptors})"
                    if key == "normalizer"
                    else value
                )
                for key, value in passed.items()
            },
            "settings_scored": settings,
            "n_scored_per_query": {
                setting: totals[setting][arm]["timing"]["n_scored_per_query"]
                for setting in settings
            },
        }
    status = {
        "checkpoint": "C",
        "git_commit": metrics["git_commit"],
        "command": metrics["command"],
        "contract": {
            "module": "earthglyph.retrieval.perceptual_methods",
            "contour_points": perceptual_methods.CONTOUR_POINTS,
            "ranking": "smaller distance is closer; ties broken by row order",
            "coarse_window": "none",
        },
        "arms": arm_status,
    }
    (args.out_dir / "method_status.json").write_text(
        json.dumps(status, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "rows_written": len(rows),
                "csv": csv_path.as_posix(),
                "wall_seconds": metrics["wall_seconds"],
                "headline": {
                    setting: {
                        arm: totals[setting][arm]["timing"]["seconds_mean_per_query"]
                        for arm in arms
                    }
                    for setting in settings
                },
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

