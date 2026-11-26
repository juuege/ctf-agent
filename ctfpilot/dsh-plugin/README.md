# dsh-ctfpilot

[CTFPilot](../README.md) 双通道 CTF 解题引擎的 DSH（deepseek-harness）插件：把引擎的确定性快通道、经验召回与题解学习暴露为一个模型可见的 `ctfpilot` 工具。

引擎纯 Python 标准库、本机离线运行（详见 [CTFPilot README](../README.md)）。插件零运行时依赖（仅 `@deepseek-ai/schemastery`），通过子进程桥接引擎 CLI。

## 安装与部署（2026-11-26 交付级整改）

从 GitHub / npm 安装即开箱可用，**不需要另外 clone CTFPilot 引擎**：

- **引擎随包分发**：`engine/` 目录内含完整引擎（纯 Python 标准库，约 2.5MB，只含代码与基础知识库，不含任何个人账本/日志）。插件解析顺序：`config.engineDir`（可选）→ `CTFPILOT_HOME` → `<plugin>/engine` → 仓库/工作区 sibling 布局。
- **唯一环境要求：Python 3.10+**。留空 `config.python` 即自动探测 `python3` / `python`（Windows 另含 `py -3`）；探测不到时按提示设置 `config.python` 或 `CTFPILOT_PYTHON`。
- **配置不再写死路径**：`cordis.patch.yml` 里 `engineDir` / `python` 默认空串。历史配置带作者机绝对路径也不会拦死面板——会告警一次并回落到自动探测。
- **运行时数据位置**：`<plugin>/engine/memory`（轨迹库）与 `<plugin>/engine/skills`（学习/考核资产）。升级插件前建议备份该目录。
- **自检**：面板「系统演进 → 工装体检」或 `/ctf-doctor --json`，`engine.dir` 字段直接给出最终解析到的引擎目录。

## 0.4.0 工作台信息架构（2026-11-26）

主面板固定六个标签，顺序即用户动线；前三个是操作面，后三个是记忆与两类演进：

| 顺序 | 标签 | 职责 | 边界 |
|---|---|---|---|
| 1 | **开始解题** | 选题型/模式、填题面与靶机、建新对话并注入 | 只发起解题 |
| 2 | **正在解题** | 各会话运行状态、flag 追加、已解出/未解出流转 | 只观察与收尾 |
| 3 | **工具管理** | 工具健康快检、单装/更/卸、一键安装 | 只动本机工具链 |
| 4 | **知识库** | KPI + 分类覆盖；二级分段为 题目 / 经验卡 / 引擎观察（原概览、台账、探测器、经验卡四屏合并） | 只读，不改账本 |
| 5 | **题库精炼** | 交叉重刷（k-fold 闭卷考核）、批量重刷、演练台账与手工记练 | 只动题库/经验卡/探测器 |
| 6 | **系统演进** | 工装体检（引擎/Python/账本/网关/预设/日志）、RSI 提案人审、知识研究队列 | 只动插件/预设/运行时接线 |

- **命名去歧义**：原「训练场」更名为「题库精炼」（Library Refinery），原「自进化」更名为「系统演进」（System Evolution）；两个标签页各自写明分工，互相指路。
> 历史章节中的「训练场」「自进化」为旧称，现分别对应「题库精炼」「系统演进」；命令名与引擎资产名未变。
- **标签记忆**：重开面板回到上次所在屏，首访默认「开始解题」。
- **标签栏常驻**：账本为空或看板读取失败时，六个标签仍全部可用——工具管理/题库精炼/系统演进不依赖台账数据，不会被数据门槛锁死；知识库的加载/空态只在该屏内提示。
- **角标**：正在解题=运行中会话数；工具管理=未装工具数；题库精炼=考核进行中；系统演进=待审提案数（均由模块级缓存订阅驱动，零额外轮询）。

## 0.4.0 运维与 AI 可诊断

- **`/ctf-doctor [--json]`**：一次只读体检——引擎目录/`ctfpilot.py`、Python 版本、账本大小、HTTP 网关挂载、三个 CTF 预设 resolve、最近错误与 ops 事件尾部。失败项 `✗`、警告项 `△`；`--json` 输出结构化 `checks/engine/python/presets/logs`，可直接交给 AI 运维。
- **`ctfpilot-ops.log`（JSONL）**：每条命令一行 `{ts,pid,event,name,via,rid,ms,kind,...}`；高频轮询命令（dashboard / solving-state / redrill-status / challenge-detail）成功不落盘、慢调用与失败照记。与 `ctfpilot-errors.log` 共用 `rid`，面板错误可一键对上日志。
- **日志轮转**：错误日志 2MB、ops 日志 4MB、redrill Host 链路 2MB，均保留一代 `.1`。
- **网关拒绝也留痕**：401/403/404/405/413 与 500 全部写 ops 事件（含 rid、method、url、原因），不再是「面板空白但日志无记录」。

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

### 运维日志（2026-11-26 用户令：交付可运维）

三层观测，各有分工：

| 日志 | 位置 | 内容 | 轮转 |
|---|---|---|---|
| 引擎 ops | `<memory>/redrill-ops.log`（JSONL） | 每个 redrill 动作 `start→ok/error` + 耗时毫秒；参数脱敏（flag 只记 sha256 前 8 位 + 长度 + 格式，真值永不落盘） | 2MB → `.1` |
| Host 链路 | `<engineDir>/redrill-host.log`（JSONL） | 面板链路关键事件：start 的 resume 标志/workspace/folds、盲副本 staging、预设切换、注入结果、每个失败环节的 phase（plan/state/agent/stage/preset/inject/throw） | 2MB → `.1` |
| 命令错误 | `<engineDir>/ctfpilot-errors.log` | 注册包装层统一落盘的所有命令错误（含 redrill 命令族），`/ctf-logs` 查看 | 手动 clear |

- **诊断入口**：`/ctf-redrill-diag` 或训练场面板「🩺 诊断」——引擎环境
  （engine/memory/python）、state（campaign/fold/状态）、沙箱目录、ops 日志尾部、
  最近 10 条错误、Host 侧 workspace 注册与最近 15 条链路事件，一次输出。
- **面板直显**：campaign `status` JSON 内嵌 `last_errors`（最近 5 条引擎错误），
  训练场进度区直接上屏，无需进日志目录。
- **排障口径**：点按钮无反应 → 先看 `redrill-host.log` 有没有
  `ctf-redrill-start/start` 行：有 start 无 ok/error = Host 侧卡住；有 error 看
  `phase` 定位环节；正常注入但 agent 不动 → `redrill-ops.log` 看引擎侧 begin/
  verify/record 是否到达。

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

前提：已安装 DSH（≥0.2，含桌面端或 CLI），系统有 Python 3.10+。**不需要 clone 引擎仓库**——引擎已随包在 `engine/`（约 2.5MB，纯 Python 标准库，零第三方依赖）。

**方式 A：DSH 桌面端 / 任意 profile（推荐）**

把本仓库的 `ctfpilot/dsh-plugin` 目录复制（或软链）到任意本地路径，然后用 `dsh plugin` 安装——声明了 `dsh.bundle.patch` 的包会被 **reconcile 自动启用**（自动写入 profile 的 `dsh.profile.bundles`），包内 `cordis.patch.yml` 随 profile 启动自动挂载：

```bash
dsh plugin --profile desktop add "file:<你的插件目录>"
# 然后完全退出并重启 DSH（托盘右键退出，node 不热重载磁盘模块）
```

包内 `cordis.patch.yml` 的 `engineDir` / `python` 默认留空 = 自动探测：`<plugin>/engine` → 仓库/工作区 sibling → `CTFPILOT_HOME`；Python 自动探测 `python3` / `python`（Windows 另含 `py -3`）。需要自定义时才填绝对路径。

> 旧布局兼容：直接 clone 全仓库时 `ctfpilot/{CTFPilot-v0.1, dsh-plugin}` 互为兄弟，仍按相对位置解析；但分发插件只需 `dsh-plugin` 目录即可开箱运行。

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
# 考核期（活跃 redrill campaign）smoke 自动让路：引擎侧禁用裸 ctfpilot 工具，
# smoke 打印 [SKIP] 并只跑注册/桩断言，退出码 0；考核结束后重跑拿全量结果。
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

## 0.3.0 交叉重刷训练（k-fold 闭卷）

历史抄题解问题的 harness 级根治（SOTA 出处：ExpeL AAAI-24 四折验证、NYU CTF Bench 白名单+私有 flag 裁判、SWE-rebench 时间戳污染标记、SWE-Bench Pro V2 hardened protocol）：

- **引擎 `redrill` 命令族**：`plan`（同 flag 分组→原件预检→固定种子分折→盲副本+flag 打码/剔除）/ `begin` / `fast-try` / `recall`（闭卷视图）/ `hint` / `verify`（布尔判定恒不回真值）/ `record --via fast|manual|fail|missing`（引擎证据守门：无 verify MATCH 的 fast/manual 一律物理拒记）/ `fold-done` / `control`（面板 pause/abort/skip）/ `audit`（答案串溯源+禁区路径+搜题解检测，R1-R4 分级）/ `finalize`（HTML 训练报告）/ `selftest`（17 断言隔离自检）
- **k-fold 记忆隔离**：考核期间 recall 强制闭卷——考批组 writeup 卡、孤儿卡（ch=0）、冻结点后新卡一律不可见；`arena record` 直记考批题被拒；`learn-writeup` 对无 verify 证据的考批 writeup 拒收；生产 rebuild 冻结
- **插层面**：`redrill` agent 工具（Host 持引擎路径，注入协议零路径/零题名/零 flag）；campaign 活跃期间裸 `ctfpilot` 工具整体禁用（harness 级封锁）；`ctf-redrill-start/status/control/audit/finalize` 面板命令族；`preset-ctf-redrill` 专用预设（去 recall-first）
- **训练场面板**：「交叉重刷」卡片——开始/恢复按钮、折进度与 slot 状态行、暂停/继续/中止、审计/收尾，30s 轮询
- **测试**：engine selftest 17/17、plug-in commands 138/0、client 124/0、workflow 45/0、argv 0

## 0.3.1 修复与可运维（2026-11-26）

- **修复：训练场「恢复考核」按钮无反应**——`startRedrill(!rdActive)` 方向传反：
  campaign 处于 active 时点按钮走了 `plan` 分支，被引擎拒（campaign already
  active）且无路径上屏。现 active 时正确发 `--resume`（读现有状态直接续跑）。
- **修复：训练工作区目录解析时序**——`ensureSolveSession` 现在显式返回解析出的
  目录（复用 worker 取 `path`、新建取 ensure-workspace 返回值），`startRedrill`
  直接使用它；不再依赖 `workspaces` 快照的毫秒级时序（快照空窗会导致「找不到
  训练工作区」静默失败）。
- **可运维（三层日志 + 两个诊断入口）**：引擎 ops 日志统一收口在
  `ctfpilot.py::do_redrill`（所有调用方的唯一入口）；Host 链路日志补齐 staging/
  预设/注入事件；新增 `redrill diag` / `ctf-redrill-diag` 命令与面板「🩺 诊断」
  按钮；`status` JSON 内嵌 `last_errors` 直接上屏。详见「运维日志」小节。
- **selftest**：17 → 21 断言（ops 落盘、flag 脱敏不回显、diag JSON 完整性）。
- **连带修复**：selftest 开头重置 `MEMORY_DIR` 缓存——ops 包装层会先解析并缓存
  生产 memory 目录，不重置的话 selftest 的 state 会写进生产目录。
- **测试**：engine selftest 21/21、plug-in commands 138/0、client 124/0、
  workflow 45/0、argv 0