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

