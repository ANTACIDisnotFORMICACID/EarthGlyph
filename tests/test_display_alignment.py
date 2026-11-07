"""Tests for the display-only pose fitter used by the Checkpoint D review sheets.

The claim under test is narrow and it matters: ``align_for_display`` may make a
picture easier to read and may not smuggle a matcher into the pipeline.  So these
tests check that it is rigid (no scale, no shear, no mirror), that it lowers the
chamfer it optimises, that it is deterministic enough to rebuild a figure from a
commit, that its cache cannot serve a stale contour, and that it borrows no
machinery from E0..E3.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import numpy as np
import pytest

from earthglyph.data.perceptual_gallery import (
    circle,
    regular_polygon,
    standardize,
    star,
)
from earthglyph.retrieval import display_alignment
from earthglyph.retrieval.display_alignment import align_for_display, cache_stats, reset_cache

N = 128


def ring(raw: Any) -> np.ndarray:
    """The library contract: 128 samples, centroid at origin, RMS radius 1."""
    return standardize(np.asarray(raw, dtype=float), n_points=N)


def rot(angle_deg: float) -> np.ndarray:
    theta = np.radians(angle_deg)
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    reset_cache()
    yield
    reset_cache()


class TestRigidOnly:
    def test_preserves_every_radius_so_no_scale_is_fitted(self) -> None:
        query = ring(star(5, 0.42))
        candidate = ring(regular_polygon(6))
        posed, info = align_for_display(query, candidate)
        assert np.allclose(
            np.linalg.norm(posed, axis=1), np.linalg.norm(candidate, axis=1), atol=1e-12
        )

    def test_preserves_pairwise_distances(self) -> None:
        from scipy.spatial.distance import pdist

        query = ring(circle())
        candidate = ring(star(4, 0.3))
        posed, _ = align_for_display(query, candidate)
        assert np.allclose(pdist(posed), pdist(candidate), atol=1e-10)

    def test_output_is_a_rotation_of_the_input_not_a_reflection(self) -> None:
        """``(x, y) -> (-x, y)`` would be a mirror, i.e. a different shape."""
        query = ring(star(5, 0.42))
        candidate = ring(regular_polygon(5))
        posed, info = align_for_display(query, candidate)
        matrix = rot(float(info["theta_deg"]))
        assert np.linalg.det(matrix) == pytest.approx(1.0, abs=1e-12)
        assert np.allclose(posed, candidate @ matrix.T, atol=1e-10)


class TestItAligns:
    def test_recovers_a_known_rotation_up_to_the_shapes_own_symmetry(self) -> None:
        base = ring(star(5, 0.40))
        rotated = base @ rot(37.0).T
        _, info = align_for_display(base, rotated)
        # the residual cannot reach 0: the two rings sample the same curve 128 times and
        # a rotation that is not a multiple of the arc step leaves the points interleaved,
        # so the floor is half an edge.  What is under test is that the sweep got to that
        # floor rather than to a wrong angle.
        steps = np.diff(np.vstack([base, base[:1]]), axis=0)
        edge = float(np.median(np.linalg.norm(steps, axis=1)))
        assert float(info["chamfer_after"]) < 0.75 * edge
        assert float(info["chamfer_after"]) < 0.25 * float(info["chamfer_before"])
        # a 5-pointed star is invariant under 72°, so any angle congruent to -37 is a
        # correct answer; the test is about the congruence, not the representative
        residual = (float(info["theta_deg"]) + 37.0) % 72.0
        assert min(residual, 72.0 - residual) < 1.5

    def test_lowers_the_cost_it_optimises(self) -> None:
        shape = ring(star(6, 0.35))
        _, info = align_for_display(shape, shape @ rot(100.0).T)
        assert info["chamfer_after"] < info["chamfer_before"]

    def test_a_circle_is_left_where_the_grid_starts(self) -> None:
        """No rotation can improve a circle, so the sweep must not wander."""
        _, info = align_for_display(ring(circle()), ring(circle()))
        assert info["chamfer_after"] == pytest.approx(info["chamfer_before"], abs=1e-12)


class TestDeterminismAndCache:
    def test_same_inputs_same_pose(self) -> None:
        query = ring(circle())
        candidate = ring(regular_polygon(7, phase_deg=13.0))
        first, a = align_for_display(query, candidate)
        second, b = align_for_display(query, candidate)
        assert np.array_equal(first, second)
        assert a["theta_deg"] == b["theta_deg"]

    def test_cache_returns_the_identical_pose_and_reports_a_hit(self) -> None:
        query = ring(star(5, 0.40))
        candidate = ring(regular_polygon(5))
        first, _ = align_for_display(query, candidate, key=("q", "c"))
        second, _ = align_for_display(query, candidate, key=("q", "c"))
        assert np.array_equal(first, second)
        assert cache_stats() == {"hits": 1, "misses": 1}

    def test_cache_cannot_serve_one_shape_under_another_ids_key(self) -> None:
        """The caller reuses id pairs; the contour hash is what keeps it honest."""
        query = ring(star(5, 0.40))
        pentagon = ring(regular_polygon(5))
        circle_ring = ring(circle())
        _, first = align_for_display(query, pentagon, key=("q", "c"))
        _, second = align_for_display(query, circle_ring, key=("q", "c"))
        assert first["chamfer_before"] != pytest.approx(second["chamfer_before"])
        assert cache_stats() == {"hits": 0, "misses": 2}


class TestIndependentOfTheArms:
    def test_imports_only_the_shared_input_contract(self) -> None:
        """A fitter that borrowed E0's alignment would not be a neutral display rule."""
        tree = ast.parse(inspect.getsource(display_alignment))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        assert modules <= {
            "__future__",
            "hashlib",
            "typing",
            "collections.abc",
            "numpy",
            "scipy.spatial.distance",
            "earthglyph.retrieval.perceptual_methods",
        }

    def test_exposes_no_score_and_no_ranking(self) -> None:
        """Everything public is a pose or a counter — nothing that could rank a library."""
        assert set(display_alignment.__all__) == {
            "align_for_display",
            "cache_stats",
            "reset_cache",
        }
        for name in display_alignment.__all__:
            assert callable(getattr(display_alignment, name))
