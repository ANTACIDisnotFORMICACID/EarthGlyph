# p0_feature_probe — P0-3 特征提取、索引构建与采样点数消融

> **记录类型：特征定义 + 几何保真度 + 特征空间测量。不是检索实验。**
> 本轮没有真值对、没有查询草图、没有 Top-K 命中数字；
> 文中任何"近邻重合率"只表示同一描述子在参数变化下的自一致性，不得引用为检索命中率。
> 规则见 [`AGENTS.md`](../../AGENTS.md)、[`docs/EXPERIMENTS.md`](../../docs/EXPERIMENTS.md)。

## 0. 目标（一句话）

验证 P0-3 的三件事：① 粗筛特征能否按声明的数学定义稳定计算并具备声明的不变性；
② N=128 的弧长采样在**真实**岸线上是否够用（64/128/256 对照）；
③ 特征索引能否从真实数据确定性构建、逐候选回查真实地点。

## 1. 数据与版本

| 项 | 值 |
|----|----|
| 上游来源 | Overture Maps Base theme / type=water（ODbL 适用许可） |
| release | `2026-10-21.0` |
| bbox | `-90,44,-89,45`（美国威斯康星 1°×1°） |
| 原始文件 | `data/raw/wisconsin_water.parquet`，6,543 行，sha256 `1c5431f2…1a417` |
| 输入数据集 | `data/processed/contours.parquet`，839 候选，sha256 `65cafc2d…2d2740` |
| 投影 | 局部 aeqd（`+lat_0=44.51248355 +lon_0=-89.4323509 +ellps=WGS84 +units=m`，D16） |
| 环境 | Python 3.12.10 / numpy 2.5.4 / scipy 1.18.1 / shapely 2.2.0 (GEOS 3.14.1) / pandas 3.1.0 / pyarrow 26.0.1 |
| 随机性 | **无**：所有选样规则确定性（面积分层 / 形状指标 / 等距下标），`random_seed: null` |

## 2. 运行命令

```bash
# Task A/B/D：特征与索引
python scripts/build_index.py --contours data/processed/contours.parquet \
    --out-dir data/index --n-harmonics 16 --fold positive --dtype float32 --force
# Task A/B/E：特征空间与候选库研究
python scripts/analyze_features.py --contours data/processed/contours.parquet \
    --out-dir experiments/p0_feature_probe
# Task C：采样点数消融（从原始外环重新采样）
python scripts/analyze_sampling.py --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
# Task C 的度量正确性校验（云近似 vs 精确 GEOS / 8× 细云）
python scripts/analyze_sampling.py --validate-exact --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
# Task F：待分析类别（只读）
python scripts/analyze_pending.py --out-dir experiments/p0_feature_probe
# 单元测试 / 静态检查
pytest        # 281 passed
ruff check .  # All checks passed
mypy          # Success: no issues found in 17 source files
```

## 3. Task A — Fourier 描述子（结论见 D24/D25/D26）

**定义**：闭合轮廓 `z_i = x_i + i·y_i`（已 RMS 归一、CCW）→ `Z_k = DFT(z)` →
描述子 = `|Z_k|/N`，k = 1..16，**按谐波序号排列**（不按数值排序）。
`Z_0` 丢弃（它是质心，携带位置不是形状）；除以 N 使 64/128/256 点的描述子可比 ——
采样消融正依赖这一点。折叠 `positive` 只取 k≥1；`symmetric`（`( |Z_k|+|Z_-k| )/2`）保留为选项。

**不变性（理论 + 单测双重覆盖，`tests/test_fourier.py` 49 项）**：
平移（DC 丢弃）、尺度（RMS 归一）、旋转（公共相位因子被幅值丢弃）、
循环起点（k 相关相位被幅值丢弃）——四项按定义精确成立；
`n_harmonics` 受 Nyquist 限制 `N//2 − 1`（N=128 时上限 63），越界即抛 `FeatureError`。

**明确否掉的两个说法**：
1. **幅值描述子不区分"走向反转"与"真镜像"**：反转使 `Z_k → Z_-k`，
   显式反射（复共轭，D9）使 `Z_k → conj(Z_-k)`，两者都是 k↔−k 互换。
   `positive` 折叠下镜像对落到不同坐标，只是因为预处理固定了 CCW（D23），
   **不能**对外称"能检测镜像"。
2. **单点与批量必须等价**：`batch_fourier_descriptors` 与逐条 `fourier_descriptor`
   在真实 839 条上逐元素相等，由单测钉住，防止快路径漂移出定义。

**谐波数消融（12 / 16 / 24，839 个真实候选）**：

| 谐波数 | 非 DC 能量捕获 p10 / p50 | 成对距离 p10/p50/p90 | 最近邻中位 L2 | 与 h16 的 Top-10 重合（120 查询，k=10） |
|--------|--------------------------|----------------------|---------------|------------------------------------------|
| 12 | 0.7025 / 0.9007 | 0.0501 / 0.1290 / 0.2751 | 0.02146 | mean 0.9808（min 0.9） |
| 16 | 0.7025 / 0.9008 | 0.0502 / 0.1292 / 0.2756 | 0.02180 | 1.0 |
| 24 | 0.7025 / 0.9010 | 0.0503 / 0.1293 / 0.2758 | 0.02203 | mean 0.9875（min 0.9） |

能量捕获在 p10 处几乎完全相同（0.7025）——**说明限制不在谐波数，而在形状本身的
高频能量分布**；h12↔h16↔h24 的成对距离 pearson ≥ 0.99997，即更宽的描述子没有带来
可测的形状分离增益。取 **16** 的代价是描述子更宽、精度冗余，收益是与 12 相比最近邻
间距 +1.6%。此为几何取舍，**不宣称检索提升**（`not_measured: retrieval accuracy`）。

**幅值丢相位的代价（量化）**：对 839 个候选各构造一个"幅值完全相同、相位随机重排"的
冒形（同谱不同结构），二者 region IoU 分布 min 0.0007 / p10 0.1535 / **p50 0.5338** /
p90 0.8050，**98.81% 的冒形对 IoU < 0.9**（最差 6 例见 `phase_loss_examples.png`，
含真实经纬度）。→ 幅值命中**永远不能**当最终答案；相位或显式几何精排才是区分手段（D10）。

## 4. Task B — 辅助几何特征（结论见 D27/D28）

4 个无量纲统计量，全部在**标准化采样轮廓**（外环、闭合到自身）上计算 ——
基准明确写入 manifest，查询端必须同基准计算才可比：

| 列 | 公式 | 退化保护 |
|----|------|----------|
| `compactness` | `4πA/P²`（圆盘 = 1） | A=0 或 P=0 → `FeatureError` |
| `solidity` | `A/A_hull`（凸 = 1） | 凸包退化 → `FeatureError` |
| `aspect_ratio` | 最小面积外接矩形长边/短边 | 矩形宽 = 0 → `FeatureError` |
| `diameter_fill` | `4A/(πD²)`，D = 最远点对距离 | D=0 → `FeatureError` |

- 不变性实测：平移 / 均匀缩放 / 旋转 / 镜像 / 走向反转下四者全不变
  （`tests/test_geometry_features.py` 28 项，含"同一不变性群"参数化断言）。
  **对采样点数 N 不不变**（见 §5），这是查询/候选必须同 N 的原因。
- **实测推翻库实现**：GEOS `minimum_rotated_rectangle` 在一个 40 顶点**有效**凸包上
  把矩形塌成面积 6.279e-18 的细条 → aspect_ratio 出现非物理值。
  改为自带 Andrew monotone-chain 凸包 + 旋转卡尺（D28），单测用已知矩形钉住。
- 退化输入策略：**抛错并计数，绝不静默丢候选**。构建结果 `skipped_candidates: 0`。
- 两组特征只拼接不加权（`fusion: none`），组偏移写入 manifest `index.feature_groups`，
  便于 P0-4 逐项消融；未引入任何未验证的融合权重。

## 5. Task C — 采样点数消融 N=64/128/256（结论见 D29/D30）

**方法约束**：64/128/256 全部由 `exterior_ring(projected part)` **原始外环**重新弧长采样，
未使用"把已存 128 点插值到 256"的路径（任务明令禁止）；
参考基准 = N=128。三类被严格区分的量：① 弧长采样间隔（按构造无误差，不是质量指标）；
② 相邻点直线距离（弦）→ `perimeter_retention` / `max_shortcut_ratio` / `chord_dispersion`；
③ 重建误差 → Hausdorff / Chamfer / region IoU / 带符号面积误差。

**整体分布（839 候选 × 3 = 2,517 行，见 `sampling_metrics.csv`）**：

| 指标 | N=64 | N=128 | N=256 |
|------|------|-------|-------|
| Hausdorff 归一化 p50 / p90 / max | 0.0313 / 0.0708 / 0.2568 | 0.0157 / 0.0344 / 0.1483 | 0.0078 / 0.0178 / 0.0789 |
| Hausdorff 米 p50 / p99 / max | 2.49 / 107.4 / 801.9 | 1.19 / 64.0 / 543.1 | 0.61 / 31.3 / 310.3 |
| Chamfer 归一化 p50 / p90 / max | 0.0049 / 0.0118 / 0.0456 | 0.0022 / 0.0048 / 0.0221 | 0.0010 / 0.0020 / 0.0117 |
| region IoU min / p10 / 均值 | 0.6772 / 0.9704 / 0.9863 | 0.8104 / 0.9913 / 0.9949 | 0.8959 / 0.9977 / 0.9982 |
| 面积相对误差 p90（绝对值） | 0.0052 | 0.0014 | 0.0004 |
| 周长保持率 p10 | 0.9527 | 0.9769 | 0.9887 |
| 最坏弦长离散度 | 1.2514 | 1.0448 | 0.9094 |
| 重建自交的候选数 | 5 | 5 | 4 |

**特征稳定性（同一描述子定义：12 谐波幅值，参考 = N=128）**：
相对 L2 偏差 p50 / p99 / max = 4.85e-4 / 7.29e-3 / 2.01e-2（N=64），
1.18e-4 / 1.97e-3 / 5.87e-3（N=256）；
但几何统计量更敏感：`compactness` p99 相对偏差 **24.7%（N=64）/ 16.2%（N=256）**，
`aspect_ratio` max 35.3% / 31.7%。
特征空间近邻自一致性（120 查询、k=10，与 N=128 参考库比较）：
Top-10 重合 mean 0.9925（N=64）/ 0.9967（N=256），min 0.9 ——
**这是自一致性，不是检索正确性。**

**达标计数（目标：region IoU ≥ 0.95 且 Hausdorff 归一 ≤ 0.05；两个阈值是本研究的
筛选线，不是产品验收标准）**：
N=64 即达标 648 个；仅 N=128 起达标 152 个；仅 N=256 起达标 31 个；
**任何测试 N 都不达标 8 个**（7 个 reservoir + 1 个 lake）。
按 subtype：pond 全部在 64/128/256 = 400/69/3；lake = 237/60/14（1 个不达标）；
reservoir = 11/23/14（**7 个不达标**）。

**六类代表形状（`sampling_comparison.png`，含 `sampling_zoom.png` 局部放大与真实经纬度）**：

| 类别 | candidate_id | 面积 ha | 顶点 | IoU @64/128/256 | Hausdorff 归一 @128 |
|------|--------------|---------|------|------------------|---------------------|
| 近圆湖 | `8ee9166f-…` | 11.20 | 22 | 0.9983/0.9997/0.9999 | 0.0062 |
| 月牙湖 | `db4176d6-…` | 8.10 | 47 | 0.9956/0.9987/0.9997 | 0.0096 |
| 细长湖（18.3:1） | `7ce4100d-…` | 2.93 | 32 | 0.9899/0.9971/0.9993 | 0.0284 |
| 枝状水库 | `4b2c4aea-…-part1` | 2,269.76 | 8,085 | 0.7037/0.8104/0.8959 | 0.1483 |
| 深凹水库 | `bcb74a8c-…` | 157.52 | 690 | 0.6772/0.8209/0.9150 | 0.1296 |
| 多湾水库 | `28bbcf56-…` | 2,592.86 | 2,710 | 0.7568/0.8553/0.9220 | 0.1149 |

选样规则（min/max of 指标 + 面积下限，含放宽与兜底三档）逐条记录在
`sampling_summary.json → selection_rules`；**未只挑易处理的样本** —— 六类中四类是
最难的水库，且失败案例全部保留在 `failure_cases` 与 `worst_hausdorff_norm_at_reference`。

**失败案例（必须报告）**：
1. **枝状 / 多湾水库**：`4b2c4aea-…-part1` 在 N=128 仍只保留 55% 周长
   （`max_shortcut_ratio` 0.897 = 最坏单段被切掉 90%），N=256 才升到 64%。
   这类 8 个候选在任何测试 N 都达不到 IoU 0.95。
2. **重建自交**：N=128 有 5 个候选的闭合折线自交（全部是 reservoir，含
   `4b2c4aea-…-part1`、`d7182ef0-…-part2`、`dbdc7403-…`、`9038d463-…`、`0575ce64-…`）。
   源环本身全部有效（`source_ring_valid = True`），交叉来自弦跨过凹口/收颈，
   因此是**重采样在极端几何上的固有行为**，不是数据缺陷；实现选择是**上报不修复**
   （`self_intersected` + `validity_reason`），修不修、如何修留给精排设计。
3. **P0-2 "104% 偏差" 的归类修正**：该数字是 `chord_dispersion`
   （弦长对均值的最大相对偏离，N=128 实测 1.0448），不是重建误差；
   同一候选的真实重建误差是 Hausdorff 归一 0.1483 / IoU 0.8104。

**度量正确性校验（`--validate-exact`，新增）**：云近似 Hausdorff 对
① GEOS 闭合曲线离散 Hausdorff 与 ② 8× 更细点云 双向比较：
中位偏差 0.0122 / 0.0068 / 0.0038 m（N=64/128/256），最大绝对偏差
6.21 / 2.97 / 0.73 m，最大偏差 / 一个云步长 ≤ 0.12 / 0.46 / 0.43，
**2,517 对中超步长违例 = 0**。→ 上表的偏差是**保守上界**（偏大不偏小）。
校验过程中发现并修掉两个真实陷阱：`metric_points` 是**开**数组，比较前必须闭合
（某候选 Hausdorff 520 m(闭) vs 610 m(开)，闭弦 1,258 m）；
GEOS 距离是**顶点离散**值，是连续距离的下界，不能当作"精确真值"直接判优。

**结论（Task C 只允许主张几何保真与特征稳定）**：
- **N=128 保留为默认**：64 在 `compactness` 上引入 p99 24.7% 的漂移且最坏 IoU 只有 0.68；
  256 的增益（最坏 IoU 0.81→0.90、偏差再减半）不足以支付轮廓字节翻倍
  （5 万候选：48.8 → 97.7 MiB），且 P0 的浏览器延迟预算本身未测。
- **256 不值当前投入**，但**精排阶段可能需要**：自交与低 IoU 的 8 个候选全是水库，
  若 P0-4 显示它们是失败主因，再针对 reservoir 单独提高 N（而非全局翻倍）。
- **有支持自适应 N 的证据**（648/152/31/8 的分层结构 + subtype 差异明显），
  **但本轮不实施**：自适应 N 会破坏定宽批量读取与跨 N 可比性，收益未证明。
- **重采样器本身是否缺陷**：对 839 个候选的存储轮廓逐点回推误差 = 0，
  N=128 下仅 5 个极端几何出现自交、0 个候选被静默丢弃 → 判定"实现正确、
  在极端枝状/收颈岸线上有已知表达上限"，而非实现缺陷。

## 6. Task D — 索引构建与一致性

产物与校验和见 [`docs/DATA_PIPELINE.md`](../../docs/DATA_PIPELINE.md) §10。
一致性检查（数量相等、id 唯一、行序契约 `feature_row`、全部有限、维度/宽度、
轮廓可回推、经纬度合法、`n_points` 一致、重建字节一致）由 `earthglyph/data/index.py`
强制，异常注入由 `tests/test_index.py`（31 项）覆盖。
确定性实测：同命令两次构建三个产物字节一致；float32 往返最大绝对误差
features 6.70e-7 / shapes 5.96e-8。
**上游回归**：P0-2 的 `contours.parquet` 在 P0-3 重构 `earthglyph/data/prepare.py`
（抽出 `build_from_source` / `SourceBuild` 供采样消融复用）之后仍与 P0-2 manifest
记录的 sha256 **逐字节一致**（`65cafc2d…2d2740`），且 `绘图辅助函数抽到
scripts/probe_shared.py` 后重跑 `scripts/visualize_geometry.py` 四张 PNG 全部字节一致
→ P0-3 没有改动 P0-2 的数据与图（见 `experiments/experiment_log.md`）。
可回查性：839 个候选全部经 `candidate_id → source_id + part_index` 回到
release `2026-10-21.0` 的真实 feature，`lon/lat` 为 `representative_point`（D12）；
单测确认正式索引不含任何合成记录。
耗时（本机本次）：特征计算 1.659 s / 839 候选；批量描述子 0.0211 s；
839 候选全量 L2 扫描 1.7e-5 s；粗筛宽度对成本影响线性。

## 7. Task E — 候选库规模与多样性（未下载新数据）

- 839 候选：pond 472 / lake 312 / reservoir 55；class 含 lagoon 22 / basin 3 / oxbow 2；
  面积 0.10–9,819.87 ha（4.99 个数量级，p50 1.96 ha）。
- 形状分布（p5 / p50 / p95）：compactness 0.157/0.614/0.906、solidity 0.479/0.904/1.0、
  aspect_ratio 1.094/1.807/4.671、diameter_fill 0.123/0.428/0.738
  → 不是"一库椭圆"，但 reservoir（compactness 中位 0.240）是少数派（55 个）。
- 45 个带孔洞（最多 234 孔）、17 个 MultiPolygon part、8 个 `extends_beyond_frame`、0 个需修复。
- **近重复**：描述子 L2 < 0.02 且全谱 < 0.03 且相对面积 < 2% 的判定下，
  23 对 / 42 个候选 / 20 簇（最大簇 3）。因旋转与循环起点**尚未对齐**，
  这是可混淆程度的**下界**；去重决策留给 P0-4（阈值是研究选择）。
- Type A（算法）vs Type B（数据覆盖）的判别字段已备好但**不判定**：
  查询侧需 `query_sketch_id` / `query_source` / `topk_candidate_ids` / `topk_feature_l2` /
  `topk_aligned_geometric_score`(P0-4) / `human_label`；
  库侧现有 `candidate_id`/`source_id`/`subtype`/`area_ha`/4 个统计量/`lon`/`lat`。
  Type B 定义 = "最宽描述子下也无候选越过精排阈值"；Type A = "肉眼匹配的候选被描述子排低"。
  现状信号：单 1°×1° 单州框 → 库的地域覆盖窄，是本阶段最主要的 Type B 风险来源。

## 8. Task F — pending 类别（只分析，未入索引，文件未删改）

`data/processed/pending_analysis.parquet`：1,034 feature / 1,035 part
（water 836 / river 143 / human_made 50 / stream 4 / canal 1），
0 个无效几何、0 个标准化失败、310 个低于 0.1 ha、725 个可过面积下限。

| subtype | features | 面积中位 ha | compactness 中位 | 到最近候选 L2 中位 | 超出候选库自身 p90 间距(0.05749)的比例 |
|---------|----------|-------------|-------------------|---------------------|-----------------------------------------|
| water | 836 | 0.205 | 0.664 | 0.01629 | **6.82%** |
| river | 143 | 1.291 | 0.106 | 0.0512 | **42.36%** |
| human_made | 50 | 0.008 | 0.750 | 0.01845 | 4.0% |
| stream | 4 | 0.151 | 0.070 | 0.04982 | 50.0% |
| canal | 1 | 0.013 | 0.877 | 0.01155 | 0.0% |

判读：`water` 与已发布候选**高度冗余**（93% 落在候选库自身间距之内），并入只增噪声；
`river`/`stream` 确实带来新形状，但那是**线状地物被人为封合的轮廓**，
不是手绘闭合形状查询想表达的对象。
→ 建议 **`add_now: false`**，扩容条件 = P0-4/P0-6 出现"缺类型"型失败（Type B）。
（该建议是数据治理判断，不是检索结论；决策权在 P0-4，见 D32。）

## 9. 本轮不允许的主张

- 不主张"检索质量已验证"或"算法已得到提升"（无任何 Top-K 数字）。
- 不主张 137 项旧测试或本轮 281 项测试是检索效果的证据 —— 它们只是实现正确性。
- 不主张采样误差（Hausdorff/IoU）等于搜索质量。
- 不主张 Fourier 幅值能区分镜像或能区分所有形状（§3 已量化其失效）。
- 不主张候选库覆盖全球或跨纬度可用（单 1°×1° 区域，跨纬度/极区/180° 经线未验证）。

## 10. 下一步（P0-4 输入条件）

1. 精排：实现 `D(A,B) = min_{s,θ} mean |a_k − e^{iθ} b_{(k+s) mod N}|²`，θ 由复数相关求（D8），
   镜像显式复共轭（D9），并输出真实 Top-10 叠加图 + 每候选地图定位（真实手绘 ≥1 例）。
2. Baseline 对照：Hu 矩（`cv2.matchShapes`）vs Fourier 粗筛 vs 粗筛+精排，同一候选库同一查询。
3. 用 §7 的字段把每个失败查询记为 Type A 或 Type B；`results.csv` 从此才有指标。
4. 依据真实查询复核两件事：8 个极端水库是否需要单独提高 N；`river` 类是否需要入场。
5. 跨区域无效几何覆盖度（本轮 `make_valid` 路径仅由合成单测覆盖）另立任务。
