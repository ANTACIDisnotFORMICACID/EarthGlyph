"""Re-derive every number quoted in an E1/E2/E3 POLICY string.

Checkpoint B's modules state their own limitations in prose, and prose with digits in
it is only worth what the digits cost: each claim below came from a throwaway script
during development, which a reader cannot re-run.  This file is that script, kept.  It
checks the measured values against the numbers the POLICY strings quote, inside a
stated tolerance, so a parameter change that breaks a sentence also breaks this run
instead of leaving the sentence behind.

Nothing here evaluates retrieval.  There is no hit rate, no ranking against human
judgement, no Top-K figure — that is Checkpoint C.  These are self-measurements of the
arms: what their invariances are worth, what the rejected formulations would have
cost, and where a claim stops being true.

Run (about four minutes on this machine; the E3 census is the slow part — 780 pairs, each
scored in both directions through a 24-step rotation search, and its timings are printed
because the POLICY quotes them.  ``--pairs`` shrinks the census for a quick look, but the
numbers the POLICY quotes are the ones from the default 40 members, so a ``--pairs`` run is
a smoke test, not a verification):

    python experiments/p0_perceptual_probe/verify_policy_claims.py
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np

from earthglyph.data.perceptual_gallery import (
    blob_matching_solidity,
    circle,
    crescent,
    heart,
    irregular,
    regular_polygon,
    standardize,
    star,
)
from earthglyph.features.geometry_stats import shape_metrics
from earthglyph.retrieval import perceptual_features as e1
from earthglyph.retrieval import perceptual_methods
from earthglyph.retrieval import salient_structure as e2
from earthglyph.retrieval import shape_context_baseline as e3

ROOT = Path(__file__).resolve().parents[2]
GALLERY = ROOT / "experiments/p0_perceptual_probe/gallery.jsonl"


def rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = math.radians(float(angle_deg))
    matrix = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    return np.asarray(points @ matrix.T, dtype=float)


def gallery_rows(count: int) -> list[np.ndarray]:
    lines = [line for line in GALLERY.read_text(encoding="utf-8").splitlines() if line]
    if len(lines) < count:
        raise SystemExit(f"{GALLERY} has {len(lines)} rows, this script needs {count}")
    return [np.asarray(json.loads(line)["contour"], dtype=float) for line in lines[:count]]


def report(claim: str, got: float, quoted: float, tolerance: float) -> None:
    """Print one measured value against the number a POLICY string quotes."""
    drift = abs(got - quoted)
    if drift > tolerance:
        raise SystemExit(f"{claim}: measured {got:.6g}, POLICY quotes {quoted:.6g}")
    print(f"  [ok] {claim}: measured {got:.6g}, POLICY quotes {quoted:.6g}")


def distance_with(
    cost: Callable[[e2.SalientEvent, e2.SalientEvent, float], float],
    left: e2.SalientSignature,
    right: e2.SalientSignature,
) -> float:
    """Score two signatures with a replacement cost function (a rejected variant).

    mypy is right that a ``Callable`` is not the function object ``_match_cost``
    declares, so the patch carries one ignore; the DP looks the cost up through the
    module globals, which is what makes the swap observable at all.
    """
    shipped = e2._match_cost
    try:
        e2._match_cost = cost  # type: ignore[assignment]
        return float(e2.align_sequences(left, right)["distance"])
    finally:
        e2._match_cost = shipped


def gain_with(value: float, a: np.ndarray, b: np.ndarray) -> float:
    """Score a real pair at a requested PEAK_GAIN (a rejected variant)."""
    shipped = e2.PEAK_GAIN
    try:
        e2.PEAK_GAIN = value
        return float(e2.distance(a, b)[0])
    finally:
        e2.PEAK_GAIN = shipped


# --- E3: the asymmetry census and the cost of a searched rotation ----------------


def check_e3(pairs_from: int) -> None:
    print(f"E3 shape context — asymmetry over the first {pairs_from} gallery members")
    rows = gallery_rows(pairs_from)
    gaps = [
        abs(e3.distance(rows[i], rows[j])[0] - e3.distance(rows[j], rows[i])[0])
        for i in range(pairs_from)
        for j in range(i + 1, pairs_from)
    ]
    vector = np.array(gaps)
    print(f"  pairs={vector.size} exactly-equal={int((vector == 0.0).sum())} "
          f"nonzero={int((vector > 0.0).sum())}")
    report("median |d(A,B) - d(B,A)|", float(np.median(vector)), 0.0061, 0.0006)
    report("mean   |d(A,B) - d(B,A)|", float(vector.mean()), 0.0092, 0.0006)
    report("max    |d(A,B) - d(B,A)|", float(vector.max()), 0.0610, 0.006)
    library = np.stack(gallery_rows(179))
    for mode in ("sample", "canonical"):
        start = time.perf_counter()
        e3.score_library(rows[0], library, orientation=mode)
        print(f"  orientation={mode:<10} {time.perf_counter() - start:6.2f} s per query "
              f"over {library.shape[0]} candidates")


# --- E2: what each cost term is worth, including the ones that were rejected ------


def scaled_by_persistence(sig: e2.SalientSignature) -> e2.SalientSignature:
    """The rejected variant: charge every event again by how many windows saw it."""
    return replace(
        sig,
        events=tuple(
            replace(event, magnitude=event.magnitude * event.persistence)
            for event in sig.events
        ),
    )


def budget_normalised_drift(
    left: e2.SalientEvent, right: e2.SalientEvent, drift: float
) -> float:
    """The rejected variant: divide the drift excess by the deformation budget."""
    turning = abs(left.signed_magnitude - right.signed_magnitude)
    excess = max(0.0, drift - e2.DEFORMATION_BUDGET) / e2.DEFORMATION_BUDGET
    shift = min(abs(left.signed_magnitude), abs(right.signed_magnitude))
    return turning + shift * excess + e2.PEAK_GAIN * abs(left.peak - right.peak)


def check_e2() -> None:
    print("E2 salient structure — corner charges, noise, pointedness, rejected variants")
    sharp = standardize(star())
    worn = standardize(star(tip_round=0.18, valley_round=0.12))
    six = standardize(star(points_count=6))
    disk = standardize(circle())
    pentagon = standardize(regular_polygon(5))
    lobed_ring, _achieved, _info = blob_matching_solidity(
        float(shape_metrics(sharp).solidity), count=5, sharpness=2.0
    )
    lobed = standardize(lobed_ring)

    signature = e2.signature(sharp)
    charges = sorted(round(event.magnitude, 3) for event in signature.events)
    print(f"  star(5) events={len(signature.events)} convex={signature.n_convex} "
          f"concave={signature.n_concave}")
    report("smallest corner charge (rad)", charges[0], 1.169, 0.001)
    report("largest corner charge (rad)", charges[-1], 2.426, 0.001)

    rng = np.random.default_rng(0)
    mild = standardize(disk + rng.normal(0.0, 0.005, disk.shape))
    tiny = standardize(disk + rng.normal(0.0, 0.002, disk.shape))
    print(f"  events raised: 0.2% radial noise -> {len(e2.signature(tiny).events)}, "
          f"0.5% -> {len(e2.signature(mild).events)}")
    report("d(disk, 0.5%-noise disk)", e2.distance(disk, mild)[0], 0.0805, 0.0005)
    report("d(disk, star)", e2.distance(disk, sharp)[0], 2.8610, 0.001)
    share = e2.distance(disk, mild)[0] / e2.distance(disk, sharp)[0]
    report("noise as a share of structure", share, 0.0281, 0.0005)

    for value, quoted in ((0.0, 0.0), (0.25, 0.2348), (1.0, 0.9391)):
        report(f"d(star, worn star) at PEAK_GAIN={value}", gain_with(value, sharp, worn),
               quoted, 0.0005)
    report("d(star, star6) at PEAK_GAIN=1.0", gain_with(1.0, sharp, six), 1.3909, 0.001)
    print(f"  shipped PEAK_GAIN={e2.PEAK_GAIN}: d(star, worn)={e2.distance(sharp, worn)[0]:.4f} "
          f"d(star, star6)={e2.distance(sharp, six)[0]:.4f}")

    print("  rejected variant, persistence multiplied into every charge:")
    for label, a, b in (
        ("star vs five-lobed", sharp, lobed),
        ("star vs worn star", sharp, worn),
        ("pentagon vs disk", pentagon, disk),
        ("disk vs 0.5%-noise", disk, mild),
    ):
        left, right = e2.signature(a), e2.signature(b)
        shipped = float(e2.align_sequences(left, right)["distance"])
        variant = e2.align_sequences(scaled_by_persistence(left), scaled_by_persistence(right))
        weighted = float(variant["distance"])
        print(f"    {label:<20} shipped={shipped:.4f} persistence-weighted={weighted:.4f}")
    report(
        "persistence-weighted d(star, five-lobed)",
        e2.align_sequences(
            scaled_by_persistence(e2.signature(sharp)), scaled_by_persistence(e2.signature(lobed))
        )["distance"],
        5.3802,
        0.002,
    )

    six_drift = distance_with(budget_normalised_drift, e2.signature(sharp), e2.signature(six))
    disk_drift = distance_with(budget_normalised_drift, e2.signature(sharp), e2.signature(disk))
    print(
        f"  rejected variant, drift excess / budget: d(star, star6)={six_drift:.4f} against "
        f"d(star, disk)={disk_drift:.4f} — order inverted. The disk raises no event, so its "
        "score is all gaps and this cost function cannot move it; the shipped value is "
        f"{e2.distance(sharp, disk)[0]:.4f} either way."
    )
    report("variant d(star, star6)", six_drift, 3.9716, 0.002)

    chiral = standardize(irregular(3))
    reflected = e2._mirror(chiral)
    print(f"  mirror switch: off={e2.distance(chiral, reflected, allow_mirror=False)[0]:.4f} "
          f"on={e2.distance(chiral, reflected, allow_mirror=True)[0]:.4f} "
          "(a rotation in place of a reflection left these equal)")

    shipped_merge = e2._merge_duplicates
    try:
        e2._merge_duplicates = lambda events, **_kw: list(events)
        raw = len(e2.signature(worn).events)
    finally:
        e2._merge_duplicates = shipped_merge
    print(f"  duplicate collapse on the worn star: {raw} detections -> "
          f"{len(e2.signature(worn).events)} events")

    turned = rotate(sharp, 37.0)
    print(f"  rotation residual: d(star, star rotated 37 deg)={e2.distance(sharp, turned)[0]:.4f}")


# --- E1: the orientation frame, and what it is worth ------------------------------


def check_e1() -> None:
    print("E1 descriptor battery — orientation frame census, rotation residual, self-fit")
    shapes = {
        "circle": standardize(circle()),
        "square": standardize(regular_polygon(4)),
        "triangle": standardize(regular_polygon(3)),
        "pentagon": standardize(regular_polygon(5)),
        "hexagon": standardize(regular_polygon(6)),
        "star5": standardize(star()),
        "star6": standardize(star(points_count=6)),
        "crescent": standardize(crescent()),
        "heart": standardize(heart()),
        "chiral": standardize(irregular(3)),
    }
    quoted = {
        "circle": 1.00000,
        "square": 1.00000,
        "triangle": 0.99904,
        "pentagon": 0.99939,
        "hexagon": 0.99922,
        "star5": 0.99869,
        "star6": 0.99582,
        "crescent": 0.31132,
        "heart": 0.59892,
        "chiral": 0.76984,
    }
    statuses = e1.orientation_statuses(np.stack(list(shapes.values())))
    for name, row in zip(shapes, statuses, strict=True):
        ratio = float(row["moment_axis_ratio"])
        expected = "isotropic-fallback" if ratio >= e1.ORIENTATION_ISOTROPY_MAX else "canonical"
        status = str(row["orientation_status"])
        if status != expected or abs(ratio - quoted[name]) > 0.0002:
            raise SystemExit(f"{name}: {status} ratio {ratio:.5f} contradicts the POLICY text")
        print(f"  [ok] {name:<9} {status:<18} lambda2/lambda1={ratio:.5f}")

    query = shapes["star5"]
    library = np.stack(
        [rotate(query, 37.0), shapes["circle"], shapes["crescent"], shapes["pentagon"],
         shapes["chiral"]]
    )
    canonical, _ = e1.score_library(query, library)
    raw, _ = e1.score_library(query, library, orientation="raw")
    report("E1 d(star, rotated star) canonical", float(canonical[0]), 4.8047, 0.002)
    report("E1 d(star, rotated star) raw", float(raw[0]), 4.6945, 0.002)
    for arm in ("E0", "E2", "E3"):
        scores, _meta = perceptual_methods.score_library(arm, query, list(library))
        print(f"  {arm} d(star, rotated star) = {float(scores[0]):.4f} on the same five rows")

    chiral_query = shapes["chiral"]
    control_rows = np.stack(
        [rotate(chiral_query, 37.0), shapes["circle"], shapes["pentagon"], query,
         shapes["crescent"]]
    )
    control_canonical, _ = e1.score_library(chiral_query, control_rows)
    control_raw, _ = e1.score_library(chiral_query, control_rows, orientation="raw")
    report("E1 d(chiral, rotated chiral) canonical", float(control_canonical[0]), 0.0, 1e-9)
    report("E1 d(chiral, rotated chiral) raw", float(control_raw[0]), 1.3434, 0.002)

    three, _ = e1.score_library(query, np.stack([library[0], shapes["circle"],
                                                 shapes["crescent"]]))
    print("  self-fitted scale reorders with library size:")
    for label, vector in (("3 rows", three), ("5 rows", canonical)):
        order = perceptual_methods.rank_order(vector)
        print(f"    {label}: rotated copy ranks {int(list(order).index(0)) + 1} "
              f"{[round(float(v), 4) for v in vector]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-derive the POLICY numbers")
    parser.add_argument("--pairs", type=int, default=40, help="gallery rows for the E3 census")
    args = parser.parse_args()
    check_e3(args.pairs)
    check_e2()
    check_e1()
    print("every quoted number reproduced")


if __name__ == "__main__":
    main()
