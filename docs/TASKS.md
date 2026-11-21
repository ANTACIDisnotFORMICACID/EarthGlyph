# TASKS — 当前任务、状态、阻塞与下一步

> 最后更新：2026-11-22（P0 收官自主工程轮：B1 装配尺度密度敏感性实验 +
  弧长加权修复 D96、B2 切向 NaN 穿透修复 D97、B3 全局拼接与预算边界
  D98；此前 P0-6 评分闭环 `67878bc`、P0-7 静态导出与跨语言
  契约 `2609450`；P0-0…P0-4.6 历史检查点与 2026-11-18 现状快照
  仍逐字存于 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）。

## 当前状态总览（2026-11-20，基线 `6572b68`）

| 项 | 状态 |
|---|---|
| 生产检索管线（真实数据端到端） | ✅ VERIFIED：Overture 2026-10-21.0（bbox -90,44,-89,45）→ 839 真实候选 → 手绘 → Top-10（真实经纬度 + candidate_id） |
| 工程基线（本日实跑） | `pytest -q -o addopts=""` **1139 passed**（2026-11-22，B1/B2/B3 各 +3/+3/+2 条回归） · `ruff check .` 全过 · `mypy` 42 源文件无问题 · `mypy --strict`（5 个生产脚本）无问题 |
| 合成扰动（P0-5） | ✅ Top-1 **0.7887** [0.7698, 0.8078]（3,658 查询，源聚类 bootstrap）；精排相对纯粗排 **+5.6pp**；归因 A 294 / **B 粗筛 1 / C 精排 0** / D 296 |
| 研究对照（P0-4.5 / P0-4.6） | ✅ 收官：E1/E2/E3 三臂 + 官方 ShapeComp 端口 + 旋转对照；结论**不换生产默认**（D54 / D65 / D86） |
| P0-6 真人采集 | 🟡 管线就绪（协议冻结、门禁对空数据实跑正确拒绝、**评分汇总闭环已建**）但 **0 份真人数据** |
| P0-7 Qoder Sites 验证 / P0-8 Go-No-Go | 🟡 P0-7 技术核心已验证（真实索引导出 + 跨语言粗筛契约逐位一致 + Vite 确定性构建，2026-11-21）；浏览器 fetch 冒烟与实际发布 NOT RUN；P0-8 待 P0-6 数据 |
| 调试工具 | ✅ 画板一键检索（`21acd9c`）+ M1 多笔画拼接（`12b4c01`）；均不跨 P0 门禁、不构成 P0-6 证据 |

**门禁：P0 未通过。** 唯一剩余入口是 P0-6 真人采集（判据 `EXPERIMENTS.md` §6，
主判据：≥10 名用户的抽屉自评 Top-10 含 similar 占比 > 50%）。在拿到真人数据前，
不得宣称真人命中率 / "人眼匹配准确率" / 产品体验验收；合成 0.79 不得说成产品准确率。
P0 未过前不启动大规模 Web 开发。

## 当前阶段：P0 — 真实数据与检索可行性验证（进行中）

**门禁：P0 未通过前不进行大规模 Web 产品开发。**

### P0 任务拆解（可独立实施、可验证）

> 每个任务交付：输入文件、预期输出、运行命令、测试样例、失败处理、改动清单。
> 检索 / 实验类任务须遵循 `earthglyph-retrieval` Skill 工作流。

**P0-0 环境与依赖补齐** ✅ 完成（实测记录见 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）
- 安装 P0 所需 Python 依赖：`pip install -e ".[data,cv]"`（geopandas/pyarrow/duckdb/overturemaps/matplotlib/opencv）。
- 按需安装系统 GDAL CLI（数据检查 / 转换时用）。→ 实测**不需要**：pyogrio 自带 GDAL 3.12.4，Python 栈已覆盖 P0 需求。
- 验收：`python -c "import geopandas, cv2, overturemaps"` 无错；`pytest` 仍通过。→ ✅ 65 passed + mypy strict 通过。

**P0-1 数据获取（小 bbox 起）** ✅ 完成（实测记录见 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）
- 初始区域：美国威斯康星州湖泊区 `bbox=-90,44,-89,45`（样例，可换芬兰南部等湖泊密集区）。
- 来源：Overture Maps Base/Water，`overturemaps download --type=water -f geoparquet`。
  → 实测：本机官方 CLI 的 S3 直连不稳定，默认改为 STAC + DuckDB httpfs range 读取（D13），
  `--fetch-method cli` 仍保留官方路径。
- 交付：`scripts/download_data.py`、`data/raw/*.parquet`（不入 Git）。
- 失败处理：若下载被网络阻断，明确报告阻塞 + 复现命令，**不得伪造数据**；备选 Geofabrik OSM PBF。
  → 未触发：真实数据已获取（6,543 行），Geofabrik 备选无需启用。
- 验收：打印实际字段，确认含 `id/geometry/subtype/names/bbox/sources`。→ ✅ 全部存在，实际 Schema 见 DATA_PIPELINE §8.2。

**P0-2 几何清洗与轮廓提取** ✅ 完成（实测记录见 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）
- 过滤 Polygon/MultiPolygon + subtype(lake/pond/reservoir)；`make_valid` 修复；MultiPolygon 拆分记 `parent_id`；局部投影转米制；面积过滤；弧长重采样 128 点；归一化。
- 交付：`scripts/prepare_polygons.py`、`earthglyph/geometry`（resample/project/extract）、`data/processed/`。
- 验收：单元测试覆盖平移/缩放/旋转不变性、镜像开关、无效草图、重复水体过滤；输出真实轮廓缩略图 + 坐标 + 来源 ID。→ ✅ 137 passed；四类真实数据检查图见 `experiments/p0_geometry_probe/`。
- 实测修正：D15 框边过滤作废（无裁剪证据，改信息性标志）；面积/长宽比不做硬性形状删除（月牙、枝状、细长湖均保留，见图 D）。

**P0-3 特征与索引构建** ✅ 完成（实测记录见 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）
- 计算 Fourier 描述子（12–24 维）+ 紧致度/长宽比等粗筛特征；保留 128 点精排轮廓。
- 交付：`scripts/build_index.py`、`earthglyph/features`、`data/index/`（features/metadata/shapes/data_manifest.json）。
- 验收：`data_manifest.json` 记录来源/版本/下载时间/覆盖/过滤/有效地物数/许可/commit；每个候选可经 id 回查真实经纬度。→ ✅ 全部满足；839/839 入索引、0 跳过、两次构建字节一致。
- 实测修正：谐波数 16（12/24 无可测增益）；**N=128 保留**（64/256 对照见 D29）；
  GEOS 最小外接矩形在有效凸包上退化 → 自实现（D28）；P0-2 的"104% 步长偏差"是弦长离散度不是重建误差（D30）。

**P0-4 手绘抽象提取与检索 Baseline** ✅ 完成（实测记录见 [`TASKS_HISTORY.md`](TASKS_HISTORY.md)）
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

**P0-5 算法正确性实验（合成扰动）** ✅ 完成（2026-11-11，结果见下方「近期检查点记录」）
- 抽 100–200 真实轮廓，施加已知平移/缩放/旋转/轻微形变/重采样作模拟查询。
- 交付：`experiments/<exp>/`（results.csv + 图 + notes.md + 复现命令）。
- 验收：报告 Top-1/Top-10 命中率、mAP、不同扰动强度曲线；对照 Hu vs Fourier vs 粗筛+精排。
  → ✅ 3,658 查询全量完成（118 源 × 31 变体），数字与复现命令见 `experiments/p0_perturbation_sweep/`。

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


---

## 近期检查点记录（P0-5 起，完整历史见 TASKS_HISTORY.md）

### P0-5 已完成（B1–B4 + C1/C2 审计），P0-6 采集就绪未启动

**P0-5（2026-11-11 执行）**：设计先冻结（7b60ac8）→ 118 源 × 31 变体 = 3658 条查询（b303f45）
→ 生产检索器零改动跑完全集（317fbf6）。要点（详见 `experiments/p0_perturbation_sweep/notes.md`
与 EXPERIMENTS.md §3.4）：
- 总体 Top-1 **0.7887** [0.7698, 0.8078]、Top-10 0.8384、MRR 0.8035（源聚类 bootstrap）；
  对齐重排比纯粗排配对 **+5.6pp** [+3.4, +7.9]。
- G1 严格不变性全 1.000；mirror 0.1017（禁镜像政策；allowed 口径 1.000）；sparse-16 失败是
  提取拒绝不是排序失败，sparse-32 恢复 0.898；G2 最难 aspect-1.4（0.288）与 gap-0.35（0.110）。
- 归因：A 294 / D 296 / B **1** / C **0** —— 重排与去重从未弄丢真值。
- B4（6d631a6）：row 294 在 N=128/256/512 下秩完全不变（24 miss 持续、IoU 0.81→0.94）→
  **不是采样保真问题**，全局升点否决（D88）。
- C1/C2（317fbf6）：汇总数字全部由 `recompute_p05_metrics.py` 从 CSV 逐位复算 PASS；
  复算抓出并修复 `_cell` 的 top1_p0 分母口径错误（发表前，D91）；四类证据切分
  （代码正确性 / 受控合成 / 真人相似 / UX）写入 notes.md，合成 0.79 不得说成产品准确率。

**P0-6（231d19c）**：协议、采集器、盲评单生成器、预检门禁、参与者须知与模板全部就绪并测试
（15 条新测试守卫诚实数据边界）；**等待真人采集**——这是 P0 门禁唯一剩余入口。

**下一步（按优先级）**：
1. **真人采集执行**：按 `experiments/p0_6_human_queries/PROTOCOL.md` 收 ≥10 人 × ≥6 幅最终版，
   `collect_p06_queries.py` 登记 → `validate_p06_dataset.py` 过预检 → `make_p06_judgement_sheets.py`
   生成盲评单 → 拿到真人判定后按 §6 Go/No-Go 出决。**在拿到真实手绘与人工判定前，任何数字不得
   替代这一步。**
2. **枝状覆盖的算法级方案立项**（仅当授权）：B4 已排除采样密度；候选方向是多假设对齐 /
   分段弹性度量，须独立设计冻结 + 消融，不得与 P0-6 采集并行抢同一份人眼预算。
3. **P0-7 Qoder Sites 发布路径**：仅在 P0-6 Go 之后；若 No-Go 按 EXPERIMENTS.md §6 处置
   （改数据/算法或调整定位），不启动大规模 Web 开发。

**P0 门禁仍未通过**：在完成 P0-6 并拿到真实手绘查询的 Top-10 人工判定证据前，不启动大规模 Web 开发。

### P0-6 管线修复轮：外部审查三项发现全部证实并修复（2026-11-17，采集前）

外部审查（对照 8622c2e）指出 P0-6 管线三处缺陷；本轮逐条对照源码核实，**全部属实**，
端到端测试中另发现第四处（审查未报）。修复见 `scripts/collect_p06_queries.py`、
`scripts/make_p06_judgement_sheets.py`、`.gitignore`，决策记录见 D92：

1. **盲评身份映射错位（高）**：option 的 `row` 取自打乱后的 `hits[order[slot]]`，
   `candidate_id` 却取自打乱前的 `hits[slot]`——同一选项的两个身份指向不同水体，
   人工判定将无法可靠回溯。已统一取自同一 permuted hit。
2. **无效文件二次采集崩溃（中高）**：invalid 记录无 `query_id`，重跑采集器在
   `seen_sha[record["sha256"]] = record["query_id"]` 处 KeyError。已改为 invalid 走独立
   `seen_invalid` 集合；重跑已见文件（无论有效/无效）一律 no-op（`duplicate_invalid`），
   不再重复追加 manifest 记录。
3. **匿名化不完整（高）**：`.gitignore` 只挡 `sessions/key_*.json`，且 `session.json`
   记录参与者原始文件名（含自选代号，如 `alice-final-lake.json`）。已改为**整个
   `sessions/` 目录不入库**，manifest 不再写 `original` 字段；代号只存在于收件文件名与
   本地密钥表。参与者须知补知情同意条款（匿名聚合与去标识查询集形式发布）。
4. **（新发现）生成器在真实 query_id 上必然崩溃**：排序种子公式 `int(query_id[1:4])` 对
   `PXX-qNN` 切出 `"01-"` → ValueError——该生成器此前从未被端到端执行过。已修正为
   `query_id[1:3]`（参与者号 ×100 + 查询号），并由端到端测试钉住。

同时按协议补齐两处缺口：盲评生成器现在**强制先过 §7 预检门禁**（`--min-participants/
--min-finals` 仅供演练降阈值，其余检查不放宽，门禁阈值写入 manifest）；评委材料从单一
未打乱 CSV 改为**每位评委一份独立固定种子打乱的 `sheet_judge_NN.csv`**（协议 §5"标注
顺序由每评委固定种子打乱"，种子写入 manifest `judge_seeds`）。

验证：新增 8 条测试。采集器 5 条：重跑 no-op 不崩溃、修复后正常登记（P01-q02）、
重跑去重、同轮重复 invalid 不新增伪匿名号、manifest 无参与者文件名且 .gitignore 条目存在。
生成器 3 条：collect→gate→sheets 全链路（4 候选合成索引，经生产 build_index 落盘构建），
身份配对断言为**独立重放检索 + 同种子重建排列后逐项比对 `key == expected`**，并断言
`metadata.iloc[row].candidate_id == key.candidate_id`（该测试用排列 [3,1,2,0] 非恒等，
对修复前代码必然失败）；低于冻结阈值（10×6）拒绝且零写入；空 sessions 拒绝。
全量 `pytest -q` **1042 passed**（上轮 1034，0 failed 0 skipped）、`ruff check .` 全过、
`mypy` 41 源文件无问题。检索算法、P0-5 产物、真实数据一律未动。
**仍无任何真人数据；P0 门禁依旧未通过。**下一步即按协议采集真人数据（审查优先级 2）。

检查点：commit `d0adc97`（fix: repair P0-6 pipeline defects found in external review (D92)），
已推送并核验远程 `refs/heads/main` 与本地 HEAD sha 一致。

### 调试工具扩展：画板一键直接检索（2026-11-18，非产品功能，不跨 P0 门禁）

**检查点：`21acd9c`**（feat，含保真修复 1/2；已推送，`git ls-remote origin refs/heads/main`
== `git rev-parse HEAD` == 21acd9c42868e6636cd6c82fe00d9b1cbc35605c）。
门禁：`pytest -q -o addopts=""` 1058 passed / `ruff check .` 全过 / `mypy` 41 文件无问题。
复现：`python scripts/serve_sketch_search.py --index data/index --port 8765`。

用户选定的快捷检索测试入口：画板画完即出真实 Top-10。**生产代码（`earthglyph/`）零改动、
真实索引零改动、检索默认参数零改动**；这是 P0-4 调试工具链的扩展，不是 Web 产品开发。

- 新增 `scripts/serve_sketch_search.py`：stdlib HTTP，**仅 127.0.0.1**、两条固定路由
  （`GET /` 返回画板；`POST /search`）、1 MiB 请求体上限、无路径参数、无写操作。
  检索走冻结生产默认（`fourier_align / top_k=10 / shortlist=200 / smoothing=light /
  allow_mirror=False`）；**客户端传入的算法参数一律忽略**（测试钉住）；
  提取拒绝如实返回 400 + 原因，不替用户封口。
- `tools/sketch_pad.html` 新增"直接检索（本地服务）"按钮 + 结果面板：
  Top-10 表（rank / 类型 / D / 真实经纬度）+ 点击行切换叠加图
  （红虚线 = 查询，绿实线 = 候选在匹配器**实际拟合姿态**下的轮廓）；
  弱命中告警原样显示；面板明示"D 不是相似度百分比、不构成 P0-6 真人数据"。
  顺带修复既有 CLI 提示错误：`search_sketch.py`（不存在）→ `query_sketch.py`。
- 测试：新增 `tests/test_sketch_search_server.py` **15 条**（合成 4 候选索引经生产
  `prepare_contours → build_index` 落盘构建）：默认冻结 / 参数注入被忽略 / 确定性 /
  开口笔画拒绝 / 5 类畸形输入 / HTTP 契约（200/400/404）/ 413 体积上限。
- 浏览器实测（真实 839 候选索引，服务实跑）：月牙机器轨迹 → **Top-1 lake D=0.082**
  （−89.70723, 44.73080），检索 0.041 s，Top-10 全带真实经纬度；叠加图渲染与行切换正常
  （像素计数 2952 → 3054）；开口 3/4 圆弧 → 拒绝消息原样显示、零结果行。
  **机器轨迹仅作工具测试，未登记为真人数据、未写入 sessions。**
- 验证：`pytest -q -o addopts=""` → **1057 passed**（1042 基线 + 15）、`ruff check .` 全过、
  `mypy` 41 源文件无问题、`mypy --strict scripts/serve_sketch_search.py` clean。
- 复现：`python scripts/serve_sketch_search.py --index data/index --port 8765` →
  浏览器打开 `http://127.0.0.1:8765/` → 画一笔闭合轮廓 → 点"直接检索"。

### 保真修复：红虚线必须画原始笔迹（2026-11-18，用户反馈"红虚线不是我画的轮廓"）

- **检索输入链路核查（生产代码零改动）**：`serve_sketch_search.run_search` 把
  `payload["points"]` **原样**传给 `search_by_contour`；标准化（去重→闭合判定→CCW→
  128 点弧长重采样→轻度平滑→质心/RMS 归一）是生产检索自己的 documented 步骤，
  与候选库同一条代码路径。新增契约测试 `test_search_receives_the_users_exact_points`
  （monkeypatch 捕获实参，钉住 `points is payload["points"]`、closed/unit 透传）。
- **问题根因是显示层**：旧叠加图红虚线画的是 `query_contour.points`（标准化后），
  不是原始笔迹。定量探针（尖角五角星，201 点）：标准化引入的最大边界偏移
  **0.0488 RMS 半径**（Hausdorff，集中在尖角处）——足以肉眼看出"不像我画的"。
- **修复（仅 `tools/sketch_pad.html`）**：红虚线 = 原始笔迹（仅质心平移 + RMS 统一缩放
  的显示归一，与生产 `normalize_contour` 同型，不重采样不平滑）；新增灰细线 =
  检索实际使用的标准化轮廓；绿实线 = 候选拟合姿态。红灰差异即标准化的全部改动。
  告警区同时显示 `query_contour.warnings`（平滑/闭弦假设不再被隐藏）。
- **验证**：桥接测试 **16/16**（15 + 新契约测试）；浏览器实测尖角五角星 →
  红虚线径向剖面 5 个尖峰、半径 62.1→147.9 px（绘制比 60/150≈尖角保留），
  灰/绿线各自渲染，行切换时候选变、红笔迹稳定；平滑告警如实列出。
  全量 `pytest -q -o addopts=""` → **1058 passed**（1057 + 1 契约测试）、
  `ruff check .` 全过、`mypy` 41 源文件无问题。

### 保真修复 2：叠加图"不中心对齐"是显示坐标帧错误，不是比对疏忽（2026-11-18）

- **比对端核查（零改动）**：查询与候选都经 `normalize_contour`（质心居中 + RMS=1），
  `fourier_align` 闭式遍历全部 128 个循环移位 + 最优旋转（`alignment.py` 互谱取最大），
  **D 已是最优姿态下的距离**——显示上再"对准"不会提高报告的相似度。
- **显示端两个实锤缺陷（定量，非均匀采样椭圆探针）**：
  1. 红线的显示归一化直接对**原始指针点**求质心/RMS，而指针密度随手速不均
     （慢处点密）→ 质心偏移 **0.534 倍形状半径**、缩放比 0.883，
     红灰 Hausdorff 达 **0.773**；生产帧是弧长均匀重采样后统计的。
  2. 叠加图用**联合包围盒中点**做画布中心，包围盒中点 ≠ 质心 → 整组再偏
     **0.387 倍半径**。
- **修复（仅 `tools/sketch_pad.html`）**：`displayNormalize` 改为在弧长均匀重采样
  （128 点，仅用于统计）上求质心/RMS，再变换原始点——画的仍是原始笔迹；
  探针验证该帧与生产帧质心差 **0.0**、Hausdorff **0.0013**（仅平滑残差）。
  `drawOverlay` 改为以原点（=查询质心）为画布中心，缩放由最大绝对范围决定。
- **验证**：浏览器画非均匀椭圆（右半 5 倍点密）→ 红/灰/绿像素质心
  (156.1,148.7)/(158.2,149.9)/(163.4,147.1)，聚于画布中心 (150,150) ±7 px
  （修复前红线应偏约 58 px）；Top-1 D=0.036 强命中；桥接测试 16/16 复跑通过。
  本轮零 Python 改动，全量门禁沿用当轮 1058 passed 结果。

### M1 多笔画闭合轮廓输入（2026-11-20，调试画板能力扩展，不跨 P0 门禁）

**范围**：在不修改生产检索算法/默认参数的前提下，为调试画板与本地检索桥增加 2–8 笔
闭合轮廓输入与自动拼接。生产链路（`fourier_align / top_k=10 / shortlist=200 /
smoothing=light / allow_mirror=False`、128 点契约、旧 `points` JSON、P0-5 产物、
P0-6 冻结协议）一律未动；v2 输入不进入 P0-6 真人数据集。

**交付**：
- `earthglyph/geometry/contour_assembly.py`（新模块，D93 冻结）：协议 v2 解析、
  ≤8 笔/≤4096 点、全局尺度=全部点 RMS；端点连接容差 0.03×尺度 + 折角 ≤150°
  （初始实验值，标注假设/待验证）；受限回溯找用全部笔画恰好一次的简单闭合环，
  0 解→拒绝（附原因码）、1 解→ok、≥2 解→ambiguous 不挑选；候选用生产
  `_self_intersection_info` + `contour_from_points` 复检；输出闭合点列首尾严格重复
  （防 `close_trace` 吞末点）。拒绝码：`invalid_input / too_many_strokes /
  too_many_points / degenerate_stroke / closed_loop_with_extras / open_gap /
  no_legal_assembly / self_intersection / degenerate_shape / ambiguous /
  search_complexity`。
- `scripts/serve_sketch_search.py`：`strokes` 分支 → 拼接 → 拒绝/歧义不调生产检索器
  （spy 测试钉住）→ `search_by_contour`；响应含 `assembly` 审计字段（原始笔画、
  遍历序、逐接头端点/吸附点/修正量/折角）。回环绑定与 1 MiB 上限未动。
- `tools/sketch_pad.html`：1 笔仍导出旧格式；2–8 笔导出 v2；状态栏显示笔画数/拼接
  状态/拒绝原因；叠加图红=每条原始笔迹（开放折线，不互连）、琥珀=系统接头标记与
  补接线（非红）、灰=拼接+标准化、绿=候选；显示帧沿用 11-18 质心/RMS 修复。
- 测试：`tests/test_multistroke_assembly.py`（42 条：2/3/4/8 笔拆分保真、乱序/反向/
  非均匀密度、等价性、两环/T 分支/领结/8 字/明显缺口/孤立笔画/歧义/退化、确定性、
  解析拒绝）+ `test_sketch_search_server.py` M1 节（等价逐位一致、失败不调检索器、
  旧路径绕过新模块、HTTP 新旧成功/拒绝/歧义 400）。
- `experiments/_probe_ambiguity.py`：歧义构造的穷举验证（3 簇 6 笔→恰 1 解；
  凸位端点 4 车道→恰 2 解 ambiguous），测试用例由此构造冻结。
- `experiments/m1_equivalence_smoke.py`：真实索引等价冒烟。

**验证（全部实跑）**：
- `pytest -q -o addopts=""` → **1114 passed**（上轮 1058；+56 M1，0 failed）。
- `ruff check .` 全过；`mypy` 42 源文件无问题；`mypy --strict scripts/serve_sketch_search.py` 无问题。
- 真实索引冒烟（839 候选，`data/index`）：3 条真实闭合轨迹（ph-lightning/moon/star，
  各自源与许可见其 JSON）单笔 vs 2/4 笔拆分 v2，经 127.0.0.1:8765 桥 →
  Top-10 候选 ID 全部一致、align_distance 差 **0.0000**（拆分共享边界点、间隙 0）。
  QuickDraw 开放轨迹（rel gap 0.06–0.24）拆分被正确 400 拒绝（不桥接明显缺口）。
- 浏览器实测（127.0.0.1:8765 + DOM/像素质心核验）：4 笔矩形 → v2 导出、
  "4 笔拼接（4 个接头）"、Top-10、四色像素质心核验（红 1454/琥珀 176/灰 579/绿 2449）；
  行切换重画；撤销 4→3、清板→0；1 笔仍导出旧格式且检索走旧路径（琥珀 0）；
  两笔自闭环 → 400 `closed_loop_with_extras`；角点 6 px 缺口 → 400 `open_gap`
  列出孤立端点；角点 2 px 缺口 → 拼接成功且告警"4 接头各修 1.41 px，连接线是假设
  非笔迹"；往返自叠轨迹 → 生产退化拒绝如实透传。
- 已知局限（如实记录，非缺陷）：容差与折角上限为初始实验值；歧义判定按"接头配对
  集合"划分，几何上几乎相同但配对不同的两解也会判歧义；多笔画不处理自交拓扑输入
  （材料性自交直接拒绝）；本工具改进不构成 P0-6 证据。

**Git 检查点**：`12b4c01`（feat: multi-stroke closed-contour input with audited assembly，
2026-11-20 推送 `bd5a7b9..12b4c01 main -> main`；`git ls-remote origin refs/heads/main` ==
`git rev-parse HEAD` == `12b4c0192de8bb1603c2f5e628e1f77920f53a57` 已核验；作者
`ANTACIDisnotFORMICACID <ANTACIDisnotFORMICACID@users.noreply.github.com>`（逐命令 `-c` 注入））。
复现命令：`.venv/Scripts/python.exe scripts/serve_sketch_search.py --index data/index
--port 8765` + `.venv/Scripts/python.exe experiments/m1_equivalence_smoke.py`（真实索引
等价）；`.venv/Scripts/python.exe experiments/_probe_ambiguity.py`（歧义构造穷举）；
门禁：`.venv/Scripts/python.exe -m pytest -q -o addopts=""` → 1114 passed、
`-m ruff check .` 全过、`-m mypy` 42 文件无问题、`-m mypy --strict
scripts/serve_sketch_search.py` 无问题。索引数据未重建（`data/index` 沿用 Overture
release 2026-10-21.0 构建产物，本轮零触碰）。

### P0 收官自主工程轮：P0-6 评分汇总闭环（2026-11-21，采集前工程准备）

长期自主工程（`experiments/p0_next_autonomy/` 有独立 PLAN/PROGRESS/RESULTS）第一轮。
本轮前先复核基线：HEAD == origin/main == `0044482`（无漂移），四项 Python 门禁 +
Web 四项门禁（npm ci / vitest 6 passed / tsc / vite build 142.79 kB）全部实跑通过。

**交付：P0-6 评价闭环的最后一块拼图**——盲评评分回收、校验与 Go/No-Go 汇总
（此前采集/预检/盲评单生成齐备，唯独没有评分工具）：

- `scripts/score_p06_judgements.py`（新增）：读 session manifests + judgement
  manifest + gitignored 选项密钥 + 填好的 `sheet_*.csv`（自评 `self_PXX` /
  独立评委 `judge_NN`），**不 import 检索代码**（循 D91"聚合工具只读证据"先例）。
  校验：CSV 结构 / 枚举 / 重复评分 / 不存在 query_id / 身份错位（抽屉只能评
  自己的查询）/ 选项-候选唯一映射（密钥 sha256 防篡改 + 同查询选项去重）/
  生成后数据集漂移。统计：主判据三口径（`registered_finals` 判定口径 /
  `sheeted` / `complete_data`，单调不减，**取最严格定判**，杜绝静默选有利
  分母）、拒绝率、每参与者分布、每评委 similar 率与自评一致率、漏评覆盖、
  内部双路交叉核验（行驱动重算 + 按参与者求和）、可选 `--targets` 归因
  （缺目标登记时明示 not_computed）。退出码 0 ⇔ GO。`--synthetic` 横幅与
  报告标记（合成数据永不冒充真人证据）。报告只含 PXX/聚合数（测试保证无
  candidate_id 泄漏）。
- `scripts/make_p06_judgement_sheets.py`（扩展）：协议 §5 的抽屉自评此前没有
  材料——新增每参与者 `sheet_self_<PXX>.csv`（独立种子打乱、只含本人查询）、
  manifest 记录 `self_seeds` 与 `key_options_sha256`（防篡改凭据）。评委表
  行为零改动（既有断言全过）。
- `experiments/p0_6_human_queries/SCORING_GUIDE.md`（新增）：采集→预检→盲评→
  评分全流程操作手册，含需用户组织的真人环节清单、三口径解释、隐私红线、
  已知缺口（§2.3 题目登记未实现 → 归因判据 not_computed；拒绝后重画须重走
  ①→③，评分器以"数据集已变"硬错误兜底）。
- `tests/test_p06_scoring.py`（新增 16 条）：10 人 × 6 幅 = 60 查询模块级真实
  管线夹具（走完整 sheet 生成），逐测试分叉填充/破坏 CSV。覆盖简报全部边界：
  完整合法数据 GO / 恰 50% 必 NO-GO / 略高于 50% GO / 评委漏评不破主判据 /
  重复评分 / 错误候选映射 / 不存在 query_id / 非法 verdict 与 most_similar /
  身份错位 / 数据集漂移 / 提取拒绝（严格口径 1/2=50%→NO-GO vs 出单口径 1/1，
  分歧如实并列）/ 参与者不足 / 每人幅数不足 NOT READY / 独立重算交叉核验 /
  targets 归因 / 报告无候选身份泄漏。全部 SYNTHETIC TEST。

**已知缺口（如实记录）**：协议 §2.3"题目记入 session manifest"在 collector
未实现——归因（§6 次要判据）依赖目标登记，评分器已预留 `--targets` 入口并
默认明示 not_computed；是否在采集前给 collector 加题目字段属协议工具设计决策，
留给用户（见 SCORING_GUIDE §6）。

**验证（全部实跑，采集前完成）**：`pytest tests/test_p06_scoring.py` → 16 passed；
全量 `pytest -q -o addopts=""` → 1130 passed（+16）；`ruff check .` 全过；`mypy`
42 源文件无问题；`mypy --strict scripts/score_p06_judgements.py
scripts/make_p06_judgement_sheets.py` 无问题。协议、冻结判据、P0-5 产物、
索引产物零改动。

### P0-7 静态部署可行性：真实索引导出 + 跨语言检索契约（2026-11-21，Phase D）

P0-7 的技术核心（EXPERIMENTS.md §7 / ROADMAP）在不公开发布的前提下完成验证：

- **`scripts/export_static_index.py`**：真实 `data/index`（839 候选，Overture
  2026-10-21.0）→ 单文件自包含 JSON（Float32 特征矩阵 + 128 点轮廓 + ID/类型/
  经纬度 + 完整血缘：源版本、bbox、feature_columns、索引产物 sha256、索引
  git_commit）。7.4 MB，**不入库**（可由命令复现，sha256 `6e7c6baf…` 已记录）。
  索引缺失时拒绝运行，不用合成数据顶替。
- **`scripts/make_golden_retrieval_vectors.py` + `web/src/search/golden_retrieval.json`**：
  把跨语言契约从单一 `normalize_contour`（既有 golden_contour.json）扩展到全链路
  ——弧长重采样 → 循环高斯平滑(σ=0.8) → 质心/RMS 归一 → 16 谐波幅度 → 4 几何
  统计 → 真实索引粗筛 Top-10，全部由生产函数直跑生成，容差逐级记录（1e-9）。
- **`web/src/search/retrieval.ts`**：TS 端生产链移植（含单调链凸包、旋转卡壳
  最小旋转矩形、O(N²) 直排 DFT——N=128 无需 FFT 库）。`retrieval.test.ts`
  逐级 1e-9 验证；**核心结论：TS 粗筛在真实导出索引上返回的 Top-10 行序列与
  距离与 Python 生产 `coarse.rank` 逐位一致**（行 358,144,113,82,342,234,766,
  252,302,84；candidate_id 随行一致）。导出文件缺失时测试显式降级为"仅断言
  已提交的 golden 短名单"（干净 clone/CI 可跑，不静默跳过契约）。
- **Vite 构建确定性**：两次干净构建产物哈希一致（`index-D5IlZnSq.js`）。

**边界（如实）**：align 精排（循环移位+旋转对齐+Chamfer）未移植——完整移植
加数值等价验证约 1–2 天，判定超出 P0 技术验证边界，复杂度与风险已记录留 P1
（Worker 内 O(短名单×128²) 距离预估 <1 s，未实测）；本地静态服务 + 浏览器
fetch 加载冒烟 NOT RUN；Qoder Sites 实际发布 NOT RUN（需授权，不擅自发布）。

**验证**：web 侧 `npm run test` → 17 passed（含真实全库契约）、`npm run
typecheck`、`npm run build` 全绿；Python 侧 `mypy --strict`（两个新脚本）、
`ruff` 全过。详见 `experiments/p0_next_autonomy/RESULTS.md` §3。

### M1 装配尺度密度敏感性：成对实验 + 弧长加权修复（2026-11-22，Phase B / B1）

长期自主工程轮 Phase B 第一项（M1 可靠性定向实验，不重开发）。**结论：
VERIFIED —— 缺陷真实存在并已最小修复**（D96；证据
`experiments/p0_next_autonomy/b1_density_sensitivity.py` + `.json`）：

- **缺陷（成对实验，单一变量=每笔点数）**：M1 装配尺度原为原始点 RMS
  （按点数加权）——同一幅 400×240 四笔矩形在 5 种指针密度模式下尺度
  168.5–192.3 px，端点容差 5.054–5.768 px（**±12.8%**）；固定角缝
  5.411 px 时**同一幅画 3 模式 ok、2 模式 rejected(open_gap)，判决随指针
  密度翻转**。机制经验证实为 scale→容差路径；可装配模式粗筛 Fourier
  向量差 ≤3.1e-4（门敏感性 ≫ 输出敏感性）。
- **修复（简报 B 预授权的"弧长均匀重算"）**：`_arc_weighted_scale` 逐段
  闭式积分替换原始点 RMS；修复后 5 模式尺度全部 184.581（离散差 2.8e-14
  px），同一幅画全部 ok，容差天花板仍成立。独立验证：直线边矩形解析值
  rel<1e-12；先写 3 条失败回归测试再修复。
- **影响面**：仅 `contour_assembly.py` 内部尺度定义；单笔画生产路径与
  Fourier 数学零改动；D93 其余冻结结构（0.03/150°/回溯/歧义拒绝）零改动；
  既有 45 条 M1 测试零改动全过。

**验证（全部实跑）**：全量 `pytest -q -o addopts=""` → **1134 passed**
（+3）；`ruff check .` 全过；`mypy` 42 源文件无问题；`mypy --strict`
（serve_sketch_search / score / sheets / export / golden 共 5 个生产脚本）
无问题。详见 `experiments/p0_next_autonomy/RESULTS.md` §4。

### M1 端点切向退化：NaN 穿透修复 + 设计边界刻画（2026-11-22，Phase B / B2）

Phase B 第二项。**结论：VERIFIED —— 1 个实现缺陷修复 + 5 类设计边界实测**
（D97；证据 `experiments/p0_next_autonomy/b2_tangent_degeneracies.py`）：

- **缺陷（已修）**：以"原路精确折返"开头的笔画使 2% 弧切向探针弦恰为零 →
  0/0 产生 NaN 切向 → NaN 转角**静默绕过 150° 折角守卫**被接受，且
  `json.dumps` 输出裸 `NaN`（浏览器 `JSON.parse` 拒绝 → 调试桥 200 响应体
  不可解析）。修复：探针占比倍增（2%→…→全弦）直到弦非零；非退化输入
  逐位不变；3 条失败先行回归测试。
- **设计行为（实测分类，不改）**：四条 2 点笔画方形 → 生产链自身"≥8 点"
  规则拒绝（正确委托）；针尖三角形在 h=57.74 精确翻转（150° 上限）；
  笔内 99% 点在前 1% 弧 / 端点 0.5 px 抖动 → 与均匀参考同判决同转角
  （弧长比例探针天然稳健）；自闭合笔画 → `closed_loop_with_extras`。

**验证（全部实跑）**：M1 套件 48 passed（+3，既有 45 条零改动）；全量
`pytest -q -o addopts=""` → **1137 passed**；ruff、mypy（42 文件 +
--strict 5 脚本）全绿。详见 `experiments/p0_next_autonomy/RESULTS.md` §4.5。

### M1 全局拼接与预算边界：search_complexity 路径补测（2026-11-22，Phase B / B3）

Phase B 第三项（收官）。**结论：VERIFIED —— 全局边界全部按设计拒绝；
`search_complexity` 预算拒绝路径（M1 交付以来零覆盖）功能正确，且实测
≤8 笔画下不可达**（D98；证据 `experiments/p0_next_autonomy/
b3_assembly_ambiguity.py`）：

- **E1 容差边界精确成立**：二分求得的合法间隙边界 4.498424 px 与
  0.03×scale=4.498424 px 机器精度一致；−0.01 ok / +0.01 rejected。
- **E2/E3 设计拒绝**：两个独立两笔画环、底边画两遍（0.5 px）均
  `no_legal_assembly`——不合并多环、不静默为重画边选一条。
- **E4 预算可达性**：对抗稠密端点图（两簇直线双向扇 1 节点——紧簇处
  任何环必 180° 掉头被 150° 折角规则杀死；8 条弯折笔画两簇 **936 节点
  = 预算 0.94%**；四角重合端点重画方形 47；四车道歧义件 7）全部
  ≤2.9 ms，无一触发 100k 预算。精确节点数由二分法测得（进程内临时
  调低预算，生产常量零改动）。结论：≤8 笔画下预算结构性不可达。
- **拒绝路径功能验证 + 2 条回归测试**：临时调低预算下节点预算 →
  rejected/search_complexity；匹配上限 → 已枚举候选保守丢弃
  （tests/test_multistroke_assembly.py，monkeypatch，生产常量零改动）。
- **语义澄清（不改代码）**：`MAX_MATCHINGS` 是枚举中止守卫而非严格
  界限——枚举先于守卫自然结束时返回真判决（四车道件 cap=1 仍正确
  ambiguous）；节点数为精确值、匹配数为守卫可见下界。

**验证（全部实跑）**：M1 套件 50 passed（+2，既有 48 条零改动）；全量
`pytest -q -o addopts=""` → **1139 passed**；ruff、mypy（42 文件 +
--strict 5 脚本）全绿。详见 `experiments/p0_next_autonomy/RESULTS.md`
§4.6–§4.7。Phase B（B1+B2+B3）至此收官。

## 阻塞与已知问题（当前，2026-11-21）

- **P0-6 真人数据 = 唯一门禁阻塞**：管线就绪但 0 份真人数据（门禁要求 ≥10 名可区分
  参与者 × ≥6 幅最终版）；QuickDraw 的 5 个机器查询不算真人。真人盲评 0 份
  （`AI_visual_review` 是模型评图，已明确标注不是真人）。
- **候选库覆盖缺口（P0-5 已归因，覆盖类 D 为主因）**：库仅单 1°×1° 区域；星形 / 角状
  类查询无对应地物；枝状水库 `row 294` 贡献 22/30 行 D 类失败（N=128/256/512 秩不变，
  B4 已否决"采样保真"假说，瓶颈在查询侧提取平滑 + align 饱和，D88）；N=128 下 5 个
  reservoir 候选重建自交（已上报不修复）。
- **镜像政策**：默认禁镜像（D33）下 mirror 族 Top-1 仅 0.1017；真人是否常画镜像轮廓
  待 P0-6 观察后再定。
- **M1 多笔画阈值为初始实验值（D93，假设/待验证）**：端点容差 0.03×尺度、折角上限
  150°、歧义按"接头配对集合"划分；须真实验证后才可声称为合理默认。不处理自交拓扑
  输入（材料性自交直接拒绝）。尺度定义已按 B1 实验证据从原始点 RMS 改为弧长加权
  RMS（D96，密度不变；容差系数本身仍为初始值）。
- **P0-4.5 遗留（研究口径，不阻塞生产）**：E1 各向同性回退（正多边形 / 星形上切向
  角块不旋转不变，已量化未修复）；E3 sample 昂贵（T3 约 43 s/查询）；包络匹配
  crescent/heart/star 仅部分达标；图库同形缺陷待修（D53 下一轮顺序：修图库 → 补真人
  查询到 ≥10 → 攻枝状覆盖）。
- **非阻塞遗留**：系统 GDAL CLI 未安装（Python 栈已覆盖）；`web/` 的 `npm audit`
  6 个开发链依赖项待 P1 复评；`scripts/inspect_data.py` / `scripts/preview_data.py`
  有 10 个 mypy strict 报错（P0-1 遗留，mypy 配置范围为 `earthglyph/`）；跨区域 /
  极区无效几何覆盖度未测（`make_valid` 路径仅由合成单测覆盖）。

## 待办（下一步）

1. **P0-6 真人采集**（门禁唯一入口，协议已冻结）：≥10 名参与者 × ≥6 幅最终版 →
   `collect_p06_queries.py` 登记 → `validate_p06_dataset.py` 预检 →
   `make_p06_judgement_sheets.py` 盲评单（含每参与者自评表）→ 抽屉自评 + ≥2 名
   独立盲评 → `score_p06_judgements.py` 出 Go/No-Go（全流程操作手册见
   `experiments/p0_6_human_queries/SCORING_GUIDE.md`）。注意：多笔画 v2 输入
   不进入 P0-6 数据集（D93）。
2. **P0-7 Qoder Sites 静态发布路径验证**：跑通一次最小静态部署，记录实际步骤。
3. **P0-8 Go/No-Go 决策**：依据 `EXPERIMENTS.md` §6 记录结论与证据。
4. **M2 候选（调试工具，不跨门禁）**：多笔画容差 / 折角的真实验证实验；
   歧义时的交互式消歧；画板采集的独立可用性记录。

> 仓库维护检查点规则与全量台账（2026-11-05 起，含每次远程核验 sha）见
> [`AGENTS.md`](../AGENTS.md)「Git 规范与仓库维护」与
> [`TASKS_HISTORY.md`](TASKS_HISTORY.md)。
