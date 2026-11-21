# P0 收官长期自主工程 — 结果记录

> 规则：本文件只写**已验证**的结论；未运行的部分标 NOT RUN；被阻塞的部分标
> BLOCKED 并写明阻塞物。每个结论必须能由复现命令重新得到。

## 1. 基线审计（Phase A，2026-11-21）

| 项目 | 实测值 | 复现命令 |
|---|---|---|
| Git 基线 | HEAD == origin/main == `0044482431fab9a825d12c506b6929ff5ec0e3fb`，工作区干净 | `git rev-parse HEAD` && `git ls-remote origin refs/heads/main` |
| Python 测试 | **1114 passed**（142 s，全部离线） | `.venv/Scripts/python.exe -m pytest -q -o addopts=""` |
| Ruff | All checks passed | `.venv/Scripts/python.exe -m ruff check .` |
| mypy（strict，42 文件） | no issues | `.venv/Scripts/python.exe -m mypy` |
| mypy --strict serve_sketch_search.py | no issues | `.venv/Scripts/python.exe -m mypy --strict scripts/serve_sketch_search.py` |
| Web 测试 | 6 passed（vitest，1.00 s） | `cd web && npm run test` |
| Web typecheck | tsc --noEmit 干净 | `cd web && npm run typecheck` |
| Web 构建 | 成功，`dist/assets/index-D5IlZnSq.js` 142.79 kB（gzip 46.14 kB） | `cd web && npm run build` |
| npm ci | 干净安装成功（Node v24.18.0 / npm 12.x） | `cd web && npm ci` |

结论与简报基线一致：main=`0044482`，无漂移、无未提交工作、无失败测试。

### 1.1 实际状态盘点

- **真实索引在位**：`data/index/`（metadata.parquet + features.npy + shapes.npy +
  data_manifest.json），839 候选，Overture 2026-10-21.0，bbox (-90, 44, -89, 45)。
- **P0-6 真人数据：0 份**。`experiments/p0_6_human_queries/sessions/` 不存在。
  已有工具：collector（登记+结构校验+去重+匿名化）、preflight gate、
  judgement-sheet 生成器（冻结参数检索+盲评单+评委乱序 CSV）。
  **缺最后一环：评分读取与 Go/No-Go 汇总**（Phase C 目标）。
- **M1 多笔画能力**：`earthglyph/geometry/contour_assembly.py`（42 测试）+
  HTTP 桥 v2 载荷 + 画板多笔画 UI；等价冒烟（ph-* 闭合轨迹，6 次运行
  Top-10 ID 一致，|Δd|≤5e-3）已在 `0044482` 基线验证。
- **Web/部署**：Vite 构建可用；**无任何静态部署产物、无 TS 端检索实现**
  （Phase D 目标是验证契约而非产品开发）。
- **CI：无**（`.github/workflows/` 不存在）→ Phase E。

### 1.2 主要风险

1. P0-6 是 P0 门禁唯一剩余入口，依赖真人采集（外部事件，不可自主完成）；
   本轮把采集前的全部工程准备做完，采集一到即可出数。
2. 盲评评分口径若在采集后才设计，容易引入"赛后有利分母"；Phase C 先冻结
   工具与测试。
3. 静态部署的 TS 端算法若与 Python 端存在数值漂移，会静默破坏检索一致性
   （Phase D 金丝雀向量用于暴露这一点）。

## 2. Phase C — P0-6 评分汇总闭环（2026-11-21，全部 SYNTHETIC TEST）

**结论：VERIFIED（合成端到端）；真人数据 0 份，本轮产出不含任何真人证据。**

### 2.1 交付

| 文件 | 内容 | 验证 |
|---|---|---|
| `scripts/score_p06_judgements.py` | 评分读取/校验/三口径汇总/Go-No-Go/报告 | 16 条新测试 + mypy --strict |
| `scripts/make_p06_judgement_sheets.py` | +抽屉自评表、+key_options sha256 | 3 条既有测试（含新断言） |
| `experiments/p0_6_human_queries/SCORING_GUIDE.md` | 采集→评分操作手册与真人环节清单 | 文档评审（命令与脚本一致） |
| `tests/test_p06_scoring.py` | 16 条端到端（10×6 夹具） | `pytest tests/test_p06_scoring.py` |

### 2.2 三口径设计（D94）

registered_finals（判定口径，拒绝与漏评=不成功）≤ sheeted（拒绝出局）≤
complete_data（漏评出局）。判定取最严格；"恰 50% 必 NO-GO"与"提取拒绝计入
分母"两条测试钉死该行为。协议歧义以并列报告处理，不静默选分母。

### 2.3 测试矩阵实测结果（简报 C4 全覆盖）

完整合法 GO / 恰 50% NO-GO / 略高 GO / 评委漏评不影响主判据 / 重复评分 INVALID /
错误映射 INVALID（同时抓出密钥 sha 篡改）/ 未知 query_id INVALID / 非法 verdict
与 most_similar INVALID / 身份错位 INVALID / 数据集漂移 INVALID / 提取拒绝
（1/2 vs 1/1 口径分歧如实并列，严格口径 NO-GO）/ 参与者不足 NOT READY /
幅数不足 NOT READY / 独立重算逐位相等 / --targets 归因计算正确 / 报告无
candidate_id 与 row 泄漏。

### 2.4 已知缺口（移交用户决策）

协议 §2.3"题目记入 session manifest"collector 未实现：§6 归因判据需要目标
登记；评分器缺 `--targets` 时明示 not_computed。采集前是否给 collector 加
题目字段 = 协议工具设计决策（SCORING_GUIDE §6）。

## 3. Phase D — 静态部署与浏览器检索可行性（2026-11-21）

**结论：静态导出 + 跨语言检索链 VERIFIED（真实索引，粗筛级）；Vite 确定性构建
VERIFIED；本地静态服务冒烟 NOT RUN（本轮未做浏览器端到端）；完整 align 精排
移植判定为超出 P0 技术验证边界（复杂度报告见下）。**

### 3.1 交付

| 文件 | 内容 | 验证 |
|---|---|---|
| `scripts/export_static_index.py` | 真实索引 → 自包含 JSON（Float32 特征/128 点轮廓/ID/经纬度/血缘） | 839 候选导出实测，sha256 记录 |
| `scripts/make_golden_retrieval_vectors.py` | 全链路 golden 向量生成器（生产函数直跑） | 与 export 同索引生成 |
| `web/src/search/golden_retrieval.json` | golden 向量：270 点轨迹 → 重采样/平滑/归一/16 谐波/几何 4 统计/真实索引 Top-10 | Python 侧再生可复现 |
| `web/src/search/retrieval.ts` | TS 端生产链移植（ensureCcw/arcLengthResample/smoothCyclic/normalize/harmonicAmplitudes/shapeMetrics（含单调链凸包+旋转卡壳）/coarseShortlist） | 17 vitest 全过 |
| `web/src/search/retrieval.test.ts` | 契约测试（逐级 1e-9 容差 + 真实导出全库 Top-10 行与距离逐位一致） | 17 vitest 全过 |

### 3.2 跨语言等价实测（核心数字）

- 重采样/平滑/归一化：每点绝对/相对差 < 1e-9。
- 16 个 Fourier 幅度 + 4 个几何统计（含凸包、最小旋转矩形）：相对差 < 1e-9。
- **真实索引粗筛 Top-10**：TS `coarseShortlist`（839×16 Float32）与 Python
  生产 `coarse.rank` 行序列完全一致（358, 144, 113, 82, 342, 234, 766, 252,
  302, 84），距离相对差 < 1e-9。candidate_id 随行一致。
- TS DFT 为 O(N²) 直排（N=128，~16k 复数运算/次），无需 FFT 库依赖。

### 3.3 Vite 构建确定性

两次相隔数小时的干净构建产物哈希相同（`index-D5IlZnSq.js` 142.79 kB，
gzip 46.14 kB）→ 内容寻址未变，构建确定性 VERIFIED。

### 3.4 范围与未做（如实）

- **align 精排（循环移位+旋转对齐+Chamfer）未移植**：生产精排含
  循环互相关 + 逐对 Chamfer 重排（`earthglyph/retrieval/alignment.py`
  + `chamfer.py`），是查询时 CPU 大头；完整移植+数值等价验证估计
  1–2 天工程量。判定：**超出 P0 技术验证边界，保留给 P1**（简报 D3 允许）。
  P1 风险记录：精排若在 Worker 中用 O(N²) 直排需 ~839×128×128 距离计算，
  预估 < 1 s（Worker 中可接受），但**未实测**。
- **本地静态服务冒烟（D4-3）NOT RUN**：未起 vite preview + 浏览器加载
  static_index.json 走 fetch 路径（本轮 TS 侧直接从文件系统读）。留给
  下一个检查点或 CI。
- **Qoder Sites 实际发布 NOT RUN**：需账号授权，按简报不擅自发布。
- static_index.json（7.4 MB）不入库（数据产物，可由脚本从 data/index 复现，
  sha256=6e7c6baf063c7016d7f5704d5ae1d53b6d4f3d5c8448cfd9c761e70c54490a13；
  导出命令写在脚本 docstring）。
