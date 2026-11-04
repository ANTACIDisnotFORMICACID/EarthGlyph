"""Fourier shape descriptors for closed contours (P0-3, Task A).

Definition. A contour ``P = {(x_i, y_i)}_{i=0..N-1}`` is written as the complex
sequence ``z_i = x_i + i*y_i`` and transformed with the DFT

    Z_k = sum_i z_i * exp(-2*pi*i*k*i/N),   k = 0 .. N-1

The descriptor keeps the **amplitudes** ``|Z_k| / N`` of the low harmonics
``k = 1 .. H``, held in ascending harmonic order (entries are addressed by ``k``,
never re-sorted by value). ``Z_0`` (the DC term) is the centroid and is
never part of the descriptor: it carries position, not shape. Dividing by the
sample count ``N`` turns the DFT sum into the coefficient of the underlying
curve, which is what makes descriptors from 64-, 128- and 256-point samplings of
the same outline comparable (the sampling ablation depends on it).

What this representation is invariant to (and what it is not) is stated in
:func:`feature_definition` and enforced by ``tests/test_fourier.py``:

* translation — exact, after centering (the DC term is dropped);
* scale — exact, after RMS-radius scaling (:func:`earthglyph.geometry.normalize_contour`);
* rotation — amplitudes only, because rotating multiplies every ``Z_k`` by the
  same phase factor ``exp(i*theta)``;
* cyclic start point — amplitudes only, a shift adds a ``k``-dependent phase;
* traversal direction / mirroring — **not** invariant when only positive
  harmonics are used (see below).

Direction and mirror are different operations but the spectrum reacts to both in
the same way: reversing the point order maps ``Z_k -> Z_-k`` and reflecting the
plane (``z -> conj(z)``, D9) maps ``Z_k -> conj(Z_-k)``, so in both cases the
amplitude at harmonic ``k`` becomes the amplitude at harmonic ``-k``. That is
why this module offers two modes:

``"positive"``
    uses ``|Z_k|`` for ``k >= 1`` only. Combined with the CCW canonical winding
    that preprocessing already guarantees (D23), mirror pairs of the same shape
    get different descriptors — useful for the coarse filter, but it must never
    be described as "mirror detection" independent of direction.
``"symmetric"``
    folds the two sides, ``( |Z_k| + |Z_-k| ) / 2``, giving a descriptor that is
    invariant under *both* direction reversal and mirroring.

Amplitude FDs discard phase and therefore structure: two different shapes can
share a low-harmonic spectrum. Per docs/DECISIONS.md D10 these descriptors are
**coarse-filter only**; mirror handling and structural matching belong to the
re-ranking stage.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from earthglyph.geometry.normalization import normalize_contour

DEFAULT_N_HARMONICS = 16
DEFAULT_FOLD: Fold = "positive"
FOURIER_SCHEMA_VERSION = "1.0"

Fold = Literal["positive", "symmetric"]
_FOLDS: tuple[str, ...] = ("positive", "symmetric")

# Harmonics at or above N/2 (Nyquist) alias for a sequence of N samples, so the
# highest usable harmonic index is N//2 - 1.
_NYQUIST_MARGIN = 1


class FeatureError(ValueError):
    """Raised when a contour cannot be turned into a shape descriptor."""


def _as_float_array(contours: Any, name: str) -> np.ndarray:
    arr = np.asarray(contours, dtype=float)
    if arr.ndim not in (2, 3) or arr.shape[-1] != 2:
        raise FeatureError(f"{name} must have shape (N, 2) or (M, N, 2), got {arr.shape}")
    if arr.shape[-2] < 3:
        raise FeatureError(f"a contour needs at least 3 points, got {arr.shape[-2]}")
    if not np.all(np.isfinite(arr)):
        raise FeatureError(f"{name} contains non-finite coordinates")
    return arr


def as_complex_sequence(points: Any, *, normalize: bool = True) -> np.ndarray:
    """Return the contour as a complex sequence, optionally RMS-normalized.

    The stored contours are already normalized, so ``normalize=True`` is
    idempotent on them (centering a centered contour changes nothing beyond
    float round-off).
    """
    arr = _as_float_array(points, "points")
    if arr.ndim != 2:
        raise FeatureError(f"expected a single (N, 2) contour, got shape {arr.shape}")
    if not normalize:
        return arr[:, 0] + 1j * arr[:, 1]
    try:
        scaled = normalize_contour(arr)
    except ValueError as exc:  # zero RMS radius, <3 points
        raise FeatureError(f"degenerate contour: {exc}") from exc
    return scaled[:, 0] + 1j * scaled[:, 1]


def harmonic_limit(n_points: int) -> int:
    """Highest harmonic index that can be represented without aliasing."""
    return n_points // 2 - _NYQUIST_MARGIN


def check_harmonics(n_points: int, n_harmonics: int) -> None:
    if n_harmonics < 1:
        raise FeatureError(f"n_harmonics must be >= 1, got {n_harmonics}")
    limit = harmonic_limit(n_points)
    if n_harmonics > limit:
        raise FeatureError(
            f"n_harmonics={n_harmonics} exceeds the Nyquist limit for "
            f"{n_points}-point contours (max {limit})"
        )


def check_fold(fold: str) -> str:
    if fold not in _FOLDS:
        raise FeatureError(f"fold must be one of {_FOLDS}, got {fold!r}")
    return fold


def fourier_coefficients(points: Any, *, normalize: bool = True) -> np.ndarray:
    """Complex DFT coefficients ``Z_0 .. Z_{N-1}`` of one contour.

    Exposed because the retrieval stage needs the full complex coefficients
    (phase included) for cyclic-start and rotation alignment (D8); the coarse
    descriptor below deliberately throws the phase away.
    """
    return np.fft.fft(as_complex_sequence(points, normalize=normalize))


def _negative_amplitudes(amplitudes: np.ndarray, k: np.ndarray) -> np.ndarray:
    """``|Z_-k|`` read out of the ``|Z_0 .. |Z_{N-1}`` amplitude array."""
    n = amplitudes.shape[-1]
    return np.asarray(np.take(amplitudes, (-k) % n, axis=-1))


def harmonic_amplitudes(
    points: Any,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    *,
    fold: Fold = DEFAULT_FOLD,
    per_sample: bool = True,
) -> np.ndarray:
    """Low-harmonic amplitude descriptor of one contour, length ``n_harmonics``.

    Values are non-negative, finite and ordered by ascending harmonic index.
    Contours are RMS-normalized first unless they already are (idempotent).
    With ``per_sample=True`` the amplitudes are divided by the sample count, so
    the descriptor of a 64-, 128- or 256-point sampling of the *same* curve is
    directly comparable — the unnormalized DFT sum grows with N
    (docs/EXPERIMENTS.md; needed by the sampling ablation).
    """
    z = as_complex_sequence(points)
    amplitudes = np.abs(np.fft.fft(z)) / (z.shape[0] if per_sample else 1)
    check_harmonics(z.shape[0], n_harmonics)
    k = np.arange(1, n_harmonics + 1)
    positive = np.asarray(amplitudes[k])
    if check_fold(fold) == "positive":
        return positive
    return np.asarray(0.5 * (positive + _negative_amplitudes(amplitudes, k)))


def _normalize_stack(arr: np.ndarray) -> np.ndarray:
    """Normalize one (N, 2) contour or each row of an (M, N, 2) stack."""
    if arr.ndim == 2:
        return normalize_contour(arr)
    try:
        return np.stack([normalize_contour(row) for row in arr])
    except ValueError as exc:  # zero RMS radius, <3 points
        raise FeatureError(f"degenerate contour: {exc}") from exc


def batch_harmonic_amplitudes(
    contours: Any,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    *,
    fold: Fold = DEFAULT_FOLD,
    per_sample: bool = True,
) -> np.ndarray:
    """Vectorized :func:`harmonic_amplitudes` over an (M, N, 2) stack.

    Equivalent to stacking the single-contour results; the equality is asserted
    by ``tests/test_fourier.py`` so the fast path cannot drift from the
    definition.
    """
    arr = _as_float_array(contours, "contours")
    check_harmonics(arr.shape[-2], n_harmonics)
    check_fold(fold)
    normalized = _normalize_stack(arr)
    complex_seq = normalized[..., 0] + 1j * normalized[..., 1]
    amplitudes = np.abs(np.fft.fft(complex_seq, axis=-1)) / (
        arr.shape[-2] if per_sample else 1
    )
    k = np.arange(1, n_harmonics + 1)
    positive = np.asarray(np.take(amplitudes, k, axis=-1))
    if fold == "positive":
        return positive
    return np.asarray(0.5 * (positive + _negative_amplitudes(amplitudes, k)))


def fourier_descriptor(
    points: Any,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    *,
    fold: Fold = DEFAULT_FOLD,
    per_sample: bool = True,
) -> np.ndarray:
    """Public single-contour entry point (alias of :func:`harmonic_amplitudes`)."""
    return harmonic_amplitudes(points, n_harmonics, fold=fold, per_sample=per_sample)


def batch_fourier_descriptors(
    contours: Any,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    *,
    fold: Fold = DEFAULT_FOLD,
    per_sample: bool = True,
) -> np.ndarray:
    """Public batch entry point (alias of :func:`batch_harmonic_amplitudes`)."""
    return batch_harmonic_amplitudes(contours, n_harmonics, fold=fold, per_sample=per_sample)


def harmonic_energy_fractions(points: Any, n_harmonics: int) -> np.ndarray:
    """Cumulative share of non-DC spectral energy captured by harmonics 1..K.

    Returns an array of length ``n_harmonics`` whose last entry is the fraction
    ``sum_{k=1..K} |Z_k|^2 / sum_{k=1..N-1} |Z_k|^2``. This is the quantity the
    12 / 16 / 24-dimension ablation measures; it says nothing about retrieval
    quality, which belongs to P0-4.
    """
    z = as_complex_sequence(points)
    check_harmonics(z.shape[0], n_harmonics)
    power = np.abs(np.fft.fft(z)) ** 2
    total = float(power[1:].sum())  # non-DC energy, k = 1 .. N-1
    if not np.isfinite(total) or total <= 0.0:
        raise FeatureError("contour carries no spectral energy")
    k = np.arange(1, n_harmonics + 1)
    return np.cumsum(power[k]) / total


def descriptor_columns(n_harmonics: int, fold: str = DEFAULT_FOLD) -> list[str]:
    """Stable column names so an index file can be decoded without this source."""
    check_fold(fold)
    side = "fd" if fold == "positive" else "fds"
    return [f"{side}_{index:02d}" for index in range(1, n_harmonics + 1)]


def feature_definition(
    n_harmonics: int, fold: str = DEFAULT_FOLD, *, per_sample: bool = True
) -> dict[str, Any]:
    """Machine- and human-readable specification of the descriptor (for manifests)."""
    check_fold(fold)
    invariant = [
        "translation (centroid removed with the DC term)",
        "uniform scale (RMS-radius normalization)",
        "rotation (amplitudes discard the common phase factor)",
        "cyclic start index (amplitudes discard the k-dependent phase)",
    ]
    if fold == "symmetric":
        invariant += ["point-order reversal", "mirroring (folded +/-k amplitudes)"]
    else:
        invariant.append(
            "NOT invariant to point-order reversal or mirroring: both map |Z_k| -> "
            "|Z_-k|; usable because preprocessing fixes CCW winding (D23)"
        )
    return {
        "schema_version": FOURIER_SCHEMA_VERSION,
        "name": "low-harmonic Fourier amplitude descriptor",
        "definition": (
            "z_i = x_i + i*y_i of the RMS-normalized closed contour; "
            "Z_k = DFT(z); descriptor = |Z_k| / N kept in ascending harmonic order "
            "for k=1..n_harmonics (the values are NOT re-sorted)"
            + (" ; each entry is (|Z_k| + |Z_-k|)/2" if fold == "symmetric" else "")
            + " ; Z_0 (DC) is dropped"
        ),
        "normalized_by_sample_count": per_sample,
        "n_harmonics": n_harmonics,
        "fold": fold,
        "columns": descriptor_columns(n_harmonics, fold),
        "invariances": invariant,
        "known_losses": [
            "phase information: structurally different shapes can share low-harmonic amplitudes",
            "high-frequency detail beyond harmonic n_harmonics",
            "size information (deliberate: coarse filtering compares shapes, not areas)",
        ],
        "usage": "coarse filtering only (docs/DECISIONS.md D10); not a mirror test",
    }
