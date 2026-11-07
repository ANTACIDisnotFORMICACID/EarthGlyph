"""Tests for the D2 diagnostics runner — the claims a table of numbers has to keep.

``scripts/diagnose_perceptual_arms.py`` re-ranks nothing that Checkpoint C already
ranked, so the properties worth testing are the ones that would silently change the
answer: that rankings come out of ``results.csv`` rather than a fresh scoring pass,
that the E1 group ablation is a column subset of the *same* fitted scale instead of a
second normalization, that the rotation probe keeps the centred/unit-RMS contract it
relies on, and that the "not a bug" verdict for E2's rank-4 is only reachable when the
zero-distance tie actually exists.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_perceptual_arms.py"
GALLERY_DIR = ROOT / "experiments" / "p0_perceptual_probe"
INDEX_DIR = ROOT / "data" / "index"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diag() -> Any:
    return _load("diagnose_perceptual_arms", SCRIPT)


@pytest.fixture(scope="module")
def frozen(diag: Any) -> Any:
    return diag.Frozen(GALLERY_DIR, INDEX_DIR, GALLERY_DIR / "results.csv")


# ------------------------------------------------------------------ provenance


def test_d2_borrows_its_parameter_policy_from_the_c_runner(diag: Any) -> None:
    """A D2 re-ranking must use the parameters C used, or the two are not comparable.

    ``__module__`` is the honest check: a locally re-implemented helper would report
    this script's own module name, which is exactly the drift this test forbids.
    """
    for name in ("arm_params", "fit_gallery_normalizer", "build_library", "load_gallery"):
        assert getattr(diag, name).__module__ == "evaluate_perceptual_methods"


def test_main_refuses_to_mix_settings(diag: Any) -> None:
    with pytest.raises(SystemExit, match="one setting"):
        diag.main(["--settings", "T1,T2"])


# ------------------------------------------------------------------ E1 ablation


def _fitted_normalizer(e1: Any, rows: int = 40) -> Any:
    names = e1.feature_names(e1.GROUPS, orientation="canonical")
    matrix = np.random.default_rng(7).normal(size=(rows, len(names)))
    return e1.fit_normalizer(matrix, names=names, fitted_on="test corpus")


def test_normalizer_slice_is_a_column_subset_of_the_same_fit(diag: Any) -> None:
    from earthglyph.retrieval import perceptual_features as e1

    source = _fitted_normalizer(e1)
    holder = types.SimpleNamespace(normalizer=source)
    selected = ("curvature", "fourier")
    sliced = diag._normalizer_for(holder, selected)

    wanted = set(e1.feature_names(selected, orientation="canonical"))
    columns = [index for index, name in enumerate(source.names) if name in wanted]
    matrix = np.random.default_rng(11).normal(size=(6, source.n_descriptors))
    assert sliced.n_descriptors == len(columns)
    assert np.allclose(sliced.transform(matrix[:, columns]), source.transform(matrix)[:, columns])
    assert sliced.method == source.method
    assert sliced.n_samples == source.n_samples


def test_full_normalizer_is_rejected_by_a_group_subset(diag: Any) -> None:
    """The guard the slice exists to satisfy: two scales cannot be compared silently."""
    from earthglyph.retrieval import perceptual_features as e1

    source = _fitted_normalizer(e1)
    rng = np.random.default_rng(5)
    left = rng.normal(size=(128, 2))
    right = rng.normal(size=(128, 2))
    with pytest.raises(e1.PerceptualFeatureError, match="group selection has"):
        e1.distance(left, right, normalizer=source, groups=("curvature",))


def test_group_widths_add_up_to_the_fitted_dimension(diag: Any) -> None:
    from earthglyph.retrieval import perceptual_features as e1

    widths = [diag._group_width(group) for group in diag.e1_groups()]
    assert widths == [10, 6, 18, 15, 42]
    assert sum(widths) == len(e1.feature_names(diag.e1_groups(), orientation="canonical"))


# ------------------------------------------------------------------ rotation


def test_rotation_is_a_rigid_map_that_keeps_the_standardization_contract(diag: Any) -> None:
    ring = np.random.default_rng(3).normal(size=(128, 2))
    ring -= ring.mean(axis=0)
    ring /= float(np.sqrt((ring**2).sum(axis=1)).mean())

    assert np.allclose(diag._rotate(ring, 0.0), ring)
    for angle in (37.0, 90.0, 180.0):
        turned = diag._rotate(ring, angle)
        assert np.allclose(turned.mean(axis=0), 0.0, atol=1e-12)
        assert abs(float(np.sqrt((turned**2).sum(axis=1)).mean()) - 1.0) < 1e-9
        assert np.allclose(np.linalg.norm(turned, axis=1), np.linalg.norm(ring, axis=1))


def test_rotation_angles_cover_a_full_star_period(diag: Any) -> None:
    """72 deg is the five-pointed-star period; a probe that skipped it would flatter E1."""
    assert 72.0 in diag.ROTATION_ANGLES
    assert 0.0 in diag.ROTATION_ANGLES and 180.0 in diag.ROTATION_ANGLES


# ------------------------------------------------------------------ verdicts


def test_missing_first_relevant_is_counted_as_beyond_top10(diag: Any) -> None:
    assert diag._direction(None, None) == "tie"
    assert diag._delta(None, None) is None
    assert diag._direction(None, 7) == "E3 better"
    assert diag._delta(None, 7) == 4.0
    assert diag._direction(3, 5) == "E0 better"
    assert diag._delta(3, 5) == -2.0
    assert diag._direction(2, 2) == "tie"


def test_zero_tie_is_the_only_basis_for_the_not_a_bug_verdict(diag: Any) -> None:
    assert "not an implementation error" in diag._self_rank_verdict(5, True)
    assert "one tie of 5 rows" in diag._self_rank_verdict(5, True)
    assert "needs reading" in diag._self_rank_verdict(1, True)
    assert "needs reading" in diag._self_rank_verdict(4, False)


def test_pair_signs_reads_the_alignment_the_score_produced(diag: Any) -> None:
    aligned = {
        "pairs": [
            {"left": 0, "right": 0, "sign_match": True, "drift": 0.001, "cost": 0.1},
            {"left": 1, "right": 1, "sign_match": False, "drift": 0.001, "cost": 0.4},
            {"left": 2, "right": 2, "sign_match": True, "drift": 0.9, "cost": 0.3},
        ]
    }
    assert diag._pair_signs(aligned) == {"opposite": 1, "drifted": 1}


# ------------------------------------------------------------------ frozen reads


def test_top_rows_are_exactly_the_csv_rows_in_rank_order(diag: Any, frozen: Any) -> None:
    query_id = frozen.queries[0]
    key = ("T2", "E0", query_id)
    expected = sorted(
        (
            record
            for record in frozen.results
            if (record["setting"], record["arm"], record["query_id"]) == key
        ),
        key=lambda record: int(record["rank"]),
    )[:10]
    assert frozen.top("T2", "E0", query_id, 10) == expected
    assert [int(row["rank"]) for row in expected] == list(range(1, 11))


def test_first_relevant_rank_reads_the_csv_not_a_recomputation(diag: Any, frozen: Any) -> None:
    for query_id in frozen.queries:
        rows = frozen.top("T2", "E0", query_id, 10)
        expected = next((int(row["rank"]) for row in rows if row["relevant"] == "1"), None)
        assert diag.first_relevant_rank(frozen, "T2", "E0", query_id) == expected


def test_relevance_column_agrees_with_labels_json(diag: Any, frozen: Any) -> None:
    for query_id in frozen.queries:
        relevant = frozen.relevant(query_id)
        for row in frozen.top("T2", "E1", query_id, 10):
            assert (row["relevant"] == "1") == (row["candidate_id"] in relevant)


def test_item_returns_the_ring_the_library_scored(diag: Any, frozen: Any) -> None:
    gallery_id = "syn-heart-query"
    assert frozen.item(gallery_id).shape == (128, 2)
    assert np.allclose(frozen.item(gallery_id), frozen.gallery[gallery_id].contour)

    landform_id = frozen.index_ids[4]
    assert frozen.item(landform_id).shape == (128, 2)
    assert np.allclose(
        frozen.item(landform_id), frozen.index_rows[frozen.index_position[landform_id]]
    )
    assert frozen.meta(landform_id)["family"] == "real_landform"


def test_meta_carries_the_generators_own_note(diag: Any, frozen: Any) -> None:
    note = frozen.meta("syn-heart-round")["note"]
    assert note and note == frozen.notes["syn-heart-round"]


def test_library_excludes_the_query_itself_for_t2(diag: Any, frozen: Any) -> None:
    query_id = "syn-heart-query"
    library = frozen.library("T2", query_id)
    assert query_id not in library.ids
    assert len(library.ids) == len(library.rows)


# ------------------------------------------------------------------ material audit


@pytest.fixture(scope="module")
def material(diag: Any, frozen: Any) -> Any:
    return diag.part_material(frozen, "T2")


@pytest.fixture(scope="module")
def e2_part(diag: Any, frozen: Any) -> Any:
    return diag.part_e2(frozen, "T2")


def test_identical_contour_groups_really_are_identical(
    diag: Any, frozen: Any, material: Any
) -> None:
    """The audit's only claim is geometric, so check the geometry, not the labels."""
    for group in material["groups"]:
        ids = group["ids"]
        first = np.round(np.asarray(frozen.item(ids[0]), dtype=float), 9)
        for other in ids[1:]:
            assert np.array_equal(first, np.round(np.asarray(frozen.item(other), float), 9)), other
    assert material["members_in_a_group"] == sum(len(g["ids"]) for g in material["groups"])


def test_the_provenance_read_agrees_with_the_geometry_read(
    diag: Any, frozen: Any, material: Any
) -> None:
    """Two unrelated fields must tell the same story, or the audit is one bug deep.

    The partition comes from hashing point arrays; the cross-check comes from the
    ``origin_ref`` column the generator wrote.  Neither is derived from the other, so
    agreement is evidence and disagreement is a loud failure rather than a quiet one.
    """
    assert material["geometric_and_provenance_reads_agree"] is True
    assert material["origin_ref_groups"] == material["identical_contour_groups"]
    assert material["origin_ref_members"] == material["members_in_a_group"]
    for entry in material["provenance_of_groups"]:
        assert entry["origin_ref"], entry
        assert len(entry["group"]) >= 2
        for item_id in entry["group"]:
            assert frozen.origin_refs[item_id] == entry["origin_ref"], item_id


def test_a_reused_quickdraw_asset_is_named_by_its_key_id(
    diag: Any, frozen: Any, material: Any
) -> None:
    """The reuse has to be findable in the data, not just inferred from coordinates."""
    quickdraw = [
        entry
        for entry in material["provenance_of_groups"]
        if entry["origin_ref"].startswith("quickdraw:")
    ]
    assert quickdraw
    for entry in quickdraw:
        category = entry["origin_ref"].split(":")[1]
        for item_id in entry["group"]:
            assert f"quickdraw:{category}:" in frozen.origin_refs[item_id], item_id


def test_one_contour_carries_several_family_labels(diag: Any, material: Any) -> None:
    """The measured fact behind D2's re-reading: labels differ where contours do not.

    Without this, "E0 cannot tell a circle from a band" would be a claim about the
    algorithm; with it, the sentence belongs to the gallery, not to E0.
    """
    widest = max(material["groups"], key=lambda group: len(group["families_present"]))
    assert len(widest["families_present"]) >= 5
    assert len(widest["roles_present"]) >= 2
    assert any("syn-circle" in item_id for item_id in widest["ids"])


def test_a_query_library_can_still_contain_a_copy_of_the_query(
    diag: Any, frozen: Any, material: Any
) -> None:
    leakage = {
        block["query_id"]: block["identical_rows_in_library"]
        for block in material["query_duplicates_left_in_library"]
    }
    assert leakage, "T2 is supposed to remove the answer; a duplicate is not the answer"
    for query_id, hits in leakage.items():
        assert query_id not in hits
        assert "query" not in [
            frozen.meta(hit)["role"] for hit in hits
        ], f"{query_id}: {hits} should have been excluded as a near duplicate"


def test_undeclared_duplicates_are_counted_not_assumed(
    diag: Any, frozen: Any, material: Any
) -> None:
    declared = {
        frozenset((entry["left"], entry["right"]))
        for entry in frozen.labels["near_duplicate_pairs"]
    }
    assert material["pairs_total"] == material["pairs_declared_in_labels"] + len(
        material["pairs_not_declared"]
    )
    for pair in material["pairs_not_declared"]:
        assert frozenset(pair) not in declared


# ------------------------------------------------------------------ e0 attribution


@pytest.fixture(scope="module")
def e0_part(diag: Any, frozen: Any) -> Any:
    return diag.part_e0(frozen, "T2")


def test_e0_lookalike_rows_split_by_whether_the_shapes_differ_at_all(
    diag: Any, frozen: Any, e0_part: Any
) -> None:
    """The table has to say which rows are evidence about E0 and which are about the gallery.

    A query and a "hard negative" that are one and the same point array cannot be
    separated by any metric, so leaving those rows mixed into the unlabelled count would
    overstate E0's blindness — and, worse, would let a real blindness hide among them.
    """
    rows = e0_part["indistinguishable_but_not_relevant"]
    digests = diag.contour_digests(frozen)
    for row in rows:
        assert row["same_contour_as_query"] == (
            digests[row["query_id"]] == digests[row["candidate_id"]]
        ), row
    assert e0_part["same_contour_as_query"] == sum(
        1 for row in rows if row["same_contour_as_query"]
    )
    assert (
        e0_part["same_contour_as_query"]
        + e0_part["distinct_contours"]
        == e0_part["count_indistinguishable_non_relevant"]
    )
    # six ids of one contour must not read as six findings
    assert e0_part["distinct_shape_comparisons"] <= len(rows)
    assert (
        e0_part["distinct_comparisons_on_a_different_contour"]
        <= e0_part["distinct_shape_comparisons"]
    )
    assert e0_part["distinct_comparisons_on_a_different_contour"] >= 1, rows


def test_e0_table_keeps_a_genuine_envelope_only_case(
    diag: Any, frozen: Any, e0_part: Any
) -> None:
    """At least one close-but-unlabelled pair really is two different contours.

    If the whole table were duplicate ids, C1's answer would be "nothing to see"; this
    pins that the surviving claim is measured, not assumed.
    """
    genuine = [row for row in e0_part["indistinguishable_but_not_relevant"]
               if not row["same_contour_as_query"]]
    assert genuine
    for row in genuine:
        assert not np.array_equal(
            np.round(np.asarray(frozen.item(row["query_id"]), float), 9),
            np.round(np.asarray(frozen.item(row["candidate_id"]), float), 9),
        ), row
    assert any(row["distance"] > 0.0 for row in genuine)


# ------------------------------------------------------------------ e2 reads


def test_zero_distance_top_hits_match_the_stored_ranking(
    diag: Any, frozen: Any, e2_part: Any
) -> None:
    """Every rank this block lists must read as exactly 0.0 in C's own results.csv."""
    for group in e2_part["zero_distance_top_hits"]["groups"]:
        rows = {int(row["rank"]): row for row in frozen.rows[("T2", "E2", group["query_id"])]}
        for rank in group["ranks_at_zero"]:
            assert float(rows[rank]["distance"]) == 0.0, (group["query_id"], rank)
        for item in group["candidates"]:
            assert len(diag.e2.signature(frozen.item(item["candidate_id"])).events) == (
                item["events"]
            )


def test_deletion_vs_match_reports_both_directions(diag: Any, frozen: Any, e2_part: Any) -> None:
    """A one-sided table would be the "only show the winner" failure the brief bans."""
    compare = e2_part["deletion_vs_match"]
    wins = [group for group in compare["groups"] if group["deletion_wins"]]
    losses = [group for group in compare["groups"] if not group["deletion_wins"]]
    assert wins and losses
    assert compare["deletion_wins"] == len(wins)
    for group in compare["groups"]:
        lost = group["cheapest_with_a_dropped_event"]
        kept = group["cheapest_fully_matched_relevant"]
        assert lost["query_events_dropped"] >= 1
        assert kept["query_events_dropped"] == 0 and kept["relevant"]
        assert group["margin"] == round(kept["distance"] - lost["distance"], 5)
        # both sides must be rows Checkpoint C actually stored, not a fresh scoring pass
        row = frozen.rows[("T2", "E2", group["query_id"])]
        for entry in (lost, kept):
            assert any(
                int(each["rank"]) == entry["rank"]
                and each["candidate_id"] == entry["candidate_id"]
                for each in row
            ), entry


def test_zero_distance_split_separates_the_two_explanations(
    diag: Any, frozen: Any, e2_part: Any
) -> None:
    """A 0.0 here has two unrelated causes, and conflating them would erase the bug.

    One cause is the gallery reusing a contour (nothing to fix).  The other is two
    eventless sequences meeting at cost 0 on shapes that really do differ — that one is
    E2 running out of signal, and it has to survive the split.
    """
    zero = e2_part["zero_distance_top_hits"]
    digests = diag.contour_digests(frozen)
    items = [item for group in zero["groups"] for item in group["candidates"]]
    for item in items:
        group = next(g for g in zero["groups"] if item in g["candidates"])
        assert item["same_contour_as_query"] == (
            digests[group["query_id"]] == digests[item["candidate_id"]]
        ), item
    assert zero["zero_hits_that_are_the_same_contour"] == sum(
        1 for item in items if item["same_contour_as_query"]
    )
    assert zero["zero_hits_that_are_the_same_contour"] + zero[
        "zero_hits_on_a_genuinely_different_contour"
    ] == len(items)
    assert zero["zero_hits_on_a_genuinely_different_contour"] >= 1, items
    different = [item for item in items if not item["same_contour_as_query"]]
    assert all(item["events"] == 0 for item in different), different


def test_zero_distance_relevance_is_the_label_not_an_inference(
    diag: Any, frozen: Any, e2_part: Any
) -> None:
    for group in e2_part["zero_distance_top_hits"]["groups"]:
        relevant = frozen.relevant(group["query_id"])
        for item in group["candidates"]:
            assert item["relevant"] == (item["candidate_id"] in relevant), item


# ------------------------------------------------------------------ figure plan


def _plan_data() -> dict[str, Any]:
    return {
        "e0": {
            "count_indistinguishable_non_relevant": 11,
            "same_contour_as_query": 3,
            "distinct_comparisons_on_a_different_contour": 3,
            "indistinguishable_but_not_relevant": [
                {"query_id": "ph-circle-fill", "candidate_id": "syn-circle", "distance": 7.3e-05,
                 "same_contour_as_query": False}
            ]
        },
        "e1": {
            "heart_queries": [
                {"query_id": "syn-heart-query", "top3": [{"candidate_id": "syn-heart-round"}]}
            ]
        },
        "e2": {
            "structure_loss_cases": [
                {
                    "query_id": "ph-heart-fill",
                    "candidate_id": "qd-band-circle-03",
                    "rank": 1,
                    "distance": 0.379,
                    "query_concave": 1,
                    "candidate_concave": 0,
                }
            ]
        },
        "e3": {
            "where_e3_loses": [
                {
                    "query_id": "qd-circle-query-01",
                    "e3_top1": "qd-circle-01",
                    "e3_first_relevant": 5,
                    "e0_first_relevant": 4,
                }
            ],
            "per_query": [
                {
                    "query_id": "ph-circle-fill",
                    "e3_top1": "syn-circle",
                    "e3_first_relevant": 7,
                    "e0_first_relevant": None,
                    "direction": "E3 better",
                },
                {
                    "query_id": "ph-heart-fill",
                    "e3_top1": "syn-heart-round",
                    "e3_first_relevant": 1,
                    "e0_first_relevant": 1,
                    "direction": "tie",
                },
            ],
        },
    }


def test_panel_plan_draws_losses_before_wins_and_skips_ties(diag: Any, frozen: Any) -> None:
    panels = {block["title"]: block for block in diag._panel_plan(frozen, _plan_data())}
    cases = panels["E3 vs E0 - losses first, then wins"]["cases"]
    assert [case["query_id"] for case in cases] == ["qd-circle-query-01", "ph-circle-fill"]
    assert "vs E0 4" in cases[0]["label"]


def test_the_duplicate_contour_panel_comes_first(diag: Any, frozen: Any) -> None:
    """A reader must meet the material defect before any arm's zero distance."""
    data = _plan_data()
    data["material"] = {
        "identical_contour_groups": 11,
        "members_in_a_group": 31,
        "gallery_members": 179,
        "groups": [
            {
                "ids": ["syn-circle", "syn-circle-band", "syn-circle-heart"],
                "families_present": ["band", "crescent", "heart"],
            }
        ],
        "query_duplicates_left_in_library": [
            {"query_id": "qd-circle-query-01", "identical_rows_in_library": ["qd-circle-01"]}
        ],
    }
    panels = diag._panel_plan(frozen, data)
    assert panels[0]["title"] == "Gallery first: one contour, several ids"
    assert "11 groups" in panels[0]["caption"] and "origin_ref" in panels[0]["caption"]
    labels = [case["label"] for case in panels[0]["cases"]]
    assert any("same contour" in text for text in labels)
    assert any("families" in text for text in labels)


def test_the_e0_panel_draws_comparisons_not_repeated_ids(diag: Any, frozen: Any) -> None:
    """Six ids of one contour would otherwise draw six identical cells and read as six findings.

    The panel that claims "same boundary, different structure" has to earn that sentence:
    duplicate-id rows are filtered out, and what is left is de-duplicated by shape.
    """
    data = _plan_data()
    data["e0"]["indistinguishable_but_not_relevant"] = [
        # the gallery handing the query its own contour back - not E0's failure
        {"query_id": "qd-circle-query-01", "candidate_id": "qd-circle-01", "distance": 0.0,
         "same_contour_as_query": True},
        # three ids, one synthetic circle - one comparison
        {"query_id": "ph-circle-fill", "candidate_id": "syn-circle", "distance": 7.3e-05,
         "same_contour_as_query": False},
        {"query_id": "ph-circle-fill", "candidate_id": "syn-circle-band", "distance": 7.3e-05,
         "same_contour_as_query": False},
        {"query_id": "ph-circle-fill", "candidate_id": "syn-circle-heart", "distance": 7.3e-05,
         "same_contour_as_query": False},
        {"query_id": "syn-blob-query", "candidate_id": "syn-egg", "distance": 6.3e-07,
         "same_contour_as_query": False},
    ]
    panels = {block["title"]: block for block in diag._panel_plan(frozen, data)}
    cases = panels["E0 same boundary, different structure"]["cases"]
    assert not any(case["candidate_id"] == "qd-circle-01" for case in cases)
    drawn = [case["candidate_id"] for case in cases]
    assert drawn.count("syn-circle") == 1
    assert "syn-circle-band" not in drawn and "syn-circle-heart" not in drawn
    assert sorted(drawn) == ["syn-circle", "syn-egg"]
    assert "3 distinct shape comparisons" in panels["E0 same boundary, different structure"][
        "caption"
    ]


def test_panel_plan_only_names_shapes_that_exist(diag: Any, frozen: Any) -> None:
    known = set(frozen.gallery) | set(frozen.index_position)
    for block in diag._panel_plan(frozen, _plan_data()):
        for case in block["cases"]:
            assert case["query_id"] in known
            assert case["candidate_id"] in known


def test_draw_cases_writes_a_png_with_one_row_per_panel(
    diag: Any, frozen: Any, tmp_path: Path
) -> None:
    out = tmp_path / "cases.png"
    info = diag.draw_cases(frozen, _plan_data(), out, dpi=60)
    assert out.exists() and out.stat().st_size > 2000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(info["panels"]) == 4
