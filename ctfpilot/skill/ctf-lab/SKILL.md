---
name: ctf-lab
description: "Evaluation science for CTF solver: define the adversarial clock metric, run leak/poison/holdout probes to verify that metrics honestly reflect the first-principles goal (fastest correct solve on unseen challenges), and audit whether evaluation methods themselves are trustworthy. Use when the user asks to 检查评测真实性/测试指标是否靠谱/跑leak或poison探针/生成留出集/审计评测方法/做评测方法消融. This is Layer 0 (no dependencies) — all other ctf-* skills import metric definitions from here."
name_cn: CTF评测科学
description_cn: "定义对抗时钟指标、跑泄漏/投毒/留出探针验证评测真实性、审计评测方法本身是否可信。所有其他 ctf-* skill 从这里引用指标定义。"
create_source: super-agent-skill-creator
---

# CTF 评测科学（ctf-lab）

## 依赖图

```
ctf-lab (L0, 本skill) ← 无上游
  ↑ ctf-solve (L1)  — 引用对抗时钟定义
  ↑ ctf-arena (L2)  — 引用探针与指标
  ↑ ctf-feed  (L2)  — 落库字段引用指标口径
  ↑ ctf-research(L3) — 用探针验证新模块是否有效
  ↑ ctf-evolve (L4) — 用指标做进化门控
```

## 第一性目标与对抗时钟

**绝对目标：做一道从未见过的 CTF 题，用时最短解出正确答案。**

对抗时钟公式（唯一定义在此，其他 skill 不得重定义）：

```
adversarial_time = mean(time | solved) × solved_rate + timeout × (1 − solved_rate)
```

理由：慢而解不出比快而解不出更差；快而解不出最差（最优但未达标）。
solved_rate = N_solved / N_total；time 单位秒；timeout = 该变体的超时上限。

## 三个真实性探针（`ctfpilot/lab/probes.py`）

| 命令 | 用途 | 判定阈值 |
|---|---|---|
| `probes.py leak --cases <cases.jsonl> --writeups <写库>` | 卡间互命中率（背题风险）+ 悬空指针率 | 互命中率与零假设差 <2pp 为安全；悬空指针 >0 即修 |
| `probes.py poison --engine <根> --cases <cases.jsonl>` | 注入 5 张毒卡看指标是否降 | top1 或 top3 降 <2pp = **ALARM** |
| `probes.py holdout-split --writeups <写库> [--n 20]` | 固定 seed 抽留出集 | 已在库中 = "已污染"警告；配套隔离见下「留出隔离协议」 |
| `probes.py report --engine <根>` | 连引擎 eval-retrieval 出体检 | top1/top3 降超 5pp = FAIL |

## 留出隔离协议（2026-11-14 落地）

留出集只是清单不够，rebuild 必须能真隔离，否则评测仍在自证：

1. `probes.py holdout-split` 生成 `lab/holdout.json`（固定 seed，可复现）
2. `ctfpilot.py rebuild-knowledge <写库> --exclude lab/holdout.json`
   - 写入 `skills/knowledge-holdout/`（**产线 skills/knowledge 与生产召回库一律不动**）
   - 留出项既不进卡片也不进 lessons；lessons 落隔离库 `<db>.holdout`（CTFPILOT_DB 已设则沿用）
   - 策展 overlay（精化 signals、人工裁决 category）从产线 cases.jsonl 继承后再过滤
3. arena/solve-holdout 跑隔离构建：快照引擎后用 knowledge-holdout 覆盖 skills/knowledge，CTFPILOT_DB 指向 .holdout 库
4. 生产库永远全量（无 --exclude 重建），不留出不丢知识

## 审计结论存档（2026-11-14）

- **自证泄漏已修**：eval 查询去除同源附件/载体行（lab 泄漏探针：carrier 覆盖中位 0.4+ → 0.09，status ok）；探针与引擎同口径（import 引擎 `_eval_query_text`，镜像已同步）
- **真实基线**：top1 62.5% / top3 81.2% / MRR 0.712 / evaluable 16（旧口径 75.0/89.3/0.817/28 为同源回声虚高，12 条查询实为载体自证）；信号打分 vs 纯新近基线仍压倒性（62.5% vs 0.0%），精化 signals 有真实检索力
- **9 张 medium 卡人工裁决落地**（misc×4 / forensics×3 / reverse×1 / crypto×1），回归 0 漂移；merge_refined 布尔字段 bug 已修（category_inferred=False 此前会被 rebuild 冲回）

## 新指标上线三问（写进 `lab/eval_audit.md`）

1. **与第一性目标的相关性论证**：该指标为什么能映射到"最快解出未见题"？
2. **对已知污染的敏感性验证**：注入已知毒数据后该指标是否正确响应？
3. **与既有指标的冗余度**：与对抗时钟/top1/top3/solved率的相关系数；高度相关的只留一个。

## 当前指标审计表

| 指标 | 映射第一性 | 层级 | 状态 |
|---|---|---|---|
| adversarial_time | **直接映射** | 目标 | 唯一真指标 |
| top1/top3 (retrieval) | 代理指标 | 路径 | 需 arena 联动验证相关性 |
| solved_rate | 必要非充分 | 门槛 | 不能独立使用 |
| MRR | 排名质量 | 信号 | 仅用于精化验收参考 |

## 评测方法消融（任务 6 的"RSI"）

用 **ctf-arena** 的变体跑同一留出集，对比不同评测协议下的指标排序是否稳定。
如果某评测协议在两次运行间排名翻转 >30%，标记不可靠，不得作为 gate。

## 引用纪律

- 对外报数字必须标注 [全文精读] / [摘要级] / [工程改造]
- 未经本域复现的不叫 SOTA，叫候选机制
- 探针结果 → `lab/last_report.json`，下次探针对比基线

## 相关文件

- 引擎侧探针：`ctf-agent/ctfpilot/lab/probes.py`（可执行）
- 指标审计协议：`ctf-agent/ctfpilot/lab/eval_audit.md`
- 设计文档：`ctf-agent/ctfpilot/lab/README.md`
