# P0-4 审查报告 —— 真实索引上的第一个检索实验

| 项 | 值 |
|---|---|
| 审查对象 | `experiments/p0_retrieval_baseline/`（P0-4 全部产物）+ 其文档主张 |
| 审查日期 | 2026-11-07（复核时仓库 HEAD = `85aa9ae`，P0-4.5 Checkpoint B 之后） |
| 被审提交 | `8cec88b` feat（176 文件，425 passed，`899101d..8cec88b`，7.37 MiB / 189 objects）→ `e9ba12d` docs（排序契约更正）→ `4c4bf66` docs（核验补记） |
| 依据文件 | [`notes.md`](notes.md)（§2 运行命令 / §5 镜像探针 / §9 分层指标 / §10 归因 / §11 产物清单 / §12 主张边界）、`summary.json`、`results.csv`、`figures.json`、[`docs/EXPERIMENTS.md`](../../docs/EXPERIMENTS.md) §6、[`docs/TASKS.md`](../../docs/TASKS.md) 检查点表 |
| 审查方式 | 逐产物实测复核（哈希比对、行数统计、图片肉眼判读、代码定位），不采信文档转述 |

---

## 1. 结论

1. **交付成立。** P0-4 的三层证据（G1 实现正确性 / G2 受控鲁棒性 / G3 手绘式输入）都落在真实 839 候选索引上，241 行逐候选记录**全部保留**在 `results.csv`，失败案例独立成图，没有挑选性呈现。
2. **数据完整性可核验。** 索引三件套的 sha256 与 `figures.json → index.output_sha256` **逐字节匹配**（本次实测：features `b7255ede…` / shapes `fbaf4933…` / metadata `e18c3f95…` 全 MATCH）；该目录自 `8cec88b` 起 **0 次后续修改**（`git log -- experiments/p0_retrieval_baseline` 仅一条），git blob 即内容定址。
3. **P0 门禁仍未通过，且报告不得被读成"已通过"。** 门禁要求 ≥10 个**真人**手绘查询的 Top-10 肉眼判读（`docs/EXPERIMENTS.md` §6）；本轮 `drawn-*` 是程序生成的指针轨迹与栅格图，属 P0-6 缺口。
4. **发现 6 项问题**（§6）：F1、F2 是文档与产物的实际不一致（纯文档，可立即用一次 `docs:` 提交修掉）；F3、F4、F5 属 Checkpoint C 的实现与自证范围；F6 是 C 启动前必须定的归属。均不推翻 P0-4 结论。

---

## 2. 实测复核：分层指标

`top_k=10`、`shortlist_size=200`、`smoothing=light`、`allow_mirror=False`、`method=fourier_align`、`query_manifest_seed=20261105`、`n_queries=115`。
其中 `smoothing=light` 是 G1/G3 的口径；**G2 的 180 行 = 60 个探针 × `none/light/medium` 三档**（`results.csv` 的 `smoothing` 列实测 light 121 / none 60 / medium 60）。

### 2.1 G1 算法正确性（对真实轮廓施加已知变换，48 行）

| 族 | n | Top-1 | Top-10 | 中位排名 | 最差 | 真值 align 中位 | 标签 |
|---|---|---|---|---|---|---|---|
| self | 6 | 1.000 | 1.000 | 1 | 1 | 0.01286 | ok 6 |
| rot | 12 | 1.000 | 1.000 | 1 | 1 | 0.01275 | ok 12 |
| scale | 6 | 1.000 | 1.000 | 1 | 1 | 0.01273 | ok 6 |
| shift | 6 | 1.000 | 1.000 | 1 | 1 | 0.01286 | ok 6 |
| reverse | 6 | 1.000 | 1.000 | 1 | 1 | 0.01287 | ok 6 |
| **sparse** | 6 | **0.667** | 0.833 | 1 | 2 | 0.06398 | ok 5 · D 1 |
| **mirror（禁镜像）** | 6 | **0.167** | 0.667 | **5.5** | 10 | 0.33359 | ok 4 · D 2 |

- 前五族 = 平移/尺度/旋转/起点方向不变性**成立**（扰动幅度：`rot` 37°/191°、`scale` ×3.7、`shift` (+40,−25)、`reverse`、`self`），这是"实现正确"的证据，不是"检索准"的证据。
- `mirror` 的 0.167 是**策略正确**的表现而非缺陷：默认 `allow_mirror=False`。§5 的 6 个镜像探针给出机制级证据 —— 真值粗筛排名恒为 **1**（Fourier 幅值对镜像盲），禁镜像时精排把真值挤到错误行（D=0.074–0.524），开镜像后 **6/6 回到第 1**（D 塌到 0.004–0.247）。⚠ `results.csv` 的 241 行 `allow_mirror` **全为 False**，开镜像的对照是 §5 单独跑的探针，不在 CSV 内 → 不能从 CSV 复算出 1.000。
- `sparse`（26 点稀疏重采样）的 1 行非 ok 是 `sparse-r294` —— 同一个枝状水库 `row 294`，全库精排第 149 名 → 与 §2.3 的 D 类同源，不是采样档本身失效。

### 2.2 G2 受控鲁棒性（受控形变，180 行）

| 族 | n | Top-1 | Top-10 | 最差 | 真值 align 中位 | 标签 |
|---|---|---|---|---|---|---|
| jag | 54 | 0.833 | 0.833 | 1 | 0.03565 | ok 45 · D 9 |
| island | 18 | 1.000 | 1.000 | 1 | 0.07955 | ok 18 |
| bay | 18 | 0.833 | **1.000** | 8 | 0.07489 | ok 18 |
| aspect | 18 | 0.667 | 0.667 | 1 | **0.19764** | ok 12 · D 6 |
| gap | 72 | **0.583** | 0.583 | 1 | 0.07865 | ok 42 · **A 18** · D 12 |

- 每族 n 是**三个平滑档 × 探针数**：`jag` 54 = 3×18（18 = 3 强度 × 6 候选）、`gap` 72 = 3×24（24 = 4 规格 × 6 候选）、`island/bay/aspect` 各 18 = 3×6 —— 所以 G2 的 n 比 G1 大一个量级，两层的率不可直接横比。
- `bay` 的 Top-1 0.833 / Top-10 1.000 说明损失发生在**排序首位**而非窗口外。
- `aspect` 的真值 align 中位 0.198 是全表最大 —— 极端长宽比下"找到了但贴得不紧"。
- `gap` 的 30 行非 ok 里 18 行是 **A_extraction（根本没进检索）**，只有 12 行是检索层问题：读 G2 命中率时必须先剔除提取拒绝，否则把输入校验失败算成算法失败。

### 2.3 失败归因（四类，全部由实测排名决定）

`labels_overall = ok 180 · D_coverage_or_deformation 30 · A_extraction 22 · G3_no_ground_truth 9`；
**B_coarse_filter = 0，C_rerank_order = 0，C_dedup_or_truncation = 0** —— 两级链路的排序与截断环节没有观测到缺陷，失败集中在"输入被拒"和"库里覆盖不够/形变过大"。
D 的 30 行中 **22 行来自同一个枝状水库 `row 294`**（全库精排 23–502 名）→ 数据覆盖问题，不是算法问题。

### 2.4 平滑消融（全库 839 条，detail 带 k=9..16，基线占比 0.000402）

| 档 | σ | 细节保留 均值/最差 | compactness Δ 均值/最差 | 周长 Δ 均值/最差 |
|---|---|---|---|---|
| none | 0.0 | 1.000 / 1.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| light | 0.8 | 0.831 / 0.760 | 0.015 / 0.094 | 0.021 / 0.276 |
| medium | 2.0 | 0.337 / 0.197 | 0.044 / 0.236 | 0.057 / 0.513 |

`medium` 会削掉约 2/3 的细节频带、最差候选周长变化 51% —— 这就是默认 `light` 的代价依据。

### 2.5 短名单窗口扫描（`--sweep 100,200,300`，102 个探针）

三档窗口下逐族逐位**完全相同**（含 `mirror` 与 `gap` 全部一致）→ 粗筛窗口不是瓶颈。
⚠ 口径：该扫描用 `method="fourier"`（**只粗筛、不精排**，见 `scripts/evaluate_retrieval.py:368-394`），所以 `G1/mirror top1=1.0` 与 §2.1 的 0.167 不矛盾，读成"放大窗口能修镜像"是错的（见 F3）。

---

## 3. 图像产物审查（28 张）

| 图 | 张数 | 读法要点 | 审查判断 |
|---|---|---|---|
| `queries/*.png` | 6 | 320×320 手绘输入本身，不含结果 | 与 `*.json` 笔画通路成对；命名后缀 `_json`/`_png` = 两种输入通路，**不是两个查询** |
| `extraction_examples.png` | 1（1216×8800，22 行） | 原始轨迹 → 抽取外轮廓 → 128 点标准化轮廓；10 行是**拒绝案例**（原始笔画 + 报错原文） | 拒绝理由量化（如端点间隙 0.21 > 限 0.15），可复核；`n_inputs=121 / n_refused=10` 与 `figures.json` 一致 |
| `smoothing_ablation.png` | 1（3 面板） | 蓝柱=均值，**黄柱=最差候选**，约束来自黄柱 | 与 `summary.json → smoothing_ablation` 数值逐格一致 |
| `topk_<query>.png` | **10** | 左上红=查询；10 格为真实候选在**匹配器所选变换**下的叠加；每格四行 = rank+id / subtype+真实经纬度 / 面积 ha + `d` / θ+shift+mirror | 标题原样打印检索参数与 warnings（含 "these are the closest candidates in this library, **not verified matches**"）；排名未被图重排。⚠ 张数与文档不符 → F1 |
| `baselines_<query>.png` | 9 | 同查询同库同标准化的 hu / fourier / fourier_align 各 Top-5；标题写明 "overlays are the comparison, **not the numbers**" | `hu` 行 `d=0.0000, no re-rank` 且 θ 标 `(drawing only)`：hu 只有粗筛，展示对齐角是画图时补算，**不是该方法返回的变换** |
| `failure_cases.png` | 1（1350×4140，12 例） | 每行三列 = 查询 / 真值 / 实际返回 #1；第二列给 `true row N: align / coarse# / full# / returned#` | `n_shown=12 / n_non_ok_rows=52` 与 `summary.json → failure_figure` 一致；A_extraction 类只有两列（未进检索），符合定义 |

**图上不得读出的东西**：`d` 是无量纲内部距离（RMS 边界偏移），不是相似度百分比；经纬度真实 ≠ 地理上"你要找的那块"；θ/shift 是显示层拟合。

---

## 4. 主张边界（沿用 `notes.md` §12，审查确认无越界）

1. 不是人类手绘效果（`drawn-*` 为机器生成轨迹；真人判读属 P0-6）。
2. 不能说"算法优于 baseline"（三 baseline 在 9 个查询上 Top-1 互异，无人工标注）。
3. 不能说"检索准确率 X%"（G1/G2 是实现正确性，样本为 6 个候选的受控形变，非分层抽样；分级曲线属 P0-5）。
4. 不把 `align_distance` 当相似度（禁止输出百分比）。
5. 没有扩大数据、未新增类别（pending `add_now: false` 见 D32）。
6. 未做 256 点升级、未删复杂水库。
7. **P0 门禁仍未通过。**

审查补充：`hand_like` 13 行中 9 行为 `G3_no_ground_truth`、4 行为 `A_extraction`，5 行带 `weak_note` —— 手绘式输入的**可用证据面只有 9 行**，任何"手绘可用"的表述都超出数据支撑。

---

## 5. 可复现性核验

```bash
# 索引版本核验（本次实测三件套逐字节 MATCH）
python -c "import hashlib,json;f=json.load(open('experiments/p0_retrieval_baseline/figures.json',encoding='utf-8'))['index']['output_sha256'];print({k:hashlib.sha256(open(p,'rb').read()).hexdigest()==v for k,p,v in zip(f,('data/index/features.npy','data/index/shapes.npy','data/index/metadata.parquet'),f.values())})"

# 重跑命令逐字引用 notes.md §2（不在此重写，避免转述失真）：
#   1) python scripts/make_retrieval_queries.py --index data/index \
#          --out experiments/p0_retrieval_baseline/queries --seed 20261105
#   2) python scripts/evaluate_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
#          --out-dir experiments/p0_retrieval_baseline --smoothing light --ablation --sweep 100,200,300
#   3) python scripts/visualize_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
#          --out-dir experiments/p0_retrieval_baseline --top 10 --smoothing light --figures-for 'drawn-*'
#   4) 单查询调试：scripts/query_sketch.py（含 --method fourier --shortlist 300 --allow-mirror --chamfer-top 10）
# 完整原文与第 5 步（测试/静态检查）见 notes.md §2。
```

提交链：`8cec88b`（feat，实验本体）→ `e9ba12d`（docs，更正排序契约表述并补记核验）→ `4c4bf66`（docs，说明核验由 `e9ba12d` 承载）。

---

## 6. 发现的问题与建议

| # | 严重度 | 问题 | 证据 | 建议动作 | 归属 |
|---|---|---|---|---|---|
| F1 | 中 | 文档计数与产物不符：`docs/EXPERIMENTS.md` §6 写"9 张 `topk_*.png`"，实际 **10 张** | 多出的是 `topk_drawn-pentagram_json.png`（文件时间 11-05 10:14，其余 11:26；`notes.md` §8 记为 opt-in 运行） | 一次 `docs:` 提交把 §6 改为"10 张（含 1 张 opt-in）"并与 notes §8 交叉引用 | 立即可修 |
| F2 | 中 | 门禁段落只引"G1 五族 Top-1 = 1.000"，同层还有 `sparse 0.667` / `mirror 0.167` 两个负面族，读者会误判 G1 全绿 | `summary.json → layers.G1_correctness` 共 7 族 | 在 §6 门禁段补一句"另两族为负面结果，见 notes §9.1"，并保留 mirror 的策略性解释 | 立即可修 |
| F3 | 中 | 短名单扫描是**粗筛 only**口径，但输出里无标注，`G1/mirror top1=1.0` 与 headline 0.167 并置极易误读为"窗口能修镜像" | `scripts/evaluate_retrieval.py:368-394`（`method="fourier"`，注释写明 coarse filter only, no re-rank） | 在 `summary.json → shortlist_sweep` 与 notes §9.4 增加 `"search": "coarse_only"` 字段/说明（C 复用同一脚本时一并做） | C 一并修 |
| F4 | 低 | P0-4 没有"重跑必须字节一致"的自证：产物靠 git blob 定址（自 `8cec88b` 起 0 改动），但缺 P0-4.5 A/B 那种 manifest + sha256 + 递归 diff 机制 | `git log -- experiments/p0_retrieval_baseline` 仅 1 条；目录内无产物哈希清单 | C 的新产物（`results.csv` / `metrics.json` / 图）落地时按 D48 同标准出 `figures_manifest.json`（sha256 + `reproduce.git_commit` + 命令），并做一次重跑 diff 自证 | C |
| F5 | 中 | C 不能"只跑现成命令"：评测脚本无四臂开关，画图脚本只会画控制图库且 `--control-only` 是空转开关 | `scripts/evaluate_retrieval.py:151`/`:394` 写死 `method="align"`/`"fourier"`；`scripts/visualize_perceptual.py:245-249` 解析 `--control-only` 后从未读取、`labels` 取回即丢弃 | 新增按臂评测入口（调 `perceptual_methods.score_library`，18 查询 × 4 臂 × T1/T2/T3），并让 `--control-only` 生效或删除 | C |
| F6 | 低 | `perception_blind_review.png` 归属未决：Task #4 记为 C，`notes.md` §11 把盲评记为 D | 两处口径冲突 | 开跑前定归属（建议：图归 D，C 只出 `comparison_<family>.png` ≥5 + overview + CSV/JSON），并同步 TASKS | C 启动前 |

**未发现的问题（正面结论）**：无挑选性呈现（241 行全留）；归因 B/C 类为 0；索引哈希与记录一致；图与 `summary.json` / `figures.json` 数值互相对得上；`notes.md` §12 的七条限制在全部文档与图题中被实际执行，未见越界主张。
