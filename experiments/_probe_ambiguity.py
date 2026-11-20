"""M1 ambiguity verification probe (docs/DECISIONS.md D93).

Exhaustively enumerates every junction matching for two synthetic layouts and
validates each candidate ring with the production criteria, showing WHY a
layout is or is not ambiguous:

* three_cluster_layout: 6 strokes around a triangle of endpoint clusters ->
  exactly 1 valid assembly (the alternative pairings self-intersect);
* lane_layout: 4 nested lanes between two clusters with endpoints fanned in
  convex position -> exactly 2 valid assemblies (ambiguous). This is the
  construction frozen into tests/test_multistroke_assembly.py and
  tests/test_sketch_search_server.py.

Run:  .venv/Scripts/python.exe experiments/_probe_ambiguity.py
Synthetic geometry only; no real geographic data is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import earthglyph.geometry.contour_assembly as ca  # noqa: E402


def poly(pts, n=12):
    out = []
    for a, b in zip(pts[:-1], pts[1:], strict=True):
        for i in range(n):
            t = i / n
            out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    out.append(list(pts[-1]))
    return out


def three_cluster_layout(spread=1.0):
    s = spread
    A = poly([(160, 260 + 2 * s), (160, 380), (300, 420), (420 - 2 * s, 380 + 2 * s)])
    B = poly([(160 + 2 * s, 260), (300, 260), (420 - 1 * s, 380 - 2 * s)])
    C = poly([(160, 260 - 2 * s), (160, 140), (300, 100), (420 - 2 * s, 140 + 2 * s)])
    D = poly([(160 - 2 * s, 260), (100, 260), (100, 60), (300, 60), (420 - 2 * s, 140 - 2 * s)])
    E = poly([(420 + 1 * s, 380 - 1 * s), (380, 260), (420 + 1 * s, 140 + 2 * s)])
    F = poly([(420 + 2 * s, 380 + 1 * s), (460, 260), (420 + 2 * s, 140 - 2 * s)])
    return [np.asarray(p, dtype=float) for p in (A, B, C, D, E, F)]


def enumerate_matchings(strokes):
    _raw, deduped, scale = ca._validate_strokes(strokes)
    n = len(deduped)
    tol = ca.ENDPOINT_TOL_REL * scale
    ends = [[arr[0], arr[-1]] for arr in deduped]
    tangents = [[ca._endpoint_tangent(arr, e) for e in (0, 1)] for arr in deduped]
    table = {}
    for i in range(n):
        for ei in (0, 1):
            for j in range(n):
                if j == i:
                    continue
                for ej in (0, 1):
                    gap = float(np.linalg.norm(ends[i][ei] - ends[j][ej]))
                    if gap > tol:
                        continue
                    turn = ca._turn_deg(-tangents[i][ei], tangents[j][ej])
                    if turn > ca.MAX_JUNCTION_TURN_DEG:
                        continue
                    table[(i, ei, j, ej)] = ca._junction_record(
                        i, ei, j, ej, ends, gap, turn, scale)
    print(f"scale={scale:.2f} tol={tol:.2f} legal_junctions={len(table)}")
    for key, rec in sorted(table.items()):
        print(f"  {key} gap={rec['gap']:.2f} turn={rec['turn_deg']:.1f}")

    matchings = {}

    def dfs(current, exit_end, used, order, junctions):
        if len(order) == n:
            closing = table.get((current, exit_end, 0, 0))
            if closing is not None:
                cycle = junctions + [closing]
                pairs = []
                for link in cycle:
                    a = (link["from_stroke"], link["from_endpoint"])
                    b = (link["to_stroke"], link["to_endpoint"])
                    pairs.append((a, b) if a <= b else (b, a))
                key = frozenset(pairs)
                matchings.setdefault(key, (list(order), cycle))
            return
        for j in range(n):
            if used & (1 << j):
                continue
            for ej in (0, 1):
                link = table.get((current, exit_end, j, ej))
                if link is None:
                    continue
                order.append((j, ej == 1))
                junctions.append(link)
                dfs(j, 1 - ej, used | (1 << j), order, junctions)
                order.pop()
                junctions.pop()

    dfs(0, 1, 1, [(0, False)], [])
    print(f"distinct matchings: {len(matchings)}")
    for key in sorted(matchings, key=lambda k: sorted(k)):
        order, junctions = matchings[key]
        ring = ca._build_ring(order, junctions, deduped, scale)
        ok, code, reason = ca._validate_candidate(ring)
        print(f"  order={order} -> {'VALID' if ok else code}")
        if not ok:
            print(f"    {reason}")
    return matchings


def lane_layout(spread=2.0, bulges=(128.0, 40.0, -40.0, -128.0), n=33):
    """4 lanes P->Q; endpoints fanned in convex position at each cluster."""
    px, py, qx, qy = 150.0, 260.0, 430.0, 260.0
    # angular order A,B,C,D counter-clockwise at BOTH clusters
    off_p = [(0, spread), (spread, 0), (0, -spread), (-spread, 0)]
    off_q = [(0, spread), (-spread, 0), (0, -spread), (spread, 0)]
    strokes = []
    for k, bulge in enumerate(bulges):
        p0 = (px + off_p[k][0], py + off_p[k][1])
        p1 = (qx + off_q[k][0], qy + off_q[k][1])
        pts = []
        for i in range(n):
            t = i / (n - 1)
            x = p0[0] + (p1[0] - p0[0]) * t
            y = p0[1] + (p1[1] - p0[1]) * t + 4 * bulge * t * (1 - t)
            pts.append([x, y])
        strokes.append(np.asarray(pts, dtype=float))
    return strokes


if __name__ == "__main__":
    print("=== three_cluster_layout (expected: exactly 1 valid) ===")
    enumerate_matchings(three_cluster_layout())
    result = ca.assemble_contour(three_cluster_layout())
    print(f"assemble: {result.status} {result.reason_code} "
          f"n_valid={result.n_valid_solutions}\n")
    print("=== lane_layout (expected: ambiguous, 2 valid) ===")
    enumerate_matchings(lane_layout())
    result = ca.assemble_contour(lane_layout())
    print(f"assemble: {result.status} {result.reason_code} "
          f"n_valid={result.n_valid_solutions}")