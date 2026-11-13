# Arena · 消融基准与对抗时钟

本目录只做一件事：用同一把尺子量"从未见过的题，多快解出正确答案"，
并量化每个机制的真实贡献。不训练、不改引擎、不生产题目内容。

## 目标树（6 任务 → 组件）

| # | 目标 | 落点 |
|---|------|------|
| T1 | 正确率：新题一次答对的比例 | `baseline-auto` → `solved_rate` |
| T2 | 速度：解出前提下的最短用时 | `adversarial_time`（见下） |
| T3 | 快通道下界：确定性检测器免费拿到的分 | `baseline-fast` |
| T4 | 记忆贡献：知识/教训召回值多少 | `no-memory`（空库启动） |
| T5 | 深通道贡献：JIT 重检索是否值得 | `no-jit`（`CTFPILOT_NO_JIT=1`） |
| T6 | 可信：不背题、可复跑、断电不丢结果 | 指纹隔离 + 固定排序 + 逐行 fsync |

## 对抗时钟（adversarial clock）

```text
adversarial_time = mean(time | solved) * solved_rate + timeout * (1 - solved_rate)
```

未解出按满罚 `timeout` 计。理由：CTF 没有部分分，早到的错误答案与晚到的错误答案
同样为零；按满罚后"慢慢解出"劣于"快速解出"，而"解不出"无论多快都落在最差档，
时钟不会奖励早放弃。`mean_solved_time` 与 `timeouts` 仅作诊断，不参与排序。

## 消融矩阵

| 变体 | 求解参数 | 额外 env | 回答的问题 |
|------|----------|----------|------------|
| `baseline-fast` | `--mode fast` | — | 纯确定性检测器的下界 |
| `baseline-auto` | `--mode auto --benchmark --json` | — | 完整双通道基线 |
| `no-memory` | 同上 | `CTFPILOT_DB=<临时空库>` | 知识库/教训召回带来多少增益 |
| `no-jit` | 同上 | `CTFPILOT_NO_JIT=1` | 深通道 JIT 重检索是否值得保留 |

命名以 `no-` 开头即"关掉某能力"；每个变体只允许一个自变量，禁止叠加消融。

## 防作弊纪律（题目隔离）

1. 指纹：题目录内容 sha256，忽略引擎产物 `_ctfpilot_*`、`_extracted/`、`__pycache__/`。
2. 先记账后跑：每题首次运行前写入 `state/quarantine.jsonl`，进程崩溃也留痕。
3. 默认拒绝重跑：命中台账直接 exit 2；`--allow-quarantined` 仅用于排障，结果不得调参。
4. 训练剔除：writeups 入库、`rebuild-knowledge`、卡片精化前，必须比对台账指纹并跳过。
5. 记忆冻结：每个 (题, 变体) 从快照复制独立 sqlite，题间不串味，跑完即删。
6. 顺序固定：同一变体内题目按题名排序，变体顺序即代码内顺序，可原样复跑。

## 输出格式

- JSONL，一行一个 (题, 变体)：
  `challenge / variant / solved / flag / time_seconds / attempts / variant_unsupported /
  unsupported_reason / rc / status / time_source / degraded / category / timestamp /
  memory_mode / pool`
- 逐行 `flush + fsync`，断电最多丢最后一行；汇总在结束时从磁盘读回计算。
- 汇总表列：`n / solved / rate / mean_solved / adv_clock / timeout / unsup / degraded`。

## 接口约定

命令：

```powershell
python -X utf8 run_ablation.py --pool <池> --variants baseline-fast,baseline-auto
python -X utf8 run_ablation.py --pool <池> --variants all --out results.jsonl
```

- 池约定：每题一个子目录，子目录名即题名。
- 引擎契约：stdout 打 `[benchmark] name=.. solved=.. time=.. attempts=.. flag=..`，
  或写 `_ctfpilot_result.json`（同名字段）；两者都缺时回退解析现有
  `[result] SOLVED in ..s / FLAG: ..` 输出。
- env 开关必须被 `env_ack=` 显式列出才算生效，否则记 `variant_unsupported=true`
  （原因 `env-not-acknowledged`）；引擎报 `unsupported_env=` 则记 `env-rejected`。
  这样"开关不存在"不会被误读成"该能力无贡献"。
- 引擎暂不认识 `--benchmark/--json` 时：剥离被拒参数降级重跑一次，记
  `degraded=true` + `variant_unsupported=true`；数字可作基线参考，不能作消融结论。
- solve-loop / lab：`from harness import list_variants, run_variant, summarize, Quarantine`；
  训练前用 `Quarantine.has(challenge_digest(dir))` 拒收已评测题。

## 现状

引擎 `solve` 尚未实现 `--benchmark --json` 与 `CTFPILOT_NO_JIT`，当前
`baseline-auto / no-memory / no-jit` 走降级运行，`baseline-fast` 原生可用；接口落地后本目录无需改动，`variant_unsupported` 会自动转为 `false`。