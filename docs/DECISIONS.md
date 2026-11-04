# DECISIONS — 重要设计决策及依据

记录格式：**D# 决策** — 状态（已定 / 假设-待验证）— 依据 — 影响。
新决策追加，不删除历史；状态变化时更新并注记日期。

## 架构

**D1 静态优先，无运行时后端** — 已定
依据：降低 Qoder Sites 额度与复杂度依赖；V1 候选规模（1–5 万）可在浏览器 Worker 内检索。
影响：索引产物为静态文件；前后端共用特征 / 精排约定。规模超预算时再升级 FastAPI+FAISS（见 ARCHITECTURE §2）。

**D2 两级检索（粗筛 + 精排）** — 已定
依据：粗筛保速度，精排保效果；避免全量精排的 O(N) 高成本。
影响：需维护低维粗筛特征 + 128 点精排轮廓两套产物。

**D5 Baseline 先行，不急于深度学习** — 已定
依据：缺乏可解释基线时引入模型无法判断收益，且违背"真实可行 > 扩展"原则。
影响：P0 必须先建 Hu/Fourier/Chamfer 基线并有实验对照。

## 数据与几何

**D3 局部投影，不把经纬度当欧氏 x/y** — 已定
依据：东西方向尺度随纬度变化，直接当平面坐标会 distort 形状与距离。
影响：每个候选用局部等距投影转米制；极区 / 跨 180° 经线单独处理。

**D4 MultiPolygon 拆为独立候选并记录 parent_id** — 已定
依据：离散水面合成单一轮廓会产生怪异形状，污染检索。
影响：去重阶段需按 parent_id 折叠同源重复部分。

**D6 数据首选 Overture Base/Water，subtype 限 lake/pond/reservoir** — 已定
依据：Schema 明确、许可清晰（ODbL）、字段含来源 ID 可回查。
影响：过滤规则与许可署名见 DATA_PIPELINE.md。

**D7 弧长均匀采样 128 点** — 已定（2026-11-05 由 P0-3 实测确认，结论见 D29）
依据：兼顾表达力与计算量；64/256 的影响待实验。
影响：P0 需做采样点数消融；若 128 不足则调整。
注记（2026-11-05，P0-2 实测）：839 个候选的弧长步长偏差中位数约 0.5%，但枝状水库
（如 `4b2c4aea-…-part1`，2,269.8 ha、全边界约 213 km）最坏偏差约 104% —— 128 点无法
表达极端枝状岸线。该证据支持 P0-3 的采样点数消融（含按复杂度自适应 N 的选项）。
注记（2026-11-05，P0-3 复核）：该 "104%" 是弦长离散度 `chord_dispersion`，不是重建误差，
P0-3 实测 N=128 下该候选的真实归一化 Hausdorff 为 0.1483、region IoU 为 0.8104（见 D29/D30）。

## 算法

**D8 精排旋转对齐用复数相关求 θ，不暴力遍历 360°** — 已定
依据：闭式解更快更稳。
影响：实现须处理循环移位 s 与 θ 的联合最优。

**D9 镜像必须显式反射（复共轭），由用户开关控制** — 已定
依据：逆序采样只改走向，不等于镜像；混淆会导致错误匹配解释。
影响：精排与前端开关须一致。

**D10 Fourier 幅值仅用于粗筛** — 已定
依据：幅值降低平移 / 缩放 / 起点 / 旋转影响，但丢结构、不区分镜像。
影响：镜像区分与结构匹配留给精排。

## 产品

**D11 score 为内部排序代价，非展示用"相似度百分比"** — 已定
依据：避免人为制造相似度数字误导用户。
影响：前端不显示"98% 相似"类文案。

**D12 representative point 须在多边形内部** — 已定
依据：质心可能落在水体之外（凹形 / 环状）。
影响：metadata 的地图点用 shapely representative_point，非 centroid。

## 数据获取（P0-1 实测后新增）

**D13 数据获取默认走 STAC 索引 + DuckDB httpfs range 读取，官方 CLI 作为可选路径** — 已定（2026-11-04）
依据：本机 pyarrow 的 S3 SDK 直连 Overture 数据桶出现响应截断（GetObject 长度与 content-length
不符，只落盘 5KB 残片），urllib 偶发 `WinError 10048`；而 DuckDB httpfs 对同一批公共 HTTPS 对象
做 parquet row-group range 读取稳定，且只传输与 bbox 相交的数据（~5.5MB vs 源文件 1.2GB×32）。
文件地址取自官方 STAC 索引 `stac.overturemaps.org/<release>/collections.parquet`，
bbox 过滤语义与官方 CLI 完全一致（feature bbox struct 与查询框相交）。
影响：`scripts/download_data.py --fetch-method {duckdb,cli}`，sidecar 记录 `fetch_method` 与
`cli_equivalent`；输出需手写 GeoParquet 1.0 `geo` 元数据（WKB + EPSG:4326）保持格式合法。
换环境或网络恢复时可用 `--fetch-method cli` 复现同一结果，两条路径都保留。

**D14 Schema 以实际下载数据为准：subtype/class 为字符串，并新增可用字段** — 已定（2026-11-04）
依据：release 2026-10-21.0 实测 15 列，`subtype`/`class` 是字符串而非设计文档假设的列表；
数据含 `is_intermittent` / `is_salt` / `level` / `source_tags` / `wikidata`。
影响：`_category_key` 同时兼容标量与列表（防未来 Schema 回退）；P0-2 过滤可用
`is_intermittent`（间歇水体形状易退化）与 `is_salt`；设计文档 §2.1 的字段清单按 DATA_PIPELINE §8.2 修正。

**D15 排除 feature bbox 触及下载框边缘的记录** — 已修订（2026-11-05；原 2026-11-04 规则作废）
原依据：威斯康星样本中面积最大（98 km²）的"水体"是被查询框切断的密歇根湖 / 格林湾边缘，
属于切片伪轮廓而非真实闭合形状。
修订依据（P0-2 几何证据，task #14）：对 release 2026-10-21.0 该 bbox 的 1,984 个可用
多边形逐一检查，**0 个**的外环顶点或边落在下载框边界上；17 个 feature 的 bbox 超出下载框，
但逐一核对均为**完整几何**（其中最大者是密西西比河河道水池，而非被切断的密歇根湖边缘）。
即 Overture 的 bbox 相交查询返回完整 feature，"bbox 触及框边 = 被裁剪"不成立。
修订后规则：P0-2 管道**不**按框边接触过滤；改为记录信息性标志
`extends_beyond_frame`（839 个候选中 8 个为 true），供后续扩大 bbox 时复核。
影响：`prepare.py` 不再丢弃框边地物；`preview_data.py` 的 `_touching_frame` 仅保留为
P0-1 预览选样的保守手段（实测不会剔除任何真实地物），不作为索引构建规则。

**D16 米制量算统一用局部方位等距投影 aeqd（以样本中心为原点）** — 已定（2026-11-04）
依据：落实 D3。实测 `+proj=eqa` 不是合法 PROJ 投影名；`aeqd` 必须显式给 `+ellps=WGS84`，
否则 pyproj 报 "Must specify ellipsoid or sphere"。geopandas 在地理 CRS 下直接取 `.area`
得到的是平方度且会告警，故面积 / 分层抽样一律先投影。
影响：`inspect.py` / `preview_data.py` 使用
`+proj=aeqd +lat_0=<中心纬> +lon_0=<中心经> +ellps=WGS84 +units=m +no_defs`；
P0-2 特征提取沿用同一约定并写入 manifest（跨纬度、极区、180° 经线仍需专门处理）。

**D17 复现性以"内容摘要"为准，文件 sha256 仅作辅助** — 已定（2026-11-04）
依据：同一不可变 release 两次下载得到逻辑内容完全相同（15 列逐列相等、行序一致）但物理字节
不同的文件 —— 差异来自取数代码的 chunk 策略（`fetch_arrow_table()` 零拷贝多 chunk →
`to_arrow_table()` 单 chunk）改变 parquet 页 / 字典布局，而非数据变化。若只以文件 sha256
判定复现，会在依赖升级后误判为"数据变了"。
影响：`data_manifest.json`（P0-2）同时记录文件 sha256 与内容摘要（排序后的 id→WKB 摘要）；
审计时优先比对内容摘要 + 行数 + release；已用当前代码重下并覆盖入库文件，使"入库文件"与
"入库代码"字节一致，旧副本保留待清理。

## 几何清洗与标准化轮廓（P0-2，2026-11-05 新增）

**D18 过滤采用 feature 级 / part 级两级记账，并留存被过滤样本** — 已定
依据：单一计数无法区分"整条记录不可用"与"拆分后某个 part 不可用"，也无法回答
"过滤是否过度"；任务要求输出每项过滤条件影响的候选数量并保存部分被过滤形状。
影响：manifest 记录 `rejected_features_by_reason` 与 `rejected_parts_by_reason`，
恒等式 `raw_rows = rejected_features + features_reaching_part_stage`、
`parts_extracted = candidates + rejected_parts` 由单测强制；
`rejected_samples.parquet` 按原因各存至多 200 条真实形状供目视检查。

**D19 允许表之外的多边形不删除，落入 pending_analysis.parquet** — 已定
依据：river/canal/human_made/未标注 water 等 1,034 个多边形只是"当前不检索"，
并非错误数据；删除会丢失后续分析机会，违背"可保留为待分析数据"的要求。
影响：`pending_analysis.parquet` 保存其米制面积、part 数与 WKB；候选库只含
lake/pond/reservoir（`--subtypes all` 可关闭允许表）。

**D20 最小面积阈值是实验参数，默认 0.1 ha，不是硬规则** — 已定
依据：任务明确"最小面积暂以 0.1 ha 为实验参数，不得硬编码为不可更改的规则"；
且面积下限的合理值依赖检索目标（小池塘 vs 大湖）。
影响：`scripts/prepare_polygons.py --min-area-ha`；manifest 记录实际取值；
被该规则拒绝的 121 个真实形状已留存，图 C 的面积直方图用于判断是否过度过滤。

**D21 标准化轮廓只存外环；孔洞与部件信息入 metadata；candidate_id 可回查** — 已定
依据：孔洞（岛屿）对"手绘外轮廓"检索是噪声，但孔洞数量是有用的元数据；
MultiPolygon 必须拆 part（D4）且保留父级链接。
影响：`contour` 为外环 128 点；`n_holes`、`part_index`、`n_parts`、`source_id`、
代表点经纬度入库；`candidate_id = source_id`（单 part）或 `source_id-partK`（多 part），
单测验证代表点落在源 part 内、面积与轮廓可由原始几何复算。

**D22 高重叠去重：IoU > 0.95 视为重复，保留面积最大者** — 已定
依据：Overture 同源水面存在几乎完全重叠的重复要素；保留较大者避免同一水面占两个候选。
影响：`resolve_duplicates` 用 STRtree + 并查集聚类；本 release 真实数据中 2 对高 IoU
（0.9982 / 0.9822）均为**跨 subtype**（pond/water、human_made/water），允许表内 0 触发，
故 839 候选无去重损失；合成单测覆盖同表内去重路径。

**D23 预处理输出对旋转协变而非不变；旋转/循环对齐与镜像开关留在检索阶段** — 已定
依据：D8/D9 的对齐属于匹配代价的一部分；若在预处理"归一化掉"旋转或起点，
会破坏镜像区分并提前丢失判别信息。
影响：`normalize_contour` 只做平移 + RMS 缩放 + CCW 走向统一（走向≠镜像）；
单测断言旋转后轮廓随之旋转、镜像轮廓不与原轮廓重合、循环起点不被对齐。

## 特征与索引（P0-3，2026-11-05 新增）

**D24 粗筛特征 = 低谐波 Fourier 幅值描述子（DC 丢弃、逐样本除以 N、按谐波序号排列）** — 已定
依据：z=x+iy 的 DFT 中 `Z_0` 是质心（携带位置而非形状），必须丢弃；不除以 N 时幅值随采样点数
线性增长，64/128/256 的描述子不可比 —— Task C 的采样消融正依赖这一点。
影响：`earthglyph/features/fourier.py` 提供 `per_sample=True`（默认）；查询端必须用同一定义；
描述子按谐波索引 k 定址排列，**不按数值排序**（排序会破坏 k 的语义）。

**D25 谐波数取 16（在 12 / 16 / 24 三者中）** — 已定
依据（`experiments/p0_feature_probe/features_summary.json`，839 个真实候选）：
非 DC 谱能量捕获中位数 h12=0.9007 / h16=0.9008 / h24=0.9010（p10 均为 ~0.702）；
两两成对距离相关 pearson ≥ 0.99997、Top-10 近邻重合 mean 0.98（h12 vs h16）/ 0.9875（h24 vs h16）；
h16 相比 h12 仅把最近邻中位距离从 0.02146 抬到 0.02180，即分离度增益 < 2%。
影响：默认 `DEFAULT_N_HARMONICS = 16`，索引 feature_dim = 16 + 4 = 20；
**该结论只是几何与特征空间层面的取舍，不构成检索精度提升的证据**（尚无真值对与手绘查询）。

**D26 折叠模式选 `positive`（只用 k≥1），不选 `symmetric`** — 已定
依据：走向反转使 `Z_k → Z_-k`，显式镜像使 `Z_k → conj(Z_-k)`，两者都表现为 k↔−k 幅值互换；
预处理已把走向固定为 CCW（D23），因此保留 k↔−k 的差异是有意义的判别信息，
而 `symmetric` 折叠会把它抹掉（同时对走向与镜像不变）。
影响：索引 descriptor 对镜像**不等价**，但**不得**对外表述为"能检测镜像"——
它只是"在 CCW 前提下把镜像对推到不同坐标"；镜像开关仍属检索阶段（D9），
`fold="symmetric"` 作为可选实现保留。

**D27 辅助几何特征只保留 4 个无量纲统计量，且与 Fourier 组只做拼接不做加权融合** — 已定
依据：任务要求"各特征组保持独立以便消融"，且未验证的融合权重会掩盖哪一组在起作用。
影响：`compactness=4πA/P²`、`solidity=A/A_hull`、`aspect_ratio`=最小外接矩形长短边比、
`diameter_fill=4A/(πD²)`，全部在**标准化采样轮廓**（外环、闭合到自身）上计算，
定义写入 manifest 的 `features.geometry.formulas`；查询端必须同基准计算才可比。

**D28 最小外接矩形（长宽比）自实现凸包 + 旋转卡尺，不用 GEOS `minimum_rotated_rectangle`** — 已定
依据（实测）：GEOS 在本 release 的一个 40 顶点有效凸包上把矩形退化为面积 6.279e-18 的细条，
导致 aspect_ratio 出现非物理值；shapely 2.2.0 / GEOS 3.14.1。
影响：`earthglyph/features/geometry_stats.py` 自带 Andrew monotone-chain 凸包与
旋转卡尺最小面积矩形；零面积 / 零周长 / 零凸包 / 零矩形宽度一律抛 `FeatureError`
并**计数**，不得静默丢弃候选。

**D29 采样点数默认仍为 N=128；不引入自适应 N** — 已定（解决 D7）
依据（`experiments/p0_feature_probe/sampling_summary.json`，839 个候选全部从**原始外环**
重新弧长采样，未使用"128 点插值到 256"的捷径）：
- 归一化 Hausdorff p50/p90/max = 0.0313/0.0708/0.2568（N=64）、0.0157/0.0344/0.1483（N=128）、
  0.0078/0.0178/0.0789（N=256）；region IoU 均值 0.9863/0.9949/0.9982，最小值 0.6772/0.8104/0.8959。
- 特征稳定性：与 N=128 参考相比，12 谐波幅值相对 L2 偏差 p50 为 4.85e-4（N=64）/ 1.18e-4（N=256），
  但 `compactness` 的 p99 相对偏差达 24.7%（N=64）与 16.2%（N=256）——
  **查询与候选必须用同一 N 标准化**，这比 N 取多大更关键。
- 成本：轮廓字节数随 N 线性翻倍（5 万候选：N=128 约 48.8 MiB，N=256 约 97.7 MiB），
  而粗筛扫描耗时几乎不变（839 候选全量 L2 扫描 1.7e-5 s）。
- 128→256 只把"达标候选数"从 152 抬到 31（在 iou≥0.95 且 hausdorff_norm≤0.05 目标下），
  另有 8 个候选在**任何**测试 N 下都不达标（其中 7 个为 reservoir）。
影响：V1 保持 `n_points=128`；自适应 N 未采纳（会破坏固定宽度批量读取与跨 N 可比性），
若 P0-4 精排证明极端枝状岸线是主要失败源，再作为独立特性引入。

**D30 曲线偏差用加密点云近似，误差上界 = 一个云步长且偏保守；已对精确参考做全校验** — 已定
依据：GEOS `hausdorff_distance` 是**顶点离散**距离（连续距离的下界），且对 8,085 顶点的
水库外环直接算连续距离不可控；本实现把两条曲线各按 `step/8` 加密后 kNN 求对称距离，
残差 ≤ 一个云步长且偏**高**（粗参考点云的最近点可能落在弦上而非垂足）。
实测校验（`--validate-exact`，2,517 对）：中位偏差 vs GEOS = 0.0122/0.0068/0.0038 m
（N=64/128/256），最大绝对偏差 6.21/2.97/0.73 m，
最大偏差 / 一个云步长 ≤ 0.12/0.46/0.43，**超出步长上界的对数 = 0**；
与 8× 更细点云比较同样 0 违例。
影响：报告中的 Hausdorff 是保守上界而非乐观估计；P0-2 流传的"最坏 104% 弧长步长偏差"
已重新归类为 `chord_dispersion`（弦长对均值的最大偏离比，N=128 实测 1.0448），
**不是**重建误差；另有一条硬约束：参与比较的点列必须先闭合，
否则真实候选的 Hausdorff 会从 520 m 变成 610 m（闭弦 1,258 m）。

**D31 索引产物 = 3 个文件 + manifest，float32 小端 C-order，行序契约由 metadata 自证** — 已定
依据：浏览器端需要能零拷贝解码的定长块，且"features[i] ↔ shapes[i] ↔ metadata 第 i 行"
必须由数据自身可验证，而不是靠约定信任构建脚本。
影响：`data/index/{features.npy (M×20 f4), shapes.npy (M×128×2 f4), metadata.parquet}`；
metadata 含 `feature_row` / `candidate_id` / `source_id` / `part_index` / `subtype` /
`lon` / `lat`（representative point，D12）/ `area_m2` / `n_points`；
一致性检查（数量、id 唯一、行序、有限值、维度、轮廓可回推、经纬度合法、重建字节一致）
由 `earthglyph/data/index.py` 与 31 个单测强制；
float32 舍入实测最大绝对误差 features 6.7e-7 / shapes 6.0e-8（对检索无影响，但已记录）。

**D32 pending 类别不并入正式索引；失败类型 A/B 只准备字段不下结论** — 已定
依据（`experiments/p0_feature_probe/pending_summary.json`，只读分析，`pending_analysis.parquet` 未删改）：
1,034 个 feature / 1,035 个 part，其中 310 个低于 0.1 ha、725 个可过面积下限；
`water`（836）与已发布候选高度冗余（到最近候选的描述子 L2 中位 0.01629，
只有 6.82% 超出候选库自身 p90 间距 0.05749），而 `river`（143）确有 42.4% 超出候选库间距，
但其闭合轮廓是线状地物被人为封合的结果，不是手绘形状查询想表达的对象。
影响：`add_now: false`，扩容决策留给 P0-4/P0-6（"只有真实查询因缺类而失败才加"）；
候选库只有 1°×1° 单州范围（Type B 风险来源）已在 `features_summary.json`
的 `failure_type_fields` 中备好记账字段（per-query 字段清单 + 现在可用的 library 字段），
本轮**不**据此判定任何失败类型。


## 待验证假设清单（P0 跟踪）

- ~~D7 采样点数（128 vs 64/256）~~ → **已由 P0-3 实测解决，见 D29（N=128 保留）**。
  残留子问题：极端枝状 reservoir 是否需要在精排阶段单独处理（P0-4 判定）。
- Fourier 粗筛 + 对齐精排在真实手绘（含形变）下的命中率（P0-4 / P0-5 / P0-6）。
- 1–5 万候选在浏览器 Worker 内的检索延迟可接受性（P0-3 只测了本机 NumPy 扫描）。
- 候选库覆盖是否足以支撑"惊喜感"（Type B 风险：单 1°×1° 区域、3 个 subtype；见 D32，P0-4 判定）。
- Qoder Sites 对 React/Vite 静态项目的实际发布流程（P0 需验证，见 ROADMAP P0-7）。
