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

## 进行中：P0-4.5 面向人眼感知的形状检索算法对比 ✅ Checkpoint A（2026-11-06）

任务边界：本次做到 **Checkpoint A（数据与实验设计 + 受控图库 + control_gallery + 固定划分）**
即停止提交同步；B（E1/E2/E3 实现）、C（全方法运行 + 对比图）、D（指标 + 盲评 + 结论）为后续检查点。
**未进入 P0-5 扰动分级曲线，未开发任何 Web 产品界面。**

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
| Task D `control_gallery.png` | ✅ 22 行 × 14 列（1 184 570 B，入库理由：受控设计的人工复核物，可由命令重建，见提交信息）；标签 "Controlled Shape Retrieval — 非真实地理景观" |
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
- 提交身份：一次性 `-c user.name/user.email` 传入（`ANTACIDisnotFORMICACID`），未修改机器级 `git config`。
- 远程通道：`ssh://git@ssh.github.com:443/...`（本机 `github.com:443` HTTPS 被阻断）；
  未使用 `push --force`，未改写已推送历史（修正一律新增提交）。

## 阻塞（当前）

- **无阻塞。** P0-0 / P0-1 / P0-2 / P0-3 / P0-4 已完成：标准轮廓库、特征索引与
  真实索引上的两级检索链路均可用（`search_by_contour` / `search_by_image`）。
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
