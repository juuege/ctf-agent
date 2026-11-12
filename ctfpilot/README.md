# CTFPilot · 双通道 CTF 解题智能体

CTFPilot 是一个本机离线运行的 CTF 解题 Agent：快通道用确定性检测器秒解编码链、古典密码、XOR 与压缩解包；模型探索通道基于历史经验 `recall` 与 `references/` 自主补位信号、取证与逆向题。全程纯标准库、零第三方依赖，不依赖任何 LLM 后端。

## 目录结构

```text
ctfpilot/
├── CTFPilot-v0.1/              引擎源码
│   ├── ctfpilot.py             CLI 入口
│   ├── config.json             沙箱 / flag / 预算配置
│   ├── core/                   路由、沙箱、flag 判定
│   ├── fastpath/               快通道确定性检测器
│   ├── deep/                   深通道主循环与策略梯
│   ├── learn/                  题解学习
│   ├── memory/                 轨迹库（运行时生成）
│   ├── skills/                 七类题型技能卡
│   └── tests/challs/           6 道冒烟题
├── skill/ctfpilot/             TeleAgent Skill
│   ├── SKILL.md                工作流与决策点
│   └── references/             CLI / RSI / 检索 / 信号 playbook
├── dsh-plugin/                 DSH（deepseek-harness）插件
│   ├── lib/index.js            ctfpilot 工具（进程桥接引擎）
│   ├── locale/                 中英文元信息
│   └── test/smoke.mjs          注册面 + 真实引擎端到端测试
├── writeups/                   题解复盘（按日期归档）
├── CTFPilot-技能看板.html      单文件状态看板
├── 一键打包CTFPilot.bat        标准包一键打包
└── README.md
```

## 快速开始

环境：Windows + Python 3.10 及以上，无第三方依赖。

```powershell
$py = "<Python 路径>"
$env:CTFPILOT_PYTHON = $py

cd CTFPilot-v0.1
& $py -X utf8 .\ctfpilot.py selftest
& $py -X utf8 .\ctfpilot.py solve <题目目录> --mode auto --write-trace
& $py -X utf8 .\ctfpilot.py recall --category misc
& $py -X utf8 .\ctfpilot.py fixations --min 3
& $py -X utf8 .\ctfpilot.py learn-writeup <writeup.md> --name <题名>
& $py -X utf8 .\ctfpilot.py stats
```

- `--mode fast`：只跑快通道，适合编码链 / 凯撒 / XOR / 压缩解包类。
- `--mode auto`：先快后慢，未命中时进入模型探索。
- `--write-trace`：生成 `_ctfpilot_trace.json`（成功与失败都写，含 provenance），用于复盘与经验固化。

## 状态看板

双击 `CTFPilot-技能看板.html`，即可查看技能属性、双通道流程、检测器命中、知识库、运行配置、台账与健康状态。看板是单文件静态快照，零外部依赖，可直接打印或导出 PDF。

## 一键打包

双击 `一键打包CTFPilot.bat`：脚本会自动定位 `skill/ctfpilot`、`CTFPilot-v0.1` 与看板，生成带时间戳的 ZIP，内含 `manifest.json`（逐文件 SHA256）与 `打包说明.txt`。默认排除 `__pycache__`、轨迹库与 `challs_incoming` 题目原始数据。

## DSH 插件

[`dsh-plugin/`](dsh-plugin/) 把引擎接入 DeepSeek 的 DSH（deepseek-harness）运行时：一个模型可见的 `ctfpilot` 工具，暴露 solve / recall / learn-writeup / stats / selftest / fixations 六个子命令，进程桥接本地引擎，零 LLM 依赖、离线运行。

挂载（patch overlay 示例）：

```yaml
- insert:
    - id: ctfpilot
      name: '/abs/path/to/ctf-agent/ctfpilot/dsh-plugin/lib/index.js'
```

详见 [`dsh-plugin/README.md`](dsh-plugin/README.md)。

## 题解复盘

[`writeups/`](writeups/) 按日期归档 2026-11-10 至 2026-11-12 的 121 篇复盘：解出 78 篇、部分 8 篇、未解 33 篇、跳过 2 篇。

- 每篇保留题目结构、解法链、七维标签与失败原因。
- 题解保留真实 flag，仅用于已结束赛事的复盘与个人记录。
- 入口：[`writeups/README.md`](writeups/README.md)

## 近期更新

- v0.2：flag 来源门控（raw-echo 不计功）、失败也写 trace、`fixations` 固化读数、selftest 独立记忆库、学习卡 `-vN` 版本化。
- 2026-11-11 实测数信杯 BB.q1.iq：引擎 5.1s 未解出，模型补位 BPSK/Viterbi 解出并入库，recall 闭环命中。
- 快通道新增 PNG LSB 多通道扫描、`flag:...:` 冒号格式与诱饵 flag 过滤。
- 新增 2026-11-12 批次 77 篇复盘与对应技能卡，`skills/learned/` 持续增厚。
- 新增 `dsh-plugin/`：DSH 插件（ctfpilot 工具），真实 dsh 0.2.1-rc.3 挂载验证通过。

## 安全边界

- 仅用于授权比赛与本地靶场。
- 沙箱默认离线，网络命令按模式拦截（静态校验）。
- pwn / web / osint 只产出 exp 源码与 payload 清单，不实际发包。
- 题目数据不出本机；轨迹库与题库原始数据不进入版本库。

## 说明

本仓库不包含 `memory/trajectories.sqlite` 与 `challs_incoming/` 题目数据。首次运行会在 `CTFPilot-v0.1/memory/` 下自动生成本地轨迹库。
