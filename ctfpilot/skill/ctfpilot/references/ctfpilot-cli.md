# CTFPilot CLI 参考

本文件供模型在需要时加载，包含完整命令、参数、返回格式、题型判断与故障排查。

## 运行前提

- **Python 3.10+**（本机已验证 3.12）。
- 运行 `solve` 时设置 `$env:CTFPILOT_PYTHON=<python 可执行文件>`，深通道沙箱会用该解释器运行生成的代码；未设置则回退到 `python`。
- 所有命令都在 CTFPilot 工程根目录（含 `ctfpilot.py`、`config.json`）下执行。

## 命令与参数总览

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `solve <题目目录>` | 解一道题 | `--mode auto/fast/deep`、`--brain auto/llm/mock`、`--name`、`--flag-regex`、`--write-trace` |
| `learn-writeup <writeup.md> --name <题名>` | 从题解学习，生成技能卡+检查点+经验（自动解析模板字段：题型/题面特征/失败路径/标签等；卡片版本化不覆盖旧卡） | `--name` |
| `recall --category <题型>` | 检索历史经验与最近挑战（仅含带证据的 writeup 经验；空壳自动记录不进，见 fixations） | `--category` |
| `fixations [--min N]` | 列出 hits+successes ≥ N 的技术（固化环触发依据，频次来自轨迹库） | `--min`（默认 3） |
| `stats` | 统计落库数据 | — |
| `selftest` | 运行内置 6 道冒烟题（用 `.temp/selftest.sqlite` 独立记忆库，不污染生产经验） | — |

### solve 参数详解

- `--mode auto`（默认）：先快通道，未解出再进深通道。
  - `--mode fast`：只跑确定性快通道（编码链/凯撒/XOR/解压），0 次 LLM 调用，秒级。
  - `--mode deep`：只跑深通道假设-执行-观测循环，走沙箱。
- `--brain auto|mock`（兼容旧参数 `llm`）：
  - 引擎不依赖 LLM 后端（config 的 `backends` 已清空），`auto` 等同 `mock`。
  - `mock`：离线确定性策略梯（按题型的策略表），无需任何 LLM。
- `--flag-regex <正则>`：追加一个自定义 flag 匹配正则（比赛专属格式）。
- `--write-trace`：解完后在题目目录写 `_ctfpilot_trace.json`（含题型、flag、provenance、全部尝试轨迹）。**成功与未解出都会写**，失败 trace 是归因复盘的主要原料。

### 返回码

- `0`：解出 flag（或学习/检索成功）。
- `1`：`solve` 未解出；`selftest` 有未通过项。
- `2`：参数错误（目录不存在等）。

## 返回/日志解析

- 关键输出行：
  - `[result] SOLVED in Xs` / `[result] FLAG: <flag>` → 解题成功。
  - `[result] NOT SOLVED in Xs (N attempts)` → 未解出。
  - `[flagcheck] raw-echo: flag appears verbatim in original file ...` → flag 与原始题目文件逐字相同，不算解出（见“边界与合规”）。
  - `[triage] category=<题型> confidence=<概率>` → 题型判定。
  - `[fast]` / `[deep]` 前缀 → 走的通道。
  - `[llm] probe <后端> -> down/up` → LLM 后端探测结果（全 down 时自动 mock）。
- `--write-trace` 生成 `_ctfpilot_trace.json`，含每步尝试的 `detector/detail/success/flag`，适合需要轨迹留痕时使用。

## 题型判断速查（route 输出）

- 内容含 `=`, 尾部 `=` 对齐、ASCII 可打印、比例高 → b64/hex 编码链。
- 字母整体偏移、保留大小写/标点、含 `flag{`/`synt{` 之类移位 → 凯撒（ROT）。
- 单字节重复 xor 特征（固定 key 混淆） → 单字节 XOR。
- 题目自带 key/note → token-key XOR。
- 魔数 `1f 8b` → gzip；`PK\x03\x04` → zip（可能多层嵌套）。

## 边界与合规

- **默认离线沙箱**：网络命令（curl/wget/nc/ssh/Invoke-WebRequest 等）一律拒绝，保证 clean solve，不联网搜题解。
- **flag 来源门控（provenance）**：`config.json` 的 `flagcheck.require_derived`（默认 true）——与原始题目文件逐字相同的 flag 视为 raw-echo，不计成功、不写经验，日志提示 `[flagcheck] raw-echo`；题目自带的 `flag.txt`、readme 示例 flag 因此不会被误记为技术经验。遇到“答案就藏在文件里”的题可临时改 false，但此类经验不落库。
- **pwn/交互服务题**：Windows 沙箱无法跑 ELF，只产出 exp 源码与偏移计算，不实际运行。
- **web/osint**：默认只产出 payload/检索清单，不发网络包。
- flag 判定用 `config.json` 的 `flag_patterns` 正则（默认含 `flag{}`、`CTF{}`、`NSSCTF{}`、`hgame{}` 等）；可增配比赛专属格式。

## 故障排查

- 深通道未解出：先 `--mode fast` 确认快通道是否可解；再 `--brain mock` 排除 LLM 后端问题。
- 出现 raw-echo 提示：说明 flag 在原始文件里就能直接读到，引擎按设计拒绝计功；确认是否题目自带答案/示例 flag，若确属“找文件藏 flag”题型，临时把 `config.flagcheck.require_derived` 改 false，但该经验不入库。
- LLM 全 down：`auto` 会自动 mock，不需要停；要强制确定性就 `--brain mock`。
- 加比赛专属 flag：`--flag-regex` 或改 `config.json` 的 `flag_patterns`。
- 看历史：`recall --category <题型>` 看该题型学过什么技术。
- 题目为信号/音频/隐写类（引擎无对应检测器）：按 SKILL.md"引擎失败后的补位分析（深水区）"流程由模型补位；涉及图片判读时按"看图规则"打开图片交用户确认。

> AI生成
