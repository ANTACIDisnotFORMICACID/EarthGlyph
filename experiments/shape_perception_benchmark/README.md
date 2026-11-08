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
| `results/figure8_network_audit.json` | raw-array recomputation of the official MatNet/KerNet correlations, input-contract and H1-overlap checks; model-vs-model only |
| `results/shapecomp_port_validation.json` | port faithfulness vs published **network** outputs (H2 KerNet2 ≈ exact, Figure 8 KerNet1/2), plus the H1 approximation-to-original check (explicitly not faithfulness) |
| `figures/` | overview, five comparison families × two poses, three human-evaluation figures (six-arm scatter) |

## Reproduce

```bash
# 1. acquire sources (8 MB of Range reads + 3.4 MB MPEG-7; no full-archive download)
.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py

# 1b. S0K1/S0K2 ONLY: official toolbox (gitignored, no redistribution licence) at
#     shapeComp_17_12_2024/ + the port extra. E0/E1/E3/S0 need none of this.
.venv/Scripts/python.exe -m pip install -e ".[shapecomp]"   # h5py + matplotlib

# 2. validate the port against published network outputs (seconds; writes the faithfulness JSON)
.venv/Scripts/python.exe scripts/validate_shapecomp_port.py

# 3. human-rating prediction (fast, ~50 s) — S0 is appended automatically for this stage
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2

# 4. retrieval stages (~40 min with the run networks; E3 is ~55 s per full-gallery query)
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2

# 5. combined headline table
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary

# 5b. official-network audit (seconds; model-vs-model, no human ratings)
.venv/Scripts/python.exe scripts/audit_figure8_networks.py

# 6. figures (reads the stored rankings; re-ranks nothing)
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all

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
   retrieval stage. Not rotation/mirror invariant.
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