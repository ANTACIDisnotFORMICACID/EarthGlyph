# inpolygon 水平边界漏点修正记录（P0-4.6 域外修正，不触碰任何冻结产物）

- 日期：2026-11-11
- 基线 commit：`fd7db93bb88c74ad17c2282a3e67fb5ce915b068`（HEAD 与审查基准一致）
- 修复对象：`earthglyph/shape_benchmark/shapecomp_port.py::inpolygon`
- 性质：**确定性 bug 修复**（符合工程规则 2 的例外条款），有回归证据；不改变生产默认检索方法
  （inpolygon 仅服务于 ShapeComp KerNet2 光栅化，不属于检索管线默认路径）。

## 1. 缺陷确认（修复前，独立测试构造）

原实现对 `y0 == y1` 的边直接 `continue`，把**边界判定**与**交叉计数**一起跳过。
后果：恰好落在水平边内部（非端点）的网格像素只有在其它非水平边的半开区间
`[lo, hi)` 覆盖该 y 时才会经交叉规则被算作"内部"；当水平边位于多边形 max-y
（顶部平台）时没有任何边覆盖该 y，这些像素被完全漏掉。

实测（40×40 网格，修复前）：

| 构造输入 | 期望在掩码内 | 实际 | 缺失 |
|---|---|---|---|
| 单位正方形 (0,0),(1,0),(1,1),(0,1)，CCW | 1600 | 1562 | 上边界内部 38 点 |
| 同上，CW | 1600 | 1562 | 上边界内部 38 点 |
| 同上 + 重复顶点 | 1600 | 1562 | 同 38 点 |
| 网格对齐矩形（y ∈ [0, 10/39]） | 440 | 402 | 顶边 38 点 |
| 顶部平台轮廓（y=1.0 处 x∈[10/39,29/39]） | 平台段 20 | 0 | 平台 20 点全漏 |
| 自交 bowtie（两种绕向） | 4 角在内 | 通过 | 无 |

机制：下边界内部点"碰巧正确"（被垂直边的 `[lo,hi)` 交叉规则误判为内部，
`inside|on_boundary` 合并后结果不变）；**顶部（max-y）水平边系统性漏点**。
绕向与重复顶点不改变结论。

## 2. 修复内容

- 每条边（含水平边）都执行精确共线 + 包围盒边界测试（无容差，与原契约一致）；
- 水平边仍不参与交叉计数（射线在其自身 y 上即边本身，奇偶由非水平边决定）；
- 不引入任何容差；对自交、重复顶点、多种绕向行为不变。

## 3. 验证（tests/test_shapecomp_port.py 新增 7 个测试）

1. `test_inpolygon_unit_square_is_fully_inside_on_the_grid`（ccw/cw/dup）——1600/1600；
2. `test_inpolygon_top_plateau_horizontal_edge_is_boundary`——平台段 20/20；
3. `test_inpolygon_grid_aligned_rectangle_counts_both_horizontal_edges`——440/440；
4. `test_inpolygon_agrees_with_independent_reference_on_lattice_polygons`——格点顶点
   随机环（含封口变体）与**独立逐点 PNPOLY 参照实现**逐像素一致（3 个种子 × 30 环）；
5. `test_inpolygon_self_intersecting_with_horizontal_edges_matches_reference`——
   bowtie 三绕向 + 带水平边的自交五边形，参照一致 + 水平边 36 点边界断言；
6. `test_inpolygon_matches_geos_covers_on_lattice_convex_hulls`——25 个格点凸包，
   距边界 >1e-9 的 39,530 个像素与 GEOS covers 全等；水平边格点 112 个全部入掩码；
7. 原有 `test_inpolygon_matches_geos_covers_on_official_contours` 继续通过
   （官方轮廓逐像素等于 GEOS covers）。

## 4. 已知局限（如实记录，未修复，不属于本缺陷范围）

格点凸包测试中 33 个像素与 GEOS covers 不一致，经精确有理数核算**全部位于边界
浮点舍入壳层内**（12 个精确落在非水平边内部、21 个距边界 <1e-9），远离边界的
像素零分歧。这类"精确共线于非水平边"的像素由双精度舍入决定，引入容差修复会
违反"不随意容差"约束且偏离 MATLAB 自身的双精度行为；需精确谓词才能消除，
 MATLAB inpolygon 在该类上的行为无法在本机验证（无 MATLAB 运行时）。
**因此不宣称与 MATLAB 在对抗性输入上逐位一致。**

## 5. 对历史实验的影响（rerun_regression.py，results/regression_results.json）

对提交实验中实际流经 KerNet2 的全部 967 个轮廓（Figure8 60、H2 keras 15、
H2 fig7D 15、MPEG7 查询 33、合成探针 5、图库候选 839）重算修复前后
40×40 掩码与 KerNet2 22 维坐标：

| 轮廓族 | 数量 | 掩码变化 | KerNet2 输出变化 |
|---|---|---|---|
| figure8 | 60 | 0 | 无 |
| gallery_candidates (data/index) | 839 | 0 | 无 |
| h2_fig7d_file | 15 | 0 | 无 |
| h2_keras_file | 15 | 0 | 无 |
| queries_MPEG7 | 33 | 0 | 无 |
| queries_synthetic_probe | 5 | 0 | 无 |

**结论：修复对全部已冻结实验产物（CP7/CP8）零影响，历史结果无需修正、保持原样。**
权重 sha256 记录见 regression_results.json。随机多边形属性检验（种子 42，120 个
含自交的 3–39 顶点环）：修复前后 removed=0 恒成立，added=0（随机浮点环不会
命中精确水平边类）。

## 6. 复现命令

```bash
.venv/Scripts/python.exe experiments/p0_shapecomp_inpolygon_correction/rerun_regression.py
.venv/Scripts/python.exe -m pytest tests/test_shapecomp_port.py -q
```
