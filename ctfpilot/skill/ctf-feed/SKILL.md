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
  1. bench-pull --source gh-org:CTF-Archives --dest <落题目录>  → 拉一道未见题
     （或 --source local:<题池> 用本地池；gh:<owner>/<repo> 指定单仓库）
  2. solve <落题目录>/<新题> --mode auto --benchmark --json --write-trace
  3. 轨迹自动落库（_ctfpilot_result.json + _ctfpilot_trace.json + SQLite）
  4. 解出则 learn-writeup 入库，未解出则记失败路径
```

## 命令

| 步骤 | 命令 |
|---|---|
| 拉题 | `ctfpilot.py bench-pull --source <local:目录\|gh-org:组织\|gh:owner/repo> --dest <目录> [--tag <标记>]`（旧写法 `--pool <目录>` 等价 local:） |
| 解题 | `ctfpilot.py solve <题目目录> --mode auto --benchmark --json --write-trace` |
| 学习 | `ctfpilot.py learn-writeup <writeup.md> --name <题名>` |

## 题源约定（2026-11-14 起支持 GitHub 源）

**源的三种形态**（--source 参数，可混用，ledger 按 `owner/repo::path` 跨源判重）：

1. `gh-org:CTF-Archives`（默认推荐）：CTF-Archives 是 GitHub 组织，每场比赛一个仓库（2023-zjsdxs、2024-qwbs8、网鼎杯等）。适配器随机挑一个年份命名的仓库，再随机挑题（比赛仓库根目录平铺 `分类-题名` 子目录；单题仓库如 `2022-xhlj-web-xxx` 整仓即题）。
2. `gh:<owner>/<repo>`：指定任意一个 GitHub 题库仓库 —— 这是**自主找题的接入点**：agent 发现新题库（GitHub 搜索 recent CTF archives、新比赛归档、其他组织）后直接以该形式接入，**零代码改动**。
3. `local:<目录>`：本地题池，每题一个子目录（子目录名 = 题名）。旧写法 `--pool` 等价。

**流量全部走 api.github.com**（本机验证稳定；github.com 本体间歇抖动），优先复用 gh CLI token（5000 请求/小时），组织仓库清单缓存 24h，单次拉题约 2-4 个 API 调用。

**哑弹现实**（不过滤，如实计入基准）：部分归档仓库只有 docker 配置/平台依赖件而无题目附件（实测 2022-ccbgx-pwn-easy_sql-service 只有 2 个部署文件）。这类拉题会自然解题失败并被对抗时钟如实计为未解 —— 基准必须反映真实题源分布，不得人为美化。

**自主找题协议**（当源耗尽/陈旧时）：
- 用 GitHub 搜索找新题库（关键词如 `CTF archives in:name`、`topic:ctf`、当年赛事名），验证结构（比赛子目录或单题仓）后用 `gh:<owner>/<repo>` 接入
- 新组织用 `gh-org:<org>` 直接拉（年份仓命名约定自动过滤）
- 把发现记录到会话日志，避免重复探索

## 防泄漏纪律

- `bench-pull` 自动排除 `__pycache__` / `_ctfpilot*` / `.temp`（引擎痕迹不带进新题）
- 已解过的题通过 ledger 判重，不重复拉
- 池子空/源耗尽返回码 3，脚本据此停或换源；源硬错误返回码 4
- GitHub 拉的题附件即题目本体，无引擎文件混入

## Ledger（断电安全）

`_bench_ledger.json`：`{"pulled": N, "remaining": M, "history": [题名(截尾200)], "keys": [判重键]}`
- 每拉一题追加更新，断电后重跑不会重复
- `keys` 跨源判重（GitHub 源用 `owner/repo::路径`，本地源用目录名）

## 无人值守纪律

- `--benchmark` 禁用户交互（看图改脚本化证据，失败跳过）
- `--json` 写结果文件（含 name/category/solved/flag/time_seconds/attempts/mode/provenance）
- 输出 stdout 单行 `[benchmark] name=... solved=... time=... attempts=...` 机器可解析

## 定时任务集成

用 TeleAgent scheduler 创建：
- 触发词：每 N 分钟 / 定时拉题
- task_prompt 示例：`用 ctf-feed skill：bench-pull --source gh-org:CTF-Archives 拉题→solve --benchmark --json→learn-writeup 落库，全流程无人值守；源耗尽时按自主找题协议换新源`
- 引擎：`%USERPROFILE%\.local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1`，Python：`%USERPROFILE%\.local\share\TeleAgent\runtimes\python\python.exe -X utf8`

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
