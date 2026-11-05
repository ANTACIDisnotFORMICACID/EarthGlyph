#!/usr/bin/env python
"""CLI: build the frozen controlled gallery for the P0-4.5 perceptual experiment.

The gallery answers a question the 839-candidate landform index cannot: when a
retrieval method returns a circle for a hand-drawn star, is the algorithm wrong
or is the library empty?  It is a *control set*, not geography, and it never
enters ``data/index/``.

Examples
--------
python scripts/prepare_perceptual_dataset.py            # full build (network)
python scripts/prepare_perceptual_dataset.py --offline   # rebuild from the cache
python scripts/prepare_perceptual_dataset.py --no-quickdraw --out-dir /tmp/probe

Outputs under --out-dir:
  gallery.jsonl          one record per shape: raw outline, 128-point contour,
                         metrics, licence, provenance
  labels.json            the frozen query/relevance/split table (written before
                         any retrieval method looks at it)
  dataset_manifest.json  sources, licences, byte ranges, sha256, counts, refusal
                         census, build command, environment, git commit
  rejections.csv         counted refusals (capped sample + complete per-reason
                         counts)
  queries/<id>.json      the role=query items, in the same JSON the P0-4 query
                         loader reads
  control_gallery.png    every query with its variants and hard negatives, for
                         human sanity-checking of the design itself
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from visualize_perceptual import control_gallery_figure  # noqa: E402

from earthglyph.data.perceptual_gallery import (  # noqa: E402
    FAMILIES,
    GALLERY_VERSION,
    PHOSPHOR,
    QUICKDRAW,
    SYNTHETIC,
    GalleryItem,
    Rejection,
    build_gallery,
)

UNIT_BY_ORIGIN = {
    "synthetic": "generator units (dimensionless, RMS-normalised later)",
    "phosphor": "SVG viewBox units, y flipped to y-up",
    "quickdraw": "Quick Draw 256 px canvas, y flipped to y-up",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the frozen P0-4.5 controlled perceptual gallery."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/p0_perceptual_probe"),
        help="where gallery.jsonl / labels.json / manifest / figures go",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="download cache (default: <out-dir>/cache, never committed)",
    )
    parser.add_argument("--offline", action="store_true", help="use the cache only")
    parser.add_argument("--no-phosphor", action="store_true", help="skip Phosphor icons")
    parser.add_argument("--no-quickdraw", action="store_true", help="skip Quick Draw")
    parser.add_argument(
        "--near-max-align",
        type=float,
        default=0.02,
        help="align distance treated as a near-duplicate (data hygiene only)",
    )
    parser.add_argument(
        "--max-rejection-rows",
        type=int,
        default=300,
        help="cap on individual refusal rows written to rejections.csv per reason "
        "(the per-reason counts stay complete)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing gallery")
    parser.add_argument(
        "--no-figure", action="store_true", help="skip control_gallery.png (debug only)"
    )
    return parser.parse_args(argv)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - provenance is best effort, never fatal
        return None
    return result.stdout.strip() or None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_gallery(items: list[GalleryItem], path: Path) -> None:
    text = "\n".join(
        json.dumps(item.as_record(), ensure_ascii=False, separators=(",", ":"))
        for item in items
    )
    path.write_text(text + "\n", encoding="utf-8")


QUERY_MARKER = "scripts/prepare_perceptual_dataset.py"


def write_queries(items: list[GalleryItem], directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for item in items:
        if item.role != "query":
            continue
        payload = {
            "item_id": item.item_id,
            "family": item.family,
            "role": item.role,
            "origin": item.origin,
            "origin_ref": item.origin_ref,
            "license": item.license,
            "points": [
                [round(float(x), 3), round(float(y), 3)] for x, y in item.points
            ],
            "closed": True,
            "unit": UNIT_BY_ORIGIN[item.origin],
            "n_raw_points": int(item.points.shape[0]),
            "note": item.note,
            "tool": QUERY_MARKER,
            "gallery_version": GALLERY_VERSION,
        }
        (directory / f"{item.item_id}.json").write_text(
            json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        written.append(
            {
                "item_id": item.item_id,
                "family": item.family,
                "origin": item.origin,
                "file": f"{item.item_id}.json",
                "n_points": int(item.points.shape[0]),
            }
        )
    stale = prune_stale_queries(directory, {entry["item_id"] for entry in written})
    for name in stale:
        print(f"  pruned stale query file (its item is no longer role=query): {name}")
    return written


def prune_stale_queries(directory: Path, current_ids: set[str]) -> list[str]:
    """Delete query JSONs this builder wrote for items the audit later demoted.

    Without this, a rebuild after a role revision leaves a phantom query on disk
    and Checkpoint C would retrieve with a query the frozen labels no longer list.
    Only files carrying this tool's marker are touched; anything else is left alone.
    """
    pruned: list[str] = []
    for path in sorted(directory.glob("*.json")):
        if path.stem in current_ids:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or record.get("tool") != QUERY_MARKER:
            continue
        path.unlink()
        pruned.append(path.name)
    return pruned


def collect_assets(items: list[GalleryItem], census: dict[str, Any]) -> dict[str, Any]:
    """Every external byte that entered the gallery, with hash and licence."""
    phosphor: dict[str, Any] = {}
    for item in items:
        if item.origin != "phosphor":
            continue
        record = item.provenance
        entry = phosphor.setdefault(
            str(record["url"]),
            {
                "url": record["url"],
                "sha256": record["sha256"],
                "bytes": record["bytes"],
                "icon": record["icon"],
                "weight": record["weight"],
                "view_box": record["view_box"],
                "subpaths": record["subpaths"],
                "exterior_rings": record["exterior_rings"],
                "holes": record["holes"],
                "license": record["license"],
                "attribution": record["attribution"],
                "used_by": [],
            },
        )
        entry["used_by"].append(item.item_id)
    quickdraw: dict[str, Any] = {}
    for category, bundle in sorted(census.items()):
        for asset in bundle["assets"]:
            quickdraw[asset["sha256"][:16] + "-" + category] = {
                "url": asset["url"],
                "category": category,
                "byte_range": asset["byte_range"],
                "sha256": asset["sha256"],
                "bytes": asset["bytes"],
                "slice_index": asset["slice_index"],
                "license": QUICKDRAW["license"],
                "attribution": QUICKDRAW["attribution"],
                "record_count_in_slice": bundle["census"]["records"]
                if asset["slice_index"] == 0
                else None,
            }
    return {
        "phosphor_files": sorted(phosphor.values(), key=lambda row: str(row["url"])),
        "quickdraw_slices": sorted(
            quickdraw.values(), key=lambda row: (str(row["category"]), row["slice_index"])
        ),
        "counts": {
            "phosphor_files": len(phosphor),
            "quickdraw_slices": len(quickdraw),
            "quickdraw_bytes": sum(
                int(row["bytes"] or 0) for row in quickdraw.values()
            ),
            "phosphor_bytes": sum(int(row["bytes"] or 0) for row in phosphor.values()),
        },
    }


def write_rejections(
    rejections: list[Rejection], path: Path, *, cap: int
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        key = f"{rejection.source}/{rejection.reason}"
        counts[key] = counts.get(key, 0) + 1
    sampled: list[Rejection] = []
    seen: dict[str, int] = {}
    for rejection in rejections:
        key = f"{rejection.source}/{rejection.reason}"
        if seen.get(key, 0) >= cap:
            continue
        seen[key] = seen.get(key, 0) + 1
        sampled.append(rejection)
    header = ["source", "reference", "reason", "detail"]
    lines = [",".join(header)]
    for rejection in sampled:
        record = rejection.as_dict()
        lines.append(
            ",".join(
                '"' + str(record[key]).replace('"', '""').replace("\n", " ") + '"'
                for key in header
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "total_refusals": len(rejections),
        "by_reason": dict(sorted(counts.items())),
        "rows_written": len(sampled),
        "rows_capped_at_per_reason": cap,
        "note": (
            "by_reason is the complete count; rejections.csv holds a deterministic "
            "prefix sample per reason so the file stays reviewable"
        ),
        "file": path.name,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir: Path = args.out_dir
    cache_dir: Path = args.cache_dir or (out_dir / "cache")
    if not out_dir.exists():
        raise SystemExit(f"{out_dir} does not exist — run from the repository root")
    gallery_path = out_dir / "gallery.jsonl"
    if gallery_path.exists() and not args.force:
        raise SystemExit(f"{gallery_path} exists; pass --force to rebuild over it")

    built = build_gallery(
        cache_dir=cache_dir,
        include_phosphor=not args.no_phosphor,
        include_quickdraw=not args.no_quickdraw,
        offline=args.offline,
        near_max_align=args.near_max_align,
    )
    items = built["items"]
    if not items:
        raise SystemExit("gallery is empty — nothing was accepted; not writing outputs")

    write_gallery(items, gallery_path)
    (out_dir / "labels.json").write_text(
        json.dumps(built["labels"], indent=1, ensure_ascii=False), encoding="utf-8"
    )
    queries = write_queries(items, out_dir / "queries")
    rejections = write_rejections(
        built["rejections"], out_dir / "rejections.csv", cap=args.max_rejection_rows
    )
    assets = collect_assets(items, built["quickdraw_census"])

    figure = None
    if not args.no_figure:
        figure = control_gallery_figure(items, out_dir / "control_gallery.png")

    manifest = {
        "gallery_version": GALLERY_VERSION,
        "built_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "purpose": (
            "controlled shape-retrieval gallery for P0-4.5: targets, independent "
            "variants and hard negatives in seven structure families. Not "
            "geographic data; must never be merged into data/index/."
        ),
        "label": "Controlled Shape Retrieval — 非真实地理景观",
        "families": list(FAMILIES),
        "counts": built["counts"],
        "totals": {
            "items": len(items),
            "queries": len(queries),
            "by_origin": _by_origin(items),
            "by_role": _by_role(items),
        },
        "content_sha256": {
            gallery_path.name: _sha256_file(gallery_path),
            "labels.json": _sha256_file(out_dir / "labels.json"),
        },
        "standardization": {
            "n_points": 128,
            "smoothing": "none",
            "path": "earthglyph.geometry.sketch.contour_from_points(closed=True)",
            "note": (
                "gallery members enter through the same extractor as a user query, "
                "so a query and a candidate are comparable numbers rather than two "
                "different pipelines"
            ),
        },
        "sources": {
            "phosphor": PHOSPHOR,
            "quickdraw": QUICKDRAW,
            "synthetic": SYNTHETIC,
        },
        "assets": assets,
        "rejections": rejections,
        "role_audit": {
            "rule": (
                "for a family whose defining structure is a concavity (star, "
                "crescent, heart, forked, band) the measured convexity deficiency "
                "(1 - solidity) must agree with the role the plan wrote: a *query* "
                "below the no-concavity floor becomes a hard negative, and a "
                "*variant* must keep a quarter of the deficiency of the mildest "
                "query left in its family. Every demotion is recorded here and on "
                "the item, because a convex disk listed as relevant to a crescent "
                "query would score the wrong behaviour"
            ),
            "revisions": built["role_revisions"],
            "revision_count": len(built["role_revisions"]),
            "metric": "concave_deficiency(contour) = 1 - area/convex_hull.area",
            "rejected_metrics": {
                "sharpest concave turning vertex": (
                    "a thin crescent's horns meet at a shallow angle spread over "
                    "many samples, so its peak turn is near zero"
                ),
                "total inward turning": (
                    "pen jitter on a drawn circle accumulates to about the same "
                    "radians as a real crescent's bay"
                ),
            },
        },
        "design_coverage": built["coverage"],
        "quickdraw_census": {
            category: {"census": bundle["census"], "url": bundle["url"],
                       "byte_ranges": bundle["byte_ranges"],
                       "span_source": bundle["span_source"],
                       "object_bytes": bundle["object_bytes"],
                       "selected": bundle["selected"]}
            for category, bundle in sorted(built["quickdraw_census"].items())
        },
        "near_duplicate_control": {
            "rule": (
                "pairs whose E0 align distance is <= the threshold are recorded as "
                "near-duplicates and excluded from T2 (and from the relevant set), "
                "so no method can score by returning a rigid copy of the query"
            ),
            "threshold": args.near_max_align,
            "pairs": built["labels"]["near_duplicate_pairs"],
            "pair_count": len(built["labels"]["near_duplicate_pairs"]),
            "warning": (
                "these pairs are data hygiene for the controlled gallery only; the "
                "839 real landform candidates are untouched and this is not a "
                "geographic dedupe rule"
            ),
        },
        "matched_envelope_control": matched_envelope_report(items),
        "settings": {
            "T1": "exact target present in the library (sanity check on implementation)",
            "T2": "target's rigid copies and near-duplicates removed; variants and "
                  "hard negatives kept (the discriminative setting)",
            "T3": "gallery shapes mixed into the real 839-candidate landform index, "
                  "every candidate labelled with its provenance",
        },
        "reproduce": {
            "command": _command(args),
            "cache_dir": cache_dir.as_posix(),
            "git_commit": _git_commit(),
            "determinism": (
                "synthetic generators are seeded through their parameters; external "
                "drawings are chosen even-spaced out of a record list whose byte "
                "ranges are pinned to cache/quickdraw/spans.json on the first live "
                "build and replayed by every later build, --offline included, so no "
                "run can silently scan a different set of drawings. The pinned "
                "ranges and each slice's sha256 are recorded under assets and "
                "quickdraw_census; the byte-identical live/offline check is logged "
                "in experiments/experiment_log.md"
            ),
        },
    }
    (out_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    print(f"gallery: {len(items)} items, {len(queries)} queries")
    for family, roles in sorted(built["counts"].items()):
        print(f"  {family:10s} " + "  ".join(f"{role}={roles[role]}" for role in sorted(roles)))
    print(f"refusals: {rejections['total_refusals']}")
    for key, value in sorted(rejections["by_reason"].items()):
        print(f"  {key:34s} {value}")
    print(f"role revisions from the structural audit: {len(built['role_revisions'])}")
    for revision in built["role_revisions"]:
        print(f"  {revision['item_id']:26s} {revision['stage']:6s} "
              f"{revision['from']} -> {revision['to']} "
              f"(deficiency {revision['concave_deficiency']:.4f} "
              f"< floor {revision['threshold']:.4f})")
    coverage = built["coverage"]
    print(f"design coverage: {coverage['classes']} classes, "
          f"within the brief's ranges: {coverage['within_brief']}")
    for note in coverage["over_range"]:
        print(f"  over ceiling: {note}")
    envelope = manifest["matched_envelope_control"]
    print(f"matched-envelope negatives: {envelope['matched_count']}/{envelope['count']} "
          f"reached their target solidity")
    for note in envelope["unmatched"]:
        print(f"  partial match: {note}")
    print(f"wrote {gallery_path}")
    if figure:
        print(f"wrote {figure['path']} ({figure['rows']} rows x {figure['columns']} columns)")
    return 0


def matched_envelope_report(items: list[GalleryItem]) -> dict[str, Any]:
    """Measure every "matched envelope" negative against the solidity it claims.

    A hard negative is only interesting if the claim in its name holds, and the
    bisection that builds it can saturate: a radial lobe shape stays star-shaped
    about its centre, so it cannot hollow out to a crescent's 0.56. Each row
    therefore reports the target, the achieved solidity and the residual, and the
    summary says in one number how many of them really matched.
    """
    rows: list[dict[str, Any]] = []
    for item in items:
        parameters = item.provenance.get("parameters") or {}
        if "target_solidity" not in parameters:
            continue
        target = float(parameters["target_solidity"])
        achieved = float(item.metrics["solidity"])
        rows.append(
            {
                "item_id": item.item_id,
                "family": item.family,
                "generator": item.provenance.get("generator"),
                "target_solidity": round(target, 6),
                "achieved_solidity": round(achieved, 6),
                "residual": round(abs(achieved - target), 6),
                "matched": bool(abs(achieved - target) <= 0.005),
            }
        )
    rows.sort(key=lambda row: row["item_id"])
    return {
        "rule": (
            "an envelope-matched negative is built by bisection on one parameter "
            "and the achieved solidity is re-measured from the stored contour; "
            "|residual| <= 0.005 counts as matched"
        ),
        "rows": rows,
        "count": len(rows),
        "matched_count": sum(1 for row in rows if row["matched"]),
        "unmatched": [
            f"{row['item_id']}: {row['residual']:.3f}" for row in rows if not row["matched"]
        ],
    }


def _by_origin(items: list[GalleryItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item.origin] = out.get(item.origin, 0) + 1
    return dict(sorted(out.items()))


def _by_role(items: list[GalleryItem]) -> dict[str, int]:
    out = {"query": 0, "variant": 0, "hard_negative": 0}
    for item in items:
        out[item.role] += 1
    return out


def _command(args: argparse.Namespace) -> str:
    parts = ["python scripts/prepare_perceptual_dataset.py"]
    if args.offline:
        parts.append("--offline")
    if args.no_phosphor:
        parts.append("--no-phosphor")
    if args.no_quickdraw:
        parts.append("--no-quickdraw")
    if args.force:
        parts.append("--force")
    if str(args.out_dir) != "experiments/p0_perceptual_probe":
        parts.append(f"--out-dir {shlex.quote(str(args.out_dir))}")
    if args.near_max_align != 0.02:
        parts.append(f"--near-max-align {args.near_max_align}")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
