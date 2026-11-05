"""Tests for hand-drawn query abstraction (P0-4, Task A).

Two families of guarantees:

* the output satisfies the library contour contract, so a query and a candidate
  are comparable by construction, and every canvas property (position, size,
  sample count, sample spacing, pixel orientation) is provably removed;
* the rejections are real rejections — an open stroke, a line, an ambiguous
  image or a structurally self-crossed outline must not be quietly turned into a
  plausible lake.

Smoothing is asserted to be switchable, deterministic and *bounded* in what it
removes: the notch-depth tests below quantify how much real structure each level
costs, which is the point of the ablation rather than a side note.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from earthglyph.features.fourier import harmonic_amplitudes
from earthglyph.geometry import DEFAULT_N_POINTS, arc_length_resample
from earthglyph.geometry.resampling import signed_area
from earthglyph.geometry.sketch import (
    MATERIAL_CROSS_AREA_REL,
    OPEN_REJECT_REL,
    OPEN_STROKE_SOLIDITY,
    OPEN_STROKE_WIDTH_REL,
    SEAM_PERIMETER_SHARE,
    SMOOTHING_SIGMA,
    SketchError,
    close_trace,
    contour_from_image,
    contour_from_points,
    contour_from_sketch,
    dedupe_points,
    load_sketch_json,
    smooth_cyclic,
    trace_scale,
)

cv2 = pytest.importorskip("cv2", reason="image input needs OpenCV")


def jittered_oval(scale: float = 100.0, seed: int = 3, n: int = 300) -> np.ndarray:
    """An asymmetric outline drawn with a shaky pen on a canvas."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = 1.0 + 0.28 * np.cos(t) + 0.16 * np.sin(2 * t) + 0.09 * np.cos(3 * t + 1.1)
    points = np.column_stack(
        [scale * radius * np.cos(t) + 4 * scale, scale * 0.75 * radius * np.sin(t) + 3 * scale]
    )
    return points + rng.normal(0.0, 0.012 * scale, points.shape)


def notched_circle(scale: float = 100.0, n: int = 700) -> np.ndarray:
    """A circle with a V-shaped bite taken out of one side (a real concavity)."""
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    depth = np.where((t > 1.0) & (t < 1.5), -0.35 * np.sin(np.pi * (t - 1.0) / 0.5), 0.0)
    radius = scale * (1.0 + depth)
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


def pentagram(scale: float = 100.0) -> np.ndarray:
    """A five-pointed star drawn as one self-crossing stroke."""
    angle = np.arange(0, 10) * (2.0 * np.pi / 10.0)
    radius = np.where(angle % (4.0 * np.pi / 10.0) < 1e-9, scale, scale * 0.38)
    order = np.array([0, 2, 4, 6, 8, 1, 3, 5, 7, 9, 0])
    points = np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])
    return points[order]


def best_shift_rms(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest pointwise RMS offset over cyclic starts (the alignment the
    retrieval stage performs, so tests compare shapes, not bookkeeping)."""
    return min(
        float(np.sqrt(np.mean(np.sum((a - np.roll(b, shift, axis=0)) ** 2, axis=1))))
        for shift in range(a.shape[0])
    )


def reference_contour(points: np.ndarray, n: int = 128) -> np.ndarray:
    from earthglyph.geometry import arc_length_resample, ensure_ccw, normalize_contour, open_ring

    return normalize_contour(arc_length_resample(ensure_ccw(open_ring(points)), n))


# --- the contour contract -----------------------------------------------------


def test_query_contour_satisfies_the_library_contract() -> None:
    query = contour_from_points(jittered_oval(), smoothing="none")
    assert query.points.shape == (128, 2)
    assert np.abs(query.points.mean(axis=0)).max() < 1e-12
    assert abs(float(np.sqrt(np.mean(np.sum(query.points**2, axis=1)))) - 1.0) < 1e-12
    assert query.winding == "ccw"
    assert signed_area(query.points) > 0.0
    assert query.source == "trace"


def _filled_png() -> np.ndarray:
    image = np.full((300, 300), 255, np.uint8)
    cv2.circle(image, (150, 150), 90, 0, -1)
    return image


def test_units_are_recorded_for_audit() -> None:
    assert contour_from_points(jittered_oval(), unit="canvas").unit == "canvas"
    assert contour_from_image(_filled_png()).unit == "pixel"


@pytest.mark.parametrize("n_points", [8, 17, 40, 300, 900])
def test_any_point_count_produces_the_same_shape(n_points: int) -> None:
    """The device decides how many samples; the contour must not depend on it."""
    t = np.linspace(0.0, 2.0 * np.pi, n_points, endpoint=False)
    radius = 1.0 + 0.3 * np.cos(t) + 0.12 * np.sin(3 * t + 0.7)
    trace = np.column_stack([radius * np.cos(t), radius * np.sin(t)])
    if n_points > 3:
        trace = np.vstack([trace, trace[:1]])
    query = contour_from_points(trace, smoothing="none")
    reference = reference_contour(trace)
    assert best_shift_rms(query.points, reference) < 5e-9
    assert harmonic_amplitudes(query.points, 4)[0] == pytest.approx(
        harmonic_amplitudes(reference, 4)[0], rel=1e-3 if n_points >= 40 else 5e-2
    )


def test_canvas_translation_and_scale_are_removed() -> None:
    base = jittered_oval(100.0)
    left = contour_from_points(base, smoothing="light")
    right = contour_from_points(base * 3.7 + np.array([900.0, -450.0]), smoothing="light")
    assert np.max(np.abs(left.points - right.points)) < 1e-9


def test_non_uniform_sample_spacing_becomes_uniform_arc_length() -> None:
    dense = np.linspace(0.0, 0.6 * np.pi, 400, endpoint=False)
    sparse = np.linspace(0.6 * np.pi, 2.0 * np.pi, 24, endpoint=False)
    angle = np.concatenate([dense, sparse, [2.0 * np.pi]])
    trace = np.column_stack([100.0 * np.cos(angle), 100.0 * np.sin(angle)])
    query = contour_from_points(trace, smoothing="none")

    def spread(points: np.ndarray) -> float:
        step = np.linalg.norm(np.diff(np.vstack([points, points[:1]]), axis=0), axis=1)
        return float(step.std() / step.mean())

    assert spread(trace) > 1.0  # the stroke is sampled over 40x denser in one arc
    assert spread(query.points) < 5e-3
    assert spread(query.points) < spread(trace) / 100.0


def test_repeated_points_are_removed_but_shape_is_kept() -> None:
    trace = jittered_oval(80.0, n=120)
    stuttered = np.repeat(trace, 4, axis=0)
    query = contour_from_points(stuttered, smoothing="none")
    assert query.raw_points.shape[0] == len(stuttered)
    assert query.outline_points.shape[0] < query.raw_points.shape[0]
    reference = contour_from_points(trace, smoothing="none")
    assert best_shift_rms(query.points, reference.points) < 1e-9
    assert dedupe_points(stuttered).shape[0] == trace.shape[0]


def test_trace_scale_is_the_only_yardstick() -> None:
    small = jittered_oval(10.0, n=120)
    large = jittered_oval(400.0, n=120)
    assert trace_scale(large) / trace_scale(small) == pytest.approx(40.0, rel=0.3)


# --- closure, openness and degeneracy ----------------------------------------


def test_tiny_gap_is_closed_and_recorded() -> None:
    trace = jittered_oval(100.0)
    trace[-1] = trace[0] + np.array([0.5, 0.0])
    query = contour_from_points(trace, smoothing="none")
    assert query.closure["gap_rel"] == pytest.approx(0.005, abs=2e-3)
    assert query.closure["auto_closed"] == 1.0
    assert query.self_intersection["is_simple"] or not query.self_intersection["material"]


def test_moderate_gap_is_closed_with_an_explicit_warning() -> None:
    trace = jittered_oval(100.0)
    trace[-1] = trace[0] + np.array([6.0, 0.0])
    query = contour_from_points(trace, smoothing="none")
    assert any("sealed by a straight chord" in note for note in query.warnings)
    assert "assumption" in " ".join(query.warnings)


def test_obviously_open_stroke_is_refused_not_sealed() -> None:
    t = np.linspace(0.0, 1.6 * np.pi, 200)
    trace = np.column_stack([100.0 * np.cos(t), 100.0 * np.sin(t)])
    with pytest.raises(SketchError, match="redraw"):
        contour_from_points(trace, closed=None, smoothing="none")


def inlet_ring(n_draw: int, *, n_inlets: int = 16, depth: float = 0.3) -> np.ndarray:
    """A many-inleted closed curve, long of perimeter for its radius.

    Real reservoirs look like this — library row 294 has a perimeter of 15.6 RMS
    radii — and such a curve is what breaks a purely scale-relative closure test:
    resampled to 128 points, its wrap chord alone is 0.196 of the shape scale.
    """
    t = np.linspace(0.0, 2.0 * np.pi, n_draw, endpoint=False)
    radius = 100.0 * (1.0 + depth * np.cos(n_inlets * t))
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


def test_a_dense_ring_is_closed_by_its_own_seam_not_by_luck() -> None:
    ring = arc_length_resample(inlet_ring(4000), DEFAULT_N_POINTS)
    outline, closure, notes = close_trace(dedupe_points(ring))
    # the scale test alone would have refused this ring: it is closed by sampling
    assert closure["gap_rel"] > OPEN_REJECT_REL
    assert closure["seam_closed"] == 1.0 and closure["auto_closed"] == 1.0
    assert closure["gap"] == pytest.approx(closure["median_step"], rel=0.05)
    assert any("wrap chord" in note for note in notes)
    assert outline.shape == (ring.shape[0] - 1, 2)

    query = contour_from_points(ring, smoothing="none")
    assert query.closure["seam_closed"] == 1.0
    assert any("wrap chord" in note for note in query.warnings)
    assert query.points.shape == (DEFAULT_N_POINTS, 2)


def test_the_closing_vertex_is_a_chord_not_geometry() -> None:
    """An error confined to the last sample cannot change the extracted contour.

    A closed trace's final vertex is dropped and the ring closes with a chord
    (``close_trace`` line: ``outline = points[:-1]``), because the return to the
    start is implied by the declaration ``closed = true``, not drawn. So a probe
    that displaces only that vertex measures the refusal limit and nothing else —
    this is how the P0-4 gap probes were found to be no-ops until the
    perturbation was spread over a tail of samples. The tail variant below is the
    one that actually moves the contour.
    """
    ring = arc_length_resample(jittered_oval(), 240)
    base = contour_from_points(ring, closed=True, smoothing="none").points
    scale = trace_scale(ring)
    direction = ring[-1] - ring[-2]
    direction = direction / float(np.linalg.norm(direction))

    one = ring.copy()
    one[-1] = one[-1] + direction * (0.05 * scale)
    assert float(np.linalg.norm(one[-1] - one[0])) / scale < OPEN_REJECT_REL
    one_points = contour_from_points(one, closed=True, smoothing="none").points
    assert np.array_equal(one_points, base)

    tail = ring.copy()
    tail[-13:] = tail[-13:] + np.outer(np.linspace(0.0, 1.0, 13), direction * (0.05 * scale))
    tail_points = contour_from_points(tail, closed=True, smoothing="none").points
    moved = float(np.abs(tail_points - base).max())
    assert moved > 0.01
    assert best_shift_rms(tail_points, base) > 0.0


def test_the_seam_limit_also_needs_a_perimeter_share() -> None:
    """One missing vertex of a 12-gon is exactly one step long, yet not a seam.

    The sample-spacing test on its own would seal it, because the gap *is* one
    chord; what makes it a real hole is that the chord is 1/12 of the perimeter,
    so the seal would fabricate that much region. Both limits are therefore
    required and the smaller one governs.
    """
    t = np.linspace(0.0, 2.0 * np.pi * (1.0 - 1.0 / 12.0), 12)
    trace = np.column_stack([100.0 * np.cos(t), 100.0 * np.sin(t)])
    gap = float(np.linalg.norm(trace[0] - trace[-1]))
    steps = np.linalg.norm(np.diff(trace, axis=0), axis=1)
    step = float(np.median(steps))
    assert gap == pytest.approx(step, rel=1e-9)  # one step, by construction
    with pytest.raises(SketchError, match="open stroke"):
        close_trace(trace)
    assert gap > SEAM_PERIMETER_SHARE * (float(steps.sum()) + gap)


def test_a_dense_open_arc_cannot_use_the_seam_rule() -> None:
    """The seam allowance shrinks with the step size, so an open arc stays open.

    338 degrees traced at 128 points has a gap of 0.38 shape scales — eight times
    its own step — and the refusal still quotes the classic 0.15 limit, which is
    how one tells that the seam rule widened nothing here.
    """
    t = np.linspace(0.0, 5.9, 128)
    trace = np.column_stack([np.cos(t), np.sin(t)])
    with pytest.raises(SketchError, match=r"limit 0\.15"):
        close_trace(trace)
    gap = float(np.linalg.norm(trace[0] - trace[-1]))
    step = float(np.median(np.linalg.norm(np.diff(trace, axis=0), axis=1)))
    assert gap / step == pytest.approx(8.2, abs=0.3)


def test_declared_open_but_seamed_trace_is_still_closed_and_announced() -> None:
    ring = arc_length_resample(inlet_ring(4000), DEFAULT_N_POINTS)
    outline, closure, notes = close_trace(ring, closed=False)
    assert closure["auto_closed"] == 1.0
    assert any("declared open" in note for note in notes)
    assert outline.shape[0] == ring.shape[0] - 1


def test_the_same_pixel_gap_is_judged_against_the_shape_not_the_canvas() -> None:
    t = np.linspace(0.0, 2.0 * np.pi, 200, endpoint=False)
    for scale, expect_error in ((400.0, False), (20.0, True)):
        trace = np.column_stack([scale * np.cos(t), scale * np.sin(t)])
        trace[-1] = trace[0] + np.array([8.0, 0.0])  # the same absolute gap
        if expect_error:
            with pytest.raises(SketchError, match="open stroke"):
                close_trace(dedupe_points(trace))
        else:
            outline, closure, _ = close_trace(dedupe_points(trace))
            assert closure["auto_closed"] == 1.0
            assert outline.shape[0] == trace.shape[0] - 1


def test_declared_open_trace_is_refused() -> None:
    t = np.linspace(0.0, 2.0 * np.pi, 120, endpoint=False)
    trace = np.column_stack([100.0 * np.cos(t), 100.0 * np.sin(t)])
    trace[-1] = trace[0] + np.array([40.0, 0.0])
    with pytest.raises(SketchError, match="open stroke"):
        contour_from_points(trace, closed=False, smoothing="none")


def test_a_line_drawn_back_on_itself_is_degenerate() -> None:
    forward = np.column_stack([np.linspace(0.0, 90.0, 12), np.zeros(12)])
    back = np.column_stack([np.linspace(90.0, 0.0, 12), np.full(12, 0.02)])
    with pytest.raises(SketchError, match="no area"):
        contour_from_points(np.vstack([forward, back]), closed=True, smoothing="none")


def test_too_few_points_is_rejected() -> None:
    with pytest.raises(SketchError, match="at least 3 points"):
        contour_from_points([[0, 0], [1, 1]], closed=True)
    with pytest.raises(SketchError, match="too short"):
        contour_from_points([[0, 0], [10, 0], [10, 9], [0, 9], [3, 3]], closed=True)


def test_non_finite_and_wrong_shape_inputs_are_rejected() -> None:
    with pytest.raises(SketchError, match="non-finite"):
        contour_from_points([[0, 0], [1, float("nan")], [2, 1], [1, 2], [0, 1]], closed=True)
    with pytest.raises(SketchError, match="shape"):
        contour_from_points(np.zeros((5, 3)))


# --- self-intersection --------------------------------------------------------


def test_structural_self_crossing_is_refused_by_default() -> None:
    with pytest.raises(SketchError, match="self-intersecting"):
        contour_from_points(pentagram(), closed=True, smoothing="none")


def test_structural_self_crossing_is_usable_when_the_caller_opts_in() -> None:
    query = contour_from_points(
        pentagram(), closed=True, smoothing="none", allow_self_intersection=True
    )
    assert query.self_intersection["material"]
    assert query.self_intersection["region_impact"] > MATERIAL_CROSS_AREA_REL
    assert any("material self-crossing" in note for note in query.warnings)
    # the crossing is not repaired away: the drawn curve is still what is matched
    assert query.n_points == 128


def test_a_pen_wobble_loop_is_kept_and_announced() -> None:
    trace = notched_circle(100.0)
    rng = np.random.default_rng(11)
    trace = trace + rng.normal(0.0, 1.2, trace.shape)
    loop = np.vstack([trace[:90], trace[89] + [3.0, 3.0], trace[89] + [-2.0, 4.0], trace[90:]])
    query = contour_from_points(loop, closed=True, smoothing="none")
    if query.self_intersection["count"]:
        assert not query.self_intersection["material"]
        assert any("pen wobble" in note for note in query.warnings)


# --- smoothing ----------------------------------------------------------------


def test_smoothing_levels_are_named_switchable_and_reproducible() -> None:
    trace = jittered_oval(120.0)
    first = contour_from_points(trace, smoothing="light")
    again = contour_from_points(trace, smoothing="light")
    assert np.array_equal(first.points, again.points)
    assert first.smoothing == "light" and first.smoothing_sigma == SMOOTHING_SIGMA["light"]
    assert contour_from_points(trace, smoothing="none").smoothing_sigma == 0.0
    assert contour_from_points(trace, smoothing=0.35).smoothing_sigma == 0.35
    with pytest.raises(SketchError, match="unknown smoothing level"):
        contour_from_points(trace, smoothing="heavy")
    with pytest.raises(SketchError, match="sigma must be"):
        contour_from_points(trace, smoothing=-1.0)


def test_smoothing_attenuates_high_harmonics_monotonically() -> None:
    trace = jittered_oval(120.0)
    fd = {
        level: harmonic_amplitudes(contour_from_points(trace, smoothing=level).points, 16)
        for level in ("none", "light", "medium")
    }
    for level in ("light", "medium"):
        assert fd[level][15] < fd["none"][15]
    assert fd["medium"][15] < fd["light"][15]
    # the low band that carries the overall shape barely moves under light smoothing
    assert abs(fd["light"][0] - fd["none"][0]) / fd["none"][0] < 0.01
    assert abs(fd["light"][15] - fd["none"][15]) / fd["none"][15] < 0.35


def test_light_smoothing_keeps_the_concavity_medium_smoothing_fills_it() -> None:
    """The cost of smoothing is measured on real structure, not on prettiness."""
    trace = notched_circle(100.0)
    radius_at = {}
    for level in ("none", "light", "medium"):
        points = contour_from_points(trace, smoothing=level).points
        angle = np.arctan2(points[:, 1], points[:, 0])
        sector = (angle > 0.9) & (angle < 1.6)
        radius_at[level] = float(np.sqrt(np.sum(points[sector] ** 2, axis=1)).min())
    assert radius_at["none"] < radius_at["light"] < radius_at["medium"]
    assert radius_at["light"] - radius_at["none"] < 0.03
    assert radius_at["medium"] - radius_at["none"] > radius_at["light"] - radius_at["none"]


def test_smoothing_preserves_area_and_does_not_move_the_start() -> None:
    trace = notched_circle(100.0)
    none = contour_from_points(trace, smoothing="none")
    medium = contour_from_points(trace, smoothing="medium")
    area_none = abs(signed_area(none.points))
    area_medium = abs(signed_area(medium.points))
    assert abs(area_medium - area_none) / area_none < 2e-3
    assert np.linalg.norm(none.points[0] - medium.points[0]) < 0.05


def test_smooth_cyclic_is_zero_phase_on_a_uniform_grid() -> None:
    n = 128
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    circle = np.column_stack([np.cos(angle), np.sin(angle)])
    z = circle[:, 0] + 1j * circle[:, 1]
    # a pure first harmonic is an eigenfunction of the cyclic Gaussian: only its
    # amplitude may change, never its phase (start point) or its DC term
    smoothed = smooth_cyclic(circle, sigma=2.0)
    assert np.abs(smoothed.mean(axis=0)).max() < 1e-12
    assert np.argmax(smoothed[:, 0]) == 0
    ratio = np.linalg.norm(smoothed[:, 0] + 1j * smoothed[:, 1]) / np.linalg.norm(z)
    assert ratio < 1.0
    assert abs(np.angle((smoothed[:, 0] + 1j * smoothed[:, 1])[0] / z[0])) < 1e-12


# --- image input --------------------------------------------------------------


def _draw_stroke(
    points: np.ndarray, width: int = 6, invert: bool = False, closed: bool = True
) -> np.ndarray:
    image = np.zeros((400, 520), np.uint8) if invert else np.full((400, 520), 255, np.uint8)
    ink = 255 if invert else 0
    cv2.polylines(image, [np.round(points).astype(np.int32)], closed, ink, width, cv2.LINE_AA)
    return image


def _draw_filled(points: np.ndarray, invert: bool = False) -> np.ndarray:
    image = np.zeros((400, 520), np.uint8) if invert else np.full((400, 520), 255, np.uint8)
    cv2.fillPoly(image, [np.round(points).astype(np.int32)], 255 if invert else 0)
    return image


def _geometry_truth(canvas_points: np.ndarray) -> np.ndarray:
    """The same shape in y-up geometry coordinates, standardized."""
    return reference_contour(np.column_stack([canvas_points[:, 0], 399 - canvas_points[:, 1]]))


def test_stroked_outline_is_read_as_its_centre_line() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    radius = 1 + 0.28 * np.cos(angle) + 0.16 * np.sin(2 * angle) + 0.09 * np.cos(3 * angle + 1.1)
    drawn = np.column_stack([260 + 120 * radius * np.cos(angle), 200 + 90 * radius * np.sin(angle)])
    query = contour_from_image(_draw_stroke(drawn, width=6), smoothing="none")
    assert query.image_info["representation"].startswith("stroke mid-line")
    assert best_shift_rms(query.points, _geometry_truth(drawn)) < 0.05


@pytest.mark.parametrize("width", [3, 6, 12])
def test_line_width_does_not_become_geometry(width: int) -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    drawn = np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)])
    query = contour_from_image(_draw_stroke(drawn, width=width), smoothing="none")
    truth = _geometry_truth(drawn)
    assert best_shift_rms(query.points, truth) < 0.05
    assert query.image_info["stroke_width_px"] == pytest.approx(width + 1.0, abs=1.6)


def test_an_unclosed_stroke_in_an_image_is_refused_not_guessed_shut() -> None:
    """A stroked arc leaves a *closed* pixel band, so nothing geometric forces a
    refusal — but reading it as a shape would invent an outline the user never drew.
    The band statistics say "pen stroke", and the answer is a redraw request."""
    angle = np.linspace(0.0, 1.5 * np.pi, 240)
    arc = np.column_stack([260 + 120 * np.cos(angle), 200 + 120 * np.sin(angle)])
    image = _draw_stroke(arc, width=4, closed=False)
    with pytest.raises(SketchError, match="does\\s+not close|not an outline"):
        contour_from_image(image)


def test_a_genuinely_thin_filled_shape_survives_the_stroke_test() -> None:
    """Both statistics have to trip, so a shape that is merely thin stays usable: a
    180×3 ellipse is a narrower band than a 4-pixel pen stroke, but it fills its own
    convex hull (solidity ~1) while a stroked arc leaves most of its hull empty."""
    angle = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    ellipse = np.column_stack([260 + 180 * np.cos(angle), 200 + 3 * np.sin(angle)])
    thin = contour_from_image(_draw_filled(ellipse), smoothing="none")
    assert thin.image_info["representation"] == "outer edge of a filled region"
    assert thin.image_info["band_width_rel"] < OPEN_STROKE_WIDTH_REL
    assert thin.image_info["fill_solidity"] > OPEN_STROKE_SOLIDITY


def test_filled_region_is_read_from_its_outer_edge() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    radius = 1 + 0.3 * np.cos(angle)
    drawn = np.column_stack([260 + 120 * radius * np.cos(angle), 200 + 90 * radius * np.sin(angle)])
    query = contour_from_image(_draw_filled(drawn), smoothing="none")
    assert query.image_info["representation"] == "outer edge of a filled region"
    assert best_shift_rms(query.points, _geometry_truth(drawn)) < 0.03


def test_inverted_polarity_is_handled() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 300, endpoint=False)
    drawn = np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)])
    light = contour_from_image(_draw_stroke(drawn), smoothing="none")
    dark = contour_from_image(_draw_stroke(drawn, invert=True), smoothing="none")
    assert light.image_info["polarity"] != dark.image_info["polarity"]
    assert best_shift_rms(light.points, dark.points) < 1e-9


def test_image_coordinates_are_flipped_to_geometry_coordinates() -> None:
    """A shape drawn counter-clockwise on screen must come out CCW, and a mirrored
    drawing of a chiral outline must stay distinguishable from the original."""
    angle = np.linspace(0.0, 2.0 * np.pi, 300, endpoint=False)
    radius = 1 + 0.3 * np.cos(angle) + 0.18 * np.sin(2 * angle)
    drawn = np.column_stack([260 + 120 * radius * np.cos(angle), 200 + 90 * radius * np.sin(angle)])
    query = contour_from_image(_draw_stroke(drawn), smoothing="none")
    assert query.winding == "ccw"
    assert best_shift_rms(query.points, _geometry_truth(drawn)) < 0.05
    mirrored = np.column_stack([520 - drawn[:, 0], drawn[:, 1]])
    mirror_query = contour_from_image(_draw_stroke(mirrored), smoothing="none")
    assert best_shift_rms(query.points, mirror_query.points) > 0.08


def test_anti_aliasing_does_not_add_boundary_detail() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 60, endpoint=False)
    drawn = np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)])
    smooth_ink = contour_from_image(_draw_stroke(drawn, width=5), smoothing="none")
    fd = harmonic_amplitudes(smooth_ink.points, 16)
    assert fd[15] < 0.02 * fd[0]


def test_several_shapes_are_refused_instead_of_guessing() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 200, endpoint=False)
    image = _draw_stroke(np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)]))
    cv2.circle(image, (60, 60), 28, 0, -1)
    with pytest.raises(SketchError, match="separate shapes"):
        contour_from_image(image)


def test_lettering_is_refused() -> None:
    image = np.full((120, 400), 255, np.uint8)
    cv2.putText(image, "LAKE", (5, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    with pytest.raises(SketchError, match="separate shapes"):
        contour_from_image(image)


def test_flat_and_blank_images_are_refused() -> None:
    with pytest.raises(SketchError, match="almost flat"):
        contour_from_image(np.full((80, 80), 128, np.uint8))
    with pytest.raises(SketchError, match="almost flat|no ink|cannot separate"):
        contour_from_image(np.full((80, 80), 255, np.uint8))
    noisy = np.random.default_rng(1).integers(0, 24, (80, 80), dtype=np.uint8)
    expected = "almost flat|separate shapes|cannot separate|no closed boundary"
    with pytest.raises(SketchError, match=expected):
        contour_from_image(noisy)


def test_image_input_reports_pixel_provenance() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 200, endpoint=False)
    drawn = np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)])
    query = contour_from_image(_draw_stroke(drawn), smoothing="light")
    payload = query.to_dict()
    assert payload["source"] == "image"
    assert payload["unit"] == "pixel"
    assert payload["image"]["canvas"] == [400, 520]
    assert 0 < payload["image"]["quantization_rel"] < 0.02
    json.dumps(payload)


# --- input plumbing -----------------------------------------------------------


def test_load_sketch_json_accepts_the_documented_shapes(tmp_path: Any) -> None:
    written = tmp_path / "sketch.json"
    payload = json.dumps({"points": [[0, 0], [10, 0], [10, 9], [0, 9]], "closed": True})
    written.write_text(payload, encoding="utf-8")
    points, closed = load_sketch_json(written)
    assert closed is True
    assert points.shape == (4, 2)
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([[0, 0], [10, 0], [10, 9]]), encoding="utf-8")
    assert load_sketch_json(bare)[1] is None
    strokes = tmp_path / "two.json"
    strokes.write_text(
        json.dumps({"strokes": [[[0, 0], [1, 1], [2, 0]], [[0, 0], [1, 1], [2, 0]]]}),
        encoding="utf-8",
    )
    with pytest.raises(SketchError, match="one closed outline"):
        load_sketch_json(strokes)


def test_contour_from_sketch_dispatches_on_the_file_type(tmp_path: Any) -> None:
    trace_path = tmp_path / "trace.json"
    payload = json.dumps({"points": notched_circle(100.0).tolist(), "closed": True})
    trace_path.write_text(payload, encoding="utf-8")
    from_trace = contour_from_sketch(trace_path, smoothing="none")
    assert from_trace.source == "trace"

    png_path = tmp_path / "drawing.png"
    angle = np.linspace(0.0, 2.0 * np.pi, 200, endpoint=False)
    drawn = np.column_stack([260 + 120 * np.cos(angle), 200 + 90 * np.sin(angle)])
    ok, buffer = cv2.imencode(".png", _draw_stroke(drawn))
    assert ok
    buffer.tofile(str(png_path))
    from_image = contour_from_sketch(png_path, smoothing="none")
    assert from_image.source == "image"

    other = tmp_path / "notes.md"
    other.write_text("# nothing", encoding="utf-8")
    with pytest.raises(SketchError, match="unsupported query file"):
        contour_from_sketch(other)


def test_diagnostics_survive_json_serialisation() -> None:
    query = contour_from_points(jittered_oval(90.0), smoothing="medium")
    payload = json.loads(json.dumps(query.to_dict()))
    assert payload["smoothing"] == "medium"
    assert payload["closure"]["gap_rel"] >= 0.0
    assert payload["n_outline_points"] <= payload["n_raw_points"]
    assert math.isfinite(payload["outline_perimeter"])
