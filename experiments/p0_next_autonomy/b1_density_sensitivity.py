#!/usr/bin/env python
"""Phase B / B1 — sampling-density sensitivity of the M1 multi-stroke assembly
(task brief §四 B problem 1: raw-point ``trace_scale``).

Hypothesis under test
---------------------
``_validate_strokes`` computes the assembly scale as
``trace_scale(np.vstack(raw))`` — the RMS radius of the RAW pointer points.
Real pointer input samples per-stroke at wildly different densities (a slowly
drawn stroke yields many points, a fast flick few). The RMS radius is a mean
over *points*, not over *arc length*, so the per-stroke density distribution
moves the scale. The endpoint tolerance ``ENDPOINT_TOL_REL * scale`` moves with
it, and junction legality (and therefore the assembly verdict) could flip
between two exports of the SAME geometry.

Experiment design (single variable, paired)
-------------------------------------------
One fixed geometry: a 400x240 canvas-pixel rectangle drawn as 4 full-side
strokes (bottom, right, top, left). Every stroke end is pulled back by the
same ``pullback`` from its corner, so consecutive strokes leave one identical
corner gap ``pullback * sqrt(2)`` at each of the 4 corners. The corner turns
are 90 deg everywhere (legal), and no other endpoint pair is ever close, so
the junction graph is exactly the intended 4-cycle.

The single variable is the per-stroke POINT COUNT: identical vertices and
gaps, different density patterns (arc-length-proportional reference, equal
counts, one slow stroke x8, one slow vertical stroke x8, all x8).

Measured per variant: the raw-point scale (the pre-fix yardstick, kept as
the "before" column), the assembly scale actually used by
``assemble_contour``, and the assembly verdict at a fixed corner gap chosen
inside the raw tolerance spread. For variants that assemble, the coarse
Fourier vector is compared against the arc-proportional reference to
separate "gate sensitivity" (verdict flips) from "output sensitivity"
(retrieval vector moves).

Outcome (2026-11-22, run on this file): the raw-point tolerance spread was
5.05..5.77 px (+12.8%) and the same drawing flipped between ok and
open_gap across density patterns — hypothesis CONFIRMED, remediated in
docs/DECISIONS.md D96 by replacing the raw-point RMS with the exact
arc-length-weighted RMS (``_arc_weighted_scale``): one scale (spread
2.8e-14 px) for every density pattern, no verdict flip, tolerance ceiling
verified empirically. Rerun to reproduce the post-fix state.

Run: python experiments/p0_next_autonomy/b1_density_sensitivity.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from earthglyph.features.fourier import harmonic_amplitudes  # noqa: E402
from earthglyph.geometry.contour_assembly import (  # noqa: E402
    ENDPOINT_TOL_REL,
    assemble_contour,
)
from earthglyph.geometry.resampling import (  # noqa: E402
    arc_length_resample,
    ensure_ccw,
)
from earthglyph.geometry.sketch import trace_scale  # noqa: E402

W, H = 400.0, 240.0  # rectangle size in canvas px
# per-stroke point counts: (bottom, right, top, left); sides are 400,240,400,240
VARIANTS: dict[str, tuple[int, int, int, int]] = {
    "arc_prop": (50, 30, 50, 30),      # counts proportional to side length
    "equal_25": (25, 25, 25, 25),      # naive equal counts per stroke
    "slow_bottom": (193, 49, 25, 49),  # one slow horizontal stroke (x8 of 25)
    "slow_right": (49, 193, 49, 25),   # one slow vertical stroke
    "all_x8": (193, 193, 193, 193),    # equal counts, high (same shape as equal_25)
}
REFERENCE = "arc_prop"


def _linspace_pts(a: tuple[float, float], b: tuple[float, float],
                  count: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, count)
    return np.column_stack((a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t))


def side_strokes(pullback: float,
                 counts: tuple[int, int, int, int]) -> list[np.ndarray]:
    """4 full sides, each end pulled back by `pullback` along its own side.

    Consecutive strokes then miss each corner symmetrically, leaving a corner
    gap of pullback*sqrt(2) — like a hand drawing that misses the corner.
    """
    x0, x1 = pullback, W - pullback
    y0, y1 = pullback, H - pullback
    return [
        _linspace_pts((x0, 0.0), (x1, 0.0), counts[0]),  # bottom, left->right
        _linspace_pts((W, y0), (W, y1), counts[1]),      # right, bottom->top
        _linspace_pts((x1, H), (x0, H), counts[2]),      # top, right->left
        _linspace_pts((0.0, y1), (0.0, y0), counts[3]),  # left, top->bottom
    ]


def describe(strokes: list[np.ndarray]) -> dict[str, object]:
    result = assemble_contour(strokes)
    info: dict[str, object] = {
        "n_points": [int(s.shape[0]) for s in strokes],
        "status": result.status,
        "reason_code": result.reason_code,
        "n_valid": result.n_valid_solutions,
        "junctions": len(result.junctions),
        "max_correction": (max((j["correction"] for j in result.junctions),
                               default=None) if result.junctions else None),
    }
    if result.status == "ok" and result.contour_points is not None:
        ring = ensure_ccw(result.contour_points)
        contour = arc_length_resample(ring, 128)
        info["fourier"] = harmonic_amplitudes(contour, 16)
    return info


def main() -> int:
    print("B1: sampling-density sensitivity of raw-point trace_scale")
    print("=" * 72)
    print(f"geometry: {W:g}x{H:g} rectangle, 4 full-side strokes, "
          "identical corner gaps pullback*sqrt(2)")
    print(f"tolerance rule: gap <= {ENDPOINT_TOL_REL} * scale "
          "(assembly scale: arc-length weighted since D96; raw-RMS shown for "
          "the pre-fix comparison)")

    # --- pass 1: scale/tolerance per density pattern (pullback tiny, so the
    # scale reflects the pattern, not the pullback) --------------------------
    probe_pullback = 0.5
    print(f"\n[1] scale / tolerance per density pattern (pullback="
          f"{probe_pullback}):")
    print("    raw-RMS   = trace_scale over raw points (the OLD yardstick; "
          "count-weighted)")
    print("    assembly  = scale used by assemble_contour (arc-length "
          "weighted; density-invariant by construction)")
    raw_scales: dict[str, float] = {}
    asm_scales: dict[str, float] = {}
    for name, counts in VARIANTS.items():
        strokes = side_strokes(probe_pullback, counts)
        raw_scales[name] = trace_scale(np.vstack(strokes))
        asm_scales[name] = assemble_contour(strokes).scale
        print(f"  {name:14s} pts={counts} raw-RMS={raw_scales[name]:8.3f} "
              f"(tol {ENDPOINT_TOL_REL * raw_scales[name]:6.3f})  "
              f"assembly={asm_scales[name]:8.3f} "
              f"(tol {ENDPOINT_TOL_REL * asm_scales[name]:6.3f})")
    raw_tols = {n: ENDPOINT_TOL_REL * s for n, s in raw_scales.items()}
    tol_min, tol_max = min(raw_tols.values()), max(raw_tols.values())
    spread = tol_max - tol_min
    rel_spread = spread / raw_tols[REFERENCE]
    asm_spread = max(asm_scales.values()) - min(asm_scales.values())
    print(f"  raw tolerance spread: {tol_min:.3f}..{tol_max:.3f} px "
          f"(abs {spread:.3f}, {rel_spread:+.1%} vs {REFERENCE})")
    print(f"  assembly scale spread across patterns: {asm_spread:.3e} px")

    # --- pass 2: paired drawing at one fixed corner gap inside the raw spread -
    # The drawing is IDENTICAL across variants (same vertices, same gaps);
    # only the pointer sampling density differs. Under the OLD raw-point scale
    # this gap sat between the slow-stroke and equal-count tolerances, so
    # verdicts flipped; under the arc-weighted scale every pattern must agree.
    corner_gap = (tol_min + tol_max) / 2.0
    pullback = corner_gap / np.sqrt(2.0)
    print(f"\n[2] paired drawing, fixed corner gap {corner_gap:.3f} px "
          f"(pullback {pullback:.3f}) — middle of the RAW tolerance spread:")
    infos: dict[str, dict[str, object]] = {}
    for name, counts in VARIANTS.items():
        infos[name] = describe(side_strokes(pullback, counts))
        info = infos[name]
        print(f"  {name:14s} status={info['status']:9s} "
              f"reason={info['reason_code']} "
              f"n_valid={info['n_valid']} "
              f"max_corr={info['max_correction']}")
    statuses = {str(i["status"]) for i in infos.values()}
    if len(statuses) > 1:
        print("  => VERDICT FLIP CONFIRMED: the same drawing assembles or is "
              "rejected depending only on the per-stroke sampling density")
    else:
        print(f"  => no verdict flip across density patterns (all {statuses})")

    # --- pass 3: empirical legality ceiling of the reference -----------------
    # proves the flip mechanism is the scale->tolerance path: ok just below
    # tol, open_gap just above tol
    print(f"\n[3] empirical legality ceiling ({REFERENCE}):")
    ref_tol = ENDPOINT_TOL_REL * assemble_contour(
        side_strokes(probe_pullback, VARIANTS[REFERENCE])).scale
    for gap, label in ((ref_tol - 0.05, "tol - 0.05"),
                       (ref_tol + 0.05, "tol + 0.05")):
        pb = gap / np.sqrt(2.0)
        result = assemble_contour(side_strokes(pb, VARIANTS[REFERENCE]))
        print(f"  gap {label:11s} ({gap:6.3f} px): status={result.status:8s} "
              f"reason={result.reason_code}")

    # --- pass 4: output sensitivity (Fourier vector) for assembling variants -
    print(f"\n[4] coarse fourier max-abs deviation vs {REFERENCE} "
          "(variants that assemble):")
    ref_fourier = infos[REFERENCE].get("fourier")
    assert isinstance(ref_fourier, np.ndarray)
    for name, info in infos.items():
        fourier = info.get("fourier")
        if fourier is None:
            print(f"  {name:14s} n/a (status {info['status']})")
            continue
        deviation = float(np.max(np.abs(np.asarray(fourier) - ref_fourier)))
        print(f"  {name:14s} {deviation:.3e}")

    verdict = {
        "raw_tolerance_spread_px": float(spread),
        "raw_tolerance_rel_spread": float(rel_spread),
        "raw_tol_per_pattern": {n: float(t) for n, t in raw_tols.items()},
        "assembly_scale_per_pattern": {n: float(s) for n, s in asm_scales.items()},
        "assembly_scale_spread_px": float(asm_spread),
        "paired_corner_gap_px": float(corner_gap),
        "statuses_at_paired_gap": {n: str(i["status"]) for n, i in infos.items()},
        "verdict_flip": len(statuses) > 1,
    }
    out = Path(__file__).with_name("b1_density_sensitivity.json")
    out.write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
