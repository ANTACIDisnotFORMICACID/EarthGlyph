#!/usr/bin/env python
"""CLI: build the P0-4 query set — reproducible, and honest about its own origin.

Three families, never mixed in one evaluation:

``probe`` — synthetic perturbation (docs/EXPERIMENTS.md layer A/B inputs)
    A real library contour, or a deliberate distortion of one, handed over as a
    point trace. Used to test invariances and to attribute failures. These are
    **not** hand drawings, and the manifest says so on every record.

``sketch`` — machine-drawn pointer traces
    Crescent / heart / S / stars / forked / irregular curves sampled the way a
    pointer samples: uneven steps, pen wobble, an end gap. They exercise
    extraction plus search end to end and try the shape families the brief lists,
    but a program made them, so they are **not** independent human hand drawings
    either. Real drawings exported from ``tools/sketch_pad.html`` go in the same
    directory and are read by the same code path.

``image`` — the same archetypes rasterized as black-on-white PNGs, plus one page
    with two shapes, which must be refused rather than guessed.

Example
-------
python scripts/make_retrieval_queries.py \
    --index data/index --out experiments/p0_retrieval_baseline/queries --seed 20261105
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.geometry import (  # noqa: E402
    DEFAULT_N_POINTS,
    arc_length_resample,
    ensure_ccw,
    normalize_contour,
    open_ring,
)

PEN_STEP_NOISE = 0.55  # how unevenly the "pen" advances, in units of the mean step
MIN_TRACE_POINTS = 90
TAIL_MIN_STEPS = 6  # a closing-error ramp never spans fewer than this many samples
PROBE_ROWS = (7, 61, 100, 294, 365, 745)  # lake, lake, lake, reservoir, pond, sibling lake
ARCHETYPES = (
    "crescent",
    "heart",
    "s-shape",
    "star10",
    "pentagram",
    "forked",
    "irregular",
)


def wobble(points: np.ndarray, *, seed: int, amplitude: float) -> np.ndarray:
    """Radial pen wobble: a smoothed random walk, not independent noise per sample.

    A real hand shakes continuously, and white noise on each sample would test the
    resampler rather than the descriptor.
    """
    rng = np.random.default_rng(seed)
    count = points.shape[0]
    raw = rng.normal(0.0, 1.0, count + 12)
    kernel = np.exp(-0.5 * (np.arange(-6, 7) / 2.2) ** 2)
    kernel = kernel / kernel.sum()
    smooth = np.convolve(raw, kernel, mode="same")[6 : 6 + count]
    smooth = smooth / (np.max(np.abs(smooth)) + 1e-12)
    centre = points.mean(axis=0)
    radial = points - centre
    length = np.linalg.norm(radial, axis=1)
    return points + radial / np.maximum(length, 1e-12)[:, None] * (
        smooth * amplitude * float(np.mean(length))
    )[:, None]


def resample_uneven(
    points: np.ndarray,
    *,
    seed: int,
    n: int = MIN_TRACE_POINTS,
    sigma: float = PEN_STEP_NOISE,
) -> np.ndarray:
    """Re-sample along the perimeter with uneven steps, as a stroke actually comes out.

    ``sigma`` is the spread of the step-length noise in units of the mean step;
    the default is the P0-4 pen, so existing query sets regenerate unchanged.
    """
    ring = np.vstack([points, points[:1]])
    step = np.linalg.norm(np.diff(ring, axis=0), axis=1)
    total = float(step.sum())
    rng = np.random.default_rng(seed)
    weights = np.abs(rng.normal(1.0, sigma, n)) + 1e-3
    targets = np.cumsum(weights / weights.sum()) * total
    cum = np.concatenate([[0.0], np.cumsum(step)])
    index = np.clip(np.searchsorted(cum, targets, side="right") - 1, 0, len(step) - 1)
    local = (targets - cum[index]) / np.maximum(step[index], 1e-12)
    return ring[index] + local[:, None] * (ring[index + 1] - ring[index])


def as_trace(points: np.ndarray, *, seed: int, wobble_amplitude: float = 0.0) -> np.ndarray:
    traced = resample_uneven(open_ring(points), seed=seed)
    if wobble_amplitude:
        traced = wobble(traced, seed=seed + 1, amplitude=wobble_amplitude)
    return traced


def turn(points: np.ndarray, theta: float) -> np.ndarray:
    c, s = float(np.cos(theta)), float(np.sin(theta))
    return points @ np.array([[c, -s], [s, c]])


def mirror(points: np.ndarray) -> np.ndarray:
    return points * np.array([[-1.0, 1.0]])


def local_deform(
    points: np.ndarray, *, centre_angle: float, width: float, height: float
) -> np.ndarray:
    """Push a patch of the boundary out (an island) or pull it in (a bay)."""
    centre = points.mean(axis=0)
    radial = points - centre
    angle = np.arctan2(radial[:, 1], radial[:, 0])
    delta = np.angle(np.exp(1j * (angle - centre_angle)))
    weight = np.exp(-0.5 * (delta / width) ** 2)
    return centre + radial * (1.0 + weight * height)[:, None]


def open_end(points: np.ndarray, *, share: float, ramp: float = 0.0) -> np.ndarray:
    """The pen does not come back to the start.

    ``share`` is how far the free end lands, as a fraction of the shape scale.
    ``ramp = 0`` displaces only the last sample — measured in P0-4, closure drops
    that vertex (a closing edge is implied by the ring, not drawn), so it tests
    the refusal limit and nothing else. A nonzero ``ramp`` drifts a tail of the
    stroke out to that same offset, which is what a hand that misses the start
    point and keeps going actually leaves: a tail that is part of the shape.

    ``ramp`` is a fraction of the stroke's own perimeter, floored at
    ``TAIL_MIN_STEPS`` sample steps — on a spidery reservoir one step can already
    exceed 15% of the shape scale, and a ramp shorter than a step is no ramp.
    """
    scale = float(np.sqrt(np.mean(np.sum((points - points.mean(axis=0)) ** 2, axis=1))))
    direction = points[-1] - points[-2]
    direction = direction / max(float(np.linalg.norm(direction)), 1e-12)
    if ramp <= 0.0:
        moved = points.copy()
        moved[-1] = points[-1] + direction * (share * scale)
        return moved
    step = np.linalg.norm(np.diff(points, axis=0), axis=1)
    remaining = np.concatenate([np.cumsum(step[::-1])[::-1], [0.0]])
    reach = max(ramp * float(step.sum()), TAIL_MIN_STEPS * float(np.median(step)))
    weight = np.clip((reach - remaining) / max(reach, 1e-12), 0.0, 1.0)
    return points + np.outer(weight * (share * scale), direction)


# --- library-derived probes --------------------------------------------------


PROBE_SPECS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("self", {}),
    ("rot", {"theta_deg": 37.0}),
    ("rot", {"theta_deg": 191.0}),
    ("scale", {"factor": 3.7}),
    ("shift", {"dx": 40.0, "dy": -25.0}),
    ("mirror", {}),
    ("reverse", {}),
    ("sparse", {"n": 26}),
    ("jag", {"level": 0.02}),
    ("jag", {"level": 0.05}),
    ("jag", {"level": 0.1}),
    ("island", {"height": 0.18}),
    ("bay", {"height": -0.18}),
    ("aspect", {"factor": 1.25}),
    ("gap", {"share": 0.06}),
    ("gap", {"share": 0.06, "ramp": 0.15}),
    ("gap", {"share": 0.35}),
    ("gap", {"share": 0.35, "ramp": 0.15}),
)


def library_probes(index: Any, *, seed: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for position, row in enumerate(PROBE_ROWS):
        contour = np.asarray(index.shapes[row], dtype=float)
        for family, params in PROBE_SPECS:
            points, name = contour.copy(), f"{family}-r{row}"
            if family == "rot":
                points = turn(points, np.deg2rad(float(params["theta_deg"])))
                name += f"-{params['theta_deg']:g}deg"
            elif family == "scale":
                points = points * float(params["factor"])
                name += f"-{params['factor']:g}x"
            elif family == "shift":
                points = points + np.array([float(params["dx"]), float(params["dy"])])
            elif family == "mirror":
                points = mirror(points)
            elif family == "reverse":
                points = points[::-1]
            elif family == "sparse":
                points = arc_length_resample(open_ring(points), int(params["n"]))
                name += f"-{params['n']}pts"
            elif family == "jag":
                level = float(params["level"])
                points = as_trace(points, seed=seed + position, wobble_amplitude=level)
                name += f"-{level:g}"
            elif family == "island":
                points = local_deform(points, centre_angle=0.9, width=0.35,
                                      height=float(params["height"]))
            elif family == "bay":
                points = local_deform(points, centre_angle=2.4, width=0.3,
                                      height=float(params["height"]))
            elif family == "aspect":
                factor = float(params["factor"])
                points = points * np.array([factor, 1.0 / factor])
            elif family == "gap":
                share = float(params["share"])
                ramp = float(params.get("ramp", 0.0))
                points = open_end(as_trace(points, seed=seed + position), share=share, ramp=ramp)
                name += f"-{share:g}" + ("-tail" if ramp > 0.0 else "")
            out.append(
                {
                    "name": name,
                    "family": "probe",
                    "perturbation": family,
                    "params": params,
                    "source_row": int(row),
                    "source_candidate_id": str(index.metadata.iloc[int(row)]["candidate_id"]),
                    "points": points,
                    "closed": True,
                }
            )
    return out


# --- archetypes --------------------------------------------------------------


def archetype(kind: str) -> np.ndarray:
    """One closed curve per shape family, in math coordinates (y up).

    The crescent and the S are polygon booleans, because that is what they are:
    a disk minus a disk, and a stroked path. Hand-assembling them from parametric
    arcs is how a fake crescent gets into a test set.
    """
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    def ring_of(geometry: Any) -> np.ndarray:
        polygon = geometry if geometry.geom_type == "Polygon" else unary_union(geometry.geoms)
        if polygon.geom_type == "MultiPolygon":
            polygon = max(polygon.geoms, key=lambda part: part.area)
        return np.asarray(polygon.exterior.coords)

    if kind == "crescent":
        outer = Point(0.0, 0.0).buffer(1.0, quad_segs=96)
        inner = Point(0.5, 0.0).buffer(0.82, quad_segs=96)
        return ring_of(outer.difference(inner))
    if kind == "heart":
        t = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
        x = 16.0 * np.sin(t) ** 3
        y = 13.0 * np.cos(t) - 5.0 * np.cos(2 * t) - 2.0 * np.cos(3 * t) - np.cos(4 * t)
        return normalize_contour(np.column_stack([x, y]))
    if kind == "s-shape":
        path = LineString([(0.30, 1.10), (0.62, 0.35), (-0.62, -0.35), (-0.30, -1.10)])
        return ring_of(path.buffer(0.30, quad_segs=24, cap_style=1))
    if kind in ("star10", "pentagram"):
        if kind == "star10":
            angles = np.arange(10) * (np.pi / 5.0)
            radius = np.where(np.arange(10) % 2 == 0, 1.0, 0.42)
            return normalize_contour(
                np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])
            )
        # a pentagram is one stroke that crosses itself: kept as drawn, so the
        # self-intersection refusal is exercised rather than hidden
        angles = np.arange(5) * (2.0 * np.pi / 5.0)
        order = np.array([0, 2, 4, 1, 3])
        star = np.column_stack([np.cos(angles[order]), np.sin(angles[order])])
        return normalize_contour(star)
    if kind == "forked":
        t = np.linspace(0.0, 2.0 * np.pi, 480, endpoint=False)
        radius = 1.0 + 0.62 * np.cos(3.0 * t + 0.4) ** 2 + 0.18 * np.sin(7 * t)
        return normalize_contour(np.column_stack([radius * np.cos(t), radius * np.sin(t) * 0.82]))
    if kind == "irregular":
        rng = np.random.default_rng(7)
        count = 420
        t = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
        k = np.arange(2, 9)[:, None]
        basis = np.concatenate([np.cos(k * t[None, :]), np.sin(k * t[None, :])])
        coefs = rng.uniform(0.05, 0.30, basis.shape[0])
        coefs = coefs * (0.5 / coefs.sum())
        radius = 1.0 + 0.3 * np.cos(t + 0.4) + coefs @ basis
        return normalize_contour(np.column_stack([radius * np.cos(t), radius * np.sin(t)]))
    raise ValueError(f"unknown archetype {kind!r}")


def sketch_probes(*, seed: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for position, kind in enumerate(ARCHETYPES):
        shape = ensure_ccw(open_ring(archetype(kind)))
        traced = as_trace(shape, seed=seed + 40 + position, wobble_amplitude=0.035)
        out.append(
            {
                "name": f"drawn-{kind}",
                "family": "sketch",
                "perturbation": "machine-drawn pointer trace",
                "params": {"archetype": kind, "wobble": 0.035, "uneven_steps": True},
                "source_row": None,
                "source_candidate_id": None,
                "points": traced,
                "closed": True,
            }
        )
    return out


# --- writing -----------------------------------------------------------------


def write_query(record: dict[str, Any], directory: Path) -> dict[str, Any]:
    payload = {
        "points": [[round(float(x), 3), round(float(y), 3)] for x, y in record["points"]],
        "closed": bool(record["closed"]),
        "unit": "canvas",
        "y_axis": "up (flipped from the canvas' downward axis at export)",
        "canvas": {"width": 520, "height": 520},
        "tool": "scripts/make_retrieval_queries.py",
        "note": "raw trace samples, nothing smoothed or snapped",
        "family": record["family"],
        "perturbation": record["perturbation"],
        "params": record["params"],
        "source_row": record["source_row"],
        "source_candidate_id": record["source_candidate_id"],
    }
    path = directory / f"{record['name']}.json"
    path.write_text(json.dumps(payload, indent=1), "utf-8")
    return {key: value for key, value in record.items() if key != "points"} | {
        "file": path.name
    }


def write_png_sketches(directory: Path, *, size: int = 320, width: int = 4) -> list[str]:
    """The archetypes as black-on-white PNGs, plus one page that must be refused."""
    try:
        import cv2
    except ImportError:  # pragma: no cover - the cv extra is optional
        print("cv2 not installed; skipping PNG queries", file=sys.stderr)
        return []
    written: list[str] = []
    for kind in ("crescent", "heart", "star10", "irregular"):
        shape = archetype(kind)
        scale = 0.40 * size / float(np.abs(shape).max())
        pixel = np.column_stack(
            [size / 2 + scale * shape[:, 0], size / 2 - scale * shape[:, 1]]
        ).astype(np.int32)
        image = np.full((size, size), 255, dtype=np.uint8)
        cv2.polylines(image, [pixel], True, 0, width)
        path = directory / f"drawn-{kind}.png"
        cv2.imwrite(str(path), image)
        written.append(path.name)

    # two separate shapes on one page: ambiguous, so it must be refused
    image = np.full((size, size), 255, dtype=np.uint8)
    for centre, radius in ((np.array([0.30, 0.35]), 0.20), (np.array([0.70, 0.65]), 0.16)):
        t = np.linspace(0.0, 2.0 * np.pi, 140, endpoint=False)
        shape = 1.0 + 0.12 * np.sin(5.0 * t + float(centre[0]))
        pixel = np.column_stack(
            [
                (centre[0] + float(radius) * np.cos(t) * shape) * size,
                (centre[1] + float(radius) * np.sin(t) * shape) * size,
            ]
        ).astype(np.int32)
        cv2.polylines(image, [pixel], True, 0, width)
    path = directory / "ambiguous-two-shapes.png"
    cv2.imwrite(str(path), image)
    written.append(path.name)

    # a shape that does not close: open strokes are a redraw request, not a query
    t = np.linspace(0.4, 0.4 + 1.55 * np.pi, 160)
    pixel = np.column_stack(
        [size / 2 + 0.36 * size * np.cos(t), size / 2 - 0.36 * size * np.sin(t)]
    ).astype(np.int32)
    image = np.full((size, size), 255, dtype=np.uint8)
    cv2.polylines(image, [pixel], False, 0, width)
    path = directory / "open-arc.png"
    cv2.imwrite(str(path), image)
    written.append(path.name)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/p0_retrieval_baseline/queries")
    )
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--no-png", action="store_true")
    args = parser.parse_args(argv)

    from earthglyph.retrieval.search import load_index_handle

    index = load_index_handle(args.index)
    args.out.mkdir(parents=True, exist_ok=True)
    records = library_probes(index, seed=args.seed) + sketch_probes(seed=args.seed)
    entries = [write_query(record, args.out) for record in records]
    pngs = [] if args.no_png else write_png_sketches(args.out)

    manifest = {
        "generated_by": "scripts/make_retrieval_queries.py",
        "command": f"python scripts/make_retrieval_queries.py --index {args.index} "
        f"--out {args.out} --seed {args.seed}",
        "seed": args.seed,
        "pen_step_noise": PEN_STEP_NOISE,
        "trace_points": MIN_TRACE_POINTS,
        "n_points": DEFAULT_N_POINTS,
        "index": index.provenance(),
        "families": {
            "probe": "real library contour or a deliberate distortion of one — "
            "synthetic perturbation, NOT a hand drawing",
            "sketch": "machine-drawn pointer trace of a parametric archetype — NOT an "
            "independent human hand drawing; real drawings exported from "
            "tools/sketch_pad.html belong in this directory and read the same way",
            "image": "rasterized sketch, plus the two cases that must be refused "
            "(ambiguous-two-shapes.png, open-arc.png)",
        },
        "queries": entries,
        "png_queries": pngs,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8"
    )
    print(f"wrote {len(entries)} trace queries and {len(pngs)} PNGs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
