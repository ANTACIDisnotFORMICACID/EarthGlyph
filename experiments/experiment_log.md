# experiment_log — 实验与探测台账

> 追加式记录。**检索类实验**（P0-5 / P0-6 起）的指标写入 `results.csv`；
> 数据探测类记录不构成检索证据。规则见 [`docs/EXPERIMENTS.md`](../docs/EXPERIMENTS.md)。

| 日期 | id | 类型 | 数据 / 版本 | 目的 | 产物 | 结论 |
|------|----|------|-------------|------|------|------|
| 2026-11-04 | `p0_data_probe` | **数据检查（非检索）** | Overture Base/water, release 2026-10-21.0, bbox -90,44,-89,45, 6,543 行 | 确认真实水体轮廓可获取、Schema 正确、候选量与多样性足以支撑检索实验 | `dataset_summary.json`, `geometry_preview.png`, `notes.md` | 可用闭合轮廓 1,984（30.3%），lake/pond/reservoir ≈979；发现三类伪轮廓（下载框切片 / 游泳池矩形 / 河岸细长条）须在 P0-2 过滤；**未验证检索命中率** |
| 2026-11-05 | `p0_geometry_probe` | **数据预处理检查（非检索）** | 同上原始数据 → `data/processed/contours.parquet`（839 候选） | 目视确认清洗与标准化未破坏真实形状、过滤不过度、复杂形状（枝状/月牙/带孔/MultiPolygon/细长）保留 | `before_after.png`, `candidate_gallery.png`, `rejected_examples.png`, `complex_contours.png`, `summary.json`, `notes.md` | 6,543 行 → 839 标准轮廓，损失全部可解释；归一化保持比例；D15 框边过滤被几何证据推翻改为标志位；N=128 对极端枝状岸线欠采样（最坏步长偏差 ~104%）→ 采样消融留给 P0-3；**未验证检索命中率** |
| 2026-11-05 | `p0_feature_probe` | **特征定义 / 几何保真度 / 特征空间测量（非检索）** | Overture release 2026-10-21.0, bbox -90,44,-89,45 → `data/processed/contours.parquet`（839 候选，128 点）→ `data/index/` | ① Fourier 幅值描述子与 4 个几何统计量的定义、不变性与退化处理；② 谐波数 12/16/24 取舍；③ N=64/128/256 采样消融（从原始外环重采样）；④ 索引确定性与可回查；⑤ 候选库多样性与 pending 类别是否扩容 | `features_summary.json`, `harmonic_ablation.png`, `phase_loss_examples.png`, `near_duplicate_pairs.csv`, `candidate_distribution.png`, `sampling_metrics.csv`(2,517 行), `sampling_summary.json`, `sampling_comparison.png`, `sampling_zoom.png`, `sampling_error_scatter.png`, `sampling_metric_validation.{csv,json}`, `pending_summary.json`, `pending_parts.csv`, `pending_distribution.png`, `notes.md` | 谐波数取 16（12/24 无可测增益）；**N=128 保留**（Hausdorff 归一 p50 0.0157、region IoU 均值 0.9949；256 的增益不值字节翻倍），8 个候选在任何 N 都达不到 IoU≥0.95、5 个在 N=128 重建自交（全为 reservoir，已上报不修复）；云近似偏差经 2,517 对实测 0 违例；索引 839 行确定性构建、逐候选可回查真实经纬度；pending 建议 `add_now: false`；**未验证检索命中率** |

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

> 更正记录：本台账 2026-11-05 `p0_geometry_probe` 行的"最坏步长偏差 ~104%"
> 在 P0-3 被重新归类为**弦长离散度**（`chord_dispersion`），不是重建误差；
> 同一候选 N=128 的重建误差实测为 Hausdorff 归一 0.1483 / region IoU 0.8104（见 D30）。
