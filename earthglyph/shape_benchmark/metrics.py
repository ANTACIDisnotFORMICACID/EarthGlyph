"""Evaluation metrics for the shape-perception benchmark.

The human-rating side keeps the participant level intact: H1 is 250 pairs x 14 observers,
so confidence intervals resample pairs *and* observers rather than treating 3500 ratings as
independent draws. The retrieval side reports class recall and rank stability, and never
converts a category label into a similarity score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
from scipy.stats import pearsonr, spearmanr


@dataclass(frozen=True)
class Correlation:
    spearman: float
    pearson: float
    n: int

    def as_dict(self) -> dict[str, float | int]:
        return {"spearman": self.spearman, "pearson": self.pearson, "n": self.n}


def correlate(distance: np.ndarray, rating: np.ndarray) -> Correlation:
    """Distance (smaller = more similar) against a rating whose direction is given.

    ``rating`` is passed as-is; the sign of the result carries the direction. For H1 the
    rating is similarity, so a good model gives a NEGATIVE correlation.
    """
    x = np.asarray(distance, dtype=float)
    y = np.asarray(rating, dtype=float)
    if x.shape != y.shape:
        raise ValueError(f"shape mismatch: {x.shape} vs {y.shape}")
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < 3:
        raise ValueError("need at least 3 finite pairs to correlate")
    return Correlation(
        spearman=float(spearmanr(x, y).statistic),
        pearson=float(pearsonr(x, y).statistic),
        n=int(len(x)),
    )


def bootstrap_correlation(
    distance: np.ndarray,
    rating: np.ndarray,
    *,
    n_boot: int = 2000,
    seed: int = 20261107,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Stimulus-level bootstrap: resample the pairs, keeping the two measures coupled."""
    x = np.asarray(distance, dtype=float)
    y = np.asarray(rating, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(x)
    values = np.empty(n_boot)
    for b in range(n_boot):
        index = rng.integers(0, n, n)
        xb, yb = x[index], y[index]
        if np.ptp(xb) == 0 or np.ptp(yb) == 0:
            values[b] = np.nan
            continue
        values[b] = spearmanr(xb, yb).statistic
    return _interval(values, n_boot=n_boot, seed=seed, alpha=alpha, scheme="pairs")


def bootstrap_two_level(
    distance: np.ndarray,
    ratings: np.ndarray,
    *,
    n_boot: int = 2000,
    seed: int = 20261107,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Resample pairs AND observers, so neither level is treated as independent replicates.

    ``ratings`` is (n_pairs, n_observers). Each replicate draws pairs with replacement and
    observers with replacement, then averages the drawn observers before correlating.
    """
    x = np.asarray(distance, dtype=float)
    matrix = np.asarray(ratings, dtype=float)
    if matrix.shape[0] != len(x):
        raise ValueError("ratings rows must align with distances")
    rng = np.random.default_rng(seed)
    n_pairs, n_observers = matrix.shape
    values = np.empty(n_boot)
    for b in range(n_boot):
        pairs = rng.integers(0, n_pairs, n_pairs)
        observers = rng.integers(0, n_observers, n_observers)
        mean = matrix[np.ix_(pairs.tolist(), observers.tolist())].mean(axis=1)
        xb = x[pairs]
        if np.ptp(xb) == 0 or np.ptp(mean) == 0:
            values[b] = np.nan
            continue
        values[b] = spearmanr(xb, mean).statistic
    return _interval(values, n_boot=n_boot, seed=seed, alpha=alpha, scheme="pairs+observers")


def _interval(
    values: np.ndarray, *, n_boot: int, seed: int, alpha: float, scheme: str
) -> dict[str, Any]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError(
            "every bootstrap replicate was degenerate, so there is no variance to resample; "
            "a constant distance or a constant rating vector cannot be correlated"
        )
    return {
        "scheme": scheme,
        "n_boot": n_boot,
        "seed": seed,
        "alpha": alpha,
        "valid_replicates": int(len(finite)),
        "spearman_ci_low": float(np.quantile(finite, alpha / 2.0)),
        "spearman_ci_high": float(np.quantile(finite, 1.0 - alpha / 2.0)),
        "spearman_boot_mean": float(finite.mean()),
        "spearman_boot_std": float(finite.std(ddof=1)),
    }


def _crossed_grid(
    groups: np.ndarray, conditions: np.ndarray, unique_groups: np.ndarray
) -> np.ndarray:
    """Row index grid of shape (n_clusters, n_conditions) for a complete crossed design."""
    if len(conditions) != len(groups):
        raise ValueError("condition_ids must align with group_ids")
    unique_conditions = np.unique(conditions)
    group_slot = {key: position for position, key in enumerate(unique_groups)}
    condition_slot = {key: position for position, key in enumerate(unique_conditions)}
    grid = np.full((len(unique_groups), len(unique_conditions)), -1, dtype=np.int64)
    for row, (group, condition) in enumerate(zip(groups, conditions, strict=True)):
        slot = grid[group_slot[group], condition_slot[condition]]
        if slot != -1:
            raise ValueError("a (stimulus, condition) cell appears twice; design is not crossed")
        grid[group_slot[group], condition_slot[condition]] = row
    if (grid < 0).any():
        raise ValueError("the design is incomplete: some stimulus has no row for some condition")
    return grid


#: Canonical reading of a pipe-keyed paired difference. :func:`bootstrap_grouped_two_level` scores
#: each replicate with ``spearmanr(distance, human similarity)`` — negative for a good arm — and
#: forms ``values[first] - values[second]``, while the published per-arm agreement is the negation
#: of that correlation. Two negations cancel: the raw difference for key ``"A|B"`` already *is*
#: ``agreement(B) - agreement(A)``. Writers must store that direction untouched: negating it in one
#: writer and not another is what made the same key mean opposite things in
#: ``d5_grouped_metrics.json`` and ``d6_rotation_metrics.json``.
PAIRED_DIFFERENCE_DIRECTION = "second_minus_first"
PAIRED_DIFFERENCE_CONVENTION = (
    'key "A|B" stores score(B) - score(A) on the agreement scale, so a positive value means the '
    "arm after the pipe is better; the mirrored reading is published beside it under an "
    "explicitly named field rather than left to the key order"
)


def paired_difference(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Restate one bootstrap difference in both directions, each named by what it means.

    ``raw`` is one entry of :func:`bootstrap_grouped_two_level`'s ``differences`` block. Only the
    canonical direction is a headline number; ``agreement_first_minus_second`` exists so a reader
    holding an artefact written before the unification can still find the number they quoted, and so
    nobody has to infer a sign from the order of a key.
    """
    value = float(raw["mean"])
    low = float(raw["ci_low"])
    high = float(raw["ci_high"])
    return {
        "direction": PAIRED_DIFFERENCE_DIRECTION,
        "agreement_second_minus_first": value,
        "agreement_first_minus_second": -value,
        "ci_low_second_minus_first": low,
        "ci_high_second_minus_first": high,
        "ci_low_first_minus_second": -high,
        "ci_high_first_minus_second": -low,
        "std": float(raw["std"]),
        "excludes_zero": bool(raw["excludes_zero"]),
        "n_valid_replicates": int(raw["n_valid_replicates"]),
    }


def bootstrap_grouped_two_level(
    distances: dict[str, np.ndarray],
    ratings: np.ndarray,
    group_ids: np.ndarray,
    *,
    condition_ids: np.ndarray | None = None,
    resample_conditions: bool = False,
    n_boot: int = 2000,
    seed: int = 20261107,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Repeated-measures bootstrap: resample whole stimulus clusters, never single rows.

    ``ratings`` is (n_rows, n_observers) and ``group_ids`` labels the stimulus each row belongs
    to. A rotation sweep contributes one row per (stimulus, angle), so the same stimulus appears
    several times and those rows are *not* independent observations — they share one stimulus and
    one rating panel. Each replicate therefore draws clusters with replacement and keeps every
    row of a drawn cluster together, then applies one observer-panel draw.

    By default the conditioning factor stays fixed, so the intervals answer "this protocol, a
    different sample of stimuli and observers". Pass ``condition_ids`` with
    ``resample_conditions=True`` to treat the conditions themselves as a random sample (the
    angles drawn with replacement too), which is the wider claim "any rotation of this kind"
    would need — reported as a sensitivity, not as the headline.

    Every arm is scored on the *same* replicate, so the per-arm intervals and the arm-vs-arm
    difference intervals come from one shared resampling — the requirement for a paired
    comparison of correlated estimates.
    """
    matrix = np.asarray(ratings, dtype=float)
    groups = np.asarray(group_ids)
    if matrix.ndim != 2 or matrix.shape[0] != len(groups):
        raise ValueError("rating rows must align with group_ids")
    arrays = {name: np.asarray(value, dtype=float) for name, value in distances.items()}
    if any(len(value) != len(groups) for value in arrays.values()):
        raise ValueError("every arm must cover exactly the same rows")
    unique = np.unique(groups)
    cluster_rows = [np.flatnonzero(groups == key) for key in unique]
    sizes = np.array([len(rows) for rows in cluster_rows])
    uniform = bool((sizes == sizes[0]).all())
    stacked: np.ndarray | None = np.array(cluster_rows) if uniform else None
    grid: np.ndarray | None = None
    if resample_conditions:
        if condition_ids is None:
            raise ValueError("resample_conditions needs condition_ids of the same length")
        grid = _crossed_grid(groups, np.asarray(condition_ids), unique)
    rng = np.random.default_rng(seed)
    n_clusters, n_observers = len(unique), matrix.shape[1]
    values = {name: np.full(n_boot, np.nan) for name in arrays}
    for replicate in range(n_boot):
        drawn = rng.integers(0, n_clusters, n_clusters)
        if grid is not None:
            drawn_conditions = rng.integers(0, grid.shape[1], grid.shape[1])
            cells = grid[
                np.repeat(drawn, grid.shape[1]), np.tile(drawn_conditions, drawn.size)
            ]
            rows = cells.ravel()
        elif stacked is not None:
            rows = stacked[drawn].ravel()
        else:
            rows = np.concatenate([cluster_rows[key] for key in drawn])
        observers = rng.integers(0, n_observers, n_observers)
        mean = matrix[rows][:, observers].mean(axis=1)
        for name, array in arrays.items():
            xb = array[rows]
            if np.ptp(xb) == 0 or np.ptp(mean) == 0:
                continue
            values[name][replicate] = spearmanr(xb, mean).statistic
    names = list(arrays)
    fixed = not resample_conditions
    out: dict[str, Any] = {
        "scheme": (
            "clusters(stimuli)+observers, condition factor fixed"
            if fixed
            else "clusters(stimuli)+conditions+observers, all three resampled"
        ),
        "n_boot": n_boot,
        "seed": seed,
        "alpha": alpha,
        "n_clusters": int(n_clusters),
        "rows_per_cluster": int(sizes[0]) if uniform else None,
        "cluster_size_min": int(sizes.min()),
        "cluster_size_max": int(sizes.max()),
        "n_rows": int(len(groups)),
        "condition_factor": (
            "fixed: the rotation angles are a pre-declared design set and are never resampled, "
            "so inference is over stimuli and observers only"
            if fixed
            else "random: the rotation angles are themselves drawn with replacement, so the "
            "interval also covers 'some other set of angles of the same size'"
        ),
        "per_arm": {
            name: _interval(
                values[name], n_boot=n_boot, seed=seed, alpha=alpha,
                scheme="clusters+observers" if fixed else "clusters+conditions+observers",
            )
            for name in names
        },
        "differences": {},
    }
    for first, second in combinations(names, 2):
        both = np.isfinite(values[first]) & np.isfinite(values[second])
        delta = values[first][both] - values[second][both]
        if delta.size < 2:
            continue
        low = float(np.quantile(delta, alpha / 2.0))
        high = float(np.quantile(delta, 1.0 - alpha / 2.0))
        out["differences"][f"{first}|{second}"] = {
            "n_valid_replicates": int(delta.size),
            "mean": float(delta.mean()),
            "std": float(delta.std(ddof=1)),
            "ci_low": low,
            "ci_high": high,
            "excludes_zero": bool(low > 0.0 or high < 0.0),
        }
    return out


def per_observer_correlations(
    distance: np.ndarray, ratings: np.ndarray
) -> list[dict[str, float | int]]:
    """One correlation per observer — the participant-level view, with no averaging."""
    x = np.asarray(distance, dtype=float)
    matrix = np.asarray(ratings, dtype=float)
    out: list[dict[str, float | int]] = []
    for observer in range(matrix.shape[1]):
        result = correlate(x, matrix[:, observer])
        out.append({"observer": observer, **result.as_dict()})
    return out


def observer_summary(per_observer: Sequence[dict[str, float | int]]) -> dict[str, float | int]:
    values = np.array([float(row["spearman"]) for row in per_observer])
    return {
        "n_observers": int(len(values)),
        "spearman_min": float(values.min()),
        "spearman_median": float(np.median(values)),
        "spearman_max": float(values.max()),
        "spearman_mean": float(values.mean()),
        "spearman_std": float(values.std(ddof=1)),
    }


# --------------------------------------------------------------------------- retrieval


def bootstrap_agreement_difference(
    distance_a: np.ndarray,
    distance_b: np.ndarray,
    ratings: np.ndarray,
    *,
    n_boot: int = 2000,
    seed: int = 20261107,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Paired two-level bootstrap of ``agreement(a) - agreement(b)``.

    Marginal intervals overlapping does not settle whether two arms differ: both are measured
    on the same pairs and the same observers, so the difference must be resampled *paired*.
    Each replicate draws one set of pairs and one set of observers and scores both arms on it.
    ``ratings`` are similarity, so agreement is the correlation with their negation.
    """
    a = np.asarray(distance_a, dtype=float)
    b = np.asarray(distance_b, dtype=float)
    matrix = np.asarray(ratings, dtype=float)
    if not (len(a) == len(b) == matrix.shape[0]):
        raise ValueError("distances and rating rows must align")
    rng = np.random.default_rng(seed)
    n_pairs, n_observers = matrix.shape
    values = np.empty(n_boot)
    for i in range(n_boot):
        pairs = rng.integers(0, n_pairs, n_pairs)
        observers = rng.integers(0, n_observers, n_observers)
        target = -matrix[np.ix_(pairs.tolist(), observers.tolist())].mean(axis=1)
        xa, xb = a[pairs], b[pairs]
        if np.ptp(target) == 0 or np.ptp(xa) == 0 or np.ptp(xb) == 0:
            values[i] = np.nan
            continue
        values[i] = spearmanr(xa, target).statistic - spearmanr(xb, target).statistic
    interval = _interval(values, n_boot=n_boot, seed=seed, alpha=alpha, scheme="pairs+observers")
    finite = values[np.isfinite(values)]
    return {
        **interval,
        "observed_difference": float(
            spearmanr(a, -matrix.mean(axis=1)).statistic
            - spearmanr(b, -matrix.mean(axis=1)).statistic
        ),
        "share_positive": float(np.mean(finite > 0.0)),
        "excludes_zero": bool(
            interval["spearman_ci_low"] > 0.0 or interval["spearman_ci_high"] < 0.0
        ),
    }


def observer_agreement(ratings: np.ndarray) -> dict[str, Any]:
    """How well observers agree with each other — the ceiling any model can be judged against.

    Two views, both on the same (n_pairs, n_observers) matrix: every observer pair's Spearman
    (91 values for 14 observers), and each observer against the mean of the *other* observers
    (leave-one-out, so an observer is never correlated with a mean containing themselves).
    """
    matrix = np.asarray(ratings, dtype=float)
    n_pairs, n_observers = matrix.shape
    if n_observers < 2:
        raise ValueError("observer agreement needs at least two observers")
    pairwise: list[dict[str, float | int]] = []
    for i in range(n_observers):
        for j in range(i + 1, n_observers):
            pairwise.append(
                {
                    "observer_a": i,
                    "observer_b": j,
                    "spearman": float(spearmanr(matrix[:, i], matrix[:, j]).statistic),
                }
            )
    leave_one_out: list[dict[str, float | int]] = []
    for i in range(n_observers):
        others = np.delete(matrix, i, axis=1).mean(axis=1)
        leave_one_out.append(
            {"observer": i, **correlate(others, matrix[:, i]).as_dict()}
        )
    pair_values = np.array([float(row["spearman"]) for row in pairwise])
    loo_values = np.array([float(row["spearman"]) for row in leave_one_out])
    return {
        "n_pairs": int(n_pairs),
        "n_observers": int(n_observers),
        "pairwise": pairwise,
        "pairwise_summary": {
            "n": int(len(pair_values)),
            "min": float(pair_values.min()),
            "median": float(np.median(pair_values)),
            "mean": float(pair_values.mean()),
            "max": float(pair_values.max()),
        },
        "leave_one_out": leave_one_out,
        "leave_one_out_summary": {
            "min": float(loo_values.min()),
            "median": float(np.median(loo_values)),
            "mean": float(loo_values.mean()),
            "max": float(loo_values.max()),
        },
        "note": (
            "Both views correlate one observer's SIMILARITY ratings with another's, so they "
            "are positive. A model's distance-vs-similarity correlation is negative for the "
            "same data; compare magnitudes, not signs."
        ),
    }


def class_recall_at_k(
    categories: Sequence[str | None], query_category: str | None, k: int
) -> float:
    """Fraction of the Top-K that shares the query's MPEG-7 category.

    A category label is a retrieval relevance proxy, NOT a human similarity judgement.
    """
    if query_category is None:
        raise ValueError("class recall needs a query category")
    top = list(categories[:k])
    if not top:
        return 0.0
    return float(sum(1 for c in top if c == query_category) / len(top))


def top_k_overlap(order_a: np.ndarray, order_b: np.ndarray, k: int) -> float:
    """Jaccard overlap of two Top-K sets — the rotation-stability measure."""
    a = set(np.asarray(order_a)[:k].tolist())
    b = set(np.asarray(order_b)[:k].tolist())
    if not a and not b:
        return 1.0
    return float(len(a & b) / len(a | b))


def rank_shift(order_a: np.ndarray, order_b: np.ndarray, index: int) -> int | None:
    """How far one candidate moved between two rankings (None if absent from either)."""
    a = np.flatnonzero(np.asarray(order_a) == index)
    b = np.flatnonzero(np.asarray(order_b) == index)
    if not len(a) or not len(b):
        return None
    return int(b[0]) - int(a[0])


def mean_reciprocal_rank(ranks: Sequence[int | None]) -> float:
    """Mean of ``1/rank`` over queries; a query whose target is absent contributes 0.

    Relevance here is whatever the caller decided the target is (the exact shape, or the first
    same-class candidate), so the same helper serves T0 and the class-level T1/T2.
    """
    if not len(ranks):
        raise ValueError("mean reciprocal rank needs at least one query")
    return float(sum(1.0 / int(rank) for rank in ranks if rank) / len(ranks))


def ndcg_at_k(relevance: Sequence[int], k: int) -> float:
    """Binary-relevance NDCG@K over a ranking given in rank order (best candidate first).

    Raises when the ranking holds nothing relevant: an ideal DCG of zero has no defined ratio,
    and returning 0.0 there would silently punish a method for a gallery that never contained
    the class.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    flags = np.asarray(list(relevance), dtype=float)
    if flags.ndim != 1:
        raise ValueError("relevance must be a 1-D sequence")
    if flags.size and not set(np.unique(flags).tolist()) <= {0.0, 1.0}:
        raise ValueError("relevance must be binary")
    total = float(flags.sum())
    if total == 0.0:
        raise ValueError("no relevant candidate in this ranking, so NDCG has no denominator")
    gains = flags[:k]
    discounts = np.log2(np.arange(2, len(gains) + 2))
    ideal = np.ones(min(k, int(total)))
    ideal_discounts = np.log2(np.arange(2, len(ideal) + 2))
    return float(np.sum(gains / discounts) / np.sum(ideal / ideal_discounts))


def rank_displacement(order_a: np.ndarray, order_b: np.ndarray) -> dict[str, float]:
    """Full-ranking drift between two orderings of the SAME candidate set.

    Top-K overlap only looks at the head; this reports how far every candidate moved, which is
    what a wrapper that "shuffles the tail but keeps the head" would otherwise hide.
    """
    a = np.asarray(order_a, dtype=int)
    b = np.asarray(order_b, dtype=int)
    if len(a) != len(b) or set(a.tolist()) != set(b.tolist()):
        raise ValueError("rankings must cover exactly the same candidates")
    size = len(a)
    # Slots are the shared members sorted by id, NOT range(size): a ranking of a gallery with
    # candidates removed carries gapped ids, and indexing a dense array by them would spill over
    # the end or silently compare the wrong members. argsort(order)[j] is the rank position of the
    # j-th smallest member id, so both sides line up on the same member without a dense lookup.
    position_a = np.argsort(a)
    position_b = np.argsort(b)
    shift = np.abs(position_b - position_a)
    rho = float(spearmanr(position_a, position_b).statistic) if size > 2 else float("nan")
    return {
        "mean_abs_shift": float(shift.mean()),
        "max_abs_shift": float(shift.max(initial=0)),
        "spearman_between_rankings": rho,
        "n_compared": float(size),
    }