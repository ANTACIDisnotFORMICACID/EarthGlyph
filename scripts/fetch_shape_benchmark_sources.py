"""Acquire the P0-4.6 shape-perception benchmark sources.

Pulls only the ZIP members we actually use from the 308 MB ShapeComp Zenodo deposit
(over HTTP Range, ~6 MB transferred) plus the 3.4 MB MPEG-7 CE-Shape-1 archive, and
writes a manifest of checksums and counts. Raw downloads land in the gitignored
``cache/`` directory; nothing here touches geographic data.

Usage:
    python scripts/fetch_shape_benchmark_sources.py [--cache-dir PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from earthglyph.shape_benchmark.remote_zip import USER_AGENT, RemoteZip

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / "experiments" / "shape_perception_benchmark" / "cache"

ZENODO_RECORD = "4730985"
ZENODO_URL = (
    f"https://zenodo.org/api/records/{ZENODO_RECORD}/files/shapecomp%20datashare.zip/content"
)
ZENODO_EXPECTED_BYTES = 307965089
ZENODO_EXPECTED_MD5 = "c084b94e6ac2defe74542b36c7aae5fe"

MPEG7_URL = "https://dabi.temple.edu/external/shape/MPEG7/MPEG7dataset.zip"
MPEG7_EXPECTED_BYTES = 3380427

# Members needed for H1 (250 rated GAN pairs) and H2 (Li et al. circular shape subset).
ZENODO_MEMBERS = [
    "shapecomp datashare/Figure 5 and 11A/rating_expt/stim/Fig5B_shapeStim4RatingExpt.mat",
    "shapecomp datashare/Figure 5 and 11A/rating_expt/results/ShapeSimRatings.mat",
    "shapecomp datashare/Figure 5 and 11A/view_rating_expt_stim_and_resp.m",
    "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat",
    "shapecomp datashare/Figure 9/data/fig7D_lietal_points_2.mat",
    "shapecomp datashare/Figure 9/Figure9.m",
    "shapecomp datashare/Figure 3/data/GAN/GANratings.mat",
    "shapecomp datashare/Figure 3/data/GAN/GANlabels.mat",
    # Figure 8D: the authors' published MatNet/KerNet1/KerNet2 outputs on 1000 held-out
    # GAN shapes, together with the shapes themselves. Model-vs-model only, no ratings.
    "shapecomp datashare/Figure 8/Figure8.m",
    "shapecomp datashare/Figure 8/data/Figure8NetComparison.mat",
    "shapecomp datashare/README.txt",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_zenodo(cache: Path) -> dict[str, Any]:
    destination = cache / f"zenodo{ZENODO_RECORD}"
    members: dict[str, Any] = {}
    with RemoteZip(ZENODO_URL) as archive:
        if archive.archive_size != ZENODO_EXPECTED_BYTES:
            raise RuntimeError(
                f"Zenodo archive size changed: {archive.archive_size} != {ZENODO_EXPECTED_BYTES}"
            )
        for name in ZENODO_MEMBERS:
            path = archive.extract_to(name, destination)
            members[name] = {
                "local_path": str(path.relative_to(REPO)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        transfer = archive.stats()
    return {
        "record": ZENODO_RECORD,
        "doi": "10.5281/zenodo.4730985",
        "url": ZENODO_URL,
        "archive_bytes": ZENODO_EXPECTED_BYTES,
        "archive_md5_published": ZENODO_EXPECTED_MD5,
        "license": "CC-BY-4.0",
        "transfer": transfer,
        "members": members,
    }


def fetch_mpeg7(cache: Path) -> dict[str, Any]:
    path = cache / "MPEG7dataset.zip"
    if not path.exists():
        request = urllib.request.Request(MPEG7_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=180) as response:
            path.write_bytes(response.read())
    if path.stat().st_size != MPEG7_EXPECTED_BYTES:
        raise RuntimeError(f"MPEG-7 archive size changed: {path.stat().st_size}")
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".gif")]
        # confusions.gif / shapedata.gif are documentation figures, not stimuli.
        stimuli = [n for n in names if "-" in Path(n).name]
        classes = Counter(Path(n).name.rsplit("-", 1)[0] for n in stimuli)
    return {
        "url": MPEG7_URL,
        "local_path": str(path.relative_to(REPO)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "stimulus_count": len(stimuli),
        "class_count": len(classes),
        "per_class_counts": sorted(set(classes.values())),
        "classes": dict(sorted(classes.items())),
        "excluded_non_stimuli": sorted(set(names) - set(stimuli)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    cache: Path = args.cache_dir
    cache.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "earthglyph.shape_perception_benchmark.sources/1",
        "h1_h2": fetch_zenodo(cache),
        "mpeg7": fetch_mpeg7(cache),
    }
    out = cache.parent / "dataset_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)}")
    print(f"  zenodo transfer: {manifest['h1_h2']['transfer']}")
    print(
        f"  mpeg7: {manifest['mpeg7']['stimulus_count']} stimuli, "
        f"{manifest['mpeg7']['class_count']} classes"
    )


if __name__ == "__main__":
    main()