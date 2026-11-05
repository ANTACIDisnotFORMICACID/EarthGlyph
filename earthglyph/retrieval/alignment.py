"""Cyclic-start and rotation alignment of closed contours (P0-4, Task C3).

This is the geometric re-rank of the two-stage design: for a query contour ``A``
and a candidate contour ``B``, both standardized to ``N`` points of unit RMS
radius, the score is the smallest pointwise boundary offset over the two
correspondence freedoms that preprocessing deliberately left alone (D8/D9):

    D(A, B) = min over s, theta of sqrt( (1/N) * sum_k |a_k - exp(i*theta) b_(k+s mod N)|^2 )

``s`` is the cyclic start offset and ``theta`` the in-plane rotation. Neither is
searched over a grid: expanding the square gives

    D^2(s) = mean|a|^2 + mean|b|^2 - 2 * |C_s|,     C_s = (1/N) sum_k a_k conj(b_(k+s))

so the optimal rotation for a given shift is ``theta_s = arg(C_s)`` — a *closed
form*, not a 360-degree loop — and all ``C_s`` at once are one inverse FFT of the
cross-spectrum ``FFT(a) * conj(FFT(b))``. The whole candidate library is then a
single batched FFT.

Mirroring is handled here, in geometry, and nowhere else. The Fourier coarse
filter is provably mirror-blind once the walk is CCW-canonical (the mirrored
contour has ``FFT = conj(FFT(original))``, so its amplitudes are identical), so a
mirror policy expressed through feature amplitudes would be a coincidence, not a
control. ``allow_mirror`` compares the query against the reflected candidate and
reports which one matched.

Symmetric shapes have many equally good starts; the returned shift is always the
lowest index among the near-optimal ones, so a fixed input produces a fixed
answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

MIRROR_TOLERANCE = 1e-9
# Shifts whose induced distance is within this much of the best one are counted
# as equivalent optima (a near-circular outline has ~N of them).
DEGENERACY_TOL = 1e-6

AlignmentVector = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


class AlignmentError(ValueError):
    """Raised when two contours cannot be compared at all."""


@dataclass(frozen=True)
class AlignResult:
    """One pairwise alignment: distance plus the transform that achieved it."""

    distance: float
    shift: int
    theta_rad: float
    mirrored: bool
    alternatives: int
    direct_distance: float
    mirror_distance: float | None = None

    @property
    def theta_deg(self) -> float:
        return float(np.rad2deg(self.theta_rad))

    def to_dict(self) -> dict[str, Any]:
        return {
            "align_distance": self.distance,
            "align_shift": self.shift,
            "align_theta_deg": self.theta_deg,
            "align_mirrored": self.mirrored,
            "align_alternative_shifts": self.alternatives,
            "align_direct_distance": self.direct_distance,
            "align_mirror_distance": self.mirror_distance,
        }


def _as_complex(points: Any, name: str) -> np.ndarray:
    """Accept an ``(N, 2)`` contour or an already-complex ``(N,)`` sequence."""
    if np.iscomplexobj(points):
        sequence = np.asarray(points, dtype=np.complex128)
        if sequence.ndim != 1:
            raise AlignmentError(f"{name} must be a 1-D complex sequence, got {sequence.shape}")
    else:
        array = np.asarray(points, dtype=float)
        if array.ndim != 2 or array.shape[1] != 2:
            raise AlignmentError(f"{name} must have shape (N, 2), got {array.shape}")
        sequence = array[:, 0] + 1j * array[:, 1]
    if sequence.shape[0] < 3:
        raise AlignmentError(f"{name} needs at least 3 points, got {sequence.shape[0]}")
    if not np.all(np.isfinite(sequence)):
        raise AlignmentError(f"{name} contains non-finite coordinates")
    return sequence


def complex_sequence(points: Any) -> np.ndarray:
    """Contour as ``x + i*y`` — the form every alignment formula below uses."""
    return _as_complex(points, "points")


def contour_energy(sequence: np.ndarray) -> float:
    """``mean |z_k|^2``: 1.0 for a unit-RMS contour, computed rather than assumed."""
    return float(np.mean(np.abs(sequence) ** 2))


def spectral(points: Any) -> np.ndarray:
    """DFT of a contour, or of every contour in an ``(M, N, 2)`` stack.

    This is the cached quantity an index keeps: aligning a query against the
    library then costs one FFT of the query plus a batched inverse transform.
    """
    if np.iscomplexobj(points):
        array = np.asarray(points, dtype=np.complex128)
        if array.ndim not in (1, 2):
            raise AlignmentError(f"complex sequence must be 1-D or (M, N), got {array.shape}")
    else:
        array = np.asarray(points, dtype=float)
        if array.ndim == 2 and array.shape[1] == 2:
            array = array[:, 0] + 1j * array[:, 1]
        elif array.ndim == 3 and array.shape[2] == 2:
            array = array[..., 0] + 1j * array[..., 1]
        else:
            raise AlignmentError(f"contour must be (N, 2) or (M, N, 2), got {array.shape}")
    if array.shape[-1] < 3:
        raise AlignmentError(f"a contour needs at least 3 points, got {array.shape[-1]}")
    if not np.all(np.isfinite(array)):
        raise AlignmentError("contour contains non-finite coordinates")
    return np.fft.fft(array, axis=-1)


def mirror_sequence(sequence: np.ndarray) -> np.ndarray:
    """Reflect in the x-axis and restore the counter-clockwise walk.

    ``b^m_k = conj(b_{-k})``: reflection alone reverses the traversal direction,
    and the canonical CCW mirror is therefore reflected *and* re-ordered. With
    this convention ``FFT(b^m) == conj(FFT(b))`` exactly, so a mirrored candidate
    costs one conjugation instead of a second FFT.
    """
    sequence = _as_complex(sequence, "sequence")
    return np.conj(np.roll(sequence[::-1], 1))


def mirror_points(points: Any) -> np.ndarray:
    """The same reflection written on ``(N, 2)`` arrays, for overlays."""
    sequence = mirror_sequence(_as_complex(points, "points"))
    return np.column_stack((sequence.real, sequence.imag))


def _cross_spectrum(a_hat: np.ndarray, b_hat: np.ndarray, n: int) -> np.ndarray:
    """``C_s = (1/N) sum_k a_k conj(b_{k+s})`` for every cyclic shift ``s``."""
    return np.fft.fft(a_hat * np.conj(b_hat), axis=-1) / float(n * n)


def _pick_best(energy_sum: float, coefficients: np.ndarray) -> tuple[float, int, float, int]:
    magnitude = np.abs(coefficients)
    best_index = int(np.argmax(magnitude))
    peak = float(magnitude[best_index])
    distance = float(np.sqrt(max(energy_sum - 2.0 * peak, 0.0)))
    tolerance = DEGENERACY_TOL * max(energy_sum, 1.0) / 2.0
    alternatives = int(np.count_nonzero(magnitude >= peak - tolerance))
    theta = float(np.angle(coefficients[best_index])) if peak > MIRROR_TOLERANCE else 0.0
    return distance, best_index, theta, alternatives


def align(
    query: Any,
    candidate: Any,
    *,
    allow_mirror: bool = False,
    query_hat: np.ndarray | None = None,
    candidate_hat: np.ndarray | None = None,
) -> AlignResult:
    """Best cyclic-start + rotation match of two contours.

    ``query_hat`` / ``candidate_hat`` accept precomputed spectra so a loop over a
    library does not repeat the transforms; they are the cached FFT of the same
    arrays, nothing else.
    """
    a = _as_complex(query, "query")
    b = _as_complex(candidate, "candidate")
    if a.shape[0] != b.shape[0]:
        raise AlignmentError(
            f"query and candidate must share the sample count, got {a.shape[0]} vs {b.shape[0]}"
        )
    n = a.shape[0]
    a_hat = np.fft.fft(a) if query_hat is None else np.asarray(query_hat)
    b_hat = np.fft.fft(b) if candidate_hat is None else np.asarray(candidate_hat)
    energy = contour_energy(a) + contour_energy(b)

    direct = _cross_spectrum(a_hat, b_hat, n)
    direct_distance, direct_shift, direct_theta, direct_alternatives = _pick_best(energy, direct)

    if not allow_mirror:
        return AlignResult(
            distance=direct_distance,
            shift=direct_shift,
            theta_rad=direct_theta,
            mirrored=False,
            alternatives=direct_alternatives,
            direct_distance=direct_distance,
            mirror_distance=None,
        )

    # FFT(mirror(b)) == conj(FFT(b)), so the mirrored pass is free once cached.
    mirrored = _cross_spectrum(a_hat, np.conj(b_hat), n)
    mirror_distance, mirror_shift, mirror_theta, mirror_alternatives = _pick_best(
        energy, mirrored
    )
    if mirror_distance < direct_distance - MIRROR_TOLERANCE:
        return AlignResult(
            distance=mirror_distance,
            shift=mirror_shift,
            theta_rad=mirror_theta,
            mirrored=True,
            alternatives=mirror_alternatives,
            direct_distance=direct_distance,
            mirror_distance=mirror_distance,
        )
    return AlignResult(
        distance=direct_distance,
        shift=direct_shift,
        theta_rad=direct_theta,
        mirrored=False,
        alternatives=direct_alternatives,
        direct_distance=direct_distance,
        mirror_distance=mirror_distance,
    )


def align_library(
    query: Any,
    library: Any,
    *,
    allow_mirror: bool = False,
    query_hat: np.ndarray | None = None,
    library_hat: np.ndarray | None = None,
    library_energy: np.ndarray | None = None,
) -> AlignmentVector:
    """Align one query against an ``(M, N, 2)`` stack (or cached spectra).

    Returns ``(distance, shift, theta_rad, mirrored)`` arrays of length ``M`` —
    the re-rank scores in row order of the library.
    """
    a = _as_complex(query, "query")
    stack = np.asarray(library, dtype=float)
    if stack.ndim != 3 or stack.shape[2] != 2:
        raise AlignmentError(f"library must be (M, N, 2), got {stack.shape}")
    if stack.shape[1] != a.shape[0]:
        raise AlignmentError(
            f"sample count mismatch: query {a.shape[0]} vs library {stack.shape[1]}"
        )
    n = a.shape[0]
    a_hat = np.fft.fft(a) if query_hat is None else np.asarray(query_hat)
    b_hat = (
        np.fft.fft(stack[..., 0] + 1j * stack[..., 1], axis=-1)
        if library_hat is None
        else np.asarray(library_hat)
    )
    energy_b = (
        np.mean(np.abs(b_hat / np.sqrt(n)) ** 2, axis=-1)  # Parseval, per candidate
        if library_energy is None
        else np.asarray(library_energy, dtype=float)
    )
    energy_a = contour_energy(a)
    coefficients = _cross_spectrum(a_hat[None, :], b_hat, n)
    energy_sum = energy_a + energy_b[:, None]
    every_shift = np.sqrt(np.maximum(energy_sum - 2.0 * np.abs(coefficients), 0.0))
    best = np.argmax(np.abs(coefficients), axis=1)
    rows = np.arange(best.shape[0])
    distance = every_shift[rows, best]
    shift = best.astype(int)
    theta = np.where(
        np.abs(coefficients[rows, best]) > MIRROR_TOLERANCE,
        np.angle(coefficients[rows, best]),
        0.0,
    )
    mirrored = np.zeros(best.shape[0], dtype=bool)
    if allow_mirror:
        mirror_coefficients = _cross_spectrum(a_hat[None, :], np.conj(b_hat), n)
        mirror_every_shift = np.sqrt(
            np.maximum(energy_sum - 2.0 * np.abs(mirror_coefficients), 0.0)
        )
        mirror_best = np.argmax(np.abs(mirror_coefficients), axis=1)
        mirror_distance = mirror_every_shift[rows, mirror_best]
        wins = mirror_distance < distance - MIRROR_TOLERANCE
        distances = np.where(wins, mirror_distance, distance)
        shifts = np.where(wins, mirror_best, shift)
        thetas = np.where(wins, np.angle(mirror_coefficients[rows, mirror_best]), theta)
        return distances, shifts.astype(int), thetas, wins
    return distance, shift.astype(int), theta, mirrored


def apply_alignment(
    points: Any, *, shift: int, theta_rad: float, mirrored: bool = False
) -> np.ndarray:
    """Move a candidate onto the query: reflect, roll by ``-shift``, rotate.

    The alignment convention is ``query_k <-> exp(i*theta) * candidate_(k+s)``,
    so the aligned candidate array is the candidate rolled left by ``shift`` and
    rotated by ``theta_rad`` (after mirroring, if that is what won).
    """
    sequence = _as_complex(points, "points")
    if mirrored:
        sequence = mirror_sequence(sequence)
    aligned = np.roll(sequence, -int(shift), axis=0) * np.exp(1j * float(theta_rad))
    return np.column_stack((aligned.real, aligned.imag))
