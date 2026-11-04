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

**D7 弧长均匀采样 128 点** — 假设-待验证
依据：兼顾表达力与计算量；64/256 的影响待实验。
影响：P0 需做采样点数消融；若 128 不足则调整。
注记（2026-11-05，P0-2 实测）：839 个候选的弧长步长偏差中位数约 0.5%，但枝状水库
（如 `4b2c4aea-…-part1`，2,269.8 ha、全边界约 213 km）最坏偏差约 104% —— 128 点无法
表达极端枝状岸线。该证据支持 P0-3 的采样点数消融（含按复杂度自适应 N 的选项）。

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

## 待验证假设清单（P0 跟踪）

- D7 采样点数（128 vs 64/256）。
- Fourier 粗筛 + 对齐精排在真实手绘（含形变）下的命中率。
- 1–5 万候选在浏览器 Worker 内的检索延迟可接受性。
- Qoder Sites 对 React/Vite 静态项目的实际发布流程（P0 需验证，见 ROADMAP P0-7）。
