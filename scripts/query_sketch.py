#!/usr/bin/env python
"""CLI: one sketch in, the real Top-K out (P0-4, Task E — algorithm debugging).

Reads a canvas trace (``tools/sketch_pad.html`` exports ``{"points", "closed"}``), a
bare point list, or a simple black-on-white PNG, runs the same two-stage search the
browser would run against the real index, then prints the ranking and writes the
overlay figure and a JSON dump. Refusals are printed as refusals: an open stroke,
an ambiguous image or a structurally self-crossing outline does not become a lake.

Examples
--------
python scripts/query_sketch.py experiments/queries/moon-01.json
python scripts/query_sketch.py experiments/queries/hand-drawn.png --top 10
python scripts/query_sketch.py sketch.json --method fourier --shortlist 300
python scripts/query_sketch.py sketch.json --allow-mirror --chamfer-top 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from visualize_retrieval import topk_figure  # noqa: E402

from earthglyph.geometry.sketch import SketchError  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    METHODS,
    SearchError,
    load_index_handle,
    search_by_contour,
)


def format_table(result: Any) -> str:
    header = (
        f"{'rank':>4}  {'row':>4}  {'candidate_id':<22} {'type':<10} "
        f"{'lon':>9} {'lat':>8} {'area/ha':>9} {'coarse':>7} {'align':>7} "
        f"{'theta':>7} {'shift':>5} {'mir':>5} {'cham':>6}"
    )
    lines = [header, "-" * len(header)]
    for hit in result.hits:
        lines.append(
            f"{hit.rank:>4}  {hit.row:>4}  {hit.candidate_id[:22]:<22} {hit.subtype:<10} "
            f"{hit.lon:>9.4f} {hit.lat:>8.4f} {hit.area_m2 / 1e4:>9.1f} "
            f"{_num(hit.coarse_distance):>7} {_num(hit.align_distance):>7} "
            f"{_num(hit.align_theta_deg, 1):>7} "
            f"{'- ' if hit.align_shift is None else f'{hit.align_shift} ':>5} "
            f"{_yes_no(hit.align_mirrored):>5} {_num(hit.chamfer, 3):>6}"
        )
    return "\n".join(lines)


def _num(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _yes_no(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sketch", type=Path, help="sketch JSON or black-on-white PNG")
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/p0_retrieval_baseline"))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--shortlist", type=int, default=200)
    parser.add_argument("--method", default="fourier_align", choices=METHODS)
    parser.add_argument("--smoothing", default="light")
    parser.add_argument("--coarse-scale", default="none", choices=("none", "std"))
    parser.add_argument("--max-per-source", type=int, default=1)
    parser.add_argument("--sort-by", default="align", choices=("align", "chamfer"))
    parser.add_argument(
        "--chamfer-top", type=int, default=None, help="cross-check the best N with Chamfer"
    )
    parser.add_argument("--allow-mirror", action="store_true")
    parser.add_argument("--allow-self-intersection", action="store_true")
    parser.add_argument("--closed", default="auto", choices=("auto", "true", "false"))
    parser.add_argument("--no-figure", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.sketch.exists():
        print(f"{args.sketch}: no such file", file=sys.stderr)
        return 2
    try:
        index = load_index_handle(args.index)
        result = search_by_contour(
            args.sketch,
            index,
            top_k=args.top,
            shortlist_size=args.shortlist,
            method=args.method,
            smoothing=args.smoothing,
            coarse_scale=args.coarse_scale,
            max_per_source=args.max_per_source,
            sort_by=args.sort_by,
            chamfer_top=args.chamfer_top,
            allow_mirror=args.allow_mirror,
            allow_self_intersection=args.allow_self_intersection,
            closed={"auto": None, "true": True, "false": False}[args.closed],
        )
    except (SketchError, SearchError) as error:
        # A refusal is a result: it says what the input was not, and nothing is ranked.
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1

    source = result.query["query_contour"]
    print(f"query      : {args.sketch}  ({source['source']}, {source['n_raw_points']} raw "
          f"points -> {source['n_outline_points']} outline -> {source['n_points']} standard)")
    print(f"             closure gap={source['closure']['gap_rel']:.4f} "
          f"auto_closed={bool(source['closure']['auto_closed'])} "
          f"seam={bool(source['closure'].get('seam_closed'))} winding={source['winding']} "
          f"smoothing={source['smoothing']} (sigma={source['smoothing_sigma']:g})")
    print(f"             self-crossings={source['self_intersection']['count']} "
          f"material={source['self_intersection']['material']}")
    print(f"index      : {result.provenance['source_name']} "
          f"{result.provenance['n_candidates']} candidates, "
          f"{result.provenance['n_points']} pts, {result.provenance['n_harmonics']} harmonics, "
          f"commit {str(result.provenance['index_git_commit'])[:8]}")
    print(format_table(result))
    for warning in result.warnings:
        print(f"note       : {warning}")
    for warning in source["warnings"]:
        print(f"extraction : {warning}")
    print(
        "timings    : "
        + "  ".join(f"{key}={value:.6f}s" for key, value in result.timings.items())
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    # The input's suffix belongs in the artifact name: drawn-crescent.json and
    # drawn-crescent.png are two different readings of one shape and must not
    # overwrite each other's dump or figure.
    label = f"{args.sketch.stem}_{args.sketch.suffix.lstrip('.').lower()}"
    payload = result.to_dict(with_contours=True)
    json_path = args.out_dir / f"query_{label}.json"
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), "utf-8")
    print(f"json       : {json_path}")
    if not args.no_figure:
        figure = args.out_dir / f"topk_{label}.png"
        topk_figure(
            result,
            figure,
            query_label=label,
            index=index,
            allow_mirror=args.allow_mirror,
            top=args.top,
        )
        print(f"figure     : {figure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
