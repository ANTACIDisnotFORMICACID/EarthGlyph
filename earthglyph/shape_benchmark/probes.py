"""Designed structure probes and controlled transforms for the shape benchmark.

These are *synthetic* stimuli with known geometry. They are used for the Part D3 structure
stress tests and the Part D4 invariance tests, where the point is to check a geometric
property, not to measure human preference. Nothing here is presented as human ground truth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from earthglyph.shape_benchmark.datasets import BenchmarkShape, standardize

NATIVE_POINTS = 384
Transform = Callable[[np.ndarray], np.ndarray]
ProbeFactory = Callable[..., np.ndarray]


def _ring(points: np.ndarray) -> np.ndarray:
    return np.asarray(points, dtype=float)


def regular_polygon(sides: int, n: int = NATIVE_POINTS, radius: float = 1.0) -> np.ndarray:
    vertices = np.array(
        [
            [radius * np.cos(t), radius * np.sin(t)]
            for t in np.linspace(0.0, 2.0 * np.pi, sides, endpoint=False) + np.pi / 2.0
        ]
    )
    closed = np.vstack([vertices, vertices[:1]])
    per_edge = max(2, n // sides)
    segments = [
        np.linspace(a, b, per_edge, endpoint=False)
        for a, b in zip(closed[:-1], closed[1:], strict=True)
    ]
    return _ring(np.vstack(segments))


def star(points_count: int = 5, n: int = NATIVE_POINTS, inner: float = 0.42) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, 2 * points_count, endpoint=False) + np.pi / 2.0
    radii = np.where(np.arange(2 * points_count) % 2 == 0, 1.0, inner)
    vertices = np.column_stack([radii * np.cos(theta), radii * np.sin(theta)])
    closed = np.vstack([vertices, vertices[:1]])
    per_edge = max(2, n // (2 * points_count))
    segments = [
        np.linspace(a, b, per_edge, endpoint=False)
        for a, b in zip(closed[:-1], closed[1:], strict=True)
    ]
    return _ring(np.vstack(segments))


def ellipse(n: int = NATIVE_POINTS, aspect: float = 1.6) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return _ring(np.column_stack([aspect * np.cos(theta), np.sin(theta)]))


def heart(n: int = NATIVE_POINTS) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    x = 16.0 * np.sin(t) ** 3
    y = 13.0 * np.cos(t) - 5.0 * np.cos(2 * t) - 2.0 * np.cos(3 * t) - np.cos(4 * t)
    points = np.column_stack([x, y])
    return _ring(points / np.abs(points).max())


def crescent(n: int = NATIVE_POINTS, offset: float = 0.45, inner: float = 0.88) -> np.ndarray:
    """A lune: inside the unit disk, outside a disk of radius ``inner`` centred at (offset, 0).

    The boundary is the outer circle's major arc plus the inner circle's arc that lies
    inside the outer disk. The inner arc is traversed in the opposite angular direction,
    which is what keeps the ring simple instead of self-intersecting at the cusps.
    """
    if not 0.0 < offset < 1.0 + inner or inner <= 0.0:
        raise ValueError("crescent needs two genuinely intersecting circles")
    a = (offset**2 + 1.0 - inner**2) / (2.0 * offset)
    if abs(a) >= 1.0:
        raise ValueError("crescent circles do not intersect in two points")
    h = np.sqrt(1.0 - a**2)
    theta_top = np.arctan2(h, a)
    outer_t = np.linspace(theta_top, 2.0 * np.pi - theta_top, n // 2)
    outer = np.column_stack([np.cos(outer_t), np.sin(outer_t)])
    inner_top = np.arctan2(h, a - offset)
    inner_bottom = np.arctan2(-h, a - offset) + 2.0 * np.pi
    inner_t = np.linspace(inner_bottom, inner_top, n - len(outer))
    inner_arc = np.column_stack([offset + inner * np.cos(inner_t), inner * np.sin(inner_t)])
    # Each arc drops its own end vertex: the next arc starts there, and repeating it makes
    # the ring self-intersect at the cusp.
    return _ring(np.vstack([outer[:-1], inner_arc[:-1]]))


def forked(n: int = NATIVE_POINTS, branches: int = 3) -> np.ndarray:
    """A radial silhouette with ``branches`` prongs and deep notches between them."""
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = 0.55 + 0.45 * np.maximum(np.cos(branches * theta), 0.0) ** 0.6
    return _ring(np.column_stack([radius * np.cos(theta), radius * np.sin(theta)]))


def blob(n: int = NATIVE_POINTS, seed: int = 0, roughness: float = 0.12) -> np.ndarray:
    """A smooth irregular outline — the 'simple curve' / notch-free control."""
    rng = np.random.default_rng(seed)
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = np.ones(n)
    for harmonic in range(2, 7):
        radius += (roughness / harmonic) * rng.normal() * np.cos(
            harmonic * theta + rng.uniform(0, 2 * np.pi)
        )
    return _ring(np.column_stack([radius * np.cos(theta), radius * np.sin(theta)]))


PROBE_BUILDERS: dict[str, ProbeFactory] = {
    "star": star,
    "heart": heart,
    "crescent": crescent,
    "forked": forked,
    "circle": lambda: ellipse(aspect=1.0),
    "pentagon": lambda: regular_polygon(5),
    "ellipse": ellipse,
    "disk": lambda: ellipse(aspect=1.0),
    "simple_curve": blob,
    "blob": blob,
}


def make_probe(name: str, **kwargs: Any) -> BenchmarkShape:
    points = np.asarray(PROBE_BUILDERS[name](**kwargs), dtype=float)
    return BenchmarkShape(
        shape_id=f"probe_{name}",
        source="synthetic_probe",
        points=standardize(points),
        display_points=points - points.mean(axis=0),
        category=None,
    )


# --------------------------------------------------------------------------- transforms


def rotate(points: np.ndarray, degrees: float) -> np.ndarray:
    angle = np.deg2rad(degrees)
    matrix = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    return np.asarray(points @ matrix.T, dtype=float)


def scale(points: np.ndarray, factor: float) -> np.ndarray:
    return np.asarray(points * factor, dtype=float)


def translate(points: np.ndarray, dx: float, dy: float) -> np.ndarray:
    return np.asarray(points + np.array([dx, dy]), dtype=float)


def shift_start(points: np.ndarray, fraction: float) -> np.ndarray:
    shift = int(round(fraction * len(points))) % len(points)
    return np.roll(points, shift, axis=0)


def mirror(points: np.ndarray) -> np.ndarray:
    return np.asarray(points * np.array([-1.0, 1.0]), dtype=float)


def deform_local(
    points: np.ndarray, *, amplitude: float = 0.08, centre: float = 0.25, width: float = 0.12
) -> np.ndarray:
    """Push a local arc of the boundary outward — a notch/bump, not a rigid transform."""
    n = len(points)
    index = np.arange(n) / n
    distance = np.minimum(np.abs(index - centre), 1.0 - np.abs(index - centre))
    weight = np.exp(-0.5 * (distance / width) ** 2)
    centroid = points.mean(axis=0)
    radial = points - centroid
    norm = np.linalg.norm(radial, axis=1, keepdims=True)
    direction = radial / np.maximum(norm, 1e-12)
    return np.asarray(points + amplitude * weight[:, None] * direction * norm.mean(), dtype=float)


TRANSFORMS: dict[str, Transform] = {
    "rotate_37": lambda p: rotate(p, 37.0),
    "rotate_90": lambda p: rotate(p, 90.0),
    "rotate_191": lambda p: rotate(p, 191.0),
    "scale_up": lambda p: scale(p, 1.6),
    "scale_down": lambda p: scale(p, 0.55),
    "translate": lambda p: translate(p, 2.3, -1.7),
    "start_shift": lambda p: shift_start(p, 0.37),
    "deform_local": lambda p: deform_local(p),
    "mirror": mirror,
}


@dataclass(frozen=True)
class StructureProbe:
    """A query plus candidates whose structural role is known by construction."""

    query: BenchmarkShape
    structural_variants: list[BenchmarkShape]
    """Same salient structure, perturbed boundary/pose. Should outrank gross distractors."""
    gross_distractors: list[BenchmarkShape]
    """Similar overall size/aspect but different structure."""


def _variant(base: BenchmarkShape, name: str, transform: Transform) -> BenchmarkShape:
    points = np.asarray(transform(base.display_points), dtype=float)
    return BenchmarkShape(
        shape_id=f"{base.shape_id}__{name}",
        source="synthetic_probe",
        points=standardize(points),
        display_points=points - points.mean(axis=0),
    )


def build_structure_probe(kind: str, natural: BenchmarkShape | None = None) -> StructureProbe:
    """Build the Part D3 stress test for one query family.

    ``natural`` supplies a real MPEG-7 outline for the 'natural' family; the other four
    families use explicitly synthetic primitives because MPEG-7 has no star/crescent class.
    """
    if kind == "natural":
        if natural is None:
            raise ValueError("the natural probe needs a real source outline")
        query = BenchmarkShape(
            shape_id=f"probe_natural_{natural.shape_id}",
            source="synthetic_probe",
            points=natural.points,
            display_points=natural.display_points,
        )
        distractors = [
            make_probe("circle"),
            make_probe("ellipse"),
            make_probe("simple_curve"),
        ]
    else:
        query = make_probe(kind)
        distractors = {
            "star": [make_probe("circle"), make_probe("pentagon"), make_probe("ellipse")],
            "heart": [make_probe("ellipse"), make_probe("circle"), make_probe("blob", seed=4)],
            "crescent": [make_probe("disk"), make_probe("ellipse"), make_probe("circle")],
            "forked": [make_probe("circle"), make_probe("simple_curve"), make_probe("ellipse")],
        }[kind]

    variants = [
        _variant(query, "rot37", lambda p: rotate(p, 37.0)),
        _variant(query, "deformed", lambda p: deform_local(p, amplitude=0.10)),
        _variant(query, "scaled", lambda p: scale(p, 1.35)),
    ]
    for index, distractor in enumerate(distractors):
        distractors[index] = BenchmarkShape(
            shape_id=f"{query.shape_id}__distractor_{distractor.shape_id}",
            source="synthetic_probe",
            points=distractor.points,
            display_points=distractor.display_points,
        )
    return StructureProbe(query=query, structural_variants=variants, gross_distractors=distractors)