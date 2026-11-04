"""How faithfully a resampled contour reproduces its source boundary (P0-3, Task C).

Three different quantities are routinely confused, so this module keeps them
separate and separately measurable:

1. **arc-length sampling interval** — by construction the samples are equidistant
   *along the source curve* (``earthglyph.geometry.arc_length_resample``), so
   this has no error at all and is not a quality measure;
2. **straight-line distance between adjacent samples** (the chord) — shorter than
   the arc it replaces whenever the curve bends; summarized by
   ``perimeter_retention`` (polyline length / source length) and
   ``max_shortcut_ratio`` (largest relative chord shortfall);
3. **geometric reconstruction error** — the distance between the closed polyline
   and the source boundary, in metres and normalized by the equal-area radius:
   symmetric Hausdorff, symmetric Chamfer, region IoU and area error.

P0-2 reported a "~104 % worst-case arc-length step deviation". That number is a
dispersion statistic of the chord lengths around their own mean
(``max |d_i - mean(d)| / mean(d)``) and can exceed 100 % on dendritic shorelines;
it is **not** a reconstruction error and is not recomputed here except as
``chord_dispersion`` so the two are never conflated again.

Curve-to-curve distances are approximated by point clouds densified along each
polyline (``_curve_cloud``), which keeps every source vertex and bounds the
number of *added* points by ``cloud_cap``. The residual error is at most one
cloud step (``step / densify_divisor``, or ``perimeter / cloud_cap`` when the cap
binds) and it skews *high* — a coarse reference cloud can put its nearest point
beside a chord instead of on the perpendicular foot — so the reported deviation
is a conservative upper bound, never an optimistic one.
Everything is deterministic: no sampling, no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from earthglyph.geometry.resampling import ring_perimeter, signed_area

DEFAULT_DENSIFY_DIVISOR = 8  # cloud step = arc-length interval / 8
DEFAULT_CLOUD_CAP = 40_000  # hard bound on points *added* per curve, for cost control


class FidelityError(ValueError):
    """Raised when two rings cannot be compared at all."""


@dataclass(frozen=True)
class FidelityMetrics:
    """Reconstruction error of one sampled ring against its source boundary.

    Distances are metres in the local projection (D16); ``*_norm`` divides them
    by the equal-area radius ``sqrt(area / pi)`` of the source exterior, so a
    large lake does not look worse than a pond purely because of its size.
    """

    source_perimeter_m: float
    sampled_perimeter_m: float
    equal_area_radius_m: float
    step_m: float
    perimeter_retention: float  # sampled/source, <= 1
    max_shortcut_ratio: float  # 1 - min(chord)/step, in [0, 1]
    chord_dispersion: float  # P0-2 style, max |d - mean(d)| / mean(d)
    hausdorff_m: float
    hausdorff_norm: float
    chamfer_m: float
    chamfer_norm: float
    region_iou: float
    area_rel_error: float
    self_intersected: bool  # sampled chord polyline is not a valid ring
    validity_reason: str | None  # GEOS complaint, e.g. Self-intersection[...]
    source_ring_valid: bool  # the *source* exterior ring needs no repair either
    n_source_cloud: int
    n_sampled_cloud: int

    def as_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in (
                "source_perimeter_m",
                "sampled_perimeter_m",
                "equal_area_radius_m",
                "step_m",
                "perimeter_retention",
                "max_shortcut_ratio",
                "chord_dispersion",
                "hausdorff_m",
                "hausdorff_norm",
                "chamfer_m",
                "chamfer_norm",
                "region_iou",
                "area_rel_error",
                "self_intersected",
                "validity_reason",
                "source_ring_valid",
                "n_source_cloud",
                "n_sampled_cloud",
            )
        }


def _as_ring(ring: Any) -> np.ndarray:
    """Open ring (closing vertex removed) validated for shape and finiteness."""
    arr = np.asarray(ring, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise FidelityError(f"ring must have shape (M, 2), got {arr.shape}")
    if arr.shape[0] < 3:
        raise FidelityError(f"a ring needs at least 3 points, got {arr.shape[0]}")
    if not np.all(np.isfinite(arr)):
        raise FidelityError("ring contains non-finite coordinates")
    if np.allclose(arr[0], arr[-1], rtol=0.0, atol=1e-12):
        arr = arr[:-1]
    return arr


def exterior_only(geometry: Any) -> Any:
    """The same polygon without its holes, so 'outer boundary' means one thing.

    The candidate sets carry up to 234 interior rings (islands). Sampling targets
    the exterior ring only (D21), so every fidelity comparison must drop holes on
    both sides to stay consistent.
    """
    import shapely

    if geometry.geom_type == "MultiPolygon":
        geometry = max(list(geometry.geoms), key=lambda g: g.area)
    return shapely.Polygon(geometry.exterior)


def chord_lengths(ring: Any) -> np.ndarray:
    """Straight-line distance between consecutive samples of a closed ring."""
    arr = _as_ring(ring)
    closed = np.vstack([arr, arr[:1]])
    return np.linalg.norm(np.diff(closed, axis=0), axis=1)


def _curve_cloud(ring: np.ndarray, max_step: float, cap: int) -> np.ndarray:
    """Points along a closed polyline, no further apart than ``max_step``.

    Original vertices are always kept; segments longer than ``max_step`` get
    intermediate points. If the result would exceed ``cap`` points the step is
    enlarged once, which bounds cost while keeping the cloud deterministic.
    """
    closed = np.vstack([ring, ring[:1]])
    segments = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    total = float(segments.sum())
    if total <= 0.0 or not np.isfinite(total):
        raise FidelityError("ring has zero perimeter")
    step = max_step
    if total / step > cap:
        step = total / cap
    parts: list[np.ndarray] = [closed[:-1]]
    for i, length in enumerate(segments):
        count = int(np.floor(length / step))
        if count < 1:
            continue
        frac = np.arange(1, count + 1, dtype=float) / (count + 1)
        parts.append(closed[i] + np.outer(frac, closed[i + 1] - closed[i]))
    return np.vstack(parts)


def _nn_distances(query: np.ndarray, reference: np.ndarray) -> np.ndarray:
    from scipy.spatial import cKDTree

    return np.asarray(cKDTree(reference).query(query, k=1)[0])


def ring_polygon(ring: np.ndarray) -> tuple[Any, str | None]:
    """Polygon of a closed ring plus the GEOS validity complaint, if any.

    Coarse sampling across a narrow neck or a small bay can make the chord
    polyline self-intersect. That is a real finding, so it is reported instead of
    hidden: the polygon is repaired with ``make_valid`` only for the area / IoU
    terms and the validity reason is returned for accounting.
    """
    import shapely

    polygon = shapely.Polygon(np.vstack([ring, ring[:1]]))
    if polygon.is_valid:
        return polygon, None
    reason: str = shapely.is_valid_reason(polygon)
    return shapely.make_valid(polygon), reason


def max_deviation_point(
    source_ring: Any,
    sampled_ring: Any,
    *,
    densify_divisor: int = DEFAULT_DENSIFY_DIVISOR,
    cloud_cap: int = DEFAULT_CLOUD_CAP,
) -> tuple[float, float, float]:
    """Where the source boundary sits furthest from the reconstruction.

    Returns ``(x, y, distance_m)`` of the source-boundary point with the largest
    distance to the sampled polyline — the place a figure should zoom into.
    """
    source = _as_ring(source_ring)
    sampled = _as_ring(sampled_ring)
    step = ring_perimeter(source) / sampled.shape[0]
    ref_step = step / max(1, densify_divisor)
    source_cloud = _curve_cloud(source, ref_step, cloud_cap)
    sampled_cloud = _curve_cloud(sampled, ref_step, cloud_cap)
    distances = _nn_distances(source_cloud, sampled_cloud)
    worst = int(np.argmax(distances))
    x, y = (float(v) for v in source_cloud[worst])
    return x, y, float(distances[worst])


def region_iou(a: Any, b: Any) -> float:
    """Intersection over union of two areal geometries."""
    import shapely

    intersection = float(shapely.intersection(a, b).area)
    union = float(shapely.union(a, b).area)
    return intersection / union if union > 0.0 else 0.0


def contour_fidelity(
    source_ring: Any,
    sampled_ring: Any,
    *,
    densify_divisor: int = DEFAULT_DENSIFY_DIVISOR,
    cloud_cap: int = DEFAULT_CLOUD_CAP,
) -> FidelityMetrics:
    """Compare one resampled closed polyline against its source boundary.

    Both rings must be given in the same metric frame (projected metres). The
    source ring defines the truth; the sampled ring is the reconstruction. The
    densification step is derived from the source perimeter and the number of
    samples, so the two arguments are not interchangeable: the Hausdorff maximum
    survives a swap, the Chamfer mean and the signed area error do not.
    """
    source = _as_ring(source_ring)
    sampled = _as_ring(sampled_ring)
    source_perimeter = ring_perimeter(source)
    if not np.isfinite(source_perimeter) or source_perimeter <= 0.0:
        raise FidelityError("source ring has zero perimeter")
    step = source_perimeter / sampled.shape[0]

    chords = chord_lengths(sampled)
    mean_chord = float(chords.mean())
    retention = ring_perimeter(sampled) / source_perimeter
    area = abs(signed_area(source))
    equal_area_radius = float(np.sqrt(area / np.pi)) if area > 0 else float("nan")

    ref_step = step / max(1, densify_divisor)
    source_cloud = _curve_cloud(source, ref_step, cloud_cap)
    sampled_cloud = _curve_cloud(sampled, ref_step, cloud_cap)
    forward = _nn_distances(sampled_cloud, source_cloud)
    backward = _nn_distances(source_cloud, sampled_cloud)
    hausdorff = float(max(forward.max(), backward.max()))
    chamfer = float(0.5 * (forward.mean() + backward.mean()))

    source_polygon, source_reason = ring_polygon(source)
    sampled_polygon, validity_reason = ring_polygon(sampled)
    iou = region_iou(source_polygon, sampled_polygon)
    area_error = (
        float(sampled_polygon.area - source_polygon.area) / source_polygon.area
        if source_polygon.area > 0
        else float("nan")
    )

    norm = equal_area_radius if equal_area_radius and equal_area_radius > 0 else 1.0
    return FidelityMetrics(
        source_perimeter_m=source_perimeter,
        sampled_perimeter_m=ring_perimeter(sampled),
        equal_area_radius_m=equal_area_radius,
        step_m=step,
        perimeter_retention=retention,
        max_shortcut_ratio=float(max(0.0, 1.0 - chords.min() / step)),
        chord_dispersion=float(np.abs(chords - mean_chord).max() / mean_chord),
        hausdorff_m=hausdorff,
        hausdorff_norm=hausdorff / norm,
        chamfer_m=chamfer,
        chamfer_norm=chamfer / norm,
        region_iou=iou,
        area_rel_error=area_error,
        self_intersected=validity_reason is not None,
        validity_reason=validity_reason,
        source_ring_valid=source_reason is None,
        n_source_cloud=int(source_cloud.shape[0]),
        n_sampled_cloud=int(sampled_cloud.shape[0]),
    )


def summarize_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Percentile summary of the numeric columns of a per-candidate table."""
    if not rows:
        return {}
    frame = [key for key in rows[0] if isinstance(rows[0][key], (int, float, np.floating))]
    out: dict[str, Any] = {}
    for key in sorted(frame):
        values = np.asarray([float(row[key]) for row in rows if row[key] is not None])
        if values.size == 0 or not np.all(np.isfinite(values)):
            values = values[np.isfinite(values)]
            if values.size == 0:
                continue
        out[key] = {
            "min": float(np.min(values)),
            "p50": float(np.percentile(values, 50)),
            "p90": float(np.percentile(values, 90)),
            "p99": float(np.percentile(values, 99)),
            "max": float(np.max(values)),
            "mean": float(np.mean(values)),
        }
    return out
