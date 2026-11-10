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
& $py -X utf8 .\ctfpilot.py learn-writeup <writeup.md> --name <题名>
& $py -X utf8 .\ctfpilot.py stats
```

- `--mode fast`：只跑快通道，适合编码链 / 凯撒 / XOR / 压缩解包类。
- `--mode auto`：先快后慢，未命中时进入模型探索。
- `--write-trace`：生成 `_ctfpilot_trace.json`，用于复盘与经验固化。

## 状态看板

双击 `CTFPilot-技能看板.html`，即可查看技能属性、双通道流程、检测器命中、知识库、运行配置、台账与健康状态。看板是单文件静态快照，零外部依赖，可直接打印或导出 PDF。

## 一键打包

双击 `一键打包CTFPilot.bat`：脚本会自动定位 `skill/ctfpilot`、`CTFPilot-v0.1` 与看板，生成带时间戳的 ZIP，内含 `manifest.json`（逐文件 SHA256）与 `打包说明.txt`。默认排除 `__pycache__`、轨迹库与 `challs_incoming` 题目原始数据。

## 安全边界

- 仅用于授权比赛与本地靶场。
- 沙箱默认离线，网络命令一律拒绝。
- pwn / web / osint 只产出 exp 源码与 payload 清单，不实际发包。
- 题目数据不出本机；轨迹库与题库原始数据不进入版本库。

## 说明

本仓库不包含 `memory/trajectories.sqlite` 与 `challs_incoming/` 题目数据。首次运行会在 `CTFPilot-v0.1/memory/` 下自动生成本地轨迹库。