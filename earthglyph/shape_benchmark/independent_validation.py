"""The CP8-B independent validation design (P0.6 Checkpoint 8), as rules rather than prose.

Checkpoint 7 burned twelve queries and five angles per arm. Any claim that the rotation wrappers
"generalise" therefore rests on the same shapes they were designed and tuned against. Task D of
Checkpoint 8 asks the opposite: a query set that never entered a scored cell, frozen and committed
**before** retrieval runs, so that swapping a failing query or redrawing an angle afterwards would
be visible as a diff rather than invisible inside a headline.

This module holds the frozen rules; :mod:`scripts.design_checkpoint8_validation` turns them into
`checkpoint8/independent_validation_design.json`. Nothing here scores a shape, ranks a candidate or
reads a human rating -- and that is the point: the design cannot depend on a result because it has
no access to one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

CONTRACT_POINTS = 128
"""The shared centred / unit-RMS / CCW contour contract every arm is fed."""

CP8_SEED = 20261110
"""Fixed for this study. Changing it re-draws every angle, which is a new experiment."""

CP8_SCHEMA = "earthglyph.shape_perception_benchmark/independent_validation/1"

#: One instance per class for a multi-class family, four instances for the single-class family.
#: Four per family x six families = 24 queries, above the brief's floor of 20 with a margin that
#: does not need a replacement draw if one shape turns out to be unscorable.
CP8_QUERIES_PER_FAMILY = 4

#: The D6 slot structure, kept identical on purpose: 1 angle the 24-step grid can hit, 2 grid
#: midpoints it cannot, 2 angles at least 2 degrees away from both. A different slot mix would make
#: the two studies uncomparable, which is the opposite of what an independent check is for.
CP8_ANGLE_SLOTS: tuple[str, ...] = ("on_grid", "mid_grid", "mid_grid", "random", "random")
CP8_CONTROL_SLOT = "control"
CP8_ROTATION_STEPS = 24
CP8_OFF_GRID_MARGIN_DEG = 2.0

#: Six arms, of which the first five are the brief's minimum set. Math and parameters are frozen by
#: their own modules; this list is a *selection*, not a modification.
CP8_ARMS: tuple[str, ...] = ("E0", "E1", "E3/sample", "S0K1/R3", "S0K2/R3", "S0K2/R2")
CP8_PRIMARY_CONDITION = "T2"
CP8_CHECK_CONDITION = "T0"
CP8_TOP_K = 10
CP8_N_BOOT = 2000

#: Every stem that has already been a query in a scored cell of this benchmark. Re-using one would
#: make "independent" mean "the shape we already tuned on". The two lists live in the runner; a test
#: pins this copy against them so a silent divergence becomes a red build.
BURNED_QUERY_STEMS: tuple[str, ...] = (
    "ray-1",
    "octopus-1",
    "Heart-1",
    "horseshoe-1",
    "fork-1",
    "tree-1",
    "bat-1",
    "sea_snake-1",
    "Comma-1",
    "HCircle-1",
    "device2-1",
    "brick-01",
    "apple-1",
    "butterfly-1",
)

FAMILY_KEYS: tuple[str, ...] = (
    "pointed",
    "heart",
    "curved",
    "forked",
    "near_circle",
    "irregular_natural",
)


@dataclass(frozen=True)
class FamilySpec:
    """One requested shape family, the classes chosen to carry it, and what must hold of them."""

    key: str
    label_zh: str
    classes: tuple[str, ...]
    why_these_classes: str
    measurable_expectation: str
    gap: str

    @property
    def per_class(self) -> bool:
        """Whether one instance per class is taken rather than several from one class."""
        return len(self.classes) >= CP8_QUERIES_PER_FAMILY


FAMILIES: tuple[FamilySpec, ...] = (
    FamilySpec(
        key="pointed",
        label_zh="尖角",
        classes=("crown", "key", "pencil", "hammer"),
        why_these_classes=(
            "each silhouette carries deep, narrow corners "
                "(crown points, key teeth, a pencil tip, a "
            "hammer head) rather than a smooth boundary"
        ),
        measurable_expectation=(
            "solidity well under 1 and turning energy concentrated in few vertices"
        ),
        gap=(
            "MPEG-7 has no star or spiky class; four man-made outlines are the closest available, "
            "so 'pointed' here means cornered, not needle-thin"
        ),
    ),
    FamilySpec(
        key="heart",
        label_zh="心形",
        classes=("Heart",),
        why_these_classes="the dataset's one heart class, two lobes over a single apical notch",
        measurable_expectation="exactly one deep notch and a high area-to-hull ratio",
        gap=(
            "one class only, so these four queries are a "
                "within-class sample (four different hearts), "
            "not a cross-family test of heart-like shapes; Heart-1 itself is burned and excluded"
        ),
    ),
    FamilySpec(
        key="curved",
        label_zh="弯曲",
        classes=("Comma", "horseshoe", "Bone", "spoon"),
        why_these_classes=(
            "C- and U-shaped outlines with a dominant curvature axis (comma, horseshoe, a bone's "
            "two bowed ends, a spoon's bowl and handle)"
        ),
        measurable_expectation="one large concavity and an elongated principal axis",
        gap="horseshoe-1 and Comma-1 are burned instances; the class pools supply the rest",
    ),
    FamilySpec(
        key="forked",
        label_zh="分叉",
        classes=("fork", "tree", "bird", "fly"),
        why_these_classes=(
            "multi-branch outlines: a fork's tines, a tree's "
                "crown, spread wings -- the case where a "
            "single principal axis is not a meaningful pose cue"
        ),
        measurable_expectation="several separate hull gaps, i.e. more than one deep notch",
        gap=(
            "fork-1 and tree-1 are burned; bird and fly are winged silhouettes rather than binary "
            "forks, included because the dataset has few true two-prong outlines left"
        ),
    ),
    FamilySpec(
        key="near_circle",
        label_zh="接近圆形",
        classes=("HCircle", "watch", "device2", "apple"),
        why_these_classes=(
            "isotropic outlines whose rotation is nearly unobservable, which is exactly where a "
            "rotation-search wrapper should be provably worthless rather than supposedly harmless"
        ),
        measurable_expectation="circularity above 0.7 and elongation near 1.0",
        gap=(
            "HCircle-1 and device2-1 are burned; HCircle is hand-traced and deliberately wobbly, "
            "and an apple's stalk dent keeps it from being a disc -- no class is a true circle"
        ),
    ),
    FamilySpec(
        key="irregular_natural",
        label_zh="不规则自然剪影",
        classes=("camel", "elephant", "lizzard", "turtle"),
        why_these_classes=(
            "asymmetric natural outlines with protruding parts (humps, a trunk, limbs, a head) -- "
            "closest to a real-world silhouette that nothing was designed around"
        ),
        measurable_expectation="no symmetry axis, elongation away from 1.0, several hull gaps",
        gap=(
            "MPEG-7 CE-Shape-1 silhouettes are single-part, simplified outlines; nothing here has "
            "occlusion, attached parts at arbitrary angles, or the ragged boundary of a real "
            "leaf/rock, so 'irregular natural' is bounded by the dataset's own stylisation"
        ),
    ),
)

#: Each ``measurable_expectation`` restated as bounds on the descriptors that ``queries`` already
#: carries, so a family whose sampled instances do not deliver it shows up as arithmetic rather than
#: as prose nobody checked. Evaluated before any retrieval ran; nothing here re-selects.
FAMILY_CRITERIA: dict[str, tuple[tuple[str, str, float, str], ...]] = {
    "pointed": (
        ("solidity", "<=", 0.85, "deep corners remove area from the hull"),
        ("deep_gap_runs", ">=", 1.0, "at least one excursion away from the hull"),
    ),
    "heart": (
        ("deep_gap_runs", "==", 1.0, "exactly one apical notch"),
        ("solidity", ">=", 0.85, "two lobes fill most of the hull"),
    ),
    "curved": (
        ("deep_gap_runs", ">=", 1.0, "the concavity of the C or U"),
        ("elongation", "<=", 0.65, "a dominant principal axis"),
    ),
    "forked": (("deep_gap_runs", ">=", 2.0, "more than one branch gap"),),
    "near_circle": (
        ("circularity", ">=", 0.70, "a near-disc boundary"),
        ("elongation", ">=", 0.80, "isotropic, so rotation is nearly unobservable"),
    ),
    "irregular_natural": (
        ("deep_gap_runs", ">=", 2.0, "protruding parts make several separate gaps"),
        ("elongation", "<=", 0.80, "not a disc"),
    ),
}

#: What a shortfall in that family costs the read-out. Written as a bound on the *claim*, not as a
#: reason to redraw: the brief forbids replacing a failing query after seeing a result.
FAMILY_COVERAGE_CONSEQUENCE: dict[str, str] = {
    "pointed": (
        "a cornered-shape conclusion rests on the queries "
            "that pass; a smooth one in the pool is an "
        "ellipse-with-a-lid case, not a pointed case"
    ),
    "heart": "the heart family is the only one whose label is "
        "unambiguous, so it carries the shape-specific claim",
    "curved": "a query that fails the axis bound is a ring-like outline; it still tests rotation "
    "observability, just not the 'bent' story",
    "forked": "branch-heavy outlines are where a single "
        "principal axis stops meaning anything; fewer "
    "of them weakens the strongest argument against pose search",
    "near_circle": "the isotropic case is the one place a rotation "
        "wrapper should be provably worthless; "
    "if these queries are not isotropic the study "
        "cannot make that claim, only measure the roundish "
    "ones that survive",
    "irregular_natural": "these are the closest thing to a real "
        "silhouette, so a shortfall here is a "
    "shortfall in the most transfer-relevant family",
}

#: What this study is NOT, written before running rather than discovered afterwards.
INDEPENDENCE_LIMITS: tuple[str, ...] = (
    "Independent means **disjoint query instances from the scored cells of this benchmark**, not a "
    "different data distribution: gallery and queries "
        "are both MPEG-7 CE-Shape-1, so a wrapper that "
    "happens to fit this dataset's drawing style still fits.",
    "The 1 400-shape pool was already the *candidate* "
        "set in D2/D6, so the queries are new while the "
    "gallery is not. Nothing in this round can measure "
        "transfer to a pool the arms have never seen.",
    "No new human ratings are collected. The human-perception axis is re-used from H1 with its own "
    "provenance, so this study does NOT independently "
        "validate perceptual similarity and must not be "
    "reported as if it did.",
    "The categories are MPEG-7 object labels. They are a relevance proxy for class retrieval only, "
    "never a similarity judgement.",
    "Six arms are scored, not sixteen: the cheapest and "
        "the strongest wrapper families are represented "
    "(R2/R3 on both networks) plus the three EarthGlyph references, while R0/R1_36/R1_72 and "
    "E3/canonical are deliberately absent. A claim about *those* arms cannot come from this file.",
    "The family labels state a design intent; "
        "``coverage_audit`` counts, from the descriptors already "
    "in ``queries``, how many frozen draws actually satisfy that intent's geometric criteria. A "
    "family below its full count is under-covered, and its headline may only be read over the "
    "queries that pass -- loosening a criterion after seeing a shortfall would be result-shopping.",
)


def contour_descriptors(points: np.ndarray) -> dict[str, float]:
    """Cheap geometric evidence for a family assignment -- never a selector.

    These numbers are recorded so a reader can check that a claimed family is not wishful thinking.
    They are deliberately NOT used to pick queries: selecting on a descriptor would make the family
    labels the result of an optimisation instead of a stated design choice.
    """
    ring = np.asarray(points, dtype=float)
    if ring.ndim != 2 or ring.shape[1] != 2 or len(ring) < 3:
        raise ValueError(f"need an (n, 2) closed ring, got {ring.shape}")
    if len(ring) != CONTRACT_POINTS:
        raise ValueError(f"the contract is {CONTRACT_POINTS} points, got {len(ring)}")
    x, y = ring[:, 0], ring[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - float(np.dot(y, np.roll(x, -1)))))
    perimeter = float(np.sum(np.hypot(*np.diff(np.vstack([ring, ring[:1]]), axis=0).T)))
    centered = ring - ring.mean(axis=0)
    covariance = np.cov(centered.T)
    eigenvalues = np.sort(np.linalg.eigvalsh(covariance))[::-1]
    rms = float(np.sqrt(np.mean(np.sum(centered**2, axis=1))))
    hull = _convex_hull(ring)
    hx, hy = hull[:, 0], hull[:, 1]
    hull_area = 0.5 * abs(
        float(np.dot(hx, np.roll(hy, -1))) - float(np.dot(hy, np.roll(hx, -1)))
    )
    gaps = _hull_gap_runs(ring, hull, rms)
    ratio = float(eigenvalues[1] / eigenvalues[0]) if eigenvalues[0] > 0 else 1.0
    return {
        "area": area,
        "circularity": 4.0 * np.pi * area / perimeter**2 if perimeter else 0.0,
        "solidity": area / hull_area if hull_area else 0.0,
        "elongation": float(np.sqrt(ratio)),
        "deep_gap_runs": float(gaps["runs"]),
        "deepest_gap": gaps["deepest"],
    }


def _convex_hull(ring: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain, so the design needs no dependency beyond numpy."""
    points = np.asarray(ring, dtype=float)
    order = np.lexsort((points[:, 1], points[:, 0]))
    sorted_points = [(float(p[0]), float(p[1])) for p in points[order]]

    def half(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(sorted_points), half(sorted_points[::-1])
    return np.array(lower[:-1] + upper[:-1], dtype=float)


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _hull_gap_runs(ring: np.ndarray, hull: np.ndarray, rms: float) -> dict[str, float]:
    """Count separate excursions of the outline away from its convex hull.

    One run is a single deep concavity (a heart's notch), several are branches (a fork, a tree).
    The threshold is relative to the RMS radius, so the count does not depend on the drawing scale.
    """
    points = np.asarray(ring, dtype=float)
    corners = np.asarray(hull, dtype=float)
    if len(corners) < 3:
        return {"runs": 0.0, "deepest": 0.0}
    starts = corners
    ends = np.roll(corners, -1, axis=0)
    vectors = ends - starts
    lengths_sq = np.einsum("ij,ij->i", vectors, vectors)
    distance = np.full(len(points), np.inf)
    for index in range(len(starts)):
        if lengths_sq[index] == 0.0:
            nearest = np.hypot(*(points - starts[index]).T)
        else:
            along = np.clip(
                ((points - starts[index]) @ vectors[index]) / lengths_sq[index], 0.0, 1.0
            )
            projected = starts[index] + along[:, None] * vectors[index]
            nearest = np.hypot(*(points - projected).T)
        distance = np.minimum(distance, nearest)
    outside = distance > 0.10 * rms
    if not outside.any() or outside.all():
        runs = 0.0
    else:
        runs = float(int(np.sum(outside != np.roll(outside, 1))) // 2)
    return {"runs": runs, "deepest": float(distance.max()) / rms}


def audit_coverage(queries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check the frozen families against the descriptors they were chosen to carry.

    Pure derivation: it reads only what :func:`select_queries` already stored, so it cannot move a
    query, redraw an angle or depend on a retrieval result. Its whole job is to say out loud which
    claimed family the sample did not actually deliver.
    """
    per_query: list[dict[str, Any]] = []
    for query in queries:
        descriptors = query["descriptors"]
        violations: list[dict[str, Any]] = []
        for metric, op, bound, why in FAMILY_CRITERIA[query["family"]]:
            value = float(descriptors[metric])
            if op == "<=":
                ok = value <= bound + 1e-12
            elif op == ">=":
                ok = value >= bound - 1e-12
            else:
                ok = abs(value - bound) < 1e-12
            if not ok:
                violations.append(
                    {"metric": metric, "op": op, "bound": bound, "value": value, "expected": why}
                )
        per_query.append(
            {
                "stem": query["stem"],
                "family": query["family"],
                "meets_expectation": not violations,
                "violations": violations,
            }
        )
    per_family: dict[str, Any] = {}
    for family in FAMILIES:
        rows = [row for row in per_query if row["family"] == family.key]
        per_family[family.key] = {
            "n_queries": len(rows),
            "n_meeting": sum(1 for row in rows if row["meets_expectation"]),
            "failing_stems": [row["stem"] for row in rows if not row["meets_expectation"]],
            "criteria": [
                {"metric": metric, "op": op, "bound": bound, "why": why}
                for metric, op, bound, why in FAMILY_CRITERIA[family.key]
            ],
            "if_short_falls_then": FAMILY_COVERAGE_CONSEQUENCE[family.key],
        }
    return {
        "derived_from": "the descriptors in queries[]; no query, class or angle was changed by it",
        "method": (
            "each family's measurable_expectation restated as bounds on circularity, solidity, "
            "elongation and hull-gap runs, evaluated per query and counted per family"
        ),
        "per_query": per_query,
        "per_family": per_family,
        "n_queries": len(per_query),
        "n_queries_meeting": sum(1 for row in per_query if row["meets_expectation"]),
        "honest_finding": (
            "a family whose n_meeting is below n_queries is under-covered: the label describes the "
            "design intent, the count states what the "
                "frozen draw actually supplies, and a headline "
            "for that family may only be read over the queries that pass."
        ),
    }


def angle_grid(step_deg: float = 360.0 / CP8_ROTATION_STEPS) -> dict[str, Any]:
    """The three slot kinds, stated as arithmetic rather than as adjectives."""
    return {
        "step_deg": step_deg,
        "on_grid": "a non-zero multiple of the step, i.e. a pose the search grid contains exactly",
        "mid_grid": "step/2 offset, i.e. the worst case a k-step grid can miss by half a step",
        "random": (
            f"uniform on [0, 360), rejected unless at least "
                f"{CP8_OFF_GRID_MARGIN_DEG} deg from both "
            "the grid and its midpoints, and never 0 -- an 'arbitrary' angle one degree off a grid "
            "line would be a grid angle in disguise"
        ),
    }


def draw_angles(rng: np.random.Generator, n_queries: int) -> list[dict[str, Any]]:
    """The stress angles for ``n_queries`` queries, in query order, from one rng stream."""
    step = 360.0 / CP8_ROTATION_STEPS
    rows: list[dict[str, Any]] = []
    for index in range(n_queries):
        for slot in CP8_ANGLE_SLOTS:
            if slot == "on_grid":
                deg = float(int(rng.integers(1, CP8_ROTATION_STEPS)) * step)
            elif slot == "mid_grid":
                deg = float(int(rng.integers(0, CP8_ROTATION_STEPS)) * step + step / 2.0)
            else:
                deg = _off_grid(rng, step)
            rows.append({"query_index": index, "slot": slot, "angle_deg": deg})
    return rows


def _off_grid(rng: np.random.Generator, step: float) -> float:
    for _ in range(1000):
        deg = float(rng.uniform(0.0, 360.0))
        if all(
            min(abs((deg - offset) % step), step - abs((deg - offset) % step))
            > CP8_OFF_GRID_MARGIN_DEG
            for offset in (0.0, step / 2.0)
        ):
            return deg
    raise RuntimeError("could not draw an off-grid angle")


def select_queries(
    shapes: Sequence[Any], seed: int = CP8_SEED
) -> list[dict[str, Any]]:
    """Pick the frozen query set by rule, without looking at anything a model might say.

    Eligibility is a class membership test minus the burned stems; the draw inside each class is a
    seeded permutation. The order of families and classes is fixed in ``FAMILIES``, so the whole
    selection is reproducible from the seed alone.
    """
    by_stem: dict[str, list[Any]] = {}
    for shape in shapes:
        stem = str(shape.shape_id).removeprefix("mpeg7_")
        class_name, _, number = stem.rpartition("-")
        if number.isdigit():
            by_stem.setdefault(stem, []).append(shape)
    eligible: dict[str, list[Any]] = {}
    for stem, hits in by_stem.items():
        class_name = stem.rpartition("-")[0]
        if stem in BURNED_QUERY_STEMS or len(hits) != 1:
            continue
        eligible.setdefault(class_name, []).append(hits[0])
    for class_name in eligible:
        eligible[class_name].sort(key=lambda shape: str(shape.shape_id))

    chosen: list[dict[str, Any]] = []
    for family_index, family in enumerate(FAMILIES):
        missing = [name for name in family.classes if name not in eligible]
        if missing:
            raise ValueError(f"family {family.key} has no shapes for {missing}")
        for class_index, class_name in enumerate(family.classes):
            pool = eligible[class_name]
            count = 1 if family.per_class else CP8_QUERIES_PER_FAMILY
            if len(pool) < count:
                raise ValueError(f"{class_name} has only {len(pool)} unused shapes for {count}")
            order = np.random.default_rng(seed + 1_000 * family_index + class_index).permutation(
                len(pool)
            )
            for pick in order[:count]:
                shape = pool[int(pick)]
                stem = str(shape.shape_id).removeprefix("mpeg7_")
                chosen.append(
                    {
                        "shape_id": str(shape.shape_id),
                        "stem": stem,
                        "class": class_name,
                        "family": family.key,
                        "family_zh": family.label_zh,
                        "descriptors": contour_descriptors(np.asarray(shape.points)),
                    }
                )
    if len(chosen) != len(FAMILIES) * CP8_QUERIES_PER_FAMILY:
        raise ValueError(f"expected 24 queries, built {len(chosen)}")
    return chosen


def build_design(
    shapes: Sequence[Any], *, provenance: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The whole frozen study plan, as one JSON-ready mapping."""
    queries = select_queries(shapes)
    angles = draw_angles(np.random.default_rng(CP8_SEED), len(queries))
    return {
        "schema": CP8_SCHEMA,
        "task": "P0-4.6 CP8-B",
        "status": (
            "frozen before any retrieval ran in this study. The file is a design, not a result: it "
            "contains no distance, no rank and no hit."
        ),
        "seed": CP8_SEED,
        "provenance": dict(provenance or {}),
        "objective": (
            "test whether the CP7 conclusions hold on query "
                "shapes that never entered a scored cell, "
            "using the frozen math of every arm and the CP8-A two-view statistics"
        ),
        "queries": queries,
        "n_queries": len(queries),
        "families": {
            family.key: {
                "label_zh": family.label_zh,
                "classes": list(family.classes),
                "why_these_classes": family.why_these_classes,
                "measurable_expectation": family.measurable_expectation,
                "honest_gap": family.gap,
                "n_selected": sum(q["family"] == family.key for q in queries),
            }
            for family in FAMILIES
        },
        "coverage_audit": audit_coverage(queries),
        "angle_design": {
            "slots_per_query": list(CP8_ANGLE_SLOTS),
            "n_rotated_per_query": len(CP8_ANGLE_SLOTS),
            "control_slot": CP8_CONTROL_SLOT,
            "control_angle_deg": 0.0,
            "grid": angle_grid(),
            "rotation_steps": CP8_ROTATION_STEPS,
            "off_grid_margin_deg": CP8_OFF_GRID_MARGIN_DEG,
            "draw": (
                "one numpy default_rng(CP8_SEED) stream, queries in frozen order, slots in frozen "
                "order -- no per-query rejection sampling after a result is seen"
            ),
            "rows": angles,
        },
        "cell_arithmetic": {
            "n_rotated_cells_per_arm_condition": len(queries) * len(CP8_ANGLE_SLOTS),
            "n_control_cells_per_arm_condition": len(queries),
            "n_cells_per_arm_condition": len(queries) * (len(CP8_ANGLE_SLOTS) + 1),
            "note": (
                "reported as two views, never pooled. CP8-A "
                    "exists because pooling the control into "
                "a rotation headline inflated pose-blind arms by 12 free perfect scores."
            ),
        },
        "arms": {
            "labels": list(CP8_ARMS),
            "note": (
                "selection of six arms, all with math and parameters already frozen elsewhere. The "
                "S0K arms need the official toolbox, which "
                    "is not redistributable and is read from a "
                "gitignored directory; if it is absent the "
                    "run reports which arms are blocked rather "
                "than quietly dropping them."
            ),
        },
        "conditions": {
            "primary": CP8_PRIMARY_CONDITION,
            "correctness_check": CP8_CHECK_CONDITION,
            "definitions": {
                "T0": "full gallery, exact target kept -- a wiring check, not the headline",
                "T1": "exact target removed",
                "T2": "exact target and rigid near-duplicates removed -- the primary condition",
            },
            "near_duplicate_rule": "E0 aligned distance below T2_THRESHOLD, as in CP7-C",
        },
        "top_k": CP8_TOP_K,
        "statistics": {
            "n_boot": CP8_N_BOOT,
            "statistical_unit": (
                "the original query: five rotated cells share one shape, so intervals cluster by "
                "query and 24 x 5 rows are never treated as 120 independent observations"
            ),
            "report_separately": [
                "category retrieval (Precision@10, Recall@10, NDCG@10, same-class Top-1, MRR)",
                "rotation stability (Top-1 stability, Top-10 "
                    "Jaccard, rank shift, in-grid vs off-grid)",
                "engineering cost (gallery precompute, mean query time, P95 where samples allow)",
            ],
            "metric_conventions": [
                "Precision@K = hits / min(k, returned); "
                    "Recall@K = hits / the same-class candidates "
                "this query retained, a per-query denominator, undefined when nothing remains",
                "paired differences are published as agreement(B) - agreement(A) under the name "
                "second_minus_first, the one convention CP8-A made enforced",
            ],
        },
        "not_independent": list(INDEPENDENCE_LIMITS),
        "immutability": (
            "a query swap or angle redraw after seeing a "
                "result is a new study with a new seed and a "
            "new schema version, recorded as such. The generator refuses to rewrite this file in "
            "place; see scripts/design_checkpoint8_validation.py."
        ),
        "prohibitions_honoured": [
            "no official ShapeComp weight or MATLAB file was touched",
            "no algorithm definition, parameter or training was changed",
            "no production retriever, real water-body data or Web UI work",
            "no historical CSV overwritten; this file is new, not a revision",
            "no category label or H1 reuse presented as a new human judgement",
        ],
    }


def design_content_sha256(design: Mapping[str, Any]) -> str:
    """Hash of everything except the provenance block and the hash itself.

    Dropping ``content_sha256`` makes the function idempotent, so the committed file can be
    re-hashed to prove it still matches the rules; provenance is excluded because it records the
    machine, the interpreter and the commit that wrote it, none of which is part of the study.
    """
    payload = {k: v for k, v in design.items() if k not in {"provenance", "content_sha256"}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
