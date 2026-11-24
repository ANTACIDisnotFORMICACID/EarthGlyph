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

## 4. Phase B / B1 — M1 装配尺度密度敏感性（2026-11-22，合成几何）

结论：**VERIFIED —— 缺陷真实存在（判决随指针密度翻转）并已最小修复**。
决策记录 D96；实验脚本与 JSON 证据在本目录
（`b1_density_sensitivity.py` / `.json`）。

### 4.1 假设与设计

M1 装配尺度原为 `trace_scale(np.vstack(raw))`（原始点 RMS）——按**点数**
加权的均值；真实指针在慢笔画上密集、快笔画上稀疏。成对实验：同一幅
400×240 四笔矩形（角部统一留缝 `pullback·√2`），唯一变量 = 每笔点数，
5 种密度模式（弧长按比例 / 等数 / 单横笔×8 / 单竖笔×8 / 全×8）。

### 4.2 实测（before → after）

| 度量 | 原始点 RMS（before） | 弧长加权（after，D96） |
|---|---|---|
| 尺度范围（5 模式） | 168.48 – 192.26 px | 184.581 px 全部一致（离散差 2.8e-14） |
| 端点容差范围 | 5.054 – 5.768 px（**±12.8%**） | 5.537 px |
| 固定角缝 5.411 px 判决 | **3 模式 ok、2 模式 rejected(open_gap)——翻转** | 5 模式全部 ok |
| 容差天花板（tol±0.05） | — | ok / open_gap 边界成立 |
| 可装配模式粗筛 Fourier 向量差 | ≤3.1e-4（门敏感性 ≫ 输出敏感性） | 同左 |

机制经验证实：翻转路径就是 scale→容差（二分边界在 tol±0.05）。

### 4.3 修复与独立验证

`_arc_weighted_scale`（contour_assembly.py）：逐段闭式积分
`∫p ds = l(a+b)/2`、`∫|p−c|²ds = l(|a−c|²+(a−c)·(b−a)+l²/3)`；零长段
跳过；全零弧 → 0 → 沿用"全部重合"拒绝。独立验证：直线边矩形解析值
`sqrt(a²(3b+a)/(3(a+b))+b²(3a+b)/(3(a+b)))` 与实现 rel<1e-12（不等边数
证明密度无关）。流程：先写 3 条失败回归测试（密度无关 scale / 固定角缝
判决稳定全 ok / 解析值）再修复。

### 4.4 影响面与门禁

仅 `contour_assembly.py` 内部尺度定义（含 dedupe 阈值与 gap_rel 审计字段）；
sketch.py 单笔画生产路径、Fourier/精排数学、D93 其余结构（0.03/150°/
回溯/歧义拒绝）零改动；bridge/web 无 scale 消费者。既有 45 条 M1 测试
零改动全过；全量 **1134 passed**、ruff 全过、mypy 42 文件无问题、
`mypy --strict`（5 个生产脚本）无问题。

### 4.5 Phase B / B2 — 端点切向退化（2026-11-22，合成几何）

结论：**VERIFIED —— 1 个实现缺陷（NaN 穿透）已修复，5 类设计边界实测
刻画**（D97；`b2_tangent_degeneracies.py`）：

| 退化类 | 输入 | 实测行为 | 分类 |
|---|---|---|---|
| D1 来回折返前缀 | 笔画以原路精确折返开头 | （修复前）2% 弧探针弦=0 → NaN 切向 → **NaN 转角静默绕过 150° 守卫** + `json.dumps` 裸 `NaN`（浏览器 `JSON.parse` 拒绝 → 桥响应不可解析）；（修复后）ok，4×90°，JSON 严格合法 | **实现缺陷，已修**（探针占比倍增至弦非零） |
| D2 极短笔画 | 四条 2 点笔画方形 | 装配正确产环（5 点）→ 生产链自身"≥8 点"规则拒绝 `degenerate_shape` | 设计行为（正确委托，D93） |
| D3 近 180° 尖端 | 针尖三角形扫 h | 精确翻转于 h=57.74（57.24 拒 / 58.24 ok，转角 149.8°） | 设计阈值（150° 上限）边界成立 |
| D4 笔内极端非均匀 | 99% 点在前 1% 弧 | ok，与均匀参考同判决同转角 | 稳健（探针按弧长） |
| D5 端点近重复 | 端点 0.5 px 抖动簇 | ok，转角不受影响 | 稳健 |
| D6 自闭合笔画 | 闭环 + 三条边 | `closed_loop_with_extras` 拒绝 | 设计行为（既有守卫） |

修复验证：3 条失败先行回归测试（切向有限/自闭合无警告/转角全有限+JSON
严格合法）；M1 套件 48 passed（+3，既有 45 条零改动）；全量 1137 passed、
ruff、mypy（42 文件 + --strict 5 脚本）全绿。

### 4.6 Phase B / B3 — 全局拼接与预算边界（2026-11-22，合成几何）

结论：**VERIFIED —— 全局边界全部按设计拒绝；`search_complexity`
预算拒绝路径（M1 交付以来零覆盖）功能正确，且实测在 ≤8 笔画下不可达**
（D98；`b3_assembly_ambiguity.py`）：

| 边界类 | 输入 | 实测行为 | 分类 |
|---|---|---|---|
| E1 容差边界 | 二分求合法间隙边界（两弧圆） | 4.498424 px == 0.03×scale 4.498424 px（机器精度）；−0.01 ok / +0.01 rejected | 设计阈值精确成立 |
| E2 多个独立环 | 两个两笔画圆 | `no_legal_assembly` 拒绝，绝不合并两环 | 设计行为 |
| E3 近重合重画边 | 底边画两遍（0.5 px）+ 三边 | `no_legal_assembly` 拒绝，不静默选边 | 设计行为（意图歧义→拒） |
| E4 预算可达性 | 对抗稠密端点图 ×4 | 最深搜索 **936 节点（预算 0.94%）**，全部 ≤2.9 ms；无一触发预算 | 结构性不可达（见下） |
| E4' 拒绝路径功能 | 临时调低预算（生产常量不动） | 节点预算 → rejected/search_complexity；匹配上限 → 已枚举候选保守丢弃 | 设计行为，已补回归测试 |

E4 四个对抗输入与精确搜索规模（二分法，进程内临时调低预算）：
两簇直线双向扇 **1 节点**（紧簇处任何环必 180° 掉头，150° 折角规则直接
杀死——第一步即无可行后继）；8 条弯折笔画两簇 **936 节点**（弯折软化
掉头后的最密结构 ≈4!² 环量级）；四角各 4 个重合端点的双向重画方形
**47 节点**（self_intersection 拒绝）；四车道歧义件 **7 节点**
（ambiguous，2 个合法解）。

附带语义发现（不改代码）：`MAX_MATCHINGS` 是**枚举中止守卫而非严格
界限**——四车道件在 cap=1 下枚举先于守卫自然结束，正确返回 ambiguous
（真判决）；守卫仅在节点进入时检查，最后节点进入之后追加的匹配不被观察
（节点数为精确值、匹配数为守卫可见下界：lanes 守卫见 1、实际 ≥2）。

新增 2 条回归测试直接覆盖 `search_complexity` 拒绝路径
（tests/test_multistroke_assembly.py，monkeypatch 临时调低预算，生产
常量零改动）；M1 套件 50 passed（+2，既有 48 条零改动）；全量
**1139 passed**、ruff、mypy（42 文件 + --strict 5 脚本）全绿。

### 4.7 Phase B 小结

B1（D96 修复：尺度密度缺陷）、B2（D97 修复：NaN 穿透 + 5 类设计边界
分类）、B3（D98：全局边界刻画 + 预算拒绝路径补测试）全部完成。Phase B
验收对照：量化边界敏感性 ✓（B1 ±12.8% 容差漂移 + 判决翻转；B2 针尖
精确翻转；B3 容差边界机器精度一致）；新增回归测试 ✓（B1 +3、B2 +3、
B3 +2）；既有 M1 测试零改动全过 ✓（45→50 只增不改）；单笔画生产路径
不受影响 ✓；生产 Fourier 检索数学零改动 ✓；结论区分 VERIFIED（B1 缺陷
已证并修、B2 缺陷已证并修、B3 全部 VERIFIED）/ 无 BLOCKED 项。


## 5. Phase E — 轻量 GitHub CI + 干净环境复现（2026-11-24）

**结论：VERIFIED —— 干净环境（git archive 纯跟踪树，无 data/ 无本地产物）
下 Python 与 Web 门禁全绿；远端 CI 先后暴露三个真实缺陷并逐一修复
（eol 呈现失配 `d13f40a`、假绿门禁 `b269fbc`、亚底容差测试 `b5c85a5`），
`aed8cda` 实跑后 CI 首次真正全绿（见 §5.3 表末行）。**

### 5.1 干净树实测（本地，git archive HEAD）

| 门禁 | 结果 |
|---|---|
| `pytest -q -o addopts=""` | **1027 passed + 112 skipped，0 failed / 0 errors**（96 s）；此前 24 处产物依赖测试缺守卫（23 ERROR + 1 FAIL，根因=`p0_perceptual_probe/gallery.jsonl` 等未入库冻结产物），按仓库既有"build artifact not in this checkout"惯例补 skip（`c7feb93`），产物齐全时行为零变化（本地仍 1139 passed） |
| web `npm ci` / `npm run test` / `npm run typecheck` / `npm run build` | 全绿：vitest **17 passed**（含全库契约测试按设计降级为 golden 短名单断言）、tsc 无错、构建产物 `index-D5IlZnSq.js` 142.79 kB **哈希与本地一致**（确定性第三次独立确认） |

### 5.2 真实数据 / 产物测试归类（CI 零数据下载）

- **自动 skip（既有设计）**：`data/index` 检索集成与 golden 再生测试（
  `data_manifest.json` 不存在即 skip）；Overture 数据检查 / 预览 / 索引
  复现测试（raw/processed 缺失即 skip）；ShapeComp 权重与 MPEG7 源缓存
  测试；cv2 / h5py 可选依赖测试；CP8 冻结表测试。
- **本轮补守卫（`c7feb93`）**：`tests/test_arm_diagnostics.py` 的 `frozen`
  fixture（23 条）与 `tests/test_visual_review_report.py` 的
  `draw_examples` 测试（1 条）——依赖未入库的感知图库冻结产物。
- **CI 不下载任何数据**（简报边界：无授权不大规模下载）；CI 绿 ⇔
  "干净 clone + 声明依赖即可复现门禁"。

### 5.3 CI 配置与远端实跑（含一次假绿事故的完整复盘）

配置 `8f04772`（`.github/workflows/ci.yml`），两 job 逐字镜像本地检查点
门禁：
- **python**（ubuntu-latest, Python 3.12）：`pip install -e
  ".[dev,data,cv,shapecomp]"` + 工具钉本地实测版（ruff 0.17.3 / mypy
  2.4.1 / pytest 9.2.0）；`pytest -q -o addopts=""` → `ruff check .` →
  `mypy` → `mypy --strict`（5 个生产脚本）。
- **web**（ubuntu-latest, Node 26）：`npm ci` → `npm run test` →
  `npm run typecheck` → `npm run build`。
- 仓库 public → GitHub-hosted runner 免费（无付费资源）；触发 =
  push / PR to main。

远端实测（API 实查；job 日志需 admin 权限 403，以步骤级 conclusion、
注记与取证为准）：

| run | commit | 真实判读 |
|---|---|---|
| 45379510833 | `8f04772` | web **success**；python pytest 步骤 **failure**——真实失败：LF checkout 下 4 个审计哈希测试（见 §5.4） |
| 45383867580 | `dadcfd8` | **假绿**：诊断步骤 `pytest \| tee` 未设 pipefail，步骤出口码取 tee 的 0，同 4 个失败仍在但步骤显示 success（ruff/mypy/strict 为普通命令，真实通过） |
| 45386706411 | `d13f40a` | 显示 success（4 个哈希测试此时已真过；但 pytest 步骤仍带吞错风险，其出口码不可信） |
| 45386888432 | `f592feb` | 显示 success + 取证注记：**git 2.56.1 / core.autocrlf=unset / d6 产物 sha256 == CRLF 审计记录值**——证明 runner 默认 LF checkout（首跑失败机制）、且 `.gitattributes` 已实际生效；pytest 出口码同样不可信 |
| 45387765398 | `b269fbc` | **failure**——pipefail 后**首个可信 pytest 门禁**，暴露此前不可见的真实失败：`tests/test_alignment.py::test_batch_mirror_decisions_match_the_pairwise_switch`（Linux 数值，根因与修复见 §5.4/§5.5） |
| 45390183895 | `aed8cda` | **success**——CI 首次真正全绿（python 四门禁 + web 四门禁） |

### 5.4 两个失败根因（定位过程如实记录）

**根因 1（run 1 的 4 个审计哈希失败，`d13f40a` 修复）**：
- **根因（确证）**：runner 的 git `core.autocrlf=unset`（取证注记）→
  无 `.gitattributes` 时 checkout = blob 字节 = **LF**；而 d5b/d6/cp8
  审计记录（`read_only_sources`/`original_file`/`inputs`/`design` 块）
  钉的 sha256/size 是作者机的 **CRLF 呈现**（索引 i/lf、Windows 工作区
  w/crlf）。本地把 benchmark 产物重写为 LF 精确复现 **4 failed**：
  `test_the_correction_records_the_exact_bytes_it_read`、
  `test_history_files_are_declared_untouched_and_the_agreement_side_is_
  not_rewritten`、`test_d5b_artefacts_agree_with_the_committed_d5_values`、
  `test_published_run_binds_the_design_it_promised`。
- **中途误判（已更正）**：`dadcfd8` 重跑全绿曾被解读为"首跑失败是一次性
  瞬态"——实际是上述 pipefail 假绿掩盖了同样的 4 个失败。取证注记
  （autocrlf=unset + 产物哈希 == CRLF 记录）与本地 LF 复现共同定案。
- **排除项（排查如实）**：fresh-venv + CI 同款安装命令干净树复现
  （1027 passed + 112 skipped）排除依赖版本漂移；路径/导入大小写扫描、
  matplotlib 后端、winreg 类平台专用 API 引用全部零命中。

**根因 2（run 5 的对齐测试失败，`b5c85a5` 修复）**：
`test_batch_mirror_decisions_match_the_pairwise_switch` 以 `abs=1e-9`
比较批处理与成对距离，但该库含完美匹配——距离即闭式解的 sqrt(eps)
地板 ~2e-8（`D² = E_a + E_b - 2|C|` 相消两个 O(1) 项；本套件
`test_closed_form_has_a_machine_precision_floor` 明文钉住该地板），两条
代码路径对 O(1) 项的舍入不同（Parseval vs 时域能量、批 vs 单对 FFT），
地板级距离可在地板量级内漂移。**进程内即证**：给批路径传入数学等价
的时域能量（仅求和次序不同，`library_energy` 注入）→ 残差钳到 0.0，
与成对路径的 2.107e-8 差 **21× 容差**；能量 ±1 ulp 扰动同幅（2.1e-8）。
Windows 默认路径逐位巧合一致才本地通过；Linux 的舍入不同即失败——
容差低于被测量自身的噪声地板，属测试缺陷而非生产缺陷（生产对齐数学
零改动；镜像判定余量实测 ~5e-2，不受影响）。

### 5.5 三个修复

- **eol 呈现钉死（`d13f40a`，修 CI 实际失败）**：`.gitattributes` 对
  `experiments/shape_perception_benchmark/**` 钉 `text=auto eol=crlf`——
  blob 零改动，仅把该子树 checkout 呈现钉到审计记录所钉的同一呈现
  （npz/png 经 text=auto 二进制探测不受影响）。独立验证：本地 LF 重写
  → 4 failed；`git -c core.autocrlf=false clone`（模拟 Linux 默认 git
  设置）→ 产物字节与 Windows 工作区逐位一致，4 个 eol 敏感测试文件
  **109 passed**；全量 1139 passed / ruff / mypy 复跑全绿；远端
  run 45386706411 实跑 success + f592feb 取证哈希匹配。冻结实验产物
  与统计口径零改动。
- **pipefail（`b269fbc`，修诊断步骤自身）**：`set -o pipefail` 使
  `pytest | tee` 透传 pytest 出口码（本地双路径验证：失败→摘要分支
  exit 1，成功→0）；摘要/注记逻辑不变。假绿期间无人据"run 2 全绿"
  做出发布决策（Phase E 尚未收口），事故影响 = 归因多绕一轮。
- **测试地板感知容差（`b5c85a5`，修 run 5 暴露的测试缺陷）**：该断言
  改为 floor-aware——距离 <1e-6 时 `abs=1e-7`（同一测试 9 行后的
  offset 断言既有先例），否则 `abs=1e-9`；O(1) 距离实测差 ~1e-15，
  判别力不降。生产对齐数学、`align_library` 实现零改动（受保护项
  未触碰）；本地全量门禁复跑 1139 passed / ruff / mypy 全绿。

### 5.6 诊断与取证通道（防复发）

- **失败摘要（`dadcfd8`）**：pytest 失败时 FAILED/ERROR 行写入
  `$GITHUB_STEP_SUMMARY`，前 10 个失败测试 ID 以 `::error` 注记发布——
  check-runs 注记 API 无需登录可读（本地已模拟验证该 bash 块）。
- **checkout 取证（`f592feb`）**：每次运行把 git 版本、`core.autocrlf`
  与一个钉死审计产物的 sha256 以 `::notice` 注记发布——本次定位根因
  即直接依赖该通道。
- **断言细节注记（`aed8cda`）**：失败摘要分支把 pytest 的前 10 条
  `E  assert ...` 行也发布为 `::error` 注记（本地假日志模拟验证）——
  断言实际比较值经 check-runs API 可读。本网络 github.com:443 被阻
  （run 页面不可达），注记通道是唯一可读通道，本轮补齐它正是为了
  下次失败不再靠推断定位。

### 5.7 范围与未做（如实）

- CI 对齐测试的失败为**平台舍入差异**触发（Linux runner vs Windows
  作者机）；修复针对测试容差的噪声地板，非平台差异本身。numpy 在 CI
  **未钉版**（pyproject 约束内取最新，本地为 2.5.4）——若未来 numpy
  小版本改变批/单对 FFT 舍入，同类地板级漂移仍可能出现（届时按根因 2
  同法处置）；刻意不钉 numpy 以保持 CI 检验"干净环境可复现"的本义。
- 基于 6 次运行（1 真失败 + 2 吞错期 + 1 取证 + 1 pipefail 暴露 + 1
  全绿）的样本，不做超出实测的稳定性声明；此后每个检查点推送自动
  触发回归。
- 远端**定期回归未建立**（只依赖 push/PR 触发；无 schedule
  cron——刻意保持轻量）。
- Actions 缓存（pip/npm）首跑冷启动，后续命中；无缓存失效问题已知。
- 未加 `timeout-minutes` / concurrency 取消（轻量权衡，后续可加）。

## 6. Phase F — 证据裁决与方向选择（2026-11-24）

**结论：选定 F1 地理扩容（计划冻结、执行待用户授权）；F2 本轮否决
（阴性结果如实报告）；F3 顺延；F4 出界不选。裁决 = D99。**

### 6.1 裁决依据（全部为既有实测证据的合成，无新实验）

| 方向 | 裁决 | 依据（逐条引用既有测量） |
|---|---|---|
| F1 地理扩容规划 | **选定** | P0-4：角状/星形查询全库无对应地物（十角星 Top-1 D≈0.21 弱命中）；库单 1°×1° 区域 839 候选、reservoir 仅 55 → 数据侧覆盖为已量化失败类（D_coverage 296/3658）；规格本计划"索引先 1k–5k"；本地储备 pending 1,034 已被 D32 否决 → 来源 = 新区域 |
| F2 精排改进配对实验 | **本轮否决（阴性结果）** | P0-5 归因 C_rerank/C_dedup = 0/3658、B_coarse = 1/3658、重排 +5.6pp——精排非总体最弱环节；row 294 家族失败 B4 定位为 align 枝状区分度饱和（真源 0.27–0.31 ≫ 0.12，粗筛 Top-10 内），B4 结论 4 要求独立冻结+消融+另行授权、不与 P0-6 抢人眼预算；受保护数学边界 |
| F3 M2 复杂区域接口研究 | 顺延 | 调试工具域，不跨 P0 门禁、不构成 P0-6 证据（D93/TASKS 既有边界）；优先级低于门禁唯一入口（P0-6 真人采集 0 份） |
| F4 路网等出界项 | 不选 | 简报明确出界 |

### 6.2 交付物

- **D99**（`docs/DECISIONS.md`）：四方向裁决记录，含 F2 阴性结果的
  完整证据链与"否决的是时机不是方向"的边界。
- **F1 扩容计划冻结**（`docs/DATA_PIPELINE.md` §11）：区域候选表、
  探针先行序、冻结协议（零新算法）、量化验收（覆盖探针以
  WEAK_ALIGN_DISTANCE=0.12 为阈值、多样性基线、P0-5 对照协议、字节
  级复现）、成本估算（12.8% 转化率标定）、授权边界与回退、明确不做
  （不修算法、不动 P0-5 冻结结论、不并入 pending、不宣称产品准确率）。

### 6.3 边界（如实）

- Phase F 完成条件 = 裁决记录 + 计划冻结，**不含执行**；执行需用户
  授权数据下载（量级与回退见 DATA_PIPELINE §11.4）。
- 区域候选表中的行数量级为标定估算，探针实测前不作承诺；
  bbox 为提案坐标，不是数据。
- F2 否决依据"本轮证据 + 既有授权边界"，若用户授权枝状区分度立项，
  须按 B4 结论 4 走独立设计冻结 + 消融。
