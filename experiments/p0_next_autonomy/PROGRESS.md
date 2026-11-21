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
