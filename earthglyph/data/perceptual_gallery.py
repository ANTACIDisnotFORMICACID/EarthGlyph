"""Controlled perceptual gallery: real, licensed, struct-labelled closed shapes.

Why this module exists (P0-4.5 Task B): the production library is 839 water
bodies from one 1°×1° tile, so it cannot answer "does the matcher see a corner".
If every candidate is a shoreline, a star query can only be judged by what a
human thinks of the top ten — there is no five-lobed-with-sharp-tips negative to
rank against a circle. This module builds the missing second library: shape
families with **targets, independent variants, and hard negatives whose region
statistics are matched to the target on purpose**, so that "it looked about the
right size" is not a strategy an algorithm can win with.

Three origins, each with its own provenance record:

``phosphor``
    Real designed silhouettes from `@phosphor-icons/core` (MIT), decoded from the
    SVG with :mod:`earthglyph.data.svg_path` — paths *and* shape primitives, so a
    ``<circle>`` hard negative is read by the same code as a ``<path>`` star.
``quickdraw``
    Real human drawings from the Quick, Draw! dataset (CC BY 4.0, Google). Read
    from a leading byte slice of one category file over HTTP ``Range``,
    **single-stroke drawings only**, and only those the project's own extractor
    accepts as a closed, non-self-crossing contour. Every refusal is counted with
    its reason, because the refusals are a finding: for ``star`` most single-stroke
    human drawings are pentagrams, i.e. self-crossing, so they cannot be treated
    as a region outline at all.
``synthetic``
    Parametric contours with the parameters written down. Used for the controls
    that no licensed asset can provide at a matched statistic — the circle of the
    same envelope, the five-lobed blob with the star's solidity and no corners —
    and for the families (heart, forked, band) where a real asset would be a
    second drawing of the same idea rather than an independent sample.

Nothing here is retrieved or scored. The module produces
``gallery.jsonl`` (raw + standardized contours + metrics + provenance),
``labels.json`` (family membership and the T1/T2/T3 splits, frozen before any
algorithm runs) and ``dataset_manifest.json`` (license, sha256, byte ranges,
rejection counts). Controlled shapes are **not** geography and are never written
into ``data/index/``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from earthglyph.data.svg_path import (
    Subpath,
    decode_path,
    exterior_subpaths,
    flip_y,
    subpath_signed_areas,
    svg_invisible_elements,
    svg_subpaths,
    view_box,
)
from earthglyph.features.geometry_stats import shape_metrics
from earthglyph.geometry.resampling import (
    DEFAULT_N_POINTS,
    arc_length_resample,
    ensure_ccw,
    open_ring,
    ring_perimeter,
    signed_area,
)
from earthglyph.geometry.sketch import SketchError, contour_from_points

GALLERY_VERSION = "1.0"
N_SAMPLES = DEFAULT_N_POINTS
SMOOTHING = "none"  # gallery members are clean outlines; pen jitter is a query concern

FAMILIES = ("star", "crescent", "heart", "forked", "band", "blob", "irregular")
ROLES = ("query", "variant", "hard_negative")

# Families whose defining structure is a concavity (corner, cleft, prong, horn,
# turn). "blob" and "irregular" are excluded: a smooth outline is the point there,
# so convexity says nothing about whether a member belongs.
CONCAVE_STRUCTURE_FAMILIES = frozenset({"star", "crescent", "heart", "forked", "band"})

# A variant must keep at least this share of the convexity deficiency of the
# *mildest* query in its family. A quarter is deliberately generous: it rejects a
# disk posing as a crescent, not a worn or slightly lopsided family member, and
# the mildest query — not the deepest — defines what the family requires.
MIN_VARIANT_CONCAVE_SHARE = 0.25
# Below this, a contour simply has no concavity, so it cannot be the query of a
# family whose defining structure is one.
MIN_FAMILY_CONCAVE = 0.01

# The design brief's structural ranges, re-measured on the built gallery by
# :func:`check_design_coverage` rather than trusted from the plan.
MIN_CLASSES = 6
MIN_QUERIES_PER_FAMILY = 2
VARIANT_RANGE = (5, 15)
NEGATIVE_RANGE = (10, 20)

# --- provenance of the two external sources ----------------------------------

PHOSPHOR: dict[str, Any] = {
    "project": "phosphor-icons/core (@phosphor-icons/core 2.1.1)",
    "repository": "https://github.com/phosphor-icons/core",
    "license": "MIT",
    "license_spdx": "MIT",
    "redistribution": "allowed with attribution (MIT)",
    "attribution": "Copyright (c) 2018-2024 Phosphor Icons",
    "url_template": (
        "https://raw.githubusercontent.com/phosphor-icons/core/main/raw/"
        "{weight}/{name}-{weight}.svg"
    ),
}
QUICKDRAW: dict[str, Any] = {
    "project": "The Quick, Draw! Dataset (Google, 2017)",
    "repository": "https://github.com/googlecreativelab/quickdraw-dataset",
    "license": "CC BY 4.0",
    "license_spdx": "CC-BY-4.0",
    "redistribution": "allowed with attribution (CC BY 4.0)",
    "attribution": "Data made available by Google, Inc. under CC BY 4.0",
    "format": "simplified ndjson: one JSON object per drawing, strokes as [xs, ys]",
    "url_template": (
        "https://storage.googleapis.com/quickdraw_dataset/full/simplified/{quote}.ndjson"
    ),
    "canvas_px": 256,
}
SYNTHETIC: dict[str, Any] = {
    "project": "EarthGlyph controlled gallery (parametric generators)",
    "license": "MIT (project source)",
    "license_spdx": "MIT",
    "note": "generated by earthglyph/data/perceptual_gallery.py, parameters recorded",
}

USER_AGENT = "EarthGlyph-perceptual-gallery/1.0 (research; reproducible dataset build)"

# Quick Draw acceptance accounting thresholds — the extractor's own refusals,
# plus one hard floor on how few vertices a "contour" may have.
QD_MIN_STROKE_POINTS = 8
QD_SLICE_BYTES = 512_000
QD_SLICE_FRACTIONS = (0.0, 0.25, 0.5, 0.75)

# A gallery member is a *single closed contour*: one exterior ring. Assets whose
# silhouette is genuinely several pieces are refused here and counted, never
# merged into one invented outline.
MAX_RINGS = 1

# A negative-space icon (solid panel, motif cut out of it) decodes to the panel.
# Both tests are needed: convexity alone matches a genuine disk or hexagon, and
# covering the canvas alone matches a glyph that simply fills the icon box.
FRAME_SOLIDITY_MIN = 0.995
FRAME_VIEWBOX_FILL_MIN = 0.70


@dataclass(frozen=True)
class Rejection:
    """One asset or drawing that did not become a gallery member, with its reason."""

    source: str
    reference: str
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "reference": self.reference,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class GalleryItem:
    """One controlled shape: raw outline, standardized contour, metrics, provenance."""

    item_id: str
    family: str
    role: str
    origin: str  # "phosphor" | "quickdraw" | "synthetic"
    origin_ref: str  # icon name+weight / drawing key_id / generator+parameters
    points: np.ndarray  # raw outline, math units, y up, not yet normalized
    contour: np.ndarray  # (128, 2) centered, unit RMS radius, CCW — the library row
    metrics: dict[str, float]  # compactness / solidity / aspect_ratio / diameter_fill
    license: str
    provenance: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_record(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "family": self.family,
            "role": self.role,
            "origin": self.origin,
            "origin_ref": self.origin_ref,
            "license": self.license,
            "note": self.note,
            "n_raw_points": int(self.points.shape[0]),
            "raw_points": _round_pairs(self.points),
            "n_contour_points": int(self.contour.shape[0]),
            "contour": _round_pairs(self.contour, digits=6),
            "metrics": {key: round(value, 6) for key, value in self.metrics.items()},
            "provenance": self.provenance,
        }


def _round_pairs(points: np.ndarray, digits: int = 3) -> list[list[float]]:
    return [[round(float(x), digits), round(float(y), digits)] for x, y in points]


# --- fetching ------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_cached(
    url: str,
    cache_dir: Path,
    *,
    byte_range: tuple[int, int] | None = None,
    timeout: int = 120,
    offline: bool = False,
) -> dict[str, Any]:
    """GET once, keep the bytes under ``cache_dir``, return text + hash + size.

    The cache key includes the byte range, so a range-read and a whole-file read
    of the same URL cannot silently stand in for each other.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = f".{byte_range[0]}-{byte_range[1]}.range" if byte_range else ""
    path = cache_dir / f"{digest}{suffix}"
    if path.is_file():
        data = path.read_bytes()
        cached = True
    elif offline:
        raise RuntimeError(f"offline build requested but cache is missing: {path}")
    else:
        headers = {"User-Agent": USER_AGENT}
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        request = urllib.request.Request(url, headers=headers)
        content_range: str | None = None
        # ~100 sequential connections in one build can exhaust this machine's
        # ephemeral ports (WinError 10048). Recovery is not instant, so the retry
        # waits geometrically instead of giving up after 1.5 s; that separates a
        # local socket limit from a real network failure.
        attempts = 5
        backoff = (2.0, 8.0, 24.0, 60.0)
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read()
                    status = int(response.status)
                    content_range = response.headers.get("Content-Range")
                break
            except (urllib.error.URLError, OSError) as error:
                if attempt == attempts - 1:
                    raise RuntimeError(f"fetch failed for {url}: {error}") from error
                time.sleep(backoff[attempt])
        if byte_range is not None and status != 206:
            raise RuntimeError(
                f"{url}: asked for bytes {byte_range[0]}-{byte_range[1]} but got "
                f"status {status} (server ignored Range; refusing to mix a whole "
                "file into a range cache key)"
            )
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        cached = False
    return {
        "url": url,
        "path": path.as_posix(),
        "sha256": _sha256(data),
        "bytes": len(data),
        "byte_range": list(byte_range) if byte_range else None,
        "content_range": content_range if not cached else None,
        "cached": cached,
        "text": data.decode("utf-8", "replace"),
    }


def remote_size(url: str, *, timeout: int = 60) -> int | None:
    """``Content-Length`` of the whole object, from a one-byte range request."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_range = response.headers.get("Content-Range") or ""
            match = re.search(r"/(\d+)\s*$", content_range)
            if match:
                return int(match.group(1))
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except urllib.error.URLError:
        return None


# --- standardization -----------------------------------------------------------


def standardize(points: np.ndarray, *, n_points: int = N_SAMPLES) -> np.ndarray:
    """Raw outline -> the library contract, through the same extractor as a query.

    One closed CCW ring of ``n_points`` equal-arc-length samples, centroid at the
    origin, RMS radius 1 — produced by
    :func:`earthglyph.geometry.sketch.contour_from_points` with ``smoothing="none"``
    so no structure is filtered out of a dataset member. This is the only place
    geometry enters the gallery, so a query and a candidate really are
    comparable numbers.
    """
    contour = contour_from_points(
        points, closed=True, n_points=n_points, smoothing=SMOOTHING
    )
    return np.asarray(contour.points, dtype=float)


def item_from_points(
    *,
    item_id: str,
    family: str,
    role: str,
    origin: str,
    origin_ref: str,
    license_text: str,
    points: np.ndarray,
    note: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> GalleryItem:
    """Standardize and measure one outline, refusing anything not a closed region."""
    contour = standardize(points)
    metrics = shape_metrics(contour).as_dict()
    return GalleryItem(
        item_id=item_id,
        family=family,
        role=role,
        origin=origin,
        origin_ref=origin_ref,
        points=np.asarray(points, dtype=float),
        contour=contour,
        metrics=metrics,
        license=license_text,
        provenance=dict(provenance or {}),
        note=note,
    )


# --- phosphor assets -----------------------------------------------------------


def phosphor_rings(
    name: str,
    *,
    weight: str,
    cache_dir: Path,
    segments: int = 24,
    offline: bool = False,
) -> dict[str, Any]:
    """Decode one Phosphor icon into candidate silhouette rings (y up).

    Returns the rings plus the asset record (url, sha256, subpath census). A
    glyph made of several unconnected pieces is *reported*, not glued: the
    caller refuses it.
    """
    url = PHOSPHOR["url_template"].format(weight=weight, name=name)
    asset = fetch_cached(url, cache_dir / "phosphor", offline=offline)
    document = asset["text"]
    subpaths = svg_subpaths(document, segments=segments)
    box = view_box(document)
    height = float(box[3]) if box else 256.0
    ups = flip_y(subpaths, height)
    areas = subpath_signed_areas(ups)
    exterior = exterior_subpaths(ups)
    holes = len(ups) - len(exterior)
    raw_rings = [ring.points for ring in exterior if ring.n_points >= 4]
    rings = []
    densified = 0
    for ring in raw_rings:
        points = np.asarray(ring, dtype=float)
        if points.shape[0] > 1 and np.allclose(points[0], points[-1]):
            # an SVG closed subpath repeats its first vertex; the extractor's
            # contract is one implicit wrap chord, not a doubled point
            points = points[:-1]
        if points.shape[0] < 64:
            points = densify(points, per_edge=8)
            densified += 1
        rings.append(points)
    # Phosphor draws several icons as a solid panel with the motif cut out of it.
    # Under the exterior-only policy (D21) the silhouette of such a glyph is the
    # *panel*, so "snowflake" enters a shape gallery as a rounded rectangle. That
    # is not a decoder bug, it is the documented hole policy meeting a negative-space
    # design — and it must be reported, because a role label written against the
    # icon's name would then be a lie about the geometry the algorithms actually see.
    frame_like = False
    if len(exterior) == 1 and holes:
        from shapely.geometry import Polygon

        panel = Polygon(np.asarray(exterior[0].points, dtype=float))
        hull_area = float(panel.convex_hull.area)
        solidity = float(panel.area / hull_area) if hull_area > 0.0 else 0.0
        box_width = float(box[2]) if box else 0.0
        span_x = float(exterior[0].points[:, 0].max() - exterior[0].points[:, 0].min())
        frame_like = bool(
            box_width > 0.0
            and solidity > FRAME_SOLIDITY_MIN
            and span_x / box_width > FRAME_VIEWBOX_FILL_MIN
        )
    record = {
        "source": "phosphor",
        "icon": name,
        "weight": weight,
        "url": asset["url"],
        "sha256": asset["sha256"],
        "bytes": asset["bytes"],
        "cached": asset["cached"],
        "view_box": list(box) if box else None,
        "subpaths": len(ups),
        "exterior_rings": len(exterior),
        "holes": holes,
        "frame_like": frame_like,
        "ring_areas": [round(float(value), 2) for value in areas],
        "segments_per_command": segments,
        "rings_densified": densified,
        "invisible_elements_dropped": svg_invisible_elements(document),
        "transforms": [
            "y-down to y-up about the viewBox height",
            "no scaling, no simplification",
            "sparse straight-edge rings subdivided 8x (lossless: corners kept)",
        ],
        "license": PHOSPHOR["license"],
        "attribution": PHOSPHOR["attribution"],
    }
    return {"rings": rings, "record": record}


def phosphor_item(
    item_id: str,
    family: str,
    role: str,
    name: str,
    weight: str,
    cache_dir: Path,
    rejections: list[Rejection],
    *,
    note: str = "",
    offline: bool = False,
) -> GalleryItem | None:
    """One icon -> one gallery member, or a counted refusal."""
    try:
        decoded = phosphor_rings(name, weight=weight, cache_dir=cache_dir, offline=offline)
    except (RuntimeError, ValueError) as error:
        rejections.append(
            Rejection("phosphor", f"{weight}/{name}", "fetch-or-decode", str(error)[:200])
        )
        return None
    rings = decoded["rings"]
    if len(rings) == 0:
        rejections.append(Rejection("phosphor", f"{weight}/{name}", "no_ring", "no drawable ring"))
        return None
    if len(rings) > MAX_RINGS:
        rejections.append(
            Rejection(
                "phosphor",
                f"{weight}/{name}",
                "multiple_pieces",
                f"{len(rings)} unconnected exterior rings; a gallery member is one closed "
                "contour, and gluing them would invent a shape the designer did not draw",
            )
        )
        return None
    if decoded["record"]["frame_like"]:
        rejections.append(
            Rejection(
                "phosphor",
                f"{weight}/{name}",
                "frame_silhouette",
                "the motif is cut out of a solid panel, so under the exterior-only "
                "policy the silhouette is the panel (a rounded rectangle, or a disk "
                "for circle-half): it would join the gallery wearing the icon's name "
                "and duplicate a convex member the synthetic set already contains",
            )
        )
        return None
    try:
        item = item_from_points(
            item_id=item_id,
            family=family,
            role=role,
            origin="phosphor",
            origin_ref=f"{weight}/{name}.svg",
            license_text=PHOSPHOR["license"],
            points=rings[0],
            note=note,
            provenance=decoded["record"],
        )
    except (SketchError, ValueError) as error:
        rejections.append(
            Rejection("phosphor", f"{weight}/{name}", "not_a_closed_region", str(error)[:220])
        )
        return None
    return item


# --- quick, draw! --------------------------------------------------------------


def _qd_rows(text: str, *, drop_partial_first: bool) -> list[dict[str, Any]]:
    rows = text.split("\n")
    if drop_partial_first:
        rows = rows[1:]
    out: list[dict[str, Any]] = []
    for line in rows:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a truncated tail line, not a drawing
        if isinstance(record, dict) and "drawing" in record:
            out.append(record)
    return out


def quickdraw_strokes(drawing: Sequence[Any]) -> list[np.ndarray]:
    """Split a Quick Draw ``drawing`` into strokes of ``(x, y)`` points, y up.

    The ``simplified`` format keeps a pen-up as an empty stroke (or a ``101``
    marker row in the raw format); both mean "more than one stroke", which is
    more than one contour, so such a drawing is not usable here.
    """
    strokes: list[np.ndarray] = []
    for item in drawing:
        if isinstance(item, (int, float)):  # raw-format pen-up marker
            strokes.append(np.zeros((0, 2)))
            continue
        if len(item) < 2:
            strokes.append(np.zeros((0, 2)))
            continue
        xs, ys = np.asarray(item[0], dtype=float), np.asarray(item[1], dtype=float)
        if xs.size == 0:
            strokes.append(np.zeros((0, 2)))
            continue
        canvas = float(QUICKDRAW["canvas_px"])
        strokes.append(np.column_stack([xs, canvas - 1.0 - ys]))
    return strokes


def _span_ledger_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "quickdraw" / "spans.json"


def resolve_quickdraw_spans(
    category: str,
    url: str,
    cache_dir: Path,
    *,
    slice_bytes: int = QD_SLICE_BYTES,
    fractions: Sequence[float] = QD_SLICE_FRACTIONS,
    offline: bool = False,
) -> tuple[list[tuple[int, int]], str, int | None]:
    """Which byte ranges of one category file are read — pinned, never ad hoc.

    The ranges used to be recomputed from a live ``Content-Length`` probe every
    build. When that probe fails (this machine runs out of ephemeral ports under
    ~100 sequential connections), the old code fell back to one long anchor read,
    i.e. it silently scanned a *different set of drawings* and still called the
    result the same dataset. That is exactly the kind of unreproducible experiment
    this repository forbids, so the ranges are now written to a ledger on the first
    successful live build and replayed by every later build, offline included.

    Returns ``(spans, source, total_bytes)``; ``source`` is recorded in the
    manifest so a reader can see which rule produced this build.
    """
    ledger_path = _span_ledger_path(cache_dir)
    ledger: dict[str, Any] = (
        json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    )
    pinned = ledger.get(category)
    if isinstance(pinned, dict) and pinned.get("spans"):
        spans = [(int(start), int(end)) for start, end in pinned["spans"]]
        total = int(pinned["total"]) if pinned.get("total") else None
        return spans, str(pinned.get("source", "ledger")) + " (replayed)", total
    if offline:
        raise RuntimeError(
            f"no pinned byte ranges for Quick Draw category {category!r} in "
            f"{ledger_path}, and an offline build cannot discover them; run one "
            "live build of this category first"
        )
    total = remote_size(url)
    if total is None:
        raise RuntimeError(
            f"cannot read the object size of {url}; refusing to guess the byte "
            "ranges, because a different range means a different set of drawings "
            "and therefore a different dataset. Retry once the local socket limit "
            "has cleared, or build with --offline once the ranges are pinned."
        )
    spans = [
        (int(fraction * total), int(fraction * total) + slice_bytes - 1)
        for fraction in fractions
    ]
    spans = [(start, min(end, total - 1)) for start, end in spans if start < total]
    ledger[category] = {"url": url, "total": total, "source": "remote", "spans": spans}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(ledger, indent=1, sort_keys=True), encoding="utf-8"
    )
    return spans, "remote", total


def quickdraw_candidates(
    category: str,
    cache_dir: Path,
    *,
    slice_bytes: int = QD_SLICE_BYTES,
    fractions: Sequence[float] = QD_SLICE_FRACTIONS,
    offline: bool = False,
) -> dict[str, Any]:
    """Read the pinned slices of one category file and split drawings three ways.

    Returns ``{"usable": [...], "rejected": [...], "assets": [...], "census": {...}}``
    where a drawing is *usable* only if it is one stroke, the project's extractor
    accepts it as a closed non-self-crossing contour, and it has enough vertices to
    be a contour rather than a three-fingered scribble.
    """
    quote = urllib.parse.quote(category)
    url = QUICKDRAW["url_template"].format(quote=quote)
    spans, span_source, total = resolve_quickdraw_spans(
        category,
        url,
        cache_dir,
        slice_bytes=slice_bytes,
        fractions=fractions,
        offline=offline,
    )
    assets: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        asset = fetch_cached(url, cache_dir / "quickdraw", byte_range=span, offline=offline)
        assets.append(
            {
                "url": asset["url"],
                "byte_range": asset["byte_range"],
                "sha256": asset["sha256"],
                "bytes": asset["bytes"],
                "cached": asset["cached"],
                "slice_index": index,
            }
        )
        for position, row in enumerate(
            _qd_rows(asset["text"], drop_partial_first=index > 0 or asset["byte_range"] is not None)
        ):
            row["_slice"] = index
            row["_line"] = position
            records.append(row)
    usable: list[dict[str, Any]] = []
    rejected: list[Rejection] = []
    census = {
        "records": len(records),
        "single_stroke": 0,
        "multi_stroke": 0,
        "too_few_points": 0,
        "open_stroke": 0,
        "self_crossing": 0,
        "degenerate": 0,
        "other_refusal": 0,
        "usable": 0,
    }
    for row in records:
        key_id = row.get("key_id") or f"slice{row['_slice']}-line{row['_line']}"
        reference = f"{category}/{key_id}"
        strokes = quickdraw_strokes(row.get("drawing", []))
        non_empty = [stroke for stroke in strokes if stroke.shape[0] > 0]
        if len(non_empty) != 1:
            census["multi_stroke"] += 1
            rejected.append(
                Rejection(
                    "quickdraw",
                    reference,
                    "multi_stroke",
                    f"{len(non_empty)} strokes; a multi-stroke sketch is not one closed "
                    "contour and closing it by hand would be inventing a shape",
                )
            )
            continue
        census["single_stroke"] += 1
        points = non_empty[0]
        if points.shape[0] < QD_MIN_STROKE_POINTS:
            census["too_few_points"] += 1
            rejected.append(
                Rejection(
                    "quickdraw", reference, "too_few_points", f"{points.shape[0]} vertices"
                )
            )
            continue
        try:
            contour = standardize(points)
        except SketchError as error:
            message = str(error)
            if "open stroke" in message:
                reason = "open_stroke"
            elif "self-intersecting" in message:
                reason = "self_crossing"
            elif "degenerate" in message or "too short" in message:
                reason = "degenerate"
            else:
                reason = "other_refusal"
            census[reason] += 1
            rejected.append(Rejection("quickdraw", reference, reason, message[:220]))
            continue
        except (ValueError, IndexError):
            census["other_refusal"] += 1
            rejected.append(
                Rejection("quickdraw", reference, "other_refusal", "point array rejected")
            )
            continue
        census["usable"] += 1
        usable.append(
            {
                "category": category,
                "key_id": str(row.get("key_id")),
                "countrycode": str(row.get("countrycode")),
                "timestamp": str(row.get("timestamp")),
                "recognized": bool(row.get("recognized", False)),
                "n_strokes": len(non_empty),
                "n_points": int(points.shape[0]),
                "slice_index": row["_slice"],
                "line_in_slice": row["_line"],
                "points": points,
                "contour": contour,
            }
        )
    return {
        "usable": usable,
        "rejected": rejected,
        "assets": assets,
        "census": census,
        "url": url,
        "spans": [list(span) for span in spans],
        "span_source": span_source,
        "object_bytes": total,
    }


def even_spaced(indices: Sequence[int], count: int) -> list[int]:
    """``count`` positions spread over ``indices``, deterministic, no repeats."""
    if count <= 0 or not indices:
        return []
    positions = np.linspace(0, len(indices) - 1, min(count, len(indices)))
    return [int(indices[int(round(value))]) for value in positions]


# --- synthetic generators ------------------------------------------------------


def _normalize(points: np.ndarray) -> np.ndarray:
    return np.asarray(
        contour_from_points(points, closed=True, n_points=N_SAMPLES, smoothing="none").points
    )


def _ring(polygon: Any) -> np.ndarray:
    """Exterior ring of a shapely geometry, multi-part reduced to the largest part."""
    if polygon.is_empty:
        raise ValueError("boolean operation produced an empty geometry")
    if polygon.geom_type == "GeometryCollection":
        parts = [part for part in polygon.geoms if part.area > 0.0]
        if not parts:
            raise ValueError("boolean operation produced no area-bearing part")
        polygon = max(parts, key=lambda part: part.area)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda part: part.area)
    if polygon.geom_type != "Polygon":
        raise ValueError(f"expected a polygon outline, got {polygon.geom_type}")
    return np.asarray(polygon.exterior.coords, dtype=float)


def circle(radius: float = 1.0, *, samples: int = 512) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    return np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])


def densify(ring: np.ndarray, *, per_edge: int = 48) -> np.ndarray:
    """Subdivide every edge of a closed corner polygon, keeping the corners exact.

    A five- or six-vertex polygon is a legitimate *region* but an illegal *trace*
    for the extractor: its wrap chord is a whole edge (10–20 % of the perimeter),
    which is exactly what the open-stroke test is there to refuse. Densifying the
    boundary describes the same polygon as a traced outline would arrive from a
    digitiser, so the extractor sees a sampling seam instead of a hole. The
    corners stay vertices, so arc-length resampling does not round them.
    """
    count = ring.shape[0]
    if count < 3:
        raise ValueError("a corner polygon needs at least three vertices")
    out: list[np.ndarray] = []
    for index in range(count):
        start = ring[index]
        end = ring[(index + 1) % count]
        share = np.linspace(0.0, 1.0, per_edge, endpoint=False)[:, None]
        out.append(start[None, :] + share * (end - start)[None, :])
    return np.vstack(out)


def ellipse(a: float, b: float, *, samples: int = 512, tilt_deg: float = 0.0) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    points = np.column_stack([a * np.cos(angle), b * np.sin(angle)])
    theta = float(np.deg2rad(tilt_deg))
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ rotation.T, dtype=float)


def regular_polygon(
    sides: int, radius: float = 1.0, *, phase_deg: float = 90.0, per_edge: int = 48
) -> np.ndarray:
    angle = np.deg2rad(phase_deg) + np.arange(sides) * (2.0 * np.pi / sides)
    return densify(
        np.column_stack([radius * np.cos(angle), radius * np.sin(angle)]), per_edge=per_edge
    )


def star(
    points_count: int = 5,
    inner_ratio: float = 0.42,
    *,
    tip_round: float = 0.0,
    valley_round: float = 0.0,
    arm_jitter: Sequence[float] | None = None,
    phase_deg: float = 90.0,
    per_edge: int = 24,
) -> np.ndarray:
    """A many-pointed star outline, with optional corner rounding.

    ``tip_round`` / ``valley_round`` are fractions of the mean edge length; the
    corner is cut by an arc approximated with a quadratic bevel, which is how a
    real worn or hand-drawn star differs from the ideal one — same number of
    arms, same envelope, softer vertices. ``arm_jitter`` makes the arms
    individually different lengths, which no rigid transform can produce.
    """
    outer = np.ones(points_count, dtype=float)
    if arm_jitter is not None:
        if len(arm_jitter) != points_count:
            raise ValueError(f"arm_jitter needs {points_count} values")
        outer = 1.0 + np.asarray(arm_jitter, dtype=float)
    angle = np.deg2rad(phase_deg) + np.arange(2 * points_count) * (np.pi / points_count)
    radius = np.empty(2 * points_count, dtype=float)
    radius[0::2] = outer
    radius[1::2] = inner_ratio * outer
    ring = np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])
    if tip_round or valley_round:
        ring = _bevel(ring, even=tip_round, odd=valley_round)
    return densify(ring, per_edge=per_edge)


def _bevel(ring: np.ndarray, *, even: float, odd: float) -> np.ndarray:
    """Replace alternating vertices by a short quadratic bevel, keeping order."""
    out: list[np.ndarray] = []
    count = ring.shape[0]
    edge = np.roll(ring, -1, axis=0) - ring
    length = np.linalg.norm(edge, axis=1)
    for index in range(count):
        share = even if index % 2 == 0 else odd
        if share <= 0.0:
            out.append(ring[index])
            continue
        previous = ring[index] - ring[index - 1]
        previous_length = float(np.linalg.norm(previous))
        step_in = previous / max(previous_length, 1e-12) * min(share, 0.45) * min(
            previous_length, length[index - 1]
        )
        step_out = edge[index] / max(length[index], 1e-12) * min(share, 0.45) * min(
            length[index], previous_length
        )
        a = ring[index] + step_in
        b = ring[index] + step_out
        out.append(a)
        t = np.linspace(0.0, 1.0, 7)[1:-1]
        for value in t:
            out.append((1 - value) ** 2 * a + 2 * (1 - value) * value * ring[index] + value**2 * b)
        out.append(b)
    return np.asarray(out, dtype=float)


def crescent(
    outer_radius: float = 1.0,
    inner_radius: float = 0.82,
    offset: float = 0.5,
    *,
    offset_angle_deg: float = 0.0,
    taper: float = 0.0,
    samples: int = 512,
) -> np.ndarray:
    """Disk minus disk (optionally a shrinking disk), which is what a crescent is.

    ``taper`` shrinks the cutting disk as it sweeps, producing the asymmetric
    waxing crescent rather than the symmetric lens-minus-circle. Boolean
    geometry, not a hand-written arc pair: the P0-4 note that "hand-assembling
    them is how a fake crescent gets into a test set" is the reason this stays a
    shapely difference.
    """
    from shapely.geometry import Point

    outer = Point(0.0, 0.0).buffer(outer_radius, quad_segs=max(8, samples // 8))
    if taper == 0.0:
        cutter = Point(offset, 0.0).buffer(inner_radius, quad_segs=max(8, samples // 8))
    else:
        sweeps = []
        for value in np.linspace(0.0, 1.0, 60):
            centre = np.array([offset * value, 0.0])
            radius = inner_radius * (1.0 - taper * value)
            sweeps.append(
                Point(float(centre[0]), float(centre[1])).buffer(
                    radius, quad_segs=max(4, samples // 24)
                )
            )
        cutter = unary_union_all(sweeps)
    from shapely.affinity import rotate

    result = outer.difference(rotate(cutter, angle=offset_angle_deg, origin=(0, 0)))
    return _ring(result)


def unary_union_all(parts: Sequence[Any]) -> Any:
    from shapely.ops import unary_union

    return unary_union(list(parts))


def heart(
    *,
    lobe: float = 1.0,
    cleft: float = 1.0,
    tip_pull: float = 1.0,
    samples: int = 512,
) -> np.ndarray:
    """The classic heart curve with three shape knobs, none of them a rigid move.

    ``lobe`` scales the horizontal spread, ``cleft`` deepens the notch between the
    lobes (the top harmonics), ``tip_pull`` stretches the point at the bottom.
    """
    t = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    x = 16.0 * np.sin(t) ** 3 * lobe
    y = (
        13.0 * np.cos(t)
        - 5.0 * cleft * np.cos(2 * t)
        - 2.0 * np.cos(3 * t)
        - np.cos(4 * t) * tip_pull
    )
    return np.column_stack([x, y])


def radial(
    coefficients: Mapping[int, tuple[float, float]],
    *,
    base: float = 1.0,
    y_scale: float = 1.0,
    samples: int = 512,
) -> np.ndarray:
    """``r(t) = base + sum_k a_k cos(k t) + b_k sin(k t)``, in polar form.

    The workhorse for lobed, forked, banded and irregular outlines: harmonic
    amplitudes are the knobs, so "three shallow lobes" and "three sharp prongs"
    are two rows of the same generator rather than two different programs.
    """
    t = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    radius = np.full(samples, base, dtype=float)
    for order, (cosine, sine) in coefficients.items():
        radius += float(cosine) * np.cos(order * t) + float(sine) * np.sin(order * t)
    radius = np.maximum(radius, 1e-3)
    return np.column_stack([radius * np.cos(t), radius * np.sin(t) * y_scale])


def power_lobes(
    count: int,
    amplitude: float,
    *,
    sharpness: float = 2.0,
    phase_deg: float = 0.0,
    base: float = 1.0,
    samples: int = 600,
) -> np.ndarray:
    """``r = base + amplitude * |cos(count*t/2 + phase)|^sharpness``.

    ``sharpness = 2`` is a smooth bulge, ``sharpness < 1`` gives a pinched cusp
    with a large turning angle at the cusp — the difference between a five-lobed
    blob and a five-pointed star, at the same area and the same envelope. That is
    the controlled contrast the hard negatives exist to create.
    """
    t = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    shape = np.abs(np.cos(0.5 * count * t + np.deg2rad(phase_deg))) ** sharpness
    radius = base + amplitude * shape
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


def rounded_bar(
    length: float = 2.0,
    thickness: float = 0.5,
    *,
    bend: float = 0.0,
    samples: int = 48,
) -> np.ndarray:
    """A stroked path turned into an outline: the band / S / lightning family.

    ``bend`` is the turn of the centre line in radians; 0 is a straight capsule
    (the negative a curved band must not be confused with), ~pi is an S.
    """
    from shapely.geometry import LineString

    t = np.linspace(-1.0, 1.0, samples)
    half = 0.5 * bend
    if abs(half) < 1e-6:
        centre = np.column_stack([length * t, np.zeros_like(t)])
    else:
        radius = length / (2.0 * half)
        angle = half * t
        centre = np.column_stack([radius * np.sin(angle), radius * (np.cos(angle) - 1.0)])
    path = LineString(centre)
    polygon = path.buffer(
        thickness / 2.0, quad_segs=16, cap_style=1, join_style=1
    )
    return _ring(polygon)


def semicircle(radius: float = 1.0, *, samples: int = 256) -> np.ndarray:
    """A half-disk as a *traceable* ring: the arc and the diameter are both drawn.

    Closing the arc with the extractor's wrap chord is not an option — the chord
    is the diameter, two shape scales long, and the open-stroke test is meant to
    refuse exactly that. So the straight edge is part of the outline here.
    """
    angle = np.linspace(0.0, np.pi, samples, endpoint=False)
    arc = np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])
    back = np.column_stack(
        [
            np.linspace(-radius, radius, samples, endpoint=False),
            np.zeros(samples, dtype=float),
        ]
    )
    return np.vstack([arc, back])


def vesica(radius: float = 1.0, offset: float = 0.5, *, samples: int = 400) -> np.ndarray:
    from shapely.geometry import Point

    left = Point(-offset, 0.0).buffer(radius, quad_segs=max(8, samples // 8))
    right = Point(offset, 0.0).buffer(radius, quad_segs=max(8, samples // 8))
    return _ring(left.intersection(right))


def teardrop(*, samples: int = 400, pointiness: float = 1.0) -> np.ndarray:
    from shapely.geometry import LineString, Point

    path = LineString([(0.0, -1.2 * pointiness), (0.0, 0.2)])
    top = Point(0.0, 0.45).buffer(0.75, quad_segs=48)
    return _ring(path.buffer(0.55, quad_segs=24, cap_style=1).union(top))


def irregular(
    seed: int,
    *,
    harmonics: Sequence[int] = (2, 3, 4, 5, 6, 7, 8),
    total_amplitude: float = 0.5,
    base_wobble: float = 0.3,
    phase: float = 0.4,
    samples: int = 480,
) -> np.ndarray:
    """A natural-looking closed curve: random harmonic amplitudes over a fixed budget.

    The seed is part of the dataset record; two calls with different seeds are
    different shapes, not transforms of one another, which is what makes them
    usable as independent variants of a family.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    orders = np.asarray(list(harmonics), dtype=float)[:, None]
    basis = np.concatenate([np.cos(orders * t[None, :]), np.sin(orders * t[None, :])])
    coefficients = np.asarray(rng.uniform(0.05, 0.30, basis.shape[0]), dtype=float)
    coefficients = coefficients * (total_amplitude / coefficients.sum())
    radius = 1.0 + base_wobble * np.cos(t + phase) + coefficients @ basis
    return np.column_stack([radius * np.cos(t), radius * np.sin(t)])


def blob_matching_solidity(
    target_solidity: float,
    *,
    count: int = 5,
    sharpness: float = 2.0,
    phase_deg: float = 90.0,
    tolerance: float = 0.004,
    iterations: int = 40,
) -> tuple[np.ndarray, float, dict[str, float]]:
    """A lobed blob whose *solidity* equals the target's, by bisection on amplitude.

    This is how the matched-envelope negative is built rather than asserted: the
    only free parameter is the lobe amplitude, solidity is monotone in it, and the
    search reports the achieved value so the manifest can show the residual.
    """
    low, high = 0.0, 1.5
    achieved = 1.0
    ring = power_lobes(count, 0.0, sharpness=sharpness, phase_deg=phase_deg)
    for _ in range(iterations):
        middle = 0.5 * (low + high)
        ring = power_lobes(count, middle, sharpness=sharpness, phase_deg=phase_deg)
        contour = _normalize(ring)
        achieved = float(shape_metrics(contour).solidity)
        if abs(achieved - target_solidity) <= tolerance:
            break
        if achieved > target_solidity:
            low = middle
        else:
            high = middle
    return ring, achieved, {"target_solidity": float(target_solidity), "achieved": achieved}


# --- family specifications ------------------------------------------------------

Generator = Callable[[], np.ndarray]


@dataclass(frozen=True)
class SyntheticSpec:
    item_id: str
    family: str
    role: str
    generator: str
    parameters: dict[str, Any]
    note: str = ""


def _spec(specs: list[SyntheticSpec], item_id: str, family: str, role: str, generator: str,
          parameters: Mapping[str, Any], note: str = "") -> None:
    specs.append(
        SyntheticSpec(item_id, family, role, generator, dict(parameters), note)
    )


def synthetic_specs() -> list[SyntheticSpec]:
    """The parametric members of the gallery, with every knob written down.

    Roles are decided *structurally*, before any algorithm runs: a member of the
    queried family that keeps its defining structure (a star's corner pattern, a
    crescent's two cusps) is a variant; a member that loses that structure while
    keeping the envelope is a hard negative. The star family is the worked
    example of the whole point of this dataset: the circle, the pentagon, the
    four-point star and the matched-solidity five-lobed blob are all negatives,
    and the last one is the one an area-based matcher cannot separate.
    """
    specs: list[SyntheticSpec] = []
    # --- star ---
    _spec(specs, "syn-star-query", "star", "query", "star",
          {"points_count": 5, "inner_ratio": 0.42},
          "the P0-4 star10 proportions with five arms instead of ten")
    _spec(specs, "syn-star-inner-030", "star", "variant", "star",
          {"points_count": 5, "inner_ratio": 0.30}, "sharper arms, same arm count")
    _spec(specs, "syn-star-inner-055", "star", "variant", "star",
          {"points_count": 5, "inner_ratio": 0.55}, "stubby arms, still five corners")
    _spec(specs, "syn-star-rounded", "star", "variant", "star",
          {"points_count": 5, "inner_ratio": 0.42, "tip_round": 0.18, "valley_round": 0.12},
          "worn star: tips and valleys beveled")
    _spec(specs, "syn-star-jitter", "star", "variant", "star",
          {"points_count": 5, "inner_ratio": 0.42,
           "arm_jitter": [0.14, -0.09, 0.21, 0.03, -0.16], "phase_deg": 82.0},
          "uneven arm lengths, i.e. not a regular polygon construction")
    _spec(specs, "syn-star-soft", "star", "variant", "star",
          {"points_count": 5, "inner_ratio": 0.48, "tip_round": 0.08}, "lightly rounded")
    _spec(specs, "syn-circle", "star", "hard_negative", "circle", {"radius": 1.0},
          "no corners at all; the shape a purely envelope-based matcher loves")
    _spec(specs, "syn-pentagon", "star", "hard_negative", "regular_polygon",
          {"sides": 5}, "five sides, five convex corners, no concave valleys")
    _spec(specs, "syn-hexagon", "star", "hard_negative", "regular_polygon", {"sides": 6},
          "six sides, convex")
    _spec(specs, "syn-star-4", "star", "hard_negative", "star",
          {"points_count": 4, "inner_ratio": 0.40}, "four arms, not five")
    _spec(specs, "syn-star-6", "star", "hard_negative", "star",
          {"points_count": 6, "inner_ratio": 0.45}, "six arms, not five")
    _spec(specs, "syn-star-lobed-5", "star", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-star-query", "count": 5, "sharpness": 2.0},
          "five smooth lobes matched to the query's solidity: same area class and "
          "same lobe count, no sharp vertex anywhere")
    _spec(specs, "syn-star-lobed-5-cusp", "star", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-star-query", "count": 5, "sharpness": 0.55},
          "five pinched lobes: cusps exist but the convex/concave sequence differs")
    # --- crescent ---
    _spec(specs, "syn-crescent-query", "crescent", "query", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.82, "offset": 0.5},
          "the P0-4 crescent archetype (disk minus disk)")
    _spec(specs, "syn-crescent-thin", "crescent", "variant", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.92, "offset": 0.35}, "narrower horn")
    _spec(specs, "syn-crescent-fat", "crescent", "variant", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.70, "offset": 0.62}, "wider opening")
    _spec(specs, "syn-crescent-turn", "crescent", "variant", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.80, "offset": 0.45,
           "offset_angle_deg": 35.0}, "asymmetric waxing crescent")
    _spec(specs, "syn-crescent-taper", "crescent", "variant", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.86, "offset": 0.62, "taper": 0.45},
          "tapered cutter: horns of unequal width")
    _spec(specs, "syn-crescent-deformed", "crescent", "variant", "crescent",
          {"outer_radius": 1.0, "inner_radius": 0.78, "offset": 0.5, "samples": 300},
          "coarser boolean sampling, i.e. a boundary perturbation, not a rigid move")
    _spec(specs, "syn-ellipse-e1", "crescent", "hard_negative", "ellipse",
          {"a": 1.0, "b": 0.62}, "convex, no cusp")
    _spec(specs, "syn-circle-crescent", "crescent", "hard_negative", "circle",
          {"radius": 1.0}, "same outer arc, no inner arc")
    _spec(specs, "syn-semicircle", "crescent", "hard_negative", "semicircle",
          {"radius": 1.0}, "one flat chord instead of a second arc")
    _spec(specs, "syn-vesica", "crescent", "hard_negative", "vesica",
          {"radius": 1.0, "offset": 0.42}, "two arcs, but pointed at both ends symmetrically")
    _spec(specs, "syn-crescent-lobed", "crescent", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-crescent-query", "count": 2, "sharpness": 1.0},
          "two-lobe peanut aimed at the crescent's solidity. The bisection lands at "
          "0.95 against a target of 0.56: a radial lobe shape is star-shaped about "
          "its centre and cannot hollow out that far (a straight-notched disk was "
          "tried too and floors at 0.826, because its own convex hull shrinks with "
          "the notch), so this row is a *partial* envelope match and the residual is "
          "recorded rather than smoothed over")
    # --- heart ---
    _spec(specs, "syn-heart-query", "heart", "query", "heart",
          {"lobe": 1.0, "cleft": 1.0, "tip_pull": 1.0}, "classic heart curve")
    _spec(specs, "syn-heart-wide", "heart", "variant", "heart",
          {"lobe": 1.18, "cleft": 1.0, "tip_pull": 1.0}, "wider lobes")
    _spec(specs, "syn-heart-shallow", "heart", "variant", "heart",
          {"lobe": 1.0, "cleft": 0.72, "tip_pull": 1.0}, "shallow cleft")
    _spec(specs, "syn-heart-deep", "heart", "variant", "heart",
          {"lobe": 0.94, "cleft": 1.28, "tip_pull": 1.0}, "deep cleft, higher concavity")
    _spec(specs, "syn-heart-point", "heart", "variant", "heart",
          {"lobe": 1.0, "cleft": 1.0, "tip_pull": 1.45}, "longer point")
    _spec(specs, "syn-heart-round", "heart", "variant", "heart",
          {"lobe": 1.06, "cleft": 0.86, "tip_pull": 0.82}, "short, round heart")
    _spec(specs, "syn-teardrop", "heart", "hard_negative", "teardrop",
          {"pointiness": 1.1}, "one point, no cleft: the convex half of a heart")
    _spec(specs, "syn-egg", "heart", "hard_negative", "ellipse",
          {"a": 0.72, "b": 1.0, "tilt_deg": 0.0}, "asymmetric-ish convex blob")
    _spec(specs, "syn-circle-heart", "heart", "hard_negative", "circle", {"radius": 1.0})
    _spec(specs, "syn-heart-lobed-2", "heart", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-heart-query", "count": 2, "sharpness": 1.6},
          "two smooth lobes, heart's solidity, no notch between them")
    _spec(specs, "syn-spade-ish", "heart", "variant", "heart",
          {"lobe": 1.24, "cleft": 0.30, "tip_pull": 1.6},
          "wide, shallow-clefted and long-pointed: still two lobes, a cleft and a "
          "point, which is what the family is (first written as a negative; the "
          "decoded contour disagrees)")
    _spec(specs, "syn-heart-lobed-3", "heart", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-heart-query", "count": 3, "sharpness": 1.4},
          "three smooth lobes, heart's solidity: lobe-like but neither the count nor "
          "the notch of a heart")
    _spec(specs, "syn-heart-lobed-4", "heart", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-heart-query", "count": 4, "sharpness": 2.6},
          "four shallow lobes at the heart's solidity: more bulges than a heart, "
          "no cleft between two of them and no point below")
    _spec(specs, "syn-heart-bean", "heart", "variant", "heart",
          {"lobe": 1.05, "cleft": 1.05, "tip_pull": 0.22},
          "short point, deep cleft: the cleft and the two lobes survive, so the "
          "structure is kept (written as a kidney-shaped negative; the decoded "
          "contour is a heart with a stubby tip, which is a variant)")
    _spec(specs, "syn-heart-kite", "heart", "hard_negative", "regular_polygon",
          {"sides": 4, "phase_deg": 0.0},
          "a diamond: pointed at the bottom like a heart, four sharp corners and no "
          "lobe or cleft anywhere")
    _spec(specs, "syn-heart-bumps-6", "heart", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-heart-query", "count": 6, "sharpness": 3.2},
          "six very shallow bumps at the heart's solidity: many slight concavities, "
          "none of them a cleft")
    # --- forked ---
    _spec(specs, "syn-forked-query", "forked", "query", "radial",
          {"coefficients": {"3": (0.62, 0.0), "7": (0.0, 0.18)}, "y_scale": 0.82},
          "the P0-4 forked archetype: three lobes plus a fine modulation")
    _spec(specs, "syn-forked-sharp", "forked", "variant", "power_lobes",
          {"count": 3, "amplitude": 1.2, "sharpness": 0.7, "base": 0.2, "phase_deg": 90.0},
          "three prongs with pinched valleys: the valley floor is the generator's "
          "base radius, so amplitude alone only bulges the lobes — an earlier "
          "base=1.0 version of this row was a blunt trefoil (deficiency 0.028) and "
          "the structural audit demoted it, which is the parameter bug this fixes")

    _spec(specs, "syn-forked-uneven", "forked", "variant", "radial",
          {"coefficients": {"3": (0.55, 0.12), "5": (0.14, 0.0)}, "y_scale": 0.9},
          "three lobes broken by a fifth harmonic: unequal tines")
    _spec(specs, "syn-forked-four", "forked", "variant", "radial",
          {"coefficients": {"4": (0.5, 0.0), "8": (0.1, 0.05)}, "y_scale": 0.9},
          "same construction, four lobes (still a fork, a different count)")
    _spec(specs, "syn-forked-bar", "forked", "hard_negative", "rounded_bar",
          {"length": 1.1, "thickness": 0.55, "bend": 0.0},
          "a stubby capsule: the same stroke as a single tine but no split anywhere, "
          "so it loses the structure that defines the family (first called a weak "
          "variant; the honest label is negative)")
    _spec(specs, "syn-two-prong", "forked", "hard_negative", "power_lobes",
          {"count": 2, "amplitude": 0.6, "sharpness": 0.7, "phase_deg": 0.0},
          "two prongs, not three")
    _spec(specs, "syn-dumbbell", "forked", "hard_negative", "radial",
          {"coefficients": {"2": (0.55, 0.0)}, "y_scale": 0.7}, "a bar, no split")
    _spec(specs, "syn-forked-lobed6", "forked", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-forked-query", "count": 6, "sharpness": 2.2},
          "six shallow bumps at the query's solidity: many lobes, wrong structure")
    _spec(specs, "syn-circle-forked", "forked", "hard_negative", "circle", {"radius": 1.0})
    # --- band (S / lightning) ---
    _spec(specs, "syn-band-query", "band", "query", "rounded_bar",
          {"length": 2.2, "thickness": 0.6, "bend": np.pi},
          "the P0-4 S: a stroked path, twice bent")
    _spec(specs, "syn-band-loose", "band", "variant", "rounded_bar",
          {"length": 2.2, "thickness": 0.72, "bend": 2.2}, "less turn, thicker band")
    _spec(specs, "syn-band-tight", "band", "variant", "rounded_bar",
          {"length": 2.0, "thickness": 0.42, "bend": 3.6}, "tighter, thinner S")
    _spec(specs, "syn-band-zigzag", "band", "hard_negative", "radial",
          {"coefficients": {"1": (0.0, 0.0), "2": (0.55, 0.0), "4": (0.18, 0.0)},
           "y_scale": 0.55},
          "measured, not assumed: this flattens to a convex elongated body (solidity "
          "0.97, no concave vertex), so it has lost the two turns that define the "
          "family and cannot be a variant of it")
    _spec(specs, "syn-bar-straight", "band", "hard_negative", "rounded_bar",
          {"length": 2.3, "thickness": 0.6, "bend": 0.0},
          "same length and thickness, zero turns: the band's own negative control")
    _spec(specs, "syn-ellipse-band", "band", "hard_negative", "ellipse",
          {"a": 1.1, "b": 0.42}, "elongated but convex")
    _spec(specs, "syn-circle-band", "band", "hard_negative", "circle", {"radius": 1.0})
    _spec(specs, "syn-band-single-turn", "band", "hard_negative", "rounded_bar",
          {"length": 2.2, "thickness": 0.6, "bend": 1.5}, "one turn (a C), not two")
    # --- blob (smooth control family) ---
    _spec(specs, "syn-blob-query", "blob", "query", "ellipse",
          {"a": 1.0, "b": 0.72, "tilt_deg": 18.0}, "an ordinary rounded water body")
    _spec(specs, "syn-blob-circle", "blob", "variant", "circle", {"radius": 1.0},
          "the limit case of the family")
    _spec(specs, "syn-blob-e2", "blob", "variant", "ellipse",
          {"a": 1.0, "b": 0.86, "tilt_deg": -12.0}, "mildly eccentric")
    _spec(specs, "syn-blob-smooth", "blob", "variant", "irregular",
          {"seed": 11, "harmonics": [2, 3], "total_amplitude": 0.16, "base_wobble": 0.12},
          "smooth low-order perturbation of an ellipse")
    _spec(specs, "syn-blob-square", "blob", "hard_negative", "regular_polygon",
          {"sides": 4, "phase_deg": 45.0}, "four sharp corners")
    _spec(specs, "syn-blob-triangle", "blob", "hard_negative", "regular_polygon",
          {"sides": 3}, "three sharp corners")
    _spec(specs, "syn-blob-star5", "blob", "hard_negative", "star",
          {"points_count": 5, "inner_ratio": 0.34}, "a star: strong corner structure")
    # --- irregular natural ---
    _spec(specs, "syn-irregular-query", "irregular", "query", "irregular",
          {"seed": 7, "total_amplitude": 0.5, "base_wobble": 0.3},
          "the P0-4 irregular archetype (its own fixed seed 7 reproduced through this API)")
    _spec(specs, "syn-irregular-a", "irregular", "variant", "irregular",
          {"seed": 21, "total_amplitude": 0.42, "base_wobble": 0.28})
    _spec(specs, "syn-irregular-b", "irregular", "variant", "irregular",
          {"seed": 34, "total_amplitude": 0.55, "base_wobble": 0.24})
    _spec(specs, "syn-irregular-c", "irregular", "variant", "irregular",
          {"seed": 55, "total_amplitude": 0.35, "base_wobble": 0.34})
    _spec(specs, "syn-irregular-silted", "irregular", "variant", "irregular",
          {"seed": 7, "total_amplitude": 0.28, "base_wobble": 0.3,
           "harmonics": [2, 3, 4, 5]}, "the query with the finest harmonics removed")
    _spec(specs, "syn-irregular-circle", "irregular", "hard_negative", "circle",
          {"radius": 1.0})
    _spec(specs, "syn-irregular-ellipse", "irregular", "hard_negative", "ellipse",
          {"a": 1.0, "b": 0.7})
    _spec(specs, "syn-irregular-bumps", "irregular", "hard_negative", "blob_matching_solidity",
          {"target": "query:syn-irregular-query", "count": 9, "sharpness": 2.4},
          "many small smooth bumps at the query's solidity: high wiggle count, "
          "wrong structure")
    _spec(specs, "syn-irregular-bumps-11", "irregular", "hard_negative",
          "blob_matching_solidity",
          {"target": "query:syn-irregular-query", "count": 11, "sharpness": 1.1},
          "eleven *periodic* cusped bumps at the query's solidity: the query wiggles "
          "aperiodically, so a matcher that counts oscillations is fooled but a "
          "matcher that compares their spacing is not")
    _spec(specs, "syn-irregular-bar", "irregular", "hard_negative", "rounded_bar",
          {"length": 2.4, "thickness": 0.55, "bend": 0.0},
          "elongated convex bar: comparable perimeter, an aspect ratio the query does not have")
    return specs


_GENERATORS: dict[str, Callable[..., np.ndarray]] = {
    "circle": circle,
    "ellipse": ellipse,
    "regular_polygon": regular_polygon,
    "star": star,
    "crescent": crescent,
    "heart": heart,
    "radial": radial,
    "power_lobes": power_lobes,
    "rounded_bar": rounded_bar,
    "semicircle": semicircle,
    "vesica": vesica,
    "teardrop": teardrop,
    "irregular": irregular,
}


def _generate(
    spec: SyntheticSpec, resolved: Mapping[str, np.ndarray]
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run one synthetic spec, resolving ``target:query:<id>`` references lazily."""
    parameters = dict(spec.parameters)
    extra: dict[str, Any] = {}
    if spec.generator == "blob_matching_solidity":
        target = str(parameters.pop("target"))
        if not target.startswith("query:"):
            raise ValueError(f"solidity target must be 'query:<id>', got {target!r}")
        query_id = target.split(":", 1)[1]
        if query_id not in resolved:
            raise ValueError(f"target query {query_id!r} is not built yet")
        wanted = float(shape_metrics(resolved[query_id]).solidity)
        ring, achieved, extra = blob_matching_solidity(wanted, **parameters)
        parameters["target_solidity"] = round(wanted, 6)
        parameters["achieved_solidity"] = round(achieved, 6)
    else:
        if spec.generator == "radial":
            coefficients = {
                int(key): tuple(float(value) for value in values)
                for key, values in parameters["coefficients"].items()
            }
            parameters = {**parameters, "coefficients": coefficients}
        ring = _GENERATORS[spec.generator](**parameters)
    return ring, extra


# --- asset plans ---------------------------------------------------------------


@dataclass(frozen=True)
class PhosphorPlan:
    item_id: str
    family: str
    role: str
    name: str
    weight: str
    note: str = ""


@dataclass(frozen=True)
class QuickDrawPlan:
    family: str
    category: str
    role: str
    count: int
    id_prefix: str
    note: str = ""


def phosphor_plan() -> list[PhosphorPlan]:
    """Real designed silhouettes: two queries per family where the asset allows it."""
    return [
        # star
        PhosphorPlan("ph-star-fill", "star", "query", "star", "fill",
                     "designed five-point star, filled"),
        PhosphorPlan("ph-seal-fill", "star", "hard_negative", "seal", "fill",
                     "many-point star burst: corners exist, but a different count"),
        PhosphorPlan("ph-star4-fill", "star", "hard_negative", "star-four", "fill",
                     "four-point star"),
        PhosphorPlan("ph-snowflake-fill", "star", "hard_negative", "snowflake", "fill",
                     "six-fold, deeply branched — kept in the plan although it is "
                     "drawn as negative space inside a panel, so the build refuses it "
                     "as frame_silhouette and the refusal is counted, not hidden"),
        PhosphorPlan("ph-polygon-fill", "star", "hard_negative", "polygon", "fill",
                     "Phosphor's own 'polygon' glyph. Decoded it is a rounded, mildly "
                     "concave five-sided body (solidity 0.78) whose interior hole the "
                     "exterior-only policy drops — a polygon-like negative, and "
                     "explicitly not the convex pentagon this plan first claimed"),
        # crescent
        PhosphorPlan("ph-moon-fill", "crescent", "query", "moon", "fill", "designed crescent"),
        PhosphorPlan("ph-moon-stars-fill", "crescent", "variant", "moon-stars", "fill",
                     "crescent plus stars: multiple pieces expected, likely refused"),
        PhosphorPlan("ph-star-crescent-fill", "crescent", "hard_negative",
                     "star-and-crescent", "fill", "crescent plus a star"),
        PhosphorPlan("ph-circle-half-fill", "crescent", "hard_negative", "circle-half", "fill",
                     "a disk cut in half: one straight edge, no horn"),
        PhosphorPlan("ph-drop-fill", "crescent", "hard_negative", "drop", "fill",
                     "teardrop: one cusp, not two"),
        # heart
        PhosphorPlan("ph-heart-fill", "heart", "query", "heart", "fill", "designed heart"),
        PhosphorPlan("ph-heart-half-fill", "heart", "variant", "heart-half", "fill",
                     "half a heart: same local structure, different region"),
        PhosphorPlan("ph-heart-break-fill", "heart", "variant", "heart-break", "fill",
                     "a heart whose crack is carved into the exterior ring, so the "
                     "silhouette is still a heart: labelled variant, not negative"),
        PhosphorPlan("ph-spade-fill", "heart", "hard_negative", "spade", "fill",
                     "inverted lobes and a stem"),
        PhosphorPlan("ph-leaf-fill", "heart", "hard_negative", "leaf", "fill",
                     "two cusps, but point-up and convex-backed"),
        # forked
        PhosphorPlan("ph-tree-structure-fill", "forked", "query", "tree-structure", "fill",
                     "branching structure"),
        PhosphorPlan("ph-clover-fill", "forked", "variant", "clover", "fill",
                     "three lobes with a stem"),
        PhosphorPlan("ph-club-fill", "forked", "variant", "club", "fill", "three lobes plus stem"),
        PhosphorPlan("ph-brackets-curly-fill", "forked", "hard_negative", "brackets-curly",
                     "fill", "two mirrored curves — also a negative-space panel, so it "
                     "is refused as frame_silhouette"),
        PhosphorPlan("ph-bone-fill", "forked", "hard_negative", "bone", "fill",
                     "two bulges at the ends"),
        PhosphorPlan("ph-scissors-fill", "forked", "hard_negative", "scissors", "fill",
                     "crossed loops"),
        # band
        PhosphorPlan("ph-lightning-fill", "band", "query", "lightning", "fill",
                     "a zigzag band"),
        PhosphorPlan("ph-lightning-a-fill", "band", "variant", "lightning-a", "fill",
                     "a second zigzag design"),
        PhosphorPlan("ph-spiral-fill", "band", "hard_negative", "spiral", "fill",
                     "coiled, not two-turn"),
        PhosphorPlan("ph-knife-fill", "band", "hard_negative", "knife", "fill",
                     "elongated and almost straight"),
        PhosphorPlan("ph-wave-sine-fill", "band", "variant", "wave-sine", "fill",
                     "a sine band — negative-space panel as well, refused as "
                     "frame_silhouette"),
        # blob
        PhosphorPlan("ph-circle-fill", "blob", "query", "circle", "fill", "a disk"),
        PhosphorPlan("ph-hexagon-fill", "blob", "hard_negative", "hexagon", "fill",
                     "six flat corners"),
        PhosphorPlan("ph-square-fill", "blob", "hard_negative", "square", "fill", "four corners"),
        PhosphorPlan("ph-triangle-fill", "blob", "hard_negative", "triangle", "fill",
                     "three corners"),
        # irregular
        PhosphorPlan("ph-cloud-fill", "irregular", "variant", "cloud", "fill",
                     "lobed natural silhouette"),
        PhosphorPlan("ph-ghost-fill", "irregular", "hard_negative", "ghost", "fill",
                     "smooth top, scalloped bottom"),
        PhosphorPlan("ph-tree-fill", "irregular", "hard_negative", "tree", "fill",
                     "blob on a trunk"),
        PhosphorPlan("ph-fire-fill", "irregular", "variant", "fire", "fill",
                     "pointed natural silhouette"),
        PhosphorPlan("ph-feather-fill", "irregular", "variant", "feather", "fill",
                     "elongated natural silhouette"),
    ]


def quickdraw_plan() -> list[QuickDrawPlan]:
    """Real human drawings, one category per structural family.

    Each family also contributes a *human* query, because the point of this
    gallery is a hand-drawn query against designed and synthesized candidates.
    Quick Draw has no heart category, so the heart family's second query stays
    Phosphor's.
    """
    return [
        QuickDrawPlan("star", "star", "query", 1, "qd-star-query",
                      "a human-drawn star, held out as a query"),
        QuickDrawPlan("star", "star", "variant", 8, "qd-star",
                      "human single-stroke stars"),
        QuickDrawPlan("star", "circle", "hard_negative", 5, "qd-circle",
                      "human circles drawn by the same population"),
        QuickDrawPlan("star", "diamond", "hard_negative", 4, "qd-diamond",
                      "human diamonds: four corners"),
        QuickDrawPlan("crescent", "moon", "query", 1, "qd-moon-query",
                      "a human-drawn moon, held out as a query"),
        QuickDrawPlan("crescent", "moon", "variant", 8, "qd-moon", "human moons"),
        QuickDrawPlan("crescent", "cloud", "hard_negative", 4, "qd-cloud",
                      "bumpy, but convex-ish"),
        QuickDrawPlan("band", "lightning", "query", 1, "qd-lightning-query",
                      "a human-drawn lightning, held out as a query"),
        QuickDrawPlan("band", "lightning", "variant", 6, "qd-lightning",
                      "human lightning zigzags"),
        QuickDrawPlan("band", "snake", "variant", 5, "qd-snake", "human snakes: long bands"),
        QuickDrawPlan("band", "circle", "hard_negative", 4, "qd-band-circle",
                      "a closed loop: same stroke thickness, no turns"),
        QuickDrawPlan("forked", "tree", "query", 1, "qd-tree-query",
                      "a human-drawn tree, held out as a query"),
        QuickDrawPlan("forked", "tree", "variant", 5, "qd-tree",
                      "human trees (mostly multi-stroke)"),
        QuickDrawPlan("forked", "palm tree", "variant", 4, "qd-palm", "human palm trees"),
        QuickDrawPlan("forked", "fork", "hard_negative", 4, "qd-fork",
                      "human forks, tine count varies"),
        QuickDrawPlan("blob", "circle", "query", 1, "qd-circle-query",
                      "a human-drawn circle, the smooth family's own query"),
        QuickDrawPlan("blob", "circle", "variant", 4, "qd-blob-circle",
                      "circles as members of the smooth family"),
        QuickDrawPlan("blob", "triangle", "hard_negative", 4, "qd-triangle", "human triangles"),
        QuickDrawPlan("irregular", "cloud", "query", 1, "qd-cloud-query",
                      "a human-drawn cloud, held out as a query"),
        QuickDrawPlan("irregular", "cloud", "variant", 5, "qd-irregular-cloud",
                      "lobed human drawings"),
        QuickDrawPlan("irregular", "leaf", "hard_negative", 4, "qd-leaf",
                      "smooth pointed outlines"),
    ]


def build_synthetic(
    rejections: list[Rejection],
) -> tuple[list[GalleryItem], dict[str, np.ndarray]]:
    """Materialise every synthetic spec, resolving solidity targets as they appear."""
    items: list[GalleryItem] = []
    resolved: dict[str, np.ndarray] = {}
    specs = synthetic_specs()
    # two passes: queries first, because the matched-envelope negatives are
    # generated against a query's solidity and need it to already exist
    for pass_role in ("query", "other"):
        for spec in specs:
            is_query = spec.role == "query"
            if (pass_role == "query") != is_query:
                continue
            try:
                ring, extra = _generate(spec, resolved)
            except (ValueError, KeyError) as error:
                rejections.append(
                    Rejection("synthetic", spec.item_id, "generator", str(error)[:200])
                )
                continue
            try:
                contour = standardize(ring)
            except (SketchError, ValueError) as error:
                rejections.append(
                    Rejection("synthetic", spec.item_id, "not_a_closed_region", str(error)[:220])
                )
                continue
            resolved[spec.item_id] = contour
            parameters = dict(spec.parameters)
            if extra:
                parameters.update(
                    {
                        "target_solidity": extra["target_solidity"],
                        "achieved_solidity": extra["achieved"],
                    }
                )
            items.append(
                GalleryItem(
                    item_id=spec.item_id,
                    family=spec.family,
                    role=spec.role,
                    origin="synthetic",
                    origin_ref=(
                        f"{spec.generator}("
                        f"{json.dumps(parameters, sort_keys=True, default=str)})"
                    ),
                    points=np.asarray(ring, dtype=float),
                    contour=contour,
                    metrics=shape_metrics(contour).as_dict(),
                    license=SYNTHETIC["license"],
                    provenance={"generator": spec.generator, "parameters": parameters},
                    note=spec.note,
                )
            )
    return items, resolved


def build_phosphor(
    cache_dir: Path,
    rejections: list[Rejection],
    *,
    offline: bool = False,
) -> list[GalleryItem]:
    items: list[GalleryItem] = []
    for plan in phosphor_plan():
        item = phosphor_item(
            plan.item_id,
            plan.family,
            plan.role,
            plan.name,
            plan.weight,
            cache_dir,
            rejections,
            note=plan.note,
            offline=offline,
        )
        if item is not None:
            items.append(item)
    return items


def build_quickdraw(
    cache_dir: Path,
    rejections: list[Rejection],
    *,
    offline: bool = False,
) -> tuple[list[GalleryItem], dict[str, Any]]:
    """Sample human drawings, then standardize each and count what was refused."""
    items: list[GalleryItem] = []
    census: dict[str, Any] = {}
    for plan in quickdraw_plan():
        bundle = quickdraw_candidates(plan.category, cache_dir, offline=offline)
        entry = census.setdefault(
            plan.category,
            {"url": bundle["url"], "assets": bundle["assets"], "census": bundle["census"],
             "byte_ranges": bundle["spans"], "span_source": bundle["span_source"],
             "object_bytes": bundle["object_bytes"], "selected": []},
        )
        usable = bundle["usable"]
        rejections.extend(bundle["rejected"])
        picks = even_spaced(list(range(len(usable))), plan.count)
        for order, position in enumerate(picks):
            drawing = usable[position]
            contour = drawing["contour"]
            metrics = shape_metrics(contour).as_dict()
            item_id = f"{plan.id_prefix}-{order + 1:02d}"
            items.append(
                GalleryItem(
                    item_id=item_id,
                    family=plan.family,
                    role=plan.role,
                    origin="quickdraw",
                    origin_ref=f"quickdraw:{plan.category}:{drawing['key_id']}",
                    points=np.asarray(drawing["points"], dtype=float),
                    contour=contour,
                    metrics=metrics,
                    license=QUICKDRAW["license"],
                    provenance={
                        "category": plan.category,
                        "key_id": drawing["key_id"],
                        "countrycode": drawing["countrycode"],
                        "timestamp": drawing["timestamp"],
                        "recognized": drawing["recognized"],
                        "n_strokes": drawing["n_strokes"],
                        "n_points": drawing["n_points"],
                        "slice_index": drawing["slice_index"],
                        "line_in_slice": drawing["line_in_slice"],
                        "url": bundle["url"],
                        "format": QUICKDRAW["format"],
                        "selection": f"even-spaced {plan.count} of {len(usable)} usable drawings "
                                     f"in {len(bundle['assets'])} x {QD_SLICE_BYTES} B slices",
                        "coordinate_transform": "256 px canvas, y flipped to y-up, then "
                                                "contour_from_points(closed=True, smoothing=none)",
                        "license": QUICKDRAW["license"],
                        "attribution": QUICKDRAW["attribution"],
                    },
                    note=plan.note,
                )
            )
            entry["selected"].append(
                {
                    "item_id": item_id,
                    "key_id": drawing["key_id"],
                    "family": plan.family,
                    "role": plan.role,
                    "countrycode": drawing["countrycode"],
                    "timestamp": drawing["timestamp"],
                    "n_points": drawing["n_points"],
                    "slice_index": drawing["slice_index"],
                }
            )
    return items, census


# --- near-duplicate control ----------------------------------------------------


def near_duplicate_pairs(
    items: Sequence[GalleryItem], *, family_only: bool = True, max_align: float = 0.02
) -> list[dict[str, Any]]:
    """Pairs of gallery members that are the same outline up to the P0-4 freedoms.

    Uses the existing closed-form alignment as a *data hygiene* test, not as a
    score: two members closer than ``max_align`` (2 % of the RMS radius) are one
    shape twice, which would let a matcher score well on T2 by memorising a
    duplicate. The report is written into the manifest so the number of near
    duplicates is a measured property of the gallery, not a claim.
    """
    from earthglyph.retrieval.alignment import align

    pairs: list[dict[str, Any]] = []
    by_id = {item.item_id: item for item in items}
    order = sorted(by_id)
    for left_index, left_id in enumerate(order):
        for right_id in order[left_index + 1:]:
            left, right = by_id[left_id], by_id[right_id]
            if family_only and left.family != right.family:
                continue
            result = align(left.contour, right.contour, allow_mirror=True)
            if result.distance <= max_align:
                pairs.append(
                    {
                        "left": left_id,
                        "right": right_id,
                        "align_distance": round(float(result.distance), 6),
                        "same_family": left.family == right.family,
                    }
                )
    return pairs


# --- labels & splits -----------------------------------------------------------


def build_labels(
    items: Sequence[GalleryItem], *, near_max_align: float = 0.02
) -> dict[str, Any]:
    """Freeze the ground truth before any algorithm is run on the gallery.

    Relevance is family membership decided at design time: for a query of family
    ``F``, the members of ``F`` that are not the query itself and not one of its
    near-duplicates are relevant, and everything else is not. Two libraries are
    pinned per query: ``t2_family`` (that family's variants plus its own hard
    negatives — the discriminative test) and ``t2_full`` (the whole gallery).
    """
    families: dict[str, dict[str, list[str]]] = {}
    for item in items:
        bucket = families.setdefault(item.family, {"query": [], "variant": [], "hard_negative": []})
        bucket[item.role].append(item.item_id)
    duplicates = near_duplicate_pairs(items, max_align=near_max_align)
    by_id = {item.item_id: item for item in items}
    near_of_query: dict[str, list[str]] = {}
    for pair in duplicates:
        for left, right in ((pair["left"], pair["right"]), (pair["right"], pair["left"])):
            if by_id[left].role == "query":
                near_of_query.setdefault(left, []).append(right)
    queries: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.role != "query":
            continue
        family = item.family
        bucket = families[family]
        excluded = sorted({item.item_id, *near_of_query.get(item.item_id, [])})
        relevant = sorted(
            [member for member in bucket["variant"] if member not in excluded],
        )
        negatives = sorted(bucket["hard_negative"])
        queries[item.item_id] = {
            "family": family,
            "relevant_variants": relevant,
            "family_hard_negatives": negatives,
            "excluded_as_query_or_near_duplicate": excluded,
            "library_t2_family": sorted([*relevant, *negatives]),
            "library_t2_full": sorted(
                [item_id for item_id in by_id if item_id not in excluded]
            ),
            "library_t1": sorted([*bucket["query"], *relevant, *negatives]),
        }
    return {
        "frozen_at_build": True,
        "rule": (
            "relevant = same family, role=variant, not the query and not a near-duplicate of "
            "the query (align distance <= 0.02 of RMS radius); non-relevant = every other "
            "member, including the family's hard negatives. Decided from the family design "
            "table before any method ran; no method result can redefine a label."
        ),
        "near_max_align": near_max_align,
        "families": families,
        "near_duplicate_pairs": duplicates,
        "queries": queries,
        "items": sorted(by_id),
    }


# --- assembly ------------------------------------------------------------------


def concave_deficiency(contour: np.ndarray) -> float:
    """How far the shape falls short of its own convex hull: ``1 - solidity``.

    This is the measured statement of "does the defining concavity still exist",
    and the alternatives were tried and rejected. The *sharpest* concave vertex
    fails because a thin crescent's inner arc meets the outer one at a shallow
    angle, so its horns are spread over many samples and peak at almost nothing.
    The *total* inward turning fails because a hand-drawn circle wobbles: jitter
    alone accumulates to about the same radians as a real crescent's bay. Area
    missing from the convex hull has neither problem — a disk scores ~0.001 and a
    crescent ~0.44, thin or fat.
    """
    from shapely.geometry import Polygon

    polygon = Polygon(np.asarray(contour, dtype=float))
    hull_area = float(polygon.convex_hull.area)
    if hull_area <= 0.0:
        return 0.0
    return float(max(0.0, 1.0 - polygon.area / hull_area))


def audit_structural_roles(
    items: Sequence[GalleryItem],
) -> tuple[list[GalleryItem], list[dict[str, Any]]]:
    """Demote every member whose contour contradicts the role the plan wrote for it.

    The roles are designed by hand, but a hand-written label is a claim about
    geometry, and two kinds of claim broke in practice:

    * a Quick Draw drawing aimed at ``moon`` that is a convex disk cannot *query*
      the crescent family — as a query it would reward exactly the circle-seeking
      behaviour this experiment exists to detect, so it becomes a hard negative;
    * a planned variant whose defining concavity has flattened away is not
      "relevant" to a crescent or forked query either.

    The variant bar is a quarter of the *mildest* surviving query of the family,
    not of the strongest: using the strongest let one hand-drawn tree's deep bay
    veto a blunt clover, which is a worn member of the family rather than a
    impostor. Nothing is deleted, and no label is softened afterwards: this runs
    before any method sees the gallery, and every revision is written into the
    manifest with the measured numbers.
    """
    measured = {item.item_id: concave_deficiency(item.contour) for item in items}
    replacements: dict[str, GalleryItem] = {}
    revisions: list[dict[str, Any]] = []

    def demote(item: GalleryItem, stage: str, threshold: float, detail: str) -> None:
        revisions.append(
            {
                "item_id": item.item_id,
                "family": item.family,
                "stage": stage,
                "from": item.role,
                "to": "hard_negative",
                "concave_deficiency": round(measured[item.item_id], 4),
                "threshold": round(threshold, 4),
                "reason": detail,
            }
        )
        replacements[item.item_id] = replace(
            item, role="hard_negative", provenance={**item.provenance, "role_revised": detail}
        )

    # --- pass 1: a query of a concavity-defining family must be concave itself ---
    for item in items:
        if item.role != "query" or item.family not in CONCAVE_STRUCTURE_FAMILIES:
            continue
        got = measured[item.item_id]
        if got >= MIN_FAMILY_CONCAVE:
            continue
        demote(
            item,
            "query",
            MIN_FAMILY_CONCAVE,
            f"planned as the {item.family} query but its contour fills {1.0 - got:.4f} of "
            f"its own convex hull — deficiency {got:.4f} against the family's floor "
            f"{MIN_FAMILY_CONCAVE:.2f}, so it carries none of the concavity that defines "
            f"{item.family} and cannot stand as a relevant example of it",
        )

    # --- pass 2: a variant must keep a quarter of the family's mildest query ---
    depth_by_family: dict[str, list[float]] = {}
    for item in items:
        if item.role == "query" and item.item_id not in replacements:
            depth_by_family.setdefault(item.family, []).append(measured[item.item_id])

    for item in items:
        if item.item_id in replacements:
            continue
        depths = depth_by_family.get(item.family, [])
        keep = (
            item.role == "variant"
            and item.family in CONCAVE_STRUCTURE_FAMILIES
            and bool(depths)
            and max(depths) >= MIN_FAMILY_CONCAVE
        )
        if not keep:
            continue
        wanted = min(depths)
        got = measured[item.item_id]
        if got >= MIN_VARIANT_CONCAVE_SHARE * wanted:
            continue
        demote(
            item,
            "variant",
            MIN_VARIANT_CONCAVE_SHARE * wanted,
            f"planned as a {item.family} variant but it fills {1.0 - got:.4f} of its own "
            f"convex hull against the mildest {item.family} query's {1.0 - wanted:.4f}: "
            f"deficiency {got:.4f} is under the family floor "
            f"{MIN_VARIANT_CONCAVE_SHARE * wanted:.4f}, the defining concavity is gone, "
            "so as a *relevant* item it would teach the wrong lesson",
        )

    revised = [replacements.get(item.item_id, item) for item in items]
    return revised, revisions


def check_design_coverage(counts: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """Measure the built gallery against the brief's structural ranges.

    A shortfall *raises* instead of warning: a family with one query, or with fewer
    than five variants, is not the test set the experiment was asked to run, and a
    metric computed on it would be reported as if it were. Overshooting a ceiling
    (more than 15 variants or 20 hard negatives) is only reported — a stricter
    library is still a valid library — so the number a reader sees is measured and
    never restated from the plan.
    """
    shortfalls: list[str] = []
    over_range: list[str] = []
    if len(counts) < MIN_CLASSES:
        shortfalls.append(f"only {len(counts)} structure classes, the brief asks for {MIN_CLASSES}")
    for family, bucket in sorted(counts.items()):
        queries = int(bucket.get("query", 0))
        variants = int(bucket.get("variant", 0))
        negatives = int(bucket.get("hard_negative", 0))
        if queries < MIN_QUERIES_PER_FAMILY:
            shortfalls.append(f"{family}: {queries} queries, need {MIN_QUERIES_PER_FAMILY}")
        if variants < VARIANT_RANGE[0]:
            shortfalls.append(f"{family}: {variants} variants, need {VARIANT_RANGE[0]}")
        elif variants > VARIANT_RANGE[1]:
            over_range.append(f"{family}: {variants} variants, ceiling {VARIANT_RANGE[1]}")
        if negatives < NEGATIVE_RANGE[0]:
            shortfalls.append(f"{family}: {negatives} hard negatives, need {NEGATIVE_RANGE[0]}")
        elif negatives > NEGATIVE_RANGE[1]:
            over_range.append(f"{family}: {negatives} hard negatives, ceiling {NEGATIVE_RANGE[1]}")
    if shortfalls:
        raise RuntimeError(
            "the gallery does not meet the experiment's own design brief: "
            + "; ".join(shortfalls)
            + ". Fix the plan (or the audit thresholds) and rebuild; do not report "
              "metrics from a library that is not the intended test set."
        )
    return {
        "rule": (
            f">{MIN_CLASSES} structure classes, >={MIN_QUERIES_PER_FAMILY} queries, "
            f"{VARIANT_RANGE[0]}-{VARIANT_RANGE[1]} variants and "
            f"{NEGATIVE_RANGE[0]}-{NEGATIVE_RANGE[1]} hard negatives per class, counted "
            "from the built gallery after the structural role audit — measured, not "
            "restated from the plan"
        ),
        "classes": len(counts),
        "counts": {family: dict(bucket) for family, bucket in sorted(counts.items())},
        "shortfalls": shortfalls,
        "over_range": over_range,
        "within_brief": not over_range,
    }


def build_gallery(
    *,
    cache_dir: Path,
    include_phosphor: bool = True,
    include_quickdraw: bool = True,
    offline: bool = False,
    near_max_align: float = 0.02,
) -> dict[str, Any]:
    """Assemble the gallery, refusing loudly and counting whatever will not be one."""
    rejections: list[Rejection] = []
    synthetic_items, _ = build_synthetic(rejections)
    items: list[GalleryItem] = list(synthetic_items)
    if include_phosphor:
        items.extend(build_phosphor(cache_dir, rejections, offline=offline))
    if include_quickdraw:
        quickdraw_items, census = build_quickdraw(cache_dir, rejections, offline=offline)
        items.extend(quickdraw_items)
    else:
        census = {}
    items, role_revisions = audit_structural_roles(items)
    ordered = sorted(items, key=lambda item: (item.family, item.role, item.item_id))
    labels = build_labels(ordered, near_max_align=near_max_align)
    counts: dict[str, dict[str, int]] = {}
    for item in ordered:
        bucket = counts.setdefault(item.family, {"query": 0, "variant": 0, "hard_negative": 0})
        bucket[item.role] += 1
    coverage = check_design_coverage(counts)
    reasons: dict[str, int] = {}
    for rejection in rejections:
        reasons[f"{rejection.source}/{rejection.reason}"] = (
            reasons.get(f"{rejection.source}/{rejection.reason}", 0) + 1
        )
    return {
        "items": ordered,
        "labels": labels,
        "counts": counts,
        "coverage": coverage,
        "rejections": rejections,
        "rejection_reasons": reasons,
        "role_revisions": role_revisions,
        "quickdraw_census": census,
    }


def gallery_array(items: Sequence[GalleryItem]) -> np.ndarray:
    """``(M, 128, 2)`` standardized contours in gallery order."""
    return np.stack([item.contour for item in items]).astype(np.float64)


def perimeter_of(points: np.ndarray) -> float:
    return float(ring_perimeter(open_ring(points)))


def area_of(points: np.ndarray) -> float:
    return float(abs(signed_area(open_ring(points))))


def ccw_open(points: np.ndarray) -> np.ndarray:
    return ensure_ccw(open_ring(points))


def arc_resample(points: np.ndarray, n: int = N_SAMPLES) -> np.ndarray:
    return arc_length_resample(ensure_ccw(open_ring(points)), n)


__all__ = [
    "FAMILIES",
    "GALLERY_VERSION",
    "PHOSPHOR",
    "QUICKDRAW",
    "ROLES",
    "SYNTHETIC",
    "GalleryItem",
    "PhosphorPlan",
    "QuickDrawPlan",
    "Rejection",
    "Subpath",
    "arc_resample",
    "area_of",
    "build_gallery",
    "build_labels",
    "ccw_open",
    "circle",
    "crescent",
    "decode_path",
    "ellipse",
    "even_spaced",
    "exterior_subpaths",
    "fetch_cached",
    "gallery_array",
    "heart",
    "irregular",
    "item_from_points",
    "near_duplicate_pairs",
    "phosphor_plan",
    "phosphor_rings",
    "power_lobes",
    "quickdraw_candidates",
    "quickdraw_plan",
    "quickdraw_strokes",
    "radial",
    "regular_polygon",
    "rounded_bar",
    "semicircle",
    "shape_metrics",
    "standardize",
    "star",
    "subpath_signed_areas",
    "synthetic_specs",
    "teardrop",
    "vesica",
    "view_box",
]
