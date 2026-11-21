"""Multi-stroke contour assembly (M1, docs/DECISIONS.md D93).

An independent preprocessing adapter: join 2..8 separately drawn strokes into
one simple closed ring, or refuse with a traceable reason. It never modifies
the production retrieval math — a successful assembly is handed to the existing
:func:`earthglyph.geometry.sketch.contour_from_points` /
:func:`earthglyph.retrieval.search.search_by_contour` chain unchanged.

Method (frozen in D93; thresholds are initial experimental values, not claims
of optimality):

* scale = arc-length-weighted RMS radius of the drawn strokes (the raw-point
  RMS was replaced after the B1 density experiment: one slowly drawn stroke
  moved it by ~13% and flipped verdicts on the same geometry —
  docs/DECISIONS.md D96); endpoint tolerance = ``ENDPOINT_TOL_REL * scale``;
  junction turn limit ``MAX_JUNCTION_TURN_DEG`` (a rectangle corner drawn as
  separate strokes turns 90 deg and is legal; a 180 deg hairpin is not);
* strokes are nodes, legal endpoint joins are edges; a bounded backtracking
  search finds cycles that use every stroke exactly once (no nearest-neighbour
  greed). Fixing stroke 0 forward at its start removes the rotation/reversal
  duplicates of the same ring;
* every complete candidate is validated with the production criteria
  (material self-intersection via the sketch module's own GEOS check, then the
  full ``contour_from_points`` chain). Zero valid solutions -> rejected; exactly
  one -> ok; two or more substantially different junction matchings ->
  ambiguous, never an arbitrary pick.

The output ring repeats its first point at the end on purpose: ``close_trace``
drops the last vertex whenever the endpoint gap is nonzero, so an exact closing
duplicate is what keeps the last legitimate contour point alive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from earthglyph.geometry.resampling import open_ring
from earthglyph.geometry.sketch import (
    SketchError,
    _self_intersection_info,
    contour_from_points,
    dedupe_points,
)

MAX_STROKES = 8
MAX_TOTAL_POINTS = 4096
# Initial experimental values (D93): validated on the test samples, not optimal.
ENDPOINT_TOL_REL = 0.03
MAX_JUNCTION_TURN_DEG = 150.0
# Endpoint tangent = chord back over this share of the stroke's arc length.
TANGENT_ARC_SHARE = 0.02
SEARCH_NODE_BUDGET = 100_000
MAX_MATCHINGS = 10_000


class AssemblyError(ValueError):
    """Raised when the multi-stroke input structure itself is unusable."""


@dataclass(frozen=True)
class AssemblyResult:
    """Assembly outcome plus the full audit trail (D93 output contract)."""

    status: str  # "ok" | "rejected" | "ambiguous"
    reason_code: str | None
    reason: str | None
    scale: float
    n_strokes: int
    order: list[tuple[int, bool]]  # (stroke index, reversed) in traversal order
    junctions: list[dict[str, Any]]
    contour_points: np.ndarray | None  # closed ring, first point repeated
    warnings: list[str]
    n_valid_solutions: int
    raw_strokes: list[np.ndarray]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "scale": self.scale,
            "n_strokes": self.n_strokes,
            "order": [[int(i), bool(rev)] for i, rev in self.order],
            "junctions": [dict(j) for j in self.junctions],
            "contour_points": (
                None
                if self.contour_points is None
                else [[float(x), float(y)] for x, y in self.contour_points]
            ),
            "warnings": list(self.warnings),
            "n_valid_solutions": self.n_valid_solutions,
            "strokes": [[[float(x), float(y)] for x, y in s] for s in self.raw_strokes],
            "endpoint_tol_rel": ENDPOINT_TOL_REL,
            "max_junction_turn_deg": MAX_JUNCTION_TURN_DEG,
        }


def parse_strokes_payload(payload: Any) -> tuple[list[np.ndarray], str]:
    """Validate the frozen schema-v2 envelope; geometry checks live in assemble.

    Returns ``(strokes, unit)`` where each stroke is the raw ``points`` value.
    The legacy single-stroke ``points`` format never passes through here.
    """
    if not isinstance(payload, dict):
        raise AssemblyError("invalid_input: expected a schema-v2 object with 'strokes'")
    if "points" in payload:
        raise AssemblyError("invalid_input: 'points' and 'strokes' are mutually exclusive")
    version = payload.get("schema_version")
    if type(version) is not int or version != 2:
        raise AssemblyError("invalid_input: 'schema_version' must be the integer 2")
    mode = payload.get("mode", "contour")
    if mode != "contour":
        raise AssemblyError(f"invalid_input: mode {mode!r} is not supported (M1 is contour only)")
    unit = payload.get("unit", "canvas")
    if not isinstance(unit, str):
        raise AssemblyError("invalid_input: 'unit' must be a string")
    strokes = payload.get("strokes")
    if not isinstance(strokes, list) or not strokes:
        raise AssemblyError("invalid_input: 'strokes' must be a non-empty list")
    for index, stroke in enumerate(strokes):
        if not isinstance(stroke, dict) or "points" not in stroke:
            raise AssemblyError(f"invalid_input: stroke {index} must be an object with 'points'")
    arrays: list[np.ndarray] = []
    for index, stroke in enumerate(strokes):
        try:
            arrays.append(np.asarray(stroke["points"], dtype=float))
        except (TypeError, ValueError) as error:
            raise AssemblyError(f"invalid_input: stroke {index} is not a point list") from error
    return arrays, unit


def _arc_weighted_scale(strokes: list[np.ndarray]) -> float:
    """Exact arc-length-weighted RMS radius of the drawn polylines.

    The pointer fires at erratic, per-stroke-varying rates, so an RMS over raw
    points is a count-weighted mean: one slowly drawn stroke shifts the scale
    — and every tolerance derived from it — while the geometry stands still
    (B1: tolerance 5.05..5.77 px on one 400x240 drawing, verdict flips).
    Integrating along the drawn arc removes the sampling density from the
    yardstick. Per segment [a, b] of length l the closed forms are
    ``int p ds = l (a + b) / 2`` and
    ``int |p - c|^2 ds = l (|a - c|^2 + (a - c) . (b - a) + l^2 / 3)``.
    Zero-length segments carry no arc and are skipped. Returns 0.0 when the
    strokes carry no arc at all (the caller treats that as coincident input).
    """
    centroid_num = np.zeros(2, dtype=float)
    total_length = 0.0
    for arr in strokes:
        delta = np.diff(arr, axis=0)
        length = np.linalg.norm(delta, axis=1)
        keep = length > 0.0
        delta, length = delta[keep], length[keep]
        if length.size == 0:
            continue
        starts, ends = arr[:-1][keep], arr[1:][keep]
        centroid_num += ((starts + ends) / 2.0 * length[:, None]).sum(axis=0)
        total_length += float(length.sum())
    if total_length <= 0.0:
        return 0.0
    centroid = centroid_num / total_length
    var_num = 0.0
    for arr in strokes:
        delta = np.diff(arr, axis=0)
        length = np.linalg.norm(delta, axis=1)
        keep = length > 0.0
        delta, length = delta[keep], length[keep]
        if length.size == 0:
            continue
        d = arr[:-1][keep] - centroid
        e = arr[1:][keep] - centroid
        var_num += float((length * (
            np.sum(d * d, axis=1) + np.sum(d * (e - d), axis=1)
            + length * length / 3.0
        )).sum())
    return float(np.sqrt(var_num / total_length))


def _validate_strokes(strokes: Any) -> tuple[list[np.ndarray], list[np.ndarray], float]:
    """Structural checks + per-stroke dedupe. Returns (raw, deduped, scale)."""
    if not isinstance(strokes, (list, tuple)) or not strokes:
        raise AssemblyError("invalid_input: expected a non-empty list of strokes")
    if len(strokes) > MAX_STROKES:
        raise AssemblyError(f"too_many_strokes: {len(strokes)} strokes (max {MAX_STROKES})")
    raw: list[np.ndarray] = []
    total = 0
    for index, stroke in enumerate(strokes):
        try:
            arr = np.asarray(stroke, dtype=float)
        except (TypeError, ValueError) as error:
            raise AssemblyError(f"invalid_input: stroke {index} is not a point list") from error
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise AssemblyError(f"invalid_input: stroke {index} must have shape (M, 2)")
        if not np.all(np.isfinite(arr)):
            raise AssemblyError(f"invalid_input: stroke {index} contains non-finite coordinates")
        if arr.shape[0] < 2:
            raise AssemblyError(f"degenerate_stroke: stroke {index} has fewer than 2 points")
        total += arr.shape[0]
        raw.append(arr)
    if total > MAX_TOTAL_POINTS:
        raise AssemblyError(f"too_many_points: {total} points total (max {MAX_TOTAL_POINTS})")
    scale = _arc_weighted_scale(raw)
    if scale <= 0.0:
        raise AssemblyError("invalid_input: all points coincide")
    deduped = [dedupe_points(arr, scale=scale) for arr in raw]
    for index, arr in enumerate(deduped):
        if arr.shape[0] < 2:
            raise AssemblyError(
                f"degenerate_stroke: stroke {index} collapses to a point after dedupe"
            )
    return raw, deduped, scale


def _endpoint_tangent(points: np.ndarray, end: int) -> np.ndarray:
    """Unit vector from the endpoint into the stroke, over >=2% of its arc."""
    seq = points if end == 0 else points[::-1]
    step = np.linalg.norm(np.diff(seq, axis=0), axis=1)
    cumulative = np.cumsum(step)
    target = TANGENT_ARC_SHARE * float(cumulative[-1])
    # cumulative[k] is the arc to vertex k+1; never pick the endpoint itself
    index = min(int(np.searchsorted(cumulative, target)) + 1, seq.shape[0] - 1)
    direction = seq[index] - seq[0]
    norm = float(np.linalg.norm(direction))
    return np.asarray(direction / norm, dtype=float)


def _turn_deg(incoming: np.ndarray, outgoing: np.ndarray) -> float:
    cosine = float(np.clip(np.dot(incoming, outgoing), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def _junction_record(
    i: int, ei: int, j: int, ej: int,
    ends: list[list[np.ndarray]], gap: float, turn: float, scale: float,
) -> dict[str, Any]:
    a, b = ends[i][ei], ends[j][ej]
    snap = (a + b) / 2.0
    return {
        "from_stroke": i,
        "from_endpoint": ei,
        "to_stroke": j,
        "to_endpoint": ej,
        "from_point": [float(a[0]), float(a[1])],
        "to_point": [float(b[0]), float(b[1])],
        "snap_point": [float(snap[0]), float(snap[1])],
        "gap": float(gap),
        "gap_rel": float(gap / scale),
        "correction": float(gap / 2.0),
        "turn_deg": float(turn),
    }


def _build_ring(
    order: list[tuple[int, bool]],
    junctions: list[dict[str, Any]],
    strokes: list[np.ndarray],
    scale: float,
) -> np.ndarray:
    """Walk the cycle, snap junction endpoints, dedupe, repeat the first point."""
    pieces: list[np.ndarray] = []
    n = len(order)
    for k, (index, reversed_flag) in enumerate(order):
        pts = strokes[index][::-1] if reversed_flag else strokes[index]
        pts = pts.copy()
        pts[0] = np.asarray(junctions[(k - 1) % n]["snap_point"])
        pts[-1] = np.asarray(junctions[k]["snap_point"])
        pieces.append(pts)
    ring = np.vstack(pieces)
    keep = np.ones(ring.shape[0], dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(ring, axis=0), axis=1) > 1e-9 * scale
    ring = ring[keep]
    # the closing junction already lands on the first point; only append the
    # closing duplicate when the walk did not end exactly where it started
    if np.linalg.norm(ring[-1] - ring[0]) > 1e-9 * scale:
        ring = np.vstack([ring, ring[:1]])
    return ring


def _classify_sketch_error(message: str) -> str:
    if message.startswith("self-intersecting"):
        return "self_intersection"
    if message.startswith("degenerate") or message.startswith("trace too short"):
        return "degenerate_shape"
    return "no_legal_assembly"


def _validate_candidate(
    ring: np.ndarray,
) -> tuple[bool, str | None, str | None]:
    """Production criteria only: GEOS material-crossing check, then full chain."""
    crossing = _self_intersection_info(open_ring(ring))
    if crossing["count"] and crossing["material"]:
        return False, "self_intersection", (
            f"assembled ring self-intersects materially ({crossing['reason']}); "
            "the strokes cross instead of forming one simple outline"
        )
    try:
        contour_from_points(ring, closed=True)
    except SketchError as error:
        message = str(error)
        return False, _classify_sketch_error(message), f"assembled ring refused: {message}"
    return True, None, None


class _BudgetExceeded(Exception):
    pass


def assemble_contour(strokes: Any) -> AssemblyResult:
    """Assemble 1..8 strokes into one closed ring, or refuse with a reason.

    A single stroke is delegated untouched to the production closure rules
    (``contour_from_points``); the junction machinery only runs for 2..8.
    Structural problems raise :class:`AssemblyError`; geometric refusals and
    ambiguity are returned as a result with ``status`` and ``reason_code``.
    """
    raw, deduped, scale = _validate_strokes(strokes)
    n = len(deduped)
    tol = ENDPOINT_TOL_REL * scale

    def refused(code: str, reason: str, *, n_valid: int = 0) -> AssemblyResult:
        return AssemblyResult(
            status="rejected", reason_code=code, reason=reason, scale=scale,
            n_strokes=n, order=[], junctions=[], contour_points=None,
            warnings=[], n_valid_solutions=n_valid, raw_strokes=raw,
        )

    if n == 1:
        return AssemblyResult(
            status="ok", reason_code=None, reason=None, scale=scale, n_strokes=1,
            order=[(0, False)], junctions=[], contour_points=deduped[0],
            warnings=[], n_valid_solutions=1, raw_strokes=raw,
        )

    ends = [[arr[0], arr[-1]] for arr in deduped]
    tangents = [[_endpoint_tangent(arr, e) for e in (0, 1)] for arr in deduped]
    gaps = np.linalg.norm(
        np.asarray([ends[i][ei] for i in range(n) for ei in (0, 1)])[:, None, :]
        - np.asarray([ends[j][ej] for j in range(n) for ej in (0, 1)])[None, :, :],
        axis=2,
    )

    for i in range(n):
        if float(np.linalg.norm(ends[i][0] - ends[i][1])) <= tol:
            return refused("closed_loop_with_extras", (
                f"stroke {i} already closes on itself while {n - 1} other stroke(s) "
                "remain: that is a separate loop or a branch, not one outline"
            ))

    # legal junction table: (exit stroke, exit end, entry stroke, entry end)
    table: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    has_close_partner = np.zeros((n, 2), dtype=bool)
    for i in range(n):
        for ei in (0, 1):
            for j in range(n):
                if j == i:
                    continue
                for ej in (0, 1):
                    gap = float(gaps[2 * i + ei, 2 * j + ej])
                    if gap > tol:
                        continue
                    has_close_partner[i, ei] = True
                    has_close_partner[j, ej] = True
                    turn = _turn_deg(-tangents[i][ei], tangents[j][ej])
                    if turn > MAX_JUNCTION_TURN_DEG:
                        continue
                    table[(i, ei, j, ej)] = _junction_record(
                        i, ei, j, ej, ends, gap, turn, scale
                    )

    matchings: dict[frozenset[tuple[tuple[int, int], tuple[int, int]]],
                    tuple[list[tuple[int, bool]], list[dict[str, Any]]]] = {}
    nodes = 0

    def dfs(current: int, exit_end: int, used: int,
            order: list[tuple[int, bool]], junctions: list[dict[str, Any]]) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > SEARCH_NODE_BUDGET or len(matchings) > MAX_MATCHINGS:
            raise _BudgetExceeded
        if len(order) == n:
            closing = table.get((current, exit_end, 0, 0))
            if closing is not None:
                cycle = junctions + [closing]
                pairs: list[tuple[tuple[int, int], tuple[int, int]]] = []
                for link in cycle:
                    a = (int(link["from_stroke"]), int(link["from_endpoint"]))
                    b = (int(link["to_stroke"]), int(link["to_endpoint"]))
                    pairs.append((a, b) if a <= b else (b, a))
                key = frozenset(pairs)
                if key not in matchings:
                    matchings[key] = (list(order), cycle)
            return
        for j in range(n):
            if used & (1 << j):
                continue
            for ej in (0, 1):
                edge = table.get((current, exit_end, j, ej))
                if edge is None:
                    continue
                order.append((j, ej == 1))
                junctions.append(edge)
                dfs(j, 1 - ej, used | (1 << j), order, junctions)
                order.pop()
                junctions.pop()

    try:
        dfs(0, 1, 1, [(0, False)], [])
    except _BudgetExceeded:
        return refused("search_complexity", (
            "too many plausible junction combinations to search within the node "
            f"budget ({SEARCH_NODE_BUDGET}); redraw with clearer separated endpoints"
        ))

    if not matchings:
        if not bool(has_close_partner.all()):
            isolated = [
                f"stroke {i} {'start' if ei == 0 else 'end'}"
                for i in range(n) for ei in (0, 1) if not has_close_partner[i, ei]
            ]
            return refused("open_gap", (
                "endpoint(s) " + ", ".join(isolated[:4]) + " have no partner within "
                f"{ENDPOINT_TOL_REL:.2f} of the shape scale ({tol:.1f} px); "
                "the obvious gap is not bridged — draw the missing part or bring "
                "the ends closer"
            ))
        return refused("no_legal_assembly", (
            "endpoints are close but no junction keeps the turn below "
            f"{MAX_JUNCTION_TURN_DEG:.0f} deg while using every stroke in one "
            "closed ring (hairpin reversal, branch or isolated stroke)"
        ))

    valid: list[tuple[list[tuple[int, bool]], list[dict[str, Any]], np.ndarray]] = []
    failures: list[tuple[str, str]] = []
    for key in sorted(matchings, key=lambda k: sorted(k)):
        order, junctions = matchings[key]
        ring = _build_ring(order, junctions, deduped, scale)
        ok, code, reason = _validate_candidate(ring)
        if ok:
            valid.append((order, junctions, ring))
            if len(valid) >= 2:
                break
        else:
            assert code is not None and reason is not None
            failures.append((code, reason))

    if len(valid) >= 2:
        return AssemblyResult(
            status="ambiguous", reason_code="ambiguous", reason=(
                f"{len(valid)}+ substantially different assemblies all use every "
                "stroke in a valid closed ring; the intended outline is ambiguous "
                "— redraw the ambiguous junctions so only one pairing is plausible"
            ),
            scale=scale, n_strokes=n, order=[], junctions=[], contour_points=None,
            warnings=[], n_valid_solutions=len(valid), raw_strokes=raw,
        )
    if not valid:
        code, reason = failures[0]
        return refused(code, reason)

    order, junctions, ring = valid[0]
    warnings: list[str] = []
    snapped = [j for j in junctions if j["gap"] > 0.0]
    if snapped:
        worst = max(snapped, key=lambda j: j["gap"])
        warnings.append(
            f"{len(snapped)} junction(s) snapped by up to {worst['correction']:.2f} px "
            f"({worst['gap_rel']:.3f} of the shape scale each side); the short straight "
            "connectors are assumptions, not drawn ink"
        )
    return AssemblyResult(
        status="ok", reason_code=None, reason=None, scale=scale, n_strokes=n,
        order=order, junctions=junctions, contour_points=ring,
        warnings=warnings, n_valid_solutions=1, raw_strokes=raw,
    )