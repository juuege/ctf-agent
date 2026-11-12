# ctf-agent

CTF agent-related projects and utilities.

## Structure

```text
ctf-agent/
├── .gitignore
├── README.md
└── ctfpilot/                   CTFPilot 双通道 CTF 解题智能体
    ├── writeups/               题解复盘（2026-11，121 篇）
    └── dsh-plugin/             DSH（deepseek-harness）插件：ctfpilot 工具
```

## Projects

### CTFPilot

本机离线运行的双通道 CTF 解题智能体：快通道用确定性检测器秒解编码链、古典密码、XOR 与压缩解包；模型探索通道基于历史经验与参考资料补位信号、取证与逆向题。

- 入口：[`ctfpilot/`](ctfpilot/)
- 状态看板：`ctfpilot/CTFPilot-技能看板.html`
- 一键打包：`ctfpilot/一键打包CTFPilot.bat`
- 题解复盘：`ctfpilot/writeups/`
- DSH 插件：`ctfpilot/dsh-plugin/`（DeepSeek harness 的 ctfpilot 工具）
- 使用说明：[`ctfpilot/README.md`](ctfpilot/README.md)

## Usage

```bash
git clone https://github.com/juuege/ctf-agent.git
```

Add your CTF scripts, tools, writeups, and agent experiments here. Keep
sensitive data out of version control.
