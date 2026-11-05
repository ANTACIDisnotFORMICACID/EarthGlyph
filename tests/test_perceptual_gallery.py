"""Tests for the P0-4.5 controlled perceptual gallery.

Nothing here touches the network: external assets are replayed from a seeded
cache directory, and the one code path that probes a remote object size is
monkeypatched. The behaviours under test are the ones the dataset's credibility
rests on — that roles are decided by measured geometry, that the byte ranges of
a Quick Draw file are pinned rather than rediscovered, and that a refusal is a
counted record rather than a silent drop.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from earthglyph.data import perceptual_gallery as pg
from earthglyph.data.perceptual_gallery import (
    Rejection,
    audit_structural_roles,
    blob_matching_solidity,
    build_labels,
    check_design_coverage,
    circle,
    concave_deficiency,
    crescent,
    even_spaced,
    fetch_cached,
    item_from_points,
    near_duplicate_pairs,
    phosphor_item,
    phosphor_rings,
    quickdraw_strokes,
    resolve_quickdraw_spans,
    standardize,
    star,
    synthetic_specs,
)

# --- helpers -------------------------------------------------------------------


def make_item(item_id: str, family: str, role: str, points: np.ndarray) -> Any:
    return item_from_points(
        item_id=item_id,
        family=family,
        role=role,
        origin="synthetic",
        origin_ref="test",
        license_text=pg.SYNTHETIC["license"],
        points=points,
    )


def seed_phosphor(cache_dir: Path, name: str, weight: str, document: str) -> str:
    """Write one icon into the cache exactly the way ``fetch_cached`` would."""
    url = pg.PHOSPHOR["url_template"].format(weight=weight, name=name)
    folder = cache_dir / "phosphor"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]).write_text(
        document, encoding="utf-8"
    )
    return url


PANEL_WITH_CUTOUT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
    '<path fill="currentColor" d="M16 16H240V240H16Z M128 60L180 180L76 180Z"/>'
    "</svg>"
)
PLAIN_STAR = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
    '<path fill="currentColor" d="M128 16L160 96L240 128L160 160L128 240'
    'L96 160L16 128L96 96Z"/></svg>'
)
TWO_PIECES = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
    '<path fill="currentColor" d="M10 10H60V60H10Z M180 180H230V230H180Z"/></svg>'
)


# --- standardization -----------------------------------------------------------


def test_standardize_returns_the_library_contract():
    contour = standardize(crescent(1.0, 0.82, 0.5))
    assert contour.shape == (pg.N_SAMPLES, 2)
    assert np.isfinite(contour).all()
    assert np.linalg.norm(contour.mean(axis=0)) < 1e-9
    assert float(np.sqrt((contour**2).sum(axis=1).mean())) == pytest.approx(1.0)
    closed = np.vstack([contour, contour[:1]])
    signed = 0.5 * np.sum(
        closed[:-1, 0] * closed[1:, 1] - closed[1:, 0] * closed[:-1, 1]
    )
    assert signed > 0.0  # CCW, so a query and a candidate wind the same way


def test_standardize_absorbs_the_rigid_freedoms_of_the_raw_outline():
    base = standardize(circle(1.0))
    moved = standardize(circle(1.0) + np.array([37.0, -12.0]))
    scaled = standardize(circle(900.0))
    assert np.allclose(base, moved, atol=1e-6)
    assert np.allclose(base, scaled, atol=1e-6)


def test_item_metrics_are_the_stored_geometry_not_a_claim():
    item = make_item("x-circle", "blob", "query", circle(1.0))
    assert set(item.metrics) == {"compactness", "solidity", "aspect_ratio", "diameter_fill"}
    assert item.metrics["solidity"] == pytest.approx(1.0, abs=1e-3)
    assert item.metrics["compactness"] == pytest.approx(1.0, abs=1e-3)
    record = item.as_record()
    assert record["n_contour_points"] == pg.N_SAMPLES
    assert len(record["raw_points"]) == len(item.points)


# --- the structural role audit -------------------------------------------------


def test_concave_deficiency_is_one_minus_solidity():
    disk = standardize(circle(1.0))
    bay = standardize(crescent(1.0, 0.82, 0.5))
    assert concave_deficiency(disk) == pytest.approx(1.0 - 1.0, abs=0.01)
    assert concave_deficiency(bay) > 0.3
    points = standardize(star(5, 0.42))
    assert concave_deficiency(points) > 0.3


def test_a_convex_query_of_a_concave_family_is_demoted_not_left_as_a_query():
    """The Quick Draw "moon" case: people draw circles, and a circle cannot query a crescent."""
    items = [
        make_item("q-crescent", "crescent", "query", crescent(1.0, 0.82, 0.5)),
        make_item("q-moon-disk", "crescent", "query", circle(1.0)),
    ]
    revised, revisions = audit_structural_roles(items)
    by_id = {item.item_id: item for item in revised}
    assert by_id["q-moon-disk"].role == "hard_negative"
    assert by_id["q-crescent"].role == "query"
    query_stages = [row for row in revisions if row["stage"] == "query"]
    assert [row["item_id"] for row in query_stages] == ["q-moon-disk"]
    assert query_stages[0]["concave_deficiency"] < query_stages[0]["threshold"]
    assert "role_revised" in by_id["q-moon-disk"].provenance


def test_a_blunt_family_member_survives_the_mildest_query_not_the_deepest():
    """The bar is a quarter of the *mildest* query, so a worn member stays relevant."""
    items = [
        make_item("q-deep", "crescent", "query", crescent(1.0, 0.86, 0.22)),
        make_item("q-shallow", "crescent", "query", crescent(1.0, 0.70, 0.62)),
        make_item(
            "v-worn", "crescent", "variant",
            pg.power_lobes(3, 1.2, sharpness=0.7, base=0.2, phase_deg=90.0),
        ),
    ]
    depths = {item.item_id: concave_deficiency(item.contour) for item in items}
    assert depths["q-shallow"] < depths["q-deep"]
    # the variant sits between the two bars: a quarter of the mildest query keeps it,
    # a quarter of the deepest would have demoted a legitimate worn family member
    assert 0.25 * depths["q-shallow"] < depths["v-worn"] < 0.25 * depths["q-deep"]
    revised, revisions = audit_structural_roles(items)
    assert revisions == []
    assert {item.item_id: item.role for item in revised}["v-worn"] == "variant"


def test_audit_leaves_smooth_families_and_negatives_alone():
    items = [
        make_item("q-blob", "blob", "query", circle(1.0)),
        make_item("v-blob", "blob", "variant", circle(0.8)),
        make_item("n-crescent", "crescent", "hard_negative", crescent(1.0, 0.82, 0.5)),
    ]
    revised, revisions = audit_structural_roles(items)
    assert revisions == []
    assert [(item.item_id, item.role) for item in revised] == [
        (item.item_id, item.role) for item in items
    ]


def test_audit_demotes_a_variant_that_lost_the_concavity():
    items = [
        make_item("q", "crescent", "query", crescent(1.0, 0.82, 0.5)),
        make_item("v-disk", "crescent", "variant", circle(1.0)),
    ]
    revised, revisions = audit_structural_roles(items)
    assert [row["item_id"] for row in revisions] == ["v-disk"]
    assert revisions[0]["stage"] == "variant"
    assert revisions[0]["from"] == "variant" and revisions[0]["to"] == "hard_negative"
    by_id = {item.item_id: item for item in revised}
    # demotion changes the role only: the contour and the id are untouched
    assert np.allclose(by_id["v-disk"].contour, items[1].contour)


# --- design coverage -----------------------------------------------------------


def test_coverage_accepts_the_briefs_ranges_and_reports_no_overrun():
    counts = {
        family: {"query": 2, "variant": 7, "hard_negative": 12}
        for family in ("star", "crescent", "heart", "forked", "band", "blob")
    }
    report = check_design_coverage(counts)
    assert report["classes"] == 6
    assert report["within_brief"] is True
    assert report["shortfalls"] == []


def test_coverage_raises_when_a_family_is_thin():
    counts = {
        family: {"query": 2, "variant": 7, "hard_negative": 12}
        for family in ("star", "crescent", "heart", "forked", "band", "blob")
    }
    counts["star"]["query"] = 1
    with pytest.raises(RuntimeError, match="star: 1 queries"):
        check_design_coverage(counts)
    counts["star"]["query"] = 2
    counts["blob"]["variant"] = 4
    with pytest.raises(RuntimeError, match="design brief"):
        check_design_coverage(counts)
    del counts["blob"]
    with pytest.raises(RuntimeError, match="structure classes"):
        check_design_coverage(counts)


def test_coverage_reports_a_ceiling_overrun_without_failing():
    counts = {
        family: {"query": 3, "variant": 17, "hard_negative": 12}
        for family in ("star", "crescent", "heart", "forked", "band", "blob")
    }
    report = check_design_coverage(counts)
    assert report["within_brief"] is False
    assert any("17 variants" in note for note in report["over_range"])


# --- labels and splits ---------------------------------------------------------


def test_labels_freeze_relevance_from_roles_and_exclude_the_query():
    items = [
        make_item("q1", "crescent", "query", crescent(1.0, 0.82, 0.5)),
        make_item("q2", "crescent", "query", crescent(1.0, 0.86, 0.22)),
        make_item("v1", "crescent", "variant", crescent(1.0, 0.80, 0.45)),
        make_item("n1", "crescent", "hard_negative", circle(1.0)),
        make_item("sq1", "blob", "query", circle(1.0)),
    ]
    labels = build_labels(items, near_max_align=0.02)
    entry = labels["queries"]["q1"]
    assert labels["frozen_at_build"] is True
    assert "v1" in entry["relevant_variants"]
    assert "q1" not in entry["relevant_variants"]
    assert entry["family_hard_negatives"] == ["n1"]
    assert "q1" not in entry["library_t2_family"]
    assert "q1" in entry["library_t1"]  # T1 is the sanity check: the target is present
    assert set(labels["items"]) == {item.item_id for item in items}


def test_labels_exclude_a_rigid_copy_of_the_query_from_t2():
    """A rotated/scaled duplicate must not be a *relevant* answer under T2."""
    query = make_item("q1", "crescent", "query", crescent(1.0, 0.82, 0.5))
    rigid = make_item(
        "copy", "crescent", "variant",
        np.roll((query.contour * 3.5) @ np.array([[0.0, -1.0], [1.0, 0.0]]), 7, axis=0),
    )
    labels = build_labels([query, rigid, make_item("n", "crescent", "hard_negative", circle(1.0))])
    pairs = {(row["left"], row["right"]) for row in labels["near_duplicate_pairs"]}
    assert ("copy", "q1") in pairs or ("q1", "copy") in pairs
    excluded = labels["queries"]["q1"]["excluded_as_query_or_near_duplicate"]
    assert excluded == ["copy", "q1"]
    assert "copy" not in labels["queries"]["q1"]["relevant_variants"]
    assert "copy" not in labels["queries"]["q1"]["library_t2_full"]


def test_near_duplicate_control_is_family_scoped():
    disk = make_item("d-blob", "blob", "hard_negative", circle(1.0))
    twin = make_item("d-star", "star", "hard_negative", circle(1.0))
    assert near_duplicate_pairs([disk, twin], family_only=True) == []
    cross = near_duplicate_pairs([disk, twin], family_only=False)
    assert len(cross) == 1
    assert cross[0]["align_distance"] <= 0.02


# --- fetching, caching and the pinned byte ranges ------------------------------


def test_fetch_cached_reads_a_seeded_file_and_reports_its_hash(tmp_path: Path):
    folder = tmp_path / "blob"
    folder.mkdir()
    url = "https://example.test/some/file.txt"
    payload = b"hello, contour\n"
    (folder / hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]).write_bytes(payload)
    got = fetch_cached(url, folder, offline=True)
    assert got["cached"] is True
    assert got["text"] == payload.decode()
    assert got["sha256"] == hashlib.sha256(payload).hexdigest()
    assert got["bytes"] == len(payload)


def test_offline_build_refuses_when_the_cache_is_missing(tmp_path: Path):
    with pytest.raises(RuntimeError, match="offline build requested"):
        fetch_cached("https://example.test/absent.svg", tmp_path / "none", offline=True)


def test_byte_range_and_whole_read_do_not_share_a_cache_key(tmp_path: Path):
    url = "https://example.test/big.ndjson"
    (tmp_path / hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]).write_bytes(b"whole")
    whole = fetch_cached(url, tmp_path, offline=True)
    with pytest.raises(RuntimeError, match="offline build requested"):
        fetch_cached(url, tmp_path, byte_range=(0, 99), offline=True)
    assert whole["text"] == "whole"


def test_spans_are_pinned_on_the_first_live_build_and_replayed_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    url = "https://example.test/star.ndjson"
    monkeypatch.setattr(pg, "remote_size", lambda _url, **_kw: 1_000_000)
    spans, source, total = resolve_quickdraw_spans("star", url, tmp_path)
    assert source == "remote"
    assert total == 1_000_000
    assert spans[0] == (0, pg.QD_SLICE_BYTES - 1)
    assert [start for start, _ in spans] == [
        int(fraction * 1_000_000) for fraction in pg.QD_SLICE_FRACTIONS
    ]
    assert pg._span_ledger_path(tmp_path).is_file()

    # a later build must replay the ledger even though the object now looks bigger
    monkeypatch.setattr(pg, "remote_size", lambda _url, **_kw: 5_000_000)
    replayed, replay_source, replay_total = resolve_quickdraw_spans("star", url, tmp_path)
    assert replayed == spans
    assert replay_total == total
    assert "(replayed)" in replay_source


def test_offline_build_without_a_ledger_is_an_error_not_a_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pg, "remote_size", lambda _url, **_kw: 10)
    with pytest.raises(RuntimeError, match="no pinned byte ranges"):
        resolve_quickdraw_spans("moon", "https://example.test/moon.ndjson", tmp_path, offline=True)


def test_a_failed_size_probe_refuses_to_guess_the_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pg, "remote_size", lambda _url, **_kw: None)
    with pytest.raises(RuntimeError, match="refusing to guess the byte ranges"):
        resolve_quickdraw_spans("leaf", "https://example.test/leaf.ndjson", tmp_path)


# --- quick draw record handling ------------------------------------------------


def test_partial_first_and_truncated_last_lines_of_a_slice_are_dropped():
    text = "\n".join(
        [
            '{"key_id": "a", "drawing": [[[1',  # cut mid-record by the range start
            '{"key_id": "b", "drawing": [[[0, 1], [1, 1]]], "word": "star"}',
            '{"key_id": "c", "drawing": [[[0, 1]',  # cut by the range end
            "",
        ]
    )
    rows = pg._qd_rows(text, drop_partial_first=True)
    assert [row["key_id"] for row in rows] == ["b"]
    kept = pg._qd_rows(text, drop_partial_first=False)
    assert [row["key_id"] for row in kept] == ["b"]  # the first line is not even a dict


def test_quickdraw_strokes_flip_y_and_mark_pen_up():
    drawing = [[[10, 20, 30], [40, 50, 60]]]
    strokes = quickdraw_strokes(drawing)
    assert len(strokes) == 1
    canvas = float(pg.QUICKDRAW["canvas_px"])
    assert strokes[0][0].tolist() == [10.0, canvas - 1.0 - 40.0]
    multi = quickdraw_strokes([[[0, 1], [0, 1]], [], [[2, 3], [4, 5]]])
    assert [int(stroke.shape[0]) for stroke in multi] == [2, 0, 2]
    markers = quickdraw_strokes([[[0, 1], [0, 1]], 101, [[2, 3], [4, 5]]])
    assert markers[1].shape == (0, 2)


def test_even_spaced_selection_is_deterministic_and_within_bounds():
    indices = list(range(1000))
    first = even_spaced(indices, 8)
    assert len(first) == 8 and len(set(first)) == 8
    assert first == even_spaced(indices, 8)
    assert first[0] == 0 and first[-1] == 999
    assert len(even_spaced(indices, 2000)) == 1000  # cannot invent records
    assert even_spaced(indices, 0) == []


# --- phosphor decoding policy --------------------------------------------------


def test_a_negative_space_icon_is_flagged_as_a_frame(tmp_path: Path):
    seed_phosphor(tmp_path, "snowflake", "fill", PANEL_WITH_CUTOUT)
    decoded = phosphor_rings("snowflake", weight="fill", cache_dir=tmp_path, offline=True)
    record = decoded["record"]
    assert record["exterior_rings"] == 1
    assert record["holes"] == 1
    assert record["frame_like"] is True


def test_a_genuine_silhouette_is_not_a_frame(tmp_path: Path):
    seed_phosphor(tmp_path, "star", "fill", PLAIN_STAR)
    decoded = phosphor_rings("star", weight="fill", cache_dir=tmp_path, offline=True)
    assert decoded["record"]["frame_like"] is False
    assert decoded["record"]["exterior_rings"] == 1
    assert decoded["record"]["holes"] == 0


def test_frame_icons_are_refused_with_a_counted_reason(tmp_path: Path):
    seed_phosphor(tmp_path, "snowflake", "fill", PANEL_WITH_CUTOUT)
    rejections: list[Rejection] = []
    item = phosphor_item(
        "ph-snowflake-fill", "star", "hard_negative", "snowflake", "fill", tmp_path, rejections
    )
    assert item is None
    assert [row.reason for row in rejections] == ["frame_silhouette"]
    assert "exterior-only" in rejections[0].detail


def test_several_unconnected_pieces_are_refused_not_glued(tmp_path: Path):
    seed_phosphor(tmp_path, "moon-stars", "fill", TWO_PIECES)
    rejections: list[Rejection] = []
    item = phosphor_item(
        "ph-moon-stars-fill", "crescent", "variant", "moon-stars", "fill", tmp_path, rejections
    )
    assert item is None
    assert [row.reason for row in rejections] == ["multiple_pieces"]


def test_unpainted_canvas_does_not_become_the_largest_ring(tmp_path: Path):
    document = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<rect width="256" height="256" fill="none"/>' + PLAIN_STAR.split(">", 1)[1]
    )
    seed_phosphor(tmp_path, "star", "fill", document)
    decoded = phosphor_rings("star", weight="fill", cache_dir=tmp_path, offline=True)
    assert decoded["record"]["invisible_elements_dropped"] == ["rect"]
    assert len(decoded["rings"]) == 1
    assert max(float(ring[:, 0].max()) for ring in decoded["rings"]) < 250.0


# --- generators and the spec table ---------------------------------------------


def test_matched_envelope_negative_reports_the_residual_it_achieved():
    ring, achieved, extra = blob_matching_solidity(0.92, count=3, sharpness=2.0)
    assert ring.shape[1] == 2
    assert extra["target_solidity"] == pytest.approx(0.92)
    assert achieved == pytest.approx(0.92, abs=0.004)


def test_matched_envelope_bisection_reports_a_target_it_cannot_reach():
    """A radial lobe shape is star-shaped about its centre: it cannot hollow out to 0.56.

    The bisection must still answer with the value it actually got, because that
    residual is what the manifest publishes as a measured limitation.
    """
    _ring, achieved, extra = blob_matching_solidity(0.557, count=2, sharpness=1.0)
    assert achieved > 0.9
    assert abs(achieved - extra["target_solidity"]) > 0.3


def test_every_synthetic_spec_names_a_registered_generator_and_a_real_family():
    registered = set(pg._GENERATORS) | {"blob_matching_solidity"}
    seen: set[str] = set()
    for spec in synthetic_specs():
        assert spec.item_id not in seen, f"duplicate item id {spec.item_id}"
        seen.add(spec.item_id)
        assert spec.family in pg.FAMILIES
        assert spec.role in pg.ROLES
        assert spec.generator in registered, spec.item_id


def test_plan_covers_every_family_with_two_independent_queries():
    """The brief's minimum is asserted from the *plan*, before any asset is fetched."""
    planned: dict[str, set[str]] = {}
    for entry in pg.phosphor_plan():
        planned.setdefault(entry.family, set()).add(f"phosphor:{entry.item_id}")
        assert entry.family in pg.FAMILIES and entry.role in pg.ROLES
    for entry in pg.quickdraw_plan():
        planned.setdefault(entry.family, set()).add(f"quickdraw:{entry.id_prefix}")
        assert entry.family in pg.FAMILIES and entry.role in pg.ROLES
    for spec in synthetic_specs():
        if spec.role == "query":
            planned.setdefault(spec.family, set()).add(f"synthetic:{spec.item_id}")
    for family in pg.FAMILIES:
        origins = {source.split(":", 1)[0] for source in planned.get(family, set())}
        assert len(origins & {"phosphor", "quickdraw", "synthetic"}) >= 2, family


def test_a_rounded_star_is_worn_and_not_spiked():
    """The bevel must cut either side of the corner, never overshoot past it.

    ``_bevel`` walks one cut point back along the incoming edge and the other forward
    along the outgoing one.  When the first was walked forward too, the curve jumped
    beyond the tip and doubled back, and ``densify`` filled the overshoot with near
    reversals: measured on ``star(5, 0.42, tip_round=valley_round=0.12)`` the worst
    vertex turn was 176.8 degrees (171.5 after standardization) and the boundary grew
    to 8.464 against the sharp star's 7.049 — so the gallery's "worn star" was a spiked
    one.  Rounding a corner can only take perimeter away, and a worn star still has to
    turn like a star.
    """

    def closed(points: np.ndarray) -> np.ndarray:
        return np.vstack([points, points[:1]])

    def perimeter(points: np.ndarray) -> float:
        return float(np.linalg.norm(np.diff(closed(points), axis=0), axis=1).sum())

    def area(points: np.ndarray) -> float:
        ring = closed(points)
        return float(abs(np.sum(ring[:-1, 0] * ring[1:, 1] - ring[:-1, 1] * ring[1:, 0])) / 2.0)

    def worst_turn(points: np.ndarray) -> float:
        step = np.diff(closed(points), axis=0)
        cross = step[:-1, 0] * step[1:, 1] - step[:-1, 1] * step[1:, 0]
        dot = np.sum(step[:-1] * step[1:], axis=1)
        return float(np.abs(np.arctan2(cross, dot)).max())

    sharp = star(5, 0.42)
    worn = star(5, 0.42, tip_round=0.12, valley_round=0.12)
    assert worst_turn(worn) < math.radians(90.0)
    assert worst_turn(standardize(worn)) < math.radians(120.0)
    assert perimeter(worn) < perimeter(sharp)
    assert abs(area(worn) - area(sharp)) / area(sharp) < 0.01


def test_build_synthetic_is_deterministic_and_refuses_nothing():
    rejections_a: list[Rejection] = []
    rejections_b: list[Rejection] = []
    items_a, _ = pg.build_synthetic(rejections_a)
    items_b, _ = pg.build_synthetic(rejections_b)
    assert rejections_a == [] and rejections_b == []
    assert [item.item_id for item in items_a] == [item.item_id for item in items_b]
    for left, right in zip(items_a, items_b, strict=True):
        assert np.array_equal(left.contour, right.contour)
        assert left.metrics == right.metrics
    assert items_a[0].contour.shape == (pg.N_SAMPLES, 2)
