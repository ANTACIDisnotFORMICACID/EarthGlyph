# P0-4.6 notes — running log

Scope: independent 2D shape retrieval, **no geographic data**. Production retrieval
defaults, the water-body gallery and the geographic index are untouched this round.
Prior P0-4 / P0-4.5 results are not overwritten; the old gallery's `REL` labels are
**not** used as human truth anywhere here.

---

## Checkpoint 1 — sources and official implementation (2026-11-07)

### What was actually done

1. Re-probed every official resource entry point rather than trusting the earlier P0-4
   audit. Dropbox, `drive.google.com`, `drive.usercontent.google.com` and
   `sites.google.com` are all still blocked (connection refused / 21–30 s timeouts).
2. Confirmed no MATLAB, no Octave, no TensorFlow in `.venv`. Nothing commercial was
   installed and no license was purchased.
3. Built `earthglyph/shape_benchmark/remote_zip.py` — a seekable HTTP-Range file object —
   and enumerated **both** Zenodo archives' full central directories. This is the step
   that made the round possible: 8 MB transferred instead of 2.54 GB, and it let me read
   the authoritative `.m` code before deciding what to extract.
4. Read `view_rating_expt_stim_and_resp.m` and `Figure9.m`. These are the authors' own
   scripts and they document the exact data layout, so the loaders follow the published
   contract instead of guessing.
5. Extracted 9 members (~1.9 MB) and verified them numerically.

### Findings that change the plan

**F1 — Official code is unobtainable, but official *outputs* are published.**
Neither Zenodo record has the descriptor engine, a demo, `.py`, or weights. The larger
record 4892335 looked promising (132 `.m` vs 37) but the extra files are vendored
psignifit + Psychtoolbox. However `Fig5B_shapeStim4RatingExpt.mat` carries
`shape1SC`/`shape2SC` (22-D) and `dist`, and `dist == ‖SC1−SC2‖` to **4.44e-16**.
So `S0-MDS-precomputed` is a real arm on the 500 H1 stimuli.

*Constraint accepted:* S0 cannot score a new shape. Projecting one needs the unobtainable
109-descriptor engine + 25,712-shape reference corpus + Procrustes, or the unobtainable
network weights. **Therefore S0 is excluded from MPEG-7 Top-K retrieval** and appears
only in the H1/H2 pairwise human-prediction experiment. This is recorded per-arm, not
hidden in an aggregate.

**F2 — H1 is a complete, clean primary validation set.**
250 pairs × 14 observers, ratings 0–100, **zero NaN**, 500/500 unique contours (no
duplicate stimuli — the defect that invalidated P0-4.5). Participant columns are
preserved, so bootstrap can resample observers and pairs separately instead of treating
3500 ratings as independent.

**F3 — Both paper headline correlations reproduce from raw data.**
H1 Pearson −0.904 / Spearman −0.911 (paper: 0.91). H2 Pearson 0.778 (paper: 0.78).
This is a pipeline correctness check, not a new result, and is labelled as such.

**F4 — H2's human target is digitised, not raw.**
`Figure9.m` states verbatim that `x,y` were "acquired using getpts and image of figure 7D
from Li et al." So H2's human distance is a reconstruction of a published MDS arrangement
and carries digitisation error. Only 15 of 360 shapes are present (every 24th: ids
1, 25, …, 337). **H2 is auxiliary only.** It is never pooled with H1 into a single
"human correlation" number.

**F5 — H2's two `.mat` files disagree on the contours.**
`stim` is not array-identical between `fig7D_lietal_points_2.mat` and
`lietal_ShapeCompSolution_keras_mds_image.mat`. Investigated rather than assumed: after
centre + unit-RMS normalisation, the best cyclic-start + rotation alignment leaves a
worst mean absolute residual of **0.0082** (0.8% of unit RMS) over all 15 shapes. They are
the same shapes with different start points / resampling. Pairing is by the shared `n`
id. The canonical contour for display and for the EarthGlyph arms is taken from
`fig7D_lietal_points_2.mat`, which is the file plotted against the human `x,y`.

**F6 — The Figure 3 "GANratings" file is a trap and was rejected.**
`GANlabels.mat` contains free-text object *names* ("cow udder", "monkey", "hammer",
"italy", "x"). That is a naming/identifiability task. Despite the filename saying
"ratings", it is **not** similarity data and is not used as truth.

**F7 — Mendeley 2D Shape Structure is unreachable.**
API host `api.datasearch.elsevier.com` does not resolve; the dataset page returns HTTP 200
with `<title>FAQ</title>` and zero download URLs; the public-files endpoint 404s; the
guessable S3 zip 404s. Not acquired. Consequence: no part-structure annotations, so the
Part D3 structure probes use MPEG-7 real shapes plus **explicitly labelled synthetic**
primitives and are not claimed as human-validated.

### Decisions

- **D1.** No Python port of the 109 descriptors is written. The brief forbids relabeling a
  self-written descriptor set as a ShapeComp reproduction. E1 stays "ShapeComp-inspired".
- **D2.** MPEG-7 category labels are used for class-recall retrieval metrics only, never
  as human similarity.
- **D3.** Raw downloads stay in gitignored `cache/`; `dataset_manifest.json` carries the
  URLs + sha256 + transfer stats so they are rebuildable.
- **D4.** H1 and H2 are reported separately, always, with their different target semantics.

### Reproduce Checkpoint 1

```bash
.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py
```

Transfer: 8,072,354 B fetched / 9 Range requests for the 307,965,089 B Zenodo archive;
3,380,427 B for MPEG-7 (`sha256 ce8d87409d1b87b07352ebf6e0eafb8884bea6e4027cb7a23c228e7a3685bd0d`).
Per-member sha256 values are in `dataset_manifest.json`.

### Open items carried forward

- S0 local execution: **blocked**, needs a network that can reach Google/Dropbox plus a
  licensed MATLAB runtime, or the Keras weights plus TensorFlow. Not resolvable here.
- Li et al. full 360-shape set and raw trial-level judgments: not located on a reachable host.
- 2D Shape Structure part annotations: not acquired.

Commit sha for this checkpoint is recorded in `docs/TASKS.md`.

---

## Checkpoint 2 — data loaders and unified retrieval (2026-11-07)

### Built

- `earthglyph/shape_benchmark/datasets.py` — H1 / H2 / MPEG-7 loaders. Each shape carries
  both the shared 128-point algorithm contract (`points`) and its original published pose
  (`display_points`, figures only, never ranked on).
- `earthglyph/shape_benchmark/retrieval.py` — `Gallery` (per-arm corpora precomputed once)
  and `search_shape(query, gallery, method, top_k=10, exclude=...)`. Returns the **full**
  ranking, not just Top-K, plus per-candidate distances and wall-clock time.
- `scripts/search_shape_benchmark.py` — the required CLI. `--gallery` accepts `mpeg7`,
  `h1`, or an image directory; `--query` accepts an image path or a gallery shape_id.
- `tests/test_shape_benchmark.py` — 18 tests. The Range reader is tested against a local
  HTTP server that actually honours `Range`, including a 1 MB member that must *not* be
  fetched. Dataset tests skip when the cache is absent so the suite runs offline.

### Findings

**F8 — The published contours need explicit ring closure.**
`contour_from_points(closed=True)` refused the H1 contours: pair 0's wrap chord is 0.196 of
its RMS radius, over the sketch loader's 0.15 open-stroke limit. Investigated rather than
loosened the guard: the median step is 0.0124 while the wrap chord is 0.0950, i.e. ~7.6
steps, because the published sampling is non-uniform (step range 0.004–0.114). These are
closed polygons that MATLAB `patch` seals implicitly. Repeating the first vertex states
that closure explicitly; **500/500 H1 and 15/15 H2** contours then standardize cleanly.
The sketch guard was left untouched — it is correct for user sketches.

**F9 — MPEG-7 polarity needs no manual inversion.**
`_binary_mask` detects polarity from the border median, so the white-on-black GIFs work
as-is. Verified on all 1400: **1398 extract, 2 genuinely fail** (`pocket-11`, `pocket-12`
each contain two disconnected blobs). Both are recorded in an exclusion ledger and
returned by the loader rather than silently repaired. Gallery = 1398 shapes / 70 classes.

**F10 — E3's cost is query-side, so descriptor caching does not rescue it.**
Measured on 200 candidates: `build_library_descriptors` 0.08 s, but scoring still
41.9 ms/candidate, because the 24-rotation × Hungarian assignment runs per query.
E1 is the opposite: with a supplied corpus normalizer and cached features, scoring 200
candidates drops to 0.01 s. E0 is 0.1 ms/candidate.

*Consequence for the design:* the human-rating experiment (D1) computes only the **250
pairs that were actually rated**, via `pair_distance`, instead of ranking 500 shapes 250
times. Full-gallery ranking is reserved for the retrieval experiments (D2–D4).

**F11 — E1 needs one corpus normalizer, fitted once.**
`perceptual_features.score_library` self-fits a minmax normalizer on whatever library it
is handed when none is supplied. That would make distances incomparable across queries and
across exclusion conditions. `Gallery` therefore fits one normalizer on the full candidate
set and passes it explicitly to every call.

**F12 — `rank_order` rejects non-finite distances.**
Excluded candidates are held as `+inf` to keep the distance array gallery-aligned, so
ranking is done on the finite subset and mapped back to gallery indices. Stable
tie-breaking is preserved.

### Fairness contract

E0 / E1 / E3 all receive the identical 128-point centred/unit-RMS/CCW ring and the identical
candidate set. S0 is different in kind and says so: it consumes the authors' published
22-D coordinates, produced by their own pipeline (384×2 min-max contour, or the KerNet2
40×40 image route for H2). No attempt is made to force S0 onto the unified preprocessing,
because that would break the official input contract. S0 is unavailable on MPEG-7 and
raises an explicit error rather than returning a fabricated score.

### Verify

```bash
.venv/Scripts/python.exe -m pytest -q          # 744 passed
.venv/Scripts/python.exe -m ruff check .       # All checks passed
.venv/Scripts/python.exe -m mypy               # no issues in 36 source files
.venv/Scripts/python.exe scripts/search_shape_benchmark.py --query mpeg7_Heart-1 --gallery mpeg7 --method E0 --top-k 5
.venv/Scripts/python.exe scripts/search_shape_benchmark.py --query h1_0_a --gallery h1 --method S0 --top-k 4
```

The E0 call returns Heart-1 then Heart-6/8/11/19 (all same class); the S0 call returns
`h1_0_a` at distance 0 and its rated partner `h1_0_b` at 0.71253, matching the published
`dist` field exactly.

Commit sha for this checkpoint is recorded in `docs/TASKS.md`.