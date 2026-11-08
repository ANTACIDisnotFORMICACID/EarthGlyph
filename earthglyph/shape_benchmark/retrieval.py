"""Unified, geography-free shape retrieval over the EarthGlyph arms plus official outputs.

Every method scores the *same* candidate set. E0/E1/E3 share one preprocessing contract
(128-point centred/unit-RMS/CCW rings). The official ShapeComp arms come in two kinds:

* **S0** — the authors' *published* 22-D coordinates. No preprocessing, but it can only score
  shapes whose coordinates were published (H1/H2 stimuli), never MPEG-7 or a new drawing.
* **S0K1 / S0K2** — the official KerNet1/KerNet2 weights *actually run* locally through the
  numpy port (``shapecomp_port``). These feed the network the ORIGINAL contour
  (``display_points``) under the official input contract, deliberately NOT EarthGlyph's
  128-point contract, and can score any shape including MPEG-7. The official networks are
  NOT rotation-invariant, so the raw arm is not either.
* **S0K1 / S0K2 with ``rotation_search=True``** — the same official weights wrapped in a
  query-side rotation-grid search, ``d(q, g) = min_theta ||net(R_theta q) - net(g)||``, over
  the same 360/``rotation_steps`` grid E3's ``orientation="sample"`` uses. The gallery is
  scored once in its stored pose; only the query contour is turned through the grid before
  the network call. This ADDS rotation robustness the official network lacks without touching
  ``kernet1_input``/``kernet2_input``, so the official input contract is preserved exactly.
* **S0K1 / S0K2 with ``rotation_strategy``** — that wrapper generalised to the alternatives
  measured in Checkpoint 7-C (finer grid, bilateral canonicalisation, bilateral multi-direction
  min). The strategies live in :mod:`earthglyph.shape_benchmark.rotation_methods`; ``"R0"``
  names the grid search above and must give the same number as ``rotation_search=True``.

``allow_mirror`` is a real branch in E0/E1/E3 and is **refused** for S0/S0K1/S0K2
(:data:`MIRROR_AWARE_METHODS`). The official arms have no reflected path — the published
coordinates exist only in the authors' own pose and a mirrored KerNet score would need a second
forward pass that has never been evaluated — so they raise rather than accept a switch that would
change nothing.

That asymmetry is reported, never hidden. Loading the weights is lazy and optional (h5py),
so E0/E1/E3 still run without the ``shapecomp`` extra installed.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from earthglyph.retrieval import perceptual_features as e1
from earthglyph.retrieval import perceptual_methods as pm
from earthglyph.retrieval import shape_context_baseline as e3
from earthglyph.shape_benchmark.datasets import BenchmarkShape
from earthglyph.shape_benchmark.rotation_methods import (
    Strategy,
    canonical_frame,
    describe,
    gallery_distances,
    pose_embedding,
    pose_pair_distance,
    resolve_strategy,
)

if TYPE_CHECKING:
    from earthglyph.shape_benchmark.shapecomp_port import ShapeCompNets

EARTHGLYPH_METHODS = ("E0", "E1", "E3")
SHAPECOMP_RUN_METHODS = ("S0K1", "S0K2")
ALL_METHODS = ("E0", "E1", "E3", "S0", "S0K1", "S0K2")
_RUN_NETWORK = {"S0K1": "kernet1", "S0K2": "kernet2"}

#: The arms whose code actually branches on ``allow_mirror``.
#:
#: ``P0-4.6 Checkpoint 7-D, item F2``. E0 reflects through ``alignment.align``'s explicit mirrored
#: branch, E1 re-extracts the library mirrored and keeps the smaller distance, E3 has its own
#: reflected-descriptor branch. The official arms have no such branch: S0 reads the authors'
#: published 22-D coordinates, which exist only for the shape as the authors drew it, and
#: S0K1/S0K2 run one forward pass of the contour in its stored pose. Adding a mirrored forward
#: pass would be a NEW capability of the port — as un-evaluated as the rotation wrapper was before
#: Checkpoint 7-C — so it is not slipped in behind a boolean. Rather than accept the switch and
#: ignore it, which is the failure mode the checkpoint names ("a parameter that looks effective
#: but does nothing"), these arms refuse it.
#:
#: E1's branch had a second, quieter failure of the same kind, found while testing this: with a
#: self-fitted scale, a corpus column whose whole range is floating-point residue was still divided
#: by that residue, so the mirrored descriptor of the query's OWN reflection came out ~8 units away
#: instead of ~0 and the switch could never win. :func:`fit_normalizer` now treats a numerically
#: constant column as constant.
MIRROR_AWARE_METHODS = EARTHGLYPH_METHODS


def _require_mirror_support(method: str, allow_mirror: bool) -> None:
    """Refuse ``allow_mirror`` on an arm that would silently ignore it."""
    if not allow_mirror or method in MIRROR_AWARE_METHODS:
        return
    raise ValueError(
        f"allow_mirror=True is not implemented for {method!r}: it would be accepted and "
        f"ignored. Mirror search exists only for {MIRROR_AWARE_METHODS} (D33). For "
        f"'S0' the published 22-D coordinates describe the shape in the authors' own pose, and "
        f"for 'S0K1'/'S0K2' a mirrored score would need a second forward pass through the "
        f"official network — a new capability that has to be implemented and evaluated as its "
        f"own arm, not hidden behind a flag."
    )

#: Rotation-grid resolution for the ``rotation_search`` S0K wrapper, inherited from E3 so the
#: "same rotation condition" comparison searches the identical grid.
KERNET_ROTATION_STEPS = e3.ROTATION_STEPS

_SHAPECOMP_NETS: ShapeCompNets | None = None


def _rotate_contour(points: np.ndarray, angle_deg: float) -> np.ndarray:
    """Turn a raw contour counter-clockwise by ``angle_deg`` (E3's rotation convention)."""
    theta = np.deg2rad(float(angle_deg))
    matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return np.asarray(points @ matrix.T, dtype=float)


def kernet_rotation_frames(
    network: str, contour: np.ndarray, steps: int = KERNET_ROTATION_STEPS
) -> np.ndarray:
    """Run the official network on one contour turned through the rotation grid, (steps, 22).

    Row ``i`` is ``net(R_angles[i] contour)`` with ``R_0`` first, so row 0 is the plain
    no-search score. Precomputing the frames lets a whole sweep reuse them instead of
    re-running the network for every pair; the query-side search in ``search_shape`` and
    ``pair_distance`` is exactly a min over these rows.
    """
    nets = _load_shapecomp_nets()
    fn = nets.kernet1 if network == "kernet1" else nets.kernet2
    angles = e3.rotation_grid_deg(steps)
    frames = [np.asarray(fn(_rotate_contour(contour, angle)), dtype=float) for angle in angles]
    return np.stack(frames)


def _load_shapecomp_nets() -> ShapeCompNets:
    """Load the official weights once, lazily, so h5py is only needed when a run arm is used."""
    global _SHAPECOMP_NETS
    if _SHAPECOMP_NETS is None:
        from earthglyph.shape_benchmark.shapecomp_port import ShapeCompNets

        _SHAPECOMP_NETS = ShapeCompNets()
    return _SHAPECOMP_NETS


def _scorer_for(network: str) -> Any:
    """The official network as one callable: ORIGINAL contour in, 22 coordinates out."""
    nets = _load_shapecomp_nets()
    return nets.kernet1 if network == "kernet1" else nets.kernet2


def _contour_of(shape: BenchmarkShape | np.ndarray) -> np.ndarray:
    """The contour an S0K arm sees — the ORIGINAL outline, not EarthGlyph's 128-point ring."""
    if isinstance(shape, BenchmarkShape):
        return shape.display_points
    return np.asarray(shape, dtype=float)


@dataclass(frozen=True)
class Hit:
    rank: int
    shape_id: str
    source: str
    category: str | None
    distance: float


@dataclass
class SearchResult:
    query_id: str
    method: str
    gallery_size: int
    distances: np.ndarray
    """Distance to every gallery shape, in gallery order (excluded entries are +inf)."""
    order: np.ndarray
    """Full ranking of gallery indices, best first. Not just the Top-K."""
    hits: list[Hit]
    elapsed_seconds: float
    details: dict[str, Any] = field(default_factory=dict)

    def rank_of(self, shape_id: str, gallery: Gallery) -> int | None:
        index = gallery.index_of(shape_id)
        if index is None or not np.isfinite(self.distances[index]):
            return None
        return int(np.flatnonzero(self.order == index)[0]) + 1


class Gallery:
    """A candidate set with the per-arm corpora precomputed once and shared by all queries."""

    def __init__(
        self,
        shapes: Sequence[BenchmarkShape],
        *,
        official_coords: dict[str, np.ndarray] | None = None,
        e3_params: dict[str, Any] | None = None,
        e1_normalizer: e1.FeatureNormalizer | None = None,
        shapecomp_nets: ShapeCompNets | None = None,
        name: str = "gallery",
    ) -> None:
        if not shapes:
            raise ValueError("gallery is empty")
        self.name = name
        self.shapes = list(shapes)
        self.matrix = np.stack([shape.points for shape in self.shapes])
        self._index = {shape.shape_id: i for i, shape in enumerate(self.shapes)}
        if len(self._index) != len(self.shapes):
            raise ValueError("gallery contains duplicate shape_id values")
        self.official_coords = official_coords
        self.e3_params: dict[str, Any] = dict(e3_params or {})
        self._shapecomp_nets = shapecomp_nets
        self._kernet_coords: dict[str, np.ndarray] = {}
        self._pose_embeddings: dict[str, dict[str, np.ndarray]] = {}

        features = e1.features_for_library(list(self.matrix))
        # Inheriting keeps "added candidates" the only variable when a condition extends the
        # gallery; refitting would silently rescale every E1 distance at the same time.
        self.e1_normalizer = e1_normalizer or e1.fit_normalizer(
            features,
            method="minmax",
            names=e1.feature_names(),
            fitted_on=f"{name} ({len(self.shapes)} shapes)",
        )
        self.e1_normalizer_inherited = e1_normalizer is not None
        self.e1_features = features
        self.e3_descriptors = e3.build_library_descriptors(list(self.matrix), **self.e3_params)

    def __len__(self) -> int:
        return len(self.shapes)

    def index_of(self, shape_id: str) -> int | None:
        return self._index.get(shape_id)

    def has_official_coords(self) -> bool:
        return self.official_coords is not None and all(
            shape.shape_id in self.official_coords for shape in self.shapes
        )

    def _nets(self) -> ShapeCompNets:
        return self._shapecomp_nets if self._shapecomp_nets is not None else _load_shapecomp_nets()

    def score_contour(self, network: str, contour: np.ndarray) -> np.ndarray:
        """Run the official network on one ORIGINAL contour under its own input contract."""
        nets = self._nets()
        fn = nets.kernet1 if network == "kernet1" else nets.kernet2
        return np.asarray(fn(contour), dtype=float)

    def kernet_coords(self, network: str) -> np.ndarray:
        """(n, 22) official-network coordinates for the gallery, scored once and cached.

        Each shape is scored on its ``display_points`` (the original contour), not the shared
        128-point ``points``, so the official preprocessing contract is preserved. The output
        is absolute (no corpus fitting), so adding candidates never rescales existing scores.
        """
        if network not in self._kernet_coords:
            self._kernet_coords[network] = np.array(
                [self.score_contour(network, shape.display_points) for shape in self.shapes],
                dtype=float,
            )
        return self._kernet_coords[network]

    def params_for(self, method: str) -> dict[str, Any]:
        if method == "E1":
            return {"normalizer": self.e1_normalizer, "orientation": "canonical"}
        if method == "E3":
            return dict(self.e3_params)
        return {}

    def pose_embeddings(self, network: str, strategy: Strategy | str) -> np.ndarray:
        """``(n, k, 22)`` pose embeddings for every gallery shape under one rotation wrapper.

        Built once per (network, strategy) because the gallery side of ``R3`` is the expensive
        part: 24 poses for every candidate. ``k`` is 1 for every other wrapper, and for
        ``naive``/``R0``/``R1`` that single row is exactly :meth:`kernet_coords`, which is what
        the determinism test checks.
        """
        resolved = strategy if isinstance(strategy, Strategy) else resolve_strategy(strategy)
        cache = self._pose_embeddings.setdefault(network, {})
        if resolved.name not in cache:
            if not resolved.bilateral and not resolved.canonical:
                # naive / R0 / R1 score the gallery once, in its stored pose: literally the rows
                # the plain arm uses, so a wrapper can never disagree with it about what a
                # candidate IS — only about which query pose is allowed to match it.
                return self.kernet_coords(network)[:, None, :]
            scorer = self._scorer(network)
            cache[resolved.name] = np.stack(
                [
                    pose_embedding(scorer, shape.display_points, resolved, "gallery")
                    for shape in self.shapes
                ]
            )
        return cache[resolved.name]

    def _scorer(self, network: str) -> Any:
        nets = self._nets()
        return nets.kernet1 if network == "kernet1" else nets.kernet2


def _query_points(query: BenchmarkShape | np.ndarray) -> tuple[np.ndarray, str]:
    if isinstance(query, BenchmarkShape):
        return query.points, query.shape_id
    return np.asarray(query, dtype=float), "query"


def search_shape(
    query: BenchmarkShape | np.ndarray,
    gallery: Gallery,
    method: str,
    top_k: int = 10,
    *,
    allow_mirror: bool = False,
    exclude: Iterable[str] = (),
    rotation_search: bool = False,
    rotation_steps: int = KERNET_ROTATION_STEPS,
    rotation_strategy: str | None = None,
) -> SearchResult:
    """Rank the whole gallery against ``query`` and return the Top-K plus the full order.

    ``exclude`` removes shape_ids from the candidate set (Part D2's T1/T2/T3). Excluded
    entries stay in ``distances`` as +inf so the array remains gallery-aligned, but they
    are dropped from ``order`` and ``hits``.

    ``rotation_search`` applies only to the S0K arms: it wraps the official network in a
    query-side rotation-grid min, ``d(q, g) = min_theta ||net(R_theta q) - net(g)||``, on the
    same grid E3's ``orientation="sample"`` uses. It is a capability ADDED to the port, not a
    property of the official network; E0/E1 are rotation-invariant by construction and E3
    already searches, so the flag is inert for them.

    ``rotation_strategy`` names a wrapper from
    :mod:`earthglyph.shape_benchmark.rotation_methods` — ``naive``/``R0``/``R1_36``/``R1_72``/
    ``R2``/``R3`` — and takes precedence over ``rotation_search``, which stays as the CP6 entry
    point. ``R0`` through this door is the same arithmetic as ``rotation_search=True``.

    ``allow_mirror`` is honoured by E0/E1/E3 only and RAISES for S0/S0K1/S0K2 (Checkpoint 7-D,
    item F2). An arm that has no reflected branch must refuse the switch instead of accepting it
    and ignoring it; a mirrored forward pass through KerNet would be a new, unevaluated
    capability, not a flag.
    """
    if method not in ALL_METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {ALL_METHODS}")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    _require_mirror_support(method, allow_mirror)
    if rotation_strategy is not None and method not in SHAPECOMP_RUN_METHODS:
        raise ValueError(
            f"rotation_strategy describes a wrapper around the official network and cannot be "
            f"applied to {method!r}; E0/E1 are rotation-invariant by construction and E3 "
            f"chooses its own orientation policy"
        )
    points, query_id = _query_points(query)
    excluded = set(exclude)
    mask = np.array([shape.shape_id not in excluded for shape in gallery.shapes])
    if not mask.any():
        raise ValueError("every gallery shape was excluded")

    started = time.perf_counter()
    details: dict[str, Any]
    if method in SHAPECOMP_RUN_METHODS:
        network = _RUN_NETWORK[method]
        query_contour = _contour_of(query)
        gallery_coords = gallery.kernet_coords(network)
        indices = np.flatnonzero(mask)
        distances = np.full(len(gallery), np.inf)
        if rotation_strategy is not None:
            strategy = resolve_strategy(rotation_strategy)
            query_rows = pose_embedding(gallery._scorer(network), query_contour, strategy, "query")
            distances[indices] = gallery_distances(
                query_rows, gallery.pose_embeddings(network, strategy)
            )[indices]
            details = {
                "kind": "official_network_with_rotation_wrapper",
                "network": network,
                "rotation_strategy": strategy.name,
                "query_poses": int(query_rows.shape[0]),
                "gallery_poses": int(strategy.steps) if strategy.bilateral else 1,
                "forward_passes": int(query_rows.shape[0])
                + (int(strategy.steps) if strategy.bilateral else 1),
                "rotation_step_deg": strategy.step_deg,
                "orientation_status": (
                    canonical_frame(query_contour).status if strategy.canonical else None
                ),
                "contract": describe(strategy.name),
                "note": (
                    "Official KerNet weights run locally through the numpy port under the "
                    "unchanged input contract; the rotation wrapper is an ADDED capability, not a "
                    "property of the network. The pose decision is taken in contour space, so "
                    "KerNet1's vector input and KerNet2's raster input share one axis convention."
                ),
            }
        elif rotation_search:
            query_frames = kernet_rotation_frames(network, query_contour, rotation_steps)
            per_angle = np.linalg.norm(
                gallery_coords[indices][None, :, :] - query_frames[:, None, :], axis=2
            )
            distances[indices] = per_angle.min(axis=0)
            details = {
                "kind": "official_network_actually_run",
                "network": network,
                "rotation_search": True,
                "rotation_step_deg": 360.0 / float(rotation_steps),
                "rotations_searched": int(query_frames.shape[0]),
                "note": "Official KerNet weights run locally via the numpy port with a query-side "
                "rotation-grid min over the same 360/steps grid as E3's orientation='sample'; "
                "the gallery is scored once in its stored pose. This rotation robustness is an "
                "ADDED wrapper, not a property of the official network, and the input contract "
                "(kernet1_input/kernet2_input) is unchanged.",
            }
        else:
            run_coords = gallery.score_contour(network, query_contour)
            distances[indices] = np.linalg.norm(gallery_coords[indices] - run_coords, axis=1)
            details = {
                "kind": "official_network_actually_run",
                "network": network,
                "rotation_search": False,
                "note": "Official KerNet weights run locally via the numpy port on the original "
                "contour (official input contract); scores shapes with no published coordinates. "
                "Not rotation/mirror invariant, as the official networks are not.",
            }
    elif method == "S0":
        if not gallery.has_official_coords():
            raise ValueError(
                "S0 needs published official 22-D coordinates for every gallery shape; "
                "they exist only for the H1/H2 stimuli, not for MPEG-7"
            )
        coords = gallery.official_coords
        assert coords is not None
        query_coords = coords.get(query_id)
        if query_coords is None:
            raise ValueError(f"no published official coordinates for query {query_id!r}")
        gallery_coords = np.array([coords[shape.shape_id] for shape in gallery.shapes])
        distances = np.linalg.norm(gallery_coords - query_coords, axis=1)
        details = {
            "kind": "official_precomputed_coordinates",
            "note": "Euclidean distance in the authors' published 22-D ShapeComp space; "
            "not a local execution of ShapeComp",
        }
    elif method == "E3":
        indices = np.flatnonzero(mask)
        distances = np.full(len(gallery), np.inf)
        # ``allow_mirror`` means two different things here. In ``e3_params`` it says the CACHE holds
        # reflected contexts (a build-time cost); on the call it says this QUERY may use them. Only
        # the second one is a scoring argument, so the first is taken out of the forwarded params
        # instead of colliding with it. A query-side True against a cache built without mirrors
        # still raises in ``score_library_from_descriptors`` rather than falling back silently.
        scoring_params = dict(gallery.e3_params)
        scoring_params.pop("allow_mirror", None)
        scores, details = e3.score_library_from_descriptors(
            points, gallery.e3_descriptors, indices, allow_mirror=allow_mirror, **scoring_params
        )
        distances[indices] = scores
    else:
        indices = np.flatnonzero(mask)
        distances = np.full(len(gallery), np.inf)
        scores, details = pm.score_library(
            method,
            points,
            [gallery.matrix[i] for i in indices],
            allow_mirror=allow_mirror,
            params=gallery.params_for(method),
        )
        distances[indices] = scores
    elapsed = time.perf_counter() - started

    finite = np.flatnonzero(np.isfinite(distances))
    order = finite[np.asarray(pm.rank_order(distances[finite]), dtype=int)]
    hits = [
        Hit(
            rank=rank,
            shape_id=gallery.shapes[int(index)].shape_id,
            source=gallery.shapes[int(index)].source,
            category=gallery.shapes[int(index)].category,
            distance=float(distances[int(index)]),
        )
        for rank, index in enumerate(order[:top_k], start=1)
    ]
    return SearchResult(
        query_id=query_id,
        method=method,
        gallery_size=len(gallery),
        distances=distances,
        order=order,
        hits=hits,
        elapsed_seconds=elapsed,
        details=details,
    )


def pair_distance(
    left: BenchmarkShape | np.ndarray,
    right: BenchmarkShape | np.ndarray,
    method: str,
    *,
    normalizer: e1.FeatureNormalizer | None = None,
    allow_mirror: bool = False,
    rotation_search: bool = False,
    rotation_steps: int = KERNET_ROTATION_STEPS,
    rotation_strategy: str | None = None,
    orientation: str | None = None,
) -> float:
    """Distance between exactly two shapes — the unit of the human-rating experiment.

    Computing only the pairs that were actually rated avoids ranking 500 shapes 250 times.

    ``rotation_search`` (S0K arms only) turns the LEFT contour through the shared rotation
    grid and keeps the smallest network distance, so a rotated copy scores near the
    un-rotated distance; E0/E1/E3 already handle rotation and the flag is inert for them.
    ``rotation_strategy`` names a wrapper from ``rotation_methods`` instead and takes
    precedence, so ``"R0"`` here and ``rotation_search=True`` must give one number — a test
    holds them together. ``orientation`` selects E3's policy for the E3 arm only.
    ``allow_mirror`` follows :func:`search_shape`: E0/E1/E3 only, refused elsewhere.
    """
    _require_mirror_support(method, allow_mirror)
    if method in SHAPECOMP_RUN_METHODS and rotation_strategy is not None:
        strategy = resolve_strategy(rotation_strategy)
        scorer = _scorer_for(_RUN_NETWORK[method])
        return float(
            pose_pair_distance(
                pose_embedding(scorer, _contour_of(left), strategy, "query"),
                pose_embedding(scorer, _contour_of(right), strategy, "gallery"),
            )
        )
    if method in SHAPECOMP_RUN_METHODS:
        network = _RUN_NETWORK[method]
        fn = _scorer_for(network)
        left_contour = _contour_of(left)
        right_contour = _contour_of(right)
        right_coords = np.asarray(fn(right_contour), dtype=float)
        if rotation_search:
            left_frames = kernet_rotation_frames(network, left_contour, rotation_steps)
            return float(np.min(np.linalg.norm(left_frames - right_coords, axis=1)))
        return float(np.linalg.norm(np.asarray(fn(left_contour), dtype=float) - right_coords))
    if rotation_strategy is not None:
        raise ValueError(
            f"rotation_strategy wraps the official network and cannot be applied to {method!r}"
        )
    left_points, _ = _query_points(left)
    right_points, _ = _query_points(right)
    if method == "S0":
        raise ValueError("use the published coordinates directly for S0 pair distances")
    params: dict[str, Any] = {}
    if method == "E1":
        if normalizer is None:
            raise ValueError("E1 pair distances need a corpus normalizer for comparability")
        params = {"normalizer": normalizer, "orientation": "canonical"}
    elif method == "E3" and orientation is not None:
        params = {"orientation": orientation}
    elif orientation is not None:
        raise ValueError(
            f"orientation is a policy of E1 and E3 only, not of {method!r}; "
            f"{'E0 is rotation-invariant by construction' if method == 'E0' else method!r}"
        )
    scores, _ = pm.score_library(
        method, left_points, [right_points], allow_mirror=allow_mirror, params=params
    )
    return float(scores[0])