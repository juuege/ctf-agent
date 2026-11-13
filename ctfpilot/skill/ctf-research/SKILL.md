---
name: ctf-research
description: "Search SOTA papers and open-source projects, read them in full, reproduce their mechanisms in the CTF domain, run ablation tests to verify improvement in adversarial clock, and propose merge into the ctf-solve harness. Use when the user asks to 检索SOTA论文/精读复现/找缺失模块/验证新方法是否有效/论文消融/查最新进展并合入. This is Layer 3 — depends on ctf-arena (ablation runner) and ctf-lab (evaluation probes)."
name_cn: CTF SOTA研究
description_cn: "检索→精读→复现→消融→合入的完整流水线。用对抗时钟验证新模块是否真的快，不快不合。"
create_source: super-agent-skill-creator
---

# CTF SOTA 研究（ctf-research）

## 依赖图

```
ctf-lab (L0) ← 本 skill 用探针验证新模块的真实性
ctf-solve (L1) ← 本 skill 的产出合入其 harness
ctf-arena (L2) ← 本 skill 用其消融运行器跑验证
ctf-research (L3, 本skill)
  ↑ ctf-evolve (L4) — 从本 skill 取已验证的新模块
```

## 流水线（五步）

### 1. 检索
- arXiv API：`abs:"<关键词>"`，sortBy=submittedDate，取 20~40
- 顶会标记：看 `<arxiv:comment>` 是否含 NeurIPS/ICLR/ICML/ACL/EMNLP 2026
- 纯检索不下载，英文关键词质量最佳

### 2. 精读（全文，非摘要）
- 获取：`arxiv.org/html/<id>v1` → 失败试 `ar5iv.labs.arxiv.org/html/<id>`
- **逐条核对机制与数字**（摘要常夸大：ArchOR 摘要说 "consistent improvements"，实际 micro +1.64pp 且去仲裁回退直接 −1.91pp）
- 标注证据等级：[全文精读] / [摘要级] / [工程改造]

### 3. 复现（在本域）
- 识别论文机制与我们现状的差距（保真度审计）
- 写差距表：一致 / 简化 / 缺失 / 证伪
- 用我们自己的 121 案例库做本地 A/B（eval-retrieval 或 arena 消融）

### 4. 消融（用 ctf-arena）
```powershell
cd ctfpilot/arena
& $py -X utf8 run_ablation.py --pool <留出集> --variants baseline-auto,<new-variant> [--out results.jsonl]
```
- 新模块包装为 `HarnessVariant`（env 覆盖 / solve 参数 / pre_cmds）
- **对抗时钟不优于 incumbent 则不合入**
- 用 ctf-lab 的 `poison` 探针验证新模块对污染是否敏感

### 5. 合入（改 ctf-solve 的 harness）
- 合入前过三关：
  1. 对抗时钟在留出集上**显著更优**（非全库，防背题）
  2. ctf-lab 的 `poison` 探针**不因新模块而失灵**
  3. `selftest` 6/6 不降
- 合入后 `--save-baseline` 抬基线，记入 git commit
- 不合入的记入 backlog 并留复现数字

## 当前已知缺失模块（backlog）

| 模块 | 来源论文 | 缺失原因 | 复现状态 |
|---|---|---|---|
| 使用效果加权排序 | Zep 2611.01386 | 需真实 recall→solve 回环（solved 当代理已证伪） | 暂缓 |
| lesson 准入三探针 | FGSE 2611.14326 | 需 74 道已解题当回归集 | P1 待做 |
| 合取否决 | SKALOS 2610.34728 | 121 卡规模无收益（本地持平） | 500+ 卡再评估 |
| 语义重排 | ArchOR 2610.41909 | 无嵌入模型，纯标准库 | 需 .temp 装依赖 |
| 冲突保留 | SEMV 2609.27175 | 需案例对撞检测 | 随下次精化上 |

## 论文证据链维护

- 精读记录写入 `ctfpilot/references/kb-evolve-papers.md`
- 新增论文必须带：机制要点 / 关键数字 / 保真度判定 / 本域复现结果
- 三个月前的记录重跑一次消融确认结论仍成立

## 引用纪律

- 对外报数字：[全文精读] / [摘要级] / [工程改造] 三档标注
- 未经本域复现的不叫 SOTA，叫候选机制
- 精读发现的摘要夸大处必须如实报告
