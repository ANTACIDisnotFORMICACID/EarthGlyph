"""Loaders for the independent shape-perception benchmark (P0-4.6).

Three sources, three different kinds of ground truth, kept strictly apart:

* **H1** — 250 GAN shape pairs with a 250x14 per-observer similarity rating matrix and
  the authors' published official 22-D ShapeComp coordinates. Real human truth.
* **H2** — 15 Li et al. circular-space shapes with official KerNet2 coordinates and a
  human MDS arrangement *digitised from a published figure*. Auxiliary, not raw ratings.
* **MPEG-7** — 1400 silhouettes with object *category* labels. A retrieval gallery only;
  a category label is never treated as a human similarity judgement.

No geographic data, no coordinates, no EarthGlyph index.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io as sio
from scipy.spatial.distance import pdist

from earthglyph.geometry.sketch import contour_from_points
from earthglyph.retrieval.perceptual_methods import CONTOUR_POINTS

ZENODO_SUBDIR = "zenodo4730985"
_H1_STIM = (
    "shapecomp datashare/Figure 5 and 11A/rating_expt/stim/Fig5B_shapeStim4RatingExpt.mat"
)
_H1_RATINGS = "shapecomp datashare/Figure 5 and 11A/rating_expt/results/ShapeSimRatings.mat"
_H2_HUMAN = "shapecomp datashare/Figure 9/data/fig7D_lietal_points_2.mat"
_H2_SC = "shapecomp datashare/Figure 9/data/lietal_ShapeCompSolution_keras_mds_image.mat"
_MPEG7_ZIP = "MPEG7dataset.zip"
_MPEG7_NON_STIMULI = frozenset({"confusions.gif", "shapedata.gif"})


@dataclass(frozen=True)
class BenchmarkShape:
    """One closed outline, in both the algorithm contract and its original pose."""

    shape_id: str
    source: str
    points: np.ndarray
    """(CONTOUR_POINTS, 2) centred / unit-RMS / CCW — the shared E0/E1/E3 contract."""
    display_points: np.ndarray
    """Original published coordinates, for figures only. Never fed to a ranker."""
    category: str | None = None
    """MPEG-7 object class. NOT a human similarity label."""


@dataclass(frozen=True)
class RatedPair:
    pair_id: int
    left: str
    right: str
    official_dist: float
    ratings: np.ndarray
    """(n_observers,) raw similarity ratings; higher means more similar."""

    @property
    def mean_rating(self) -> float:
        return float(np.mean(self.ratings))


@dataclass
class H1Dataset:
    shapes: dict[str, BenchmarkShape]
    pairs: list[RatedPair]
    official_coords: dict[str, np.ndarray]
    n_observers: int
    provenance: dict[str, Any] = field(default_factory=dict)

    def pair_matrix(self) -> np.ndarray:
        return np.array([pair.ratings for pair in self.pairs], dtype=float)


@dataclass
class H2Dataset:
    shape_ids: list[str]
    shapes: dict[str, BenchmarkShape]
    official_coords: dict[str, np.ndarray]
    human_arrangement: np.ndarray
    """(n, 2) digitised published human MDS coordinates — NOT raw ratings."""
    provenance: dict[str, Any] = field(default_factory=dict)

    def human_pair_distance(self) -> np.ndarray:
        """Condensed pairwise distance in the digitised human arrangement, max-normalised."""
        distance: np.ndarray = np.asarray(pdist(self.human_arrangement), dtype=float)
        return distance / float(distance.max())

    def official_pair_distance(self) -> np.ndarray:
        coords = np.array([self.official_coords[s] for s in self.shape_ids], dtype=float)
        distance: np.ndarray = np.asarray(pdist(coords), dtype=float)
        return distance / float(distance.max())


@dataclass(frozen=True)
class ExcludedImage:
    name: str
    reason: str


def standardize(points: np.ndarray) -> np.ndarray:
    """Project a published closed polygon onto the shared (CONTOUR_POINTS, 2) contract.

    The Zenodo contours are closed polygons that MATLAB ``patch`` seals implicitly, and
    their sampling is non-uniform, so the wrap chord can be ~8 median steps long. The
    sketch loader's open-stroke guard reads that as an unclosed drawing and refuses.
    Repeating the first vertex states the published closure explicitly instead of
    guessing it, which is what ``patch`` does.
    """
    array = np.asarray(points, dtype=float)
    ring = np.vstack([array, array[:1]])
    contour = contour_from_points(
        ring,
        closed=True,
        n_points=CONTOUR_POINTS,
        smoothing="none",
    )
    return np.asarray(contour.points, dtype=float)


def _display_points(points: np.ndarray) -> np.ndarray:
    """Centre without rotating or resampling, so figures show the published pose."""
    array = np.asarray(points, dtype=float)
    return array - array.mean(axis=0)


def _mat_structs(path: Path, key: str) -> Any:
    return sio.loadmat(path, squeeze_me=True, struct_as_record=False)[key]


def load_h1(cache_dir: Path) -> H1Dataset:
    """Load the 250 rated GAN pairs, preserving the per-observer rating columns."""
    base = cache_dir / ZENODO_SUBDIR
    stim = _mat_structs(base / _H1_STIM, "rate_stim")
    ratings = np.asarray(
        sio.loadmat(base / _H1_RATINGS, squeeze_me=True)["sim_ratings"], dtype=float
    )
    if ratings.shape != (len(stim), 14):
        raise ValueError(f"expected (250, 14) ratings, got {ratings.shape}")
    if np.isnan(ratings).any():
        raise ValueError("H1 ratings contain NaN; the published matrix is complete")

    shapes: dict[str, BenchmarkShape] = {}
    coords: dict[str, np.ndarray] = {}
    pairs: list[RatedPair] = []
    for index, entry in enumerate(stim):
        sides = (("a", entry.shape1, entry.shape1SC), ("b", entry.shape2, entry.shape2SC))
        ids: list[str] = []
        for suffix, contour, sc in sides:
            shape_id = f"h1_{index}_{suffix}"
            ids.append(shape_id)
            array = np.asarray(contour, dtype=float)
            shapes[shape_id] = BenchmarkShape(
                shape_id=shape_id,
                source="H1",
                points=standardize(array),
                display_points=_display_points(array),
            )
            coords[shape_id] = np.asarray(sc, dtype=float)
        left_id, right_id = ids
        pairs.append(
            RatedPair(
                pair_id=index,
                left=left_id,
                right=right_id,
                official_dist=float(entry.dist),
                ratings=ratings[index],
            )
        )

    residual = max(
        abs(pair.official_dist - float(np.linalg.norm(coords[pair.left] - coords[pair.right])))
        for pair in pairs
    )
    return H1Dataset(
        shapes=shapes,
        pairs=pairs,
        official_coords=coords,
        n_observers=int(ratings.shape[1]),
        provenance={
            "source": "H1",
            "data_doi": "10.5281/zenodo.4730985",
            "paper_doi": "10.1371/journal.pcbi.1008981",
            "license": "CC-BY-4.0",
            "target_semantics": "continuous similarity rating, higher = more similar, 14 observers",
            "n_pairs": len(pairs),
            "n_shapes": len(shapes),
            "n_observers": int(ratings.shape[1]),
            "official_distance_identity_max_residual": residual,
        },
    )


def load_h2(cache_dir: Path) -> H2Dataset:
    """Load the 15 Li et al. shapes, pairing the two .mat files by their shared `n` id.

    The `stim` contours are not array-identical between the two files (different start
    point / resampling); they were verified to be the same shapes to 0.8% of unit RMS.
    The canonical contour comes from the human-arrangement file, which is the one the
    authors plot against the human coordinates.
    """
    base = cache_dir / ZENODO_SUBDIR
    human = sio.loadmat(base / _H2_HUMAN, squeeze_me=True, struct_as_record=False)
    sc_file = sio.loadmat(base / _H2_SC, squeeze_me=True, struct_as_record=False)
    human_stim = human["stim"]
    sc_stim = sc_file["stim"]
    sc_by_id = {
        int(entry.n): np.asarray(row, dtype=float)
        for entry, row in zip(sc_stim, sc_file["sc"], strict=True)
    }

    shape_ids: list[str] = []
    shapes: dict[str, BenchmarkShape] = {}
    coords: dict[str, np.ndarray] = {}
    arrangement: list[np.ndarray] = []
    for index, entry in enumerate(human_stim):
        number = int(entry.n)
        shape_id = f"h2_{number:03d}"
        contour = np.asarray(entry.c, dtype=float)
        shape_ids.append(shape_id)
        shapes[shape_id] = BenchmarkShape(
            shape_id=shape_id,
            source="H2",
            points=standardize(contour),
            display_points=_display_points(contour),
        )
        coords[shape_id] = sc_by_id[number]
        # Figure9.m: "y = -y ... to match with li et al."
        arrangement.append(np.array([float(human["x"][index]), -float(human["y"][index])]))

    return H2Dataset(
        shape_ids=shape_ids,
        shapes=shapes,
        official_coords=coords,
        human_arrangement=np.array(arrangement, dtype=float),
        provenance={
            "source": "H2",
            "data_doi": "10.5281/zenodo.4730985",
            "stimulus_paper_doi": "10.1037/xge0000693",
            "license": "CC-BY-4.0 (ShapeComp deposit)",
            "target_semantics": (
                "pairwise distance in the published human MDS arrangement, digitised from "
                "Li et al. Figure 7D with MATLAB getpts; NOT raw per-trial ratings"
            ),
            "n_shapes": len(shape_ids),
            "n_pairs": len(shape_ids) * (len(shape_ids) - 1) // 2,
            "shape_ids_in_full_360_space": [int(e.n) for e in human_stim],
            "official_coords_route": "KerNet2 (40x40 binary image) -> 22-D ShapeComp",
        },
    )


def load_mpeg7(cache_dir: Path) -> tuple[list[BenchmarkShape], list[ExcludedImage]]:
    """Extract the MPEG-7 CE-Shape-1 silhouettes onto the shared contour contract.

    Polarity is auto-detected by ``contour_from_image``'s border test, so the white-on-black
    GIFs need no manual inversion. Images that genuinely contain two disconnected blobs
    are excluded and reported rather than silently repaired.
    """
    from PIL import Image  # lazy: only the GIF rasteriser needs Pillow

    from earthglyph.geometry.sketch import SketchError, contour_from_image

    shapes: list[BenchmarkShape] = []
    excluded: list[ExcludedImage] = []
    with zipfile.ZipFile(cache_dir / _MPEG7_ZIP) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if name.endswith(".gif") and Path(name).name not in _MPEG7_NON_STIMULI
        )
        for name in names:
            stem = Path(name).stem
            category, _, number = stem.rpartition("-")
            array = np.array(Image.open(io.BytesIO(archive.read(name))).convert("L"))
            try:
                query = contour_from_image(array, n_points=CONTOUR_POINTS, smoothing="none")
            except SketchError as error:
                excluded.append(ExcludedImage(name=name, reason=str(error)))
                continue
            points = np.asarray(query.points, dtype=float)
            raw = np.asarray(query.raw_points, dtype=float)
            shapes.append(
                BenchmarkShape(
                    shape_id=f"mpeg7_{stem}",
                    source="MPEG7",
                    points=points,
                    display_points=_display_points(raw),
                    category=category,
                )
            )
    return shapes, excluded