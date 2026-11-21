# P0 收官长期自主工程 — 进度日志

格式：一行一个事件（时间 / 阶段 / 事件 / 证据 / 状态）。

- 2026-11-21 / A / 目录与计划建立（PLAN.md/PROGRESS.md/RESULTS.md） / 本目录 / done
- 2026-11-21 / A / Git 复核：HEAD==remote==`0044482`，工作区干净 / `git ls-remote` 对比 `git rev-parse HEAD` / done
- 2026-11-21 / A / 四项 Python 门禁 + Web 四项门禁全部实测通过（1114 passed；ruff/mypy 干净；vitest 6 passed；build 142.79 kB）/ RESULTS.md §1 / done
- 2026-11-21 / A / 审计基线记录完成（索引 839 候选在位；P0-6 真人数据 0 份；M1 已上产；无 CI；无静态部署）/ RESULTS.md §1.1–1.2 / done
- 2026-11-21 / C / 开始 P0-6 评分体系：已读 PROTOCOL.md + 三个既有脚本 + 既有测试；评分器为缺失环节 / 本文件 / in progress
- 2026-11-21 / C / `score_p06_judgements.py` 实现完成：三口径主判据（最严定判）、
  硬错误校验（重复/未知 query/非法枚举/身份错位/密钥篡改/数据集漂移）、双路内部
  交叉核验、可选 --targets 归因、--synthetic 标记、隐私安全报告 / D94 记录
- 2026-11-21 / C / `make_p06_judgement_sheets.py` 补齐协议 §5 抽屉自评表
  `sheet_self_<PXX>.csv` + `key_options_sha256` 防篡改凭据（评委表零改动，
  既有 3 测试含新断言全过）
- 2026-11-21 / C / `tests/test_p06_scoring.py` 16 条 SYNTHETIC TEST 全过
  （10×6 真实管线夹具；覆盖简报 C4 全部边界；含独立重算交叉核验）；
  修复过程中发现并修正测试夹具两处自身缺陷（sha 重复、fork 未隔离 sessions）
- 2026-11-21 / C / `SCORING_GUIDE.md` 操作手册：真人环节清单、全流程命令、
  三口径解释、隐私红线、已知缺口（§2.3 题目登记未实现 → 归因 not_computed）
- 2026-11-21 / C / TASKS.md 检查点记录 + DECISIONS.md D94 + experiment_log 条目；
  全量门禁通过后提交推送（见 RESULTS §2 的 git 记录）
- 2026-11-21 / C / Phase C 完成：P0-6 全管线合成端到端 VERIFIED；真人数据仍 0 份。
  下一任务：Phase D 静态部署可行性验证（按推荐顺序 C→D）
- 2026-11-21 / D / `export_static_index.py`：真实 839 候选索引 → 7.4 MB 自包含
  JSON（sha256 6e7c6baf...，不入库，可由命令复现）/ 导出实测 PASS
- 2026-11-21 / D / `make_golden_retrieval_vectors.py` + `golden_retrieval.json`：
  全链路 golden（生产函数直跑：270 点轨迹→重采样→平滑→归一→16 谐波→几何 4
  统计→真实索引 Top-10）
- 2026-11-21 / D / `web/src/search/retrieval.ts` 移植 + `retrieval.test.ts` 契约：
  逐级 1e-9 通过；**真实导出全库 Top-10 与 Python 生产 rank() 行序列与距离
  逐位一致**（行 358,144,113,...；candidate_id 随行一致）。jsdom 重写
  import.meta.url 的坑以 process.cwd() 规避
- 2026-11-21 / D / Vite 构建确定性：两次构建产物哈希一致（D5IlZnSq）
- 2026-11-21 / D / Phase D 收口：粗筛级跨语言 VERIFIED；align 精排移植判超界
  留 P1；本地静态服务冒烟与 Qoder Sites 发布 NOT RUN。全 web 门禁 17 passed
  + tsc + build 全绿后提交
- 2026-11-22 / B1 / 首版 b1 脚本两处自身缺陷修正：`_linspace_pts` y 坐标笔误
  （a[1] 重复相加）；基几何误用"中点分段"（端点相距 ~200 px，全部 open_gap
  拒绝）→ 重设计为 4 条整边 + 角部统一 pullback 留缝
- 2026-11-22 / B1 / 成对实验（修复前）：5 密度模式原始点尺度 168.5–192.3 px、
  容差 5.054–5.768 px（±12.8%）；固定角缝 5.411 px 同一幅画 3 ok / 2
  rejected(open_gap) —— 判决随指针密度翻转，假设 CONFIRMED；机制=scale→容差
  （tol±0.05 二分边界）；可装配模式 Fourier 向量差 ≤3.1e-4
- 2026-11-22 / B1 / 最小修复（简报 B 预授权）：`_arc_weighted_scale` 逐段闭式
  积分替换原始点 RMS；先写 3 条失败回归测试再修复；修复后 5 模式 scale 全部
  184.581（差 2.8e-14）、同一幅画全部 ok；解析值独立验证 rel<1e-12；D93 其余
  结构零改动；45 条 M1 测试零改动全过
- 2026-11-22 / B1 / 门禁：全量 1134 passed、ruff 全过、mypy 42 文件无问题、
  --strict 5 脚本无问题；D96 + experiment_log + RESULTS §4 已写；提交推送
  （见 RESULTS §4 与 git 记录）
