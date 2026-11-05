# ShapeComp reproducibility audit — primary-source evidence log

**Task:** factual, evidence-based audit of "An image-computable model of human visual shape
similarity" (Morgenstern et al., 2021, PLOS Comput Biol) and the ShapeComp tool, to decide
reproducibility for EarthGlyph P0 perceptual-probe work.
**Environment:** Windows / Git Bash; curl 8.23.0; Python 3.12; WebFetch tool. All network probes
were run from `C:/Users/1/AppData/Local/Temp/` (nothing >20 MB downloaded, nothing written to repo
except this file). **Date of audit:** 2026-11-XX (system clock in probes showed 2026-11-06).

Legend: **[Verified]** = read directly from a primary source this session. **[Blocked]** = source
exists but unreachable from this machine (evidence given). **[Not found]** = absent from every
primary source I could reach. **[Inference]** = derived reasoning, flagged as such, not a quote.

---

## 0. CRITICAL ATTRIBUTION MISMATCH (read this first)

The user attributed the paper to authors
*"Morgenstern, Schmidt, Zelva, Ezzy, Gerbin, Aharon-Halevi, Baror, Parkes, Witzel, Singh."*

**[Verified — the paper at the given DOI has only 7 authors.** DOI
`10.1371/journal.pcbi.1008981` resolves to:
> Morgenstern Y, Hartmann F, Schmidt F, Tiedemann L, Prokott E, Maiello G, Fleming RW.
> *An image-computable model of human visual shape similarity.* **PLOS Computational Biology**
> 2021;17(6):e1008981. PMID **34061825**, PMCID **PMC8195351**. Received 2020-09-25, accepted
> 2021-04-19, published **2021-06-01**.

- The names **Zelva, Ezzy, Gerbin, Aharon-Halevi, Baror, Parkes, Witzel appear 0 times anywhere in
  the paper** (not as authors, not as citations).
- "Singh" appears 7 times but **only as cited prior work** (Feldman & Singh, Wilder/Feldman/Singh —
  the shape-skeleton source), never as an author.
- The Zenodo README lists the same 7 authors (Morgenstern, Schmidt, Hartmann, Tiedemann, Prokott,
  Maiello, Fleming).
- **The supplied PMC URL `PMC7894401` is WRONG.** `PMC7894401` maps to an unrelated
  *Clinical Pharmacology & Therapeutics* article (a ertugliflozin/mefenamic-acid PBPK study). The
  correct PMC ID is **PMC8195351** (resolved via Europe PMC search-by-DOI). This wrong ID is exactly
  why two earlier WebFetch calls returned a pharmacokinetics abstract.
- **[Not found] Any "ShapeComp 2" / 2024–2025 update.** WebSearch for the alternate author list
  returned "No results found"; Europe PMC author-combination query (`Witzel C` + `Singh R` + shape
  similarity) returned zero rows; Zenodo `q=shapecomp` returns only 3 records (see §7), none a v2.
  **[Inference]** The 11-author list (Aharon-Halevi, Baror, Parkes, Witzel, Singh = Hebrew University
  shape-perception group) plausibly belongs to a *different, newer* ShapeComp-related paper that is
  not indexed/reachable from this machine. It is **not** the 2021 PLOS paper. Confirm the intended
  source with the user before citing "ShapeComp 2021" facts against that author list.

Reproduction:
```bash
# wrong-vs-right PMC id
curl -sS "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:%2210.1371/journal.pcbi.1008981%22&resultType=core&format=json"
# full text of the real paper
curl -sSL -A "Mozilla/5.0" "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC8195351/fullTextXML"
```

Bibliographic facts used below were read from `https://pmc.ncbi.nlm.nih.gov/articles/PMC8195351/`
full text + `pcbi.1008981.s004.docx` (S1 Table) — **this is the paper the DOI/PMID point to.**

---

## A. The 109 shape descriptors — families, computation, normalization

**[Verified] Full authoritative list = S1 Table ("Table S1. List of 109 shape descriptors in
ShapeComp")**, extracted from `pcbi.1008981.s004.docx` inside the Europe PMC supplementary archive.
Main text only says "A full list of the 109 descriptors is listed in S1 Table."

Key representation split **[Verified from S1 Table note]**: *"Computations for (1)-(22) are based on
image representation of shape. (23-109) are based on point representation of shape."*

Families (numbering is the S1 Table's):

1. **Simple descriptors (1–28)** — mostly MATLAB `regionprops` (→ Image Processing Toolbox). Area,
   Perimeter, Eccentricity, **Major Axis Orientation** (1–4); ratio of principal axes (5); Extent (6);
   Compactness (7); Solidity (8); Circularity A = 4π·Area/Perimeter² (9); Circularity B =
   Perimeter²/Area (10); Convexity = convex-hull perimeter / contour perimeter (11); Centroid X/Y
   (12–13); Circularity Ratio (14); Convex Area (15); Area/Perimeter (16); x-intensity statistics
   std/skew/kurtosis (17–19); y-intensity statistics (20–22); **Curviness at different scales
   (23–26)** = RMS error of shape vs its own blurred versions with kernel size 2/4/8/16;
   Circular Variance (27), Elliptical Variance (28) — 27/28 use the **geom2d** toolbox (David
   Legland, MATLAB File Exchange).
2. **Shape Context (29–31)** — Belongie & Malik. Joint histogram of distance (15 bins) × angle
   (15 bins) (29); histogram of chord lengths (100 bins) (30); histogram of chord angles (100 bins)
   (31).
3. **Shape Signatures (32–38)** — radius, curvature, triangular-area, tangent-angle, cumulative
   tangent-angle, horizontal-point distribution, vertical-point distribution.
4. **Frequency Decomposition / Elliptic Fourier (39–49)** — Kuhl & Giardina; separate descriptors
   for first 10 coefficients (39–48) + complete Fourier description (49).
5. **Surprisal (50–51)** — Shannon information along contours (Feldman & Singh); signed (50),
   unsigned (51).
6. **Boundary Moments (52–87)** — mean/std/skew/kurtosis of each signature/chord histogram (groups
   of 4 each over descriptors 30–38).
7. **Shape Skeleton / medial axis (88–102)** — computed with **ShapeToolbox** (Wilder, Feldman,
   Singh): number of branches, max/mean depth, mean branch angle, mean stem distance, mean branch
   length rel. root, unsigned & signed total turning angle, std's, and rib-distribution moments.
8. **Fourier Component (103–109)** — absolute one-sided Fourier transform of each shape signature
   (radius, curvature, triangular area, tangent angle, cumulative tangent angle, X dist, Y dist).

**Not in ShapeComp** (the user hypothesized these; **[Verified absent]** from S1 Table & body):
**no Zernike moments, no Hu moments, no Gabor/wavelet filter banks.** The image "texture-like"
signal is limited to intensity moments (17–22) and curvature-at-scale (23–26). Fourier here is
**elliptic contour Fourier (Kuhl-Giardina)**, not region Zernike.

**[Verified] Normalization = per-descriptor max-range scaling, NOT z-score, NOT rank:**
> "The distance between shapes on each descriptor was scaled from 0 to 1 based on the range of values
> in a database of 25,712 animal shapes."
> "we computed the distances between each descriptor (scaled by their largest distance across the
> whole animal dataset)".
Each pairwise descriptor distance `d̂ᵏᵢⱼ = |fᵏᵢ − fᵏⱼ| / max_distance(k across the animal set)`
→ 0–1; then combined by a plain 109-dim Euclidean metric:
> `Dᵢⱼ = sqrt( Σ_{k=1..109} (d̂ᵏᵢⱼ)² )`  (equation in Methods; also the general form
> `ŝ = sqrt( Σ_i (f1_i − f2_i)² )`).

So scaling is **within-dataset standardization by the maximum pairwise distance per descriptor**
over the full 25,712-shape animal corpus. **[Inference]** To reproduce this on a different shape
universe you must recompute those per-descriptor maxima (they are corpus-dependent constants), which
is a real portability caveat.

---

## B. The 22-dimensional ShapeComp space — construction & projection of new shapes

**[Verified] It is NOT built from human similarity ratings.** The 22-D space is classical MDS on the
**descriptor-derived** similarity matrix of the animal corpus. The headline claim is "predicts human
judgments **without fitting any parameters to human data**."
> "We used **classical MDS** to find an orthogonal set of shape dimensions that captures the variance
> in the animal dataset."
> "We then computed classical MDS on the resultant 25,712 × 25,712 similarity matrix, taking the first
> 22-dimensions … as the ShapeComp model."
> "Across 25,712 animal shapes, **22 dimensions account for 95% of the variance** … first dimension
> only 18.54%" (95.05% at 22). MDS flavor stated in figure captions: *"dissimilarity: distances,
> criterion: metric stress"* → **metric (not non-metric) MDS**.

Human data were used only for **validation** (see §D). Number of shapes in the space = **25,712
animal silhouettes**.

**[Verified] Projection of a NEW, unseen shape — two routes:**
1. **Classical (exact) route, Methods:"Estimating coordinates for new shapes in pre-existing shape
   spaces":** (1) compute the new shape's 109 descriptors and compare against a ~25,000-shape subset;
   (2) run a **new MDS** on the enlarged (501×501) similarity matrix "scaled by the largest distance
   for each feature distance in the complete animal dataset"; (3) **Procrustes-align** the new MDS
   solution back onto the fixed animal MDS space. So it is **MDS re-computation + Procrustes
   superimposition**, *not* kernel regression.
2. **Neural (fast) route — MatNet/KerNet (§C):** networks output the 22 coords directly from a
   384×2 contour or a 40×40 image, bypassing the 109-descriptor computation and the MDS/Procrustes
   re-fit.

**[Verified] Held-out accuracy** (network approximation of ShapeComp, on 1000 untrained shapes,
pairwise-distance correlation vs the original ShapeComp solution): **MatNet r = 0.93, KerNet1 r =
0.94, KerNet2 r = 0.91** (all p < 0.01). Reproducibility of the *space itself*: distances computed
from 10 different random 500-animal subsets stay highly correlated; using only a random **55/109**
descriptor subset correlates less, and dropping the 11 most predictive (Shape-Context) features still
leaves the spaces correlated at **r = 0.77**.

---

## C. MatNet vs KerNet1 vs KerNet2

**[Verified, Methods:"Shape to ShapeComp network"]:** all are multi-layer CNNs trained to map a shape
to its 22-D ShapeComp coordinates, trained on **800,000 GAN-generated shapes** (dataset: 950,000 GAN
shapes = 800k train / 150k test), whose 22-D targets were themselves produced by the MDS+Procrustes
route in §B.

| Net | Framework / language | Input | Starting-point handling |
|---|---|---|---|
| **MatNet** | MATLAB (Neural Network Toolbox) | 384×2 contour (x,y) | assumes **min-x contour point is first** |
| **KerNet1** | Keras/TensorFlow (**Python**) | 384×2 contour (x,y) | assumes **min-x point is first** |
| **KerNet2** | Keras/TensorFlow (**Python**) | **40×40 binary image** | image-based → avoids contour correspondence / start-point problem (used for shapes with multiple min-x, e.g. the circular-shape set) |

Contour normalization **[Verified]**: "x,y coordinates (384×2) … scaled to {0–1} by first subtracting
the absolute minimum value of each coordinate, and then dividing by the resulting absolute maximum."

**Inference-time needs [Verified / Inference]:** the trained networks need **weights only** (no 25k
reference set at inference; the net replaces the MDS/Procrustes machinery). MatNet needs MATLAB +
Neural Network (Deep Learning) Toolbox; KerNet1/2 need Python + Keras/TensorFlow. **[Blocked]**
Whether the actual weight files are published is unknown from this machine (they are on the Google
Sites page, §code below — unreachable); the two Zenodo records contain **no** `.h5/.onnx/.pb/.pth`
weights and **no** `.py` files.

**[Verified] r with human ratings:** the 0.91–0.94 figures above are network-vs-ShapeComp, **not**
network-vs-human. Human-facing correlations are the §D values (e.g. ShapeComp 22-D vs 14-observer
mean ratings **r = 0.91**). Baselines vs human: full 109-descriptor space **r = 0.63**; ShapeComp
stated to beat pixel **IoU** and pre-trained CNNs (resnet50/resnet101/googlenet/inception_resnet_v2)
— **[Not found] the exact CNN/IoU r numbers in the body text** (given only in Figs 5D–G, 7D; the
CNN `.mat` response arrays are in the deposit but not transcribed here).

---

## D. What the human experiments actually validated

**[Verified] Stimuli = animal silhouettes + GAN-generated novel shapes + one literature "circular
shape space" set** (ref [90], "The validated circular shape space"). MDS/arrangement analysis uses
Kriegeskorte & Mur multi-arrangement code + a pairwise rating task. Sample sizes per experiment:
- Real-world animals: **15 participants** (mean age 24.7, range 20–35) arranged two 20-shape sets
  (rabbits, horses; from Bai et al.); r = 0.63.
- Similarity ratings: **250 GAN shape pairs, 14 participants** → ShapeComp 22-D vs mean dissimilarity
  **r = 0.91, p<0.01**.
- Circular-shape-set generalization: **105 pairs → r = 0.78, p<0.01**.
- Multi-arrangement novel sets: 4 sets, r = 0.56 / 0.69 / 0.74 / 0.72 (16-participant means).
- Triad/pixel-similarity experiment: **19 participants** (t = −7.63, df = 18).
- Ethics: paid €8/hr, consent approved by Justus-Liebig-Universität Giessen ethics board; run in
  MATLAB + Psychophysics Toolbox on a Mac Mini 2012, MATLAB 2015a.

**[Verified] Transformations tested (Methods:"Sensitivity/robustness …", Fig 1, S1 Fig):** rotation,
shear, "bloating", and contour noise — applied to one exemplar per 20 animal categories; reported as
**per-descriptor sensitivity** (0/0 and 0×Inf excluded via `nanmean`), **not** as a single model-level
human-accuracy number.

**[Not found / never tested]:**
- **"Star vs circle" structure-salience**: absent (the words star/circle appear only re: GAN "spiky"
  insets and the "circular shape space" set; no structure-salience experiment).
- **Mirror/reflection invariance**: "mirror" appears 0 times → not measured.
- **[Verified present] Rotation IS probed** (see §E), but only as descriptor sensitivity, not a
  human-accuracy drop.

---

## E. Invariances in descriptor computation

**[Verified]**
- **Scale & translation:** handled by min–max normalization of the 384×2 contour (subtract min, divide
  by range) and by `regionprops`/normalized descriptors; many simple descriptors are ratios.
- **Rotation:** **not** globally invariant at the descriptor level — handled per descriptor. "Some
  descriptors (e.g. **Solidity**) are entirely invariant across rotations, while others (e.g. **Major
  Axis Orientation**) are sensitive to orientation." There is an explicit **robustness/sensitivity
  trade-off** (bloating-sensitive ↔ rotation-insensitive and vice versa). Rotation in the sensitivity
  analysis is applied about the centroid via a standard rotation matrix. The **MDS space** is noted to
  be rotation-invariant in the sense that "individual dimensions should not be thought of as 'cardinal
  axes'."
- **Starting point:** **26 of the 109** descriptors are contour-based and require an initial point.
  Heuristic: **leftmost (minimum-x) point = first contour point.** Tie handling when min-x repeats:
  ~3.5% of shapes (neighboring point) and ~0.3% (point further along) resolved **randomly**.
- **Traversal direction (clockwise/counter-clockwise):** **[Not found]** — the body text never
  discusses traversal direction; "traversal" and "clockwise/counterclockwise" occur 0 times.
- **Measured performance drop when rotated:** **[Not found]** as an aggregate figure; only the
  per-descriptor sensitivity spectrum (Fig 1E, S1 Fig) is reported.

---

## F. Compute cost & MATLAB toolbox requirements

**[Verified] Cost:** no numeric runtime in the paper. Qualitative statements:
> "the computation of all 109 features along with pairwise comparisons with values pre-computed from a
> large dataset of stored animal shapes **is too slow for real-time applications**"; and (reviewer,
> quoted in the open peer-review) "computing the 109 dimensions using a collection of shape processing
> tools would be **extremely burdensome**." Body also: "the codebase is **convoluted** as it draws on
> many different sources." **[Inference]** This is precisely why MatNet/KerNet exist (one CNN replaces
> the whole 109-descriptor + MDS pipeline).

**[Verified] Toolbox / library dependencies** (from S1 Table + Methods; note the body text never says
"Image Processing Toolbox" — the 3 "Image Processing" hits are journal names in the reference list):
- **`regionprops`** for descriptors 1–22 → MATLAB **Image Processing Toolbox**.
- **geom2d** (David Legland, File Exchange) for 27–28.
- **ShapeToolbox** (Wilder/Feldman/Singh) for the medial-axis skeleton 88–102.
- **Global Optimization Toolbox** (genetic algorithm) for the GAN-shape stimulus optimization.
- **Neural Network / Deep Learning Toolbox** (MatNet + pre-trained CNN evaluation); **Keras +
  TensorFlow** for KerNet1/2 (Python).
- **Psychophysics Toolbox** for the experiments. **[Not found]** any mention of a Statistics Toolbox.
- Third-party **psignifit** bundled in the deposit is **GPLv3** (see §G).

---

## G. Licensing & data provenance

**[Verified] Paper:** open access **CC BY 4.0** (`© 2021 Morgenstern et al …
creativecommons.org/licenses/by/4.0`). PMID/PMCID as in §0. Preprint **[Verified]** bioRxiv
`10.1101/2020.01.10.901876` (cited in Zenodo README; bioRxiv itself returned HTTP 429 to WebFetch).

**[Verified] Zenodo data deposits (owner id 77400, both CC BY 4.0):**

| Record | Zip | Size (compressed) | md5 | Unpacked | What's inside (from ZIP central directory) |
|---|---|---|---|---|---|
| **4730985** (the DOI in the paper's Data-Availability) | `shapecomp datashare.zip` | **307,965,089 B** (~308 MB) | `c084b94e6ac2defe74542b36c7aae5fe` | ~2,230.9 MB; 968 real files + 1042 `__MACOSX` junk | Figure-repro scripts + arrays: **1402 .mat**, 240 .png, **74 .m**, 4 .xlsx, 2 .txt; **no .py, no weights** |
| **4892335** (same title, larger, rev 2) | `shapecomp datashare.zip` | **2,232,376,854 B** (~2.23 GB) | (not pulled) | — | 701 .mat, **153 .m**, 120 .png, plus bundled psignifit + Psychtoolbox; **no .py, no weights** |

- Download URL pattern **[Verified, HTTP 206/200]**:
  `https://zenodo.org/api/records/<id>/files/shapecomp%20datashare.zip/content`
  (and human form `https://zenodo.org/records/<id>/files/shapecomp%20datashare.zip?download=1`).
  Confirmed downloadable from this machine with a **non-Mozilla** UA (first 2 bytes `PK` valid zip;
  `HEAD` → 200, `Range 0-1` → 206, content-length 307,965,089).
- **[Blocked] The 403 mystery — RESOLVED: it is a bot-block keyed on the User-Agent header, NOT a
  network block.** Evidence matrix (same second, same file):
  ```
  UA='Mozilla/5.0'        -> api=403  page=403     (Zenodo bot protection)
  UA='curl/8.23.0'/default-> api=200  page=200
  UA='' (none)            -> api=200  page=200
  UA='python-urllib/3.12' -> api=200  page=200
  ```
  **[Verified] I could NOT reproduce the user's "Python urllib returns 403"** — urllib on this machine
  returned **200** for both `/api/records/4730985` and the `/records/4730985` HTML page. The 403 the
  user saw almost certainly came from a browser-like `Mozilla/*` UA being sent (or a proxy setting one).
  Fix: request Zenodo with curl/`python-urllib`/no UA, or override the UA away from "Mozilla".
- Contents of `4730985` are organized **by figure** (`Figure 1`…`Figure 9`, `misc`), containing the
  analysis `.m` + result `.mat` for reproducing the paper's figures and the experiment stimuli — the
  `.m` files are figure scripts and helpers (`add2path.m`, `plotShapeSpace.m`, `adjustContourPos.m`,
  `squareSimmat.m`, `vectorizeSimmat.m`, `horizline/vertline/shadedErrorBar.m`, per-figure
  `Figure1E.m`/`Figure2*.m`/…, `getMeanSimmat.m`, `view_rating_expt_stim_and_resp.m`) plus a vendored
  `misc/psignifit/**` (**GPLv3**) and old Psychtoolbox `.m`. **There is no reusable "compute the 109
  descriptors for an arbitrary shape" engine and no MatNet/KerNet inference code or weights in either
  Zenodo record.** `Figure 2/shapefeats/featureSimmatsAnimal_MA_Set_*.mat` are **precomputed feature
  similarity matrices**, not shape coordinates or a descriptor calculator.
- **[Verified] The deposit does NOT contain the raw 25,712 purchased silhouettes** (no such folder;
  only `.mat`/`.png`/`.m`). **[Inference]** consistent with stock-license redistribution limits below.

**Data provenance [Verified, Methods:"Animal shape analysis"]:**
> "We amassed 25,712 animal shapes — **purchased from shutterstock** (e.g., Natalia Toropova; Big
> animal silhouettes set), based on **3D animal models purchased from https://evermotion.org** (e.g.,
> archmodels volume 83), or gathered from previous work [141,142]."
So **the animal silhouettes were commercially purchased** (Shutterstock + Evermotion).
**[Inference]** Standard Shutterstock/Evermotion licenses do **not** permit free redistribution of the
stock assets, which is why only derived data (similarity matrices, GAN shapes, experiment stimuli) sit
in the CC BY deposit. The CC BY 4.0 tag attaches to the *deposit contents*, not to the underlying
commercial stock imagery.

**Reimplementing the 109 descriptors [Verified/Inference]:** the descriptors are published academic
algorithms (regionprops, shape context, elliptic Fourier, medial-axis skeleton) — **no patent or
non-commercial restriction is stated anywhere in the primary sources**. However a faithful port is
encumbered by the *third-party toolboxes* it mirrors: **geom2d**, **ShapeToolbox**, and the bundled
**psignifit (GPLv3)**. The **ShapeComp MATLAB code itself** (model + GAN + demo) is **not** in Zenodo;
it is distributed from the author's Google Sites page whose **explicit license is [Blocked/Not found]**
(page unreachable this session — see §code).

---

## H. Official Python port?

**[Verified] There is NO official Python port of the 109-descriptor engine** in any primary source I
could reach, and **no** `.py` files in either Zenodo record; GitHub API repo search for `ShapeComp`
returned **only unrelated projects** (ShapeCompletion3DTracking, ShapeCompiler, shapecompare, etc.) —
no author-owned repository. GitHub code search was **[Blocked] HTTP 401** (unauthenticated).
**[Verified] The one official Python component is KerNet1/KerNet2** — Keras/TensorFlow networks that
project a contour / 40×40 image directly to the 22-D space, i.e. an *approximation* that skips the
descriptors entirely. They are described in the paper and, per the README, distributed on the Google
Sites tools page (**[Blocked]** — could not verify their Python source or license this session).

**[Verified] Where the real code lives (from Zenodo `README.txt`):**
> "The ShapeComp model, GAN network that produces naturalistic outlines, and demonstration code in
> MATLAB that shows how to implement the ShapeComp model on a set of GAN shapes, or your own customized
> shapes, can be downloaded at: https://sites.google.com/view/yanivmorgenstern/home/tools/shapecomp"

**[Blocked] That Google Sites page is unreachable from this machine:** `curl -m 20` → *Connection timed
out* (code=000); `WebFetch` → *fetch failed*; JS-reader proxy `r.jina.ai` → *fetch failed*. Same class
of block as the `github.com:443` HTTPS issue noted in `AGENTS.md`. So the actual MATLAB toolbox,
MatNet/KerNet weights, their file names, sizes, checksums, download URLs, and their license **could not
be verified from this host** — they must be fetched from a network that can reach `sites.google.com`
(or via the SSH-over-443 route for GitHub if a mirror exists). The paper mentions **no OSF/GitHub
project** for ShapeComp (strings "osf", "github", "gitlab" occur 0 times in the body); the OSF id
`8dsqc` reachable by curl is **unrelated / unverified** and is *not* cited by the paper.

Reproduction of the block:
```bash
curl -sS -m 20 "https://sites.google.com/view/yanivmorgenstern/home/tools/shapecomp" -o /dev/null -w "%{http_code}\n"   # -> 000 (timeout)
curl -sS "https://api.github.com/search/repositories?q=ShapeComp"                                                        # -> no official repo
curl -sS "https://api.github.com/search/code?q=ShapeComp+MatNet"                                                         # -> 401 requires auth
```

---

## Concrete citable facts (quick reference)

- Journal **PLOS Computational Biology**; **2021;17(6):e1008981**; **DOI 10.1371/journal.pcbi.1008981**;
  **PMID 34061825**; correct **PMCID PMC8195351** (NOT PMC7894401). Published **2021-06-01**; received
  2020-09-25; accepted 2021-04-19. License **CC BY 4.0**. Authors: **Morgenstern, Hartmann, Schmidt,
  Tiedemann, Prokott, Maiello, Fleming** (7). **[Verified]**
- Preprint: **bioRxiv 10.1101/2020.01.10.901876**. **[Verified]** (Zenodo README; bioRxiv returned 429 to WebFetch).
- Data: **Zenodo DOI 10.5281/zenodo.4730985** (record created 2021-05-08, updated 2021-06-02, rev 3,
  publication_date 2021-04-30) → single file `shapecomp datashare.zip`, **307,965,089 B**,
  **md5 c084b94e6ac2defe74542b36c7aae5fe**, **CC BY 4.0**. Sibling/larger record **4892335** zip
  **2,232,376,854 B**. **[Verified]**
- Descriptor count **109**; **26** are contour-based requiring a start point; **(1–22) image-based,
  (23–109) point-based**. **[Verified — S1 Table]**
- Normalization: **per-descriptor distance ÷ max pairwise distance over the 25,712-shape corpus (0–1)**,
  combined by 109-D Euclidean; **not z-score/rank**. **[Verified]**
- 22-D space: **classical (metric) MDS** on the descriptor similarity matrix of **25,712 animal
  silhouettes**, first 22 dims = **95.05%** variance (dim 1 = 18.54%). **[Verified]**
- Projection of new shape: **MDS re-fit + Procrustes** (exact) or **MatNet/KerNet** CNN (fast). **[Verified]**
- Held-out correlation to ShapeComp: **MatNet 0.93 / KerNet1 0.94 / KerNet2 0.91** (1000 untrained
  shapes). ShapeComp vs human (250 GAN pairs, 14 obs) **r = 0.91**; 109-descriptor space vs human
  **r = 0.63**; circular-set generalization **r = 0.78**; drop shape-context features → **r = 0.77**. **[Verified]**
- Networks trained on **800,000 GAN shapes** (of 950,000 = 800k train /150k test). Input 384×2 contour
  (MatNet/KerNet1) or 40×40 image (KerNet2). **[Verified]**
- Provenance: silhouettes **purchased from Shutterstock + Evermotion (archmodels v83)** + prior work. **[Verified]**

## Bottom line for EarthGlyph

- **Official ShapeComp (MATLAB model + GAN + demo + MatNet/KerNet weights) cannot be fetched or run
  here** — code is only on `sites.google.com`, which **times out** from this machine; the **403 was a
  User-Agent bot-block, not a network block** (Zenodo API/page fetch fine with `curl`/`urllib`/no UA).
  Neither Zenodo record contains the descriptor engine, the projection code, weights, or the raw
  purchased silhouettes — they are figure-reproduction data.
- The **"22-D space" = classical MDS over 109 descriptor distances on 25,712 animal shapes**
  (95% variance in 22 dims); **new shapes enter via MDS+Procrustes, approximated by MatNet/KerNet CNNs.**
- **Worth reimplementing (portable, no weights, no MATLAB, no commercial images):** simple
  regionprops-style descriptors (area, perimeter, solidity, eccentricity, circularity, convexity,
  extent + x/y image moments), **curvature-at-scale (23–26)**, **shape context + chord
  histograms (29–31, the most predictive family)**, shape signatures (32–38),
  **elliptic Fourier (39–49)**, surprisal (50–51), and moments-of-signatures (52–87). Medial-axis
  skeleton (88–102) is the hardest (needs a robust skeleton like ShapeToolbox) and was among the
  *least* predictive. Reimplement the **max-per-descriptor 0–1 normalization** against your own corpus,
  then the 109-D Euclidean + classical MDS — this reproduces the core with pure Python/NumPy/Shapely.
- **Licensing ceiling:** paper + Zenodo deposits are **CC BY 4.0** (attribution required). Do **not**
  redistribute the Shutterstock/Evermotion silhouettes (not in the deposit, commercial stock license).
  Respect **GPLv3** if you reuse bundled psignifit logic, and check **geom2d / ShapeToolbox** terms.
  The ShapeComp **MATLAB code's own license is unverified** (page blocked) — confirm before vendoring.
