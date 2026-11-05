"""E2 — matching the *sequence* of salient convex/concave structure.

Why this method exists
----------------------
E0 answers "how far apart are these two boundaries on average?"  A star and a
disk of the same area can be close in that sense at every scale except the one
the eye notices, because the eye keys on the **corners**: where the boundary
turns, which way, how sharply, and in what order around the shape.  The
literature's answer (ShapeComp's curvature-at-scale descriptors, and the
corner-based accounts before it) is that perceptual similarity tracks those
turning events, not the mean offset.  E2 is that idea implemented as a
retrieval score.

The pipeline

1. **signed turning at several scales** — for a vertex, the angle between the
   incoming chord and the outgoing chord, measured over an arc-length window.
   Small windows see pen jitter, large windows see the whole lobe; a real corner
   is sharp at a middle window and still there one window up.  Sign is geometry,
   not decoration: for a CCW ring positive = convex (a prong), negative = concave
   (a valley).  A star and a circle differ here *only* in the sign pattern, so
   this is the discriminator the experiment is about.
2. **salient events, not vertices** — a local maximum of |turning| that clears a
   bar set by *the turning of a circle under the same window*.  Why that bar and
   not the shape's own mean: the mean of |turning| is dominated by how much
   turning the shape has in total, which is 2π whatever the shape is, so it does
   not shrink when a window gets coarse — meanwhile a coarse window sums more
   turning and its mean grows.  Comparing peaks to that mean makes every coarse
   scale silent and the "multi-scale" part decorative.  Comparing them to
   ``2*pi*lag/n`` instead gives a statement that stays meaningful at every window:
   *this vertex turns k times faster than the boundary of a circle would here*.
   A circle therefore produces no events at all, which is the property the whole
   experiment needs (a disk must not be allowed to look like a corner).
3. **persistence and turning, not counts** — every event records how many
   consecutive windows kept it above the bar, and is charged **in radians of
   boundary turning**, the same unit on every shape and every scale.  Two things
   follow, and each has its own test.  A sharp prong that one shape has and the
   other lacks costs far more than a shallow wiggle the two shapes disagree about:
   the corners of ``star(5)`` carry 1.169 and 2.426 rad, while the single spurious
   event of a circle disturbed by 0.5% radial noise carries 0.505, so slight pen
   jitter is down-weighted by geometry instead of by a hand-set coefficient
   (``test_pen_jitter_is_down_weighted_by_geometry``).  And because the denominator
   is 2*pi rather than the pair's own total weight, "a smooth circle against a
   noisy circle" cannot come out equal to "a circle against a star": with the pair's
   own weight as the denominator both would be exactly 1.0 by arithmetic, because in
   each case one side contributes no events at all.
   ``test_circle_against_star_is_the_stars_turning_in_full_turns`` pins the absolute
   value instead.
4. **sequence alignment with a price for unmatched events** — two shapes are
   compared by aligning their event sequences in arc-length order, allowing
   limited drift per event (the deformation budget) and charging a gap for every
   corner one shape has and the other does not.  Returning "same number of
   corners" as a win would be a corner *count*, which is exactly what this method
   is not allowed to be: the tests plant equal-count, different-arrangement
   shapes and require different scores.

Rotation and start point are handled by searching the cyclic offset between the
two event sequences, so the score is invariant to both; the winning offset is
returned because the figures are only allowed to draw the transform the method
actually computed.  Translation and uniform scale are exact rather than searched,
because angles and arc-length fractions do not see them at all; the shared
standardizer still brings every contour to the same 128-point unit-RMS ring so
that all four methods compare identical inputs.  Mirror is opt-in, like anywhere
else in this project.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from earthglyph.retrieval.alignment import mirror_points
from earthglyph.retrieval.perceptual_methods import check_contour, contour_stack

__all__ = [
    "DEFAULT_SCALES",
    "POLICY",
    "SalientStructureError",
    "SalientSignature",
    "align_sequences",
    "describe_policy",
    "distance",
    "distance_library",
    "exterior_angles",
    "score_library",
    "signature",
    "signed_turning",
    "uniform_turn_reference",
]

#: arc-length windows, as fractions of the perimeter.  1/64 of a 128-point ring is
#: two samples — it sees a nibble; 1/4 is a quarter of the ring — it sees a lobe.
#: The ladder is geometric because "the same structure one scale up" is what the
#: persistence test is counting, and that only means something if consecutive
#: windows are a constant factor apart.
DEFAULT_SCALES: tuple[float, ...] = (1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4)

#: the bar an event's prominence must clear, as a multiple of the turning a circle
#: accumulates over the same window — see :func:`uniform_turn_reference`
SALIENCE_K = 3.0
#: two vertices this close (in samples) are one event, not two
MIN_SEPARATION = 2
#: how far a corner's own turn group may reach, as a fraction of the perimeter.
#: A turn group is the run of same-sign turns that make one corner; it is bounded by
#: the next sign flip and by this reach, because on an all-convex boundary there is
#: no flip to stop it and the "run" would otherwise be the whole ring
RUN_RADIUS_FRACTION = 1 / 16
#: per-unit-arc drift allowed before the deformation penalty starts
DEFORMATION_BUDGET = 0.02
#: multiplier on the cost of a corner one shape has and the other does not
GAP_COST = 1.0
#: multiplier on the cost of disagreeing about *how pointed* a matched corner is.
#: Set well below 1 deliberately.  Measured against a sharp five-armed star: at unit
#: gain the gallery's worn star (tip 0.18, valley 0.12) sits 0.939 away and a
#: six-armed star 1.391, so rounding alone would cover two thirds of the distance that
#: separates arm counts — inverting the very hierarchy this method exists to reproduce.
#: At 0.25 those two comparisons come out 0.235 and 1.090, so rounding stays a real but
#: second-order difference.
PEAK_GAIN = 0.25
#: the unit the score is divided by: one full turn of the boundary.  Total turning
#: of a simple closed ring is exactly 2*pi, so "how many full turns of structure
#: are unexplained" is a scale that needs no pair-specific normalisation
NORMALISING_TURN = 2.0 * np.pi


class SalientStructureError(ValueError):
    """Raised when a contour cannot carry a structure signature at all."""


@dataclass(frozen=True)
class SalientEvent:
    """One corner: where it is, which way it turns, how hard, how long-lived."""

    position: float  # arc length from the ring's start, in [0, 1)
    sign: int  # +1 convex, -1 concave
    magnitude: float  # turning angle at the scale it was found, radians
    scale_index: int  # which window produced that magnitude
    persistence: int  # how many consecutive windows kept it above the bar
    peak: float = 0.0  # hardest single-vertex turn inside the group, radians

    @property
    def signed_magnitude(self) -> float:
        """The turning with its convex/concave sign attached — what matching pays on."""
        return float(self.sign) * self.magnitude


@dataclass(frozen=True)
class SalientSignature:
    """The ordered event sequence of one contour, plus what it was measured on."""

    events: tuple[SalientEvent, ...]
    n_vertices: int
    scales: tuple[float, ...]
    total_turning: float
    turning: np.ndarray = field(repr=False)  # (n_vertices, n_scales) signed

    @property
    def n_convex(self) -> int:
        return sum(1 for event in self.events if event.sign > 0)

    @property
    def n_concave(self) -> int:
        return sum(1 for event in self.events if event.sign < 0)

    def summary(self) -> dict[str, Any]:
        return {
            "events": len(self.events),
            "convex": self.n_convex,
            "concave": self.n_concave,
            "total_turning_rad": round(self.total_turning, 6),
            "positions": [round(event.position, 4) for event in self.events],
            "signed_magnitudes": [
                round(event.sign * event.magnitude, 5) for event in self.events
            ],
            "persistence": [event.persistence for event in self.events],
            "peaks": [round(event.peak, 5) for event in self.events],
        }


def _perimeter(points: np.ndarray) -> np.ndarray:
    """Cumulative arc length at each vertex, with the total appended."""
    steps = np.linalg.norm(np.diff(np.vstack([points, points[:1]]), axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)])


def _lag_for(fraction: float, n: int) -> int:
    """Window radius in samples for an arc-length fraction of the perimeter."""
    return max(1, int(round(fraction * n)))


def _run_radius(n: int) -> int:
    """Reach of one corner's own turn group, in samples."""
    return max(1, int(round(RUN_RADIUS_FRACTION * n)))


def exterior_angles(points: np.ndarray) -> np.ndarray:
    """Signed turn at every vertex, measured over one step.

    Positive = the ring turns left = convex, for the CCW rings this project
    standardizes into.  These are the atoms the multi-scale measure is built from,
    and they have the invariant that makes everything below honest: summed around a
    simple closed ring they are exactly +-2*pi, whatever the shape and however it was
    sampled.
    """
    step = np.roll(points, -1, axis=0) - points
    previous = points - np.roll(points, 1, axis=0)
    cross = previous[:, 0] * step[:, 1] - previous[:, 1] * step[:, 0]
    dot = previous[:, 0] * step[:, 0] + previous[:, 1] * step[:, 1]
    return np.asarray(np.arctan2(cross, dot), dtype=float)


def signed_turning(
    points: np.ndarray, *, scales: tuple[float, ...] = DEFAULT_SCALES
) -> np.ndarray:
    """Accumulated signed turning at every vertex for every arc-length window.

    The window is measured in arc length, not in samples, so the same scale means
    the same physical thing on a 128-point and a 256-point sampling.  Inside it the
    measure is a box filter of :func:`exterior_angles`, *not* the angle between the
    two chords spanning the window.  The reason is structural rather than measured:
    a chord only sees the part of a corner's turn that falls between its two
    endpoints, so on a rounded tip — where the turn is spread over several samples —
    the chord value depends on where the sampler happened to land, while a sum of
    exterior angles over a fixed arc length is the turning the window contains no
    matter how it is indexed.  The chord variant was not kept, so no number is quoted
    for it here; what the kept variant guarantees is stated in the sentence before.

    Cancellation is the point at coarse windows: a convex tip and the concave valley
    beside it inside one window partly cancel, which is the geometric statement
    "this window cannot resolve that pair", and is why persistence across windows is
    evidence about structure size rather than about noise.
    """
    array = check_contour(points, "contour")
    if float(np.linalg.norm(np.diff(np.vstack([array, array[:1]]), axis=0), axis=1).sum()) <= 0.0:
        raise SalientStructureError("contour has zero perimeter; it has no structure to match")
    atoms = exterior_angles(array)
    n = array.shape[0]
    out = np.zeros((n, len(scales)), dtype=float)
    for index, fraction in enumerate(scales):
        lag = _lag_for(fraction, n)
        if 2 * lag >= n:  # the window wraps onto itself twice: nothing to measure
            continue
        out[:, index] = sum(np.roll(atoms, -shift) for shift in range(-lag, lag + 1))
    return out


def uniform_turn_reference(n: int, lag: int) -> float:
    """Accumulated turning of a circle over the window that spans ``2*lag+1`` steps.

    This is the bar a corner has to beat.  On a regular ``n``-gon the boundary turns
    by ``2*pi/n`` at every vertex, so a window of ``2*lag+1`` vertices accumulates
    exactly ``2*pi*(2*lag+1)/n`` — the same quantity, computed on a shape with no
    corners at all.  Requiring a vertex to turn ``k`` times more than that is a
    statement that stays true at every window and needs no calibration constant:

    * a circle produces no events at all for ``k > 1`` (tested);
    * the bar grows with the window while an isolated corner's accumulated turning
      does not, so a coarse scale reports coarse structure instead of re-reporting
      every corner the fine scale already saw;
    * neither term contains an area or a length, so the bar cannot smuggle a size
      preference into what is supposed to be a shape comparison — and it is
      unchanged by re-sampling, which the chord version was not (tested).
    """
    return 2.0 * np.pi * (2 * lag + 1) / max(1, n)


def _cyclic_peaks(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local maxima of a periodic signal, with their prominences.

    ``scipy.signal.find_peaks`` is used rather than a hand-written ``>=`` neighbour
    test, for two reasons that both showed up as bugs.  A windowed turning signal is
    *flat* over the whole stretch of boundary where the window still contains the
    same corner — a plateau several samples wide — and a ``>=`` test reports every
    vertex of that plateau as a separate corner, which inflates the event count by
    two- to three-fold.  find_peaks resolves flat tops to a single index.  The
    prominence it returns is also the quantity that separates a corner from boundary
    texture: it measures how far a peak stands above the lowest pass that leads to a
    taller peak, which a plain threshold cannot see.

    The signal is padded to three periods so a peak near the seam sees the same
    left-right context as every other peak; only the middle period is returned.
    """
    n = values.shape[0]
    if n == 0:
        return np.empty(0, dtype=int), np.empty(0, dtype=float)
    padded = np.concatenate([values, values, values])
    indices, properties = find_peaks(padded, prominence=0.0)
    middle = (indices >= n) & (indices < 2 * n)
    return indices[middle] - n, np.asarray(properties["prominences"][middle], dtype=float)


def signature(
    points: Any,
    *,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    salience_k: float = SALIENCE_K,
    min_separation: int = MIN_SEPARATION,
) -> SalientSignature:
    """Extract the ordered salient-event sequence of one contour."""
    array = check_contour(points, "contour")
    turning = signed_turning(array, scales=scales)
    n = array.shape[0]
    lags = [_lag_for(fraction, n) for fraction in scales]
    # the bar is the turning of a circle at the same window: no absolute
    # calibration, no area or length term, and it rises with scale so a coarse
    # window reports coarse structure rather than re-reporting every corner
    thresholds = [
        salience_k * uniform_turn_reference(n, lag) if 2 * lag < n else np.inf
        for lag in lags
    ]
    magnitude = np.abs(turning)
    atoms = exterior_angles(array)
    run_radius = _run_radius(n)

    # one peak scan per window
    detected: list[tuple[np.ndarray, np.ndarray]] = []
    for scale_index, lag in enumerate(lags):
        if 2 * lag >= n:
            detected.append((np.empty(0, dtype=int), np.empty(0, dtype=float)))
            continue
        detected.append(_cyclic_peaks(magnitude[:, scale_index]))

    events: list[SalientEvent] = []
    for scale_index, (vertices, prominences) in enumerate(detected):
        for offset, vertex in enumerate(vertices):
            if float(prominences[offset]) < thresholds[scale_index]:
                continue
            # persistence: how many *larger* windows also find a salient peak here
            run = 1
            for next_index in range(scale_index + 1, len(lags)):
                far_vertices, far_prominences = detected[next_index]
                if far_vertices.size == 0:
                    break
                confirmed = any(
                    float(far_prominences[place]) >= thresholds[next_index]
                    and _cyclic_steps(int(vertex), int(far_vertex), n) <= lags[next_index]
                    for place, far_vertex in enumerate(far_vertices)
                )
                if not confirmed:
                    break
                run += 1
            position, angle, sign, peak = _measure_peak(
                atoms, n, int(vertex), radius=max(lags[scale_index], run_radius)
            )
            # the window that *found* the corner is not the window that *prices* it:
            # a coarse window sums several corners at once, so its peak height is a
            # property of the aperture.  Pricing goes through _measure_peak, which
            # integrates the corner's own same-sign turn group instead.
            events.append(
                SalientEvent(
                    position=position,
                    sign=sign,
                    magnitude=angle,
                    scale_index=scale_index,
                    persistence=run,
                    peak=peak,
                )
            )

    merged = _merge_duplicates(events, min_separation=min_separation, n=n, lags=lags)
    # the raw exterior-angle sequence sums to +-2*pi for a simple closed ring,
    # whatever the shape; it is recorded as an orientation invariant, not as a feature
    return SalientSignature(
        events=tuple(sorted(merged, key=lambda event: event.position)),
        n_vertices=n,
        scales=scales,
        total_turning=float(np.sum(exterior_angles(array))),
        turning=turning,
    )


def _measure_peak(
    atoms: np.ndarray,
    n: int,
    vertex: int,
    *,
    radius: int,
) -> tuple[float, float, int, float]:
    """Price one detected corner as the turning of its whole same-sign group.

    Three details carry the weight, and two of them were bugs before they were fixes.

    *Anchor.* find_peaks reports the *start* of a flat top, not the middle, so the
    search for the corner is widened by the detecting window and re-centred on the
    vertex that turns hardest.

    *Run.* A corner's turn is not one vertex's exterior angle.  Rounding a tip —
    which is what a hand-drawn star, a worn icon or a soft-cornered reservoir does —
    replaces one big turn by several small ones in a row, all of the same sign, so
    pricing a single vertex would read a worn tip as a different structure.  Summing
    the consecutive same-sign turns recovers the one quantity that is invariant under
    exactly that operation: how much the boundary turns here in total.  The run stops
    at a sign flip, so a tip can never swallow the valley beside it, and at ``radius``
    samples, so an all-convex boundary (where nothing flips) cannot swallow the whole
    ring.

    *Peak.* The run's total alone is invariant under rounding to the point of
    claiming identity: with the peak term switched off (``PEAK_GAIN = 0``) the
    gallery's worn star and a needle star measure exactly 0.0 apart, which over-states
    the case — people do tell those apart, and so does the turning profile.  The
    largest single-vertex turn inside the same run is the honest record of *how
    pointed* the corner is, and it is the quantity rounding destroys.  It is carried
    as a separate term so the group total can stay rotation- and phase-stable, and it
    is charged at ``PEAK_GAIN``, not at 1: the sum is the structure, the peak is its
    finish.
    """
    places = (vertex + np.arange(-radius, radius + 1)) % n
    anchor = int(places[int(np.argmax(np.abs(atoms[places])))])
    sign = 1.0 if float(atoms[anchor]) >= 0.0 else -1.0
    total = float(atoms[anchor])
    centre = float(anchor)
    mass = abs(float(atoms[anchor]))
    peak = mass
    for direction in (1, -1):
        for step in range(1, radius + 1):
            value = float(atoms[(anchor + direction * step) % n])
            if value * sign <= 0.0:
                break
            total += value
            weight = abs(value)
            peak = max(peak, weight)
            centre = _cyclic_mean(centre, mass, anchor + direction * step, weight, n)
            mass += weight
    return (centre % n) / n, abs(total), 1 if total >= 0.0 else -1, peak


def _cyclic_mean(xa: float, wa: float, xb: float, wb: float, n: int) -> float:
    """Weighted mean of two positions on a ring of ``n`` samples, short way round."""
    total = wa + wb
    if total <= 0.0:
        return xa
    delta = (xb - xa + n / 2.0) % n - n / 2.0
    return xa + delta * (wb / total)


def _cyclic_steps(a: int, b: int, n: int) -> int:
    """Distance in samples between two vertices of a ring, taking the short way."""
    raw = abs(int(a) - int(b)) % max(1, n)
    return min(raw, max(1, n) - raw)


def _merge_duplicates(
    events: list[SalientEvent], *, min_separation: int, n: int, lags: list[int]
) -> list[SalientEvent]:
    """Collapse the same corner found at several scales into one event.

    Two same-sign detections closer than half the smaller of their own windows are
    one piece of geometry seen through two apertures; keeping both would let a
    single prominent lobe appear twice in the sequence, which turns the signature
    back into a corner count.  The finest window wins, because it is where the
    corner's sharpness is measured most honestly.
    """
    def rank(event: SalientEvent) -> tuple[float, int, float]:
        return (-event.persistence, event.scale_index, -event.magnitude)

    ordered = sorted(events, key=rank)
    kept: list[SalientEvent] = []
    for event in ordered:
        if any(
            candidate.sign == event.sign
            and _cyclic_delta(candidate.position, event.position) < _merge_tolerance(
                candidate, event, lags=lags, n=n, min_separation=min_separation
            )
            for candidate in kept
        ):
            continue
        kept.append(event)
    return kept


def _merge_tolerance(
    left: SalientEvent, right: SalientEvent, *, lags: list[int], n: int, min_separation: int
) -> float:
    """How close two same-sign detections are the *same* corner.

    The coarse detection is the one whose position is uncertain — its window is
    what smears the peak — so the window of the coarser of the two sets the
    tolerance, and the finer scale's position is the one kept.
    """
    coarsest = max(left.scale_index, right.scale_index)
    return max(min_separation / max(1, n), 0.5 * lags[coarsest] / max(1, n))


def _cyclic_delta(a: float, b: float) -> float:
    """Shortest distance between two positions on a ring of circumference 1."""
    raw = abs(a - b) % 1.0
    return min(raw, 1.0 - raw)


def _match_cost(left: SalientEvent, right: SalientEvent, drift: float) -> float:
    """What it costs to say these two corners are the same corner.

    The turning difference is taken on *signed* magnitudes, so a prong matched to
    a valley pays the sum of both turns rather than a hand-picked penalty, and the
    two terms of the cost function stay in the same unit.

    Drift is free inside the deformation budget, then costs the *smaller* of the
    two corners' turns per unit of arc displaced.  The gain is deliberately not
    steeper than that, and the reason is a measurement: dividing the excess by the
    budget (so that drifting a corner by two budgets costs its whole turn) made a
    six-armed star *further* from a five-armed one than a plain circle was, because
    five and six arms cannot interleave without every corner sliding a few percent
    of the perimeter.  People do see the difference between five and six arms, but
    they see the difference between a star and a circle first.  With unit gain a
    rearrangement still costs real distance — the arrangement test in the suite
    checks that — it just cannot outrank "these corners do not exist at all".

    Pointedness is the third term and the one with the smallest gain: two corners
    that agree on total turning but disagree on whether that turning happened in one
    vertex or spread over five are the same structure with a different edge quality.
    Charging it at ``PEAK_GAIN`` keeps "worn tip" a real, second-order difference
    instead of either a false identity (gain 0) or a reclassified shape (gain 1).
    """
    turning = abs(left.signed_magnitude - right.signed_magnitude)
    excess = max(0.0, drift - DEFORMATION_BUDGET)
    shift = min(abs(left.signed_magnitude), abs(right.signed_magnitude))
    pointedness = abs(left.peak - right.peak)
    return turning + shift * excess + PEAK_GAIN * pointedness


def align_sequences(
    left: SalientSignature,
    right: SalientSignature,
    *,
    gap_cost: float = GAP_COST,
    max_offset_trials: int = 6,
) -> dict[str, Any]:
    """Compare two event sequences in order, paying for what cannot be matched.

    The alignment is monotone in arc length — structure is a *sequence*, so
    crossing matches are not a physical correspondence — and drift is charged
    beyond a small budget, which is the "limited deformation" part.  Every
    unmatched event costs ``gap_cost`` times the turning it carries, so a shape
    that has five real prongs and one that has none cannot tie.

    The cost is divided by one full turn of the boundary (2*pi), which is the
    total turning of any simple closed ring.  That denominator is a property of
    the plane, not of the pair being compared, so distances from different
    candidates are on one scale and a shape cannot look better by carrying less
    measurable structure.
    """
    a, b = left.events, right.events
    if not a and not b:
        return {"cost": 0.0, "distance": 0.0, "pairs": [], "unmatched_left": 0,
                "unmatched_right": 0, "offset": 0.0}

    # Both sequences are cut at their own strongest event, so the seam of the
    # cyclic parametrization falls on the corner that is least in doubt.  Trying
    # the few runner-up events of `b` covers shapes whose top corner is ambiguous.
    anchor_a = max(a, key=lambda event: event.magnitude).position if a else 0.0
    left_cut = _cut(a, anchor_a)
    trials = (
        sorted(range(len(b)), key=lambda index: -b[index].magnitude)[:max_offset_trials]
        if b
        else [None]
    )
    best: dict[str, Any] | None = None
    for trial in trials:
        if trial is None:  # nothing on the right: every event of a is a gap
            result = {
                "cost": float(gap_cost * sum(event.magnitude for event in a)),
                "pairs": [],
                "unmatched_left": len(a),
                "unmatched_right": 0,
            }
            result["offset"] = 0.0
        else:
            anchor_b = b[trial].position
            right_cut = _cut(b, anchor_b)
            result = _dp_align(left_cut, right_cut, gap_cost=gap_cost)
            result["offset"] = float((anchor_a - anchor_b) % 1.0)
        if best is None or result["cost"] < best["cost"]:
            best = result
    assert best is not None
    best["distance"] = float(best["cost"] / NORMALISING_TURN)
    return best


def _cut(events: tuple[SalientEvent, ...], anchor: float) -> tuple[SalientEvent, ...]:
    """Re-parametrise arc positions so ``anchor`` becomes 0, then keep the order."""
    shifted = [
        replace(event, position=(event.position - anchor) % 1.0) for event in events
    ]
    shifted.sort(key=lambda event: event.position)
    return tuple(shifted)


def _dp_align(
    a: tuple[SalientEvent, ...],
    b: tuple[SalientEvent, ...],
    *,
    gap_cost: float,
) -> dict[str, Any]:
    """Needleman-Wunsch over the two event sequences: match, or pay for a gap."""
    rows, cols = len(a), len(b)
    table = np.full((rows + 1, cols + 1), np.inf, dtype=float)
    table[0, 0] = 0.0
    for row in range(rows + 1):
        for col in range(cols + 1):
            if row == 0 and col == 0:
                continue
            if row > 0:  # this event of a is unmatched
                table[row, col] = min(
                    table[row, col], table[row - 1, col] + gap_cost * a[row - 1].magnitude
                )
            if col > 0:  # this event of b is unmatched (allowed even with a empty)
                table[row, col] = min(
                    table[row, col], table[row, col - 1] + gap_cost * b[col - 1].magnitude
                )
            if row > 0 and col > 0:  # the two events correspond
                left, right = a[row - 1], b[col - 1]
                correspondence = table[row - 1, col - 1] + _match_cost(
                    left, right, abs(left.position - right.position)
                )
                table[row, col] = min(table[row, col], correspondence)
    pairs = _trace(table, a, b, gap_cost=gap_cost)
    return {
        "cost": float(table[rows, cols]),
        "pairs": pairs,
        "unmatched_left": rows - len(pairs),
        "unmatched_right": cols - len(pairs),
    }


def _trace(
    table: np.ndarray,
    a: tuple[SalientEvent, ...],
    b: tuple[SalientEvent, ...],
    *,
    gap_cost: float,
) -> list[dict[str, Any]]:
    """Walk the DP table back to the matched pairs (for the figures and audits)."""
    row, col = table.shape[0] - 1, table.shape[1] - 1
    pairs: list[dict[str, Any]] = []
    while row > 0 or col > 0:
        current = table[row, col]
        if row > 0 and (
            abs(current - (table[row - 1, col] + gap_cost * a[row - 1].magnitude)) < 1e-12
        ):
            row -= 1
            continue
        if col > 0 and (
            abs(current - (table[row, col - 1] + gap_cost * b[col - 1].magnitude)) < 1e-12
        ):
            col -= 1
            continue
        if row > 0 and col > 0:
            drift = abs(a[row - 1].position - b[col - 1].position)
            cost = table[row - 1, col - 1] + _match_cost(a[row - 1], b[col - 1], drift)
            if abs(current - cost) < 1e-12:
                pairs.append(
                    {
                        "left": row - 1,
                        "right": col - 1,
                        "sign_match": a[row - 1].sign == b[col - 1].sign,
                        "drift": round(drift, 5),
                        "cost": round(_match_cost(a[row - 1], b[col - 1], drift), 6),
                    }
                )
                row -= 1
                col -= 1
                continue
        break  # pragma: no cover - a cell always has at least one legal predecessor
    return list(reversed(pairs))


def _mirror(points: np.ndarray) -> np.ndarray:
    """Reflect in the x-axis and restore the CCW walk, via the project's one mirror.

    This used to be ``points[:, ::-1] * [-1, 1]``, which looks like a reflection and
    is not: ``(x, y) -> (-y, x)`` has determinant +1, so it is a quarter turn.  The
    bug was invisible in the scores it produced (a rotation is already absorbed by
    the cyclic offset search, so ``allow_mirror`` changed nothing for a chiral pair:
    measured 0.6181 with the switch on and off) and would have made the mirror column
    of the experiment a no-op.  ``alignment.mirror_points`` is the same convention
    E0, E1 and E3 use, so ``allow_mirror`` means one thing across all four arms.
    """
    return mirror_points(points)


def distance(
    query: Any,
    candidate: Any,
    *,
    allow_mirror: bool = False,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    salience_k: float = SALIENCE_K,
    gap_cost: float = GAP_COST,
) -> tuple[float, dict[str, Any]]:
    """Structure distance between two contours, plus the transform it used."""
    left = signature(query, scales=scales, salience_k=salience_k)
    right = signature(candidate, scales=scales, salience_k=salience_k)
    aligned = align_sequences(left, right, gap_cost=gap_cost)
    result: dict[str, Any] = {
        "mirrored": False,
        "rotation_offset": aligned["offset"],
        "pairs": aligned["pairs"],
        "unmatched_left": aligned["unmatched_left"],
        "unmatched_right": aligned["unmatched_right"],
        "query_summary": left.summary(),
        "candidate_summary": right.summary(),
    }
    score = float(aligned["distance"])
    if allow_mirror:
        mirrored = signature(_mirror(check_contour(candidate, "candidate")),
                             scales=scales, salience_k=salience_k)
        flipped = align_sequences(left, mirrored, gap_cost=gap_cost)
        if flipped["distance"] < score:
            result.update(
                {
                    "mirrored": True,
                    "rotation_offset": flipped["offset"],
                    "pairs": flipped["pairs"],
                    "unmatched_left": flipped["unmatched_left"],
                    "unmatched_right": flipped["unmatched_right"],
                    "candidate_summary": mirrored.summary(),
                }
            )
            score = float(flipped["distance"])
    return score, result


def distance_library(
    query: Any,
    library: Any,
    *,
    allow_mirror: bool = False,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    salience_k: float = SALIENCE_K,
    gap_cost: float = GAP_COST,
) -> np.ndarray:
    rows = contour_stack(library, "library")
    return np.array(
        [
            distance(
                query,
                row,
                allow_mirror=allow_mirror,
                scales=scales,
                salience_k=salience_k,
                gap_cost=gap_cost,
            )[0]
            for row in rows
        ],
        dtype=float,
    )


POLICY = {
    "id": "E2",
    "name": "multi-scale salient structure matching (this project's design)",
    "score": "weighted sequence-alignment cost of signed salient turning events, "
    "in radians, divided by 2*pi (one full turn of the boundary). 0.0 = identical "
    "structure. The denominator is geometry, not the pair's own weight, so a shape "
    "cannot win by carrying less measurable structure (see the noise test)",
    "invariance": {
        "translation": "exact (chord angles and arc-length positions ignore position)",
        "uniform_scale": "exact (angles are scale-free and the salience bar is a "
        "turning-per-vertex of a circle, also scale-free; the shared standardizer is "
        "still applied so every method sees the same ring)",
        "rotation": "searched as a cyclic offset of the event sequences",
        "start_point": "same search as rotation: positions are arc-length fractions",
        "traversal_direction": "NOT invariant; rings arrive CCW, a reversal is a probe",
        "mirror": "only when allow_mirror=True",
        "pen_jitter": "down-weighted by geometry, not by a coefficient: a gap costs the "
        "turning an event carries, so a 0.3 rad wobble is a sixth of a 1.8 rad prong, "
        "and the bar (3x the accumulated turning of a circle over the same window) "
        "decides what counts as a corner at all. Measured on a unit-RMS disk: 0.2% "
        "radial noise raises no event at all (distance exactly 0.0), 0.5% raises one "
        "0.505 rad event worth 0.0805 against 2.8610 for circle-vs-star. persistence "
        "is reported per event so a single-window detection can be audited but is not "
        "multiplied into the score; doing that was tried on this checkpoint's shapes "
        "and it is a rescaling rather than a filter — pairs whose corners share a "
        "lifetime were unchanged to 4 decimals (worn star, pentagon, the 0.5% noise "
        "disk) while star-vs-five-lobed went 1.5045 -> 5.3802",
    },
    "not_a_corner_count": "the score charges signed turning magnitude, convex-vs-concave "
    "sign (a prong matched to a valley pays both turns), ordered position drift beyond a "
    "0.02 budget, how pointed each matched corner is (PEAK_GAIN=0.25, so worn tips are a "
    "real but second-order difference rather than a false identity) and unmatched events; "
    "two shapes with the same event count but a different arrangement score differently "
    "(tested)",
    "scales": [round(value, 6) for value in DEFAULT_SCALES],
    "cost": "two signature extractions (O(n * scales)) plus O(max_offset_trials * n_events^2) "
    "per pair; the DP is the slow part and runs on tens of events, not on 128 vertices",
    "new_code_in_this_checkpoint": True,
}


def describe_policy() -> dict[str, Any]:
    return dict(POLICY)


def score_library(
    query: Any,
    library: Any,
    *,
    allow_mirror: bool = False,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    salience_k: float = SALIENCE_K,
    gap_cost: float = GAP_COST,
    **_: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    rows = contour_stack(library, "library")
    scores = np.empty(rows.shape[0], dtype=float)
    transforms: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        value, fitted = distance(
            query,
            row,
            allow_mirror=allow_mirror,
            scales=scales,
            salience_k=salience_k,
            gap_cost=gap_cost,
        )
        scores[index] = value
        transforms.append(
            {
                "mirrored": fitted["mirrored"],
                "rotation_offset": fitted["rotation_offset"],
                "unmatched_left": fitted["unmatched_left"],
                "unmatched_right": fitted["unmatched_right"],
                "query_events": fitted["query_summary"]["events"],
                "candidate_events": fitted["candidate_summary"]["events"],
                "pairs": fitted["pairs"],
            }
        )
    return scores, {"policy": describe_policy(), "transforms": transforms}
