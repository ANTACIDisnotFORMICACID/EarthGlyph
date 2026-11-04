"""Tests for the Fourier shape descriptor (P0-3, Task A + Task G).

Every invariance claimed in ``earthglyph/features/fourier.py`` is asserted here,
and so is every invariance the module *denies*: amplitude descriptors cannot
tell a reversed traversal from a mirrored shape, and they cannot pin a curve
down because phase is discarded. Those negative results are tests too — a
future change that quietly claims more invariance than the math supports will
fail here.

All fixtures are synthetic shapes used only as mathematical inputs; no
retrieval claim is made from them.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthglyph.features.fourier import (
    DEFAULT_FOLD,
    DEFAULT_N_HARMONICS,
    FeatureError,
    as_complex_sequence,
    batch_harmonic_amplitudes,
    check_fold,
    check_harmonics,
    descriptor_columns,
    feature_definition,
    fourier_coefficients,
    harmonic_amplitudes,
    harmonic_energy_fractions,
    harmonic_limit,
)
from earthglyph.geometry import arc_length_resample, normalize_contour, open_ring

N_POINTS = 128


def ring(points: list[tuple[float, float]]) -> np.ndarray:
    """Close a vertex list the way ``exterior_ring`` returns it."""
    arr = np.asarray(points, dtype=float)
    return np.vstack([arr, arr[:1]])


def sampled(closed: np.ndarray, n: int = N_POINTS) -> np.ndarray:
    """Standardized (unit RMS radius, centroid at origin) contour of a ring."""
    return normalize_contour(arc_length_resample(open_ring(closed), n))


def circle(radius: float = 100.0, n: int = N_POINTS) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])


def ellipse(a: float = 300.0, b: float = 90.0, n: int = N_POINTS) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([a * np.cos(angle), b * np.sin(angle)])


def arrow(n: int = N_POINTS) -> np.ndarray:
    """An asymmetric, non-convex outline: rotation of a shape must not be flat."""
    return sampled(
        ring(
            [
                (0.0, 0.0),
                (60.0, 8.0),
                (58.0, 30.0),
                (120.0, 40.0),
                (60.0, 55.0),
                (62.0, 80.0),
                (0.0, 60.0),
                (-8.0, 30.0),
            ]
        ),
        n,
    )


def rotate(points: np.ndarray, theta: float) -> np.ndarray:
    cosine, sine = np.cos(theta), np.sin(theta)
    return points @ np.array([[cosine, -sine], [sine, cosine]]).T


# --- determinism and basic shape of the output -------------------------------


def test_descriptor_is_deterministic() -> None:
    contour = arrow()
    first = harmonic_amplitudes(contour, 16)
    second = harmonic_amplitudes(contour, 16)
    assert np.array_equal(first, second)
    assert first.shape == (16,)


def test_descriptor_is_non_negative_and_finite() -> None:
    for contour in (circle(), ellipse(), arrow()):
        values = harmonic_amplitudes(contour, 24)
        assert np.all(np.isfinite(values))
        assert np.all(values >= 0.0)


def test_dc_term_is_centroid_and_never_in_the_descriptor() -> None:
    shifted = arrow() + np.array([1200.0, -800.0])
    coefficients = fourier_coefficients(shifted, normalize=False)
    centroid = shifted.mean(axis=0)
    # The raw DFT sum is N times the centroid; the descriptor divides by N.
    assert np.isclose(coefficients[0].real / len(shifted), centroid[0], rtol=0.0, atol=1e-9)
    assert np.isclose(coefficients[0].imag / len(shifted), centroid[1], rtol=0.0, atol=1e-9)
    # The descriptor starts at k=1, so the position term cannot leak in.
    assert np.allclose(
        harmonic_amplitudes(shifted, 16), harmonic_amplitudes(arrow(), 16), atol=1e-12
    )


# --- claimed invariances ----------------------------------------------------


@pytest.mark.parametrize("offset", [(1200.0, -800.0), (0.0, 0.0), (-5.0, 9.0)])
def test_translation_invariant(offset: tuple[float, float]) -> None:
    contour = arrow()
    moved = contour + np.asarray(offset, dtype=float)
    assert np.allclose(harmonic_amplitudes(moved, 16), harmonic_amplitudes(contour, 16), atol=1e-12)


@pytest.mark.parametrize("factor", [0.017, 1.0, 3.7, 940.0])
def test_uniform_scale_invariant(factor: float) -> None:
    contour = ellipse()
    assert np.allclose(
        harmonic_amplitudes(contour * factor, 16), harmonic_amplitudes(contour, 16), atol=1e-12
    )


@pytest.mark.parametrize("theta", [0.1, np.pi / 2, 2.3, np.pi])
def test_rotation_invariant_in_amplitudes(theta: float) -> None:
    contour = arrow()
    for fold in ("positive", "symmetric"):
        base = harmonic_amplitudes(contour, 16, fold=fold)
        turned = harmonic_amplitudes(rotate(contour, theta), 16, fold=fold)
        assert np.allclose(turned, base, atol=1e-11)


@pytest.mark.parametrize("shift", [1, 7, 64, 127])
def test_cyclic_start_invariant(shift: int) -> None:
    contour = arrow()
    for fold in ("positive", "symmetric"):
        base = harmonic_amplitudes(contour, 16, fold=fold)
        rolled = harmonic_amplitudes(np.roll(contour, shift, axis=0), 16, fold=fold)
        assert np.allclose(rolled, base, atol=1e-11)


def test_rotation_does_not_make_every_shape_identical() -> None:
    """Invariance must not come from a descriptor that collapsed to a constant."""
    turned = harmonic_amplitudes(rotate(arrow(), 1.1), 16)
    other = harmonic_amplitudes(circle(), 16)
    assert np.linalg.norm(turned - other) > 1e-3


# --- denials: what the descriptor is NOT invariant to ------------------------


def test_positive_fold_is_direction_sensitive() -> None:
    contour = arrow()
    assert not np.allclose(
        harmonic_amplitudes(contour[::-1], 16, fold="positive"),
        harmonic_amplitudes(contour, 16, fold="positive"),
        atol=1e-9,
    )


def test_positive_fold_is_mirror_sensitive() -> None:
    contour = arrow()
    mirrored = contour * np.array([1.0, -1.0])
    assert not np.allclose(
        harmonic_amplitudes(mirrored, 16, fold="positive"),
        harmonic_amplitudes(contour, 16, fold="positive"),
        atol=1e-9,
    )


@pytest.mark.parametrize("fold", ["positive", "symmetric"])
def test_reversal_and_mirror_are_the_same_operation_on_amplitudes(fold: str) -> None:
    """The k -> -k mapping is shared, so amplitudes cannot separate the two.

    This is the guard against the D9/D10 confusion: a "mirror-invariant" claim
    about an amplitude descriptor is really a direction-and-mirror statement.
    """
    contour = arrow()
    reversed_amplitudes = harmonic_amplitudes(contour[::-1], 16, fold=fold)
    mirrored_amplitudes = harmonic_amplitudes(contour * np.array([1.0, -1.0]), 16, fold=fold)
    assert np.allclose(reversed_amplitudes, mirrored_amplitudes, atol=1e-12)


def test_symmetric_fold_is_mirror_and_direction_invariant() -> None:
    contour = arrow()
    base = harmonic_amplitudes(contour, 16, fold="symmetric")
    assert np.allclose(base, harmonic_amplitudes(contour[::-1], 16, fold="symmetric"), atol=1e-11)
    assert np.allclose(
        base,
        harmonic_amplitudes(contour * np.array([1.0, -1.0]), 16, fold="symmetric"),
        atol=1e-11,
    )


def test_symmetric_fold_is_the_average_of_the_two_sides() -> None:
    contour = arrow()
    amplitudes = np.abs(np.fft.fft(as_complex_sequence(contour))) / N_POINTS
    k = np.arange(1, 17)
    expected = 0.5 * (amplitudes[k] + amplitudes[(-k) % N_POINTS])
    assert np.allclose(harmonic_amplitudes(contour, 16, fold="symmetric"), expected, atol=1e-12)


def test_amplitudes_alone_do_not_determine_the_curve() -> None:
    """Zeroing the phase keeps the descriptor identical but changes the shape."""
    contour = arrow()
    spectrum = np.fft.fft(as_complex_sequence(contour))
    spectrum[0] = 0.0
    rebuilt = np.fft.ifft(np.abs(spectrum))
    impostor = np.column_stack([rebuilt.real, rebuilt.imag])
    assert np.allclose(
        harmonic_amplitudes(impostor, 16), harmonic_amplitudes(contour, 16), atol=1e-12
    )
    assert np.max(np.abs(impostor - contour)) > 1e-2


# --- dimension choices ------------------------------------------------------


@pytest.mark.parametrize("n_harmonics", [1, 4, 12, 16, 24])
def test_descriptor_is_a_prefix_of_the_longer_one(n_harmonics: int) -> None:
    contour = arrow()
    long = harmonic_amplitudes(contour, 24)
    assert np.allclose(harmonic_amplitudes(contour, n_harmonics), long[:n_harmonics], atol=1e-15)


def test_harmonic_limit_is_nyquist_minus_one() -> None:
    assert harmonic_limit(128) == 63
    assert harmonic_limit(64) == 31
    with pytest.raises(FeatureError, match="Nyquist"):
        harmonic_amplitudes(circle(100.0, 64), 32)


# --- comparability across sampling resolution ------------------------------


def test_per_sample_normalization_makes_different_n_comparable() -> None:
    closed = ring([(0.0, 0.0), (200.0, 10.0), (210.0, 120.0), (60.0, 150.0), (-20.0, 70.0)])
    coarse = harmonic_amplitudes(sampled(closed, 64), 12)
    fine = harmonic_amplitudes(sampled(closed, 256), 12)
    assert np.linalg.norm(coarse - fine) / np.linalg.norm(fine) < 0.05


def test_unnormalized_dft_grows_with_the_sample_count() -> None:
    """``per_sample=False`` is the raw DFT sum, so it scales with N (documented)."""
    closed = ring([(0.0, 0.0), (200.0, 10.0), (210.0, 120.0), (60.0, 150.0), (-20.0, 70.0)])
    coarse = sampled(closed, 64)
    fine = sampled(closed, 256)
    raw_ratio = np.linalg.norm(
        harmonic_amplitudes(fine, 8, per_sample=False)
    ) / np.linalg.norm(harmonic_amplitudes(coarse, 8, per_sample=False))
    normalized_ratio = np.linalg.norm(
        harmonic_amplitudes(fine, 8, per_sample=True)
    ) / np.linalg.norm(harmonic_amplitudes(coarse, 8, per_sample=True))
    assert raw_ratio == pytest.approx(256.0 / 64.0, rel=0.05)
    assert normalized_ratio == pytest.approx(1.0, rel=0.05)


# --- batch path -------------------------------------------------------------


def test_batch_matches_single_contour_exactly() -> None:
    contours = np.stack([circle(), ellipse(), arrow(), rotate(arrow(), 0.7)])
    batch = batch_harmonic_amplitudes(contours, 16)
    for row, contour in enumerate(contours):
        assert np.allclose(batch[row], harmonic_amplitudes(contour, 16), atol=0.0)
    assert batch.shape == (4, 16)


def test_batch_symmetric_matches_single() -> None:
    contours = np.stack([arrow(), ellipse()])
    batch = batch_harmonic_amplitudes(contours, 12, fold="symmetric")
    for row, contour in enumerate(contours):
        assert np.allclose(
            batch[row], harmonic_amplitudes(contour, 12, fold="symmetric"), atol=0.0
        )


# --- energy fractions -------------------------------------------------------


def test_energy_fractions_are_monotone_and_bounded() -> None:
    fractions = harmonic_energy_fractions(arrow(), 24)
    assert fractions.shape == (24,)
    assert np.all(np.diff(fractions) >= -1e-15)
    assert np.all((fractions > 0.0) & (fractions <= 1.0 + 1e-12))


def test_energy_fraction_matches_the_definition() -> None:
    contour = arrow()
    power = np.abs(np.fft.fft(as_complex_sequence(contour))) ** 2
    expected = power[1:13].sum() / power[1:].sum()
    assert np.isclose(harmonic_energy_fractions(contour, 12)[-1], expected, rtol=1e-12)


# --- illegal and degenerate input ------------------------------------------


def test_rejects_non_finite_coordinates() -> None:
    contour = arrow()
    contour[3, 1] = np.nan
    with pytest.raises(FeatureError, match="non-finite"):
        harmonic_amplitudes(contour, 16)


def test_rejects_too_few_points() -> None:
    with pytest.raises(FeatureError, match="at least 3"):
        harmonic_amplitudes(np.array([[0.0, 0.0], [1.0, 1.0]]), 16)


def test_rejects_wrong_shape() -> None:
    with pytest.raises(FeatureError, match="shape"):
        harmonic_amplitudes(np.zeros((10, 3)), 16)


def test_rejects_zero_size_contour() -> None:
    with pytest.raises(FeatureError, match="degenerate"):
        harmonic_amplitudes(np.ones((16, 2)) * 5.0, 16)


def test_rejects_collapsed_line_contour() -> None:
    """A collinear ring is not degenerate for the DFT, but it has no area.

    The descriptor is still defined, so this is asserted rather than hidden: the
    degenerate-shape guard belongs to the geometry statistics, not to Fourier.
    """
    line = np.column_stack([np.linspace(0.0, 100.0, 32), np.zeros(32)])
    values = harmonic_amplitudes(line, 8)
    assert np.all(np.isfinite(values))


def test_rejects_n_harmonics_below_one() -> None:
    with pytest.raises(FeatureError, match="n_harmonics"):
        harmonic_amplitudes(arrow(), 0)


def test_rejects_unknown_fold() -> None:
    with pytest.raises(FeatureError, match="fold"):
        harmonic_amplitudes(arrow(), 16, fold="bogus")  # type: ignore[arg-type]
    with pytest.raises(FeatureError, match="fold"):
        check_fold("positive-ish")


def test_check_harmonics_agrees_with_the_descriptor() -> None:
    check_harmonics(128, 63)
    with pytest.raises(FeatureError, match="Nyquist"):
        check_harmonics(128, 64)


def test_batch_rejects_non_finite_in_any_row() -> None:
    contours = np.stack([arrow(), arrow()])
    contours[1, 0, 0] = np.inf
    with pytest.raises(FeatureError, match="non-finite"):
        batch_harmonic_amplitudes(contours, 16)


# --- specification ----------------------------------------------------------


def test_descriptor_columns_and_definition() -> None:
    columns = descriptor_columns(16, "positive")
    assert len(columns) == 16
    assert columns[0] == "fd_01"
    assert columns[-1] == "fd_16"
    assert descriptor_columns(4, "symmetric") == ["fds_01", "fds_02", "fds_03", "fds_04"]
    definition = feature_definition(DEFAULT_N_HARMONICS, DEFAULT_FOLD)
    assert definition["columns"] == columns
    assert definition["n_harmonics"] == DEFAULT_N_HARMONICS
    assert definition["fold"] == "positive"
    assert definition["normalized_by_sample_count"] is True
    assert "DFT" in definition["definition"]
    assert any("phase" in loss.lower() for loss in definition["known_losses"])
    assert "coarse" in definition["usage"]


def test_definition_states_the_denied_invariances() -> None:
    """The spec must say what the descriptor cannot do, in the manifest itself."""
    positive = feature_definition(16, "positive")
    denial = [entry for entry in positive["invariances"] if entry.startswith("NOT invariant")]
    assert len(denial) == 1
    assert "reversal" in denial[0] and "mirroring" in denial[0]
    symmetric = feature_definition(16, "symmetric")
    assert "point-order reversal" in symmetric["invariances"]
    assert not any(entry.startswith("NOT invariant") for entry in symmetric["invariances"])
    assert "not a mirror test" in positive["usage"]
