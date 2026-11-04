# experiment_log — 实验与探测台账

> 追加式记录。**检索类实验**（P0-5 / P0-6 起）的指标写入 `results.csv`；
> 数据探测类记录不构成检索证据。规则见 [`docs/EXPERIMENTS.md`](../docs/EXPERIMENTS.md)。

| 日期 | id | 类型 | 数据 / 版本 | 目的 | 产物 | 结论 |
|------|----|------|-------------|------|------|------|
| 2026-11-04 | `p0_data_probe` | **数据检查（非检索）** | Overture Base/water, release 2026-10-21.0, bbox -90,44,-89,45, 6,543 行 | 确认真实水体轮廓可获取、Schema 正确、候选量与多样性足以支撑检索实验 | `dataset_summary.json`, `geometry_preview.png`, `notes.md` | 可用闭合轮廓 1,984（30.3%），lake/pond/reservoir ≈979；发现三类伪轮廓（下载框切片 / 游泳池矩形 / 河岸细长条）须在 P0-2 过滤；**未验证检索命中率** |
| 2026-11-05 | `p0_geometry_probe` | **数据预处理检查（非检索）** | 同上原始数据 → `data/processed/contours.parquet`（839 候选） | 目视确认清洗与标准化未破坏真实形状、过滤不过度、复杂形状（枝状/月牙/带孔/MultiPolygon/细长）保留 | `before_after.png`, `candidate_gallery.png`, `rejected_examples.png`, `complex_contours.png`, `summary.json`, `notes.md` | 6,543 行 → 839 标准轮廓，损失全部可解释；归一化保持比例；D15 框边过滤被几何证据推翻改为标志位；N=128 对极端枝状岸线欠采样（最坏步长偏差 ~104%）→ 采样消融留给 P0-3；**未验证检索命中率** |

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
