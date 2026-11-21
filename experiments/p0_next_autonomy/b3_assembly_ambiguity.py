#!/usr/bin/env python
"""Phase B / B3 — global assembly and ambiguity edges of the M1 assembly
(task brief §四 B problem 3).

Edge classes probed (all synthetic geometry):
  E1 tolerance boundary   a two-stroke circle whose endpoint gap sits exactly
                          at the tolerance. ``gap <= tol`` is the designed
                          rule, so gap == tol must assemble and gap > tol
                          must refuse (open_gap). Characterized.
  E2 multiple loops       two separate two-stroke circles: every endpoint has
                          a close partner, but no single ring uses all four
                          strokes -> must refuse (no legal assembly), never
                          merge two loops into one outline. Characterized.
  E3 near-coincident      the bottom side drawn twice (0.5 px apart) plus the
       duplicate strokes  other three sides: the ring must use every stroke,
                          so any assembly traverses the doubled side twice —
                          user-intent ambiguity, expected ambiguous or
                          refused, never a silent single pick. Characterized.
  E4 search budget        the ``search_complexity`` refusal (node budget
                          100k / matching cap 10k) has had no test coverage
                          since M1 shipped. Adversarial <=8-stroke inputs are
                          timed and their exact dfs node counts bisected
                          through process-locally lowered budgets (production
                          constants untouched); the refusal path itself is
                          then functionally verified by lowering the budgets
                          on purpose. Side finding: the matching cap is an
                          abort guard, not a strict bound — an enumeration
                          that finishes naturally returns its true verdict
                          even past the cap value.

T junctions, bowtie/X crossings, figure-eights and the 8-stroke star are
already pinned by tests/test_multistroke_assembly.py (t_junction / bowtie /
figure_eight / star_polygon) and are not re-probed here.

Run: python experiments/p0_next_autonomy/b3_assembly_ambiguity.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import earthglyph.geometry.contour_assembly as ca  # noqa: E402
from earthglyph.geometry.contour_assembly import ENDPOINT_TOL_REL  # noqa: E402

CX, CY, R = 260.0, 260.0, 150.0


def arc(a0: float, a1: float, n: int, *, r: float = R,
        cx: float = CX, cy: float = CY) -> np.ndarray:
    t = np.linspace(a0, a1, n)
    return np.column_stack((cx + r * np.cos(t), cy + r * np.sin(t)))


def seg(a: tuple[float, float], b: tuple[float, float], n: int,
        dy: float = 0.0) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    return np.column_stack((a[0] + (b[0] - a[0]) * t,
                            a[1] + dy + (b[1] - a[1]) * t))


def report(name: str, strokes: list[np.ndarray]) -> None:
    start = time.perf_counter()
    result = ca.assemble_contour(strokes)
    elapsed = time.perf_counter() - start
    print(f"  {name:36s} status={result.status:9s} "
          f"reason={str(result.reason_code):20s} "
          f"n_valid={result.n_valid_solutions} "
          f"junctions={len(result.junctions)} "
          f"time={elapsed * 1000:7.1f} ms")


def e1_tolerance_boundary() -> None:
    print("E1 endpoint gap at the tolerance (bisect the legal boundary):")
    def two_arcs(gap: float) -> list[np.ndarray]:
        theta = 2.0 * np.arcsin(gap / (2.0 * R))
        return [arc(theta / 2.0, np.pi - theta / 2.0, 49),
                arc(np.pi + theta / 2.0, 2 * np.pi - theta / 2.0, 49)]

    def status_at(gap: float) -> tuple[str, float]:
        result = ca.assemble_contour(two_arcs(gap))
        return result.status, result.scale

    # bisect the ok -> refused boundary; the scale (and tol) move slightly
    # with the gap, so the boundary must be found self-consistently
    lo, hi = 0.1, 10.0  # lo assembles, hi does not
    assert status_at(lo)[0] == "ok", "bisection bracket broken at the low end"
    assert status_at(hi)[0] != "ok", "bisection bracket broken at the high end"
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if status_at(mid)[0] == "ok":
            lo = mid
        else:
            hi = mid
    scale = ca.assemble_contour(two_arcs(lo)).scale
    print(f"  empirical legal-gap boundary: {lo:.6f}..{hi:.6f} px "
          f"(bisected); assembly tol = {ENDPOINT_TOL_REL} * {scale:.3f} = "
          f"{ENDPOINT_TOL_REL * scale:.6f} px")
    for label, gap in (("boundary - 0.01", lo - 0.01),
                       ("boundary + 0.01", hi + 0.01)):
        status, _ = status_at(gap)
        print(f"  gap={label:17s} ({gap:7.3f} px): status={status}")


def e2_multiple_loops() -> None:
    print("E2 two separate two-stroke circles (characterize):")
    left = [arc(0.0, np.pi, 33, r=60, cx=160), arc(np.pi, 2 * np.pi, 33, r=60, cx=160)]
    right = [arc(0.0, np.pi, 33, r=60, cx=360), arc(np.pi, 2 * np.pi, 33, r=60, cx=360)]
    report("two_circles_four_strokes", left + right)


def e3_duplicated_side() -> None:
    print("E3 bottom side drawn twice, 0.5 px apart (characterize):")
    W, H = 400.0, 240.0
    strokes = [
        seg((0.0, 0.0), (W, 0.0), 25, dy=0.0),
        seg((0.0, 0.0), (W, 0.0), 25, dy=0.5),
        seg((W, 0.0), (W, H), 25),
        seg((W, H), (0.0, H), 25),
        seg((0.0, H), (0.0, 0.0), 25),
    ]
    report("doubled_bottom_side", strokes)


def two_cluster_fan() -> list[np.ndarray]:
    """8 strokes between two tight clusters, half drawn in each direction:
    with mixed directions the same-type cluster junctions (end->end,
    start->start) become turn-legal too, so nearly every endpoint pair is a
    legal edge — the densest legal-edge graph a two-cluster layout allows."""
    c1 = np.asarray([100.0, 100.0])
    c2 = np.asarray([400.0, 100.0])
    strokes = []
    for k in range(8):
        spread = 1.5 * np.asarray([np.cos(k * np.pi / 4), np.sin(k * np.pi / 4)])
        a, b = c1 + spread, c2 - spread
        if k % 2:  # half the strokes are drawn C2 -> C1
            a, b = b, a
        t = np.linspace(0.0, 1.0, 21)
        strokes.append(np.column_stack(
            (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)))
    return strokes


def square_doubled_mixed() -> list[np.ndarray]:
    """Every side of a square drawn twice, once per direction: four corner
    clusters of four coincident endpoints each — the densest legal-edge
    graph we could construct within the eight-stroke limit (no cross-stroke
    dedupe exists, so the duplicates all stay in the search)."""
    a, b, c, d = (100.0, 100.0), (400.0, 100.0), (400.0, 400.0), (100.0, 400.0)
    strokes: list[np.ndarray] = []
    for p, q in ((a, b), (b, c), (c, d), (d, a)):
        strokes.append(seg(p, q, 21))
        strokes.append(seg(q, p, 21))
    return strokes


def ambiguous_four_lanes() -> list[np.ndarray]:
    """The four-lane ambiguity fixture from tests/test_multistroke_assembly.py
    (two distance- and turn-legal junction pairings -> exactly 2 valid
    assemblies, verified there by exhaustive matching enumeration)."""
    px, py, qx, qy = 150.0, 260.0, 430.0, 260.0
    off_p = [(0.0, 2.0), (2.0, 0.0), (0.0, -2.0), (-2.0, 0.0)]
    off_q = [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)]
    strokes = []
    for k, bulge in enumerate((128.0, 40.0, -40.0, -128.0)):
        p0 = (px + off_p[k][0], py + off_p[k][1])
        p1 = (qx + off_q[k][0], qy + off_q[k][1])
        t = np.linspace(0.0, 1.0, 33)
        strokes.append(np.column_stack(
            (p0[0] + (p1[0] - p0[0]) * t,
             p0[1] + (p1[1] - p0[1]) * t + 4 * bulge * t * (1 - t))))
    return strokes


def bent_strokes_two_clusters() -> list[np.ndarray]:
    """8 two-segment strokes between two clusters, each bending at a point
    placed on a compass ray around the far cluster: arrival and departure
    headings at the clusters are spread over directions, which is the only
    way to soften the structural U-turn a straight two-cluster fan forces
    (arrive moving east, depart moving west -> 180 deg, always illegal)."""
    a = np.asarray([120.0, 260.0])
    b = np.asarray([400.0, 260.0])
    strokes = []
    for k in range(8):
        phi = k * np.pi / 4
        bend = b + 120.0 * np.asarray([np.cos(phi), np.sin(phi)])
        first = seg(tuple(a), tuple(bend), 25)
        second = seg(tuple(bend), tuple(b), 25)
        strokes.append(np.vstack((first[:-1], second)))
    return strokes


def count_search_size(name: str, strokes: list[np.ndarray]) -> tuple[int, int]:
    """Bisect the exact dfs node count and the matching count observable by
    the entry guard, by process-locally lowering the two budgets; the patch
    context managers restore the production constants after every probe.

    Node counts are exact (the guard checks at every node entry). The
    matching figure is what the entry guard can see: matchings appended
    after the final node entry stay unobserved, so it is a lower bound on
    the enumerated cycles (observed on ambiguous_four_lanes: guard sees 1,
    yet 2 valid assemblies exist -> at least 2 cycles were enumerated)."""
    with mock.patch.object(ca, "MAX_MATCHINGS", 10 ** 9):
        lo, hi = 1, ca.SEARCH_NODE_BUDGET  # default budget completes (timed)
        while lo < hi:
            mid = (lo + hi) // 2
            with mock.patch.object(ca, "SEARCH_NODE_BUDGET", mid):
                fired = (ca.assemble_contour(strokes).reason_code
                         == "search_complexity")
            if fired:
                lo = mid + 1
            else:
                hi = mid
        nodes = lo
    with mock.patch.object(ca, "SEARCH_NODE_BUDGET", 10 ** 9):
        with mock.patch.object(ca, "MAX_MATCHINGS", 0):
            fired = (ca.assemble_contour(strokes).reason_code
                     == "search_complexity")
        if not fired:
            matchings = 0
        else:
            lo, hi = 1, ca.MAX_MATCHINGS
            while lo < hi:
                mid = (lo + hi) // 2
                with mock.patch.object(ca, "MAX_MATCHINGS", mid):
                    fired = (ca.assemble_contour(strokes).reason_code
                             == "search_complexity")
                if fired:
                    lo = mid + 1
                else:
                    hi = mid
            matchings = lo
    print(f"  {name:36s} exact search size: {nodes} nodes "
          f"({100.0 * nodes / ca.SEARCH_NODE_BUDGET:.2f}% of budget), "
          f">={matchings} matchings at the guard")
    return nodes, matchings


def e4_search_budget() -> None:
    print("E4 dense endpoint-cluster graphs vs the backtracking budget:")
    cases: list[tuple[str, list[np.ndarray]]] = [
        ("two_cluster_bidirectional_fan", two_cluster_fan()),
        ("bent_strokes_two_clusters", bent_strokes_two_clusters()),
        ("square_doubled_mixed", square_doubled_mixed()),
        ("ambiguous_four_lanes", ambiguous_four_lanes()),
    ]
    for name, strokes in cases:
        report(name, strokes)
    print("  exact search sizes (bisected via process-local budget lowering;")
    print("  production constants restored by the patches):")
    for name, strokes in cases:
        count_search_size(name, strokes)
    print("  budget-refusal path check (budgets lowered on purpose):")
    with mock.patch.object(ca, "SEARCH_NODE_BUDGET", 8):
        result = ca.assemble_contour(cases[2][1])
    print(f"    node budget 8 on square_doubled_mixed -> {result.status}/"
          f"{result.reason_code}")
    assert result.reason_code == "search_complexity"
    with mock.patch.object(ca, "MAX_MATCHINGS", 1):
        result = ca.assemble_contour(cases[2][1])
    print(f"    matching cap 1 on square_doubled_mixed -> {result.status}/"
          f"{result.reason_code} (>= 2 enumerated candidates discarded)")
    assert result.reason_code == "search_complexity"
    with mock.patch.object(ca, "MAX_MATCHINGS", 1):
        result = ca.assemble_contour(cases[3][1])
    print(f"    matching cap 1 on ambiguous_four_lanes -> {result.status}/"
          f"{result.reason_code} (enumeration finishes before the guard; the"
          "\n    cap is an abort guard, not a strict bound -> true verdict "
          "returned)")
    assert result.status == "ambiguous"


def main() -> int:
    print("B3: global assembly and ambiguity edges (M1)")
    print("=" * 72)
    e1_tolerance_boundary()
    e2_multiple_loops()
    e3_duplicated_side()
    e4_search_budget()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
