# P0-4.5 面向人眼感知的形状检索对比实验 — 实验台账

本目录是 **受控形状检索** 的实验产物，不是地理数据。
图上与 `dataset_manifest.json → label` 一致：受控图标注
"Controlled Shape Retrieval — 非真实地理景观"，真实地貌候选标注
"Real Landform Retrieval — Overture Maps"。

本轮按任务边界停在 **Checkpoint A（数据与实验设计）**；
B（E1/E2/E3 实现）、C（全方法运行 + 对比图）、D（指标 + 人工盲评 + 结论）见 §7 状态表。

---

## 1. 一句话结论（Checkpoint A）

一个 **179 成员 / 18 查询 / 7 结构类** 的受控感知图库已经建立并冻结，
许可、原始字节哈希、拒绝普查与角色修订全部可追溯；
两次离线重建产物 **逐字节一致**；`control_gallery.png` 已生成并肉眼复核。
**尚无任何检索方法在该图库上跑过** → 本轮不给出任何检索命中率或"更符合肉眼"的主张。

---

## 2. 数据源与许可（Task A + Task B 的资产口径）

| 来源 | 项目 / 版本 | 许可 | 再分发 | 进入图库的量 |
|------|-------------|------|--------|--------------|
| Phosphor Icons | `phosphor-icons/core` 2.1.1，`raw/{weight}/{name}-{weight}.svg` | MIT | 允许（署名） | 25 个 SVG 文件 / 10 632 B → 25 成员 |
| Quick, Draw! | Google CreativeLab 2017，`full/simplified/{cat}.ndjson` | CC BY 4.0 | 允许（署名） | 48 个字节区间切片 / 24 576 000 B → 80 成员 |
| 本项目参数化生成器 | `earthglyph/data/perceptual_gallery.py` | MIT（项目源码） | — | 74 成员（参数逐条记录在 `provenance.parameters`） |

- 12 个 Quick Draw 类别（`circle cloud diamond fork leaf lightning moon palm tree snake star tree triangle`），
  每类 4 个 512 KB 切片 = 48 切片；切片首尾字节、`sha256`、`key_id` 与记录时间戳见
  `dataset_manifest.json → assets.quickdraw_slices / quickdraw_census`。
- **ShapeComp 官方数据未使用**：Morgenstern et al. 2021 的代码只挂在 `sites.google.com`，
  本机超时不可达；两个 Zenodo 记录（CC BY 4.0）只含论文复现图数据，不含 109 维描述子引擎、
  22 维投影、MatNet/KerNet 权重，也不含商业剪影。逐条证据与 10 问回答见
  [`shapecomp_research.md`](shapecomp_research.md)。
  → E1 只能是 **明确标注 ShapeComp-inspired** 的自实现基线（Checkpoint B），
  且不得称其为原始 ShapeComp。
- 未使用任何商业剪影素材；未训练任何网络；未新增第三方依赖
  （只用仓库已有的 numpy / shapely / matplotlib / requests）。

## 3. 为什么不用真实地貌充当受控图库

受控图库的每个成员都有 **结构角色**（query / variant / hard_negative），角色由设计意图决定；
真实 Overture 地物没有"某条湖岸线是星形的困难负例"这种标注，把它们塞进受控角色表
等于用不可控的自然分布冒充设计。因此：

- 受控图库（本目录）**只服务于 E0–E3 的判别性对比**；
- 真实地貌的 T3 设置 = 把图库形状 **混进 839 条真实候选的同一检索空间**，
  每个候选带 provenance 标签，**永不合并进正式地理索引**（`data/index` 未被写入，
  仍是 P0-3 的 `77b083c9`）；
- T3 的图库成员在结果里必须显示 `Controlled Shape Retrieval`，不得冒充地物。

## 4. 标准：与 P0-4 完全同一条链路

`dataset_manifest.json → standardization`：`n_points=128`、`smoothing="none"`、
路径 `earthglyph.geometry.sketch.contour_from_points(closed=True)` ——
图库成员与用户查询走 **同一个提取器**，避免"查询一条管线、候选另一条管线"造成的假距离。

外部轮廓进入前的三条硬规则（每条违规都变成一次 **计数拒绝**，不是静默丢弃）：

1. **不闭合的笔迹不封闭**：Quick Draw 首尾不重合 → `open_stroke` 拒绝。
   任务明令"禁止把未闭合图形随意封闭后伪装成原始有效样本"。
2. **多段图形不粘合**：Phosphor 图标若解出 >1 条外环 → `multiple_pieces` 拒绝
   （粘起来就是凭空造出设计师没画的形状）。
3. **负空间图标不进图库**：`solidity > 0.995` 且 x 跨度 > viewBox 70% 的图标其剪影是 **底板**
   （圆角矩形/圆盘）而非母题 → `frame_silhouette` 拒绝，理由是这类成员会顶着图标名字
   成为与凸集基线重复的"假样本"。

孔洞（holes）在 exterior-only 策略（D21）下不进入轮廓，但 **逐文件记录在
`assets.phosphor_files[].holes`**，因此该策略对第三方可审计。

## 5. 结构角色审计（本轮最重要的一处修正）

审计指标：**`concave_deficiency(contour) = 1 - area / convex_hull.area`（即 1 - solidity）**，
每条轮廓测一次，两个阶段：

- 阶段 1（查询）：对以凹结构定义自身的类（star / crescent / heart / forked / band），
  deficiency < `MIN_FAMILY_CONCAVE = 0.01` 的查询 **降级为 hard_negative**；
- 阶段 2（变体）：变体必须 ≥ 同族 **存活查询中最弱者** deficiency 的
  `MIN_VARIANT_CONCAVE_SHARE = 0.25`。

修订结果（3 条，全部记录在 `role_audit.revisions` 与成员自身的 `provenance.role_revised`）：

```
qd-moon-query-01   query   -> hard_negative  (deficiency 0.0010 < floor 0.0100)
qd-moon-01         variant -> hard_negative  (deficiency 0.0010 < floor 0.0524)
qd-moon-05         variant -> hard_negative  (deficiency 0.0040 < floor 0.0524)
```

顺带修掉一处会造成 **假查询** 的构建缺陷：`queries/<id>.json` 之前只增不减，
`qd-moon-query-01` 被降级后它那份旧的 query JSON 仍留在目录里（19 个文件 vs 18 个查询角色），
Checkpoint C 一旦按目录 glob 取查询，就会拿一个已被冻结标签判为困难负例的形状当查询用。
现在 `write_queries` 之后执行 `prune_stale_queries`：只删 **带本构建器 `tool` 标记**
且不在本轮查询集合里的 JSON，其它同名文件（例如 P0-4 的手绘查询、无法解析的文件）一律不动，
删除逐条打印。实测：本轮打印 1 条；离线重建得到 18 个查询文件且逐个与上一次构建字节一致。

Why：一个人画的 "moon" 常常画成近圆盘（0.9990 的凸包填充率）。
把它列为新月查询，等于让"圆盘"当新月的相关真值 —— 圆盘恰恰是新月查询最典型的困难负例。

被否决的三种备选指标（都实测过，都会在别处产生错误修订）：
"最尖凹点转角"（对平滑噪声敏感，圆盘上的抖动也能造出尖点）、
"内向转角总量"（把顶点数当结构，顶点多的噪声形状胜出）、
"以最强查询为门槛"（会把 `ph-club-fill` 0.089 / `qd-tree-05` 0.104 这类合法的钝形成员踢掉，
而真正的圆盘反而留在查询角色里）。

配套修正：`syn-forked-sharp` 曾被降级，根因是生成器参数写错
（`power_lobes` 的谷地半径就是 `base`，只调 `amplitude` 只会让瓣变鼓而不是变尖；
原 `base=1.0` 版本是个 deficiency 0.028 的钝三叶形）。
改为 `count=3, amplitude=1.2, sharpness=0.7, base=0.2` 后 deficiency 实测 0.1554，
保留 variant 角色 —— 这是审计 **暴露了一个真 bug**，而不是审计误判。

## 6. 设计覆盖与近重复控制（实测，不是复述计划）

`design_coverage`：7 类，每类查询 ≥2、变体 5–15、困难负例 10–20，**shortfalls = []，over_range = []**。

| family | query | variant | hard_negative |
|--------|-------|---------|---------------|
| band | 3 | 13 | 11 |
| blob | 3 | 7 | 10 |
| crescent | 2 | 11 | 13 |
| forked | 3 | 14 | 10 |
| heart | 2 | 9 | 10 |
| irregular | 2 | 11 | 10 |
| star | 3 | 13 | 19 |

总计 179 成员 / 18 查询；by_origin = phosphor 25 / quickdraw 80 / synthetic 74，
by_role = query 18 / variant 78 / hard_negative 83。
该检查是 **会失败的断言**（`check_design_coverage` 抛 `RuntimeError`），
所以覆盖不足不可能被静默放行。

**近重复控制**：用 P0-4 的 `align(..., allow_mirror=True)` 对图库内部两两配对，
`align_distance ≤ 0.02` 记为近重复（9 对，如 `ph-circle-fill` ↔ `syn-blob-circle` 7.3e-05、
`qd-blob-circle-01` ↔ `qd-circle-query-01` 0.0）。这些对在 **T2 中被剔除**，
因此没有任何方法能靠"返回查询本身的刚性副本"得分。
⚠ 该规则只是受控图库的数据卫生，**不是**地理去重规则，839 条真实候选未受影响。

**包络匹配负例（matched-envelope）**：目标是"外接尺寸/面积比与查询一致，只有内部结构不同"。
生成后 **重新测量** 实际 solidity，`|residual| ≤ 0.005` 才算达到目标：**10 个里 6 个达标**，
未达标 4 个连同残差公开（`matched_envelope_control.unmatched`）：

```
syn-crescent-lobed    target 0.5570 achieved 0.9523 residual 0.3954
syn-heart-lobed-2     target 0.9207 achieved 0.9536 residual 0.0329
syn-star-lobed-5      residual 0.138
syn-star-lobed-5-cusp residual 0.326
```

Why（这是本轮的技术假设，标注为**待验证/受限**）：径向瓣族（`power_lobes` / `bumps`）
在 2/3/5 瓣下分别有约 0.94 / 0.86 / 0.67 的 solidity 下限，够不到星形查询的凹陷程度；
曾尝试用 `notch_matching_solidity`（带直切口的圆盘）二次求根补足，
实测发现 **切口圆盘的 solidity 非单调**（1.0 → 约 95° 时最低 0.826 → 179° 回升 0.995），
因为切口越大凸包自身越小，求根必然失败 → 该生成器与其规格 **已删除**，不留死代码。
结论：包络控制在 crescent/heart/star 上 **部分达成**，在 Checkpoint D 的 F3
（图库覆盖局限）分析里作为图库自身的已知局限处理，不隐藏。

## 7. 拒绝普查（75 968 次，逐原因计数）

| reason | 计数 |
|--------|------|
| quickdraw/multi_stroke | 47 772 |
| quickdraw/open_stroke | 19 881 |
| quickdraw/self_crossing | 7 744 |
| quickdraw/too_few_points | 557 |
| quickdraw/degenerate | 4 |
| phosphor/frame_silhouette | 4 |
| phosphor/multiple_pieces | 6 |

`rejections.csv` 每个原因最多保留若干条样本行（`--max-rejection-rows`，默认 300），
但 `dataset_manifest.json → rejections` 的 **计数始终完整**。
这组数字的作用：说明单笔画 + 闭合 + 不自交的真实约束有多苛刻，
也说明图库不是"从无限样本里挑好看的" —— Quick Draw 每类 6 235 条记录里只有 4 119 条可用（circle 类实测）。

## 8. 复现命令与确定性

```bash
# 首次（联网：抓 Phosphor SVG + Quick Draw 字节切片，并把字节区间钉进 cache/quickdraw/spans.json）
python scripts/prepare_perceptual_dataset.py --force
# 之后（离线重放同一批字节区间，产物应逐字节一致）
python scripts/prepare_perceptual_dataset.py --offline --force
pytest -q -o addopts=""      # 498 passed
ruff check .                 # All checks passed
mypy                         # Success: no issues found in 26 source files
```

- 确定性来源：合成生成器由参数完全决定（无随机数）；外部画作从 **固定字节区间**
  的记录列表里等距选取，第一次联网构建把区间写入 `cache/quickdraw/spans.json`，
  此后（含 `--offline`）只能重放，不能悄悄换一批画作。
- 本轮实测（工作区干净状态下，共享同一 cache 重建到临时目录，`cmp` 逐文件比对）：

```
gallery.jsonl     identical: True   (sha256 3a7646646cd9d496c32a5e5fcec5d10538709972ddb9744222cda17bc3acc138)
                  # Checkpoint B 的 _bevel 修复后重建 = 15be97ae5ae583ceacfbf5ea76a18911acd1004bb3dc2c17b7d552b189e77891（§11）
labels.json       identical: True   (sha256 26e444ce2299c414c6deac7d8a260f4597360bcbe35d65dde2e66d2dbcb3b213)
queries/*.json    identical: True   (18 files, 逐个 cmp)
rejections.csv    identical: True
control_gallery.png identical: True (sha256 8c07d12a8c24632be20bffecb4fd64fd16a1ecac0c9c5011b7381b633e19c983)
                  # 同上重建后 = 585ffb283dc7460a8cbd66817cf217e883f12050af631552fec6bdd261be0211，1 184 570 → 1 184 600 B
```

`diff -r` 下 **唯一** 不同的文件是 `dataset_manifest.json`，且只差两处自指字段：
`built_at_utc` 与 `reproduce.command` 里的 `--out-dir` 字符串（临时目录名）。
几何、计数、哈希、许可、字节区间全部一致。

- 提交 **之后** 在工作区干净状态下重跑离线构建：`gallery.jsonl` / `labels.json` /
  `control_gallery.png` 三个 sha256 与入库版本 **一致**，
  只有 `dataset_manifest.json` 的 `built_at_utc` 与 `git_commit` 变化
  （现等于检查点 sha `10c9e8fab8ca39401b6f2e1336780bbb90f54718`）。
  该 manifest 的更新随核验提交入库，因此 **入库 manifest 记录的 `git_commit` 是构建时的
  父提交 `4c4bf66`，而工作区 manifest 记录 `10c9e8fa`** —— 提交无法写入自身 sha，
  两者都保留在上面的哈希链里，不是不一致。

- 不入库（`.gitignore`）：`experiments/**/cache/`（42 MB，可由 manifest 里的
  URL + 字节区间 + sha256 重建）、`experiments/**/gallery.jsonl`（1.2 MB 生成几何）。
  入库的是 `labels.json`（118 796 B，冻结的角色/划分）、`dataset_manifest.json`
  （76 709 B，完整性记录）、`queries/`（18 个查询 JSON）、`rejections.csv`
  （200 332 B）、`control_gallery.png`（1 184 570 B > 1 MiB，理由见提交信息；
  Checkpoint B 的 `_bevel` 修复后重建为 1 184 600 B，见 §11）。

## 9. 本轮修掉的真实缺陷（不是实验设计的一部分，但是图库正确性的前提）

`earthglyph/data/svg_path.py`：

1. **`Z`/`z` 从未被派发**：指令扫描里 `if command is not None and buffer` 对无参数指令
   （`Z` 正是）永远为假 → 文档里 **所有** 闭合路径的闭合动作被静默丢弃，且后续指令
   继承错误的隐式命令。修法：去掉 `and buffer` 守卫。
2. 修好 1 之后暴露第二处：**已经在起点的路径被再加一次起点**，产生重复顶点
   （`close_ring` 现先比较末点与起点）。
3. `<circle>` 的报告命令从 `"ellipse"` 改为 `"circle"`（`_ellipse_ring` 接受 `command` 参数）。

影响核查（**不是**"无影响"，实测过）：用当前解码器与**逐条回退**后的解码器分别离线重建图库，
再按成员比对标准化轮廓：

| 回退项 | 42 个缓存 SVG 中/gallery 环受影响 | 图库标准化轮廓受影响 |
|--------|----------------------------------|----------------------|
| 1 `Z` 未派发 | 2 | —— |
| 2 重复收尾顶点 | 35 | —— |
| 1 + 2 同时回退（=修复前） | —— | **14 / 25 个 Phosphor 成员** |

- 最大逐点位移 `0.0879`（`ph-spiral-fill`），其次 `0.0705`（`ph-bone-fill`）、`0.0506`（`ph-heart-break-fill`）；
  用本项目自己的对齐（循环起点 + 刚性 + 镜像）复测，修复前后同一成员的对齐距离最大 `0.0525`。
- **未受影响**：179 个成员的 id / 族 / **角色** 全等（role diffs = 0），
  `raw_points` 只有那 14 个成员各少 1 个顶点。
- 受影响的下游数值：近重复表中 `ph-heart-fill ↔ ph-heart-half-fill`
  的 `align_distance` 由 0.002278 变为 0.001748（仍 < 0.02，仍是近重复，
  所以 T2 的剔除集合不变）；其余 8 对完全一致。
- 结论：**冻结的 `gallery.jsonl`（sha256 `3a7646…`）绑定修复后的解码器**，
  复现时必须用仓库里的 `svg_path.py`；这是一次有意的、改变测量结果的正确性修复，
  不是无害的清理。因为该修复改动了共享模块，`tests/test_svg_path.py` 必须一直跑。
- `tests/test_svg_path.py` 40 项，其中 8 项失败经核查是 **4 项测试自身假设写错**
  （绝对/相对 `H` 语义、S/T 采样索引、混用 `segments`、原始元素的命令普查）
  + **2 项揭示了上述真 bug**，两边都已改对，不是放宽断言。
- 核查脚本与两份重建产物留在 `.scratch/`（已 gitignore，非交付物，可能随工作区清理而消失）：
  `old_decoder_build/gallery.jsonl` 是"修复前"对照；上述数值来自本会话实测。

## 10. 本轮 **不允许** 的主张（写在前面，防止后续误引）

1. 没有任何检索方法跑过 → 没有 Recall@K / MRR / mAP / NDCG，没有排名，没有时延结论。
2. 没有人工评分 → **不得**称任何内部指标为"人眼匹配准确率"；
   `labels.json` 的相关性是 **设计意图**（谁与谁结构同类），不是人类真值。
3. ShapeComp-derived 的邻居关系（Checkpoint C 之后才有）只能表述为 **ShapeComp 自己的定义**，
   不得等同人类相似度；E1 是 inspired 复现，不是原始 ShapeComp。
4. 未做查询挑选或权重调参：查询集、划分、阈值在算法运行 **之前** 冻结
   （`labels.json` 的 `frozen_at_build` 与 sha256 即冻结凭证）。
5. 未把实验图库并入正式索引：`data/index` 未重建、未扩容（仍 `77b083c9`）。
6. 几何距离 **不换算** 成百分比或"相似度分数"。

## 11. 检查点状态

| 检查点 | 状态 | 证据 |
|--------|------|------|
| A 数据与实验设计 | **完成并推送**（`10c9e8fa`，`4c4bf66..10c9e8f main -> main`；`git ls-remote origin` = `git rev-parse HEAD`） | §2–§9；`control_gallery.png`；498 passed；ruff/mypy 干净；逐字节重复构建 |
| B E1/E2/E3 实现 + 测试 | **完成并推送**（`5a9f1bc` fix + `1b8461c` data + `1696873` feat，`6535e98..1696873 main -> main`；`git ls-remote origin` = `1696873f4dbc5e5b7a7aedfc7bfa2818e891326f` = `git rev-parse HEAD`） | E1 `perceptual_features.py` + E2 `salient_structure.py` + E3 `shape_context_baseline.py` + 四臂契约 `perceptual_methods.py` + E0 适配器 `perceptual_e0.py`（**产品链路 0 改动**）；605 passed、ruff/mypy 干净；POLICY 里每个数字由 `python experiments/p0_perceptual_probe/verify_policy_claims.py` 重跑并做容差检查。B **未做检索评价** → 命中率 / 排名 / 对比图属 C。**注意**：`5a9f1bc` 的 `_bevel` 修复改变了生成器 → 本目录的 `gallery.jsonl` 重建为 sha256 `15be97ae…`、`control_gallery.png` 为 `585ffb28…`（1 184 600 B），**§8 里那两个 A 期 sha 因此成为历史记录**；`labels.json` `26e444ce…` 与逐资产字节区间不变，故 §2–§7 的许可、角色与划分结论全部仍然成立 |
| C 全方法运行 + 对比图 | 未开始 | 需 `perception_comparison_overview.png` + 5 张 `comparison_*.png` + 全量实际排名；E3 的 `sample` 模式约 7 s/查询 → 耗时必须随指标一起报告 |
| D 指标 / 不变性 / 盲评 / 结论 | 未开始 | 需 T1/T2/T3 分开报告 + ≥3 人盲评（拿不到就如实标注未完成） |

已知阻塞 / 待办：
- E1 的 22 维只能自建（官方代码不可达，见 §2）；归一化必须逐描述子说明尺度处理。
- 包络匹配在 crescent/heart/star 上只部分达标（§6），Checkpoint D 的 F3 需据此分析。
- 人工盲评需要真人；若最终拿不到评分，`perception_blind_review.png` 与评分 CSV 仍要交付，
  并在报告里标注"未获评分"。
- C 必须先固定三件事，否则数字没有可比性：① 库（E1 的归一化常数自拟合于当前库）；
  ② E3 的 `d(query, candidate)` 参数方向（780 对实测 735 对不对称）；
  ③ E1 的 `orientation` 模式（`canonical` 与 `raw` 在正多边形 / 星形上是两个不同的方法）。

## 12. Checkpoint B 的落点（三臂实现后，本目录的哪些结论仍然成立）

B 没有改动图库的任何设计结论，只改了 **生成器的一个缺陷** 和由此重放的几何：

1. `_bevel` 之前只切角的一侧，圆角星变成尖刺（实测最坏顶点转角 176.8°，设计意图 48.0°；
   周长 8.464 → 6.624，总转角 40.35 → 17.91，面积变化 <0.3%；数字记在
   `earthglyph/data/perceptual_gallery.py` 的 `_bevel` 文档串里）。
   → 受影响的全是 `syn-*` 生成成员，外部画作不经过 `_bevel`。
2. **两份 manifest 的递归 diff 只差 3 个字段**（下面这条命令可直接复现该比对）：

```bash
mkdir -p .scratch && git show 10c9e8f:experiments/p0_perceptual_probe/dataset_manifest.json \
    > .scratch/manifest_A.json
python - <<'PY'
import json
def walk(x, y, path=""):
    if isinstance(x, dict) and isinstance(y, dict):
        for key in sorted(set(x) | set(y)):
            walk(x.get(key), y.get(key), f"{path}/{key}")
    elif isinstance(x, list) and isinstance(y, list):
        assert len(x) == len(y), f"LEN {path} {len(x)} {len(y)}"
        for index, (left, right) in enumerate(zip(x, y, strict=True)):
            walk(left, right, f"{path}[{index}]")
    elif x != y:
        print("DIFF", path, repr(x)[:48], "->", repr(y)[:48])
walk(json.load(open(".scratch/manifest_A.json", encoding="utf-8")),
     json.load(open("experiments/p0_perceptual_probe/dataset_manifest.json", encoding="utf-8")))
PY
```

```
DIFF /built_at_utc '2026-11-05T21:02:38+00:00' -> '2026-11-05T23:15:53+00:00'
DIFF /content_sha256/gallery.jsonl '3a7646646cd9d496c32a5e5fcec5d10538709972ddb9744 -> '15be97ae5ae583ceacfbf5ea76a18911acd1004bb3dc2c1
DIFF /reproduce/git_commit '4c4bf66fe178fafee4b7ddc21349a2dd5476cc7b' -> '5a9f1bcc924c9554843e66d382ebd962b1ae3b52'
```

   即 §2 的逐资产哈希与许可、§5 的角色审计（含 3 条降级）、§6 的设计覆盖与包络匹配残差
   （**6/10 达标、未达标 4 个 id 完全相同**：`syn-crescent-lobed` / `syn-heart-lobed-2` /
   `syn-star-lobed-5` / `syn-star-lobed-5-cusp`，最大残差仍是 0.395）、
   §6 的 9 对近重复、§7 的 75 968 次拒绝 **逐字段未变**；
   `labels.json`（sha256 `26e444ce…`）也不变 → §2–§7 的结论不需要重写。
3. 冻结标签先于任何算法这一条继续成立：B 只跑了自测量探针（同一对形状的距离、不变性残差），
   **没有**按标签筛查询、调权重或改划分。
4. 三臂的数字与"被否决公式"的代价现在都有仓库内重跑命令（§11 的
   `verify_policy_claims.py`），不再依赖 `.scratch/` 里的一次性脚本。§9 最后一条对
   `.scratch/` 的依赖仍然存在于 **解码器修复** 的对照产物上，那部分证据只在工作区，不在仓库。
