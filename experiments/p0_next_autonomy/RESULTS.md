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
