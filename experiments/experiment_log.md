# experiment_log — 实验与探测台账

> 追加式记录。**检索类实验**（自 P0-4 `p0_retrieval_baseline` 起）的指标写入其目录下的
> `results.csv`；数据探测类记录不构成检索证据。规则见
> [`docs/EXPERIMENTS.md`](../docs/EXPERIMENTS.md)。

| 日期 | id | 类型 | 数据 / 版本 | 目的 | 产物 | 结论 |
|------|----|------|-------------|------|------|------|
| 2026-11-04 | `p0_data_probe` | **数据检查（非检索）** | Overture Base/water, release 2026-10-21.0, bbox -90,44,-89,45, 6,543 行 | 确认真实水体轮廓可获取、Schema 正确、候选量与多样性足以支撑检索实验 | `dataset_summary.json`, `geometry_preview.png`, `notes.md` | 可用闭合轮廓 1,984（30.3%），lake/pond/reservoir ≈979；发现三类伪轮廓（下载框切片 / 游泳池矩形 / 河岸细长条）须在 P0-2 过滤；**未验证检索命中率** |
| 2026-11-05 | `p0_geometry_probe` | **数据预处理检查（非检索）** | 同上原始数据 → `data/processed/contours.parquet`（839 候选） | 目视确认清洗与标准化未破坏真实形状、过滤不过度、复杂形状（枝状/月牙/带孔/MultiPolygon/细长）保留 | `before_after.png`, `candidate_gallery.png`, `rejected_examples.png`, `complex_contours.png`, `summary.json`, `notes.md` | 6,543 行 → 839 标准轮廓，损失全部可解释；归一化保持比例；D15 框边过滤被几何证据推翻改为标志位；N=128 对极端枝状岸线欠采样（最坏步长偏差 ~104%）→ 采样消融留给 P0-3；**未验证检索命中率** |
| 2026-11-05 | `p0_feature_probe` | **特征定义 / 几何保真度 / 特征空间测量（非检索）** | Overture release 2026-10-21.0, bbox -90,44,-89,45 → `data/processed/contours.parquet`（839 候选，128 点）→ `data/index/` | ① Fourier 幅值描述子与 4 个几何统计量的定义、不变性与退化处理；② 谐波数 12/16/24 取舍；③ N=64/128/256 采样消融（从原始外环重采样）；④ 索引确定性与可回查；⑤ 候选库多样性与 pending 类别是否扩容 | `features_summary.json`, `harmonic_ablation.png`, `phase_loss_examples.png`, `near_duplicate_pairs.csv`, `candidate_distribution.png`, `sampling_metrics.csv`(2,517 行), `sampling_summary.json`, `sampling_comparison.png`, `sampling_zoom.png`, `sampling_error_scatter.png`, `sampling_metric_validation.{csv,json}`, `pending_summary.json`, `pending_parts.csv`, `pending_distribution.png`, `notes.md` | 谐波数取 16（12/24 无可测增益）；**N=128 保留**（Hausdorff 归一 p50 0.0157、region IoU 均值 0.9949；256 的增益不值字节翻倍），8 个候选在任何 N 都达不到 IoU≥0.95、5 个在 N=128 重建自交（全为 reservoir，已上报不修复）；云近似偏差经 2,517 对实测 0 违例；索引 839 行确定性构建、逐候选可回查真实经纬度；pending 建议 `add_now: false`；**未验证检索命中率** |
| 2026-11-05 | `p0_retrieval_baseline` | **检索实验（本项目第一个）** | 同 `data/index`（839 候选 / 128 点 / 20 维，索引 commit `77b083c9`）+ `scripts/make_retrieval_queries.py --seed 20261105` 生成的 115 轨迹 + 6 PNG 查询 | ① 手绘轮廓抽象提取（矢量轨迹 / 单封闭轮廓 PNG，歧义即报错）；② 平滑三档消融（全库代价）；③ 镜像 / 走向在整条管线上的行为；④ 三 baseline（Hu / Fourier / Fourier+闭式对齐精排）+ Chamfer 复核；⑤ 三层评估 G1 合成扰动 / G2 不规则草图 / G3 类手绘；⑥ 失败四类归因（A 提取 / B 粗筛 / C 精排 / D 覆盖） | `results.csv`(241 行), `summary.json`, `figures.json`, `queries/`(115 轨迹 JSON + 6 PNG + manifest), `extraction_examples.png`, `topk_*.png`(10), `baselines_*.png`(9), `smoothing_ablation.png`, `failure_cases.png`, `query_drawn-*.json`(3), `notes.md` | **G1**（实现正确性，非手绘能力）：self/rot/scale/shift/reverse **Top-1 = 1.000**（6/12/6/6/6），稀疏 26 点 0.667，镜像（默认禁）0.167 且真值粗筛排名恒为 1 → **幅值镜像盲已被实测**，`allow_mirror=True` 时 6/6 真值回到第 1（D 由 0.10–0.52 塌到 0.004–0.25）；**G2**：`none/light/medium` 三档排序结果**逐族完全相同**（45 个 ok 行中 44 行 Top-1），仅对真值距离变化 → 选 `light` 的依据是抖动/凹口折中，不是"更好看"；**全库消融**：light 保留 k=9..16 谱能量 0.831（最差 0.760）/ medium 仅 0.337（最差 0.197），medium 最坏改动 51% 周长；**短名单扫描 100=200=300**（逐族逐位相同）→ 粗筛窗口不是瓶颈；**归因**：ok 180 / A 提取 22（10 输入）/ **B 0 / C 0** / D 覆盖或形变 30（22 行集中于 row 294 枝状水库，全库精排 23–502 名）/ G3 无真值 9；**G3**：月牙→pond `f774dda7…`（−89.89456, 44.39991，D=0.150）、心形→lake `d3a552b9…`（−89.15608, 44.33571，D=0.106）、S 形→pond `79949450…`（D=0.109）、星形 D≈0.21 并标注弱命中（库里无角状地物）；三 baseline 的 Top-1 在 9 个查询上**互不相同**且距离量纲不可比（Hu 0–1.1e-4 / Fourier 0.003–0.023 / 精排 0.052–0.213）→ 不宣称任何方法"更准"。**G3 的 `drawn-*` 是机器指针轨迹，不是人类手绘；P0 门禁仍未通过（需 P0-6 ≥10 真人查询）** |
| 2026-11-06 | `p0_perceptual_probe` **A** | **数据构建（非检索）** | Phosphor MIT 25 SVG + Quick Draw CC BY 4.0 48 字节切片 + 本项目参数化 74 → 179 成员 / 18 查询 / 7 结构类 | 建立许可清晰、角色由实测几何裁决、划分与标签**先于任何算法**冻结的受控感知图库 | `gallery.jsonl`(179), `labels.json`, `queries/`(18), `rejections.csv`(75 968), `dataset_manifest.json`, `control_gallery.png`, `shapecomp_research.md` | 覆盖门槛实测达标（`shortfalls=[]`）；3 条角色降级 + 包络匹配 6/10 达标（残差公开）；9 对近重复在 T2 剔除；**一个检索方法都没跑** → 无命中率、无排名 |
| 2026-11-06 | `p0_perceptual_probe` **B** | **算法实现 + 自测量探针（非检索评价）** | A 冻结图库（`gallery.jsonl` sha256 `15be97ae…`（`_bevel` 修复后重建）、`labels.json` `26e444ce…`）+ 生成器合成分布；`data/index` 本轮**未使用** | 实现 E1/E2/E3 与四臂评分契约，并把每臂的不变性、被否决公式的代价量化成可重跑的数字 | `perceptual_methods.py`(137), `perceptual_features.py`(1520), `salient_structure.py`(831), `shape_context_baseline.py`(717), `perceptual_e0.py`(88), `verify_policy_claims.py`, `tests/` 4 文件(105→106), 本文 §Checkpoint B | 四臂在同一 128 点全库上可打分（无粗筛窗口），E0 默认行为逐位未变；E1 无主轴形状丢旋转不变（star vs 自身 37° 副本 4.8047，E0/E2 0.0000、E3 0.1405）；E3 分数**不对称**（780 对 median 0.0061 / 45 对全等 / 735 对非零）；E2 0.2% 噪声 0 事件、0.5% 仅 1 事件（0.0805 = 结构的 2.81%）；**仍未验证任何命中率 / 排名 / "更符合肉眼"** → 属 Checkpoint C/D |

## p0_data_probe 复现命令

```bash
python scripts/download_data.py --bbox=-90,44,-89,45 \
    --output data/raw/wisconsin_water.parquet --release 2026-10-21.0
python scripts/inspect_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/dataset_summary.json
python scripts/preview_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/geometry_preview.png \
    --count 32 --seed 42 --frame=-90,44,-89,45
pytest        # 65 passed（离线）
```

环境 / 代码：Python 3.12.10 venv，依赖版本见 [`docs/ENVIRONMENT.md`](../docs/ENVIRONMENT.md)；
Git commit：初始化阶段尚未提交首个 commit（等待确认），故本轮以文件 sha256 + 依赖版本锁定复现条件
（见 `data/raw/wisconsin_water.parquet.meta.json`）。

## p0_geometry_probe 复现命令

```bash
python scripts/prepare_polygons.py \
    --input data/raw/wisconsin_water.parquet --out-dir data/processed --force
python scripts/visualize_geometry.py \
    --contours data/processed/contours.parquet \
    --out-dir experiments/p0_geometry_probe
pytest        # 137 passed（离线；合成样本仅在 tmp_path）
```

确定性：可视化无随机种子，选样规则（面积分层 / 形状指标）与每幅图的 candidate_id
记录在 `experiments/p0_geometry_probe/summary.json`；重建 `contours.parquet` 字节一致
（sha256 见 `data/processed/data_manifest.json`）。

**重构回归校验（2026-11-05，P0-3 期间）**：把绘图辅助函数从 `scripts/visualize_geometry.py`
抽到 `scripts/probe_shared.py`（供 P0-3 特征 / 采样图复用）后，重跑上面的
`visualize_geometry.py`，四张 PNG 与 `summary.json` **全部字节一致**（`cmp` 逐文件比对），
故 P0-2 的可视化产物未因该重构而改变。

## p0_feature_probe 复现命令

```bash
python scripts/build_index.py --contours data/processed/contours.parquet \
    --out-dir data/index --n-harmonics 16 --fold positive --dtype float32 --force
python scripts/analyze_features.py --contours data/processed/contours.parquet \
    --out-dir experiments/p0_feature_probe
python scripts/analyze_sampling.py --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
python scripts/analyze_sampling.py --validate-exact --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
python scripts/analyze_pending.py --out-dir experiments/p0_feature_probe
pytest        # 281 passed（离线；合成样本仅存在于 tmp_path）
```

无随机种子（选样规则确定，`random_seed: null`）；索引与三个产物两次构建字节一致
（sha256 见 `data/index/data_manifest.json → output`）。
Git 检查点：`77b083c`（已推送，`git ls-remote origin` 与 `git rev-parse HEAD` 相同）；
提交后在工作区干净状态下重建索引，产物字节不变且 manifest `git_commit` = 该 sha。
完整结论、失败案例与"本轮不允许的主张"见
[`experiments/p0_feature_probe/notes.md`](p0_feature_probe/notes.md)。

## p0_retrieval_baseline 复现命令

```bash
python scripts/make_retrieval_queries.py --index data/index \
    --out experiments/p0_retrieval_baseline/queries --seed 20261105
python scripts/evaluate_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline \
    --smoothing light --ablation --sweep 100,200,300
python scripts/visualize_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline --top 10 --smoothing light --figures-for 'drawn-*'
python scripts/query_sketch.py experiments/p0_retrieval_baseline/queries/drawn-crescent.png --top 10
pytest        # 425 passed（离线；查询与结果图产物在本目录，索引不入库）
```

随机种子只在查询生成器（笔迹抖动 `as_trace`）；评估与可视化对同一查询集确定，
`query_sketch.py` 的输出除 `timings` 墙钟字段外逐字节可重复。索引未在本轮重建，
仍为 `data/index`（sha256 见 `data/index/data_manifest.json`，`summary.json → params.index` 一致）。
Git 检查点与完整结论、失败案例、"本轮不允许的主张"见
[`experiments/p0_retrieval_baseline/notes.md`](p0_retrieval_baseline/notes.md)。

> 更正记录：本台账 2026-11-05 `p0_geometry_probe` 行的"最坏步长偏差 ~104%"
> 在 P0-3 被重新归类为**弦长离散度**（`chord_dispersion`），不是重建误差；
> 同一候选 N=128 的重建误差实测为 Hausdorff 归一 0.1483 / region IoU 0.8104（见 D30）。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint A：受控感知图库）

```bash
# 首次联网构建（抓 25 个 Phosphor SVG + 48 个 Quick Draw 字节切片，并把区间钉进 cache/quickdraw/spans.json）
python scripts/prepare_perceptual_dataset.py --force
# 之后离线重放同一批字节区间（期望产物逐字节一致）
python scripts/prepare_perceptual_dataset.py --offline --force
# 确定性核验：重建到临时目录，但复用同一 cache（--out-dir 必须已存在，脚本不替你创建）
mkdir -p .scratch/determinism && python scripts/prepare_perceptual_dataset.py --offline --force \
    --out-dir .scratch/determinism --cache-dir experiments/p0_perceptual_probe/cache
diff -r --exclude=cache .scratch/determinism experiments/p0_perceptual_probe   # 只差 built_at_utc 与命令字符串
pytest -q -o addopts=""     # 498 passed
ruff check .                # All checks passed
mypy                        # Success: no issues found in 26 source files
```

内容：179 成员 / 18 查询 / 7 结构类（band 3-13-11、blob 3-7-10、crescent 2-11-13、
forked 3-14-10、heart 2-9-10、irregular 2-11-10、star 3-13-19），
by_origin = phosphor 25 / quickdraw 80 / synthetic 74，拒绝计数 75 968 条（逐原因完整）。
`gallery.jsonl` sha256 `3a7646646cd9d496c32a5e5fcec5d10538709972ddb9744222cda17bc3acc138`、
`labels.json` sha256 `26e444ce2299c414c6deac7d8a260f4597360bcbe35d65dde2e66d2dbcb3b213`、
`control_gallery.png` sha256 `8c07d12a8c24632be20bffecb4fd64fd16a1ecac0c9c5011b7381b633e19c983`
（`gallery.jsonl` 与 `cache/` 不入库，可由上述命令重建）。
**本轮一个检索方法都没跑** → 无命中率、无排名、无"更符合肉眼"的主张；
数据源许可与哈希、角色审计（3 条降级）、包络匹配残差（6/10 达标）、
拒绝普查与"本轮不允许的主张"见 [`notes.md`](p0_perceptual_probe/notes.md)，
ShapeComp 官方可复现性逐问取证见 [`shapecomp_research.md`](p0_perceptual_probe/shapecomp_research.md)。
Git 检查点：`10c9e8fa`（已推送，`4c4bf66..10c9e8f main -> main`，
`git ls-remote origin` = `git rev-parse HEAD` = `10c9e8fab8ca39401b6f2e1336780bbb90f54718`）；
**提交后**在工作区干净状态下离线重建，三个入库产物 sha256 不变
（`dataset_manifest.json` 的 `git_commit` 因此变为该检查点 sha，见 `docs/TASKS.md` 检查点表）。

> 更正记录：本轮台账初稿写"`svg_path.py` 的闭合语义修复对图库 **0 处差异**"，
> 该结论来自一份来源无法确认的旧快照。改用逐条回退解码器 + 离线重建比对后实测：
> 修复前/后的标准化轮廓有 **14/25 个 Phosphor 成员** 发生变化（最大逐点位移 0.0879，
> `ph-spiral-fill`；用本项目对齐复测最大 0.0525），角色与成员清单不变，
> 仅 `ph-heart-fill ↔ ph-heart-half-fill` 的 `align_distance` 从 0.002278 变为 0.001748
> （仍 < 0.02，T2 剔除集合不变）。台账与 D47 已按实测数字更正；
> 冻结产物绑定 **修复后** 的解码器。


## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint B：E1/E2/E3 三臂 + 四臂评分契约）

```bash
# 重跑 POLICY 里每个数字（不是检索评价：没有 T1/T2/T3 指标，没有 Top-K 图，没有命中率）
python experiments/p0_perceptual_probe/verify_policy_claims.py            # 约 4 分钟，逐条 [ok]，超容差即非零退出
python experiments/p0_perceptual_probe/verify_policy_claims.py --pairs 8   # 抽样冒烟版：实测 4.3 s 就退出，但**必然非零**——容差检查的对象是默认 40 成员（780 对）那组被 POLICY 引用的数，抽样值本来就不同（本次 median 0.0041）
python scripts/prepare_perceptual_dataset.py --offline --force             # 重建脚本要读的 gallery.jsonl（1.2 MB，不入库）
pytest -q -o addopts=""      # 605 passed
ruff check .                 # All checks passed
mypy                         # Success: no issues found in 31 source files
```

- 随机性：只有 E2 噪声探针用 `np.random.default_rng(0)`（种子写在脚本源码里，不依赖环境变量），
  其余测量全确定。**两次完整运行**的输出 `diff` **只差 1 行**（E3 的 sample 墙钟 7.18 / 6.95 s，
  ±3%），所有实测数字逐字符相同；另一次运行在漂移变体处提前失败（见下方方法论记录），
  其失败前打印的 sample 时间是 7.08 s。该比对不需要额外凭证：把上面的命令跑两遍再 `diff` 即可。
- 内容：5 个新模块 = 四臂契约 `perceptual_methods.py`(137) + E1 `perceptual_features.py`(1520)
  + E2 `salient_structure.py`(831) + E3 `shape_context_baseline.py`(717) + E0 适配器
  `perceptual_e0.py`(88)；新单测 `test_salient_structure.py` 28 + `test_shape_context_baseline.py` 27
  + `test_perceptual_features.py` 24 + `test_perceptual_contract.py` 27 = **106 项**（另 `5a9f1bc`
  给 `test_perceptual_gallery.py` 加 1 项磨损星）；P0-4 产品链路（`alignment` / `search` / `coarse`
  / `chamfer` / `hu_baseline`）**0 改动**，由契约测试钉住默认值。
- 数据回归：`_bevel` 修复（`5a9f1bc`）后离线重建（`1b8461c`）→ `gallery.jsonl`
  `3a764664…` → **`15be97ae…`**、`control_gallery.png` `8c07d12a…` → **`585ffb28…`**
  （1 184 570 → 1 184 600 B）、**`labels.json` `26e444ce…` 不变** → Checkpoint A 冻结的角色 /
  划分 / 近重复凭证仍然有效，C 继续用它；E3 的 780 对不对称普查在重建前后**逐位相同**。
- Git 检查点：`5a9f1bc`（`fix:`）+ `1b8461c`（`data:`）+ `1696873`（`feat:`，9 文件全新增）
  一并推送 `6535e98..1696873 main -> main`，`git ls-remote origin` =
  `1696873f4dbc5e5b7a7aedfc7bfa2818e891326f` = `git rev-parse HEAD`；
  紧随的 `exp:`（重跑脚本 + POLICY `measurements` 键）与 `docs:`（本节）为同一小版本的收尾提交：
  `3ababaf`（`exp:`，5 文件 +362 行，含新增 `verify_policy_claims.py`）+ `78915ba`（`docs:`，
  4 文件 +203/−17），一并推送 `1696873..78915ba main -> main`，推送后核验
  `git ls-remote origin` = `78915bafe632a3056e6186c021a4dc858a7babd2` = `git rev-parse HEAD`、
  `git status --short` 无输出；提交前 gate 为 `pytest -o addopts="" -q` **605 passed** ·
  `ruff check .` All checks passed · `mypy` no issues in 31 source files ·
  `mypy --strict` 对新脚本 clean · `verify_policy_claims.py` 退出码 0（"every quoted number
  reproduced"）。

**本轮不允许的主张**（写了就是伪造检索证据）：

1. 任何**命中率 / mAP / Top-K 排名 / "哪个方法更符合肉眼"** —— B 一个检索评价都没跑，
   只有对合成分布与图库成员的自测量探针。
2. 不得把 POLICY 里的自测量数字称作"人眼匹配准确率"（无人工评分，评分属 D）。
3. 不得把 E1 的 ShapeComp-inspired 近邻当人类相似度真值（D46：官方实现与权重本机不可达）。
4. 不得跨库比较 E1 的距离数值（归一化常数自拟合于当前库：同一查询在 3 行库排第 2、5 行库排第 1）。
5. 不得宣称 E1 全域旋转不变（λ2/λ1 ≥ 0.98 的形状走各向同性回退，42 列切向角块丢不变性；
   star5 0.99869 就是这种形状，实测旋转残差 4.8047，而 E0/E2 为 0.0000）。
6. 不得混用 E3 的 `d(a,b)` 与 `d(b,a)` 排序（实测 780 对中 735 对不对称，median 0.0061）。
7. 不得据 B 宣称 P0 门禁通过（门禁要 ≥10 真人查询，属 P0-6）。

**方法论记录（不删，因为它解释了脚本为什么存在）**：把开发期 `.scratch/` 探针转成入库脚本时，
第一版把 E2 被否决的漂移公式误写成 `min(abs(left), right)`（少一个 `abs`），实测得到
**−17.0258** 的负代价，而不是仓库公式的 3.9716。负数当场被容差检查拦下、**没有**进入任何
POLICY 或文档；但它说明" prose 里的数字来自一次性脚本"是不可靠的，所以每个数字现在都有一个
在仓库里、可重跑、超容差即失败的来源。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint C：四臂同集评价 + 对比图 + 结果文件）

```bash
# 前置：gallery.jsonl 不入库，先重建（构建器产物，含 control_gallery.png）
python scripts/prepare_perceptual_dataset.py --offline --force
# 第一次真正的检索评价：18 查询 × 4 臂 × T1/T2/T3 = 216 次全库评分（无粗筛窗口）
python scripts/evaluate_perceptual_methods.py --top 10              # 墙钟 1151.1 s
# 24 张图（7 族 × 3 设置 comparison + 3 张 overview）+ figures_manifest.json
python scripts/visualize_perceptual.py --settings all --top 5
# 文档里所有表格与产物哈希的唯一来源（不要手抄）
python experiments/p0_perceptual_probe/report_checkpoint_c.py
python experiments/p0_perceptual_probe/report_checkpoint_c.py --files
pytest -o addopts="" -q          # 621 passed
ruff check .                     # All checks passed
mypy                             # Success: no issues found in 31 source files
```

- 随机性：**没有随机数**（四臂在同一 128 点弧长重采样环上确定性地打分；平票按库行序 = D42）。
  验证方式是缺陷修复前后各跑一遍完整评价，再把 `results.csv` 取列比对：
  **3 601 行逐字节相同**。参与比对的列是 `setting, arm, query_id, rank, candidate_id, distance,
  relevant, in_top_k` 加上六列臂内附加列（`theta_deg/shift/mirrored/query_events/
  candidate_events/pairs`）；刻意 **不** 比对的两列是 `arm_seconds_for_query` / `wall_seconds`
  （墙钟，1247.8 → 1151.1 s）与 `align_indistinguishable_from_query` —— 后者正是 `05c50b6`
  唯一要改的东西，所以它是 **预期中** 的差异，不是不确定性。
  21 张 `comparison_*.png` 的 sha256 亦相同（它们只依赖评分列与 `metrics.json` 的标签计数）；
  3 张 `perception_comparison_overview_*.png` **字节不同**，因为它们把 `sec/query` 印在表里，
  表里的命中率 / ap / 排名数字逐位未变。
- 口径（C 之前必须先固定，否则三个设置的数字互相不可比，见 D49）：E1 的 minmax 归一化在
  **冻结的 179 成员语料** 上拟合一次并传给全部设置（`metrics.json → e1_normalizer`，
  91 描述子 / `orientation=canonical` / 7 个退化描述子；代价：真实索引 76 349 个描述子单元里
  0.5% 越界，最差 `curv00_w32_concave_share`）；E3 固定 `d(query, candidate)`
  （`e3_direction_probe`：6 对反向 median 0.0071 / max 0.0113）；E3 正式评价用 `sample` 档
  （论文定义那一档），`sample/canonical` 耗时比 18.7–19.4× 随产物发布而不是隐藏。
- 产物（`report_checkpoint_c.py --files` 打印字节与 sha256）：`results.csv`
  （3 601 行 / 20 列：Top-10 + 全部带标签行，`relevant` 只答冻结标签，
  `align_indistinguishable_from_query` 标出"眼睛分不出、标签说不同"的候选）、
  `metrics.json`（`frozen_inputs` / `protocol` / 三条固定口径 / 每臂 `aggregate`+`top_slots`+
  `label_pressure`+`per_query`+`timing`+`exact_target_self_rank`+`top1` / `claim_limits`）、
  `method_status.json`（四臂模块 + 参数 + 契约 + POLICY）、`figures_manifest.json`
  （inputs/outputs 的 sha256 + `reproduce.git_commit` + 三条命令）。
  每个文件"怎么读 / 不能读成什么"逐条写在 notes.md §13.5；24 张图自身的行/列/颜色/叠加环语义
  与人工检查顺序写在 notes.md §13.5.1（十六进制值以
  `grep -nE '"#[0-9a-f]{6}"' scripts/visualize_perceptual.py scripts/visualize_retrieval.py`
  为准，图只读 `results.csv`，可视化脚本不重新打分）。
- 数据边界：本轮 **只读** `data/index`（839 候选，commit `77b083c9`，三个文件哈希
  `fbaf4933…` / `e18c3f95…` / `b7255ede…` 未变）与 `gallery.jsonl`（`15be97ae…`）/
  `labels.json`（`26e444ce…`）；T3 的混库只发生在评价脚本内，**未合并进正式索引**。
  `control_gallery.png` 是构建器产物，C 不再重画（曾静默改写：`585ffb28…` → `e294997d…`，
  根因是发布版 `gallery.jsonl` 的轮廓只存 6 位小数；已恢复并改成显式开关）。
- Git 检查点：`48ec6fc`（`exp:`，runner + 可视化 + 表格打印脚本 + 测试）与
  `05c50b6`（`fix:`，E0 的 lookalike 列）→ 之后 **整份重跑**（缺陷影响 CSV 的解释列，
  评分不受影响，但不留两份互相矛盾的产物）→ `1a73d6e`（`data:` 28 个产物文件），推送 `78915ba..1a73d6e main -> main`，推送后核验
  `git ls-remote origin` = `1a73d6e678ce5eea6d08d2fd274119d87d4ebeea` = `git rev-parse HEAD`；本轮文档提交无法写入自身 sha，故不再另立检查点。

**本轮不允许的主张**（写了就是伪造证据）：

1. 这些命中率 / MRR / mAP / NDCG **不是**"人眼匹配准确率"：全程无人工评分，
   相关集合是 `labels.json` 的 **设计意图**（同族 variant），评分属 Checkpoint D。
2. 不得称 E1 为原始 ShapeComp（D46：官方 MATLAB / 权重 / 商业剪影本机不可达）。
3. 不得跨语料比较 E1 的距离数值；E0 的 `d=0.02` 与 E1 的 `d=0.02` 之间没有关系（不同量纲的代价函数）。
4. T3 里真实地貌候选 **无相关性标签**：地貌进 Top-k 不是假阳性，只能说有标签的变体被挤出前列。
5. `sec/query` 与 `wall_seconds` 是本机墙钟，换机不可复现；可复现的是分数与排名。
6. E2 的 `exact_target_self_rank` 有 1 个查询排在第 4，**未解释** → 不得写"四臂通过自测"。
7. 不得据 C 宣称 P0 门禁通过（要 ≥10 真人查询，属 P0-6）；未做 P0-5 分级曲线；未做 Web 产品。

**方法论记录**：评价脚本本身也会出错，而错误未必落在数字上。本轮三个缺陷里有两个
（`relevant` 的位置/行索引口径、E0 lookalike 列全空）评分完全正确、只有"解释列"错，
肉眼很难发现——发现的唯一办法是让 **另一条独立路径**（E0 的距离阈值集合）去核对它，
并把核对写成断言（`test_the_lookalike_column_is_filled_for_e0_whatever_arms_are_asked_for`
在修复前的提交上确实失败）。因此 C 的表格全部由 `report_checkpoint_c.py` 从产物打印，
文档不手抄（D48/D49）。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint D · D1：公平展示 + 可盲评材料）

```bash
# 前置：C 的产物必须已在（D1 只读 results.csv / labels.json / gallery.jsonl / data/index）
python scripts/blind_perceptual_review.py --figures fair,blind,blind_unaligned   # 本机墙钟 58 s
pytest -o addopts="" -q      # 643 passed（+22：display_alignment 11 + blind_review 11）
ruff check .                 # All checks passed
mypy                         # Success: no issues found in 32 source files
```

- 随机性：**只有一个种子** `--seed 20261107`，且只用于**盲评字母的置换**
  （`default_rng(seed)` 后按 (setting, query, rank) 排序逐组 `permutation`）。形状的几何、
  排名、距离一律不经过随机数；展示拟合是确定性的粗扫 3° → 精扫 0.25°。
- 确定性核验：默认参数连跑 **两次到两个全新目录** → 37 张 PNG 的 md5 全同、
  `visual_review_template.csv` 字节相同、`blind_mapping.json` 除 `generated_at_utc` 外相同；
  第三次写回 `checkpoint_d/` 与全新目录产物 md5 仍全同。
  **为什么需要这一条**：首稿产物是用一组更低的 DPI **命令行手旗**画出来的（11 650 934 B），
  而脚本默认已是另一组值（13 082 441 B）——"复现命令"与入库字节不符就等于不可复现，
  所以整目录按默认参数作废重生成，而不是把当时的手旗抄进文档（手旗已不可考，抄即伪造）。
- 本轮**没有运行任何检索评价**：`results.csv` 无新行、`metrics.json` 未变；D1 读排名、画图像，
  不产生也不修改任何分数。
- Git 检查点：`16c7d59`（`exp:` 代码 + 22 项测试）→ `3e8229c`（`data:` 40 个材料文件）→ 本行所在
  的 `docs:` 提交（`checkpoint_d/notes.md` + TASKS + 本日志）。C 的 `results.csv` / `metrics.json` /
  `figures_manifest.json` / 24 张图与 `data/index` 在本轮**只读**，未被覆盖。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint D · D2：E0–E3 逐臂归因 + 模型辅助视觉复核）

```bash
# 前置：C 的 results.csv / metrics.json / labels.json / gallery.jsonl 与 D1 的盲评材料必须已在（本轮只读）
python scripts/diagnose_perceptual_arms.py            # 入库那次 564 s（e1 267.6 s + rotation 284.4 s 占大头）
python scripts/visual_review_report.py                # 本机 5.9 s：读盲评页分数表 → scores.csv / summary.json / examples.png（连跑两次三个产物 md5 全同）
pytest -o addopts="" -q      # 701 passed（+58：test_arm_diagnostics 33 + test_visual_review_report 25）
ruff check .                 # All checks passed
mypy                         # Success: no issues found in 32 source files
```

- **本轮没有重新评分、没有重新排名**：`arm_diagnostics.json` 里所有距离与名次都从 C 的
  `results.csv` 读出来（`frozen_rankings_read_not_recomputed = true`），只有两处例外且都写进产物：
  E1 的特征分组消融（查询与图库冻结、不调权重）和 D3 之前的耗时分解。
- **视觉分是 `AI_visual_review`，不是真人实验**：500 格（T2 全部 18 查询 × Top-5 × 4 字母 = 360，
  加 T3 每族 1 个代表查询 = 140）由模型**读盲评 PNG 像素**打出，评分时 `blind_mapping.json` 未打开，
  打完才写 `visual_review_summary.json`，之后**没有重打任何一格**（`read_order` 字段记录这个顺序）。
  `unaligned` 视图与 T3 其余查询**未评**，模板里仍是 `pending_review`。
  口径列（`key_structure_preserved` / `obvious_error` / `low_confidence`）是**规则不是观察**，
  规则写在 `derived_columns_are_rules_not_observations` 里，其中低置信列由
  `low_confidence_audit` 证明"声明的 5 个难组全部已被机械规则覆盖"，不是手设。
- **本轮最重要的新事实是材料缺陷，不是任何臂的胜负**：图库 179 成员里 11 组 / 31 个成员的点列
  **逐位相同**（sha256 与 `origin_ref` 两条独立读法给出同一分组，`geometric_and_provenance_reads_agree
  = true`）；40 个同形对只有 6 对在 `labels.json` 声明过。两个查询（`qd-circle-query-01`、
  `qd-cloud-query-01`）的 T2 库里还留着与自己逐位相同的行——所以"T2 已剔除目标"只对 id 成立。
  它把 E0 的 11 行"不可区分硬负例"减弱为 **3 个真正不同的形状比较**，也把 E2 的 13 行零距离 Top
  命中拆成 **3 行同形 + 10 行确实不同**（E2 的结论不受影响）。
- **本轮撤回两条上一版结论**：① "E0 的距离标尺是反的"—— `count_indistinguishable_relevant = 0`
  被 `labels.json.rule` 的定义保证，按构造必然成立，只是标注/打分一致性检查；
  ② "E0 把 6 个族的内部结构看成一样"——那 6 行是同一条 `circle({"radius": 1.0})` 轮廓被登记 6 次。
  另撤回 ③ "DP 系统性偏好删除"：7 个可比查询里删角只赢 2 次。
- **可复现的是分数、名次与角度极差，不是秒**：E3 `sample` 模式同一项三次运行 7.42 / 8.28 / 7.96 s，
  入库这一次是 7.963 s / canonical 0.414 s / 旋转网格占 94.67 % / 比 19.24×。文档只抄入库产物里的数字。
- **T1 那个 rank 4 已定位，不是实现错误**：`syn-blob-query` 自距离恰好 0.0，T1 库恰好 5 行同为 0.0
  且显著事件数全为 0，名次由 `np.argsort(..., kind="stable")` 的行序决定（规则 D42）。
  在定位之前不写"代码 bug"，定位之后也不写"已修复"——那是图库与标注的性质，不是缺陷。
- Git 检查点：`c3ca33ee`（`exp:` 两个 runner + 58 项测试）→ `33e374c3`（`data:` 5 个产物，
  共 1 260 993 B，最大 726 515 B，无一超过 1 MiB）→ 本行所在的 `docs:` 提交
  （`checkpoint_d/notes.md` + TASKS + 本日志）。
  C 的 `results.csv` / `metrics.json` / `figures_manifest.json` / 24 张图与 D1 的 40 个材料文件
  在本轮**只读**，未被覆盖；`data/index` 只被 E1 漂移检查读取。
- **未完成项（交给 D3/D4，不在本轮假装做完）**：E0 粗筛 + E3 精排的两段式混合检索器**一行未跑**，
  所以"窗口 20/50/100/200 的 Top-10 覆盖率"和"E0+E3 是否够快"目前**没有任何实测数字**；
  真人盲评仍未做（P0-6 需要 ≥10 真人查询）；`unaligned` 视图的评分；E1 归一化在真实地貌上的
  重拟合（本轮只测量越界比例，没有重训）。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint D · D3：E0 粗筛 + E3 精排两级实验）

任务书 §6：E3 单查询全库 7.96 s（T2）/ 41.7 s（T3），E0 只要 0.016 s / 0.089 s。
D3 要回答的是"**让 E0 只负责筛人、E3 只负责排序**，能省多少时间、丢多少答案"，
并且要**把预计算成本算进去**——不许悄悄省掉重建再宣称更快。

```bash
python scripts/hybrid_e0_e3_retrieval.py            # 本机墙钟 2297.6 s（T2 到 521.3 s，T3 到 2297.6 s）
python scripts/hybrid_e0_e3_retrieval.py --redraw-from-json \
    experiments/p0_perceptual_probe/checkpoint_d/hybrid_retrieval_metrics.json   # 只重画窗口对比图，不重算分
pytest -o addopts="" -q                              # 726 passed（+25：test_hybrid_retrieval.py）
ruff check .                                        # All checks passed
mypy                                                # no issues in 32 source files
```

产物：`checkpoint_d/hybrid_retrieval_metrics.json`、`hybrid_retrieval_results.csv`（2160 行 =
2 设置 × 18 查询 × 6 臂 × Top-10）、`hybrid_window_comparison.png`（4 面板）。
**C 的 `metrics.json` / `results.csv` / 24 张图与 D1/D2 的产物本轮只读，未覆盖。**

- **口径先固定**（`payload.contract`，不是注释）：① E0 不贡献分数——窗口内只按 E3 距离排序，
  平手按库行号（D42 那条），把 E0 距离全部打乱 Top-10 一个字节都不变（测试钉住）；
  ② E3 参数逐臂相同（`orientation="sample"`、24 步、5×15 格，原文进 contract）；
  ③ 每查询计时 = 缓存重建 + 精排，**缓存构建时间不从主张里减掉**；
  ④ 生产默认未动：`search_by_contour` 仍是 `method="fourier_align"` / `shortlist_size=200`，
  入口文件里没有 hybrid 与缓存 API 的引用（测试钉住）。
- **T2（库 177/178 行）**：Full-E3 NDCG@10 0.7329 / 7.2538 s；@20 0.6897 / 0.8817 s（**8.2×**）、
  @50 0.7147 / 2.0823 s、@100 0.7263 / 4.1063 s、@200 0.7329 / 7.2468 s。
  hit@1 六臂全同（0.667），所以 T2 上"筛人"没有改变谁排第一。@200 与 Full-E3 到小数点后 4 位相同
  是**自检行**（库只有 177 行，200 窗口 = 全库），不是成果。
- **T3（库 1016/1017 行，含 839 真实地貌）**：Full-E3 0.6358 / 41.7052 s；@20 0.5679 / 1.2064 s
  （**34.6×**）、@50 0.5750 / 2.4046 s、@100 0.6041 / 4.4182 s（9.4×）、@200 0.6066 / 8.4394 s。
  **没有任何窗口追平 Full-E3 的 NDCG@10**（最好的 @200 仍差 0.029）；真实地貌把粗筛的代价放大了。
- **两条不能写成"混合更好"的数**：① T3@100 hit@1 0.722 > Full-E3 0.667 —— 只有
  `ph-tree-structure-fill` 一个查询从 2 变 1，而两个名次**都是相关项**：Full-E3 的第 1 名
  `syn-forked-sharp` 在 E0 里排 377，任何 ≤200 的窗口都看不见它，于是 Top-1 换成了同样相关的
  `qd-tree-02`；18 查询里 1 个翻转不是结论。② `first_relevant_rank` 均值 T3@100 = 20.17，
  18 查询合计 363 里 **333 来自两个查询**（254 + 79），中位数仍是 1 —— 均值被长尾拖住，不许拿它主张整体质量。
- **被粗筛排除的候选台账**（`excluded_ledger`，一行当且仅当 `e0_rank > window`，所以同一候选会在
  更小窗口各记一次；列和 = 台账行数）：T2 54 行（33/14/7/0）、T3 96 行（40/26/19/11）。
  **去重后**被排除的相关项 T2 15 对（forked 11 / band 3 / star 1）、T3 12 对（forked 8 / star 2 /
  heart 1 / blob 1）——枝状占 73 % / 67 %，与 D2.6 的 E0 失效模式（"不改变外包络的内部改造"）同源，
  新信息是：**E0 不只是排不对，它会把 E3 的正确答案筛到看不见的位置**。
  连 W=200 都救不回来的只有 T3 的 2 对（E3 第 2 名 ↔ E0 第 377；E3 第 7 名 ↔ E0 第 629）。
- **台账的覆盖范围必须一起读**：它只跟 Full-E3 的 Top-10。`ph-lightning-fill` 在 T3 上第一个相关项
  `qd-lightning-05` 是 E3 的**第 35 名**（本来就在 Top-10 外）、E0 的第 **254** 名，
  所以四个窗口的 first_relevant_rank 全是 254 而 Full-E3 是 35 —— 这条是单独复查出来的，
  台账里**没有**。任何"排除项总数"的说法都低估了损失。
- **描述子缓存：正确但没用**（新 API `build_library_descriptors` / `score_library_from_descriptors`，
  生产路径未改）。逐位等价核验 `cache_equivalence`：T2 177 行、T3 1016 行
  `max_abs_difference = 0.0`、`identical = true`、`rankings_identical = true`。
  但实测只省 **0.94 %**（T2：0.0689 s / 7.3435 s）与 **1.12 %**（T3：0.0893 s / 7.9514 s），
  因为 `score_library` 本来就把库侧描述子每次调用只建一次；内存 6 796 800 B（177 行）/
  39 014 400 B（1016 行）= 每行 38 400 B（64 点 × 75 格 × 8 B）。
  **两级结构的加速全部来自"少打分多少候选"**（每候选 ~0.040 s，与 D2.9 的 0.045 s 同量级），
  与缓存无关，写报告时不得用缓存主张提速。
- **本轮两次自我纠正（过程记录）**：① `shape_context_baseline.py` 的越界检查在实验**运行期间**被
  mypy 逼着改了一次（`selection.min()` → Python `min(rows)`）；用改后代码重跑 `ph-heart-fill` 的
  T2 @20/@50 与入库 CSV 逐行比，**40 个距离最大绝对差 0.000e+00**、id 与两个 rank 列全同，
  所以入库数字与提交代码一致——这条是测出来的，不是假设。② 窗口图第 3 面板的对数轴把小刻度印成
  "3 × 10¹" 压在 50 上，只改绘图并加 `--redraw-from-json`；数据文件 mtime 未变
  （JSON `a1eeb812…` / CSV `8a86510b…` 仍是 16:46，PNG 16:53）。
- Git 检查点：`exp:`（hybrid runner + 缓存 API + 25 项测试）→ `data:`（3 个产物）→
  本行所在的 `docs:` 提交（`checkpoint_d` D3 段 + `diagnose_perceptual_arms.py` 的越界检查
  改动 + TASKS + 本日志），随后推送 `origin main`。
- **未完成项（交给 D4，不在本轮假装做完）**：真人盲评仍没有（`AI_visual_review` 不是真人）；
  混合结构**没有**在只含真实地貌的库上评价过（T3 库始终是 1016/1017 行混合，839 行地貌只是其中一部分）；
  18 个查询里 **5 个是 QuickDraw 真人手绘**，两级每一档都跑了它们——
  上一版此处写"没有在真人手绘查询上评价过"是**数量口径错误**，已在 D4 订正（见 D4 段第 ④ 条）。
  E0 粗筛在枝状族上的失效**没有**修复（D3 只是量出了它的代价）；
  窗口 20/50/100/200 之外没有扫描更小的窗口（10 以下），因为 18 查询的样本量不足以分辨；
  `unaligned` 视图的视觉评分仍空。

## p0_perceptual_probe 复现命令（P0-4.5 · Checkpoint D · D4：五个问题的汇总与文档）

任务书 §7 要五个**有证据**的结论。D4 的边界是：**不重打分、不重算距离、不重画任何图**——
它只读 C 与 D1–D3 已经入库的产物，把它们拼成"哪个算法更像 / E3 的优势是否普遍 /
E2 为什么失败 / 两级值不值得 / 下一轮投什么"。唯一的新计算是**对已入库分数 CSV 按查询来源
重新切片**（D4.2b），因为发现上一版把"18 个查询里有 5 个真人手绘"错写成了"真人查询为 0"，
而一数清楚，分组排序就变了。D4 里出现的每个数都必须能在下面的命令里打印出来；
打印不出来就等于没写过。

```bash
# 只读核对：不重算、不重画、不改任何产物（改 mtime 的脚本本轮一条都没跑）
python - <<'PY'
import json
D = "experiments/p0_perceptual_probe/"
c = json.load(open(D + "metrics.json", encoding="utf-8"))
h = json.load(open(D + "checkpoint_d/hybrid_retrieval_metrics.json", encoding="utf-8"))
v = json.load(open(D + "checkpoint_d/visual_review_summary.json", encoding="utf-8"))
print("--- C vs D3 (must agree) ---")                  # 两条独立路径给同一答案
for st in ("T2", "T3"):
    for arm, src in (("E0_only", "E0"), ("Full-E3", "E3")):
        a = h["settings"][st]["summary"][arm]["labels"]
        b = c["results"][st][src]["aggregate"]
        keys = ("ndcg@10", "mrr", "hit@1")
        ok = all(abs(a[k] - b[k]) < 1e-4 for k in keys)
        print(f" {st} {arm:8s} vs C/{src}: {[round(a[k], 4) for k in keys]} == {[round(b[k], 4) for k in keys]} {ok}")
print("--- windows: d_ndcg, sec/query, speedup, top10 agreement ---")
for st in ("T2", "T3"):
    full = h["settings"][st]["summary"]["Full-E3"]
    for w in (20, 50, 100, 200):
        x = h["settings"][st]["summary"][f"E0->E3@{w}"]
        print(f" {st} @{w:<3} {x['labels']['ndcg@10'] - full['labels']['ndcg@10']:+.4f} "
              f"{x['seconds']['mean_query']:7.4f}s {full['seconds']['mean_query'] / x['seconds']['mean_query']:5.1f}x "
              f"{x['agreement_with_full_e3']['top10']:.3f}")
print("--- visual (combined 25) + C per-query timing ladder ---")
for arm in ("E0", "E1", "E2", "E3"):
    p = v["combined"]["per_arm"][arm]
    print(f" {arm} top1 {p['mean_top1_visual']:.3f} err {p['obvious_error_share']:.1%} "
          f"T2 sec/query {c['results']['T2'][arm]['timing']['seconds_mean_per_query']:.4f}")
PY

# D4 唯一的新计算：按查询来源切片（读已入库的分数 CSV，一格分数都没改）
python - <<'PY'
import csv, json
from statistics import mean
rows = [r for r in csv.DictReader(open(
    "experiments/p0_perceptual_probe/checkpoint_d/visual_review_scores.csv", encoding="utf-8"))
    if r["setting"] == "T2"]
human = {r["query_id"] for r in rows if r["query_id"].startswith("qd-")}
print("human-drawn queries:", sorted(human))           # 必须是 5 个，不是 0
for tag, sel in (("human  ", [r for r in rows if r["query_id"] in human]),
                 ("non-human", [r for r in rows if r["query_id"] not in human])):
    print(tag, {a: f"{mean([int(r['score_0_3']) for r in sel if r['arm'] == a]):.3f}"
                f"({len([r for r in sel if r['arm'] == a])})"
                for a in ("E0", "E1", "E2", "E3")})
PY

# 第三条独立读法：数 key_id（图库缺陷的性质 + 真人查询到底是不是"不同用户"）
python - <<'PY'
import json, collections, hashlib
rows = [json.loads(l) for l in open("experiments/p0_perceptual_probe/gallery.jsonl", encoding="utf-8")]
qd = [r for r in rows if r["origin"] == "quickdraw"]
kids = collections.Counter(r["provenance"]["key_id"] for r in qd)
print(f"quickdraw items {len(qd)} / distinct key_id {len(kids)} / reused {sum(1 for v in kids.values() if v > 1)}")
q = [r for r in qd if r["role"] == "query"]
print("query key_ids:", len({r['provenance']['key_id'] for r in q}), "of", len(q),
      "| countries:", sorted(r["provenance"]["countrycode"] for r in q))
grp = collections.defaultdict(list)
for r in qd:
    grp[r["provenance"]["key_id"]].append(r)
bad = {k: v for k, v in grp.items() if len(v) > 1}
ident = sum(1 for v in bad.values() if len({json.dumps(r["contour"]) for r in v}) == 1)
print(f"reused-key_id groups {len(bad)}, of which bit-identical contours: {ident}")
allg = collections.defaultdict(list)
for r in rows:
    allg[hashlib.sha256(json.dumps(r["contour"]).encode()).hexdigest()].append(r["item_id"])
big = [v for v in allg.values() if len(v) > 1]
print("identical-contour groups over all 179:", len(big), "members:", sum(len(v) for v in big),
      "| quickdraw members:", sum(1 for v in big for i in v if i.startswith("qd-")))
PY
pytest -o addopts="" -q          # 726 passed（D4 未新增测试：没有新代码，也就没有新断言）
ruff check .                     # All checks passed
mypy                             # Success: no issues found in 32 source files
```

- **核对读的是哪四份**（md5，用于确认文档抄的是入库字节）：C `metrics.json`
  `9731ac73…`、C `results.csv` `b74f9ac3…`、D1/D2 `arm_diagnostics.json` `1754675a…`、
  D2 `visual_review_summary.json` `dfaf28c3…`、D3 `hybrid_retrieval_metrics.json` `a1eeb812…`。
  `visual_review_report.py` **本轮没有运行**——它会重写 `visual_review_summary.json` 与示例图，
  而 D4 只需要读；"能重算"与"已重算"是两件事，混淆它就等于把复现命令写成了幻觉。
- **五个问题的一句话答案**（详细表在 `checkpoint_d/notes.md` D4.2–D4.6）：
  ① 没有全面胜出的臂，而且**"第一是谁"取决于查询来源**——合并 25 组 Top-1 E3 2.640 / E1 2.560 /
  E0 2.520 / E2 2.040，T2 全 18 查询单看是 **E1 2.778** 第一、T3 代表组是 **E3 2.429** 第一，
  **只看 5 个真人手绘查询**则是 **E1 2.360 > E3 2.160 > E2 2.080 > E0 1.840**；
  最大领先 0.120 个名次分（n=25），真人子集差距更大但 n=5；
  ② E3 的优势**不普遍**：逐查询第一相关名次 4 赢 / 1 输 / 13 平，赢在 E0 无解的查询，
  heart / crescent / star 三族**一个赢例都没有**；
  ③ E2 最差是**标定与尺度**问题（丢一个凸角的价格 0.0477 = 阈值的 2.4 倍、凹事件只活在 1/64
  窗口、删角在 7 个可比查询里只赢 2 次），T1 的 rank-4 是 `stable` 排序平手，**不是 bug**；
  ④ 两级**值得做实验但不值得当默认**：可用折中是 T2@50（2.082 s，−0.018）与 T3@100
  （4.418 s，−0.032），T3 上**没有任何窗口追平 Full-E3**，而 E0 会把 E3 的正确答案筛到
  第 377 / 第 629 名——W=200 也救不回来；
  ⑤ 下一轮顺序 = **修图库同形缺陷 → 把真人手绘查询从 5 个补到 ≥10 个不同用户 + ≥3 人盲评
  → 攻枝状覆盖与保真**，之后才轮到粗筛替换与 E1 重拟合；**不建议现在做**：修 E2、扩真实地貌库。
- **为什么"扩库"排在最后**（这条容易被读反，写成数字）：C 的 T2→T3 对照里四个臂的 NDCG@10
  **全部下降**——E0 0.6653→0.5466、E1 0.7210→0.5520、E2 0.6619→0.5539、E3 0.7329→0.6358；
  视觉复核同样 T3 < T2（E3 2.429 vs 2.722）。839 条地貌已经把每个臂压低 0.10–0.17，
  在真人手绘查询仍只有 5 个之前继续扩库只增加**不可解释**的行。
- **D4 唯一的独立新事实是那条交叉核对**：D3 重算的 `E0_only` / `Full-E3` 与 C 冻结的
  `E0` / `E3` 四项聚合到小数点后 4 位相同（核对命令第一块打印 8 行 `True`）。
  它不证明算法好坏，只证明**排名与距离没有在这轮工程里漂移**——把"新实验"与"重抄旧表"
  区分开的正是这一条，而不是脚本文字。
- **三条换算禁令（D4 全程遵守）**：几何距离**不**换算成视觉分，`labels.json` 的相关性**不**
  换算成视觉分，视觉分**不**换算成名次。实测过它们确实不等价：四臂 Pearson(距离, Top-1 视觉分)
  = E0 −0.747 / E1 −0.625 / E2 −0.303 / E3 −0.560（n=18，D2.5）——方向对、强度不足以互替。
- **本轮撤回/降级的表述**：① 上一版"哪个算法更符合肉眼"的**任何**排序说法都降级为
  "`AI_visual_review` 在受控图案上的排序"，**人工判读人数 = 0**；② E1/E3 的领先不足以支撑换算法，
  只支撑"继续投入"；③ "E0+E3 够快"必须带窗口与库规模一起说（T2 的 8.2× 不可外推到生产，
  因为 T2 库只有 177 行，@200 就是全库）；
  ④ **数量口径订正（本轮新发现，D1–D3 的文档都中了这一条）**：D1–D4 上一版把"真人手绘查询"
  写成 **0 个**，实际是 **5 个**——`gallery.jsonl` 里 `role = "query"` 且 `origin = "quickdraw"`
  的有 `qd-circle-query-01`（blob）、`qd-cloud-query-01`（irregular）、`qd-lightning-query-01`（band）、
  `qd-star-query-01`（star）、`qd-tree-query-01`（forked）；其余 13 个是 6 条 Phosphor 图标 +
  7 条合成原型。**分数与名次一个都没变**，但这条错话遮蔽了两件要事：按来源切片后第一名从 E3
  变成 E1，且 E0 在真人子集上掉到 1.840（非真人子集 2.446，落差 0.606 是四臂最大）。
  错的成因值得记下来："真人查询"在本项目里长期特指 **P0-6 待采集的那个固定集**，
  于是"尚未按门禁口径采集"被顺手写成了"数量为零"——词汇绑定掩盖了清点，这是 §4 新增
  "混合样本必须按来源切片报告"那条规范的直接来由。
  ⑤ 顺着这条订正再查 `key_id`，**又错了一层**：起初想写"5 个不同 `key_id` = 5 个不同的人"，
  查官方 README 后不成立——`key_id` 定义是 "A unique identifier across all **drawings**"，
  元数据只到 `countrycode`（这 5 条是 BE / FI / GE / US / US），**没有玩家身份**。
  所以门禁"≥10 个**不同用户**"这一维用公共数据集**不可核验**，只能本项目自己采集。
  同一趟复查给图库缺陷加了第三条独立证据：**80 条 QuickDraw 成员只有 66 个不同 `key_id`**
  （10 个 key_id 复用成 14 条额外行，且这 10 组点列**逐位相同**），
  31 个同形成员里 24 个是"同一幅真人画被登记到不同 role/family 槽位" →
  缺陷是**插入未去重**，不是几何生成逻辑错，因此修它最便宜。
  同时把 D2 的"两个查询受影响"**限定住**：只有 `qd-circle-query-01`（T2 库 2 行同形）与
  `qd-cloud-query-01`（1 行）漏了，另 3 个真人查询的同幅画已被
  `excluded_as_query_or_near_duplicate` 显式剔除——**不能**扩大成"5 个真人查询全被污染"。
- Git 检查点：本行所在的 `docs:` 提交（`checkpoint_d/notes.md` 的 D4 段 + `docs/TASKS.md` +
  `docs/EXPERIMENTS.md` §4/§6 + `docs/DECISIONS.md` D50–D53 + 本日志），随后推送 `origin main`。
  **代码、产物与测试在 D1–D3 已各自提交并推送，D4 不新增也不改动它们**；
  C 的 `results.csv` / `metrics.json` / 24 张图与 D1–D3 的产物在本轮**只读**，md5 未变。
- **未完成项（D4 收尾时仍然未完成，如实列出）**：真人手绘查询 **5 个**（门禁要 ≥10 个不同用户）、
  ≥3 人盲评 **0 份**
  （材料 `blind_review_t2.png` / `blind_review_t3.png` / `blind_mapping.json` /
  `visual_review_template.csv` 已交付可填）；`unaligned` 视图未评分；图库 11 组 / 31 成员逐位
  同形的缺陷**未修**；E0 粗筛的枝状盲区**未修**；E2 未重标定；E1 归一化未在真实地貌上重拟合；
  两级结构未在浏览器 Worker / 1–10 万候选上测过延迟。**P0 门禁仍未通过 → 不进入 P1 Web 开发。**
  正式检索默认算法**未改**（仍是 `method="fourier_align"`、`shortlist_size=200`）；
  是否改用 E3 精排或两级结构，留给用户审查 D4 的表之后决定。

## shape_perception_benchmark 复现命令（P0-4.6 · 独立形状感知基准，Checkpoint 1–4）

目录 [`experiments/shape_perception_benchmark/`](shape_perception_benchmark/README.md)；
逐条证据与十问回答在 [`notes.md`](shape_perception_benchmark/notes.md)。
**完全排除 Overture / 水体 / 经纬度；不改正式检索默认；不覆盖 P0-4 / P0-4.5 产物。**

```bash
# 1 来源：HTTP Range 只读 ZIP 中央目录 + 所需成员（12.3 MB / 存档 308 MB），MPEG-7 3.4 MB
.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py
# 2 人类评分预测（H1 250 对 × 14 被试；S0 仅用作者发布的 22 维坐标，自动追加）
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d1 --methods E0 E1 E3
# 3 检索三阶段（~25 min；E3 约 58 s/全库查询）
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3
# 4 汇总表 + 官方网络审计（秒级，模型对模型，无人类评分）
.venv/Scripts/python.exe scripts/run_shape_benchmark.py --stage summary
.venv/Scripts/python.exe scripts/audit_figure8_networks.py
# 5 图（只读已存排名，不重新排名）
.venv/Scripts/python.exe scripts/plot_shape_benchmark.py --figure all
```

- **官方 ShapeComp 本地没跑成**：Dropbox / Drive / Sites 全阻断，无 MATLAB / Octave / TF，
  未装商业软件、未买许可、未写 109 维移植。S0 只能是**发布的预计算坐标**（H1 500 + H2 15），
  `dist == ‖SC1−SC2‖` 残差 4.44e-16；不能给新形状打分，故缺席一切检索阶段。
- **D1（主真值 H1）**：agreement ρ = S0 0.911 / E3 0.839 / E1 0.782 / E0 0.683；
  边际 CI 重叠，改用**配对**两级 bootstrap（对 + 被试），六个差值全部排除 0
  （最弱 E3−E1 = +0.057 [+0.006,+0.107]）。人类自身一致性：被试间中位 0.601、留一中位 0.779。
  H2 为数字化辅助目标，单独报、永不合并。
- **D2**：T2/T3 平均 class-recall@10 = E0 0.717 / E1 0.833 / E3 0.917（类别≠人类相似度）。
  **T3 无区分度**：异类硬干扰物 0/6 进入任何臂 Top-10 → 诚实负结果，干扰物太"粗"不算硬。
  完整排名存 `d2_full_rankings.npz`，排除台账 `excluded_candidates.csv`。
- **公平性缺陷出数前修复**：`Gallery` 扩集时继承 E1 minmax 归一化器（否则"加干扰"与"重缩放"
  同变量）；反事实实测重拟合会动 27/91 尺度、最大偏移 22.1%；2 个回归测试守住。
- **D3**：五族全部结构变体排在粗干扰之上；裕量差异 E1 压干扰最深但丢一个 star 变体出 Top-10，
  E0 保变体却放干扰到 rank 23。**角色是构造假设，非人类真值**，图上明示。
- **D4**：**E0 不镜像不变**（Top-10 overlap 0.00，自匹配 rank 246/163）；**E3 的 15° 旋转网格**
  使 37°/191° overlap 减半；E1 最耐变换但镜像仍丢 2/3。单查询成本 E0 0.1 s / E1 12 s / E3 58 s。
- **Checkpoint 1 错误纠正**：`Figure8NetComparison.mat` **带刺激**（1000 个 384×2 轮廓 + 25 维
  GAN latent）与 MatNet/KerNet1/KerNet2 官方坐标；原"刺激不在成员内"撤回。从原始数组复算
  Figure 8D：r = 0.928/0.945/0.910（论文 0.93/0.94/0.91）；`SC` 是 40 维 MDS（前 22 列仅 88.76%
  方差，**不**等于规范 22 维空间）。该 1000 形状无人类评分、与 H1 不相交、2 个自交 → 仅模型对模型，
  **未跑其检索臂**，列为下一步选项。
- Git 检查点：CP1 `1e4d6ca`、CP2 `46e2fad`、CP3 `5e84530`、CP4 `a70c7bd`（十问回答 + 下一步建议）。
  验证：`pytest -q` 777 passed；`ruff check .` 全过；`mypy` 38 文件无问题。
- **结论与边界**：官方 ShapeComp 现成 footprint 不适合 EarthGlyph（新形状投影不可得）；
  但发布空间在人类评分上显著胜过所有可跑臂，E3→S0 的 +0.072 是近似/混合方案的头部空间。
  建议下一步做 Figure 8 检索臂 + 小回归器逼近发布 22 维坐标两项**有界**实验；
  **不改正式默认、不跨 P0 门禁**，是否整合由用户决定。

## shape_perception_benchmark · Checkpoint 5（P0-4.6 · 官方 KerNet1/KerNet2 翻译为 Python 并实跑，2026-11-08）

**触发与授权**：用户把官方工具箱下载到 `E:\EarthGlyph\shapeComp_17_12_2024`，问能否把 MATLAB 翻译成
Python 以运行和对比。经 AskUserQuestion 用户选定 **(a) h5py + numpy 路线**（读官方 `.h5` 权重、numpy 前向、
**无 TensorFlow**、无重训练）+ **(b) 完整有界范围**"两网络 + 验证 + 检索对比"。**不改正式检索默认。**

- **翻译了什么**：`earthglyph/shape_benchmark/shapecomp_port.py` 只翻译官方 MATLAB 预处理
  （质心 / `resamplePolygon` 弧长重采样 / `adjustContourPos` min-max / **保留顺时针强制** / `inpolygon`→`shapely.covers`
  栅格化 / `flipud`），并用 h5py 读 `net/keras/shape2sc22a.h5`（KerNet1，dense_1..7）与 `im2sc22d.h5`
  （KerNet2，conv2d_4/5/6 + dense_3/4），numpy 逐字前向（Dense `x@W+b`+ReLU；Conv2D 'same' 零填充互相关
  `sliding_window_view`+`einsum`；MaxPool 2×2；KerNet1 flatten order='F' 768 维，KerNet2 40×40×1）。
  **无 TF/Keras 运行时、无 GPU、无重训练。**
- **没翻译什么**：109 维描述子引擎（2024 工具箱不含）、MatNet（MATLAB 权重不可得）、任何自写描述子近似。
  E1 仍标"EarthGlyph ShapeComp-inspired"；端口运行的是官方**权重**（用户明确授权），不是自写描述子冒充复现。
- **忠实度验证**（`scripts/validate_shapecomp_port.py` → `results/shapecomp_port_validation.json`）：
  **KerNet2 对 H2 发布 KerNet2 坐标近乎精确**——逐维 r 0.9982、成对 Spearman **0.9989**、RMSE **0.0065**；
  KerNet1/2 对 Figure 8 发布网络列 Spearman **0.980/0.957**，残差是**输入溯源**（存档轮廓是原始非均匀显示轮廓，
  非网络精确输入；跳过重采样塌到 ~0.56，证明流水线必需且正确；999/1000 已顺时针，故保留的顺时针步近乎 no-op）。
  **H1 不是忠实度测试**（`is_faithfulness_test:false`）：其 `shape1SC/shape2SC` 是**原始 109 描述子模型的 22 维** ShapeComp MDS
  坐标（另一模型/基），逐维 r 无意义（均值≈−0.06），成对 Spearman≈0.927/0.929 衡量神经近似对原始模型的追踪。
- **D1（六臂，主真值 H1，agreement ρ 正=更好，两级配对 bootstrap）**：
  S0 0.9108 / **S0K1 0.9032** / **S0K2 0.9005** / E3 0.8391 / E1 0.7824 / E0 0.6831。
  **网络间差值全含 0**（S0K1−S0 −0.0076 [−0.031,+0.013]、S0K2−S0 −0.0103 [−0.031,+0.009]、
  S0K1−S0K2 +0.0027 [−0.015,+0.019]）→ 实跑网络与发布原始空间**统计不可区分**；
  **每个 EarthGlyph 臂对每个网络臂差值全排除 0**（E3−S0K1 −0.0641 [−0.125,−0.013]、
  E3−S0K2 −0.0614、E1−S0K1 −0.1208、E0−S0K1 −0.2201）→ 端口忠实复现官方网络的人类预测力且显著胜过所有 EarthGlyph 臂。
  H2 辅助：S0K2 0.7622 匹配发布 0.7656（H2 目标由**图像**路线建），S0K1 0.6298 较低（轮廓 MLP 是不同近似）；永不并入 H1。
- **许可边界**：工具箱 `shapeComp_17_12_2024/` 已 gitignore（`.gitignore:72`）；上游仅"please cite"、**无再分发许可**，
  故工具箱与 `.h5` 权重**不入库**，运行时从用户本地副本读取。端口代码/验证脚本/测试/结果 JSON 入库。
  引用 Morgenstern et al. 2021（DOI 10.1371/journal.pcbi.1008981）与 Carbajal Chavez et al.（PsyArXiv）。
  复现需用户本地提供工具箱 + `pip install -e ".[shapecomp]"`（h5py + matplotlib）。
- **D2–D4 实跑网络检索对比**（`run_shape_benchmark.py --stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2`）：
  **E0/E1/E3 行与 CP4 逐位一致**（D2 T2/T3 recall 0.717/0.833/0.917；D4 E0 镜像 0.00、E3 rotate_37 0.54、
  E1 镜像 0.33）→ 确定性核验通过。**D2** 类别 recall@10：E3 0.917 > E1 0.833 > S0K2 0.800 > S0K1 0.767 > E0 0.717；
  网络胜 E0 但不胜 E1/E3——网络优化**人类感知相似度**（D1）而非物体类别。逐查询 `tree-1` S0K2 0.9 vs E0 0.0；
  `bat-1` S0K2 0.2 vs E1/E3 0.9（枝状=D1 失败模式）。T3 仍 0/6 干扰入 Top-10。**D3** 五族全过，E1 压干扰最深、网络居中。
  **D4 关键整合阻塞：官方网络不旋转不变**——37°/90°/191° 后 Top-10 overlap 塌到 **0.00**、自匹配 rank 至 **301**
  （fork-1 90° S0K1）；镜像形状依赖（bat 0.05、heart 0.82）；缩放/平移/起点 S0K1 基本 intact（1.00）。
  E0/E1 旋转不变；故经人类验证的网络**不能现成接入** EarthGlyph 旋转不变流水线，需旋转搜索/不变包装（未验证）。
  **成本**：网络一次性打分图库（~20 s）后每查询≈**0 s**（远低于 E3 55 s/查询），但旋转敏感。
- **验证与确定性**：`pytest -q` **786 passed**（含 9 端口测试，0 跳过）；`ruff check .` 全过；`mypy` 39 文件无问题。
  加入 S0K1/S0K2 重跑后 **E0/E1/E3 的 D2/D4 行与 CP4 逐位一致**。新增 `shapecomp_port.py` /
  `validate_shapecomp_port.py` / `test_shapecomp_port.py`；改 `retrieval.py`（S0K 臂、lazy h5py）/
  `run_shape_benchmark.py` / `plot_shape_benchmark.py` / `pyproject.toml`（`shapecomp` extra）/ `.gitignore` /
  `official_shapecomp_status.json`；产物 `shapecomp_port_validation.json` + 重生成 D1–D4 JSON/CSV/图。
  工具箱与权重**不入库**（gitignored、无再分发许可）。复现：`validate_shapecomp_port.py` →
  `run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2` → `--stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2`
  → `--stage summary` → `plot_shape_benchmark.py --figure all`。
- **Git 检查点**：CP5 工作提交 `31e6b00`（`feat: run official ShapeComp KerNet1/KerNet2 via numpy port`），
  已推送 `origin main`（ssh.github.com:443）；核验 `git ls-remote origin refs/heads/main` == `git rev-parse HEAD`
  == `31e6b00efc754938217bb0ea68a7ee3e831c3d9a`，作者 `ANTACIDisnotFORMICACID`。CP1–CP4 见上。

## shape_perception_benchmark · Checkpoint 6（P0-4.6 · 给 ShapeComp 端口加旋转搜索并同条件对比，2026-11-08）

- **本轮目标（用户指令）**：项目目的是"旋转可查找"。CP5 证实官方 KerNet 权重人类预测力强但**不旋转不变**
  （D4 overlap 0.00、自匹配 rank 至 301）。本轮给端口**加旋转分析能力**，并与已有旋转设计的方案在
  **同一旋转条件**下对比。属方法优化的有界研究，**不改正式检索默认、不重训练、不动官方输入契约**。
- **加什么**：`retrieval.py` 给 S0K 臂加**查询侧旋转网格搜索** `d(q,g)=min_θ‖net(R_θ q)−net(g)‖`，
  用与 E3 `orientation="sample"` **相同的 24 步 / 15° 网格**；图库只按存储姿态打分一次，仅查询轮廓在送网络前旋转。
  新助手 `kernet_rotation_frames(network, contour)`→`(24,22)`（第 0 行=普通打分），`run_d5` 按形状缓存整段扫描复用。
  开关 `rotation_search=False` 默认关，**官方网络输入契约 `kernet1_input`/`kernet2_input` 一字未改**，权重仍为作者原始；
  旋转鲁棒性是**外加包装**、非网络属性，处处标注 `details["rotation_search"]`。
- **同条件协议（`--stage d5`，旋转是唯一变量）**：
  (a) **旋转压力人类一致性**——把每对已评分 H1 的一侧按各应力角旋转，各臂须在旋转后仍恢复其被评分的**发布人类相异度**；
  共享真值、两级配对 bootstrap（对+被试、2000 次、seed 20261107）。应力角混 15 的整数倍（15/45/90/135/270）与网格外 37/191，另 0 作对照。
  (b) **旋转压力检索**——查询按各角旋转后在 D4 分层图库排名，稳定性=与其自身未旋转 Top-10 的 Jaccard overlap + 精确目标 rank。
- **结果（`results/d5_rotation_metrics.json`，图 `figures/rotation_stress.png`）**：
  (a) 旋转压力下合并 agreement ρ：E0 **+0.683**、E1 **+0.782**、E3 **+0.839**（三者随角度近乎恒定=旋转鲁棒）；
  S0K1 naive **+0.434**（φ=0 为 0.903，90°→0.075、270°→**−0.002** 塌方）→ **S0K1+rot +0.747**（曲线 0.706–0.772 持平）；
  S0K2 naive +0.458 → **S0K2+rot +0.774**。**加旋转搜索把网络在旋转下的人类预测力从 ~0.44 救回 ~0.75–0.77**，
  但**仍不胜 E3（0.839）甚至 E1（0.782）**；且**牺牲峰值**：φ=0 时 S0K1 由 0.903 降到 0.763（多姿态取最小让不同形状相互"撞近"）。
  (b) 旋转下 Top-10 overlap（均值/最差）：E0/E1 1.000/1.000、E3 0.916/0.674；S0K1 naive 0.162/0.037（rank 至 **297**）
  → **S0K1+rot 0.943/0.725（旋转口径 21 例中 20 例 rank 1，仅网格外 fork 37° 落 rank 6）**；S0K2 naive 0.148/0.018 → **S0K2+rot 0.910/0.632（19/21）**。
  **口径说明**：(a) 的 `n_rows=1750 = 250 对 × 7` 与 (b) 的 `21 = 3 目标 × 7` **都已在构造上排除 0° 对照**
  （`run_shape_benchmark.py` 两处都丢弃 `phi == 0`；对照单独存为 `unrotated_control_overlap`，七臂全 1.000）。
  这正是实际工况——查询旋转未知 ⇒ 搜索永远在跑——所以头部数字即"只看在转"的数字；
  φ=0 那一列只用来确认包装没有破坏本来正确的情况，**不是工作点**。
  **结论：旋转搜索使官方网络由"旋转失明"变为"旋转可查找"，检索稳定性达 E3 同级、目标基本回到 rank 1；
  少数失配正是 E3 已披露的半步网格（37°/191° 网格外）局限。**
  **为何 CP5 的领先没有延续**：S0 对 E3 的 +0.072 是在**姿态已对齐**的前提下挣到的；对 24 个姿态取 `min`
  等于把每对形状换成它"最乐观的距离"，被压平的恰是网络最敏感的那部分细序——我们要的平坦（鲁棒）是用更低的水平换来的。
  故**人类验证的相似度**与**旋转不变性**是两个独立属性：搜索修好了后者，同时花掉了前者。
  旋转未知工况下排序为 agreement **E3 > E1 > S0K2+rot > S0K1+rot > E0**；检索稳定性
  **E0=E1 > S0K1+rot > E3 ≈ S0K2+rot ≫ naive**，且 E0/E1/E3 在**每个**旋转查询上都把目标排在 rank 1。
- **判据对齐（与用户确认）**：本项目的要求是"**查询随机旋转时查找效果最好**"⇒ **主判据是 (b) 检索**，
  (a) 的 agreement 仅为"表示是否捕捉人类相似度"的支持性证据（源自 P0-4.6 的原始研究问题），**两者不得互替**。
  按 (b) 排名 **E0=E1（21/21）> E3（21/21）≈ S0K1+rot（20/21）≈ S0K2+rot（19/21）≫ naive（1/21、4/21）**。
  **CP5 的 S0>E3 完全属于 (a) 且以"姿态已对齐"为前提，从未承诺 (b)**；本轮得到的是"旋转可查找"，不是新的检索冠军。
  后续若要在 (b) 上真正挑战 E0/E1，路径是**朝向规范化**（查询与图库同旋到参考朝向）而非查询侧取 `min`，
  验收标准：**同时满足 (b) ≥ 21/21 rank 1 与 (a) ≥ E3 0.839**，任一退化即否掉该包装。
  以上判据与两条**未实测的机制推断**已记入 `docs/DECISIONS.md`：**D54**（主判据 = 随机旋转下的查找效果）、
  **D55**（朝向规范化可同时保住 (a)(b)，假设/待验证）、**D56**（更细网格救不回 (a)，假设/待验证）。
- **一致性核验**：`run_d5` 缓存打分器在 φ=0 复现 CP5 D1 值（S0K1 0.903、S0K2 0.901 vs D1 0.9032/0.9005）→ 捷径 == `pair_distance`。
- **确定性**：E0/E1/E3 与 **naive** S0K 路径本轮算术未变（`rotation_search` 默认 False，新代码在其后；S0K naive 分支仅改写为
  数值等价的对称式 `‖net(right)−net(left)‖`）；故 **D1–D4 结果文件未重跑**，行值仍为 CP5。仅新增 `d5_*` 与折入 `rotation_stress` 的 `metrics.json`。
- **验证**：`pytest -q` **792 passed**（含 15 端口测试，0 跳过）；`ruff check .` 全过；`mypy` 39 文件无问题。
  d5 实跑约 **39 分钟**（S0K 帧构建 + E3 检索扫描为主，帧缓存使 250 对扫描可控）。工具箱与权重仍 gitignored、不入库，许可边界同 CP5。
- **工程整合判断（交用户决定）**：加旋转搜索后的实跑网络在**旋转检索**上已可用（rank 1 恢复、overlap≈E3），
  但**人类 agreement 未超 E3**、且牺牲无旋转峰值——故**不替换正式默认**；是否把"旋转可查找的 ShapeComp"纳入产品由用户定。
- **Git 检查点**：CP6 工作提交 `e43f587`（`feat: add rotation search to ShapeComp port and compare under
  identical rotation (P0-4.6 CP6)`），已推送 `origin main`（ssh.github.com:443）；核验
  `git ls-remote origin refs/heads/main` == `git rev-parse HEAD` ==
  `e43f587107faa4bb7bed9666338464c680febd3d`，作者 `ANTACIDisnotFORMICACID`。CP1–CP5 见上。

## shape_perception_benchmark · Checkpoint 7 A/B（P0-4.6 · 先证官方复刻正确，再修 D5 统计单位，2026-11-09）

详细逐函数对照表与结果在 `experiments/shape_perception_benchmark/notes.md` §Checkpoint 7（A1–A5、B1–B6）；
决策记录 **D57–D63**。本节只留复现命令与关键数值。

- **CP7-A 端口忠实度审计**（工作提交 `82af902`）：官方 MATLAB ↔ `shapecomp_port.py` 逐函数对照 + 输入契约 + SHA256。
  新脚本 `scripts/hash_shapecomp_toolbox.py` → `results/official_toolbox_hashes.json`（工具箱 2340 文件 / 333 个 `.m` /
  3 个 `.h5`；`shape2sc22a.h5` sha `a6fd1f06cee40626…`、`im2sc22d.h5` sha `035f655b5c9af13b…`；`redistribution` 字段说明
  CC BY 不覆盖工具箱二进制）。`validate_shapecomp_port.py` schema 升 `/2` 并加 flatten 次序消融与"相位对齐"搜索。
  **关键数值**：H2 逐维 Spearman 0.99894 / 0.99823、RMSE **0.0064962**（与 CP5 逐位相同 ⇒ KerNet2 已验证路径**无精度退化**）；
  **8/15** 个 H2 形状在"起点 × 方向 × 存档文件"对齐后与发布 22 维坐标差 **中位 1.5e-07**（原生相位中位 1.5e-02）；
  其余 7 例 **4.8e-3–1.8e-2** 记为**未归因**（禁止说成全部来自预处理，D57）；KerNet1 只钉住 768 维**次序**
  （列主序 x-then-y 0.184 ≪ 行主序 1.922 ≪ y-then-x 2.622），网络级忠实度未到机器精度；
  40×40 **单像素翻转 = 嵌入范数 18.5%**（中位 0.2887，D59）；`inpolygon` 直译替换 GEOS `covers` 后在 75 条官方轮廓
  （含自交）**逐像素 0 差异**；不合法输入显式 `ShapeCompInputError` 拒绝（全协点 / <3 顶点 / 非有限 / 无 extent）。
  `tests/test_shapecomp_port.py` 15 → **34** 项（含 `resamplePolyline` MATLAB 循环字面翻译的逐位等价测试）。
- **CP7-B D5 统计单位修正**（工作提交 `193c0f8`）：数值与判读见 notes §B1–B6。
  新阶段 `python scripts/run_shape_benchmark.py --stage d5b`（约 30 s，**只读** `d5_rotation_results.csv`，
  不需要工具箱）→ `results/d5_grouped_metrics.json` + `d5_grouped_results.csv`；随后 `--stage summary` 折入
  **新增**块 `rotation_stress_agreement_grouped`。**既有 `d5_*` 文件一字未改**（其 SHA256+字节数记入新产物，测试再哈希）。
  新函数 `metrics.bootstrap_grouped_two_level()`：整簇（一对的 7 行同进同出）+ 观察者两级重抽样、所有臂共用
  replicate 流 ⇒ 配对差；角度默认作**固定设计因子**，`resample_conditions=True` 为敏感性。
  **关键数值**：复现闸门最大偏差 **0.000e+00**；区间宽度 ×1.49–2.13（E0 0.0689→**0.1468**、E1 0.0675→0.1159、
  E3 0.0625→0.1054、S0K1 0.0926→0.1462、S0K2 0.0967→0.1443）；**点估计逐位不动**（E0 +0.6831 / E1 +0.7824 /
  E3 +0.8387 / S0K1 +0.4341 / S0K1+rot +0.7472 / S0K2 +0.4582 / S0K2+rot +0.7735）。
  **独立复核**：D1（未旋转、本就 250 对为单位）给出 E0 宽度 0.1468、E1 0.1159、E3 0.1066 ⇒ 修正后的旋转鲁棒臂
  **逐位重合**（E3 差 0.0012，因其旋转搜索确实随角度改变距离），naive 臂则合理变宽。
  配对后 21 个臂序 **19 成立**；**E1 vs S0K2+rot**（+0.0085 [−0.0287,+0.0467]）与 **S0K1 vs S0K2 naive**
  （−0.0239 [−0.0496,+0.0015]）**不可区分**；角度随机化敏感性下再失 2 条（E1>S0K1+rot、S0K2+rot>S0K1+rot）。
  检索判据 (b) 与本阶段无关，未触碰任何检索数字（主判据仍 D54）。
- **假设声明**：D5(a) 旋转一个成员后**沿用原有人类评分**＝假设"旋转不改变被试感知到的相似度"；本轮未复测任何旋转对、
  不存在新评分 ⇒ (a) 度量"对图像变换的不变性"，不是"人类对旋转形状的判断"。已写入产物 `assumptions` 与 D63。
- **验证**：`pytest` **818 passed**（新增 7 项：grouped 区间必须宽于行独立、复制臂差恰为 0 证明共用 replicate、
  条件随机化须变宽、ragged 设计、退化/错位/非交叉/不完整输入显式拒绝、产物审计与 SHA256 复核）；
  `ruff check .` 全过；`mypy` 无问题。唯一触及共享辅助函数的改动是 `_interval()` 在全 replicate 退化时改抛
  `ValueError`（原先在 `np.quantile` 内崩），真实数据不触发（各臂 2000/2000 有效 replicate）。
- **边界**：本轮**未**改生产默认检索器、**未**引入真实水体数据、**未**重跑 D1–D5 打分；CP7-C（R0/R1/R2 + T0/T1/T2）
  与 CP7-D（F1–F4、图与最终报告）见下一节，A/B 两节本身未触碰检索数字。

## shape_perception_benchmark · Checkpoint 7 C/D（P0-4.6 · 冻结旋转方案对照 + 接口/术语/许可审计，2026-11-09）

详细设计与逐条判读在 `experiments/shape_perception_benchmark/notes.md` §Checkpoint 7（C1–C7、D1–D6）
与 §十问；决策记录 **D64–D76**。本节只留复现命令与关键数值。

**CP7-C 冻结旋转研究**

- 复现：`python scripts/run_shape_benchmark.py --stage d6 --methods E0 E1 E3 S0K1 S0K2`（需工具箱）
  → `results/d6_retrieval_results.csv`（3456 行）、`d6_top10_hits.csv`、`d6_agreement_results.csv`（28000 行）、
  `d6_rotation_metrics.json`；随后 `--stage summary`；图：`python scripts/plot_rotation_checkpoint7.py`。
- **设计在运行前冻结**（产物 `design.frozen_before_run=true`）：12 查询（覆盖星状/心形/新月/分叉/细长/近圆/对称多解）
  × 5 角度槽（`on_grid`×1、`mid_grid`×2、`random`×2；栅格步长 15°，`random` 距栅格与中点均 ≥2.0°）
  + 1 control = **72 单元/臂/条件**，条件 T0/T1/T2，**主条件 T2**（删精确目标 + 删刚性近重复，阈值 0.05），
  seed 20261107，top_k 10，16 臂。每臂每单元都跑 ⇒ 不存在"少跑难例换速度"的自由度。
- 新模块 `earthglyph/shape_benchmark/rotation_methods.py`（+ `tests/test_shape_benchmark_rotation_methods.py`
  **48** 项，注入 scorer 后**无需工具箱**即可运行）：`naive(1,1)`、`R0(24,1)`（= CP6 搜索，等值测试钉住）、
  `R1_36(36,1)`、`R1_72(72,1)`、`R2(1,1)` 双向朝向规范化、`R3(24,24)` 双向多方向 min。
  选姿态**不看目标/类别/图库**（`test_pose_choice_never_consults_the_gallery`），
  R3 多姿态仍每形状一条（`test_r3_adds_gallery_poses_without_changing_the_query_grid`）。
- **T2 关键数值**（同族 Top-1 / Top-1 稳定性 / Recall@10 / MRR / NDCG@10 / Top-10 重合 / 平均排名位移 / s 每查询）：
  E1 **0.833**/1.000/0.267/0.879/0.722/0.995/0.2/0.013；
  S0K2/R2 **0.764**/0.972/0.225/0.819/0.621/0.958/0.7/**0.009**；E0 0.750/1.000；S0K1/R3 0.694/0.778/
  **0.288**/0.811/**0.735**/0.810/5.1/0.086；S0K1/R0 0.611/0.736；S0K1/R1_72 0.611/0.833/0.244/0.743/0.612/**0.911**/1.8/0.183；
  **naive 0.264 / 0.250，稳定性 0.250 / 0.208**，且 72 单元中分别 **36 / 40** 个在 Top-10 内**无同族形状**
  （首个同族 rank 达 250 / 241），平均位移 **46.5 / 45.9**、最大 **343 / 324**。
- **错误吸引**（目标距离 − 最近异类距离，正=安全）：naive **−0.273 / −0.263**，包装器全部为正 +0.248…+0.593。
  缓存与逐对距离最大偏差 **0.0**（16 臂 × 40 检查）⇒ 预计算只改变耗时。
- **R2 严格不变性的直接验证**：`python scripts/verify_r2_pose_invariance.py` →
  `results/r2_pose_invariance_check.json`。同一轮廓 37/191/270° 下 naive 坐标移动 **0.61–2.04**
  （坐标范数 ~1.22，即超过自身长度），R2 在有主轴的 11 条上 **≤5.1e-14（K1）/ 恰好 0.0（K2）**；
  唯一 `isotropic-fallback` 轮廓 `device2-1` 上 R2 与 naive **逐位相同**（0.1523/0.4336）⇒ 无轴即完全不生效。
  图库侧状态统计：362 条轮廓 `ok` 343 / `minor` 4 / `isotropic-fallback` **13** / `sign-tie` **2**；
  R2 与 E3 同一条轮廓朝向差最大 **180°**（317 条比较，中位 0）；规范化轮廓栅格化后 mask 主轴偏差最大 **179.6°**（tree-1）。
- **预计算成本**：图库 2.899 s、E3 canonical 0.156 s、姿态栅格 **KerNet1 22.363 s / KerNet2 78.404 s**（一次性，按 network×strategy 缓存）。
- **人类一致性（与检索分表，永不合并）**：E3/sample **0.839** > S0K1/R3 0.830 ≈ S0K2/R3 0.829 > E1 0.782 >
  S0K2/R0 0.774 > S0K1/R0 0.747 > E0 0.683 > S0K2/R2 0.677 > S0K1/R2 0.639 > naive 0.458 / 0.434。
  配对差（共用 replicate，250 簇）：R3−R0 = **+0.081 [+0.055,+0.108]**（K1）、**+0.055 [+0.034,+0.078]**（K2）；
  R2−R0 = **−0.108 [−0.180,−0.045]**；R3−naive = +0.388 / +0.365。
  R2 的 7 角度一致性曲线**完全平**（spread `0.000e+00`）——配合上面的坐标检查才说明"不变"而非"丢弃姿态"。

**CP7-D F1–F4**

- **F1**：`python scripts/measure_e1_orientation_sensitivity.py` → `results/f1_e1_orientation_sensitivity.{json,csv}`
  （11 探针 × 12 姿态，91 维）。结论：`principal_frame()` 手构特征向量在**协方差对角且大方差在 y**时下溢走 `+x`
  兜底，交付帧与主轴差 **90°** 而状态仍写 `canonical`（5 探针各 2 姿态命中，漂移 ~80× 于轴正确姿态）；
  isotropic fallback（circle/hexagon/near_circle/square/star5）最坏 all-drift 0.0242、直方图块 0.6825；
  所有探针的旋转位移均未超过到最近异类的距离。**故意不修**（修它=重跑全部已提交 E1 数字），只记录 + 另立方案：
  E1 的 24 姿态查询侧 min 实测 0.0116→0.2787 s/查询，自距离 0.1327→0.0167（去除 87.4% 旋转位移），
  异类距离比值 0.9999（最坏 1.0）⇒ **计价，不采纳**。
- **F2**：`allow_mirror` 对 `S0/S0K1/S0K2` 改为**显式 `ValueError`**（`_require_mirror_support`，在载入权重前触发）；
  连带修两处更隐蔽的静默无效——① 含镜像的缓存无视 `allow_mirror=False` 仍使用镜像（改为按开关取用，
  `test_a_mirror_capable_cache_still_obeys_the_query_flag` 双向断言）；② E1 `fit_normalizer` 把浮点残差列
  （H1 6 列，极差 5e-17–4e-16）除成 2.4–8.6 距离，使自身镜像永不可能胜出（加 `_SPREAD_FLOOR_RELATIVE=1e-12`；
  两套真实语料**改变 0 列**，最小真实极差比地板高 5–8 个数量级，`shapecomp_port_validation.json` 重跑 0 个数值变化）；
  ③ `Gallery(e3_params={"allow_mirror":True})` + `search_shape("E3")` 的 `TypeError` 关键字冲突。
- **F3**：术语统一为"官方 **109** 描述子 → **22** 维 MDS；91 是 EarthGlyph 自写 E1 的宽度"，
  含两处**代码生成字符串**（arm_availability、`reference_semantics`）；核实仓库从未声称复现 109 维引擎
  （`getShapeComp22.m:26` 即 `SC = predict(net2, points)`）。**无任何结果数值变化**（错在文字）。
- **F4**：README 新增 "Official toolbox" 节（作者渠道取回 → 放仓库根 `shapeComp_17_12_2024/` → 用
  `validate_shapecomp_port.py` 比对 24 项 SHA256）；`.gitignore` 增 `*.h5`、`shapeComp_*.zip`。
  **CC BY 只覆盖论文与其数据沉积，不构成工具箱权重/源码的再分发许可**。

**交付图**：`checkpoint7/rotation_methods_overview.png`(1 233 404 B)、`rotation_rank_stability.png`(478 131 B)、
`rotation_human_agreement.png`(313 592 B)、`rotation_failure_cases.png`(443 286 B)、
`shapecomp_port_diagnostics.png`(243 908 B，CP7-A)。
出图过程发现并修掉一处真实缺陷：`d6_top10_hits.csv` 的 pose 四舍五入到 3 位小数而
`d6_retrieval_results.csv` 保留全精度，`abs(pose−angle)<1e-6` 的联配对 8 个最差单元中的 3 个匹配 0 条 ⇒
**空白面板 = 静默删除证据**；容差改 1e-3° 后 8 行均 `matched=10`。
失败原因只在有存储测量支撑处标注，否则写 `cause undetermined - not guessed (open item)`。

**探索性披露**：R1/R2/R3 与 F1 的 E1 旋转搜索方案均在**同一冻结样本**上设计、调参与评价，
无独立保留集、无新人类评分 ⇒ 属探索性结论；角度集为**固定设计因子**（D62）。

**验证**：`pytest -q` **通过**、`ruff check .` 全过、`mypy` 40 个源文件无问题。

**边界**：未改生产默认检索器；未引入真实水体数据；官方 MATLAB 与 `.h5` 只读未改；本轮到此停止，
不做 Web UI、不扩大图库。

Git 检查点：工作提交 `2edce1a87cbaed087d7e662bc974ec9a660fdcbd`
（`feat: freeze the rotation-method comparison and audit the mirror interface (P0-4.6 CP7-C/D)`），
文档提交 `b4840d1cf301d792218205b7388034534e0f815c`；已推送 `origin main`（ssh.github.com:443）并核验
`git ls-remote origin refs/heads/main` == `git rev-parse HEAD`，作者 `ANTACIDisnotFORMICACID`。
CP7-A `82af902`、CP7-B `193c0f8` 见上节。

## shape_perception_benchmark · Checkpoint 7 收官：文档整合与口径复核（P0-4.6，2026-11-09，纯文档轮）

**输入**：CP7-A…CP7-D 已入库的产物（`results/d6_rotation_metrics.json`、`d6_retrieval_results.csv`、
`d6_top10_hits.csv`、`d6_agreement_results.csv`、`r2_pose_invariance_check.json`、`f1_*`、
`d5_grouped_metrics.json`、`official_toolbox_hashes.json`）与 CP1–CP7 全部文档。
**本轮没有运行任何 stage、没有重算任何数字、没有新图** ⇒ 不产生新的结果文件，只做"文字是否与产物一致"。

**改了什么（7 个文档文件）**

1. `docs/DECISIONS.md`：**D55 / D56 / D59** 从"假设 / 待验证"改写为**已实测判定**，每条给出产物路径、
   点估计与配对区间；原推断文字保留并注明是当时的推断（不覆盖历史）。P0-4.5 的
   "ShapeComp-inspired 基线能否成立"一条由悬空问题改为**已定量回答**（E1 agreement 0.782 /
   T2 同族 Top-1 0.833，网络臂对齐姿态 0.903 但最佳包装 0.764），并按 D60 收窄 D46 的范围。
2. `docs/EXPERIMENTS.md`：新增 §3.3 基准协议表（D1 / D2–D4 / D5–D5b / D6 四层，逐层写明**禁止的解读**），
   并在 §6 门禁处补一段：门禁状态**未变**，本基准**不是**门禁证据（14 人评分对 ≠ ≥10 个不同用户的真人手绘；
   MPEG-7 类别 ≠ 人类感知相似度），只贡献三条方法学结论（两个独立属性、按簇置信区间、材料重复先于算法归因）。
3. `docs/ROADMAP.md`：P0 门禁下补一段记录 CP1–CP7 与"不纳入生产默认"的结论（D54/D65/D67/D71），写明门禁不变。
4. `docs/TASKS.md`：P0-4.6 标题改为 **Checkpoint 7 收官**；CP6 的"下一步（优先）"里两条当时未实测的推断
   补上 CP7-C 的判定与数字。
5. `experiments/shape_perception_benchmark/README.md`：新增 Checkpoint 7 整节（包装器×策略×成本表、
   16 臂 T2 指标表、agreement 排名与三条分开陈述的结论、D55 否判、R2 不变性小节、配对差**读法陷阱**表、
   CP7-A/B 一句话结论），并把 `## Files` 与 `## Reproduce` 补齐到本轮全部脚本与 CSV。
6. `experiments/shape_perception_benchmark/INTERPRETATION.md`：旧"尚未实测"处就地补**已实测并判否**的日期块，
   含最终定夺与 git 检查点序列（CP7-A `82af902`、CP7-B `193c0f8`、CP7-C/D `2edce1a`）。

**复核中抓到的三处真实口径问题**（都已写进文档，不改代码）

- **配对差的符号约定在两个产物里相反**：`d5_grouped_metrics.json` 的 `delta_agreement` 由
  `scripts/run_shape_benchmark.py:1022` 的 `delta()` 翻到 agreement 尺度，而 `d6_rotation_metrics.json`
  在 `run_shape_benchmark.py:1868` 直接存 `earthglyph/shape_benchmark/metrics.py:249` 的原始 bootstrap delta。
  ⇒ 键 `A|B` 在 d6 里 `mean = agreement(B) − agreement(A)`。README 现出表说明，避免把 −0.108 读成 +0.108。
- **D55 的分母曾被写错**：初稿把"21/21"说成"250 对 H1 刺激上按对计的 rank-1 统计"。核对
  `notes.md` §Checkpoint 6 后确认 **21 = 3 个目标 × 7 个非零角度**、材料是 30 成员的 D4 分层 MPEG-7 图库，
  0° control 不进头部统计而单独存为 `unrotated_control_overlap`；250 对 × 7 角度 = 1750 行属于 **(a)** 侧。
  ⇒ D55 与 README 现在都写明三套分母（CP6-b 的 21 / CP6-a 的 1750 / CP7-C 的 72×16）不可互换，
  且否判依据在 (a) 侧、与分母争议无关。
- **`--stage d6` 的覆盖陷阱**：`run_d6` 的臂列表由 `_d6_arms()`（`run_shape_benchmark.py:1276`）按
  `--methods` 生成，而 `--methods` 默认只有 `("E0","E1","E3")` ⇒ 不带 `--methods` 重跑会把 16 臂的
  `d6_*` 产物**改写成 4 臂表**。这是已存在行为，本轮按边界只记录不改码，复现命令一律带 `--methods`。

**验证**（本轮实际执行）：`pytest -q -o addopts=""` → **874 passed in 94.29s**；`ruff check .` → All checks passed；
`mypy` → no issues found in 40 source files。另跑两项文档自检：8 个 markdown 文件的表格列数一致性 **0 处不一致**；
术语扫描（"91 × ShapeComp"）命中 8 行全为**合法用法**（E1 自写 91 维的标注，或纠正说明本身）。
所有引用的数字都回到产物重取，不抄 prose。

**边界**：结果文件、`.h5` 权重、官方压缩包、生产默认检索器、真实水体库、Web UI 均未触碰；
`git diff --stat` 只含文档文件。本轮到此停止。

Git 检查点：文档提交 `32b14bad9408d170b0b350c2f6906e029018d3f2`
（`docs: consolidate the CP7 verdicts into every record and fix three provenance slips (P0-4.6)`，
7 files / +359 / −25）；已推送 `origin main`（ssh.github.com:443）并核验
`git ls-remote origin refs/heads/main` == `git rev-parse HEAD` == 上式，作者 `ANTACIDisnotFORMICACID`。
