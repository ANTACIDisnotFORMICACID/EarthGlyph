# Checkpoint D notes — 视觉感知评价与算法优化决策

Checkpoint C 交付了"四臂在同一冻结图库上的排名"，但它**不能**回答"人眼觉得哪个更像"，
因为 C 的图有两个毛病：不可比（只有 E0 的格子被摆到查询姿态上）、不盲（臂名、距离、
`item_id` 全印在图上）。Checkpoint D 的任务是把"更像不像"变成可判定的证据，再据此决定
E3（Shape Context）是否值得作为精排算法采用。

本文件按 D 的四个子检查点累加。**D1 = 材料**，不含任何视觉分数。
---

## D1 · 公平展示 + 可盲评材料（2026-11-07）

### D1.1 一条命令重建本目录全部材料

```bash
python scripts/blind_perceptual_review.py --figures fair,blind,blind_unaligned
```

其余参数全部用脚本默认（`--settings T2,T3 --top 5 --fair-top 3 --fair-settings T2
--dpi 170 --fair-dpi 150 --unaligned-dpi 130 --overview-dpi 120 --seed 20261107
--blind-columns 3`）。本机墙钟约 58 s，其中包含从零重扫 439 条展示姿态。
输入是**只读**的：`experiments/p0_perceptual_probe/{results.csv,labels.json,gallery.jsonl}`
与 `data/index/`；`blind_mapping.json` 的 `inputs` 里记了前两者的 sha256
（`ecf5bd45…` / `26e444ce…`），对不上就说明材料不是从 C 的那份排名画出来的。

### D1.2 文件怎么读

| 文件 | 是什么 | 用来回答 | 不能读成 |
|------|--------|----------|----------|
| `fair_{族}_t2.png` | 带标签对照页。**一行 = 一个臂**，左块是该臂 Top-3 **按存储原样**，右块是**同样那 3 个候选**经过统一展示拟合 | "展示规则是否对四臂一视同仁"（A2 的验收页） | 右块重叠好看 ≠ 该臂检索得准。拟合不属于任何臂 |
| `blind_{族}_{t2,t3}.png` | **正文盲评页**（`display_aligned` 视图）。一行 = 一个 (查询, 名次) 组：查询 + A/B/C/D 四个候选 | 视觉评分（B1 的 0–3 分） | 字母与臂的对应关系**不在图上**，见 D1.4 |
| `blind_unaligned_{族}_{t2,t3}.png` | 同一批组的 `unaligned` 参照页：候选按标准器存储原样画，不做任何拟合 | 交叉检查——两页结论不一致时，以哪页为准要事先说清 | 它不是"第二套评分"，是防止拟合本身引入偏置的对照 |
| `blind_review_{t2,t3}.png` | 单页总览（contact sheet），全部 90 组一页看 | 快速扫一遍有没有明显画错/漏格 | 分辨率不足以判读细结构，正式评分用族页 |
| `visual_review_template.csv` | **空评分表**，1 440 行 = 180 组 × 4 臂 × 2 视图，`status=pending_review` | 唯一的评分入口 | 表里任何数字都不存在，别从别处抄 |
| `blind_mapping.json` | 字母→臂/候选 id 的完整密钥 + 渲染规则 + 输入哈希 | **写完评分表之后**才打开 | 先打开它，评分就变成查表而不是判断 |
| `display_poses.json` | 展示拟合的磁盘缓存（439 条），键含两侧轮廓字节的 sha | 复现时省时间 | 不是算法输出，E0..E3 谁也没算过这些角度 |

### D1.3 评分怎么做（B1 的口径，印在每张族页页脚）

给每个字母打 0–3，只跟**同一行左边的查询**比：

- `0` 明显不同 / 关键结构缺失
- `1` 局部相似但整体不同
- `2` 整体相似，关键结构基本保留
- `3` 非常相似，只差可信的局部形变

看的是：整体轮廓、显著凸/凹角、主要结构的**个数**与**沿轮廓的排布**、局部形变是否可信。
**不按角数给分**：五角星和五瓣叉都是五个尖，但不是同一个结构。钝星与尖星可以判"非常相似"。
朝向不计分（`unaligned` 页允许脑内旋转）。同一形状可能在两个字母下出现——那是两个臂返回了
同一个候选，是事实不是印错。

每行同时填 `key_structure_preserved` / `obvious_error` / `low_confidence`，难例就标
`low_confidence`，不要硬猜；`reason` 写一句短理由。

### D1.4 盲评的完整性声明

- 图上隐藏：臂名、距离、REL 标签、候选族与角色、受控图案 vs 真实地貌来源、`item_id`
  （`syn-heart-e3` / `qd-star-variant-02` 这类 id 自己会把答案念出来）。
- 图上保留：查询码 `Q##`、候选码 `C####`、名次、字母。
- 字母由 `default_rng(20261107)` 按 (setting, query, rank) **逐组独立**置换；核验结果：
  每个设置里每臂恰好出现 90 次，单一字母最多承载某臂 35/90 → 没有"永远是 B"可反推。
- 渲染对四臂**逐字节同规则**：一个候选填色、一个查询填色、一个线宽、一个每设置共享的方形框
  （实测 T2 `±1.937`、T3 `±1.897`），`aspect="equal"`，不重采样、不平滑、不缩放。
- 候选形状未被修改：展示拟合是**纯刚体旋转**，点对距离不变（`tests/test_display_alignment.py::TestRigidOnly`）。

### D1.5 D1 能主张 / 不能主张

**能**：材料公平、可盲、可复现（种子 + 输入哈希 + 基线 commit `2b5cc966`），
且两次全新目录连跑产物字节一致。
**不能**：本目录**没有任何视觉分数**；不得把 `labels.json` 的相关性、也不得把几何距离
换算成视觉分；不得把受控图案说成真实地貌；`display_aligned` 的重叠度不是算法能力证据。

**下一步 = D2**：E0–E3 逐臂诊断 + `diagnostic_cases.png`；视觉复核（若有）一律标
`AI_visual_review`，不是真人受试者结论。

---

## D2 · 视觉复核 + 四臂归因（2026-11-07）

D2 有两条**互不依赖**的证据线：B3 的视觉评分（读像素）和 C1–C4 的算法归因（读排名与源码）。
两条线各自成立，也各自有不能越界的地界——归因表解释"为什么这个候选排在前面"，
视觉分回答"它看起来像不像"，谁也不能替谁产生数据。

### D2.1 复现命令

```bash
# 归因：读 C 的 results.csv，不重算任何排名。本机墙钟 ~8.5 min，其中 e1 268 s、rotation 探针占大头
python scripts/diagnose_perceptual_arms.py                       # 默认 --settings T2 --parts e0,e1,e2,e3,rotation
python scripts/diagnose_perceptual_arms.py --from-json           # 只按现有 json 重画图，不重算

# 视觉复核：把已写下的盲评分数与密钥对账，再聚合
python scripts/visual_review_report.py                           # 本机墙钟 ~40 s
```

两个脚本的输入都是**只读**的：`results.csv` / `labels.json` / `gallery.jsonl` / `data/index/`
以及 D1 的 `blind_mapping.json` / `visual_review_template.csv`。
C 的 `metrics.json`、`results.csv`、24 张图与 D1 的 40 个材料文件在本轮**一个字节都没改**
（`git status` 只有新增文件，无 modified）。

### D2.2 新增文件怎么读

| 文件 | 是什么 | 用来回答 | 不能读成 |
|------|--------|----------|----------|
| `arm_diagnostics.json` | C1–C4 + 材料审计 + 不变性探针的全部数字，`frozen_rankings_read_not_recomputed: true` | "为什么这个候选排在这个位置" | 里面**没有**任何视觉分；视觉分在 `visual_review_*` |
| `arm_diagnostics.json` 的 `material` 块 | 179 个图库成员按轮廓 sha256 分组的结果（11 组 / 31 成员 / 40 同形对 / 34 对未声明 / 2 个查询库里留着自己的副本） | **"这两个形状本来就是同一个吗"**——读任何 `d = 0.0` 之前的前置问题 | 它**不是**对任何臂的评价；它说明某些行没有评价价值 |
| `diagnostic_cases.png` | 四块面板，每块画的形状都被上面表格点名 | 归因的肉眼核验 | 图只是点名的那些对，不是随机抽样 |
| `visual_review_scores.csv` | 500 行 = 125 组 × 4 字母，**每格原始分**都在 | 任何汇总数字的可追溯底稿 | 它是 `AI_visual_review`，不是人 |
| `visual_review_summary.json` | 每臂均值 / NDCG@5 / 族内最优 + 口径与缺陷声明 | 汇总 | 汇总只在**已评分的那部分**上成立，见 D2.3 |
| `visual_review_examples.png` | 每臂最好与最差的一组，8 行 × 5 格 | "高分和低分各长什么样" | 只展示两端，**不是**代表性抽样（任务书禁止只展示获胜臂的案例，所以两端都画） |

### D2.3 视觉复核实际覆盖了什么（先声明范围，再给数字）

评分**先写进 `SCORES` 表、后打开 `blind_mapping.json`**；`scripts/visual_review_report.py`
把这条读序写在模块 docstring 里，并且对不上密钥就直接 `SystemExit`。
口径是 D1.3 预注册的 0–3 分，打分过程中**没有回头改过任何一格**。

- 已评：T2 全部 18 查询 × 名次 1–5 × 4 字母 = 360 格；T3 每族 1 个代表查询 = 140 格。合计 500 格。
- **未评**：`unaligned` 视图全部；T3 其余查询。模板里这些行仍是 `pending_review`，
  下面任何数字都**不覆盖**它们。

**T2（18 查询 / 360 格）**

| 臂 | Top-1 | Top-3 | Top-5 | NDCG@5 | 明显错误率 | 低置信率 |
|----|-------|-------|-------|--------|-----------|---------|
| E0 | 2.667 | 2.352 | 2.278 | 0.969 | 23.3 % | 33.3 % |
| E1 | 2.778 | 2.630 | 2.500 | 0.977 | 15.6 % | 33.3 % |
| E2 | 2.222 | 2.111 | 2.044 | 0.892 | 30.0 % | 33.3 % |
| E3 | 2.722 | 2.593 | 2.467 | 0.973 | 17.8 % | 33.3 % |

族内最优（Top-5 均分）：band E1 · blob E3 · crescent E1 · forked E1 · heart E0 · irregular E1 · star E3。

**T3（7 个代表查询 / 140 格）**

| 臂 | Top-1 | Top-3 | Top-5 | NDCG@5 | 明显错误率 | 低置信率 |
|----|-------|-------|-------|--------|-----------|---------|
| E0 | 2.143 | 2.191 | 2.200 | 0.903 | 28.6 % | 34.3 % |
| E1 | 2.000 | 2.143 | 2.171 | 0.893 | 31.4 % | 34.3 % |
| E2 | 1.571 | 1.524 | 1.629 | 0.845 | 48.6 % | 34.3 % |
| E3 | 2.429 | 2.381 | 2.314 | 0.998 | 28.6 % | 34.3 % |

### D2.4 这张表里三个会读错的地方

1. **低置信率四臂完全相同不是巧合**：一个组要么低置信要么不是，而每个臂在每组恰好占一格，
   所以这一列对臂间比较**没有任何信息量**（T2 = 30/90，T3 = 12/35）。它只说明"这批样本里
   有三分之一我判不动"，不说明谁判得更好。
2. **T3 的"族内最优"不可用**：每族只有 1 个查询、5 格，一个形状就能决定胜负
   （E2 在 band 上"最优"同时总体最差就是证据）。T2 的族表每族也只有 2–3 个查询。
   族级结论要等真人评分或更大样本。
3. **NDCG@5 在一个组全 0 分时按定义记 1.0**（没有更好的排序可供责怪），
   所以 NDCG 单独看会**抬高**一个彻底失败的臂；必须和"明显错误率"一起读。
   这条约定由 `tests/test_visual_review_report.py::test_ndcg_of_an_all_zero_group_is_pinned_to_one` 钉住。

### D2.5 定性发现（读像素读出来的，不是从分数推的）

- **心形 / 星形 / blob 三族大量组出现"四个不同候选同分"**，即 3 分饱和：图库里这些族的变体
  本来就彼此极像，盲评分不开是**材料的性质**，不是某臂的性质。这类组全部计入低置信。
- **`Q06`（三叉结构图）在 T2 与 T3 上四臂都只有 0.2–0.6 分**，`Q03`（闪电）在 T3 上处处 1 分。
  这是"库里没有该结构的对应物"，不是"某臂把它排错了"——归因见 D2.6 的 C1/C3。
- **距离换算不出视觉分，实测如此**：把每臂 T2 的 Top-1 按距离排序再看分，
  E0 从 `d=0.000000`（判 3 分）到 `d=0.410148`（判 0 分，`ph-tree-structure-fill` → `syn-band-tight`）；
  最刺眼的是 **E2 的 `ph-circle-fill`**：它的 Top-1/2/3 距离**恰好都是 `0.000000`**
  （`syn-band-loose` / `syn-blob-e2` / `syn-blob-query`），而肉眼给 Top-1 打 **0 分**——
  一个实心圆配一条松散带，在 E2 的度量里是"完全相同"，因为这批形状一个显著事件都没有（见 D2.6 C3）。
  四臂的 Pearson(距离, 分) 分别是 E0 −0.747 / E1 −0.625 / E2 −0.303 / E3 −0.560（n=18，仅 Top-1），
  方向对但强度不足以换算；且四臂量纲互不相同（E1 的 Top-1 距离最大到 1.834），
  跨臂比距离数值本身就是 C 定下的禁用项。

**D2.5 能主张**：在 500 个已评格上，E1/E3 的视觉均分高于 E0，E2 明显低于其余三臂，
且 E2 的差距在 T3（真实地貌混入）上更大。
**不能主张**：这是真人受试者结论；`unaligned` 视图下结论相同；心形/星形族上任何臂"更懂结构"
（那些组大量同分）；T3 族级排名。

下面 C1–C4 全部数字来自 `arm_diagnostics.json`，由 `diagnose_perceptual_arms.py` 读 C 的
`results.csv` 打印（`frozen_rankings_read_not_recomputed: true`），**没有重算任何排名**。

### D2.5m 材料审计：图库里有 11 组"逐位相同"的轮廓（先读，它改写 C1/C3 的一部分归因）

任务书 C1/C3 都在问"为什么某个臂把结构不同的形状判成相同"。回答之前必须先确认
**这两个形状在材料里是不是本来就是同一个形状**。`part_material` 把 179 个图库成员的轮廓
数组四舍五入到 9 位小数后取 sha256 分组，结果：

| 量 | 值 |
|---|---|
| 图库成员 | 179 |
| 逐位相同的组（轮廓 sha256） | **11 组 / 31 个成员** |
| 同一结论的第二条独立读法（`gallery.jsonl` 的 `origin_ref` 分组） | **11 组 / 31 个成员**，`geometric_and_provenance_reads_agree: true` |
| 组内两两配对 | **40 对** |
| 其中已在 `labels.json.near_duplicate_pairs` 声明 | **6 对** |
| **未声明**的相同对 | **34 对** |
| 自己的 T2 库里仍留着一条与自己逐位相同的 query | **2 个**：`qd-circle-query-01`（`qd-band-circle-01`、`qd-circle-01`）、`qd-cloud-query-01`（`qd-cloud-01`） |

两条读法互不依赖：一条把点列四舍五入到 9 位后哈希，另一条直接读生成器自己写的
`origin_ref`（QuickDraw 的 `key_id`、合成形状的配方字符串）。
它们给出**完全相同的 11 组 / 31 个成员**，所以这不是"哈希函数碰巧撞了"，
而是**同一张图被登记成多个 id** 这件事在数据里有两种独立记录。
`provenance_of_groups` 逐组给出被复用的资产：

| `origin_ref` | 成员数 | 被登记成 |
|---|---|---|
| `quickdraw:circle:5064695922819072` | 4 | band 硬负例 / blob 变体 / star 硬负例 / **blob 查询** |
| `quickdraw:circle:4511254590783488` | 3 | band / blob 变体 + star 硬负例 |
| `quickdraw:circle:5715360001032192` | 2 | band 硬负例 / blob 变体 |
| `quickdraw:circle:5881692050948096` | 2 | band 硬负例 / blob 变体 |
| `quickdraw:cloud:5007611478933504` | 3 | crescent 硬负例 / **irregular 查询** / irregular 变体 |
| `quickdraw:cloud:5006251572330496` | 2 | crescent 硬负例 / irregular 变体 |
| `quickdraw:lightning:4855432919121920` | 2 | band 查询 / band 变体（已互斥） |
| `quickdraw:moon:5783988255850496` | 2 | crescent 硬负例 ×2 |
| `quickdraw:star:5770830308966400` | 2 | star 查询 / star 变体（已互斥） |
| `quickdraw:tree:5294749034479616` | 2 | forked 查询 / forked 变体（已互斥） |
| `circle({"radius": 1.0})` | 7 | band / blob / crescent / forked / heart / irregular / star 七个族 |

11 组的全貌（族 = 组内出现过的 `family` 标签）：

| 组 | 成员 | 横跨的族 | 角色 |
|---|---|---|---|
| 1 | `qd-band-circle-01` `qd-blob-circle-01` `qd-circle-01` `qd-circle-query-01` | band / blob / **star** | query + variant + hard_negative |
| 2 | `qd-band-circle-02` `qd-blob-circle-02` | band / blob | variant + hard_negative |
| 3 | `qd-band-circle-03` `qd-blob-circle-03` | band / blob | variant + hard_negative |
| 4 | `qd-band-circle-04` `qd-blob-circle-04` `qd-circle-05` | band / blob / star | variant + hard_negative |
| 5 | `qd-cloud-01` `qd-cloud-query-01` `qd-irregular-cloud-01` | crescent / irregular | query + variant + hard_negative |
| 6 | `qd-cloud-04` `qd-irregular-cloud-05` | crescent / irregular | hard_negative |
| 7 | `qd-lightning-01` `qd-lightning-query-01` | band | query + variant（已互相排除） |
| 8 | `qd-moon-01` `qd-moon-query-01` | crescent | hard_negative（`-query-` 只是命名，它不是查询） |
| 9 | `qd-star-01` `qd-star-query-01` | star | query + variant（已互相排除） |
| 10 | `qd-tree-01` `qd-tree-query-01` | forked | query + variant（已互相排除） |
| 11 | `syn-blob-circle` `syn-circle` `syn-circle-band` `syn-circle-crescent` `syn-circle-forked` `syn-circle-heart` `syn-irregular-circle` | **band / blob / crescent / forked / heart / irregular / star（7 族）** | variant + hard_negative |

根因在生成器里，可逐行复核（不是猜测）：**组 11 的 7 个 id 全部由同一个构造器 + 同一组参数
`"circle", {"radius": 1.0}` 生成**，彼此只差 `family` 字符串——
`earthglyph/data/perceptual_gallery.py:1184`（`syn-circle` / star）、`:1220`（`syn-circle-crescent`）、
`:1251`（`syn-circle-heart`）、`:1311`（`syn-circle-forked`）、`:1331`（`syn-circle-band`）、
`:1337`（`syn-blob-circle` / blob 的 variant）、`:1363`（`syn-irregular-circle`）。
其中 **4 条连 `note` 参数都没写**（`syn-circle-heart` / `-forked` / `-band` /
`syn-irregular-circle`），即源码自己就没有声称它们内部结构不同；
而写了 `note` 的 3 条里，`syn-circle-crescent:1220` 的说明是 **"same outer arc, no inner arc"**
——**它描述的是设计意图，不是这段代码产生的形状**：同一条 `circle` 配方根本没有内弧可挖。
这一条是"受控图案的族内难度被高估"的直接证据，比任何臂的分数都靠前。
QuickDraw 侧同理：`qd-circle` / `qd-blob-circle` / `qd-band-circle` / `qd-circle-query`
复用同一批真人圆形的同一行（组 1–4），`qd-cloud` / `qd-irregular-cloud` 复用同一行（组 5–6），
于是"原本打算放进去的内部结构"从来没进过点列。

**这条审计对声明的独立性**：`near_duplicate_pairs` 的排除只在**查询自己的族内**生效。
组 7–10 的互斥对（`qd-star-01` ↔ `qd-star-query-01` 等）因此被正确剔出各自的库；
但组 1/5 里跨族的同形副本（`qd-band-circle-01`、`qd-circle-01`、`qd-cloud-01`）没有被排除——
它们的 `family` 与查询不同，就漏过了。库长的证据：18 个查询里 6 个的 T2 库长度是 177 而非 178，
其中 `qd-circle-query-01` 与 `qd-cloud-query-01` 恰好是**仍留有同形副本**的那两个。

另外 9 条声明对里有 3 条**不是**逐位相同（`ph-circle-fill~syn-blob-circle` 对齐距离 7.3e-05、
`ph-heart-fill~ph-heart-half-fill` 1.748e-03、`ph-square-fill~syn-blob-square` 记为 0.0 但原始点列不同）
——"对齐后相同"与"材料相同"是两件事，之前容易被混读。

**它改变了什么归因**（下面 C1/C3 已按此改写）：
- 凡是**跨不同 id 的 `d = 0.0`**，先查是不是同组同形。E0 表里 `qd-circle-query-01` 的两条 0.0、
  `qd-cloud-query-01` 的 1e-08，以及 `ph-circle-fill` 那 6 个 `7.283e-05` 的"跨 6 族同距离"，
  现在明确是**材料把同一条轮廓标成 6 个族的硬负例**，任何度量都不可能把它们分开。
  这是 C 遗留的**数据缺陷**，不是 E0 的盲区，也不是 E2 的。
- **没有**被这条审计解释掉的，仍然是真现象：E2 的零显著事件退化（D2.8），
  `ph-circle-fill` 的 E2 Top-1/2/3 同时 0.000000（那三个 id 分属不同组，靠的是零事件而非同形），
  以及 E0 阈值带里 0 个相关项的**计数**（带内确实全是硬负例，只是其中一部分是"同形"造成的）。

### D2.6 C1 · E0：老办法到底差在哪

E0 = P0-4 正式管线（Fourier + 闭式旋转/循环移位对齐）。T2 Top-1 相关率 **0.667**；
按"简单族"（band / blob / irregular，8 个查询）算只有 **0.375**。

它**不是**"因为老所以差"：7 个 `syn-*` 合成查询里 6 个 Top-1 判对（唯一失败的是
`syn-blob-query`，它落进 D2.8 的零事件退化），距离全部 ≤ 0.106；
视觉复核里 E0 的 Top-1 有 **15/18** 组拿 3 分。E0 的失效模式只有一种——**它只拟合外轮廓**：

| 对（E0 认为不可区分，`d ≤ near_max_align = 0.02`） | d | 候选真实身份 | 与查询同一条轮廓？ |
|---|---|---|---|
| `qd-circle-query-01 ~ qd-band-circle-01` | **0.0** | band / hard_negative | **是**（组 1） |
| `qd-circle-query-01 ~ qd-circle-01` | **0.0** | star / hard_negative | **是**（组 1） |
| `qd-cloud-query-01 ~ qd-cloud-01` | 1.0e-08 | crescent / hard_negative | **是**（组 5） |
| `syn-blob-query ~ syn-egg` | 6.3e-07 | heart / hard_negative | 否 |
| `ph-circle-fill ~ syn-circle` | **7.283e-05** | star / hard_negative | 否（但 6 行同源，组 11） |
| `ph-circle-fill ~ syn-circle-band` | **7.283e-05** | band / hard_negative | 否（同上） |
| `ph-circle-fill ~ syn-circle-crescent` | **7.283e-05** | crescent / hard_negative | 否（同上） |
| `ph-circle-fill ~ syn-circle-forked` | **7.283e-05** | forked / hard_negative | 否（同上） |
| `ph-circle-fill ~ syn-circle-heart` | **7.283e-05** | heart / hard_negative | 否（同上） |
| `ph-circle-fill ~ syn-irregular-circle` | **7.283e-05** | irregular / hard_negative | 否（同上） |
| `syn-blob-query ~ syn-irregular-ellipse` | 0.0109 | irregular / hard_negative | 否 |

这 11 行现在按 D2.5m 拆开读（`e0.same_contour_as_query / distinct_contours /
distinct_shape_comparisons / distinct_comparisons_on_a_different_contour`
= **3 / 8 / 5 / 3**）：

- **3 行（2 个比较）根本是同一条轮廓**——`qd-circle-query-01 ~ qd-band-circle-01`、
  `~ qd-circle-01`（同组 1）、`qd-cloud-query-01 ~ qd-cloud-01`（同组 5）。
  E0 返回 0.0 / 1e-08 是**算术正确**，任何度量都分不开它们，这一栏与 E0 的能力无关。
- **"6 个跨 6 族的候选距离逐位相同"** 也**不是** E0 看不出内部结构：组 11 就是**同一条轮廓**
  被登记成 6 个族的硬负例，所以 6 行必然同距离；真正关于 E0 的信息只有
  "真人画的圆 `ph-circle-fill` 与这条合成圆相差 7.283e-05"这**一个**比较——
  而 C 的 `near_duplicate_pairs` 已经把 `ph-circle-fill ~ syn-blob-circle`
  （同一条组 11 轮廓的另一个 id）记为 7.3e-05 的近重复，即**材料设计本来就当它们是同一个形状**。
- **剩下真正支持"外包络相同 / 内部结构不同"这一 C1 问题的，只有 3 个比较**：
  `syn-blob-query ~ syn-egg`（6.3e-07，被标为 heart 硬负例）、
  `syn-blob-query ~ syn-irregular-ellipse`（1.09e-02）、
  `ph-circle-fill ~ 组 11`（7.283e-05）。方向仍然成立——E0 只看外轮廓，
  挖内弧 / 收带 / 内部加叉这类**不改变外包络的内部改造**会把它骗到 d≈0——
  但**证据量是 3，不是 11**，任何"E0 大面积分不清结构"的说法都超出这批数据。

还有一个必须先承认的读错：**`count_indistinguishable_relevant = 0` 不是发现，是定义**。
`labels.json.rule` 写的是"relevant = 同族 + role=variant + 不是查询本身 + **不是查询的近重复
（对齐距离 ≤ 0.02）**"——近重复判据用的就是 E0 的对齐距离和同一个阈值 0.02，
所以"0.02 带内没有相关项"**按构造必然成立**。它唯一能证明的是 C 的标注遍历与 E0 的
打分互相一致（这值得留着当一致性检查），**不能**用来论证"E0 的距离标尺是反的"。
同样，"E0 判对的 Top-1 距离全落在 0.0253 – 0.2349"也是同一个定义的另一面，
不是标尺异常。上一版把这两句写成结论，本轮按定义撤回。

E0 仍站得住的部分：T2 Top-1 相关率 **0.667**（18 查询）、`syn-*` 合成查询 7 个里 6 个判对、
7 个 `syn-*` 全在 d ≤ 0.106 内、视觉复核 Top-1 有 15/18 组拿 3 分；
它确实失效的那一类只剩"内部结构改造"与"简单族 Top-1 仅 0.375"两处，
后者要等真实手绘样本或修正后的材料（D2.5m）才能定责。

### D2.7 C2 · E1：91 维自实现特征里，真正在决定排名的是哪几维

`ph-heart-fill` 的 Top-1 是 `qd-circle-02`（真人画的圆，`star/hard_negative`），
尖头的心形变体被排到 **57** 和 **49** 名。同一查询在 `syn-heart-query` 上表现正常
（Top-1 = `syn-heart-round`，尖头变体在 4–5 名）——**差别在查询是手绘填充形还是合成人**。
分组距离把原因摊开：

| 查询 → Top-1 | global | moments | curvature | fourier | histogram | 合计 |
|---|---|---|---|---|---|---|
| `ph-heart-fill` → `qd-circle-02` | 0.135 | 0.041 | **0.685** | 0.131 | **0.719** | 1.011 |
| `syn-heart-query` → `syn-heart-round` | 0.112 | 0.017 | 0.354 | 0.098 | 0.378 | 0.539 |

五组距离是**平方相加再开根**的（0.135²+0.041²+0.685²+0.131²+0.719² = 1.0115²），
所以看份额要看平方：`curvature` 45.8 % + `histogram` 50.5 % = **平方距离的 96.4 %**。
而这两组恰好是"圆和钝头心形最像"的组——所以 E1 把心形判成圆，**不是实现错，
是特征权重天然偏包络/直方图**。（E1 的 Top-1 虽错，第一个相关项仍在第 2 名 `syn-heart-bean`，
视觉复核也给这一格打了 3 分——它错得靠前，不是错得离谱。）

组消融（查询与图库**冻结**，只换特征列；未调任何权重）：

| 配置 | k | hit@1 | Δ | mean 1st rel |
|---|---|---|---|---|
| full | 91 | 0.611 | 0.000 | 2.56 |
| drop_moments | 85 | **0.667** | **+0.056** | 2.39 |
| drop_curvature | 73 | **0.667** | **+0.056** | 2.89 |
| drop_global | 81 | 0.611 | 0.000 | 2.28 |
| drop_histogram | 49 | 0.611 | 0.000 | 2.00 |
| drop_fourier | 76 | 0.500 | −0.111 | 2.78 |
| 单用 fourier | 15 | 0.500 | −0.111 | 2.83 |
| 单用 histogram | 42 | 0.556 | −0.056 | 2.67 |
| 单用 moments | 6 | 0.611 | 0.000 | 2.83 |
| 单用 global | 10 | 0.500 | −0.111 | 3.56 |
| 单用 curvature | 18 | **0.444** | −0.167 | 3.94 |

结论要按字面读：**去掉 `moments` 或 `curvature` 反而更好**，`fourier` 是唯一不能去的组，
`curvature` 单独用最差。这解释了"心形返回圆钝形"，也说明 E1 的问题不是"维度不够"，
而是**有几组特征在真实手绘查询上是噪声**。本表来自一次冻结消融，
没有为了测试集成绩反复调权（任务书禁止），因此 +0.056 只能读作"这两组值得重做"，
不能读作"E1 应该改成 85 维"。

归一化分布漂移（C2 最后一问）：语料拟合的 minmax 尺度用到 839 条真实地貌上，
76 349 个单元格里 **97 个低于下界 / 310 个高于上界 = 0.5331 %**，
最严重的描述子是 `curv00_w32_concave_share`（越界幅度 4.7676 %）；
在它自己拟合的 179 行语料上越界 **0 个**（16 289 格）。→ 漂移真实存在但很小，
且集中在曲率组，与上面"curvature 单独用反而最差"方向一致。

### D2.8 C3 · E2：显著结构匹配为什么没达到预期

E2 = 多尺度带符号转角显著事件 + Needleman-Wunsch 单调 DP，代价除以 2π。
常量：`DEFAULT_SCALES=(1/64,1/32,1/16,1/8,1/4)`、`SALIENCE_K=3.0`、`MIN_SEPARATION=2`、
`GAP_COST=1.0`、`DEFORMATION_BUDGET=0.02`、`PEAK_GAIN=0.25`。

**(a) T1 那个 rank 4 已定位，不是代码 bug。**
`syn-blob-query` 的自距离**恰好 0.0**，而 T1 库里**恰好 5 行同为 0.0**
（`ph-circle-fill` 行 0、`syn-blob-circle` 13、`syn-blob-e2` 14、`syn-blob-query` 15、
`syn-blob-smooth` 16），五者显著事件数**全是 0**。排名由
`np.argsort(..., kind="stable")` 的行序决定（C 定的 D42 规则），行 0 的 `ph-circle-fill` 赢。
签名本身没有区分力（空序列 vs 空序列按定义代价 0），**实现是对的，是这族形状没信号可用**。

**(b) 同一个退化在 T2 上更贵：它直接吃掉 Top-1。**
`ph-circle-fill` 的 E2 Top-1/2/3 距离**都是 0.000000**（`syn-band-loose`、`syn-blob-e2`、
`syn-blob-query`），三者显著事件数也都是 0，肉眼给 Top-1 打 **0 分**。
全库普查：1018 个形状里 **19 个（1.87 %）零事件**，事件数直方图
`{0:19, 1:23, 2:130, 3:181, 4:195, 5:128, 6:76, 7:67, 8:70, 9:52, 10:41, 11:16, 12:10, 13:6, 14:2, 15:1, 16:1}`。
零事件时 E2 的"完美匹配"是**度量失灵**，不是相似。

`zero_distance_top_hits` 把 Top-5 里所有 `d = 0.0` 逐个落地数（13 行，**必须**带着 D2.5m 读，
否则会把两类原因混成一类）：

| 查询 | 名次 | 候选（事件数 / 族 / 角色） | 与查询同一条轮廓？ |
|---|---|---|---|
| `ph-circle-fill` | 1 | `syn-band-loose` 0 / band / variant | 否 |
| `ph-circle-fill` | 2 | `syn-blob-e2` **0（相关项）** | 否 |
| `ph-circle-fill` | 3 | `syn-blob-query` 0 / blob / query | 否 |
| `ph-circle-fill` | 4 | `syn-blob-smooth` **0（相关项）** | 否 |
| `ph-circle-fill` | 5 | `syn-circle` 0 / star / hard_negative | 否 |
| `qd-circle-query-01` | 1–2 | `qd-band-circle-01`、`qd-circle-01` | **是**（组 1，两者事件数也为 0） |
| `qd-cloud-query-01` | 1 | `qd-cloud-01` | **是**（组 5） |
| `syn-blob-query` | 1–5 | `ph-circle-fill`、`syn-band-loose`、`syn-blob-circle`、`syn-blob-e2`、`syn-blob-smooth` | 否 |

计数：**13 行里 3 行是同一条轮廓**（`zero_hits_that_are_the_same_contour`，那 3 行 0.0 无信息量，
任何度量都会给 0），**10 行发生在真正不同的轮廓上**（`zero_hits_on_a_genuinely_different_contour`），
而且**这 10 行的候选事件数全为 0**——不是"13 行里 7 行相同所以没事"，
而是"剩下 10 行确实全是零事件退化"，`tests/test_arm_diagnostics.py::
test_zero_distance_split_separates_the_two_explanations` 钉住这条。
注意 `ph-circle-fill` 一行里同时出现两种失败：**Top-1 是硬负例、两个相关项被挤到 2 和 4**，
肉眼给这一组 Top-1 打 0 分——零事件不但给错误答案满分，还会把对的答案压到并列，
名次再由 D42 行序决定。

**(c) 缺失事件的惩罚确实偏小。** 实测 DP 为一个角付出的代价（除以 2π 后的距离）：

| 丢掉一个角 | 弧度 | cost | 距离 |
|---|---|---|---|
| 凸角 | 0.3 | 0.3 | 0.0477 |
| 凸角 | 0.6 | 0.6 | 0.0955 |
| 凸角 | 1.0 | 1.0 | 0.1592 |
| 凸角 | 1.6 | 1.6 | 0.2546 |
| 凸↔凹错配 | 1.0 | 2.0 | 0.3183 |

对比 `near_max_align = 0.02`：丢一个 **0.3 rad 的角 = 0.0477，已经是"不可区分"阈值的 2.4 倍**，
而 E2 的 Top-3 距离常常只有 0.379 —— 也就是说**排序里根本没有为"结构缺失"留出足够的相对差距**。

**(c-2) "删角比老实用 DP 便宜"这个怀疑，实测只成立一半。**
`deletion_vs_match` 只读 C 已存的 Top-10（不重算排名），对每个查询取
"最便宜的、丢掉了查询事件的候选"与"最便宜的、事件全配上且相关的候选"比距离：
**7 个查询可比，删角赢 2 次、输 5 次。**
赢的两次是 `ph-heart-fill`（删凹口的候选比第一个全配上的相关项便宜 **0.0201**）和
`syn-irregular-query`（便宜 **0.1534**）；输的 5 次里差距是反方向的，
所以**不能**把 C3 的失败写成"DP 系统性地偏好删除"——
正确的说法是：删除的价格**足够低到能在个别查询上翻盘**（心形正是那个查询），
而不是普遍翻盘。心形的凹口是它唯一的凹事件，删掉它的代价 0.379 与"匹配成功"的
0.03–0.08 只差一个数量级，这就够把 Top-1 换掉。

**(d) 结构丢失的实例（T2，`drift` = 对齐后位移超预算的对数）：**

| 查询（凸/凹） | 名次 | 候选 | d | 候选（凸/凹） | 反号对 | drift |
|---|---|---|---|---|---|---|
| `ph-heart-fill` 1/1 | 1 | `qd-band-circle-03` | 0.379 | 1/0 | 0 | 1 |
| `ph-heart-fill` 1/1 | 2 | `qd-blob-circle-03` | 0.379 | 1/0 | 0 | 1 |
| `ph-heart-fill` 1/1 | 3 | `syn-irregular-silted` | 0.381 | 1/0 | 0 | 1 |
| `ph-moon-fill` 2/0 | 2 | `syn-band-query` | 0.100 | 2/0 | 0 | 1 |
| `ph-moon-fill` 2/0 | 3 | `syn-band-zigzag` | 0.154 | 2/0 | 0 | 1 |

心形的**中央凹口（1 个凹事件）在 Top-3 里全部消失**，候选都只剩 1 个凸事件；
DP 仍把它们排前三。凹口消失后代价只有 0.379，与"匹配成功"的 0.03–0.08 只差一个数量级。
对照 `syn-heart-query`（合成人）Top-3 是 `syn-heart-wide/point/deep`，凸/凹 1/1 全保留、
drift 0 —— **同一套代码在合成查询上是对的**，失败集中在手绘查询的事件提取上。

**(e) 多尺度是否把重要结构滤掉了：是，而且滤得非常早。** 逐窗口的显著事件数：

| 查询 | 并集 | 1/64 | 1/32 | 1/16 | 1/8 | 1/4 |
|---|---|---|---|---|---|---|
| `ph-circle-fill` | 0 | 0 | 0 | 0 | 0 | 0 |
| `ph-heart-fill` | 2 | **2** | 0 | 0 | 0 | 0 |
| `ph-moon-fill` | 2 | 2 | 2 | 1 | 0 | 0 |
| `ph-star-fill` | 10 | **10** | 5 | 0 | 0 | 0 |
| `ph-tree-structure-fill` | 12 | 11 | 7 | 6 | 2 | 0 |

心形的 2 个事件**只在最细窗口（1/64）存在**，粗一个尺度就归零——
于是 E2 对心形的全部"结构知识"来自 128 点轮廓上 ±2 点的局部弯曲，
抖动、平滑或真实地貌的锯齿都能把它抹掉（T3 上 E2 明显错误率 **48.6 %**，四臂最高）。

### D2.9 C4 · E3 相对 E0：赢在哪、输在哪（完整对比，不只记赢）

按"第一个相关候选的名次"逐查询对比（T2，None = Top-10 内没有相关项）：

| 查询 | 族 | E0 | E3 | 方向 |
|---|---|---|---|---|
| `ph-circle-fill` | blob | None | 7 | **E3 better** |
| `ph-lightning-fill` | band | None | 9 | **E3 better** |
| `ph-tree-structure-fill` | forked | 9 | 2 | **E3 better** |
| `qd-cloud-query-01` | irregular | 5 | 3 | **E3 better** |
| `qd-circle-query-01` | blob | 4 | 5 | **E0 better** |
| `syn-blob-query` | blob | 4 | 4 | tie |
| `ph-heart-fill` / `ph-moon-fill` / `ph-star-fill` / `qd-lightning-query-01` / `qd-star-query-01` / `qd-tree-query-01` / `syn-band-query` / `syn-crescent-query` / `syn-forked-query` / `syn-heart-query` / `syn-irregular-query` / `syn-star-query` | — | 1 | 1 | tie |

**E3 better 4 / E0 better 1 / tie 13。** 族内分布：blob 1:1:1、band 1:0:2、forked 1:0:2、
irregular 1:0:1、heart 0:0:2、crescent 0:0:2、star 0:0:3。

所以 E3 的优势**不是普遍的**：它只在"E0 根本没有答案"的查询上出现（4 个赢例里 2 个 E0 是 None），
在 heart / crescent / star 三个族上**一个赢例都没有**——而这三个恰是任务书假设 E3 会赢的族。
E3 输的那一例是 `qd-circle-query-01`（真人画的圆）从 4 掉到 5。

**时间去向（实测，非估算，取自入库的 `arm_diagnostics.json`）**：`ph-circle-fill` 在 177 行库上
`orientation="sample"` 7.963 s、`canonical` 0.414 s、单个旋转步 0.424 s
→ **旋转网格占 94.67 %**，sample/canonical 比 **19.24×**，每候选 0.0450 s。
胜出旋转角分布 min 0° / median 75° / max 315°、16 个不同值——
即 24 个姿态里 E3 真的在到处找，代价几乎全花在这里。
（墙钟随机器浮动，同一项三次运行分别 7.42 / 8.28 / 7.96 s，比值相应 19.4 / 19.4 / 19.24；
可复现的是分数、排名与角度的名次极差，不是秒。文档只抄入库那一次的数字。）

### D2.10 不变性探针（同一形状转一个角度再查，名次会不会变）

9 个角度 `[0, 15, 30, 37, 45, 60, 72, 90, 180]`，18 个查询：

| 臂 | 平均名次极差 | 最大 | Top-1 改变的查询 |
|---|---|---|---|
| E0 | 0.000 | 0 | 0/18（闭式对齐，按构造不变） |
| E1 | 0.056 | 1 | 1/18（`syn-star-query`，2 个不同 Top-1） |
| E2 | 0.556 | **6** | 2/18（`ph-tree-structure-fill` 极差 6、`qd-tree-query-01` 极差 4） |
| E3 | 0.667（仅 3 个查询） | 2 | 1/3（`ph-lightning-fill` 极差 2） |

E1 的"旋转后不稳定"在**这个探针里几乎不存在**（只有星形 1 例），任务书里担心的那一条
在 T2 上不成立；真正不稳的是 **E2 的枝状/叉状查询**，与 D2.8(e) 的"事件只活在最细尺度"一致。
E3 只跑了 3 个查询（一次全库评分 8 s 级别，18 查询 × 9 角度不划算），
所以 E3 的旋转稳定性**只能算抽样，不能算结论**。

### D2.11 D2 能主张 / 不能主张

**能**：四个臂的失效模式各自定位到具体查询与具体源码常量，可复现；
E2 的 T1 rank-4 已证明是稳定排序的行序 tie-break，不是实现错误；
E3 相对 E0 的增益是**局部的**（4/18 赢、1/18 输、13/18 平），且赢例集中在 E0 完全失败的查询；
E1 的心形失败可归因到 `histogram + curvature` 两组占**平方距离** 96.4 %（D2.7）；
E2 的心形失败是"唯一的凹事件可以在 0.0201 的价格内被删掉"（D2.8 c-2，只在 7 个可比查询里的 2 个成立）；
材料审计本身（11 组同形轮廓、34 对未声明）是一个**独立于任何臂的缺陷**，可以直接进下一轮修复清单。

**不能**：不得把 `AI_visual_review` 的表说成真人实验；不得用 0.5331 % 的归一化越界
宣称"E1 在真实地貌上失效"；不得把 E3 的 ~19× 旋转开销说成可忽略；
不得把 D2.7 的消融 Δ 当作"改进后的 E1"（那是同一次冻结消融里读出来的假设，未重新训练、未重新评价）；
不得把 T3 族表、E3 的 3 查询旋转抽样当结论。

**本轮撤回的两条（上一版写成结论，现在按定义否证）**：
1. ~~"E0 的距离标尺是反的：0.02 带里 11 个候选相关项 0 个"~~ ——
   `labels.json.rule` 定义 relevant 时就排除了"对齐距离 ≤ 0.02 的近重复"，
   所以 `count_indistinguishable_relevant = 0` **按构造必然成立**，只是标注与打分的一致性检查。
2. ~~"E0 把 6 个不同族的内部结构看成一样"~~ 与 ~~"E2 有 13 个 0.0 是度量失灵"~~ 的**未拆分版本** ——
   其中 3 行（E0）/ 3 行（E2）是图库把同一条轮廓登记成多个 id，任何度量都得 0；
   E0 真正剩下的证据是 **3 个不同轮廓的比较**，E2 真正剩下的是 **10 行零事件候选**。
   拆分之后 E2 的结论**不变**（仍是度量失灵），E0 的结论**减弱**（11 → 3）。


