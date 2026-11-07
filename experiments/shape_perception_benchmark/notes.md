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