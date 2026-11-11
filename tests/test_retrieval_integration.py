"""End-to-end retrieval against the real P0-3 index (P0-4, Task D).

Every assertion runs on the published 839-candidate library over the ``1°×1°``
tile around Wittenberg, Wisconsin — real ``candidate_id`` / ``source_id`` /
coordinates, no fixture standing in for the library. The file skips itself when
``data/index`` is missing, because that directory is a build product and is not
in Git; the reproduce command lives in ``docs/DATA_PIPELINE.md``.

Synthetic shapes appear only as *queries* (a mirrored copy of a real candidate, a
noised copy of one, a rectangle). Nothing here claims a hand-drawn result, and no
expected landform is hard-coded: what is pinned is the contract — provenance,
determinism, the reported transform reproducing its own score, the mirror switch
behaving geometrically, and the audit trail staying visible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely.geometry import Polygon

from earthglyph.geometry.sketch import (
    OPEN_STROKE_SOLIDITY,
    OPEN_STROKE_WIDTH_REL,
    SketchError,
    contour_from_image,
    contour_from_sketch,
    trace_scale,
)
from earthglyph.retrieval.alignment import align, apply_alignment
from earthglyph.retrieval.query import feature_columns, prepare
from earthglyph.retrieval.search import (
    METHODS,
    SearchError,
    load_index_handle,
    search_by_contour,
    search_by_image,
)

INDEX_DIR = Path(__file__).resolve().parents[1] / "data" / "index"

pytestmark = pytest.mark.skipif(
    not (INDEX_DIR / "data_manifest.json").exists(),
    reason="data/index is a build product; run the index build from docs/DATA_PIPELINE.md",
)


@pytest.fixture(scope="module")
def index() -> Any:
    return load_index_handle(INDEX_DIR)


def rectangle(n: int = 200, width: float = 1.0, height: float = 0.3) -> np.ndarray:
    """A four-sided trace, walked once, at arbitrary sampling density."""
    corners = np.array(
        [(-width, -height), (width, -height), (width, height), (-width, height)], dtype=float
    )
    t = np.linspace(0.0, 1.0, n // 4, endpoint=False)[:, None]
    sides = [corners[i] + t * (corners[(i + 1) % 4] - corners[i]) for i in range(4)]
    return np.vstack(sides)


def noised(index: Any, row: int, seed: int = 4, jitter: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return index.shapes[row] * (1.0 + rng.normal(0.0, jitter, index.shapes[row].shape))


# --- the library itself ------------------------------------------------------


def test_index_loads_and_reports_where_it_came_from(index: Any) -> None:
    provenance = index.provenance()
    assert provenance["n_candidates"] == 839
    assert provenance["n_points"] == 128
    assert provenance["n_harmonics"] == 16
    assert provenance["fourier_fold"] == "positive"
    assert provenance["feature_dim"] == 20
    assert provenance["source_name"].startswith("Overture")
    assert len(provenance["index_git_commit"]) == 40
    # three content digests, so a result can be tied to the bytes that produced it
    assert set(provenance["output_sha256"]) == {"features", "shapes", "metadata"}
    assert all(len(value) == 64 for value in provenance["output_sha256"].values())
    box = provenance["source_bbox"]
    assert (box["min_lon"], box["max_lon"]) == (-90.0, -89.0)
    assert (box["min_lat"], box["max_lat"]) == (44.0, 45.0)


def test_library_contours_are_unit_rms_centred_rings(index: Any) -> None:
    assert index.shapes.shape == (839, 128, 2)
    rms = np.sqrt(np.mean(np.sum(index.shapes**2, axis=2), axis=1))
    assert np.all(np.abs(rms - 1.0) < 1e-3)  # float32 storage, so not exactly 1
    centre = index.shapes.mean(axis=1)
    assert np.max(np.abs(centre)) < 1e-3


def test_verification_refuses_a_library_that_does_not_agree_with_itself(tmp_path: Path) -> None:
    for name in ("features.npy", "shapes.npy", "metadata.parquet", "data_manifest.json"):
        (tmp_path / name).write_bytes((INDEX_DIR / name).read_bytes())
    broken = np.load(tmp_path / "features.npy")
    broken[3, 0] = np.nan
    np.save(tmp_path / "features.npy", broken)
    with pytest.raises(SearchError, match="consistency check"):
        load_index_handle(tmp_path)


# --- the reported search is the search that ran ------------------------------


@pytest.mark.parametrize("row", [0, 7, 100, 400, 838])
def test_a_candidate_is_its_own_best_match(index: Any, row: int) -> None:
    """Algorithm correctness (G1), not hand-drawing performance.

    The query is the stored float32 contour re-traced through the query pipeline,
    so the residual measures the pipeline's own round-trip error: ``< 0.02`` of the
    shape radius on every probed row.
    """
    result = search_by_contour(index.shapes[row], index, top_k=5, smoothing="none")
    best = result.hits[0]
    assert best.row == row
    assert best.align_distance < 0.02
    assert best.coarse_distance < 0.01
    assert best.rank == 1


def test_hits_carry_real_geography_not_placeholders(index: Any) -> None:
    result = search_by_contour(rectangle(), index, top_k=10, smoothing="none")
    box = index.provenance()["source_bbox"]
    seen_ids = set()
    for hit in result.hits:
        assert hit.candidate_id and hit.source_id
        assert hit.subtype in {"lake", "pond", "reservoir", "river"}
        assert box["min_lon"] <= hit.lon <= box["max_lon"]
        assert box["min_lat"] <= hit.lat <= box["max_lat"]
        assert hit.area_m2 > 0.0
        assert hit.candidate_id not in seen_ids
        seen_ids.add(hit.candidate_id)
    assert [hit.rank for hit in result.hits] == list(range(1, len(result.hits) + 1))


def test_the_reported_transform_reproduces_the_reported_distance(index: Any) -> None:
    """``(shift, theta, mirrored)`` describes the match that produced the number."""
    result = search_by_contour(index.shapes[7], index, top_k=3, smoothing="none")
    hit = result.hits[0]
    moved = apply_alignment(
        index.shapes[hit.row],
        shift=hit.align_shift,
        theta_rad=np.deg2rad(hit.align_theta_deg),
        mirrored=hit.align_mirrored,
    )
    assert align(index.shapes[7], moved).distance < 1e-6
    assert hit.contour is not None
    assert np.max(np.abs(hit.contour - moved)) < 1e-9


def test_repeated_searches_return_the_same_answer(index: Any) -> None:
    first = search_by_contour(rectangle(), index, top_k=5, chamfer_top=3, smoothing="none")
    second = search_by_contour(rectangle(), index, top_k=5, chamfer_top=3, smoothing="none")
    left, right = first.to_dict(), second.to_dict()
    left.pop("timings"), right.pop("timings")
    assert left == right


def test_no_uncalibrated_similarity_number_is_reported(index: Any) -> None:
    payload = search_by_contour(rectangle(), index, top_k=3, smoothing="none").to_dict()
    for hit in payload["results"]:
        assert not any("similarity" in key for key in hit)
        assert hit["align_distance"] is None or 0.0 <= hit["align_distance"] < 1.0
        assert -180.0 <= hit["align_theta_deg"] <= 180.0


# --- the mirror switch is geometry (D33) -------------------------------------


def test_mirrors_are_never_used_unless_they_are_asked_for(index: Any) -> None:
    mirrored = index.shapes[7] * np.array([-1.0, 1.0])
    strict = search_by_contour(mirrored, index, top_k=5, smoothing="none", allow_mirror=False)
    assert all(hit.align_mirrored is False for hit in strict.hits)
    assert all(hit.align_mirrored is False for hit in strict.pre_dedup)


def test_allowing_mirrors_recovers_the_candidate_a_mirror_was_cut_from(index: Any) -> None:
    """The same query, the same library, one switch: row 7 is reachable again.

    ``allow_mirror=False`` cannot see row 7 at all in its top 3 (its best is the
    far worse row 157), while ``allow_mirror=True`` returns row 7 first and says
    so. The separation is ``0.011`` against ``0.333`` — two orders of magnitude,
    produced by the correspondence check, not by a numerical accident in the
    amplitudes (which are *exactly* invariant to this reflection).
    """
    mirrored = index.shapes[7] * np.array([-1.0, 1.0])
    strict = search_by_contour(mirrored, index, top_k=3, smoothing="none", allow_mirror=False)
    lenient = search_by_contour(mirrored, index, top_k=3, smoothing="none", allow_mirror=True)
    assert strict.hits[0].row != 7
    assert strict.hits[0].align_distance > 0.3
    assert lenient.hits[0].row == 7
    assert lenient.hits[0].align_mirrored is True
    assert lenient.hits[0].align_distance < 0.05
    assert lenient.hits[0].align_distance < strict.hits[0].align_distance / 10.0


def test_a_weak_best_is_labelled_as_such(index: Any) -> None:
    mirrored = index.shapes[7] * np.array([-1.0, 1.0])
    result = search_by_contour(mirrored, index, top_k=3, smoothing="none", allow_mirror=False)
    assert result.hits[0].align_distance > 0.12
    assert any("closest candidates in this library" in note for note in result.warnings)
    assert any("mirrors disallowed" in note for note in result.warnings)


# --- the two stages stay separable -------------------------------------------


def test_the_coarse_stage_narrows_and_the_re_rank_sorts(index: Any) -> None:
    result = search_by_contour(index.shapes[7], index, top_k=3, shortlist_size=50, smoothing="none")
    assert result.shortlist is not None
    assert len(result.shortlist.indices) == 50
    assert result.shortlist.group == "fourier"
    rows = {hit.row for hit in result.pre_dedup}
    assert rows <= set(result.shortlist.indices.tolist())


def test_a_shortlist_that_is_too_small_really_does_lose_the_best(index: Any) -> None:
    """The Task H diagnostic, pinned: truncation is a source of error by itself.

    A noised copy of candidate 250 is best matched by candidate 250 when the whole
    library is re-ranked (``0.122``), but a one-candidate shortlist is forced to
    answer with row 220 at ``0.245``. So a miss can come from the coarse filter
    rather than from the matcher, and ``method="align"`` is how the two are told
    apart.
    """
    query = noised(index, 250)
    truncated = search_by_contour(query, index, top_k=3, shortlist_size=1, smoothing="none")
    full = search_by_contour(query, index, top_k=3, method="align", smoothing="none")
    assert len(truncated.hits) == 1
    assert truncated.hits[0].row == 220
    assert full.hits[0].row == 250
    assert full.hits[0].align_distance < truncated.hits[0].align_distance


@pytest.mark.parametrize("group", ["fourier", "geometry", "all"])
def test_each_coarse_group_answers_on_its_own_columns(index: Any, group: str) -> None:
    result = search_by_contour(
        rectangle(), index, top_k=3, method=f"{group}_align", smoothing="none"
    )
    assert result.shortlist.group == group
    width = 16 if group == "fourier" else (4 if group == "geometry" else 20)
    assert len(result.shortlist.indices) == 200
    payload = result.to_dict()["shortlist"]
    assert payload["coarse_group"] == group and payload["coarse_shortlist_size"] == 200
    assert len(payload["coarse_top_distances"]) == min(10, width + 200)


def test_coarse_only_methods_report_no_alignment(index: Any) -> None:
    result = search_by_contour(rectangle(), index, top_k=3, method="fourier", smoothing="none")
    for hit in result.hits:
        assert hit.align_distance is None
        assert hit.align_shift is None
        assert hit.align_mirrored is None
        assert hit.coarse_distance is not None


def test_the_chamfer_cross_check_runs_only_on_its_window(index: Any) -> None:
    result = search_by_contour(
        index.shapes[7], index, top_k=5, chamfer_top=3, smoothing="none", sort_by="chamfer"
    )
    scored = [hit for hit in result.hits if hit.chamfer is not None]
    assert len(scored) == 3
    assert scored[0].chamfer <= max(hit.chamfer for hit in scored) + 1e-12
    assert result.timings["chamfer"] > 0.0


def test_sorting_by_chamfer_reorders_the_window_it_scored(index: Any) -> None:
    plain = search_by_contour(
        index.shapes[7], index, top_k=5, chamfer_top=3, smoothing="none", sort_by="align"
    )
    by_chamfer = search_by_contour(
        index.shapes[7], index, top_k=5, chamfer_top=3, smoothing="none", sort_by="chamfer"
    )
    assert {hit.row for hit in plain.hits[:3]} == {hit.row for hit in by_chamfer.hits[:3]}
    assert [hit.row for hit in by_chamfer.hits[3:]] == [hit.row for hit in plain.hits[3:]]


# --- the Hu baseline is a different instrument -------------------------------


def test_hu_method_has_no_alignment_stage_and_reports_its_cost(index: Any) -> None:
    pytest.importorskip("cv2")  # the hu arm rasterises and calls cv2.matchShapes
    result = search_by_contour(index.shapes[7], index, top_k=5, method="hu", smoothing="none")
    assert result.shortlist is None
    assert result.hu is not None
    for hit in result.hits:
        assert hit.align_distance is None
        assert hit.coarse_distance is not None
    assert result.timings["rerank"] == 0.0
    assert result.timings["coarse"] > 0.1  # one matchShapes call per candidate
    payload = result.to_dict()["hu"]
    assert payload["hu_method"] == "I1" and payload["hu_source"] == "mask"
    assert payload["hu_canvas"] == 512
    assert payload["hu_top_indices"][:1] == [hit.row for hit in result.hits[:1]]


# --- every documented method runs -------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_documented_methods_return_ranked_real_hits(index: Any, method: str) -> None:
    if method == "hu":
        pytest.importorskip("cv2")  # the hu arm rasterises and calls cv2.matchShapes
    result = search_by_contour(index.shapes[100], index, top_k=4, method=method, smoothing="none")
    assert len(result.hits) == 4
    assert result.method == method
    assert [hit.rank for hit in result.hits] == [1, 2, 3, 4]
    assert len({hit.row for hit in result.hits}) == 4
    assert result.timings["total"] > 0.0


def test_dedup_caps_a_source_but_keeps_the_pre_dedup_audit(index: Any) -> None:
    """The cap changes a real answer, and the audit trail still shows what it removed.

    ``source_id`` ``048b7831`` contributes two parts of one lake (rows 744 and 745,
    52 567 and 69 849 m², 350 m apart). Queried with row 745 and ``top_k=30``, the
    sibling reaches rank 27, so it is inside the window: allowing two parts per
    source returns it, the default of one drops exactly it, and only the capped run
    stays free of the "still share a source_id" warning. The dropped row remains
    visible in ``pre_dedup`` at the position the matcher gave it.

    At the ordinary ``top_k=10`` no query in this library repeats a source — the
    parts of one source are neighbours on the ground, not look-alikes, and the
    measured sibling rank is 27 — so the binding case is widened here rather than
    faked with a fixture.
    """
    loose = search_by_contour(
        index.shapes[745], index, top_k=30, max_per_source=2, smoothing="none"
    )
    capped = search_by_contour(index.shapes[745], index, top_k=30, smoothing="none")
    assert loose.hits[0].row == 745
    assert loose.hits[26].row == 744
    assert loose.hits[26].source_id == loose.hits[0].source_id
    loose_rows = [hit.row for hit in loose.hits]
    audit_rows = [hit.row for hit in loose.pre_dedup]
    assert [hit.row for hit in capped.hits] == [row for row in audit_rows if row != 744][:30]
    assert len({hit.source_id for hit in capped.hits}) == len(capped.hits)
    assert any("share a source_id" in note for note in loose.warnings)
    assert not any("share a source_id" in note for note in capped.warnings)
    # the audit window is unfiltered: both parts are in it, in matcher order
    assert [hit.row for hit in loose.pre_dedup[:30]] == loose_rows
    assert {744, 745} <= {hit.row for hit in capped.pre_dedup}
    assert capped.pre_dedup[0].row == 745


def test_every_library_contour_survives_the_query_path(index: Any) -> None:
    """The query contract accepts all 839 stored contours, including the ragged ones.

    Two of them are closed only as a *seam*: row 294 (a 22.7 km² reservoir, perimeter
    15.6 RMS radii) and row 365 (a dendritic pond, 20.0) have wrap chords of 0.196 and
    0.157 of their own scale, which is what a 128-point sampling of a long boundary
    costs and not an open stroke. They are recorded here because §12.2 of the brief
    asks for exactly these candidates to stay testable — dropping them from the
    library would be the wrong fix, and a closure rule that re-loses them must fail.
    """
    seam: list[int] = []
    for row in range(index.n_candidates):
        contour = contour_from_sketch(
            index.shapes[row], n_points=index.n_points, smoothing="none"
        )
        if contour.closure["seam_closed"]:
            seam.append(row)
    assert seam == [294, 365]


def test_the_query_contract_is_checked_against_the_index(index: Any) -> None:
    wrong = prepare(rectangle(), n_harmonics=8)
    with pytest.raises(Exception, match="does not match the index"):
        search_by_contour(wrong, index, top_k=3)
    assert feature_columns(16, "positive") == index.columns


def test_an_image_query_reaches_the_same_library(index: Any, tmp_path: Path) -> None:
    """A PNG of a real candidate's outline, drawn at 256 px, still finds it.

    The distance is an order of magnitude worse than the vector path (``0.047``
    against ``0.010``) because the pixel grid quantizes the stroke — that gap is a
    property of the input, recorded here so nobody later mistakes it for a
    retrieval regression.
    """
    cv2 = pytest.importorskip("cv2")
    canvas = 256
    contour = index.shapes[7]
    scale = 0.42 * canvas / float(np.abs(contour).max())
    pixel = np.column_stack(
        [canvas / 2 + scale * contour[:, 0], canvas / 2 - scale * contour[:, 1]]
    ).astype(np.int32)
    image = np.full((canvas, canvas), 255, dtype=np.uint8)
    cv2.polylines(image, [pixel], True, 0, 3)
    path = tmp_path / "sketch.png"
    assert cv2.imwrite(str(path), image)
    result = search_by_image(path, index, top_k=5, smoothing="none")
    assert result.hits[0].row == 7
    assert result.hits[0].align_distance < 0.06
    assert result.query["query_contour"]["source"] == "image"
    assert result.to_dict()["query"]["query_columns"] == index.columns


def test_the_most_band_like_candidates_are_not_read_as_open_strokes(
    index: Any, tmp_path: Path
) -> None:
    """The image path refuses an unclosed pen stroke by measuring the ink band
    (:data:`OPEN_STROKE_WIDTH_REL` / :data:`OPEN_STROKE_SOLIDITY`). Measured on the
    real library, the worst candidates against those limits are row 419 (band width
    0.065 of its scale, solidity 0.160) and row 536 (0.092, 0.238), while a 4-pixel
    270° arc is (0.026, 0.057) — so the limits sit below every real shape.

    Rasterizing the twelve most band-like contours as *filled* ink re-checks that
    against the library as it is today, not against the numbers quoted above: no
    dendritic or elongated lake may be refused as a stroke.
    """
    cv2 = pytest.importorskip("cv2")
    canvas = 320
    scores = []
    for row in range(index.n_candidates):
        contour = index.shapes[row]
        polygon = Polygon(contour)
        if polygon.is_empty or not polygon.is_valid:
            continue
        width_rel = 2.0 * polygon.area / polygon.length / trace_scale(contour)
        scores.append((width_rel, row, polygon.area / polygon.convex_hull.area))
    scores.sort()
    refusals = []
    for width_rel, row, solidity in scores[:12]:
        contour = index.shapes[row]
        scale = 0.42 * canvas / float(np.abs(contour).max())
        pixel = np.column_stack(
            [canvas / 2 + scale * contour[:, 0], canvas / 2 - scale * contour[:, 1]]
        ).astype(np.int32)
        image = np.full((canvas, canvas), 255, dtype=np.uint8)
        cv2.fillPoly(image, [pixel], 0)
        path = tmp_path / f"filled_{row}.png"
        assert cv2.imwrite(str(path), image)
        try:
            contour_from_image(path, smoothing="none")
        except SketchError as error:
            refusals.append((row, width_rel, solidity, str(error)))
    assert refusals == []
    assert scores[0][2] > OPEN_STROKE_SOLIDITY
    assert scores[0][0] > OPEN_STROKE_WIDTH_REL


def test_an_ambiguous_image_is_refused_rather_than_guessed(index: Any, tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")
    image = np.full((200, 200), 255, dtype=np.uint8)
    cv2.circle(image, (60, 60), 30, 0, 3)
    cv2.circle(image, (140, 140), 30, 0, 3)
    path = tmp_path / "two_shapes.png"
    assert cv2.imwrite(str(path), image)
    with pytest.raises(Exception, match="one closed outline|separate shapes"):
        search_by_image(path, index, top_k=3)
