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

---

## D1 — human-rating prediction (2026-11-07, run before Checkpoint 3)

Run: `.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3`
(`main()` appends S0 for d1 only). 40.7 s total. Products:
`results/pairwise_predictions.csv` (350 rows = 250 H1 + 105 H2, every pair, no selection),
`results/human_metrics.json`, `results/failure_cases.csv` (all 250 H1 pairs, sorted).

### Direction is unified before anything is compared

H1's published target is **similarity** (higher = more similar); H2's is a **distance** in a
digitised arrangement. Raw correlations therefore have opposite signs for equally good models.
`human_metrics.json` keeps the raw signed value *and* an `agreement_*` restatement in which
**positive always means "agrees with human dissimilarity"**. H1 and H2 are still never pooled:
different pairs, different observers, different target semantics.

### Result on H1 — 250 pairs, 14 observers, nothing selected

| Arm | Agreement ρ | 95% CI (pairs **and** observers) | median \|ρ\| vs one observer | H1 wall time |
|---|---|---|---|---|
| **S0** official ShapeComp 22-D (published coords) | **0.9108** | [0.856, 0.918] | 0.750 | n/a (published) |
| **E3** Shape Context + rotation search | **0.8391** | [0.762, 0.868] | 0.692 | 13.2 s |
| **E1** EarthGlyph ShapeComp-inspired, 91-D, ours | **0.7824** | [0.703, 0.819] | 0.640 | 4.4 s |
| **E0** geometric alignment | **0.6831** | [0.589, 0.736] | 0.579 | 0.04 s |

Pearson (same direction convention): S0 0.904, E3 0.824, E1 0.765, E0 0.701.

**F13 — The point estimates are strictly ordered, but the marginal intervals overlap.**
S0 > E3 > E1 > E0 on both Spearman and Pearson. **The marginal CIs overlap between every
adjacent pair** (S0 [0.856, 0.918] vs E3 [0.762, 0.868], etc.), so overlapping intervals alone
would not license a "significantly better" claim. Both arms are measured on the *same* pairs and
the *same* observers, so the correct test is a **paired** two-level bootstrap of the difference.
All six pairwise differences exclude zero (`human_metrics.json → H1.paired_differences`):

| Contrast | Δ agreement | 95% CI | bootstrap share Δ>0 |
|---|---|---|---|
| E3 − E0 | +0.1560 | [+0.0882, +0.2180] | 1.0000 |
| E1 − E0 | +0.0993 | [+0.0376, +0.1614] | 0.9990 |
| S0 − E0 | +0.2277 | [+0.1534, +0.2998] | 1.0000 |
| S0 − E3 | +0.0717 | [+0.0242, +0.1293] | 0.9985 |
| S0 − E1 | +0.1284 | [+0.0764, +0.1848] | 1.0000 |
| E3 − E1 | +0.0567 | [+0.0058, +0.1070] | 0.9870 |

The weakest contrast is E3 over E1 (lower bound +0.006), so "E3 beats E1" is supported but
fragile; "E3/E1 beat E0" and "S0 beats all three" are not. This is the first evidence in this
project that a *perception-validated* model beats the geometric matchers on **independent human
ratings** rather than on a self-built label.

**F14 — The participant level changes the answer's confidence, not its direction.**
Resampling pairs only gives E3 [0.762, 0.868]; resampling pairs **and** observers gives the same
point estimate with a slightly wider interval. Treating the 3500 ratings as independent draws
would have been wrong; `bootstrap_two_level` draws pairs with replacement and observers with
replacement, then averages the drawn observers before correlating.

**F15 — There is a measurable human noise ceiling, and it must be quoted alongside the models.**
On the same 250 pairs: observer-vs-observer Spearman median **0.601** (91 pairs, range
0.397–0.762); observer-vs-mean-of-the-other-13 median **0.779** (range 0.583–0.856).
E3's per-observer median 0.692 sits *inside* that band; S0's 0.750 sits near its top.
A model has no observation noise, so exceeding a single observer's leave-one-out score is
expected and is **not** evidence of superhuman perception — the model is being compared against
a 14-observer mean, which is less noisy than a 13-observer mean.

**F16 — E3's largest failures are systematic, not random.**
Rule (stated before looking): rank all 250 pairs by
`|distance percentile − human dissimilarity percentile|` for E3; show the top 6.
**5 of the 6 are negative** — E3 calls the pair *closer* than humans do. The shapes involved are
elongated / branchy GAN outlines (pairs 217, 96, 189, 247, 18). E3's rotation-searched Shape
Context is too permissive when one shape has limbs the other lacks. S0's disagreement on the
same six is ≤0.17 in five cases. Pair 194 is the one where E3 errs in the other direction
(+0.369) and S0 also errs (+0.072). `failure_cases.csv` keeps all 250 rows, so the cut hides nothing.

### Auxiliary H2 — 105 pairs, digitised target, reported separately

Agreement ρ: S0 **0.7656**, E0 **0.7203**, E3 **0.6936**, E1 **0.6584**.
The ordering is **not** the same as H1 (E0 rises to second). With 15 shapes, a digitised
arrangement and no per-trial ratings, this is a consistency check on the loaders, not a
second independent confirmation. It is never averaged with H1.

### What D1 cannot claim

- S0 here is the authors' **published coordinates**, not a local run of their code. No claim is
  made about reproducing their pipeline, their training, or their network accuracy.
- H1's 500 stimuli are GAN shapes drawn from the same generator family the authors used to train
  MatNet/KerNet. **S0's 0.911 is therefore not a fully independent generalisation test for the
  official model.** E0/E1/E3 never saw these shapes in any training, so their numbers are
  out-of-sample for EarthGlyph; the comparison is fair as "which distance function matches these
  human ratings", not as "which model generalises better to unseen shape universes".
- No triad/preference data was available in any reachable source, so preference agreement is
  **not computed** rather than approximated from the rating scale.

---

## Checkpoint 3 — retrieval, structure probes, transform stability (2026-11-07)

### F17 — A fairness defect was caught and fixed before the retrieval numbers were produced

The first D2 run was **aborted at query 4 of 6** and restarted. Cause: `Gallery.__init__` always
fitted a fresh E1 minmax normalizer on whatever candidate set it was handed, so

* T3 (base gallery + 3 synthetic distractors) refitted E1 on 1401 shapes instead of 1398, and
* each D3 probe family refitted E1 on its own 1404-shape gallery.

That makes "added distractors" and "rescaled every E1 distance" the *same* experimental change —
exactly the self-fitting defect P0-4.5 Checkpoint B measured (E1's distances were not comparable
across libraries). `Gallery` now accepts `e1_normalizer=`; T3 and every D3 gallery **inherit** the
plain MPEG-7 fit, so the candidate set is the only variable. A regression test asserts that all
three arms' distances to the original members are bit-for-bit unchanged when the gallery is
extended this way.

The counterfactual is still measured, not assumed: `d2_metrics.json →
e1_normalizer_drift_if_refitted` records how many of the 91 descriptors a refit would have moved
and the largest relative scale shift. D4 keeps its own subset-fitted normalizer (one gallery for
all transforms and all arms), and says so — its absolute E1 distances are not comparable to D2's,
and only within-D4 ranks are used.

### F18 — The natural probe family is deliberately asymmetric

For `natural` the query is a real MPEG-7 outline (`bat-1`) that **stays in the gallery**, so rank 1
is the exact original — a useful sanity check. For the four synthetic families the query itself
is absent and only its three variants are present. This is stated in `d3_metrics.json` and on the
figure, not left for a reader to discover.

### D2 result — exact-target removal on MPEG-7 (6 queries, 1398-shape gallery)

Mean class-recall@10 over the six queries (`Heart-1, fork-1, bat-1, tree-1, apple-1, butterfly-1`).
Category recall uses MPEG-7 **object categories**, not human similarity; it is a retrieval sanity
metric, never a perception score.

| condition | candidates | E0 | E1 | E3 | E0 top-1 in category |
|---|---|---|---|---|---|
| T0 nothing removed | 1398 | 0.767 | 0.850 | 0.917 | 6/6 |
| T1 exact image removed | 1397 | 0.733 | 0.833 | 0.917 | 5/6 |
| T2 + rigid near-duplicates removed | 1396 | 0.717 | 0.833 | 0.917 | 5/6 |
| T3 + wrong-category hard distractors | 1399 | 0.717 | 0.833 | 0.917 | 5/6 |

E3 > E1 > E0 in every condition. E0's single out-of-category top-1 is `tree-1`, whose Top-3 under
E0 is `flatfish-14, jar-20, jar-2` while E1 and E3 both return trees — visible in
`figures/perception_benchmark_overview.png`. Per-query cost at ~1400 candidates: E0 ≈ 0.1 s,
E1 ≈ 12 s, E3 ≈ 58 s (CPU, 12 cores available, single-query latency).

**F19 — T3 did not discriminate the arms (honest negative result).** Not one of the three
synthetic wrong-category distractors entered *any* arm's Top-10 for *any* query
(`queries_with_synthetic_distractor_in_top10 = 0` everywhere). The T2→T3 rows are therefore
identical and carry no information about hard-distractor robustness. The distractors (circle /
pentagon / ellipse against star, heart, …) are too grossly different to be "hard" for these
descriptors. A future T3 needs distractors matched on global statistics but differing in part
structure; this run cannot claim that stress was passed.

The normalizer-inheritance fix (F17) is confirmed to have mattered: refitting E1 on the T3 gallery
would have moved **27 of 91** descriptor scales, largest relative shift **22.1%**
(`d2_metrics.json → e1_normalizer_drift_if_refitted`). Without inheritance, T3 would have confounded
"added distractors" with "rescaled distances".

### D3 result — structure probes (1404-shape gallery per family)

Every arm ranks a structural variant above every gross distractor in all five families
(`structure_beats_gross = true` throughout). The arms differ in *margin*, not in pass/fail:

| family | best gross-distractor rank E0 / E1 / E3 | variants in Top-10 E0 / E1 / E3 |
|---|---|---|
| star | 23 / 1163 / 207 | 3 / 2 / 3 |
| heart | 36 / 556 / 33 | 3 / 3 / 3 |
| crescent | 893 / 1165 / 799 | 3 / 3 / 3 |
| forked | 46 / 664 / 478 | 3 / 3 / 3 |
| natural | 440 / 1387 / 1283 | 3 / 3 / 3 |

Read as a trade-off, not a ranking: E1 pushes gross distractors furthest down (best precision) but
drops one star variant to rank 65, outside its Top-10; E0 keeps all variants in the Top-10 yet lets
a distractor in as high as rank 23; E3 sits between. On `natural`, E3 demotes the rotated variant to
rank 7 while E0/E1 hold it at rank 2. These are **constructed roles, not validated human truth** —
the ordering "variant above distractor" is a geometric hypothesis stated on every figure.

### D4 result — transform stability (350-shape gallery, 3 targets × 9 transforms)

Jaccard overlap of each arm's Top-10 with its own untransformed Top-10, averaged over targets:

| transform | E0 | E1 | E3 |
|---|---|---|---|
| rotate 37° / 90° / 191° | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 0.54 / 1.0 / 0.54 |
| scale up / down, translate | 1.0 | 1.0 | 1.0 |
| contour start shift | 1.0 | 1.0 | 0.82 |
| moderate local deformation | 0.67 | 0.82 | 0.82 |
| **mirror** | **0.00** | 0.33 | 0.33 |

Two failures are structural, not noise. **E0 is not mirror-invariant**: mirroring `Heart-1` drops
its self-match to rank 246 and `bat-1` to rank 163, because E0's closed-form alignment searches
rotation only. **E3 is not exactly rotation-invariant**: its 24-step (15°) rotation search leaves
37° and 191° off-grid, halving Top-10 overlap. E1 tolerates both better but still loses two thirds
of its Top-10 under mirroring. None of this says anything about official ShapeComp, whose rotation
behaviour is per-descriptor and was never executed here.

### F20 — Checkpoint 1's Figure 8 claim was wrong; corrected with direct evidence

`official_shapecomp_status.json` originally recorded that `Figure8NetComparison.mat` holds the
coordinate matrices "but the corresponding stimuli are not in that member". Direct
`scipy.io.loadmat` inspection disproves that: the member contains a `shapes` array of **1000
structs, each with `c` (384×2 contour) and `lat` (25-D GAN latent)**, alongside `SC (1000,40)` and
`matnet/kernet1/kernet2 (1000,22)`. The status file now carries a `correction_2026_11_07` block and
S0-MatNet / S0-KerNet1 move from `unavailable` to `outputs_only`.

Recomputed from the raw arrays, exactly as the authors' `Figure8.m` does (`pdist(SC)` over all 40
columns): Pearson r = **0.9276 (MatNet), 0.9452 (KerNet1), 0.9100 (KerNet2)**; the paper quotes
0.93 / 0.94 / 0.91. Using only `SC[:, :22]` instead gives 0.9372 / 0.9527 / 0.9223 — a different
basis that must not be conflated with the published figure. `SC` here is a **40-D** MDS solution
whose first 22 columns retain 88.76% of its variance (the paper's animal space retains 95.05% in
22 dims), so those columns are *not* verified to be the canonical ShapeComp coordinates.

Three limits keep this from becoming a headline result: the 1000 shapes are **disjoint from the
500 human-rated H1 stimuli** (0 exact contour matches, minimum SC-space NN distance 0.54) and carry
**no human ratings**, so any use is model-vs-model; the deposited `c` arrays are raw GAN contours,
not the paper's min-max-normalised minimum-x-start network input, so a retrieval comparison would
confound descriptors with preprocessing; and 2 of the 1000 contours (indices 245, 711) are
self-intersecting and refused by `standardize()`. **No Figure 8 retrieval arm was run.** It is
recorded as a next-step option in Checkpoint 4, not as a result.
Reproduce: `python scripts/audit_figure8_networks.py` → `results/figure8_network_audit.json`.

### Checkpoint 3 artefacts and verify

`results/`: `retrieval_results.csv` (Top-K rows for D2), `d2_full_rankings.npz` (the **full**
gallery order per query/condition/arm, −1-padded, not just Top-10), `excluded_candidates.csv`
(the T1/T2 removal ledger), `d2_metrics.json`, `structure_probe_results.csv` + `d3_metrics.json`,
`transform_results.csv` + `d4_metrics.json`, `metrics.json` (headline + provenance),
`figure8_network_audit.json`, and the D1 files (`pairwise_predictions.csv`, `failure_cases.csv`,
`human_metrics.json`).
`figures/`: `perception_benchmark_overview.png`, `comparison_{star,heart,crescent,forked,natural}.png`
each in aligned and `_original_pose` variants (identical ranks, display pose only),
`human_similarity_scatter.png`, `human_pairwise_agreement.png`,
`human_similarity_failure_cases.png`. `queries/query_contours.csv`.
All PNGs are < 0.5 MB. Figures re-rank nothing: they read the stored CSV/JSON.

```bash
.venv/Scripts/python.exe -m pytest -q        # 777 passed
.venv/Scripts/python.exe -m ruff check .     # All checks passed
.venv/Scripts/python.exe -m mypy             # 38 source files, no issues
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all
```

## Checkpoint 4 — the ten acceptance questions, answered from the artefacts (2026-11-07)

Every answer below cites a file in this directory. Nothing is asserted that was not run or read.

**1. Did official ShapeComp actually run on this machine?**
**No.** The MATLAB toolbox, Demo 1/2, the 109-descriptor engine and every network weight live only
on `sites.google.com` / `dropbox.com` / `drive.google.com`, all network-blocked (exact curl evidence
in `official_shapecomp_status.json → A1_resource_probes`). No MATLAB, no Octave, no TensorFlow is
installed; nothing commercial was installed and no licence was bought. `official_demo_run = false`,
`python_reimplementation_written = false`. What ran locally is EarthGlyph's E0/E1/E3 only.

**2. Which model variant is "S0" here?**
`S0-MDS-precomputed`: Euclidean distance between the authors' **published** 22-D coordinates
(H1's 500 stimuli, H2's 15). Verified `dist == ‖SC1−SC2‖` to 4.44e-16. It is not a local execution
and cannot score a new shape. Recorded separately: `S0-descriptor` unavailable; `S0-MatNet` and
`S0-KerNet1` **outputs_only** on the Figure 8 1000-shape set (corrected this checkpoint, F20);
`S0-KerNet2` outputs_only on H2 and Figure 8. No neural variant was executed anywhere.

**3. Which public data carries verifiable human similarity?**
**H1 only**: 250 pairs × a 250×14 per-observer rating matrix, 0 NaN, 500/500 unique contours
(Zenodo 4730985, CC-BY-4.0). H2 is **auxiliary**: its "human" target is an MDS arrangement digitised
from Li et al. Fig 7D with `getpts`, not raw per-trial ratings, and is never pooled with H1.

**4. Which sources are only category / structure / model scores?**
MPEG-7 (object **categories**), the D3 probes (**constructed** variant/distractor roles),
Figure 3 `GANlabels.mat` (free-text **naming**, rejected), 2D Shape Structure (**not acquired**;
structure annotations would not be similarity anyway), the Figure 8 networks (**model-vs-model**,
no ratings), and H2 (digitised arrangement). None is treated as human pairwise truth.

**5. Which algorithm correlates higher with independent human ratings?**
On H1, agreement ρ (positive = better): **S0 0.911 [0.856,0.918] > E3 0.839 [0.762,0.868] >
E1 0.782 [0.703,0.819] > E0 0.683 [0.589,0.736]**. Marginal CIs overlap, so the ordering rests on
the **paired** two-level bootstrap: all six differences exclude zero (weakest E3−E1 = +0.057
[+0.006,+0.107]). Human consistency on the same pairs: observer-vs-observer median 0.601,
leave-one-out median 0.779 — S0 exceeds a single observer's consistency, E3 approaches it.
Among *runnable* arms, **E3** is the best human predictor. (`human_metrics.json`, scatter figure.)

**6. Which returns more sensible Top-10 on real closed contours?**
**E3**, on the category-recall sanity metric: T2/T3 mean recall@10 = 0.917 vs E1 0.833, E0 0.717.
The overview figure shows E0 answering a `tree-1` query with `flatfish-14, jar-20, jar-2` while
E1/E3 return trees. Caveat: category recall is not human similarity; the human-validated ordering
is question 5's, and the two agree on E3 > E1 > E0.

**7. Are corners / notches / branches matched more reliably?**
Partially, and only as a geometric hypothesis. D3: every arm ranks a structural variant above every
gross distractor in all five families, but margins differ (E3 best on heart/forked, distractor rank
33/478; E1 suppresses distractors hardest yet drops one star variant to rank 65). D4: moderate local
deformation cuts Top-10 overlap to 0.67 (E0) / 0.82 (E1,E3). There is **no independent human
preference record for these probes**, so "more reliably" is not validated truth. Notably the shapes
where E3 disagrees most with humans are exactly elongated/branchy GAN outlines (pairs 217, 96, 189,
247, 18 in `failure_cases.csv`) — branchiness is a shared weakness, not a strength.

**8. E0 / E1 / E3 strengths and failures.**
E0: 0.1 s/query, exact rigid alignment, but **not mirror-invariant** (Top-10 overlap 0.00; self-match
rank 246/163) and weakest human correlation. E1: most transform-tolerant, strongest gross-distractor
suppression, mid human correlation, 12 s/query; drops a star variant from Top-10. E3: best runnable
human correlation and best category recall, but 58 s/query, its 15° rotation grid halves overlap at
37°/191°, and it systematically calls elongated/branchy pairs closer than humans do.

**9. Is ShapeComp's compute / dependency footprint suitable for EarthGlyph?**
**Not as-is.** Exact projection of a new shape needs the 109-descriptor engine + the 25,712-shape
reference corpus + MDS re-fit + Procrustes; the fast route needs MATLAB+Neural-Network-Toolbox
(MatNet) or Keras/TensorFlow (KerNet1/2) plus weights. None is obtainable here, TF is not installed,
and no GPU was used. The published-coordinate route costs nothing at query time but **cannot score
new shapes**, which is fatal for live retrieval. So official ShapeComp cannot be dropped in.

**10. Is a Python port / model approximation / hybrid worth pursuing?**
**Yes as an investigation, not as an integration yet.** The published ShapeComp space beats every
runnable arm on human ratings with paired CIs excluding zero, and E3 already sits near the human
noise ceiling; the E3→S0 gap (+0.072) is the headroom an approximated ShapeComp-like embedding could
target. Two concrete, bounded next steps: (a) run the Figure 8 retrieval arm on the 998 loadable
shapes against all four published coordinate routes to measure end-to-end ranking disagreement
(model-vs-model, preprocessing confound disclosed); (b) test whether a small regressor from E1/E3
features onto the published 22-D coordinates closes the D1 gap on held-out H1 pairs. **Do not change
production retrieval defaults and do not cross the P0 gate on this evidence.** A full 109-descriptor
port remains forbidden by the brief and was not attempted.

### Blockers and open items carried out of P0-4.6

- ~~Official code/weights remain unobtainable from this network~~ **Resolved 2026-11-08 (Checkpoint 5):**
  the user manually downloaded the toolbox and the official KerNet1/KerNet2 weights were executed
  locally via the numpy port and validated against published network outputs. The original
  109-descriptor engine is still absent from the 2024 toolbox, so exact original-ShapeComp projection
  of a new shape remains unavailable; only the two neural approximations run.
- T3's hard distractors were too gross to discriminate (F19); a part-structure-matched distractor
  set is needed before claiming hard-distractor robustness.
- H2's digitised target and the Figure 8 model-vs-model set must never be merged into H1's human
  truth; both are labelled auxiliary wherever they appear.
- E0 mirror-invariance and E3 rotation-grid resolution are known, measured defects, not surprises.

## Checkpoint 5 — official KerNet1/KerNet2 translated to Python and actually run (2026-11-08)

**Trigger.** The user downloaded the official toolbox to `E:\EarthGlyph\shapeComp_17_12_2024` and
asked whether the MATLAB code could be translated to Python to run and compare. Via AskUserQuestion
the user chose **(a) the h5py + numpy route** (read the official `.h5` weights, numpy forward pass,
**no TensorFlow**, no retraining) and **(b) the full bounded scope** "两网络 + 验证 + 检索对比":
translate preprocessing → load KerNet1/KerNet2 → validate faithfulness against published 22-D
coordinates → add them as actually-run `S0K1`/`S0K2` arms to the D1–D4 comparison vs E0/E1/E3.
**Production retrieval defaults were not changed.**

### What was translated, and what was deliberately not

`earthglyph/shape_benchmark/shapecomp_port.py` translates **only the official MATLAB preprocessing**
and runs the authors' **own weights verbatim**:

- Preprocessing ported from `getShapeComp22netkeras.m` / `getShapeComp22netkerasimage.m`:
  centroid = mean of points; `resamplePolygon` (close → arc-length resample to n+1 → drop last);
  `adjustContourPos` = (c − min)/(max − min); **clockwise enforcement kept** (lines 9–13 of the
  contour script); `inpolygon` → `shapely.covers` for the 40×40 raster; `flipud` for KerNet2.
  KerNet1 input is `np.ravel(points, order="F")` (768-D, MATLAB column-major `points(:)`);
  KerNet2 input is a 40×40×1 float32 binary image.
- Weights read with **h5py** from `net/keras/shape2sc22a.h5` (KerNet1: dense_1..7) and
  `net/keras/im2sc22d.h5` (KerNet2: conv2d_4/5/6 + dense_3/4). Keras 2.1.2 layout
  `model_weights/<layer>/<layer>/kernel:0|bias:0`; Dense kernel (in,out); Conv2D (3,3,in,out)
  channels_last. Forward pass: Dense `x@W+b` then ReLU; Conv2D 'same' = zero-pad-1
  cross-correlation (`sliding_window_view` + `einsum("hwcij,ijco->hwo")`); MaxPool 2×2 reshape-max;
  CNN flatten order='C'. **No TensorFlow/Keras runtime, no GPU, no retraining.**
- **NOT translated:** the 109-descriptor engine (absent from the 2024 toolbox), MatNet (MATLAB
  Neural-Network-Toolbox, no weights obtainable), and any self-written descriptor approximation.
  E1 remains labelled "EarthGlyph ShapeComp-inspired"; the port is never called a descriptor
  reproduction. The brief's ban on passing a self-written 91/109-D version off as ShapeComp is
  honoured — this port runs the official **weights**, which the user explicitly authorised.

### Faithfulness validation (`results/shapecomp_port_validation.json`)

Reproduce: `.venv/Scripts/python.exe scripts/validate_shapecomp_port.py`. Metrics are
basis-invariant pairwise-distance Spearman/Pearson plus per-dim Pearson r and raw/Procrustes RMSE.

- **KerNet2 vs published H2 KerNet2 coords — essentially exact:** per-dim r 0.9982 (min 0.9949),
  pairwise Spearman **0.9989**, Pearson 0.9992, raw RMSE **0.0065**, Procrustes RMSE 0.0044.
  H2's `lietal_ShapeCompSolution_keras_mds_image.mat` pairs each input contour `stim[i].c` with its
  own published output `sc[i]` — a clean input→output pair for the ported network.
- **KerNet1 / KerNet2 vs Figure 8 published network columns:** pairwise Spearman **0.980 / 0.957**,
  per-dim r mean 0.951 / 0.905. The residual is **input provenance, not a port bug**: deposited
  `shapes[i].c` is a raw non-uniform 384-point display contour, not the exact normalised network
  input. Skipping the resample collapses agreement to ~0.56, confirming the pipeline is required
  and correct. Orientation check: 999/1000 deposited contours are already clockwise, so the kept
  clockwise step is a near-no-op here and cannot explain the residual.
- **H1 is explicitly NOT a faithfulness test** (`is_faithfulness_test: false`): its
  `shape1SC/shape2SC` are the **original 91-descriptor** ShapeComp MDS coords
  (`view_rating_expt_stim_and_resp.m` line 21), a different model/basis — per-dim r is meaningless
  (mean ≈ −0.06). Pairwise Spearman ≈ 0.927 / 0.929 measures how well the neural approximation
  tracks the original model, which is the D1 question, not port correctness.

### D1 — the run networks match the published space and beat every EarthGlyph arm

`human_metrics.json`, six arms, agreement ρ (positive = better), two-level paired bootstrap:

| Arm | H1 agreement ρ | 95% CI | H2 agreement |
|---|---|---|---|
| S0 (published original 22-D) | 0.9108 | [0.856, 0.918] | 0.7656 |
| **S0K1 (KerNet1 actually run)** | **0.9032** | [0.846, 0.911] | 0.6298 |
| **S0K2 (KerNet2 actually run)** | **0.9005** | [0.846, 0.909] | 0.7622 |
| E3 (Shape Context) | 0.8391 | [0.762, 0.868] | 0.6936 |
| E1 (EarthGlyph 91-D) | 0.7824 | [0.703, 0.819] | 0.6584 |
| E0 (geometric alignment) | 0.6831 | [0.589, 0.736] | 0.7203 |

- **Network-vs-network contrasts include zero** (statistically indistinguishable from the published
  original): S0K1−S0 = −0.0076 [−0.0306, +0.0127], S0K2−S0 = −0.0103 [−0.0305, +0.0086],
  S0K1−S0K2 = +0.0027 [−0.0150, +0.0188].
- **Every EarthGlyph-vs-network contrast excludes zero:** e.g. E3−S0K1 = −0.0641 [−0.1247, −0.0134],
  E3−S0K2 = −0.0614 [−0.1201, −0.0137], E1−S0K1 = −0.1208 [−0.1802, −0.0630],
  E0−S0K1 = −0.2201 [−0.2931, −0.1478]. So the ported official networks significantly out-predict
  every EarthGlyph arm on independent human ratings.
- **H2 nuance (faithful, not a bug):** S0K2 (image CNN) matches the published H2 KerNet2-based
  arrangement (0.7622 vs S0 0.7656), while S0K1 (contour MLP) scores lower (0.6298) — H2's target
  was built from the **image** route, so the contour network is a different approximation there.
  H2 stays auxiliary and is never pooled with H1.

### D2–D4 — the run networks on real closed contours (MPEG-7, no human truth claimed)

Reproduce: `run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2` → `--stage summary`.
S0 is absent from every retrieval stage (no published MPEG-7 coordinates). **E0/E1/E3 rows are
bit-identical to Checkpoint 4** (D2 T2/T3 recall 0.717/0.833/0.917; D4 E0 mirror 0.00, E3 rotate_37
0.54, E1 mirror 0.33) — the determinism check the brief required.

- **D2 category recall@10 (T2/T3 mean over 6 queries):** E0 0.717, E1 0.833, **E3 0.917**,
  S0K1 0.767, S0K2 0.800. On *category* recall the networks beat E0 but **not** E1/E3. This is
  expected and honest: the networks optimise **human perceptual similarity** (D1), not MPEG-7 object
  categories, and the two are not equivalent. Per query the split is stark — `tree-1`: S0K2 **0.9**
  vs E0 0.0 / E1 0.1 / E3 0.6; `bat-1`: S0K1 0.3 / S0K2 **0.2** vs E1/E3 0.9. Bat is elongated/branchy,
  precisely the shapes where D1 shows the networks diverging from humans (failure_cases 217/96/189/
  247/18). **T3 still adds no discrimination**: 0/6 queries put a synthetic distractor in any arm's
  Top-10 (F19 unchanged). Full rankings in `d2_full_rankings.npz`.
- **D3 structure probes:** all five families pass for every arm (structural variant above gross
  distractor; variant rank 1, natural 2). Distractor suppression: E1 hardest (best distractor rank
  556–1387), networks intermediate (S0K1 41–1103, S0K2 42–1077), E3 33–1283, E0 admits a distractor
  as early as rank 23. Roles remain a **constructed hypothesis, not human truth**.
- **D4 transform stability — the measured integration blocker.** The official networks are **not
  rotation-invariant**: rotating the query 37°/90°/191° collapses Top-10 overlap to **0.00** (bat-1
  aggregate) and pushes the self-match to rank **301** (fork-1, 90°, S0K1) / 247 (S0K2). Mirroring is
  shape-dependent (bat-1 overlap 0.05, self-rank 112/101; heart-1 overlap 0.82). Scale/translation/
  start-shift leave S0K1 intact (overlap 1.00) and S0K2 mostly intact (0.67). By contrast E0/E1 are
  rotation-invariant (overlap 1.00 across all rotations); E0 is **not mirror-invariant** (overlap 0.00,
  self-rank 246/163); E3's 15° grid halves overlap at 37°/191° (0.54). **Conclusion:** the
  human-validated networks cannot be dropped into EarthGlyph's rotation-invariant retrieval as-is —
  they need a rotation search or invariant wrapper, which is unvalidated here.
- **Cost asymmetry (engineering):** the networks score the gallery **once** (absolute 22-D coords, no
  corpus fitting; ~20 s one-time for 1398 shapes), then each query is one forward pass + L2 →
  **≈ 0 s marginal** (D2 T1/T2 logged 0.0 s). E3 recomputes per query at ~55 s. So the networks are far
  cheaper at query time *after* gallery scoring, but rotation-sensitive; E1 is the transform-tolerant
  middle ground at ~12 s/query.

### Licence and redistribution boundary

The toolbox at `shapeComp_17_12_2024/` is **gitignored** (`.gitignore:72`). Upstream carries only a
"please cite" notice and **no redistribution licence**, so neither the toolbox nor the `.h5` weights
are committed. The port code, validation script, tests and result JSONs are committed; the weights
are read at runtime from the user's local copy. Cite both Morgenstern et al. 2021 (PLoS Comput Biol,
DOI 10.1371/journal.pcbi.1008981) and Carbajal Chavez et al. (PsyArXiv). Reproduction requires the
user to supply the toolbox locally and `pip install -e ".[shapecomp]"` (h5py + matplotlib).

### Checkpoint 5 artefacts and verify

New code: `earthglyph/shape_benchmark/shapecomp_port.py` (the port), `scripts/validate_shapecomp_port.py`
(faithfulness), `tests/test_shapecomp_port.py` (9 tests). Modified: `retrieval.py` (S0K1/S0K2 arms,
lazy h5py), `run_shape_benchmark.py` + `plot_shape_benchmark.py` (six-arm wiring), `pyproject.toml`
(`shapecomp` extra), `.gitignore` (toolbox), `official_shapecomp_status.json` (port status). New
results: `shapecomp_port_validation.json`, `run_d1.log`, `run_d234.log`; regenerated D1–D4 JSON/CSV,
`d2_full_rankings.npz`, `metrics.json` and all figures (six-arm scatter, five comparison families).

```bash
.venv/Scripts/python.exe -m pytest -q        # 786 passed, 0 skipped (incl. 9 port tests)
.venv/Scripts/python.exe -m ruff check .     # All checks passed
.venv/Scripts/python.exe -m mypy             # 39 source files, no issues
.venv/Scripts/python.exe scripts/validate_shapecomp_port.py
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all
```

Determinism: re-running with S0K1/S0K2 leaves E0/E1/E3 **bit-identical** to Checkpoint 4
(D2 T2/T3 recall 0.717/0.833/0.917; D4 E0 mirror overlap 0.00, E3 rotate_37 0.54, E1 mirror 0.33).
The toolbox and `.h5` weights are **not** in Git (gitignored, no redistribution licence); only the
port code, scripts, tests and result artefacts are committed.

Git checkpoint: CP5 work commit `31e6b00` (`feat: run official ShapeComp KerNet1/KerNet2 via numpy
port`), pushed to `origin main` (ssh.github.com:443); verified `git ls-remote origin refs/heads/main`
== `git rev-parse HEAD` == `31e6b00efc754938217bb0ea68a7ee3e831c3d9a`, author `ANTACIDisnotFORMICACID`.

---

## Checkpoint 6 — rotation-search added to the ShapeComp port, compared under identical rotation (2026-11-08)

**Project goal this checkpoint:** EarthGlyph must retrieve under rotation. Checkpoint 5 established the
official KerNet weights win on human similarity but are **not rotation-invariant** (D4 overlap 0.00, target
rank up to 301). The user directed: give the ShapeComp port a rotation-analysis capability and compare it
against the arms that already handle rotation, **under the same rotation condition**. Nothing here changes
the production retrieval default; it is a bounded method-optimisation study.

### What was added (and what was deliberately not)

* `retrieval.py`: a query-side **rotation-grid search** wraps the S0K arms —
  `d(q, g) = min_θ ‖net(R_θ q) − net(g)‖` over the **same 24-step / 15° grid** E3's `orientation="sample"`
  uses, exposed as `search_shape(..., rotation_search=True)` and `pair_distance(..., rotation_search=True)`.
  The gallery is scored once in its stored pose; only the query contour is turned before the network call.
  New helper `kernet_rotation_frames(network, contour)` returns the `(24, 22)` rotated-frame matrix (row 0 is
  the plain score), which `run_d5` caches per shape so a whole sweep reuses it.
* **The official input contract is untouched**: `kernet1_input` / `kernet2_input` are not modified; the
  rotation is applied to the raw contour *before* the unmodified preprocessing. The weights are the authors'
  own, used verbatim. This rotation robustness is an **added wrapper**, not a property of the official network,
  and is labelled as such everywhere (`details["rotation_search"]`).
* Not added: no retraining, no orientation canonicalisation of the network itself, no change to E0/E1/E3.

### Same-condition protocol (`--stage d5`, rotation is the only variable)

* **(a) rotation-stress human agreement (H1)** — one member of every rated pair is turned by each stress
  angle; each arm must recover the *published human dissimilarity* it was scored on. Shared ground truth,
  two-level bootstrap (pairs AND observers, 2000 reps, seed 20261107). Arms: E0, E1, E3, S0K1, S0K1+rot,
  S0K2, S0K2+rot. Stress angles mix on-grid multiples of 15 (15/45/90/135/270) with off-grid 37/191, plus 0
  as the control.
* **(b) rotation-stress retrieval (MPEG-7 stratified, D4 gallery)** — the query is turned by each angle and
  ranked; stability = Top-10 Jaccard overlap with its own untransformed ranking + the exact target's rank.

### Results (`results/d5_rotation_metrics.json`, figure `figures/rotation_stress.png`)

Agreement under rotation stress (pooled over the rotated angles, two-level bootstrap CI):

| arm | pooled ρ | 95% CI | ρ at φ=0 | worst rotated ρ |
|-----|---------:|:------:|---------:|----------------:|
| E0 (invariant) | +0.683 | [0.635, 0.704] | 0.683 | +0.683 (flat) |
| E1 (canonical) | +0.782 | [0.731, 0.798] | 0.782 | +0.782 (flat) |
| **E3 (sample grid)** | **+0.839** | [0.785, 0.848] | 0.839 | +0.837 (flat) |
| S0K1 naive | +0.434 | [0.378, 0.470] | 0.903 | **−0.002** (270°) |
| **S0K1 +rot** | **+0.747** | [0.695, 0.763] | 0.763 | +0.706 |
| S0K2 naive | +0.458 | [0.399, 0.495] | 0.901 | +0.073 (90°) |
| **S0K2 +rot** | **+0.774** | [0.723, 0.789] | 0.775 | +0.746 |

Retrieval Top-10 overlap vs untransformed, over the rotated angles (mean / worst). Target-rank counts use the
same rotated-only denominator: **21 = 3 targets × 7 non-zero angles**; the 0° control is excluded from every
headline cell (it is stored separately as `unrotated_control_overlap` = 1.000 for all seven arms).

| arm | mean | worst | target rank (rotated) |
|-----|-----:|------:|-------------|
| E0 / E1 | 1.000 | 1.000 | 1 in 21/21 |
| E3 | 0.916 | 0.674 | 1 in 21/21 |
| S0K1 naive | 0.162 | 0.037 | 1 in **1/21**, up to **297** (fork 90°) |
| **S0K1 +rot** | **0.943** | 0.725 | 1 in **20/21** (23/24 with control), 6 at off-grid fork 37° |
| S0K2 naive | 0.148 | 0.018 | 1 in 4/21, up to 244 |
| **S0K2 +rot** | **0.910** | 0.632 | 1 in **19/21** (22/24 with control), 2 at off-grid bat 37°/191° |

The agreement pool above is likewise control-free: `n_rows = 1750 = 250 pairs × 7 rotated angles`
(`scripts/run_shape_benchmark.py` drops `phi == 0.0` in both studies), so the headline numbers answer exactly
the operating question — *the query's rotation is unknown, so the search always runs*. The φ=0 column is kept
only as the sanity check that the wrapper does not break the already-correct case, not as an operating point.

**Read-out.** The added rotation search turns the official networks from *rotation-blind* into
*rotation-searchable*: retrieval stability jumps from ~0.15 to ~0.91–0.94 (E3-comparable), and the exact
target returns to rank 1 in almost every rotated query (the few misses are off-grid 37°/191°, precisely
the half-grid-step limitation E3 documents). Human agreement under rotation rises from ~0.44 to ~0.75–0.77,
recovering most of the port's Checkpoint-5 advantage — but it does **not** exceed E3 (0.839) or even E1
(0.782). The wrapper also **costs peak accuracy**: at φ=0 the search drops S0K1 from 0.903 to 0.763, because
minimising over rotations lets distinct shapes collide. This trade is reported, not hidden.

**Why the Checkpoint-5 lead does not survive rotation.** S0's +0.072 over E3 was earned with the pose already
aligned. Taking `min` over 24 poses replaces each pair by its most optimistic distance, which flattens exactly
the fine ordering the network is sensitive to — the flatness we wanted (robustness) is bought with a lower
level. Human-validated similarity and rotation invariance are therefore *independent* properties: the search
repairs the second and, in doing so, spends the first. Under the unknown-rotation operating point the ranking
is E3 > E1 > S0K2+rot > S0K1+rot > E0 for agreement, and E0=E1 > S0K1+rot > E3 ≈ S0K2+rot for retrieval
stability, with E0/E1/E3 all recovering the exact target at rank 1 in every rotated query.

**Consistency check:** the cached frames scorer reproduces the committed D1 numbers exactly at φ=0
(S0K1 0.903, S0K2 0.901 vs D1 0.9032 / 0.9005), confirming `run_d5`'s shortcut equals `pair_distance`.

**Criterion alignment (decided with the user, 2026-11-08).** This project's requirement is *best retrieval when
the query is rotated at random*, so study **(b)** is the primary criterion and **(a)** is supporting evidence
that the representation tracks human similarity — they answer different questions and neither may be swapped in
for the other. Judged on (b) the winner is **E0/E1 (21/21 rank 1, overlap 1.000)**, then **E3 (21/21)**, then
**S0K1+rot (20/21)** and **S0K2+rot (19/21)**, with the naive arms far behind (1/21 and 4/21). The port's
Checkpoint-5 lead lived entirely in (a), which never promised (b): what this checkpoint bought is
"rotation-searchable", not a new retrieval champion. The follow-up that could challenge E0/E1 on (b) *and* keep
(a) is orientation **canonicalisation of query and gallery alike** rather than a query-side `min`
(acceptance: (b) ≥ 21/21 rank 1 **and** (a) ≥ E3's 0.839 — any regression vetoes the wrapper, and the comparison
stays on the same 30-member D4 gallery so the denominators remain comparable). Both that wrapper and the
cheaper "finer grid" alternative are **mechanism inferences, not measurements** — recorded as assumptions
D55/D56 in `docs/DECISIONS.md`; the criterion itself is D54.

### Determinism

E0/E1/E3 and the **naive** S0K paths are arithmetically unchanged by this checkpoint: `rotation_search`
defaults to `False`, the new code sits behind that flag, and the S0K `pair_distance` naive branch was only
rewritten to the numerically identical symmetric form `‖net(right)−net(left)‖`. The committed D1–D4 result
files were therefore **not re-run**; their rows remain the Checkpoint-5 values. Only `d5_*` artefacts and
`metrics.json` (which now folds in a `rotation_stress` headline) are new.

### Checkpoint 6 artefacts and verify

New code: `kernet_rotation_frames` + `rotation_search` in `retrieval.py`; `run_d5` + `_rotated`/`_d5_arms`
in `run_shape_benchmark.py`; `plot_rotation_stress` in `plot_shape_benchmark.py`; 6 new tests in
`test_shapecomp_port.py` (frames row-0 identity, rotated-copy distance recovery, cached==public, rank-1
recovery). New results: `d5_rotation_metrics.json`, `d5_rotation_results.csv`, `run_d5.log`,
`rotation_stress.png`; regenerated `metrics.json`.

```bash
.venv/Scripts/python.exe -m pytest -q        # 792 passed, 0 skipped (incl. 15 port tests)
.venv/Scripts/python.exe -m ruff check .     # All checks passed
.venv/Scripts/python.exe -m mypy             # 39 source files, no issues
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d5 --methods E0 E1 E3 S0K1 S0K2
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure rotation
```

d5 took ~39 min wall (the S0K frame builds and E3 retrieval sweep dominate; the frames cache keeps the
250-pair sweep tractable). Toolbox and weights remain gitignored and uncommitted; the wrapper inherits the
same licence boundary as Checkpoint 5.

Git checkpoint: CP6 work commit `e43f587` (`feat: add rotation search to ShapeComp port and compare under
identical rotation (P0-4.6 CP6)`), pushed to `origin main` (ssh.github.com:443); verified
`git ls-remote origin refs/heads/main` == `git rev-parse HEAD` ==
`e43f587107faa4bb7bed9666338464c680febd3d`, author `ANTACIDisnotFORMICACID`. CP1–CP5 shas are above.