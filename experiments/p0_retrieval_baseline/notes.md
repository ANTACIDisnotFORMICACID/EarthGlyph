# p0_retrieval_baseline — P0-4 手绘图形抽象提取与真实地貌轮廓检索

> **记录类型：本项目第一个检索实验。** 有真值对（probe 来自库内真实轮廓）、有 Top-K 命中数字、
> 有真实候选的经纬度与 id，因此按 `docs/EXPERIMENTS.md` §3 写入 `results.csv`。
> 三层评价**不得互换指标**：G1/G2 是**合成扰动**（证明实现正确性），G3 是**机器指针轨迹**
> （证明管线可跑通），二者都**不是**人类手绘效果 —— 真人查询集属 P0-6。

## 0. 目标（一句话）

从一个不精确的手绘图形中提取主要形状特征，并在这个 839 个真实湖泊 / 池塘 / 水库候选库里
找到肉眼相似的地貌：本轮要回答"提取 → 抽象 → 粗筛 → 精排 → 去重"全管线的行为、
失败发生在哪一层，以及三种 baseline 在结果图上的差异。

## 1. 数据与版本

| 项 | 值 |
|----|----|
| 候选库 | Overture Maps Base theme / water，release **2026-10-21.0**，bbox `-90,44,-89,45` |
| 索引 | `data/index/`，**839 候选 × 128 点 × 20 维**，`n_harmonics=16`，fold `positive` |
| 索引构建时间 | `2026-11-04T21:27:55+00:00` |
| 索引对应代码 commit | `77b083c931b72cf4fd153a9185254f5a9486aeab`（P0-3 检查点） |
| 索引产物 sha256 | features `b7255ede…62e063` / shapes `fbaf4933…0a2776` / metadata `e18c3f95…59a211`（全值见 `summary.json → params.index`） |
| 查询集 | `scripts/make_retrieval_queries.py --seed 20261105` → **115 个轨迹 JSON + 6 个 PNG** |
| 运行时 commit | 本检查点提交（sha 见 `docs/TASKS.md` 检查点表；索引 sha 不变） |
| 随机种子 | `20261105`（查询生成器唯一随机源：笔迹抖动 `as_trace`；评估与可视化无额外随机） |

查询集三条血统在文件内自述（`manifest` 每条记录带 `family` / `perturbation` / `source_row` /
`source_candidate_id`）：`probe` = 库内真实轮廓的受控形变（合成扰动，**不是手绘**）；
`sketch` = 程序按指针采样方式生成的轨迹（不均匀步长 + 笔抖 + 端点缺口，**不是人类手绘**）；
`image` = 上述 archetypes 栅格化的黑底白字 PNG，外加一张两个图形的歧义页。
`tools/sketch_pad.html` 导出的真实人手指轨迹与 `sketch` 走同一条代码路径、放同一目录，
**本轮没有人类手绘输入**（见 §12）。

## 2. 运行命令

```bash
# 1) 查询集（确定性；重跑与本轮文件逐字节一致）
python scripts/make_retrieval_queries.py --index data/index \
    --out experiments/p0_retrieval_baseline/queries --seed 20261105

# 2) 三层评估 + 全库平滑消融 + 短名单扫描 → results.csv / summary.json / 两张图
python scripts/evaluate_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline \
    --smoothing light --ablation --sweep 100,200,300

# 3) 三阶段提取对照图 + 每查询 Top-10 图 + 三 baseline 对比图
python scripts/visualize_retrieval.py --queries experiments/p0_retrieval_baseline/queries \
    --out-dir experiments/p0_retrieval_baseline --top 10 --smoothing light --figures-for 'drawn-*'

# 4) 单查询调试 CLI（Task E 工具链）
python scripts/query_sketch.py experiments/p0_retrieval_baseline/queries/drawn-crescent.png --top 10
python scripts/query_sketch.py experiments/p0_retrieval_baseline/queries/drawn-heart.json \
    --method fourier --shortlist 300 --allow-mirror --chamfer-top 10

# 5) 测试与静态检查
pytest              # 425 passed
ruff check .        # All checks passed
mypy                # Success: no issues found in 24 source files
```

统一入口：`earthglyph.retrieval.search.search_by_contour(points, index, *, top_k=10,
shortlist_size=200, method="fourier_align", allow_mirror=False, smoothing="light", …)`
与 `search_by_image(path, index, …)`；参数全量记录在 `summary.json → params` 与
`figures.json → params`。

## 3. Task A — 抽象提取的真实行为（121 个输入）

三阶段对照图 `extraction_examples.png`（22 行：10 个拒绝案例的原始笔画 + 12 个三阶段行
（原始轨迹 / 抽取外轮廓 / 128 点标准化轮廓）），
121 个输入中 **111 个接受、10 个报错拒绝**。拒绝全部是"不猜测用户意图"的显式报错，
不是静默修复：

| 输入 | 拒绝类别 | 报错内容（截取） |
|------|----------|------------------|
| `ambiguous-two-shapes.png` | 多分量 | `image contains 2 separate shapes (areas [2174, 1728], boxes …); V1 extracts a single closed outline` |
| `open-arc.png` | 笔画带非闭合区域 | `the ink is a 3.8-pixel band (width 0.034 of the shape scale) that fills only 5.7% of its own convex hull` |
| `drawn-irregular.json` | 开放曲线 | `open stroke: start/end gap is 0.15 of the shape scale (limit 0.15); redraw the outline closed` |
| `drawn-pentagram.json` | 结构性自交 | `self-intersecting stroke (1 crossing …): the crossing moves 30.7% of the hull in or out of the enclosed region` |
| `gap-r{61,100,745}-0.35{,-tail}.json`（6 个） | 开放曲线 | 端距 0.21 / 0.24 / 0.28 个尺度，超出各自 0.15–0.17 的上限 |

判据一律以轨迹自身 RMS 尺度为基准（D35），故画布大小 / 线宽 / 采样密度不改变判定：
以 `drawn-crescent.json` 为例，`n_raw=90 → n_outline=89`（末点被视为闭合弦而非几何顶点），
端距 `0.130` 尺度、该图上限 `0.184`，接受并写入 warning
`start/end gap of 0.130 of the shape scale sealed by a straight chord; that chord is an assumption, not something you drew`。
PNG 只取笔画中线，线宽 3/6/12 px 的轮廓偏差 < 0.05 尺度；y 轴向下在几何坐标处翻转；
抗锯齿不产生边界细节（以上均有单测）。

**本轮修掉的一个真实方法学缺陷**：`closed=true` 的轨迹末点按约定被丢弃（闭合边是隐含的，
不是画出来的），因此"只位移最后一个顶点"的闭合误差探针在**接受**分支上完全测不出东西 ——
`gap-r294-0.06` 与 `gap-r294-0.35` 提取出的 128 点轮廓曾逐点完全相同。修正方式是把位移
铺成一段尾部（`open_end(..., ramp=…)`，尾长 = max(周长的 15%, 6 个采样步)），
并加单测 `test_the_closing_vertex_is_a_chord_not_geometry` 钉住该语义。
`ramp` 早先按"尺度的 15%"定义，在枝状轮廓上小过一个采样步，于是退化成单顶点位移 ——
现按周长定义并设步数下限（`TAIL_MIN_STEPS = 6`），实测尾部跨 11–17 个采样点。

## 4. Task A3 — 平滑档位消融（全库 839 条，非目测）

`smoothing_ablation.png` + `summary.json → smoothing_ablation`。三档只差 σ（`none=0` /
`light=0.8` / `medium=2.0` 个采样步），循环零相位高斯低通，面积保持 ≤2e-3。

| 档位 | σ | 保留的 k=9..16 谱能量占比（均值） | 最差候选 | \|Δcompactness\| 均值 / 最大 | \|Δperimeter\| 均值 / 最大 |
|------|---|-----------------------------------|----------|------------------------------|----------------------------|
| none | 0 | 1.000 | 1.000 | 0 / 0 | 0 / 0 |
| light | 0.8 | **0.831** | 0.760 | 0.0148 / 0.0940 | 0.0206 / 0.2761 |
| medium | 2.0 | **0.337** | 0.197 | 0.0439 / 0.2356 | 0.0567 / 0.5128 |

即 `light` 削掉约 1/6 的细节频带能量、`medium` 削掉 2/3；`medium` 对个别候选能改动 51% 的周长
——"把真实凹口抹掉"是可测的，不是审美判断。

**排序结论（关键负面结果）**：G2 五个扰动族在 `none / light / medium` 三档下的
**Top-1 / Top-10 / 失败归类完全相同**（每档 45 个 ok 行中有 44 行落在第 1 名，唯一例外是
`bay` 族的一条，排名 5–8；每档探针数：`jag` 18、`gap` 24、其余各 6），差异只体现在对真值的对齐距离：

| 族 | none | light | medium |
|----|------|-------|--------|
| `jag`（对齐距离中位） | 0.0312 | 0.0315 | 0.0355 |
| `gap` | 0.0225 | 0.0268 | 0.0532 |
| `aspect` | 0.2173 | 0.2175 | 0.2261 |
| `bay` | 0.0719 | 0.0715 | 0.0755 |
| `island` | 0.0802 | 0.0794 | 0.0804 |

在这批扰动强度下，**平滑不改变命中，只改变距离**；`none` 在对真值距离上最好
（因为它保留了用户真正画出的细节）。因此选 `light` 作为默认值的理由是它对**笔画抖动**
（真实手绘才有，本轮探针没有）与凹口保留 0.831 的折中，**不是**"平滑后更好看所以更合适搜索"——
本轮数据不支持后者，反而显示它略微增大与真值的误差。

## 5. Task B — 镜像与走向（几何控制，不靠幅值）

六个不对称候选（row 7/61/100/294/365/745）的镜像查询，走完整管线：

| 探针 | 粗筛里真值排名 | `allow_mirror=False` 精排#1 | D | `allow_mirror=True` 精排#1 | D | mirrored 字段 |
|------|----------------|------------------------------|---|-----------------------------|---|---------------|
| mirror-r7 | **1** | row 157 | 0.32956 | row 7（真值） | 0.01641 | True |
| mirror-r61 | **1** | row 813 | 0.07454 | row 61（真值） | 0.00963 | True |
| mirror-r100 | **1** | row 794 | 0.16347 | row 100（真值） | 0.00433 | True |
| mirror-r294 | **1** | row 213 | 0.25389 | row 294（真值） | 0.24737 | True |
| mirror-r365 | **1** | row 365（真值） | 0.52360 | row 365（真值） | 0.08643 | True |
| mirror-r745 | **1** | row 61 | 0.07621 | row 745（真值） | 0.00378 | True |

三条结论：
1. **粗筛对镜像在数学上不可见**：六个探针的真值粗筛排名全为 1（走向规范化后镜像把 `Z_k` 变为
   `conj(Z_k)`，幅值精确不变）→ 镜像策略不能写在特征层（D26 修订 / D33）。
2. **几何精排的开关确实改变结果**：`allow_mirror=True` 时六个探针全部把真值提到第 1 名，
   距离从 0.10–0.52 塌到 0.004–0.25；`False` 时排名 1/2/9/–/–/10（`–` = 掉出 Top-10）。
   注意 mirror-r365 禁镜像时真值仍排第 1，但 D=0.5236 属弱命中并写入
   `these are the closest candidates in this library, not verified matches` —— 是"库里没别的更像的"，
   不是"镜像被匹配上了"。
3. **走向反转 ≠ 镜像**：反转点序被 `ensure_ccw` 还原为同一条走向（对齐距离 = 原距离），
   反射始终产生新几何。G1 `reverse` 族 6/6 Top-1 命中即此项证据。

## 6. Task C — 三个 baseline 在同一数据 / 同一查询集上

`baselines_<query>.png` 每查询一幅三联对比（Hu / Fourier / Fourier+对齐精排，Top-5 展示、
显示层对齐后叠画）。`figures.json → queries[*].baselines.methods` 存 Top-3 行号与距离。

| 查询 | Hu Top-3 行 | Fourier Top-3 行 | Fourier+align Top-3 行 |
|------|-------------|-------------------|-------------------------|
| drawn-crescent_json | 63, 577, 3 | 100, 76, 348 | **64**, 100, 780 |
| drawn-crescent_png | 677, 662, 77 | 100, 348, 76 | **64**, 100, 663 |
| drawn-forked_json | 47, 168, 621 | 389, 168, 692 | **389**, 766, 697 |
| drawn-heart_json | 38, 682, 218 | 88, 551, 271 | **716**, 207, 200 |
| drawn-heart_png | 755, 323, 459 | 88, 551, 779 | **716**, 207, 644 |
| drawn-irregular_png | 54, 378, 723 | 832, 54, 634 | **53**, 629, 518 |
| drawn-s-shape_json | 498, 119, 194 | 269, 656, 742 | **269**, 595, 656 |
| drawn-star10_json | 276, 381, 251 | 111, 569, 626 | **111**, 227, 569 |
| drawn-star10_png | 251, 825, 105 | 111, 227, 723 | **111**, 227, 569 |

- 三者的 **Top-1 在 9 个查询上互不相同**（Hu 从未与 fourier_align 的 #1 一致）。
  这是目视判读的前提：不存在"数值更低的算法"这种可比说法。
- **距离量纲不可跨方法比较**（实测）：Hu 的 `matchShapes` 值域 0 – 1.1e-4，Fourier 粗筛
  0.0033 – 0.0225，对齐精排 0.052 – 0.213。因此本实验**没有**任何跨方法混合 L2、
  也没有把距离换算成"相似度百分比"。
- 精排确实在改排序而非复读粗筛：`drawn-heart` 粗筛 #1 是 row 88（coarse 0.00784），
  精排把 row 716（coarse 0.01352、D=0.10580）提到第 1；`drawn-crescent` 同理（row 100 → row 64）。
- **Chamfer 对称复核**（`chamfer_top=10` + `sort_by="chamfer"`）：9 个查询里 **6 个 Top-1 不变**，
  3 个改变（`drawn-heart.png`、`drawn-star10.json`、`drawn-star10.png`）。
  Chamfer 复核会改动 Top-10 中 6–10 个位次，但本轮**没有人工标注**，
  所以只记录"两条几何度量在星形 / 心形上给出不同顺序"，不宣称哪个更符合肉眼。
  （已实测 Chamfer 对刚性运动 / 循环起点 / 走向反转**不变**，但对单向旋转或镜像**会变** →
  它不能用于裁决镜像，见 D38 与单测。）

## 7. Task D — 统一接口与可追溯性

每个 `Hit` 带 `rank` / `index_row` / `candidate_id` / `source_id` / `subtype` / `class` /
`lon` / `lat` / `area_m2` / `coarse_distance` / `align_distance` / `align_shift` /
`align_theta_deg` / `align_mirrored` / `chamfer`。示例（`drawn-crescent.png`，Top-3）：

| rank | candidate_id | subtype | lon, lat | 面积 | coarse D | 精排 D | θ | shift |
|------|--------------|---------|----------|------|----------|--------|-----|-------|
| 1 | `f774dda7-af3d-30dc-8540-f794eb9358f3` | pond | −89.89456, 44.39991 | 5.7 ha | 0.02109 | 0.14994 | −91.3° | 14 |
| 2 | `5a2a8f36-16d6-35f8-b874-941844a94d83` | pond | −89.97170, 44.05294 | 0.3 ha | 0.01599 | 0.18307 | −149.2° | 46 |
| 3 | `0c46fd46-4376-3872-ac77-07fa00b9c869` | lake | −89.15979, 44.15142 | 52.9 ha | 0.02604 | 0.19761 | 86.5° | 103 |

- **确定性**：排序主键为对齐距离、次键 `index_row`（稳定 argsort）。同一输入连续三次运行
  `scripts/query_sketch.py`，除 `timings`（墙钟耗时，实测 0.034–0.100 s）之外的全部字段、
  排名与控制台文本**逐字节一致**；因此留档 JSON 的比较必须忽略 `timings`。
- **弱结果措辞**：D > 0.12（`WEAK_ALIGN_DISTANCE`）时写入 warning
  `best boundary offset is 0.157 of the shape radius: these are the closest candidates in this library, not verified matches`；
  禁镜像时另加 `mirrors disallowed (allow_mirror=False); amplitudes cannot see one (D26/D33)`。
- **去重**：`max_per_source=1` 保留每源最优候选，同时 `SearchResult.pre_dedup` 保留去重前顺序
  （本轮 Top-10 查询的审计长度为 30）。实测这 9 个查询在**不加限制**时 Top-10 已是 10 个不同
  `source_id`，即本轮 Top-10 没有被同一地物的多部分刷屏；去重上限是为了更大库时的行为可控，
  本轮无"去重前后 #1 改变"的案例。

## 8. Task E — 调试工具（不是产品界面）

- `tools/sketch_pad.html`：独立本地 Canvas，画 / 清 / 导出 `{"points": [[x,y], …], "closed": true}`
  （含 `unit: "canvas"`、`y_axis: "y_up"` 声明），无 React、无地图、无部署。
- `scripts/query_sketch.py`：读 JSON 或 PNG，打印 / 保存 Top-K 图片与
  `experiments/p0_retrieval_baseline/query_<label>.json`。本轮留存三份：
  `query_drawn-crescent_json.json`、`query_drawn-crescent_png.json`（同一图形的两种输入通路）
  与 `query_drawn-pentagram_json.json` —— 最后一份是 `--allow-self-intersection --top 8` 的
  **显式 opt-in** 结果，返回的 8 个候选带
  `best boundary offset is 0.785 of the shape radius: these are the closest candidates in this library, not verified matches`，
  即"按闭合曲线勉强去匹配自交五角星"的真实代价；**默认路径**下该输入是直接报错而非猜测
  （见 §3 与 `failure_cases.png`）。
- 该工具的定位是算法调试；界面文案不得宣称"AI 认出了你画的东西"。

## 9. Task G — 三层评估（241 行 → `results.csv`）

行数构成：G1 48（8 个规格 × 6 候选，仅 `light`）、G2 180（10 个规格 × 6 候选 × 3 平滑档）、
G3 13（`light`），覆盖 121 个不同查询文件。

### 9.1 G1 算法正确性（合成扰动，`top_k=10`，`shortlist=200`，`allow_mirror=False`）

| 扰动 | n | Top-1 | Top-10 | 中位排名 | 最大排名 | 对真值对齐距离（中位） |
|------|---|-------|--------|----------|----------|------------------------|
| self | 6 | 1.000 | 1.000 | 1 | 1 | 0.01286 |
| rot 37°/191° | 12 | 1.000 | 1.000 | 1 | 1 | 0.01275 |
| scale ×3.7 | 6 | 1.000 | 1.000 | 1 | 1 | 0.01273 |
| shift (+40,−25) | 6 | 1.000 | 1.000 | 1 | 1 | 0.01286 |
| reverse | 6 | 1.000 | 1.000 | 1 | 1 | 0.01287 |
| sparse 26 点 | 6 | 0.667 | 0.833 | 1 | 2 | 0.06398 |
| mirror（禁镜像） | 6 | 0.167 | 0.667 | 5.5 | 10 | 0.33359 |

前五行是**实现正确性**证据（不是手绘能力）；`sparse` 的 1 个失败是 row 294（枝状水库，
全库精排第 149 名）；`mirror` 行是**策略正确**的表现（默认不允许镜像），其逐条证据见 §5。

### 9.2 G2 不规则草图鲁棒性（每档探针数：`jag` 18 = 3 强度 × 6 候选，`gap` 24 = 4 规格 × 6 候选，其余各 6）

| 族（含义） | Top-1（三档相同） | 失败归类（三档相同） | 备注 |
|------------|-------------------|----------------------|------|
| `jag` 笔抖 0.02 / 0.05 / 0.10 | 0.833（ok 行 15/15 全为第 1） | 3 行 D（全部 row 294） | 距离随强度上升：0.031 → 0.036 |
| `island` 局部凸起 +0.18 | 1.000 | 无 | 对真值距离 0.079 |
| `bay` 局部凹陷 −0.18 | 0.833 | 无 | 未 Top-1 的一条在 Top-10（最大排名 5–8） |
| `aspect` 长宽比 ±1.25 | 0.667（ok 行 4/4 全为第 1） | 6 行 D（r61 / r745） | r61 真值全库精排 348–350 名 → 见 §10 |
| `gap` 端点闭合误差 | 0.583（ok 行 14/14 全为第 1） | 18 行 A_extraction + 12 行 D（全部 r294） | 单顶点 0.06/0.35 与尾部 0.06/0.35 各 6 行 |

`gap` 族的尾部（ramp）探针给出本轮唯一可测的"闭合误差量化"：合法缺口内（0.06 尺度）
尾部位移使对真值距离从 0.0163 升到 0.0228、0.35 尺度从 0.1063 升到 0.1643，
**排名不变**（接受分支 5/5、2/2 仍 Top-1）；0.35 尺度有 3/6 个候选直接被判为开放曲线拒绝。

**完整的 100–200 形状分级研究不在本轮**（任务边界，属 P0-5）；本轮是 6 个候选 × 受控扰动族。

### 9.3 G3 类手绘查询（`sketch` / `image`，**无真值**，只判读结果图）

13 个输入 → 9 个返回结果、4 个显式拒绝。Top-1 全部是真实地物（id / subtype / 经纬度见 §7 与图）：

| 查询 | Top-1 | subtype | 面积 | 精排 D | 弱命中标注 |
|------|-------|---------|------|--------|------------|
| drawn-crescent.json / .png | row 64 `f774dda7…` | pond | 5.7 ha | 0.15668 / 0.14994 | 是（>0.12） |
| drawn-heart.json / .png | row 716 `d3a552b9…` | lake | 4.6 ha | 0.10580 / 0.10611 | 否 |
| drawn-s-shape.json | row 269 `79949450…` | pond | 0.4 ha | 0.10856 | 否 |
| drawn-star10.json / .png | row 111 `ef25003c…` | pond | 0.4 ha | 0.21272 / 0.21714 | 是 |
| drawn-forked.json | row 389 `02a32751…` | pond | 0.7 ha | 0.16188 | 是 |
| drawn-irregular.png | row 53 `089e0867…` | pond | 0.1 ha | 0.05206 | 否 |
| drawn-pentagram.json | 拒绝（自交） | — | — | — | — |
| drawn-irregular.json | 拒绝（开放） | — | — | — | — |
| ambiguous-two-shapes.png / open-arc.png | 拒绝（歧义 / 笔画带） | — | — | — | — |

月牙与心形查询的肉眼判读（`topk_*.png`，10 个真实轮廓叠加显示）给出的是"弯月状池塘 /
带凹口湖泊"级别的相似；星形查询 D≈0.21 并带弱命中措辞 —— 当前 839 个候选里**没有**角状地物，
这是覆盖问题，不是排序问题（任务书明确"不要预设这些类别在当前库中一定有对应结果"）。

### 9.4 短名单窗口扫描（`--sweep 100,200,300`，粗筛 `method="fourier"`，light，102 个探针）

**Top-100 = Top-200 = Top-300，逐族指标逐位相同**（12 个族 × 3 个窗口，`summary.json →
shortlist_sweep`）。粗筛把真值排在窗口内很宽裕：唯一掉出 200 窗口的真值是 `aspect-r61`
（全库粗筛约 348 名），而它即使全库精排也在 348 名 → 放大窗口不会带来任何命中增益。
（注意该扫描的统计口径：只含粗筛 + 去重后的排名，排除了 6 个提取拒绝，所以 `gap` 族的
0.778 高于 §9.2 的 0.583。两类指标不可混用。）

## 10. Task H — 失败归因（四类，全部由实测排名决定）

`labels_overall = {ok 180, A_extraction 22, D_coverage_or_deformation 30, G3_no_ground_truth 9}`；
**B_coarse_filter = 0，C_rerank_order = 0，C_dedup_or_truncation = 0。**

归因规则（`scripts/evaluate_retrieval.py::attribute`，只使用测得的排名）：
真值在返回列表内 → ok；在去重前窗口内但被去重 / 截断挤掉 → C（去重）；
粗筛全库排名 > 窗口而全库精排 ≤ Top-10 → **B（粗筛把真值挡在门外）**；
全库精排 ≤ Top-10 但没进返回 → C（精排顺序）；
**全库 839 个都精排后真值仍在 Top-10 之外 → D（库覆盖不足或形变超出可恢复范围）**。

| 类 | 行数 | 独立输入 | 说明 |
|----|------|----------|------|
| A 图形提取失败 | 22 | 10 | `gap-r{61,100,745}-0.35{,-tail}.json` 6 个输入 × 3 档 = 18 行；`drawn-irregular.json`（开放）、`drawn-pentagram.json`（自交）、`ambiguous-two-shapes.png`（多分量）、`open-arc.png`（笔画带）各 1 行（G3 只跑 `light`） |
| B 粗筛失败 | **0** | — | 没有任何"精排会认可但粗筛没送进来"的候选 |
| C 精排 / 去重失败 | **0** | — | 无：Top-10 截断与 `max_per_source` 未挤掉任何真值 |
| D 数据覆盖不足 / 形变 | 30 | 12 | `jag-r294` 9 行、`gap-r294` 12 行、`aspect-r61` 3 行、`aspect-r745` 3 行、`mirror-r100/r294` 各 1、`sparse-r294` 1 |
| G3 无真值 | 9 | 9 | 只做目视判读，不计入命中率 |

**D 类集中于 row 294**：该候选是 22/30 行 D 的共同来源（枝状水库，P0-2/P0-3 已知 N=128 欠采样、
重建 IoU 最差的一类）。它的任何形变查询在全库精排里都落在第 23–502 名，而返回的 Top-1 是
D≈0.22–0.26 的别的候选 —— 即"库里有 10 个形状比它更像这个查询"，放大窗口或换排序键都无解。
这不是精排算错，而是 **1°×1° 单区域库的覆盖 + 该候选自身采样保真度**的联合后果（呼应 D29 / D32）。
失败案例图 `failure_cases.png` 展示 52 个非 ok 行中的 12 个去重后独立案例
（含拒绝原因的原文），未做任何挑选。

**全库精排不是人类视觉真值**：§10 的 rank_full 只用于区分 B / C / D，
不能读成"人应该觉得第 N 名最像"。

## 11. 产物清单

| 文件 | 内容 |
|------|------|
| `results.csv` | 241 行 × 25 列（查询、平滑档、真值行、四个排名、三类距离、镜像开关证据列、归类、拒绝原因、Top-K 行号串） |
| `summary.json` | 参数 + 索引版本与 sha256 + 分层指标 + 全库平滑消融 + G3 摘要 + 短名单扫描 + 失败图统计 |
| `figures.json` | 索引版本 + 可视化参数（121 输入 / 10 拒绝）+ 每查询三阶段提取记录 + Top-10 全量命中（含经纬度）+ 三 baseline 数据 |
| `extraction_examples.png` | Task A 验收图：22 行 = 10 个拒绝案例（原始笔画 + 报错原文）+ 12 个三阶段行（原始轨迹 / 抽取外轮廓 / 128 点标准化轮廓） |
| `topk_<query>.png`（10 张） | 每查询 Top-10：真实轮廓 + 显示层对齐叠加 + candidate id / subtype / 真实经纬度 / 排名 / 内部距离 / θ / shift / mirror / Chamfer + 查询几何量（第 10 张是 `drawn-pentagram_json` 的 opt-in 运行，见 §8） |
| `baselines_<query>.png`（9 张） | 同查询三 baseline 对比（Hu / Fourier / Fourier+align） |
| `smoothing_ablation.png` | 全库 839 条的 detail 频带保留率、compactness、周长三项代价（均值 + 最差候选） |
| `failure_cases.png` | 12 个去重后非 ok 案例（查询 + 真值 + 实际返回 #1 + 拒绝原文） |
| `queries/`（122 个文件 = 115 轨迹 JSON + 6 PNG + `manifest.json`） | 确定性查询集，每条 JSON 自带血统与参数 |
| `query_drawn-*.json` | 调试 CLI 的输出留档（含一个拒绝案例） |

## 12. 本轮不允许的主张

1. **不是人类手绘效果**：G3 的 `drawn-*` 是程序生成的指针轨迹与栅格图，**不是**独立人类手绘；
   `probe` 是真实轮廓的受控形变，**不是**手绘。真人 Top-K 判读需 P0-6 的固定查询集（≥10 人）。
2. **不能说"算法优于 baseline"**：三 baseline 的 Top-1 在 9 个查询上互不相同，
   且没有独立人工标注 —— 只能陈述"哪个方法返回了什么"，不能把内部距离更小解释为视觉更好。
3. **不能说"检索准确率 X%"**：G1/G2 的命中率是**实现正确性**，样本是 6 个候选的受控形变，
   不是分层抽样；100–200 形状分级研究属 P0-5。
4. **不把 `align_distance` 当相似度**：它是 RMS 边界偏移（单位 = 形状尺度），
   未做人工校准，禁止输出百分比。
5. **没有扩大数据**：未下载新区域、未新增类别（pending `add_now: false` 决策见 D32，判定证据仍不足）。
6. **未做 256 点升级、未删复杂水库**：任务书风险条款，本轮未触碰索引内容。
7. **P0 门禁仍未通过**：`docs/EXPERIMENTS.md` §6 的 GO 判据要求 ≥10 个真人手绘查询的 Top-10 判读，
   本轮没有该数据。

## 13. 下一步（P0-5 / P0-6 输入条件）

1. P0-5：从 839 库分层抽样 100–200 个轮廓做扰动强度曲线（含 `jag` 强度轴与 `gap` 尾部轴），
   与本轮 G1/G2 指标**分开记录**；本轮的 `evaluate_retrieval.py` 已支持任意查询目录，可直接复用。
2. P0-6：真人手绘固定查询集（先固定、后观测），逐条填写肉眼判读；本轮的
   `tools/sketch_pad.html` 导出格式即其输入格式。
3. row 294 类枝状候选的采样保真（D29 遗留）应作为独立问题处理，不要用平滑或删候选掩盖。
4. 覆盖问题（星形 / 角状 / 多凹口）需更大 bbox 或 pending 类别决策，属 P0-7 / D32。
5. 若要把 Top-K 交给前端，`preprocess.ts` 必须与 `earthglyph.geometry.sketch` 同约定
   （同 N、同 RMS 归一、同 CCW、同 `\|Z_k\|/N`），否则离线评价不可迁移（§8.3）。
