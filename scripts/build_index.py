#!/usr/bin/env python
"""CLI: build the P0-3 shape-feature index from the standardized contours.

Examples
--------
python scripts/build_index.py --contours data/processed/contours.parquet \
    --out-dir data/index --force

python scripts/build_index.py --n-harmonics 24 --fold symmetric \
    --out-dir data/index_harmonics24 --force

Inputs are read-only. The index is rebuilt from ``contours.parquet`` alone, so
the raw GeoParquet never has to be re-downloaded. ``--dry-run`` computes and
verifies everything without writing files, which is what the reproducibility
check uses.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earthglyph.data.index import IndexConfig, build_index  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contours",
        type=Path,
        default=Path("data/processed/contours.parquet"),
        help="standardized contour dataset (read-only)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/index"),
        help="directory for features.npy / shapes.npy / metadata.parquet",
    )
    parser.add_argument(
        "--n-harmonics",
        type=int,
        default=16,
        help="number of Fourier harmonics in the coarse descriptor (default 16)",
    )
    parser.add_argument(
        "--fold",
        choices=("positive", "symmetric"),
        default="positive",
        help="positive: mirror/direction sensitive; symmetric: both invariant",
    )
    parser.add_argument(
        "--dtype",
        choices=("float32", "float64"),
        default="float32",
        help="storage dtype for the .npy arrays",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing index")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and verify the index without writing any file",
    )
    args = parser.parse_args(argv)

    if args.n_harmonics < 1:
        print("--n-harmonics must be >= 1", file=sys.stderr)
        return 2

    command = "python " + " ".join(
        shlex.quote(item) for item in [sys.argv[0], *(argv or sys.argv[1:])]
    )
    try:
        index = build_index(
            args.contours,
            args.out_dir,
            config=IndexConfig(
                n_harmonics=args.n_harmonics,
                fold=args.fold,  # type: ignore[arg-type]
                dtype=args.dtype,
            ),
            force=args.force,
            build_command=command,
            write_files=not args.dry_run,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"index build failed: {exc}", file=sys.stderr)
        return 1

    manifest = index.manifest
    counts = manifest["counts"]
    print(f"OK  contours={manifest['source']['contours_path']}")
    print(
        f"    release={manifest['source']['source_release']}  "
        f"candidates={counts['indexed_candidates']}  skipped={counts['skipped_candidates']}"
    )
    print(
        f"    feature_dim={manifest['index']['feature_dim']} "
        f"(fourier {index.groups['fourier']['width']} + "
        f"geometry {index.groups['geometry']['width']})  "
        f"n_points={manifest['index']['n_points']}  dtype={args.dtype}"
    )
    print(f"    feature_seconds={index.feature_seconds:.3f}")
    if not args.dry_run:
        output = manifest["output"]
        for name in ("features", "shapes", "metadata"):
            print(
                f"    {name}: {output[name]['path']} "
                f"({output[name]['size_bytes']} bytes) sha256={output[name]['sha256'][:16]}..."
            )
        print(f"    manifest={manifest['manifest_path']}")
    else:
        print("    (dry run - nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
