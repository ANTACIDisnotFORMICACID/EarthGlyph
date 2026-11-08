"""Record SHA256 + version + dependency facts for the manually downloaded ShapeComp toolbox.

The toolbox is gitignored and is NOT redistributed by this repository: the authors' page only
asks for citation and carries no formal redistribution licence, so the paper's CC BY licence
does not license the ``.h5`` weights for re-publication. What IS committed is this manifest —
every file the port reads, hashed, so a later run can prove it loaded the same weights.

Usage:
    python scripts/hash_shapecomp_toolbox.py [--toolbox PATH] [--out PATH] [--all-files]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import platform
from pathlib import Path
from typing import Any

import h5py
import numpy as np

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TOOLBOX = REPO / "shapeComp_17_12_2024"
DEFAULT_OUT = REPO / "experiments" / "shape_perception_benchmark" / "results" / (
    "official_toolbox_hashes.json"
)

#: The official MATLAB the port was translated from, plus the geom2d helpers it inlines.
README_FILES = ("ShapeComp Readme.rtf", "loadShapeComp.m")
TRANSLATED_SOURCES = (
    "ShapeComp_src/getShapeComp22netkeras.m",
    "ShapeComp_src/getShapeComp22netimagekeras.m",
    "ShapeComp_src/getShapeComp22.m",
    "ShapeComp_src/adjustContourPos.m",
    "ShapeComp_src/ShapeComp2contour.m",
    "ShapeComp_src/GANinput2shape.m",
    "misc/clockwise.m",
    "misc/geom2d/geom2d/centroid.m",
    "misc/geom2d/polygons2d/resamplePolygon.m",
    "misc/geom2d/polygons2d/resamplePolyline.m",
    "misc/geom2d/polygons2d/parametrize.m",
)
#: Everything under net/ that the toolbox ships, weights included (hashes only, never copied).
WEIGHTS = (
    "net/keras/shape2sc22a.h5",
    "net/keras/shape2sc22a.json",
    "net/keras/im2sc22d.h5",
    "net/keras/im2sc22d.json",
    "net/keras/sc40to25GANc.h5",
    "net/keras/sc40to25GANc.json",
    "net/keras/KerNet1.mat",
    "net/keras/KerNet2.mat",
    "net/keras/netkeras.mat",
    "net/matlab/net_shape2mds_matlab.mat",
    "net/gan/net-epoch-106-MATLAB_DL.mat",
)
OFFICIAL_PAGES = {
    "tool_page": "https://sites.google.com/view/yanivmorgenstern/home/tools/shapecomp",
    "toolbox_2024_drive": "https://drive.google.com/file/d/1CfIo-uzqz1RRk6zEibVEDN4_Vu9liQz8/view",
    "toolbox_2021_dropbox": (
        "https://www.dropbox.com/scl/fi/gdqt4i1ugsj0qs8i1gzix/"
        "shapeComp_31_05_2021.zip?rlkey=239gmqds2kjgaynoe0ibbw8ew&dl=0"
    ),
    "paper": "https://doi.org/10.1371/journal.pcbi.1008981",
    "data_doi": "10.5281/zenodo.4730985",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(toolbox: Path, rel: str) -> dict[str, Any]:
    path = toolbox / rel
    if not path.exists():
        return {"path": rel, "missing": True}
    return {
        "path": rel,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "modified_utc": datetime.datetime.fromtimestamp(
            path.stat().st_mtime, tz=datetime.UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _attr(handle: h5py.File, key: str) -> str:
    value = handle.attrs.get(key, "")
    return value.decode() if isinstance(value, bytes) else str(value)


def _h5_summary(toolbox: Path, rel: str) -> dict[str, Any]:
    """Layer names, shapes and dtype read straight from the Keras HDF5 — no TensorFlow.

    Keras 2.1.2 stores each layer's tensors at ``model_weights/<layer>/<layer>/<name>:0`` and
    the model metadata on the ``model_weights`` group. Only the weighted layers have that
    inner group, so the Dropout / MaxPooling / Flatten entries are reported as weight-free.
    """
    out: dict[str, Any] = {}
    with h5py.File(toolbox / rel, "r") as handle:
        weights = handle["model_weights"]
        out["keras_version"] = _attr(weights, "keras_version")
        out["backend"] = _attr(weights, "backend")
        layers: dict[str, Any] = {}
        for name in (value.decode() for value in weights.attrs["layer_names"]):
            node = weights[name]
            if not isinstance(node, h5py.Group) or name not in node:
                layers[name] = []
                continue
            layers[name] = [
                f"{key.removesuffix(':0')}{dataset.shape}:{dataset.dtype}"
                for key, dataset in node[name].items()
            ]
        out["layers"] = layers
        out["weighted_layers"] = [n for n, t in layers.items() if t]
        out["weight_free_layers"] = [n for n, t in layers.items() if not t]
    return out


def build(toolbox: Path, all_files: bool = False) -> dict[str, Any]:
    if not toolbox.exists():
        raise FileNotFoundError(f"toolbox not found: {toolbox}")
    files = [_entry(toolbox, rel) for rel in README_FILES + TRANSLATED_SOURCES + WEIGHTS]
    if all_files:
        listed = {entry["path"] for entry in files}
        files = [
            *files,
            *(
                _entry(toolbox, str(path.relative_to(toolbox)).replace("\\", "/"))
                for path in sorted(toolbox.rglob("*"))
                if path.is_file()
                and str(path.relative_to(toolbox)).replace("\\", "/") not in listed
                and "__pycache__" not in path.parts
            ),
        ]
    m = list(toolbox.rglob("*.m"))
    return {
        "schema": "earthglyph.shape_perception_benchmark.official_toolbox_hashes/1",
        "toolbox_directory": toolbox.name,
        "obtained_by": (
            "manual download by the user from the authors' page; "
            "no script in this repository fetches the toolbox"
        ),
        "redistribution": (
            "NOT committed. The .h5 weights and the official MATLAB sources stay outside the "
            "repository (gitignored). The paper's CC BY-4.0 licence covers the article and the "
            "Zenodo data deposit, not re-publication of the toolbox binaries."
        ),
        "official_pages": OFFICIAL_PAGES,
        "citation": (
            "Morgenstern, Y., Schmidt, F., Hartmann, F., Tiedemann, H., Prokott, E., "
            "Maiello, G., & Fleming, R. (2021). An image computable model of human visual "
            "shape similarity. PLoS Computational Biology. 10.1371/journal.pcbi.1008981"
        ),
        "geom2d_licence": "BSD-3 (INRA, D. Legland) as shipped under misc/geom2d",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy_version": np.__version__,
            "h5py_version": h5py.__version__,
            "tensorflow_used": False,
            "matlab_used": False,
            "note": (
                "The port reads the Keras HDF5 weights with h5py and runs a numpy forward pass. "
                "MATLAB and TensorFlow are not required at run time; the .mat network exports "
                "are MATLAB v7 classdef handles that scipy cannot decode, so they are hashed "
                "for provenance but never parsed."
            ),
        },
        "directory_inventory": {
            "total_files": sum(1 for p in toolbox.rglob("*") if p.is_file()),
            "matlab_files": len(m),
            "h5_files": len(list(toolbox.rglob("*.h5"))),
        },
        "files": files,
        "keras_layer_inventory": {
            rel: _h5_summary(toolbox, rel)
            for rel in ("net/keras/shape2sc22a.h5", "net/keras/im2sc22d.h5")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolbox", type=Path, default=DEFAULT_TOOLBOX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="hash every file in the toolbox, not only the ones the port translates or loads",
    )
    args = parser.parse_args()
    payload = build(args.toolbox, all_files=args.all_files)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out.relative_to(REPO)}")
    for entry in payload["files"]:
        if entry.get("missing"):
            print(f"  MISSING {entry['path']}")
    for rel, summary in payload["keras_layer_inventory"].items():
        print(
            f"  {rel}: keras {summary['keras_version']}, "
            f"{len(summary['weighted_layers'])} weighted / "
            f"{len(summary['weight_free_layers'])} weight-free layers, "
            f"backend {summary['backend']}"
        )
    print(
        f"  toolbox {payload['directory_inventory']['total_files']} files, "
        f"{payload['directory_inventory']['matlab_files']} .m, "
        f"{payload['directory_inventory']['h5_files']} .h5"
    )


if __name__ == "__main__":
    main()
