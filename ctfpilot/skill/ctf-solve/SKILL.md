---
name: ctf-solve
description: "Solve a single CTF challenge using the SOTA dual-channel method (deterministic fastpath + model exploration with case memory), measured by the adversarial clock defined in ctf-lab. Use when the user hands you a CTF challenge and asks to solve it, recover a flag, decode something, or run the CTFPilot engine. This is Layer 1 — depends on ctf-lab for metric definitions only."
name_cn: CTF快速解题
description_cn: "双通道解题：快通道确定性检测器秒解 + 模型探索案例卡下钻。对抗时钟度量。所有解题操作的入口。"
create_source: super-agent-skill-creator
---

# CTF 快速解题（ctf-solve）

## 依赖图

```
ctf-lab (L0) ← 本 skill 引用其对抗时钟定义
ctf-solve (L1, 本skill)
  ↑ ctf-feed  (L2) — 调用本 skill 解题
  ↑ ctf-arena (L2) — 调用本 skill 跑变体
  ↑ ctf-evolve (L4) — 进化本 skill 的 harness
```

## 前置

1. **CTFPilot 工程**：`%USERPROFILE%\.local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1`
2. **Python**：`%USERPROFILE%\.local\share\TeleAgent\runtimes\python\python.exe`
3. 设 `$env:CTFPILOT_PYTHON=<python路径>`；命令在工程根目录执行

## 解题工作流（四步）

### 1. 知识预热
```powershell
& $py -X utf8 .\ctfpilot.py recall --category <题型> --query "<信号词>"
```
- 信号词 = 魔数/协议名/关键文件名/技术名词
- 案例卡带证据指针（source→写库原文）；需要细节时**下钻读原文**
- recall 为空时 grep `skills\learned\*.json`

### 2. 运行引擎
```powershell
& $py -X utf8 .\ctfpilot.py solve <题目目录> --mode auto --write-trace
```

### 3. 解析结果
- `SOLVED ... FLAG:` → 成功
- `NOT SOLVED` → 进入模型探索（深水区补位分析）
- `raw-echo` 提示 → flag 与原始文件逐字相同，不算解出
- `budget` attempt（detector=budget，如 `xor-single: fastpath brute budget 60s exhausted, 25/37 inputs scanned`）→ 快通道 brute 段（xor-single/xor-token/mz/png 等 O(文件×密钥) 检测器）的 60 秒共享预算（config `budget.fastpath_time_limit_s`）耗尽被截断，属预期行为，不是报错；正常 1-3 文件题永远到不了预算线，大附件目录（如 glibc 全家桶哑弹）不再烧 5-8 分钟墙钟。正常题完整搜索空间不受影响（截断只发生在文件边界）

### 4. 复盘落库
- 成功与否都写 writeup（按 ctfpilot-rsi 模板）
- `learn-writeup <writeup.md> --name <题名>` 入库
- 30 秒元认知四问（耗时/标签/修复/提醒归零）

## 模型探索（深水区）

引擎 NOT SOLVED 且超出边界时：

1. **卡壳先重新 recall**（题面信号 + 当前假设 + 已排除路径）
2. **四条下钻触发**：卡面缺细节 / 失败来源 / 两卡冲突 / 上一步动作失败
3. 读 `references/` 相关知识（信号类先读 iq-signal-playbook）
4. `.temp/` 装临时依赖自写脚本，每步打印可读证据
5. 卡壳 15 分钟或同策略连败 2 次 → 按 extsearch 检索

### 深水区决策速查（2026-11-16 本轮固化）

| 信号 | 先做什么 | 不要做什么 |
|---|---|---|
| 脚本含 `MULTIPLIER`/`ITERATIONS`/`>> 32`，附 `iv.txt`、`seed.txt` 缺失、密文 `.dat` | 先把 N 轮 LCG 合成为 `s' = a*s + c (mod 2^64)`；枚举低 32 位并用其余高 32 位过滤；Windows 用 C# `Parallel.For`（4 核 2^32 约 11s）；续算 key 后 SM4-CBC 解密 | 不要先上 z3/SMT：64 位乘法+Extract 约束 180s 仍 unknown；不要对密文做 Caesar/XOR 全扫 |
| SM4/AES 脚本 + `iv.txt` + 加密 `.dat` | 先恢复 key（脚本常量/LCG/硬编码），再解密；解出后 strings/HTTP JSON 找 flag | 不要先猜 key 或爆破 128 位 |
| 大文件 >2MB | 先采样/strings/文件类型，再决定是否全量 | 不要直接全量跑所有检测器（审计：2.9MB JSON 曾打满 120s） |
| pcap/pcapng | 先 `strings` 找 HTTP/JSON/flag，再上协议解析 | 不要先装重型解析器 |

- 每步打印可读证据（key、IV 回放、明文头、flag 来源）。
- 同一策略连败 2 次 → 换路径；15 分钟卡壳 → extsearch。

## 时钟边界（2026-11-16 审计口径）

- 对抗时钟在 **flag 验证通过** 时停止；writeup/learn/复盘不计入时钟。
- 未解出按 timeout 满罚；不要用“过程叙述”代替可验证证据。
- 无人值守/flag-only 输出优先，减少中间叙述与工具往返；审计显示输出格式对耗时的影响可达数倍。

## 无人值守模式

```powershell
& $py -X utf8 .\ctfpilot.py solve <题目目录> --mode auto --benchmark --json --write-trace
```
- `--benchmark`：禁用户交互，输出 `[benchmark] name=... solved=... time=...` 行
- `--json`：写 `_ctfpilot_result.json`（全字段机器可解析）
- 供 ctf-feed 定时任务与 ctf-arena 消融使用

## 对抗时钟（来自 ctf-lab）

解题完成后计算单题对抗时钟：solved 则 time，未解出则 timeout。多题平均值才可比。

## 参考（渐进披露）

- 完整 CLI 参数/返回码/题型速查/故障排查 → `ctfpilot/references/ctfpilot-cli.md`
- RSI 复盘模板/固化 SOP/元认知 checklist → `ctfpilot/references/ctfpilot-rsi.md`
- 信号题 playbook → `ctfpilot/references/iq-signal-playbook.md`
- 外部检索源决策 → `ctfpilot/references/ctfpilot-extsearch.md`
