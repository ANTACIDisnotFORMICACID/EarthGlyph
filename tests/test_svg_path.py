"""Tests for the SVG path decoder (P0-4.5, external silhouette assets).

The decoder is the only way a third-party vector asset enters the gallery, so a
silent geometric error here would put *wrong shapes* into a dataset whose whole
purpose is to be checked by eye. The assertions are therefore about exact
geometry — where a curve passes through, what radius an arc has, which ring
contains which — not merely about "it did not raise".

One test is a regression guard for a real bug: the cubic basis once multiplied
the control points by an extra 3 on top of the Bernstein basis' own 3, which
inflated every curved glyph past its viewBox and made clean icons fail the
sketch extractor as "self-intersecting strokes". The midpoint assertions below
are the ones that catch that.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from earthglyph.data.svg_path import (
    SvgPathError,
    decode_path,
    exterior_subpaths,
    flip_y,
    subpath_signed_areas,
    svg_invisible_elements,
    svg_subpaths,
    tokenize,
    view_box,
)


def points(subpath) -> np.ndarray:
    return np.asarray(subpath.points, dtype=float)


def at(subpath, index: int) -> np.ndarray:
    """The point at a sample index, counting back from the command's own samples."""
    return points(subpath)[index]


# --- tokenising ------------------------------------------------------------------


def test_tokenize_splits_numbers_from_commands_and_accepts_compressions():
    assert tokenize("M0 0H10V10z") == ["M", "0", "0", "H", "10", "V", "10", "z"]
    # exponent form, leading dot, and a number ending in a decimal point
    assert tokenize("L1.5e2 .5-3 4.") == ["L", "1.5e2", ".5", "-3", "4."]


def test_tokenize_rejects_non_string():
    with pytest.raises(SvgPathError, match="must be a string"):
        tokenize(None)  # type: ignore[arg-type]


# --- straight lines --------------------------------------------------------------


def test_closed_square_repeats_its_first_vertex_and_reports_closed():
    (sub,) = decode_path("M 10 20 L 90 20 L 90 80 L 10 80 Z")
    assert sub.closed is True
    ring = points(sub)
    assert ring[0] == pytest.approx(ring[-1])
    # the four corners appear in tracing order, and nothing else is emitted
    assert [tuple(row) for row in ring] == [(10.0, 20.0), (90.0, 20.0), (90.0, 80.0),
                                            (10.0, 80.0), (10.0, 20.0)]


def test_h_and_v_move_only_one_axis():
    (sub,) = decode_path("M 0 0 H 5 V 7 h -2")
    assert [tuple(row) for row in points(sub)[1:]] == [(5.0, 0.0), (5.0, 7.0), (3.0, 7.0)]
    # the capital forms are absolute: H -2 is x = -2, not a step of -2
    (absolute,) = decode_path("M 0 0 H 5 V 7 H -2")
    assert points(absolute)[-1] == pytest.approx([-2.0, 7.0])


def test_lowercase_is_relative_to_the_current_point():
    (sub,) = decode_path("m 10 10 l 20 0 l 0 20 h -20 z")
    assert [tuple(row) for row in points(sub)] == [(10.0, 10.0), (30.0, 10.0),
                                                   (30.0, 30.0), (10.0, 30.0),
                                                   (10.0, 10.0)]


def test_implicit_repeat_after_move_is_a_lineto_not_a_new_subpath():
    subpaths = decode_path("M 0 0 10 10 20 0")
    assert len(subpaths) == 1
    assert [tuple(row) for row in points(subpaths[0])] == [(0.0, 0.0), (10.0, 10.0),
                                                           (20.0, 0.0)]


def test_second_move_opens_a_second_subpath():
    subpaths = decode_path("M 0 0 L 4 0 Z M 10 10 L 14 10")
    assert [sub.closed for sub in subpaths] == [True, False]
    assert points(subpaths[1])[0] == pytest.approx([10.0, 10.0])


def test_z_closes_to_the_subpath_origin_not_to_the_last_point():
    (sub,) = decode_path("M 3 4 L 9 4 L 9 9 Z")
    assert points(sub)[-1] == pytest.approx([3.0, 4.0])


# --- curves: the exact geometry --------------------------------------------------


def test_cubic_midpoint_uses_each_control_point_exactly_once():
    """B(0.5) = (P0 + 3P1 + 3P2 + P3) / 8 — the regression guard.

    With the old double-scaled basis this point lands at (3.0, 4.5) instead of
    (1.0, 1.5): the same curve, three times too tall. That is what turned a
    256-unit icon into a 700-unit blob.
    """
    (sub,) = decode_path("M 0 0 C 0 2 2 2 2 0", segments=2)
    ring = points(sub)
    assert ring[0] == pytest.approx([0.0, 0.0])
    assert ring[1] == pytest.approx([1.0, 1.5], abs=1e-12)
    assert ring[-1] == pytest.approx([2.0, 0.0])


def test_cubic_stays_inside_its_control_hull():
    control = np.array([[0.0, 0.0], [0.0, 8.0], [8.0, 8.0], [8.0, 0.0]])
    (sub,) = decode_path("M 0 0 C 0 8 8 8 8 0", segments=48)
    ring = points(sub)
    assert ring[:, 1].max() <= control[:, 1].max() + 1e-12
    assert ring[:, 0].min() >= control[:, 0].min() - 1e-12


def test_quadratic_midpoint_is_the_exact_parabola_point():
    """B(0.5) = 0.25 P0 + 0.5 C + 0.25 P2, whatever the degree elevation used."""
    (sub,) = decode_path("M 0 0 Q 1 2 2 0", segments=2)
    assert points(sub)[1] == pytest.approx([1.0, 1.0], abs=1e-12)


def test_smooth_cubic_reflects_the_previous_control_point():
    """The S segment starts from 2*P_current - P_last_control = (2, -2)."""
    (sub,) = decode_path("M 0 0 C 0 2 2 2 2 0 S 4 -2 4 0", segments=2)
    ring = points(sub)
    # segments=2 emits two points per command, so the joint between them is index 2
    assert ring[2] == pytest.approx([2.0, 0.0])
    # and the first midpoint of the S segment is B(0.5) of (2,0),(2,-2),(4,-2),(4,0)
    assert ring[3] == pytest.approx([3.0, -1.5], abs=1e-12)


def test_smooth_without_a_preceding_curve_uses_the_endpoint_as_control():
    (sub,) = decode_path("M 0 0 L 4 0 S 4 4 0 4", segments=2)
    # pen is None after L, so first control = current = (4, 0)
    assert points(sub)[-1] == pytest.approx([0.0, 4.0])


def test_quadratic_smooth_continuation_reflects_the_control():
    first = decode_path("M 0 0 Q 0 4 4 4", segments=4)[0]
    combined = decode_path("M 0 0 Q 0 4 4 4 T 8 0", segments=4)[0]
    # the T segment must start where the Q ended, not jump
    assert points(combined)[first.n_points - 1] == pytest.approx([4.0, 4.0])
    assert points(combined)[-1] == pytest.approx([8.0, 0.0])
    # the reflected control is 2*(4,4) - (0,4) = (8,4), so the T parabola at t=0.5
    # is 0.25*(4,4) + 0.5*(8,4) + 0.25*(8,0) = (7,3) — above the chord, not on it
    ring = points(combined)
    mid = first.n_points + 1  # the T command emits t = 0.25, 0.5, 0.75, 1.0
    assert ring[mid] == pytest.approx([7.0, 3.0], abs=1e-12)
    assert ring[mid] != pytest.approx([6.0, 2.0], abs=0.5)  # the straight-chord answer


def test_repeated_t_continues_reflecting_and_never_loses_the_endpoint():
    (sub,) = decode_path("M 0 0 Q 1 2 2 0 T 4 0 T 6 0", segments=8)
    ring = points(sub)
    assert ring[-1] == pytest.approx([6.0, 0.0])
    # three arches, so the traced curve returns to y=0 twice in between
    crossings = np.where(np.abs(ring[:, 1]) < 1e-9)[0]
    assert len(crossings) >= 3


# --- arcs ------------------------------------------------------------------------


def test_arc_points_lie_on_the_requested_circle():
    (sub,) = decode_path("M 0 0 A 5 5 0 0 1 10 0", segments=48)
    ring = points(sub)
    centre = np.array([5.0, 0.0])
    assert np.allclose(np.linalg.norm(ring - centre, axis=1), 5.0, atol=1e-9)
    assert ring[-1] == pytest.approx([10.0, 0.0])


def test_large_arc_flag_controls_how_much_of_the_circle_is_traced():
    chord = "M 0 0 A 5 5 0 {large} 1 8 0"
    small = points(decode_path(chord.format(large=0), segments=48)[0])
    large = points(decode_path(chord.format(large=1), segments=48)[0])
    # both share a radius-5 circle centred on the bisector x=4; the large arc
    # wraps past the far side, so it reaches further from the chord's midpoint
    assert np.abs(large[:, 1]).max() > np.abs(small[:, 1]).max() + 1.0


def test_sweep_flag_picks_the_side_of_the_chord():
    up = points(decode_path("M 0 0 A 1 1 0 0 1 2 0", segments=24)[0])
    down = points(decode_path("M 0 0 A 1 1 0 0 0 2 0", segments=24)[0])
    assert np.sign(np.median(up[:, 1] - 0.0)) != np.sign(np.median(down[:, 1] - 0.0))


def test_radii_too_small_for_the_chord_are_scaled_up_not_rejected():
    """SVG F.6: a 1-unit radius cannot span a 4-unit chord, so both grow to 2."""
    (sub,) = decode_path("M 0 0 A 1 1 0 0 1 4 0", segments=48)
    ring = points(sub)
    assert np.allclose(
        np.linalg.norm(ring - np.array([2.0, 0.0]), axis=1), 2.0, atol=1e-9
    )


def test_zero_radius_degenerates_the_arc_to_a_line_to_the_endpoint():
    (sub,) = decode_path("M 0 0 A 0 0 0 0 1 6 8")
    assert points(sub)[-1] == pytest.approx([6.0, 8.0])
    assert sub.n_points == 2


def test_arc_is_sampled_per_quarter_turn_so_a_full_circle_keeps_its_shape():
    (sub,) = decode_path("M 0 0 A 3 3 0 1 1 0 0.0001", segments=24)
    ring = points(sub)
    # nearly a whole circle: the y-extent must reach the diameter, not a chord
    assert np.abs(ring[:, 1]).max() > 3.0 * 0.6


# --- errors ---------------------------------------------------------------------


def test_argument_count_not_a_multiple_of_the_command_width_is_rejected():
    with pytest.raises(SvgPathError, match="not a multiple of"):
        decode_path("M 0 0 L 1 2 3")


def test_number_before_any_command_is_rejected():
    with pytest.raises(SvgPathError, match="before any command"):
        decode_path("7 7 L 1 2")


def test_empty_path_is_rejected():
    with pytest.raises(SvgPathError, match="empty path data"):
        decode_path("")


def test_segments_below_one_is_rejected():
    with pytest.raises(SvgPathError, match="segments"):
        decode_path("M 0 0 L 1 1", segments=0)


# --- whole documents -------------------------------------------------------------


ICON = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" fill="none"/>
  <path fill="currentColor" d="M 128 16 L 240 240 L 16 240 Z"/>
  <circle cx="128" cy="128" r="20" fill="currentColor"/>
  <ellipse cx="40" cy="40" rx="12" ry="6" fill="currentColor"/>
  <polygon points="10,10 30,10 30,30 10,30" fill="currentColor"/>
  <polyline points="0,0 5,5 10,0" fill="currentColor"/>
</svg>
"""


def test_view_box_is_read_from_the_svg_element():
    assert view_box(ICON) == (0.0, 0.0, 256.0, 256.0)


def test_svg_subpaths_read_paths_and_primitives_alike():
    subpaths = svg_subpaths(ICON)
    # a <path> reports the command it opened with, a primitive reports its tag
    commands = sorted(sub.command for sub in subpaths)
    assert commands == ["M", "circle", "ellipse", "polygon", "polyline"]
    flags = {sub.command: sub.closed for sub in subpaths}
    assert flags == {
        "M": True,  # the triangle's Z is a real close, not an inferred one
        "circle": True,
        "ellipse": True,
        "polygon": True,
        "polyline": False,
    }


def test_unpainted_elements_are_not_part_of_the_silhouette():
    subpaths = svg_subpaths(ICON)
    # the 256x256 canvas spacer must not become the largest ring in the document
    assert max(point for sub in subpaths for point in sub.points[:, 0]) < 250.0
    assert svg_invisible_elements(ICON) == ["rect"]


def test_display_and_visibility_attributes_also_hide_an_element():
    document = ICON + '<svg><rect width="9" height="9" display="none"/>'
    assert "rect" in svg_invisible_elements(document)


def test_stroke_only_geometry_is_kept():
    document = '<svg viewBox="0 0 10 10"><path d="M 0 0 L 8 0 L 8 8 Z" stroke="#000"/></svg>'
    assert len(svg_subpaths(document)) == 1


def test_flip_y_turns_svg_screen_space_into_math_space():
    (sub,) = flip_y(svg_subpaths(ICON), 256.0)[0:1]
    ring = points(sub)
    # the triangle's apex is at y=16 in SVG space, i.e. near the top of the canvas
    assert ring[0] == pytest.approx([128.0, 240.0])


def test_signed_area_is_positive_for_the_array_order_and_negative_reversed():
    (cw,) = decode_path("M 0 0 L 4 0 L 4 4 L 0 4 Z")
    (ccw,) = decode_path("M 0 0 L 0 4 L 4 4 L 4 0 Z")
    areas = subpath_signed_areas([cw, ccw])
    assert areas[0] * areas[1] < 0.0
    assert abs(areas[0]) == pytest.approx(16.0)


def test_exterior_subpaths_keeps_the_outer_ring_and_drops_the_hole():
    document = (
        '<svg viewBox="0 0 100 100">'
        '<path d="M 10 10 L 90 10 L 90 90 L 10 90 Z"/>'
        '<path d="M 40 40 L 60 40 L 60 60 L 40 60 Z"/>'
        "</svg>"
    )
    rings = exterior_subpaths(svg_subpaths(document))
    assert len(rings) == 1
    assert abs(subpath_signed_areas(rings)[0]) == pytest.approx(6400.0)


def test_separate_pieces_are_both_kept_as_exterior():
    document = (
        '<svg viewBox="0 0 100 100">'
        '<path d="M 5 5 L 25 5 L 25 25 L 5 25 Z"/>'
        '<path d="M 60 60 L 90 60 L 90 90 L 60 90 Z"/>'
        "</svg>"
    )
    assert len(exterior_subpaths(svg_subpaths(document))) == 2


def test_a_ring_fully_outside_another_is_not_a_hole_even_if_smaller():
    document = (
        '<svg viewBox="0 0 200 200">'
        '<path d="M 0 0 L 100 0 L 100 100 L 0 100 Z"/>'
        '<path d="M 150 150 L 160 150 L 160 160 L 150 160 Z"/>'
        "</svg>"
    )
    assert len(exterior_subpaths(svg_subpaths(document))) == 2


def test_not_an_svg_document_is_rejected():
    with pytest.raises(SvgPathError, match="not an SVG document"):
        svg_subpaths("M 0 0 L 1 1")


def test_odd_coordinate_count_in_a_points_list_is_rejected():
    document = '<svg viewBox="0 0 10 10"><polygon points="1,1 2,2 3"/></svg>'
    with pytest.raises(SvgPathError, match="not a multiple of 2"):
        svg_subpaths(document)


def test_degenerate_primitives_are_dropped_rather_than_emitted_as_nothing():
    document = (
        '<svg viewBox="0 0 10 10">'
        '<circle cx="1" cy="1" r="0"/><rect width="0" height="5"/>'
        '<ellipse rx="0" ry="3"/><path d="M 1 1 L 2 2"/>'
        "</svg>"
    )
    assert svg_subpaths(document) == []


def test_sampling_is_deterministic_and_does_not_depend_on_document_order_twice():
    first = svg_subpaths(ICON)
    second = svg_subpaths(ICON)
    assert [points(sub).shape for sub in first] == [points(sub).shape for sub in second]
    for before, after in zip(first, second, strict=True):
        assert np.array_equal(points(before), points(after))


def test_more_segments_only_adds_samples_between_the_same_joints():
    coarse = points(decode_path("M 0 0 C 0 8 8 8 8 0", segments=4)[0])
    fine = points(decode_path("M 0 0 C 0 8 8 8 8 0", segments=32)[0])
    assert fine.shape[0] > coarse.shape[0]
    # the exact endpoints are shared, so the joint positions do not drift
    assert coarse[-1] == pytest.approx(fine[-1])
    assert math.isclose(coarse[-1, 0], 8.0)
