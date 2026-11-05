"""Coarse filtering over the P0-3 feature index (P0-4, Task C2).

The index stores one row per candidate as ``[fourier(16) | geometry(4)]`` with the
group offsets recorded in the manifest. This module ranks **one group at a time**:
an ablation has to be able to say which group put a candidate into the shortlist,
and ranking the concatenated 20 columns with a single L2 would silently fix a
1:1 weight between a harmonic amplitude and a dimensionless solidity. That
combination is legal, not the default truth, so it is reachable only by naming
the ``"all"`` group explicitly (and it is measured, not assumed, in
``experiments/p0_retrieval_baseline``).

Nothing here is learned or tuned per query. Two knobs, both recorded in the
result:

``group``
    ``"fourier"`` (default — the descriptor the index was built around),
    ``"geometry"`` (the four shape statistics) or ``"all"`` (the raw
    concatenation, kept for the ablation).
``scale``
    ``"none"`` compares the published values; ``"std"`` divides each column by
    the *library's* population standard deviation, so a column that is nearly
    constant across candidates stops dominating. The reference statistics come
    from the candidate library alone — never from the query — which keeps the
    transform a property of the index instead of of the query that happens to
    be running.

Distances are plain Euclidean in the chosen representation, computed in float64,
and ties break by candidate row index so a repeated search returns the same
shortlist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

GROUPS = ("fourier", "geometry", "all")
SCALES = ("none", "std")


class CoarseError(ValueError):
    """Raised when a feature matrix cannot be searched at all."""


@dataclass(frozen=True)
class Shortlist:
    """Candidate rows ordered by coarse distance, best first."""

    indices: np.ndarray
    distances: np.ndarray
    group: str
    scale: str

    @property
    def size(self) -> int:
        return int(self.indices.shape[0])

    def to_dict(self, k: int | None = None) -> dict[str, Any]:
        limit = self.size if k is None else min(int(k), self.size)
        return {
            "coarse_group": self.group,
            "coarse_scale": self.scale,
            "coarse_shortlist_size": self.size,
            "coarse_top_indices": [int(i) for i in self.indices[:limit]],
            "coarse_top_distances": [float(d) for d in self.distances[:limit]],
        }


def check_group(group: str) -> str:
    if group not in GROUPS:
        raise CoarseError(f"group must be one of {GROUPS}, got {group!r}")
    return group


def check_scale(scale: str) -> str:
    if scale not in SCALES:
        raise CoarseError(f"scale must be one of {SCALES}, got {scale!r}")
    return scale


def group_columns(groups: dict[str, Any], group: str, feature_dim: int) -> list[int]:
    """Column indices of one feature group, read from the manifest's offsets."""
    check_group(group)
    if group == "all":
        return list(range(feature_dim))
    try:
        spec = groups[group]
    except KeyError as error:  # a manifest from a different build
        raise CoarseError(f"manifest has no feature group {group!r}") from error
    offset, width = int(spec["offset"]), int(spec["width"])
    if offset < 0 or width < 1 or offset + width > feature_dim:
        raise CoarseError(
            f"group {group!r} spans [{offset}, {offset + width}) outside a "
            f"{feature_dim}-column feature matrix"
        )
    return list(range(offset, offset + width))


def column_reference(features: np.ndarray, columns: Sequence[int], scale: str) -> np.ndarray:
    """Per-column divisor used to compare candidates (ones when unstandardized).

    Computed from the **candidate library only**. :func:`rank` applies the result
    to the query vector as well, so query and library always live in the same
    space — a divisor on one side alone would silently redefine the metric.
    """
    check_scale(scale)
    block = features[:, list(columns)]
    if scale == "none":
        return np.ones(block.shape[1], dtype=float)
    std = np.std(block, axis=0, ddof=0)
    # a constant column carries no information; dividing it by 1 keeps it inert
    # instead of amplifying float round-off into noise
    return np.where(std > 1e-12, std, 1.0)


def rank(
    query_vector: np.ndarray,
    library: np.ndarray,
    *,
    group: str = "fourier",
    scale: str = "none",
    reference: np.ndarray | None = None,
    shortlist_size: int | None = None,
) -> Shortlist:
    """Euclidean shortlist of a library against one query feature vector.

    ``library`` is the group's own columns (see :func:`group_columns`), and
    ``query_vector`` the same columns of the query, in the published units — the
    divisor is applied here, to both sides, so no caller has to remember to
    standardize one and not the other.
    """
    check_group(group)
    check_scale(scale)
    query = np.asarray(query_vector, dtype=float)
    stack = np.asarray(library, dtype=float)
    if query.ndim != 1:
        raise CoarseError(f"query vector must be 1-D, got shape {query.shape}")
    if stack.ndim != 2:
        raise CoarseError(f"library must be 2-D, got shape {stack.shape}")
    if stack.shape[0] == 0:
        raise CoarseError("library is empty")
    if query.shape[0] != stack.shape[1]:
        raise CoarseError(
            f"query has {query.shape[0]} features, library has {stack.shape[1]}"
        )
    if not np.all(np.isfinite(query)) or not np.all(np.isfinite(stack)):
        raise CoarseError("feature values must all be finite")
    if shortlist_size is not None and shortlist_size < 1:
        raise CoarseError(f"shortlist_size must be >= 1, got {shortlist_size}")
    if reference is None:
        if scale == "std":
            # deriving the divisor from the library being searched is the only
            # unambiguous choice when one is not supplied
            std = np.std(stack, axis=0, ddof=0)
            reference = np.where(std > 1e-12, std, 1.0)
        else:
            reference = np.ones(stack.shape[1], dtype=float)
    divisor = np.asarray(reference, dtype=float)
    if divisor.shape != (stack.shape[1],):
        raise CoarseError(f"reference width {divisor.shape[0]} != library width {stack.shape[1]}")
    if not np.all(np.isfinite(divisor)) or np.any(divisor <= 0.0):
        raise CoarseError("reference divisors must be finite and positive")

    # root-mean-square per column, so a 4-column and a 16-column group are at
    # least on the same "typical per-feature deviation" footing
    distances = np.sqrt(np.mean(((stack - query) / divisor) ** 2, axis=1))
    order = np.argsort(distances, kind="stable")
    if shortlist_size is not None:
        order = order[:shortlist_size]
    return Shortlist(
        indices=order.astype(np.int64),
        distances=distances[order].astype(float),
        group=group,
        scale=scale,
    )


def shortlist_overlap(a: Shortlist, b: Shortlist) -> float:
    """Jaccard overlap of two shortlists — the ablation's "did the group matter"."""
    left, right = set(a.indices.tolist()), set(b.indices.tolist())
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def load_groups(manifest: dict[str, Any]) -> dict[str, Any]:
    """Feature groups from a P0-3 index manifest, with a clear error if absent."""
    try:
        groups = manifest["index"]["feature_groups"]
    except (KeyError, TypeError) as error:
        raise CoarseError("manifest has no index.feature_groups") from error
    return dict(groups)
