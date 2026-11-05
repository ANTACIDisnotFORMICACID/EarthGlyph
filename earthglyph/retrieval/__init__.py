"""Similarity retrieval: coarse feature filtering -> geometric re-ranking -> dedup.

Two stages, as designed in ``docs/RETRIEVAL_DESIGN.md``:

* :mod:`~earthglyph.retrieval.coarse` ranks the P0-3 feature index group by group
  and returns a shortlist — a cheap set of plausible candidates, nothing more;
* :mod:`~earthglyph.retrieval.alignment` scores the shortlist by actually aligning
  the 128-point contours (cyclic start + rotation, closed form, mirror switch);
* :mod:`~earthglyph.retrieval.chamfer` offers the nearest-neighbour cross-check
  that decides whether a correspondence-free score adds anything;
* :mod:`~earthglyph.retrieval.hu_baseline` is the ``cv2`` Hu-moment reference;
* :mod:`~earthglyph.retrieval.query` turns a hand trace or sketch image into the
  vectors both stages read, and :mod:`~earthglyph.retrieval.search` glues it to an
  index and returns geography plus provenance.
"""

from earthglyph.retrieval.alignment import (
    AlignmentError,
    AlignResult,
    align,
    align_library,
    apply_alignment,
    mirror_points,
    spectral,
)
from earthglyph.retrieval.chamfer import (
    ChamferError,
    chamfer_after_alignment,
    chamfer_distance,
    chamfer_library,
    densify,
)
from earthglyph.retrieval.coarse import (
    CoarseError,
    Shortlist,
    column_reference,
    group_columns,
    rank,
    shortlist_overlap,
)
from earthglyph.retrieval.hu_baseline import (
    BaselineError,
    HuRanking,
    hu_moments,
    match_shapes,
    rank_by_hu,
    rasterize,
)
from earthglyph.retrieval.query import (
    QueryError,
    QueryVector,
    feature_columns,
    prepare,
    vectors_for,
)
from earthglyph.retrieval.search import (
    METHODS,
    Hit,
    IndexHandle,
    SearchError,
    SearchResult,
    load_index_handle,
    search_by_contour,
    search_by_image,
)

__all__ = [
    "METHODS",
    "AlignResult",
    "AlignmentError",
    "BaselineError",
    "ChamferError",
    "CoarseError",
    "Hit",
    "HuRanking",
    "IndexHandle",
    "QueryError",
    "QueryVector",
    "SearchError",
    "SearchResult",
    "Shortlist",
    "align",
    "align_library",
    "apply_alignment",
    "chamfer_after_alignment",
    "chamfer_distance",
    "chamfer_library",
    "column_reference",
    "densify",
    "feature_columns",
    "group_columns",
    "hu_moments",
    "load_index_handle",
    "match_shapes",
    "mirror_points",
    "prepare",
    "rank",
    "rank_by_hu",
    "rasterize",
    "search_by_contour",
    "search_by_image",
    "shortlist_overlap",
    "spectral",
    "vectors_for",
]
