# P0-5 扰动扫描 — 运行报告（B3）与指标审计（C1/C2）

设计冻结于 `DESIGN.md`（commit 7b60ac8，首次运行前仅修订过 P2 一行并已在该文件中记录）。
本文是唯一结论来源；所有数字可由保存的 CSV 独立复算（见 §C1）。

## 溯源（provenance）

| 项 | 值 |
| --- | --- |
| 样本设计 | `DESIGN.md`（frozen），118 源 × 31 变体 = 3658 查询（commit b303f45） |
| 查询集 | `queries/`（manifest 含逐查询 seed = seed×1000 + row×100 + variant） |
| 索引 | `data/index`，构建于 commit 77b083c9，shapes sha256 `fbaf4933…`，839 候选 × N=128，16 谐波 positive fold |
| 运行命令 | `python scripts/evaluate_p05.py --index data/index --queries experiments/p0_perturbation_sweep/queries --out-dir experiments/p0_perturbation_sweep/results` |
| Bootstrap | 源聚类（118 簇），B=1000，seed 20261111 |
| 运行日期 | 2026-11-11 |
| 产物 | `results/results.csv`（3658 行）、`results/shapecomp_arm.csv`（183 行）、`results/summary_by_strength.json`、`results/figures/*.png` |

## 总体指标（生产 fourier_align，top_k=10，shortlist=200，no-mirror）

| 指标 | 点估计 | 95% CI（源聚类） |
| --- | --- | --- |
| Top-1 | 0.7887 | [0.7698, 0.8078] |
| Top-10 | 0.8384 | [0.8245, 0.8532] |
| MRR | 0.8035 | [0.7856, 0.8204] |
| 配对 ΔTop-1（P1−P0） | **+0.0563** | [+0.0344, +0.0793] |

配对差在同样 3658 条查询上按源聚类重采样：**对齐重排比纯粗排显著更好**
（CI 不含 0）。NaN 秩 = 真值不在返回列表 = 失败，从不 dropna；拒绝同样留在分母。

## 分强度结果（每格 n=118，NaN/拒绝均计入分母）

### G1 不变性 / 稀疏（Top-1 / Top-10 / 拒绝数）

| 变体 | P0 粗排 Top-1 | P1 Top-1 | P1 Top-10 | 拒绝 | 备注 |
| --- | --- | --- | --- | --- | --- |
| rot-37° | 0.9831 | **1.000** | 1.000 | 0 | 任意角度旋转不变 |
| scale-3.7× | 0.9831 | **1.000** | 1.000 | 0 | 平移+等比缩放不变 |
| shift / self / reverse | 0.9831 | **1.000** | 1.000 | 0 | 严格不变性全部通过 |
| **mirror** | 0.9831 | **0.1017** | 0.3559 | 0 | 见 §镜像政策；mirror-allowed Top-1 = **1.000** |
| sparse-16 | 0.0169 | 0.0508 | 0.0932 | **102** | 失败主体是提取拒绝，不是排序失败（见下） |
| sparse-32 | 0.6949 | 0.8983 | 0.9492 | 0 | |
| sparse-64 | 0.8729 | 0.9831 | 0.9915 | 0 | |

**sparse-16 的正确读法**：118 条里 102 条在提取阶段被拒（闭合缺口超限，16 点折线
无法闭合），这是**提取契约的极限**，不是检索算法失败；其余 16 条 Top-1 仅 2/16
（粗排 0.0169 → 全排 0.0508），其中 1 条是全库唯一的 B 类（见 §归因）。
**sparse-32 起排序恢复到 0.90** —— 生产契约下用户笔迹 ≥32 点即可用。

### G2 形变强度曲线（Top-1，Top-10）

| 层 | 强度梯度 → Top-1 | 强度梯度 → Top-10 |
| --- | --- | --- |
| aspect（非等比缩放） | 1.15: 0.686 → 1.25: 0.466 → **1.4: 0.288** | 0.771 → 0.619 → 0.398 |
| gap（缺口/断笔） | 0.06: 0.958 → 0.2+tail: 0.619 → **0.35+tail: 0.110** | 0.966 → 0.644 → 0.127 |
| island（挖洞） | 0.1: 1.000 → 0.18: 0.958 → 0.3: 0.814 | 1.000 → 1.000 → 0.949 |
| bay（海湾） | 0.1: 1.000 → 0.18: 0.958 → 0.3: 0.797 | 1.000 → 1.000 → 0.958 |
| finger（凸出枝） | 0.3: 0.839 → 0.45: 0.763 | 0.983 → 0.822 |
| jag（锯齿噪声） | 0.02: 0.941 → 0.05: 0.915 → 0.1: 0.864 | 0.949 → 0.932 → 0.881 |
| uneven（不均匀采样） | 0.25: 0.983 → 0.55: 0.941 → 0.9: 0.881 | 0.992 → 0.949 → 0.907 |
| notch（结构缺口） | 0.45: 0.695 | 0.797 |

最难的两族：**aspect**（非等比缩放，Fourier 粗排与 align 都按形变退化，1.4× 时
Top-1 只剩 0.288）和 **gap 大缺口**（0.35 时 102/118 被提取拒绝，存活者排序也崩）。
轻中度形变（island/bay/jag/uneven 全梯度、gap≤0.18）Top-1 ≥ 0.79。

### 分形状族（含 CI，见 `summary_by_strength.json → bootstrap_by_family`）

| 族 | n | Top-1 [CI] | Top-10 |
| --- | --- | --- | --- |
| deep_concave | 620 | 0.8661 [0.848, 0.884] | 0.8903 |
| irregular | 620 | 0.8339 [0.808, 0.857] | 0.8581 |
| forked | 558 | 0.8315 [0.772, 0.884] | 0.8763 |
| elongated | 620 | 0.8274 [0.800, 0.850] | 0.8710 |
| compact | 620 | 0.7500 [0.716, 0.784] | 0.8097 |
| **near_circular** | 620 | **0.6274 [0.615, 0.639]** | 0.7290 |

near_circular 显著最弱（D 类 113/620）：圆形候选之间彼此相近，形变把真值挤出
Top-10 的机理与 P0-4 的结论一致。forked 不再是最差族（P0-4 单点印象被 558 条
修正：0.8315，优于 compact）。

## 镜像政策（生产 allow_mirror=False 是显式决策）

- 生产口径（no-mirror）：Top-1 0.1017，Top-10 0.3559 —— 镜像查询按设计**不算命中**。
- 诊断口径（allow_mirror=True）：Top-1 **1.000**，Top-10 **1.000** —— 算法能力没问题，
  是政策关闭了它。粗排 P0 Top-1 = 0.9831 说明 |F_k| 幅值本身镜像不敏感，区分发生在
  align 重排。
- 竞品需求若要求"镜像也算同一形状"，把 `allow_mirror=True` 打开即得 1.000；
  这是从 `results.csv` 的 `mirror_allowed_rank` 列直接读出的实测，不是推测。

## 失败归因（3658 条，四类 + 拒绝）

| 类 | 条数 | 占比 | 含义 |
| --- | --- | --- | --- |
| ok | 3067 | 83.8% | 命中 |
| A_extraction | 294 | 8.0% | 提取拒绝（闭合缺口/自交），从未进入检索 |
| D_coverage_or_deformation | 296 | 8.1% | 粗排与全库对齐都找不到（形变超出度量） |
| B_coarse_filter | **1** | 0.03% | 全库对齐能找到但粗排 shortlist 丢弃 |
| C_rerank_order / C_dedup | **0** | 0% | 重排序与去重从未弄丢真值 |

- **B 类唯一一例**：`sparse-16-r221` —— 粗排把真值排在第 297（shortlist=200 之外），
  全库 align 第 2。16 点折线的 Fourier 描述子偏差过大；与 sparse-16 层的其它发现
  一致（提取契约极限），不是 shortlist=200 的系统性问题（1/3658）。
- **C 类全零**：`rank_pre_dedup` 与 `rank_final` 从未不同、真值从未在 shortlist 内
  被重排挤出 Top-10 —— 直接回答 `docs/TASKS.md` 的问题：**row 294 类失败与去重/
  截断无关**；配合 B4（`experiments/p0_sampling_fidelity_294/notes.md`）：
  也不是 N=128 保真问题（覆盖 top10_p0=0.939），瓶颈在查询侧提取平滑 + align
  度量在枝状形状上的饱和。
- 拒绝按原因全表可由 `results.csv` 的 `refusal` 列复算；大头：sparse-16 102、
  gap-0.35-tail0.15 102、gap-0.2-tail0.15 41。

## 耗时（同一进程先冷后热两遍）

| 量 | 值 |
| --- | --- |
| 冷启动中位 total | 9.51 ms/查询 |
| 热启动中位 total | **3.71 ms/查询** |
| 冷启动 P90 | 10.89 ms |
| 首条查询 | 33.5 ms（一次性进程开销） |
| 冷热差来源 | `prepare` 阶段缓存（逐层 timings 存于两列） |

分强度中位耗时 3.9–9.9 ms（aspect 族查询较短故更快）；**全部远低于任何交互阈值**。
区分：冷 = 进程内首遍含准备；热 = 索引与特征已驻留；索引构建是一次性离线成本
（`data/index` manifest 记录），不计入查询时延。

## ShapeComp 独立臂（每 20 条抽 1，n=183）

| 量 | 值 |
| --- | --- |
| 拒绝 | 43（与主臂同源，提取拒绝） |
| Top-1 / Top-10 / MRR | 0.7049 / 0.765 / 0.7268（分母含拒绝） |
| 距离 | KerNet1+KerNet2 22-D 嵌入平均余弦距离，**只在臂内可比** |

对照主臂同口径（top1 0.79）：官方 ShapeComp 嵌入在本库上略弱但同量级；
两套距离**从未合并**（DESIGN.md 冻结条款）。`shapecomp_arm.csv` 保留逐行秩与距离。

## C1 — 指标独立复算（审计）

`scripts/recompute_p05_metrics.py` 只读 `results.csv` + `shapecomp_arm.csv`，
不 import 任何检索代码，重新实现全部聚合与源聚类 bootstrap（同 seed、同抽样法）：

```
PASS: every recomputed metric matches summary_by_strength.json (tol 5e-4);
bootstrap draws reproduced bit-for-bit from the same seed.
```

**复算抓到并修复了一个汇总侧 bug**（发生在首次提交前）：
`evaluate_p05._cell` 的 `top1_p0` 分子只在"最终秩已找到"的行上取值，而 76 条
mirror 的 D 类行有合法粗排秩 1 —— 镜像层 P0 Top-1 被低估为 0.3559（正确
0.9831）；同型影响 sparse-64（0.8644→0.8729）、aspect-1.15（0.5847→0.6102）、
aspect-1.25（0.3136→0.3559）及全部分族 `top1_p0`。**逐行 `results.csv` 一直是对的**；
修复 `ranks_p0` 取全集后用 `evaluate_p05.py --aggregate-only` 从 CSV 重新生成汇总，
未重跑任何检索。配对差、总体 Top-1/10/MRR、bootstrap、ShapeComp 臂不受影响。

## C2 — 证据类型切分（本报告支持什么、不支持什么）

| 断言 | 本轮证据 | 状态 |
| --- | --- | --- |
| 代码/数学正确性（不变性、对齐、复算） | G1 严格不变全 1.000；C1 复算 PASS；A3 金向量测试 | ✅ 已证 |
| 受控合成扰动下的检索成功 | 全部 §分强度表 —— 仅在"合成扰动定义的成功"内成立 | ✅ 已证（限定范围） |
| 真实手绘相似度（人判定） | **无** —— 本轮 0 条真人查询 | ❌ 待 P0-6 |
| 产品 UX | **无** —— 未测界面、延迟感知、失败提示 | ❌ 不在 P0 范围 |

合成扰动的"正确答案"是程序设定的（扰动前的源轮廓）；真人画的轮廓分布不同
（更粗糙、闭合更差、比例失真更大）。**不得把 0.79 的合成 Top-1 说成"产品准确率"**。

## 不能声称的事（沿 DESIGN.md 冻结清单，运行后逐条核对）

1. 未证明对**任意**真实笔迹鲁棒 —— 样本是 31 种参数化扰动，不是人类分布。
2. 未证明 mirror 语义正确 —— 只是测出了政策的代价（0.10 vs 1.00）。
3. 未优化任何阈值 —— 全部用生产默认（top_k=10、shortlist=200、closure 0.15 等）；
   sparse-16/gap-0.35 的高拒绝率是测量结果，不是需要"调低阈值修掉"的缺陷。
4. ShapeComp 结果不构成与 align 的排名冲突 —— 不同度量空间，只在各自臂内可比。
5. 单次运行 —— bootstrap 给出抽样不确定性，但不是跨机器/跨版本的重复性证据。

## 检查产物指南（人眼核验）

| 文件 | 怎么读 |
| --- | --- |
| `figures/strength_curves.png` | 左：G1 各变体 P1 Top-1（蓝条）、P0 粗排 Top-1（橙短杠）、Top-10（灰虚线）—— mirror 与 sparse-16 两个缺口一目了然；中/右：G2 各层随强度的 Top-1 / Top-10 曲线，aspect（蓝）与 gap（红）掉得最狠 |
| `figures/failure_overlays.png` | 6 格真实叠加（红=查询，蓝=真值索引形状）：A 格红笔画开口未闭合；B 格 16 点折线完全丢掉枝杈；D-aspect 红框是被非等比拉伸的同一形状；D-mirror 是明显的镜像翻转；ok 格几乎重合（align 0.0044）。C 类两子类为 0 条，如实写进图注，不伪造 |
| `results.csv`（3658 行） | 每查询一行：四种秩 + 归因 + 拒绝原因 + 冷/热 timings；`rank_*` 为空 = 真值不在返回列表 = 失败 |
| `summary_by_strength.json` | 全部聚合 + bootstrap CI；可由 `scripts/recompute_p05_metrics.py` 独立复算验证 |

## 复现

```bash
python scripts/evaluate_p05.py --aggregate-only        # 从 CSV 重出汇总（不重跑检索）
python scripts/recompute_p05_metrics.py                # 独立复算并 diff 汇总
python scripts/p05_figures.py                          # 重画两张检查图
# 完整重跑（约 40 min）：
python scripts/evaluate_p05.py --index data/index \
    --queries experiments/p0_perturbation_sweep/queries \
    --out-dir experiments/p0_perturbation_sweep/results
```
