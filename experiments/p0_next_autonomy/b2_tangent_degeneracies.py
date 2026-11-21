#!/usr/bin/env python
"""Phase B / B2 — endpoint tangent degeneracies of the M1 assembly
(task brief §四 B problem 2).

Degeneracy classes probed (all synthetic geometry, single variable each):
  D1 out-and-back prefix     a stroke that starts by doubling back exactly
                             over itself -> the 2%-arc tangent probe chord
                             is exactly zero. BEFORE the fix the 0/0 divide
                             produced a NaN tangent; the NaN turn silently
                             passed the 150 deg guard and json.dumps emitted
                             bare NaN (rejected by every strict JSON parser,
                             incl. the browser's JSON.parse -> broken bridge
                             response). Classification: IMPLEMENTATION BUG,
                             fixed by widening the probe share (D97).
  D2 minimal strokes         a square drawn as four 2-point strokes (the
                             smallest legal input). Expected: assembles.
  D3 near-180 deg tips       a needle triangle; the base-corner junction turn
                             is 180 deg - atan(h/base). The 150 deg limit is
                             a D93 design choice, so the verdict must flip
                             exactly at h = base*tan(30 deg) — characterized,
                             not a bug.
  D4 within-stroke density   one side carries 99% of its points in the first
                             1% of its arc (exponential spacing). The tangent
                             probes arc, not point index, so the verdict must
                             match the uniformly sampled twin. Characterized.
  D5 endpoint jitter         near-duplicate points (0.5 px, above the dedupe
                             threshold) clustered at a stroke end. Expected:
                             assembles, tangent unaffected (arc fraction).
  D6 self-closing stroke     a fully closed stroke among others is refused by
                             the closed_loop_with_extras guard. Characterized.

Run: python experiments/p0_next_autonomy/b2_tangent_degeneracies.py
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from earthglyph.geometry.contour_assembly import assemble_contour  # noqa: E402

W, H = 400.0, 240.0


def side(a: tuple[float, float], b: tuple[float, float], n: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    return np.column_stack((a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t))


def rectangle(pullback: float = 0.0) -> list[np.ndarray]:
    x0, x1 = pullback, W - pullback
    y0, y1 = pullback, H - pullback
    return [
        side((x0, 0.0), (x1, 0.0), 25),
        side((W, y0), (W, y1), 25),
        side((x1, H), (x0, H), 25),
        side((0.0, y1), (0.0, y0), 25),
    ]


def report(name: str, strokes: list[np.ndarray]) -> dict[str, object]:
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="invalid value encountered")
        result = assemble_contour(strokes)
    turns = [j["turn_deg"] for j in result.junctions]
    payload = json.dumps(result.to_dict())
    info = {
        "status": result.status,
        "reason": result.reason_code,
        "n_valid": result.n_valid_solutions,
        "junction_turns": turns,
        "all_finite": all(np.isfinite(t) for t in turns),
        "json_strict": ("NaN" not in payload) and ("Infinity" not in payload),
    }
    turns_txt = (f"turns=[{', '.join(f'{t:.1f}' for t in turns[:6])}"
                 f"{'...' if len(turns) > 6 else ''}]"
                 if turns else "turns=[]")
    print(f"  {name:34s} status={info['status']:9s} reason={str(info['reason']):24s} "
          f"{turns_txt} finite={info['all_finite']} json_strict={info['json_strict']}")
    return info


def d1_out_and_back() -> None:
    print("D1 out-and-back stroke prefix (implementation bug, fixed):")
    points = [(0.0, 0.0)]
    for _ in range(10):
        points += [(5.0, 0.0), (0.0, 0.0)]
    wiggle = np.asarray(points, dtype=float)
    bottom = np.vstack([wiggle, rectangle()[0][1:]])
    strokes = [bottom] + rectangle()[1:]
    report("wiggle_prefix_rectangle", strokes)


def d2_minimal_strokes() -> None:
    print("D2 minimal legal strokes (2 points each, characterize):")
    strokes = rectangle()
    strokes = [s[[0, -1]] for s in strokes]  # corners only
    report("square_from_4_two_point_strokes", strokes)


def d3_needle_boundary() -> None:
    print("D3 near-180 deg tips (D93 turn limit, characterize the boundary):")
    base = 200.0
    # legs run from the base corners to the apex at base/2, so the base-corner
    # turn is 180 - atan(h / (base/2)); the 150 deg limit flips at
    # h = (base/2) * tan(30 deg)
    flip_h = (base / 2.0) * np.tan(np.radians(30.0))
    print(f"  needle: base {base:.0f}, legs to apex (base/2, h); base-corner "
          f"turn = 180 - atan(h/{base / 2.0:.0f}); limit 150 deg -> flip at "
          f"h = {flip_h:.3f}")
    for h in (40.0, flip_h - 0.5, flip_h + 0.5, 80.0):
        apex = (base / 2.0, h)
        strokes = [
            side((0.0, 0.0), (base, 0.0), 25),
            side((base, 0.0), apex, 25),
            side(apex, (0.0, 0.0), 25),
        ]
        report(f"needle_h={h:7.2f}", strokes)


def d4_within_stroke_density() -> None:
    print("D4 within-stroke density (tangent probes arc, characterize):")
    uniform = rectangle()
    # 99% of the points inside the first 1% of the arc (0..4 px of 400 px)
    dense_head = side((0.0, 0.0), (4.0, 0.0), 297)
    sparse_tail = side((4.0, 0.0), (W, 0.0), 4)
    stroke = np.vstack([dense_head, sparse_tail[1:]])
    strokes = [stroke] + uniform[1:]
    report("dense_head_bottom", strokes)
    report("uniform_reference", uniform)


def d5_endpoint_jitter() -> None:
    print("D5 near-duplicate endpoint jitter (characterize):")
    strokes = rectangle()
    bottom = strokes[0]
    # three diagonal 0.5*sqrt(2) px steps (above the 1e-3*scale dedupe
    # threshold) clustered at the stroke's start
    pts = [bottom[0]]
    for k in (3, 2, 1):
        pts.append(bottom[0] + np.asarray([0.5 * k, 0.5 * k]))
    jittered = np.vstack([np.asarray(pts), bottom[1:]])
    report("jittered_start_bottom", [jittered] + strokes[1:])


def d6_self_closing_stroke() -> None:
    print("D6 self-closing stroke among others (existing guard, characterize):")
    theta = np.linspace(0.0, 2 * np.pi, 49)
    loop = np.column_stack((200.0 + 60.0 * np.cos(theta),
                            120.0 + 60.0 * np.sin(theta)))
    strokes = [loop] + rectangle()[:3]
    report("closed_loop_plus_three_sides", strokes)


def main() -> int:
    print("B2: endpoint tangent degeneracies (M1 assembly)")
    print("=" * 72)
    d1_out_and_back()
    d2_minimal_strokes()
    d3_needle_boundary()
    d4_within_stroke_density()
    d5_endpoint_jitter()
    d6_self_closing_stroke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
