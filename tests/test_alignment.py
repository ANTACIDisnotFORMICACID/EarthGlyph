"""Tests for cyclic-start / rotation alignment and the mirror switch (Task C3/B).

The closed-form solution is checked against a brute-force scan over every shift
and, independently, against the distance reconstructed from the reported
``(shift, theta)``. The invariance tests are the reason the re-rank can be called
a matching step rather than a similarity number: rotating or re-starting either
contour must not change the *best* distance, only which transform achieved it.

The mirror tests draw the line the task brief insists on: reflecting the plane is
not the same operation as walking the outline backwards, and the amplitude
descriptor cannot tell a mirror at all once the walk is canonicalized.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthglyph.features.fourier import harmonic_amplitudes
from earthglyph.geometry import (
    arc_length_resample,
    build_contour,
    ensure_ccw,
    normalize_contour,
    open_ring,
)
from earthglyph.retrieval.alignment import (
    AlignmentError,
    align,
    align_library,
    apply_alignment,
    complex_sequence,
    contour_energy,
    mirror_points,
    mirror_sequence,
    spectral,
)

N = 128


def raw_chiral(seed: int = 0) -> np.ndarray:
    """A dense, high-resolution outline with no mirror or rotational symmetry.

    Deliberately *not* pre-processed: the four-pipeline tests below feed it
    through the real chain (mirror / reverse / rotate, then CCW, resample,
    normalize) exactly as a query or a candidate would arrive.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * np.pi, 4096, endpoint=False)
    phase = rng.uniform(0.0, 2.0 * np.pi)
    radius = (
        1.0
        + 0.30 * np.cos(t)
        + 0.17 * np.sin(2 * t + phase)
        + 0.10 * np.cos(3 * t + 1.1 + 0.4 * phase)
        + 0.06 * np.sin(5 * t + 0.9 + phase)
    )
    return np.column_stack([radius * np.cos(t), 0.75 * radius * np.sin(t)])


def chiral_contour(seed: int = 0, n: int = N) -> np.ndarray:
    return build_contour(raw_chiral(seed), n).points


def rotate(points: np.ndarray, theta_rad: float) -> np.ndarray:
    c, s = np.cos(theta_rad), np.sin(theta_rad)
    return np.column_stack(
        [points[:, 0] * c - points[:, 1] * s, points[:, 0] * s + points[:, 1] * c]
    )


def brute_force(
    a: np.ndarray, b: np.ndarray
) -> tuple[float, int, float]:
    """min over every cyclic shift, with the closed-form rotation for that shift."""
    ac = complex_sequence(a)
    bc = complex_sequence(b)
    n = ac.shape[0]
    best = (float("inf"), 0, 0.0)
    for shift in range(n):
        rolled = np.roll(bc, -shift)
        correlation = np.sum(ac * np.conj(rolled)) / n
        theta = float(np.angle(correlation))
        distance = float(np.sqrt(np.mean(np.abs(ac - np.exp(1j * theta) * rolled) ** 2)))
        if distance < best[0]:
            best = (distance, shift, theta)
    return best


def direct_distance(a: np.ndarray, b: np.ndarray, shift: int, theta: float) -> float:
    return apply_distance(complex_sequence(a), complex_sequence(b), shift, theta)


def apply_distance(ac: np.ndarray, bc: np.ndarray, shift: int, theta: float) -> float:
    return float(np.sqrt(np.mean(np.abs(ac - np.exp(1j * theta) * np.roll(bc, -shift)) ** 2)))


# --- the closed form is the minimiser ---------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_distance_matches_brute_force_scan(seed: int) -> None:
    a = chiral_contour(seed)
    b = chiral_contour(seed + 5)
    result = align(a, b)
    distance, shift, theta = brute_force(a, b)
    assert result.distance == pytest.approx(distance, abs=1e-9)
    assert result.shift == shift
    assert result.theta_rad == pytest.approx(theta, abs=1e-9)


def test_self_match_is_zero_and_rotation_is_recovered_exactly() -> None:
    a = chiral_contour(3)
    result = align(a, rotate(a, 0.7))
    assert result.distance < 1e-6
    assert result.theta_rad == pytest.approx(-0.7, abs=1e-12)
    assert result.shift == 0


def test_reported_transform_reproduces_the_reported_distance() -> None:
    a = chiral_contour(4)
    b = rotate(chiral_contour(9), 1.9)
    result = align(a, b)
    reconstructed = direct_distance(a, b, result.shift, result.theta_rad)
    assert reconstructed == pytest.approx(result.distance, abs=1e-9)
    aligned = apply_alignment(b, shift=result.shift, theta_rad=result.theta_rad)
    offset = float(np.sqrt(np.mean(np.sum((a - aligned) ** 2, axis=1))))
    assert offset == pytest.approx(result.distance, abs=1e-9)


# --- invariances that make the score a matching step ------------------------


def test_rotation_of_either_contour_leaves_the_best_distance_unchanged() -> None:
    a = chiral_contour(6)
    b = chiral_contour(7)
    base = align(a, b).distance
    for angle in (0.3, 1.1, np.pi, -2.2):
        assert align(rotate(a, angle), b).distance == pytest.approx(base, abs=1e-9)
        assert align(a, rotate(b, angle)).distance == pytest.approx(base, abs=1e-9)


def test_cyclic_start_leaves_the_best_distance_unchanged() -> None:
    a = chiral_contour(8)
    b = chiral_contour(11)
    base = align(a, b)
    for shift in (1, 7, 64, 127):
        moved = align(a, np.roll(b, shift, axis=0))
        assert moved.distance == pytest.approx(base.distance, abs=1e-9)
        # the reported start absorbs the re-indexing exactly
        assert (moved.shift - base.shift) % N == shift % N


def test_scale_and_translation_are_removed_by_preprocessing_not_by_the_score() -> None:
    """A standardized candidate is invariant; a raw one is scored as it stands.

    The re-rank deliberately does not re-normalize its inputs: that is the
    query-side contract (Task A) and the index contract (P0-3), and silently
    rescaling here would hide a preprocessing bug instead of surfacing it.
    """
    a = chiral_contour(12)
    b = chiral_contour(13)
    base = align(a, b).distance
    scaled = normalize_contour(b * 4.5 + np.array([12.0, -7.0]))
    assert align(a, scaled).distance == pytest.approx(base, abs=1e-9)
    # ... and the same scale change left un-normalized is *not* invisible
    assert align(a, b * 4.5 + np.array([12.0, -7.0])).distance > 1.0


def test_symmetric_shapes_report_many_equivalent_optima() -> None:
    t = np.linspace(0.0, 2.0 * np.pi, N, endpoint=False)
    circle = normalize_contour(np.column_stack([np.cos(t), np.sin(t)]))
    square = normalize_contour(
        arc_length_resample(
            ensure_ccw(open_ring(np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], dtype=float))), N
        )
    )
    circle_result = align(circle, circle)
    assert circle_result.alternatives > N * 0.9
    square_result = align(square, rotate(square, np.pi / 4))
    assert square_result.alternatives >= 4
    assert square_result.distance < 1e-6


# --- mirror, direction and what the amplitudes cannot see -------------------


def test_mirroring_is_a_reflection_not_a_reversal() -> None:
    a = chiral_contour(14)
    reversed_walk = ensure_ccw(open_ring(a[::-1]))
    # walking backwards and re-canonicalizing to CCW returns the same contour
    assert align(a, normalize_contour(reversed_walk)).distance < 1e-9
    mirrored = mirror_points(a)
    assert align(a, mirrored).distance > 0.05
    assert a.shape == mirrored.shape
    assert np.max(np.abs(mirror_points(mirrored) - a)) < 1e-12


def test_mirror_switch_changes_the_answer_only_when_it_is_allowed() -> None:
    a = chiral_contour(15)
    mirrored_query = rotate(mirror_points(a), 0.9)
    strict = align(mirrored_query, a, allow_mirror=False)
    lenient = align(mirrored_query, a, allow_mirror=True)
    assert strict.distance > 0.05
    assert strict.mirrored is False
    assert strict.mirror_distance is None
    assert lenient.mirrored is True
    assert lenient.distance < 1e-7
    assert lenient.direct_distance == pytest.approx(strict.distance, abs=1e-9)
    assert lenient.mirror_distance == pytest.approx(lenient.distance, abs=1e-9)


def test_mirror_of_the_spectrum_is_the_conjugate() -> None:
    """The identity that makes the coarse filter mirror-blind, stated exactly."""
    a = chiral_contour(16)
    sequence = complex_sequence(a)
    assert np.max(np.abs(spectral(mirror_points(a)) - np.conj(spectral(sequence)))) < 1e-12
    assert np.max(np.abs(mirror_sequence(sequence) - complex_sequence(mirror_points(a)))) < 1e-12


def test_four_pipeline_variants_show_amplitudes_are_mirror_blind() -> None:
    """Task B: the four variants, each through the *real* standardization chain.

    ``build_contour`` is the production path (CCW unification -> arc-length
    resample -> center -> RMS scale), so variants 2 and 3 are canonicalized the
    way a query and a candidate are, not hand-preprocessed inside the test.
    """
    raw = raw_chiral(17)
    original = build_contour(raw, N).points
    mirrored = build_contour(raw * [-1.0, 1.0], N).points
    reversed_walk = build_contour(raw[::-1], N).points
    rotated = build_contour(rotate(raw, 0.9), N).points

    def fd(points: np.ndarray) -> np.ndarray:
        return harmonic_amplitudes(points, 16, fold="positive")

    base = fd(original)
    # 2. mirror -> CCW -> resample -> Fourier: the amplitudes survive to
    #    round-off, so the descriptor is *blind* to the reflection. The residual
    #    ~4e-8 is the arc-length grid landing on the mirrored walk at slightly
    #    different sub-segment positions, not a real difference.
    mirror_gap = float(np.max(np.abs(fd(mirrored) - base)))
    # 3. point-order reversal -> CCW -> resample -> Fourier: the same walk, so
    #    the descriptor is bit-identical. Reversal is *not* a mirror.
    reversal_gap = float(np.max(np.abs(fd(reversed_walk) - base)))
    # 4. rotation -> Fourier: only the phase moves
    rotation_gap = float(np.max(np.abs(fd(rotated) - base)))
    assert mirror_gap < 1e-6
    assert reversal_gap == 0.0
    assert rotation_gap < 1e-9

    # the same three pairs, scored in geometry instead of in amplitudes
    assert align(original, mirrored).distance > 0.05
    assert align(original, reversed_walk).distance < 1e-6
    assert align(original, rotated).distance == 0.0


def test_mirror_switch_is_enforced_by_geometry_not_by_amplitudes() -> None:
    """``allow_mirror`` decides, because the coarse vector cannot.

    The mirrored candidate is the *same* descriptor, so a filter that only
    compared amplitudes would rank it with distance 0 -- it cannot demote it
    either. The re-rank is where the policy lives: 0.075 blind, 0.001 allowed.
    """
    raw = raw_chiral(2)
    original = build_contour(raw, N).points
    mirrored = build_contour(raw * [-1.0, 1.0], N).points
    strict = align(original, mirrored, allow_mirror=False)
    lenient = align(original, mirrored, allow_mirror=True)
    assert strict.distance == pytest.approx(0.0749, abs=1e-3)
    assert lenient.mirrored is True
    assert lenient.distance < strict.distance / 10.0
    assert lenient.mirror_distance == pytest.approx(lenient.distance, abs=1e-12)


def test_amplitudes_are_invariant_to_the_freedoms_the_alignment_searches() -> None:
    """Coarse filter is invariant to rotation / start / mirror; re-rank is not.

    This is why the shortlist is only a shortlist: three shapes the descriptor
    scores as identical are separated by the geometric distance.
    """
    raw = raw_chiral(2)
    original = build_contour(raw, N).points
    variants = {
        "start": np.roll(original, 31, axis=0),
        "rotation": rotate(original, 1.3),
        "mirror": build_contour(raw * [-1.0, 1.0], N).points,
    }
    base = harmonic_amplitudes(original, 16, fold="positive")
    for points in variants.values():
        # round-off only: |FFT| is invariant to cyclic shift, rotation is a
        # phase, and the canonical mirror conjugates the spectrum
        assert np.max(np.abs(harmonic_amplitudes(points, 16, fold="positive") - base)) < 1e-6
    assert align(original, variants["start"]).distance < 1e-6
    assert align(original, variants["rotation"]).distance < 1e-6
    assert align(original, variants["mirror"]).distance > 0.05


# --- batch path -------------------------------------------------------------


def test_library_batch_matches_pairwise_results() -> None:
    query = chiral_contour(18)
    library = np.stack([chiral_contour(seed) for seed in range(19, 27)])
    distances, shifts, thetas, mirrored = align_library(query, library, allow_mirror=False)
    for index in range(library.shape[0]):
        result = align(query, library[index])
        assert distances[index] == pytest.approx(result.distance, abs=1e-9)
        assert shifts[index] == result.shift
        assert thetas[index] == pytest.approx(result.theta_rad, abs=1e-9)
        assert not mirrored[index]


def test_cached_spectra_and_energy_are_equivalent_to_recomputing() -> None:
    query = chiral_contour(19)
    library = np.stack([chiral_contour(seed) for seed in range(20, 26)])
    plain = align_library(query, library)
    cached = align_library(
        query,
        library,
        query_hat=spectral(query),
        library_hat=spectral(library),
        library_energy=np.mean(np.sum(library**2, axis=2), axis=1),
    )
    assert np.max(np.abs(plain[0] - cached[0])) < 1e-12
    assert np.array_equal(plain[1], cached[1])
    assert np.max(np.abs(plain[2] - cached[2])) < 1e-12


def test_library_energy_from_parseval_matches_the_direct_mean() -> None:
    library = np.stack([chiral_contour(seed) for seed in range(30, 36)])
    sequence = library[..., 0] + 1j * library[..., 1]
    direct = np.mean(np.abs(sequence) ** 2, axis=1)
    via_parseval = np.mean(np.abs(np.fft.fft(sequence, axis=-1)) ** 2, axis=-1) / N
    assert np.max(np.abs(direct - via_parseval)) < 1e-12
    assert contour_energy(complex_sequence(library[0])) == pytest.approx(direct[0])


def test_batch_mirror_decisions_match_the_pairwise_switch() -> None:
    query = rotate(mirror_points(chiral_contour(21)), 0.4)
    library = np.stack([chiral_contour(21), chiral_contour(22), mirror_points(chiral_contour(21))])
    distances, shifts, thetas, mirrored = align_library(query, library, allow_mirror=True)
    for index in range(library.shape[0]):
        result = align(query, library[index], allow_mirror=True)
        assert distances[index] == pytest.approx(result.distance, abs=1e-9)
        assert mirrored[index] == result.mirrored
        aligned = apply_alignment(
            library[index], shift=int(shifts[index]), theta_rad=float(thetas[index]),
            mirrored=bool(mirrored[index]),
        )
        offset = float(np.sqrt(np.mean(np.sum((query - aligned) ** 2, axis=1))))
        # the closed form subtracts two numbers of size ~1, so its floor is
        # sqrt(machine eps) ~ 2e-8; a direct re-evaluation can land lower
        assert offset == pytest.approx(result.distance, abs=1e-7)


def test_closed_form_has_a_machine_precision_floor() -> None:
    """How close to zero the score can be trusted: the floor, not the decimals.

    ``D^2 = E_a + E_b - 2|C|`` cancels two O(1) terms, so a perfect match reads
    ~2e-8 rather than 0. Any threshold below that would be comparing noise.
    """
    a = chiral_contour(27)
    perfect = align(a, a)
    floor = float(np.sqrt(np.finfo(float).eps))
    assert perfect.distance <= floor
    assert align(a, np.roll(a, 17, axis=0)).distance <= floor
    # ... and a real, small difference is still resolved far above the floor
    assert align(a, chiral_contour(28)).distance > 100.0 * floor


def test_energy_term_is_general_not_a_unit_rms_assumption() -> None:
    """The formula uses each contour's own energy, so a mis-scaled input is still
    scored consistently instead of silently treated as unit."""
    a = chiral_contour(23)
    assert contour_energy(complex_sequence(a)) == pytest.approx(1.0, abs=1e-9)
    assert align(a, 1.3 * a).distance == pytest.approx(0.3, abs=1e-9)
    assert align(a, 0.5 * a).distance == pytest.approx(0.5, abs=1e-9)


# --- input validation -------------------------------------------------------


def test_mismatched_sample_counts_and_bad_arrays_are_rejected() -> None:
    a = chiral_contour(25)
    with pytest.raises(AlignmentError, match="sample count"):
        align(a, a[:64])
    with pytest.raises(AlignmentError, match="at least 3 points"):
        align(a[:2], a[:2])
    with pytest.raises(AlignmentError, match="non-finite"):
        align(np.array([[0.0, np.nan], [1.0, 0.0], [0.0, 1.0]]), a)
    with pytest.raises(AlignmentError, match="shape"):
        align(a, a[:, :1])
    with pytest.raises(AlignmentError, match="library must be"):
        align_library(a, a)
    with pytest.raises(AlignmentError, match="sample count mismatch"):
        align_library(a, np.zeros((3, 64, 2)))


def test_energy_and_sequence_helpers() -> None:
    a = chiral_contour(26)
    assert contour_energy(complex_sequence(a)) == pytest.approx(1.0, abs=1e-9)
    assert spectral(a).shape == (N,)
