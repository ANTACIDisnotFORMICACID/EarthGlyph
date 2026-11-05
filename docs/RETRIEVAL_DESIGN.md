# RETRIEVAL_DESIGN — 检索算法、形状特征与技术方案

> 数学表达与查询结构见 [`DESIGN_V1.md`](DESIGN_V1.md) §3。本文件固化算法流水线与实现约定。

## 1. 流水线

```
轮廓提取 → 几何清洗/有效性修复 → 形状标准化 → 特征计算
        → 特征粗筛（近邻）→ 几何精排 → 去重/多样性重排 → 地理结果
```

## 2. 形状标准化（必须处理的不变性）

- **平移不变**：中心平移到原点。
- **尺度不变**：按 RMS 半径缩放（`earthglyph.geometry.normalize_contour` 已实现该最小子集）。
- **旋转**：精排阶段做旋转对齐（默认识别旋转），或使用旋转不变特征（Fourier 幅值）。
- **镜像**：必须对坐标显式反射（复共轭），由用户开关控制；逆序采样只改变走向，**不等于**镜像。
- **起点与方向**：轮廓点序列起点用循环移位对齐；方向（顺 / 逆时针）需归一。
- **采样一致**：按弧长均匀采样固定点数（V1 取 128，P0-3 消融确认，见 D29）。
  **查询与候选必须用同一 N 标准化**：64/256 点相对 128 点参考的 `compactness`
  p99 偏差达 24.7% / 16.2%，比 N 的绝对取值更影响可比性。

几何预处理必须有单元测试（平移 / 缩放 / 旋转不变性、镜像开关、无效草图、重复水体过滤）。

## 3. 两级检索

### 粗筛（速度）
对草图计算低维特征（Fourier 描述子、紧致度等），与全部候选比较，保留 Top 100–300。
1–5 万候选先用 NumPy / 浏览器 Web Worker 顺序计算，**不急上 FAISS**。

### 精排（效果）
草图与候选 128 点轮廓做尺度 / 平移归一，针对起点循环移位求最优旋转对齐，计算平均边界距离：

`D(A,B) = min_{s,θ} sqrt( (1/N) Σ_k |a_k − e^{iθ} b_{(k+s mod N)}|² )`

- θ 由两序列复数相关计算，**不暴力遍历 360°**。
- 可加对称 Chamfer Distance、二值区域 IoU 复核少量 Top 候选。
- 该指标假设点列沿轮廓保持相近弧长结构，对严重形变不稳定。

### 去重 / 多样性重排
去掉同一源地物重复部分、极近重叠多边形、大量几乎相同候选（避免首页十个椭圆）。

## 4. Baseline 特征（先建基线）

| 方法 | 用途 | 备注 |
|------|------|------|
| Hu Moments（`cv2.matchShapes`） | 基准对照 | 旋转 / 尺度 / 镜像不变，未必最优 |
| Fourier Descriptors（低频幅值） | 粗筛 | 降低平移 / 缩放 / 起点 / 旋转影响，但**丢相位即丢结构**；走向规范化后镜像把 `Z_k` 变成 `conj(Z_k)`，幅值精确不变 → **镜像盲**，镜像策略只能由几何精排的 `allow_mirror` 控制（D26 修订 / D33） |
| Chamfer Distance | 精排复核 | 对称版本 |
| 轮廓旋转对齐 | 精排 | 复数相关求 θ + 循环移位求 s（闭式，不遍历角度） |

**缺乏基线实验前不引入深度学习模型。**

## 5. 绘图输入规范化

- 自由画笔 + 关闭后自动闭合；明显开放曲线不硬解释成可信湖泊轮廓。
- 检测自交、极短曲线、面积近零 → 提示重画，或提供清楚标注的自动修复选项。
- 支持撤销 / 清空 / 简单平滑 / 镜像开关；旋转匹配默认开启。
- 圆 / 椭圆等常见形状不强行宣称"惊人相似"；呈现不同候选与区域过滤。
- V1 不支持多笔画汉字 / 线条简笔画，界面明确提示"不支持的图形种类"。

## 6. 前后端一致性

前端 `web/src/search/preprocess.ts` 的草图标准化必须与 Python `earthglyph.geometry` 的归一化约定一致（同采样点数、同平移 / 缩放定义），保证离线评价与在线检索行为一致。

## 7. 未验证假设（须在 DECISIONS.md 跟踪）

- ~~128 点采样是否足够表达湖泊轮廓细节（vs 64/256）~~ → P0-3 已实测：保留 128，见 D29。
- Fourier 粗筛 + 对齐精排在真实手绘（含形变）下的命中率。
- 1–5 万候选在浏览器 Worker 内的检索延迟是否可接受。
- 单区域候选库的覆盖是否足以支撑检索"惊喜感"（Type B 风险，见 D32）。

## 8. P0-3 已实现：粗筛特征与索引契约（2026-11-05）

### 8.1 粗筛向量（`data/index/features.npy`，float32，shape `(M, 20)`）

| 列 | 定义 | 不变性 |
|----|------|--------|
| `fd_01..fd_16` | `z_i = x_i + i·y_i`（RMS 归一、CCW、闭合）的 DFT 幅值 `abs(Z_k)/N`，k=1..16，**按谐波序号排列** | 平移 / 尺度 / 旋转 / 循环起点 |
| `compactness` | `4πA/P²`（圆盘 = 1） | 平移 / 尺度 / 旋转 / 镜像 |
| `solidity` | `A / A_hull`（凸 = 1） | 同上 |
| `aspect_ratio` | 最小面积外接矩形长边 / 短边（自实现凸包 + 旋转卡尺，见 D28） | 同上 |
| `diameter_fill` | `4A / (πD²)`，D = 最远点对距离 | 同上 |

- **幅值丢相位**：结构不同的形状可共享低谐波幅值。实测 839 个候选中，与自己**幅值完全相同**
  的人工冒形（同幅值、随机相位）region IoU 中位仅 0.5338，98.81% 低于 0.9
  （`experiments/p0_feature_probe/phase_loss_examples.png`）→ 幅值命中**只能**当粗筛，
  区分必须靠相位或精排（D10）。
- **幅值对镜像盲**（P0-4 修正 P0-3 的 D26 表述）：走向固定为 CCW 后，
  反转还原成同一条走向、规范镜像把 `Z_k` 变为 `conj(Z_k)`，两者都不改变幅值。
  实测四管线对照见 D26 与本文件 §9.3；因此镜像允许与否**不能**靠幅值差异实现。
- 特征分组独立存储于 manifest 的 `index.feature_groups`（fourier offset 0 width 16 /
  geometry offset 16 width 4），**无融合权重**，便于逐项消融。
- 退化输入（零面积 / 零周长 / 零凸包 / 非有限值 / 谐波数超 Nyquist `N//2−1`）抛
  `FeatureError` 并计数，不静默丢弃。

### 8.2 精排输入与元数据

- `data/index/shapes.npy`：`(M, 128, 2)` float32，标准化后的完整外环轮廓，保留给精排
  （旋转对齐 θ 由复数相关求，循环移位 s 与 θ 联合最优，见 D8 —— **P0-4 已实现为闭式解**，
  一次正/逆 FFT 同时得到全部 N 个起点的 optimal θ，见 D34 与 §9.4）。
- `data/index/metadata.parquet`：`candidate_id` / `source_id` / `part_index` / `n_parts` /
  `subtype` / `class` / `lon` / `lat`（representative point，必在多边形内，D12）/
  `area_m2` / `n_points` / `feature_row`。
- **行序契约**：`features[i]` ↔ `shapes[i]` ↔ metadata 第 i 行描述同一候选，
  `feature_row` 让该契约可从文件自身验证；构建期一致性检查覆盖数量、id 唯一、行序、
  有限值、维度、轮廓可回推、经纬度合法与重建字节一致。

### 8.3 与前端的一致性

`web/src/search/preprocess.ts` 必须实现同一约定：同 N、同弧长采样、同 RMS 归一、同 CCW 走向、
同 `\|Z_k\|/N` 定义与同 4 个几何统计量公式；否则离线评价与在线检索不可比。

## 9. P0-4 已实现：手绘查询抽象与两级检索（2026-11-05）

模块划分（避免多余抽象层，每层只做一件事）：

```
earthglyph/geometry/sketch.py    轨迹/图像 → QueryContour（128 点、RMS 归一、CCW）
earthglyph/retrieval/query.py    QueryContour → QueryVector（粗筛列 + 精排轮廓）
earthglyph/retrieval/coarse.py   分组欧氏近邻 + 稳定 argsort → Shortlist(indices, distances)
earthglyph/retrieval/alignment.py 闭式 min_{s,θ} D（direct / mirror 两支）
earthglyph/retrieval/chamfer.py  对称 Chamfer 复核
earthglyph/retrieval/hu_baseline.py cv2.matchShapes 基线（统一像素尺度，见 D40）
earthglyph/retrieval/search.py   search_by_contour / search_by_image → SearchResult(hits, pre_dedup, …)
```

### 9.1 抽象提取的判据（全部相对于轨迹自身 RMS 尺度，D35）

| 判据 | 常量 | 实测行为 |
|------|------|----------|
| 重复点合并 | `1e-3` 尺度 | 笔迹停笔产生的重合点不进入弧长统计 |
| 自动闭合端距 | `AUTO_CLOSE_REL = 0.03` | 超过则写入 warning "that chord is an assumption, not something you drew" |
| 采样缝隙闭合 | `SEAM_STEP_TOLERANCE = 2.0` 步 且 `SEAM_PERIMETER_SHARE = 0.05` 周长 | 密集采样的环由其自身 wrap chord 闭合；实测各候选上限 0.15–0.18 尺度 |
| 开放拒绝 | `OPEN_REJECT_REL = 0.15` | 端距 0.15–0.28 的输入被**显式拒绝**并要求重画，不猜测成封闭图形 |
| 笔画带（图像） | `OPEN_STROKE_WIDTH_REL = 0.05` **且** `OPEN_STROKE_SOLIDITY = 0.10` | 3.8 px 宽的 270° 弧被判为"未闭合笔画"；库内最窄真实候选为 0.065 / solidity 0.160，故该合取不误杀 |
| 结构性自交 | 交叉改动凸包面积 `≥ 0.005` | 五角星被拒绝（默认），可 `allow_self_intersection` 显式改为"按闭合曲线匹配" |
| 退化下限 | 等周比 `< 1e-3` | 线状 / 原地点列拒绝 |

`closed=true` 的轨迹**末点被视为闭合弦而非几何顶点**（`outline = points[:-1]`）：因此只位移末点的
"闭合误差"在精排前不可见，量化闭合误差必须位移一段尾部（D36，单测
`test_the_closing_vertex_is_a_chord_not_geometry`）。

### 9.2 平滑（Task A3）

`SMOOTHING_SIGMA = {none: 0, light: 0.8, medium: 2.0}`（单位 = 采样步），实现为循环零相位高斯低通
（面积保持 ≤2e-3、不移动特征位置）。全库 839 条实测代价：`light` 保留 k=9..16 谱能量占比 0.831
（最差候选 0.760），`medium` 仅 0.337（最差 0.197），且 `medium` 最坏改动 51% 周长。
**G2 全部扰动族在三档下排序完全相同**，仅对真值距离变化 → 默认取 `light` 是抖动/细节的折中，
不得以"更好看"为由选档（数据见 `experiments/p0_retrieval_baseline/notes.md` §4）。

### 9.3 精排的镜像与走向（D33）

- Fourier **幅值**在走向规范化后对镜像精确不变 → 粗筛**镜像盲**，实测六个镜像查询的真值粗筛排名恒为 1。
  镜像策略只能实现在几何精排：`align(query, candidate, allow_mirror=False)`，允许时对候选规范镜像谱
  取 `conj(B̂)`，并按 direct / mirror 两支更小的 D 决定 `mirrored`（两支距离同时返回，可审计）。
- 实测：`allow_mirror=True` 时六个镜像查询的真值全部回到第 1，D 从 0.10–0.52 塌到 0.004–0.25；
  `False` 时真值排名 1/2/9/–/–/10（`–` = 掉出 Top-10）。
- **点序反转 ≠ 镜像**：反转被 `ensure_ccw` 还原为同一走向（D 与原形完全相等），
  反射始终产生新几何。二者在实现与文档中都不得混称。
- Chamfer 复核的实测不变性：对两曲线的刚性运动、循环起点、走向反转**不变**；
  对单向旋转或镜像**会变** → 不能拿它裁决镜像。

### 9.4 闭式对齐（D34）

`D²(s) = mean|a|² + mean|b|² − 2|C_s|`，`C_s = IFFT(Â·conj(B̂))_s / N²`，`θ_s = arg(C_s)`。
一次正 FFT + 一次逆 FFT 得到全部 N 个起点的 optimal θ 与 D，**不遍历 360°**。
返回值含 `shift`、`theta_rad`、`mirrored`、`alternatives`（对称形状的等价最优个数）、
`direct_distance`、`mirror_distance`。闭式两次相减带来**数值地板 √eps ≈ 2.1e-8**，
低于该值不可与"完全重合"区分，故所有距离断言按 1e-6 阈值写。
`apply_alignment(points, shift, theta_rad, mirrored)` 的约定是"把候选搬到查询上"。

### 9.5 排序、去重与结果契约

- 排序主键 = 声明的实际匹配误差（默认 `sort_by="align"`），次键 = `index_row`（稳定，可复现）。
- `max_per_source=1` 只保留每个真实源地物的最优候选；`SearchResult.pre_dedup` 完整保留去重前顺序，
  去重不得改变"第 1 名是谁"以外的审计事实。
- `method="align"` 是**全库诊断**（跳过短名单，重排 839 条），只用于失败归因 B / C / D，
  不作为产品路径；`shortlist_size` 在 P0-4 实测 100 = 200 = 300（逐族逐位相同）。
- 每个 `Hit` 必带 `candidate_id` / `source_id` / `subtype` / `class` / `lon` / `lat` / `area_m2` /
  `coarse_distance` / `align_distance` / `align_shift` / `align_theta_deg` / `align_mirrored` /
  `chamfer`；弱结果（D > 0.12）必须带"只是当前库中最接近的候选"措辞，**禁止**输出未校准的相似度百分比。
- 可视化中的对齐叠加**只用于展示**：图形按 `apply_alignment` 摆好再画，排序与数值不受绘图影响。

### 9.6 三个 baseline 的实测关系（不作优劣结论）

同一查询集上 Hu / Fourier / Fourier+align 的 Top-1 在 9 个类手绘查询上**互不相同**，
且三者距离量纲不可比（Hu `matchShapes` 0–1.1e-4、Fourier 0.003–0.023、align 0.052–0.213），
因此**不得**用"某种方法距离更低"论证视觉更相似；判读依据只能是结果叠加图与人工标注（P0-6）。
Chamfer 复核会改变 9 个查询中 3 个的 Top-1（星形 / 心形族），同样只记录现象不下结论。
