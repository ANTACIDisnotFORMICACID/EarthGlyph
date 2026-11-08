"""E3 — Shape Context (Belongie et al. 2002), implemented in this repo.

Paper: Belongie, Malik, Poon, "Shape Matching and Object Recognition Using Shape
Contexts" (CVPR 2000, with the implementation details of the NeurIPS-era version).
For every sampled boundary point the descriptor is a log-polar histogram of the
positions of *all the other* sampled points relative to it; two shapes are compared
by matching those histograms one-to-one under the chi-square distance.

**Not OpenCV, and not presented as OpenCV.**  The obvious shortcut —
``cv2.createShapeContextDistanceExtractor`` — is not available here: the installed
``opencv-python-headless`` 5.0.0 has no contrib ``shape`` module (checked with
``hasattr`` against the live install, recorded in ``POLICY["implementation"]``).  So
this is a from-scratch NumPy + SciPy implementation of the paper's formulation, and
it introduces no new dependency.

Why this method belongs in the comparison: E0 minimises the *mean boundary offset*,
a metric quantity, while the question being asked is what an eye calls the same
shape.  A shape context assumes no point correspondence at all — it compares
distributions of relative positions, so a corner stays a corner even when the
outline wobbles.  That is exactly the property the experiment probes.

Deviations from the paper, each deliberate and each one true of this code (passing a
modified variant off as the original is forbidden):

* **no curvature term.**  The paper's full objective adds ``gamma * sum (k1 - k2)^2``
  over matched pairs.  Omitted: it needs a curvature estimate per sample, and on 64
  points of a 128-sample standardized ring that estimate is sampling noise.
* **mean matched cost, not total.**  The total assignment cost grows with the number
  of samples; dividing by the number of matched contexts keeps the score comparable
  across ``context_points``.
* **each histogram normalised to sum 1** (the paper does this too), so the chi-square
  denominator is a probability mass and the ``+ epsilon`` guard added to it is
  scale-free.  With 75 bins the mean mass is 1/75, so ``EPSILON = 1e-6`` biases a term
  of the sum by about 1e-4 relative, and costs nothing where both histograms are
  empty — which is the 0/0 it exists to prevent.
* **out-of-range radii are clipped into the end bins instead of dropped.**  The
  paper's grid spans ``[R/8, R/2]`` and silently discards every pair beyond ``R/2``;
  here ``r_max_factor = 2.0`` mean pairwise distances covers essentially all pairs of
  a unit-RMS ring, and anything farther saturates rather than vanishing.
* **the scale normalisation is the mean pairwise distance ``R``** (the paper's own
  recipe) on top of the standardizer's unit RMS radius.  Redundant for a rigid copy —
  that redundancy is the point: the descriptor stays scale-free even if a
  non-standardized contour reaches it.
* **5 radial x 15 angular bins** (paper: 5 x 20) and **64 of the 128 ring samples**
  (paper: 100 of its own outline samples), both for wall-clock, both parameters.

Orientation is *not* quietly absorbed.  Shape context is rotation-**covariant**, not
invariant: rotating a shape rotates every histogram's angular axis, so two shapes
differing only by an in-plane rotation have different descriptors unless something
aligns them.  Two policies are offered, each honest about what it assumes:

* ``orientation="sample"`` (default) searches a fixed grid of relative rotations and
  reports the winner.  Invariance therefore holds only up to half a grid step, and
  the cost is ``rotation_steps`` assignments per candidate.
* ``orientation="canonical"`` rotates each contour into its own principal-axis frame:
  one assignment per candidate, and it removes *any* rotation — but only as far as
  that frame is identifiable.  See :func:`canonical_frame` for the eigenvector sign
  rule and for the two ambiguities it cannot resolve.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from earthglyph.retrieval.alignment import mirror_points
from earthglyph.retrieval.perceptual_methods import check_contour, contour_stack

__all__ = [
    "ANGULAR_BINS",
    "CONTEXT_POINTS",
    "EPSILON",
    "LibraryDescriptors",
    "ORIENTATION_MODES",
    "POLICY",
    "R_MAX_FACTOR",
    "R_MIN_FACTOR",
    "RADIAL_BINS",
    "ROTATION_STEPS",
    "ShapeContextError",
    "build_library_descriptors",
    "canonical_frame",
    "chi_square_costs",
    "context_match_cost",
    "describe_policy",
    "distance",
    "distance_library",
    "resolve_params",
    "rotation_grid_deg",
    "score_library",
    "score_library_from_descriptors",
    "shape_contexts",
]

RADIAL_BINS = 5
ANGULAR_BINS = 15
#: The chi-square tensor grows with points^2 * bins and the assignment with points^3,
#: so the default is 64 of the 128 samples; context_points=128 roughly quadruples it.
CONTEXT_POINTS = 64
ROTATION_STEPS = 24
R_MIN_FACTOR = 0.125
R_MAX_FACTOR = 2.0
EPSILON = 1e-6
ORIENTATION_MODES = ("canonical", "sample")
#: ``(lambda_1 - lambda_2) / (lambda_1 + lambda_2)`` below this means the two
#: principal axes are indistinguishable, so the canonical frame is picked by
#: floating-point noise rather than by the shape.
ANISOTROPY_TOLERANCE = 0.05
#: ``mean((p . u)^3)`` this small means no odd moment can tell the two directions of
#: the major axis apart, so the sign convention falls back to a frame-dependent
#: tie-break and reports that it did.
SIGN_MOMENT_TOLERANCE = 1e-9
#: The mirrored pass must beat the direct one by this much to be reported as the
#: winner, so a tie keeps the direct match (E0's convention).
MIRROR_MARGIN = 1e-9

_DEFAULT_PARAMS: dict[str, Any] = {
    "radial_bins": RADIAL_BINS,
    "angular_bins": ANGULAR_BINS,
    "context_points": CONTEXT_POINTS,
    "r_min_factor": R_MIN_FACTOR,
    "r_max_factor": R_MAX_FACTOR,
    "epsilon": EPSILON,
    "orientation": "sample",
    "rotation_steps": ROTATION_STEPS,
}

#: What each orientation policy actually claims about rotation.
_ORIENTATION_TEXT: dict[str, str] = {
    "canonical": (
        "assumed away: every contour is rotated onto its own principal axes, so any "
        "rotation is removed for one assignment per candidate - but only where that "
        "frame is identifiable, which excludes near-isotropic shapes and shapes whose "
        "odd moments cannot fix the axis sign (both reported per candidate)"
    ),
    "sample": (
        "searched, not assumed: the query is rotated through a fixed grid of relative "
        "rotations and the cheapest is kept and reported, so invariance holds only up "
        "to half a grid step. Not exact beyond that, and not a function of the relative "
        "rotation alone either: the log-polar bin grid is fixed to the coordinate frame, "
        "so a common rotation of both shapes re-bins both histograms against static "
        "edges and changes the score. The score is also not symmetric in its arguments: "
        "measured over the 780 unordered pairs of the first 40 gallery members at the "
        "defaults, |d(A,B) - d(B,A)| has median 0.0061, mean 0.0092 and maximum 0.0610 "
        "on a 0-1 score, and 45 of the 780 pairs agree exactly"
    ),
}


class ShapeContextError(ValueError):
    """A shape-context call was outside its own parameter domain."""


@dataclass(frozen=True)
class _Grid:
    """One resolved set of descriptor parameters — validated, never implicit."""

    radial_bins: int
    angular_bins: int
    context_points: int
    r_min_factor: float
    r_max_factor: float
    epsilon: float
    orientation: str
    rotation_steps: int
    ignored: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bins(self) -> int:
        return self.radial_bins * self.angular_bins

    @property
    def step_deg(self) -> float:
        return 360.0 / self.rotation_steps if self.orientation == "sample" else 0.0

    def call_params(self) -> dict[str, Any]:
        """The keywords that reproduce this grid — safe to feed back into the API."""
        return {
            "radial_bins": self.radial_bins,
            "angular_bins": self.angular_bins,
            "context_points": self.context_points,
            "r_min_factor": self.r_min_factor,
            "r_max_factor": self.r_max_factor,
            "epsilon": self.epsilon,
            "orientation": self.orientation,
            "rotation_steps": self.rotation_steps,
        }

    def as_dict(self) -> dict[str, Any]:
        reported = dict(self.call_params())
        reported["log_polar_bins"] = self.bins
        reported["rotation_step_deg"] = self.step_deg
        reported["ignored_params"] = list(self.ignored)
        return reported


def _positive_int(value: Any, name: str) -> int:
    number = int(value)
    if number < 1:
        raise ShapeContextError(f"{name} must be >= 1, got {value!r}")
    return number


def resolve_params(**params: Any) -> _Grid:
    """Validate descriptor parameters and freeze them into one :class:`_Grid`.

    Keywords this method does not own are accepted and ignored, because the comparison
    runner hands every method the same ``params`` mapping — but they are listed in
    ``ignored_params`` rather than vanishing, so a misspelled knob cannot make a
    reported policy differ from the run that produced the numbers.
    """
    known = {key: value for key, value in params.items() if key in _DEFAULT_PARAMS}
    ignored = tuple(sorted(set(params) - set(_DEFAULT_PARAMS)))
    settings = dict(_DEFAULT_PARAMS)
    settings.update(known)
    low = float(settings["r_min_factor"])
    high = float(settings["r_max_factor"])
    if not 0.0 < low < high:
        raise ShapeContextError(
            f"need 0 < r_min_factor < r_max_factor, got {settings['r_min_factor']!r}, "
            f"{settings['r_max_factor']!r}"
        )
    guard = float(settings["epsilon"])
    if not guard > 0.0:
        raise ShapeContextError(f"epsilon must be > 0, got {settings['epsilon']!r}")
    orientation = str(settings["orientation"])
    if orientation not in ORIENTATION_MODES:
        raise ShapeContextError(
            f"orientation must be one of {ORIENTATION_MODES}, got {settings['orientation']!r}"
        )
    return _Grid(
        radial_bins=_positive_int(settings["radial_bins"], "radial_bins"),
        angular_bins=_positive_int(settings["angular_bins"], "angular_bins"),
        context_points=_positive_int(settings["context_points"], "context_points"),
        r_min_factor=low,
        r_max_factor=high,
        epsilon=guard,
        orientation=orientation,
        rotation_steps=_positive_int(settings["rotation_steps"], "rotation_steps"),
        ignored=ignored,
    )


def rotation_grid_deg(steps: int = ROTATION_STEPS) -> np.ndarray:
    """The relative rotations searched by ``orientation="sample"``, in degrees."""
    count = _positive_int(steps, "rotation_steps")
    return np.arange(count, dtype=float) * (360.0 / count)


def _sample_indices(n_points: int, count: int) -> np.ndarray:
    """Deterministic, evenly spread subset of the ring's sample indices."""
    if count > n_points:
        raise ShapeContextError(
            f"context_points={count} exceeds the {n_points} samples of the standardized ring"
        )
    return np.linspace(0, n_points, count, endpoint=False, dtype=int)


def _radial_edges(radii: np.ndarray, count: int, grid: _Grid) -> np.ndarray:
    """Geometric edges over ``[r_min_factor*R, r_max_factor*R]``, R = mean pair distance.

    ``radii`` is the full ``count x count`` matrix including its zero diagonal, so the
    mean is taken over the ``count * (count - 1)`` *distinct* ordered pairs — averaging
    the diagonal in would shrink R by 1.6% and make the "[R/8, 2R]" claim in POLICY
    false by exactly that much.
    """
    pairs = count * (count - 1)
    mean_distance = float(radii.sum() / pairs) if pairs > 0 else 0.0
    if not mean_distance > 0.0 or not np.isfinite(mean_distance):
        raise ShapeContextError("point set has zero mean pairwise distance; no scale to use")
    return np.exp(
        np.linspace(
            np.log(mean_distance * grid.r_min_factor),
            np.log(mean_distance * grid.r_max_factor),
            grid.radial_bins + 1,
        )
    )


def shape_contexts(points: Any, *, grid: _Grid | None = None, **params: Any) -> np.ndarray:
    """The ``(k, radial*angular)`` context histogram of one contour's sample points.

    Row ``i`` is the log-polar histogram of the vectors running from sample ``i`` to
    every *other* sample, normalised to sum 1 (the paper's own step).

    No centroid removal and no rescaling happen here: relative vectors are
    translation-free by construction and the radial grid is measured in units of this
    point set's own mean pairwise distance, so the descriptor is invariant to both on
    its own merits, independently of what the standardizer already did.
    """
    settings = grid if grid is not None else resolve_params(**params)
    ring = check_contour(points, "points")
    samples = ring[_sample_indices(ring.shape[0], settings.context_points)]
    count = samples.shape[0]
    delta = samples[None, :, :] - samples[:, None, :]
    radii = np.sqrt(np.sum(delta * delta, axis=2))
    edges = _radial_edges(radii, count, settings)
    # side="right" - 1 puts the excluded self-pair (r = 0) in the innermost bin, where
    # the diagonal mask then drops it; pairs beyond the range saturate in the ends.
    radial_bins = np.clip(
        np.searchsorted(edges, radii, side="right") - 1, 0, settings.radial_bins - 1
    )
    angles = np.arctan2(delta[..., 1], delta[..., 0])
    # arctan2 returns (-pi, pi], so floor() sends the +pi wrap to bin `angular_bins`;
    # the clip is what makes that wrap land in the last bin instead of out of range.
    angular_bins = np.clip(
        np.floor((angles + np.pi) * (settings.angular_bins / (2.0 * np.pi))),
        0,
        settings.angular_bins - 1,
    ).astype(np.int64)
    total = settings.bins
    flat = radial_bins.astype(np.int64) * settings.angular_bins + angular_bins
    rows = np.broadcast_to(np.arange(count, dtype=np.int64)[:, None] * total, (count, count))
    off_diagonal = ~np.eye(count, dtype=bool)
    counts = np.bincount(
        (rows[off_diagonal] + flat[off_diagonal]).ravel(), minlength=count * total
    ).reshape(count, total)
    masses = counts.sum(axis=1, dtype=float)
    # A row is empty only when every other point coincides with it; keep it zero rather
    # than 0/0 = NaN, and let chi_square_costs' epsilon guard absorb it.
    return counts / np.where(masses > 0.0, masses, 1.0)[:, None]


def _rotate(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(float(angle_deg))
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ rotation.T, dtype=float)


def canonical_frame(points: Any) -> tuple[np.ndarray, dict[str, Any]]:
    """Rotate a contour onto its principal axes, resolving the eigenvector sign.

    ``numpy.linalg.eigh`` fixes an eigenvector's magnitude but not its sign, so
    without a rule the same shape can be described in a frame flipped by 180 degrees —
    silently a different descriptor.  The resolver is the third central moment along
    the major axis, ``mean((p . u)^3)``: the axis points toward the lopsided end.  It
    is rotation-covariant, so it does not smuggle in a coordinate convention.

    What it cannot resolve, and does not claim to (both reported per contour):

    * **degenerate axes** — a circle, a regular polygon or a symmetric star has
      ``lambda_1 ~= lambda_2``, so *every* direction is principal and the choice is
      noise (``axes_degenerate``, threshold ``ANISOTROPY_TOLERANCE``);
    * **vanishing odd moment** — if the projection onto the major axis is symmetric
      then ``mean((p . u)^3) == 0`` and the two directions are genuinely
      indistinguishable, so the last-resort tie-break below (largest-magnitude
      component positive) depends on the input coordinate frame, i.e. in exactly those
      cases the canonical frame is *not* rotation-invariant (``sign_ambiguous``).

    Returns the contour in its canonical frame plus that provenance.
    """
    ring = check_contour(points, "points")
    centered = ring - ring.mean(axis=0)
    covariance = centered.T @ centered / float(centered.shape[0])
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    major = np.asarray(eigenvectors[:, 1], dtype=float)
    minor = np.asarray(eigenvectors[:, 0], dtype=float)
    low = float(eigenvalues[0])
    high = float(eigenvalues[-1])
    anisotropy = (high - low) / (high + low) if high + low > 0.0 else 0.0
    third_moment = float(np.mean((centered @ major) ** 3))
    sign_ambiguous = abs(third_moment) <= SIGN_MOMENT_TOLERANCE
    if sign_ambiguous:
        pivot = int(np.argmax(np.abs(major)))
        if major[pivot] < 0.0:
            major = -major
    elif third_moment < 0.0:
        major = -major
    if major[1] < 0.0 or (major[1] == 0.0 and major[0] < 0.0):
        minor = -minor
    axis_deg = float(np.degrees(np.arctan2(major[1], major[0])))
    aligned = _rotate(centered, -axis_deg)
    return aligned, {
        "anisotropy": anisotropy,
        "axis_deg": axis_deg,
        "sign_moment": third_moment,
        "sign_ambiguous": bool(sign_ambiguous),
        "axes_degenerate": bool(anisotropy < ANISOTROPY_TOLERANCE),
        "minor_axis": [float(minor[0]), float(minor[1])],
    }


def _aligned(points: np.ndarray, grid: _Grid) -> tuple[np.ndarray, dict[str, Any]]:
    """The contour the descriptor is built from, under the orientation policy."""
    if grid.orientation == "canonical":
        return canonical_frame(points)
    return points, {}


def chi_square_costs(left: Any, right: Any, *, epsilon: float = EPSILON) -> np.ndarray:
    """Pairwise chi-square distance between two stacks of context histograms.

    ``d^2(a, b) = sum_k (a_k - b_k)^2 / (a_k + b_k + epsilon)`` over bins.  The
    ``+ epsilon`` is the addition to the paper's formula: a bin both histograms leave
    empty would otherwise be 0/0, so it is what keeps a degenerate all-zero context
    finite instead of NaN; on sum-to-1 histograms its bias is about 1e-4 relative.
    """
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.ndim != 2 or b.ndim != 2:
        raise ShapeContextError(f"context stacks must be 2-D, got {a.shape} and {b.shape}")
    if a.shape[1] != b.shape[1]:
        raise ShapeContextError(f"context bin counts disagree: {a.shape[1]} vs {b.shape[1]}")
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise ShapeContextError("context histograms contain non-finite values")
    guard = float(epsilon)
    if not guard > 0.0:
        raise ShapeContextError(f"epsilon must be > 0, got {epsilon!r}")
    costs = np.empty((a.shape[0], b.shape[0]), dtype=float)
    # One query row at a time: this is the hot path — rotation steps x candidates of it
    # — and the looped layout measurably beats the (n, n, bins) broadcast at this size.
    for index in range(a.shape[0]):
        row = a[index]
        difference = row[None, :] - b
        costs[index] = np.sum(difference * difference / (row[None, :] + b + guard), axis=1)
    return costs


def context_match_cost(left: Any, right: Any, *, epsilon: float = EPSILON) -> tuple[float, int]:
    """One-to-one assignment cost between two context stacks, per matched context.

    ``scipy.optimize.linear_sum_assignment`` minimises the *total* chi-square over
    bijections; dividing by the number of matched pairs makes the score independent of
    the sample count.  Two shapes whose context sets coincide score exactly 0, because
    the identity matching then has zero cost.
    """
    costs = chi_square_costs(left, right, epsilon=epsilon)
    rows, columns = linear_sum_assignment(costs)
    matched = int(rows.shape[0])
    if matched == 0:
        raise ShapeContextError("assignment matched no contexts")
    return float(costs[rows, columns].sum() / matched), matched


def _best_over_orientations(
    query_contexts: np.ndarray,
    angles: np.ndarray,
    candidate_contexts: np.ndarray,
    grid: _Grid,
) -> tuple[float, float, int]:
    """Cheapest ``(cost, angle, matched)`` of the search grid against one candidate."""
    best_cost = np.inf
    best_angle = 0.0
    matched = 0
    for index in range(query_contexts.shape[0]):
        cost, matched = context_match_cost(
            query_contexts[index], candidate_contexts, epsilon=grid.epsilon
        )
        if cost < best_cost:
            best_cost, best_angle = cost, float(angles[index])
    return float(best_cost), best_angle, matched


def _score_pair(
    query_contexts: np.ndarray,
    angles: np.ndarray,
    candidate_contexts: np.ndarray,
    mirror_contexts: np.ndarray | None,
    grid: _Grid,
    notes: Mapping[str, dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Best orientation/mirror combination for one query-candidate pair."""
    direct_cost, direct_angle, matched = _best_over_orientations(
        query_contexts, angles, candidate_contexts, grid
    )
    cost, angle, mirrored, mirror_cost = direct_cost, direct_angle, False, None
    if mirror_contexts is not None:
        candidate_mirror, mirror_angle, _ = _best_over_orientations(
            query_contexts, angles, mirror_contexts, grid
        )
        mirror_cost = candidate_mirror
        # Only a strict win flips the flag, so a tie stays on the direct match.
        if candidate_mirror < direct_cost - MIRROR_MARGIN:
            cost, angle, mirrored = candidate_mirror, mirror_angle, True
    detail: dict[str, Any] = {
        "score": float(cost),
        "orientation_mode": grid.orientation,
        # ``rotation_deg`` is the angle the *query* was turned through to reach the best
        # match, so the candidate sits at ``candidate_rotation_deg`` relative to the
        # query's own frame.  Always 0.0 in canonical mode, where no grid is searched.
        "rotation_deg": float(angle),
        "candidate_rotation_deg": float((-angle) % 360.0),
        "rotation_step_deg": grid.step_deg,
        "rotations_searched": int(query_contexts.shape[0]),
        "mirrored": bool(mirrored),
        "matched_contexts": matched,
        "direct_cost": direct_cost,
        "mirror_cost": float(mirror_cost) if mirror_cost is not None else None,
        "log_polar_bins": grid.bins,
    }
    for prefix, note in notes.items():
        for key, value in note.items():
            detail[f"{prefix}_{key}"] = value
    return float(cost), detail


def _query_frames(
    query: np.ndarray, grid: _Grid
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """``(angles_deg, contexts per angle, query frame note)`` under the policy.

    The search is over the *relative* rotation, and it is run on the query side so the
    library's descriptors are built once and only the query's are rebuilt per grid step
    — ``rotation_steps`` histogram builds per query, not per query x candidate.  Rotating
    the candidate through the same grid would be the same search in the paper's
    continuum sense but not numerically identical: the bin grid is fixed to the
    coordinate frame, so an absolute rotation of a pair does move mass across bin
    edges — that is the asymmetry POLICY reports, not a bug in the loop.

    Rotating the coordinates and re-binning is what makes each grid step exact; an
    angular-bin roll would only be right at multiples of the 24 deg bin width and would
    quietly misrepresent the 15 deg grid.
    """
    if grid.orientation == "canonical":
        aligned, note = _aligned(query, grid)
        return np.zeros(1), shape_contexts(aligned, grid=grid)[None, :, :], note
    angles = rotation_grid_deg(grid.rotation_steps)
    contexts = np.stack([shape_contexts(_rotate(query, angle), grid=grid) for angle in angles])
    return angles, contexts, {}


def _score_stack(
    query: Any, library: Any, *, allow_mirror: bool, grid: _Grid
) -> tuple[np.ndarray, list[dict[str, Any]], float]:
    """Score one query against every row of a library stack; library contexts built once."""
    started = time.perf_counter()
    rows = contour_stack(library, "library")
    a = check_contour(query, "query")
    if a.shape[0] != rows.shape[1]:
        raise ShapeContextError(
            f"sample count mismatch: query has {a.shape[0]}, library has {rows.shape[1]}"
        )
    angles, query_contexts, query_note = _query_frames(a, grid)
    frames = [_aligned(row, grid) for row in rows]
    candidate_contexts = np.stack([shape_contexts(row, grid=grid) for row, _ in frames])
    mirror_contexts = (
        np.stack(
            [
                shape_contexts(_aligned(mirror_points(row), grid)[0], grid=grid)
                for row, _ in frames
            ]
        )
        if allow_mirror
        else None
    )
    scores = np.empty(rows.shape[0], dtype=float)
    details: list[dict[str, Any]] = []
    for index in range(rows.shape[0]):
        mirror = mirror_contexts[index] if mirror_contexts is not None else None
        cost, detail = _score_pair(
            query_contexts,
            angles,
            candidate_contexts[index],
            mirror,
            grid,
            {"query": query_note, "candidate": frames[index][1]},
        )
        detail["row"] = index
        scores[index] = cost
        details.append(detail)
    return scores, details, time.perf_counter() - started


def distance(
    query: Any, candidate: Any, *, allow_mirror: bool = False, **params: Any
) -> tuple[float, dict[str, Any]]:
    """Shape-context distance between two standardized contours, plus what was fitted.

    Smaller is more similar.  The keywords are the descriptor parameters (see
    :func:`resolve_params`); the detail dict reports the winning relative rotation,
    whether the mirrored pass won and how many contexts were matched, so a results
    table can print the transform it relied on instead of hiding it.  Traversal
    direction is deliberately not part of this: the descriptor is a permutation-free set
    of histograms, so reversing the walk cannot be distinguished (the ``POLICY``
    traversal_direction entry states exactly when that is 0 and when the subsampling
    phase leaves a residual).
    """
    grid = resolve_params(**params)
    a = check_contour(query, "query")
    b = check_contour(candidate, "candidate")
    if a.shape[0] != b.shape[0]:
        raise ShapeContextError(
            f"query and candidate must share the sample count, got {a.shape[0]} vs {b.shape[0]}"
        )
    angles, query_contexts, query_note = _query_frames(a, grid)
    aligned_candidate, candidate_note = _aligned(b, grid)
    candidate_contexts = shape_contexts(aligned_candidate, grid=grid)
    mirror_contexts = (
        shape_contexts(_aligned(mirror_points(aligned_candidate), grid)[0], grid=grid)
        if allow_mirror
        else None
    )
    return _score_pair(
        query_contexts,
        angles,
        candidate_contexts,
        mirror_contexts,
        grid,
        {"query": query_note, "candidate": candidate_note},
    )


def distance_library(
    query: Any, library: Any, *, allow_mirror: bool = False, **params: Any
) -> np.ndarray:
    """Shape-context distances from one query to every library row, in row order."""
    grid = resolve_params(**params)
    scores, _, _ = _score_stack(query, library, allow_mirror=allow_mirror, grid=grid)
    return scores


def score_library(
    query: Any, library: Any, *, allow_mirror: bool = False, **params: Any
) -> tuple[np.ndarray, dict[str, Any]]:
    """ ``(distances, policy + transforms)`` for every row of ``library``.

    The wall-clock, stated rather than hidden: the default ``orientation="sample"`` runs
    ``rotation_steps`` chi-square tensors *and* ``rotation_steps``
    ``linear_sum_assignment`` calls **per candidate** — 24x the matching work of
    ``orientation="canonical"``.  Measured on this machine at the defaults against a
    179-member gallery: about 7 s per query in sample mode, about 0.4 s in canonical
    mode, so a full 18-query pass is minutes instead of seconds.  That is the price of
    not assuming an orientation convention, and ``elapsed_seconds`` is recorded per call
    so the experiment's timing column reads the measurement instead of this sentence.
    """
    grid = resolve_params(**params)
    scores, details, elapsed = _score_stack(query, library, allow_mirror=allow_mirror, grid=grid)
    return scores, {
        "policy": describe_policy(**grid.call_params()),
        "transforms": details,
        "params": grid.as_dict(),
        "elapsed_seconds": round(elapsed, 6),
        "seconds_per_candidate": round(elapsed / max(scores.shape[0], 1), 9),
    }


@dataclass(frozen=True)
class LibraryDescriptors:
    """Candidate context sets for one library, built once and reusable across queries.

    This is the precompute half of a two-stage retriever: :func:`_score_stack` already
    builds these once *per call*, so the saving is the repeat work across queries, not
    per candidate.  The arrays are the same objects that path would have produced — no
    descriptor is approximated, truncated or re-binned to make the cache smaller.
    """

    contexts: np.ndarray
    candidate_notes: list[dict[str, Any]]
    mirror_contexts: np.ndarray | None
    sample_count: int
    grid_params: dict[str, Any]
    build_seconds: float

    @property
    def rows(self) -> int:
        return int(self.contexts.shape[0])

    @property
    def bytes_used(self) -> int:
        owned = int(self.contexts.nbytes)
        if self.mirror_contexts is not None:
            owned += int(self.mirror_contexts.nbytes)
        return owned


def build_library_descriptors(
    library: Any, *, allow_mirror: bool = False, **params: Any
) -> LibraryDescriptors:
    """Precompute every candidate's context set (and its mirror) for one library.

    The returned object carries its own build wall-clock and byte count so a results
    table can charge the precompute to the arm that uses it instead of quoting a
    query time that quietly excludes it.
    """
    grid = resolve_params(**params)
    started = time.perf_counter()
    rows = contour_stack(library, "library")
    frames = [_aligned(row, grid) for row in rows]
    contexts = np.stack([shape_contexts(row, grid=grid) for row, _ in frames])
    mirror_contexts = (
        np.stack(
            [
                shape_contexts(_aligned(mirror_points(row), grid)[0], grid=grid)
                for row, _ in frames
            ]
        )
        if allow_mirror
        else None
    )
    return LibraryDescriptors(
        contexts=contexts,
        candidate_notes=[note for _, note in frames],
        mirror_contexts=mirror_contexts,
        sample_count=int(rows.shape[1]),
        grid_params=grid.call_params(),
        build_seconds=time.perf_counter() - started,
    )


def score_library_from_descriptors(
    query: Any,
    cached: LibraryDescriptors,
    indices: Any = None,
    *,
    allow_mirror: bool = False,
    **params: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Score one query against cached candidate descriptors, optionally a subset.

    Same loop, same ``_score_pair``, same grid as :func:`score_library`; only the
    candidate context arrays come from the cache.  ``indices`` selects rows in cache
    order, which is what a coarse filter hands over — the distances of the selected
    rows are numerically identical to the full-library call, and a test pins that.
    """
    grid = resolve_params(**params)
    if grid.call_params() != cached.grid_params:
        raise ShapeContextError(
            "the cache was built with a different descriptor grid than this query uses: "
            f"{cached.grid_params} vs {grid.call_params()}"
        )
    if allow_mirror and cached.mirror_contexts is None:
        raise ShapeContextError("this cache holds no mirrored contexts; rebuild with allow_mirror")
    a = check_contour(query, "query")
    if a.shape[0] != cached.sample_count:
        raise ShapeContextError(
            f"sample count mismatch: query has {a.shape[0]}, cache has {cached.sample_count}"
        )
    selection = (
        np.arange(cached.rows, dtype=int)
        if indices is None
        else np.asarray(indices, dtype=int)
    )
    if selection.ndim != 1:
        raise ShapeContextError(f"indices must be 1-D, got shape {selection.shape}")
    rows = [int(row) for row in selection]
    if rows and (min(rows) < 0 or max(rows) >= cached.rows):
        raise ShapeContextError(
            f"indices out of range for a {cached.rows}-row cache: {min(rows)}..{max(rows)}"
        )
    started = time.perf_counter()
    angles, query_contexts, query_note = _query_frames(a, grid)
    scores = np.empty(len(rows), dtype=float)
    details: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        # ``allow_mirror`` gates the scoring, not just the error above: a cache that HOLDS
        # reflected contexts must not quietly opt every query into them. ``POLICY`` promises
        # "mirror: only when allow_mirror=True", and the uncached path honours it per call.
        held = cached.mirror_contexts
        mirror = held[row] if allow_mirror and held is not None else None
        cost, detail = _score_pair(
            query_contexts,
            angles,
            cached.contexts[row],
            mirror,
            grid,
            {"query": query_note, "candidate": cached.candidate_notes[int(row)]},
        )
        detail["row"] = int(row)
        scores[position] = cost
        details.append(detail)
    elapsed = time.perf_counter() - started
    return scores, {
        "policy": describe_policy(**grid.call_params()),
        "transforms": details,
        "params": grid.as_dict(),
        "elapsed_seconds": round(elapsed, 6),
        "seconds_per_candidate": round(elapsed / max(scores.shape[0], 1), 9),
        "cache_build_seconds": round(cached.build_seconds, 6),
        "cache_bytes": cached.bytes_used,
        "cache_rows": cached.rows,
        "rows_scored": int(scores.shape[0]),
    }


POLICY: dict[str, Any] = {
    "id": "E3",
    "name": "Shape Context (Belongie et al. 2002), reference implementation in this repo",
    "implementation": "this project's own NumPy + SciPy code. OpenCV contrib is not used "
    "and is not presented as the implementation, because the installed "
    "opencv-python-headless 5.0.0 in this environment has no "
    "cv2.createShapeContextDistanceExtractor (checked against the live install).",
    "score": "mean over the one-to-one matched context pairs of the chi-square distance "
    "between their log-polar histograms (smaller is closer; exactly 0 when the two "
    "context sets coincide under this policy, unrelated shapes sit near O(1); an internal "
    "cost, not a similarity percentage and not a probability)",
    "descriptor": "per sampled point, a log-polar histogram of the relative positions of "
    "every other sampled point: 5 radial bins geometrically spaced over [R/8, 2R] where R "
    "is the point set's mean pairwise distance, and 15 angular bins over the full 2*pi "
    "(24 deg each); every histogram row is normalised to sum 1; radii outside the range "
    "are clipped into the end bins instead of dropped",
    "points": "64 of the 128 standardized ring samples (every second one), exposed as "
    "context_points",
    "matching": "chi-square histogram distance, then a one-to-one assignment with "
    "scipy.optimize.linear_sum_assignment; total cost divided by the number of matched "
    "contexts",
    "orientation": _ORIENTATION_TEXT["sample"],
    "invariance": {
        "translation": "exact - the descriptor uses relative positions only",
        "uniform_scale": "exact - the radial grid is in units of the point set's own mean "
        "pairwise distance, on top of the standardizer's unit RMS radius",
        "rotation": _ORIENTATION_TEXT["sample"],
        "start_point": "invariant - no index correspondence is assumed; the assignment finds "
        "the pairing, which is what makes the descriptor tolerant of a wobbly trace. Exact "
        "when context_points covers every sample; at the default 64-of-128 subsampling a "
        "shifted start selects a different phase of the same ring, which leaves a small "
        "residual (see traversal_direction)",
        "traversal_direction": "NOT a control. The contexts are a correspondence-free set "
        "of histograms and the assignment finds the pairing, so permuting the samples of a "
        "contour - reversing the walk included - leaves the score exactly unchanged "
        "(verified with context_points equal to the ring's sample count). E3 is therefore "
        "direction-blind and cannot stand in for the reversal probe the shared contract "
        "wants measured separately. At the default 64-of-128 subsampling a reversed ring is "
        "sampled at the opposite phase, which is a different point set, so a small residual "
        "survives: a sampling-phase artefact, not a direction sensitivity.",
        "mirror": "only when allow_mirror=True (off by default, same policy as E0 and the "
        "product path); with it off a mirrored candidate is scored as a different shape",
    },
    "deviations_from_the_paper": [
        "the curvature penalty term of the paper's objective is omitted (chi-square only)",
        "the total assignment cost is divided by the number of matched contexts",
        "a positive epsilon is added to the chi-square denominator so an empty bin cannot "
        "divide by zero (1e-6 on sum-to-1 histograms)",
        "pairs beyond the radial range are clipped into the last bin instead of discarded",
        "5x15 log-polar bins instead of the paper's 5x20, and 64 outline samples instead "
        "of 100",
        "the rotation search is exhaustive over the reported grid with the winner "
        "reported, not the paper's estimate-and-refine on matched-point displacement",
        "the canonical orientation policy is this implementation's addition: the paper's "
        "contexts are rotation-covariant and assume an already aligned frame",
    ],
    "cost": "per candidate and per orientation step searched: one chi-square tensor of "
    "context_points^2 x log_polar_bins (64^2 x 75 at the defaults) plus one "
    "linear_sum_assignment of that size; the default sample mode is 24x canonical. The "
    "library's descriptors are built once per gallery and the query's rotated variants "
    "once per query. Measured here at the defaults, 179 candidates per query: about 7 s "
    "sample mode, about 0.4 s canonical.",
    "measurements": "the asymmetry census and the two timings quoted above are re-derived by "
    "`python experiments/p0_perceptual_probe/verify_policy_claims.py` (it reads the "
    "gitignored `gallery.jsonl`, which `scripts/prepare_perceptual_dataset.py --offline` "
    "rebuilds; `--pairs` sets how many members the census uses), so a change that moves a "
    "quoted digit fails that run instead of leaving the sentence behind",
    "new_code_in_this_checkpoint": True,
}


def describe_policy(**params: Any) -> dict[str, Any]:
    """What E3 will actually do, with the rotation claim matching the orientation mode.

    Takes the same keywords as :func:`distance`.  The prose entries of ``POLICY``
    describe the defaults; ``params`` in the returned dict carries this call's effective
    values, so a table cannot print a policy the run did not use.
    """
    grid = resolve_params(**params)
    policy = dict(POLICY)
    policy["orientation"] = _ORIENTATION_TEXT[grid.orientation]
    policy["invariance"] = dict(POLICY["invariance"], rotation=_ORIENTATION_TEXT[grid.orientation])
    policy["params"] = grid.as_dict()
    return policy
