# CTFPilot 插件详情（v0.4.0）

![CTFPilot 解题看板与提交确认卡片](preview.svg)

## 当前信息架构（v0.4.0）

主面板固定六标签，顺序即动线：**开始解题 → 正在解题 → 工具管理 → 知识库 → 题库精炼 → 系统演进**。

> 历史章节中的「训练场」「自进化」为旧称，现分别对应「题库精炼」「系统演进」。

- **知识库**：KPI 与分类覆盖置顶，二级分段「题目 / 经验卡 / 引擎观察」。原「概览」「题目台账」「探测器」「经验卡」四屏合并到这里；引擎观察集中探测器命中、失败画像与解题模式。
- **题库精炼（原训练场）**：交叉重刷（k-fold 闭卷考核）、批量重刷、演练台账与手工记练；角色说明写明「只动题库资产，不动插件本身」。
- **系统演进（原自进化）**：工装体检（`/ctf-doctor`）、RSI 提案人审、知识研究队列；角色说明写明「只动插件 / 预设 / 运行时接线」，题库资产优化在题库精炼。
- **体验细节**：标签持久化（重开回上次所在屏、首访默认开始解题）；标签角标显示运行中会话数、未装工具数、考核进行中、待审提案数。

### 运维日志（v0.4.0）

- `ctfpilot-errors.log`：人读错误日志（命令名、`via`、`rid`、堆栈），2MB 轮转。
- `ctfpilot-ops.log`：机器读 JSONL，一行一事件（`ts/pid/event/name/via/rid/ms/kind`），4MB 轮转；高频成功轮询不落盘，失败/慢调用照记。
- `redrill-host.log`：redrill Host 链路事件（staging/预设/注入），2MB 轮转。
- `/ctf-doctor [--json]`：一次汇总引擎、Python、账本、网关、预设与日志；面板「系统演进 → 工装体检」提供一键体检 / 错误日志 / 预设审计三键。

## 概览

dsh-ctfpilot 是 CTFPilot 双通道解题引擎的 DSH 插件（Host 半边 `lib/index.js` + Client 半边 `client.js` + 包内 bundle patch）。零运行时依赖（仅 `@deepseek-ai/schemastery`），经子进程桥接本地离线 Python 引擎。

**v0.2.3 变更：批量重刷训练场（历史题重解闭环）**

- 新命令 `/ctf-drill-start [--category C] [--name N …] [--limit N] [--dir D]` + 训练场页新按钮「批量重刷题库」：新开**一个**快解对话，Host 从引擎台账取已解题（默认最近 10 道，自带历史 flag），把整条练兵协议注入该对话——agent 逐题：定位题目→引擎快通道重解→与历史 flag 自验→`arena register/record` 记练（--fast/--fail 语义）→recall 对比历史，更优解法写 writeup 并 `learn-writeup` 入库→末尾 `rebuild-knowledge` 与逐题汇总表 + RSI 线索提示。
- 纪律内建：不重复 record-win、诚实跳过（原件缺失）、止损（连败 3 次跳题）、认证边界硬停；引擎调用带完整前缀（本机无 ctfpilot PATH 垫片）；预设切换失败则 error 终止不注入（同 solve-start 删降级纪律）。

**v0.2.2 变更：工具目录补齐五类空白（pcap 流量 / 包构造 / Web 目录爆破 / 元数据取证 / 逆向）**

- 新增 5 项：tshark（pcap 流量分析，winget 包 WiresharkFoundation.Wireshark 4.6.10，choco 包名 wireshark）、scapy（数据包构造与解析，pip）、dirsearch（Web 目录爆破，pip）、exiftool（文件元数据取证，winget 包 OliverBetz.ExifTool 13.64）、radare2（逆向工程 CLI，choco 有包 / winget 无包→指引 GitHub w64 zip）。
- 通道全部经实测验证（winget search / pip index / choco 官网），不拍脑袋；安装通道实测口径从「仅 nmap 有 winget 包」更新为「nmap/tshark/exiftool 有包」。

**v0.2.1 变更：工具管理「一键安装」**

- `/ctf-tools install-all` 新动词 + 工具管理页新按钮（带未装计数）：点一下 → 新开**一个**对话（固定 CTF-worker 工作区），Host 实探全部工具后把所有未装工具的安装请求合并成**一条**注入该对话，agent 按清单串行装完（失败不阻塞后续、末尾一次统一复检）——不再逐工具各开一个对话。
- 全部就绪时如实回执不注入；blocked 工具带 choco/手动获取指引，且提示 choco 只需装一次；缺装集以 Host 侧实探为准（面板行仅作计数展示）。

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
  - 本轮结束后未解出 → 在同一会话直接 followup 开新回合继续解题（上下文/进度全保留；不迁移、不 fork——fork 会话不能切预设、也无法正确接续对话）。

**UI-3 工具管理**（主面板新 tab「工具管理」）：
- 条目常驻目录（13 项）：nmap / tshark / sqlmap / dirsearch / john / hashcat / binwalk / exiftool / radare2（Kali 式系统包，tshark=Wireshark CLI）；scapy / volatility3 / pwntools / dirsearch（pip 类）；protocol-mcp（MCP，指引至 MCP Manager 页面）。
- 一键快检（健康探测）→ 每行安装/更新/卸载按钮；卸载后条目不消失，仅回到「安装」态。
- **一键安装**（v0.2.1）：工具栏新按钮，把全部未装工具的安装请求合并成一条注入**一个**新对话，agent 按清单串行装完（失败不阻塞、末尾统一复检）。
- **乱码消毒（源头）**：`shellRun` 固定 `pwsh` UTF-8 控制台编码 + `asciiOnly()` 把探测 stdout/stderr 里非 ASCII 字节全部丢弃。where.exe 在中文系统输出 GBK 本地化错误串（「系统找不到指定的文件。」），若按 UTF-8/1252 解读即乱码上屏——在源头丢弃是唯一可靠消毒点，不依赖控制台编码真生效。

### 3. 命令与入口汇总

| 入口 | 说明 |
|---|---|
| `/ctfpilot` | 旧引擎入口（斜杠命令前缀） |
| `/ctf-solve-start [mode] [--category] [--attach …] 题面` | 界面 1 后端：注入模式提示词 |
| `/ctf-continue [--category] [--target] [--attach …] 题面` | 界面 2 未解出流：同会话 followup 开新回合续攻（不切预设、不 fork） |
| `/ctf-writeup [--name]` | 界面 2 已解出流：终止轮 + 同会话 writeup |
| `/ctf-drill-start [--category] [--name] [--limit] [--dir]` | 批量重刷训练场：台账已解题的整条练兵协议注入一个快解对话（重解→自验→记练→更优入库→汇总） |
| `/ctf-tools status\|install-all\|install\|update\|uninstall` | 界面 3 后端：pwsh 健康探测、一键安装（一条注入装全部）与单工具装/更/卸 |
| `/ctf-record-win --name --category --flag` | 人审提交记账（模型提 flag → 确认卡 → 台账） |
| `/ctf-dashboard` `/ctf-stats` | 看板数据 / 台账统计 |

设计约束：单操作两端口共用单一 Host 实现（user-actions.md）；样式仅用 `--dsw-alias-*` 主题 token；组件自写不加载 client-ui-primitives；文案全走 locale 字典（zh/en 完全一致）。