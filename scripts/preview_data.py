#!/usr/bin/env python
"""CLI: render a grid preview of real closed water contours from an extract.

This is DATA INSPECTION, not a retrieval result: it shows real outlines from
the downloaded dataset so we can judge whether the candidate pool is varied
and usable. Shapes are projected to a local azimuthal-equidistant metric
plane (aeqd) so aspect ratio and geometry are not distorted; lon/lat are
never treated as planar coordinates.

Example
-------
python scripts/preview_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/geometry_preview.png --count 32 --seed 42
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

USABLE_GEOM_TYPES = ("Polygon", "MultiPolygon")


def _touching_frame(
    row_bbox: dict, frame: tuple[float, float, float, float], eps: float = 1e-6
) -> bool:
    """True if the feature bbox reaches the query frame: likely a clip artifact."""
    xmin, ymin, xmax, ymax = frame
    return (
        abs(row_bbox["xmin"] - xmin) < eps
        or abs(row_bbox["xmax"] - xmax) < eps
        or abs(row_bbox["ymin"] - ymin) < eps
        or abs(row_bbox["ymax"] - ymax) < eps
    )


def select_samples(gdf, frame: tuple[float, float, float, float], count: int, seed: int):
    """Pick a size-stratified sample of clean closed contours, mixed subtypes."""
    import shapely

    geom = gdf.geometry
    usable = geom.geom_type.isin(USABLE_GEOM_TYPES) & shapely.is_valid(geom.array)
    cand = gdf.loc[usable].copy()
    cand = cand[
        [not _touching_frame(b, frame) for b in cand["bbox"]]
    ]
    if len(cand) == 0:
        return cand
    rng = np.random.default_rng(seed)
    # Stratify by metric area decile to span tiny ponds -> large lakes. Area is
    # measured after projection: geographic-CRS area is in square degrees.
    lon0 = (frame[0] + frame[2]) / 2
    lat0 = (frame[1] + frame[3]) / 2
    areas = np.array([g.area for g in _project(cand.geometry, lon0, lat0)])
    order = cand.index.to_numpy()[np.argsort(areas)]
    deciles = np.array_split(order, count)
    picks = [rng.choice(list(d)) for d in deciles if len(d) > 0]
    return gdf.loc[picks]


def _project(geoms, lon0: float, lat0: float):
    import shapely
    from pyproj import Transformer

    tr = Transformer.from_proj(
        "EPSG:4326",
        f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +ellps=WGS84 +units=m +no_defs",
        always_xy=True,
    )

    def _f(xy: np.ndarray) -> np.ndarray:
        x, y = tr.transform(xy[:, 0], xy[:, 1])
        return np.column_stack((x, y))

    return [shapely.transform(g, _f) for g in geoms]


def _primary_name(names: object) -> str:
    if isinstance(names, dict):
        return str(names.get("primary") or "")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--frame",
        default=None,
        help="query bbox used for download (min_lon,min_lat,max_lon,max_lat); "
             "default: total bounds of the file",
    )
    args = parser.parse_args(argv)

    import geopandas as gpd
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gdf = gpd.read_parquet(args.input)
    if args.frame:
        frame = tuple(float(v) for v in args.frame.split(","))
    else:
        b = np.array(gdf.total_bounds)
        frame = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))

    samples = select_samples(gdf, frame, args.count, args.seed)
    n = min(len(samples), args.count)
    if n == 0:
        print("no usable samples selected", file=sys.stderr)
        return 1
    samples = samples.head(n)
    lon0 = (frame[0] + frame[2]) / 2
    lat0 = (frame[1] + frame[3]) / 2
    projected = _project(samples.geometry, lon0, lat0)

    cols = 6
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.4, rows * 2.2))
    axes = np.atleast_2d(axes)
    for i, ax in enumerate(axes.flat):
        if i >= n:
            ax.axis("off")
            continue
        geom = projected[i]
        minx, miny, maxx, maxy = geom.bounds
        pad = max(maxx - minx, maxy - miny) * 0.08 + 1e-9
        patch = matplotlib.patches.PathPatch(
            matplotlib.path.Path.make_compound_path(*_geom_paths(geom)),
            facecolor="#7fb2d9", edgecolor="#28556e", lw=0.8,
        )
        ax.add_patch(patch)
        ax.set_xlim(minx - pad, maxx + pad)
        ax.set_ylim(miny - pad, maxy + pad)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        row = samples.iloc[i]
        short_id = str(row["id"])[:22]
        area_ha = projected[i].area / 1e4  # projected m2 -> hectares
        area_label = f"{area_ha:.2f} ha" if area_ha < 1 else f"{area_ha:.1f} ha"
        rp = row.geometry.representative_point()
        ax.set_title(
            f"{row['subtype']} · {area_label} · ({rp.y:.4f},{rp.x:.4f})\n"
            f"id {short_id}…",
            fontsize=6.0,
        )
    fig.suptitle(
        "EarthGlyph P0-1 data probe — REAL Overture water closed contours "
        f"(Wisconsin bbox {frame[0]},{frame[1]},{frame[2]},{frame[3]}, release 2026-10-21.0)\n"
        "projected to local aeqd metres (equal aspect); inspection only, NOT retrieval results",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150)
    print(f"preview written: {args.output} ({n} real features)")
    return 0


def _geom_paths(geom):
    """Build matplotlib compound path parts from a Polygon/MultiPolygon."""
    from matplotlib.path import Path

    parts = []
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        parts.append(Path(poly.exterior.coords))
        for hole in poly.interiors:
            # Reverse hole winding so the nonzero rule renders it empty.
            parts.append(Path(list(hole.coords)[::-1]))
    return parts


if __name__ == "__main__":
    raise SystemExit(main())
