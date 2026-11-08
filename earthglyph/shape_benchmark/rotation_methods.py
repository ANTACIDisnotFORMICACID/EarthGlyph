"""Rotation strategies for the official ShapeComp port arms (P0-4.6 Checkpoint 7-C).

The official KerNet weights are not rotation invariant (CP5/CP6, DECISIONS D46/D52). CP6 added
one wrapper — a query-side grid search over 24 poses — and measured it (R0 below). This module
implements the *competing* wrappers so the choice of rotation method is an experiment rather
than an assumption:

``naive``
    no wrapper: ``d(q, g) = ||net(q) - net(g)||``, one forward pass per shape. The floor.
``R0``
    CP6's baseline: query-side min over the 24-step / 15-degree grid, gallery scored once in
    its stored pose.
``R1``
    the same query-side min over a finer grid (36 = 10 degrees, 72 = 5 degrees).
``R2``
    bilateral orientation canonicalisation: BOTH query and gallery contour are turned into a
    label-free principal-axis frame before the official input contract, then one forward pass
    each — no grid at all.
``R3``
    bilateral multi-direction min: query AND gallery each get the 24-frame grid and the score
    is the min over all 24x24 pose pairs.

Why R3 is not a mathematical duplicate of R0
    the grid is closed under composition, so for an *equivariant* encoder min over pose pairs
    would collapse to min over relative pose. The networks are not equivariant — rotating the
    input changes the resampling, the leftmost-start cut and the bounding-box normalisation —
    so R3 can differ from R0, and it is also the variant most exposed to wrong-shape
    attraction (more pose freedom for two DIFFERENT shapes to coincidentally line up). It is
    measured, not assumed.

One convention, applied in contour space
    R2 uses exactly one orientation convention, defined here, and it is applied to the contour
    BEFORE ``kernet1_input`` / ``kernet2_input``. That is what keeps the image and the contour
    on the same axis system: KerNet2's ``flipud`` maps a math-convention angle to its negative,
    and the convention pins the principal axis to +x, which ``flipud`` leaves alone. EarthGlyph
    already owns two *different* conventions (E1 ``principal_frame`` and E3 ``canonical_frame``);
    they are compared against this one in the d6 diagnostics instead of being silently merged.

Label-free
    every function here sees ONE contour. Nothing may consult the query's true target, its
    class, or any other gallery member when choosing a pose — a pose picked with target
    knowledge would be retrieval oracle leakage, not a rotation method.

The scorer is injected, so these strategies are testable without the official weights and are
usable with any 22-D encoder.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from earthglyph.retrieval import shape_context_baseline as e3

__all__ = [
    "ISOTROPY_MAX",
    "ROTATION_STRATEGIES",
    "SIGN_TIE_TOLERANCE",
    "CanonicalFrame",
    "RotationInputError",
    "resolve_strategy",
    "Strategy",
    "canonical_frame",
    "canonicalise",
    "centroid",
    "describe",
    "gallery_distances",
    "pose_embedding",
    "pose_pair_distance",
    "rows_for",
]

#: A contour is treated as axis-degenerate once its minor/major variance ratio reaches this.
#: Set equal to E1's ``ORIENTATION_ISOTROPY_MAX`` so the two fallbacks decide the same way.
ISOTROPY_MAX = 0.98
#: Standardised third moment below which the 180-degree sign of an axis is undecidable (a shape
#: symmetric front-to-back along that axis, like a rectangle or an ellipse). Calibrated on
#: contour-ring moments: a left-right symmetric 128-point ring leaves ~9e-6 of floating-point
#: residue, while real asymmetry measures >= 1.8e-2, so 1e-3 separates the two by two orders of
#: magnitude on both sides. E1's ``FLIP_TIE_TOLERANCE`` is 1e-9 because it reads AREA moments,
#: where the same decision is numerically far larger — the tolerances are not interchangeable.
SIGN_TIE_TOLERANCE = 1e-3

Scorer = Callable[[np.ndarray], np.ndarray]
Role = Literal["query", "gallery"]


class RotationInputError(ValueError):
    """Raised for a contour from which no rotation decision can legitimately be taken."""


@dataclass(frozen=True)
class Strategy:
    """One rotation wrapper, described entirely by what it costs and what it searches."""

    name: str
    steps: int
    """0 for a single forward pass; otherwise the size of the pose grid that is searched."""
    canonical: bool
    """Rotate each contour into the label-free canonical frame before scoring."""
    bilateral: bool
    """Search the pose grid on the gallery side as well as the query side."""

    @property
    def step_deg(self) -> float:
        return 0.0 if self.steps == 0 else 360.0 / float(self.steps)


#: ``naive`` is included so a run can report the un-wrapped floor next to every wrapper.
ROTATION_STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in (
        Strategy("naive", steps=0, canonical=False, bilateral=False),
        Strategy("R0", steps=e3.ROTATION_STEPS, canonical=False, bilateral=False),
        Strategy("R1_36", steps=36, canonical=False, bilateral=False),
        Strategy("R1_72", steps=72, canonical=False, bilateral=False),
        Strategy("R2", steps=0, canonical=True, bilateral=False),
        Strategy("R3", steps=e3.ROTATION_STEPS, canonical=False, bilateral=True),
    )
}


def describe(name: str) -> str:
    """The one-sentence contract of a strategy, for ``details`` blocks and figures."""
    strategy = resolve_strategy(name)
    if strategy.name == "naive":
        return "no rotation wrapper: the official network scores the contour in its stored pose"
    if strategy.canonical:
        return "bilateral orientation canonicalisation, one forward pass per shape, no grid"
    if strategy.bilateral:
        return f"bilateral multi-direction min over a {strategy.steps}-pose grid on both sides"
    return f"query-side min over a {strategy.steps}-pose grid, gallery scored once in stored pose"


def resolve_strategy(name: str) -> Strategy:
    try:
        return ROTATION_STRATEGIES[name]
    except KeyError as error:
        raise ValueError(
            f"unknown rotation strategy {name!r}, expected one of {tuple(ROTATION_STRATEGIES)}"
        ) from error


def _as_contour(points: np.ndarray, what: str) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise RotationInputError(f"{what} must be an (n, 2) contour, got shape {pts.shape}")
    if len(pts) < 3:
        raise RotationInputError(f"{what} needs at least 3 points to define an orientation")
    if not np.isfinite(pts).all():
        raise RotationInputError(f"{what} contains non-finite coordinates")
    return pts


def centroid(points: np.ndarray) -> np.ndarray:
    """The point mean — the same definition the official ``centroid`` uses."""
    return _as_contour(points, "contour").mean(axis=0)


def _rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(float(angle_deg))
    matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ matrix.T, dtype=float)


@dataclass(frozen=True)
class CanonicalFrame:
    """The orientation decision taken for one contour, with its own degeneracy evidence."""

    angle_deg: float
    """Turn the contour by ``-angle_deg`` to put its major axis on +x."""
    status: str
    """``ok`` | ``isotropic-fallback`` | ``sign-tie``, and ``minor`` when only the minor
    axis could decide the 180-degree flip (a heart lies on its side: the distinguishing
    bulge-vs-point skew runs along the MINOR axis, not the major one)."""
    axis_ratio: float
    """minor variance / major variance, in (0, 1]. 1.0 = a circle."""
    sign_margin: float
    """The standardised third moment that decided the flip. Small = the flip is a coin toss."""

    @property
    def canonicalised(self) -> bool:
        return self.status in {"ok", "minor"}


def canonical_frame(points: np.ndarray) -> CanonicalFrame:
    """Principal-axis orientation of ONE contour, with the 180-degree sign resolved by skew.

    Diagnostics are part of the result: ``isotropic-fallback`` means no axis exists to align to
    (near-circles, regular polygons, n>=4 stars) and ``sign-tie`` means the axes exist but the
    contour is front-to-back symmetric to third order along both, so the flip is decided by
    floating-point noise. The two-level rule (major skew, else minor skew) is the rule E1's
    ``principal_frame`` documents, applied here to contour moments instead of region moments.
    """
    pts = _as_contour(points, "contour")
    centred = pts - pts.mean(axis=0)
    covariance = centred.T @ centred / float(len(centred))
    values, vectors = np.linalg.eigh(covariance)
    major_variance = float(values[-1])
    if major_variance <= 0.0:
        raise RotationInputError("contour has zero variance along every axis: it is a single point")
    ratio = float(max(min(values[-2] / major_variance, 1.0), 0.0))
    if ratio >= ISOTROPY_MAX:
        return CanonicalFrame(0.0, "isotropic-fallback", ratio, 0.0)
    major = vectors[:, -1]
    minor = vectors[:, -2]
    skew_major = _standardised_skew(centred, major, major_variance)
    skew_minor = _standardised_skew(centred, minor, float(values[-2]))
    if abs(skew_major) >= SIGN_TIE_TOLERANCE:
        status, deciding = "ok", skew_major
    elif abs(skew_minor) >= SIGN_TIE_TOLERANCE:
        status, deciding = "minor", skew_minor
    else:
        # Nothing decides the flip; report the value that (by noise) did, so the case is visible.
        status = "sign-tie"
        deciding = skew_major if abs(skew_major) >= abs(skew_minor) else skew_minor
    # The flip is one decision on the whole frame: major and minor invert together.
    if deciding < 0.0:
        major = -major
    return CanonicalFrame(
        float(np.rad2deg(np.arctan2(major[1], major[0]))), status, ratio, deciding
    )


def _standardised_skew(centred: np.ndarray, axis: np.ndarray, variance: float) -> float:
    """``mean((p.axis)^3) / variance^1.5`` — scale-free, so one tolerance fits every shape."""
    if variance <= 0.0:
        return 0.0
    projection = centred @ axis
    return float(np.mean(projection**3) / variance**1.5)


def canonicalise(points: np.ndarray) -> tuple[np.ndarray, CanonicalFrame]:
    """Turn a contour about its centroid so its major axis lands on +x; report what was decided.

    The translation is deliberately NOT normalised away: the official preprocessing centres by
    the point mean itself, so handing it a rotated contour is the only change this makes.
    """
    pts = _as_contour(points, "contour")
    frame = canonical_frame(pts)
    return _rotate(pts, -frame.angle_deg), frame


def rows_for(strategy: Strategy, role: Role) -> int:
    """How many pose rows one shape contributes on a given side."""
    if role == "gallery":
        return strategy.steps if strategy.bilateral else 1
    return strategy.steps if strategy.steps else 1


def pose_embedding(
    scorer: Scorer,
    contour: np.ndarray,
    strategy: Strategy | str,
    role: Role = "query",
) -> np.ndarray:
    """``(rows_for(strategy, role), 22)`` network coordinates for one contour.

    The pose decision (canonicalisation or grid) happens in CONTOUR space; each pose is then
    handed to the official ``scorer`` under its untouched input contract.
    """
    strategy = resolve_strategy(strategy) if isinstance(strategy, str) else strategy
    pts = _as_contour(contour, "contour")
    if strategy.canonical:
        pts, _ = canonicalise(pts)
    rows = rows_for(strategy, role)
    if rows == 1:
        return np.asarray([np.asarray(scorer(pts), dtype=float)])
    grid = e3.rotation_grid_deg(strategy.steps)
    return np.stack([np.asarray(scorer(_rotate(pts, angle)), dtype=float) for angle in grid])


def pose_pair_distance(query_rows: np.ndarray, gallery_rows: np.ndarray) -> np.ndarray:
    """Min over pose pairs: a ``(m, 22)`` against a ``(k, 22)`` -> one distance.

    A ``(22,)`` row on either side is accepted too, which is what ``naive``, ``R0``'s gallery side
    and ``R2`` look like; ``(24, 22)`` against ``(24, 22)`` is ``R3``.
    """
    delta = np.atleast_2d(np.asarray(query_rows, dtype=float))[
        :, None, :
    ] - np.atleast_2d(np.asarray(gallery_rows, dtype=float))[None, :, :]
    return np.asarray(np.linalg.norm(delta, axis=2).min())


def gallery_distances(
    query_rows: np.ndarray, gallery_rows: np.ndarray | Sequence[np.ndarray]
) -> np.ndarray:
    """Distances from one query embedding to every gallery embedding, min over pose pairs.

    Loops over the query rows instead of materialising the full ``(m, n, k, 22)`` tensor, which
    for ``R3`` against a 350-shape gallery would be tens of megabytes per query.
    """
    stack = np.asarray(gallery_rows, dtype=float)
    if stack.ndim != 3:
        raise ValueError(f"gallery embeddings must be (n, k, 22), got shape {stack.shape}")
    rows = np.atleast_2d(np.asarray(query_rows, dtype=float))
    if stack.shape[2] != rows.shape[1]:
        raise ValueError("query and gallery embeddings have different coordinate dimensions")
    out = np.full(stack.shape[0], np.inf)
    for row in rows:
        per_pose = np.linalg.norm(stack - row, axis=2).min(axis=1)
        out = np.minimum(out, per_pose)
    return out
