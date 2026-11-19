# CTFPilot 插件详情（v0.2.0）

![CTFPilot 解题看板与提交确认卡片](preview.svg)

## 概览

dsh-ctfpilot 是 CTFPilot 双通道解题引擎的 DSH 插件（Host 半边 `lib/index.js` + Client 半边 `client.js` + 包内 bundle patch）。零运行时依赖（仅 `@deepseek-ai/schemastery`），经子进程桥接本地离线 Python 引擎。

**v0.2.0 变更：合并双模式预设 + 三界面 UI/UX 升级**

### 1. 双模式即双预设（CTF快解 / CTF深度解析）

- 包内 `cordis.patch.yml` 声明两条真实预设行（Loader 行 id 约定 `preset-<config.id>`）：
  - `preset-ctf-quick`（order 5，`ctf-quick`「CTF 快解」）：竞赛节奏工具集（fs read/write/edit、fs-search、compaction 组、持久 shell）。
  - `preset-ctf-deep`（order 6，`ctf-deep`「CTF 深度解析」）：深解析全集（agent-instructions、jobs、schedule、goal、todo 并发、compaction 组、delegation 委派组、ask-user、web fetch、持久 shell）。
- 两 persona verbatim 分别强调：快解=recall-first → 快通道先行 → 最小化往返；深度解析=读引擎轨迹续探而非重开、并行委派子代理、写 writeup 后 rebuild-knowledge 闭环。
- 界面提交 `/ctf-solve-start quick|deep …` 时，Host 先经 `sessionController.select(agent, ctf-quick|ctf-deep)` 把**该对话的 agent 预设切到所选模式**（失败静默降级仅注入题面），再把题面文本注入会话——模式差异由真实预设承载，非提示词差异。

### 2. 三界面（UI-1/UI-2/UI-3）

**UI-1 解题表单**（主面板新 tab「开始解题」，对标 Skill Explorer 创建页形态：无会话时显式空态提示）：
- 下拉选快解/深度解析；题面 textarea；附件路径 input（支持拖入/点选目录选择器，默认下载文件夹）；题型可选。
- 「开始解题」→ 向当前会话执行 `/ctf-solve-start quick|deep [--category] [--attach …] 题面`：Host 先切换该对话的 agent 预设到所选模式，再注入题面触发执行。
- 无桥接时降级为复制题面手动发送，绝不静默吞掉。
- **深色模式**：`.ctfp-formlab`/`.ctfp-input-area` 等原生控件补齐 `--dsw-alias-*` token 样式规则后才跟随主题切换（硬编码色值已全部 token 化）。

**UI-2 正在解题状态板**（主面板新 tab「正在解题」）：
- 每会话一张卡：**整卡可点**（role=button + tabIndex + Enter/空格键）跳转该对话（`openSession`），完成态不可点；运行/本轮结束徽标。
- **历史记录删除**：卡片右上角 ✕（aria-label/title 双提示）一键删除该解题条目，清理后板只余活跃项；写回 Host 会话投影。
- flag 实时匹配：每步 assistant/message 与 tool/result 经 FLAG_SCAN_RE 扫描，**只追加不覆盖**，重复出现计数 ×N。长题面/flag 溢出收敛（overflow-wrap:anywhere）。
- 双按钮「已解出」「未解出」均弹确认框避免误点：
  - 未解出在对话进行中禁用；
  - 已解出确认 → `agent.cancel({keepInbox:true})` 终止活动轮，同会话注入 writeup 请求（learn-writeup + rebuild-knowledge）；
  - 本轮结束后未解出：快解迁移全部上下文 fork 新会话并转深度解析启动，深解析原地续探。

**UI-3 工具管理**（主面板新 tab「工具管理」）：
- 条目常驻目录：nmap / sqlmap / john / hashcat / binwalk（Kali 式系统包）、volatility3 / pwntools（pip 类）、protocol-mcp（MCP，指引至 MCP Manager 页面）。
- 一键快检（健康探测）→ 每行安装/更新/卸载按钮；卸载后条目不消失，仅回到「安装」态。
- **乱码消毒（源头）**：`shellRun` 固定 `pwsh` UTF-8 控制台编码 + `asciiOnly()` 把探测 stdout/stderr 里非 ASCII 字节全部丢弃。where.exe 在中文系统输出 GBK 本地化错误串（「系统找不到指定的文件。」），若按 UTF-8/1252 解读即乱码上屏——在源头丢弃是唯一可靠消毒点，不依赖控制台编码真生效。

### 3. 命令与入口汇总

| 入口 | 说明 |
|---|---|
| `/ctfpilot` | 旧引擎入口（斜杠命令前缀） |
| `/ctf-solve-start [mode] [--category] [--attach …] 题面` | 界面 1 后端：注入模式提示词 |
| `/ctf-writeup [--name]` | 界面 2 已解出流：终止轮 + 同会话 writeup |
| `/ctf-tools status\|install\|update\|uninstall` | 界面 3 后端：pwsh 健康探测与装/更/卸 |
| `/ctf-record-win --name --category --flag` | 人审提交记账（模型提 flag → 确认卡 → 台账） |
| `/ctf-dashboard` `/ctf-stats` | 看板数据 / 台账统计 |

设计约束：单操作两端口共用单一 Host 实现（user-actions.md）；样式仅用 `--dsw-alias-*` 主题 token；组件自写不加载 client-ui-primitives；文案全走 locale 字典（zh/en 完全一致）。