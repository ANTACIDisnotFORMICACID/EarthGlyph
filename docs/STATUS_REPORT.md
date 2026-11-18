# EarthGlyph 现状汇总报告

> 日期：2026-11-18 · 基线 commit：`729849c4ec9bcb423d075beff4d097d04dc6d520`（main，已推送）
> 基线复验（本日实跑）：`pytest -q -o addopts=""` → **1042 passed**（150.12 s，0 failed 0 skipped）·
> `ruff check .` → All checks passed · `mypy` → no issues in 41 source files。
> 本文所有数字均出自已入库的实验产物与 `docs/TASKS.md` 台账，无新增计算；
> 引用处标注产物路径。状态词严格区分 VERIFIED / PARTIALLY VERIFIED / BLOCKED / NOT STARTED。

---

## 一、当前具体实现的功能

### A. 生产检索管线（真实数据，端到端可运行）— VERIFIED

| 环节 | 实现 | 规模/状态 |
|---|---|---|
| 数据获取 | `scripts/download_data.py`：Overture Maps Base/water，STAC + DuckDB httpfs 通道（D13），sha256 校验、幂等不覆盖 | 真实 6,543 行（威斯康星湖泊区 bbox=-90,44,-89,45，release 2026-10-21.0） |
| 清洗与标准化 | `earthglyph/geometry/`（projection / extraction / filtering / resampling / normalization）：局部 aeqd 投影 → 两级过滤 → 去重 → 弧长重采样 128 点 → 归一化；拒绝逐条记账不静默丢弃 | 839 个可检索轮廓（pond 472 / lake 312 / reservoir 55）；每个候选可经 `candidate_id → source_id + part_index` 回查真实经纬度 |
| 特征与索引 | `earthglyph/features/`：16 谐波 Fourier 描述子（D24–D26）+ 4 辅助几何特征（compactness / solidity / aspect_ratio / diameter_fill，D27），共 20 维粗筛向量 + 128 点精排轮廓；`scripts/build_index.py` | `data/index/` 839 行 / 0 跳过；两次构建字节一致；特征组独立可消融，无未验证融合权重 |
| 手绘抽象提取 | `earthglyph/geometry/sketch.py`：矢量轨迹 JSON / 单轮廓黑白 PNG → 标准轮廓；22 项阈值全部相对轨迹自身 RMS 尺度（D35）；歧义输入（多分量 / 笔画带 / 开放 / 自交）显式拒绝不猜测 | 图库成员与用户查询走同一条 `contour_from_points → 128 点` 管线 |
| 两级检索 | `earthglyph/retrieval/search.py`：`search_by_contour` / `search_by_image` = Fourier 粗筛（窗口 200）+ 闭式旋转/循环对齐精排（一次逆 FFT，D34）+ Chamfer 窗口复核（D38）；默认禁镜像（D33）、`max_per_source=1` 去重、弱命中显式告警（阈值 0.12，D42） | 冻结默认：`fourier_align / top_k=10 / shortlist=200 / smoothing=light / allow_mirror=False`；839 候选全量 L2 粗筛扫描 ~1.7e-5 s |
| 调试工具 | `tools/sketch_pad.html`（独立本地画板，导出时翻转 y 轴）+ `scripts/query_sketch.py`（JSON/PNG → Top-10 + 出图） | 非产品界面；2026-11-18 浏览器实跑冒烟通过：模拟一笔闭合轨迹 → 导出 JSON → 采集器结构校验 PASS → 生产提取 PASS（128×2、全有限、RMS 半径 1.0） |

### B. 研究资产（不进生产默认，全部有冻结协议与产物）— VERIFIED（研究口径）

- **三条对照臂**（P0-4.5 Checkpoint B）：E1 ShapeComp-inspired（91 维、5 组可消融，**明确标注非官方实现**，D46）、
  E2 显著结构匹配（带符号转角尺度空间 + DP 对齐）、E3 Shape Context（纯 numpy 自实现）；
  与 E0 共用同一评分契约 `earthglyph/retrieval/perceptual_methods.py`。
- **官方 ShapeComp 神经近似 Python 端口**（P0-4.6 CP5–CP7）：KerNet1/KerNet2，h5py + numpy 逐字前向，
  无 TensorFlow / 无重训练；KerNet2 对 H2 发布坐标成对 Spearman **0.9989**（近乎机器精度），
  KerNet1 只钉住输入次序、网络级忠实度未钉到机器精度（D57，如实标注）。
  官方工具箱与 `.h5` 权重 gitignored、不入库（无再分发许可，D60）。
- **旋转方案对比**（CP6–CP8）：naive / R0 查询侧旋转搜索 / R1 细网格 / R2 朝向规范化 / R3 多姿态，
  冻结设计 + 独立新查询验证；结论见第三节。
- **两套受控基准**：P0-4.5 感知图库（179 成员 / 18 查询 / 7 结构类，许可逐资产哈希入库）；
  P0-4.6 独立基准（官方 H1：250 对 × 14 名被试人类评分，500 轮廓唯一；MPEG-7 1,398 形状 / 70 类）。

### C. 评测与采集基础设施 — VERIFIED（管线）/ BLOCKED（真人数据）

- P0-5 合成扰动管线：设计先冻结（`7b60ac8`）再运行；汇总数字由 `recompute_p05_metrics.py` 从 CSV 逐位复算 PASS。
- 盲评材料生成器（P0-4.5 D1）：臂名隐藏、固定种子逐组置换、不透明编码、身份映射本地密钥分离。
- **P0-6 真人采集管线就绪**（`231d19c` + 修复轮 `d0adc97`，D92）：
  `scripts/collect_p06_queries.py`（登记 + 结构校验 + sha256 去重 + 匿名化）、
  `scripts/validate_p06_dataset.py`（预检门禁）、
  `scripts/make_p06_judgement_sheets.py`（强制先过门禁；每评委独立固定种子打乱盲评单）。
  2026-11-18 实跑复核：门禁对空 sessions 正确拒绝（exit 1，`NOT READY`）；**尚无任何真人数据**。
- Web 前端仅有测试脚手架（vitest 链路），**无产品**：P0 门禁未过，按 AGENTS.md 不启动大规模 Web 开发。

---

## 二、水平如何衡量：四层证据，严格分开

| 层 | 衡量什么 | 工具/协议 | 现状 |
|---|---|---|---|
| 1 代码正确性 | 实现是否符合声明 | 1042 项单测（不变性 / 异常注入 / 契约 / 防写盘器漂移）+ ruff + mypy strict + 逐字节确定性复现 | VERIFIED |
| 2 受控合成检索 | 已知扰动下能否找回真值 | P0-5：118 真实轮廓 × 31 变体 = 3,658 查询，生产检索器零改动；源聚类 bootstrap CI；独立复算 | VERIFIED |
| 3 人类感知相关性 | 距离排序是否追踪人类相似度判断 | P0-4.6 D1：官方 H1 250 对 × 14 被试评分为真值，Spearman 相关 + 两级 bootstrap；另有 `AI_visual_review`（模型评图，**明确标注不是真人**） | VERIFIED（H1 官方数据）；新评分 0 份 |
| 4 真人手绘 + 人工判读 | 真实用户能否在 Top-10 找到肉眼相似地物 | P0-6 协议（冻结）：≥10 人 × ≥6 幅最终版，抽屉自评 + ≥2 名独立盲评 | **BLOCKED：0 份真人数据** |

**口径纪律**（协议与决策记录明文规定）：

- 合成扰动命中率 ≠ 真人命中率，永远分开报告；
- 类别召回 / 结构角色 ≠ 人类相似度真值；
- 无人工评分时，任何内部指标不得称"人眼匹配准确率"；
- 感知最优与旋转鲁棒最优是两个不同目标，不得合并成单一"最优方法"（D54 / CP7）。

---

## 三、实测结果

### 3.1 生产管线（E0，冻结默认）

| 指标 | 数值 | 口径 / 出处 |
|---|---|---|
| 严格不变性（自匹配 / 旋转 / 缩放 / 平移 / 走向反转） | Top-1 **1.000** | P0-4 G1 + P0-5 G1（`experiments/p0_perturbation_sweep/`） |
| 合成扰动 Top-1 | **0.7887** [0.7698, 0.8078] | P0-5 全集 3,658 查询，源聚类 bootstrap |
| 合成扰动 Top-10 / MRR | 0.8384 / 0.8035 | 同上 |
| 精排相对纯粗排增益 | **+5.6 pp** [+3.4, +7.9] | 配对比较 |
| 失败归因 | A 提取拒绝 294 / D 库覆盖 296 / **B 粗筛 1 / C 精排 0** | 重排与去重从未弄丢真值 |
| 镜像 | mirror 族 0.1017（禁镜像政策）；allowed 口径 1.000 | 政策所致，非实现缺陷 |
| 最难扰动 | aspect-1.4 → 0.288；gap-0.35 → 0.110 | G2 形变档 |
| 枝状水库 row 294 | N=128/256/512 秩完全不变（24 miss 持续，IoU 0.81→0.94） | B4：**不是采样保真问题**，全局升点否决（D88） |
| 粗筛成本 | 839 候选全量 L2 扫描 ~1.7e-5 s | 本机实测 |

真实检索示例（P0-4，真实索引 `77b083c9`）：月牙手绘轨迹 → 5.7 ha 弯月状池塘
（−89.89456, 44.39991，D=0.150）；心形 → 带凹口湖泊（−89.15608, 44.33571，D=0.106）。
失败集中在提取拒绝（A）与库覆盖（D），粗筛/精排窗口当轮 B=C=0。
注：P0-4 的"类手绘"查询是机器指针轨迹，**不是真人手绘**。

### 3.2 研究臂对照（不是生产结论）

**人类评分相关性**（H1，250 对；前提：姿态已对齐）：

```
S0 官方发布坐标 0.911 > E3 0.839 > E1 0.782 > E0 0.683
```

E3 接近人类留一噪声上限 0.779；配对 bootstrap 下每个 EarthGlyph 臂对每个网络臂差全排除 0。
出处：`experiments/shape_perception_benchmark/results/`（D1 / CP5）。

**旋转鲁棒检索**（项目主判据，D54）：

- E0 / E1 严格不变（21/21 自匹配 rank 1）；E3 同档但 15° 网格离格损失（37°/191° overlap 减半）；
- 官方网络 naive 几乎全灭（S0K1 1/21、S0K2 4/21），加旋转包装后 20/21、19/21；
- 代价：旋转搜索取 min 把 φ=0 峰值一致性从 0.903/0.900 压到 0.763/0.775 ——
  **感知领先属于"姿态已对齐"前提，从未承诺旋转工况**（CP6）。

**CP8 独立验证**（24 个从未参与设计与调参的新查询，六臂 × 120 旋转格）：
同类 Top-1 0.825–0.858（区间重叠）、Top-1 稳定 1.000 vs 0.48–0.55、Precision@10 0.538–0.745、
成本 0.008–56.1 s/搜索 —— 四轴互相矛盾，无单一赢家 → **不改生产默认检索器**。
出处：`experiments/shape_perception_benchmark/checkpoint8/`。

**成本对照**（研究口径，本机墙钟）：E0 ~0.016 s/查询（177 行库）vs E3/sample ~56 s/搜索（1,398 行库），
差约 3 个数量级；E1 描述子缓存命中 0.03 s / 未命中 ~11 s（D87，跨表比较须同缓存状态）。

### 3.3 工程质量

- 测试：**1042 passed**（本日实跑，0 failed 0 skipped）；ruff 全过；mypy strict 41 源文件无问题。
- 确定性：索引两次构建字节一致；P0-4 检索输出除 timings 外逐字节可复现；
  P0-5 / CP8 产物带输入 sha256 + 设计 content_sha256 + git commit 溯源。
- 数据卫生：原始数据与生成索引不入库（manifest + sha256 + 复现命令版本化）；
  官方权重 gitignored；参与者密钥表 gitignored（D92 匿名化修复）。

---

## 四、总体判定

### 能主张（VERIFIED）

1. 一条从真实地理数据到"手绘 → 带真实经纬度与 candidate_id 的 Top-10 相似地物"的完整管线已存在，
   独立于 Web 前端运行。
2. 正确性由 1042 项测试与逐字节复现钉住；合成扰动下 Top-1 ≈ 0.79，
   排序环节（粗筛 + 精排）在 3,658 查询中只丢 1 个真值。
3. 算法选型研究（含官方 ShapeComp 端口与旋转包装）已按冻结协议收官，
   结论明确：**不换生产默认**；E0 的感知相关性弱于 E3 是已测事实，但 E3 的成本与旋转离格损失同测在案。
4. P0-6 真人采集管线就绪并经实跑复核（门禁正确拒绝空数据、画板→提取冒烟通过）。

### 不能主张

- **真人手绘命中率**：0 份真人数据（门禁要求 ≥10 个不同用户）；
- **"人眼匹配准确率"**：真人盲评 0 份（`AI_visual_review` 不是真人实验）；
- **产品体验**：无 Web 产品，未做用户体验验收；
- **跨库泛化**：候选库仅单 1°×1° 区域；星形 / 枝状类查询存在覆盖缺口（D 类失败主因）；
- 合成 0.79 不得说成产品准确率（P0-5 notes 四类证据切分）。

### 门禁状态与下一步

**P0 门禁未通过。** 唯一剩余入口是 P0-6 真人采集（`experiments/p0_6_human_queries/PROTOCOL.md`，已冻结）：

1. ≥10 名可区分参与者 × ≥6 幅最终版（题目绘制前固定；重画全保留、只评最终版）；
2. `collect_p06_queries.py` 登记 → `validate_p06_dataset.py` 预检 → `make_p06_judgement_sheets.py` 盲评单；
3. 抽屉自评 + ≥2 名独立盲评（每评委固定种子打乱）；
4. **主判据（Go/No-Go）：抽屉自评 Top-10 含 `similar` 的查询占比 > 50%（严格多数）**；
   同时报告独立盲评占比与一致率；No-Go 按 EXPERIMENTS.md §6 处置。

在拿到真实手绘与人工判定前，任何合成数字不得替代这一步；P0 未过前不启动大规模 Web 开发。

---

## 附录：证据出处与复现命令

| 结论 | 产物 | 复现 |
|---|---|---|
| 工程基线 | 本仓 HEAD `729849c` | `pytest -q -o addopts=""` · `ruff check .` · `mypy` |
| 数据与索引 | `data/processed/data_manifest.json`、`data/index/data_manifest.json`、`docs/DATA_PIPELINE.md` §10 | `scripts/download_data.py` → `scripts/prepare_polygons.py` → `scripts/build_index.py` |
| P0-4 检索证据 | `experiments/p0_retrieval_baseline/`（results.csv 241 行 + 21 图 + notes.md） | `scripts/evaluate_retrieval.py --seed 20261105`（命令全文见 notes §2） |
| P0-5 扰动曲线 | `experiments/p0_perturbation_sweep/`（notes.md + CSV） | 设计冻结 → 运行 → `recompute_p05_metrics.py` 独立复算（见 notes） |
| 感知四臂对照 | `experiments/p0_perceptual_probe/`（含 checkpoint_d/） | notes §13.9 三条命令；POLICY 数字重跑 `verify_policy_claims.py` |
| ShapeComp 基准 | `experiments/shape_perception_benchmark/`（results/ + checkpoint7/ + checkpoint8/） | `run_shape_benchmark.py --stage d1…d6 --methods E0 E1 E3 S0K1 S0K2`（需本地官方工具箱）；`--stage d5b / d6-correct` 为只读再分析 |
| P0-6 就绪 | `experiments/p0_6_human_queries/PROTOCOL.md` + templates/ | `python scripts/validate_p06_dataset.py --sessions-root experiments/p0_6_human_queries/sessions`（当前预期 exit 1：无数据） |

> 本文档为只读汇总，不改变任何冻结协议、实验产物或生产默认参数。
> 数字如有更新，以 `docs/TASKS.md` 与对应实验 notes 为准。