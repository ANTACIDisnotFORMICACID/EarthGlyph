"""E1 — a ShapeComp-*inspired* battery of classical shape descriptors.

What this is, and what it is not
--------------------------------
This arm is **inspired by** ShapeComp (Morgenstern, Hartmann, Schmidt, Tiedemann,
Prokott, Maiello & Fleming, *An image-computable model of human visual shape
similarity*, PLOS Comput. Biol. 17(6):e1008981, 2021).  **It is not the ShapeComp
model, and it must never be cited as "the ShapeComp model", as a reproduction of
it, or as evidence for any of its published correlations.**  The concrete
differences, each a limitation a reader needs before quoting a number from this
arm (primary-source audit: ``experiments/p0_perceptual_probe/shapecomp_research.md``):

1. **different descriptor set.**  ShapeComp uses 109 published descriptors.  This
   module keeps the families that audit ranked as portable — simple region
   statistics (families 1-16 in spirit), curvature-at-scale (23-26), shape
   signatures and chord/tangent histograms (29-38), elliptic Fourier (39-49) — and
   drops the rest: no medial-axis skeleton (88-102), no surprisal (50-51), no
   Fourier-of-signature (103-109), no GAN shapes, no image-intensity moments
   (17-22).  The exact count is measured from the code into :data:`POLICY`, never
   written by hand.
2. **no 25,712-animal corpus.**  ShapeComp scales each descriptor distance by the
   maximum over that corpus; those constants are unpublished and the silhouettes
   were purchased stock images (Shutterstock/Evermotion) this project may neither
   download nor redistribute.  The normalizer here is therefore fitted to
   **whatever library is handed in** (:func:`fit_normalizer`); its numbers are
   corpus-dependent and comparable only within one run.
3. **no 22-D space.**  No classical MDS, no Procrustes step, no 22-dimensional
   projection: distances stay in descriptor space, so nothing here reproduces the
   ShapeComp space or its reported r = 0.91 against human ratings.
4. **no neural network.**  No MatNet/KerNet, no weights (the official MATLAB
   engine is only on sites.google.com, unreachable from this machine — see the
   audit, section H).
5. **vector, not raster.**  Their descriptors (1)-(22) come from MATLAB
   ``regionprops`` on a rasterised image; everything here is computed on the
   sampled vector contour, with region integrals evaluated exactly for a polygon,
   so there is no canvas resolution to depend on and no ``cv2`` dependency.
6. **re-derived formulas, not a port.**  Where a normalisation choice existed
   (``extent``, the elliptic Fourier angles, the tangent histogram's orientation)
   the option taken and the option rejected are written down below and in
   :data:`POLICY`.

Contract and the two-stage scale policy
---------------------------------------
Input is the shared contract: an ``(n, 2)`` array, closed CCW, centroid-centred,
unit RMS radius (:func:`earthglyph.data.perceptual_gallery.standardize`).
:func:`prepare` re-applies winding, centring and RMS scaling defensively, so the
invariances claimed below hold even if a caller forgets; for conforming input it
is a no-op.

* stage 1 — every descriptor is computed in the standardized frame, so lengths
  (area, perimeter, chords, radii, EFD semi-axes) are dimensionless in units of
  the RMS radius, and ratios/angles/event shares are scale-free by definition;
* stage 2 — before any distance, each descriptor is divided by a **corpus-fitted**
  spread (``method="minmax"`` default, mirroring ShapeComp's 0-1 scaling intent;
  ``method="robust"`` = median/IQR as an alternative).

Stage 2 is fitted on a *corpus*, never on the query: a descriptor scaled by the
query's own values cannot express "this corpus varies a lot on that feature", so
that would be a much weaker claim.  :func:`score_library` therefore takes an
explicit ``normalizer``; when it is ``None`` the function self-fits on the library
it is given and **reports it** in the returned records
(``normalizer["self_fit"] is True`` with ``fitted_on`` naming the library).
:func:`distance` without a normalizer says ``"none (raw descriptor units)"``.

Groups and ablation
-------------------
Descriptors are organised into named groups (``global``, ``moments``,
``curvature``, ``fourier``, ``histogram``) and every entry point takes
``groups=...``, so "which feature group actually earns the accuracy" is a
parameter, not a code edit.  Group sizes are unequal, so the plain Euclidean
combination weights them unequally by dimension count; no fusion weights are
invented — that inequality is what the ablation measures.

Invariances, per mechanism
--------------------------
* translation — exact: re-centroided in :func:`prepare`, and every feature uses
  differences or central integrals.
* uniform scale — exact: re-scaled to unit RMS radius in :func:`prepare`.
* rotation — exact for ``global``, ``moments``, ``curvature``, ``fourier`` and for
  the chord/radius histograms (multisets of rotation-invariant lengths).  The
  **tangent-angle histogram is rotation covariant** (a rotation by ``a`` shifts it
  cyclically by ``a``), so it is measured in a canonical frame: the region's major
  principal axis rotated onto ``+x``, sign fixed by the normalised third moment.
  Cost: one exact polygon-moment pass plus one 2x2 eigenpair — no angular search.
  The rejected alternative was a cyclic search over ``K`` sampled angles inside the
  distance, about ``K x library_size`` extra descriptor extractions per query.
  Honest consequence: the frame is only as well defined as the principal axis.  For
  a near-isotropic contour (``lambda2/lambda1 >= ORIENTATION_ISOTROPY_MAX``) there is
  no principal axis, the frame falls back to the input frame, and that block is
  **not** rotation invariant; :func:`orientation_statuses` reports the flag per
  candidate.  The set is bigger than it looks — any 3-fold-or-higher rotationally
  symmetric shape qualifies, so it is not only the circle and the square: measured,
  ``star(5)`` gives ``lambda2/lambda1 = 0.99868`` and falls back, which is why the
  gallery's stars are the worst case for this arm rather than an edge case.
  ``orientation="raw"`` leaves the covariant block in the input frame on purpose,
  for measuring what the frame buys.
* start point — exact for every group: each feature is a statistic over the whole
  closed loop, never an indexed sequence.
* traversal direction — exact: :func:`prepare` re-winds with the project's
  :func:`earthglyph.geometry.resampling.ensure_ccw`, so the signed turning angle
  always means the interior-angle deficit (convex positive) and a reversed ring is
  the same contour.  This is stronger than E0's policy, which treats reversal as a
  separate probe.
* mirror — not claimed.  Length, magnitude and ratio descriptors and the chord/radius
  histograms are mirror-*blind*: they cannot separate a chiral shape from its reflection
  (the D9/D26 finding for amplitude descriptors applies here unchanged).  Of Hu's
  invariants only the seventh is reflection-odd — under ``y -> -y`` the six emitted here
  are even, which the mirror test checks — so omitting it makes the moments block
  mirror-blind too, and the only block that moves is the tangent histogram.
  ``allow_mirror=True`` scores ``min(d(q, c), d(q, mirror(c)))`` — a real
  mirror-equivalence test that needs no search, at twice the library feature cost,
  reported per candidate.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from earthglyph.features.fourier import harmonic_limit
from earthglyph.features.geometry_stats import (
    convex_hull_points,
    rotated_rectangle_extents,
    shape_metrics,
)
from earthglyph.geometry.normalization import normalize_contour
from earthglyph.geometry.resampling import ensure_ccw, ring_perimeter, signed_area
from earthglyph.retrieval.perceptual_methods import check_contour, contour_stack

__all__ = [
    "CHORD_AXIS_MAX",
    "CHORD_BINS",
    "CURVATURE_CONCAVE_MAX",
    "CURVATURE_CONVEX_MIN",
    "CURVATURE_WINDOW_FRACTIONS",
    "DEFAULT_GROUPS",
    "DISTANCE_KINDS",
    "EFD_HARMONICS",
    "FeatureNormalizer",
    "GROUPS",
    "HU_INVARIANTS",
    "MOMENT_MAX_ORDER",
    "NORMALIZATION_METHODS",
    "ORIENTATION_ISOTROPY_MAX",
    "ORIENTATION_MODES",
    "POLICY",
    "PerceptualFeatureError",
    "PreparedContour",
    "RADIUS_AXIS_MAX",
    "RADIUS_BINS",
    "TANGENT_BINS",
    "TANGENT_WINDOW_FRACTION",
    "cache_stats",
    "central_moments",
    "descriptor_counts",
    "describe_policy",
    "descriptor_values",
    "distance",
    "distance_library",
    "efd_coefficients",
    "ellipse_axes",
    "feature_names",
    "features_for_contour",
    "features_for_library",
    "fit_normalizer",
    "hu_invariants",
    "orientation_statuses",
    "prepare",
    "principal_frame",
    "raw_moments",
    "score_library",
    "signed_turning",
]

# --- declared constants: every number a descriptor depends on lives here ---------

GROUPS: tuple[str, ...] = ("global", "moments", "curvature", "fourier", "histogram")
DEFAULT_GROUPS: tuple[str, ...] = GROUPS

#: Arc-length windows of the curvature block, as the fraction of the perimeter
#: spanned by the *pair* of chords meeting at a vertex (so the window reaches
#: fraction/2 of the perimeter to each side).  1/32 sees a nibble, 1/8 sees a lobe.
CURVATURE_WINDOW_FRACTIONS: tuple[float, ...] = (1 / 32, 1 / 16, 1 / 8)
#: A vertex counts as a convex event when its turning is at least this many times the
#: turning a *circle* shows at the same window.  After the baseline division the
#: circle's value is exactly 1.0, so one constant means the same thing at every
#: window, at every shape size and at every sampling count.
CURVATURE_CONVEX_MIN = 2.0
#: ... and concave when it turns backward by at least half that same reference, which
#: is what keeps pen jitter on a smooth outline out of the concave count.
CURVATURE_CONCAVE_MAX = -0.5

#: Elliptic Fourier harmonics kept (magnitudes only, see _fourier_block).
EFD_HARMONICS = 8
#: Region moments up to this total order feed the Hu invariants that are emitted.
MOMENT_MAX_ORDER = 3

CHORD_BINS = 16
RADIUS_BINS = 12
TANGENT_BINS = 12
#: Fixed axes in the standardized (unit RMS radius) frame.  A thin bar of unit RMS
#: radius spans sqrt(12) ~ 3.46, so 4.0 covers its longest chord; 3.0 the same for a
#: centroid radius.  Values beyond an axis are counted in the boundary bin *and*
#: reported as an explicit overflow share, so a fixed axis is a resolution choice and
#: not a place where shape detail disappears.
CHORD_AXIS_MAX = 4.0
RADIUS_AXIS_MAX = 3.0

#: Tangent directions are measured as a chord over this perimeter fraction.
TANGENT_WINDOW_FRACTION = 1 / 32
#: Above this lambda2/lambda1 the second moments carry no orientation at all.
ORIENTATION_ISOTROPY_MAX = 0.98
#: Normalised third moment whose sign decides the axis direction (tie-broken on the
#: other axis when it is numerically zero).
FLIP_TIE_TOLERANCE = 1e-9

NORMALIZATION_METHODS: tuple[str, ...] = ("minmax", "robust")
DISTANCE_KINDS: tuple[str, ...] = ("euclidean", "cityblock")
ORIENTATION_MODES: tuple[str, ...] = ("canonical", "raw")

NormalizationMethod = Literal["minmax", "robust"]
DistanceKind = Literal["euclidean", "cityblock"]
OrientationMode = Literal["canonical", "raw"]

_AREA_EPS = 1e-9
_CACHE_ENTRIES = 4
#: Below this magnitude a Hu invariant is reported as exactly 0.0 (see
#: :func:`hu_invariants`); it is the epsilon of the project-wide signed-log form.
_HU_LOG_EPSILON = 1e-20

#: The project's mirror convention (docs/DECISIONS.md D9): ``z -> conj(z)``, i.e.
#: ``y -> -y``, so ``allow_mirror`` reflects the same way alignment.py does.
_MIRROR_SIGN = np.array([1.0, -1.0])


class PerceptualFeatureError(ValueError):
    """Raised when a contour cannot carry this descriptor battery at all."""


# --- stage 1: the contour every descriptor is defined on ------------------------


@dataclass(frozen=True)
class PreparedContour:
    """One contour in the frame the descriptors are defined in."""

    ring: np.ndarray = field(repr=False)  # (n, 2) CCW, centroid at 0, unit RMS radius
    n_samples: int
    area: float
    perimeter: float
    central_moments: np.ndarray = field(repr=False)  # (4, 4) mu[p, q], p + q <= 3


def _open_ring(points: np.ndarray) -> np.ndarray:
    """Drop a repeated closing vertex, if the caller left one in."""
    if points.shape[0] >= 4 and np.allclose(points[0], points[-1], rtol=0.0, atol=1e-12):
        return points[:-1]
    return points


def prepare(points: Any, *, name: str = "contour") -> PreparedContour:
    """Bring one contour into the descriptor frame: CCW, centred, unit RMS radius.

    Idempotent on contract-conforming input and defensive about everything else, so
    the claimed translation/scale/winding invariance holds for any array rather than
    only for standardised ones.  Degenerate input raises instead of producing NaN: a
    NaN that reaches a distance turns a refused candidate into a plausible score.
    """
    array = check_contour(points, name)
    ring = _open_ring(array)
    try:
        scaled = normalize_contour(ensure_ccw(ring))
    except ValueError as exc:  # zero RMS radius, fewer than 3 points
        raise PerceptualFeatureError(f"{name} is not a region outline: {exc}") from exc
    area = abs(signed_area(scaled))
    perimeter = ring_perimeter(scaled)
    if area <= _AREA_EPS:
        raise PerceptualFeatureError(
            f"{name} encloses no area ({area:.3e} in the unit-RMS frame): the region "
            "descriptors of this battery are undefined for a line drawn back on itself"
        )
    if perimeter <= _AREA_EPS:
        raise PerceptualFeatureError(f"{name} has no perimeter ({perimeter:.3e})")
    return PreparedContour(
        ring=scaled,
        n_samples=int(scaled.shape[0]),
        area=area,
        perimeter=perimeter,
        central_moments=central_moments(scaled, MOMENT_MAX_ORDER),
    )


# --- exact polygon (region) moments ---------------------------------------------

_TERM_CACHE: dict[tuple[int, int], list[tuple[float, int, int, int, int, int, int]]] = {}


def _compositions(total: int) -> list[tuple[int, int, int]]:
    return [
        (a, b, total - a - b) for a in range(total + 1) for b in range(total - a + 1)
    ]


def _moment_terms(p: int, q: int) -> list[tuple[float, int, int, int, int, int, int]]:
    """Monomial coefficients of ``int int x**p y**q dA`` over one triangle.

    ``int_T lambda1**a lambda2**b lambda3**c dA = 2A * a!b!c!/(a+b+c+2)!`` with the
    *signed* triangle area ``A``; expanding ``x = sum(lambda_i x_i)`` and
    ``y = sum(lambda_i y_i)`` turns the integral into a finite monomial sum.
    Deriving it here instead of rasterising (what ShapeComp's ``regionprops``
    descriptors do) keeps the moments exact and free of any canvas resolution.
    """
    cached = _TERM_CACHE.get((p, q))
    if cached is not None:
        return cached
    terms: list[tuple[float, int, int, int, int, int, int]] = []
    for a, b, c in _compositions(p):
        for r, s, t in _compositions(q):
            ways = math.factorial(p) // (
                math.factorial(a) * math.factorial(b) * math.factorial(c)
            )
            ways *= math.factorial(q) // (
                math.factorial(r) * math.factorial(s) * math.factorial(t)
            )
            weight = (
                ways
                * math.factorial(a + r)
                * math.factorial(b + s)
                * math.factorial(c + t)
                / math.factorial(p + q + 2)
            )
            terms.append((weight, a, b, c, r, s, t))
    _TERM_CACHE[(p, q)] = terms
    return terms


def raw_moments(ring: np.ndarray, max_order: int = MOMENT_MAX_ORDER) -> np.ndarray:
    """``m[p, q] = int int x**p y**q dA`` over the polygon, exact for a simple ring.

    The ring is decomposed into the *signed* fan of triangles from vertex 0, i.e.
    ``(v_0, v_i, v_{i+1})`` for ``i = 1 .. n - 2``.  Interior edges cancel in the signed
    sum, so the result is the polygon integral for a concave outline as well; no
    interior triangulation is needed.  The fan must stop at ``v_{n-1}`` rather than wrap
    back to ``v_1``: a wrapped last triangle ``(v_0, v_{n-1}, v_1)`` is a real, large,
    spurious region, and the sum then depends on which vertex the ring starts at — a
    start-point dependence of ~1e-3 in ``mu[0, 2]``, which :func:`central_moments`
    inherits and which the start-point test would have failed.
    """
    count = ring.shape[0]
    if count < 3:
        raise PerceptualFeatureError(f"a polygon needs at least 3 vertices, got {count}")
    x1, y1 = float(ring[0, 0]), float(ring[0, 1])
    second = ring[1:-1]
    third = ring[2:]
    x2, y2 = second[:, 0], second[:, 1]
    x3, y3 = third[:, 0], third[:, 1]
    twice_area = x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)
    out = np.zeros((max_order + 1, max_order + 1), dtype=float)
    for order in range(max_order + 1):
        for p in range(order + 1):
            q = order - p
            acc = np.zeros(count - 2, dtype=float)
            for weight, ea, eb, ec, er, es, et in _moment_terms(p, q):
                acc += (
                    weight
                    * (x1**ea)
                    * (x2**eb)
                    * (x3**ec)
                    * (y1**er)
                    * (y2**es)
                    * (y3**et)
                )
            out[p, q] = float(np.dot(twice_area, acc))
    return out


def central_moments(ring: np.ndarray, max_order: int = MOMENT_MAX_ORDER) -> np.ndarray:
    """Region moments about the polygon's own area centroid (``mu[p, q]``).

    ``mu[1, 0] == mu[0, 1] == 0`` and ``mu[0, 0] == `` signed area, by construction;
    the tests check both rather than trusting the derivation.
    """
    raw = raw_moments(ring, max_order)
    area = float(raw[0, 0])
    if abs(area) <= _AREA_EPS:
        raise PerceptualFeatureError("polygon has no signed area: moments undefined")
    shifted = ring - np.array([raw[1, 0] / area, raw[0, 1] / area])
    return raw_moments(shifted, max_order)


#: The Hu invariants this battery emits, in order.  **The seventh is deliberately
#: absent** — :func:`hu_invariants` documents the measurement that decided it.
HU_INVARIANTS: tuple[str, ...] = ("I1", "I2", "I3", "I4", "I5", "I6")


def hu_invariants(mu: np.ndarray, area: float) -> np.ndarray:
    """Hu's first six moment invariants, in the signed ``log10`` form used project-wide.

    ``eta[p, q] = mu[p, q] / area**(1 + (p + q)/2)`` makes every term scale-free, and
    the combinations below are invariant to translation, uniform scale and rotation
    *by construction* — which is why this group needs no canonical frame.  The form is
    ``sign(h) * log10(|h| + 1e-20)``, as in :func:`earthglyph.retrieval.hu_baseline.log_hu`
    (the raw values span many orders of magnitude), with one documented change: an
    invariant whose magnitude falls under that epsilon is reported as exactly ``0.0``
    instead of ``±20``, because there the sign is round-off and a sign is all the log
    form can still see.

    **Why there are six and not seven.**  ``I7`` is the only reflection-odd Hu
    invariant, and it is the only one this arm cannot make an honest claim about.
    Two measurements, both reproduced by ``tests/test_perceptual_features.py`` from
    the same closed form.  (1) ``crescent()`` is a mirror-symmetric *curve* whose
    128 equal-arc-length *samples* are not mirror-symmetric (a mirrored vertex sits
    up to 0.014 from the vertex set), and ``I7`` reports ``-2.5e-8`` for it — a
    sampling artefact, since refining the sampling shrinks it (``1.5e-7`` at 64
    points, ``1.3e-9`` at 512, ``5.3e-10`` at 1024).  Eleven decades above the
    epsilon floor, it is emitted at face value as ``+7.6``, i.e. the descriptor
    would call a symmetric shape chiral.  (2) Where the samples *are* symmetric the
    float64 value is pure round-off: ``1e-30`` for a five-point star, ``1e-64`` for a
    six-point one, and its *sign* is chosen by the rotation angle (it flips across
    14 rotations of either).  The under-epsilon rule then reports a stable ``0.0`` —
    stable only because those magnitudes happen to fall under ``1e-20``; a contour
    whose near-symmetry lands ``I7`` between ``1e-20`` and ``1e-14`` emits
    ``±14`` to ``±20`` instead, and without the rule the same unrotated star spans
    59.9 log10 units across those 14 rotations (129.2 for the six-point star).
    ``I1``-``I6`` under the same rotations stay inside ``3.0e-7``.  A descriptor
    whose value is owned by the sampler and the frame would inject that term into
    every distance after stage-2 normalisation, so this arm emits six invariants and
    says so, rather than shipping seven slots and hoping.
    """
    if mu.shape[0] <= MOMENT_MAX_ORDER:
        raise PerceptualFeatureError("Hu invariants need central moments to order 3")
    if area <= _AREA_EPS:
        raise PerceptualFeatureError("Hu invariants need a positive area")
    orders = np.arange(MOMENT_MAX_ORDER + 1)[:, None] + np.arange(MOMENT_MAX_ORDER + 1)[None, :]
    eta = mu / area ** (1.0 + 0.5 * orders)
    e20, e02, e11 = float(eta[2, 0]), float(eta[0, 2]), float(eta[1, 1])
    e30, e03, e21, e12 = float(eta[3, 0]), float(eta[0, 3]), float(eta[2, 1]), float(eta[1, 2])
    a_plus = e30 + e12
    b_plus = e21 + e03
    a_minus = e30 - 3.0 * e12
    b_minus = 3.0 * e21 - e03
    values = (
        e20 + e02,
        (e20 - e02) ** 2 + 4.0 * e11**2,
        a_minus**2 + b_minus**2,
        a_plus**2 + b_plus**2,
        a_minus * a_plus * (a_plus**2 - 3.0 * b_plus**2)
        + b_minus * b_plus * (3.0 * a_plus**2 - b_plus**2),
        (e20 - e02) * (a_plus**2 - b_plus**2) + 4.0 * e11 * a_plus * b_plus,
    )
    array = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(array)):
        raise PerceptualFeatureError("Hu invariants came out non-finite")
    logged: list[float] = []
    for value in array:
        if abs(value) < _HU_LOG_EPSILON:
            # the sign is round-off here, and a sign is all the log form still sees
            logged.append(0.0)
        else:
            sign = 1.0 if value > 0.0 else -1.0
            logged.append(sign * math.log10(abs(value) + _HU_LOG_EPSILON))
    return np.array(logged, dtype=float)


# --- canonical frame for the one rotation-covariant block -----------------------


@dataclass(frozen=True)
class OrientationFrame:
    """How a contour was rotated, and whether that rotation meant anything."""

    angle_rad: float
    status: str  # "canonical" | "isotropic-fallback" | "raw"
    axis_ratio: float  # lambda2 / lambda1 of the region covariance
    flipped: bool
    ring: np.ndarray = field(repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "orientation_status": self.status,
            "orientation_angle_deg": round(math.degrees(self.angle_rad), 6),
            "orientation_flipped": self.flipped,
            "moment_axis_ratio": round(self.axis_ratio, 6),
        }


def _rotate(ring: np.ndarray, angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    matrix = np.array([[cosine, -sine], [sine, cosine]], dtype=float)
    return np.asarray(ring @ matrix.T, dtype=float)


def principal_frame(prep: PreparedContour, *, mode: str = "canonical") -> OrientationFrame:
    """Rotate the region's major principal axis onto ``+x``, with a documented sign rule.

    Only the tangent-angle histogram needs a frame.  Taking it from the second central
    moments costs one eigenpair on numbers already computed, which is why no angular
    search is performed.  Sign rule: axis alignment leaves a 180-degree ambiguity, so
    the frame is flipped when the normalised third moment along ``+x`` is negative
    (the fatter half of the shape then points to ``+x``); if that moment is numerically
    zero the same test runs along ``+y``; if both vanish the contour is
    centrosymmetric to third order and the flip leaves the tangent histogram fixed.
    When the second moments are near-isotropic there is no axis to align to and the
    input frame is kept — see the module docstring for what that costs.
    """
    check_orientation(mode)
    mu = prep.central_moments
    area = prep.area
    cov = np.array(
        [[mu[2, 0] / area, mu[1, 1] / area], [mu[1, 1] / area, mu[0, 2] / area]]
    )
    trace = float(cov[0, 0] + cov[1, 1])
    spread = math.sqrt(max(0.25 * (cov[0, 0] - cov[1, 1]) ** 2 + cov[0, 1] ** 2, 0.0))
    top, bottom = 0.5 * trace + spread, 0.5 * trace - spread
    ratio = float(bottom / top) if top > _AREA_EPS else 1.0
    if mode == "raw":
        return OrientationFrame(0.0, "raw", ratio, False, prep.ring)
    if ratio >= ORIENTATION_ISOTROPY_MAX:
        return OrientationFrame(0.0, "isotropic-fallback", ratio, False, prep.ring)
    vector = np.array([top - cov[1, 1], cov[0, 1]], dtype=float)
    if float(np.linalg.norm(vector)) <= 1e-15:
        vector = np.array([1.0, 0.0])
    angle = float(np.arctan2(vector[1], vector[0]))
    rotated = _rotate(prep.ring, -angle)
    moments = central_moments(rotated, 3)
    rotated_area = float(moments[0, 0])
    eta30 = float(moments[3, 0] / rotated_area**2.5)
    eta03 = float(moments[0, 3] / rotated_area**2.5)
    flipped = eta30 < -FLIP_TIE_TOLERANCE or (
        abs(eta30) <= FLIP_TIE_TOLERANCE and eta03 < -FLIP_TIE_TOLERANCE
    )
    if flipped:
        rotated = _rotate(rotated, math.pi)
        angle = float((angle + math.pi) % (2.0 * math.pi))
    return OrientationFrame(angle, "canonical", ratio, flipped, rotated)


# --- the descriptor groups -------------------------------------------------------


def _global_block(prep: PreparedContour, frame: OrientationFrame) -> dict[str, float]:
    """Simple region/boundary statistics (ShapeComp families (1)-(16) in spirit).

    ``compactness``, ``solidity`` and ``diameter_fill`` come from the project's own
    :func:`earthglyph.features.geometry_stats.shape_metrics`, so this arm and the
    gallery's stored metrics measure the same number.  ``extent`` uses the
    *minimum-area rotated* rectangle rather than the axis-aligned bounding box MATLAB's
    ``regionprops.Extent`` reports, because the axis-aligned version is orientation
    dependent and would make this group's rotation-invariance claim false.
    ``circularity`` is their descriptor (10), ``P**2/A``: deliberately kept beside
    ``compactness`` although both are functions of ``A`` and ``P``, because the
    published battery carries the same redundancy.  The cost is a doubled weight on
    the (A, P) direction in the Euclidean sum — a property of this arm, not a bug to
    hide.
    """
    metrics = shape_metrics(prep.ring)
    area, perimeter = prep.area, prep.perimeter
    hull = convex_hull_points(prep.ring)
    hull_perimeter = ring_perimeter(hull)
    long_side, short_side = rotated_rectangle_extents(prep.ring)
    if long_side * short_side <= _AREA_EPS:
        raise PerceptualFeatureError("enclosing rectangle has no area: extent undefined")
    covariance = np.array(
        [
            [prep.central_moments[2, 0], prep.central_moments[1, 1]],
            [prep.central_moments[1, 1], prep.central_moments[0, 2]],
        ]
    ) / area
    eigen = np.linalg.eigvalsh(covariance)
    top, bottom = float(eigen[1]), float(eigen[0])
    if top <= _AREA_EPS:
        raise PerceptualFeatureError("second moments collapse: no axis to measure")
    if bottom <= _AREA_EPS:
        raise PerceptualFeatureError(
            f"the contour has no width in its own minor direction (lambda2={bottom:.3e}); "
            "aspect and eccentricity are undefined rather than reported as infinity"
        )
    return {
        "global_area": area,
        "global_perimeter": perimeter,
        "global_compactness": float(metrics.compactness),
        "global_solidity": float(metrics.solidity),
        "global_convexity": hull_perimeter / perimeter,
        "global_extent": area / (long_side * short_side),
        "global_eccentricity": math.sqrt(max(0.0, 1.0 - bottom / top)),
        "global_moment_aspect": math.sqrt(top / bottom),
        "global_circularity": perimeter**2 / area,
        "global_diameter_fill": float(metrics.diameter_fill),
    }


def _moments_block(prep: PreparedContour, frame: OrientationFrame) -> dict[str, float]:
    """Hu's first six invariants of the exactly integrated polygon region.

    Deliberately *only* the invariants: the normalised central moments ``eta[p, q]``
    themselves are rotation covariant, so exposing them would either need a canonical
    frame or break this group's rotation claim.  The seventh invariant is omitted for
    the measured numerical reason given in :func:`hu_invariants`.  ``hu_baseline`` is
    not reused because its moments come from a rasterised mask through OpenCV; these are
    exact polygon integrals and this module must not require ``cv2``.
    """
    values = hu_invariants(prep.central_moments, prep.area)
    return {f"mom_hu_{index + 1}": float(values[index]) for index in range(values.shape[0])}


def signed_turning(ring: np.ndarray, window: int) -> np.ndarray:
    """Baseline-normalised signed turning angle of a CCW ring at an arc-length window.

    At vertex ``i`` the angle is taken between the incoming chord ``p_i - p_{i-k}`` and
    the outgoing chord ``p_{i+k} - p_i`` and divided by the value a *circle* gives at
    the same window (``2*pi*k/n``), so 1.0 means "turning exactly like a disc", larger
    is a convex corner and negative is a concave one.  Without that division a single
    threshold would mean something different at 1/32 and at 1/8 of the perimeter, which
    is how a curvature-at-scale battery becomes uninterpretable.  Positive means convex
    because the ring is CCW — the interior-angle deficit is a property of the shape, not
    of the walk.
    """
    count = ring.shape[0]
    if window < 1 or 2 * window >= count:
        raise PerceptualFeatureError(
            f"curvature window {window} does not fit in a {count}-point contour"
        )
    incoming = ring - np.roll(ring, window, axis=0)
    outgoing = np.roll(ring, -window, axis=0) - ring
    lengths = np.linalg.norm(incoming, axis=1) * np.linalg.norm(outgoing, axis=1)
    if not np.all(lengths > 0.0):
        raise PerceptualFeatureError(
            "two samples coincide on the contour, so a turning angle is undefined; "
            "resample through the shared standardizer"
        )
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = incoming[:, 0] * outgoing[:, 0] + incoming[:, 1] * outgoing[:, 1]
    return np.asarray(np.arctan2(cross, dot) * count / (2.0 * math.pi * window), dtype=float)


def _curvature_block(prep: PreparedContour, frame: OrientationFrame) -> dict[str, float]:
    """Curvature-at-scale statistics (ShapeComp families (23)-(26) as turning angle).

    Their (23)-(26) are the RMS error between a shape and its own blurred versions at
    kernel 2/4/8/16; this is the same multi-scale idea expressed as the signed turning
    angle, which needs no image and no blur kernel.  The event counts are the
    load-bearing part: at constant area and perimeter they are what separates a star
    from a disc, and they are rotation invariant because they count angles.  The mean
    of the *absolute* normalised turning is emitted rather than the signed mean,
    because the signed sum is pinned near 1 by the total-turning theorem and carries
    almost no information.
    """
    out: dict[str, float] = {}
    count = prep.n_samples
    for index, fraction in enumerate(CURVATURE_WINDOW_FRACTIONS):
        window = max(1, int(round(fraction * count / 2.0)))
        psi = signed_turning(prep.ring, window)
        label = f"{index:02d}_w{int(round(1.0 / fraction))}"
        out[f"curv{label}_mean_abs"] = float(np.mean(np.abs(psi)))
        out[f"curv{label}_std"] = float(np.std(psi))
        out[f"curv{label}_max"] = float(np.max(psi))
        out[f"curv{label}_min"] = float(np.min(psi))
        out[f"curv{label}_convex_share"] = float(np.mean(psi >= CURVATURE_CONVEX_MIN))
        out[f"curv{label}_concave_share"] = float(np.mean(psi <= CURVATURE_CONCAVE_MAX))
    return out


def efd_coefficients(ring: np.ndarray, n_harmonics: int = EFD_HARMONICS) -> np.ndarray:
    """Elliptic Fourier coefficients ``(a, b, c, d)`` per harmonic of the arc-length ring.

    The contour is piecewise linear in arc length, so each
    ``int x(t) cos(2*pi*n*t/T) dt`` has a closed form and no extra sampling
    approximation is inserted (the Kuhl-Giardina construction).  The contract's samples
    are uniform in arc length, so ``dt`` is the constant ``T/n_samples`` and the
    traversal time of the parameter interval is the perimeter.
    Returns an ``(n_harmonics, 4)`` float64 array of the Fourier-series coefficients
    ``a_n, b_n`` (for x) and ``c_n, d_n`` (for y).
    """
    limit = harmonic_limit(ring.shape[0])
    if not 1 <= n_harmonics <= limit:
        raise PerceptualFeatureError(
            f"n_harmonics={n_harmonics} outside the usable range for "
            f"{ring.shape[0]}-point contours (1..{limit})"
        )
    closed = np.vstack([ring, ring[:1]])
    step = np.diff(closed, axis=0)
    total = float(np.linalg.norm(step, axis=1).sum())
    if total <= _AREA_EPS:
        raise PerceptualFeatureError("contour has zero perimeter: no Fourier parameterisation")
    count = ring.shape[0]
    step_time = total / count
    knots = np.arange(count, dtype=float) * step_time
    start = closed[:-1]
    out = np.zeros((n_harmonics, 4), dtype=float)
    for harmonic in range(1, n_harmonics + 1):
        omega = 2.0 * math.pi * harmonic / total
        phase = omega * knots
        delta = omega * step_time
        sin_here, cos_here = np.sin(phase), np.cos(phase)
        sin_next, cos_next = np.sin(phase + delta), np.cos(phase + delta)
        for axis in (0, 1):
            value = start[:, axis]
            slope = step[:, axis] / step_time
            cos_integral = (
                value * (sin_next - sin_here)
                + slope * (step_time * sin_next + (cos_next - cos_here) / omega)
            ) / omega
            sin_integral = (
                value * (cos_here - cos_next)
                + slope * (-step_time * cos_next + (sin_next - sin_here) / omega)
            ) / omega
            out[harmonic - 1, 2 * axis] = 2.0 * float(cos_integral.sum()) / total
            out[harmonic - 1, 2 * axis + 1] = 2.0 * float(sin_integral.sum()) / total
    return out


def ellipse_axes(coefficients: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Semi-axes ``(lambda1, lambda2)`` of the ellipse traced by each harmonic.

    Harmonic ``n`` contributes ``[x; y] = R_n [cos; sin]`` with
    ``R_n = [[a_n, b_n], [c_n, d_n]]``, so its semi-axes are the singular values of
    ``R_n`` — the square roots of the eigenvalues of ``R_n R_n.T``, which is the
    closed form Kuhl and Giardina give.  ``tests/test_perceptual_features.py`` checks
    the closed form against ``np.linalg.svd`` so it cannot drift.
    """
    a, b = coefficients[:, 0], coefficients[:, 1]
    c, d = coefficients[:, 2], coefficients[:, 3]
    g = a**2 + b**2
    h = c**2 + d**2
    f = a * c + b * d
    middle = 0.5 * (g + h)
    spread = np.sqrt(np.maximum(0.25 * (g - h) ** 2 + f**2, 0.0))
    return np.sqrt(np.maximum(middle + spread, 0.0)), np.sqrt(np.maximum(middle - spread, 0.0))


def _fourier_block(prep: PreparedContour, frame: OrientationFrame) -> dict[str, float]:
    """Elliptic Fourier magnitudes, normalised so the first harmonic's major axis is 1.

    The classic Kuang/Rossi normalisation rotates the contour by ``-theta_1`` (the
    major-axis angle of the first harmonic's ellipse) and divides every coefficient by
    ``lambda_1,1``.  This arm applies the *invariant equivalent*: the semi-axis
    magnitudes ``lambda1_n, lambda2_n`` (singular values of the coefficient matrix)
    divided by ``lambda1_1``.  Those are unchanged both by an arbitrary rotation and by
    any cyclic start point, so the rotation claim does not rest on a frame choice, and
    the division by ``lambda1_1`` is exactly the "first harmonic's major axis = 1"
    scaling.  The price, stated plainly: the per-harmonic angles ``psi_n`` (major-axis
    direction) and ``theta_n`` (starting direction) are **not** emitted, because
    ``psi_n`` is defined only modulo pi and is ill-conditioned as a harmonic amplitude
    tends to zero (a near-circular contour), and ``theta_n`` is not start-point
    invariant.  So this group carries magnitudes only — the same trade the project's
    own amplitude Fourier descriptor makes (docs/DECISIONS.md D10) — but per harmonic
    *ellipse* instead of per harmonic magnitude, which does keep the polarisation, i.e.
    how circular each harmonic is.
    """
    coefficients = efd_coefficients(prep.ring, EFD_HARMONICS)
    top, bottom = ellipse_axes(coefficients)
    reference = float(top[0])
    if reference <= _AREA_EPS:
        raise PerceptualFeatureError("first harmonic collapses to a point: no scale to divide by")
    out: dict[str, float] = {"efd_minor_01": float(bottom[0] / reference)}
    for harmonic in range(2, EFD_HARMONICS + 1):
        index = harmonic - 1
        out[f"efd_major_{harmonic:02d}"] = float(top[index] / reference)
        out[f"efd_minor_{harmonic:02d}"] = float(bottom[index] / reference)
    return out


def _histogram_block(prep: PreparedContour, frame: OrientationFrame) -> dict[str, float]:
    """Chord-length, centroid-radius and tangent-angle histograms (families (29)-(38)).

    Fixed bin counts (``CHORD_BINS``, ``RADIUS_BINS``, ``TANGENT_BINS``) on fixed axes
    in the standardized frame, each bin reported as a *share* of the pairs or samples
    so the block does not depend on the pair count or the sample count.  Values beyond
    an axis fall in the boundary bin and are additionally reported as an explicit
    overflow share, so the fixed axis cannot lose shape detail silently.  Chord and
    radius histograms are rotation invariant because they are multisets of lengths; the
    tangent histogram is the one covariant feature of the battery and is measured in
    :func:`principal_frame` — read that function for when the frame is valid.
    ShapeComp uses 100-bin chord and chord-angle histograms plus moment summaries of
    them ((52)-(87)); this arm uses fewer bins, no moment summaries, and all vertex
    pairs rather than a sampled subset.
    """
    ring = prep.ring
    count = prep.n_samples
    diff = ring[:, None, :] - ring[None, :, :]
    distances = np.sqrt((diff**2).sum(axis=2))
    pairs = distances[np.triu_indices(count, k=1)]
    chord = np.histogram(
        np.clip(pairs, 0.0, CHORD_AXIS_MAX), bins=CHORD_BINS, range=(0.0, CHORD_AXIS_MAX)
    )[0] / pairs.shape[0]
    radius = np.linalg.norm(ring - ring.mean(axis=0), axis=1)
    radius_share = np.histogram(
        np.clip(radius, 0.0, RADIUS_AXIS_MAX), bins=RADIUS_BINS, range=(0.0, RADIUS_AXIS_MAX)
    )[0] / count
    window = max(1, int(round(TANGENT_WINDOW_FRACTION * count / 2.0)))
    tangent = np.roll(frame.ring, -window, axis=0) - np.roll(frame.ring, window, axis=0)
    if not np.all(np.linalg.norm(tangent, axis=1) > 0.0):
        raise PerceptualFeatureError("tangent chord collapses: no direction to histogram")
    angles = np.mod(np.arctan2(tangent[:, 1], tangent[:, 0]), 2.0 * math.pi)
    tangent_share = (
        np.histogram(angles, bins=TANGENT_BINS, range=(0.0, 2.0 * math.pi))[0] / count
    )
    out: dict[str, float] = {}
    for index, value in enumerate(chord):
        out[f"hist_chord_{index:02d}"] = float(value)
    out["hist_chord_overflow"] = float(np.mean(pairs > CHORD_AXIS_MAX))
    for index, value in enumerate(radius_share):
        out[f"hist_radius_{index:02d}"] = float(value)
    out["hist_radius_overflow"] = float(np.mean(radius > RADIUS_AXIS_MAX))
    for index, value in enumerate(tangent_share):
        out[f"hist_tangent_{index:02d}"] = float(value)
    return out


_BUILDERS: dict[str, Callable[[PreparedContour, OrientationFrame], dict[str, float]]] = {
    "global": _global_block,
    "moments": _moments_block,
    "curvature": _curvature_block,
    "fourier": _fourier_block,
    "histogram": _histogram_block,
}


def check_groups(groups: Sequence[str]) -> tuple[str, ...]:
    """Validate a group selection: known names only, no repeats, order preserved."""
    selected = tuple(groups)
    if not selected:
        raise PerceptualFeatureError("groups must not be empty")
    for name in selected:
        if name not in _BUILDERS:
            raise PerceptualFeatureError(f"unknown group {name!r}; available: {list(_BUILDERS)}")
    if len(set(selected)) != len(selected):
        raise PerceptualFeatureError(f"groups repeat: {selected}")
    return selected


def check_orientation(mode: str) -> str:
    if mode not in ORIENTATION_MODES:
        raise PerceptualFeatureError(
            f"orientation must be one of {ORIENTATION_MODES}, got {mode!r}"
        )
    return mode


def check_distance_kind(kind: str) -> str:
    if kind not in DISTANCE_KINDS:
        raise PerceptualFeatureError(
            f"distance_kind must be one of {DISTANCE_KINDS}, got {kind!r}"
        )
    return kind


def descriptor_values(
    points: Any,
    *,
    groups: Sequence[str] = DEFAULT_GROUPS,
    orientation: str = "canonical",
    prep: PreparedContour | None = None,
    name: str = "contour",
) -> dict[str, float]:
    """Ordered ``{descriptor: value}`` for one contour (stage 1, not yet normalised).

    Names and values come out of the same block calls, so a column and its label
    cannot drift apart.
    """
    selected = check_groups(groups)
    mode = check_orientation(orientation)
    prepared = prep if prep is not None else prepare(points, name=name)
    frame = principal_frame(prepared, mode=mode)
    out: dict[str, float] = {}
    for group in selected:
        out.update(_BUILDERS[group](prepared, frame))
    bad = [key for key, value in out.items() if not math.isfinite(value)]
    if bad:
        raise PerceptualFeatureError(
            f"{name} produced non-finite descriptors {bad}; a NaN must never reach a distance"
        )
    return out


def features_for_contour(
    points: Any,
    *,
    groups: Sequence[str] = DEFAULT_GROUPS,
    orientation: str = "canonical",
) -> np.ndarray:
    """Descriptor vector of one contour, in :func:`feature_names` order."""
    values = descriptor_values(points, groups=groups, orientation=orientation)
    return np.array(list(values.values()), dtype=float)


# --- names, read off the same code that computes the values ---------------------

_REFERENCE_CACHE: dict[tuple[str, ...], list[str]] = {}


def _reference_contour() -> np.ndarray:
    """A fixed asymmetric outline used only to read descriptor *names* off the blocks.

    Deterministic by construction (no RNG): a radial curve with well-separated
    principal axes, so every group — including the frame-dependent tangent block — is
    exercised when the names are collected.
    """
    t = np.linspace(0.0, 2.0 * math.pi, 128, endpoint=False)
    radius = 1.0 + 0.42 * np.cos(t) + 0.18 * np.cos(2.0 * t + 0.7) + 0.11 * np.sin(3.0 * t)
    radius = np.maximum(radius, 1e-3)
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


def feature_names(
    groups: Sequence[str] = DEFAULT_GROUPS, *, orientation: str = "canonical"
) -> list[str]:
    """Descriptor names for a group selection, in vector order."""
    selected = check_groups(groups)
    check_orientation(orientation)
    cached = _REFERENCE_CACHE.get(selected)
    if cached is not None:
        return list(cached)
    names = list(
        descriptor_values(_reference_contour(), groups=selected, orientation=orientation)
    )
    _REFERENCE_CACHE[selected] = names
    return list(names)


def descriptor_counts(
    groups: Sequence[str] = DEFAULT_GROUPS, *, orientation: str = "canonical"
) -> dict[str, int]:
    """Per-group descriptor counts, measured from the code rather than hand-written."""
    counts: dict[str, int] = {}
    for group in check_groups(groups):
        counts[group] = len(feature_names((group,), orientation=orientation))
    counts["total"] = len(feature_names(groups, orientation=orientation))
    return counts


# --- stage 2: corpus-fitted scale normalization ---------------------------------


@dataclass(frozen=True)
class FeatureNormalizer:
    """Fitted per-descriptor scale: ``transform(x) = (x - center) / scale``.

    ``ranges()`` records the interval each descriptor was fitted over.  ShapeComp's
    point is that these are *corpus* properties, so a results table can show them
    beside the scores instead of trusting a comment.
    """

    method: str
    center: np.ndarray = field(repr=False)
    scale: np.ndarray = field(repr=False)
    lo: np.ndarray = field(repr=False)
    hi: np.ndarray = field(repr=False)
    names: tuple[str, ...]
    n_samples: int
    degenerate: tuple[str, ...]
    fitted_on: str = "explicit corpus"

    @property
    def n_descriptors(self) -> int:
        return int(self.center.shape[0])

    def transform(self, matrix: Any) -> np.ndarray:
        """Apply the fitted shift and divisor; the width must match the fit.

        Dimension-preserving on purpose: a ``(d,)`` descriptor vector in, a ``(d,)``
        vector out.  Promoting to ``(1, d)`` and leaving it there made every
        ``distance()`` call that supplied a corpus normalizer die inside
        ``np.dot(delta, delta)`` with ``shapes (1, 91) and (1, 91) not aligned`` — a
        corpus-fitted scale is the *required* configuration for the experiment, so the
        crash sat exactly on the path the comparison runs on.
        """
        array = np.asarray(matrix, dtype=float)
        vector = array.ndim == 1
        if vector:
            array = array[None, :]
        if array.ndim != 2:
            raise PerceptualFeatureError(
                f"expected an (m, d) feature matrix or a (d,) vector, got {array.shape}"
            )
        if array.shape[1] != self.n_descriptors:
            raise PerceptualFeatureError(
                f"matrix has {array.shape[1]} descriptors, this normalizer was fitted "
                f"on {self.n_descriptors}"
            )
        if not np.all(np.isfinite(array)):
            raise PerceptualFeatureError("cannot normalize a matrix with non-finite entries")
        scaled = (array - self.center) / self.scale
        return np.asarray(scaled[0] if vector else scaled, dtype=float)

    def ranges(self) -> dict[str, tuple[float, float]]:
        """Per-descriptor fitted interval: (min, max) for minmax, (q25, q75) for robust."""
        return {name: (float(self.lo[i]), float(self.hi[i])) for i, name in enumerate(self.names)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "fitted_on": self.fitted_on,
            "n_samples": self.n_samples,
            "n_descriptors": self.n_descriptors,
            "degenerate_descriptors": list(self.degenerate),
            "ranges": self.ranges(),
        }


def fit_normalizer(
    matrix: Any,
    *,
    method: str = "minmax",
    names: Sequence[str] | None = None,
    fitted_on: str = "explicit corpus",
) -> FeatureNormalizer:
    """Fit the stage-2 scale on a corpus of descriptor rows.

    ``minmax`` follows ShapeComp's intent: every descriptor spans a common 0-1 range,
    ``center`` being the corpus minimum and ``scale`` the corpus range, so the fitted
    corpus maps exactly onto [0, 1] per column.  ``robust`` uses median and IQR
    instead, which keeps the bulk of a column spread out when a single outlier owns
    the range — at the cost of values outside [0, 1].  A constant column is recorded
    in ``degenerate`` and divided by 1.0 rather than by 0.

    ShapeComp divides each *pairwise descriptor distance* by the corpus maximum
    pairwise distance; dividing each *descriptor value* by the corpus range is the same
    intent but not the same constant (it differs by a per-column factor of at most 2),
    and that difference is recorded here instead of being papered over.
    """
    if method not in NORMALIZATION_METHODS:
        raise PerceptualFeatureError(
            f"method must be one of {NORMALIZATION_METHODS}, got {method!r}"
        )
    array = np.asarray(matrix, dtype=float)
    if array.ndim != 2:
        raise PerceptualFeatureError(f"matrix must be (m, d), got shape {array.shape}")
    if array.shape[0] < 2:
        raise PerceptualFeatureError(
            f"a corpus of {array.shape[0]} rows cannot define a spread; need at least 2"
        )
    if not np.all(np.isfinite(array)):
        raise PerceptualFeatureError("cannot fit a normalizer on non-finite descriptors")
    if names is not None and len(names) != array.shape[1]:
        raise PerceptualFeatureError(
            f"{len(names)} names for {array.shape[1]} descriptor columns"
        )
    if method == "minmax":
        lo, hi = array.min(axis=0), array.max(axis=0)
        center, scale = lo, hi - lo
    else:
        lo, hi = np.percentile(array, 25.0, axis=0), np.percentile(array, 75.0, axis=0)
        center, scale = np.median(array, axis=0), hi - lo
    labels = (
        tuple(names)
        if names is not None
        else tuple(f"descriptor_{index:03d}" for index in range(array.shape[1]))
    )
    flat = scale <= 0.0
    degenerate = tuple(label for label, flag in zip(labels, flat, strict=True) if bool(flag))
    safe = np.where(flat, 1.0, scale)
    return FeatureNormalizer(
        method=method,
        center=np.asarray(center, dtype=float),
        scale=np.asarray(safe, dtype=float),
        lo=np.asarray(lo, dtype=float),
        hi=np.asarray(hi, dtype=float),
        names=labels,
        n_samples=int(array.shape[0]),
        degenerate=degenerate,
        fitted_on=fitted_on,
    )


# --- library features, content-addressed cache ----------------------------------

_FEATURE_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_CACHE_HITS = 0
_CACHE_MISSES = 0


def _mirror_rows(rows: np.ndarray) -> np.ndarray:
    """Reflect every contour (``y -> -y``, the project's D9 mirror convention)."""
    return np.asarray(rows * _MIRROR_SIGN, dtype=float)


def features_for_library(
    library: Any,
    *,
    groups: Sequence[str] = DEFAULT_GROUPS,
    orientation: str = "canonical",
    use_cache: bool = True,
) -> np.ndarray:
    """``(m, d)`` descriptor matrix for a whole library, in :func:`feature_names` order.

    Work inside one contour is NumPy-vectorised over its samples; the loop over
    library rows is Python, dominated by the per-row convex hull and rotating-calipers
    pass (``O(m * n log n)``).  Results are cached by a content hash of the stacked
    array plus the configuration, because the runner wants the same library once per
    query (18 queries over one 179-row library) and a candidate's descriptors do not
    depend on the query at all.  A cache hit returns a copy, so the stored matrix
    cannot be mutated by a caller.
    """
    global _CACHE_HITS, _CACHE_MISSES
    rows = contour_stack(library, "library")
    selected = check_groups(groups)
    check_orientation(orientation)
    key = (
        hashlib.sha256(rows.tobytes()).hexdigest()
        + "|"
        + ",".join(selected)
        + "|"
        + orientation
    )
    if use_cache:
        hit = _FEATURE_CACHE.get(key)
        if hit is not None:
            _CACHE_HITS += 1
            return hit.copy()
    names = feature_names(selected, orientation=orientation)
    matrix = np.empty((rows.shape[0], len(names)), dtype=float)
    for index, row in enumerate(rows):
        prepared = prepare(row, name=f"library[{index}]")
        values = descriptor_values(
            row, groups=selected, orientation=orientation, prep=prepared,
            name=f"library[{index}]",
        )
        matrix[index] = list(values.values())
    if not np.all(np.isfinite(matrix)):
        bad = np.flatnonzero(~np.isfinite(matrix).all(axis=1))
        raise PerceptualFeatureError(
            f"library rows {list(int(i) for i in bad[:5])} produced non-finite descriptors"
        )
    if use_cache:
        _CACHE_MISSES += 1
        _FEATURE_CACHE[key] = matrix.copy()
        while len(_FEATURE_CACHE) > _CACHE_ENTRIES:
            _FEATURE_CACHE.popitem(last=False)
    return matrix


def orientation_statuses(
    library: Any, *, orientation: str = "canonical"
) -> list[dict[str, Any]]:
    """Per-candidate report of the frame the tangent-angle histogram was measured in.

    :func:`features_for_library` caches only the descriptor matrix, so the frame status
    is not smuggled through that cache — it costs one eigenpair per row on numbers
    :func:`prepare` has already produced, which is what :func:`score_library`'s records
    cannot say on their own.  A results table for this arm should print
    ``orientation_status`` next to the distance, because ``"isotropic-fallback"`` means
    the 42-column histogram block of that row was measured against static bin edges and
    is therefore *not* rotation invariant (see ``POLICY["known_limitations"]``).
    """
    rows = contour_stack(library, "library")
    mode = check_orientation(orientation)
    reports: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        frame = principal_frame(prepare(row, name=f"library[{index}]"), mode=mode)
        reports.append({"row": index, **frame.as_dict()})
    return reports


def cache_stats() -> dict[str, Any]:
    """Cache counters; hits are content-addressed, so they cannot change a number."""
    return {
        "hits": _CACHE_HITS,
        "misses": _CACHE_MISSES,
        "entries": len(_FEATURE_CACHE),
        "max_entries": _CACHE_ENTRIES,
    }


# --- the distances ---------------------------------------------------------------


def _pairwise_distance(left: np.ndarray, right: np.ndarray, kind: str) -> float:
    delta = left - right
    if check_distance_kind(kind) == "euclidean":
        return float(np.sqrt(np.dot(delta, delta)))
    return float(np.abs(delta).sum())


def _check_normalizer(normalizer: FeatureNormalizer | None, width: int) -> str:
    if normalizer is None:
        return "none (raw descriptor units, no corpus scale applied)"
    if normalizer.n_descriptors != width:
        raise PerceptualFeatureError(
            f"normalizer was fitted on {normalizer.n_descriptors} descriptors, this "
            f"group selection has {width}"
        )
    return (
        f"supplied by caller (method={normalizer.method}, fitted on "
        f"{normalizer.n_samples} rows: {normalizer.fitted_on})"
    )


def distance(
    query: Any,
    candidate: Any,
    *,
    normalizer: FeatureNormalizer | None = None,
    groups: Sequence[str] = DEFAULT_GROUPS,
    distance_kind: str = "euclidean",
    orientation: str = "canonical",
    allow_mirror: bool = False,
) -> tuple[float, dict[str, Any]]:
    """Score one pair in normalized descriptor space, plus what was actually applied.

    Without a normalizer the number is a raw Euclidean distance over mixed units
    (ratios next to angles next to log-moments), which is *not* a comparable scale;
    the metadata says so instead of pretending otherwise.
    """
    selected = check_groups(groups)
    mode = check_orientation(orientation)
    left = descriptor_values(query, groups=selected, orientation=mode, name="query")
    right = descriptor_values(candidate, groups=selected, orientation=mode, name="candidate")
    left_vector = np.array(list(left.values()), dtype=float)
    right_vector = np.array(list(right.values()), dtype=float)
    note = _check_normalizer(normalizer, left_vector.shape[0])
    if normalizer is not None:
        left_vector = normalizer.transform(left_vector)
        right_vector = normalizer.transform(right_vector)
    score = _pairwise_distance(left_vector, right_vector, distance_kind)
    info: dict[str, Any] = {
        "groups": list(selected),
        "n_descriptors": int(left_vector.shape[0]),
        "distance_kind": check_distance_kind(distance_kind),
        "normalizer": note,
        "mirrored": False,
    }
    if allow_mirror:
        array = np.asarray(check_contour(candidate, "candidate"), dtype=float) * _MIRROR_SIGN
        mirrored = descriptor_values(array, groups=selected, orientation=mode)
        vector = np.array(list(mirrored.values()), dtype=float)
        if normalizer is not None:
            vector = normalizer.transform(vector)
        flipped = _pairwise_distance(left_vector, vector, distance_kind)
        if flipped < score:
            score = float(flipped)
            info["mirrored"] = True
        info["mirror_distance"] = float(flipped)
    return float(score), info


def distance_library(
    query: Any,
    library: Any,
    *,
    normalizer: FeatureNormalizer | None = None,
    groups: Sequence[str] = DEFAULT_GROUPS,
    distance_kind: str = "euclidean",
    orientation: str = "canonical",
    allow_mirror: bool = False,
) -> np.ndarray:
    """Scores for every library row; prefer :func:`score_library`, which caches."""
    rows = contour_stack(library, "library")
    return np.array(
        [
            distance(
                query,
                row,
                normalizer=normalizer,
                groups=groups,
                distance_kind=distance_kind,
                orientation=orientation,
                allow_mirror=allow_mirror,
            )[0]
            for row in rows
        ],
        dtype=float,
    )


def score_library(
    query: Any,
    library: Any,
    *,
    normalizer: FeatureNormalizer | None = None,
    groups: Sequence[str] = DEFAULT_GROUPS,
    distance_kind: str = "euclidean",
    orientation: str = "canonical",
    allow_mirror: bool = False,
    **_: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    """``(distances, records)`` for every row of ``library`` under the E1 arm.

    Library descriptors are extracted once and reused for all rows; when no
    ``normalizer`` is supplied the corpus scale is self-fitted on **this** library and
    the records state that (``normalizer["self_fit"]`` and ``fitted_on``), because a
    silent self-fit would let a weak claim read as a strong one.
    """
    rows = contour_stack(library, "library")
    selected = check_groups(groups)
    mode = check_orientation(orientation)
    matrix = features_for_library(rows, groups=selected, orientation=mode)
    query_values = descriptor_values(query, groups=selected, orientation=mode, name="query")
    left = np.array(list(query_values.values()), dtype=float)
    applied = normalizer
    if applied is None:
        applied = fit_normalizer(
            matrix,
            method="minmax",
            names=list(query_values),
            fitted_on="self-fitted on the library it scored in this call",
        )
    _check_normalizer(applied, left.shape[0])
    scaled = applied.transform(matrix)
    left_scaled = applied.transform(left)
    scores = np.array(
        [_pairwise_distance(left_scaled, scaled[index], distance_kind) for index in
         range(scaled.shape[0])],
        dtype=float,
    )
    transforms: list[dict[str, Any]] = []
    if allow_mirror:
        mirrored = applied.transform(
            features_for_library(_mirror_rows(rows), groups=selected, orientation=mode)
        )
        for index in range(scaled.shape[0]):
            flipped = _pairwise_distance(left_scaled, mirrored[index], distance_kind)
            wins = bool(flipped < scores[index])
            if wins:
                scores[index] = float(flipped)
            transforms.append({"mirrored": wins, "mirror_distance": float(flipped)})
    else:
        transforms = [{"mirrored": False} for _ in range(scaled.shape[0])]
    for index, record in enumerate(transforms):
        record["row"] = index
        record["distance"] = float(scores[index])
    return scores, {
        "policy": describe_policy(),
        "applied_groups": list(selected),
        "descriptors": {
            "count": int(left.shape[0]),
            "per_group": descriptor_counts(selected, orientation=mode),
            "names": list(query_values),
        },
        "distance_kind": check_distance_kind(distance_kind),
        "orientation_mode": mode,
        "normalizer": {
            **applied.as_dict(),
            "supplied_by_caller": normalizer is not None,
            "self_fit": normalizer is None,
            "claim": (
                "corpus-fitted scale supplied by the caller: the fitted ranges below are "
                "properties of the corpus it was fitted on, not of this query"
                if normalizer is not None
                else "self-fit on the scored library: a weaker claim than a corpus fit, "
                     "reported here rather than applied silently"
            ),
        },
        "allow_mirror": bool(allow_mirror),
        "transforms": transforms,
    }


POLICY = {
    "id": "E1",
    "name": "ShapeComp-inspired classical descriptor battery (not the ShapeComp model)",
    "score": "Euclidean (or cityblock) distance in corpus-normalized descriptor space; "
    "smaller is closer; ties broken by library row order",
    "provenance": (
        "inspired by Morgenstern et al. 2021, PLOS Comput Biol 17(6):e1008981. Not a "
        "port and not a reproduction: different descriptor set, no 25,712-shape corpus, "
        "no classical MDS, no 22-D space, no MatNet/KerNet weights, no purchased "
        "silhouettes, no MATLAB. Do not cite as 'the ShapeComp model'. Evidence log: "
        "experiments/p0_perceptual_probe/shapecomp_research.md"
    ),
    "descriptor_counts": descriptor_counts(),
    "groups": {
        "global": "area, perimeter, compactness, solidity, convexity, extent, "
        "eccentricity, moment aspect, circularity, diameter fill",
        "moments": "Hu's first six invariants of the exactly integrated polygon "
        "region (the seventh is omitted: see known_limitations)",
        "curvature": "per window: mean |psi|, std, max, min, convex-event share, "
        "concave-event share, where psi is the signed turning angle divided by the "
        "turning a circle shows at the same arc-length window",
        "fourier": "elliptic Fourier semi-axis magnitudes divided by the first "
        "harmonic's major axis (magnitudes only; the per-harmonic angles are not used)",
        "histogram": "chord-length, centroid-radius and tangent-angle shares on fixed "
        "axes plus the explicit overflow shares",
    },
    "scale_policy": (
        "stage 1: every descriptor is computed on the shared standardized contour "
        "(unit RMS radius, centroid centred), so lengths are in units of the RMS radius "
        "and ratios/angles/event shares are dimensionless; stage 2: each descriptor is "
        "divided by a corpus-fitted spread before any distance is taken"
    ),
    "normalization": {
        "default_method": "minmax",
        "alternatives": list(NORMALIZATION_METHODS),
        "fitted_on": (
            "a corpus, never the query. score_library(normalizer=None) self-fits on the "
            "library it scores and reports it in normalizer.fitted_on; distance() with "
            "no normalizer reports 'none (raw descriptor units)'. A query-time fit is a "
            "much weaker claim than a corpus fit and is never made silently here"
        ),
        "deviation_from_shapecomp": (
            "ShapeComp divides each pairwise descriptor distance by the corpus maximum "
            "pairwise distance over 25,712 animal silhouettes; this divides each "
            "descriptor value by the corpus range (or IQR) of the library in hand. Same "
            "intent, different constant, and the constants are not comparable across runs"
        ),
    },
    "invariance": {
        "translation": "exact (re-centroided in prepare; every feature is a difference "
        "or a central integral)",
        "uniform_scale": "exact (re-scaled to unit RMS radius in prepare; every angle, "
        "ratio and event share is scale-free by definition)",
        "rotation": "exact for global, moments, curvature, fourier and the chord/radius "
        "histograms. The tangent-angle histogram is rotation COVARIANT and is measured "
        "in a canonical frame (region principal axis onto +x, sign from the normalised "
        f"third moment). The frame is undefined for near-isotropic contours "
        f"(lambda2/lambda1 >= {ORIENTATION_ISOTROPY_MAX}), where it falls back to the "
        "input frame and that block is NOT rotation invariant; "
        "orientation_statuses(library) reports the flag per candidate. Measured, the "
        "fallback is not an edge case here: every 3-fold-or-higher symmetric member of "
        "the gallery takes it (circle and square 1.00000, pentagon 0.99939, hexagon "
        "0.99922, star(5) 0.99869, star(6) 0.99582, triangle 0.99904), while crescent "
        "0.31132, heart 0.59892 and the chiral blob 0.76984 get a real frame",
        "start_point": "exact: every descriptor is a statistic over the closed loop, "
        "never an indexed sequence",
        "traversal_direction": "exact, by re-winding to CCW with ensure_ccw; a reversed "
        "ring is the same contour and scores 0.0 against itself",
        "mirror": "not claimed. Length/magnitude/ratio descriptors and the "
        "chord/radius histograms are mirror-blind and cannot separate a chiral pair. "
        "Among Hu's invariants only the seventh is reflection-odd, and it is omitted, "
        "so the only block that moves under a mirror is the tangent histogram. "
        "allow_mirror=True scores min(d(query, candidate), d(query, mirror(candidate))) "
        "at twice the library feature cost",
        "sampling_count": "not invariant: fixed bin counts and window fractions are "
        "defined on the shared 128-point resampling",
    },
    "orientation_handling": {
        "method": "principal-axis canonical frame: one 2x2 eigenpair on the region's "
        "second moments plus one third-moment pass for the sign rule",
        "search_not_used": "a cyclic search over K sampled angles inside the distance "
        "was rejected: it multiplies the library feature cost by K (about K x 179 "
        "descriptor extractions per query) to fix one block of the vector",
        "cost": "O(n_samples) per contour for the moments (exact polygon integration, "
        "no rasterisation), O(n_samples) for the rotation",
    },
    "constants": {
        "curvature_window_fractions": [
            round(value, 6) for value in CURVATURE_WINDOW_FRACTIONS
        ],
        "curvature_convex_min": CURVATURE_CONVEX_MIN,
        "curvature_concave_max": CURVATURE_CONCAVE_MAX,
        "efd_harmonics": EFD_HARMONICS,
        "chord_bins": CHORD_BINS,
        "radius_bins": RADIUS_BINS,
        "tangent_bins": TANGENT_BINS,
        "chord_axis_max": CHORD_AXIS_MAX,
        "radius_axis_max": RADIUS_AXIS_MAX,
        "tangent_window_fraction": round(TANGENT_WINDOW_FRACTION, 6),
        "orientation_isotropy_max": ORIENTATION_ISOTROPY_MAX,
        "moment_max_order": MOMENT_MAX_ORDER,
    },
    "known_limitations": {
        "hu_seventh_omitted": (
            "Hu I7 is the reflection-odd invariant and this arm has no honest use for "
            "it. Measured at the contract's 128-point sampling: the mirror-symmetric "
            "crescent curve reports I7 = -2.5e-8 from its (asymmetric) samples alone - "
            "1.5e-7 at 64 points, 5.3e-10 at 1024, so a sampling artefact eleven "
            "decades above the under-epsilon floor and emitted as +7.6; where the "
            "samples are symmetric the value is round-off (1e-30 for a 5-point star, "
            "1e-64 for a 6-point one) whose sign flips with the rotation angle. I1-I6 "
            "move by less than 3.0e-7 under the same rotations. Emitting I7 would put "
            "a sampler- and frame-dependent term in every distance, so this arm emits "
            "six invariants; tests/test_perceptual_features.py reproduces these numbers"
        ),
        "area_and_perimeter_cannot_both_be_matched_to_a_disc": (
            "equal area and equal perimeter force compactness 4*pi*A/P^2 to 1, and the "
            "isoperimetric equality holds only for the disc. The star-vs-disc contrast "
            "test therefore matches the two sizes to a stated tolerance (area to 1%, "
            "perimeter to 2%) instead of claiming exactness"
        ),
        "group_sizes_unequal": (
            "the histogram block has 42 of the 91 descriptors against the global "
            "block's 10, so the plain Euclidean sum weights it 4.2x by dimension "
            "count alone; the grouped ablation measures exactly that, and no fusion "
            "weight is invented"
        ),
        "isotropic_shapes_lose_rotation_invariance": (
            "the priced consequence of the frame rule quoted under invariance. "
            "score_library() without a normalizer puts a 37-degree rotation of star(5) "
            "at 4.8047 in the five-row library [rotated copy, circle, crescent, "
            "pentagon, chiral blob] - the unrelated rows there run 6.0360 to 7.3931, so "
            "the rotated copy still ranks first, just not by much, while E0 and E2 "
            "measure exactly 0.0000 for the same pair and E3 0.1405. Cause: with no "
            "principal axis the 42-column histogram block is measured against static "
            "bin edges. The frame is then doing nothing at all, and the same library in "
            "orientation='raw' gives 4.6945 for that pair; the contrast is the control "
            "that shows the mechanism works where it is defined - the chiral blob "
            "(lambda2/lambda1 = 0.76984) measures 1.3434 raw and exactly 0.0000 "
            "canonical. This arm's rotation invariance is therefore a property of the "
            "shape's second moments, not of the arm; tests/test_perceptual_features.py "
            "reproduces the flag, both residuals and the control"
        ),
        "self_fitted_scale_moves_with_the_library": (
            "score_library(normalizer=None) fits its minmax ranges on the rows it "
            "scores, so the constants - and occasionally the order - depend on the "
            "library: in the three-row library [circle, crescent, rotated star(5)] the "
            "rotated copy of the query ranks second (7.5459 against the crescent's "
            "7.5072), where the five-row library above ranks it first (4.8047). A "
            "comparison grid must therefore pass one fixed corpus-fitted normalizer "
            "through every query; the self-fit path exists so a single call cannot be "
            "quoted as a corpus claim"
        ),
    },
    "ablation": "groups=... is a parameter of every entry point, so 'which group earns "
    "the accuracy' needs no code edit; group sizes are unequal, so the plain Euclidean "
    "sum weights them unequally by dimension count and the ablation measures that",
    "cost": "library features O(m * n log n) (convex hull per row), cached by content "
    "hash so the queries of one grid share a single extraction per candidate; the "
    "distance itself is O(d) per pair",
    "measurements": "the orientation census is a call, not a sentence: `orientation_statuses()` "
    "returns lambda2/lambda1 and the status for every row. The rotation residuals and the "
    "self-fitted-scale reorder quoted under invariance/known_limitations are re-derived "
    "against a tolerance by `python experiments/p0_perceptual_probe/verify_policy_claims.py`",
    "new_code_in_this_checkpoint": True,
}


def describe_policy() -> dict[str, Any]:
    return dict(POLICY)
