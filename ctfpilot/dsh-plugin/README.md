# dsh-ctfpilot

[CTFPilot](../README.md) 双通道 CTF 解题引擎的 DSH（deepseek-harness）插件：把引擎的确定性快通道、经验召回与题解学习暴露为一个模型可见的 `ctfpilot` 工具。

引擎纯 Python 标准库、本机离线运行（详见 [CTFPilot README](../README.md)）。插件零运行时依赖（仅 `@deepseek-ai/schemastery`），通过子进程桥接引擎 CLI。

## 工具

```
ctfpilot(command, ...)
```

| command | 专有参数 | 作用 |
|---|---|---|
| `solve` | `challenge`(必填)、`mode`(auto/fast/deep)、`write_trace`、`flag_regex` | 解题；快通道 0-2s 出编码链/凯撒/XOR/解压/LSB 类；深通道注入 top-3 相关案例卡并每 4 步/2 次失败重检索 |
| `recall` | `category`、`query`(信号词) | 召回同题型历史检查点；`query` 按信号相关度排序（魔数/协议名/技术名，如 "NTRU lattice LLL"） |
| `learn-writeup` | `writeup`(必填)、`name` | 把题解 markdown 入库为版本化技能卡/检查点 |
| `rebuild-knowledge` | `writeups_dir`(必填)、`dry_run` | 从题解目录批量重建案例卡知识库（4 格式解析、幂等） |
| `eval-retrieval` | `writeups_dir`、`save_baseline` | 检索回归门禁：top1/top3/MRR 对基线，超 5pp 容差报 FAIL；改检索/卡片后必跑 |
| `stats` | — | 引擎台账统计 |
| `selftest` | — | 6 道冒烟题自检（安装验证，独立记忆库） |
| `fixations` | `min` | 列出反复命中的固化候选技术 |
| `dashboard` | `out_html` | 生成交互式 HTML 看板（SQLite 轨迹数据） |

返回规范 JSON：`{command, solved, flag, elapsed, summary, output, category, confidence, evidence_count, steps[]}`。`steps` 来自引擎的机器可读 `[meta]` 行（快通道/深预检/深执行/规划），UI 卡片据此渲染过程时间线。

## 人类确认卡与斜杠命令

- `propose_submit`：模型提交 flag 时先出确认卡，用户在卡片上确认后才写入引擎台账（`ctf-record-win`）。
- 斜杠命令（手敲诊断用，面板不占用）：`/ctf-record-win --name N --category C --flag F`、`/ctf-dashboard`、`/ctf-stats`、`/ctf-preset-audit`、`/ctf-logs` 等——全部保留注册，可在对话里手动执行。
- 会话投影 `ctfpilot`：从 `tool/call`、`tool/result`、`command/run` 事件折叠出 pending 提交与已确认台账，供前端卡片结算。

## HTTP 数据网关（2026-11-20：对话里只允许解题上下文）

官方命令通道 `dsh-commands.execute()` **无静默开关**：每执行一次必写
`command/run` + `command/done` 两个 log-only 事件，结果全文进
`command/done`——面板拉一次 24KB 看板 JSON 就会永久落进会话日志并折成
对话卡片。因此面板↔Host 的数据与动作全部改走 **webServer 命名路由
`/ctfpilot/data/<cmd>`**：

- Host 侧经运行时 `ctx.inject(['webServer','connection','agents'])` 挂载
  prefix 路由，复用与命令通道同一批包装后 handler（`commandHandlerRegistry`，
  零逻辑重复）；认证挂官方 `connection.admit`（与 `/api` 同款 Host/Origin
  栅栏 + 浏览器 owner cookie）；会话作用域命令带 `?sid=`，经 `ctx.agents.get`
  解析 agent——与命令通道同源语义。
- 协议：GET 走 `?args=`（纯读白名单：dashboard / challenge-detail / stats /
  solving-state / logs / preset-audit）；POST 走请求体（题面正文/含反斜杠路径
  零转码，1MiB 上限）。响应体 = CommandResult JSON。
- client 侧 `panelDataFetch(cmd, {method, sid})` 返回与旧命令桥**同形信封**
  `{ok, value:{result:{kind,text}}}`，下游解包零改动；数据读取与会话完全
  解耦（零会话可拉数据，主面板 noSession 空态已拆除）。
- 会话里的命令卡片从此**只来自用户手敲**；模型解题注入不受影响。网关错误
  经包装层落盘 `ctfpilot-errors.log`（`via=gateway-error-result|gateway-throw`），
  双通道显形纪律不变。

## 错误日志（失败必显形，2026-11-19）

本插件**不含吞错降级**：任何命令的失败都会双通道显形——

1. **上屏**：命令返回 `kind:'error'`，真因（原始错误码 + 消息 + 定位提示）完整展示；面板
   `doStart` 的异常同样全文上屏，不再折成通用文案。
2. **落盘**：注册包装层把每条命令错误（含抛出的异常）追加到
   `<engineDir>/ctfpilot-errors.log`（不可写时退系统临时目录），带时间戳、命令名、
   `via=error-result|throw|gateway-error-result|gateway-throw` 与堆栈。

查看与清理：`/ctf-logs`（默认 tail 30）、`/ctf-logs tail 100`、`/ctf-logs clear`。
预设切换诊断：`/ctf-preset-audit`（服务可达性 / roster 全量行 / ctf 预设逐个
resolve / 本会话 turnBoundary 锁定态）。

`/ctf-solve-start` 的预设切换失败即**终止**（不降级注入）：切换不过去却继续解题
= 用错预设假装修题。失败回报按官方错误码分类（locked / not-found / invalid /
no-service），并指引用 `/ctf-preset-audit` 与 `/ctf-logs` 定位。

## 双模式对话预设（快解 / 深度解析）

插件内置两条真实预设：`preset-ctf-quick`（config id `ctf-quick`，order 5）与
`preset-ctf-deep`（config id `ctf-deep`，order 6）——两模式的区别即对话的 **agent 预设不同**：
快解配竞赛节奏的能力集（文件读写、压缩工具组），深度解析配深解析全集（web fetch、子代理委派、
workflow、持久 shell、todo 并发、目标跟踪等）。「开始解题」表单选定模式后 `/ctf-solve-start`
先经 `agentPresets` 服务把该对话切到对应预设、再注入题面文本；**预设切换失败即终止不注入**
（见上「错误日志」一节）。

## 三个新界面

1. **开始解题表单**（主面板「开始解题」标签内）：下拉选快解/深度解析，输入题面、附件路径（可拖入、
   可点选目录选择器）、可选题型；点「开始解题」经 HTTP 网关 POST `ctf-solve-start`：自动切换该
   对话的 agent 预设到所选模式并注入题面开跑（不落命令卡片，见「HTTP 数据网关」一节）。
2. **正在解题状态板**（主面板新标签页）：每题一张卡片，**整卡可点**（role/tabIndex/Enter-空格键均可）
   跳转该对话；完成/失败的历史记录条目右上角可一键删除（清理后的板只余活跃项）；实时从每步 CoT 与输出
   token 中匹配 `flag{…}`，**只追加不覆盖**；双按钮「已解出 / 未解出」均带二次确认——运行中禁用
   未解出；确认已解出 → 终止活动轮并在同一会话请求 writeup 归档；结束后未解出 → **在同一会话
   开新回合继续解题**（上下文与进度全保留，可换策略再攻；不迁移、不 fork——2026-11-25 用户方案：
   fork 出的会话不能切换预设、也无法正确接续对话）。状态由 Host 侧会话投影 `ctfsolve` 提供。
3. **工具管理**（主面板新标签页）：一键快检 nmap / tshark / scapy / sqlmap / dirsearch / john /
    hashcat / binwalk / exiftool / volatility3 / pwntools / radare2 / protocol-mcp 的可用性；点「安装 / 更新 / 卸载」自动新建对话（固定
    CTF-worker 工作区）并把安装请求注入该对话，由 agent 代跑包管理器——通道实测（choco 优先，
    缺席回退 winget，均无则给 choco 安装/手动获取指引）、如实报输出、装完验证、结束前复检；
    **「一键安装」**（2026-11-24）：新开**一个**对话装完全部未装工具——Host 先实探全部工具，
    把所有未装工具的安装请求合并成**一条**（清单顺序串行、失败不阻塞后续、末尾一次统一复检）
    注入该对话，agent 在这一个对话里串行装完，不再逐工具各开对话；按钮带未装计数，全部就绪
    时如实回执不注入；条目常驻，卸载后回到「安装」态不消失（MCP 类引导至 MCP Manager 页面）。
    探测输出在 Host
    源头 ASCII 化并固定 pwsh UTF-8 控制台编码——中文系统的 `where.exe` GBK 错误串（如
    「系统找不到指定的文件。」）不会被按 UTF-8/1252 误解码上屏。

对应斜杠命令：`/ctf-solve-start quick|deep [--category C] [--attach P …] <题目描述>`、
`/ctf-continue [--category C] [--target T] [--attach P …] <题目描述>`、
`/ctf-writeup [--name N]`、`/ctf-drill-start [--category C] [--name N …] [--limit N] [--dir 题目根目录]`、
`/ctf-tools status|install-all|install|update|uninstall <id>`、`/ctf-arena stats|register|record|pull …`、
`/ctf-rsi cycle|list|approve|reject|adopt …`、`/ctf-research add|evidence|ingest …`
（drill-start / install-all / install / update / uninstall 走 agent 注入：面板自动开新对话，命令把请求
交给对话 agent 代跑；install-all 把全部未装工具合并成一条注入**一个**对话；drill-start 把台账
已解题的整条练兵协议——重解→与历史 flag 自验→arena 记练→更优解法 learn-writeup 入库→末尾
rebuild-knowledge 与汇总——注入**一个**快解对话，训练场页有「批量重刷」按钮）。

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

## 客户端依赖边（package.json `dsh.client.inject`）

浏览器半边注入的服务与其提供者包一一对应（包名必须是 DSH 官方包列表中的真实包）：

| 服务键（模块 `inject`） | 提供者包（package `dsh.client.inject`） |
|---|---|
| `slots` | `@deepseek-ai/dsh-client-ui-slots` |
| `locale` | `@deepseek-ai/dsh-client-locale` |
| `remote` / `remote.commands` | `@deepseek-ai/dsh-api-remotes` |
| `uiWorkspace` | `@deepseek-ai/dsh-client-ui-workspace` |
| `sessions` | `@deepseek-ai/dsh-api-session-controller` |
| `workspaces` | `@deepseek-ai/dsh-api-workspace-controller` |

席位 key（`main` / `sidebar.panellist` / `conversation.chat.turnTail`）由 `dsh-client-ui-layout` / `dsh-client-ui-sidebar` / `dsh-client-ui-conversation` 提供。`verify-client.mjs` 已将此映射固化为回归断言。


## 本地开发与测试

```bash
cd dsh-plugin
npm install              # 仅装 schemastery
node test/smoke.mjs      # 注册面 + 真实引擎 selftest/solve/recall/rebuild-knowledge/eval-retrieval/dashboard 端到端
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
