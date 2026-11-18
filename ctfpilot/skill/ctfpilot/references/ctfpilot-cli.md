# CTFPilot CLI 参考

本文件供模型在需要时加载，包含完整命令、参数、返回格式、题型判断与故障排查。

## 运行前提

- **Python 3.10+**（本机已验证 3.12）。
- 运行 `solve` 时设置 `$env:CTFPILOT_PYTHON=<python 可执行文件>`，深通道沙箱会用该解释器运行生成的代码；未设置则回退到 `python`。
- 所有命令都在 CTFPilot 工程根目录（含 `ctfpilot.py`、`config.json`）下执行。

## 命令与参数总览

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `solve <题目目录>` | 解一道题 | `--mode auto/fast/deep`、`--brain auto/llm/mock`、`--name`、`--flag-regex`、`--write-trace`、`--benchmark`、`--json` |
| `bench-pull --source <源> --dest <目录>` | 无人值守基准拉一道未见题（本地池或 GitHub 题库） | `--source local:<目录>\|gh-org:<组织>\|gh:<owner>/<repo>`、`--tag` |
| `dedup-diff [--force]` | 判重自愈只读预览（evolve-20261117-17，rsi-085）：零写入预演 `load_index` 自愈重建（缺失索引/schema 漂移/`--force` 强制）会改什么，schema 轮先用数据描述效果、不再"先变异再手工 diff"（rsi-081 手工数 14 个污染键的税）。与写路径共用同一 `_needs_rebuild()` 谓词，预览计数与真实重建 parity 由 selftest 断言；输出 will_rebuild、reason（missing-index\|forced\|schema-drift\|schemas-current）、prior/new schema、各段 prior/new/added/purged/kept、writeups_done carried；生产索引前后 sha256 一致（零写入实弹验证） | `--force`（预演强制重建） |
| `learn-writeup <writeup.md> --name <题名>` | 从题解学习，生成技能卡+检查点+经验（自动解析模板字段：题型/识别信号/失败路径/标签等；卡片版本化不覆盖旧卡）。真学习路径末尾自动触发 rebuild-knowledge（evolve-20261116-17）：新卡即时进 cases.jsonl/stats.json 可检索；源目录缺失/writeup 在源树外/重建异常或非零 rc 时显式落 `rebuild pending` 提示（不回滚已入库内容）；skip=duplicate 早退不触发重建 | `--name` |
| `account-model <题目录>` | 模型通道结果入账：把 agent/模型通道的解出与可测死路写进可统计轨迹（否则 RSI/arena 把已解题当满罚超时）；读写题目录侧车 `_ctfpilot_model_result.json`。evolve-12 起侧车对引擎重放是 raw-echo 源：引擎扫到侧车里的旗按 raw-echo 撤回（侧车=模型答案纸，非推导产物，防假 SOLVED）；A/B 重放前先清题目录 `_ctfpilot*` 引擎副产物（result/trace 豁免保留，属诚实重放语义） | `--flag`、`--time-s`、`--category`、`--summary`、`--dead-end 工具:秒[:备注]`（可重复） |
| `adjudicate <题目录>` | 裁决块 JSON 安全写入（evolve-20261117-05）：对 `_ctfpilot_result.json` 写人工裁决（假 SOLVED 撤回/确认）。全值经 stdlib JSON 编码（U+FFFD→`\ufffd`，杜绝 rsi-066 手写 repr `\U0000FFFD` 致整文件不可解析的事故）；先字节忠实备份 `_ctfpilot_result.pre-adjudicate-<时间戳>.json`（`_ctfpilot*` 前缀不进未来拉题）；写后重读 json.loads 验证才报成功；已损坏的结果文件直接拒绝不修。原始值默认从改写前顶层自动留档（original_solved/original_flag） | `--true-status`、`--reason`（必填）、`--by`、`--set-solved 0\|1`、`--set-flag <F>`（''清空）、`--original-solved`、`--original-flag`、`--field k=v`（可重复） |
| `adjudicate-db --db-row <N>` | DB 行裁决面（evolve-20261117-18，rsi-087）：裁决 dir 里没有 `_ctfpilot_result.json` 的生产行（rsi-086 row-496 场景），手术纪律四件套——WAL checkpoint(TRUNCATE) → 整库字节忠实备份落 `memory/trajectories.pre-adjuddb-<行id>-<时间戳>.sqlite`（持久，sha256 打印）→ 单事务 BEGIN IMMEDIATE..COMMIT 只改该行 → 提交后读回行+重解析尾部裁决块才报成功。块格式与 dir 面/rsi-086 手工块逐字节一致（紧凑单行 JSON，original 值内嵌）；重复裁决只追加新块、original 从当前值重新捕获（审计只增不删）；attempts/lessons 永不触碰。所有拒绝路径（行不存在/缺必填/`--field` 无`=`）在任何备份写盘之前返回 rc=2，零残留。evolve-20261117-23 起另加前置健全性检查：summary 最后一个非空行若以 `{"adjudication"` 开头但 json 解析失败（或 adjudication 值不是 JSON 对象），在 checkpoint/备份/UPDATE 之前直接 rc=2 拒绝（与 dir 面对损坏结果文件的拒绝契约对齐，杜绝损坏片段被埋在新块之下）；散文提及、空 summary、合法旧块照常通过 | `--db-row <N>`（必填）、`--true-status`、`--reason`（必填）、`--by`、`--set-solved 0\|1`、`--set-flag <F>`、`--field k=v`（可重复） |
| `rebuild-knowledge <writeups目录>` | 从写库批量重建案例知识库（四种格式解析→cases.jsonl+经验入库，幂等） | `--dry-run`、`--exclude <holdout.json>`（留出隔离构建） |
| `eval-retrieval [writeups]` | 检索回归评测（2026-11-14 起查询仅取题面，不含同源附件/载体行，防自证泄漏） | `--save-baseline` |
| `recall --category <题型>` | 检索历史经验与最近挑战（按题面信号相关度排序；空壳自动记录不进，见 fixations） | `--category`、`--query "<信号词>"` |
| `fixations [--min N]` | 列出 hits+successes ≥ N 的技术（固化环触发依据，频次来自轨迹库） | `--min`（默认 3） |
| `stats` | 统计落库数据 | — |
| `selftest` | 运行内置 6 道冒烟题（用 `.temp/selftest.sqlite` 独立记忆库，不污染生产经验） | — |

### solve 参数详解

- `--mode auto`（默认）：先快通道，未解出再进深通道。
  - `--mode fast`：只跑确定性快通道（编码链/凯撒/XOR/解压），0 次 LLM 调用，秒级。
  - `--mode deep`：只跑深通道假设-执行-观测循环，走沙箱。
- `--brain auto|mock`（兼容旧参数 `llm`）：
  - 引擎不依赖 LLM 后端（config 的 `backends` 已清空），`auto` 等同 `mock`。
  - `mock`：离线确定性策略梯（按题型的策略表），无需任何 LLM。
- `--flag-regex <正则>`：追加一个自定义 flag 匹配正则（比赛专属格式）。
- `--write-trace`：解完后在题目目录写 `_ctfpilot_trace.json`（含题型、flag、provenance、全部尝试轨迹）。**成功与未解出都会写**，失败 trace 是归因复盘的主要原料。

### 返回码

- `0`：解出 flag（或学习/检索成功）。
- `1`：`solve` 未解出；`selftest` 有未通过项。
- `2`：参数错误（目录不存在等）。
- `3`：`bench-pull` 源耗尽/判重跳过；`account-model` 判重跳过（同名同 flag 已入账，或未解出且已有先行行）——幂等，非错误。
- `4`：`bench-pull` 源硬错误（网络/token）。

`account-model` 成功输出行：`[account-model] name=<题名> solved=true|false time=<秒> dead_ends=<N>`（机器可解析，风格同 `[benchmark]`）。

## 返回/日志解析

- 关键输出行：
  - `[result] SOLVED in Xs` / `[result] FLAG: <flag>` → 解题成功。
  - `[result] NOT SOLVED in Xs (N attempts)` → 未解出。
  - `[flagcheck] raw-echo: flag appears verbatim in original file ...` → flag 与原始题目文件逐字相同，不算解出（见“边界与合规”）。
  - `[deep] ... refused (raw-echo); continuing ...` → 深水区在循环内拒收逐字回显候选（如 docker-compose 的 FLAG 占位符），继续用剩余步数搜索，不会因占位符提前结束（evolve-20261117-20）。
  - `[triage] category=<题型> confidence=<概率>` → 题型判定。
  - `[fast]` / `[deep]` 前缀 → 走的通道。
  - `[llm] probe <后端> -> down/up` → LLM 后端探测结果（全 down 时自动 mock）。
- `--write-trace` 生成 `_ctfpilot_trace.json`，含每步尝试的 `detector/detail/success/flag`，适合需要轨迹留痕时使用。

## 题型判断速查（route 输出）

- 内容含 `=`, 尾部 `=` 对齐、ASCII 可打印、比例高 → b64/hex 编码链。
- 字母整体偏移、保留大小写/标点、含 `flag{`/`synt{` 之类移位 → 凯撒（ROT）。
- 单字节重复 xor 特征（固定 key 混淆） → 单字节 XOR。
- 题目自带 key/note → token-key XOR。
- 魔数 `1f 8b` → gzip；`PK\x03\x04` → zip（可能多层嵌套）。
- **压缩包成员 pwn 载体**（2026-11-17 起，evolve-20261117-19）：小型 zip/tar 载体（≤12 个成员）同时装着 libc/ld 共享库**和**一个非库 ELF 二进制 → pwn（远端 pwn 附件的标准打包形态，实弹：register+libc.so.6 的 zip 原先被 zip magic/ext 误判 misc）。守卫：成员数上限防 /lib 全量 dump 投票、纯库包不投票、ELF 按内容 4 字节魔数核验（不信文件名）、读坏降级不投票；rar/7z 无 stdlib 读取器不处理（如实记录）；只有 ELF 没有库成员的逆向 crackme zip 维持 misc（单变量边界，另列候选）。

## 案例知识库（rebuild-knowledge）

- `rebuild-knowledge <writeups目录> [--dry-run] [--exclude <holdout.json>]`：把写库批量解析为案例卡 `skills/knowledge/cases.jsonl`（每案含 题型/识别信号/核心技术链/陷阱/可迁移模式/失败原因/一得/证据指针）并 upsert 进经验库；支持四种写库格式：旧模板（- 题面特征: …）/ 冒号式（- 识别信号: …）/ 数字七维（1. 题型: …）/ 加粗｜分隔式（- **结果**：… ｜ **分类**：…，含 ## 样本特征 表格与 ## 尝试路径 段落）；幂等可重跑，写库更新后重跑一次。2026-11-17 起（evolve-20261116-21）技术匹配结构化标签优先：核心技术链/可迁移模式值与标签行直接作为技术卡名（作者标签原样），分类由 题型字段 → 题型/类型/分类/类别 标签值 → 纯标签行分类 token 作者声明裁决；无结构化来源才回退关键词投票。2026-11-17 起（evolve-20261117-07）upsert 后自动 prune 案例卡影子行（与 ctf-lab leak 探针 lesson_shadow_audit 同口径）：案例移除留下的死影子直接删；分类改判留下的旧类别孪生把 hit/success 计数并入现判活孪生后删；活孪生缺失时保守保留（绝不删唯一内容载体）；无活体承接的死影子计数随行删除。重跑日志带 `pruned dead-shadows=N twins-merged=N`。
- `--exclude lab/holdout.json`（留出隔离构建，2026-11-14）：清单内写库既不进卡也不进 lessons；产出写到 `skills/knowledge-holdout/`（产线 knowledge 不动），lessons 落隔离库 `<db>.holdout`；策展 overlay 从产线 cases.jsonl 继承后再过滤（精化 signals、人工裁决分类不丢）。生产重建永远不加 --exclude。
- `recall` 与深通道检索按题面信号相关度排序（词法重叠打分：识别信号+链+陷阱+可迁移+载体+尝试+区别+排除），`--query` 可显式传信号词；深通道上下文自动注入 top-3 案例卡（含证据指针与四条下钻触发规则：缺细节/失败来源/冲突/动作失败），**每 4 步或连败 2 次自动重检索**（查询并入当前假设与已排除路径）。重跑 `rebuild-knowledge` 时已精化字段（signals/distinct/excludes 等）按 case id 自动保留，不会被解析器覆盖。
- **检索回归**：改动检索打分/卡片内容后先跑 `eval-retrieval`（无参数时用 rebuild 记录的写库目录），与 `eval-baseline.json` 对比；top1 或 top3 降超 5pp 返回非零（FAIL）。改完确认变好再用 `--save-baseline` 抬高基线。当前基线为去泄漏口径（top1 62.5% / top3 81.2% / evaluable 16，2026-11-14），旧基线 75.0/89.3 含同源载体回声不可比。

## 基准拉题（bench-pull，无人值守环）

- `--source local:<题池目录>`（旧写法 `--pool` 等价）：本地池每题一个子目录，拷贝时剔除引擎痕迹；池空返回 3。
- `--source gh-org:CTF-Archives`：随机挑组织内年份命名仓库再随机挑题；比赛仓库根目录平铺 `分类-题名` 子目录，单题仓库整仓即题；全走 api.github.com（本机稳定通道），优先 gh CLI token，组织清单缓存 24h。
- `--source gh:<owner>/<repo>`：任意 GitHub 题库仓库，自主发现新源后零代码接入。
- 判重（gh 源，2026-11-16 起四层）：①ledger `keys`（`owner/repo::路径`）拦本 feed 自己拉过的题；②`memory/_traj_index.json` 跨源索引（`core/dedup.py` 自动维护，缺失自播种，force 重建保留 writeups_done）拦"别的体系已做过的题"——归并五源（2026-11-16 合并后全部实体在 ctf-feed 内）：`ctf-feed/done-legacy.json`（旧 20 分钟定时任务、另一账号 TA 留下）、ctf-feed 内 `比赛__题名` 形态旧题目录、工作空间根同形态目录、轨迹库 challenges 表全部记录名、`skills/learned` 学习卡名；三因子匹配（比赛词根 + **年份参与身份** + 剥修饰核心名，如 `Web-4.教师面板`→`教师面板`，互含=fuzzy；跨年份同名比赛/题不再互判重复——2024/2025 LanqiaoCup-Finals 第五洞实弹，pairs_schema=2 起键为 comp<sep>year<sep>core；**整仓候选（`::.`）只做 exact、跳过 fuzzy**——比赛名尾巴是任一单题核心名的构造性子串，包含匹配必然误杀；带年份候选同时查无年份 legacy 键，旧条目不回归）。命中日志 `skip=duplicate(exact|fuzzy(...))`，重复候选不记入 ledger；gh 源单次调用最多连试 8 个候选。③附件指纹守卫（第四洞+第六洞，fp_schema=5）：物化目录的内容文件 md5 与索引比对，`>40` 文件目录按前 24 个名字序采样保留身份，`.zip` 附件额外折叠前 24 个内部条目指纹（逐条目容错；`docker-*.yml`/`._AppleDouble`/`sudoers`/`sshd_config` 等共享部署模板在内外两层同样排除；**wrapper 副产物 `.solve.log`/`.solve.err` 与 BOM 改写文件不产指纹**——evolve-20261116-19/20261117-13；**空文件零身份**——evolve-20261117-14：0 字节文件 md5/blob sha 全局同值零信息量，内外两层均跳过）——同题无论换名重打包还是换 zip/解包形态都能拦；跳过键只进 `skipped_keys` 审计字段不进 keys，误杀自愈。④预下载判重（evolve-16，blobs_schema=4，2026-11-16 晚起）：gh 候选在**任何内容字节下载前**先过 precheck——名字层（key+标题）+ git blob 指纹层（候选文件在 contents API 列表自带的 blob sha 对索引 `blobs` 段；本地 `git_blob_sha` 与 git hash-object 同构，空文件 `e69de29...` 向量为锚；**候选 listing 携带 0 字节文件时同样跳过**——evolve-20261117-14，防带 .gitkeep/空 `__init__.py` 的新仓库被跨题零字节误杀）；命中即换下一候选，源耗尽时 reason 携带 `precheck-duplicate(...)` 证据；实弹验证对旧需整场下载的重复候选 4.2-4.5s 零字节拦截（2026-11-16 ISCC 大仓烧 7 分钟为直接动因）。索引缺 `blobs` 段或 schema 过旧时 load_index 自动重建自愈（保留 writeups_done）。
- learn-writeup：同 md5 二次学习打 `skip=duplicate` 返回 0（不写卡不插行）；solve：对已见题名打印 `[solve] note: processed before (id=...)`（兜底可见性）。local: 池仍维持目录名判重不变（arena 消融重放按设计保留）。
- 源耗尽/连续候选全重复返回 3，硬错误返回 4。
- 哑弹现实：部分归档仓只有部署文件无附件，如实拉、如实解失败、如实计入基准，不过滤。

## 边界与合规

- **默认离线沙箱**：网络命令（curl/wget/nc/ssh/Invoke-WebRequest 等）一律拒绝，保证 clean solve，不联网搜题解。
- **flag 来源门控（provenance）**：`config.json` 的 `flagcheck.require_derived`（默认 true）——与原始题目文件逐字相同的 flag 视为 raw-echo，不计成功、不写经验，日志提示 `[flagcheck] raw-echo`；题目自带的 `flag.txt`、readme 示例 flag 因此不会被误记为技术经验。引擎自产副产物不计入"原始文件"：`_extracted/`（解包输出）、`_extract/`（旧版引擎解包残留，evolve-20261117-21 起与 `_extracted/` 同等豁免——避免旧残留把正当解出误判为 raw-echo）、`.temp/`、`_ctfpilot*` 脚手架；triage/路由的附件清点同样剪除这些目录（旧版 `_extract/` 残留不再被当作原始附件读进证据面与解题输入）。遇到“答案就藏在文件里”的题可临时改 false，但此类经验不落库。
- **乱码可信度门（evolve-20261117-03）**：fastpath 各解码检测器命中的 flag 若携带 U+FFFD 替换符或 C0/C1 控制字符，按解码乱码拒绝（`_GARBAGE_FLAG_RE`）——典型场景：错 key XOR 系统库附件时 0x00 填充区泄漏 key、乱码碰巧拼出泛型 `FLAG{...}`（实弹：large_note 用部署 decoy `FLAG=no_FLAG` 异或 stock libm 假 SOLVED 401.5s）。真 flag 从不携带这些字符，合法非 ASCII flag 文本不受影响；被拒后引擎继续尝试其余 key 而不是提前收工。
- **pwn/交互服务题**：Windows 沙箱无法跑 ELF，只产出 exp 源码与偏移计算，不实际运行。
- **web/osint**：默认只产出 payload/检索清单，不发网络包。
- flag 判定用 `config.json` 的 `flag_patterns` 正则（默认含 `flag{}`、`CTF{}`、`NSSCTF{}`、`hgame{}` 等）；可增配比赛专属格式。

## 故障排查

- 深通道未解出：先 `--mode fast` 确认快通道是否可解；再 `--brain mock` 排除 LLM 后端问题。
- 出现 raw-echo 提示：说明 flag 在原始文件里就能直接读到，引擎按设计拒绝计功；确认是否题目自带答案/示例 flag，若确属“找文件藏 flag”题型，临时把 `config.flagcheck.require_derived` 改 false，但该经验不入库。
- LLM 全 down：`auto` 会自动 mock，不需要停；要强制确定性就 `--brain mock`。
- 加比赛专属 flag：`--flag-regex` 或改 `config.json` 的 `flag_patterns`。
- 看历史：`recall --category <题型>` 看该题型学过什么技术。
- 题目为信号/音频/隐写类（引擎无对应检测器）：按 SKILL.md"引擎失败后的补位分析（深水区）"流程由模型补位；涉及图片判读时按"看图规则"打开图片交用户确认。

> AI生成
