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

