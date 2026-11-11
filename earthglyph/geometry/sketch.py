"""Hand-drawn query abstraction (P0-4, Task A).

The job of this module is to turn an imprecise input — a vector stroke trace or a
simple black-on-white PNG — into **the same kind of object the library stores**:
a closed contour of ``N`` points, walked counter-clockwise, sampled uniformly by
arc length, centroid-centered and RMS-scaled
(:func:`earthglyph.geometry.resampling.build_contour` defines that contract).

What "abstraction" means here, and what it does *not* mean:

* it removes the canvas: position, size, pixel quantisation and the number and
  spacing of the samples the input device happened to produce;
* it keeps everything that *is* the shape: notches, corners, forks, lobes,
  asymmetry, elongation;
* it never snaps a drawing to a template. There is no shape vocabulary in this
  module — no "heart", no "crescent" — because the retrieval stage must compare
  the outline the user actually drew (docs/RETRIEVAL_DESIGN.md §5, task brief
  §12.4).

Smoothing is the only step that can destroy real structure, so it is a named,
switchable, reproducible parameter (:data:`SMOOTHING_SIGMA`) applied as a cyclic
Gaussian on the *arc-length-uniform* sequence, i.e. as a low-pass filter of the
boundary in metres-of-perimeter, not in drawing time.

Invalid input fails loudly with a reason (open stroke, degenerate area,
self-intersection, ambiguous image) instead of being quietly repaired into a
plausible-looking lake.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from earthglyph.geometry.normalization import normalize_contour
from earthglyph.geometry.resampling import (
    DEFAULT_N_POINTS,
    arc_length_resample,
    ensure_ccw,
    open_ring,
    ring_perimeter,
    signed_area,
)

DEFAULT_SMOOTHING = "light"
DEFAULT_ALLOW_SELF_INTERSECTION = False

# Cyclic-Gaussian sigma expressed in sample units of the working N-point
# contour. sigma=1 attenuates harmonic k to exp(-2*pi^2*k^2/N^2), so "light"
# (0.8) barely touches the descriptor band while "medium" (2.0) removes
# boundary detail below ~1.6% of the perimeter.
SMOOTHING_SIGMA: dict[str, float] = {"none": 0.0, "light": 0.8, "medium": 2.0}

# Consecutive-input-point tolerance, relative to the trace's own RMS radius.
DUP_TOL_REL = 1e-3
# A start/end gap up to this fraction of the trace scale is closed silently.
AUTO_CLOSE_REL = 0.03
# Beyond this fraction the stroke is *open* and the user is asked to redraw.
OPEN_REJECT_REL = 0.15
# ... unless the gap is explained by the trace's own sample spacing: a densely
# sampled closed ring has an unavoidable wrap chord as long as one step, so the
# refusal must also clear these two seam limits (see close_trace).
SEAM_STEP_TOLERANCE = 2.0
SEAM_PERIMETER_SHARE = 0.05
# Below this isoperimetric ratio the "shape" is a line, not an area.
MIN_COMPACTNESS = 1e-3
# A crossing that changes the enclosed region by more than this fraction of the
# convex hull is structural (a figure-eight), not a pen wobble.
MATERIAL_CROSS_AREA_REL = 0.005
# An image component this small relative to the largest one is noise.
IMAGE_NOISE_SHARE = 0.01
# Two components above this share are two shapes (or text), not one shape.
IMAGE_AMBIGUOUS_SHARE = 0.05
# A hole this large relative to its outer ring means "the stroke is an outline".
IMAGE_HOLE_SHARE = 0.30
# Stroke thinner than this fraction of the shape radius means an outline drawing.
IMAGE_STROKE_REL = 0.15
# A hole-free ink region is either a filled shape or a *stroked open curve*, and an
# obviously open curve must be sent back for a redraw rather than quietly closed.
# Two scale-free statistics tell them apart: the band's mean width 2A/P relative to
# the shape scale, and the solidity A/A(hull). Measured on the real 839-candidate
# library, the two most band-like landforms are row 419 (0.065 width-rel, solidity
# 0.160) and row 536 (0.092, 0.238); a 3-pixel 270° arc on a 512-pixel canvas is
# (0.026, 0.057). Both limits therefore sit below every real shape and above the
# stroke, so a dendritic reservoir is never read as an unclosed pen line.
OPEN_STROKE_WIDTH_REL = 0.05
OPEN_STROKE_SOLIDITY = 0.10


class SketchError(ValueError):
    """Raised when an input cannot be turned into a trustworthy query contour."""


@dataclass(frozen=True)
class QueryContour:
    """One standardized query contour plus everything needed to audit it.

    Fields are in two families. ``points`` is the deliverable, identical in
    definition to a row of ``data/index/shapes.npy``. The rest is the trail that
    shows how it was obtained, which is what makes the abstraction step testable
    (task brief §A3 acceptance: raw trace / extracted outline / 128-point contour
    must be comparable).
    """

    points: np.ndarray  # (N, 2) centered, unit RMS radius, CCW — the query contour
    source: str  # "trace" | "image"
    raw_points: np.ndarray  # input, as supplied (canvas or pixel units)
    outline_points: np.ndarray  # deduped and closed trace (canvas/pixel units)
    working_points: np.ndarray  # N points, arc-length uniform, NOT smoothed
    smoothed_points: np.ndarray  # N points after smoothing (== working if none)
    n_points: int
    smoothing: str
    smoothing_sigma: float
    unit: str  # "canvas" | "pixel" — what the non-normalized fields are measured in
    closure: dict[str, float]
    winding: str
    self_intersection: dict[str, Any]
    image_info: dict[str, Any] = field(default_factory=dict)

    @property
    def warnings(self) -> list[str]:
        """Human-readable notes; the retrieval result must never hide these."""
        notes: list[str] = []
        if self.smoothing != "none":
            notes.append(
                f"{self.smoothing} smoothing applied (cyclic Gaussian sigma="
                f"{self.smoothing_sigma:g} samples)"
            )
        for key in ("notes", "warnings"):
            for note in self.image_info.get(key, []):
                notes.append(str(note))
        return notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "unit": self.unit,
            "points": [[float(x), float(y)] for x, y in self.points],
            "n_raw_points": int(self.raw_points.shape[0]),
            "n_outline_points": int(self.outline_points.shape[0]),
            "n_points": self.n_points,
            "winding": self.winding,
            "smoothing": self.smoothing,
            "smoothing_sigma": self.smoothing_sigma,
            "closure": dict(self.closure),
            "self_intersection": dict(self.self_intersection),
            "outline_perimeter": float(ring_perimeter(self.outline_points)),
            "outline_area": float(abs(signed_area(self.outline_points))),
            "image": {
                key: value
                for key, value in self.image_info.items()
                if key not in ("notes", "warnings")
            },
            "warnings": self.warnings,
        }


def _as_point_array(points: Any, name: str) -> np.ndarray:
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise SketchError(f"{name} must have shape (M, 2), got {arr.shape}")
    if arr.shape[0] < 3:
        raise SketchError(f"a closed shape needs at least 3 points, got {arr.shape[0]}")
    if not np.all(np.isfinite(arr)):
        raise SketchError(f"{name} contains non-finite coordinates")
    return arr


def trace_scale(points: np.ndarray) -> float:
    """RMS radius of a trace about its centroid — the yardstick for tolerances.

    Every threshold in this module is relative to this number, so a 40-pixel
    doodle and a 4000-pixel doodle are judged by the same geometry, and the
    minimum distance is never a fixed pixel count (task brief §A2).
    """
    centered = points - points.mean(axis=0)
    return float(np.sqrt(np.mean(np.sum(centered**2, axis=1))))


def dedupe_points(points: np.ndarray, *, scale: float | None = None) -> np.ndarray:
    """Drop consecutive points closer than ``DUP_TOL_REL`` times the trace scale."""
    tol = DUP_TOL_REL * (trace_scale(points) if scale is None else scale)
    keep = np.ones(points.shape[0], dtype=bool)
    if points.shape[0] > 1:
        step = np.linalg.norm(np.diff(points, axis=0), axis=1)
        keep[1:] = step > tol
    return points[keep]


def _self_intersection_info(closed: np.ndarray) -> dict[str, Any]:
    """Describe self-crossings of the closed trace and how material they are.

    A pen wobble makes a tiny loop; a figure-eight or a single-stroke star makes a
    real crossing. The two are separated by asking what the crossing costs the
    enclosed region — ``|area(make_valid) - |shoelace|| / hull_area`` — not by
    guessing intent. A cosmetic loop is reported and kept; a material crossing
    changes what "inside" means, so it is refused unless the caller opts in, and
    never quietly reinterpreted as a valid polygon (task brief §A2).
    """
    import shapely
    from shapely.geometry import LinearRing, Polygon
    from shapely.validation import explain_validity

    ring_points = np.vstack([closed, closed[:1]])
    ring = LinearRing(ring_points)
    info: dict[str, Any] = {
        "count": 0,
        "is_simple": bool(ring.is_simple),
        "reason": None,
        "points": [],
        "region_impact": 0.0,
        "material": False,
    }
    if ring.is_simple:
        return info
    polygon = Polygon(ring_points)
    reason = explain_validity(polygon)
    info["reason"] = str(reason)
    info["points"] = _parse_coordinates(str(reason))
    info["count"] = max(len(info["points"]), 1)
    drawn = abs(signed_area(closed))
    hull_area = float(polygon.convex_hull.area)
    repaired = shapely.make_valid(polygon)
    impact = abs(float(repaired.area) - drawn) / max(hull_area, 1e-12)
    info["region_impact"] = float(impact)
    info["material"] = bool(impact > MATERIAL_CROSS_AREA_REL)
    if not info["points"]:
        # GEOS did not name the crossing(s); locate them where the trace revisits
        # itself, so the report still says where the ambiguity is.
        step = max(1, closed.shape[0] // 512)
        candidates = closed[::step]
        distance = np.linalg.norm(candidates[:, None, :] - candidates[None, :, :], axis=2)
        np.fill_diagonal(distance, np.inf)
        hits = np.argwhere(distance < 1e-6 * max(trace_scale(candidates), 1.0))
        info["points"] = [(float(candidates[i, 0]), float(candidates[i, 1])) for i, _ in hits[:4]]
    return info


def _parse_coordinates(reason: str) -> list[tuple[float, float]]:
    """Read ``[(x y), (x y)]`` pairs out of a GEOS validity string."""
    pairs = re.findall(r"\[(-?[\d.]+) (-?[\d.]+)", reason)
    return [(float(x), float(y)) for x, y in pairs]


def close_trace(
    points: np.ndarray, *, closed: bool | None = None, min_points: int = 8
) -> tuple[np.ndarray, dict[str, float], list[str]]:
    """Decide the closure of a stroke and return the closed outline.

    ``closed=None`` means "judge from the geometry": the gap between the first
    and last point is compared with the shape's own scale, so a small overshoot
    is closed and an obviously open curve is rejected rather than sealed into a
    misleading landform.

    The scale test alone is not sufficient, because the gap has a floor set by
    the input's own resolution: a closed ring sampled at ``N`` points along its
    perimeter leaves a wrap chord of ``perimeter / N``, which for an elongated
    landform exceeds ``OPEN_REJECT_REL * scale`` — measured on the real library,
    two of 839 contours are genuinely closed but gap up to 0.196 of their RMS
    radius. So a gap is also accepted as a *seam* when it is about one sample
    step long **and** short relative to the perimeter it would complete; both
    limits are needed, because the first is what a densely traced outline can
    claim and the second is what stops a sparse trace from sealing a real hole.
    """
    notes: list[str] = []
    scale = trace_scale(points)
    if scale <= 0.0:
        raise SketchError("degenerate trace: all points coincide")
    gap = float(np.linalg.norm(points[0] - points[-1]))
    gap_rel = gap / scale
    step = np.linalg.norm(np.diff(points, axis=0), axis=1)
    med_step = float(np.median(step))
    perimeter = float(step.sum()) + gap
    seam_allow = min(SEAM_STEP_TOLERANCE * med_step, SEAM_PERIMETER_SHARE * perimeter)
    limit = max(OPEN_REJECT_REL * scale, seam_allow)
    closure = {
        "gap": gap,
        "gap_rel": gap_rel,
        "scale": scale,
        "auto_closed": 0.0,
        "seam_closed": 0.0,
        "min_points": float(min_points),
        "median_step": med_step,
        "perimeter": perimeter,
        "reject_limit_rel": limit / scale,
    }
    if points.shape[0] < min_points:
        raise SketchError(
            f"trace too short to judge its shape: {points.shape[0]} points "
            f"(need >= {min_points}); redraw the outline"
        )

    wants_closed = True if closed is None else bool(closed)
    if not wants_closed:
        # The caller says the stroke is intentionally open. Only a trace that
        # already *looks* closed is usable for contour retrieval.
        if gap > limit:
            raise SketchError(
                f"open stroke: start/end gap is {gap_rel:.2f} of the shape scale; "
                "an unclosed line is not a lake outline — redraw it closed"
            )
        notes.append("input declared open but the endpoints nearly touch; closed by chord")
        closure["auto_closed"] = 1.0
    elif gap > limit:
        raise SketchError(
            f"open stroke: start/end gap is {gap_rel:.2f} of the shape scale "
            f"(limit {limit / scale:.2f}); redraw the outline closed"
        )
    elif gap > OPEN_REJECT_REL * scale:
        closure["seam_closed"] = 1.0
        closure["auto_closed"] = 1.0
        notes.append(
            f"start/end gap of {gap / med_step:.1f} sample steps sealed as the ring's "
            "own wrap chord; the input is closed by its sampling, not by an endpoint match"
        )
    elif gap_rel > AUTO_CLOSE_REL:
        notes.append(
            f"start/end gap of {gap_rel:.3f} of the shape scale sealed by a straight "
            "chord; that chord is an assumption, not something you drew"
        )
        closure["auto_closed"] = 1.0
    elif gap > 0.0:
        closure["auto_closed"] = 1.0

    if gap <= 0.0:
        outline = points
    else:
        outline = points[:-1]  # the closing chord is implied, not a vertex
    return outline, closure, notes


def smooth_cyclic(points: np.ndarray, *, sigma: float) -> np.ndarray:
    """Cyclic Gaussian low-pass of an arc-length-uniform closed contour.

    Linear and zero-phase, so it shrinks no area and shifts no feature along the
    boundary: it only attenuates detail finer than ``sigma`` sample steps
    (``sigma * perimeter / N`` in canvas units). ``sigma = 0`` returns the input.
    """
    if sigma <= 0.0:
        return points
    n = points.shape[0]
    z = points[:, 0] + 1j * points[:, 1]
    freq = np.fft.fftfreq(n)
    response = np.exp(-2.0 * (np.pi * sigma * freq) ** 2)
    smoothed = np.fft.ifft(np.fft.fft(z) * response)
    return np.column_stack((smoothed.real, smoothed.imag))


def _check_degenerate(closed: np.ndarray) -> tuple[float, float]:
    perimeter = ring_perimeter(closed)
    area = abs(signed_area(closed))
    if perimeter <= 0.0:
        raise SketchError("degenerate trace: zero perimeter")
    compactness = 4.0 * np.pi * area / (perimeter**2)
    if area <= 0.0 or compactness < MIN_COMPACTNESS:
        raise SketchError(
            f"degenerate shape: it encloses almost no area (isoperimetric ratio "
            f"{compactness:.2e}); a line drawn back on itself cannot be matched "
            "against a lake outline"
        )
    return perimeter, compactness


def contour_from_points(
    points: Any,
    *,
    closed: bool | None = None,
    n_points: int = DEFAULT_N_POINTS,
    smoothing: str | float = DEFAULT_SMOOTHING,
    allow_self_intersection: bool = DEFAULT_ALLOW_SELF_INTERSECTION,
    unit: str = "canvas",
) -> QueryContour:
    """Abstract a vector stroke trace into a standardized query contour.

    Chain: dedupe -> closure decision -> degeneracy check -> self-intersection
    check -> CCW winding -> arc-length resample -> smoothing -> normalize. The
    result satisfies the library contract, so every downstream feature is
    computed by the same code path as the candidates.
    """
    raw = _as_point_array(points, "points")
    scale = trace_scale(raw)
    deduped = dedupe_points(raw, scale=scale)
    outline, closure, notes = close_trace(deduped, closed=closed)
    closure["scale"] = scale
    perimeter, compactness = _check_degenerate(outline)
    closure.update(
        {
            "perimeter": perimeter,
            "perimeter_over_scale": perimeter / scale,
            "compactness": compactness,
        }
    )

    crossing = _self_intersection_info(outline)
    if crossing["count"]:
        if crossing["material"] and not allow_self_intersection:
            raise SketchError(
                f"self-intersecting stroke ({crossing['count']} crossing, "
                f"{crossing['reason']}): the crossing moves {crossing['region_impact']:.1%} of the "
                "hull in or out of the enclosed region, so this is not a polygon outline yet and "
                "treating it as one would change what you drew. Redraw it, or pass "
                "allow_self_intersection / --allow-self-intersection to match it as a closed "
                "curve (area-based statistics then describe the curve, not a region)"
            )
        elif crossing["material"]:
            notes.append(
                f"material self-crossing accepted as a closed curve ({crossing['reason']}); "
                "compactness / solidity / diameter_fill are region statistics of the "
                "self-intersecting path, so read them as curve statistics"
            )
        else:
            notes.append(
                f"{crossing['count']} small self-crossing(s) kept as drawn: the loop moves only "
                f"{crossing['region_impact']:.2%} of the hull, i.e. it is pen wobble, and it was "
                "not removed or re-connected"
            )

    if n_points < 3:
        raise SketchError(f"n_points must be >= 3, got {n_points}")
    ordered = ensure_ccw(open_ring(outline))
    winding = "ccw" if signed_area(ordered) >= 0.0 else "cw"
    working = arc_length_resample(ordered, n_points)
    sigma = _sigma_for(smoothing)
    smoothed = smooth_cyclic(working, sigma=sigma)
    if abs(signed_area(smoothed)) < 1e-9 * max(ring_perimeter(smoothed), 1.0) ** 2:
        raise SketchError("smoothing collapsed the trace to (almost) zero area; retry with none")
    return QueryContour(
        points=normalize_contour(smoothed),
        source="trace",
        raw_points=raw,
        outline_points=outline,
        working_points=working,
        smoothed_points=smoothed,
        n_points=n_points,
        smoothing=_label_for(smoothing, sigma),
        smoothing_sigma=sigma,
        unit=unit,
        closure=closure,
        winding=winding,
        self_intersection=crossing,
        image_info={"notes": notes},
    )


def _sigma_for(smoothing: str | float) -> float:
    if isinstance(smoothing, str):
        if smoothing not in SMOOTHING_SIGMA:
            raise SketchError(
                f"unknown smoothing level {smoothing!r}; use one of "
                f"{sorted(SMOOTHING_SIGMA)} or a numeric sigma"
            )
        return SMOOTHING_SIGMA[smoothing]
    sigma = float(smoothing)
    if not np.isfinite(sigma) or sigma < 0.0:
        raise SketchError(f"smoothing sigma must be >= 0, got {smoothing}")
    return sigma


def _label_for(smoothing: str | float, sigma: float) -> str:
    if isinstance(smoothing, str):
        return smoothing
    return f"sigma={sigma:g}"


def load_sketch_json(path: Path | str) -> tuple[np.ndarray, bool | None]:
    """Read a sketch file written by the canvas tool.

    Accepted shapes: ``{"points": [[x, y], ...], "closed": true}`` and a bare
    list of pairs. ``closed`` is optional; when absent the geometry decides.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return _as_point_array(payload, "sketch points"), None
    if not isinstance(payload, dict):
        raise SketchError(f"{path}: expected an object or a list of points")
    if "points" in payload:
        raw = payload["points"]
    elif "strokes" in payload:
        strokes = payload["strokes"]
        if not isinstance(strokes, list) or len(strokes) != 1:
            raise SketchError(
                f"{path}: {len(strokes) if isinstance(strokes, list) else '?'} strokes; "
                "V1 takes one closed outline per query"
            )
        raw = strokes[0]
    else:
        raise SketchError(f"{path}: no 'points' key")
    closed = payload.get("closed")
    if closed is not None and not isinstance(closed, bool):
        raise SketchError(f"{path}: 'closed' must be true or false")
    return _as_point_array(raw, "sketch points"), closed


# --- image input -------------------------------------------------------------


def _read_image_bytes(path: Path | str) -> np.ndarray:
    import cv2

    buffer = np.fromfile(str(path), dtype=np.uint8)
    if buffer.size == 0:
        raise SketchError(f"{path}: empty file")
    image: np.ndarray | None = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise SketchError(f"{path}: not a readable image")
    return image


def _binary_mask(image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Split ink from background without assuming which side is which.

    Anti-aliased edges are thresholded at Otsu's level, which sits at the middle
    of the intensity ramp and therefore on the geometric centre of the drawn
    line — the ramp itself must not become shape detail.
    """
    import cv2

    info: dict[str, Any] = {"shape": list(image.shape)}
    if image.ndim == 3:
        if image.shape[2] == 4:
            alpha = image[:, :, 3]
            if float(alpha.min()) < 250.0:
                # transparent background: anything painted is ink
                ink = alpha > 8
                info["polarity"] = "alpha"
                painted = np.where(ink, 255, 0).astype(np.uint8)
                return painted, info
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim != 2:
        raise SketchError(f"unsupported image with shape {image.shape}")
    levels = float(image.max() - image.min())
    info["intensity_range"] = levels
    if levels < 32.0:
        raise SketchError(
            f"image is almost flat (intensity range {levels:.0f} of 255); it does not "
            "contain a black-on-white outline to extract"
        )
    threshold, _ = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border = np.concatenate([image[0, :], image[-1, :], image[:, 0], image[:, -1]]).astype(float)
    background_is_light = float(np.median(border)) > threshold
    info.update(
        {
            "otsu_threshold": float(threshold),
            "border_median": float(np.median(border)),
            "polarity": "dark ink on light background"
            if background_is_light
            else "light ink on dark background",
        }
    )
    # ``<=`` / ``>`` rather than ``<``: on a flat two-level image Otsu returns 0,
    # and the ink class would then be empty. Splitting at the middle of the
    # anti-aliasing ramp is what keeps line width out of the geometry.
    mask = (image <= threshold) if background_is_light else (image > threshold)
    coverage = float(np.count_nonzero(mask)) / mask.size
    if coverage == 0.0 or coverage > 0.99:
        raise SketchError(
            f"cannot separate ink from background (threshold {threshold:.0f}, ink covers "
            f"{coverage:.1%} of the image); export a simple black-on-white outline"
        )
    return np.where(mask, 255, 0).astype(np.uint8), info


def _components(mask: np.ndarray, info: dict[str, Any]) -> np.ndarray:
    """Keep the largest substantial blob and refuse to guess between several."""
    import cv2

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = np.asarray([stats[i, cv2.CC_STAT_AREA] for i in range(1, count)], dtype=float)
    if areas.size == 0:
        raise SketchError("no ink found in the image")
    order = np.argsort(-areas)
    largest = areas[order[0]]
    substantial = [index for index in order if areas[index] >= IMAGE_AMBIGUOUS_SHARE * largest]
    dropped = int(np.count_nonzero(areas < IMAGE_NOISE_SHARE * largest))
    info["components"] = int(areas.size)
    info["kept_components"] = [int(areas[i]) for i in substantial]
    if dropped:
        info.setdefault("warnings", []).append(
            f"{dropped} speckle component(s) below "
            f"{IMAGE_NOISE_SHARE:.0%} of the largest were ignored"
        )
    if len(substantial) > 1:
        boxes = [
            [int(stats[i + 1, cv2.CC_STAT_LEFT]), int(stats[i + 1, cv2.CC_STAT_TOP]),
             int(stats[i + 1, cv2.CC_STAT_WIDTH]), int(stats[i + 1, cv2.CC_STAT_HEIGHT])]
            for i in substantial
        ]
        raise SketchError(
            f"image contains {len(substantial)} separate shapes (areas "
            f"{[int(areas[i]) for i in substantial]}, boxes {boxes}); V1 extracts a "
            "single closed outline — send one shape per query (lettering and "
            "separate marks are not guessed at)"
        )
    keep = substantial[0] + 1
    return np.where(labels == keep, 255, 0).astype(np.uint8)


def _contours(mask: np.ndarray) -> list[np.ndarray]:
    import cv2

    found, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    rings = [np.asarray(ring.reshape(-1, 2), dtype=float) for ring in found]
    return [ring for ring in rings if ring.shape[0] >= 3]


def _mid_line(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """Centre-line of a stroked outline: each outer edge point averaged with the
    nearest hole edge point, so the pen width offsets the geometry by about half
    a stroke instead of inflating the whole shape."""
    from scipy.spatial import cKDTree

    tree = cKDTree(inner)
    distance, index = tree.query(outer)
    paired = 0.5 * (outer + inner[index])
    # A very long nearest-hole distance means the two rings are not opposites
    # here (a pinch or a fork); keep the outer point rather than invent a mid.
    scale = trace_scale(outer)
    keep = distance <= 0.5 * scale
    return np.where(keep[:, None], paired, outer)


def contour_from_image(
    image: Any,
    *,
    n_points: int = DEFAULT_N_POINTS,
    smoothing: str | float = DEFAULT_SMOOTHING,
    allow_self_intersection: bool = DEFAULT_ALLOW_SELF_INTERSECTION,
) -> QueryContour:
    """Abstract a simple black-and-white outline PNG into a query contour.

    Scope on purpose: one closed shape, filled or stroked. Anything that needs a
    guess — several shapes, lettering, a photo, a stroke whose two ends never met —
    raises instead of interpreting the user's intent (task brief §A1). A stroked
    arc really does leave a closed pixel band, so the refusal is made on the band's
    own measurements (:data:`OPEN_STROKE_WIDTH_REL`, :data:`OPEN_STROKE_SOLIDITY`)
    and says which measurement tripped.
    """
    info_base: dict[str, Any]
    if isinstance(image, (str, Path)):
        array = _read_image_bytes(image)
        info_base = {"path": str(image).replace("\\", "/")}
    else:
        array = np.asarray(image)
        if array.ndim not in (2, 3):
            raise SketchError(f"image must be 2-D or 3-D, got shape {array.shape}")
        info_base = {"path": None}
    mask, info = _binary_mask(array)
    info.update(info_base)
    single = _components(mask, info)
    rings = _contours(single)
    if not rings:
        raise SketchError("the ink blob has no closed boundary to extract")
    outer = max(rings, key=lambda ring: abs(signed_area(open_ring(ring))))
    holes = [
        ring
        for ring in rings
        if ring is not outer
        and abs(signed_area(open_ring(ring)))
        >= IMAGE_HOLE_SHARE * abs(signed_area(open_ring(outer)))
    ]
    area_outer = abs(signed_area(open_ring(outer)))
    perimeter_outer = ring_perimeter(open_ring(outer))
    stroke_width = float(np.count_nonzero(single)) / max(perimeter_outer, 1.0)
    radius = float(np.sqrt(area_outer / np.pi))
    info.update(
        {
            "pixel_perimeter": perimeter_outer,
            "pixel_area": area_outer,
            "stroke_width_px": stroke_width,
            "equivalent_radius_px": radius,
            "holes": len(holes),
            "canvas": [int(shape) for shape in (
                array.shape[0], array.shape[1] if array.ndim > 1 else array.shape[0]
            )],
        }
    )
    if stroke_width < IMAGE_STROKE_REL * radius and len(holes) == 1:
        trace = _mid_line(outer, holes[0])
        info["representation"] = "stroke mid-line (outer edge paired with hole edge)"
    elif len(holes) >= 1:
        trace = outer
        info["representation"] = "outer edge (a hole exists, ring too thick to be a stroke)"
        info.setdefault("warnings", []).append(
            f"ink band about {stroke_width:.1f}px thick was read as a filled region, "
            "so its outer edge bounds the shape"
        )
    else:
        trace = outer
        # Imported here rather than at module scope: earthglyph.features depends on
        # earthglyph.geometry, so a top-level import would be a cycle.
        from earthglyph.features.geometry_stats import convex_hull_area

        band_width = 2.0 * area_outer / max(perimeter_outer, 1e-9)
        width_rel = band_width / trace_scale(outer)
        solidity = area_outer / max(convex_hull_area(outer), 1e-9)
        info["band_width"] = band_width
        info["band_width_rel"] = width_rel
        info["fill_solidity"] = solidity
        if width_rel < OPEN_STROKE_WIDTH_REL and solidity < OPEN_STROKE_SOLIDITY:
            raise SketchError(
                f"the ink is a {band_width:.1f}-pixel band (width "
                f"{width_rel:.3f} of the shape scale) that fills only "
                f"{solidity:.1%} of its own convex hull: that is a stroke which does "
                "not close, not an outline. Join the two ends and redraw, or submit a "
                "vector trace — the open curve is not guessed shut into a shape"
            )
        info["representation"] = "outer edge of a filled region"
    if stroke_width >= 1.0:
        info["line_width_bias"] = stroke_width / max(trace_scale(trace), 1e-9)
    # image rows increase downwards; geometry is y-up. Flipping keeps "CCW"
    # meaning counter-clockwise as seen on screen.
    flipped = np.column_stack([trace[:, 0], array.shape[0] - 1 - trace[:, 1]])
    contour = contour_from_points(
        flipped,
        closed=True,
        n_points=n_points,
        smoothing=smoothing,
        allow_self_intersection=allow_self_intersection,
        unit="pixel",
    )
    quantization = trace_scale(flipped)
    image_warnings = list(info.get("warnings", []))
    image_warnings.append(
        f"the pixel grid quantises the boundary at about {0.5 / quantization:.1e} of the "
        f"shape scale (half a pixel on a {quantization:.0f}-pixel-radius drawing)"
    )
    info["quantization_rel"] = 0.5 / quantization
    merged = {**contour.image_info, **info}
    merged["notes"] = list(contour.image_info.get("notes", []))
    merged["warnings"] = image_warnings
    return QueryContour(
        points=contour.points,
        source="image",
        raw_points=contour.raw_points,
        outline_points=contour.outline_points,
        working_points=contour.working_points,
        smoothed_points=contour.smoothed_points,
        n_points=contour.n_points,
        smoothing=contour.smoothing,
        smoothing_sigma=contour.smoothing_sigma,
        unit="pixel",
        closure=contour.closure,
        winding=contour.winding,
        self_intersection=contour.self_intersection,
        image_info=merged,
    )


def contour_from_sketch(
    target: Any,
    *,
    closed: bool | None = None,
    n_points: int = DEFAULT_N_POINTS,
    smoothing: str | float = DEFAULT_SMOOTHING,
    allow_self_intersection: bool = DEFAULT_ALLOW_SELF_INTERSECTION,
    unit: str = "canvas",
) -> QueryContour:
    """Single entry point: a point array, a sketch JSON path or an image path.

    Anything ending in ``.png`` / ``.jpg`` / ``.jpeg`` is treated as an image,
    ``.json`` as a trace file, an ndarray of points as a trace. ``unit`` names the
    coordinate frame of a *trace* only — an image is always in pixels.
    """
    if isinstance(target, (str, Path)):
        suffix = Path(target).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
            return contour_from_image(
                target,
                n_points=n_points,
                smoothing=smoothing,
                allow_self_intersection=allow_self_intersection,
            )
        if suffix in {".json", ".txt"}:
            points, file_closed = load_sketch_json(target)
            return contour_from_points(
                points,
                closed=closed if closed is not None else file_closed,
                n_points=n_points,
                smoothing=smoothing,
                allow_self_intersection=allow_self_intersection,
                unit=unit,
            )
        raise SketchError(f"{target}: unsupported query file type {suffix!r}")
    return contour_from_points(
        target,
        closed=closed,
        n_points=n_points,
        smoothing=smoothing,
        allow_self_intersection=allow_self_intersection,
        unit=unit,
    )
