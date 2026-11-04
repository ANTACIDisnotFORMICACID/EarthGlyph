# TASKS — 当前任务、状态、阻塞与下一步

> 规则见 [`AGENTS.md`](../AGENTS.md)；阶段门禁见 [`ROADMAP.md`](ROADMAP.md)。
> 最后更新：2026-11-05（P0-2 完成，等待授权启动 P0-3；仓库维护规则已写入 `AGENTS.md`）。

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

> ⚠️ **P0 门禁仍未通过**：真实数据的"可检索性"（手绘草图 → Top-10 肉眼相似）尚未验证，
> 属 P0-2～P0-6 范围。P0 未通过前不进行大规模 Web 开发。

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

**P0-3 特征与索引构建**
- 计算 Fourier 描述子（12–24 维）+ 紧致度/长宽比等粗筛特征；保留 128 点精排轮廓。
- 交付：`scripts/build_index.py`、`earthglyph/features`、`data/index/`（features/metadata/shapes/data_manifest.json）。
- 验收：`data_manifest.json` 记录来源/版本/下载时间/覆盖/过滤/有效地物数/许可/commit；每个候选可经 id 回查真实经纬度。

**P0-4 检索 Baseline**
- 实现 ≥2 种基线：Hu 矩（`cv2.matchShapes`）+ Fourier 粗筛 + 轮廓对齐精排（复数相关求 θ + 循环移位）。
- 交付：`earthglyph/retrieval`、`scripts/evaluate_retrieval.py`。
- 验收：固定草图 + 种子可复现同一 Top-K；生成真实 Top-10 候选轮廓拼图 + 每个候选真实地图定位。

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
2. **候选库多样性不足**：若过滤后多为相似椭圆，检索无惊喜感 → 需换区域或调过滤。
3. **真实手绘形变超出几何匹配能力**：合成扰动通过但真人手绘失败 → 精排需加 Chamfer/IoU 或调整产品定位。
4. **浏览器端检索延迟**：1–5 万候选在 Worker 内可能偏慢 → 先优化数据格式，不急上后端。
5. **Qoder Sites 发布流程与预期不符**：P0-7 需提前验证，避免 P4 踩坑。
6. **pip/npm 代理不稳定**：环境曾现 127.0.0.1 代理告警，依赖安装需复测。

## 仓库维护检查点

规则正文见 [`AGENTS.md`](../AGENTS.md) 的「Git 规范与仓库维护」：提交内容标准、完整小版本检查点、远程与分支、历史保护。

| 日期 | commit | 内容 | 远程核验 |
|------|--------|------|----------|
| 2026-11-05 | `6c71245` | P0-0 / P0-1 / P0-2 基线（首个检查点） | ✅ `origin/main` |
| 2026-11-05 | `737ea68` | `docs:` 写入仓库维护与小版本推送规则 + `.gitignore` 审查包 | ✅ `git ls-remote origin` = `737ea681fa02beb8c4865e8528b71dd5fe67c3b3` |

- 远程通道：`ssh://git@ssh.github.com:443/...`（本机 `github.com:443` HTTPS 被阻断）；提交身份用一次性 `-c` 参数传入，未修改机器级 `git config`。
- 复现前检查：`pytest -q`（137 passed）· `ruff check .`（passed）· `mypy`（no issues in 13 files）。
- 待办：P0-3 完成后按同一规则提交并推送，记录 commit sha 与数据 sha256。

## 阻塞（当前）

- **无阻塞。** P0-0 / P0-1 / P0-2 已完成，标准轮廓库可用。
- 按任务边界，**P0-3 未启动**（等待授权）：P0-2 只证明"真实地物可转为统一轮廓"，不证明"可检索"。
- 非阻塞遗留：系统 GDAL CLI 未安装（Python 栈已覆盖需求）；`web/` 的 `npm audit` 6 个开发链依赖项待 P1 复评；
  `scripts/inspect_data.py` / `scripts/preview_data.py` 有 10 个 mypy strict 报错（P0-1 遗留，配置范围为 `earthglyph/`，未阻塞）。

## 下一步：P0-3 特征与索引构建（待授权）

P0-2 已给出明确的 P0-3 输入条件：

1. **输入**：`data/processed/contours.parquet`（839 候选，128 点外环轮廓 + 元数据），
   无需再读原始 GeoParquet；`data_manifest.json` 提供来源与内容摘要。
2. **特征**：Fourier 描述子（12–24 维）+ 紧致度 / 长宽比等粗筛特征；保留 128 点精排轮廓。
3. **采样点数消融（D7）**：P0-2 实测枝状水库最坏弧长步长偏差约 104%，
   建议 P0-3 同时评估 N=64/128/256 与按复杂度自适应 N。
4. **待分析数据**：`pending_analysis.parquet`（1,034 个 river/canal/human_made/water 多边形）
   可在 P0-3 决定是否扩入候选库（D19）。
5. 交付后进入 P0-4（Baseline）→ P0-5（合成扰动）→ P0-6（真实手绘）→ P0-8 Go/No-Go。

**P0 门禁仍未通过**：在完成 P0-3～P0-6 并拿到真实手绘查询的 Top-10 证据前，不启动大规模 Web 开发。
