"""One contract for every perceptual method, so a comparison measures the score.

Checkpoint B of the P0-4.5 experiment compares four ways of scoring "is this
candidate the same shape as that query?".  A comparison like that only means
something if every method reads the *same* numbers and is ranked by the *same*
rule; otherwise a Top-1 difference could come from a different resampling, a
different contour orientation, or a different tie-break rather than from the
feature.  So this module fixes the parts that are not under test:

* **input** — a ``(n, 2)`` float array: centered, unit RMS radius, CCW, and the
  first vertex repeated at the end is *not* included.  That is exactly what
  :func:`earthglyph.data.perceptual_gallery.standardize` produces, which is the
  same standardizer a user query goes through (see ``AGENTS.md``: one pipeline).
* **direction** — every method gets the CCW ring.  Traversal-direction reversal
  is measured separately as an invariance probe, never silently absorbed.
* **ranking** — smaller is always "more similar", and ties break by the library
  row order, so two runs give the same order (the D42 determinism rule).
* **no coarse window** — every method scores *every* candidate.  The P0-4
  pipeline's Fourier shortlist is deliberately not applied here: a candidate
  that never reaches the re-ranker would look like a feature failure when it is
  a window artefact.

Each method module exposes the same four names — ``distance``,
``distance_library``, ``POLICY`` and ``describe_policy()`` — and this module
dispatches them by name so the experiment runner never special-cases a method.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

__all__ = [
    "CONTOUR_POINTS",
    "METHOD_MODULES",
    "PerceptualMethodError",
    "check_contour",
    "contour_stack",
    "rank_order",
    "score_library",
]

CONTOUR_POINTS = 128

#: method id -> module that implements the contract.  E0 is the P0-4 pipeline,
#: reused as-is; E1/E2/E3 are this checkpoint's additions.
METHOD_MODULES: dict[str, str] = {
    "E0": "earthglyph.retrieval.perceptual_e0",
    "E1": "earthglyph.retrieval.perceptual_features",
    "E2": "earthglyph.retrieval.salient_structure",
    "E3": "earthglyph.retrieval.shape_context_baseline",
}


class PerceptualMethodError(ValueError):
    """A method was called outside the shared contract."""


def check_contour(points: Any, name: str = "contour") -> np.ndarray:
    """Validate one standardized contour and return it as ``(n, 2)`` float64."""
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2:
        raise PerceptualMethodError(
            f"{name} must be an (n, 2) array of x, y pairs, got shape {array.shape}"
        )
    if array.shape[0] < 4:
        raise PerceptualMethodError(f"{name} has {array.shape[0]} points, cannot be a ring")
    if not np.isfinite(array).all():
        raise PerceptualMethodError(f"{name} contains non-finite coordinates")
    return array


def contour_stack(contours: Sequence[Any], name: str = "library") -> np.ndarray:
    """Validate a whole library and stack it into ``(m, n, 2)``.

    All rows must share the sample count: a distance between a 128-point query
    and a 100-point candidate is not a distance, it is a sampling artefact.
    """
    arrays = [check_contour(item, f"{name}[{index}]") for index, item in enumerate(contours)]
    if not arrays:
        raise PerceptualMethodError(f"{name} is empty")
    counts = {array.shape[0] for array in arrays}
    if len(counts) > 1:
        raise PerceptualMethodError(
            f"{name} mixes sample counts {sorted(counts)}; every member must be resampled "
            "through the same standardizer"
        )
    return np.stack(arrays)


def rank_order(distances: np.ndarray) -> np.ndarray:
    """Library indices, best match first, ties broken by row order.

    ``kind="stable"`` is the whole point: with an unstable sort two runs of the
    same experiment can swap equally-scored candidates and the ranking table is
    no longer reproducible.
    """
    vector = np.asarray(distances, dtype=float)
    if vector.ndim != 1:
        raise PerceptualMethodError(f"distances must be 1-D, got shape {vector.shape}")
    if not np.isfinite(vector).all():
        raise PerceptualMethodError(
            f"{int(np.count_nonzero(~np.isfinite(vector)))} distances are not finite"
        )
    return np.argsort(vector, kind="stable")


def score_library(
    method: str,
    query: Any,
    library: Sequence[Any],
    *,
    allow_mirror: bool = False,
    params: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Score one query against every library member with the named method.

    Returns ``(distances, policy)`` — the policy is the method's own statement of
    what it is invariant to, so a results table can print it next to the numbers
    instead of relying on a comment in the runner.
    """
    import importlib

    if method not in METHOD_MODULES:
        raise PerceptualMethodError(
            f"unknown method {method!r}; implemented: {sorted(METHOD_MODULES)}"
        )
    module = importlib.import_module(METHOD_MODULES[method])
    handler: Callable[..., tuple[np.ndarray, dict[str, Any]]] = module.score_library
    return handler(
        check_contour(query, "query"),
        contour_stack(library),
        allow_mirror=allow_mirror,
        **(dict(params) if params else {}),
    )
