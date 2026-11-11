"""Tests for the D3 two-stage retriever — the contract the brief wrote, pinned.

``scripts/hybrid_e0_e3_retrieval.py`` answers "is E0 coarse + E3 re-rank worth it", and
the answer is only evidence while four properties hold, so each one gets a test rather
than a paragraph:

* E0 chooses *membership* and nothing else — its distance must not appear in the final
  order, not as a blend and not as a tie-break;
* the descriptor cache must be the *same* E3, bit for bit, or the speed claim is a
  different algorithm;
* a window that throws away a reference answer has to say which candidate, at which E0
  rank, in which query — a coverage percentage alone would hide the failure;
* the window is capped at the library, so "Top-200" on a 177-row library is Full-E3 and
  must not be reported as a shortcut that worked.

The real gallery is not needed here: a stub frozen library of 128-point rings keeps
these tests in seconds. ``E3_ORIENTATION`` is switched to ``canonical`` for speed only;
a separate test asserts the shipped run still uses ``sample`` with the 24-step grid.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("matplotlib")  # every test here renders figures

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hybrid_e0_e3_retrieval.py"
SAMPLES = 128


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hyb() -> Any:
    module = _load("hybrid_e0_e3_retrieval", SCRIPT)
    module.E3_ORIENTATION = "canonical"
    return module


# ---------------------------------------------------------------- tiny fixtures


def ring(kind: str, points: int = SAMPLES) -> np.ndarray:
    """A closed ring with an obvious signature, so E3 has something to disagree about."""
    t = np.linspace(0.0, 2.0 * np.pi, points, endpoint=False)
    if kind == "circle":
        radius = np.ones_like(t)
    elif kind == "square":
        radius = 1.0 / np.maximum(np.abs(np.cos(t)), np.abs(np.sin(t)))
        radius = radius / radius.max()
    elif kind == "star":
        radius = 1.0 + 0.5 * np.cos(5.0 * t)
    elif kind == "crescent":
        radius = 1.0 + 0.4 * np.cos(t)
    elif kind == "blob":
        radius = 1.0 + 0.05 * np.sin(3.0 * t)
    elif kind == "noise":
        generator = np.random.default_rng(7)
        radius = 1.0 + 0.02 * generator.standard_normal(points)
    else:  # pragma: no cover - guarded by the caller
        raise AssertionError(kind)
    radius = radius / np.sqrt(np.mean(radius**2))
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


class StubFrozen:
    """The five methods the runner calls on ``Frozen``, over a hand-built library."""

    def __init__(
        self,
        members: dict[str, str],
        queries: dict[str, dict[str, Any]],
        library_by_query: dict[str, list[str]],
    ) -> None:
        self.shapes = {item_id: ring(kind) for item_id, kind in members.items()}
        self.gallery = dict(members)
        self.queries = list(queries)
        self._queries = queries
        self._library_by_query = library_by_query

    def item(self, item_id: str) -> np.ndarray:
        return self.shapes[item_id]

    def library(self, setting: str, query_id: str) -> Any:
        from evaluate_perceptual_methods import Library

        ids = self._library_by_query[query_id]
        return Library(
            ids=list(ids),
            rows=[self.shapes[item_id] for item_id in ids],
            provenance=["Controlled Shape Retrieval"] * len(ids),
            families=[self._queries[query_id]["family"]] * len(ids),
            roles=["variant"] * len(ids),
        )

    def relevant(self, query_id: str) -> set[str]:
        return set(self._queries[query_id]["relevant"])

    def meta(self, item_id: str) -> dict[str, str]:
        return {"family": "star", "role": "variant", "note": f"kind={self.gallery[item_id]}"}


@pytest.fixture(scope="module")
def stub() -> StubFrozen:
    """Four queries over a fifteen-member library, with two byte-identical rows.

    ``dup-a`` and ``dup-b`` are the same contour under two ids, which is the only way to
    make E3 produce an exact tie and therefore to see which order the tie-break uses.
    Query ids are library members of other queries, as they are in the real gallery.
    """
    members = {
        "q-star": "star",
        "q-circle": "circle",
        "q-crescent": "crescent",
        "q-blob": "blob",
        "dup-a": "star",
        "dup-b": "star",
        "circle": "circle",
        "circle-2": "circle",
        "square": "square",
        "crescent": "crescent",
        "crescent-2": "crescent",
        "blob": "blob",
        "blob-2": "blob",
        "noise": "noise",
        "noise-2": "noise",
        "star-2": "star",
    }
    ids = list(members)
    queries = {
        "q-star": {"family": "star", "relevant": {"dup-a", "dup-b", "star-2"}},
        "q-circle": {"family": "blob", "relevant": {"circle", "circle-2"}},
        "q-crescent": {"family": "crescent", "relevant": {"crescent", "crescent-2"}},
        "q-blob": {"family": "blob", "relevant": {"blob", "blob-2"}},
    }
    library_by_query = {
        query_id: [item for item in ids if item != query_id] for query_id in queries
    }
    return StubFrozen(members, queries, library_by_query)


# ---------------------------------------------------------------- the E0 rule


def test_the_window_is_ordered_by_e3_distance_alone(hyb: Any, stub: StubFrozen) -> None:
    entry, _ = hyb.run_query(stub, "T2", "q-star", (6,))
    arm = entry["arms"]["E0->E3@6"]
    shortlist = arm["ids"][: arm["window"]]
    distances = [arm["distance_by_id"][item] for item in shortlist]
    assert distances == sorted(distances)


def test_scrambling_the_e0_distances_moves_no_hybrid_answer(hyb: Any, stub: StubFrozen) -> None:
    """E0 must be a filter, so its numbers are free to be wrong and nothing changes.

    The order it publishes is what the cut uses; the distance vector behind that order
    is never read by the re-ranker.  Replacing it with noise while keeping the order
    has to leave every hybrid ranking byte-identical — if any blend or any E0-based
    tie-break existed, this would move something.
    """
    base = hyb.e0_pass(stub, "T2", "q-star")
    reference = {
        window: hyb.hybrid_pass(base, window, cache)["ids"]
        for window, cache in _caches(hyb, base)
    }
    base["distances"] = np.linspace(9.0, 0.0, base["distances"].shape[0]) ** 3
    scrambled = {
        window: hyb.hybrid_pass(base, window, cache)["ids"]
        for window, cache in _caches(hyb, base)
    }
    assert reference == scrambled


def _caches(hyb: Any, base: dict[str, Any]) -> list[tuple[int, Any]]:
    import earthglyph.retrieval.shape_context_baseline as e3

    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    return [(4, cache), (8, cache)]


def test_a_tie_inside_the_window_breaks_by_row_index_not_by_e0_rank(
    hyb: Any, stub: StubFrozen
) -> None:
    """Two byte-identical rows tie at 0.0; the earlier row must win, whoever E0 preferred.

    E0 cannot separate them either, so its order is handed over with the pair swapped:
    if the re-ranker inherited E0's tie order, the swap would show up in the result.
    """
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-star")
    ids = base["library"].ids
    row_a, row_b = ids.index("dup-a"), ids.index("dup-b")
    assert row_a < row_b
    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    order = list(base["rows_in_e0_order"])
    order.remove(row_b)
    order.insert(order.index(row_a), row_b)
    assert order.index(row_b) < order.index(row_a), "the swapped order must disagree with row order"

    full_window = len(ids)
    hybrid = hyb.hybrid_pass(base, full_window, cache)
    reordered = dict(base, rows_in_e0_order=order)
    assert hyb.hybrid_pass(reordered, full_window, cache)["ids"] == hybrid["ids"]
    distances = [hybrid["distance_by_row"][row] for row in (row_a, row_b)]
    assert distances[0] == distances[1] == pytest.approx(0.0)
    assert hybrid["ids"].index("dup-a") < hybrid["ids"].index("dup-b")


# ---------------------------------------------------------------- the cache


def test_the_cache_reproduces_the_uncached_distances_exactly(hyb: Any, stub: StubFrozen) -> None:
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-star")
    rows = list(base["library"].rows)
    full = e3.score_library(base["query"], rows, orientation=hyb.E3_ORIENTATION)[0]
    cache = e3.build_library_descriptors(rows, orientation=hyb.E3_ORIENTATION)
    cached = e3.score_library_from_descriptors(
        base["query"], cache, orientation=hyb.E3_ORIENTATION
    )[0]
    assert np.array_equal(full, cached)


def test_a_subset_of_the_cache_equals_the_same_rows_from_the_full_call(
    hyb: Any, stub: StubFrozen
) -> None:
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    rows = list(base["library"].rows)
    indices = [7, 1, 9, 0]
    full = e3.score_library(base["query"], rows, orientation=hyb.E3_ORIENTATION)[0]
    cache = e3.build_library_descriptors(rows, orientation=hyb.E3_ORIENTATION)
    subset = e3.score_library_from_descriptors(
        base["query"], cache, indices, orientation=hyb.E3_ORIENTATION
    )[0]
    assert np.array_equal(subset, full[indices])


def test_the_cache_refuses_a_different_descriptor_grid(hyb: Any, stub: StubFrozen) -> None:
    """Silently mixing grids would change E3's mathematics, so it has to raise."""
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    with pytest.raises(e3.ShapeContextError, match="different descriptor grid"):
        e3.score_library_from_descriptors(base["query"], cache, orientation="sample")


def test_the_cache_refuses_an_out_of_range_index(hyb: Any, stub: StubFrozen) -> None:
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    with pytest.raises(e3.ShapeContextError, match="out of range"):
        e3.score_library_from_descriptors(
            base["query"], cache, [len(base["library"].ids)], orientation=hyb.E3_ORIENTATION
        )


def test_the_cache_refuses_to_pretend_it_has_mirrors(hyb: Any, stub: StubFrozen) -> None:
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    assert cache.mirror_contexts is None
    with pytest.raises(e3.ShapeContextError, match="no mirrored contexts"):
        e3.score_library_from_descriptors(
            base["query"], cache, allow_mirror=True, orientation=hyb.E3_ORIENTATION
        )


def test_cache_equivalence_reports_zero_difference(hyb: Any, stub: StubFrozen) -> None:
    base = hyb.e0_pass(stub, "T2", "q-star")
    full = hyb.full_e3_pass(base)
    import earthglyph.retrieval.shape_context_baseline as e3

    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    report = hyb.cache_equivalence(base, full, cache)
    assert report["identical"] and report["rankings_identical"]
    assert report["max_abs_difference"] == 0.0


def test_a_mirror_capable_cache_still_obeys_the_query_flag(hyb: Any, stub: StubFrozen) -> None:
    """Checkpoint 7-D F2, cached E3 path: holding mirrors is not the same as using them.

    ``build_library_descriptors(allow_mirror=True)`` doubles the cache, and until this test the
    scoring loop read that array whenever it existed, so ``allow_mirror=False`` on such a cache
    scored mirrored candidates anyway — a switch that looked effective and was not, in the
    opposite direction from the one the checkpoint names.
    """
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    rows = list(base["library"].rows)
    plain = e3.build_library_descriptors(rows, orientation=hyb.E3_ORIENTATION)
    with_mirrors = e3.build_library_descriptors(
        rows, allow_mirror=True, orientation=hyb.E3_ORIENTATION
    )
    assert with_mirrors.mirror_contexts is not None
    refused = e3.score_library_from_descriptors(
        base["query"], with_mirrors, allow_mirror=False, orientation=hyb.E3_ORIENTATION
    )[0]
    plain_distances = e3.score_library_from_descriptors(
        base["query"], plain, orientation=hyb.E3_ORIENTATION
    )[0]
    assert np.array_equal(refused, plain_distances), (
        "the mirror-holding cache disagrees with the mirror-free one on allow_mirror=False"
    )
    allowed = e3.score_library_from_descriptors(
        base["query"], with_mirrors, allow_mirror=True, orientation=hyb.E3_ORIENTATION
    )[0]
    # Mirroring can only ever find a better match, and here it does.
    assert np.all(allowed <= plain_distances + 1e-12)
    assert np.any(allowed < plain_distances)


# ---------------------------------------------------------------- windows


def test_a_window_larger_than_the_library_is_full_e3_not_a_shortcut(
    hyb: Any, stub: StubFrozen
) -> None:
    base = hyb.e0_pass(stub, "T2", "q-star")
    full = hyb.full_e3_pass(base)
    import earthglyph.retrieval.shape_context_baseline as e3

    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    hybrid = hyb.hybrid_pass(base, 999, cache)
    assert hybrid["window"] == len(base["library"].ids)
    assert hybrid["ids"] == full["ids"]


def test_the_tail_keeps_e0_order_and_carries_no_e3_distance(hyb: Any, stub: StubFrozen) -> None:
    import earthglyph.retrieval.shape_context_baseline as e3

    base = hyb.e0_pass(stub, "T2", "q-circle")
    cache = e3.build_library_descriptors(
        list(base["library"].rows), orientation=hyb.E3_ORIENTATION
    )
    hybrid = hyb.hybrid_pass(base, 3, cache)
    assert hybrid["ids"][3:] == base["ids"][3:]
    assert len(hybrid["distance_by_row"]) == 3
    assert hybrid["ids_in_window"] == set(base["ids"][:3])


def test_the_shipped_run_still_uses_the_paper_rotation_grid() -> None:
    """The tests may go fast, but the artefact must not be a different algorithm."""
    module = _load("hybrid_orientation_check", SCRIPT)
    import earthglyph.retrieval.shape_context_baseline as e3

    assert module.E3_ORIENTATION == "sample"
    assert e3.ROTATION_STEPS == 24
    assert module.WINDOWS == (20, 50, 100, 200)
    assert module.K_AGREEMENT == 10


# ---------------------------------------------------------------- the ledger


def test_a_dropped_reference_answer_is_ledgered_with_both_ranks(hyb: Any, stub: StubFrozen) -> None:
    entry, ledger = hyb.run_query(stub, "T2", "q-star", (2,))
    reference_top10 = entry["arms"]["Full-E3"]["ids"][:10]
    window = set(entry["arms"]["E0_only"]["ids"][:2])
    missing = [item for item in reference_top10 if item not in window]
    assert ledger, "a 2-wide window must lose part of the reference Top-10"
    assert {row["candidate_id"] for row in ledger} == set(missing)
    for row in ledger:
        assert row["setting"] == "T2"
        assert row["query_id"] == "q-star"
        assert row["window"] == 2
        assert row["e0_rank"] is not None and row["e0_rank"] > 2
        assert row["full_e3_rank"] is not None and row["full_e3_rank"] <= 10
        assert row["provenance"]


def test_a_window_that_holds_everything_produces_no_ledger(hyb: Any, stub: StubFrozen) -> None:
    _, ledger = hyb.run_query(stub, "T2", "q-star", (999,))
    assert ledger == []


def test_the_ledger_marks_relevance_from_labels_not_from_distance(
    hyb: Any, stub: StubFrozen
) -> None:
    entry, ledger = hyb.run_query(stub, "T2", "q-star", (2,))
    relevant = stub.relevant("q-star")
    for row in ledger:
        assert row["relevant"] == (row["candidate_id"] in relevant)
    assert entry["n_relevant"] == len(relevant)


# ---------------------------------------------------------------- metrics and rows


def test_label_metrics_are_the_c_runner_own_block(hyb: Any, stub: StubFrozen) -> None:
    """The metric definitions must come from Checkpoint C's module, not be re-typed."""
    assert hyb.metric_block.__module__ == "evaluate_perceptual_methods"
    ids = ["circle", "square", "blob", "noise"]
    from evaluate_perceptual_methods import metric_block

    # q-circle's labels call circle and circle-2 relevant; only one of them is listed here
    expected = metric_block(np.array([1.0, 0.0, 0.0, 0.0]), 2, 4)
    got = hyb.label_metrics(stub, "q-circle", ids, 4)
    assert got["ndcg@10"] == pytest.approx(expected["ndcg@10"])
    assert got["mrr"] == pytest.approx(expected["mrr"])
    assert got["hit@1"] == expected["hit@1"] == 1.0


def test_agreement_is_a_share_of_the_reference_top_k(hyb: Any, stub: StubFrozen) -> None:
    reference = ["a", "b", "c", "d"]
    assert hyb.agreement(["a", "b", "c", "d"], reference, 4) == 1.0
    assert hyb.agreement(["x", "y", "c", "d"], reference, 4) == 0.5
    assert hyb.agreement(["x", "y", "z", "w"], reference, 4) == 0.0


def test_summary_means_match_their_own_per_query_lists(hyb: Any, stub: StubFrozen) -> None:
    bundle = hyb.run_setting(stub, "T2", (4, 8), 8)
    summary = hyb.summarise(bundle, (4, 8))
    for name in hyb.arm_names((4, 8)):
        arm = summary[name]
        per_query = arm["per_query"]
        assert len(per_query["ndcg_at_10"]) == len(stub.queries)
        assert arm["labels"]["ndcg@10"] == pytest.approx(
            float(np.mean(per_query["ndcg_at_10"])), abs=1e-6
        )
        assert arm["n_queries"] == len(stub.queries)
    assert summary["Full-E3"]["agreement_with_full_e3"]["top10"] == 1.0
    for window in (4, 8):
        hybrid = summary[f"E0->E3@{window}"]
        assert hybrid["seconds"]["cache_build_mean"] >= 0.0
        assert hybrid["cache"]["rows_cached"] == len(stub.gallery) - 1


def test_hybrid_seconds_charge_the_cache_build(hyb: Any, stub: StubFrozen) -> None:
    """A speed claim that dropped the precompute would fail here."""
    entry, _ = hyb.run_query(stub, "T2", "q-star", (4,))
    arm = entry["arms"]["E0->E3@4"]
    assert arm["seconds"] == pytest.approx(arm["cache_build_seconds"] + arm["rerank_seconds"])
    assert arm["cache_bytes"] > 0


def test_csv_rows_cover_every_arm_and_flag_the_unscored_tail(hyb: Any, stub: StubFrozen) -> None:
    bundle = hyb.run_setting(stub, "T2", (4,), 4)
    bundle["_summary"] = hyb.summarise(bundle, (4,))
    bundle["_families"] = {item: "star" for item in stub.gallery}
    bundle["_roles"] = {item: "variant" for item in stub.gallery}
    bundle["_relevant"] = {query_id: stub.relevant(query_id) for query_id in stub.queries}
    rows = hyb.csv_rows(bundle, (4,))
    names = set(hyb.arm_names((4,)))
    assert {row["arm"] for row in rows} == names
    for entry in bundle["queries"]:
        per_arm = [row for row in rows if row["query_id"] == entry["query_id"]]
        for name in names:
            assert sum(1 for row in per_arm if row["arm"] == name) == 10
    tail = [row for row in rows if row["arm"] == "E0->E3@4" and int(row["rank"]) > 4]
    assert tail and all(row["distance"] == "" and row["scored_by_e3"] == 0 for row in tail)
    head = [row for row in rows if row["arm"] == "E0->E3@4" and int(row["rank"]) <= 4]
    assert all(row["distance"] != "" and row["scored_by_e3"] == 1 for row in head)


def test_csv_distance_kind_names_the_metric_behind_the_row(hyb: Any, stub: StubFrozen) -> None:
    bundle = hyb.run_setting(stub, "T2", (4,), 4)
    bundle["_families"] = {item: "star" for item in stub.gallery}
    bundle["_roles"] = {item: "variant" for item in stub.gallery}
    bundle["_relevant"] = {query_id: stub.relevant(query_id) for query_id in stub.queries}
    rows = hyb.csv_rows(bundle, (4,))
    kinds = {row["arm"]: row["distance_kind"] for row in rows}
    assert kinds["E0_only"] == "e0_alignment"
    assert kinds["Full-E3"] == "e3_shape_context"
    assert kinds["E0->E3@4"] == "e3_shape_context"


# ---------------------------------------------------------------- the figure


def test_the_figure_draws_the_four_questions(hyb: Any, stub: StubFrozen, tmp_path: Path) -> None:
    bundle = hyb.run_setting(stub, "T2", (4, 8), 8)
    bundle["_summary"] = hyb.summarise(bundle, (4, 8))
    out = tmp_path / "hybrid.png"
    info = hyb.draw_window_comparison({"T2": bundle}, (4, 8), out, dpi=60)
    assert out.exists() and out.stat().st_size > 2000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(info["panels"]) == 4
    assert info["windows"] == [4, 8]


def test_the_figure_survives_both_settings(hyb: Any, stub: StubFrozen, tmp_path: Path) -> None:
    bundles = {}
    for setting in ("T2", "T3"):
        bundle = hyb.run_setting(stub, setting, (4,), 4)
        bundle["_summary"] = hyb.summarise(bundle, (4,))
        bundles[setting] = bundle
    out = tmp_path / "both.png"
    info = hyb.draw_window_comparison(bundles, (4,), out, dpi=60)
    assert info["settings"] == ["T2", "T3"]
    assert out.exists()


def _fake_payload(windows: tuple[int, ...]) -> dict[str, Any]:
    arms = {"E0_only": {}, "Full-E3": {}}
    for window in windows:
        arms[f"E0->E3@{window}"] = {}
    for name in arms:
        arms[name] = {
            "agreement_with_full_e3": {"top5": 0.5, "top10": 0.6},
            "labels": {"ndcg@10": 0.7},
            "seconds": {"mean_query": 1.5},
            "top10_reached_e3_mean": 8.0,
        }
    return {"settings": {st: {"summary": arms} for st in ("T2", "T3")}}


def test_the_redraw_takes_its_windows_from_the_file(hyb: Any, tmp_path: Path) -> None:
    """A cosmetic figure fix must be re-renderable without re-running the scoring."""
    import json

    metrics = tmp_path / "hybrid_retrieval_metrics.json"
    text = json.dumps(_fake_payload((7, 13)))
    metrics.write_text(text, encoding="utf-8")
    sibling = tmp_path / "hybrid_retrieval_results.csv"
    sibling.write_text("must not be touched\n", encoding="utf-8")
    info = hyb.redraw_from_json(metrics, tmp_path, 60)
    assert info["windows"] == [7, 13]
    assert len(info["panels"]) == 4
    assert (tmp_path / "hybrid_window_comparison.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert metrics.read_text(encoding="utf-8") == text
    assert sibling.read_text(encoding="utf-8") == "must not be touched\n"


# ---------------------------------------------------------------- the boundary


def test_the_production_default_is_not_the_hybrid() -> None:
    """D3 is an experiment: the shipped retriever must still be E0 with its own window."""
    import inspect
    from pathlib import Path as _Path

    from earthglyph.retrieval import search

    signature = inspect.signature(search.search_by_contour)
    assert signature.parameters["shortlist_size"].default == 200
    assert signature.parameters["method"].default == "fourier_align"
    package = _Path(inspect.getfile(search)).parent
    entry_points = "\n".join(
        (package / name).read_text(encoding="utf-8") for name in ("search.py", "query.py")
    )
    assert "hybrid" not in entry_points.lower()
    assert "build_library_descriptors" not in entry_points
    assert "score_library_from_descriptors" not in entry_points
