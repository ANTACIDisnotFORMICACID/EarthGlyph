"""Checkpoint C's runner: library construction, metric math, and the frozen-input guard.

These tests build their own tiny gallery and label tables instead of reading
``experiments/p0_perceptual_probe/gallery.jsonl``, because that file is a gitignored build
product — a test that depended on it could not run on a fresh clone, which is exactly when
the runner's guarantees matter most.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from earthglyph.retrieval.perceptual_methods import CONTOUR_POINTS
from scripts.evaluate_perceptual_methods import (
    CSV_COLUMNS,
    FrozenInputError,
    Item,
    arm_params,
    build_library,
    check_frozen_inputs,
    metric_block,
    result_rows,
    score_run,
    sha256_of,
    summarise,
    top_slots,
)


def ring(n: int = CONTOUR_POINTS, radius: float = 1.0) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.stack([radius * np.cos(theta), radius * np.sin(theta)], axis=1)


def scaled(n: int, radius: float) -> np.ndarray:
    """A distinct contour per fake item, so ids and geometry cannot be confused."""
    return ring(n=n, radius=radius)


def make_item(item_id: str, family: str, role: str, radius: float) -> Item:
    return Item(
        item_id=item_id,
        family=family,
        role=role,
        origin="synthetic",
        contour=scaled(CONTOUR_POINTS, radius),
        metrics={"solidity": 0.9},
    )


GALLERY = {
    "q1": make_item("q1", "star", "query", 1.0),
    "v1": make_item("v1", "star", "variant", 1.02),
    "v2": make_item("v2", "star", "variant", 0.98),
    "n1": make_item("n1", "star", "hard_negative", 1.4),
    "q2": make_item("q2", "blob", "query", 1.0),
    "v3": make_item("v3", "blob", "variant", 1.05),
}

LABELS: dict[str, Any] = {
    "near_max_align": 0.02,
    "queries": {
        "q1": {
            "family": "star",
            "relevant_variants": ["v1", "v2"],
            "library_t1": ["q1", "v1", "v2", "n1"],
            "library_t2_full": ["v1", "v2", "n1", "q2", "v3"],
        },
        "q2": {
            "family": "blob",
            "relevant_variants": ["v3"],
            "library_t1": ["q2", "v3"],
            "library_t2_full": ["q1", "v1", "v2", "n1", "v3"],
        },
    },
}

INDEX_IDS = ["land-1", "land-2"]
INDEX_ROWS = [scaled(CONTOUR_POINTS, 1.7), scaled(CONTOUR_POINTS, 0.6)]


def test_t1_holds_the_exact_target_and_t2_does_not() -> None:
    t1 = build_library("T1", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    t2 = build_library("T2", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    assert t1.ids == ["q1", "v1", "v2", "n1"]
    assert "q1" not in t2.ids
    # row order is the frozen order, never a re-sort: rank_order breaks ties by row (D42)
    assert t2.ids == LABELS["queries"]["q1"]["library_t2_full"]


def test_t3_appends_the_real_index_with_provenance() -> None:
    t3 = build_library("T3", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    gallery_rows = len(LABELS["queries"]["q1"]["library_t2_full"])
    assert len(t3) == gallery_rows + len(INDEX_IDS)
    assert t3.ids[gallery_rows:] == INDEX_IDS
    assert t3.provenance[gallery_rows:] == [t3.provenance[-1]] * 2
    assert t3.provenance[gallery_rows] != t3.provenance[0]
    assert t3.roles[gallery_rows] == "unlabelled_real_shape"
    assert t3.families[gallery_rows] == "real_landform"


def test_a_query_never_scores_against_itself_outside_t1() -> None:
    broken = json.loads(json.dumps(LABELS))
    broken["queries"]["q1"]["library_t2_full"].append("q1")
    with pytest.raises(FrozenInputError, match="must not score against itself"):
        build_library("T2", "q1", broken, GALLERY, INDEX_IDS, INDEX_ROWS)


def test_metric_block_math_on_a_known_ranking() -> None:
    # relevant at ranks 2 and 4 of 5, so precision@2 = 1/2 and @4 = 2/4
    flags = np.array([0, 1, 0, 1, 0])
    block = metric_block(flags, n_relevant=2, n_library=5)
    assert block["hit@1"] == 0.0
    assert block["hit@3"] == 1.0
    assert block["mrr"] == pytest.approx(1.0 / 2)
    assert block["ap"] == pytest.approx((1 / 2 + 2 / 4) / 2)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert block["ndcg@10"] == pytest.approx(dcg / idcg)
    assert block["first_relevant_rank"] == 2.0


def test_metric_block_without_relevant_rows_is_zero_not_nan() -> None:
    block = metric_block(np.zeros(4, dtype=int), n_relevant=0, n_library=4)
    assert block["ap"] == 0.0 and block["mrr"] == 0.0 and block["ndcg@10"] == 0.0


def test_summarise_averages_every_key_and_counts_queries() -> None:
    out = summarise([{"hit@1": 1.0, "ap": 0.25}, {"hit@1": 0.0, "ap": 0.75}])
    assert out["hit@1"] == 0.5 and out["ap"] == 0.5 and out["n_queries"] == 2.0


def test_top_slots_shares_are_a_partition_of_the_head() -> None:
    library = build_library("T3", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    # ids are v1 v2 n1 q2 v3 then the two landform rows, so 5 and 6 are the landforms
    ranked = np.array([0, 1, 4, 5, 6, 2])
    slots = top_slots(
        ranked,
        query_family="star",
        relevant={"v1", "v2"},
        ids=library.ids,
        roles=library.roles,
        families=library.families,
        k=6,
    )
    assert sum(slots.values()) == pytest.approx(1.0)
    assert slots["landform_share"] == pytest.approx(2 / 6)


def test_arm_params_only_reaches_the_arms_that_own_those_knobs() -> None:
    normalizer = object()
    assert arm_params("E0", normalizer=normalizer, e3_orientation="sample") == {}
    assert arm_params("E2", normalizer=normalizer, e3_orientation="sample") == {}
    assert arm_params("E1", normalizer=normalizer, e3_orientation="sample") == {
        "normalizer": normalizer,
        "orientation": "canonical",
    }
    assert arm_params("E3", normalizer=normalizer, e3_orientation="canonical") == {
        "orientation": "canonical"
    }


def test_score_run_scores_every_row_and_excludes_self_from_metrics() -> None:
    library = build_library("T1", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    run = score_run(
        "E0",
        "q1",
        GALLERY["q1"].contour,
        library,
        {},
        allow_mirror=False,
        relevant={"v1", "v2"},
        exclude_from_metrics={"q1"},
    )
    assert run["distances"].shape[0] == len(library)
    assert run["metrics_rows"] == len(library) - 1
    assert "q1" not in [library.ids[int(i)] for i in run["ranked_scored"]]
    # the identical contour is the closest thing in T1, and it is the row metrics drop
    assert library.ids[int(run["ranked"][0])] == "q1"
    assert run["metrics"]["hit@1"] == 1.0


def test_result_rows_writes_top_k_plus_every_relevant_row() -> None:
    library = build_library("T2", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    run = score_run(
        "E0",
        "q1",
        GALLERY["q1"].contour,
        library,
        {},
        allow_mirror=False,
        relevant=set(library.ids),
    )
    rows = result_rows(
        "T2", "E0", "q1", "star", library, run, lookalike=set(), top=2
    )
    ranks = [int(row["rank"]) for row in rows]
    assert ranks == sorted(ranks)
    assert len(rows) == len(library)  # every row is relevant, so none is dropped
    assert set(rows[0]) == set(CSV_COLUMNS)
    assert rows[0]["relevant"] == 1 and rows[0]["in_top_k"] == 1


def test_result_rows_marks_lookalikes_only_when_irrelevant() -> None:
    library = build_library("T2", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    run = score_run(
        "E0", "q1", GALLERY["q1"].contour, library, {}, allow_mirror=False, relevant={"v1"}
    )
    rows = result_rows(
        "T2", "E0", "q1", "star", library, run, lookalike=set(library.ids), top=len(library)
    )
    by_id = {row["candidate_id"]: row for row in rows}
    assert by_id["v1"]["align_indistinguishable_from_query"] == 0  # relevant already
    assert by_id["n1"]["align_indistinguishable_from_query"] == 1


def test_frozen_input_guard_rejects_a_drifted_gallery(tmp_path: Path) -> None:
    good = tmp_path / "gallery.jsonl"
    good.write_text('{"item_id": "a"}\n', encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text("{}", encoding="utf-8")
    manifest = {
        "gallery_version": "1.0",
        "totals": {"items": 1},
        "settings": {},
        "content_sha256": {
            "gallery.jsonl": sha256_of(good),
            "labels.json": sha256_of(labels),
        },
    }
    (tmp_path / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert check_frozen_inputs(tmp_path)["sha256"]["gallery.jsonl"] == sha256_of(good)
    good.write_text('{"item_id": "b"}\n', encoding="utf-8")
    with pytest.raises(FrozenInputError, match="rebuild with"):
        check_frozen_inputs(tmp_path)


def test_results_csv_round_trips_through_the_figure_loader(tmp_path: Path) -> None:
    """The figures read the CSV, so the CSV must survive being written and re-read."""
    from scripts.visualize_perceptual import load_results

    library = build_library("T2", "q1", LABELS, GALLERY, INDEX_IDS, INDEX_ROWS)
    run = score_run(
        "E0", "q1", GALLERY["q1"].contour, library, {}, allow_mirror=False, relevant={"v1"}
    )
    rows = result_rows("T2", "E0", "q1", "star", library, run, lookalike={"n1"}, top=3)
    path = tmp_path / "results.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    cells = load_results(path)
    assert list(cells) == [("T2", "E0", "q1")]
    back = cells[("T2", "E0", "q1")]
    assert [row["rank"] for row in back] == [str(rank) for rank in range(1, len(back) + 1)]
    assert float(back[0]["distance"]) == pytest.approx(
        float(run["distances"][int(run["ranked"][0])])
    )
    assert back[0]["align_indistinguishable_from_query"] in ("0", "1")


def _write_fake_dataset(directory: Path) -> Path:
    """Materialise the tiny gallery as the files the runner insists on reading."""
    records = [
        {
            "item_id": item.item_id,
            "family": item.family,
            "role": item.role,
            "origin": item.origin,
            "contour": item.contour.tolist(),
            "metrics": item.metrics,
        }
        for item in GALLERY.values()
    ]
    gallery = directory / "gallery.jsonl"
    gallery.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    labels = directory / "labels.json"
    labels.write_text(json.dumps(LABELS), encoding="utf-8")
    manifest = directory / "dataset_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "gallery_version": "test",
                "content_sha256": {
                    "gallery.jsonl": sha256_of(gallery),
                    "labels.json": sha256_of(labels),
                },
                "totals": {"members": len(records)},
                "settings": ["T1"],
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_the_runner_and_the_doc_reporter_agree_on_the_schema(tmp_path: Path, capsys) -> None:
    """The tables quoted in notes.md are printed from metrics.json, so the keys must match.

    ``report_checkpoint_c.py`` reads field names the runner writes; nothing but a run ties
    them together, and a silent KeyError there means a document quoting a number has no
    command behind it (D48).  E0 on T1 is cheap enough to run for real inside a test.
    """
    import importlib.util

    from scripts.evaluate_perceptual_methods import main as run_main

    dataset = _write_fake_dataset(tmp_path)
    assert run_main(
        [
            "--gallery-dir",
            str(dataset),
            "--out-dir",
            str(dataset),
            "--settings",
            "T1",
            "--arms",
            "E0",
            "--top",
            "2",
            "--skip-e3-cost",
        ]
    ) == 0
    capsys.readouterr()

    spec = importlib.util.spec_from_file_location(
        "report_checkpoint_c",
        Path("experiments/p0_perceptual_probe/report_checkpoint_c.py"),
    )
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    assert reporter.main(["--dir", str(dataset)]) == 0
    printed = capsys.readouterr().out
    for needle in ("hit@1", "ndcg@10", "top1-lookalike", "exact_target_self_rank", "E0"):
        assert needle in printed
    for query_id in LABELS["queries"]:
        assert query_id in printed


def _lookalike_dataset(tmp_path: Path) -> Path:
    """A gallery where one hard negative is geometrically the same shape as the query."""
    near = {
        "qa": make_item("qa", "star", "query", 1.0),
        "va": make_item("va", "star", "variant", 1.3),
        # A ring 0.05% off the query: E0's alignment calls it the same shape, the frozen
        # label calls it a hard negative.  That disagreement is the whole point.
        "ha": Item(
            item_id="ha",
            family="star",
            role="hard_negative",
            origin="synthetic",
            contour=scaled(CONTOUR_POINTS, 1.0005),
            metrics={"solidity": 0.9},
        ),
    }
    fake_labels = {
        "near_max_align": 0.02,
        "queries": {
            "qa": {
                "family": "star",
                "relevant_variants": ["va"],
                "library_t1": ["qa", "va", "ha"],
                "library_t2_full": ["va", "ha"],
            }
        },
    }
    gallery = tmp_path / "gallery.jsonl"
    gallery.write_text(
        "\n".join(
            json.dumps(
                {
                    "item_id": item.item_id,
                    "family": item.family,
                    "role": item.role,
                    "origin": item.origin,
                    "contour": item.contour.tolist(),
                    "metrics": item.metrics,
                }
            )
            for item in near.values()
        )
        + "\n",
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(fake_labels), encoding="utf-8")
    (tmp_path / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "gallery_version": "test",
                "content_sha256": {
                    "gallery.jsonl": sha256_of(gallery),
                    "labels.json": sha256_of(labels),
                },
                "totals": {"members": 3},
                "settings": ["T2"],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_the_top1_table_widens_its_cells_for_36_character_landform_ids() -> None:
    """T3 Top-1 is often a feature UUID; a fixed cell width let two arms run into each other."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "report_checkpoint_c_width",
        Path("experiments/p0_perceptual_probe/report_checkpoint_c.py"),
    )
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)

    long_id = "1da7c515-eb3a-30b2-a6c0-43774ce4e996"
    metrics = {
        "results": {
            "T3": {
                arm: {
                    "per_query": {
                        "q": {
                            "top1_id": long_id if arm == "E1" else "syn-egg",
                            "top1_relevant": False,
                            "top1_is_label_lookalike": False,
                        }
                    }
                }
                for arm in ("E0", "E1", "E2", "E3")
            }
        }
    }
    line = reporter.top1_table(metrics, "T3").splitlines()[1]
    assert f"{long_id}  E2 " in line, line
    assert f"{long_id}E2" not in line, line


def test_the_lookalike_column_is_filled_for_e0_whatever_arms_are_asked_for(
    tmp_path: Path,
) -> None:
    """E0 defines the marker, so E0's own rows must carry it even when --arms lists E1 first."""
    import csv as _csv

    from scripts.evaluate_perceptual_methods import main as run_main

    dataset = _lookalike_dataset(tmp_path)
    assert run_main(
        [
            "--gallery-dir",
            str(dataset),
            "--out-dir",
            str(dataset),
            "--settings",
            "T2",
            "--arms",
            "E1,E0",
            "--top",
            "3",
            "--skip-e3-cost",
        ]
    ) == 0
    with (dataset / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(_csv.DictReader(handle))
    flagged = {row["arm"] for row in rows if row["align_indistinguishable_from_query"] == "1"}
    assert {"E0", "E1"} <= flagged, sorted(flagged)
    assert all(row["candidate_id"] == "ha" for row in rows
               if row["align_indistinguishable_from_query"] == "1")
    assert all(row["relevant"] == "0" for row in rows
               if row["align_indistinguishable_from_query"] == "1")
