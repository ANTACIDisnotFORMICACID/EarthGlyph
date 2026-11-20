"""M1 multi-stroke contour assembly tests (written before the implementation).

Contract frozen in docs/DECISIONS.md D93: an independent preprocessing adapter
that joins 2..8 strokes into one simple closed ring or refuses with a traceable
reason. Production retrieval math is never modified; equivalence tests compare
against the existing single-stroke path on a fixed synthetic index.

All geometry here is synthetic test input, not real geography and not human
data; nothing is registered into the P0-6 dataset.
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np
import pytest

from earthglyph.geometry.contour_assembly import (
    ENDPOINT_TOL_REL,
    MAX_STROKES,
    AssemblyError,
    assemble_contour,
    parse_strokes_payload,
)
from earthglyph.geometry.sketch import contour_from_points, trace_scale

CX, CY, R = 260.0, 260.0, 150.0


def circle_arc(a0: float, a1: float, n: int, *, r: float = R,
               cx: float = CX, cy: float = CY) -> list[list[float]]:
    return [
        [cx + r * np.cos(a0 + (a1 - a0) * i / (n - 1)),
         cy + r * np.sin(a0 + (a1 - a0) * i / (n - 1))]
        for i in range(n)
    ]


def split_closed_ring(points: list[list[float]], k: int) -> list[list[list[float]]]:
    """Split a closed trace (first point repeated at the end) into k strokes.

    Adjacent strokes share their boundary point exactly, so a correct assembler
    must recover the original ring with zero junction correction.
    """
    open_pts = points[:-1]
    assert np.allclose(points[0], points[-1])
    m = len(open_pts)
    assert m % k == 0
    step = m // k
    strokes = []
    for i in range(k):
        chunk = [open_pts[(i * step + j) % m] for j in range(step + 1)]
        strokes.append(chunk)
    return strokes


def circle_ring(n: int = 96) -> list[list[float]]:
    pts = circle_arc(0.0, 2 * np.pi, n + 1)
    pts[-1] = list(pts[0])
    return pts


def ring_array(strokes: list[list[list[float]]]) -> np.ndarray:
    return np.asarray([p for s in strokes for p in s], dtype=float)


def test_two_strokes_form_a_circle() -> None:
    strokes = split_closed_ring(circle_ring(), 2)
    result = assemble_contour(strokes)
    assert result.status == "ok", result.reason
    assert result.reason_code is None
    pts = result.contour_points
    assert pts is not None
    assert np.allclose(pts[0], pts[-1], rtol=0, atol=1e-12)  # closing vertex repeated
    assert len(result.junctions) == 2
    for junction in result.junctions:
        assert junction["gap"] <= ENDPOINT_TOL_REL * result.scale + 1e-9
        assert junction["correction"] <= 1e-9  # shared boundary points: zero snap
    assert len(result.order) == 2


@pytest.mark.parametrize("k", [2, 3, 4, 8])
def test_k_strokes_form_one_closed_ring(k: int) -> None:
    strokes = split_closed_ring(circle_ring(96), k)
    result = assemble_contour(strokes)
    assert result.status == "ok", result.reason
    assert result.contour_points is not None
    assert len(result.junctions) == k
    assert sorted(i for i, _ in result.order) == list(range(k))
    # every input point survives (up to dedupe of shared junction vertices)
    assembled = {tuple(np.round(p, 6)) for p in result.contour_points}
    for stroke in strokes:
        for p in stroke:
            assert tuple(np.round(p, 6)) in assembled


def test_shuffled_stroke_order_is_recovered() -> None:
    strokes = split_closed_ring(circle_ring(), 4)
    ordered = assemble_contour(strokes)
    shuffled = list(strokes)
    random.Random(42).shuffle(shuffled)
    result = assemble_contour(shuffled)
    assert result.status == "ok", result.reason
    assert result.contour_points is not None and ordered.contour_points is not None
    # compare the open rings: the repeated closing vertex is each walk's own
    # start point and legitimately differs between traversals
    a = np.sort(np.round(result.contour_points[:-1], 6), axis=0)
    b = np.sort(np.round(ordered.contour_points[:-1], 6), axis=0)
    assert a.shape == b.shape and np.allclose(a, b, atol=1e-6)


def test_one_stroke_with_reversed_direction_is_recovered() -> None:
    strokes = split_closed_ring(circle_ring(), 3)
    strokes[1] = strokes[1][::-1]
    result = assemble_contour(strokes)
    assert result.status == "ok", result.reason
    assert any(reversed_flag for _, reversed_flag in result.order)


def test_small_endpoint_gaps_are_snapped_and_recorded() -> None:
    # trim ~1 px off every arc end -> ~2 px gaps, below 0.03 * 150 = 4.5 px
    delta = 1.0 / R
    half = np.pi
    strokes = [
        circle_arc(delta, half - delta, 49),
        circle_arc(half + delta, 2 * np.pi - delta, 49),
    ]
    result = assemble_contour(strokes)
    assert result.status == "ok", result.reason
    assert result.contour_points is not None
    for junction in result.junctions:
        assert 0.5 < junction["gap"] < ENDPOINT_TOL_REL * result.scale
        assert junction["correction"] == pytest.approx(junction["gap"] / 2, abs=1e-9)
        snap = np.asarray(junction["snap_point"])
        mid = (np.asarray(junction["from_point"]) + np.asarray(junction["to_point"])) / 2
        assert np.allclose(snap, mid, atol=1e-9)
    assert result.warnings  # snapping is an assumption and must be announced


def test_non_uniform_sampling_density_is_accepted() -> None:
    strokes = [circle_arc(0.0, np.pi, 121), circle_arc(np.pi, 2 * np.pi, 25)]
    strokes[1][-1] = list(strokes[0][0])
    result = assemble_contour(strokes)
    assert result.status == "ok", result.reason


def test_split_single_trace_recovers_the_same_standardized_contour() -> None:
    """Ordered split -> assembly -> production extraction == direct extraction."""
    ring = circle_ring(96)
    direct = contour_from_points(ring, closed=True)
    for k in (2, 3, 4):
        assembled = assemble_contour(split_closed_ring(ring, k))
        assert assembled.status == "ok", assembled.reason
        assert assembled.contour_points is not None
        via = contour_from_points(assembled.contour_points, closed=True)
        assert np.allclose(via.points, direct.points, atol=1e-9), k
        assert via.winding == direct.winding


def test_two_separate_closed_loops_are_refused() -> None:
    left = circle_arc(0.0, 2 * np.pi, 49, r=60, cx=160)
    left[-1] = list(left[0])
    right = circle_arc(0.0, 2 * np.pi, 49, r=60, cx=360)
    right[-1] = list(right[0])
    result = assemble_contour([left, right])
    assert result.status == "rejected"
    assert result.reason_code == "closed_loop_with_extras"
    assert result.contour_points is None


def test_two_separate_open_arcs_are_refused() -> None:
    left = circle_arc(0.0, 1.5 * np.pi, 37, r=60, cx=160)
    right = circle_arc(0.0, 1.5 * np.pi, 37, r=60, cx=360)
    result = assemble_contour([left, right])
    assert result.status == "rejected"
    assert result.reason_code in {"open_gap", "no_legal_assembly"}


def test_t_junction_branch_is_refused() -> None:
    horizontal = [[100.0 + 10.0 * i, 260.0] for i in range(33)]
    up = [[260.0, 100.0 + 10.0 * i] for i in range(17)]
    down = [[260.0, 260.0 + 10.0 * i] for i in range(17)]
    result = assemble_contour([horizontal, up, down])
    assert result.status == "rejected"
    assert result.reason_code in {"open_gap", "no_legal_assembly"}


def test_bowtie_crossing_is_refused_as_self_intersection() -> None:
    def seg(a: tuple[float, float], b: tuple[float, float]) -> list[list[float]]:
        return [[a[0] + (b[0] - a[0]) * i / 10, a[1] + (b[1] - a[1]) * i / 10]
                for i in range(11)]

    strokes = [
        seg((100, 100), (420, 100)),
        seg((420, 100), (100, 420)),
        seg((100, 420), (420, 420)),
        seg((420, 420), (100, 100)),
    ]
    result = assemble_contour(strokes)
    assert result.status == "rejected"
    assert result.reason_code == "self_intersection"


def test_figure_eight_loops_are_refused() -> None:
    left = circle_arc(0.0, 2 * np.pi, 49, r=80, cx=180)
    left[-1] = list(left[0])
    right = circle_arc(0.0, 2 * np.pi, 49, r=80, cx=340)
    right[-1] = list(right[0])
    result = assemble_contour([left, right])
    assert result.status == "rejected"
    assert result.reason_code in {"closed_loop_with_extras", "self_intersection"}


def test_obvious_gap_is_refused_not_bridged() -> None:
    gap_rad = 30.0 / R  # ~30 px gap, far above 0.03 * scale
    strokes = [circle_arc(0.0, np.pi, 49), circle_arc(np.pi + gap_rad, 2 * np.pi, 49)]
    result = assemble_contour(strokes)
    assert result.status == "rejected"
    assert result.reason_code == "open_gap"
    assert result.contour_points is None


def test_isolated_extra_stroke_is_refused() -> None:
    strokes = split_closed_ring(circle_ring(), 2)
    strokes.append([[450.0, 450.0], [465.0, 465.0], [480.0, 480.0]])
    result = assemble_contour(strokes)
    assert result.status == "rejected"
    assert result.reason_code in {"open_gap", "no_legal_assembly"}


def ambiguous_lanes() -> list[list[list[float]]]:
    """Four nested lanes spanning the same two endpoint clusters.

    Endpoints fan out in convex position at each cluster (2 px offsets), so
    two different junction pairings — (A-B, C-D) vs (A-D, B-C) at a cluster —
    both stay distance-legal, turn-legal and produce simple rings. Verified
    by exhaustive matching enumeration (experiments/_probe_ambiguity.py):
    exactly 2 valid assemblies, the other pairings self-intersect.
    """
    px, py, qx, qy = 150.0, 260.0, 430.0, 260.0
    off_p = [(0.0, 2.0), (2.0, 0.0), (0.0, -2.0), (-2.0, 0.0)]
    off_q = [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)]
    strokes = []
    for k, bulge in enumerate((128.0, 40.0, -40.0, -128.0)):
        p0 = (px + off_p[k][0], py + off_p[k][1])
        p1 = (qx + off_q[k][0], qy + off_q[k][1])
        pts = []
        for i in range(33):
            t = i / 32
            pts.append([p0[0] + (p1[0] - p0[0]) * t,
                        p0[1] + (p1[1] - p0[1]) * t + 4 * bulge * t * (1 - t)])
        strokes.append(pts)
    return strokes


def test_multiple_plausible_assemblies_are_ambiguous_not_chosen() -> None:
    result = assemble_contour(ambiguous_lanes())
    assert result.status == "ambiguous"
    assert result.reason_code == "ambiguous"
    assert result.contour_points is None
    assert result.n_valid_solutions >= 2


def test_line_drawn_back_on_itself_is_refused() -> None:
    there = [[100.0 + 10.0 * i, 260.0] for i in range(33)]
    back = [[420.0 - 10.0 * i, 260.3] for i in range(33)]
    result = assemble_contour([there, back])
    assert result.status == "rejected"
    assert result.reason_code in {"no_legal_assembly", "degenerate_shape", "open_gap"}


def test_eight_stroke_star_polygon_is_refused_quickly() -> None:
    # {8/3} star: every junction turn is 135 deg (legal) but the ring crosses
    # itself materially; must refuse, not hang or accept
    verts = [[CX + R * np.cos(2 * np.pi * i / 8), CY + R * np.sin(2 * np.pi * i / 8)]
             for i in range(8)]
    strokes = []
    for i in range(8):
        a, b = verts[i], verts[(i + 3) % 8]
        strokes.append([[a[0] + (b[0] - a[0]) * t / 10, a[1] + (b[1] - a[1]) * t / 10]
                        for t in range(11)])
    result = assemble_contour(strokes)
    assert result.status == "rejected"
    assert result.reason_code in {"self_intersection", "no_legal_assembly"}


def test_same_input_gives_identical_results() -> None:
    strokes = split_closed_ring(circle_ring(), 4)
    first = assemble_contour(strokes).to_dict()
    second = assemble_contour(strokes).to_dict()
    assert first == second
    amb = ambiguous_lanes()
    assert assemble_contour(amb).to_dict() == assemble_contour(amb).to_dict()


def test_single_stroke_v2_delegates_closure_to_production() -> None:
    # gap ~0.10 of scale: beyond the 0.03 junction tolerance but within the
    # production single-stroke auto-close band -> assembly must not refuse it
    gap_rad = 0.10 * trace_scale(np.asarray(circle_arc(0, 2 * np.pi, 97))) / R
    stroke = circle_arc(0.0, 2 * np.pi - gap_rad, 97)
    result = assemble_contour([stroke])
    assert result.status == "ok", result.reason
    assert result.junctions == []
    assert result.contour_points is not None
    contour = contour_from_points(result.contour_points, closed=None)
    assert contour.closure["auto_closed"] == 1.0


@pytest.mark.parametrize(
    "strokes",
    [
        [],
        [[[0.0, 0.0]]],  # single point stroke
        [[[0.0, 0.0], [1.0, 1.0]], [[float("nan"), 0.0], [1.0, 1.0]]],
        [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], [[0.0, 0.0], [1.0, 1.0]]],
        [[[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]], [[1.0, 1.0], [2.0, 2.0]]],
    ],
)
def test_degenerate_or_malformed_strokes_raise(strokes: Any) -> None:
    with pytest.raises(AssemblyError):
        assemble_contour(strokes)


def test_too_many_strokes_raise() -> None:
    stroke = circle_arc(0.0, np.pi / 4, 9)
    with pytest.raises(AssemblyError) as exc:
        assemble_contour([stroke] * (MAX_STROKES + 1))
    assert "stroke" in str(exc.value).lower()


def test_too_many_points_raise() -> None:
    dense = circle_arc(0.0, np.pi, 3000)
    with pytest.raises(AssemblyError):
        assemble_contour([dense, dense])


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"strokes": []},
        {"schema_version": 1, "strokes": [{"points": [[0, 0], [1, 1]]}]},
        {"schema_version": "2", "strokes": [{"points": [[0, 0], [1, 1]]}]},
        {"schema_version": True, "strokes": [{"points": [[0, 0], [1, 1]]}]},
        {"schema_version": 2, "mode": "road", "strokes": [{"points": [[0, 0], [1, 1]]}]},
        {"schema_version": 2, "strokes": "not-a-list"},
        {"schema_version": 2, "strokes": [[[0, 0], [1, 1]]]},
        {"schema_version": 2, "strokes": [{"no_points": 1}]},
        {"schema_version": 2, "strokes": [{"points": [[0, 0], [1, 1]]}],
         "points": [[0, 0], [1, 1]]},
    ],
)
def test_parse_rejects_bad_v2_payloads(payload: Any) -> None:
    with pytest.raises(AssemblyError):
        parse_strokes_payload(payload)


def test_parse_accepts_the_frozen_v2_shape() -> None:
    payload = {
        "schema_version": 2,
        "mode": "contour",
        "unit": "canvas",
        "strokes": [
            {"points": [[10, 20], [20, 15], [30, 20]]},
            {"points": [[30, 20], [20, 25], [10, 20]]},
        ],
    }
    strokes, unit = parse_strokes_payload(payload)
    assert unit == "canvas"
    assert len(strokes) == 2
    assert strokes[0].shape == (3, 2)