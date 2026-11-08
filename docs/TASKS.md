# TASKS — 当前任务、状态、阻塞与下一步

> 规则见 [`AGENTS.md`](../AGENTS.md)；阶段门禁见 [`ROADMAP.md`](ROADMAP.md)。
> 最后更新：2026-11-05（**P0-4 完成**，P0-5 / P0-6 未启动；P0 门禁仍未通过 —— 缺 ≥10 个真人手绘查询）。

## 已完成：项目初始化 ✅

| 项 | 状态 |
|----|------|
| 环境检查（OS/Git/Python/Node/npm/npx） | ✅ |
| 安装 Node.js LTS（winget） | ✅ v26.13.0 |
| 配置三个 Skills（gdal/shapely/earthglyph-retrieval） | ✅ 已被 Qoder 加载 |
| 项目结构 + 基础工程配置（pyproject/.gitignore/git） | ✅ |
| 规范与设计文档（README/AGENTS/docs/*） | ✅ |
| Python 测试链路（pytest 7 passed） | ✅ |
| Web 测试链路（vitest 5 passed + typecheck + build） | ✅ |

## 已完成：P0-0 环境与依赖补齐 ✅（2026-11-04）

| 项 | 状态 |
|----|------|
| `pip install -e ".[data,cv]"` 进现有 `.venv`（未重建） | ✅ geopandas 1.2.0 / pyarrow 26.0.1 / duckdb 2.0.0 / overturemaps 1.0.2 / matplotlib 3.11.2 / opencv-headless 5.0.0.93 |
| 底层引擎实测 | ✅ GEOS 3.14.1 / PROJ 9.8.1 / pyogrio 内置 GDAL 3.12.4 |
| 真实 GeoParquet 读取验证 | ✅ 6,543 行可解析，CRS EPSG:4326 |
| 投影与面积量算 | ✅ aeqd 米制投影（见 D16） |
| 系统 GDAL CLI | ⚠️ 未安装，**非阻塞**：Python 栈已覆盖 P0 需求（见 ENVIRONMENT §5） |
| `pytest` | ✅ 65 passed（全部离线） |
| `mypy`（strict） | ✅ no issues in 7 source files |
| ENVIRONMENT.md 更新 | ✅ |

## 已完成：P0-1 真实数据获取与质量检查 ✅（2026-11-04）

| 项 | 状态 |
|----|------|
| 真实数据下载（Overture Base/water, release 2026-10-21.0, bbox -90,44,-89,45） | ✅ 6,543 行 / 5.53 MB，sha256 记录在 sidecar |
| 获取方式偏离说明（官方 CLI S3 SDK 截断 → STAC + DuckDB httpfs range 读取） | ✅ 见 D13、DATA_PIPELINE §8.3、probe notes.md |
| `scripts/download_data.py`（bbox/输出/版本可配、完整性校验、幂等不覆盖、`--force`、`--fetch-method`） | ✅ |
| `scripts/inspect_data.py` + `earthglyph/data/inspect.py`（Schema、几何类型、有效性、分布、重复 id、投影面积） | ✅ |
| 实际 Schema 核对（subtype/class 为字符串；新增 is_intermittent/is_salt/level/source_tags/wikidata） | ✅ 见 D14 |
| 真实轮廓预览拼图（32 个，等比例 aeqd，含源 ID / 类型 / 坐标；标注为数据检查） | ✅ `experiments/p0_data_probe/geometry_preview.png` |
| 探测记录 | ✅ `experiments/p0_data_probe/{dataset_summary.json, notes.md}` |
| 离线单元测试（合成样本仅在 `tmp_path`，不混入 `data/`） | ✅ 58 个数据层测试 |
| 复现校验（inspect 重跑逐字节一致；preview 同 seed 确定） | ✅ |

**P0-1 结论（数据侧，非检索结论）：** 单 1°×1° 区域有 1,984 个可用闭合轮廓，其中
lake/pond/reservoir 约 979 个 + 需逐个检查的 `water` 836 个 —— 候选规模足以支撑 P0-2，
**无需下载全球**。数据本身无空 / 无效 / 重复问题，但存在三类伪轮廓（下载框切片、
游泳池矩形、河岸细长条）必须在 P0-2 过滤（DATA_PIPELINE §8.4）。

## 已完成：P0-2 几何清洗与标准化轮廓提取 ✅（2026-11-05）

| 项 | 状态 |
|----|------|
| 框边裁剪几何证据核实（D15 修订依据） | ✅ 1,984 个可用多边形 0 个被裁剪；17 个 bbox 超框者均为完整几何 → 过滤改为信息性标志 `extends_beyond_frame`（8 个） |
| `earthglyph/geometry/{projection,extraction,resampling,filtering,normalization}.py` | ✅ 复用既有 `normalize_contour`（移至 normalization.py 单一实现）；mypy strict 通过 |
| `earthglyph/data/prepare.py` + `scripts/prepare_polygons.py` | ✅ 两级过滤记账、去重、manifest、rejected/pending 输出；CLI 参数化（--min-area-ha/--subtypes/--points/--max-iou） |
| 标准轮廓数据集 | ✅ `data/processed/contours.parquet` 839 候选（pond 472 / lake 312 / reservoir 55），17 个来自 MultiPolygon、45 个带孔洞、0 个需修复 |
| manifest | ✅ `data/processed/data_manifest.json`：来源+release+sha256+内容摘要、投影参数、清洗参数、N=128、逐项拒绝计数、构建命令与环境 |
| 被过滤样本留存 | ✅ `rejected_samples.parquet`（521 条，按原因≤200）+ `pending_analysis.parquet`（1,034 条非允许 subtype 多边形，未删除） |
| 自动化测试 | ✅ 137 passed（含 12 项预处理覆盖）；ruff 全过；mypy strict 13 文件无问题 |
| 可视化检查 | ✅ `experiments/p0_geometry_probe/{before_after,candidate_gallery,rejected_examples,complex_contours}.png` + `summary.json` + `notes.md`（真实数据，标注为预处理结果） |
| 复现性 | ✅ 同命令重建字节一致；内容摘要与编码无关；原始 GeoParquet 未被修改（sha256+mtime 校验） |

**P0-2 结论（预处理侧，非检索结论）：** 6,543 原始行 → 839 个可检索标准轮廓；
数据损失全部可解释（4,559 非闭合几何、1,034 待分析 subtype、121 小于 0.1 ha）。
枝状水库在 N=128 下最坏弧长步长偏差约 104%，采样点数消融留给 P0-3（D7 注记）。

## 已完成：P0-3 特征提取与索引构建 ✅（2026-11-05）

| 项 | 状态 |
|----|------|
| Task A Fourier 描述子（定义 / DC 丢弃 / `\|Z_k\|/N` 逐样本归一 / 折叠模式 / Nyquist 上限 / 单点=批量） | ✅ `earthglyph/features/fourier.py`；49 项单测；见 D24/D25/D26 |
| 谐波数消融 12 / 16 / 24（839 真实候选） | ✅ 取 **16**：能量捕获 p10 三者同为 0.7025、成对距离 pearson ≥ 0.99997 → 更宽无可测增益 |
| 幅值丢相代价量化 | ✅ "同幅值随机相位"冒形 region IoU 中位 0.5338、98.81% < 0.9 → 幅值只可粗筛（D10 复核） |
| Task B 辅助几何特征（compactness / solidity / aspect_ratio / diameter_fill） | ✅ 公式与基准写入 manifest；退化输入抛错计数（0 跳过）；见 D27 |
| GEOS `minimum_rotated_rectangle` 缺陷（有效 40 顶点凸包塌成 6.279e-18 细条） | ✅ 改自实现凸包 + 旋转卡尺，见 D28 |
| 特征组独立可消融，**无未验证融合权重** | ✅ `fusion: none`，组偏移入 manifest |
| Task C 采样点数消融 N=64/128/256（全部从**原始外环**重采样，未用 128→256 插值捷径） | ✅ 839×3=2,517 行 + 3 张对比图 + 局部放大 + 真实经纬度 |
| Task C 结论 | ✅ **保留 N=128**：64 使 `compactness` p99 漂移 24.7%、最坏 IoU 0.677；256 增益（最坏 IoU 0.81→0.90）不值轮廓字节翻倍（5 万候选 48.8→97.7 MiB）；自适应 N 有证据但本轮不实施（D29） |
| 失败案例 | ✅ 8 个候选任何 N 都达不到 IoU≥0.95（7 个 reservoir）；N=128 下 5 个候选重建自交（上报不修复）；P0-2 "104%" 重新归类为弦长离散度（D30） |
| 度量正确性校验（新增 `--validate-exact`） | ✅ 2,517 对 vs GEOS + 8× 细云：超一个云步长的违例 **0**，偏差偏保守 |
| Task D `scripts/build_index.py` + `earthglyph/data/index.py` | ✅ `data/index/{features.npy, shapes.npy, metadata.parquet, data_manifest.json}`；839 行 / 0 跳过 / 两次构建字节一致 |
| 一致性检查（数量、id 唯一、行序契约 `feature_row`、有限值、维度、轮廓回推、经纬度合法、确定性） | ✅ 31 项单测含异常注入；float32 往返误差 6.7e-7 / 6.0e-8 |
| 每候选回查真实地点 | ✅ `candidate_id → source_id + part_index`，`lon/lat` 为 representative_point（D12）；单测确认正式索引无合成记录 |
| Task E 候选库规模与多样性（**未下载新数据**） | ✅ 面积跨 4.99 个数量级、6 个 class、45 带孔洞、23 对近重复（20 簇）；Type A/B 判别字段已备好**未判定** |
| Task F pending 类别（只读分析，文件未删改、未入索引） | ✅ 1,034 feature：water 冗余（6.82% 超候选库间距）、river 新但线状 → **`add_now: false`**，决策留给 P0-4/P0-6（D32） |
| 自动化测试 | ✅ **281 passed**（P0-2 基线 137 + P0-3 新增 144）；`ruff check .` 全过；`mypy` strict 17 文件无问题 |
| 实验记录 | ✅ `experiments/p0_feature_probe/{notes.md,features_summary.json,sampling_summary.json,sampling_metric_validation.json,pending_summary.json,…}` + 6 张图 |

**P0-3 结论（特征与几何侧，非检索结论）：** 839 个真实候选已转为可批量读取的 20 维粗筛
向量 + 128 点精排轮廓，特征按声明定义计算、不变性由单测钉住、逐候选可回查真实地点；
N=128 在真实岸线上的表达上限已量化（极端枝状 / 收颈水库是已知短板）。
**仍未验证：手绘 → Top-K 是否肉眼相似** —— 属 P0-4～P0-6。

## 已完成：P0-4 手绘图形抽象提取与真实地貌检索 ✅（2026-11-05）

| 项 | 状态 |
|----|------|
| Task A 统一输入（矢量轨迹 JSON / 单轮廓黑白 PNG）→ `earthglyph/geometry/sketch.py` | ✅ `contour_from_points` / `contour_from_image`：22 项阈值全部相对轨迹自身 RMS 尺度（D35）；歧义 PNG 显式报错不猜测（多分量 / 笔画带 / 开放 / 自交） |
| Task A 处理链（去重 → 闭合判定 → 退化检测 → 非均匀采样 → 抖动 → 走向统一 → 弧长重采样 128 → 平移 + RMS 归一） | ✅ 复用 `normalize_contour` / `arc_length_resample` / `ensure_ccw` / P0-3 特征；y 向下与 PNG 线宽不进入几何 |
| Task A3 平滑三档 `none/light/medium` | ✅ `SMOOTHING_SIGMA = {0.0, 0.8, 2.0}` 循环零相位高斯低通；全库代价实测：light 保留 k=9..16 谱能量 0.831（最差 0.760）、medium 仅 0.337（最差 0.197），medium 最坏改动 51% 周长 → 取 `light`（D39） |
| 三阶段对照图 | ✅ `extraction_examples.png`（原始轨迹 / 抽取外轮廓 / 128 点标准化，22 行含 10 个拒绝案例） |
| Task B 镜像与走向全管线 | ✅ 走向反转由 `ensure_ccw` 归一（不是镜像）；镜像在精排走显式复共轭支路、由 `allow_mirror` 控制，默认关闭；实测 `allow_mirror=True` 时 6/6 镜像真值回到第 1（D 0.10–0.52 → 0.004–0.25） |
| Fourier 幅值镜像盲（实测） | ✅ 6 个镜像探针的真值**粗筛排名恒为 1**，`allow_mirror=False` 时 Top-1 仅 0.167 → 镜像策略必须由几何控制，不依赖幅值偶然数值（D26 修订 / D33） |
| Task C 三个 baseline | ✅ `hu_baseline.py`（`cv2.matchShapes`，统一像素尺度）/ `coarse.py`（分组欧氏 + 稳定 argsort）/ `alignment.py`（闭式 `min_{s,θ}`：`C_s = IFFT(Â·conj(B̂))_s/N²`，θ = arg C_s，不遍历 360 角度）+ `chamfer.py` 复核（D34） |
| Task C Chamfer 的角色 | ✅ 实测对刚性运动 / 循环起点 / 走向反转不变，对单向旋转或镜像**会变** → 只作窗口复核，不裁决镜像（D38；9 个查询中 6 个 Top-1 与精排一致） |
| Task D `search_by_contour` / `search_by_image` | ✅ 签名 `(points, top_k=10, shortlist_size=200, method="fourier_align", allow_mirror=False, …)`；`Hit` 带 rank / candidate_id / source_id / subtype / 真实 lon/lat / coarse_distance / align_distance / θ / 镜像态 / chamfer；确定性排序（并列按索引行序，稳定 argsort）；除 `timings` 外逐字节可复现 |
| Task D 结果可追溯 | ✅ 全部命中来自真实索引（commit `77b083c9`、839 候选）；无预设地点表、无虚构地物、无未校准"相似度百分比"；弱命中显式标注"只是当前库中最接近的候选"（阈值 0.12，D42） |
| Task D 去重 | ✅ `max_per_source=1` 抑制同 `source_id` 刷屏，`pre_dedup` 保留原始顺序供审计；实测 9 个查询的去重前后 Top-1 与 10/10 独立来源均不变 |
| Task E 最小调试工具 | ✅ `tools/sketch_pad.html`（独立本地 Canvas，画/清/导出 JSON，非产品界面）+ `scripts/query_sketch.py`（读 JSON/PNG → 打印 Top-10 + 出图） |
| Task F 可视化 | ✅ 9 张 `topk_<query>.png`（真实轮廓叠加 + id + 类型 + 经纬度 + rank + 内部距离）+ 9 张逐查询 `baselines_<query>.png`（三 baseline 对照，替代单张 `baseline_comparison.png`，命名理由见 [`EXPERIMENTS.md`](EXPERIMENTS.md) §3） |
| Task G 三层评估 | ✅ G1 实现正确性（self/rot/scale/shift/reverse Top-1 **1.000**，6/12/6/6/6）、G2 受控不规则草图（5 族 × 3 档，排名逐档相同）、G3 类手绘 13 输入（9 返回 / 4 拒绝，**机器指针轨迹，非真人手绘**）；100–200 形状分级研究留给 P0-5 |
| Task H 失败归因（四类，只用实测排名） | ✅ `ok 180 / A 提取 22（10 输入）/ B 粗筛 **0** / C 精排 **0** / D 覆盖或形变 30（22 行同源 row 294 枝状水库，全库精排 23–502 名）/ G3 无真值 9`；`--sweep 100,200,300` 逐族逐位相同 → 粗筛窗口不是瓶颈（D41） |
| 发现的实现缺陷（已修 + 已钉测试） | ✅ 声明闭合的轨迹丢弃末点（闭合弦是隐含边，非顶点）→ 单顶点闭合误差被完全吸收；原"尾部斜坡"探针位移小于一个采样步长，与单顶点版字节相同 → 改为按周长 + `TAIL_MIN_STEPS=6` 步下限（D36） |
| 实验规模与产物 | ✅ 121 个查询文件（`--seed 20261105`）× 方法/档位 → `results.csv` **241 行** + `summary.json` + `figures.json` + `notes.md`（13 节）+ 21 张图 |
| 自动化测试 | ✅ **425 passed**（P0-3 基线 281 + P0-4 新增 144）；`ruff check .` 全过；`mypy` strict 24 文件无问题 |

**P0-4 结论（真实数据上的检索证据，仍不等于门禁通过）：**
从 839 个真实候选出发，一条不精确的手绘轨迹确实能被抽象为与画布大小 / 位置 / 笔画速度
无关的几何表示，并检索出**带真实经纬度与 candidate_id 的肉眼相似地物**——月牙查询命中
5.7 ha 的弯月状池塘 `f774dda7…`（−89.89456, 44.39991，D=0.150）、心形查询命中带凹口湖泊
`d3a552b9…`（−89.15608, 44.33571，D=0.106）。失败集中在**提取拒绝（A）**与**库覆盖（D）**，
粗筛与精排窗口本轮没有造成任何真值丢失（B=C=0）。
**未验证：真人手绘（P0-6）与 100–200 形状分级曲线（P0-5）；星形类查询是覆盖问题，不是排序问题。**

> ⚠️ **P0 门禁仍未通过**：P0-4 已在真实索引上给出真实 Top-10 结果图与 241 行检索指标，
> 但其"类手绘"查询是**机器生成的指针轨迹**，不是真人手绘；门禁要求 **≥10 个不同用户**的
> 固定查询集与人工肉眼判读（P0-6），仍需 P0-5 的 100–200 形状分级曲线。
> **P0 未通过前不进行大规模 Web 开发。**

## P0-4.5 面向人眼感知的形状检索算法对比 ✅ Checkpoint D · D1–D4 完成（2026-11-07；真人盲评仍未做 → 归 P0-6）

任务边界：**Checkpoint A** 做到「数据与实验设计 + 受控图库 + control_gallery + 固定划分」即停止；
**Checkpoint B** 做到「E1/E2/E3 三臂实现 + 同一评分契约 + 自测量探针 + 测试全绿」即停止；
**Checkpoint C** 做到「四臂在同一冻结图库上跑完 T1/T2/T3 + 逐查询 Top-K 对比图 + 结果文件 +
表格由脚本打印」即停止。**C 不解释结论、不做不变性曲线、不做人工盲评（均属 Checkpoint D）**，
**未进入 P0-5 扰动分级曲线，未做真人手绘（P0-6，未获授权），未开发任何 Web 产品界面。**

### Checkpoint A：受控感知图库 ✅

| 项 | 状态 |
|----|------|
| Task A ShapeComp（Morgenstern et al. 2021）原始来源审计 | ✅ 10 问逐条作答 + 可核验 URL / HTTP 码，见 [`shapecomp_research.md`](../experiments/p0_perceptual_probe/shapecomp_research.md)；**官方 MATLAB + 权重 + 商业剪影不可得**（代码只在 `sites.google.com`，本机超时；403 是 UA 反爬不是网络阻断；两个 Zenodo 记录仅论文复现数据）→ E1 只能是明确标注 **ShapeComp-inspired** 的自实现基线 |
| Task B 受控感知图库（**独立于** 839 真实候选） | ✅ 179 成员 / 18 查询 / 7 结构类；每类查询 ≥2、变体 5–15、困难负例 10–20 **实测达标**（`check_design_coverage` 不达标即抛错，`shortfalls=[]`、`over_range=[]`） |
| Task B 三类角色 | ✅ query / variant（**不是**同一张图的平移旋转缩放：来自 Phosphor、Quick Draw 真人画作与参数化生成器三条独立来源）/ hard_negative（含包络匹配负例） |
| Task B 许可与再分发 | ✅ Phosphor MIT（25 文件 10 632 B）、Quick Draw CC BY 4.0（48 切片 24 576 000 B）、本项目合成 74；逐资产 `url + sha256 + 字节区间 + 变换`入库（`dataset_manifest.json → assets`）；无商业剪影、无新增依赖、无 GB 级下载 |
| Task B 拒绝即计数（不静默丢弃） | ✅ 75 968 次拒绝逐原因计数（`open_stroke` 19 881 / `multi_stroke` 47 772 / `self_crossing` 7 744 / `too_few_points` 557 / `degenerate` 4 / `frame_silhouette` 4 / `multiple_pieces` 6）；**未闭合笔迹不封闭、多段图形不粘合、负空间底板不当母题** |
| Task B 结构角色审计 | ✅ 指标 `concave_deficiency = 1 − area/convex_hull.area`，两阶段门槛；3 条降级公开记录（`qd-moon-query-01` 0.0010 → 硬负例：圆盘不能当新月真值），并否决了 3 种备选指标；反向暴露并修正 `syn-forked-sharp` 的生成器参数错误（D44） |
| Task B 包络匹配控制 | ✅ 生成后 **重测** solidity：10 个中 6 个达标，未达标 4 个连残差公开（最大 `syn-crescent-lobed` 0.395）；带直切口圆盘的 solidity **非单调**（1.0 → 0.826@95° → 0.995@179°）→ 求根生成器已删除，不留死代码 |
| Task B 近重复控制（数据卫生） | ✅ `align(allow_mirror=True)` ≤0.02 → 9 对近重复，**在 T2 中剔除**，任何方法无法靠返回查询的刚性副本得分；不影响 839 真实候选，不是地理去重规则 |
| Task B 三种设置 | ✅ 定义与实现口径写入 manifest `settings`：T1 含目标（仅实现自测）、T2 去目标/刚性副本/近重复（判别性主设置）、T3 图库形状混入 839 真实候选同一检索空间（**逐候选 provenance；不合并进正式索引**，`data/index` 仍为 `77b083c9`） |
| Task B 预处理一致性 | ✅ 图库成员与用户查询走 **同一条** `contour_from_points(closed=True)` → 128 点弧长重采样（`standardization` 记录），杜绝"两条管线比距离" |
| Task D `control_gallery.png` | ✅ 22 行 × 14 列（1 184 570 B，入库理由：受控设计的人工复核物，可由命令重建，见提交信息）；标签 "Controlled Shape Retrieval — 非真实地理景观"。**Checkpoint B 的 `_bevel` 修复后重建为 1 184 600 B / sha256 `585ffb28…`**，见下表 |
| Task E 标签冻结 | ✅ `labels.json`（118 796 B，sha256 `26e444ce…`）在 **任何算法看它之前** 生成；`queries/*.json` 18 个与 P0-4 查询加载器同格式；`near_duplicate_pairs` 一并冻结 |
| 确定性 | ✅ 无随机数；Quick Draw 字节区间首次联网钉死、此后（含 `--offline`）只能重放；同一 cache 重建到临时目录 → `gallery.jsonl` / `labels.json` / `rejections.csv` / `queries/*.json`(18) / `control_gallery.png` **全部字节一致**，`diff -r` 仅差 `built_at_utc` 与命令字符串 |
| 发现的实现缺陷（已修 + 已钉测试） | ✅ `svg_path.py`：`Z` 因 `and buffer` 守卫 **从未派发**（丢失全部闭合语义）+ 已在起点的路径被再补一次起点 + `<circle>` 误报 `ellipse`。**实测影响**：修复前后 14/25 个 Phosphor 成员的标准化轮廓变化（最大逐点位移 0.0879，对齐复测 0.0525），角色不变（D47）；构建器 `queries/` 只增不减导致的 **假查询** → 新增 `prune_stale_queries`（只删本构建器自己写的过期文件） |
| 自动化测试 | ✅ **498 passed**（P0-4 基线 425 + 新增 `test_perceptual_gallery.py` 33 + `test_svg_path.py` 40）；`ruff check .` 全过；`mypy` strict 26 文件无问题 |

**Checkpoint A 能主张的**：一个许可清晰、角色由实测几何裁决、划分与标签已冻结、
可逐字节复现的受控感知图库已经就绪，足以承载 E0/E1/E2/E3 的同集对照。
**Checkpoint A 不能主张的**：任何检索命中率 / 排名 / "更符合肉眼"（**一个方法都还没跑**）；
不得把内部指标称"人眼匹配准确率"（无人工评分）；不得把 ShapeComp 的近邻关系当人类真值；
未做查询挑选或权重调参（划分先于运行冻结，sha256 为凭证）。
已知局限：包络匹配在 crescent/heart/star 仅部分达标；`concave_deficiency` 是面积型指标，
分不清"深而窄"与"浅而宽"，Checkpoint B 的 E2 必须用带符号转角/曲率序列补足。

### Checkpoint B：E1/E2/E3 三臂实现 + 四臂同一评分契约 ✅

| 项 | 状态 |
|----|------|
| 四臂同一契约 | ✅ `perceptual_methods.py`（137 行）：`CONTOUR_POINTS=128`、`check_contour`（点数 <4 / 形状不对 / 非有限值 → `PerceptualMethodError`）、`contour_stack`（**混合采样点数即抛错**，杜绝 128 vs 100 的"距离"）、`rank_order`（稳定 argsort，平票按行序 = D42）、`score_library(method, …)` 分派 E0/E1/E2/E3 并**连同该臂自己的 POLICY 一起返回**；分数越小越近；**没有粗筛窗口**（四臂在同一 179 行全库上打分） |
| E0 默认行为不变 | ✅ `perceptual_e0.py`（88 行）只是适配器，`POLICY["new_code_in_this_checkpoint"] is False`；契约测试钉住 E0 距离与 `alignment.align()` **逐位相等**、`search_by_contour` 默认值（top_k 10 / shortlist 200 / `fourier_align` / `allow_mirror=False` / `smoothing=light`）不变；P0-4 产品链路 `alignment.py` / `search.py` / `coarse.py` / `chamfer.py` / `hu_baseline.py` 本轮 **0 改动**（`git show --stat 1696873` = 9 个新文件，无既有文件修改） |
| E1 ShapeComp-inspired | ✅ 91 维 / 5 组可消融（10 global + 6 moments + 18 curvature + 15 Fourier + 42 tangent-angle），`groups=` 是所有入口的参数 → "哪一组值钱"不需改代码；逐描述子尺度/归一化口径显式写入 POLICY（`provenance` 声明**不是**原始 ShapeComp，I7 有意省略并记入差异） |
| E1 主轴正则形 + 各向同性回退 | ✅ `ORIENTATION_ISOTROPY_MAX=0.98`，符号由归一化三阶矩定。实测 λ2/λ1：circle/square 1.00000、pentagon 0.99939、hexagon 0.99922、triangle 0.99904、star5 0.99869、star6 0.99582 → **isotropic-fallback**（此时 42 列切向角块**不再旋转不变**）；crescent 0.31132 / heart 0.59892 / chiral 0.76984 → canonical。逐候选可查：`orientation_statuses()` |
| E1 旋转代价（同一 5 行库实测） | ✅ star 与自身 37° 旋转副本：E0 0.0000 / E2 0.0000 / E3 0.1405 / **E1 canonical 4.8047（raw 4.6945）**；对照 chiral：canonical 8.35e-14≈0、raw 1.3434 → 主轴机制"**有轴时是全部不变性来源，无轴时静默退化为 raw**"，已写入 `known_limitations`，不粉饰为全域旋转不变 |
| E1 自拟合尺度的副作用 | ✅ 归一化常数由**当前库**拟合 → 同一查询在 3 行库中旋转副本排第 2（[7.5459, 8.2187, 7.5072]）、5 行库排第 1 → **E1 的距离跨库不可比**，写进 POLICY，Checkpoint C 必须固定库再报数 |
| E2 显著结构匹配 | ✅ 带符号转角的 5 尺度尺度空间（1/64…1/4）→ 峰值 + 持续性 → 单调 DP 对齐；代价 = 转角差 + 较小转角 × 超 2% 预算的弧长漂移 + 0.25 × 尖度差，**除以整周 2π**（分母是平面的性质，不是这一对的性质）。star(5) 实测 10 事件（5 凸 5 凹），角电荷 1.169–2.426 rad |
| E2 噪声 ≠ 结构 | ✅ 0.2% 径向噪声**不产生任何事件**（距离恰 0.0），0.5% 只产生 1 个 0.505 rad 事件，值 0.0805 = circle-vs-star 2.8610 的 **2.81%** |
| E2 两个被否决的公式（连数字留档） | ✅ ① 把 persistence 再乘进每个电荷：同寿命对 4 位小数**完全不变**（worn star 0.2348、pentagon-disk 1.0000、噪声 0.0805），而 star-vs-five-lobed 1.5045 → **5.3802** → 是缩放不是过滤；② 漂移超预算除以预算：star6 **3.9716** > star-disk 2.8610 → **顺序反转**（圆盘无事件、全是 gap，该公式动不了它；人有 5/6 个角的区分，但先看星 vs 圆） |
| E2 尖度增益取 0.25 的依据 | ✅ 同一对形状扫增益：0 → 1.8e-15（"磨损"看不见）、0.25 → 0.2348、1.0 → 0.9391；而 gain=1 时 star-vs-star6 = 1.3909（0.9391 已占其 **68%**）→ 单位增益会把"磨圆"重新分类成"另一个形状" |
| E2 `_mirror` 真 bug 已修 | ✅ 原写法 `(x,y)→(-y,x)` 行列式 **+1，是 1/4 转不是镜像** → `allow_mirror` 对手性对完全无效（开关两侧同为 0.6181）；改用 `alignment.mirror_points` 后 off=0.6181 / on=**0.0000**，四臂的 `allow_mirror` 从此同一语义 |
| E3 Shape Context | ✅ 纯 numpy 自实现（`shape_context_baseline.py` 717 行，与论文的 MATLAB 差异逐条写入 POLICY `deviations_from_the_paper`）：对数极坐标上下文 + **chi-square** + `linear_sum_assignment`（EMD 只作说明，不用：无可复现的 EMD 实现依赖，且代价 O(n³)）；`orientation="sample"`（24 步旋转搜索）与 `"canonical"` 两档 |
| E3 分数不对称（实测，不掩饰） | ✅ 前 40 个图库成员 780 对：median **0.0061** / mean **0.0092** / max **0.0610**（0–1 分度），45 对完全相等、**735 对非零** → POLICY 明写"排序对参数顺序敏感，实验必须固定 `d(query, candidate)` 方向" |
| E3 代价实测 | ✅ 179 候选 / 查询：sample **7.18 / 6.95 s**（两次完整运行只差这一行墙钟，±3%）、canonical **0.37 s**（两次相同）≈ 24× 匹配量。全量 18 查询 × 3 设置 × 两档在 C 里要**报告耗时而不是隐藏它** |
| 每个写进 POLICY 的数字都能重跑 | ✅ 新增 [`verify_policy_claims.py`](../experiments/p0_perceptual_probe/verify_policy_claims.py)：逐条 `[ok]` 比对 POLICY 引用值与本次实测值，**超容差即非零退出**（约 4 分钟）；三臂 POLICY 新增 `measurements` 键指向它，契约测试断言脚本存在且仍 import 对应模块 → 该做法固化为 **D48** |
| 本轮修掉的转录错误 | ✅ 该脚本首版把被否决的漂移公式误写成 `min(abs(left), right)`（少一个 `abs`），实测得到 **−17.0258** 的负代价而非 3.9716 → 与仓库内公式对齐后复现 3.9716；**负数没有进任何文档或 POLICY**，但记录在此：把开发期脚本转正时会带进转录错误 |
| 数据回归（`_bevel` 修复的下游） | ✅ `5a9f1bc` 修复后离线重建（`1b8461c`）：`gallery.jsonl` `3a764664…` → **`15be97ae…`**、`control_gallery.png` `8c07d12a…` → **`585ffb28…`**（1 184 570 → **1 184 600 B**）、`labels.json` `26e444ce…` **不变**，manifest `reproduce.git_commit` = `5a9f1bcc…`；E3 不对称普查在重建前后**逐位相同** |
| 自动化测试 | ✅ **605 passed**（A 的 498 + `test_perceptual_gallery.py` 磨损星 1 + `test_salient_structure.py` 28 + `test_shape_context_baseline.py` 27 + `test_perceptual_features.py` 24 + `test_perceptual_contract.py` 27）；`ruff check .` All checks passed；`mypy` strict **31 文件** no issues |

**Checkpoint B 能主张的**：四臂在同一冻结图库上都可运行、输入校验 / 平票规则 / 参数口径一致，
每臂的不变性与**被否决的公式**都由实测数字钉住且一条命令可复现；E0（产品链路）默认行为未变。
**Checkpoint B 不能主张的**：任何检索命中率 / mAP / Top-K 排名 / "哪个方法更符合肉眼"
——**本轮一个检索评价都没做**，只有对合成形状与图库成员的自测量探针；不得把 POLICY 的自测量
数字称"人眼匹配准确率"；不得把 E1 的 ShapeComp-inspired 近邻当人类真值；T1/T2/T3 指标仍未计算。
已知局限（留给 C/D）：E1 的各向同性回退使切向角块在正多边形/星形上不旋转不变（已量化、**未修复**，
修复需形状族特定的轴约定 → 过度设计）；E1 距离自拟合、跨库不可比；E3 sample 模式昂贵；
包络匹配 crescent/heart/star 仅部分达标仍是图库属性。
**当时（B 结束时）的下一步 = Checkpoint C**（E0–E3 同集跑 T1/T2/T3 + 逐查询 Top-K 对比图），
**C 已完成，见下节**；B 留的三条测量学口径（E1 归一化、E3 方向、E1 orientation）在 C 里固定并随产物发布证据（D49）。
**再下一步 = Checkpoint D**（T1/T2/T3 分开解释 + 不变性探针 + 人工盲评 + 结论）。
**仍未进入 P0-5 / P0-6，未做 Web 产品开发。**

### Checkpoint C：四臂同集运行 + 逐查询 Top-K 对比图 ✅

| 项 | 状态 |
|----|------|
| 任务边界 | C 做到「E0–E3 在同一冻结图库上跑完 T1/T2/T3 + 24 张对比图 + 结果文件 + 表格由脚本打印」即停止。**未做**指标解释结论、**未做**不变性曲线、**未做**人工盲评（均属 D），**未进入** P0-5/P0-6，**未开发** Web 产品 |
| 先固定的三条比较口径（D49） | ✅ ① E1 归一化在 **179 成员语料** 上 minmax 拟合一次并传给全部设置（`e1_normalizer`：91 描述子、`orientation=canonical`、7 个退化描述子；代价实测——真实索引 76 349 个描述子单元里 0.5% 越界，最差 `curv00_w32_concave_share`）；② E3 固定 `d(query, candidate)`（`e3_direction_probe`：6 对反向 median 0.0071 / max 0.0113）；③ E1 `orientation=canonical`（`raw` 是另一个方法）。不固定则三个设置的数字互相不可比 |
| 四臂评价 | ✅ 18 查询 × 4 臂 × 3 设置 = 216 次 **全库** 评分（**无粗筛窗口**，与产品链路的 `shortlist_size=200` 明确区分并在 `method_status.json` 记录）；库行数 T1 19–35 / T2 177–178 / T3 1016–1017；T3 的 839 真实候选逐候选标 provenance，**未合并进正式索引**（`data/index` 三份产物哈希 `fbaf4933…` / `e18c3f95…` / `b7255ede…` 未变） |
| 指标（`report_checkpoint_c.py` 打印，禁止手抄） | T2（判别性主设置）`hit@1 / hit@10 / ap` —— E0 .667/.889/.353，E1 .611/.944/.435，E2 .556/.944/.376，**E3 .667/1.000/.449**；T3（混入 839 真实候选）—— E0 .556/.722/.239，E1 .389/.778/.259，E2 .444/.722/.277，**E3 .667/.889/.322**；T1 自测四臂 `hit@10` 均 1.000。**没有一臂全面占优**：E3 的 `ap` 最高但每查询 40 s，E1 在 T1 最高但在 T3 的 `hit@1` 最低。完整 T1/T2/T3 表（`aggregate` / `top_slots` / `label_pressure` / 逐查询 Top-1 / `exact_target_self_rank`）由命令打印，见 notes.md §13.2 |
| 标签 vs 人眼的分裂（必须先读） | ✅ 圆盘类被故意标成星/新月族困难负例：`ph-circle-fill` vs `syn-circle` 的 E0 距离 **7.28316078840726e-05**（阈值 `near_max_align=0.02`）→ 图上正确、标签 miss。CSV 用 `align_indistinguishable_from_query` 单列标出，表里用 Top-1 的 `x` 记号与 `top1-lookalike` 比例计数（T2：E0/E1/E3 0.22、E2 0.11） |
| T1 自测（实现不变性） | ✅ `exact_target_self_rank`：E0/E1/E3 **18/18 rank 1**；**E2 17/18，最差 rank 4 → 未解释，留给 D**，在此之前不得写"四臂通过自测" |
| 对比图 | ✅ 24 张（7 结构族 × 3 设置 `comparison_*.png`，用 `--top 5`：**行 = 查询 × 四臂**（每查询连续 4 行 `E0→E1→E2→E3`）、**列 = 查询 + 该臂自己的 Top-1…Top-5**；+ 3 张 `perception_comparison_overview_*.png`，其表由 `metrics.json` 打印，下面是每查询 × 四臂的 Top-1）；候选填色按 **冻结标签**（REL 绿 `#12623c`/`#d8ecdd`，非 REL 蓝 `#28556e`/`#7fb2d9`），每格叠加查询轮廓：E0 格画 **它实际拟合到的姿态**（绿实环 `#1b7f4b`），E1/E2/E3 只画标准器坐标里的查询原样（红虚环 `#a4231d`）——它们不返回逐候选姿态，画成"已对齐"即是造假。行/列/颜色/环的逐条语义 + 人工检查清单见 [`notes.md`](../experiments/p0_perceptual_probe/notes.md) §13.5.1 |
| 结果文件 | ✅ `results.csv`（20 列 3 601 行：Top-k + 全部带标签行）、`metrics.json`（含 `protocol` / 三条固定口径 / `claim_limits`）、`method_status.json`（四臂模块 + POLICY + 契约）、`figures_manifest.json`（inputs/outputs 的 sha256 + `reproduce.git_commit` + 3 条命令）；每个文件的读法与"不能读成什么"写在 [`notes.md`](../experiments/p0_perceptual_probe/notes.md) §13.5 |
| 耗时（按 §11 要求报告而非隐藏） | ✅ E3 `sample` T2 7.01 s/查询、T3 40.32 s/查询（缺陷修复前那次是 7.60 / 43.18）；`sample/canonical` 比值 18.7–19.4×（全量 E3 在 T3 上约 12 分钟）。整套评价墙钟 `wall_seconds = 1151.1 s`（前一次 1247.8 s），**本机数字不可跨机复现** |
| 本轮修掉的三个真实缺陷 | ✅ ① `relevant` 曾按 **库行** 索引读取位置对齐的 `flags`（会静默错标 REL）；② `align_indistinguishable_from_query` 在 E0 自己的行上全空（lookalike 集合晚于 CSV 写出），修法是 E0 先跑（`arm_order`），回归测试在修复前提交上确实失败；③ Top-1 表 36 字符地貌 id 造成列宽粘连 → 动态列宽 + 对齐测试。**缺陷 ② 使第一份完整产物作废重跑**（评分正确、解释列错误 → 不手工补 CSV） |
| `control_gallery.png` 归属 | ✅ 可视化脚本曾无条件重画它并改变字节（`585ffb28…` → `e294997d…`，根因是 `gallery.jsonl` 轮廓只存 6 位小数）→ 已恢复原文件，重画改为显式 `--with-control` / `--control-only`（D49：谁生成二进制，只有它能重生成） |
| review F3/F4/F5/F6 | ✅ F5（评测脚本无四臂开关 / `--control-only` 空转）→ 新增 `evaluate_perceptual_methods.py`，`--control-only` 现在真生效；F4（产物无 sha256 清单）→ `figures_manifest.json`；F6（盲评图归属冲突）→ **定为 D**，C 只出 comparison + overview + CSV/JSON；F3（短名单扫描是粗筛 only 口径）→ P0-4 notes §9.4 已补口径与 `G1/mirror` 粗筛 1.0 vs 精排 0.1667 的两阶段解释 |
| 自动化测试 | ✅ `pytest -o addopts="" -q` → **621 passed**；`ruff check .` All checks passed；`mypy` no issues in 31 source files；`mypy --strict` 对 3 个 C 脚本（runner / visualizer / 表格打印器）clean |
| 确定性核验 | ✅ 缺陷修复前后各跑一遍完整评价，`results.csv` 的评分列（`setting,arm,query_id,rank,candidate_id,distance,relevant,in_top_k` + 六列臂内附加列）**3 601 行逐字节相同**；两列刻意不参与比对（墙钟列，和 `05c50b6` 本来就要改的 `align_indistinguishable_from_query`）→ 差异只可能来自被修的缺陷，不是随机性。21 张 comparison 图字节相同，3 张 overview 只因表里印了 `sec/query` 而变 |

**Checkpoint C 能主张的**：四臂在同一冻结图库上的检索指标、排名、逐查询 Top-1/Top-5 对比图
已实测并可由三条命令复现；E0（产品链路）的默认行为未变，评价用的全库打分与产品链路的粗筛窗口
是两件不同的事，已分别记录。
**Checkpoint C 不能主张的**：这些指标 **不是** "人眼匹配准确率"（无人工评分）；
不得称 E1 为原始 ShapeComp，也不得把它的距离跨语料比较；T3 里真实地貌进 Top-k **不是假阳性**
（无相关性标签，池塘不可能是星的变体）；时延是本机墙钟；E2 的 self-rank 异常未解释；
**仍未进入 P0-5（分级曲线）与 P0-6（真人手绘 ≥10，未获授权），未做 Web 产品开发。**
**下一步 = Checkpoint D**：T1/T2/T3 分开解释 + 不变性探针 + ≥3 人盲评（拿不到评分则交付
盲评图与评分表并如实标注"未获评分"）+ 结论与失败归因；E2 的 rank 4 需在 D 定位原因。

### Checkpoint D · D1：公平展示 + 可盲评的视觉材料 ✅（2026-11-07）

| 项 | 状态 |
|----|------|
| 任务边界 | D1 只做「让 C 的图可比、让评分可盲」：新增展示对齐器 + 盲评材料生成器 + 两类图 + 空评分表。**未做**视觉评分、**未做** E0–E3 诊断、**未做**混合检索实验、**未改**任何既有臂的产品代码与 C 的产物、**未进入** P0-5/P0-6/Web |
| C 的两个真实缺陷（本轮的立项理由） | ① **不可比**：`scripts/visualize_perceptual.py:317` 只对 E0 调用 `apply_alignment`（E0 返回逐候选姿态），E1/E2/E3 的格子画的是标准器坐标里的原样 → 红/绿环重叠度不是同一把尺子；② **不盲**：C 的 comparison 图把 `arm` / `item_id` / 距离 / REL 全印在格子上，`syn-heart-e3` 这类 id 本身就把族与角色念出来了 |
| 展示对齐器（新模块） | ✅ [`earthglyph/retrieval/display_alignment.py`](../earthglyph/retrieval/display_alignment.py)：代价 = **对称 chamfer**（不用 Fourier / shape context / turning 任何一台机器），搜索 = **仅旋转**（不做尺度拟合：标准器已把 RMS 半径钉成 1，再拟合尺度就违反"同尺寸同坐标系"），粗扫 3° → 0.25° 精扫。**它在排名读出之后才跑，因此动不了分数与名次**；`__all__` 只有三个符号，`ast` 解析出的 import 集合 ⊆ `{__future__, hashlib, typing, collections.abc, numpy, scipy.spatial.distance, earthglyph.retrieval.perceptual_methods}` → 结构上不可能偷用某一臂 |
| 两类图（A2 要求） | ✅ `unaligned`（候选按标准器存储原样画出，允许评审脑内旋转，注明"朝向不计分"）与 `display_aligned`（四臂候选统一用同一个展示拟合摆到查询上，页脚原文写明"该拟合不属于 E0..E3、只改图像"）。同一设置内**所有面板共用一个方形框**（实测 T2 `±1.937`、T3 `±1.897`，由 `frame_for()` 取全部被画轮廓的最大 `\|坐标\|×1.08`），`aspect="equal"`，不重采样、不平滑、不缩放 |
| 盲评页（A3 八条） | ✅ 每组 = 查询 + 四臂真实结果；臂名隐藏、A/B/C/D 由 `default_rng(20261107)` 按 (setting, query, rank) **逐组独立**置换；距离 / REL / 族 / 来源全部隐藏，只保留查询码、候选码、名次、字母；映射另存 `blind_mapping.json`（`reveal_rule`：写完 `visual_review_scores.csv` 之前不得打开）；渲染对四臂**逐字节同规则**（一个填色、一个线宽、一个框、无臂专属样式）；候选形状未被修改（只刚体旋转，`pdist` 不变，测试 `TestRigidOnly` 钉住） |
| 不透明编码 | ✅ `Q##` / `C####` 按**排序后的 id** 分配，故跨运行稳定；同一候选在 T2/T3 共用一个码。`item_id` 不上图，因为它编码族与角色（`qd-star-variant-02` = 星族 + 已标相关变体） |
| 材料规模 | 180 组（18 查询 × {T2,T3} × Top-5）× 4 臂 = **720 个待评单元**，两套视图 → `visual_review_template.csv` **1 440 行**，全部 `status=pending_review`、评分列全空。字母-臂均衡核验：每设置每臂恰好 90 次，单一字母最多承载某臂 35/90（E3→D, T2）→ 没有"永远是 B"这类可反推的规律 |
| 产物 | ✅ `experiments/p0_perceptual_probe/checkpoint_d/` 40 个生成文件 / 13 414 479 B（另加本检查点说明 `notes.md`）：7 张 `fair_*_t2.png`（带标签、左右双块对照）、14 张 `blind_{族}_{设置}.png`（盲评正文，display_aligned）、14 张 `blind_unaligned_{族}_{设置}.png`（同盲评的 unaligned 参照）、2 张 `blind_review_{t2,t3}.png`（单页总览）、`blind_mapping.json`、`visual_review_template.csv`、`display_poses.json`（439 条拟合缓存，按 (查询,候选) 字节 sha 校验，库变了不会命中旧姿态）；每个文件的读法与"不能读成什么"写在 [`checkpoint_d/notes.md`](../experiments/p0_perceptual_probe/checkpoint_d/notes.md) §D1.2 |
| C 的产物 | ✅ **只读**：`results.csv`（sha256 `ecf5bd45…`，926 636 B）与 `labels.json`（`26e444ce…`，118 796 B）由 `blind_mapping.json.inputs` 记哈希；`metrics.json` / `figures_manifest.json` / `notes.md` / 24 张 C 图与 `data/index` 未触碰，无覆盖 |
| 自动化测试 | ✅ `pytest -o addopts="" -q` → **643 passed**（+22：`test_display_alignment.py` 11 项含"只刚体/能恢复已知 37° 旋转/圆无法更优/缓存命中与失效/不依赖四臂/公开 API"，`test_blind_review.py` 11 项含"每组每臂恰一字母/同 seed 复现且换 seed 变化/组间独立置换/不发明不丢候选/码不透明且稳定/表序错乱即报错/缺格跳过不补/一设置一框/未加载轮廓即报错/模板 8 行且无臂列"）· `ruff check .` All checks passed · `mypy` no issues in 32 source files |
| 确定性核验 | ✅ 默认参数连跑两次到两个全新目录：37 张 PNG **md5 全同**、`visual_review_template.csv` **字节相同**、`blind_mapping.json` 除 `generated_at_utc` 外相同；再跑第三次写回 `checkpoint_d/`，与全新目录产物 md5 全同 → 入库字节 = 默认命令产物（先前草稿用的是一组更低的 DPI 手旗，已作废重生成，避免"复现命令"与产物不符） |

**D1 能主张的**：视觉材料已公平化且可盲评，材料本身可复现（种子 20261107 + 输入哈希 + 基线 commit `2b5cc966`）。
**D1 不能主张的**：`visual_review_template.csv` 是**空的**，本轮**没有**任何视觉分数；`display_aligned` 的
重叠度好看**不等于**该臂检索得准（拟合不属于任何臂）；受控图案不是真实地貌。
**下一步 = D2**：E0–E3 逐臂诊断（E1 心形/旋转/归一化漂移 + 固定查询与图库的特征组消融、E2 零事件地板之后的
具体失败与 DP 对应、E3 vs E0 逐查询全对照与耗时分解）+ `diagnostic_cases.png`；视觉能力可用则按 B2 做
`AI_visual_review`（先定标准、只读像素、不反复重评到某臂胜出）。

### Checkpoint D · D2：视觉复核 + E0–E3 逐臂归因 ✅（2026-11-07）

| 项 | 状态 |
|----|------|
| 任务边界 | D2 只做「读 C 的排名、解释名次、按 B2 做模型视觉复核」。**未**改任何臂的产品代码、**未**新增检索评价（`results.csv` / `metrics.json` 未动一行）、**未**进入 P0-5/P0-6/Web、**未**改正式默认算法 |
| 归因运行器 | ✅ [`scripts/diagnose_perceptual_arms.py`](../scripts/diagnose_perceptual_arms.py)：`frozen_rankings_read_not_recomputed: true`，全部名次读 C 的 `results.csv`；`arm_params` / `fit_gallery_normalizer` / `build_library` / `load_gallery` **从 C 的 runner import**（`__module__` 由测试钉住），因此 D2 不可能与 C 用不同参数而不自知。六个部分 `material,e0,e1,e2,e3,rotation`，默认全跑 |
| 视觉复核器 | ✅ [`scripts/visual_review_report.py`](../scripts/visual_review_report.py)：评分**先写死在 `SCORES` 表里**，之后才打开 `blind_mapping.json`；密钥与模板逐字母对账（四个字母全查），对不上直接 `SystemExit`。产物标 `reviewer = AI_visual_review`，**不是**真人受试者 |
| **材料缺陷（本轮最重要的新发现）** | ⚠️ 图库 179 成员里有 **11 组 / 31 个成员是逐位相同的轮廓**（点列 sha256 与 `origin_ref` 两条独立读法给出**完全相同**的分组）。40 个同形对里只有 **6 对**在 `labels.json.near_duplicate_pairs` 声明过 → **34 对未声明**。`circle({"radius": 1.0})` 一条配方被登记成 **7 个族**；一张真人 QuickDraw 圆（`key_id 5064695922819072`）同时是 band 硬负例、blob 变体、star 硬负例**和 blob 自己的查询**。后果：`qd-circle-query-01` / `qd-cloud-query-01` 的 T2 库里留着与自己逐位相同的行，T2「已剔除目标」的保证只对 id 成立、对形状不成立 |
| 这条缺陷改变了什么归因 | 凡是**跨 id 的 `d = 0.0`** 先查同形：E0 的 11 行"不可区分硬负例"= 3 行同形 + 8 行不同轮廓，**去重后只剩 5 个形状比较、其中 3 个真正不同**；E2 的 13 行 Top-5 零距离 = 3 行同形 + **10 行确实是不同形状且候选事件数全为 0**（E2 的结论不变，E0 的结论减弱 11 → 3） |
| **本轮撤回的两条上一版结论** | ① ~~"E0 的距离标尺是反的（0.02 带内相关项 0 个）"~~ —— `labels.json.rule` 在定义 relevant 时就排除了"对齐距离 ≤ 0.02 的近重复"，`count_indistinguishable_relevant = 0` **按构造必然成立**，只是标注/打分一致性检查；② ~~"E0 把 6 个族的内部结构看成一样"~~ —— 那 6 行是同一条轮廓被登记 6 次 |
| C1 · E0 | T2 Top-1 相关率 0.667；简单族（band/blob/irregular，8 查询）0.375；`syn-*` 7 个查询 6 个判对（d ≤ 0.106）；视觉 Top-1 15/18 组 3 分。真正的失效模式只剩「不改变外包络的内部改造」，证据量 3 个比较 |
| C2 · E1 | `ph-heart-fill` Top-1 = `qd-circle-02`（真人圆），尖头心形掉到 57 / 49；分组距离**平方相加**，`curvature` 45.8 % + `histogram` 50.5 % = **平方距离 96.4 %**。冻结消融（查询与图库不动、不调权重）：去掉 `moments` 或 `curvature` 反而 +0.056，`fourier` 是唯一不能去的组（−0.111），`curvature` 单独用最差（0.444）。归一化漂移：839 条真实地貌上 407 / 76 349 格越界 = **0.5331 %**，最严重 `curv00_w32_concave_share`（4.7676 %），自身语料 0 越界 |
| C3 · E2 | (a) T1 那个 rank 4 = `syn-blob-query`：自距离恰好 0.0，T1 库恰好 5 行同为 0.0 且显著事件数全 0，名次由 `stable` 排序行序决定 → **不是实现错误**。(b) 零事件普查：1018 形状 19 个（1.87 %）零事件。(c) 丢一个 0.3 rad 凸角 = 0.0477（"不可区分"阈值的 2.4 倍）。(c-2) **删角 vs 老实匹配**：7 个可比查询里删角只赢 **2 次**（心形 +0.0201、`syn-irregular-query` +0.1534），输 5 次 → 撤回"DP 系统性偏好删除"，改成"价格低到足以在个别查询翻盘，心形正是其一"。(d) 心形唯一的凹事件在 Top-3 全部消失。(e) 心形/星形事件**只存在于最细窗口 1/64**，粗一档即归零 |
| C4 · E3 vs E0 | 逐查询第一相关名次：E3 更好 **4** / 更差 **1** / 平 **13**（`qd-circle-query-01` 上 E0 4 vs E3 5）→ 增益是**局部的**，集中在 E0 完全失败的查询。耗时（入库实测）：`orientation="sample"` 7.963 s vs `canonical` 0.414 s vs 单步 0.424 s → **旋转网格占 94.67 %**、比 19.24×、每候选 0.0450 s |
| 不变性探针 | 同一形状转一个角度再查：E0 名次 spread 0（完全不变）、E1 0.056（1 次翻转）、E2 0.556（最大 6）、E3 0.667（3 查询抽样） |
| 视觉复核范围（先声明再给数） | 已评 **500 格** = T2 全部 18 查询 × Top-5 × 4 字母（360）+ T3 每族 1 个代表查询（140）。**未评**：`unaligned` 视图全部、T3 其余查询 → 模板里仍是 `pending_review`，任何数字都不覆盖它们 |
| T2 结果（`AI_visual_review`） | Top-1 / Top-3 / Top-5 均分、NDCG@5、明显错误率：E0 2.667 / 2.352 / 2.278 / 0.969 / 23.3 %；**E1 2.778 / 2.630 / 2.500 / 0.977 / 15.6 %**；E2 2.222 / 2.111 / 2.044 / 0.892 / 30.0 %；E3 2.722 / 2.593 / 2.467 / 0.973 / 17.8 % |
| T3 结果（7 个代表查询） | E2 最差（1.571 / 1.629，明显错误率 **48.6 %**），E3 最好（2.429 / 2.314，NDCG@5 0.998） |
| 三个会被读错的地方 | ① **低置信率四臂恒等**（T2 33.3 % = 30/90、T3 34.3 % = 12/35）是构造性的：一组要么低置信要么不是，每臂在每组恰好一格 → 该列对臂间比较**零信息**；② T3 族表每族只有 1 查询 5 格，**不可用**作族级结论；③ NDCG@5 在整组 0 分时按定义记 1.0，会**抬高**彻底失败的臂 |
| `low_confidence` 的可核验性 | 由 `hard_groups_audit()` 输出 `{declared, also_flagged_by_the_rule, flagged_only_by_the_list}`：声明的 5 个难组**全部**已被机械规则（四个不同候选同分）覆盖，`flagged_only_by_the_list = []` → 该列**不是手设**。测试 `test_the_declared_hard_list_adds_no_hand_set_flag` 钉住 |
| 距离 ≠ 视觉分（实测） | 四臂 Pearson(距离, Top-1 分) = E0 −0.747 / E1 −0.625 / E2 −0.303 / E3 −0.560（n=18）：方向对、强度不足以换算，且四臂量纲不同（E1 的 Top-1 距离最大 1.834）。E0 的 Top-1 从 `d=0.000000`（判 3 分）到 `d=0.410148`（判 0 分）都有 |
| 产物 | ✅ `checkpoint_d/`：`arm_diagnostics.json`（六部分 + `material` 交叉核验）、`diagnostic_cases.png`（5 面板，**重复轮廓面板排在最前**，E0 面板按形状去重）、`visual_review_scores.csv`（500 行逐格原始分）、`visual_review_summary.json`（含 `low_confidence_audit` 与口径声明）、`visual_review_examples.png`（每臂最好+最差，两端都画） |
| 自动化测试 | ✅ `pytest -o addopts="" -q` → **701 passed**（+58：`test_arm_diagnostics.py` 33 项 + `test_visual_review_report.py` 25 项，含"同形组真的同形 / provenance 与哈希两条读法必须一致 / 零距离的两种原因必须拆开 / 删角表必须双向 / 盲评四字母全对账 / 低置信列非手设"）· `ruff check .` All checks passed · `mypy` no issues in 32 source files |
| 复现 | 归因 `python scripts/diagnose_perceptual_arms.py`（入库那次墙钟合计 564 s ≈ 9.4 min：e1 267.6 s、rotation 284.4 s 占大头）；视觉复核 `python scripts/visual_review_report.py` |

**D2 能主张的**：四个臂的失效模式各自定位到具体查询、具体源码常量与具体形状对，可复现；
E2 的 T1 rank-4 是稳定排序 tie-break 而非实现错误；E3 相对 E0 的增益是局部的；
材料缺陷（11 组同形轮廓）是独立于任何臂的事实。
**D2 不能主张的**：`AI_visual_review` 不是真人实验；0.5331 % 越界不等于"E1 在真实地貌上失效"；
~19× 旋转开销不可忽略；消融 Δ 不等于"改进后的 E1"；T3 族表与 3 查询旋转抽样不是结论；
`unaligned` 视图下结论是否相同**未知**（未评）。
**下一步 = D3**：E0 粗筛 + E3 精排的实验型混合检索器（窗口 20/50/100/200/全库，
Full-E3 Top-5/Top-10 覆盖率、T2 NDCG@10 / MRR、T3 变体名次、E0 粗筛与 E3 精排分段耗时、
被粗筛排除的候选台账），**不改正式默认算法**。

### Checkpoint D · D3：E0 粗筛 + E3 精排的两级实验 ✅（2026-11-07）

| 项 | 结果 |
|---|---|
| 问题 | E3 单查询全库 7.96 s（T2）/ 41.7 s（T3），E0 只要 0.016 s / 0.089 s。**让 E0 只筛人、E3 只排序**能省多少时间、丢多少答案？预计算成本必须计入，不许悄悄减掉再宣称更快 |
| 契约（`payload.contract`） | ① E0 不贡献分数：窗口内只按 E3 距离排序、平手按库行号（D42），打乱 E0 距离 Top-10 一个字节不变（测试钉住）；② E3 参数逐臂相同（`orientation="sample"` / 24 步 / 5×15 格，原文入库）；③ 每查询时间 = 缓存重建 + 精排；④ **生产默认未改**：`search_by_contour` 仍是 `fourier_align` / `shortlist_size=200`，入口文件无 hybrid 与缓存 API 引用（测试钉住） |
| T2（库 177/178 行） | Full-E3 NDCG@10 **0.7329** / 7.2538 s；@20 0.6897 / 0.8817 s（**8.2×**）、@50 0.7147 / 2.0823 s、@100 0.7263 / 4.1063 s、@200 0.7329 / 7.2468 s。Top-10 与 Full-E3 一致率 0.817 / 0.922 / 0.961 / 1.000。**hit@1 六臂全同（0.667）** → T2 上筛人不改变谁排第一。@200 与 Full-E3 逐位相同是**自检行**（200 窗口 = 全库），不是成果 |
| T3（库 1016/1017 行，含 839 真实地貌） | Full-E3 **0.6358** / 41.7052 s；@20 0.5679 / 1.2064 s（**34.6×**）、@50 0.5750 / 2.4046 s、@100 0.6041 / 4.4182 s（9.4×）、@200 0.6066 / 8.4394 s。**没有任何窗口追平 Full-E3 的 NDCG@10**（最好的 @200 仍差 0.029）→ 真实地貌把粗筛代价放大 |
| 两条**不能**写成"混合更好"的数 | ① T3@100 hit@1 0.722 > Full-E3 0.667：只有 `ph-tree-structure-fill` 从 2 变 1，而两个名次**都是相关项**（Full-E3 的第 1 名 `syn-forked-sharp` 在 E0 里排 377，≤200 的窗口看不见它）——18 查询里 1 个翻转不是结论；② `first_relevant_rank` 均值 T3@100 = 20.17，合计 363 里 **333 来自两个查询**（254 + 79），中位数仍是 1 |
| 排除台账 | 一行当且仅当 `e0_rank > window`（同一候选在更小窗口各记一次，列和 = 行数）：T2 54 行（33/14/7/0）、T3 96 行（40/26/19/11）。**去重后**被排除的相关项 T2 15 对（forked 11 / band 3 / star 1）、T3 12 对（forked 8 / star 2 / heart 1 / blob 1）→ **枝状占 73 % / 67 %**，与 D2.6 的 E0 失效模式同源；新信息是 **E0 会把 E3 的正确答案筛到看不见的位置**。连 W=200 也救不回的只有 T3 的 2 对（E3 第 2 ↔ E0 第 377；E3 第 7 ↔ E0 第 629） |
| 台账的低估 | 它只跟 Full-E3 的 Top-10。`ph-lightning-fill`（T3）第一个相关项 `qd-lightning-05` 是 E3 **第 35 名**、E0 第 **254** 名 → 四个窗口的 first_relevant_rank 全是 254 而 Full-E3 是 35，**台账里没有这一条**（单独复查得出）。任何"排除项总数"都低估损失 |
| 描述子缓存 | 新 API `build_library_descriptors` / `score_library_from_descriptors`（生产路径未改）。逐位等价：T2 177 行、T3 1016 行 `max_abs_difference = 0.0` / `identical = true` / `rankings_identical = true`。**但只省 0.94 %（T2 0.0689/7.3435 s）与 1.12 %（T3 0.0893/7.9514 s）**，因为 `score_library` 本来就把库侧描述子每次调用只建一次；内存 6 796 800 B / 39 014 400 B（每行 38 400 B = 64 点 × 75 格 × 8 B）。**加速全部来自"少打分多少候选"**（每候选 ~0.040 s，与 D2.9 的 0.045 s 同量级），不得用缓存主张提速 |
| 本轮两次自我纠正 | ① `shape_context_baseline.py` 的越界检查在实验**运行期间**因 mypy 报错改过一次（`selection.min()` → `min(rows)`）；用改后代码重跑 `ph-heart-fill` T2 @20/@50 与入库 CSV 逐行比，**40 个距离最大绝对差 0.000e+00**、id 与两个 rank 列全同 → 入库数字与提交代码一致，这条是测出来的；② 窗口图第 3 面板对数轴把小刻度印成"3 × 10¹"压在 50 上，只改绘图 + 新增 `--redraw-from-json`，数据文件 mtime 与 md5 未变（JSON `a1eeb812…` / CSV `8a86510b…` 16:46，PNG 16:53） |
| 产物 | ✅ `checkpoint_d/`：`hybrid_retrieval_metrics.json`（contract + 两设置 summary + 排除台账 + 两个完整性探针）、`hybrid_retrieval_results.csv`（2160 行 = 2 设置 × 18 查询 × 6 臂 × Top-10，含 `scored_by_e3` / `e0_rank` / `full_e3_rank`）、`hybrid_window_comparison.png`（4 面板：Top-10 一致率 / labelled NDCG@10 / 秒（对数）/ 从未到达 E3 的 Top-10 数） |
| 自动化测试 | ✅ `pytest -o addopts="" -q` → **726 passed**（+25：`test_hybrid_retrieval.py`，含"E0 距离打乱名次不变 / 平手按行号 / 缓存与不缓存逐位相同 / 窗口>库 = Full-E3 / 台账双向 / 生产默认仍是 E0 / 重画不改数据"）· `ruff check .` All checks passed · `mypy` no issues in 32 source files |
| 复现 | `python scripts/hybrid_e0_e3_retrieval.py`（本机墙钟 **2297.6 s** ≈ 38 min：T2 到 521.3 s、T3 到 2297.6 s）；只重画图 `--redraw-from-json <metrics.json>` |

**D3 能主张的**：两级结构在本冻结图库上把 E3 每查询代价降到 1/8.2（T2@20）与 1/34.6（T3@20），
且预计算已计入；代价与窗口线性相关；E0 粗筛在枝状族上系统性排除 E3 的正确答案（独立于 D2 的第二次证据）；
缓存逐位等价但不提速。**D3 不能主张的**：T3@100 的 hit@1 优势（1 查询翻转）；把 T2 加速比外推到生产；
用 `first_relevant_rank` 均值主张整体质量；把台账当完整损失清单；hybrid 已上线（生产默认未改，
也没有"只含真实地貌"的库：T3 库始终是 1016/1017 行混合，839 行地貌只是其中一部分）。
**订正**：上一版这里还写着 hybrid "没在真人手绘查询上评价过"，数量上是错的——18 个查询里
**5 个是 QuickDraw 真人手绘**，两级每一档都跑了它们（见 D4.1 末段）。
**下一步 = D4**：汇总 D1–D3 证据，回答任务书 §7 的五个问题，更新 TASKS / EXPERIMENTS /
DECISIONS / 实验日志并推送；**不改正式默认算法**，是否改用 E3 精排留给用户审查后决定。

### Checkpoint D · D4：汇总与决策建议 ✅（2026-11-07）

完整论证在 `experiments/p0_perceptual_probe/checkpoint_d/notes.md` 的 D4 段（含每条数字的来源产物）。

| 问题 | 一句话答案 | 支撑它的实测 |
|---|---|---|
| ① 哪个算法视觉上更像 | **没有全面胜出的一臂，而且"第一是谁"取决于查询来源**：合并 25 组 Top-1 E3 2.640 > E1 2.560 > E0 2.520 > E2 2.040；T2 全 18 查询第一是 E1（2.778）；**只看 5 个真人手绘查询**则是 E1 2.360 > E3 2.160 > E2 2.080 > **E0 1.840**（E0 在真人组比非真人组低 0.606，四臂最大落差） | `visual_review_summary.json`（`reviewer = AI_visual_review`，500 格）+ 对同一 CSV 按 `query_id` 前缀重新切片（未重打分）；差距 0.056–0.120 在 18–25 组上**不足以换默认**，n=5 的真人子集更不足以 |
| ② E3 的优势普遍吗 | **不普遍，是局部的**：逐查询第一相关名次 赢 4 / 输 1 / 平 13，赢例集中在 E0 完全无答案的查询，heart / crescent / star **零赢例** | `arm_diagnostics.json` D2.9 + D3 覆盖率（Full-E3 Top-10 在 @20 保住 81.7 % / 77.8 %） |
| ③ E2 为什么没达到预期 | **标定问题，不是实现 bug**：删一个 0.3 rad 凸角价格 0.0477 = "不可区分"阈值 0.02 的 2.4 倍；心形/星形凹事件只存在于最细窗口 1/64；但删角在 7 个可比查询里只赢 2 次（"系统性偏好删除"已撤回）。T1 那个 rank 4 是 stable 排序 tie-break，已定位 | `arm_diagnostics.json` e2 段 + `salient_structure.py` 的 `GAP_COST` / `DEFORMATION_BUDGET` / `DEFAULT_SCALES` |
| ④ E0+E3 两级值得采用吗 | **作为实验值得、作为默认还不行**：T2 @50 用 3.5× 换 NDCG@10 −0.018；T3 @100 用 9.4× 换 −0.032，**没有任何窗口追平 Full-E3**；且两级**继承 E0 的枝状盲区**（E3 第 2 名 ↔ E0 第 377，W=200 也救不回）。要用就窗口 ≥100 + 给粗筛补召回通道 | `hybrid_retrieval_metrics.json`（含 `cache_equivalence` 逐位 0.0、台账 54 + 96 行、分段耗时） |
| ⑤ 下一阶段投什么 | **顺序固定：修图库同形缺陷 → 把真人手绘查询从 5 个补到 ≥10 个不同用户 + ≥3 人盲评 → 攻枝状覆盖/保真 → 再谈两级粗筛替换与 E1 重拟合。不建议现在做：修 E2、扩真实地貌库。** | 枝状是**跨臂共同**最差族（视觉 Top-1 全 ≤1.8；真人画的那条枝状四臂 0.8–1.8，是真人组最差一格）+ 粗筛排除项 73 %/67 % 是 forked + P0-4 `row 294` 全库 23–502 名，四条独立证据同族；扩库暂缓因为 C 的 T2→T3 四臂 NDCG 全部下降（−0.108 ~ −0.169） |

**D4 能主张的**：五个问题都有产物级证据；`AI_visual_review` 的口径、污染与未评范围已随产物声明；
两级结构的折中、缓存的无效与台账的低估都写进了同一份产物；
**"E0 在真人手绘上明显更不像"有实测切片支持**（1.840 vs 2.446），虽然 n=5。
**D4 不能主张的**：真人相似度结论（真人手绘查询 5 个 < 门禁要求的 10 个，且人工判读 **0 人**）；
`unaligned` 视图的任何结论（未评）；
E3/E1 相对 E0 的"更好"达到可换默认的量级；E2 修好就能赢（未修，也未重测）；
两级结构可用于生产（粗筛盲区未解决）。
**门禁状态：P0 仍未通过 → 不进入 P1 Web 前端开发。** 是否把 E3 设为正式精排，留给用户审查后决定。

## P0-4.6 ShapeComp 官方复现与独立形状感知检索基准（2026-11-07，进行中）

任务边界：**完全排除 Overture Maps、水体候选与地理坐标**；不扩大真实地貌库；不改正式产品检索默认；
不复用旧图库 `REL` 标签当人类真值；不覆盖 P0-4 / P0-4.5 产物。
产物目录 [`experiments/shape_perception_benchmark/`](../experiments/shape_perception_benchmark/README.md)，
运行日志 [`notes.md`](../experiments/shape_perception_benchmark/notes.md)。

核心问题：**一个经人类感知实验验证的形状模型，能否找到人眼判断相似的二维轮廓，并胜过 EarthGlyph 现有几何匹配器？**

### Checkpoint 1：来源审计 + 官方实现可用性 ✅（`1e4d6ca`）

| 项 | 实测结论 |
|---|---|
| 官方代码能否本地运行 | **不能**。Dropbox / `drive.google.com` / `sites.google.com` 全部网络阻断（curl 证据在 `official_shapecomp_status.json`）；本机无 MATLAB、无 Octave、无 TensorFlow；未安装任何商业软件、未购买许可 |
| 两个 Zenodo 存档里有什么 | 用 HTTP Range 读 ZIP 中央目录（**传输 8 MB / 存档 308 MB + 2.23 GB**）逐一枚举：**没有**描述子引擎、没有 Demo、没有 `.py`、没有权重。大修订多出的 `.m/.c/.mex` 是 vendored psignifit（GPLv3）+ Psychtoolbox |
| 但官方**输出**是公开的 | `Fig5B_shapeStim4RatingExpt.mat` 带 `shape1SC/shape2SC`（22 维）与 `dist`，且 `dist == ‖SC1−SC2‖` 残差 **4.44e-16** → `S0-MDS-precomputed` 是合法一臂，**仅限这 500 个已发布刺激**，不能给新形状打分 |
| 人类数据 | **H1**：250 对 × **14 名被试**逐列评分，0 NaN，500/500 轮廓唯一（无 P0-4.5 的重复缺陷）→ 主真值。**H2**：15 个 Li et al. 形状，人类 MDS 坐标是 `Figure9.m` 用 `getpts` 从图 7D **数字化**来的 → 仅辅助，永不与 H1 合并 |
| 被拒绝的"评分" | Figure 3 `GANlabels.mat` 是自由文本**命名**任务（"cow udder"/"italy"/"x"），文件名带 ratings 也不是相似度 → 不用 |
| 未获取 | Mendeley 2D Shape Structure（API 域名不解析、页面是标题为 FAQ 的 JS 壳）；Li et al. 完整 360 形状与逐试次原始判断 |
| 管线自检（不是新结果） | 从原始 `.mat` 复现论文两个头条数：H1 Pearson **−0.904** / Spearman **−0.911**（论文 0.91）；H2 Pearson **0.778**（论文 0.78） |

复现：`.venv/Scripts/python.exe scripts/fetch_shape_benchmark_sources.py`
（Zenodo 9 次 Range 请求 / 8 072 354 B；MPEG-7 3 380 427 B，sha256 `ce8d8740…`）。
**未写任何 109 维描述子的 Python 移植**——任务书禁止把自写描述子集冒充 ShapeComp 复现。

### Checkpoint 2：独立数据加载器 + 统一检索接口 ✅（`46e2fad`）

- `earthglyph/shape_benchmark/datasets.py`：H1 / H2 / MPEG-7 加载器，每个形状同时带
  128 点算法契约（`points`）与原始发布姿态（`display_points`，**只用于画图，绝不参与排名**）。
- `earthglyph/shape_benchmark/retrieval.py`：`Gallery` 一次性预计算各臂语料；
  `search_shape(query, gallery, method, top_k=10, exclude=...)` 返回**完整排名** + 逐候选距离 + 墙钟时间。
- `scripts/search_shape_benchmark.py`：任务书要求的 CLI（`--query/--gallery/--method/--top-k`）。
- 实测修正：发布轮廓是非均匀采样的闭合多边形（MATLAB `patch` 隐式封口），首尾弦达 0.196 RMS
  超过草图开口守卫 0.15 → **显式重复首顶点**声明闭合，500/500 + 15/15 通过，**草图守卫未放宽**；
  MPEG-7 1400 张中 **1398 提取成功 / 2 张真失败**（`pocket-11/12` 各含两个不连通 blob），进排除台账。
- 成本实测：E0 0.1 ms/候选；E1 供定归一化器 + 缓存后 200 候选 0.01 s；**E3 41.9 ms/候选且是查询侧开销**，
  描述子缓存救不了 → D1 只算**真正被评过的 250 对**，全库排名留给 D2–D4。

验证：`pytest -q` → **744 passed**；`ruff check .` 全过；`mypy` 36 文件无问题。

### Checkpoint 3：真实 Top-10 检索 + 对比图 ✅（`5e84530`）

- **D2 去真目标检索**（MPEG-7 1398 形状 / 70 类，6 查询，T0–T3）：平均 class-recall@10
  T2/T3 = **E0 0.717 / E1 0.833 / E3 0.917**；E0 在 `tree-1` 上 Top-3 返回 flatfish/jar，
  E1/E3 返回 tree（见 overview 图）。**T3 未产生区分度**：三个异类硬干扰物未进入任何臂的
  Top-10（0/6），故 T2→T3 行相同、不含硬干扰鲁棒性信息（notes F19，诚实负结果）。
  完整排名存 `d2_full_rankings.npz`（非仅 Top-10）+ 排除台账 `excluded_candidates.csv`。
- **公平性缺陷在出数前被抓并修复**（notes F17）：`Gallery` 原先对每个候选集重拟合 E1 minmax
  归一化器，使 T3/D3 的"加干扰物"与"重缩放全部 E1 距离"成为同一变量。现改为继承 MPEG-7 归一化器；
  反事实实测：重拟合会移动 **27/91** 个描述子尺度、最大相对偏移 **22.1%**。新增 2 个回归测试。
- **D3 结构探针**（每族 1404 形状）：五族全部 `structure_beats_gross=true`；差异在裕量——
  E1 把粗干扰压得最深（star 1163）但把一个 star 变体挤出 Top-10（rank 65）；E0 保住全部变体
  却放干扰到 rank 23；E3 居中。**角色是构造的几何假设，非验证过的人类真值**，图上明示。
- **D4 变换稳定性**（350 形状，3 目标 × 9 变换）：**E0 不镜像不变**（Top-10 overlap 0.00，
  自匹配掉到 rank 246/163）；**E3 的 15° 旋转网格**使 37°/191° overlap 减半（0.54）；
  E1 最耐变换但镜像仍丢 2/3 Top-10。平移/缩放/起点三者 E0/E1 全 1.0。
- **图**：`perception_benchmark_overview.png` + 五族 `comparison_*.png` 各含 aligned 与
  `_original_pose` 两版（**排名完全相同，仅展示姿态不同**）+ 三张人类评价图。全部 PNG < 0.5 MB。
  图只读已存 CSV/JSON，**不重新排名**；展示对齐用与臂无关的 chamfer 旋转。
- **Checkpoint 1 的一处错误被纠正**（notes F20）：`Figure8NetComparison.mat` **确实带刺激**
  （1000 个 384×2 轮廓 + 25 维 GAN latent）与 MatNet/KerNet1/KerNet2 官方坐标；原"刺激不在该成员"
  的说法撤回。从原始数组复算作者 Figure 8D：r = 0.928/0.945/0.910（论文 0.93/0.94/0.91）。
  该 1000 形状**无人类评分**且与 H1 不相交 → 仅模型对模型；**未跑其检索臂**。
  证据 `results/figure8_network_audit.json`，复现 `scripts/audit_figure8_networks.py`。
- 单查询成本（~1400 候选）：E0 ≈ 0.1 s、E1 ≈ 12 s、E3 ≈ 58 s（CPU）。

验证：`pytest -q` → **777 passed**；`ruff check .` 全过；`mypy` 38 文件无问题。
复现：`run_shape_benchmark.py --stage d2 d3 d4` → `--stage summary` → `plot_shape_benchmark.py --figure all`。

### Checkpoint 4：人类评分相关性 + 工程结论 ✅（`a70c7bd`）

十问逐条回答在 [`notes.md` Checkpoint 4](../experiments/shape_perception_benchmark/notes.md)，此处摘要：

1. **官方本地跑了吗？没有。** 三个分发 host 全阻断、无 MATLAB/Octave/TF、未装商业软件未买许可、
   未写 109 维移植；本地只跑 E0/E1/E3。
2. **S0 是哪个变体？** `S0-MDS-precomputed`（作者发布 22 维坐标，H1 500 + H2 15）；
   `S0-descriptor` 不可用；MatNet/KerNet1/KerNet2 仅 `outputs_only`（Figure 8 / H2），从未执行网络。
3. **可核验人类相似度只有 H1**（250 对 × 14 被试逐列矩阵）；H2 是数字化辅助目标。
4. **仅类别/结构/模型分数**：MPEG-7 类别、D3 构造角色、Fig3 命名任务（拒）、2D Shape Structure（未获取）、
   Figure 8 网络（模型对模型）、H2（数字化）。均不当人类真值。
5. **谁与人类评分更相关？** S0 0.911 > E3 0.839 > E1 0.782 > E0 0.683；配对两级 bootstrap 六差全排除 0。
   可跑臂中 **E3** 最佳，且接近人类留一噪声上限 0.779。
6. **真实闭合轮廓 Top-10 谁更合理？** E3（T2/T3 recall@10 0.917 vs 0.833 / 0.717）；
   overview 图可见 E0 对 tree 查询返回 flatfish/jar。类别召回≠人类相似度，但与 D1 排序一致。
7. **角/缺口/分支更稳吗？** 部分是，且仅为几何假设：D3 全过但裕量有别；D4 局部形变降 overlap；
   无独立人类偏好记录。枝状/细长 GAN 轮廓恰是 E3 与人类分歧最大处（failure_cases 217/96/189/247/18）。
8. **三臂强弱**：E0 快但不镜像不变、人类相关最弱；E1 耐变换、压干扰最强、中等人类相关；
   E3 人类相关与类别召回最佳但 58 s/查询、15° 网格离格损失、对枝状系统性地"判得更近"。
9. **官方 footprint 适合 EarthGlyph 吗？不适合现成接入**：新形状投影需描述子引擎 + 25,712 语料 +
   MDS/Procrustes，或 MATLAB/TF + 权重；皆不可得；发布坐标路线查询期免费但**不能打分新形状**。
10. **值得做移植/近似/混合吗？值得做有界研究，暂不整合**：E3→S0 的 +0.072 是头部空间。
    下一步两项：Figure 8 检索臂（998 可加载形状 × 四条发布坐标路线，模型对模型、披露预处理混淆）；
    小回归器从 E1/E3 特征逼近发布 22 维坐标、在留出 H1 对上检验是否缩小 D1 差距。
    **不改正式默认、不跨 P0 门禁**；是否整合由用户决定。

遗留：官方代码/权重仍不可得；T3 干扰物太粗需换_part structure_ 匹配集；H2 与 Figure 8 永不并入 H1；
E0 镜像、E3 旋转网格为已测缺陷。验证：`pytest -q` 777 passed、`ruff` 全过、`mypy` 38 文件无问题。

### Checkpoint 5：官方 KerNet1/KerNet2 翻译为 Python 并实跑 ✅（`31e6b00`）

> 本检查点**部分取代** CP4 第 1/2/9/10 问的"官方本地跑不了"结论：用户手动下载工具箱后，
> 官方两个神经近似已本地实跑并验证；原始 109 维描述子引擎仍不可得。CP4 文字作为历史记录保留。

十问回答与逐条证据在 [`notes.md` Checkpoint 5](../experiments/shape_perception_benchmark/notes.md)，此处摘要：

- **触发与授权**：用户把官方工具箱下载到 `shapeComp_17_12_2024/`，经 AskUserQuestion 选定
  **h5py + numpy 路线**（无 TensorFlow、无重训练）+ **完整有界范围**"两网络 + 验证 + 检索对比"。
  **不改正式检索默认、不跨 P0 门禁。**
- **翻译了什么**：`earthglyph/shape_benchmark/shapecomp_port.py` 只翻译官方 MATLAB 预处理
  （质心 / 弧长重采样 / min-max 定位 / **保留顺时针强制** / 栅格化 / flipud），用 h5py 读官方
  `shape2sc22a.h5`（KerNet1）/`im2sc22d.h5`（KerNet2）权重并 numpy 逐字前向。**无 TF/GPU/重训练。**
  **没翻译**：109 维引擎（2024 工具箱不含）、MatNet、任何自写描述子近似（E1 仍标"inspired"）。
- **忠实度验证**（`scripts/validate_shapecomp_port.py` → `results/shapecomp_port_validation.json`）：
  **KerNet2 对 H2 发布坐标近乎精确**（逐维 r 0.9982、成对 Spearman 0.9989、RMSE 0.0065）；
  KerNet1/2 对 Figure 8 发布列 Spearman 0.980/0.957（残差=输入溯源，非端口 bug；跳过重采样塌到 ~0.56）。
  **H1 非忠实度测试**（其坐标是原始 109 描述子模型的 22 维 ShapeComp MDS，另一基；逐维 r 无意义，成对 Spearman≈0.93 衡量近似追踪）。
- **D1 六臂**（agreement ρ 正=更好）：S0 0.911 > **S0K1 0.903** > **S0K2 0.900** > E3 0.839 > E1 0.782 > E0 0.683。
  **网络间三差全含 0**（实跑网络与发布原始空间统计不可区分）；**每个 EarthGlyph 臂对每个网络臂差全排除 0**
  （E3−S0K1 −0.064 [−0.125,−0.013]、E0−S0K1 −0.220）。H2 辅助：S0K2 0.762 匹配发布 0.766，S0K1 0.630 较低
  （H2 目标由图像路线建）；永不并入 H1。
- **D2–D4 实跑对比**（`--methods E0 E1 E3 S0K1 S0K2`）：**E0/E1/E3 与 CP4 逐位一致**（确定性通过）。
  D2 类别 recall@10 E3 0.917 > E1 0.833 > S0K2 0.800 > S0K1 0.767 > E0 0.717（网络优化人类相似度而非类别；
  tree-1 S0K2 0.9 vs E0 0.0，bat-1 S0K2 0.2 vs E1/E3 0.9）；T3 仍 0/6。D3 五族全过。
  **D4 关键阻塞：官方网络不旋转不变**（37/90/191° overlap 0.00、自匹配 rank 至 301；镜像 bat 0.05），
  E0/E1 旋转不变 → 网络**不能现成接入**旋转不变流水线，需旋转包装（未验证）。
  成本：网络一次性打分图库后每查询≈0 s（远低于 E3 55 s），但旋转敏感。
- **许可边界**：工具箱与 `.h5` 权重 gitignored（`.gitignore:72`，上游无再分发许可），**不入库**，
  运行时从用户本地副本读；端口代码/脚本/测试/结果 JSON 入库。引用 Morgenstern 2021 + Carbajal Chavez（PsyArXiv）。
  复现需本地工具箱 + `pip install -e ".[shapecomp]"`。

验证：`pytest -q` **786 passed**（含 9 端口测试，0 跳过）、`ruff` 全过、`mypy` 39 文件无问题；
E0/E1/E3 重跑逐位等于 CP4（确定性）。复现：`validate_shapecomp_port.py` →
`run_shape_benchmark.py --stage d1 --methods E0 E1 E3 S0K1 S0K2` → `--stage d2 d3 d4 --methods E0 E1 E3 S0K1 S0K2`
→ `--stage summary` → `plot_shape_benchmark.py --figure all`。工具箱与权重不入库（gitignored）。

### Checkpoint 6：给 ShapeComp 端口加旋转搜索并与各臂同旋转条件对比 ✅（`e43f587`）

> 触发：用户明确"本项目的目的就是实现旋转可查找"，要求对 shapecomp 加上旋转分析能力、
> 与有旋转设计的方案在**同一旋转条件**下对比。属方法优化研究，**不改正式检索默认、不跨 P0 门禁**。

- **加了什么**：`retrieval.py` 新增 `kernet_rotation_frames()` + `search_shape/pair_distance` 的
  `rotation_search` 开关（**默认 False**）。在 **E3 同款 24 步 / 15° 网格**上旋转**查询原始轮廓**，
  取 `min_θ ‖net(R_θ q) − net(g)‖`；图库只按存量姿态打分一次。
  **官方输入契约不动**：旋转发生在 `kernet1_input`/`kernet2_input` 之前，预处理与权重逐字不变，
  不重训练、不改网络；新臂标 `S0K1+rot` / `S0K2+rot`。
- **协议**（`--stage d5`，同条件）：φ ∈ {0, 15, 37, 45, 90, 135, 191, 270}（含 15 的整倍数与网格外 37/191，0 为对照）。
  (a) 250 对 H1 逐 φ 人类一致性；(b) 分层 MPEG-7 图库旋转查询 vs 未变换基线的 Top-10 overlap + 自匹配 rank。
  七臂同 φ 同候选集：E0 / E1 / E3 / S0K1 / S0K1+rot / S0K2 / S0K2+rot。
- **检索（关键结论）**：Top-10 overlap 均值/最差 —
  E0 与 E1 **1.000/1.000**；E3 0.916/0.674；**S0K1 naive 0.162/0.037 → +rot 0.943/0.725**；
  **S0K2 naive 0.148/0.018 → +rot 0.910/0.632**。自匹配 rank（旋转口径 21 例 = 3 目标 × 7 个非零角度）：
  **naive 仅 1/21 与 4/21、最坏 297（fork-1 90°）/244 → +rot 20/21（S0K1，例外 fork-1 37° rank 6）
  与 19/21（S0K2，例外 bat-1 37°/191° rank 2）**；而 **E0/E1/E3 在每一个旋转查询上都是 rank 1**
  → **旋转可查找已实现**，且与 E3 同档（仍不及 E0/E1 的严格不变）。
- **判据对齐（与用户确认，2026-11-08）**：本项目要求的是"**查询随机旋转时查找效果最好**"⇒ **主判据 = (b) 检索**，
  (a) 的 agreement 只是"该表示是否捕捉人类相似度"的支持性证据（源自 P0-4.6 原始研究问题），**两者不得互替**。
  按 (b) 排序 **E0=E1（21/21 rank 1）> E3（21/21）≈ S0K1+rot（20/21）≈ S0K2+rot（19/21）≫ naive（1/21、4/21）**；
  CP5 的 S0>E3 领先完全属于 (a) 且需"姿态已对齐"这一前提，**从未承诺 (b)**。本轮买到的是"旋转可查找"，不是新的检索冠军。
- **人类一致性**（agreement，正=更好；φ>0 池化，两级 bootstrap）：E0 0.683、E1 0.782、E3 **0.839**；
  S0K1 naive 0.434 → **+rot 0.747**；S0K2 naive 0.458 → **+rot 0.774**。
- **口径（重要，防止误读）**：(a) 的 `n_rows=1750 = 250 对 × 7` 与 (b) 的 `21 = 3 × 7` **都在构造上丢弃了 0° 对照**
  （`run_shape_benchmark.py` 两处 `phi == 0` 不入统计；对照另存 `unrotated_control_overlap`，七臂全 1.000）。
  **这正是实际工况**：查询旋转未知 ⇒ 搜索永远在跑，所以上面的头部数字就是"只看在转"的成绩。
  φ=0 只作校验列，用来确认包装没破坏本来正确的情况，**不是工作点**。
- **代价要诚实**：φ=0 峰值由 0.903/0.900 降至 0.763/0.775（旋转搜索取 min 会拉高"相似"下限、压缩判别）。
  **为何 CP5 的 S0>E3 领先没有延续**：那 +0.072 是在**姿态已对齐**前提下挣到的；对 24 个姿态取 `min`
  把每对形状换成"最乐观距离"，被压平的恰是网络最敏感的细序——**要的平坦（鲁棒）是用更低的水平换的**。
  故**人类验证的相似度**与**旋转不变性**相互独立：搜索修好后者、同时花掉前者。
- **一致性/确定性核验**：`d5` 的 φ=0 naive 臂与 CP5 D1 统计不可区分（S0K1 0.903 vs 0.9032、S0K2 0.901 vs 0.9005）；
  缓存的帧打分器与公开 `pair_distance` 差 ≤1e-12。**D1–D4 结果文件故意未重跑**：`rotation_search` 默认关，
  naive 分支只改写为算术等价的对称形式 → 其数值仍为 CP5 值。
- **产物**：`results/d5_rotation_metrics.json`、`d5_rotation_results.csv`、`figures/rotation_stress.png`
  （左：一致性 vs 角度；右：Top-10 overlap vs 角度；实线=naive，虚线=+rot）。
- **边界**：网格分辨率决定不变性精度（15° 网格 → 半格误差，故 37° 仍有残差）；权重与工具箱仍 gitignored、
  无再分发许可；是否把旋转包装整合进 EarthGlyph 正式流水线**由用户决定**。
  **下一步（优先）**：用**朝向规范化**（查询**与图库**同旋到参考朝向）取代"查询侧取 min"，
  验收标准写死为 **(b) ≥ 21/21 rank 1 且 (a) ≥ E3 的 0.839**，二者任一退化即否掉该包装；
  更细网格（36 步 / 10°）只能补网格外失配，列为廉价替代。两条均为**机制推断、未实测**，记入
  `docs/DECISIONS.md` **D55 / D56（假设 / 待验证）**；本条判据本身记为 **D54**。

验证：`pytest -q` **792 passed**（含 15 端口测试，0 跳过）、`ruff` 全过、`mypy` 39 文件无问题。
复现：`validate_shapecomp_port.py` → `--stage d1` → `--stage d2 d3 d4` → **`--stage d5 --methods E0 E1 E3 S0K1 S0K2`**
→ `--stage summary` → `plot_shape_benchmark.py --figure all`（d5 墙钟约 2322 s；需本地工具箱）。

### Checkpoint 7：先证官方复刻正确，再修统计与旋转实验，最后按双目标定夺（2026-11-09，进行中）

> 触发：用户要求把 CP7 拆成四个可复现检查点 —— **CP7-A** 逐函数忠实度审计 + 哈希 + 异常输入测试，
> **CP7-B** 修 D5 的重复测量 bootstrap，**CP7-C** R0/R1/R2(/R3) 旋转方案 + T0/T1/T2 判别式检索，
> **CP7-D** 接口/术语/许可修复 + 五图 + 十问报告。全程**不改生产默认检索器、不引入真实水体库、不动官方原件与权重**。

#### CP7-A：官方 MATLAB → Python 逐函数对照 ✅（`82af902`，已推送并核验）

- **逐函数对照表**（含 MATLAB 文件名:行号 ↔ `shapecomp_port.py:行号`）写入
  `experiments/shape_perception_benchmark/notes.md` §Checkpoint 7 A1：`centroid`（**点均值**，非面积质心）、
  `clockwise`（首个最高点处转向检验，`min(find(...))` ↔ `argmax`，含 `xa==xc` 分支）、
  `adjustContourPos`（**先减 min 再除已平移数组的 max**，且 min/max 跨两列整体取）、
  `resamplePolygon`（封口 → `n+1` → 丢末行）、`resamplePolyline`（`find(...,'last')`/`find(...,'first')`
  ↔ `searchsorted right−1`/`left`，含**并列顶点**与 `Lmax*1e-12` 重合守卫）、
  KerNet1 的 `points(:)'`（**列主序**）、KerNet2 的 `inpolygon` + `flipud(reshape(...,40,40,'F'))`、
  以及两条路**真实不同**之处（KerNet2 源码里 drop-last / leftmost 三行被注释掉）。
- **向量化不改数值**：`_resample_polyline` 与 MATLAB 逐行循环版在 40 组随机环（含重复顶点 / 已封口环）
  的 **~9 000 个采样位置逐位相同**（新回归测试锁定）；单次调用快 ~28×，一次 `kernet2` 打分端到端快 ~2.6×。
- **来源与哈希入库**：`scripts/hash_shapecomp_toolbox.py` → `results/official_toolbox_hashes.json`（24 文件
  SHA256 + 字节数、工具箱清单 2340 文件 / 333 `.m` / 3 `.h5`、逐层权重形状与 dtype、
  `tensorflow_used: false` / `matlab_used: false`、`redistribution` 声明）。
  权重 `shape2sc22a.h5 a6fd1f06…`（52 505 208 B）、`im2sc22d.h5 035f655b…`（2 811 864 B）；
  `.mat` 版网络导出是 MATLAB v7 classdef 句柄、scipy 解不了 → **只哈希不解析**。二进制仍 gitignored（D60）。
- **异常输入契约**：合法 8 类（顺 / 逆时针、首末重复、连续重复顶点、近圆、五角星、细叉、**自交环**）
  两路均得**有限非空**输入与有限非零 22 维；非法 3 类（全点重合、<3 顶点、含 NaN）
  一律 `ShapeCompInputError(ValueError)` **显式拒绝**，不再可能悄悄产出 NaN / 零向量。
  自交环**故意接受**（`GANinput2shape.m` 明示 GAN 形状可自交 ⇒ 官方输入类）。
- **忠实度归因（本轮最关键）**：搜索"循环起点 × 遍历方向 × 存档两文件"后 **8/15 个 H2 形状与发布坐标差
  < 1e-6（中位 1.5e-07）** —— 一次性钉死权重、层序、conv/pool/ReLU 序、C-order flatten、`inpolygon`、`flipud`；
  **其余 7 例仍 4.8e-3–1.8e-2 ⇒ 明确标为"未归因"，不宣称全部残差来自预处理**（D57）。
  KerNet1 仅确认**输入次序**（消融 0.184 vs 1.922 vs 2.622），**网络级忠实度未钉到机器精度**。
  H1 与原始 MDS 之差始终是**近似能力**，不是移植错误。
- **新测得的硬事实**：栅格化**单像素翻转**使 22 维输出移动中位 **0.2887 = 18.5% 坐标范数**（p90 0.3474 / max 0.4066）
  ⇒ "先旋转再栅格化"是**近不连续**操作，为 CP7-C 的包装设定上界（D59）。
- **验收（用户写的硬条件）**：H2 头部 **RMSE 逐位不变** = `0.006496161383207664`（Spearman 0.99894 / 逐维 r 0.99823），
  Figure 8 0.97979 / 0.95661、H1 0.92663 / 0.92889 亦逐位不变 ⇒ **已验证的 KerNet2 路径无未解释的精度退化**。
- **填充规则替换**：`shapely.covers` → MATLAB `inpolygon` 直译（偶奇交叉数、边界算内部、半开区间去重顶点、
  无容差的 `cross == 0` 边界判定）；在 15 条 H2 + 60 条 Figure 8（含自交）轮廓上**逐像素 0 差异**，
  并保留为回归测试（D58）。端口不再依赖 shapely。
- **测试**：`tests/test_shapecomp_port.py` 由 15 例增至 **34 例**（13 s 全过），新增逐函数对照 / 异常输入 /
  确定性 / 像素敏感性 / 输入对齐 / GEOS 一致性 / 重采样逐位等价。

#### CP7-B：修 D5 重复测量统计——以"形状对"而非 1750 行为统计单位 ✅（`193c0f8`，已推送并核验）

- **缺陷定位**：`run_d5` 用 `np.tile(ratings, (7,1))` 把 250 对堆成 1750 行（`run_shape_benchmark.py:777`），
  再交给 `bootstrap_two_level` 按**行**独立重抽样（`metrics.py:96`）⇒ 同一形状被计成 7 个独立受试材料、
  同一评分面板被计成独立重复，CI 系统性偏窄。**点估计自始只是描述结果，不动。**
- **新统计单位**：`metrics.bootstrap_grouped_two_level()` 按整簇重抽样（抽到一对 ⇒ 其 7 个角度行同进同出）
  + 面板层一次重抽样；`_crossed_grid()` 强制 (形状, 条件) 设计**完整且不重复**；
  **所有臂共用同一 replicate 流** ⇒ 臂间差是真正的配对比较（测试用"复制臂差恰为 0"证明这点）。
- **不覆盖、可审计**：新阶段 `--stage d5b`（`run_d5_grouped`）**只读** `d5_rotation_results.csv`，
  准入闸门 = 用旧 bootstrap 重跑存储距离必须复现 `d5_rotation_metrics.json` 的**全部**点估计与 CI
  （实测最大偏差 **0.000e+00**）。既有 d5 产物一字未改（其 SHA256+字节数写入新产物，测试再哈希一次）；
  新产物仅 `d5_grouped_results.csv` / `d5_grouped_metrics.json` + `metrics.json` 的**新增**块
  `rotation_stress_agreement_grouped`。
- **口径（写明）**：头部把七个角度视为**固定设计因子**（预先声明，永不重抽样）⇒ 区间只回答
  "这套协议、换一批形状与观察者"；另附**敏感性**=把角度也按有放回抽样（三级重抽样）。
- **CI 变化（250 簇 × 7 行，n_boot=2000，seed 20261107）**：宽度 ×1.49–2.13 —
  E0 +0.6831 [.6352,.7040]→[.5893,.7361]；E1 +0.7824 [.7305,.7981]→[.7028,.8187]；
  E3 +0.8387 [.7852,.8477]→[.7617,.8671]；S0K1 naive +0.4341 [.3778,.4704]→[.3445,.4907]；
  S0K1+rot +0.7472 [.6954,.7630]→[.6727,.7808]；S0K2 naive +0.4582 [.3987,.4954]→[.3716,.5159]；
  S0K2+rot +0.7735 [.7235,.7886]→[.7003,.8052]。
- **独立佐证（不依赖本轮任何改动）**：D1 用**未旋转的 250 对**，其 bootstrap 本来就以形状对为单位。
  对"旋转不改变距离"的臂，修正后的 D5 区间**必须**与 D1 重合 ⇒ 实测 E0 宽度 **0.1468 = D1 0.1468（差 0.0000）**、
  E1 **0.1159 = 0.1159**、E3 0.1054 ≈ D1 0.1066（差 0.0012，因 E3 的旋转搜索确实随角度改变距离）；
  两个 naive 臂相反地**宽于** D1（0.146 vs 0.065）——它们的一致性真的随所抽角度而变，
  配对设计不再把这 7 次重复当作 7 个独立刺激而平均掉。⇒ **CP6 的 D5 区间约为应有宽度的 1/2，此结论被独立复核。**
- **结论是否变化**：点估计排序**不可能**变（数值未重算）。配对后 21 个臂序中 **19 成立**；
  7 对的朴素边际区间本就重叠，其中 **5 对由配对检验补上**（E3>S0K1+rot +0.0891 [.0495,.1312]、
  E3>S0K2+rot +0.0627 [.0231,.1039]、S0K2+rot>S0K1+rot +0.0264 [.0068,.0448]、
  E1>S0K1+rot +0.0349 [.0020,.0694]、S0K1+rot>E0 +0.0614 [.0090,.1191]）；
  **两条不可区分**：E1 vs S0K2+rot（+0.0085 [−.0287,+.0467]）、S0K1 naive vs S0K2 naive（−0.0239 [−.0496,+.0015]）。
  **没有任何旧排序被推翻，被推翻的只是精度。** 角度随机化的敏感性下另失两条（E1>S0K1+rot、S0K2+rot>S0K1+rot）
  ⇒ 引用它们须限定"在本轮声明的角度集上"；naive 臂区间宽度 0.113→0.453 剧烈变宽。
  **判据不变**：主判据仍是 D54 的 (b) 检索，本阶段未触碰任何检索数字。
- **假设声明（写入产物）**：旋转一个成员后**沿用原有人类评分**＝假设"旋转不改变被试感知到的相似度"，
  本轮未复测任何旋转对、不存在新评分 ⇒ (a) 度量的是"对图像变换的不变性"，不得升格为人类感知证据。
- **顺带修正**：`_interval()` 在全 replicate 退化时改为显式 `ValueError`（原先在 `np.quantile` 内崩），
  是本轮唯一触及共享辅助函数的改动，真实数据不触发（各臂 2000/2000 有效 replicate）。

验证：`pytest` **818 passed**（新增 7 项 grouped/产物审计测试）、`ruff check .` 全过、`mypy` 无问题；
复现：`--stage d5` → **`--stage d5b`**（约 30 s，不需要工具箱，只读存量产物）→ `--stage summary`。
详细表格与解读见 `experiments/shape_perception_benchmark/notes.md` §Checkpoint 7 B1–B6；
决策记录 **D61–D63**。Git 检查点：工作提交 `193c0f8`（`fix: sample the D5 rotation sweep by rated
pair, not by angle-row (P0-4.6 CP7-B)`），已推送 `origin main` 并核验 `git ls-remote` == `HEAD`。

#### CP7-C：旋转方案与判别式检索 ✅（工作提交待补，已推送并核验）

- **冻结设计（跑前写死，产物 `design.frozen_before_run=true`）**：12 查询（星状/心形/新月/分叉/细长/近圆/对称多解）
  × 5 角度槽（`on_grid`×1、`mid_grid`×2、`random`×2，步长 15°，`random` 距栅格与中点均 ≥2°）+ control
  = **72 单元/臂/条件**；T0/T1/**T2（主条件）**；seed 20261107、top_k 10、16 臂、3456 检索行 + 28000 评分行。
  **每臂每单元都跑** ⇒ 没有"跳难例换速度"的自由度，也没有看见失败后重抽角度。
- **四方案**（`earthglyph/shape_benchmark/rotation_methods.py`，48 项测试无需工具箱）：
  `naive(1,1)`、`R0(24,1)`（等值于 CP6 搜索，测试钉住）、`R1_36/R1_72`、`R2(1,1)` 双向朝向规范化、`R3(24,24)`。
  **姿态选择只看一条轮廓**（`test_pose_choice_never_consults_the_gallery`）⇒ 无目标标签泄漏；
  R3 多姿态每形状仍一条（不作独立图库对象）。
- **T2 同族 Top-1**：**E1 0.833** > S0K2/R2 0.764 > E0 0.750 > S0K1/R3 = E3/sample 0.694 > S0K2/R3 0.653 >
  S0K1/R0 = S0K1/R1_72 0.611 > S0K1/R2 0.583 > S0K2/R1_36 = R1_72 0.542 > S0K2/R0 0.514 > **naive 0.264 / 0.250**。
  类级深度归 **R3**（S0K1/R3 Recall@10 0.288、NDCG@10 0.735）。稳定性归 **R2**（0.958/0.972，位移 0.3/0.7）。
  naive 在 72 单元中 **36/40** 个 Top-10 内无同族形状（首个同族 rank 达 250/241），位移最大 343/324，
  错误吸引为**负**（−0.273/−0.263）而四个包装器全为正。
- **细网格结论**：24→72 步 Top-1 持平/微升，稳定性与重合显著上升，代价 3× 查询耗时 ⇒ **买的是可预测性不是精度**。
- **规范化结论（两侧都报）**：便宜且严格不变 —— 直接验证 `results/r2_pose_invariance_check.json`：
  同一轮廓 37/191/270° 下 naive 坐标移动 0.61–2.04（范数 ~1.22），R2 在有主轴的 11 条上 ≤5.1e-14（K1）/恰好 0.0（K2），
  而唯一 `isotropic-fallback` 轮廓上 R2 **与 naive 逐位相同** ⇒ 无轴即完全不生效；
  图库 362 条：`ok` 343 / `minor` 4 / `isotropic-fallback` 13 / `sign-tie` 2；与 E3 朝向规则同一条轮廓最大差 **180°**；
  规范化轮廓栅格化后 mask 轴最大偏差 179.6°。**且 R2 的人类一致性最低（0.639/0.677）**。
- **目标 A/B 分表**：一致性最高 E3/sample 0.839、R3 0.830/0.829（配对 R3−R0 = +0.081/+0.055，R2−R0 = −0.108）；
  检索最高 E1 0.833。**不合并成"综合更好"**。预计算：图库 2.90 s、E3 canonical 0.16 s、姿态栅格 K1 22.4 s / K2 78.4 s。
- **探索性披露**：R1/R2/R3 在同一冻结样本上设计+调参+评价，无保留集、无新人类评分。

验证：`--stage d6` → `--stage summary` → `scripts/plot_rotation_checkpoint7.py`；`pytest` **874 passed**、
`ruff check .` 全过、`mypy` 40 源文件无问题。数值与判读见 notes §C1–C7；决策 **D64–D71**。

#### CP7-D：接口、术语、许可与交付 ✅（工作提交待补，已推送并核验）

- **F1（实测，故意不修）**：`principal_frame()` 手构特征向量在**协方差对角且大方差在 y**时下溢走 `+x` 兜底，
  交付帧与自身主轴差 **90°** 而状态仍写 `canonical`（5 探针各 2 姿态命中，漂移 ~80×）；
  isotropic fallback 最坏 all-drift 0.0242 / 直方图块 0.6825；所有探针旋转位移均未超过最近异类距离。
  修它将重跑全部已提交 E1 数字 ⇒ 只记录（docstring warning + D74）；E1 的 24 姿态 min 作**独立方案计价不采纳**
  （0.0116→0.2787 s/查询，自距离 0.1327→0.0167，异类比值 0.9999）。
- **F2（三处真实缺陷）**：`allow_mirror` 对 S0/S0K1/S0K2 改**显式拒绝**（载入权重前触发，可测）；
  含镜像缓存曾无视 `allow_mirror=False` 仍用镜像（现按开关取用 + 双向断言测试）；
  E1 `fit_normalizer` 把 6 列浮点残差（极差 5e-17–4e-16）放大成 2.4–8.6 距离使镜像永不胜出（加 1e-12 相对地板，
  **真实语料改变 0 列**、validation 数值 0 变化）；另修 `e3_params` 与调用参数的 `TypeError` 冲突。
- **F3**：官方 **109 描述子 → 22 维 MDS**，91 仅为 EarthGlyph 自写 E1 宽度；纠正文档 + 两处**代码生成字符串**，
  核实从未声称复现 109 维引擎（`getShapeComp22.m:26` = `SC = predict(net2, points)`）；结果数值 0 变化。
- **F4**：README "Official toolbox" 节 + `.gitignore` 增 `*.h5`/`shapeComp_*.zip`；
  **CC BY 覆盖论文与其数据沉积，不构成工具箱权重/源码再分发许可**；不提交压缩包与权重，只提交 manifest + 复现命令。
- **五图**（`checkpoint7/`）：`rotation_methods_overview`(1.23 MB)、`rotation_rank_stability`(478 KB)、
  `rotation_human_agreement`(314 KB)、`rotation_failure_cases`(443 KB)、`shapecomp_port_diagnostics`(244 KB，CP7-A)。
  出图发现真实联调缺陷：hits CSV 的 pose 四舍五入 3 位 vs 结果 CSV 全精度 ⇒ `1e-6` 容差使 8 个最差单元中 3 个
  匹配 0 条、面板空白；改 `1e-3` 后 8 行均 `matched=10`。失败原因仅在有存储测量处标注，否则 `cause undetermined`。
- **十问报告**：见 [`notes.md` §Checkpoint 7 — the ten acceptance questions](../experiments/shape_perception_benchmark/notes.md)。
  摘要：**不建议**把 ShapeComp 纳入生产默认检索；最明确技术阻塞 = "能对新轮廓打分的 ShapeComp 路线
  无法同时超过 E1 的旋转鲁棒性与保住人类一致性，而胜出感知的一侧（发布 22 维坐标）在查询时根本不可用"。

**边界遵守**：未改生产默认检索器、未引入真实水体库、官方 MATLAB/权重只读未改；本轮到此停止。

## 当前阶段：P0 — 真实数据与检索可行性验证（进行中）

**门禁：P0 未通过前不进行大规模 Web 产品开发。**

### P0 任务拆解（可独立实施、可验证）

> 每个任务交付：输入文件、预期输出、运行命令、测试样例、失败处理、改动清单。
> 检索 / 实验类任务须遵循 `earthglyph-retrieval` Skill 工作流。

**P0-0 环境与依赖补齐** ✅ 完成（见上）
- 安装 P0 所需 Python 依赖：`pip install -e ".[data,cv]"`（geopandas/pyarrow/duckdb/overturemaps/matplotlib/opencv）。
- 按需安装系统 GDAL CLI（数据检查 / 转换时用）。→ 实测**不需要**：pyogrio 自带 GDAL 3.12.4，Python 栈已覆盖 P0 需求。
- 验收：`python -c "import geopandas, cv2, overturemaps"` 无错；`pytest` 仍通过。→ ✅ 65 passed + mypy strict 通过。

**P0-1 数据获取（小 bbox 起）** ✅ 完成（见上）
- 初始区域：美国威斯康星州湖泊区 `bbox=-90,44,-89,45`（样例，可换芬兰南部等湖泊密集区）。
- 来源：Overture Maps Base/Water，`overturemaps download --type=water -f geoparquet`。
  → 实测：本机官方 CLI 的 S3 直连不稳定，默认改为 STAC + DuckDB httpfs range 读取（D13），
  `--fetch-method cli` 仍保留官方路径。
- 交付：`scripts/download_data.py`、`data/raw/*.parquet`（不入 Git）。
- 失败处理：若下载被网络阻断，明确报告阻塞 + 复现命令，**不得伪造数据**；备选 Geofabrik OSM PBF。
  → 未触发：真实数据已获取（6,543 行），Geofabrik 备选无需启用。
- 验收：打印实际字段，确认含 `id/geometry/subtype/names/bbox/sources`。→ ✅ 全部存在，实际 Schema 见 DATA_PIPELINE §8.2。

**P0-2 几何清洗与轮廓提取** ✅ 完成（见上）
- 过滤 Polygon/MultiPolygon + subtype(lake/pond/reservoir)；`make_valid` 修复；MultiPolygon 拆分记 `parent_id`；局部投影转米制；面积过滤；弧长重采样 128 点；归一化。
- 交付：`scripts/prepare_polygons.py`、`earthglyph/geometry`（resample/project/extract）、`data/processed/`。
- 验收：单元测试覆盖平移/缩放/旋转不变性、镜像开关、无效草图、重复水体过滤；输出真实轮廓缩略图 + 坐标 + 来源 ID。→ ✅ 137 passed；四类真实数据检查图见 `experiments/p0_geometry_probe/`。
- 实测修正：D15 框边过滤作废（无裁剪证据，改信息性标志）；面积/长宽比不做硬性形状删除（月牙、枝状、细长湖均保留，见图 D）。

**P0-3 特征与索引构建** ✅ 完成（见上）
- 计算 Fourier 描述子（12–24 维）+ 紧致度/长宽比等粗筛特征；保留 128 点精排轮廓。
- 交付：`scripts/build_index.py`、`earthglyph/features`、`data/index/`（features/metadata/shapes/data_manifest.json）。
- 验收：`data_manifest.json` 记录来源/版本/下载时间/覆盖/过滤/有效地物数/许可/commit；每个候选可经 id 回查真实经纬度。→ ✅ 全部满足；839/839 入索引、0 跳过、两次构建字节一致。
- 实测修正：谐波数 16（12/24 无可测增益）；**N=128 保留**（64/256 对照见 D29）；
  GEOS 最小外接矩形在有效凸包上退化 → 自实现（D28）；P0-2 的"104% 步长偏差"是弦长离散度不是重建误差（D30）。

**P0-4 手绘抽象提取与检索 Baseline** ✅ 完成（见上）
- 实现 ≥2 种基线：Hu 矩（`cv2.matchShapes`）+ Fourier 粗筛 + 轮廓对齐精排（复数相关求 θ + 循环移位）。
- 交付：`earthglyph/retrieval`、`scripts/evaluate_retrieval.py`。
- 验收：固定草图 + 种子可复现同一 Top-K；生成真实 Top-10 候选轮廓拼图 + 每个候选真实地图定位。
  → ✅ 121 个固定查询（`--seed 20261105`）→ `results.csv` 241 行 + 9 张 `topk_*.png`
  + 9 张 `baselines_*.png`；每个命中带 `candidate_id` + 真实经纬度，除 `timings` 外逐字节可复现。
- 实测修正：精排用**闭式解**（一次逆 FFT 求全部循环移位 + 复数相关求 θ），不是遍历 360 角度（D34）；
  Fourier **幅值对镜像天然盲**（6/6 镜像探针的真值粗筛排名恒为 1）→ 镜像只由几何精排的
  `allow_mirror` 控制（D33 修订）；Chamfer **不能**裁决镜像，只作复核（D38）；
  声明闭合的轨迹**末点是闭合弦不是顶点**，单顶点闭合误差被提取阶段吸收（D36）；
  三种 baseline 的距离**量纲不可比**，不得据此宣称谁更准（D40）。

**P0-5 算法正确性实验（合成扰动）**
- 抽 100–200 真实轮廓，施加已知平移/缩放/旋转/轻微形变/重采样作模拟查询。
- 交付：`experiments/<exp>/`（results.csv + 图 + notes.md + 复现命令）。
- 验收：报告 Top-1/Top-10 命中率、mAP、不同扰动强度曲线；对照 Hu vs Fourier vs 粗筛+精排。

**P0-6 真实手绘实验**
- 预先固定 ≥10 个不同用户手绘轮廓查询集（不得事后改测试集）。
- 交付：`experiments/queries/`（草图 + Top-10 真实结果截图 + 人工判断 + 失败类型）。
- 验收：区分合成扰动与真实手绘指标；记录失败案例；不只用挑选的成功案例。

**P0-7 Qoder Sites 静态发布路径验证**
- 验证 Qoder Sites 对 React/Vite 静态项目的实际发布流程（不假设一键导入）。
- 验收：跑通一次最小静态部署，记录实际步骤到 `docs/DECISIONS.md`（D 待验证项）。

**P0-8 Go/No-Go 决策**
- 依据 `EXPERIMENTS.md` §6，用 ≥10 真实手绘轮廓结果判定。
- 验收：在 `docs/DECISIONS.md` 记录 GO / NO-GO（改数据算法 / 改定位）结论与证据。

### 初始参数（P0 默认，可在实验中调整）

| 项 | 初始值 |
|----|--------|
| 初始地理区域 | 威斯康星州湖泊区 bbox=-90,44,-89,45（或芬兰南部） |
| 数据来源 | Overture Maps Base/Water（备选 Geofabrik OSM） |
| 地物类型 | subtype ∈ {lake, pond, reservoir}，Polygon/MultiPolygon |
| 几何清洗 | make_valid → 局部投影 → 面积过滤 → 弧长重采样 128 点 → 归一化 |
| Baseline 算法 | Hu 矩 + Fourier 描述子 + 轮廓对齐精排（Chamfer 复核） |
| 实验规模 | 合成扰动 100–200 轮廓；真实手绘 ≥10 查询；索引先 1k–5k，再试 1–5 万 |
| 正确性测试 | 平移/缩放/旋转不变性、镜像开关、无效草图、重复水体过滤 |

### 主要失败风险

1. **数据下载被网络阻断**（github 已现 clone 超时）：Overture/Geofabrik 端点可达性需先验证；阻塞则如实报告，不伪造。
   → P0-1 未触发（真实数据 6,543 行已入库；备用通道见 D13）。
2. **候选库多样性不足**：若过滤后多为相似椭圆，检索无惊喜感 → 需换区域或调过滤。
   → P0-3 部分量化：面积跨 4.99 个数量级、6 个 class、solidity p5 0.479（非"一库椭圆"），
   但 reservoir 仅 55 个且**库只覆盖单 1°×1° 区域**。
   → **P0-4 实测支持该风险**：角状 / 星形类查询在全库找不到对应地物（十角星 Top-1 `D≈0.21` 并带
   弱命中告警），D 类 30 行中 22 行来自同一枝状水库 `row 294`；但"是否有惊喜感"只能由
   P0-6 的人工判读决定，本轮不据此下结论。
3. **真实手绘形变超出几何匹配能力**：合成扰动通过但真人手绘失败 → 精排需加 Chamfer/IoU 或调整产品定位。
   → P0-3 已量化"同幅值不同相位"的失效面（98.81% 冒形 IoU < 0.9）；
   **P0-4 的 G2 受控不规则草图**：五族 Top-1 在 0.583–1.000 之间，失败全部归为 A 提取
   （闭合误差过大被显式拒绝）或 D 覆盖，B 粗筛 / C 精排各 0 行 → 管线内的匹配环节本轮未失手；
   **真人手绘仍未验证**（P0-6）。
4. **浏览器端检索延迟**：1–5 万候选在 Worker 内可能偏慢 → 先优化数据格式，不急上后端。
   → 本机已测：839 候选全量 L2 扫描 1.7e-5 s、N=256 会使轮廓字节翻倍（48.8→97.7 MiB @5 万）；
   **浏览器 Worker 内仍未测**（P1 验证）。
5. **Qoder Sites 发布流程与预期不符**：P0-7 需提前验证，避免 P4 踩坑。→ 未验证。
6. **pip/npm 代理不稳定**：环境曾现 127.0.0.1 代理告警，依赖安装需复测。→ 本轮未安装新依赖，未触发。

## 仓库维护检查点

规则正文见 [`AGENTS.md`](../AGENTS.md) 的「Git 规范与仓库维护」：提交内容标准、完整小版本检查点、远程与分支、历史保护。

| 日期 | commit | 内容 | 远程核验 |
|------|--------|------|----------|
| 2026-11-05 | `6c71245` | P0-0 / P0-1 / P0-2 基线（首个检查点） | ✅ `origin/main` |
| 2026-11-05 | `737ea68` | `docs:` 写入仓库维护与小版本推送规则 + `.gitignore` 审查包 | ✅ `git ls-remote origin` = `737ea681fa02beb8c4865e8528b71dd5fe67c3b3` |
| 2026-11-05 | `fc35ba9` | `docs:` 记录检查点 | ✅ `origin/main` |
| 2026-11-05 | `77b083c` | **P0-3 检查点**：特征模块 + 保真度度量 + 采样消融 + 形状索引 + 144 项新单测 + 文档（39 文件） | ✅ `git ls-remote origin` = `77b083c931b72cf4fd153a9185254f5a9486aeab` = `git rev-parse HEAD`（`fc35ba9..77b083c main -> main`） |
| 2026-11-05 | `8cec88b` | **P0-4 检查点**：手绘抽象提取 + 两级检索 + 三 baseline + 本项目第一个检索实验（176 文件 = `earthglyph/` 10 + `scripts/` 4 + `tests/` 5 + `tools/sketch_pad.html` + 文档 5 + 实验产物 151；新增 144 项单测 → 425 passed） | ✅ `git ls-remote origin` = `8cec88b9c9fe8ef31f84575a360f017c4861da75` = `git rev-parse HEAD`（`899101d..8cec88b main -> main`，7.37 MiB / 189 objects） |
| 2026-11-06 | `10c9e8f` | **P0-4.5 Checkpoint A**：受控感知图库（179 成员 / 18 查询 / 7 类 / 三来源许可与逐资产哈希 / 75 968 次拒绝计数）+ SVG 解码器闭合语义修复 + 结构角色审计 + 标签冻结 + `control_gallery.png` + 研究台账（34 文件 = `earthglyph/data/` 2 + `scripts/` 2 + `tests/` 2 + 实验产物 24 + 文档 4，含 `.gitignore`）；新增 73 项单测 → **498 passed** | ✅ `git ls-remote origin` = `10c9e8fab8ca39401b6f2e1336780bbb90f54718` = `git rev-parse HEAD`（`4c4bf66..10c9e8f main -> main`）；**提交后离线重建**：`gallery.jsonl` `3a764664…` / `labels.json` `26e444ce…` / `control_gallery.png` `8c07d12a…` 三个 sha256 **与提交内一致**，只有 `dataset_manifest.json` 的 `built_at_utc` 与 `git_commit`（现等于 `10c9e8fa…`）按预期变化 |
| 2026-11-06 | `5a9f1bc` | Checkpoint B 第 1 步（`fix:`）：`_bevel` 只切角的一侧 → 圆角星变成尖刺（实测最坏顶点转角 176.8°，应为 48.0°）；两侧各切 + 钉测试 `test_a_rounded_star_is_worn_and_not_spiked` | ✅ 随 `6535e98..1696873` 一并推送（见下行核验）；本行是 Checkpoint B 这一**完整小版本**内的第 1 个 commit，不是独立小版本，故不单独核验远程 |
| 2026-11-06 | `1b8461c` | Checkpoint B 第 2 步（`data:`）：用修复后的生成器离线重建图库 → `gallery.jsonl` `3a764664…`→**`15be97ae…`**、`control_gallery.png` `8c07d12a…`→**`585ffb28…`**（1 184 570→1 184 600 B）、`labels.json` **不变**，manifest `git_commit` = `5a9f1bcc…`（构建时 sha，早于本 data commit，符合"提交无法写入自身"） | ✅ 同上（`6535e98..1696873`）；重建命令 `python scripts/prepare_perceptual_dataset.py --offline --force --out-dir 'experiments\p0_perceptual_probe'` |
| 2026-11-06 | `1696873` | **P0-4.5 Checkpoint B**：E1（`perceptual_features.py` 1520 行）+ E2（`salient_structure.py` 831 行）+ E3（`shape_context_baseline.py` 717 行）+ 四臂契约 `perceptual_methods.py`（137 行）+ E0 适配器 `perceptual_e0.py`（88 行，**产品代码 0 改动**）+ 4 个测试文件 105 项 → **604 passed** | ✅ `git ls-remote origin` = `1696873f4dbc5e5b7a7aedfc7bfa2818e891326f` = `git rev-parse HEAD`（`6535e98..1696873 main -> main`，9 文件全为新增）；提交前 `pytest -o addopts="" -q` 604 passed · `ruff check .` All checks passed · `mypy` no issues in 31 source files |
| 2026-11-06 | `3ababaf` + `78915ba` | Checkpoint B 收尾两步：`exp:`（新增 `verify_policy_claims.py` + 三臂 POLICY `measurements` 键 + 契约测试钉住指针，5 文件 +362 行）与 `docs:`（TASKS / DECISIONS D48 / experiment_log / notes §12，4 文件 +203 −17）。同 `1696873` 一样是**完整小版本内部**的 commit，不单独核验远程 | ✅ 一并推送 `1696873..78915ba main -> main`，推送后 `git ls-remote origin` = `78915bafe632a3056e6186c021a4dc858a7babd2` = `git rev-parse HEAD`、`git status --short` 无输出；提交前 gate：`pytest -o addopts="" -q` **605 passed** · `ruff check .` All checks passed · `mypy` no issues in 31 source files · `mypy --strict` 对新脚本 clean · `python experiments/p0_perceptual_probe/verify_policy_claims.py` 退出码 0 |

- 复现命令与证据（P0-3）：`pytest` → 281 passed · `ruff check .` → All checks passed ·
  `mypy` → no issues in 17 source files；
  `data/index/` 三产物 sha256 见 [`DATA_PIPELINE.md`](DATA_PIPELINE.md) §10.1，
  **在 `77b083c` 提交后重建得到字节一致的文件**，其 `data_manifest.json` 的
  `git_commit` 已等于该检查点 sha（紧随的 `docs:` 提交只补记本行核验，
  因为提交无法把自己的 sha 写进自身，不另立检查点）。
- 复现命令与证据（P0-4）：`pytest -o addopts="" -q` → **425 passed** ·
  `ruff check .` → All checks passed · `mypy` → no issues in 24 source files；
  查询生成 → 评估 → 可视化 → 调试 CLI 四条命令见
  [`experiment_log.md`](../experiments/experiment_log.md) 的「p0_retrieval_baseline 复现命令」
  与 [`notes.md`](../experiments/p0_retrieval_baseline/notes.md) §2（`--seed 20261105`、
  `--index data/index`、`--smoothing light`）；检索绑定的是 **P0-3 索引**（commit `77b083c9`，
  sha256 见 [`DATA_PIPELINE.md`](DATA_PIPELINE.md) §10.1，本轮**未重建、未扩容**）；
  实验产物 151 个文件（8.7 MiB，单文件最大 950 KB < 1 MiB 门槛），
  `results.csv` 241 行可由同一命令重建（该表不含计时列）；调试 CLI 的输出 JSON
  除 `timings` 字段外逐字节一致（本轮实测三次相同）。
  紧随的 `docs:` 提交 `e9ba12d`（`8cec88b..e9ba12d main -> main`）补记本行核验并修正
  排序契约的表述；和 P0-3 一样，记录核验的提交无法写入自身 sha，故不再另立检查点。
- 复现命令与证据（P0-4.5 · Checkpoint B）：`pytest -o addopts="" -q` → **605 passed** ·
  `ruff check .` → All checks passed · `mypy` → no issues in 31 source files；
  **POLICY 里每个数字的重跑命令**：`python experiments/p0_perceptual_probe/verify_policy_claims.py`
  （约 4 分钟，逐条 `[ok]`，超容差非零退出；它读不入库的 `gallery.jsonl`，
  该文件由 `python scripts/prepare_perceptual_dataset.py --offline --force` 重建，
  E3 普查规模用 `--pairs` 调节）；本轮**未运行任何检索评价** → `results.csv` 无新行，
  台账只登记自测量探针（见 [`experiment_log.md`](../experiments/experiment_log.md) 的
  「P0-4.5 · Checkpoint B」小节）。紧随的 `docs:` 提交补记本行核验，同样写不进自身 sha。
- 复现命令与证据（P0-4.5 · Checkpoint C）：`pytest -o addopts="" -q` → **621 passed** ·
  `ruff check .` → All checks passed · `mypy` → no issues in 31 source files ·
  `mypy --strict` → clean（runner / visualizer / `report_checkpoint_c.py` 三个脚本）；
  评价与画图三条命令（顺序有意义）见 [`experiment_log.md`](../experiments/experiment_log.md)
  的「P0-4.5 · Checkpoint C」小节与 notes.md §13.9，表格与产物哈希由
  `python experiments/p0_perceptual_probe/report_checkpoint_c.py`（`--files` 出哈希）打印。
  产物：`results.csv`（3 601 行 / 20 列）+ `metrics.json` + `method_status.json` +
  `figures_manifest.json` + 24 张 PNG；评价墙钟 **1151.1 s**（本机数字，不可跨机复现），
  **分数列两次完整运行逐字节相同**（3 601 行的评分与相关列；墙钟列与
  `align_indistinguishable_from_query` 不参与比对——后者正是 `05c50b6` 要改的列，
  它变化是预期结果，见 notes.md §13.1）；21 张 `comparison_*.png` 字节相同，
  3 张 overview 只因表里印了 `sec/query` 而变字节，图上数字与排名未变。
  `data/index` 与 `gallery.jsonl` / `labels.json` / `control_gallery.png` 在本轮 **只读**，
  三个索引文件哈希与 §10 台账一致，`control_gallery.png` 仍为 `585ffb28…`。
  Git 检查点：`48ec6fc`（`exp:` runner + 可视化 + `test_perceptual_runner.py` 16 项）+
  `05c50b6`（`fix:` E0 lookalike 列）+ `11b2bc8`（`fix:` 表格列宽）
  + `1a73d6e`（`data:` 28 个产物文件，17 MiB，单文件最大 926 636 B < 1 MiB 门槛），推送 `78915ba..1a73d6e main -> main`，推送后核验
  `git ls-remote origin` = `1a73d6e678ce5eea6d08d2fd274119d87d4ebeea` = `git rev-parse HEAD`；本轮文档在紧随的 `docs:` 提交里（其自身 sha 写不进本行，沿用 B 的处理，不再另立检查点）。
  后续两个 `docs:` 提交：`17e2ea8`（C 收尾文档）与 **`f3e474c`（新增 notes.md §13.5.1「结果图像怎么读」：行/列/颜色/叠加环逐条语义 + 页脚原文 + 人工检查顺序，颜色值以脚本常量为准，附 `grep` 核验命令；TASKS 的"对比图"行与 experiment_log 同步指向该节）**。推送 `17e2ea8..f3e474c main -> main`，推送后核验 `git ls-remote origin refs/heads/main` = `f3e474c94c107173f7412690a43836fb45761598` = `git rev-parse HEAD`、`git status --short` 无输出；提交前 gate：`pytest -o addopts="" -q` **621 passed in 26.35 s** · `ruff check .` All checks passed · `mypy` no issues in 31 source files（纯文档改动，未动代码与产物）。本行自身落在紧随的下一个 `docs:` 提交里，同 B 的处理，不再另立检查点。
- 复现命令与证据（P0-4.5 · Checkpoint D · D1）：**一条默认命令即可重建全部材料**
  `python scripts/blind_perceptual_review.py --figures fair,blind,blind_unaligned`
  （其余全用脚本默认：`--gallery-dir experiments/p0_perceptual_probe --index data/index
  --out-dir experiments/p0_perceptual_probe/checkpoint_d --settings T2,T3 --top 5 --fair-top 3
  --fair-settings T2 --dpi 170 --fair-dpi 150 --unaligned-dpi 130 --overview-dpi 120
  --seed 20261107 --blind-columns 3`；本机墙钟 58 s，含从零重扫 439 条展示姿态）。
  提交前 gate：`pytest -o addopts="" -q` → **643 passed**（+22）· `ruff check .` All checks passed ·
  `mypy` no issues in 32 source files。
  确定性：两次全新目录连跑 → 37 张 PNG md5 全同、`visual_review_template.csv` 字节相同、
  `blind_mapping.json` 除 `generated_at_utc` 外相同；第三次写回 `checkpoint_d/` 后与全新目录产物
  md5 仍全同 → **入库字节 = 默认命令产物**（首稿曾用一组更低 DPI 的手旗，与默认命令不符，已作废重生成）。
  产物 40 文件 / 13 414 479 B；关键哈希 `blind_mapping.json` `5857ab02…`、
  `visual_review_template.csv` `94d0babe…`、`display_poses.json` `4b289a0c…`、
  `blind_review_t2.png` `c26d4c8d…`、`blind_review_t3.png` `3b107fe3…`。
  只读输入哈希（由 `blind_mapping.json.inputs` 自证）：`results.csv` `ecf5bd45…` / `labels.json` `26e444ce…`，
  与 §10 的 C / A 台账一致 → C 的指标与结果**未被覆盖**。
  Git 检查点：`16c7d59`（`exp:` 展示对齐器 + 盲评材料生成器 + 2 个测试文件 22 项）+
  **`3e8229c`**（`data:` `checkpoint_d/` 40 文件 / 13 414 479 B，其中 2 张单页总览 1 324 295 B 与
  1 350 489 B 超过 1 MiB，理由写在提交信息里：它们是"一页看完一个设置全部 90 组"的入口，
  分页只会增加文件数不增加信息；C 的同类交付是 17 MiB PNG）。本轮文档在紧随的 `docs:` 提交里
  （其自身 sha 写不进本行，沿用 A/B/C 的处理，不再另立检查点）。
- 提交身份：一次性 `-c user.name/user.email` 传入（`ANTACIDisnotFORMICACID`），未修改机器级 `git config`。
- 远程通道：`ssh://git@ssh.github.com:443/...`（本机 `github.com:443` HTTPS 被阻断）；
  未使用 `push --force`，未改写已推送历史（修正一律新增提交）。

## 阻塞（当前）

- **无阻塞。** P0-0 / P0-1 / P0-2 / P0-3 / P0-4 已完成：标准轮廓库、特征索引与
  真实索引上的两级检索链路均可用（`search_by_contour` / `search_by_image`）。
- **P0-4.5：A/B/C/D 四个检查点均已完成，真人盲评归 P0-6**：Checkpoint A（受控感知图库 + 标签冻结 + `control_gallery.png`，`10c9e8fa`）、
  **Checkpoint B（E1/E2/E3 三臂 + 四臂评分契约 + POLICY 数字重跑脚本，`5a9f1bc` / `1b8461c` /
  `1696873` / `3ababaf` / `78915ba`）** 与 **Checkpoint C（四臂同集 T1/T2/T3 评价 + 24 张对比图 +
  结果文件 + 表格打印脚本，`48ec6fc` + `05c50b6`）** 均已完成；
  **Checkpoint D 已完成**：**D1（公平展示对齐器 + 两类视图 + 720 单元盲评材料 + 空评分表）**、
  **D2（E0–E3 逐臂归因 + `diagnostic_cases.png` + `AI_visual_review` 500 格）**、
  **D3（E0 粗筛 + E3 精排窗口消融 + 描述子缓存 + 排除台账）** 与
  **D4（五个问题的结论 + D50–D53 决策记录 + 门禁状态）**；
  **仍未做**：真人手绘查询只有 **5 个**（QuickDraw，门禁要 ≥10 个不同用户）、≥3 人盲评 **0 份**
  （材料已交付可填）、`unaligned` 视图评分、图库同形缺陷的修复。
  D53 把下一轮顺序固定为 **修图库 → 补真人查询到 ≥10 → 攻枝状覆盖**。
  B 遗留的**测量学**约束在 C 里逐条固定并留下证据（D49）：E1 归一化改为在冻结 179 成员语料上
  一次拟合（跨设置可比，代价是真实索引 0.5% 描述子单元越界，已实测）；E3 固定
  `d(query, candidate)`；E1 固定 `orientation="canonical"`。**仍未解决**：E1 的各向同性回退
  （切向角块在正多边形 / 星形上不旋转不变，已量化未修复）、E3 sample 昂贵（T3 约 43 s/查询）、
  包络匹配 crescent/heart/star 仅部分达标；C 新发现的 **E2 self-rank 1 个查询排第 4** 已在 D2 定位
  （`syn-blob-query` 自距离 0.0、T1 库 5 行同为 0.0 且事件数为 0，名次由 stable 排序行序决定，**不是实现错误**）。
  C 的产物只认最后一次运行：发现 CSV 缺陷后 **整份重跑**，不手工补列。
  已知的真实外部约束：ShapeComp **官方** MATLAB 实现、权重与商业剪影
  在本机不可达 → E1 只能是明确标注 **ShapeComp-inspired** 的自实现基线（D46）；
  人工盲评需要真人，若最终拿不到评分则交付盲评图 + 评分表并 **如实标注"未获评分"**。
- 按任务边界，**P0-5 / P0-6 未启动**（等待授权）：P0-4 交付的是 6 候选 × 受控扰动族 +
  机器指针轨迹，**不含 100–200 形状分级曲线，也不含任何真人手绘查询** →
  **P0 门禁（≥10 真人查询）仍未通过**，不启动大规模 Web 开发。
- P0-4 留下的已知问题（不是阻塞，但决定 P0-5/P0-6 该看什么）：
  枝状水库 `row 294` 贡献 22/30 行 D 类失败（全库精排 23–502 名）→ 采样保真 vs 库覆盖需定量分离；
  极端开放 / 大闭合误差查询被提取阶段显式拒绝（A 类 22 行 / 10 输入）→ 是否放宽阈值属产品决策，
  需真人数据才能判断拒得过严还是过松；
  `mirror` 族默认策略（禁镜像）下 Top-1 仅 0.167 → 真人是否会画镜像轮廓待 P0-6 观察。
- P0-3 遗留：8 个候选（7 reservoir）在任何测试 N 下 region IoU 都 < 0.95；N=128 下 5 个候选重建自交；
  候选库仅单 1°×1° 区域（Type B 覆盖风险，P0-4 已给出方向性证据）。
- 非阻塞遗留：系统 GDAL CLI 未安装（Python 栈已覆盖需求）；`web/` 的 `npm audit` 6 个开发链依赖项待 P1 复评；
  `scripts/inspect_data.py` / `scripts/preview_data.py` 有 10 个 mypy strict 报错（P0-1 遗留，配置范围为 `earthglyph/`，未阻塞）；
  跨区域 / 极区无效几何覆盖度未测（`make_valid` 路径仅由合成单测覆盖）。

## 下一步：P0-5 合成扰动分级曲线 与 P0-6 真人手绘（未启动，等待授权）

> **最近一步不是 P0-5，而是 P0-4.5 Checkpoint D**：C 已在同一冻结划分（18 查询 / 179 成员 /
> `labels.json` sha256 `26e444ce…`、`gallery.jsonl` `15be97ae…`）上跑完 E0–E3 × T1/T2/T3
> （216 次全库评分，**无粗筛窗口**），产出 24 张对比图与 `results.csv` / `metrics.json` /
> `method_status.json` / `figures_manifest.json`，并把比较口径固定为三条（D49）。
> 数字与图由 `report_checkpoint_c.py` / `visualize_perceptual.py` 打印，不经人手抄写；
> 解读见 `experiments/p0_perceptual_probe/notes.md` §13。
> D 做的是：T1/T2/T3 **分开解释**、不变性探针、≥3 人盲评（拿不到评分则交付盲评图与评分表并
> 如实标注"未获评分"）、失败归因与结论，并解释 C 遗留的 **E2 self-rank 1 个查询 rank 4**。
> D 仍**不得**把图库形状合并进正式索引（`data/index` 保持 `77b083c9`），也不得在拿到人工评分前
> 把任何内部指标称作"人眼匹配准确率"。
> 下列 P0-5 / P0-6 条目仍是**未启动、等待授权**。

P0-4 已给出明确的输入、判据与"本轮不能宣称"的清单（`experiments/p0_retrieval_baseline/notes.md` §12）：

1. **复用输入**：`data/index/`（839 候选 / 128 点 / 20 维，commit `77b083c9`，sha256 见
   `DATA_PIPELINE.md` §10.1）、`earthglyph/retrieval/search.py` 的 `search_by_contour`、
   `scripts/make_retrieval_queries.py`（`--seed`）与 `scripts/evaluate_retrieval.py`
   （三层评估 + 四类归因 + `--sweep` 已就绪）。
2. **P0-5 范围**：把 G1/G2 从 6 个候选扩到 **100–200 个真实轮廓**，报告扰动强度**曲线**
   （Top-1 / Top-10 / mAP / rank 分布随强度变化），三 baseline 同集对照。
   目标是定量回答：`row 294` 类枝状候选失败是 N=128 保真问题还是覆盖问题（**不提前全局升 256 点**）。
3. **P0-6 范围**：**预先固定 ≥10 个真人**手绘查询（不得事后改测试集，不得用算法构造的"草图"冒充），
   逐查询做人工肉眼判读 + 失败类型标注 → 这是 §6 Go/No-Go 的唯一入口。
4. **必须与 P0-4 对照**：P0-4 的 G2 各族 Top-1 / 距离 / 归因分布是 P0-6 的基线；
   若真人失败集中在 A 提取，先评估拒绝阈值而非改特征。
5. **仍未验证**（不得当作已知）：真人手绘命中率、"惊喜感"、三 baseline 谁更符合肉眼（需人工标注）、
   浏览器 Worker 内延迟、`river`/`water` 是否入场（`add_now: false` 的复核）。
6. 之后 P0-7 Qoder Sites 发布路径 → P0-8 Go/No-Go（依 `EXPERIMENTS.md` §6）。

**P0 门禁仍未通过**：在完成 P0-4～P0-6 并拿到真实手绘查询的 Top-10 证据前，不启动大规模 Web 开发。
