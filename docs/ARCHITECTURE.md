# ARCHITECTURE — 系统模块、数据流与技术决策

> 部署与技术栈细节见 [`DESIGN_V1.md`](DESIGN_V1.md) §4。本文件固化模块边界与数据流。

## 1. 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│ Web 层 (web/)  React + TS + Vite                              │
│  SketchCanvas → preprocess → search.worker (Web Worker)       │
│  → SearchResults → GeoMap (MapLibre) → OverlayCompare/Share    │
└───────────────▲───────────────────────────────────────────────┘
                │ 静态数据文件（features / metadata / shapes）
┌───────────────┴───────────────────────────────────────────────┐
│ 索引产物 (data/index/)  由 Python 离线构建                       │
└───────────────▲───────────────────────────────────────────────┘
                │
┌───────────────┴───────────────────────────────────────────────┐
│ Python 算法与数据层 (earthglyph/, scripts/)                     │
│  data → geometry → features → retrieval                        │
│  下载/清洗 → 轮廓提取/标准化 → 特征计算 → 粗筛+精排+去重           │
└────────────────────────────────────────────────────────────────┘
```

## 2. 系统边界（V1）

**Python 离线构建索引 → 静态数据文件 → 浏览器端检索。**

- 无运行时后端、无用户登录、无云数据库、无运行时模型调用。
- 前端通过静态文件加载特征，在 Web Worker 内计算匹配。
- 候选轮廓详情按需加载，控制首屏体积。
- **扩展预留**：候选规模过大导致首次下载超预算时，再升级为后端检索 API（FastAPI + NumPy/FAISS）。索引产物格式与检索模块接口需保持稳定，使前端检索与未来后端检索可共用同一套特征 / 精排逻辑（TypeScript 与 Python 各自实现同一约定）。

## 3. Python 模块职责

| 模块 | 职责 | 关键接口（约定） |
|------|------|------------------|
| `earthglyph/data` | 读取 GeoParquet、过滤 subtype/几何类型、写 `data_manifest.json` | `load_water(bbox) -> GeoDataFrame` |
| `earthglyph/geometry` | 有效性修复、局部投影、外环提取、弧长重采样、平移+尺度归一 | `normalize_contour(pts) -> (N,2)`；`resample_by_arclen(pts, n)` |
| `earthglyph/features` | Fourier 描述子、Hu 矩、紧致度 / 长宽比等粗筛特征 | `fourier_descriptors(contour, k) -> (k,)` |
| `earthglyph/retrieval` | 粗筛（近邻）→ 精排（旋转对齐 + Chamfer）→ 去重 | `search(query, index, top_k) -> list[Result]` |

`scripts/` 提供可复现入口：`download_data.py`、`prepare_polygons.py`、`build_index.py`、`evaluate_retrieval.py`。

## 4. 索引产物（前后端契约）

```
data/index/
├── features-v1.*      # 低维粗筛特征矩阵（Float32 友好）
├── metadata-v1.json   # id, sourceId, subtype, name, lon/lat(representative point), bbox, area
├── shapes/<id>.json   # 128 点标准化轮廓 + 原始经纬度轮廓（按需加载）
└── data_manifest.json # 来源、版本、下载时间、覆盖范围、过滤规则、有效地物数、许可、构建脚本 commit
```

查询 / 结果数据结构见 `DESIGN_V1.md` §3.5（`score` 为内部排序代价，非展示用"相似度百分比"）。

## 5. Web 模块职责

| 组件 | 职责 |
|------|------|
| `SketchCanvas` | 手绘输入、自动闭合、清空 / 撤销、镜像 / 旋转开关 |
| `search/preprocess.ts` | 草图标准化（与 Python 归一化约定一致） |
| `search/retrieval.ts` + `search.worker.ts` | Web Worker 内两级检索，避免冻结页面 |
| `GeoMap` | MapLibre 展示候选坐标与真实矢量边界，保留署名 |
| `OverlayCompare` | 草图与真实轮廓叠加，交互旋转 / 透明度 |
| `ShareCard` | URL 编码查询 + 导出 SVG/PNG |

## 6. 关键技术决策（摘要）

完整决策与依据见 [`DECISIONS.md`](DECISIONS.md)。

- **D1 静态优先，无后端**：降低 Qoder Sites 额度与复杂度依赖。
- **D2 两级检索**：粗筛保速度，精排保效果；1–5 万候选先用 NumPy/Worker，不急上 FAISS。
- **D3 局部投影而非直接经纬度当 x/y**：东西尺度随纬度变化，必须投影到米制平面。
- **D4 MultiPolygon 拆分为独立候选并记录 parent_id**：避免离散水面混成怪异形状。
- **D5 baseline 先行**：Hu 矩 / Fourier / Chamfer 建立可解释基线，缺乏基线实验前不引入深度学习。
