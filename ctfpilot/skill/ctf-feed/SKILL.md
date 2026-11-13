---
name: ctf-feed
description: "Scheduled challenge feed: pull one unseen CTF challenge every N minutes, solve it with the ctf-solve engine in unattended mode, and persist the solve trajectory to the knowledge base. Use when the user asks to 定时拉题/每20分钟跑一道/自动出题循环/批量拉题落库/schedule a CTF feed. This is Layer 2 — depends on ctf-solve (solving capability)."
name_cn: CTF定时拉题
description_cn: "定时从题池拉未见题→无人值守解题→轨迹落库。每 N 分钟一道，防重、防泄漏、断电安全。"
create_source: super-agent-skill-creator
---

# CTF 定时拉题（ctf-feed）

## 依赖图

```
ctf-lab (L0) ← 本 skill 落库字段引用指标口径
ctf-solve (L1) ← 本 skill 调用其解题能力
ctf-feed (L2, 本skill)
  ↑ ctf-arena (L2) — 用拉到的题跑消融
  ↑ ctf-evolve (L4) — 用轨迹做 RSI 输入
```

## 前置

同 ctf-solve（引擎 + Python）。

## 核心流程

```
每 N 分钟：
  1. bench-pull --pool <题池> --dest <落题目录>  → 拉一道未见题
  2. solve <落题目录>/<新题> --mode auto --benchmark --json --write-trace
  3. 轨迹自动落库（_ctfpilot_result.json + _ctfpilot_trace.json + SQLite）
  4. 解出则 learn-writeup 入库，未解出则记失败路径
```

## 命令

| 步骤 | 命令 |
|---|---|
| 拉题 | `ctfpilot.py bench-pull --pool <池> --dest <目录> [--tag <标记>]` |
| 解题 | `ctfpilot.py solve <题目目录> --mode auto --benchmark --json --write-trace` |
| 学习 | `ctfpilot.py learn-writeup <writeup.md> --name <题名>` |

## 题源约定

题池目录：每题一个子目录（子目录名 = 题名）。来源可以是：
- CTF-Archives 仓库的题目
- 千里马拉的题
- 用户自己的题库文件
- ctf-research 生成的合成题

## 防泄漏纪律

- `bench-pull` 自动排除 `__pycache__` / `_ctfpilot*` / `.temp`（引擎痕迹不带进新题）
- 已解过的题通过 ledger 判重，不重复拉
- 池子空返回码 3，脚本据此停或报警

## Ledger（断电安全）

`_bench_ledger.json`：`{"pulled": N, "remaining": M, "history": [题名(截尾200)]}`
- 每拉一题追加更新，断电后重跑不会重复

## 无人值守纪律

- `--benchmark` 禁用户交互（看图改脚本化证据，失败跳过）
- `--json` 写结果文件（含 name/category/solved/flag/time_seconds/attempts/mode/provenance）
- 输出 stdout 单行 `[benchmark] name=... solved=... time=... attempts=...` 机器可解析

## 定时任务集成

用 TeleAgent scheduler 创建：
- 触发词：每 N 分钟 / 定时拉题
- task_prompt 示例：`用 ctf-feed skill：bench-pull 拉题→solve --benchmark --json→learn-writeup 落库，全流程无人值守`
- 题池路径由用户指定

## 轨迹落库字段（来自 ctf-lab 口径）

| 字段 | 来源 | 用途 |
|---|---|---|
| name | 目录名 | 题目标识 |
| solved | 引擎判定 | solved_rate 计算 |
| time_seconds | 引擎计时 | 对抗时钟分子 |
| attempts | 引擎计数 | 效率分析 |
| category | router 判定 | 分类消融 |
| provenance | flag 来源验证 | 防作弊 |

## 参考

- bench-pull 参数详解 → `ctfpilot/references/ctfpilot-cli.md` 的 bench-pull 节
- 对抗时钟公式 → `ctf-lab` skill
