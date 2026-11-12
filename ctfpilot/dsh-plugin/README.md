# dsh-ctfpilot

[CTFPilot](../README.md) 双通道 CTF 解题引擎的 DSH（deepseek-harness）插件：把引擎的确定性快通道、经验召回与题解学习暴露为一个模型可见的 `ctfpilot` 工具。

引擎纯 Python 标准库、本机离线运行（详见 [CTFPilot README](../README.md)）。插件零运行时依赖（仅 `@deepseek-ai/schemastery`），通过子进程桥接引擎 CLI。

## 工具

```
ctfpilot(command, ...)
```

| command | 专有参数 | 作用 |
|---|---|---|
| `solve` | `challenge`(必填)、`mode`(auto/fast/deep)、`write_trace`、`flag_regex` | 解题；快通道 0-2s 出编码链/凯撒/XOR/解压/LSB 类 |
| `recall` | `category` | 召回同题型历史检查点（攻题前先召回） |
| `learn-writeup` | `writeup`(必填)、`name` | 把题解 markdown 入库为检查点/技能卡 |
| `stats` | — | 引擎台账统计 |
| `selftest` | — | 6 道冒烟题自检（安装验证） |
| `fixations` | `min` | 列出反复命中的固化候选技术 |

返回规范 JSON：`{command, solved, flag, elapsed, summary, output}`。

## 安装与挂载

前提：已安装 DSH（≥0.2，含桌面端或 CLI），系统有 Python 3.10+，且已 clone 本仓库：

```bash
git clone https://github.com/juuege/ctf-agent.git
```

本仓库布局即默认解析路径（`ctfpilot/{CTFPilot-v0.1, dsh-plugin}` 互为兄弟目录，插件按相对位置自动找到引擎，无需配置）。

**方式 A：DSH 桌面端 / 任意 profile（标准）**

把插件目录复制（或软链）到任意本地路径，然后用 `dsh plugin` 安装——声明了 `dsh.bundle.patch` 的包会被 **reconcile 自动启用**（自动写入 profile 的 `dsh.profile.bundles`），包内 `cordis.patch.yml` 随 profile 启动自动挂载：

```bash
dsh plugin --profile desktop add "file:C:/dsh-plugins/dsh-ctfpilot"
# 然后完全退出并重启 DSH（托盘右键退出，node 不热重载磁盘模块）
```

桌面端默认配置（引擎路径 / Python 路径）已写在包内 `cordis.patch.yml`，按需修改后重装。

> pnpm 多 TeleAgent 用户共用一台机时可能报 `ERR_PNPM_UNEXPECTED_STORE`，追加
> `--config.store-dir=<旧store父目录>`（不带 v11 后缀）复用原 store 即可。

**方式 B：patch overlay（临时，不动 profile）**

```yaml
# ctfpilot-overlay.yml
- insert:
    - id: ctfpilot
      name: '/abs/path/to/ctf-agent/ctfpilot/dsh-plugin/lib/index.js'
```

```bash
dsh tui --patch ctfpilot-overlay.yml
```

注意：本地目录挂载须指到 `lib/index.js` 入口文件——目录路径不被 dsh 的 ESM 解析支持。

**方式 C：preset 插件行**（standard preset 的 `config.plugins` 列表追加）

```yaml
- id: ctfpilot
  name: '/abs/path/to/ctf-agent/ctfpilot/dsh-plugin/lib/index.js'
  config:
    timeoutSeconds: 300
```

**方式 D：npm**（若发布后）

```yaml
- id: ctfpilot
  name: 'dsh-ctfpilot'
```

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| `python` | 探测 | Python 3.10+ 解释器。探测顺序：`CTFPILOT_PYTHON` → python3 → python3.12/3.11/3.10 → python |
| `engineDir` | 仓库相对 | 引擎目录（含 `ctfpilot.py`）。默认顺序：`CTFPILOT_HOME` 环境变量 → 插件父目录的 `CTFPilot-v0.1`（本仓库布局） |
| `timeoutSeconds` | 300 | 单次引擎调用上限（deep 模式长题可调大） |
| `maxOutputChars` | 8000 | 返回给模型的 stdout 截断上限 |

## 本地开发与测试

```bash
cd dsh-plugin
npm install              # 仅装 schemastery
node test/smoke.mjs      # 注册面 + 真实引擎 selftest/solve/recall 端到端
```

要求引擎目录与 dsh-plugin 平级（同仓库布局）。挂载级验证：

```bash
dsh headless --patch your-overlay.yml "hi"   # 无 LLM 凭据时，若仅报 MISSING_CREDENTIAL 且无 "entry did not activate"即挂载成功
```

## 已知限制

- `selftest` 的 `06_zip_nested` 依赖深通道 mock 策略梯，干净目录上可能 5/6——引擎侧已知不稳定点，与插件无关。
- 引擎沙箱默认离线；`web/pwn/osint` 类只产 exp/payload 清单，不发包（引擎行为，随桥接工具透出）。
- 仅用于授权比赛与本地靶场。

> AI生成
