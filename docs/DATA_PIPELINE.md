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
   （属 P0-3，尚未实施。）

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

- N=128 对极端枝状岸线欠采样：最坏弧长步长偏差约 104%（`4b2c4aea-…-part1`，
  2,269.8 ha、全边界约 213 km）；中位数偏差约 0.5%。采样点数 / 自适应 N 消融留给 P0-3（D7）。
- 孔洞（岛屿）不进入轮廓，仅以 `n_holes` 记录；若后续检索需要"带岛轮廓"，需另设产物。
- 极区与跨 180° 经线区域未验证（本 bbox 不涉及），aeqd 原点取样本中心（D16）。
