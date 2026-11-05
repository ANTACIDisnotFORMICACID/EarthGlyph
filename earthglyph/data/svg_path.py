"""SVG path decoding, for reading real vector assets into closed contours.

Scope: the subset of the SVG path grammar that icon sets actually use —
``M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z/z`` with absolute and relative
coordinates and implicit repeated parameters. Elliptical arcs are converted to
the centre parameterisation and sampled in chunks of at most a quarter turn,
which is what every browser rasteriser does; the ``large-arc`` / ``sweep`` flags
follow the SVG 1.1 definition (F.6) exactly, including the "radii too small for
the chord" correction.

Curves are flattened by *uniform parameter sampling* (``segments`` per command).
That is deliberately not adaptive: the same file must always yield the same
point list, because these contours become dataset members with a recorded
sha256. ``segments=24`` puts the flattening error about two orders of magnitude
below one step of the 128-point arc-length resampling the contours go through
later, so the decoder is not what limits the dataset.

Subpaths come back in file order with their ``closed`` flag taken from the
source, not inferred. :func:`exterior_subpaths` answers the one geometric
question needed to turn a glyph into a single outline — "is this ring inside a
bigger ring" — and the hole policy (exterior only, D21) stays with the caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

_COMMANDS = "MmLlHhVvCcSsQqTtAaZz"
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[{_COMMANDS}]|{_NUMBER}")

DEFAULT_SEGMENTS = 24


class SvgPathError(ValueError):
    """Raised when a ``d`` attribute cannot be decoded."""


@dataclass(frozen=True)
class Subpath:
    """One decoded subpath, in user units, y down as SVG defines it."""

    points: np.ndarray
    closed: bool
    command: str = "M"

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])


def tokenize(text: str) -> list[str]:
    """Split a ``d`` string into command letters and numbers."""
    if not isinstance(text, str):
        raise SvgPathError(f"path data must be a string, got {type(text).__name__}")
    return _TOKEN.findall(text)


def decode_path(text: str, *, segments: int = DEFAULT_SEGMENTS) -> list[Subpath]:
    """Decode an SVG ``d`` attribute into subpaths of ``(x, y)`` points.

    Points are emitted as the curve is traced, so a closed subpath repeats its
    first vertex as its last one — the same open-array convention
    :func:`earthglyph.geometry.open_ring` expects downstream.
    """
    tokens = tokenize(text)
    if not tokens:
        raise SvgPathError("empty path data")
    if segments < 1:
        raise SvgPathError(f"segments must be >= 1, got {segments}")

    rings: list[list[np.ndarray]] = []
    closed_flags: list[bool] = []
    current = np.zeros(2, dtype=float)
    origin = np.zeros(2, dtype=float)
    pen: np.ndarray | None = None  # last control point, for S/T smooth continuation
    command: str | None = None
    buffer: list[str] = []

    def begin_ring(point: np.ndarray) -> None:
        rings.append([point.copy()])
        closed_flags.append(False)

    def extend(points: Sequence[np.ndarray]) -> None:
        if not rings:  # a path that draws before moving: start where the pen is
            begin_ring(current)
        rings[-1].extend(np.asarray(point, dtype=float) for point in points)

    def close_ring() -> None:
        if not rings:
            return
        # A path that already ends where it started (the last arc closes onto the
        # M point, which icon sets do constantly) must not get a second copy of
        # that vertex: the ring convention is "the first point appears once more at
        # the end", not "twice more".
        if not np.allclose(rings[-1][-1], origin):
            rings[-1].append(origin.copy())
        closed_flags[-1] = True

    def take(values: list[float], width: int) -> list[list[float]]:
        if len(values) % width:
            raise SvgPathError(
                f"{command!r} got {len(values)} arguments, not a multiple of {width}"
            )
        return [values[index : index + width] for index in range(0, len(values), width)]

    def run(letter: str, args: list[str]) -> None:
        nonlocal current, origin, pen
        values = [float(token) for token in args]
        absolute = letter.isupper()
        step = letter.upper()
        if step == "M":
            groups = take(values, 2)
            for index, group in enumerate(groups):
                delta = np.array(group, dtype=float)
                current = delta if absolute else current + delta
                if index == 0:
                    origin = current.copy()
                    begin_ring(current)
                else:  # implicit repetition after M is a lineto, per the spec
                    extend([current])
            pen = None
            return
        if step in {"H", "V"}:
            axis = 0 if step == "H" else 1
            for value in values:
                target = current.copy()
                target[axis] = value if absolute else current[axis] + value
                current = target
                extend([current])
            pen = None
            return
        if step == "Z":
            close_ring()
            current = origin.copy()
            pen = None
            return
        width = {"L": 2, "T": 2, "C": 6, "S": 4, "Q": 4, "A": 7}[step]
        for group in take(values, width):
            if step == "L":
                delta = np.array(group, dtype=float)
                current = delta if absolute else current + delta
                extend([current])
            elif step == "C":
                first = np.array(group[0:2], dtype=float)
                second = np.array(group[2:4], dtype=float)
                target = np.array(group[4:6], dtype=float)
                if not absolute:
                    first, second, target = first + current, second + current, target + current
                extend(_cubic(current, first, second, target, segments))
                pen = second
                current = target
            elif step == "S":
                second = np.array(group[0:2], dtype=float)
                target = np.array(group[2:4], dtype=float)
                if not absolute:
                    second, target = second + current, target + current
                first = current if pen is None else 2.0 * current - pen
                extend(_cubic(current, first, second, target, segments))
                pen = second
                current = target
            elif step == "Q":
                control = np.array(group[0:2], dtype=float)
                target = np.array(group[2:4], dtype=float)
                if not absolute:
                    control, target = control + current, target + current
                first = current + (2.0 / 3.0) * (control - current)
                second = target + (2.0 / 3.0) * (control - target)
                extend(_cubic(current, first, second, target, segments))
                pen = control
                current = target
            elif step == "T":
                target = np.array(group, dtype=float)
                if not absolute:
                    target = target + current
                control = current if pen is None else 2.0 * current - pen
                first = current + (2.0 / 3.0) * (control - current)
                second = target + (2.0 / 3.0) * (control - target)
                extend(_cubic(current, first, second, target, segments))
                pen = control
                current = target
            elif step == "A":
                radius = np.array(group[0:2], dtype=float)
                phi = float(np.deg2rad(group[2]))
                large_arc = bool(group[3])
                sweep = bool(group[4])
                target = np.array(group[5:7], dtype=float)
                if not absolute:
                    target = target + current
                extend(_arc(current, target, radius, phi, large_arc, sweep, segments))
                pen = None
                current = target
        return

    for token in tokens:
        if len(token) == 1 and token in _COMMANDS:
            if command is not None:
                # no `and buffer` guard: Z carries no arguments, and skipping an
                # empty command silently dropped every close-path in the document,
                # which lost the subpath's closed flag (and its final vertex).
                run(command, buffer)
            command = token
            buffer = []
        else:
            if command is None:
                raise SvgPathError(f"number {token!r} appears before any command")
            buffer.append(token)
    if command is not None:
        run(command, buffer)

    return [
        Subpath(points=np.asarray(ring, dtype=float), closed=closed_flags[index])
        for index, ring in enumerate(rings)
        if len(ring) >= 2
    ]


def _cubic(
    start: np.ndarray, control_1: np.ndarray, control_2: np.ndarray, end: np.ndarray,
    segments: int,
) -> list[np.ndarray]:
    """B(t) for t in (0, 1]; the joint at t=0 belongs to the previous command.

    Bernstein form, each coefficient used exactly once: the earlier version
    multiplied the control points by an extra 3 on top of the basis' own 3, which
    inflated the curve past the viewBox and turned clean icons into
    "self-intersecting strokes".
    """
    t = np.linspace(0.0, 1.0, segments + 1)[1:]
    rest = 1.0 - t
    basis = np.column_stack([rest**3, 3.0 * rest**2 * t, 3.0 * rest * t**2, t**3])
    curve = basis @ np.vstack([start, control_1, control_2, end])
    curve[-1] = end
    return list(curve)


def _arc(
    start: np.ndarray,
    end: np.ndarray,
    radius: np.ndarray,
    phi: float,
    large_arc: bool,
    sweep: bool,
    segments: int,
) -> list[np.ndarray]:
    centre, (rx, ry), theta_1, delta = _arc_to_centre(start, end, radius, phi, large_arc, sweep)
    if rx <= 0.0 or ry <= 0.0 or delta == 0.0:
        return [end]
    chunks = max(1, int(np.ceil(abs(delta) / (0.5 * np.pi))))
    count = max(1, int(np.ceil(segments / chunks)))
    angles = theta_1 + np.linspace(0.0, 1.0, count + 1)[1:] * delta
    cos_phi, sin_phi = float(np.cos(phi)), float(np.sin(phi))
    x = rx * np.cos(angles)
    y = ry * np.sin(angles)
    curve = np.column_stack(
        [cos_phi * x - sin_phi * y + centre[0], sin_phi * x + cos_phi * y + centre[1]]
    )
    curve[-1] = end
    return list(curve)


def _arc_to_centre(
    start: np.ndarray,
    end: np.ndarray,
    radius: np.ndarray,
    phi: float,
    large_arc: bool,
    sweep: bool,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Endpoint parameters to ``(centre, radii, theta1, delta)`` — SVG 1.1 F.6."""
    rx, ry = float(abs(radius[0])), float(abs(radius[1]))
    if rx <= 0.0 or ry <= 0.0:  # the spec degenerates an arc to a straight line
        return start, np.array([0.0, 0.0]), 0.0, 0.0
    cos_phi, sin_phi = float(np.cos(phi)), float(np.sin(phi))
    half = 0.5 * (start - end)
    x_prime = cos_phi * half[0] + sin_phi * half[1]
    y_prime = -sin_phi * half[0] + cos_phi * half[1]
    lambda_check = (x_prime / rx) ** 2 + (y_prime / ry) ** 2
    if lambda_check > 1.0:
        factor = float(np.sqrt(lambda_check))
        rx *= factor
        ry *= factor
    squared = rx * rx * ry * ry - rx * rx * y_prime * y_prime - ry * ry * x_prime * x_prime
    denominator = rx * rx * y_prime * y_prime + ry * ry * x_prime * x_prime
    sign = 1.0 if large_arc != sweep else -1.0
    coefficient = sign * float(np.sqrt(max(squared, 0.0) / denominator)) if denominator > 0 else 0.0
    centre_prime = np.array([coefficient * rx * y_prime / ry, -coefficient * ry * x_prime / rx])
    centre = np.array(
        [
            cos_phi * centre_prime[0] - sin_phi * centre_prime[1] + 0.5 * (start[0] + end[0]),
            sin_phi * centre_prime[0] + cos_phi * centre_prime[1] + 0.5 * (start[1] + end[1]),
        ]
    )

    def angle(u: np.ndarray, v: np.ndarray) -> float:
        dot = float(u @ v)
        cross = float(u[0] * v[1] - u[1] * v[0])
        norm = float(np.linalg.norm(u) * np.linalg.norm(v))
        value = float(np.arccos(np.clip(dot / norm if norm > 0.0 else 1.0, -1.0, 1.0)))
        return value if cross >= 0.0 else -value

    rotated = np.array([cos_phi, sin_phi])
    unit_start = np.array([(start[0] - centre[0]) / rx, (start[1] - centre[1]) / ry])
    unit_end = np.array([(end[0] - centre[0]) / rx, (end[1] - centre[1]) / ry])
    theta_1 = angle(rotated, unit_start)
    delta = angle(unit_start, unit_end)
    if not sweep and delta > 0.0:
        delta -= 2.0 * np.pi
    elif sweep and delta < 0.0:
        delta += 2.0 * np.pi
    return centre, np.array([rx, ry]), theta_1, float(delta)


_SHAPE_TAGS = ("path", "polygon", "polyline", "rect", "circle", "ellipse")

# Phosphor and most icon sets put an invisible `<rect width height fill="none"/>`
# over every glyph as a canvas spacer. It is the largest ring in the document, so
# treating it as part of the silhouette turns the real icon into a "hole" inside a
# rectangle — and the whole gallery into rectangles.


def _is_drawable(attributes: Mapping[str, str]) -> bool:
    """Would this element paint anything?"""
    fill = (attributes.get("fill") or "").strip().lower()
    stroke = (attributes.get("stroke") or "").strip().lower()
    style = (attributes.get("style") or "").lower()
    if 'fill="none"' in style or "display:none" in style or "visibility:hidden" in style:
        return False
    if fill == "none" and stroke in {"", "none"}:
        return False
    if attributes.get("display") == "none" or attributes.get("visibility") == "hidden":
        return False
    return True


def svg_invisible_elements(text: str) -> list[str]:
    """Which shape elements were dropped as unpainted, for the asset record."""
    return [
        tag
        for tag, attributes in _iter_elements(text)
        if tag in _SHAPE_TAGS and not _is_drawable(attributes)
    ]


def svg_subpaths(text: str, *, segments: int = DEFAULT_SEGMENTS) -> list[Subpath]:
    """Every drawable subpath in an SVG document, from paths *and* shape primitives.

    Icon sets mix the two: a star is a ``<path>``, a diamond is often a
    ``<polygon>``, a circle is a ``<circle>``. Reading only paths would silently
    drop the simplest hard negatives, and those are exactly the shapes the
    perceptual question is about. A primitive is expanded into the same kind of
    point ring a path decodes to, so one code path standardizes everything.
    Elements that paint nothing (``fill="none"``, ``display="none"``) are not part
    of a silhouette; see :func:`svg_invisible_elements`.
    """
    if not isinstance(text, str) or "<" not in text:
        raise SvgPathError("not an SVG document")
    subpaths: list[Subpath] = []
    for tag, attributes in _iter_elements(text):
        if tag not in _SHAPE_TAGS:
            continue
        if not _is_drawable(attributes):
            continue
        if tag == "path":
            data = attributes.get("d")
            if data:
                subpaths.extend(decode_path(data, segments=segments))
        elif tag in {"polygon", "polyline"}:
            points = _point_list(attributes.get("points", ""))
            if points.shape[0] >= 2:
                closed = tag == "polygon"
                ring = np.vstack([points, points[:1]]) if closed else points
                subpaths.append(Subpath(points=ring, closed=closed, command=tag))
        elif tag == "rect":
            x, y = float(attributes.get("x", 0.0)), float(attributes.get("y", 0.0))
            width = float(attributes.get("width", 0.0))
            height = float(attributes.get("height", 0.0))
            if width > 0.0 and height > 0.0:
                ring = np.array([[x, y], [x + width, y], [x + width, y + height], [x, y + height]])
                subpaths.append(
                    Subpath(points=np.vstack([ring, ring[:1]]), closed=True, command=tag)
                )
        elif tag == "circle":
            radius = float(attributes.get("r", 0.0))
            if radius > 0.0:
                subpaths.append(_ellipse_ring(
                    float(attributes.get("cx", 0.0)), float(attributes.get("cy", 0.0)),
                    radius, radius, segments * 8, command=tag,
                ))
        elif tag == "ellipse":
            rx, ry = float(attributes.get("rx", 0.0)), float(attributes.get("ry", 0.0))
            if rx > 0.0 and ry > 0.0:
                subpaths.append(_ellipse_ring(
                    float(attributes.get("cx", 0.0)), float(attributes.get("cy", 0.0)),
                    rx, ry, segments * 8, command=tag,
                ))
    return [sub for sub in subpaths if sub.n_points >= 3]


def _ellipse_ring(
    cx: float, cy: float, rx: float, ry: float, count: int, *, command: str = "ellipse"
) -> Subpath:
    angle = np.linspace(0.0, 2.0 * np.pi, int(count), endpoint=False)
    ring = np.column_stack([cx + rx * np.cos(angle), cy + ry * np.sin(angle)])
    return Subpath(points=np.vstack([ring, ring[:1]]), closed=True, command=command)


def _point_list(text: str) -> np.ndarray:
    values = [float(token) for token in re.findall(_NUMBER, text)]
    if len(values) % 2:
        raise SvgPathError(f"{len(values)} coordinates in a points list, not a multiple of 2")
    return np.asarray(values, dtype=float).reshape(-1, 2)


def _iter_elements(text: str) -> list[tuple[str, dict[str, str]]]:
    """``(tag, attributes)`` for every start tag, skipping comments and XML decl."""
    out: list[tuple[str, dict[str, str]]] = []
    for match in re.finditer(r"<([a-zA-Z][\w:-]*)([^<>]*)", text):
        attributes: dict[str, str] = {}
        for name, double, single in re.findall(
            r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', match.group(2)
        ):
            attributes[name.lower()] = double or single
        out.append((match.group(1), attributes))
    return out


def view_box(text: str) -> tuple[float, float, float, float] | None:
    """The document ``viewBox`` as ``(min_x, min_y, width, height)``, if present."""
    for tag, attributes in _iter_elements(text):
        if tag.lower() in {"svg", "symbol"} and "viewbox" in attributes:
            values = [float(token) for token in re.findall(_NUMBER, attributes["viewbox"])]
            if len(values) == 4:
                return (values[0], values[1], values[2], values[3])
    return None


def flip_y(subpaths: Sequence[Subpath], height: float) -> list[Subpath]:
    """SVG's y-down user space to math y-up, about the document's own baseline."""
    return [
        Subpath(
            points=np.column_stack([sub.points[:, 0], height - sub.points[:, 1]]),
            closed=sub.closed,
            command=sub.command,
        )
        for sub in subpaths
    ]


def subpath_signed_areas(subpaths: Sequence[Subpath]) -> np.ndarray:
    """Shoelace area of every subpath, sign from the array order (SVG is y down)."""
    out: list[float] = []
    for sub in subpaths:
        ring = np.asarray(sub.points, dtype=float)
        if ring.shape[0] < 3:
            out.append(0.0)
            continue
        closed = np.vstack([ring, ring[:1]])
        out.append(
            0.5
            * float(
                np.sum(closed[:-1, 0] * closed[1:, 1] - closed[1:, 0] * closed[:-1, 1])
            )
        )
    return np.asarray(out, dtype=float)


def exterior_subpaths(subpaths: Sequence[Subpath]) -> list[Subpath]:
    """Subpaths that no larger subpath contains — the silhouette rings of a glyph.

    A hole is a smaller ring drawn inside a bigger one, so containment by a
    ring of larger area is the test. Ray casting on one vertex is enough for
    icon geometry; a ring that touches its carrier at a single point is decided
    by the vertex that is strictly inside.
    """
    areas = subpath_signed_areas(subpaths)
    keep: list[Subpath] = []
    for index, sub in enumerate(subpaths):
        if sub.n_points < 3:
            continue
        contained = any(
            index != other
            and abs(areas[other]) > abs(areas[index])
            and _point_in_ring(sub.points[0], subpaths[other].points)
            for other in range(len(subpaths))
        )
        if not contained:
            keep.append(sub)
    return keep


def _point_in_ring(point: np.ndarray, ring: np.ndarray) -> bool:
    closed = np.vstack([ring, ring[:1]])
    x, y = float(point[0]), float(point[1])
    inside = False
    previous = closed[-1]
    for current in closed:
        if (current[1] > y) != (previous[1] > y):
            span = previous[1] - current[1]
            edge_x = (previous[0] - current[0]) * (y - current[1]) / span + current[0]
            if x < edge_x:
                inside = not inside
        previous = current
    return bool(inside)
