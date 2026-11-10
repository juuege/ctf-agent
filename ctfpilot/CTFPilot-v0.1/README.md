# CTFPilot v0.1 — 双通道 CTF 解题智能体

一个能在 Windows 本地离线运行的 CTF 解题 agent 骨架，目标与你提的两条需求一一对应：

- **简单题**：走快通道（fastpath），纯确定性脚本秒级测试通过，不花一次 LLM 调用。
- **复杂长线题**：走慢通道（deep），假设—执行—观测—纠错循环；每个动作经沙箱校验，
  全程轨迹入库；可以靠**题解学习**（writeup -> 技能卡 + 检查点）和**自身轨迹记忆**
  （相似题/失败教训检索）持续变强。

## 快速开始

```powershell
$py = "<Python 路径>"
$env:CTFPILOT_PYTHON = $py
cd <项目目录>

# 解题（auto: 先快后慢）
& $py -X utf8 .\ctfpilot.py solve <题目目录> --mode auto

# 学习一篇题解（生成技能卡 + 检查点 + 经验）
& $py -X utf8 .\ctfpilot.py learn-writeup <writeup.md> --name <题名>

# 检索记忆 / 统计 / 自测
& $py -X utf8 .\ctfpilot.py recall --category crypto
& $py -X utf8 .\ctfpilot.py stats
& $py -X utf8 .\ctfpilot.py selftest
```

引擎不依赖任何 LLM 后端：`--brain mock` 提供离线确定性策略梯，
模型探索由调用方模型直接完成；已实测无任何 LLM 也能解题。

## 架构

```
题目目录
   │
   ▼
┌─────────┐    category+confidence+evidence
│ router  │ (魔数/扩展名/内容指纹，可选 LLM 复核)
└────┬────┘
     ▼
┌────────────── 快通道 fastpath（0 LLM，秒级）──────────────┐
│ 编码链解码(b64/b32/hex/url/gunzip/reverse/rot13/atbash,  │
│   BFS 8 层, 按行拆分输入)                                  │
│ caesar 全扫 | 单字节 XOR 全扫 | token-key XOR(题面词)     │
│ 压缩解包后再链解码                                        │
└──────┬───────────────────────────────────────────────────┘
       │ 未解出
       ▼
┌────────────── 慢通道 deep loop（预算内迭代）──────────────┐
│ 上下文分段: 题面摘要+技能卡+检索到的经验+已试清单           │
│ (原始轨迹进 SQLite，不塞进提示词)                          │
│ 规划器: LLM 严格 JSON 动作 (python/shell/giveup)           │
│         或离线 mock 策略梯(读文件/strings/熵/解包/轮转)    │
│ 沙箱: 白名单+超时+离线(网络命令直接拒绝), 拒绝即回喂纠错    │
│ 错误分类: timeout / nonzero-exit / empty-output / reject    │
└──────┬───────────────────────────────────────────────────┘
       ▼
┌────────────── 学习闭环 learn ─────────────────────────────┐
│ 自动经验: 解出题后记录"什么技术在什么题上有效"             │
│ 题解学习: writeup -> 技术卡/命令/检查点 -> skills/learned  │
│          + SQLite checkpoints (DeepRed 式部分得分)         │
└───────────────────────────────────────────────────────────┘
```

## 设计依据（研究结论 -> 设计决策）

| 研究结论 | 来源 | 落到本工程 |
|---|---|---|
| 严格类型化的工具层+结构化反馈，比堆文档更有效（77.8% 成功率，无文档也达 77.8%） | STRIATUM-CTF, IEEE CARS 2026 最佳论文 | 沙箱先验证后执行，拒绝信息作为观察回喂 |
| 程序性知识文档在有强环境反馈时几乎无效（+8.9pp, p=0.71） | "When Skills Don't Help", ACM CAIS 2026 | 技能卡只做"路线图"，不替代执行反馈 |
| 执行验证过的轨迹可训练出更强 agent（486 条轨迹 +11.6%） | CTF-Dojo (arXiv 2508.18370) | 全量轨迹入库，按"定位"（题型+错误类+技术）索引 |
| 上下文分段可解出普通执行解不出的题（+18.52%） | RAISE 2026 @ ESORICS | LLM 只见分段摘要+已试清单，不看全历史 |
| 检查点式部分得分优于二元 solved | DeepRed (arXiv 2604.19354) | writeup 检查点入库，可对同类题做进度评分 |
| Cybench 上 37.1% 的"通过"是作弊（多为联网搜题解） | "Every Model Cheats" (arXiv 2607.21763) | 默认离线模式，网络命令一律拒绝，保证 clean solve |
| 逆向题失败集中在"理解阶段"，堆预算救不回来 | Reveree (arXiv 2609.01185) | router 先做侦察与题型确认再进慢通道 |

## 当前边界（v0.1）

- pwn/交互服务题：Windows 沙箱不能跑 ELF，只产出 exp 源码与偏移计算（见 skills/pwn.md 约定）。
- 慢通道 mock 大脑只有 5 个策略；如需接入外部模型，可在调用方完成规划，引擎保持离线。
- web/osint 类默认离线，只产出 payload/检索清单，不实际发包。
- flag 判定用严格正则（config.json 可加比赛专属格式如 `NSSCTF{}`、`hgame{}`）。
- 复杂逆向/堆题需要后续在策略梯和技能卡里继续加料（框架已就位）。

## 目录说明

```
ctfpilot.py               CLI 入口
config.json               沙箱白名单/flag 格式/预算
core/                     router(题型路由) sandbox(离线沙箱) flagcheck llm(兼容占位)
fastpath/detectors.py     快通道全部确定性检测器
deep/planner.py           可选 LLM 动作 + 离线 mock 策略梯
deep/loop.py              慢通道主循环（上下文分段+错误分类+纠错）
memory/trajstore.py       SQLite 轨迹库/经验/检查点（recall 供规划器检索）
learn/writeup_learner.py  题解解析 -> 技能卡 + 检查点
skills/*.md               七类技能卡（可被 writeup 学习持续增厚）
tests/challs/             6 道冒烟题（selftest 用）
```

## 合规与安全

- 仅用于授权比赛/本地靶场；沙箱默认禁网，题目数据不出本机。
- 对接外部工具（如 ARCHPR/Stegsolve，放在本机工具目录）时，把可执行名加入
  config.json 的 sandbox.allowed_executables 即可，仍保持离线约束。
