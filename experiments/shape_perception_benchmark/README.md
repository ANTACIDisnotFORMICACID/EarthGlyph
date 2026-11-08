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
| **E3** Shape Context + rotation search | **0.839** | [0.762, 0.868] | 0.692 |
| **E1** EarthGlyph ShapeComp-inspired, 91-D, **ours** | **0.782** | [0.703, 0.819] | 0.640 |
| **E0** geometric alignment | **0.683** | [0.589, 0.736] | 0.579 |

The marginal CIs overlap, so the ordering is tested with a **paired** bootstrap of the
difference on the same pairs and observers. All six contrasts exclude zero; the weakest is
E3 − E1 = +0.057 [+0.006, +0.107]. Human consistency on the same pairs: observer-vs-observer
median ρ = 0.601, leave-one-out median = 0.779.

**S0 is not a local run.** It is the authors' published 22-D coordinates, and it cannot score a
new shape. It therefore appears in D1 only and is absent from every retrieval stage.

### D2–D4 — retrieval on real closed contours (MPEG-7, no human truth claimed)

Mean class-recall@10 over six queries; categories are object labels, **not** human similarity.

| Condition | E0 | E1 | E3 |
|---|---|---|---|
| T2 query + rigid near-duplicates removed | 0.717 | 0.833 | **0.917** |
| T3 T2 + wrong-category hard distractors | 0.717 | 0.833 | **0.917** |

E3 > E1 > E0 throughout. **T3 added no discrimination**: no synthetic distractor entered any
arm's Top-10, so the distractors were too gross to be "hard" (notes F19). Structure probes
(D3): every arm ranks a structural variant above every gross distractor; E1 suppresses
distractors hardest but drops one star variant from its Top-10, E0 keeps all variants but admits
a distractor at rank 23. Transform stability (D4): E0 is **not mirror-invariant** (Top-10 overlap
0.00; self-match falls to rank 246), E3's 15° rotation grid halves overlap at 37°/191°, E1 is the
most transform-tolerant of the three but still loses two thirds of its Top-10 under mirroring.
Per-query cost at ~1400 candidates: E0 ≈ 0.1 s, E1 ≈ 12 s, E3 ≈ 58 s.

### Official ShapeComp reproducibility

**Official ShapeComp cannot be run here, but its published model outputs can be used.**

- The official MATLAB toolbox, Demo 1/2, the 109-descriptor engine and all
  MatNet/KerNet1/KerNet2 weights live only on `sites.google.com`, `dropbox.com` and
  `drive.google.com`. All three are network-blocked from this machine (exact curl
  evidence in `official_shapecomp_status.json`). No MATLAB and no Octave is installed.
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
| `figures/` | overview, five comparison families × two poses, three human-evaluation figures |

## Reproduce

```bash
# 1. acquire sources (8 MB of Range reads + 3.4 MB MPEG-7; no full-archive download)
.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py

# 2. human-rating prediction (fast, ~50 s) — S0 is appended automatically for this stage
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3

# 3. retrieval stages (~25 min; E3 is ~60 s per full-gallery query)
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3

# 4. combined headline table
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary

# 4b. official-network audit (seconds; model-vs-model, no human ratings)
.venv/Scripts/python.exe scripts/audit_figure8_networks.py

# 5. figures (reads the stored rankings; re-ranks nothing)
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all

# single query, required CLI
.venv/Scripts/python.exe scripts/search_shape_benchmark.py --query mpeg7_Heart-1 --gallery mpeg7 --method E3 --top-k 10
```

Raw downloads are rebuildable from the URLs + checksums in `dataset_manifest.json`, so
they stay out of Git per `AGENTS.md`.

## Naming discipline

Three things are kept strictly apart in every table and figure:

1. **S0** — official ShapeComp. Here only as *published precomputed coordinates*.
2. **E1** — EarthGlyph **ShapeComp-inspired**, 91 self-written descriptors. Never
   relabelled as official, never credited with official results.
3. **E0 / E3** — EarthGlyph geometric alignment / Shape Context.

No self-written descriptor port was created, because the brief forbids passing one off
as a ShapeComp reproduction.