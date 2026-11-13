---
name: ctf-arena
description: "Ablation testing and knowledge-base optimization: run controlled experiments comparing harness variants and card-library versions on the same challenge set, score by adversarial clock, and keep the winner. Use when the user asks to 消融测试/比较harness/比较库版本/跑对抗时钟/优化卡片标签/留存最优库/跑ablation. This is Layer 2 — depends on ctf-solve (engine) and ctf-lab (metrics)."
name_cn: CTF消融竞技场
description_cn: "同题集 × 变体矩阵跑消融，对抗时钟排名，留存最快最优。库卡片精化与 harness 对比统一在此。"
create_source: super-agent-skill-creator
---

# CTF 消融竞技场（ctf-arena）

## 依赖图

```
ctf-lab (L0) ← 本 skill 引用对抗时钟公式与探针
ctf-solve (L1) ← 本 skill 调用引擎跑题
ctf-arena (L2, 本skill)
  ↑ ctf-research (L3) — 用本 skill 消融验证新模块
  ↑ ctf-evolve (L4) — 用本 skill 做 A/B 留存赢家
```

## 前置

同 ctf-solve。arena 组件在 `ctf-agent/ctfpilot/arena/`。

## 对抗时钟（唯一定义来自 ctf-lab）

```
adversarial_time = mean(time | solved) × solved_rate + timeout × (1 − solved_rate)
```

## Harness 变体（`arena/harness.py`）

| 变体 | 配置 | 测什么 |
|---|---|---|
| `baseline-fast` | `--mode fast` | 确定性快通道下界 |
| `baseline-auto` | `--mode auto --benchmark --json` | 完整双通道基线 |
| `no-memory` | auto + `CTFPILOT_DB=<空临时库>` | 知识库贡献（库消融） |
| `no-jit` | auto + `CTFPILOT_NO_JIT=1` | JIT 重检索贡献（引擎缺开关时标 unsupported） |

自定义变体：实现 `HarnessVariant(name, env_overrides, solve_args, pre_cmds, desc)` 并注册到 `list_variants()`。

## 运行消融

```powershell
cd ctfpilot/arena
& $py -X utf8 run_ablation.py --pool <题池> --variants baseline-fast,baseline-auto,no-memory [--out results.jsonl]
```

- 题池：每题一个子目录
- 逐行落盘 JSONL（断电安全）
- 同一变体内题目按名排序（可复现）
- 题目隔离：已测题从训练/精化输入中剔除，防"背题"
- 结果含 `variant_unsupported` 标志，引擎不支持某 env 时不失败

## 库卡片优化（吸收原 kb-refine）

精化循环 → `ctfpilot.py rebuild-knowledge` → `eval-retrieval` → 留存最优库。

**五步**：
1. **精化**：Skill++ 三句式（signals/distinct/excludes），subagent 分片 ≤20 卡/片
2. **审计**：category 与源写库核对，high 应用 / medium 人工
3. **合并**：保真度门（ascii ≥60% 或整体 ≥45%），旧档归档
4. **回归**：`eval-retrieval` 对比基线，top1/top3 降超 5pp = FAIL
5. **留存**：PASS 后 `--save-baseline` 抬基线，旧库保留可回滚

## 留存纪律

- 消融胜出的库/harness 版本标记 `winner`，写入 `arena/state/`
- 败者保留（版本化，可回滚）
- 对抗时钟不优于 incumbent 时不合并（ArchOR 教训：直接替换 −1.91pp）

## 防作弊

- 题目内容 sha256 台账（重跑默认 exit 2）
- 每（题，变体）独立快照 sqlite，题间不串味
- flag provenance：与原始文件逐字相同 = raw-echo = 拒绝计功

## 参考

- arena 设计文档 → `ctf-agent/ctfpilot/arena/README.md`
- 库精化操作手册 → `ctfpilot/references/kb-evolve-playbook.md`（精化批处理模板/保真阈值/审计 rubric）
- 论文证据链 → `ctfpilot/references/kb-evolve-papers.md`（13 篇精读+本域复现记录）
