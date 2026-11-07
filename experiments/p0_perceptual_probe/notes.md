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
| B E1/E2/E3 实现 + 测试 | **完成并推送**（`5a9f1bc` fix + `1b8461c` data + `1696873` feat（604 passed）+ `3ababaf` exp（重跑脚本与 `measurements` 指针，605 passed）+ `78915ba` docs；推送 `6535e98..1696873` 与 `1696873..78915ba`，最终 `git ls-remote origin` = `78915bafe632a3056e6186c021a4dc858a7babd2` = `git rev-parse HEAD`） | E1 `perceptual_features.py` + E2 `salient_structure.py` + E3 `shape_context_baseline.py` + 四臂契约 `perceptual_methods.py` + E0 适配器 `perceptual_e0.py`（**产品链路 0 改动**）；605 passed、ruff/mypy 干净；POLICY 里每个数字由 `python experiments/p0_perceptual_probe/verify_policy_claims.py` 重跑并做容差检查。B **未做检索评价** → 命中率 / 排名 / 对比图属 C。**注意**：`5a9f1bc` 的 `_bevel` 修复改变了生成器 → 本目录的 `gallery.jsonl` 重建为 sha256 `15be97ae…`、`control_gallery.png` 为 `585ffb28…`（1 184 600 B），**§8 里那两个 A 期 sha 因此成为历史记录**；`labels.json` `26e444ce…` 与逐资产字节区间不变，故 §2–§7 的许可、角色与划分结论全部仍然成立 |
| C 全方法运行 + 对比图 | **完成并推送**（`48ec6fc` exp + `05c50b6` fix + `11b2bc8` fix + `1a73d6e` data；推送 `78915ba..1a73d6e`，`git ls-remote origin` = `1a73d6e678ce5eea6d08d2fd274119d87d4ebeea` = `git rev-parse HEAD`；621 passed、ruff/mypy 干净） | §13 全部；`results.csv` 3 601 行、`metrics.json`、`method_status.json`、`figures_manifest.json`、21 张 `comparison_*_{t1,t2,t3}.png` + 3 张 overview；E3 `sample` 耗时随指标一起报告（T2 7.01、T3 40.32 s/查询），评价代码 commit = `05c50b6` |
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

## 13. Checkpoint C 的落点（四臂同集实测 + 对比图 + 结果文件怎么读）

C 的范围只到「四臂在同一冻结图库上跑完 T1/T2/T3 + 逐查询 Top-K 对比图 + 结果文件」。
**评价结论、不变性曲线、人工盲评属 Checkpoint D**，本节一概不下。

### 13.1 先固定口径，才允许三个设置的数字互相比较

§11 给 C 留了三件事，全部按"先测量、后固定"处理，固定值由 runner 写进 `metrics.json`，
再由 `report_checkpoint_c.py` 原样打印（D48：本节每个数字都来自这条命令）：

1. **E1 的归一化尺度**：`fit_gallery_normalizer` 在 **179 成员语料** 上拟合一次 minmax，
   再传给 T1/T2/T3 全部库。不这样做时 E1 会在每个库里重拟合，同一查询的距离跨设置不可比
   （B 的 §11 实测记录过这个副作用）。代价也一并量了：真实索引里有
   0.5% 的描述子单元落在拟合区间外（最差 `curv00_w32_concave_share`）→
   **E1 的距离只在这一个语料拟合的尺度内可比**，写进 `claim_limits`。
2. **E3 的参数方向**：固定 `d(query, candidate)`。反向的代价实测（6 对被查）
   median 0.0071 / max 0.0113，因此 `e3_direction_probe` 随产物发布，不靠读者记住约定。
3. **E1 的 `orientation`**：固定 `canonical`。`raw` 是另一个方法（正多边形/星形上两者
   结论不同），不作默认。

E3 的 `sample` 与 `canonical` 两档都按 §11 的要求 **报告耗时而不是隐藏**，比值见下面的
`e3 cost` 行；正式四臂评价用 `sample`（论文定义的那一档），耗时因此进入指标表最后一列。

运行规模与墙钟：18 查询 × 4 臂 × 3 设置 = 216 次全库评分；库行数 T1 19–35、
T2 177–178、T3 1016–1017。行数不是常数，因为每个设置按 §13.3 的规则剔除不同的行
（T1 的库 **包含** 查询自己那一行，只是它被 `exclude_from_metrics` 挡在指标之外；
T2 去掉自身、刚性副本与 §8 的 9 对近重复）。

```
what this report reads, and the inputs it was computed from
command        scripts/evaluate_perceptual_methods.py --top 10
git_commit     05c50b695b6a6937ee4a4d0b6df465a43bc7a7b4
wall_seconds   1151.1
gallery_version  1.0
sha256 gallery.jsonl        15be97ae5ae583ce
sha256 labels.json          26e444ce2299c414
sha256 index/features.npy   b7255ede076922e9
sha256 index/metadata.parquet e18c3f95039f3f8d
sha256 index/shapes.npy     fbaf4933e1e1585b
e1 normalizer  minmax on 179 rows x 91 descriptors, orientation=canonical, degenerate=7
e1 off-corpus  0.005 of 76,349 descriptor cells leave the fitted interval (worst curv00_w32_concave_share)
e3 direction   d(query, candidate) fixed; reverse order differs on 6 pairs: median 0.0071 max 0.0113
e3 cost        T1: 0.75s sample / 0.04s canonical (x18.9, 19 rows)  T2: 6.95s sample / 0.36s canonical (x19.4, 177 rows)  T3: 39.66s sample / 2.12s canonical (x18.7, 1016 rows)
```

**确定性证据（缺陷修复前后两次完整运行）**：把 `results.csv` 的评分列
（`setting,arm,query_id,rank,candidate_id,distance,relevant,in_top_k` + 四列臂内附加列）
取出来比对，**3 601 行逐字节相同**。两列刻意 **不** 参与比对：`arm_seconds_for_query`
（墙钟，`wall_seconds` 1247.8 → 1151.1，每臂 `sec/q` 同步下降约 8–12%）与
`align_indistinguishable_from_query` —— 后者正是 `05c50b6` 要修的那一列，它的变化是
**预期结果**而不是测量抖动（修复后 E0 在 T2/T3 各 11 行带标记，与 E1/E3 是同一批候选，
只是原先晚于 CSV 写出而没印上）。21 张 `comparison_*.png` 的 sha256 亦相同；
3 张 overview 图 **确实变了字节**，原因可解释：它们把 `sec/query` 印在表里，
表内的命中率 / ap / 排名逐位未变。→ 结论：**分数可复现，时延不可**。
> 一条容易读错的地方：两次运行各自的 `metrics.json.git_commit` 是 `669f429`（前一次：当时
> runner 还没提交，工作区里的代码就是后来 `48ec6fc` 的内容）与 `05c50b6`（后一次）。
> 所以"字段里的 commit 不同"**不等于**"跑的代码不同"——比较时以 CSV/产物为准。

### 13.2 指标表（原样贴出，不得手抄）

生成命令：

```bash
python experiments/p0_perceptual_probe/report_checkpoint_c.py
```

```
setting T1 - query's own family only (exact target present, excluded from the metrics)
arm       hit@1    hit@3    hit@5   hit@10      mrr       ap  ndcg@10  1stRel    sec/q      rows
E0        0.889    0.944    1.000    1.000    0.928    0.723    0.892     1.3     0.00     19-35
E1        1.000    1.000    1.000    1.000    1.000    0.792    0.931     1.0     0.22     19-35
E2        0.778    0.944    1.000    1.000    0.872    0.748    0.882     1.4     0.16     19-35
E3        0.889    1.000    1.000    1.000    0.944    0.761    0.906     1.1     1.02     19-35

setting T1 - what occupied the first k slots of those 18 queries, counted by label, not by score
arm   relevant  same-fam-neg  other-fam  landform  top1-lookalike  mean-indist
E0         0.62          0.28       0.00      0.00            0.00           0.0
E1         0.68          0.21       0.00      0.00            0.00           0.0
E2         0.66          0.24       0.00      0.00            0.00           0.0
E3         0.67          0.23       0.00      0.00            0.00           0.0

setting T1 - Top-1 per arm (R=relevant, x=label lookalike, -=label miss); in T1 the query's own row is skipped here, see exact_target_self_rank below
ph-circle-fill          E0 - ph-hexagon-fill        E1 R syn-blob-e2            E2 R syn-blob-e2            E3 R syn-blob-e2            
ph-heart-fill           E0 R syn-heart-shallow      E1 R syn-heart-bean         E2 R syn-heart-bean         E3 R syn-heart-shallow      
ph-lightning-fill       E0 - qd-band-circle-01      E1 R qd-lightning-05        E2 R qd-lightning-06        E3 R qd-lightning-05        
ph-moon-fill            E0 R syn-crescent-fat       E1 R qd-moon-07             E2 R qd-moon-02             E3 R syn-crescent-fat       
ph-star-fill            E0 R syn-star-inner-055     E1 R syn-star-inner-055     E2 R syn-star-soft          E3 R syn-star-soft          
ph-tree-structure-fill  E0 R qd-tree-01             E1 R qd-palm-03             E2 - syn-forked-query       E3 - syn-forked-query       
qd-circle-query-01      E0 R qd-blob-circle-03      E1 R qd-blob-circle-03      E2 R qd-blob-circle-04      E3 - syn-blob-query         
qd-cloud-query-01       E0 R qd-irregular-cloud-03  E1 R qd-irregular-cloud-03  E2 R qd-irregular-cloud-05  E3 R qd-irregular-cloud-02  
qd-lightning-query-01   E0 R qd-lightning-04        E1 R qd-lightning-02        E2 R qd-lightning-03        E3 R qd-lightning-02        
qd-star-query-01        E0 R syn-star-jitter        E1 R qd-star-07             E2 R qd-star-08             E3 R syn-star-jitter        
qd-tree-query-01        E0 R qd-tree-03             E1 R qd-tree-03             E2 - qd-fork-01             E3 R qd-tree-04             
syn-band-query          E0 R syn-band-tight         E1 R syn-band-loose         E2 - syn-ellipse-band       E3 R syn-band-tight         
syn-blob-query          E0 R qd-blob-circle-03      E1 R syn-blob-e2            E2 - ph-circle-fill         E3 R qd-blob-circle-03      
syn-crescent-query      E0 R syn-crescent-turn      E1 R syn-crescent-turn      E2 R syn-crescent-deformed  E3 R syn-crescent-deformed  
syn-forked-query        E0 R syn-forked-uneven      E1 R syn-forked-uneven      E2 R syn-forked-uneven      E3 R syn-forked-uneven      
syn-heart-query         E0 R syn-heart-point        E1 R syn-heart-round        E2 R syn-heart-wide         E3 R syn-heart-point        
syn-irregular-query     E0 R syn-irregular-a        E1 R syn-irregular-a        E2 R syn-irregular-c        E3 R syn-irregular-a        
syn-star-query          E0 R syn-star-rounded       E1 R syn-star-inner-030     E2 R syn-star-rounded       E3 R syn-star-rounded       

setting T2 - whole controlled gallery (exact target absent)
arm       hit@1    hit@3    hit@5   hit@10      mrr       ap  ndcg@10  1stRel    sec/q      rows
E0        0.667    0.667    0.833    0.889    0.718    0.353    0.665     4.6     0.01   177-178
E1        0.611    0.833    0.833    0.944    0.724    0.435    0.721     2.7     1.45   177-178
E2        0.556    0.667    0.778    0.944    0.657    0.376    0.662     3.2     0.78   177-178
E3        0.667    0.778    0.889    1.000    0.752    0.449    0.733     2.3     7.01   177-178

setting T2 - what occupied the first k slots of those 18 queries, counted by label, not by score
arm   relevant  same-fam-neg  other-fam  landform  top1-lookalike  mean-indist
E0         0.32          0.06       0.57      0.00            0.22           0.6
E1         0.43          0.06       0.48      0.00            0.22           0.6
E2         0.35          0.07       0.56      0.00            0.11           0.6
E3         0.43          0.05       0.46      0.00            0.22           0.6

setting T2 - Top-1 per arm (R=relevant, x=label lookalike, -=label miss)
ph-circle-fill          E0 x syn-circle             E1 x syn-circle             E2 - syn-band-loose         E3 x syn-circle             
ph-heart-fill           E0 R syn-heart-shallow      E1 - qd-circle-02           E2 - qd-band-circle-03      E3 R syn-heart-shallow      
ph-lightning-fill       E0 - qd-diamond-01          E1 - qd-diamond-03          E2 R qd-lightning-06        E3 - syn-forked-four        
ph-moon-fill            E0 R syn-crescent-fat       E1 R qd-moon-07             E2 R qd-moon-02             E3 R syn-crescent-fat       
ph-star-fill            E0 R syn-star-inner-055     E1 R syn-star-inner-055     E2 R syn-star-soft          E3 R syn-star-soft          
ph-tree-structure-fill  E0 - syn-band-tight         E1 R qd-palm-03             E2 - qd-irregular-cloud-03  E3 - syn-forked-query       
qd-circle-query-01      E0 x qd-band-circle-01      E1 x qd-band-circle-01      E2 x qd-band-circle-01      E3 x qd-band-circle-01      
qd-cloud-query-01       E0 x qd-cloud-01            E1 x qd-cloud-01            E2 x qd-cloud-01            E3 x qd-cloud-01            
qd-lightning-query-01   E0 R qd-lightning-04        E1 R qd-lightning-02        E2 R qd-lightning-03        E3 R qd-lightning-02        
qd-star-query-01        E0 R syn-star-jitter        E1 R qd-star-07             E2 R qd-star-08             E3 R syn-star-jitter        
qd-tree-query-01        E0 R qd-tree-03             E1 R qd-tree-03             E2 - qd-fork-01             E3 R qd-tree-04             
syn-band-query          E0 R syn-band-tight         E1 R syn-band-loose         E2 - syn-ellipse-band       E3 R syn-band-tight         
syn-blob-query          E0 x syn-egg                E1 x syn-egg                E2 - ph-circle-fill         E3 x syn-egg                
syn-crescent-query      E0 R syn-crescent-turn      E1 R syn-crescent-turn      E2 R syn-crescent-deformed  E3 R syn-crescent-deformed  
syn-forked-query        E0 R syn-forked-uneven      E1 R syn-forked-uneven      E2 R syn-forked-uneven      E3 R syn-forked-uneven      
syn-heart-query         E0 R syn-heart-point        E1 R syn-heart-round        E2 R syn-heart-wide         E3 R syn-heart-point        
syn-irregular-query     E0 R syn-irregular-a        E1 R syn-irregular-a        E2 R syn-irregular-c        E3 R syn-irregular-a        
syn-star-query          E0 R syn-star-rounded       E1 - syn-blob-star5         E2 R syn-star-rounded       E3 R syn-star-rounded       

setting T3 - gallery + 839 real landform rows (landforms carry no relevance label)
arm       hit@1    hit@3    hit@5   hit@10      mrr       ap  ndcg@10  1stRel    sec/q      rows
E0        0.556    0.556    0.722    0.722    0.609    0.239    0.547    23.2     0.08 1016-1017
E1        0.389    0.556    0.667    0.778    0.503    0.259    0.552    19.8     8.30 1016-1017
E2        0.444    0.667    0.722    0.722    0.557    0.277    0.554    12.5     4.59 1016-1017
E3        0.667    0.722    0.778    0.889    0.729    0.322    0.636     5.7    40.32 1016-1017

setting T3 - what occupied the first k slots of those 18 queries, counted by label, not by score
arm   relevant  same-fam-neg  other-fam  landform  top1-lookalike  mean-indist
E0         0.24          0.03       0.26      0.43            0.22           0.6
E1         0.28          0.03       0.12      0.55            0.22           0.6
E2         0.27          0.04       0.26      0.41            0.11           0.6
E3         0.33          0.03       0.19      0.41            0.22           0.6

setting T3 - Top-1 per arm (R=relevant, x=label lookalike, -=label miss)
ph-circle-fill          E0 x syn-circle                            E1 x syn-circle                            E2 - syn-band-loose                        E3 x syn-circle                            
ph-heart-fill           E0 R syn-heart-shallow                     E1 - 1da7c515-eb3a-30b2-a6c0-43774ce4e996  E2 - 499ea1cb-3a4b-3278-ad85-b9558d3dab07  E3 R syn-heart-shallow                     
ph-lightning-fill       E0 - qd-diamond-01                         E1 - 526733eb-d25c-3d54-9c89-c0696ac432cf  E2 - d90df46c-9dc9-3175-944f-89d6d996d39e  E3 - 918be20f-c36a-3763-b41e-fb5df192288c  
ph-moon-fill            E0 - e709f179-c8bd-3049-b453-f2077d786ec3  E1 - 48e13726-e0b1-3453-ac46-531aa78ee3f0  E2 R qd-moon-02                            E3 R syn-crescent-fat                      
ph-star-fill            E0 R syn-star-inner-055                    E1 R syn-star-inner-055                    E2 R syn-star-soft                         E3 R syn-star-soft                         
ph-tree-structure-fill  E0 - 6eb02eb1-105e-3c62-a96f-035b7b4961ce  E1 - 6eb02eb1-105e-3c62-a96f-035b7b4961ce  E2 - ffc02e38-3394-35cf-bdb4-94a9888a291b  E3 - syn-forked-query                      
qd-circle-query-01      E0 x qd-band-circle-01                     E1 x qd-band-circle-01                     E2 x qd-band-circle-01                     E3 x qd-band-circle-01                     
qd-cloud-query-01       E0 x qd-cloud-01                           E1 x qd-cloud-01                           E2 x qd-cloud-01                           E3 x qd-cloud-01                           
qd-lightning-query-01   E0 R qd-lightning-04                       E1 R qd-lightning-02                       E2 R qd-lightning-03                       E3 R qd-lightning-02                       
qd-star-query-01        E0 R syn-star-jitter                       E1 R qd-star-07                            E2 R qd-star-08                            E3 R syn-star-jitter                       
qd-tree-query-01        E0 - 981b0f52-aed7-3d96-af8e-cb20d247bde6  E1 - 0bebdfe4-96f3-3cce-b651-9ed98eb9c7db  E2 - c8085b59-cd53-3972-a281-78cca5d942f5  E3 R qd-tree-04                            
syn-band-query          E0 R syn-band-tight                        E1 - 1c8772e1-d7b5-3d4e-b4fc-de7e8f65ea3d  E2 - syn-ellipse-band                      E3 R syn-band-tight                        
syn-blob-query          E0 x syn-egg                               E1 x syn-egg                               E2 - ph-circle-fill                        E3 x syn-egg                               
syn-crescent-query      E0 R syn-crescent-turn                     E1 R syn-crescent-turn                     E2 R syn-crescent-deformed                 E3 R syn-crescent-deformed                 
syn-forked-query        E0 R syn-forked-uneven                     E1 R syn-forked-uneven                     E2 R syn-forked-uneven                     E3 R syn-forked-uneven                     
syn-heart-query         E0 R syn-heart-point                       E1 R syn-heart-round                       E2 R syn-heart-wide                        E3 R syn-heart-point                       
syn-irregular-query     E0 R syn-irregular-a                       E1 R syn-irregular-a                       E2 - 5a8c9f94-3439-38b6-9135-13e07f7b9402  E3 R syn-irregular-a                       
syn-star-query          E0 R syn-star-rounded                      E1 - syn-blob-star5                        E2 R syn-star-rounded                      E3 R syn-star-rounded                      

T1 exact_target_self_rank - the query's own contour is a library row; rank 1 means the arm is invariant on its own input
E0     rank1 18/18  median   1  max   1
E1     rank1 18/18  median   1  max   1
E2     rank1 17/18  median   1  max   4
E3     rank1 18/18  median   1  max   1

claim limits (copied from metrics.json, not paraphrased):
  - no human judgement is involved anywhere in this file; these are not accuracy numbers against perceived similarity
  - labels.json relevance is the generator's design intent (same family, role=variant), not a human ground truth
  - T1 is an implementation sanity check: the query's own contour is in the library, so exact_target_self_rank measures invariance, while its relevance metrics reuse the family's variants
  - T3 real landform candidates carry no relevance label: a pond cannot belong to the star family, so a landform in the Top-k is not a false positive in any human sense. T3 measures how far the labelled variants survive being mixed with 839 unlabelled shapes, and nothing more
  - E1's distances are only comparable inside this one corpus-fitted normalizer; they are not comparable to a Checkpoint B self-fitted number
  - E3's score is asymmetric; the run fixes d(query, candidate) and e3_direction_probe measures what the other order would have cost
  - timings are this machine's wall clock and will not reproduce elsewhere; the scores will
```

读这几张表时要注意四件事：

- `hit@k` / `mrr` / `ap` / `ndcg@10` 的相关集合来自 **冻结标签的设计意图**（同族 variant），
  不是人工真值；`first_relevant_rank` 是"第一个该出现的东西排在哪"。
- `aggregate` 是 **18 个查询的等权算术平均**（`summarise`），不是按族加权；
  `top_slots` 的份额分母固定为 k（本次 `--top 10`），所以 `landform_share=0.43` 读作
  "平均每个查询的前 10 名里 4.3 个是真实地貌候选"。
- `ap` 比 `hit@k` 诚实：T1 的 hit@1 在 0.78–1.00 之间，而 ap 只有 0.72–0.79，
  因为族内困难负例被排进了前列。
- `sec/q` 是本机墙钟，**不可跨机复现**；`scores`（`rank`/`distance`）可复现。

### 13.3 三个设置各自在测什么——以及不能测什么

| 设置 | 库 | 精确目标在库里吗 | 测什么 | 不能测什么 |
|------|----|------------------|--------|------------|
| T1 | 查询自己的族（19–35 行） | **在**，但被 `exclude_from_metrics` 从指标中剔除 | 实现自洽性 + 族内变体的排序质量 | 判别性（库太小，同族负例有限） |
| T2 | 整个受控图库（177–178 行，已去刚性副本与 §8 的 9 对近重复） | 不在 | **判别性主设置**：变体 vs 困难负例 vs 外族 | 真实地貌干扰 |
| T3 | 图库 + 839 真实地貌候选（1016–1017 行，逐候选 provenance） | 不在 | 有标签的变体能扛多少无标签形状混入 | 任何"地理匹配质量"（地貌无相关性标签） |

T1 还有一个别的设置没有的量：查询自身的轮廓就在库里，所以
`exact_target_self_rank` 直接测 **臂对自己输入的不变性**（rank 1 = 把自己的形状认回第一）。
`git_commit` 那一行的表显示 E0/E1/E3 都是 18/18，**E2 是 17/18、最差 rank 4**。
这一条 C 只记录不解释：还没定位到是哪个尺度/事件代价造成的，留给 D，
在此之前 **不得** 写"四臂都通过自测"。

### 13.4 标签与人眼的分裂：Top-1 表里的 `x` 不是失败

`labels.json` 把圆盘类形状故意标成星族/新月族的 **困难负例**，而 E0 的对齐距离能小到
1e-4 量级（`syn-circle` 相对 `ph-circle-fill` 的距离 `7.28316078840726e-05`，
阈值 `near_max_align = 0.02`）。这类候选：

- 图上看着 **正确**（形状确实重合），标签上算 **miss**；
- 在 `results.csv` 里由 `align_indistinguishable_from_query=1` 单独标出，
  且只在 **非相关** 行上标 1（相关行不需要这个解释）；
- 在表里由 Top-1 的 `x` 记号与 `top1-lookalike` 比例计数：T2 是 E0/E1/E3 各 0.22、
  E2 0.11，T3 同分布，`mean-indist` 每查询约 0.6 行。

所以读指标时 `hit@1` 低的臂不一定"更差"，读 `x` 与 `top1-lookalike` 才能把
"标签不同意" 与 "眼睛不同意" 分开。**两者都不等于人类判断**——人类判断在 D。

### 13.5 产物清单与每个文件的读法

列出下列文件的字节数与 sha256（这条命令直接打印，不用手抄）：

```bash
python experiments/p0_perceptual_probe/report_checkpoint_c.py --files
```

| 文件 | 里面是什么 | 怎么读 | 不能读成什么 |
|------|------------|--------|--------------|
| `results.csv`（20 列，3 601 行：E0 926 / E1 889 / E2 906 / E3 880，~0.9 MB，**UTF-8 无 BOM**） | 每次 (setting, arm, query) 评分的 **Top-10 行 + 全部带相关标签的行**，按 rank 升序 | `rank` 是该臂自己的顺序；`relevant` 只答标签；`distance` 是臂内部无量纲代价；`theta_deg`/`shift` **只有 E0 有值**（它真的返回了姿态，926 行全填）；`query_events`/`candidate_events`/`pairs` **只有 E2 有值**（906 行）；`mirrored` 每行都写，本轮恒 `False`（`--allow-mirror` 默认关闭，与产品链路一致） | 不是全库导出（全库行数在 `metrics.json` 的 `n_scored`）；`distance` **不能跨臂比较**，E0 的 0.02 与 E1 的 0.02 毫无关系；`arm_seconds_for_query` 是整次评分的墙钟，不是该行的耗时；**Excel 双击打开会乱码**（无 BOM），用"数据 → 从文本/CSV"并选 UTF-8 |
| `metrics.json` | 冻结输入哈希、`protocol`、三条固定口径（`e1_normalizer` / `e3_direction_probe` / `e3_orientation_cost`）、`results[setting][arm]`（`aggregate` / `top_slots` / `label_pressure` / `per_query` / `timing` / `exact_target_self_rank` / `top1`）、`claim_limits` | 一切汇总数字的唯一来源。口径细节：`ap` 是 **全 ranking** 的平均精度（无截断），`ndcg@10` 截到 10，`hit@k` 只问前 k 里有没有；`per_query[q].top1_is_label_lookalike` 是 §13.4 的 `x`；`n_scored` 才是该次评分真正打过多少行 | 不含任何人类评分；`hit@k` 不是准确率；`aggregate` 是 18 个查询的算术平均，不是加权按族 |
| `method_status.json` | 四臂的模块、参数、`contract`、每臂 POLICY（含 `invariance` / `cost` / `known_limitations` / `measurements`） | 回答"这一臂到底是怎么算的、它自己声明的局限是什么" | 不是实验结果，改代码前不必读，读它不能得到任何命中率 |
| `figures_manifest.json` | `checkpoint` / `built_at_utc` / `reproduce.git_commit` + 3 条命令 / `inputs`（gallery、labels、manifest、results.csv、metrics.json 的 sha256+字节） / `outputs`（24 张 PNG 的 sha256+字节） | 图与数据的绑定凭证：图上的每个数字都能回溯到 `inputs` 里那两个哈希 | 不是指标表；`outputs` 的哈希只对 **当前** 磁盘文件有效，重跑即变 |
| `comparison_<family>_<setting>.png`（7 族 × 3 设置） | 一个结构族的全部查询：一行 = (查询, 臂)，最左 `QUERY` 格，右侧 5 格是 **该臂自己的 Top-5** | 每格三行：`#名次 id`、`V/N/Q 族名`（或 `landform (unlabelled)`）、`d=… [REL]`；E0 格另有 `th±° s起点头`，E2 格另有 `查询事件数/候选事件数 ev`。**绿实心环 = E0 实际拟合到的姿态**；**红虚线环 = 标准器坐标里的查询本身，没做任何变换**（E1/E2/E3 不返回逐候选姿态，画成"已对齐"就是造假） | 不是"匹配成功"的清单；是这一库里最接近的候选。REL 只表示标签认为相关。无人核验过任何一格（盲评在 D）。**逐条语义与检查顺序见 §13.5.1** |
| `perception_comparison_overview_<setting>.png`（3 张） | 顶部一张由 `metrics.json` **打印** 的汇总表（hit@1/3/5、mrr、ap、ndcg@10、sec/query + 槽位构成 + Top1-lookalike），下面每个查询 × 每臂的 **Top-1** 叠加图 | 先看表再看图；表里的数字与 notes.md 必须一字不差，因为两者都由脚本打印 | 表是标签口径；图上的 Top-1 可能正是 §13.4 的 `x` |
| `control_gallery.png` | 数据集构建器的 22×14 全测试集一览（"非真实地理景观"标注） | 确认查询/变体/负例长什么样 | **不属于 C**：C 只读它，不重画（见 §13.7） |

### 13.5.1 结果图像怎么读（行 / 列 / 颜色 / 叠加环 / 人工检查清单）

图只读 `results.csv`：`load_results` 按 `(setting, arm, query_id)` 取行并按 `rank` 升序排，
轮廓取自 `gallery.jsonl` 与真实索引文件，**可视化脚本不重新打分、不手工挑图**——所以图上的
排序与旁边的表不可能各说一套（[`scripts/visualize_perceptual.py:254`](../../scripts/visualize_perceptual.py)）。
下面每个十六进制值都是脚本里的常量，核对以命令为准，不要信本节的转述：

```bash
grep -nE '"#[0-9a-f]{6}"' scripts/visualize_perceptual.py scripts/visualize_retrieval.py
```

**A. `comparison_<族>_<设置>.png`（21 张 = 7 结构族 × T1/T2/T3，发布产物用 `--top 5`）**

| 轴 | 是什么 |
|----|--------|
| **行** | **(查询 × 臂)**：同一个查询连续占 **4 行**，顺序固定 `E0 → E1 → E2 → E3`（`METHOD_COLUMNS`），行左缘的小字 `E0/E1/E2/E3` 标明"这一行由哪个臂打分"。行数 = 该族查询数 × 4：`band` / `blob` / `forked` / `star` 各 3 查询 → 12 行，`crescent` / `heart` / `irregular` 各 2 查询 → 8 行（18 查询来自 `labels.json → queries`） |
| **列** | **1 + Top-5 = 6 列**：第 1 列是查询本身（标题 `QUERY <id>`，id 截 22 字符），第 2–6 列是 **该行那个臂自己的 Top-1…Top-5**，按 **该臂自己的名次** 排，绝不跨臂按分数重排 |
| 空格 | `ax.axis("off")`：该臂在那个名次没有返回可画的候选（或 id 不在已加载的轮廓里）。**不会**用更好看的形状补位 |
| 标题 | `Controlled Shape Retrieval — 非真实地理景观`（T1/T2）或 `Real Landform Retrieval — Overture Maps`（T3），第二行 `family '<族>' · setting <?> · Top-5 of E0 E1 E2 E3 against the same query contour` |

每个查询的那四行（overview 图里是四列）就是四个方案：**E0 = 产品基线**（默认检索链路，本轮行为未变）、
**E1 = ShapeComp-inspired 特征**、**E2 = 显著结构匹配**、**E3 = Shape Context**；四臂在同一冻结
图库、同一 128 点弧长重采样环上打分，全库、无粗筛窗口。

| 图上元素 | 值 | 含义（以及它不代表什么） |
|----|----|----|
| 候选 **绿** 描边 / 填充 | `#12623c` / `#d8ecdd` | 该候选在 **冻结标签** 的 relevant 集合里（`results.csv` 的 `relevant=1`） |
| 候选 **蓝** 描边 / 填充 | `#28556e` / `#7fb2d9` | 标签判 **不相关**：同族困难负例、别族、或无标签的真实地貌 |
| 查询格 红 / 浅粉 | `#a4231d` / `#f6e2e0` | 第 1 列的查询本身。注意 `control_gallery.png` 的查询用另一个红 `#a4262c`（`QUERY_LINE`），两处不是同一常量 |
| **绿色实环** | `#1b7f4b` | **只在 E0 格**：候选先按该行的 `theta_deg` / `shift` / `mirrored` 经 `apply_alignment` 真实变换，再叠加查询 → 环与形状贴住说明 E0 的对齐确实收上了 |
| **红色虚环** | `#a4231d` | **E1/E2/E3 格**（以及 E0 没给姿态时）：查询按标准器坐标原样叠加（质心在原点、单位 RMS 半径）。这三个臂 **不返回逐候选姿态**，把它们画成"已对齐"就是造假 |
| 格标题第 1 行 | `#名次 id`（id 截 20 字符） | 名次是该臂内的名次；id 可回 `results.csv` 精确定位同一行 |
| 第 2 行 | `V <族>` / `N <族>` / `Q <族>` / `landform (unlabelled)` | 候选在标签里的角色（variant / hard_negative / query）；最后一种只出现在 T3 |
| 第 3 行 | `d=0.1234 [REL]`，E0 追加 `th+12 s7`，E2 追加 `5/6ev` | `d` 是 **该臂内部无量纲代价**，不能跨臂比较，也不是相似度百分比；`REL` = 标签认为相关；`th/s` = E0 拟合到的角度与起点；`ev` = E2 的查询/候选事件数 |
| 格标题文字颜色 | 与该行边色一致 | 绿字 = 标签 REL，蓝字 = 标签非 REL，扫一眼就能分流 |
| 页脚固定三行（图上就是这段英文） | `solid green ring = the pose E0 fitted; dashed red ring = the query as standardized, no transform applied (E1/E2/E3 return no per-candidate pose).` / `V=variant N=hard negative Q=query role; REL = the frozen label's relevant set. d is a dimensionless internal distance, never a similarity percentage.` / `these are the closest candidates in this library, not verified matches; no human judged any panel here (blind review is Checkpoint D).` | 三行分别是：环的口径、字母与 `d` 的口径、"最接近 ≠ 已验证匹配 + 无人核验" |
| 页脚第四行（有则显） | `{N} non-relevant candidate(s) in these libraries are align-indistinguishable from the query, so a cell can look right and still count as a miss.` | N 来自 `metrics.json` 里 **E0** 的 `align_indistinguishable_non_relevant` 按该族查询求和，**不是**画出来的格子数 → 它提醒"可能看着对、计分是 miss"，不指认哪一格 |

```bash
grep -nA3 "solid green ring" scripts/visualize_perceptual.py     # 页脚原文
```

图里 **没有** `x` 符号：`x`（标签 lookalike）只出现在 §13.2 由脚本打印的 Top-1 表里。

**B. `perception_comparison_overview_<设置>.png`（3 张）**

- 上半部是一张 **从 `metrics.json` 打印** 的表（不是重算也不是手抄）：每臂
  `hit@1 hit@3 hit@5 mrr ap ndcg@10 sec/query`，再加每臂 Top-k 槽位构成
  `hard-neg / other-fam / landform / Top1-lookalike`。**表里没有 `hit@10` 和"首个 REL 名次"**，
  那两项只在 §13.2 的打印表里。
- 下半部：18 个查询 × 四臂的 **Top-1**，为了可读性拆成 2 块（每块 9 行 × 5 格 = 查询 + 4 臂），
  合计 90 格；每格与 A 的候选格同一套颜色和环语义，标题多前缀 `E? ·`。

**C. `control_gallery.png`（数据集构建器产物，不属于 C）**

按 **角色** 上色，不是按 relevant：`variant` 蓝、`hard_negative` 琥珀 `#8a5a00` / `#f7e3bd`、
`query` 红。它回答"这些形状长什么样"，不回答任何检索质量。

**D. 拿这些图做人工检查的顺序（本轮交付的目的）**

1. E0 行：绿实环与候选轮廓是否贴合 —— 分离说明对齐没收上，不是距离算错。
2. 每行 Top-1（第 2 列）：绿 or 蓝。蓝 **且明显同形** → §13.4 的标签陷阱；蓝 **且确实异形** → 真实排序失误，这两类要分开记账。
3. 同一查询的四行横向对比：哪个臂把真变体抬到前面、哪个把 `N <同族>` 抬到前面。
4. T3 图里的 `landform (unlabelled)`：不是假阳性（无相关性标签，池塘不可能是星的变体）。
5. 空格：该臂确实没返回候选，与"返回了但排不进 Top-5"是两件事。
6. T1 图的 E2 行：找 self-rank 掉到 4 的那个查询 —— 本轮唯一未解释的实现异常（留给 D）。

**E. 这些图不能读成什么**：没有任何人工评分，因此 **不是** "人眼匹配准确率"；一格绿不代表
匹配被验证过，一格蓝不代表眼睛看出它不像；跨臂只能比 **名次**，不能比 `d`；`sec/query`
印在 overview 表里，是本机墙钟，不可跨机复现；`control_gallery.png` 未被 C 重画（§13.7）。

### 13.6 本轮修掉的三个真实缺陷（都不是排版问题）

1. **`relevant` 列的索引口径**：`score_library` 的 `flags` 与 **排名位置** 对齐，不与库行对齐。
   按库行索引会把 REL 标到错误的候选上并静默污染相关性列。已修
   （[`scripts/evaluate_perceptual_methods.py:474`](../../scripts/evaluate_perceptual_methods.py)）。
2. **`align_indistinguishable_from_query` 在 E0 自己的行上全空**：
   E0 的 lookalike 集合原本在该行写出之后才构造 → 只有后跑的臂带标记
   （缺陷期产物实测：T2/T3 各 E1 11 / E2 8 / E3 11 行有标记，**E0 一行都没有**）。
   修法是 E0 先跑、集合先建再写行（`arm_order`），回归测试
   `test_the_lookalike_column_is_filled_for_e0_whatever_arms_are_asked_for`
   （在修复前的提交上 **确实失败**）。修复后的正式产物：T2 与 T3 各 **E0 11 / E1 11 / E2 8 /
   E3 11** 行被标记，四臂一致地反映了同一批候选；T1 全臂为 0，因为族内库里没有任何
   落在 0.02 内的非相关行（表里 `mean-indist` 那一列 T1 就是 0.0）。
3. **Top-1 表的列宽碰撞**：T3 的 Top-1 常是 36 字符的地貌 feature id，固定 27 列宽会让两臂
   粘连（`…4ce4e996E2 - …`）。列宽改为按最长 id 动态计算，并加了对齐测试。

顺序值得记一句：发现缺陷 2 时已经有一份完整产物，但那份 CSV 的 E0 行是错的，
所以 **整个正式运行被作废重跑**（评分本身不变，标签解释列变），产物只保留最后一次运行。

### 13.7 `control_gallery.png` 的所有权（一个静默改字节的坑）

可视化脚本原本无条件重画 `control_gallery.png`，结果把构建器的产物换成了 **不同字节**
（`585ffb28…` → `e294997d…`）。根因不是随机性：已发布的 `gallery.jsonl` 里轮廓四舍五入到
6 位小数，重画用的是这份有损数据。**已恢复原文件**，并把重画改成显式选项
（`--with-control` / `--control-only`），C 的默认命令序列不再有权改动数据集产物。
教训：谁生成一个二进制，只有它能重生成那个二进制；下游脚本默认只读。

### 13.8 本轮 **不允许** 的主张

1. 无人工评分 → 任何 `hit@k`/`mrr`/`ap`/`ndcg` **都不是**"人眼匹配准确率"。
2. E1 是 ShapeComp-inspired 自实现，不得称原始 ShapeComp，也不得把它的近邻当人类相似度真值。
3. E1 距离只在 179 成员语料拟合的尺度内可比，不得与 B 的自拟合数字比大小。
4. T3 的地貌候选无标签：地貌进 Top-k **不是假阳性**，只能说变体被挤出前列。
5. `sec/q` 是本机墙钟；可复现的是分数与排名，不是时延。
6. E2 的 `exact_target_self_rank` 有 1 个查询 rank 4，**未解释** → 不得写"四臂通过自测"。
7. 未做 P0-5 扰动强度分级曲线，未做真人手绘（≥10 属 P0-6，未获授权），未做 Web 产品开发。
8. 未重建/扩容 `data/index`（仍 `77b083c9`）；T3 的混库只发生在评价脚本内。

### 13.9 复现（三条命令，顺序有意义）

```bash
python scripts/prepare_perceptual_dataset.py --offline --force          # 数据集（构建器产物）
python scripts/evaluate_perceptual_methods.py --top 10                  # 四臂 × T1/T2/T3，约 21 分钟
python scripts/visualize_perceptual.py --settings all --top 5           # 24 张图 + figures_manifest
python experiments/p0_perceptual_probe/report_checkpoint_c.py           # 本节所有表格
python experiments/p0_perceptual_probe/report_checkpoint_c.py --files   # 产物哈希与字节数
```

前置：`gallery.jsonl` / `labels.json` 必须与 §13.1 表头里的两个 sha256 一致，
否则 runner 会以 `FrozenInputError` 退出而不是静默换库。门：`pytest -o addopts="" -q` /
`ruff check .` / `mypy`。
