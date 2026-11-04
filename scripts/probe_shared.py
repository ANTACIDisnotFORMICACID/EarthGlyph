"""Shared figure helpers for the EarthGlyph probe scripts (P0-2 / P0-3).

Extracted verbatim from ``scripts/visualize_geometry.py`` so the P0-3 feature and
sampling figures draw real source geometries and normalized contours exactly the
same way; re-running the P0-2 script after the move reproduces its four PNGs
byte for byte (verified in ``experiments/experiment_log.md``).

Matplotlib is imported lazily inside each function and every caller selects the
``Agg`` backend, so importing this module never opens a window.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from earthglyph.geometry import AeqdProjection


def projection_from_manifest(manifest: dict[str, Any]) -> AeqdProjection:
    """Rebuild the projection a dataset was built with (D16)."""
    proj = manifest["projection"]
    return AeqdProjection(lon0=float(proj["lon0"]), lat0=float(proj["lat0"]))


def geom_paths(geom: Any) -> list[Any]:
    """Matplotlib paths for any polygonal geometry (holes rendered empty)."""
    from matplotlib.path import Path

    parts: list[Any] = []
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        parts.append(Path(np.asarray(poly.exterior.coords)))
        for hole in poly.interiors:
            parts.append(Path(np.asarray(hole.coords)[::-1]))
    return parts


def draw_geometry(
    ax: Any,
    geom: Any,
    *,
    facecolor: str = "#7fb2d9",
    edgecolor: str = "#28556e",
    lw: float = 0.8,
    pad_frac: float = 0.08,
) -> None:
    """Draw a shapely geometry of any type into an equal-aspect axis."""
    import matplotlib
    from matplotlib.path import Path

    geom_type = geom.geom_type
    if geom.is_empty:
        ax.text(0.5, 0.5, "empty geometry", ha="center", va="center", fontsize=6)
        ax.set_xticks([])
        ax.set_yticks([])
        return
    if geom_type in ("Polygon", "MultiPolygon"):
        patch = matplotlib.patches.PathPatch(
            Path.make_compound_path(*geom_paths(geom)),
            facecolor=facecolor,
            edgecolor=edgecolor,
            lw=lw,
        )
        ax.add_patch(patch)
    elif geom_type in ("LineString", "MultiLineString", "LinearRing"):
        lines = list(geom.geoms) if geom_type == "MultiLineString" else [geom]
        for line in lines:
            xy = np.asarray(line.coords)
            ax.plot(xy[:, 0], xy[:, 1], color=edgecolor, lw=max(lw, 1.0))
    elif geom_type in ("Point", "MultiPoint"):
        pts = np.asarray(geom.coords if geom_type == "Point" else [p.coords[0] for p in geom.geoms])
        ax.plot(pts[:, 0], pts[:, 1], "o", ms=3.0, color=edgecolor)
    else:  # GeometryCollection and anything unexpected
        for sub in getattr(geom, "geoms", []):
            draw_geometry(ax, sub, facecolor=facecolor, edgecolor=edgecolor, lw=lw)
        return
    minx, miny, maxx, maxy = geom.bounds
    pad = max(maxx - minx, maxy - miny) * pad_frac + 1e-9
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def draw_contour(
    ax: Any, points: np.ndarray, *, color: str = "#28556e", face: str = "#cfe3f2"
) -> None:
    """Draw a normalized (N,2) contour closed onto itself."""
    ring = np.vstack([points, points[:1]])
    ax.fill(ring[:, 0], ring[:, 1], facecolor=face, edgecolor="none", zorder=1)
    ax.plot(ring[:, 0], ring[:, 1], color=color, lw=0.9, zorder=2)
    ax.plot(points[:, 0], points[:, 1], ".", ms=1.4, color=color, zorder=3)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    lim = float(np.abs(points).max()) * 1.15 + 1e-9
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)


def stratified_pick(order: np.ndarray, k: int) -> list[int]:
    """Deterministic spread: one index per equal slice of a sorted order."""
    slices = np.array_split(order, k)
    return [int(s[len(s) // 2]) for s in slices if len(s) > 0]


def short_id(identifier: str, width: int = 19) -> str:
    """Truncate a candidate/source id for a panel caption."""
    text = str(identifier)
    return text if len(text) <= width else text[: width - 3] + "..."


def candidate_caption(row: pd.Series) -> str:
    """One-line real-data caption: subtype, area and part/hole bookkeeping."""
    flags = []
    if int(row["n_holes"]) > 0:
        flags.append(f"{int(row['n_holes'])} holes")
    if int(row["n_parts"]) > 1:
        flags.append(f"part {int(row['part_index']) + 1}/{int(row['n_parts'])}")
    flag_txt = (" | " + " | ".join(flags)) if flags else ""
    return f"{row['subtype']} | {float(row['area_ha']):.3g} ha{flag_txt}"
