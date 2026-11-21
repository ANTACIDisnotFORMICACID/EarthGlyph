# P0-6 真人采集 → 盲评 → Go/No-Go 评分操作手册

> 本手册覆盖从"开始招募参与者"到"产出可复现 Go/No-Go 判定"的完整闭环。
> 协议本体见 [`PROTOCOL.md`](PROTOCOL.md)（冻结）；本文件只写操作步骤，
> 不改变协议任何条款。**在拿到真实参与者数据之前，任何环节都不得用
> 合成数据替代执行；测试用的合成运行必须带 `--synthetic` 标记。**

## 0. 工具链全景

| 阶段 | 工具 | 输入 | 输出 | 状态 |
|---|---|---|---|---|
| 1 采集登记 | `scripts/collect_p06_queries.py` | 参与者导出 JSON 收件目录 | `sessions/<PXX>/…` + 匿名密钥 | ✅ 已测试 |
| 2 预检门禁 | `scripts/validate_p06_dataset.py` | `sessions/` | READY / NOT READY | ✅ 已测试 |
| 3 盲评材料 | `scripts/make_p06_judgement_sheets.py` | READY 数据集 + `data/index` | 盲评图 + 评委/自评 CSV + refusals + manifest | ✅ 已测试 |
| 4 评分回收 | （人工填写 CSV） | 各 `sheet_*.csv` | 填好 verdict 的 CSV | ⏳ 等真人 |
| 5 汇总判定 | `scripts/score_p06_judgements.py` | `sessions/` + `judgement/` | `scoring_report.json` + Go/No-Go | ✅ 已测试（合成） |

阶段 1–3、5 均已由 `tests/test_p06_collection.py`、
`tests/test_p06_judgement_sheets.py`、`tests/test_p06_scoring.py` 端到端覆盖
（16 条评分测试全部为 SYNTHETIC TEST）。

## 1. 需要用户组织的真人环节（无法自动化）

1. **≥10 名参与者**，每人按 `templates/participant_instructions.md` 用
   `tools/sketch_pad.html` 画 **≥6 幅最终版**（文件名带 `-final-` 或勾选
   final）；导出 JSON 放入收件目录，**不要改名**。
2. **≥2 名独立评委**（未见过绘制过程，且不是相关查询的抽屉本人）：
   每人拿到自己的 `sheet_judge_NN.csv` + 全部盲评 PNG，对每行填
   `verdict ∈ {similar, weak, not_similar}`，每查询另选 `most_similar`
   （一个选项字母或 `none`）。
3. **抽屉自评**：每名参与者拿到自己的 `sheet_self_<PXX>.csv` + 自己查询的
   盲评 PNG，按同样规则填写（评委一列的 `judge_id` 已是 `self_<PXX>`，
   不要改动）。
4. 填写完成的 CSV 原样放回 `judgement/` 目录。

## 2. 采集与评分操作序列（全部命令）

```bash
# ① 登记（收件目录里是 <代号>-final-*.json 等导出）
python scripts/collect_p06_queries.py --inbox exports/incoming

# ② 预检（必须 READY 才能生成盲评材料；重复运行是安全的）
python scripts/validate_p06_dataset.py

# ③ 生成盲评材料（冻结生产参数检索一次；含每评委乱序 CSV + 每参与者自评 CSV）
python scripts/make_p06_judgement_sheets.py \
    --sessions-root experiments/p0_6_human_queries/sessions \
    --index data/index --judges 2

#    → 把 judgement/sheets/*.png 分发给评委与对应参与者；
#      对 refusal（提取拒绝）的查询，向该参与者转达"重画请求"：
#      重画后重新走 ①②③（重画版本是新查询，旧拒绝版自然留在 refusals 里）

# ④ （人工环节）回收填好的 sheet_*.csv，放回 judgement/

# ⑤ 汇总判定（输出 judgement/scoring_report.json；退出码 0 ⇔ GO）
python scripts/score_p06_judgements.py \
    --sessions-root experiments/p0_6_human_queries/sessions \
    --judgement-dir experiments/p0_6_human_queries/judgement
```

演练/测试时加 `--synthetic`（输出会打出 `SYNTHETIC TEST` 横幅并写入报告），
阈值可临时降低：生成器与评分器都接受 `--min-participants/--min-finals`
（仅供演练，其余检查从不放宽）。

## 3. 冻结主判据与分母口径（重要）

**Go = 严格多数（>50%）**：抽屉自评 Top-10 中存在 `similar` 的
**登记最终查询**占比 > 0.5，且门禁（≥10 人 × ≥6 幅最终版）通过。

协议 §6 的分母措辞（"全部有效最终查询……实际以登记数为准"）在
提取拒绝与漏评存在歧义，评分器**同时报告三种口径**并取最严格者定判：

| 口径 | 分母 | 拒绝/漏评的处理 |
|---|---|---|
| `registered_finals`（判定口径） | 全部登记的最终查询 | 提取拒绝与漏评都算不成功 |
| `sheeted` | 通过提取、生成了盲评单的查询 | 拒绝出局；漏评仍算不成功 |
| `complete_data` | 自评完整覆盖的已出单查询 | 拒绝与漏评都出局 |

三个口径单调不减（`registered_finals ≤ sheeted ≤ complete_data`），
因此最严口径过线即全部口径过线；若最严口径 ≤50% 而宽松口径 >50%，
判 NO-GO 并在报告中并列展示分歧证据，**绝不静默改用有利分母**。

## 4. 评分器拒绝的硬错误（exit 1，verdict=INVALID）

- CSV 表头/列数不符；一张表混入多个评分者；文件名与评分者不符。
- 重复评分（同一评分者对同一 (query, option) 两行）。
- 不存在的 query_id（含给拒绝查询评分、给练习稿评分）。
- 非法枚举：verdict ∉ {similar, weak, not_similar}；
  most_similar ∉ 选项 ∪ {none}。
- 身份错位：`self_PXX` 评了别人的查询。
- `key_options.json` 与生成时 sha256 不符，或同一查询两个选项指向同一候选
  （判定无法唯一回溯到候选）。
- 生成盲评单之后数据集又变了（登记数/集合不一致）→ 必须重新生成材料再评。

数据质量问题（不算硬错误，但计入报告）：评委漏评（按共同对计算一致率）、
自评覆盖不全、most_similar 与自身 verdict 矛盾。

## 5. 隐私红线（照 D92，逐条可执行）

- `sessions/` 整目录已在 `.gitignore`：参与者文件、代号→PXX 映射、
  A–J→候选映射**永不出现在提交里**；提交前 `git status` 复核。
- `scoring_report.json` 只含 PXX/query_id/聚合数（评分器已测试保证不含
  candidate_id 与密钥内容）；`judgement/` 下其余材料不含候选身份。
- 对外发布只允许：匿名化查询集、聚合统计、盲评材料（参与者知情同意范围内）。

## 6. 已知缺口（采集开始前需要注意）

- **§2.3 的"题目记入 session manifest"未实现**：collector 目前不记录
  每查询的指定水体题目，因此 §6 的次要判据"归因分布（A/B/C/D）"在
  无目标登记时无法计算。评分器已预留 `--targets targets.csv`
  （query_id,candidate_id）入口：采集时若希望出归因，需在采集中同步
  记录题目并在评分前人工核对目标是否在索引内；不提供时评分器明确输出
  `attribution: not_computed`（而不是假装算过）。
- **协议 §6 的拒绝后重画流程**：拒绝查询会触发"重画请求"，重画产生的
  新查询须重新走 ①→③；评分器会以"数据集已变"硬错误阻止在旧材料上
  评分，这是设计行为。
