# P0-1 数据探测笔记 — 威斯康星真实水体轮廓

> **性质：数据检查与可视化，不是检索结果。** 本目录不证明 EarthGlyph 已具备搜索能力。

## 数据

| 项 | 值 |
|----|----|
| 来源 | Overture Maps Base theme, type=water（主要源自 OSM，ODbL 适用许可） |
| 版本 | release **2026-10-21.0** |
| 区域 | bbox `-90,44,-89,45`（min_lon,min_lat,max_lon,max_lat；美国威斯康星州 1°×1°） |
| 文件 | `data/raw/wisconsin_water.parquet`（5,536,172 bytes） |
| sha256 | `1c5431f2aa73c99d8b1cca7e65918640587bcb21445890bf739c9a0a5ff1a417` |
| 下载时间 | 2026-11-04T15:44:28+00:00（UTC，见 sidecar `download_time_utc`） |
| 行数 | 6,543 |
| 获取方式 | `python scripts/download_data.py --bbox=-90,44,-89,45 --output data/raw/wisconsin_water.parquet --release 2026-10-21.0` |
| 溯源记录 | `data/raw/wisconsin_water.parquet.meta.json`（来源 / 版本 / bbox / 下载时间 / 校验和 / 复现命令） |

### 获取方式说明（与文档预期不一致处）

官方 `overturemaps` CLI 的 S3 SDK 直连在本机不稳定（代理干扰导致 GetObject 响应截断、
urllib 偶发 WinError 10048）。脚本改用**同一官方 STAC 索引**
（`https://stac.overturemaps.org/2026-10-21.0/collections.parquet`）解析出公共 HTTPS 数据地址，
用 DuckDB httpfs 对 1.2GB 源文件做 **parquet row-group range 读取 + bbox 谓词下推**，
仅传输相交数据（~5.5MB），过滤语义与官方 CLI 完全一致（feature bbox struct 与查询框相交）。
输出为合法 GeoParquet（WKB + GeoParquet 1.0 `geo` 元数据，EPSG:4326）。

## 实际 Schema（15 列，以真实数据为准）

`id(str)`, `names(struct)`, `subtype(str)`, `class(str)`, `sources(list<struct>)`,
`source_tags(list)`, `level(float)`, `wikidata(str)`, `is_intermittent(bool)`, `is_salt(bool)`,
`geometry(WKB Polygon/LineString/Point/MultiPolygon)`, `version(int32)`,
`bbox(struct xmin,ymin,xmax,ymax)`, `theme(str='base')`, `type(str='Feature')`

- 与设计文档预期一致：含 `id/geometry/subtype/class/names/bbox/sources`。
- `subtype`/`class` 为**字符串**（非列表）。
- 新发现字段：`is_intermittent`（间歇水体）、`is_salt`、`level`、`source_tags`、`wikidata` — P0-2 可用作过滤。

## 质量统计（详见 dataset_summary.json）

| 指标 | 值 |
|------|----|
| 几何类型 | Polygon 1,974 / LineString 4,505 / Point 54 / MultiPolygon 10 |
| 空/无效/缺失几何 | 0 / 0 / 0 |
| 重复 id | 0 |
| 可用闭合轮廓（Polygon/MultiPolygon 且有效非空） | **1,984（30.3%）** |
| subtype 分布 | stream 3407 / water 836 / canal 773 / **pond 585** / river 473 / **lake 310** / **reservoir 84** / human_made 50 / physical 17 / spring 8 |
| 面积（投影后 m²） | min 17 / p5 237 / median 4,071 / p95 213,132 / max 98,198,881 |

## 主要发现与问题

1. **候选规模足够**：lake+pond+reservoir 约 979 个多边形，加上通用 `water` 836 个，
   单 1°×1° 区域即有 ~1,800 个潜在水体轮廓候选，P0 检索实验可行。
2. **bbox 边界切片伪轮廓**：最大面积 98 km² 的"水体"实为被查询框切断的密歇根湖/格林湾边缘。
   **P0-2 必须排除 feature bbox 触及下载框四条边的记录**（预览脚本已按此规则选样）。
3. **非湖泊伪形状混在 Polygon 中**：
   - 矩形小块 = 游泳池 / `human_made`（class `swimming_pool` 50 个）；
   - 细长条 = `river`/`canal` 的河岸多边形（riverbank 类）；
   - **P0-2 过滤建议**：subtype ∈ {lake, pond, reservoir}（`water` 逐个检查后加入）；
     最小面积阈值（建议 ≥0.1 ha 起步实验）；紧致度/长宽比剔除细条；简化后顶点数下限。
4. **数据本身干净**：无空几何、无无效几何、无重复 id —— 本区域无需 make_valid
   （但不能假设其他区域同样干净，P0-2 仍需保留修复逻辑）。
5. **多样性真实存在**：预览图中可见紧凑圆形池塘、树枝状水库、牛轭湖、带岛大湖
   （孔洞渲染正确）—— 对形状检索是有意义的候选分布。

## 预览图

`geometry_preview.png`：32 个真实闭合轮廓，按面积十分位分层抽样（seed=42），
投影到局部 aeqd 米制平面（等比例，未把经纬度当平面坐标）。
每格标注 subtype、投影后面积（ha）、代表点 (lat,lon) 与源 id 前缀，可回查。

## 复现

```bash
python scripts/download_data.py --bbox=-90,44,-89,45 \
    --output data/raw/wisconsin_water.parquet --release 2026-10-21.0 --force
python scripts/inspect_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/dataset_summary.json
python scripts/preview_data.py data/raw/wisconsin_water.parquet \
    --output experiments/p0_data_probe/geometry_preview.png --count 32 --seed 42 --frame=-90,44,-89,45
```

## 复现性校验（2026-11-04 实测）

| 校验 | 结果 |
|------|------|
| `inspect_data.py` 重跑 | `dataset_summary.json` 统计**全部一致**，仅 `file.size_bytes` 随文件更新（5,530,648 → 5,536,172） |
| `preview_data.py --seed 42` 重跑 | 选样确定（同 seed → 同 32 个源 id） |
| 同 release 重新下载（多次独立运行） | 字节一致，sha256 相同 → 当前代码路径**可复现** |
| 重新下载 vs 首次下载文件 | **逻辑内容完全一致**：15 列逐列 `pyarrow.Table.equals` 通过、行序一致、id 集合一致、逐 id WKB 一致 |
| 字节差异原因 | 首次下载用 `fetch_arrow_table()`（零拷贝、多 chunk），后改为 `to_arrow_table()`（单 chunk）→ parquet 页 / 字典布局不同，压缩后大小不同。**非数据变化**（同一不可变 release）。 |
| 处置 | 用当前代码重新下载覆盖 `data/raw/wisconsin_water.parquet`，使"入库文件"与"入库代码"字节级一致，sidecar 随之自洽；首次下载副本保留为 `data/raw/wisconsin_water_225901137e.pre-refresh.parquet`（内容已证等价，确认后可删）。 |

> **结论（记入 DECISIONS D17）：** 复现性判定应以**内容摘要**（排序后 id→WKB）+ 行数 + release 为准，
> 文件 sha256 只在代码路径不变时有效。P0-2 的 `data_manifest.json` 需同时记录两者。

## 单元测试（离线，不触网）

`tests/test_data_download.py`（28）/ `tests/test_data_inspect.py`（20）/ `tests/test_data_preview.py`（10），
覆盖 bbox 校验、Parquet 完整性判定（缺失 / 0 字节 / 截断 / 0 行）、sidecar 字段、幂等复用、
`--force` 重下、损坏文件自动重下、失败不留 sidecar、CLI 参数透传；几何类型 / 有效性 / 空值 /
重复 id / subtype 分布 / 投影面积量级；预览选样的切片排除、分层确定性、孔洞绕向。

```bash
pytest            # 65 passed（含初始化阶段 7 个几何测试）
mypy earthglyph/  # strict: no issues found in 7 source files
```

**测试样本均为 `tmp_path` 下的合成小文件，只用于验证解析逻辑，不进入 `data/`，
不得当作真实地理数据或检索结果。**

## 本阶段结论与未证实项

- ✅ 真实数据可获取、可读、Schema 已核对、候选量足以支撑 P0-2。
- ✅ 数据多样性真实存在（预览图），非人工挑选。
- ❓ **尚未验证**：这些候选对**手绘草图**的检索命中率 —— 属 P0-2～P0-6，本目录不构成任何检索能力证据。
- ❓ 未验证：芬兰 / 北美之外区域的 subtype 分布是否与本地一致（扩大覆盖前需重跑 inspect）。
