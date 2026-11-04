#!/usr/bin/env python
"""CLI: light analysis of the polygons set aside by the subtype allowlist (Task F).

``data/processed/pending_analysis.parquet`` holds the polygonal features that
were **not** deleted because their Overture ``subtype`` is outside the allowlist
(D6/D19): untyped ``water``, ``river``, ``human_made``, ``stream``, ``canal``.
This script measures what is in there and what adding it would do to the
candidate library — it does **not** add anything to the index and does not
modify the pending file. The expansion decision belongs to P0-4, where real
queries can say whether the missing classes actually cause failures.

Each part goes through the *same* standardization chain as the released index,
so the numbers are comparable instead of being a second definition:

* geometry health: valid / repaired, part count, holes, metric area;
* the four geometry statistics and the Fourier descriptor of the outline;
* how far each pending outline sits from the existing candidates in the index
  descriptor space — the only honest way to say whether it brings new shapes or
  more of the same.

Outputs (under --out-dir)
-------------------------
``pending_summary.json``      counts, distributions, nearest-neighbour gap
``pending_parts.csv``         one row per measured part, with real coordinates
``pending_distribution.png``  pending vs indexed shape distributions

Example
-------
python scripts/analyze_pending.py --out-dir experiments/p0_feature_probe
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe_shared import projection_from_manifest  # noqa: E402

from earthglyph.data.download import sha256_of  # noqa: E402
from earthglyph.data.index import load_index  # noqa: E402
from earthglyph.data.prepare import load_contours  # noqa: E402
from earthglyph.features.fourier import batch_harmonic_amplitudes  # noqa: E402
from earthglyph.features.geometry_stats import (  # noqa: E402
    FEATURE_COLUMNS,
    batch_shape_metrics,
    shape_metrics,
)
from earthglyph.geometry.extraction import exterior_ring, hole_count, split_feature  # noqa: E402
from earthglyph.geometry.filtering import DEFAULT_MIN_AREA_M2  # noqa: E402
from earthglyph.geometry.resampling import build_contour  # noqa: E402

METRIC_COLUMNS = list(FEATURE_COLUMNS)


def pending_parts(
    frame: pd.DataFrame, projection: Any, n_points: int
) -> tuple[pd.DataFrame, np.ndarray, dict[str, int]]:
    """One row per polygonal part of every pending feature, with real coordinates.

    Returns the part table, the aligned (M, N, 2) contour stack and the health
    counters. A part that cannot be standardized keeps its row with an ``error``
    and NaN metrics — counted and reported, never dropped in silence; such rows
    are excluded from the contour stack, so ``rings`` and the metric rows stay
    aligned by construction.
    """
    health = {
        "features": int(len(frame)),
        "parts": 0,
        "invalid_before_repair": 0,
        "repaired": 0,
        "no_polygonal_part": 0,
        "below_min_area": 0,
        "standardization_failed": 0,
    }
    rows: list[dict[str, Any]] = []
    rings: list[np.ndarray] = []
    for record in frame.to_dict("records"):
        geometry = shapely.from_wkb(record["wkb"])
        if geometry is None or geometry.is_empty:
            health["no_polygonal_part"] += 1
            continue
        if not geometry.is_valid:
            health["invalid_before_repair"] += 1
        parts = split_feature(geometry)
        if not parts:
            health["no_polygonal_part"] += 1
            continue
        if any(part.repair.repaired for part in parts):
            health["repaired"] += 1
        for part in parts:
            health["parts"] += 1
            metric = projection.project_geometry(part.polygon)
            area_m2 = float(metric.area)
            anchor = part.polygon.representative_point()
            row: dict[str, Any] = {
                "pending_id": (
                    str(record["source_id"])
                    if part.n_parts == 1
                    else f"{record['source_id']}-part{part.part_index}"
                ),
                "source_id": str(record["source_id"]),
                "part_index": int(part.part_index),
                "n_parts_of_feature": int(part.n_parts),
                "subtype": str(record["subtype"] or "<null>"),
                "class": str(record["class"] or "<null>"),
                "lon": round(float(anchor.x), 6),
                "lat": round(float(anchor.y), 6),
                "area_m2": area_m2,
                "area_ha": area_m2 / 1e4,
                "n_holes": hole_count(part.polygon),
                "error": None,
            }
            if area_m2 < DEFAULT_MIN_AREA_M2:
                health["below_min_area"] += 1
            try:
                contour = build_contour(exterior_ring(metric), n_points)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                health["standardization_failed"] += 1
                row["error"] = f"{type(exc).__name__}: {exc}"
                row.update(dict.fromkeys(METRIC_COLUMNS, np.nan))
                rows.append(row)
                continue
            row.update(shape_metrics(contour.points).as_dict())
            rows.append(row)
            rings.append(contour.points)
    return pd.DataFrame(rows), np.stack(rings), health


def descriptor_gap(
    parts: pd.DataFrame, rings: np.ndarray, index_features: np.ndarray, n_harmonics: int
) -> dict[str, Any]:
    """Distance from each pending outline to the nearest released candidate.

    Only the Fourier group is used: the geometry group is not a metric space a
    hand-drawn sketch would live in, and concatenating the groups here would
    hide which signal is doing the work. Rows whose contour could not be
    standardized have no ring and stay unlabelled.
    """
    ok = parts[parts["error"].isna()]
    pending = batch_harmonic_amplitudes(rings, n_harmonics)
    library = index_features[:, :n_harmonics].astype(np.float64)
    distance = np.sqrt(((pending[:, None, :] - library[None, :, :]) ** 2).sum(axis=2))
    nearest = distance.min(axis=1)
    parts["nearest_candidate_l2"] = np.nan
    parts["nearest_candidate_row"] = pd.array([None] * len(parts), dtype="Int64")
    parts.loc[ok.index, "nearest_candidate_l2"] = np.round(nearest, 6)
    parts.loc[ok.index, "nearest_candidate_row"] = distance.argmin(axis=1)
    library_self = np.sqrt(((library[:, None, :] - library[None, :, :]) ** 2).sum(axis=2))
    diagonal = np.arange(library_self.shape[0])
    library_self[diagonal, diagonal] = np.inf
    own_nearest = library_self.min(axis=1)
    p90_own = float(np.percentile(own_nearest, 90))
    return {
        "descriptor_group": "fourier",
        "n_harmonics": int(n_harmonics),
        "nearest_candidate_l2": {
            key: round(float(np.percentile(nearest, q)), 5)
            for key, q in (("p10", 10), ("p50", 50), ("p90", 90), ("max", 100))
        },
        "indexed_library_own_spacing_p90": round(p90_own, 5),
        "share_farther_than_library_spacing": round(float((nearest > p90_own).mean()), 4),
        "reading": (
            "a pending outline whose nearest released candidate is farther than "
            "the library's own p90 spacing carries a shape the index does not have"
        ),
    }


def distribution(parts: pd.DataFrame) -> dict[str, Any]:
    """Percentiles of the four geometry statistics plus the real area range."""
    out: dict[str, Any] = {
        column: {
            key: round(float(np.nanpercentile(parts[column].to_numpy(dtype=float), q)), 4)
            for key, q in (("p5", 5), ("p50", 50), ("p95", 95))
        }
        for column in METRIC_COLUMNS
    }
    area = parts["area_ha"].to_numpy(dtype=float)
    out["area_ha"] = {
        key: round(float(np.percentile(area, q)), 4)
        for key, q in (("min", 0), ("p50", 50), ("p90", 90), ("max", 100))
    }
    out["rows"] = int(len(parts))
    return out


def figure(
    parts: pd.DataFrame, indexed: pd.DataFrame, features: pd.DataFrame, out_path: Path
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = [
        ("compactness", (0.0, 1.0)),
        ("solidity", (0.0, 1.0)),
        ("aspect_ratio", (1.0, 8.0)),
        ("diameter_fill", (0.0, 1.0)),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.0))
    for position, (column, span) in enumerate(panels):
        axis = axes.flat[position]
        bins = np.linspace(span[0], span[1], 26)
        axis.hist(
            indexed[column].to_numpy(dtype=float), bins=bins, alpha=0.6, label="indexed candidates"
        )
        axis.hist(
            parts[column].to_numpy(dtype=float), bins=bins, alpha=0.55, label="pending parts"
        )
        axis.set_title(column)
        axis.set_xlim(span)
        if position == 0:
            axis.legend(fontsize=7)
    axis = axes.flat[4]
    bins = np.linspace(-1.2, 4.2, 30)
    axis.hist(np.log10(indexed["area_ha"].to_numpy(dtype=float)), bins=bins, alpha=0.6)
    axis.hist(np.log10(parts["area_ha"].to_numpy(dtype=float)), bins=bins, alpha=0.55)
    axis.set_title("log10 area (ha)")
    axis = axes.flat[5]
    counts = features["subtype"].value_counts()
    axis.barh([str(key) for key in counts.index], counts.to_numpy(dtype=float), color="#7d6608")
    for position, value in enumerate(counts.to_numpy(dtype=float)):
        axis.text(value, position, f" {int(value)}", va="center", fontsize=7)
    axis.set_title("pending features by subtype")
    axis.set_xlabel("features")
    fig.suptitle(
        "P0-3 Task F - pending_analysis.parquet measured, NOT added to the index "
        "(expansion decision deferred to P0-4)",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pending", type=Path, default=Path("data/processed/pending_analysis.parquet")
    )
    parser.add_argument("--contours", type=Path, default=Path("data/processed/contours.parquet"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_feature_probe"))
    args = parser.parse_args(argv)

    started = time.perf_counter()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    features_frame = pd.read_parquet(args.pending)
    contours = load_contours(args.contours)
    source_manifest = json.loads(
        (args.contours.parent / "data_manifest.json").read_text(encoding="utf-8")
    )
    index_manifest = json.loads((args.index / "data_manifest.json").read_text(encoding="utf-8"))
    index_features, index_shapes, _ = load_index(args.index)
    n_points = int(index_manifest["index"]["n_points"])
    n_harmonics = int(index_manifest["config"]["n_harmonics"])
    projection = projection_from_manifest(source_manifest)

    parts, rings, health = pending_parts(features_frame, projection, n_points)
    gap = descriptor_gap(parts, rings, index_features, n_harmonics)

    indexed_metrics = batch_shape_metrics(np.asarray(index_shapes, dtype=float))
    indexed = contours.loc[:, ["candidate_id", "subtype", "area_ha"]].copy()
    for position, column in enumerate(METRIC_COLUMNS):
        indexed[column] = indexed_metrics[:, position]

    measured = parts.dropna(subset=METRIC_COLUMNS, how="all")
    per_subtype: dict[str, Any] = {}
    for subtype, group in measured.groupby("subtype", sort=True):
        per_subtype[str(subtype)] = {
            "features": int(group["source_id"].nunique()),
            "parts": int(len(group)),
            "area_ha_median": round(float(group["area_ha"].median()), 4),
            **{
                f"{column}_median": round(float(group[column].median()), 4)
                for column in METRIC_COLUMNS
            },
            "nearest_candidate_l2_median": round(float(group["nearest_candidate_l2"].median()), 5),
            "share_farther_than_library_spacing": round(
                float(
                    (group["nearest_candidate_l2"] > gap["indexed_library_own_spacing_p90"]).mean()
                ),
                4,
            ),
        }

    summary = {
        "stage": "P0-3 Task F pending-class review (analysis only, NOT added to the index)",
        "not_a_retrieval_result": True,
        "decision_owner": "P0-4 - expand only if real queries fail for lack of a class",
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "deterministic": True,
        "random_seed": None,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "inputs": {
            "pending_path": args.pending.as_posix(),
            "pending_sha256": sha256_of(args.pending),
            "pending_rows": int(len(features_frame)),
            "pending_file_modified": False,
            "contours_path": args.contours.as_posix(),
            "index_path": args.index.as_posix(),
            "index_release": index_manifest["source"]["source_release"],
            "index_candidates": int(index_features.shape[0]),
            "standardization": {
                "n_points": n_points,
                "n_harmonics": n_harmonics,
                "same_chain_as_index": "build_contour(exterior_ring(projected part))",
            },
        },
        "health": health,
        "by_subtype_features": {
            str(key): int(value) for key, value in features_frame["subtype"].value_counts().items()
        },
        "by_class_features": {
            str(key): int(value) for key, value in features_frame["class"].value_counts().items()
        },
        "pending_shape_distribution": distribution(measured),
        "indexed_shape_distribution": distribution(indexed),
        "descriptor_gap_to_library": gap,
        "per_subtype": per_subtype,
        "would_add": {
            "parts_above_min_area": int((measured["area_m2"] >= DEFAULT_MIN_AREA_M2).sum()),
            "min_area_m2": DEFAULT_MIN_AREA_M2,
            "note": (
                "parts clearing the released minimum-area filter; the subtype "
                "allowlist and duplicate resolution would still apply, and "
                "river/stream outlines are linear features whose closed outline is "
                "a poor target for a shape query"
            ),
        },
        "standardization_failures": [
            {
                "pending_id": str(record["pending_id"]),
                "subtype": str(record["subtype"]),
                "area_ha": round(float(record["area_ha"]), 4),
                "error": str(record["error"]),
            }
            for record in parts[parts["error"].notna()].to_dict("records")
        ],
        "recommendation": {
            "add_now": False,
            "reason": (
                "no retrieval experiment has shown a missing-class failure yet, and "
                "the pending classes are dominated by linear features whose closed "
                "outline is not what a hand-drawn shape query describes"
            ),
            "revisit_in": "P0-4 (real hand-drawn queries) and P0-6",
        },
        "reproduce": {
            "command": "python "
            + " ".join(shlex.quote(item) for item in [sys.argv[0], *(argv or sys.argv[1:])]),
            "note": "read-only over pending_analysis.parquet; writes only --out-dir",
        },
    }

    figure(measured, indexed, features_frame, args.out_dir / "pending_distribution.png")
    parts.to_csv(args.out_dir / "pending_parts.csv", index=False)
    out_path = args.out_dir / "pending_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(health, indent=2))
    print(json.dumps(gap, indent=2))
    print(json.dumps(per_subtype, indent=2))
    print(json.dumps(summary["would_add"], indent=2))
    print(f"summary written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
