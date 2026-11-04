# P0-2 geometry probe — cleaning & standardized contour inspection

**Stage:** P0-2 (geometry cleaning + standardized contour extraction).
**Kind of result:** data *preprocessing* inspection. **These figures are NOT retrieval
results** — no query sketch exists yet, nothing here measures search quality.
**Data:** real Overture Maps base/water, release `2026-10-21.0`, bbox
`-90,44,-89,45` (Wisconsin/Mississippi border), 6,543 raw rows.
**Dataset inspected:** `data/processed/contours.parquet` (839 candidates) plus
`data/processed/rejected_samples.parquet` and `pending_analysis.parquet`.

Reproduce with:

```bash
python scripts/prepare_polygons.py \
    --input data/raw/wisconsin_water.parquet --out-dir data/processed --force
python scripts/visualize_geometry.py \
    --contours data/processed/contours.parquet \
    --out-dir experiments/p0_geometry_probe
```

The visualization is deterministic (no random seed): every panel is chosen by a
documented rule over the area-sorted candidate list or over shape metrics
(`summary.json` records the exact `candidate_id` of every panel).

## Figures

| file | content |
| --- | --- |
| `before_after.png` | A — 8 real candidates: raw source polygon (local aeqd metres) next to the stored 128-point normalized contour |
| `candidate_gallery.png` | B — 36 of 839 cleaned candidates, one per equal slice of the area-sorted list (0.108 ha → 115 ha) |
| `rejected_examples.png` | C — sampled rejections per reason with the rule text, plus kept-vs-rejected area histogram around the 0.1 ha floor |
| `complex_contours.png` | D — dendritic / crescent / holed / MultiPolygon-part / slender cases, raw vs normalized |
| `summary.json` | machine-readable provenance, counts and per-panel selection |

## Findings

1. **Normalization preserves shape.** In figure A every stored contour is visually
   congruent to its raw source polygon (same proportions, same bays and branches);
   only translation to centroid 0 and RMS-radius-1 scaling were applied. The two
   reservoir rows show that even 5,747 ha / 9,819 ha dendritic shorelines keep their
   silhouette at N=128.
2. **Candidate diversity is real and wide.** Figure B spans 0.108 ha ponds to 115 ha
   reservoirs and includes crescents, S-curves, winged and branching outlines.
   Subtype mix: pond 472 / lake 312 / reservoir 55.
3. **Filtering is explainable and not excessive.** Of 6,543 raw rows, 4,559 are
   Point/LineString (`wrong_type`, cannot become a closed contour), 1,034 are
   polygonal but outside the lake/pond/reservoir allowlist (`subtype_not_in_allowlist`,
   **kept** in `pending_analysis.parquet`, not deleted), 121 polygons fall below the
   0.1 ha floor. Figure C's histogram shows the floor sits below the bulk of the kept
   distribution and removes only genuinely tiny ponds (0.05–0.10 ha).
   Five reasons (`empty_geometry`, `null_geometry`, `unrepairable_after_make_valid`,
   `degenerate_ring`, `duplicate_high_overlap`) have **zero** occurrences in this real
   extract; they are exercised by synthetic unit tests only. The real file contains no
   invalid geometry, so `make_valid` never fired on real data.
4. **Complex shapes survive.** Figure D confirms the pipeline keeps, unchanged in
   proportion: a dendritic reservoir part (complexity 1995 vs 12.6 for a disc), a true
   crescent pond (`8d19ab82-63ee…`, single dominant hull bay), a 234-hole reservoir
   (holes counted in metadata, exterior-only contour), a simple MultiPolygon part with
   parent linkage, and an 18.3:1 slender lake. No aspect-ratio or "rectangular look"
   filter exists, per the task constraints.
5. **Known limitation — extreme shorelines are under-sampled.** The dendritic case
   (`4b2c4aea-…-part1`, 2,269.8 ha, ~213 km full boundary) and the 234-hole reservoir
   (`d7182ef0-…-part2`, 5,747.6 ha, ~193 km *exterior* perimeter) lose fine branches at
   N=128: worst-case arc-length step deviation reaches ~104 % on such features (median
   across candidates ~0.5 %). This is expected and is the reason the sampling count is
   an ablation parameter for P0-3 (D7), not a fixed constant.
6. **Frame contact is not clipping.** 8 candidates carry `extends_beyond_frame=true`;
   per the geometric evidence gathered in task #14 (0 of 1,984 usable polygons have a
   vertex or edge on the download frame) these are whole features whose bbox merely
   exceeds the query box, so they are kept and only flagged. See `docs/DECISIONS.md` D15.

## Metric definitions (figure D)

- `complexity = perimeter^2 / area` (4π ≈ 12.57 for a disc). Uses the **full** boundary
  length including interior rings; the stored `perimeter_m` column is exterior-only.
- `solidity = area / convex_hull.area`.
- `aspect_ratio` = long/short side of the minimum rotated rectangle.
- crescent rule: exactly one convex-hull deficit polygon covering >95 % of the hull
  deficit, solidity in [0.6, 0.9], complexity < 60, area ≥ 0.25 ha; deepest bay wins.
