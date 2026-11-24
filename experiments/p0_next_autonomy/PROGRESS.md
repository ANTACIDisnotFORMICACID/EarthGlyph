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
- 2026-11-22 / B2 / 探针先行：原路折返前缀笔画 → 2% 弧探针弦=0 → NaN 切向
  （0/0 warning）；NaN 转角静默绕过 150° 守卫；json.dumps 输出裸 NaN（桥
  serve_sketch_search.py:87 默认 allow_nan → 浏览器 JSON.parse 拒绝）
- 2026-11-22 / B2 / 先写 3 条失败回归测试再修复：_endpoint_tangent 探针占比
  倍增（2%→4%→…→全弦）直到弦非零；自闭合笔画全弦为零 → 零向量回退（该类
  在切向被使用前已被 closed_loop 守卫拒绝，不可达）；非退化输入逐位不变
- 2026-11-22 / B2 / 五类设计边界实测：2 点笔画方形 → 生产链 ≥8 点规则拒绝
  （正确委托）；针尖三角形 h=57.74 精确翻转（150° 上限）；笔内 99% 点前 1%
  弧与端点 0.5px 抖动 → 同判决同转角；自闭合 → closed_loop_with_extras
- 2026-11-22 / B2 / 门禁：全量 1137 passed（+3）、ruff、mypy 42 文件、
  --strict 5 脚本全绿；D97 + experiment_log + RESULTS §4.5 已写；提交推送
- 2026-11-22 / B3 / E1 容差边界：二分 4.498424 px == 0.03×scale（机器精度），
  ±0.01 px 判定正确翻转；E2 双环、E3 重画边均 no_legal_assembly（不合并、
  不选边）——全部设计行为
- 2026-11-22 / B3 / E4 预算可达性：两簇直线扇 1 节点（紧簇必 180° 掉头，
  第一步即死）；8 弯折笔画两簇 936 节点（0.94% 预算，弯折软化掉头后的
  结构上限 ≈4!² 环）；重画方形 47；四车道 7；全部 ≤2.9 ms，无一触发 100k
  预算——≤8 笔画下结构性不可达；精确节点数由二分法测得（进程内临时调低
  预算，生产常量不动）
- 2026-11-22 / B3 / 语义发现：MAX_MATCHINGS 是中止守卫非严格界限（四车道
  cap=1 仍正确返回 ambiguous）；守卫只在节点进入时检查 → 匹配数为守卫
  可见下界（节点数精确）
- 2026-11-22 / B3 / 拒绝路径功能验证 + 2 条回归测试（monkeypatch 临时调低
  预算，生产常量零改动）；M1 套件 50 passed；全量 1139 passed、ruff、
  mypy（42 文件 + --strict 5 脚本）全绿；D98 + experiment_log +
  RESULTS §4.6/§4.7 已写；提交推送
- 2026-11-24 / E / 干净树验证（git archive HEAD 提取纯跟踪树，无 data/ 无
  本地产物）：Python 1027 passed + 112 skipped（0 fail / 0 error，96 s）；
  发现 24 处产物依赖测试缺 skip 守卫（test_arm_diagnostics 23 ERROR +
  test_visual_review_report 1 FAIL，根因 = p0_perceptual_probe 冻结产物
  未入库），按既有"build artifact not in this checkout"惯例补守卫
  （c7feb93），本地产物齐全时行为零变化（1139 passed 不变）；web 干净树
  四门禁全绿（npm ci / vitest 17 passed / tsc / build 142.79 kB，产物哈希
  index-D5IlZnSq.js 与本地一致）
- 2026-11-24 / E / CI 配置提交（8f04772，.github/workflows/ci.yml）：两
  job 镜像本地检查点门禁（Python 3.12：pytest -q -o addopts="" / ruff /
  mypy / --strict 5 脚本；Node 26：npm ci / test / typecheck / build）；
  工具钉本地实测版（ruff 0.17.3 / mypy 2.4.1 / pytest 9.2.0）；仓库
  public → 免费 runner、CI 零数据下载；远端首跑 run 45379510833
- 2026-11-24 / E / 首跑结果（API 实查，日志 403 无权限）：web job success
  （四步全过）；python job pytest 步骤 failure（exit 1，ruff/mypy 未及运
  行）。TASKS.md 曾误记"python job success"，发现 API 实况后立即更正——
  未验证结论不得写进文档
- 2026-11-24 / E / 归因排查（版本漂移假设排除）：pip --dry-run 解析 CI
  全新装版本 vs 本地 venv（numpy 2.5.5/2.5.4、pandas 3.1.1/3.1.0、
  opencv 5.1.0.95/5.0.0 等仅 patch/小版本差）→ 新建 /tmp/eg_freshvenv 用
  与 CI 完全相同的安装命令，干净树跑全套 → **1027 passed + 112 skipped
  （与本地一致）→ 版本漂移假设排除**
- 2026-11-24 / E / 诊断通道建设（dadcfd8）：pytest 步骤失败时把 FAILED/
  ERROR 行写 $GITHUB_STEP_SUMMARY + 前 10 个失败测试 ID 以 ::error 注记
  发布（日志无权限时 check-runs API 可读）；bash 摘要块本地模拟验证
- 2026-11-24 / E / dadcfd8 重跑 run 45383867580 显示全绿——**当时误判为
  "首跑失败是一次性瞬态"（后证实为假绿，见下）**；一度把瞬态结论写进
  未提交文档草稿，取证数据到位后全部更正
- 2026-11-24 / E / eol 根因确证：索引存 LF（i/lf）而 Windows 工作区
  CRLF → d5b/d6/cp8 审计 sha256/size 记录钉的是 CRLF 呈现；本地把
  benchmark 产物重写为 LF 精确复现 **4 failed**（d6 两条 +
  test_d5b_artefacts_agree_with_the_committed_d5_values +
  test_published_run_binds_the_design_it_promised）；全仓路径字面量/导入
  大小写与平台专用 API 扫描零命中
- 2026-11-24 / E / 修复 1（d13f40a）：.gitattributes 对
  experiments/shape_perception_benchmark/** 钉 text=auto eol=crlf（blob
  不变，仅 checkout 呈现）；证明：本地 LF 重写 4 failed → git -c
  core.autocrlf=false clone 模拟 Linux checkout → 产物字节与 Windows
  工作区逐位一致、4 个 eol 敏感测试文件 109 passed；全量门禁 1139
  passed / ruff / mypy 复跑全绿；CI run 45386706411 success
- 2026-11-24 / E / 取证通道（f592feb）：checkout 后 git 版本 /
  core.autocrlf / 钉死产物 sha256 以 ::notice 注记发布。**实跑取证定案：
  git 2.56.1 / core.autocrlf=unset（runner 默认 LF checkout，首跑失败
  机制坐实）/ d6 sha256 == CRLF 记录值（.gitattributes 实际生效）**
- 2026-11-24 / E / **假绿事故坐实与修复（b269fbc）**：dadcfd8 的摘要步骤
  `pytest | tee` 未设 pipefail → 步骤出口码取 tee 的
  0 → run 45383867580 的"全绿"实为**同 4 个失败被吞掉的假绿**（90 s
  全套实跑过但失败不可见）；修复 = `set -o pipefail`（本地双路径验证：
  失败→摘要分支 exit 1，成功→0）；影响 = 归因多绕一轮，无人据假绿做出
  发布决策（Phase E 未收口）
- 2026-11-24 / E / **pipefail 后首个可信门禁实跑（run 45387765398，
  b269fbc）：failure**——digest 注记通道定位唯一失败
  `tests/test_alignment.py::test_batch_mirror_decisions_match_the_pairwise
  _switch`（此前 5 轮全部不可见或被吞）；至此确认 CI 从未真正绿过
- 2026-11-24 / E / 根因 2 定位（进程内复现，无需 Linux）：该测试以
  abs=1e-9 比较批/成对距离，但库含完美匹配——距离即 sqrt(eps) 地板
  ~2.1e-8（本套件 test_closed_form_has_a_machine_precision_floor 明文
  钉住）；给批路径注入数学等价的时域能量（仅求和次序不同）→ 残差钳
  0.0 vs 成对 2.107e-8，差 21× 容差；±1 ulp 扰动同幅 → 容差低于被测
  量噪声地板，测试缺陷非生产缺陷；镜像判定余量 ~5e-2 不受影响；
  生产对齐数学零改动
- 2026-11-24 / E / 修复 3（b5c85a5）：断言改 floor-aware（距离
  <1e-6 → abs=1e-7，同测试 offset 断言既有先例；否则 1e-9）；
  1-ulp 注入实验证明新容差全过、O(1) 距离实测差 ~1e-15 精度不降；
  全量 1139 passed / ruff / mypy 本地复跑全绿
- 2026-11-24 / E / 诊断通道补强（aed8cda）：失败摘要把 pytest 前 10
  条 `E  assert ...` 行也发布为 ::error 注记（本地假日志模拟验证，
  本网络 run 页面不可达时唯一可读通道）
- 2026-11-24 / E / 推送 b5c85a5 + aed8cda（b269fbc..aed8cda）；run
  45390183895：web job 全绿，python job pytest/ruff/mypy/--strict
  全过 → **CI 首次真正全绿**（结果以 API 实查为准）
