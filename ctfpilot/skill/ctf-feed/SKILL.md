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

gh 源鉴权：适配器自动执行 `gh auth token` 复用登录令牌（本机 5000 请求/小时）。**不要用 `gh auth status` 判断登录**——本机 hosts 配置缺失时它会误报未登录，但 token 实际可用（2026-11-16 实测 rate_limit 5000、剩余 4972）。

## 核心流程

```
每 N 分钟（一次触发 = 一轮）：
  1. bench-pull --source gh-org:CTF-Archives --dest <落题目录>  → 拉一道未见题（一轮只拉一道）
     （或 --source local:<题池> 用本地池；gh:<owner>/<repo> 指定单仓库）
  2. solve <本轮新题> --mode auto --benchmark --json --write-trace（带 300 秒墙钟上限）
  3. 轨迹自动落库（_ctfpilot_result.json + _ctfpilot_trace.json + SQLite）
  4. 解出则 learn-writeup 入库；未解/超时写 _feed_runs.jsonl
  5. 模型通道补位解出或踩了可测死路时：account-model <题目录> 入账（同名同 flag 自动判重跳过）
  6. 本轮结束。上一轮遗留的未解题不要自动重跑（ledger 已判重，重跑只烧时间）
```

## 每轮执行纪律（不要做）

定时触发时只执行「拉一道 → 解一道 → 落库」，然后结束。以下动作明确不做：

- **不查调度器**：不要每轮 `ais schedule list` / 校验任务是否存在 / 重复创建任务。定时任务由外部调度器负责，创建一次即可；重复校验只会拖慢每轮。
- **不校验 gh 登录**：`gh auth status` 在本机会误报未登录；token 由适配器自动复用，只有 bench-pull 返回码 4 时才排查。
- **不重跑历史题**：ledger + 跨源索引已拦重复；只解本轮新拉的那一道。
- **不连环拉题**：一轮只拉一道，避免堆积未解队列。

## 命令

| 步骤 | 命令 |
|---|---|
| 拉题 | `ctfpilot.py bench-pull --source <local:目录\|gh-org:组织\|gh:owner/repo> --dest <目录> [--tag <标记>]`（旧写法 `--pool <目录>` 等价 local:） |
| 解题 | `ctfpilot.py solve <题目目录> --mode auto --benchmark --json --write-trace` |
| 学习 | `ctfpilot.py learn-writeup <writeup.md> --name <题名>`（同 md5 二次学习自动 skip，不堆卡不插行；2026-11-17 起真学习末尾自动 rebuild-knowledge，新卡即时进 cases.jsonl，无需人工补 rebuild） |
| 模型入账 | `ctfpilot.py account-model <题目录> --flag <F> --time-s <秒> [--category C] [--summary S] [--dead-end 工具:秒[:备注]]`（模型通道解出/死路写进可统计轨迹；否则 RSI/arena 会把已解出的题当满罚超时；侧车 _ctfpilot_model_result.json 自动同步；同名同 flag 判重 rc=3） |

## 题源约定（2026-11-14 起支持 GitHub 源）

**源的三种形态**（--source 参数，可混用；gh 源判重为双层：ledger keys + 跨源索引，见下）：

1. `gh-org:CTF-Archives`（默认推荐）：CTF-Archives 是 GitHub 组织，每场比赛一个仓库（2023-zjsdxs、2024-qwbs8、网鼎杯等）。适配器随机挑一个年份命名的仓库，再随机挑题（比赛仓库根目录平铺 `分类-题名` 子目录；单题仓库如 `2022-xhlj-web-xxx` 整仓即题）。
2. `gh:<owner>/<repo>`：指定任意一个 GitHub 题库仓库 —— 这是**自主找题的接入点**：agent 发现新题库（GitHub 搜索 recent CTF archives、新比赛归档、其他组织）后直接以该形式接入，**零代码改动**。
3. `local:<目录>`：本地题池，每题一个子目录（子目录名 = 题名）。旧写法 `--pool` 等价。

**流量全部走 api.github.com**（本机验证稳定；github.com 本体间歇抖动），优先复用 gh CLI token（本机已登录，5000 请求/小时；用 `gh auth token` 验证而非 `gh auth status`），组织仓库清单缓存 24h，单次拉题约 2-4 个 API 调用。

**哑弹现实**（不过滤，如实计入基准）：部分归档仓库只有 docker 配置/平台依赖件而无题目附件（实测 2022-ccbgx-pwn-easy_sql-service 只有 2 个部署文件）。这类拉题会自然解题失败并被对抗时钟如实计为未解 —— 基准必须反映真实题源分布，不得人为美化。按下方「单题时限与 dud 处理」直接跳过或限时放弃，并记入 `<dest>/_feed_runs.jsonl`，不重复消耗。

**自主找题协议**（当源耗尽/陈旧时）：
- 用 GitHub 搜索找新题库（关键词如 `CTF archives in:name`、`topic:ctf`、当年赛事名），验证结构（比赛子目录或单题仓）后用 `gh:<owner>/<repo>` 接入
- 新组织用 `gh-org:<org>` 直接拉（年份仓命名约定自动过滤）
- 把发现记录到会话日志，避免重复探索

## 防泄漏与判重纪律

- `bench-pull` 自动排除 `__pycache__` / `_ctfpilot*` / `.temp`（引擎痕迹不带进新题）
- 重复防护是**三层**的：ledger 拦本 feed 自己拉过的题；跨源索引拦"其他体系已做过的题"；gh 源的索引判重自 2026-11-16 晚（evolve-16）起在**下载前**执行（名字层 + git blob 指纹层，命中即零字节下载），附件 md5/zip 折叠指纹仍守下载后（详见下节）
- learn-writeup 学同一 writeup（md5 相同）第二次时直接 `skip=duplicate` 返回 0，不写卡不插行
- solve 对已见题名打印 `[solve] note: processed before (id=...)`（兜底可见性，不阻断）
- 池子空/源耗尽/连续 8 个候选全重复返回码 3，脚本据此停或换源；源硬错误返回码 4
- GitHub 拉的题附件即题目本体，无引擎文件混入

## Ledger 与跨源索引（双层判重，断电安全）

**第一层 `_bench_ledger.json`**（`--dest` 目录内）：`{"pulled": N, "remaining": M, "history": [题名(截尾200)], "tag": …, "keys": [判重键]}`
- 每拉一题追加更新，断电后重跑不会重复；keys 用 `owner/repo::路径`（本地源用目录名）
- 只拦"本 feed 自己拉过的题"

**第二层 `memory/_traj_index.json`**（2026-11-16 起，`core/dedup.py` 自动构建，缺失时自播种，首次 build 幂等）：
- 归并五种"已做过"来源（**2026-11-16 已合并**：旧 ctf-20min 全部实体已并入 ctf-feed）：`ctf-feed/done-legacy.json`（旧 20 分钟定时任务判重账本，另一账号 TA 留下，271 条）+ ctf-feed 内 `比赛__题名` 形态旧题目录（285 个）+ 工作空间根同形态散目录 + 轨迹库 challenges 表全部记录名 + `skills/learned` 学习卡名；旧工作产物（解题脚本/临时目录）在 `ctf-feed/.temp-legacy/`
- 匹配双因子：比赛词根（jksn/lanqiao/qwb/shangmicup…）+ 核心名（`Web-4.教师面板`→`教师面板`：剥分类前缀/题号/中文分级词/年份，去分隔符，lower）；同比赛 + 核心名互含 = fuzzy 命中；whole-repo 候选（`::.`）跳过 fuzzy（防 contest-root 子串误杀，evolve-11），但自 2026-11-17（evolve-20261117-06，第八洞）起对分类词剥离后的**别名 core 做 exact**：whole-repo 仓库名里中位的分类词（`2022-xhlj-crypto-myerrorlearn`→旧取 `cryptomyerrorlearn`）先剥再比，别名相等是 fuzzy 互含的严格子集不新增误杀面；实弹：同名西湖论剑题四层全漏重复拉取已被零字节拦截（同族 mycurveerrorlearn 同拦）
- bench-pull gh 源在落地前过滤（最多连试 8 个候选）：**下载前 precheck**——名字层（exact|exact-alias(whole-repo)|fuzzy(比赛/核心名)）+ git blob 指纹层（候选文件 API 自带的 blob sha 对索引，零字节下载即可拦截重发布/重拉，实弹验证 4.2-4.5s 拦截旧需整场下载的重复）；**下载后** attach-fp（内容 md5，同题异名）与 zip 折叠指纹（重打包）双保险；**空文件零身份**（evolve-20261117-14）：0 字节文件 md5/blob sha 全局同值、零信息量，三层指纹全部跳过（含 zip 内空条目与 is_dup_blobs 候选侧镜像），防止带 .gitkeep/空 __init__.py 的全新仓库被跨题零字节误杀；命中打日志 `skip=duplicate(...)`，源耗尽时 reason 携带 precheck-duplicate 证据；重复候选不记入 ledger keys（skipped_keys 仅审计，不阻断重拉）
- 索引随 learn-writeup 增量更新（note_learned）

**历史事故（勿重蹈）**：2026-11-16 前只有 ledger，其 keyspace 是 `owner/repo::path`，而轨迹库名是 `jksn2026-教师面板` 形态——两套键互不相认，667 道"做过"的题零防护；同一 writeup 还被 22 秒连学两遍堆卡 13 张（jksn-教师面板，2026-11-11）。教训：**判断"是否做过"必须查跨源索引（轨迹库/学习卡/旧 done.json），不能只看 ledger 或目录名**。

**每轮流水 `_feed_runs.jsonl`**（与 ledger 同目录）：一行一轮，记录 `{"name","status":"solved|unsolved|timeout|dud","seconds","tag","ts"}`；超时/dud 不写引擎 result JSON，以此流水审计。

## 无人值守纪律

- `--benchmark` 禁用户交互（看图改脚本化证据，失败跳过）
- `--json` 写结果文件（含 name/category/solved/flag/time_seconds/attempts/mode/provenance）
- 输出 stdout 单行 `[benchmark] name=... solved=... time=... attempts=...` 机器可解析

## 单题时限与 dud 处理

- **单题墙钟上限**：默认 300 秒（多附件/大二进制 180 秒）；超时 kill 进程树，不写 `_ctfpilot_result.json`，在 `<dest>/_feed_runs.jsonl` 追加 `{"name":...,"status":"timeout","seconds":...}` 后结束本轮
- **拉题后先看附件**：单文件 > 5MB，或总量 > 20MB 且混杂多题型（pcap/音频/EXE/图片同包），直接记 `dud`，不启动 solve
- **已知性能陷阱**：快速通道会对 wav/pcapng 等二进制逐行做 26 路移位，可能产生上万条 attempts（实测 5 分钟写 1.4 万条）；遇到这种题不要用 `--mode fast` 重试，按上限放弃

带时限执行（PowerShell，先设置 `$py`/`$eng`/`$d`/`$runs`）：

```powershell
$log = Join-Path $d '.solve.log'; $err = Join-Path $d '.solve.err'
$p = Start-Process -FilePath $py -ArgumentList @('-X','utf8',$eng,'solve',$d,'--mode','auto','--benchmark','--json','--write-trace') -PassThru -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err
if (-not $p.WaitForExit(300000)) { taskkill.exe /PID $p.Id /T /F; Add-Content $runs ('{"name":"' + (Split-Path $d -Leaf) + '","status":"timeout"}') }
```

## 定时任务集成

定时任务**只创建一次**，之后每轮触发只执行本 skill 的拉题/解题流程：

- 创建后不要每轮 `ais schedule list` 校验，也不要在任务里重复创建（会产生重复任务）；健康检查仅在用户明确要求时做一次
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
