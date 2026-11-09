# P0-4.6 — Independent 2D shape-perception retrieval benchmark

Geography-free. No Overture Maps, no water bodies, no coordinates, no changes to the
production retrieval defaults. The question this directory answers:

> Can a shape model validated against real human perception find the 2D outlines that
> people judge similar, and does it beat EarthGlyph's existing geometric matchers?

## Headline results

### D1 — independent human ratings (250 pairs × 14 observers, nothing selected)

Agreement ρ restates every correlation as *distance vs human dissimilarity*, so **larger is
better** and the sign is comparable across datasets. CI = two-level bootstrap resampling
**pairs and observers** (2000 replicates, seed 20261107).

| Arm | Agreement ρ | 95% CI | median \|ρ\| vs one observer |
|---|---|---|---|
| **S0** official ShapeComp 22-D, *published coordinates* | **0.911** | [0.856, 0.918] | 0.750 |
| **S0K1** official KerNet1 weights, **actually run** (numpy port) | **0.903** | [0.846, 0.911] | 0.748 |
| **S0K2** official KerNet2 weights, **actually run** (numpy port) | **0.900** | [0.846, 0.909] | 0.750 |
| **E3** Shape Context + rotation search | **0.839** | [0.762, 0.868] | 0.692 |
| **E1** EarthGlyph ShapeComp-inspired, 91-D, **ours** | **0.782** | [0.703, 0.819] | 0.640 |
| **E0** geometric alignment | **0.683** | [0.589, 0.736] | 0.579 |

The marginal CIs overlap, so the ordering is tested with a **paired** bootstrap of the
difference on the same pairs and observers. Every EarthGlyph-vs-network contrast excludes zero
(weakest E3 − S0K2 = −0.061 [−0.120, −0.014]); the three network-vs-network contrasts
**include** zero (S0K1 − S0 = −0.008 [−0.031, +0.013], S0K2 − S0 = −0.010 [−0.031, +0.009],
S0K1 − S0K2 = +0.003 [−0.015, +0.019]). So the **actually-run official networks are
statistically indistinguishable from the published original ShapeComp space**, and both beat
every EarthGlyph arm. Human consistency on the same pairs: observer-vs-observer median ρ = 0.601,
leave-one-out median = 0.779.

**S0 vs S0K1/S0K2.** `S0` is the authors' *published* 22-D coordinates (the MDS projection of the
original **109**-descriptor space) and cannot score a new shape, so it appears in D1 only.
`S0K1`/`S0K2` are the authors' **own Keras weights executed locally** through a numpy port
(h5py, no TensorFlow, no retraining); they *can* score new shapes and therefore appear in every
retrieval stage. On H1 the run networks track the published space closely (pairwise Spearman
≈ 0.93); the residual is the neural approximation of the original model, **not** a port bug —
port faithfulness is measured separately against published *network* outputs (below).

### D2–D4 — retrieval on real closed contours (MPEG-7, no human truth claimed)

Mean class-recall@10 over six queries; categories are object labels, **not** human similarity.
S0 is absent (no published MPEG-7 coordinates); S0K1/S0K2 are the actually-run official networks.

| Condition | E0 | E1 | E3 | S0K1 | S0K2 |
|---|---|---|---|---|---|
| T2 query + rigid near-duplicates removed | 0.717 | 0.833 | **0.917** | 0.767 | 0.800 |
| T3 T2 + wrong-category hard distractors | 0.717 | 0.833 | **0.917** | 0.767 | 0.800 |

On **category** recall E3 > E1 > S0K2 > S0K1 > E0. The run networks beat E0 but **not** E1/E3 here —
expected, because they optimise *human perceptual similarity* (D1), not MPEG-7 object categories.
Per query they excel on `tree-1` (S0K2 0.9 vs E0 0.0 / E1 0.1 / E3 0.6) yet fail on `bat-1`
(S0K1 0.3 / S0K2 0.2 vs E1/E3 0.9): bat is elongated/branchy, exactly the D1 failure mode.
**T3 added no discrimination**: no synthetic distractor entered any arm's Top-10 (notes F19).
E0/E1/E3 rows are **bit-identical to Checkpoint 4** (determinism check passed).

Structure probes (D3): every arm ranks a structural variant above every gross distractor in all
five families; E1 suppresses distractors hardest (rank 556–1387), the networks are intermediate
(S0K1 41–1103, S0K2 42–1077), E0 admits a distractor as early as rank 23. Roles are a constructed
hypothesis, not human truth.

Transform stability (D4) — **the measured integration blocker**: the official networks are **not
rotation-invariant**. Rotating the query 37°/90°/191° collapses their Top-10 overlap to **0.00** and
pushes the self-match to rank 301 (fork-1, 90°, S0K1); mirroring is shape-dependent (bat overlap
0.05, heart 0.82). E0/E1 are rotation-invariant (overlap 1.00); E0 is **not mirror-invariant**
(overlap 0.00, self-rank 246); E3's 15° grid halves overlap at 37°/191°. Scale/translation/start-shift
leave the networks mostly intact (S0K1 overlap 1.00). So the human-validated networks cannot be
dropped into EarthGlyph's rotation-invariant pipeline as-is — they would need a rotation search or
invariant wrapper, unvalidated here.

> **Correction (2026-11-09, Checkpoint 7-C).** That last clause *is* now validated: four wrappers
> (R0 24-pose query search, R1 36/72-pose, R2 bilateral canonicalisation, R3 24×24) were measured
> against E0/E1/E3 on a frozen 12-query × 5-angle × 3-condition design. The headline is that
> **no wrapper reaches E1's same-class Top-1 once the answer is removed from the gallery**
> (best network arm 0.764 vs E1 0.833) and the cheapest invariant arm (R2) is the *worst* of the
> wrappers at tracking human ratings (0.639 / 0.677 vs E3/sample 0.839). See
> [Checkpoint 7](#checkpoint-7--rotation-methods-r0r1r2r3-official-port-audit-and-the-d5-statistics-fix)
> below and `notes.md` §C3–C5; the ShapeComp route is **not** recommended for production retrieval.

Per-query cost at ~1400 candidates: E0 ≈ 0.1 s, E1 ≈ 12 s, E3 ≈ 55 s. The networks score the gallery
**once** (absolute coords, no corpus fitting; ~20 s one-time) then each query is a single forward
pass + L2 → **≈ 0 s marginal**, far cheaper than E3 at query time — but rotation-sensitive.

### Official ShapeComp reproducibility

**The official neural approximations now run locally via a numpy port; the original
109-descriptor engine still does not.**

- **Port (2026-11-08).** The user manually downloaded the official toolbox to
  `shapeComp_17_12_2024/` (gitignored — upstream carries only a "please cite" notice, no
  redistribution licence). `earthglyph/shape_benchmark/shapecomp_port.py` translates **only**
  the official MATLAB preprocessing (centroid / arc-length resample / min-max position /
  clockwise enforcement / rasterise) and runs the authors' **own** Keras 2.1.2 `.h5` weights
  verbatim through a numpy forward pass (h5py, **no TensorFlow, no retraining, no GPU**). This
  is the official neural approximation executed in Python — **not** a self-written descriptor
  reimplementation. KerNet1 = `net/keras/shape2sc22a.h5` (768-D contour MLP);
  KerNet2 = `net/keras/im2sc22d.h5` (40×40 binary-image CNN).
- **Faithfulness validated against published *network* outputs** (`results/shapecomp_port_validation.json`,
  reproduce `scripts/validate_shapecomp_port.py`):
  - **KerNet2 vs published H2 KerNet2 coords: essentially exact** — per-dim Pearson r 0.9982,
    pairwise-distance Spearman 0.9989, raw RMSE 0.0065.
  - **KerNet1 / KerNet2 vs Figure 8 published network columns:** pairwise Spearman 0.980 / 0.957.
    The residual is **input provenance**, not a port bug: the deposited `shapes[i].c` is a raw
    non-uniform 384-point display contour, not the exact normalised network input; skipping the
    resample collapses agreement to ~0.56, confirming the pipeline is required and correct.
  - **H1 is *not* a faithfulness test:** its `shape1SC/shape2SC` are the **original
    109-descriptor model's** 22-D MDS coords (a different model/basis), so per-dim r is meaningless
    there; pairwise Spearman ≈ 0.93 measures how well the neural approximation tracks the
    original model.
- **Historical availability (Checkpoint 1, still true at the time):** the MATLAB toolbox,
  Demo 1/2, the 109-descriptor engine and all weights live only on `sites.google.com`,
  `dropbox.com` and `drive.google.com`, all network-blocked from this machine (exact curl
  evidence in `official_shapecomp_status.json`). No MATLAB and no Octave is installed. The
  2024 toolbox the user downloaded contains **no** 109-descriptor engine, so the exact original
  ShapeComp projection of a new shape remains unavailable; only the two neural approximations run.
- Both Zenodo deposits were enumerated **without downloading them** (HTTP Range reads of
  the ZIP central directory: 8 MB transferred out of 308 MB + 2.23 GB). Neither contains
  the descriptor engine, a demo, any `.py`, or any weight file. The larger revision's
  extra `.m`/`.c`/`.mex` files are vendored psignifit (GPLv3) and Psychtoolbox.
- **But the deposits do contain the authors' own official 22-D ShapeComp coordinates**
  for 500 rated stimuli, and `dist` equals `‖SC1 − SC2‖` to 4.4e-16. So an
  `S0-MDS-precomputed` arm is legitimate on those stimuli — clearly labelled as
  *published outputs*, never as a local run, and never extended to new shapes.
- **Correction (2026-11-07):** `Figure8NetComparison.mat` *does* contain its stimuli — 1000
  structs with 384×2 contours and 25-D GAN latents — plus published MatNet/KerNet1/KerNet2
  coordinates. The Checkpoint 1 claim that the stimuli were absent was wrong and is retracted
  in `official_shapecomp_status.json → correction_2026_11_07`. Recomputing the authors' own
  Figure 8D from raw arrays gives r = 0.928 / 0.945 / 0.910 (paper: 0.93 / 0.94 / 0.91). These
  1000 shapes have **no human ratings** and are disjoint from H1, so they support model-vs-model
  comparison only; no retrieval arm was run on them. See `results/figure8_network_audit.json`.

**Real human data was recovered, with participant detail intact.**

| Set | What it actually is | Size | Role |
|---|---|---|---|
| **H1** | 250 GAN shape pairs, continuous similarity ratings, **250 × 14 per-observer** matrix, 384×2 contours, official 22-D coords | 500 unique stimuli, 0 duplicates | **primary** human truth |
| **H2** | 15 Li et al. circular-space shapes, official KerNet2 coords, human MDS arrangement **digitised from Li et al. Fig 7D** | 105 pairs | **auxiliary**, digitisation caveat |
| **MPEG-7** | CE-Shape-1 Part B, white-on-black GIF silhouettes | 1400 shapes / 70 classes / 20 each | retrieval gallery; **category labels ≠ human similarity** |
| Fig 3 GAN | free-text object *names* ("cow udder", "monkey", "italy") | 100 × 20 | **rejected** — naming task, not similarity |
| 2D Shape Structure | Mendeley `74w9c6h2np` | — | **not acquired**: API host does not resolve, page is a JS shell titled "FAQ" |

**Two paper headline numbers were reproduced from raw data, not quoted:**

- H1: official ShapeComp distance vs mean human rating → Pearson **−0.904**, Spearman
  **−0.911** (paper reports r = 0.91; sign flips because distance is inverse similarity).
- H2: official KerNet2-ShapeComp vs digitised human arrangement → Pearson **0.778**
  (paper reports r = 0.78).

Reproducing both from the raw `.mat` files is the evidence that the loaders and the
distance direction are wired correctly before any EarthGlyph arm is compared.

## Checkpoint 7 — rotation methods (R0/R1/R2/R3), official port audit, and the D5 statistics fix

**2026-11-09, the last checkpoint of P0-4.6.** Four sub-checkpoints, each its own commit;
full prose in `notes.md` §Checkpoint 7 (A1–A5, B1–B6, C1–C7, D1–D6) and the ten acceptance
questions answered from the artefacts at the end of that file. Decisions: **D57–D76**.

### What the four wrappers are, and what they cost

`earthglyph/shape_benchmark/rotation_methods.py`. Every arm scores the *same* candidate set, the
pose choice reads **one contour only** (never the gallery, its class or the target id — pinned by
`test_pose_choice_never_consults_the_gallery`), and multi-pose candidates stay **one entry per
shape** (`test_r3_adds_gallery_poses_without_changing_the_query_grid`):

| Wrapper | Pose policy | Gallery precompute | Query cost (K1 / K2) |
|---|---|---|---|
| `naive` | none — score the contour as stored | none | 0.003 / 0.009 s |
| `R0` | min over 24 query poses (15° grid) = CP6's search, bit-for-bit | none | 0.062 / 0.209 s |
| `R1_36`, `R1_72` | same, 10° / 5° grid | none | 0.092–0.183 / 0.313–0.626 s |
| `R2` | bilateral canonicalisation of the contour, one forward pass | only if canonical/bilateral | 0.003 / 0.009 s |
| `R3` | bilateral min, 24 poses on **both** sides | **22.4 s / 78.4 s** one-time | 0.086 / 0.232 s |

### The two goals, in two tables (they disagree, and that is the finding)

Frozen design **before** running: 12 queries × 5 declared angle slots (on-grid, two mid-grid, two
off-grid random ≥2° from any grid or midpoint) × T0/T1/T2, primary condition **T2** (exact target
*and* rigid near-duplicates removed), 72 cells per arm per condition, seed 20261107, 2000
replicates. `results/d6_rotation_metrics.json → retrieval_by_arm["T2"]`:

| arm | same-class Top-1 | Top-1 stability | Recall@10 | MRR | rank shift mean/max | cells with nothing same-class in Top-10 | s/query |
|---|---|---|---|---|---|---|---|
| **E1** | **0.833** | 1.000 | 0.267 | 0.879 | 0.2 / 25 | 6 | 0.013 |
| E0 | 0.750 | 1.000 | 0.250 | 0.775 | 0.0 / 0 | 12 | 0.029 |
| E3/sample | 0.694 | 0.764 | 0.251 | 0.793 | 3.9 / 67 | 4 | 14.098 |
| E3/canonical | 0.500 | 1.000 | 0.208 | 0.630 | 0.0 / 0 | 6 | 0.585 |
| S0K1/R3 | 0.694 | 0.778 | **0.288** | **0.811** | 5.1 / 133 | **0** | 0.086 |
| S0K2/R2 | 0.764 | **0.972** | 0.225 | 0.819 | 0.7 / 55 | 6 | **0.009** |
| S0K1/R2 | 0.583 | 0.958 | 0.250 | 0.663 | 0.3 / 54 | **0** | **0.003** |
| S0K1/R0 | 0.611 | 0.736 | 0.240 | 0.731 | 5.3 / 119 | 4 | 0.062 |
| S0K1/naive | 0.264 | 0.250 | 0.107 | 0.345 | 46.5 / 343 | **36** | 0.003 |
| S0K2/naive | 0.250 | 0.208 | 0.092 | 0.312 | 45.9 / 324 | **40** | 0.009 |

Agreement (goal A) is a **separate** measurement on the 250 rated H1 pairs × 7 stress angles × 14
observers, pooled as distance-vs-dissimilarity Spearman; it never mixes into the table above
(D54/D71). Full 16-arm curves in `notes.md` §C5; headline: **E3/sample 0.8387** is the best arm
that can score a new contour, S0K1/R3 0.8296 and S0K2/R3 0.8295 follow, E1 0.7824, the R2 arms
are last at 0.6386 / 0.6770, and naive sits at 0.4341 / 0.4582.

Three claims, stated separately because collapsing them is the misleading version:

1. **Nothing in the ShapeComp family reaches E1's same-class Top-1 under T2** (best 0.764 vs 0.833).
2. **Class-level depth belongs to R3**, not to the arm with the best Top-1.
3. **Stability and depth are different properties** — R2 is the most stable and cheapest wrapper
   with *below-average* MRR on KerNet1.

The pre-declared acceptance bar for replacing the query-side `min` with canonicalisation
(**D55**: `(b) ≥ 21/21 rank 1` **and** `(a) ≥ E3's 0.839`) is **not met**: R2's agreement is
significantly *below* R0's, paired −0.1083 [−0.1803, −0.0451] (K1) and −0.0965 [−0.1635, −0.0368]
(K2). So flattening the ordering by taking a `min` was never the main cause of the agreement loss.
Finer grids (**D56**) do not rescue goal A either: +0.0068 / +0.0097 at 3× query cost, which buys
stability, not accuracy.

Denominators are not interchangeable. That bar's `21` is Checkpoint 6's goal-(b) protocol —
**3 targets × 7 non-zero angles** on the 30-member D4 stratified gallery, the 0° control excluded
and stored separately as `unrotated_control_overlap` (`notes.md` §Checkpoint 6) — while the `(a)`
side of the same bar is 250 rated pairs × 7 rotated angles. Checkpoint 7-C's frozen design is a
third denominator (12 queries × 5 angle slots + 12 controls = 72 cells × 16 arms), so its goal-(b)
verdicts are reported as same-class Top-1 / stability / rank shift, **not** as a recount of that
21. The veto in D55 rests on the `(a)` side, where both sides of the comparison share the protocol.

### R2's invariance is real, and it is also nothing on an axis-less shape

`scripts/verify_r2_pose_invariance.py` → `results/r2_pose_invariance_check.json`: the same contour
rotated to 37°/191°/270° moves the **naive** 22-D coordinate by **0.61–2.04** (coordinate norm
≈ 1.22, i.e. further than its own length), while R2 moves it by **≤ 5.1e-14** (KerNet1) or
**exactly 0.0** (KerNet2) on the 11 shapes that have a principal axis. The 12th frozen query,
`device2-1`, is the only `isotropic-fallback`: its R2 displacement is **bit-identical to naive**
(0.1523 / 0.4336). Reported per shape, never averaged — canonicalisation is provably a no-op when
no axis exists (13 of 362 gallery contours).

### Reading the two paired-difference tables (a real trap)

`d5_grouped_metrics.json` and `d6_rotation_metrics.json` store the *same* comparison with opposite
signs, because only the D5b writer flips it onto the agreement scale
(`scripts/run_shape_benchmark.py:1022` negates `mean`, `ci_low`, `ci_high`; the D6 writer stores the
raw bootstrap delta at line 1868):

| Artefact | Key | Field | What the number means |
|---|---|---|---|
| `d5_grouped_metrics.json` | `A`\\|`B` | `delta_agreement` | **agreement(A) − agreement(B)**, positive = A better |
| `d6_rotation_metrics.json` | `A`\\|`B` | `mean` | **agreement(B) − agreement(A)**, positive = B better |

Verified from both files: `E0|E1` is −0.0963 in D5b and +0.0963 in D6, and E1 − E0 = 0.7824 − 0.6831
= +0.0993. So a positive D6 delta means the **second** arm is the better one. No stored number is
wrong; only the direction of reading differs. Both tables in `notes.md` quote agreement-scale
deltas and say which file they came from.

### Port faithfulness and the statistics fix, in one line each

- **CP7-A**: the numpy port reproduces the authors' published KerNet2 coordinates to
  **8/15 H2 shapes < 1e-6** once start index / direction / archive file are aligned (native-phase
  median 1.5e-02); the other 7 stay at 4.8e-3–1.8e-2 and are labelled **unattributed**. The
  verified path's headline RMSE is **bit-identical** before and after the audit
  (`0.006496161383207664`), so the audit introduced no precision regression (D57).
  Illegal inputs now raise `ShapeCompInputError` instead of emitting NaN or a zero vector (D58).
- **CP7-B**: D5's interval now resamples the **250 rated pairs** as clusters, not the 1 750 rows.
  Widths grow ×1.49–2.13, every **point estimate is unchanged**, 19 of 21 orderings survive, and
  two pairs become statistically indistinguishable. The old files are untouched and their SHA256
  is recorded in the new artefact (D61–D63).

## Files

| Path | Contents |
|---|---|
| `INTERPRETATION.md` | 简要中文解读：排除水体/地理后的结果 + 验证方法 + 下一步（brief readout） |
| `sources.json` | per-source audit: DOI, URL, license, sha256, format, counts, label semantics, duplicates, training overlap, blocked-source evidence |
| `official_shapecomp_status.json` | Part A verdict, every network probe, MATLAB/Octave/TF availability, which S0 variants exist and on what scope |
| `dataset_manifest.json` | generated by the fetch script: checksums, member list, transfer stats, MPEG-7 class inventory |
| `notes.md` | running checkpoint log, decisions, retractions, open items |
| `cache/` | **gitignored** raw downloads (`.mat`, `.zip`); rebuild with the fetch script |
| `queries/query_contours.csv` | every D2/D3/D4 query outline on the shared 128-point contract |
| `results/pairwise_predictions.csv` | all 250 H1 + 105 H2 pairs, every arm's distance, human mean/sd/min/max |
| `results/human_metrics.json` | correlations, both bootstraps, paired differences, per-observer detail, noise ceiling |
| `results/failure_cases.csv` | all 250 H1 pairs ranked by model–human percentile disagreement |
| `results/retrieval_results.csv` | D2/D3/D4 Top-K rows: rank, id, category, distance |
| `results/excluded_candidates.csv` | T2 near-duplicate removal ledger, with the E0 distance that triggered it |
| `results/d2_full_rankings.npz` | complete rankings (not just Top-10), −1-padded, keyed `query\|condition\|method` |
| `results/{d2,d3,d4}_metrics.json`, `metrics.json` | per-stage detail and the combined headline table with provenance |
| `results/structure_probe_results.csv`, `transform_results.csv` | D3 role probes and D4 transform-stability rows |
| `results/d5_rotation_{results.csv,metrics.json}` | **CP6, frozen**: 250 pairs × 7 angles × arms, per-row and the old row-independent intervals. Never rewritten (D61) |
| `results/d5_grouped_{results.csv,metrics.json}` | **CP7-B re-analysis** of the file above (no re-scoring): clustered + paired intervals, `delta_agreement` = A − B, plus the SHA256 audit link back to the frozen D5 artefacts |
| `results/d6_retrieval_results.csv` | **CP7-C**: one row per arm × condition × query × angle-slot cell (T0/T1/T2, 16 × 3 × 72), Top-1, stability, overlap, rank shift, first relevant rank, seconds |
| `results/d6_top10_hits.csv` | the actual **T2** Top-10 lists behind it (16 arms × 72 cells × 10 = 11 520 rows), with `pose`, `distance`, `same_class`, `is_query_self`, `first_relevant_rank` — everything the failure figures are drawn from |
| `results/d6_agreement_results.csv` | goal A on the D5 stimuli: 16 arms × 250 rated pairs × 7 stress angles = 28 000 rows, `model_distance` next to `human_mean_similarity` (the published 14-observer mean, never a model estimate of it). The observer panel is re-sampled inside the bootstrap, so it is not a column here |
| `results/d6_rotation_metrics.json` | the frozen design block, per-arm/per-condition tables, grouped + angle-randomised CIs, 120 paired deltas (**mean = B − A**, see the reading trap above), cached-vs-pairwise verification, orientation diagnostics, `image_axis_convention_check`, `goal_separation` |
| `results/f1_e1_orientation_sensitivity.{csv,json}` | **CP7-D F1**: 11 synthetic probes × 12 poses through `principal_frame`, per-block drift, the 90° axis defect, and the costed-but-not-adopted 24-pose E1 scheme |
| `results/r2_pose_invariance_check.json` | **CP7-C/D**: 12 frozen query contours × 0/37/191/270°, naive vs R2 coordinate displacement per shape with the frame status |
| `results/official_toolbox_hashes.json` | SHA256 + byte count + upstream mtime for the 24 toolbox files the port reads, the weights' layer shapes, `tensorflow_used`/`matlab_used` flags, and the `redistribution` statement |
| `results/figure8_network_audit.json` | raw-array recomputation of the official MatNet/KerNet correlations, input-contract and H1-overlap checks; model-vs-model only |
| `results/shapecomp_port_validation.json` | port faithfulness vs published **network** outputs (H2 KerNet2 ≈ exact, Figure 8 KerNet1/2), plus the H1 approximation-to-original check (explicitly not faithfulness) |
| `results/run_d*.log` | stdout of the staged runs, kept so a reproduce claim can be dated |
| `figures/` | overview, five comparison families × two poses, three human-evaluation figures (six-arm scatter), `rotation_stress.png` (CP6) |
| `checkpoint7/` | the five CP7 PNGs: `rotation_methods_overview`, `rotation_rank_stability`, `rotation_human_agreement`, `rotation_failure_cases`, `shapecomp_port_diagnostics` |
| `../earthglyph/shape_benchmark/rotation_methods.py` | the four pose policies R0/R1/R2/R3 + `canonicalise` / `pose_embedding` (48 tests run without the toolbox) |
| `../earthglyph/shape_benchmark/shapecomp_port.py` | the MATLAB → numpy translation of the official preprocessing and both `.h5` forward passes |
| `../earthglyph/retrieval/shape_context_baseline.py` | `search_shape(..., allow_mirror=...)`, whose mirror branch is now refused on arms that lack one (F2) |

## Reproduce

```bash
# 1. acquire sources (8 MB of Range reads + 3.4 MB MPEG-7; no full-archive download)
.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py

# 1b. S0K1/S0K2 ONLY: official toolbox (gitignored, no redistribution licence) at
#     shapeComp_17_12_2024/ + the port extra. E0/E1/E3/S0 need none of this.
.venv/Scripts/python.exe -m pip install -e ".[shapecomp]"   # h5py + matplotlib

# 2. validate the port against published network outputs (seconds; writes the faithfulness JSON)
.venv/Scripts/python.exe scripts/hash_shapecomp_toolbox.py     # -> results/official_toolbox_hashes.json
.venv/Scripts/python.exe scripts/validate_shapecomp_port.py    # -> results/shapecomp_port_validation.json

# 3. human-rating prediction (fast, ~50 s) — S0 is appended automatically for this stage
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2

# 4. retrieval stages (~40 min with the run networks; E3 is ~55 s per full-gallery query)
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2

# 5. rotation stress (CP6) and the CP7-B re-analysis of those stored rows (~30 s, no toolbox needed)
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d5 --methods E0 E1 E3 S0K1 S0K2
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d5b   # reads d5_rotation_results.csv only

# 6. the CP7-C frozen rotation-method comparison (16 arms x T0/T1/T2; needs the toolbox)
#    `--methods` is NOT optional here: it defaults to E0 E1 E3, and running the stage with that
#    default rewrites d6_* as a 4-arm table (`_d6_arms` adds the 12 wrapper arms per network that
#    is named). Pass all five methods to reproduce what is stored.
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d6 --methods E0 E1 E3 S0K1 S0K2
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary

# 7. read-only verifications that back specific claims
.venv/Scripts/python.exe scripts/verify_r2_pose_invariance.py            # -> results/r2_pose_invariance_check.json
.venv/Scripts/python.exe scripts/measure_e1_orientation_sensitivity.py   # -> results/f1_e1_orientation_sensitivity.*

# 8. official-network audit (seconds; model-vs-model, no human ratings)
.venv/Scripts/python.exe scripts/audit_figure8_networks.py

# 9. figures (reads the stored rankings; re-ranks nothing)
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all
.venv/Scripts/python.exe scripts/plot_rotation_checkpoint7.py             # -> checkpoint7/*.png
.venv/Scripts/python.exe scripts/plot_checkpoint7.py                     # -> checkpoint7/shapecomp_port_diagnostics.png

# single query, required CLI
.venv/Scripts/python.exe scripts/search_shape_benchmark.py --query mpeg7_Heart-1 --gallery mpeg7 --method E3 --top-k 10
```

Raw downloads are rebuildable from the URLs + checksums in `dataset_manifest.json`, so
they stay out of Git per `AGENTS.md`.

## Official toolbox: how to get it, and what never enters this repository

**P0-4.6 Checkpoint 7-D, item F4.** `S0K1`/`S0K2` cannot run without the authors' toolbox, and no
script here downloads it — nothing in this repository fetches, unpacks or vendors it.

1. **Obtain it from the authors, not from us.** The entry point is Yaniv Morgenstern's own tools
   page, <https://sites.google.com/view/yanivmorgenstern/home/tools/shapecomp>, which links the
   current toolbox archive (the 2024 build `shapeComp_17_12_2024` was reached through the authors'
   Google Drive link; the earlier `shapeComp_31_05_2021.zip` is on their Dropbox). Both URLs, the
   article DOI (`10.1371/journal.pcbi.1008981`) and the Zenodo data DOI (`10.5281/zenodo.4730985`)
   are recorded in `results/official_toolbox_hashes.json → official_pages`, so the route is part of
   the provenance rather than of this prose.
2. **Place it at the repository root** as `shapeComp_17_12_2024/`. That name is the default the
   port looks for (`earthglyph/shape_benchmark/shapecomp_port.py:DEFAULT_TOOLBOX`);
   `scripts/*.py --toolbox PATH` overrides it. `.gitignore` already excludes the directory.
3. **Verify before trusting it.** Run `scripts/validate_shapecomp_port.py`. It re-hashes the files
   the port actually reads and compares them with `official_toolbox_hashes.json`, which lists 24
   inputs — the three `net/keras/*.h5` weight files, every `ShapeComp_src/*.m` the port translates,
   `ShapeComp Readme.rtf`, `loadShapeComp.m` — each with byte count, SHA256 and upstream mtime.
   A mismatch means the numbers below were produced against a different artefact than yours.
4. **What stays out of Git, and why.** The `.h5` weights, the toolbox archive, and every copied
   `.m` / `.c` / `.mex`. `.gitignore` blocks the whole `shapeComp_17_12_2024/` directory and, so a
   copy elsewhere cannot quietly succeed, `*.h5` and `shapeComp_*.zip` as well. The repository
   commits *hashes, byte counts and the commands that regenerate everything*, which is enough to
   reproduce and to detect substitution, without redistributing anyone else's binaries.
5. **Licence, stated plainly.** The article is CC BY-4.0 and the Zenodo deposit is CC BY-4.0.
   **Neither licence covers re-publishing the toolbox weights.** CC BY-4.0 licenses the *article
   and its deposited data*; the `net/keras/*.h5` binaries and the MATLAB sources are a separate
   distribution that carries only the authors' "please cite" notice in `ShapeComp Readme.rtf` and
   no explicit redistribution grant. So "the paper is CC BY" must never be shortened into "the
   weights may be mirrored here". The vendored third-party code inside the toolbox is a third
   licence again — `misc/geom2d` is BSD-3 (INRA, D. Legland), `misc/psignifit` is GPLv3 — and none
   of it is executed by the port or shipped in this repository.
6. **Cite the source if you use these arms.** Morgenstern et al. (2021), *PLOS Computational
   Biology* 17(1): e1008981, plus the toolbox version you actually loaded. The full reference is
   in `official_toolbox_hashes.json → citation`.

## Naming discipline

**P0-4.6 Checkpoint 7-D, item F3.** Five different numbers get talked about in the same sentence
and describe four different things. They are kept apart in every table and figure:

| Name | What the number counts | Where it comes from | Can it score a new shape? |
|---|---|---|---|
| **109** | the **official descriptor set** — the full ShapeComp feature list (S1 Table of the paper) | the authors' MATLAB engine, which the 2024 toolbox does **not** contain | no — the engine is not obtainable, so nothing here computes it |
| **22** | the **official coordinate space** — classical MDS of those 109 distances over 25,712 animal silhouettes, first 22 dims | `S0` published columns; `S0K1`/`S0K2` network outputs | `S0`: no. `S0K1`/`S0K2`: yes |
| **91** | **EarthGlyph E1 only** — the self-written, ShapeComp-*inspired* descriptor vector (global 10 + moments 6 + curvature 18 + fourier 15 + histogram 42) | `earthglyph/retrieval/perceptual_features.py` | yes |
| **20** | the production sketch-retrieval feature vector (Hu + Fourier + alignment extras) | the pre-benchmark pipeline | yes, and it is **not** what any benchmark arm runs |

Read off that table:

1. **S0** — official ShapeComp as *published precomputed coordinates only*: the 22-D MDS
   projection of the authors' 109-descriptor space. Cannot score a new shape; D1 only.
2. **S0K1 / S0K2** — the authors' **own KerNet1 / KerNet2 weights actually run** through the
   numpy port (h5py forward pass, no TensorFlow, no retraining). These *are* official network
   outputs (validated against the published columns), can score new shapes, and appear in every
   retrieval stage. **Not rotation-invariant as shipped** — every rotated use needs one of the
   four wrappers measured in Checkpoint 7-C (`earthglyph/shape_benchmark/rotation_methods.py`),
   and each wrapper changes what the arm measures: `S0Kx/naive` is the network alone,
   `S0Kx/R0|R1|R2|R3` is network + a declared pose policy. Never quote one label for the other.
   Mirror invariance is not a property of these arms at all: `allow_mirror=True` on
   `S0`/`S0K1`/`S0K2` **raises** instead of silently doing nothing (D72).
3. **E1** — EarthGlyph **ShapeComp-inspired**, 91 self-written descriptors. Never relabelled as
   official, never credited with official results, and **never described as a port of the 109**.
4. **E0 / E3** — EarthGlyph geometric alignment / Shape Context.

> **Correction (2026-11-09).** Earlier drafts of this file, `INTERPRETATION.md`, `notes.md`,
> `docs/TASKS.md` and the experiment log described the published `shape1SC/shape2SC` columns as
> "the original **91**-descriptor ShapeComp MDS space". That was wrong in two ways at once: the
> official descriptor count is **109**, and the coordinates themselves are **22**-dimensional.
> The 91 belongs to E1, which is EarthGlyph's own set. The wording is corrected everywhere it
> appears; no number in any result file changes, because the mistake was in prose only. Anyone
> reading an older commit should treat "91-descriptor ShapeComp" as "109-descriptor model, 22-D
> coordinates".

No self-written **descriptor** port was created, because the brief forbids passing one off
as a ShapeComp reproduction. The port that *was* written translates only the official
preprocessing and runs the official **weights** verbatim — a different thing, explicitly
authorised by the user on 2026-11-08.