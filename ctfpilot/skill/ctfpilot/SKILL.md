---
name: ctfpilot
description: "Solve CTF challenges by driving the deployed CTFPilot engine (dual-channel: deterministic fastpath + deep hypothesis loop with LLM/mock planner). Use when the user hands you a CTF challenge (a challenge directory, file, or ciphertext) and asks to solve it, recover a flag, decode encoding chains (base64/hex/url/caesar/XOR/gzip/zip), learn from a writeup, recall prior solve experience, or run the bundled smoke selftest."
name_cn: CTFPilot 解题助手
description_cn: "调用本机 CTFPilot 引擎解 CTF 题：快通道秒级解编码链/凯撒/单字节与 token XOR/压缩解包，深通道走离线沙箱处理复杂题；支持题解学习、经验检索与内置自测。全程本地离线运行。"
create_source: super-agent-skill-creator
---

# CTFPilot 解题助手

## 概述

本 skill 是给大模型用的解题工作流，"双通道"定义：
1. **快通道**：CTFPilot 引擎的确定性检测器/工具链——编码链、凯撒、XOR、压缩解包类秒级解出，零模型调用。
2. **模型探索通道**：模型（你）先 `recall` + 读 `references/` 知识（含信号 playbook），再自己写脚本探索答案。

引擎的 `--mode deep --brain mock` 是可选的确定性策略梯脚手架（兜底用）；探索的大脑是模型本身，不依赖任何 LLM 后端（Ollama/网关已从 config 移除）。所有命令 UTF-8 本机离线运行，无第三方依赖。

## 前置：定位工程与 Python

1. **CTFPilot 工程目录**（含 `ctfpilot.py`、`config.json`）：
   - 默认：`%USERPROFILE%\.local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1`
   - 找不到时在"工作空间"和 `D:\` 下按 `CTFPilot*` 目录名探测。
   - 确认目录内存在 `ctfpilot.py` 后再继续。
2. **Python 3.10+**：本机用 `%USERPROFILE%\.local\share\TeleAgent\runtimes\python\python.exe`；探测 `python --version` 确认可用。
3. 运行 `solve` 前设置 `$env:CTFPILOT_PYTHON=<python 路径>`（深通道沙箱用它跑生成的代码）。
4. 所有命令在工程根目录下执行：`& $py -X utf8 .\ctfpilot.py <命令> ...`

## 单题解题工作流（核心）

1. **确认题目输入**：拿到题目目录或题目文件（编码文本、密文、压缩包等）。若只有题面文本，先在工程下建一个临时题目目录放入题面，再解题。
2. **知识预热（先查再动手）**：`recall --category <题型> --query "<信号词>"`（信号词 = 魔数/协议名/关键文件名/技术名词，按信号相关度排序命中案例卡）+ 读相关 `references/`（信号类先读 playbook）；**案例卡带证据指针（source 指向写库原文），需要细节时下钻读原文，不要凭卡面臆测**；recall 为空或太泛时，用关键词直接检索工程 `skills\learned\*.json`；已有相似解答路径时直接套用并验证，不从零开始。
3. **定位工程 + Python**（见"前置"）。
4. **运行解题**：
   ```powershell
   & $py -X utf8 .\ctfpilot.py solve <题目目录> --mode auto --write-trace
   ```
5. **解析结果**：
   - `[result] SOLVED ... FLAG: <flag>` → 成功，把 flag 回给用户。
   - `[result] NOT SOLVED ...` → 未解出，进入调优（下节）。
   - 出现 `[flagcheck] raw-echo` 提示 → flag 与原始题目文件逐字相同（题目自带的），不算解出，引擎已自动换路，不要把它记成技术经验。
6. **默认留痕**：`--write-trace` 成功与未解出**都会**生成 `_ctfpilot_trace.json`（题型、flag、provenance、全部尝试）——失败 trace 是归因复盘的主要原料；有题解文本时用 `learn-writeup` 入库（自动解析模板字段与标签），后续 `recall` 检索。
7. **复盘（成功与否都要）**：按"自主元认知与 skill 自审"一节执行，不等用户提醒。

## 命令选择

| 用户意图 | 用这个 |
|---|---|
| 解一道题 | `solve <题目目录> [--mode] [--brain]` |
| 从一篇题解学技术/检查点（自动解析题型/信号/失败路径/标签，卡片版本化不覆盖） | `learn-writeup <writeup.md> --name <题名>` |
| 从写库批量重建案例知识库（四种格式解析→cases.jsonl + 经验入库，幂等） | `rebuild-knowledge <writeups目录> [--dry-run]` |
| 检索经验（按题面信号相关度排序，--query 传信号词） | `recall --category <crypto/...> [--query "<信号词>"]` |
| 查固化候选（哪些技术被反复取用/反复成功，频次来自轨迹库） | `fixations --min 3` |
| 看整体统计 | `stats` |
| 验证引擎可用（用独立记忆库，不污染生产经验） | `selftest` |

## 模式与边界（决策点）

- **通道选择**：编码链/凯撒/XOR/解压类 → `--mode fast`（秒级，`auto` 同类）。fast 未解出 → 进入模型探索通道（"补位分析"节），可选先让 `--mode deep --brain mock` 策略梯跑一轮再接手。
- **无 LLM 后端**：skill 与引擎均不依赖 LLM 服务；`--brain mock` 即纯离线确定性。模型探索由你直接完成，不必调用任何外部模型。
- **案例记忆**：`skills/knowledge/cases.jsonl`（由 `rebuild-knowledge` 生成）在深通道按题面信号相关度自动注入 top-3 案例卡（含证据指针）；写库更新后重跑 `rebuild-knowledge`（幂等，不产生重复经验）。
- **比赛专属 flag**：用 `--flag-regex '<正则>'` 追加匹配，或改 `config.json` 的 `flag_patterns`。
- **flag 来源门控**：`config.json` 的 `flagcheck.require_derived`（默认 true）——与原始题目文件逐字相同的 flag 视为 raw-echo，不入库、不计成功；题目自带 flag 文件/示例 flag 不会被误记为技术经验。遇到“答案就藏在文件里”的题可临时改 false，但经验不落库。
- **合规边界（务必告知用户）**：沙箱**默认离线**，网络命令一律拒绝（保证 clean solve）；pwn/交互服务题只产出 exp 源码与偏移计算；web/osint 只产出 payload 清单，不发包。

## 看图规则（重要）

涉及图片时（信号瀑布图、隐写提取图、SSTV 解码图、验证码等）：**不要依赖模型视觉识别来读 flag 或关键细节**。CTF 图片中的 flag 往往非常隐晦（低对比度、局部小字、视觉陷阱），模型识图易漏易错。正确做法：

1. 用脚本把需要确认的图渲染/导出为 PNG（放 `.temp/`），并说明该图的生成方式（参数、坐标含义）。
2. **打开图片让用户确认**：给出绝对路径，请用户双击查看后口头描述所见内容。
3. 模型只做粗定位（如"能量集中在哪个频段"），**关键判读交给用户**，再据用户描述继续分析。

## 引擎失败后的补位分析（深水区）

当 `solve` 返回 NOT SOLVED 且题目超出引擎边界（信号/音频/隐写/自定义调制等）：

1. 先 `recall` 查同题型历史经验，并向用户如实说明引擎为何失败（如 IQ 信号无 DSP 检测器）。
2. **卡壳即检索（禁止盲试）**：卡壳/换假设时**先重新 `recall`**（查询 = 题面信号 + 当前假设 + 已排除路径；引擎深通道每 4 步或连败 2 次会自动重检索），仍无进展再按 `references/ctfpilot-extsearch.md` 外搜：①题名+赛事+writeup ②核心技术词+writeup（如 "IQ BPSK CTF writeup"）③工具名 GitHub。找到即抄参数、用自己脚本复现验证。
3. **模型自行补位分析**：先读 recall 结果、`references/` 相关知识（信号类读 iq-signal-playbook）与工程 `skills/` 技能卡，再在 `.temp/` 安装临时依赖（numpy/matplotlib 等，勿动全局环境）编写分析脚本：魔数侦察 → 频谱/瀑布图 → 瞬时频率 → 符号解调 → 编码尝试，每步打印可读证据，计算性结论一律脚本验证。**案例卡四条下钻触发（WikiTrace）：卡面缺细节 / 来源是失败案例 / 两卡冲突 / 上一步动作失败——命中任一条，先读证据指针指向的写库原文再行动；未解案例卡只作避坑与边界参考，不当成功路径照搬。**
4. 需要看图时遵循"看图规则"。
5. 解出后把"什么技术在什么题上有效"写成简短 writeup，用 `learn-writeup` 入库（闭合 RSI 记忆环），并附 `--write-trace` 轨迹；随后按"快通道固化跃迁"一节评估是否固化为确定性检测器。

**信号/IQ 类先读 `references/iq-signal-playbook.md`**（无头 .iq/.bin/.raw、卫星/SDR/遥测、调制识别）。核心顺序：先采样级相位定调制（0/π 跳变+BPSK, 每 N 点翻转=SPS），再找自描述明文引导头读全参数，不要先入为主做多音调 MFSK 分类；卷积码生成多项式位序与实现方向必须匹配（详见 playbook）。

## 快通道固化跃迁（RSI 第二环，自主触发）

**触发条件（不等用户提醒）**：`fixations --min 3` 列出的技术（hits+successes 达标，频次读数来自轨迹库，不靠记忆）；trace 中同类 detector 反复 miss；补位分析发现"这是可判定的确定性特征"。命中即评估固化——把经验从"知识"编译为快通道"代码资产"，让常规题回到零思考成本的确定性路径。候选类目（魔数表扩展、ZIP 伪加密修复、PNG 宽高 CRC 校验、编码特征识别、零宽字符检测、尾部数据截取、CRC 爆破）与六步 SOP 见 `references/ctfpilot-rsi.md`。

固化纪律：写"特征检测条件 + 确定性判定逻辑"规格 → 纯标准库实现（临时依赖装 `.temp`）→ 回归验证（原题重放解出同一 flag、负样本不误报）→ `selftest` 全过 → 收益写回 writeup（版本化，旧记录不删）。像素级操作（LSB 等）纯标准库不划算，走深通道或 `.temp` 依赖。

## 自主元认知与 skill 自审（每题后 30 秒，不等提醒）

北极星指标：**高效解题 + 变种覆盖**——知识沉淀进 `references/`、recall 与固化工具，模型调用 skill 后直接取用、最快速解题。skill 的一切修改只为此服务，与北极星无关的内容一律不进主干。

每题结束（含 NOT SOLVED）自问四问并当场行动：
1. **耗时剖析**：最耗时的步骤是什么？瓶颈属于：引擎缺检测器 / skill 描述不清 / 临场才学外部知识 / 重复踩已知坑？
2. **标签与新知**：本题标签是否按 `references/ctfpilot-rsi.md` §6 打全？本次"一得"是什么（新标签/搜索词/怀疑点/工具/固化候选）？
3. **自主修复**：按优先级当场执行——固化脚本（上节）→ 修 SKILL.md 触发器 → 落库 writeup。凡能自主验证的（原题重放、selftest、负样本）就自主验证，不推给用户。
4. **提醒归零**：凡是"被用户提醒才想到"的改进，立即翻译成本 skill 的自动触发器；同类情况再次发生时必须无需提醒自主触发。

修 SKILL.md 前过一遍 `references/ctfpilot-rsi.md` 的自审 checklist：是否让下一题更快/更多可解；低频场景只进 references、主干留一句索引；防过度复杂与描述歧义；保持渐进式披露，勿使 skill 膨胀拖慢首 token 与解题速度。

## 求知欲与框架进化（RSI 的引擎）

- **每题必有一得**：每道题（成功或失败）必须产出至少一项新知——新标签 / 新搜索词 / 新怀疑点 / 新工具 / 固化候选；没有即视为复盘未完成。
- **禁止机械重复**：同一策略连续失败 2 次 → 强制换路径或搜索，不允许第 3 次重复同一错误逻辑。
- **框架也要进化**：优化的对象不只是题库，还有解题框架本身（工作流、知识预热、搜索时机、补位策略）。每 5 道新题或连续失败 2 道做一次 A/B 对比（指标：保留题集平均解题时长 + 正确率），赢家写入本 SKILL.md 并留回滚点。方法见 `references/ctfpilot-rsi.md` §7。
- **标签即速度**：每道题按 §6 标签体系细致打标；相似题靠标签命中、直接套技术链——这是"举一反三"的检索基础。

## 速度纪律（模型输出 token 是最慢环节）

- 执行中叙述每步 ≤1 行（如"魔数=PNG 头损→补 89→重解"），不解释原理、不复述脚本输出；证据让脚本打印，模型只输出决策点与结论。
- writeup 按 `references/ctfpilot-rsi.md` §2 模板每字段一句话、总长 ≤15 行；对用户的总结 ≤10 行。
- 能由确定性脚本产出的信息（统计、比对、频谱）不由模型转述。
- 每题复盘自检一行：本次叙述/writeup 是否超预算，超了下次收敛。

## 结果交付

- 解题成功：回复 flag，并简要说明走的是快通道还是深通道、耗时。
- 未解出：如实说明失败与已尝试的通道，给出下一步（换 deep / 加 flag 正则 / 深水区补位分析 / 提供 writeup 学习）。

## 参考

- 完整 CLI 参数、返回码、题型判断速查、故障排查见 `references/ctfpilot-cli.md`，仅在需要细节时读取。
- RSI 双环细则（落库模板、固化候选清单与 SOP、元认知复盘模板、skill 自审 checklist）见 `references/ctfpilot-rsi.md`，在解题复盘或固化评估时读取。
- 外部检索源决策（论文/GitHub/社区的实测最快路径、对标关键词组）见 `references/ctfpilot-extsearch.md`，在"临场才学"复盘、固化前对标或修改 skill 前对标时读取。