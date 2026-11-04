# DATA_PIPELINE — 数据来源、格式、清洗与索引构建

> 下载命令与 Schema 细节见 [`DESIGN_V1.md`](DESIGN_V1.md) §2、§3.1。本文件固化数据治理规则。

## 1. 数据来源（优先级）

1. **Overture Maps Base / Water**（首选）：Schema https://docs.overturemaps.org/schema/reference/base/water/
   字段：`id`, `geometry`, `subtype`, `class`, `names`, `bbox`, `sources`。
2. **Geofabrik OSM PBF**（替代）：Overture 提取不理想时用 Osmium/PyOsmium。
3. **Natural Earth**：仅作少量全球形状示例，精度不足以作湖泊数据主体。
4. NAIP / Sentinel：属 V3 遥感，**V1 不引入**。

## 2. 许可与法律边界

- Overture Base Water 主要源自 OSM，受 **ODbL** 等适用许可约束：展示来源署名，保留数据版本 / 来源 / 许可；公开分发衍生数据库需核查 ODbL 共享要求。
- 地图底图选允许该访问模式的瓦片供应商，开发期合理限流。
- **禁止**批量下载 / 自动分析 Google Maps / Google Satellite 瓦片（政策禁止非可视化用途）。
- 分享卡片只画自有草图 + 开放矢量轮廓 + 文字，避免卫星图版权风险。

## 3. 过滤规则（V1）

- 仅收 **Polygon / MultiPolygon**；排除 Point / LineString / 海洋切片 / 瓦片边界片段。
- `subtype` 限定 `lake`, `pond`, `reservoir`；`water` 仅经检查后加入；排除 `ocean`, `physical`。
  （P0-2 落实：允许表之外的多边形**不删除**，落入 `pending_analysis.parquet` 待分析，见 D19。）
- 区分"湖中岛屿内环"与外边界，不混为一体。
  （P0-2 落实：标准化轮廓只存外环，孔洞数入 metadata，见 D21。）
- 过滤空几何、退化边界、极小 / 极大面积，但**保留筛选日志**。
  （P0-2 落实：feature 级 / part 级两级记账 + `rejected_samples.parquet` 留存真实形状，见 D18；
  最小面积 0.1 ha 为 CLI 实验参数，见 D20；**不设**长宽比 / 矩形外观等形状删除规则。）
- **不按下载框边接触过滤**（D15 已修订）：bbox 相交查询返回完整 feature，
  框边接触只记录信息性标志 `extends_beyond_frame`。

## 4. 清洗与标准化流程

1. 读取列、过滤 subtype 与几何类型。
2. 修复无效几何（`shapely.make_valid`，不盲目 `buffer(0)`）；保留重要外环。
   （P0-2 落实：仅在 `is_valid=False` 时调用，并检查修复后类型；修复为
   MultiLineString/GeometryCollection 而无多边形 part 时记 `unrepairable_after_make_valid`。）
3. MultiPolygon 拆为独立候选并记录 `parent_id`（见 DECISIONS D4）。
   （P0-2 落实：`candidate_id = source_id` 或 `source_id-partK`，见 D21。）
4. **局部投影**：用局部等距投影把经纬度转米制平面，勿直接把经纬度当欧氏 x/y；极区 / 跨 180° 经线单独处理。
   （P0-2 落实：`AeqdProjection`，参数写入 manifest，见 D16。）
5. 记录面积、周长、bbox、representative point（须在多边形内部，不能默认质心）、名称、源 ID。
6. 外环简化 / 平滑克制；按弧长均匀采样 **128 点**（可研究 64/256 影响）；中心平移原点，按 RMS 半径缩放得归一化轮廓。
   （P0-2 落实：`arc_length_resample` 按累积弧长插值，非顶点序号等间隔；不做旋转 / 循环起点对齐，见 D23。）
7. 预计算粗筛特征（低频 Fourier 12–24 维、紧致度、长宽比、凹凸性），保留 128 点标准化轮廓供精排。
   （P0-3 落实：16 谐波幅值 + 4 个几何统计量 = 20 维，见 §10 与 RETRIEVAL_DESIGN §8。）

## 5. 数据记录（必须）

每次构建生成 `data/index/data_manifest.json`，记录：
- 数据来源、版本、下载时间；
- 地理覆盖范围（bbox / 区域名）；
- 许可证；
- 清洗方法与过滤规则；
- 原始数量 → 过滤后有效地物数量；
- 按类别 / 地域 / 复杂度的分布统计；
- 处理脚本的 Git commit。

## 6. 可复现构建

- 原始数据与生成索引默认**不入 Git**（见 `.gitignore`）。
- 构建通过脚本复现：`scripts/download_data.py` → `scripts/prepare_polygons.py` → `scripts/build_index.py`。
- 先在小 bbox 验证 Schema / 数据大小 / 候选数，再扩大；**不一次下载全球**。

## 7. 反伪结果约束

每个前端候选通过 `id` 回指原始数据，可核验经纬度与真实轮廓。禁止用合成湖泊或静态结果代替真实索引。

## 8. P0-1 实测记录（真实数据，2026-11-04）

### 8.1 已获取数据

| 项 | 值 |
|----|----|
| 来源 | Overture Maps **Base theme, type=water**（主体源自 OSM，ODbL 适用许可） |
| 版本 | release `2026-10-21.0` |
| 区域 | bbox `-90,44,-89,45`（min_lon,min_lat,max_lon,max_lat；美国威斯康星 1°×1°） |
| 文件 | `data/raw/wisconsin_water.parquet`（不入 Git） |
| 大小 / 行数 | 5,536,172 bytes / **6,543 行** |
| sha256 | `1c5431f2aa73c99d8b1cca7e65918640587bcb21445890bf739c9a0a5ff1a417` |
| 溯源 | `data/raw/wisconsin_water.parquet.meta.json`（来源、版本、bbox、下载时间、校验和、复现命令） |
| 质量报告 | `experiments/p0_data_probe/dataset_summary.json` |
| 轮廓预览 | `experiments/p0_data_probe/geometry_preview.png`（32 个真实轮廓，**数据检查非检索结果**） |

### 8.2 实际 Schema（与设计文档预期差异，以数据为准）

15 列：`id(str)`, `names(struct)`, `subtype(str)`, `class(str)`, `sources(list<struct>)`,
`source_tags(list)`, `level(float)`, `wikidata(str)`, `is_intermittent(bool)`, `is_salt(bool)`,
`geometry(WKB)`, `version(int32)`, `bbox(struct xmin,ymin,xmax,ymax)`, `theme(str)`, `type(str)`。

- 设计文档 §2.1 关注的 `id/geometry/subtype/class/names/bbox/sources` **全部存在**。
- 差异 1：`subtype` / `class` 是**字符串**，不是列表 → `_category_key` 仍兼容两种形态。
- 差异 2：新增可用字段 `is_intermittent`（间歇水体，形状常退化）、`is_salt`、`level`、
  `source_tags`、`wikidata` → P0-2 过滤可直接利用（见 8.4）。
- 结论已记入 [`DECISIONS.md`](DECISIONS.md) D14。

### 8.3 获取方式（本环境实测）

```bash
python scripts/download_data.py --bbox=-90,44,-89,45 \
    --output data/raw/wisconsin_water.parquet --release 2026-10-21.0
```

- 默认 `--fetch-method duckdb`：读官方 STAC 索引
  `https://stac.overturemaps.org/<release>/collections.parquet` 取得公共 HTTPS 分片地址，
  用 DuckDB httpfs 对源文件做 **parquet row-group range 读取 + bbox 谓词下推**，
  只传输相交数据（~5.5MB，而源分片约 1.2GB×32）。
  bbox 过滤语义与官方 CLI 一致：`bbox.xmin < xmax AND bbox.xmax > xmin AND ...`。
  输出写回 GeoParquet 1.0 `geo` 元数据（WKB + EPSG:4326），可被 geopandas / pyogrio 正常识别。
- `--fetch-method cli`：官方 `python -m overturemaps download ...`（本机 pyarrow S3 SDK 直连出现
  响应截断，故不作默认；网络恢复或换环境时可用，见 D13）。
- 幂等：已存在且校验通过的文件**不重新下载**（记 `note: reused existing file`）；
  文件损坏或 0 行时自动重下；`--force` 显式覆盖。
- 完整性：每次写入后 `verify_parquet` 读取行数，sidecar 记录 sha256 与 size。

### 8.4 数据质量实测（威斯康星 1°×1°）

| 指标 | 值 |
|------|----|
| 几何类型 | Polygon 1,974 / LineString 4,505 / Point 54 / MultiPolygon 10 |
| 空几何 / 无效几何 / 缺失几何 | 0 / 0 / 0 |
| 重复 `id` | 0 |
| 可用闭合轮廓（Polygon/MultiPolygon 且有效非空） | **1,984（30.3%）** |
| subtype | stream 3407 / water 836 / canal 773 / **pond 585** / river 473 / **lake 310** / **reservoir 84** / human_made 50 / physical 17 / spring 8 |
| 面积（局部 aeqd 投影 m²） | min 17 / p5 237 / median 4,071 / p95 213,132 / max 98,198,881 |
| 面积分桶 | <0.01ha 49 / 0.01–1ha 1,181 / 1–100ha 729 / 100ha–10km² 19 / >10km² 6 |

**P0-1 初判的伪轮廓来源（P0-2 已逐条核实，结论有修正）：**

1. **下载框切片**：P0-1 曾判断面积最大（98 km²）的"水体"是被 bbox 切断的密歇根湖 / 格林湾边缘。
   **P0-2 几何证据推翻该判断**（见 D15 修订）：1,984 个可用多边形中 0 个的外环顶点或边落在
   下载框边界上；17 个 bbox 超框者均为完整几何。故**不**按框边接触过滤，仅记录
   `extends_beyond_frame` 标志。
2. **游泳池 / 人工矩形**：`human_made`、class `swimming_pool`（50 个）呈完美矩形。
   P0-2 处理：subtype 不在允许表 → 进入 `pending_analysis.parquet`（未删除）；
   **不**用"矩形外观"作为错误判据。
3. **河岸细长条**：`river` / `canal` 的 riverbank 多边形呈极窄长条。
   P0-2 处理：subtype 不在允许表 → 待分析；允许表内的细长湖（如长宽比 18.3:1 的
   `7ce4100d-…`）**保留**，不设长宽比删除规则。
4. 最小面积阈值起步实验 ≥0.1 ha：P0-2 实测拒绝 121 个 <0.1 ha 多边形，参数化为
   `--min-area-ha`（D20），被拒形状留存于 `rejected_samples.parquet` 供复核。

**规模结论**：单 1°×1° 区域 lake+pond+reservoir 约 979 个多边形，加上需逐个检查的 `water`
836 个，候选量足以支撑 P0-2 检索可行性验证 —— 无需下载全球。
（P0-2 实测：其中 950 个 feature 进入 part 阶段，产出 **839 个标准轮廓候选**。）

### 8.5 复现校验（已实测）

| 校验 | 结果 |
|------|------|
| `scripts/inspect_data.py` 重跑 → `dataset_summary.json` | 统计项**全部一致**，仅 `file.size_bytes` 随入库文件更新 |
| `scripts/preview_data.py --seed 42` 重跑 | 选样与出图确定（同 seed 同结果） |
| 同 release 重新下载（多次独立运行） | 字节一致 → 当前代码路径可复现 |
| 重新下载 vs 首次下载 | **15 列逐列相等、行序一致、逐 id WKB 相等**（逻辑内容相同）；物理字节不同，原因是取数代码的 chunk 策略（`fetch_arrow_table()` 零拷贝多 chunk → `to_arrow_table()` 单 chunk）改变 parquet 页 / 字典布局，**非数据变化**。已用当前代码重下并覆盖入库文件，使文件与代码一致；旧副本保留为 `data/raw/wisconsin_water_225901137e.pre-refresh.parquet`。 |
| 离线单元测试 | `pytest` 65 passed（合成样本仅存在于 `tmp_path`，不进入 `data/`） |

> **复现性含义：** 同一 release 的**内容**可复现（id 集合 + 几何 + 属性逐列相等）；
> 物理字节的稳定前提是取数代码路径不变。跨 pyarrow / DuckDB 版本或 chunk 策略变化时，
> 校验应比对**内容摘要**（排序后的 id→WKB 摘要）而非仅文件 sha256。P0-2 的
> `data_manifest.json` 需同时记录文件 sha256 与内容摘要。

## 9. P0-2 实测记录（几何清洗与标准化轮廓，2026-11-05）

### 9.1 构建命令与产物

```bash
python scripts/prepare_polygons.py \
    --input data/raw/wisconsin_water.parquet --out-dir data/processed --force
python scripts/visualize_geometry.py \
    --contours data/processed/contours.parquet \
    --out-dir experiments/p0_geometry_probe
```

| 产物 | 内容 |
|------|------|
| `data/processed/contours.parquet` | 839 个标准轮廓候选：外环 128 点（flat 256 float）、candidate_id/source_id/part_index/n_parts、subtype/class、代表点经纬度、米制面积/周长、n_holes、rms_radius_m、original_winding、extends_beyond_frame、was_repaired/repair_reason、bbox_lonlat |
| `data/processed/data_manifest.json` | 来源+release+下载时间+fetch_method、输入 sha256 与内容摘要、aeqd 投影参数、清洗参数、N=128、raw_rows→candidates 全链路计数、按原因拒绝计数、候选统计、环境版本、构建命令 |
| `data/processed/rejected_samples.parquet` | 被过滤真实形状样本（每原因 ≤200 条）：reason/source_id/subtype/area_m2/detail/wkb |
| `data/processed/pending_analysis.parquet` | 允许表之外的 1,034 个多边形（water 836 / river 143 / human_made 50 / stream 4 / canal 1），米制面积 + part 数 + WKB，未删除 |
| `experiments/p0_geometry_probe/*` | 四类检查图 + summary.json + notes.md（真实数据，标注为预处理结果） |

### 9.2 过滤账目（真实数据，release 2026-10-21.0）

| 阶段 | 计数 | 说明 |
|------|------|------|
| 原始行 | 6,543 | LineString 4,505 / Polygon 1,974 / Point 54 / MultiPolygon 10 |
| feature 级拒绝 | 5,593 | wrong_type 4,559；subtype_not_in_allowlist 1,034 |
| 进入 part 阶段的 feature | 950 | 恒等式：6,543 = 5,593 + 950 |
| 提取的 part | 960 | 10 个 MultiPolygon 贡献额外 part |
| part 级拒绝 | 121 | area_below_minimum 121；duplicate_high_overlap 0 |
| **标准轮廓候选** | **839** | pond 472 / lake 312 / reservoir 55；来自 831 个 feature |
| 其中 MultiPolygon part | 17 | 保留 parent source_id + part_index |
| 其中带孔洞 | 45 | 最多 234 孔（`d7182ef0-…-part2`） |
| 需要 make_valid 修复 | 0 | 本 release 无无效几何；修复路径由合成单测覆盖 |
| extends_beyond_frame | 8 | 信息性标志，未过滤（D15 修订） |

面积分布（ha）：min 0.1002 / p50 1.964 / p95 37.9 / p99 151.2 / max 9,819.87。

### 9.3 复现与一致性校验（已实测）

- 同命令重建 `contours.parquet` **字节一致**（sha256 相同）；manifest 中
  `built_at_utc` / 输出路径 / reproduce 块为唯一易变字段。
- 内容摘要（排序 id→WKB）对 gzip 重编码不变，文件 sha256 会变 → 审计以内容摘要为准（D17）。
- 原始 `data/raw/wisconsin_water.parquet` 未被修改（sha256 与 mtime_ns 前后一致）。
- 839 个候选全部可回查：代表点落在源 part 内、米制面积与外环周长可由原始几何复算、
  存储轮廓与"原始外环→投影→build_contour"重算结果逐点相等。
- 合成样本仅存在于 `tests/` 的 `tmp_path` 夹具；`contours.parquet` 经单测确认不含任何合成记录。

### 9.4 已知限制

- N=128 对极端枝状岸线欠采样：最坏**弦长离散度**约 104%（`4b2c4aea-…-part1`，
  2,269.8 ha、全边界约 213 km）；中位数约 0.5%。该数字不是重建误差（P0-3 复核见 D30）；
  采样点数 / 自适应 N 消融已在 P0-3 完成，N=128 保留（D29，见 §10.4）。
- 孔洞（岛屿）不进入轮廓，仅以 `n_holes` 记录；若后续检索需要"带岛轮廓"，需另设产物。
- 极区与跨 180° 经线区域未验证（本 bbox 不涉及），aeqd 原点取样本中心（D16）。

## 10. P0-3 实测记录（特征提取与索引构建，2026-11-05）

### 10.1 构建命令与产物

```bash
python scripts/build_index.py --contours data/processed/contours.parquet \
    --out-dir data/index --n-harmonics 16 --fold positive --dtype float32 --force
```

输入 = P0-2 的 `data/processed/contours.parquet`（839 候选，sha256
`65cafc2d03f2a82fe4153273cef3ea26e0d215acf3d2acf2a4899e913e2d2740`），
**不再读原始 GeoParquet**；来源、release、内容摘要沿 `data/processed/data_manifest.json` 传递。

| 产物 | 内容 | size / sha256 |
|------|------|---------------|
| `data/index/features.npy` | `(839, 20)` float32 小端 C-order 粗筛向量（16 谐波幅值 + 4 几何统计量） | 67,248 B / `b7255ede076922e9bf26877a1535840a4efc239e31a0fe55f79f1fbf5562e063` |
| `data/index/shapes.npy` | `(839, 128, 2)` float32 标准化外环轮廓（供 P0-4 精排） | 859,264 B / `fbaf4933e1e1585bf21878f4cdfe0a4355ffed6565494123ed06e142520a2776` |
| `data/index/metadata.parquet` | 839 行地理 / 溯源元数据（含 `feature_row`） | 124,467 B / `e18c3f95039f3f8d8283f0fe87eb05f31574902731163d08e0e117548859a211` |
| `data/index/data_manifest.json` | schema 版本、来源+release+输入 sha256+内容摘要、投影与清洗参数、特征定义与维度、依赖版本、git commit、完整构建命令、输出校验和 | — |

索引默认**不入 Git**（AGENTS.md 禁止入库）；版本化 = manifest + sha256 + 上面的复现命令。

**代码版本对齐（已实测）**：P0-3 检查点 `77b083c931b72cf4fd153a9185254f5a9486aeab` 推送后，
在工作区干净的状态下重建索引 —— 三个产物 sha256 与上表**逐字节一致**，
`data_manifest.json` 的 `git_commit` 随之等于该检查点 sha，
即"入库代码"与"入库数据"版本自洽（D17）。提交后重建不改变任何字节，
说明特征计算不含隐藏状态、随机性或时间依赖。

### 10.2 计数与一致性

| 项 | 值 |
|----|----|
| 输入候选 / 入索引 / 跳过 | 839 / **839** / 0 |
| subtype | pond 472 / lake 312 / reservoir 55 |
| feature_dim | 20（fourier 16 + geometry 4，组偏移记录在 `index.feature_groups`） |
| 唯一 `candidate_id` | 839（重复即构建失败） |
| 有限值 / 经纬度合法 / `n_points` 一致 | 全部通过（异常注入由 31 个单测覆盖） |
| float32 往返最大绝对误差 | features 6.70e-7，shapes 5.96e-8 |
| 重建确定性 | 同命令两次构建，三个产物**字节一致** |

### 10.3 可回查性（真实数据，非合成）

- 每个 `candidate_id` → `source_id` + `part_index` → Overture release `2026-10-21.0`
  中的真实 feature；`lon` / `lat` 是源 part 的 `representative_point`，落在水体内部（D12）。
- 单测读回 `data/index/` 后验证：轮廓可由 `contours.parquet` 的几何重新算出、
  元数据面积/周长可复算、索引中不含任何合成记录（合成样本只存在于 `tests/` 的 `tmp_path`）。

### 10.4 采样点数消融（Task C，几何保真度与特征稳定性，**非检索指标**）

```bash
python scripts/analyze_sampling.py --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
python scripts/analyze_sampling.py --validate-exact --raw data/raw/wisconsin_water.parquet \
    --contours data/processed/contours.parquet --out-dir experiments/p0_feature_probe
```

全部 839 个候选**从原始外环重新弧长采样**到 64 / 128 / 256（禁止用已存 128 点插值加密）。
结论与数字见 D29 / D30；产物 `sampling_metrics.csv`（2,517 行）、`sampling_summary.json`、
`sampling_metric_validation.{csv,json}` 与三张对比图在 `experiments/p0_feature_probe/`。

### 10.5 候选库与待分析类别（Task E / F，只分析不扩容）

- 候选库：4.99 个数量级面积跨度（0.10–9,819.87 ha）、3 个 subtype / 6 个 class
  （含 lagoon 22、basin 3、oxbow 2）、45 个带孔洞、17 个 MultiPolygon part、
  23 对近重复（42 个候选 / 20 簇）→ 见 `features_summary.json`、`candidate_distribution.png`。
- `pending_analysis.parquet`（1,034 feature / 1,035 part）**未修改、未删除、未入索引**；
  `add_now: false`（D32），扩容决策留给 P0-4 / P0-6 → 见 `pending_summary.json`、
  `pending_distribution.png`。

### 10.6 已知限制

- 索引只有**粗筛幅值 + 完整轮廓**：旋转对齐 θ、循环移位 s、镜像开关、Chamfer/IoU 精排
  均未实现，属 P0-4；因此本轮**没有任何检索命中率数字**。
- 单区域（1°×1°）候选库覆盖窄，形状稀此处可能在他处常见（Type B 风险，字段已备好未判定）。
- 8 个候选在任何测试 N 下都达不到 region IoU ≥ 0.95（7 个 reservoir）；
  N=128 下 5 个候选的重建多边形自交（已上报不静默修复）。跨区域的无效几何覆盖度仍未测。
