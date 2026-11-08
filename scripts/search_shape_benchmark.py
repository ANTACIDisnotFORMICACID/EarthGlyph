"""Query the independent shape benchmark from the command line (P0-4.6).

Examples:
    # Search the MPEG-7 gallery with a silhouette PNG using Shape Context.
    python scripts/search_shape_benchmark.py --query shape.png --gallery mpeg7 --method E3

    # Search the rated H1 stimuli with the published official ShapeComp coordinates.
    python scripts/search_shape_benchmark.py --query h1_0_a --gallery h1 --method S0 --top-k 10

    # A directory of images as the gallery, excluding the query's own near-duplicates.
    python scripts/search_shape_benchmark.py --query q.png --gallery ./shapes --method E0 \
        --exclude q.png --output results.csv

No geographic data is read. ``--gallery mpeg7`` and ``--gallery h1`` use the cache built by
``scripts/fetch_shape_benchmark_sources.py``. ``--allow-mirror`` is a capability of the EarthGlyph
arms only; the official arms raise instead of accepting a switch they would ignore.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from earthglyph.geometry.sketch import contour_from_image
from earthglyph.retrieval.perceptual_methods import CONTOUR_POINTS
from earthglyph.shape_benchmark.datasets import (
    BenchmarkShape,
    load_h1,
    load_mpeg7,
)
from earthglyph.shape_benchmark.retrieval import (
    ALL_METHODS,
    MIRROR_AWARE_METHODS,
    Gallery,
    search_shape,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"
IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".bmp"}


def build_gallery(spec: str, cache: Path) -> tuple[Gallery, dict[str, Any]]:
    if spec == "mpeg7":
        shapes, excluded = load_mpeg7(cache)
        info: dict[str, Any] = {
            "source": "MPEG-7 CE-Shape-1",
            "n_shapes": len(shapes),
            "excluded": [{"name": e.name, "reason": e.reason} for e in excluded],
        }
        return Gallery(shapes, name="mpeg7"), info
    if spec == "h1":
        data = load_h1(cache)
        info = {"source": "H1 rated GAN stimuli", **data.provenance}
        shapes = list(data.shapes.values())
        return Gallery(shapes, official_coords=data.official_coords, name="h1"), info
    path = Path(spec)
    if not path.is_dir():
        raise SystemExit(f"gallery must be 'mpeg7', 'h1', or an image directory; got {spec!r}")
    shapes = []
    for image in sorted(path.iterdir()):
        if image.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        query = contour_from_image(image, n_points=CONTOUR_POINTS, smoothing="none")
        points = np.asarray(query.points, dtype=float)
        shapes.append(
            BenchmarkShape(
                shape_id=image.stem,
                source=f"dir:{path.name}",
                points=points,
                display_points=points,
            )
        )
    if not shapes:
        raise SystemExit(f"no images found in {path}")
    return Gallery(shapes, name=path.name), {"source": str(path), "n_shapes": len(shapes)}


def build_query(spec: str, gallery: Gallery) -> BenchmarkShape:
    index = gallery.index_of(spec)
    if index is not None:
        return gallery.shapes[index]
    stem = Path(spec).stem
    index = gallery.index_of(stem)
    if index is not None:
        return gallery.shapes[index]
    path = Path(spec)
    if not path.is_file():
        raise SystemExit(f"query must be a gallery shape_id or an image file; got {spec!r}")
    query = contour_from_image(path, n_points=CONTOUR_POINTS, smoothing="none")
    points = np.asarray(query.points, dtype=float)
    return BenchmarkShape(
        shape_id=stem, source=f"file:{path.name}", points=points, display_points=points
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="image path, or a shape_id in the gallery")
    parser.add_argument("--gallery", default="mpeg7", help="'mpeg7', 'h1', or an image directory")
    parser.add_argument("--method", default="E3", choices=ALL_METHODS)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--allow-mirror",
        action="store_true",
        help=f"score the reflected candidate too; implemented only for "
        f"{list(MIRROR_AWARE_METHODS)} and refused for the official arms",
    )
    parser.add_argument(
        "--exclude", nargs="*", default=[], help="shape_ids to remove from candidates"
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, help="write the Top-K as CSV (or JSON with .json)")
    args = parser.parse_args()

    gallery, info = build_gallery(args.gallery, args.cache_dir)
    query = build_query(args.query, gallery)
    result = search_shape(
        query,
        gallery,
        args.method,
        top_k=args.top_k,
        allow_mirror=args.allow_mirror,
        exclude=args.exclude,
    )

    print(f"gallery: {info['source']}  candidates={len(result.order)}/{len(gallery)}")
    print(f"query:   {query.shape_id}  method={args.method}  mirror={args.allow_mirror}")
    print(f"elapsed: {result.elapsed_seconds:.3f}s")
    print(f"{'rank':>4}  {'distance':>10}  {'category':<16} shape_id")
    for hit in result.hits:
        print(
            f"{hit.rank:>4}  {hit.distance:>10.5f}  {str(hit.category or '-'):16} {hit.shape_id}"
        )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "rank": hit.rank,
                "shape_id": hit.shape_id,
                "source": hit.source,
                "category": hit.category,
                "distance": hit.distance,
            }
            for hit in result.hits
        ]
        if args.output.suffix.lower() == ".json":
            payload = {
                "query_id": result.query_id,
                "method": result.method,
                "gallery_size": result.gallery_size,
                "elapsed_seconds": result.elapsed_seconds,
                "hits": rows,
                "full_ranking": [gallery.shapes[int(i)].shape_id for i in result.order],
            }
            args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        else:
            with args.output.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()