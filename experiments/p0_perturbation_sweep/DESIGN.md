# P0-5 — 合成扰动分级曲线（设计冻结，先于任何检索执行）

- 状态：**设计已冻结**。本文档与 `samples/` 在任何检索运行之前写入并提交；
  之后不得根据结果改样本、改阈值、改强度参数或增删查询族。
- 冻结于 commit：见 `samples/selection_report.json` 的 `git_head_at_generation`
  与本文档提交的 sha（提交信息注明 "design freeze"）。
- 索引输入：`data/index`（839 候选 / 128 点 / 20 维），manifest `git_commit = 77b083c9…`
  （与 `DATA_PIPELINE.md §10.1` 一致，未重建）。
- 上游约束：`docs/TASKS.md`「下一步：P0-5…」— 把 G1/G2 从 6 候选扩到 100–200 真实轮廓，
  报告扰动强度曲线，定量回答 row-294 类枝状候选失败是 N=128 保真问题还是覆盖问题；
  **不提前全局升 256 点**（升采样只在 B4 的独立对照里做，不进生产索引）。

## 1. 样本（B1，已冻结，数据仅来源于索引本身）

生成器：`scripts/make_p05_samples.py`（seed 20261111）→
`samples/sample_sources.csv` + `samples/selection_report.json`。
脚本只读 `metadata.parquet` + `shapes.npy`，不读任何查询、不跑任何检索。

规则（冻结）：

1. 每个 `source_id` 取 `area_m2` 最大的 part（并列取 candidate_id 字典序最小者）。
   声明式例外：源 `4b2c4aea-…` 以 row 294（part 1）为代表 —— P0-4 已把该轮廓记录为
   枝状水库失败案例，扫描必须包含它；这是实验日志里的先验，不是结果驱动的挑选。
2. 六个形状族按固定顺序判定（指标全部在代表轮廓上实测，阈值冻结）：

   | 族 | 判据（先命中先归属） | 池大小 | 抽取 | 最终 |
   |---|---|---|---|---|
   | near_circular | compactness ≥ 0.85 | 113 | 20 | 20 |
   | elongated | 轴比 √(λ1/λ2) ≥ 3.0 | 114 | 20 | 20 |
   | forked | solidity ≤ 0.70 且 compactness ≤ 0.15 | 18 | 18 | 18 |
   | deep_concave | solidity ≤ 0.70 | 64 | 20 | 20 |
   | irregular | 细节带能量占比 ≥ 代表集 P90 = 0.00097131 | 20 | 20 | 20 |
   | compact | 其余 | 502 | 20 | 20 |

   forked 池只有 18 个源，**全取**（缺口 2 如实报告，不从其他族补）。
3. 族内抽样：池按 candidate_id 排序后 `default_rng(20261111).permutation` 取前 20。
4. 近重复控制：样本两两 align（不允许镜像），align < 0.05 视为近重复，
   丢弃 candidate_id 字典序靠后者，从同族池按 candidate_id 序补第一个未用者，复检，
   共 7 轮、丢 6 对（全部发生在 near_circular，round-pond 互相像），最终补满。

**有效样本量：118 个不同 source_id（0 重复）；near_circular/elongated/deep_concave/
irregular/compact 各 20，forked 18。** row 294 ∈ forked ✓；P0-4 探针行 7/61/100/365
也在样本中（745 不在，随机结果，不强行加入）。类别构成：pond 52 / lake 36 /
reservoir 19 / lagoon 10 / basin 1。

## 2. 查询（B2）— 118 源 × 31 变体 = 3658 条

每条查询 = 对该源存储的 128 点轮廓施加固定扰动后，以 P0-4 相同的 trace JSON 格式
（点列 round 3 位、closed=true、unit=canvas、y_axis 向上）写出。
涉及随机性的变体（jag / uneven / gap 的 pen 采样）每个查询用
`default_rng(20261111·1000 + source_row·100 + variant_index)`，
seed 写进 manifest 的 `params`，完全可复现。**这些是算法合成的扰动输入，不是真人手绘**；
manifest 每条都标注 `family="probe"`。

| 层 | 族 | 参数（冻结） | 每源条数 |
|---|---|---|---|
| G1 正确性 | self / rot / scale / shift / reverse / mirror | rot 37°、scale ×3.7、shift (+40,−25)（与 P0-4 同值，可直接对照）；mirror 同时在 allow_mirror=False 与 True 下评测，分开报告，不合并 | 6 |
| G1 正确性 | sparse | 重采样 64 / 32 / 16 点（P0-4 只测了 26；这里成曲线） | 3 |
| G2 鲁棒性 | jag（径向抖动） | amp 0.02 / 0.05 / 0.10（P0-4 档位） | 3 |
| G2 鲁棒性 | uneven（笔步不均，无抖动） | σ 0.25 / 0.55 / 0.90（0.55 = P0-4 默认） | 3 |
| G2 鲁棒性 | gap（收笔缺口，share 为尺度占比；ramp>0 为拖尾） | (0.06,0) / (0.06,0.15) / (0.20,0.15) / (0.35,0.15) | 4 |
| G2 鲁棒性 | island（外凸鼓包） | height 0.10 / 0.18 / 0.30 | 3 |
| G2 鲁棒性 | bay（内凹缺口） | height 0.10 / 0.18 / 0.30 | 3 |
| G2 鲁棒性 | finger（细枝状外凸：width 0.15） | height 0.30 / 0.45 | 2 |
| G2 鲁棒性 | notch（深窄切口：width 0.12, height 0.45） | — | 1 |
| G2 鲁棒性 | aspect（非均匀缩放） | factor 1.15 / 1.25 / 1.40 | 3 |

查询有效性拒绝（如自相交、点数不足）照常走 `contour_from_sketch` 的拒绝路径，
拒绝原因逐条记入结果（refusal 列），拒绝率按族报告。

## 3. 评测臂（B3）— 同一索引、同一查询集

全部经 `earthglyph.retrieval.search.search_by_contour`（生产入口，不改算法、不改默认参数，
smoothing="light"，shortlist=200，top_k=10，max_per_source=1）：

| 臂 | 定义 | 运行范围（冻结） |
|---|---|---|
| P0 coarse-only | `method="fourier"` | 全部 3658 条 |
| P1 production | `method="fourier_align"`（生产默认） | 全部 3658 条 |
| P2 全库诊断 | `method="align", top_k=839, max_per_source=839` | **仅** P1 未把 truth 送进 top-10 的查询 + 按 manifest 顺序每 5 条取 1 的冻结子样本（给出无偏的全库 rank 分布）。这一范围规则在跑之前声明，属于归因规程，不属于挑样本 |
| P3 Chamfer 复核 | `sort_by="chamfer", chamfer_top=10` | 仅 P1 归因为 D 类的查询 |
| ShapeComp 臂 | KerNet2 嵌入对 839 库嵌入排序 | 仅每 20 条取 1 的冻结子样本（≈183 条），单独成表，绝不并入 P0–P3 的指标 |

E1/E3 臂**不在本实验**：它们属于 P0-4.5C 的感知比较管线（自带 gallery/划分），
在本索引上重跑会把两种证据混在一起。

## 4. 指标（按 族 × 扰动 × 强度 分层，绝不止报总平均）

- 提取拒绝率（refusal / n）。
- Top-1、Top-10（truth 是否在返回列表）、MRR（truth 排名倒数，不在则 0）、
  truth 排名的中位数与 P90。
- `rank_coarse`（truth 的全库 coarse 位置，每条都算，廉价）、
  `rank_full`（truth 的全库 align 位置，P2 运行范围内）。
- 归因标签计数（复用 `scripts/evaluate_retrieval.py::attribute` 的四类 +
  A_extraction：ok / C_dedup_or_truncation / B_coarse_filter / C_rerank_order /
  D_coverage_or_deformation / A_extraction），无法归因的行明确标注。
- 时延：`SearchResult.timings` 的 prepare / coarse / rerank / total，报中位数；
  冷/热差异通过同进程重复第二遍全量运行测得，两遍分别落盘。

## 5. 统计可靠性（C1 输入）

- **源聚类 bootstrap**：扰动是同一源的相关样本，不是独立观测。
  按源重采样（B=1000，seed 20261111），对每族 × 每强度的 Top-1 / Top-10 / MRR
  给 95% CI；总体指标同样按源聚类。
- **配对差**：P1 − P0 同查询配对，ΔTop-1、ΔMRR 用同一 bootstrap 给 CI。
- 指标由 `results/results.csv` 独立重算复核（脚本另给 `--recompute` 路径），
  summary.json 里的数字必须能从 results.csv 复算出来。

## 6. 图（肉眼可查）

- `figures/strength_curves.png`：Top-1 / Top-10 / MRR 随强度的曲线（分族，带 bootstrap 区间）。
- `figures/failure_overlays_<class>.png`：每类归因各抽 ≥3 例，
  查询轮廓 vs truth 轮廓 vs 实际返回第 1 名的三联图（复用 `visualize_retrieval.draw_pair`）。
- 图只能由 results.csv / summary.json 生成，不手抄数字。

## 7. 运行命令（冻结）

```bash
python scripts/make_p05_queries.py --index data/index \
    --out experiments/p0_perturbation_sweep/queries --seed 20261111
python scripts/evaluate_p05.py --index data/index \
    --queries experiments/p0_perturbation_sweep/queries \
    --out-dir experiments/p0_perturbation_sweep/results
```

## 8. 本轮不能宣称（边界）

- 合成扰动的检索成功率 ≠ 真人手绘命中率（P0-6 才测人）；
  ≠ 产品 UX；≠ 三 baseline 谁更符合肉眼。
- mirror 族在 allow_mirror=False（生产默认）下的低命中率是**策略**结果，
  与 True 档分开报告，不作为"检索失败"计入 D 类。
- B4 的升点对照只回答"row-294 类失败是否 N=128 保真问题"，
  不构成改生产索引采样数的依据（改默认须另行回归验证 + 授权）。
- ShapeComp 臂分数只与同臂内排名可比，不与 P0–P3 的 align 距离可比。
