/**
 * dsh-ctfpilot 浏览器半边 —— CTFPilot 解题看板（侧栏主面板）+ 提交确认卡（回合尾部）
 *
 * 数据流（2026-11-20 起）：
 *  - 主面板：CtfpilotPanel 挂载时调用模块级 loadPanel()，经 HTTP 数据网关
 *    panelDataFetch → fetch('/ctfpilot/data/ctf-dashboard') 拉取引擎看板
 *    JSON（KPI / 分类进度 / 题目台账 / 探测器命中 / 经验卡），「刷新」按钮
 *    手动重取。**不走命令通道**：dsh-commands.execute 每执行一次必写
 *    command/run + command/done 两个 log-only 事件，24KB 看板 JSON 会永久
 *    落进会话日志并折成对话卡片（用户令：对话里只允许解题上下文）。
 *  - 提交确认卡：conversation.chat.turnTail 席位按会话注入；卡片读取会话
 *    投影 'ctfpilot'（{proposals:{...}, confirmed:[...]}），挑出与当前回合
 *    对应且已准入的 proposal，渲染「确认提交」人审卡；确认后执行
 *    /ctf-record-win（人类 slash 命令）把 Flag 记入引擎账本。
 *
 * 运行时仅依赖 react（客户端平台种子表提供）；宿主通信走 ctx.remote.commands；
 * 样式为组件内 <style> 元素（幂等、仅一份描述），颜色取 DSW 主题变量并带兜底值；
 * 布局在 390px 以下仍可用（flex-wrap）。
 */
window.__ModuleLoader__.load({
  id: 'dsh-ctfpilot',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const useState = React.useState
    const useEffect = React.useEffect
    const useMemo = React.useMemo
    const useCallback = React.useCallback
    const useRef = React.useRef

    /* ============================================================
     * 常量与字典（LocaleNamespaceMap 的 'ctfpilot' 命名空间）
     * ============================================================ */

    /** 本插件拥有的语言字典命名空间。 */
    const NS = 'ctfpilot'
    /** 主面板席位键（sidebar.panellist 的 id 与 main 的 key 必须一致）。 */
    const PANEL_ID = 'ctfpilot'
    /** 回合尾部席位注册 id。 */
    const TURN_TAIL_ID = 'dsh-ctfpilot'
    /** 会话投影键（Host 注册的 ctfpilot 会话投影单元）。 */
    const PROJECTION_KEY = 'ctfpilot'

    /** 中文主字典（键集是本命名空间的唯一事实源）。 */
    const zh = {
      // —— 侧栏面板入口 ——
      'sidePanelLabel': 'CTFPilot',
      // —— 看板头部 ——
      'panelTitle': 'CTFPilot 解题工作台',
      'panelSub': '题库、经验与工装一屏；本地引擎离线运行，记忆落在 SQLite 账本',
      'refresh': '刷新',
      'refreshing': '正在刷新…',
      // —— KPI ——
      'totalChalls': '总挑战',
      'solved': '已解',
      'rate': '通过率',
      'lessons': '经验卡',
      'unitChalls': '题',
      // —— 分类进度 ——
      'catsTitle': '分类进度',
      'noCategory': '暂无分类数据',
      // —— 解题模式 / 失败画像（概览新增）——
      'attemptsTotal': '累计尝试',
      'unitAttempts': '次',
      'modesTitle': '解题模式',
      'noModes': '暂无模式数据',
      'errorsTitle': '失败画像',
      'noErrors': '暂无失败记录',
      'errClasses': '共 {n} 类',
      'colErrClass': '失败类别',
      'colErrCount': '出现次数',
      'colErrSuc': '成功数',
      'errClassTitle': '可行动信号（语义类）：重复工作、挂机、轻率放弃——引擎的失败归因优先看这些',
      // —— 题目台账 ——
      'ledgerTitle': '题目台账',
      'searchPlaceholder': '搜索题目名 / 分类 / flag…',
      'noLedgerMatch': '没有匹配的题目',
      'colCat': '分类',
      'colMode': '模式',
      'unsolvedMark': '未解',
      'filterAll': '全部分类',
      'showLearned': '含学习导入',
      'learnBadge': '学习导入',
      'learnOnlyTitle': '仅学 writeup 的导入记录，未真实解题',
      'noChallenges': '暂无题目',
      // —— 知识库（合并原概览/台账/探测器/经验卡四屏）——
      'tabKnowledge': '知识库',
      'knowledgeTitle': '知识库',
      'knowledgeSub': '题库、经验卡与探测器命中——解题记忆都在这里',
      'knowledgeTabChalls': '题目',
      'knowledgeTabLessons': '经验卡',
      'knowledgeTabObserve': '引擎观察',
      'knowledgeLessonsSub': '按技术 / 分类 / 来源检索历史经验',
      'lessonSearchPh': '搜索经验卡 / 技术 / 分类 / 来源…',
      'lessonsCount': '共 {n} 张',
      'noLessonsMatch': '没有匹配的经验卡',
      'knowledgeObserveSub': '引擎内部信号：探测器命中、失败画像与解题模式；用于判断题库质量与下一轮重刷方向',
      'obsDetectorsSub': '快通道与深通道探测器的尝试 / 命中；命中率高说明该技术已可秒解',
      'obsErrorsSub': '失败归因（semantic:* 为可行动信号）',
      'obsModesSub': '引擎实际使用的运行模式分布',
      'tabBadgeRunning': '{n} 个会话运行中',
      'tabBadgeMissing': '{n} 个工具未安装',
      'tabBadgeRedrill': '考核进行中',
      'tabBadgeRsi': '{n} 条提案待审',
      'ledgerCount': '共 {n} 条',
      // —— 题目详情页 ——
      'detailBack': '← 返回看板',
      'detailTitle': '题目详情',
      'detailMeta': '创建于 {t}',
      'detailModeLabel': '模式',
      'attemptsTitle': '解题时间线',
      'attemptsCount': '{n} 次尝试',
      'noAttempts': '暂无尝试记录',
      'attemptHit': '命中',
      'attemptMiss': '未命中',
      'lessonsForTitle': '本题经验',
      'tagsLabel': '标签',
      'detailFailed': '读取该题详情失败，请重试。',
      // —— 探测器 ——
      'detectorsTitle': '探测器命中',
      'noDetectors': '暂无探测器命中',
      'colDetector': '探测器',
      'colAttempts': '尝试',
      'colHits': '命中',
      'colHitRate': '命中率',
      'colPhase': '阶段',
      'noHitRate': '—',
      // —— 经验卡 ——
      'lessonsTitle': '经验卡',
      'noLessons': '暂无经验卡',
      // —— 题库精炼（原训练场：用存量题库做闭环演练）——
      'tabRefinery': '题库精炼',
      'refineryTitle': '题库精炼',
      'refinerySub': '用存量题库做闭环演练：交叉重刷（闭卷考核）与批量重刷，沉淀更可信的经验卡与更优解法——只动题库资产，不动插件本身。',
      'refineryRoleNote': '分工：这里优化「题库 / 经验卡 / 探测器」；插件、对话预设等工装本身在「系统演进」。',
      'arenaTitle': '演练台账',
      'arenaFormTitle': '手工记练',
      'arenaFormSub': '补记一次演练（引擎要求题目名 + 分类）',
      'arenaSub': '练兵台账独立于生产账本，只记录刻意的演练结果',
      'arenaNamePh': '题目名',
      'arenaCatPh': '分类（crypto / web / misc …）',
      'arenaRecFail': '这次没解出',
      'arenaRecFast': '仅快速路径解出',
      'arenaRecordBtn': '记录本次结果',
      'arenaMissing': '请先填写题目名与分类，二者缺一无法记账',
      'arenaRecordFailed': '记录失败：请查看下方引擎返回的信息并重试',
      'arenaNoBoard': '暂无排行榜数据：先在题库拉一道，或在下方手动记录一次演练',
      'arenaOverall': '总演练',
      'colCat': '分类',
      'arenaColStat': '解/总数',
      'arenaColRate': '通过率',
      'arenaColTier': '档位(e/n/h)',
      'arenaRecordOk': '已记入练兵台账，排行榜自动刷新',
      // —— 批量重刷（2026-11-25 用户令：重解历史题→自验→记练→更优解法入库的自动化闭环）——
      'arenaDrillTitle': '批量重刷（重解 → 自验 → 入库）',
      'arenaDrillSub': '把台账已解题逐题重解（快通道优先）、与历史 flag 自验、自动记练；更优解法写 writeup 入库',
      'arenaDrillBtn': '批量重刷题库（新对话）',
      'arenaDrillBusy': '正在派发批量重刷…',
      'arenaDrillHint': '新开一个快解对话执行重刷协议：逐题定位→引擎快通道重解→与历史 flag 比对→arena 记练→更优解法 learn-writeup 入库→末尾 rebuild-knowledge 与汇总（默认最近 10 道，更多用 /ctf-drill-start --limit）',
      'arenaDrillFailed': '批量重刷派发失败：请查看下方信息',
      // —— 交叉重刷（k-fold 闭卷考核，2026-11-26）——
      'redrillTitle': '交叉重刷考核（k-fold 闭卷）',
      'redrillSub': '从已解题池抽折重考：题目匿名化（slot）、flag 经引擎私有裁判验证、记分需引擎证据；考批知识闭卷隔离，审计器扫描解题过程',
      'redrillStartBtn': '🔄 开始交叉重刷',
      'redrillResumeBtn': '🔄 恢复/继续考核',
      'redrillBusy': '正在规划…',
      'redrillHint': '新开专用考核对话：盲副本已复制进工作区，agent 只能通过 redrill 工具与引擎交互（ctfpilot 工具锁定）；逐题 begin→fast-try/recall→自解→verify→record，折内全 record 后 fold-done',
      'redrillFailed': '交叉重刷动作失败：请查看下方信息',
      'redrillNoWsDir': '未找到 CTF-redrill-worker 工作区目录（稍后再试或检查工作区列表）',
      'redrillProgressFold': '折进度',
      'redrillPass': '通过',
      'redrillSuspicious': '待复核',
      'redrillColSlot': 'slot',
      'redrillColCat': '折/分类',
      'redrillColState': '状态',
      'redrillColVia': '记分路径',
      'redrillNoCampaign': '尚无考核记录：点上方按钮开始（从已解题池抽折）',
      'redrillPauseBtn': '⏸ 暂停',
      'redrillContinueBtn': '▶ 继续',
      'redrillAbortBtn': '⏹ 中止',
      'redrillAuditBtn': '🛡 审计本考核',
      'redrillAuditHint': '扫描考核会话事件流：答案串溯源 / 禁区路径 / 搜题解检测，发现直接进训练报告',
      'redrillFinalizeBtn': '📊 收尾出报告',
      'redrillDiagBtn': '🩺 诊断',
      'redrillDiagHint': '一键运维快照：引擎环境/state/沙箱/ops 日志尾部/最近错误 + Host 侧 staging 与注入事件',
      'redrillRecentErrors': '最近引擎错误',
      // —— 系统演进：工装诊断 + 研究队列 + RSI 提案流（第六标签）——
      'tabEvolution': '系统演进',
      'evolutionTitle': '系统演进',
      'evolutionSub': '演进工装本身：插件与对话预设的健康诊断，研究队列与 RSI 提案的人审采纳。',
      'evolutionRoleNote': '分工：题库、经验卡与探测器的优化在「题库精炼」；这里只管插件 / 预设 / 运行时接线。',
      'opsTitle': '工装体检',
      'opsSub': '只读诊断：引擎 / 解释器 / 账本 / 预设 / 日志一屏看清；输出可直接交给 AI 运维。',
      'opsBtnDoctor': '一键体检',
      'opsBtnLogs': '错误日志',
      'opsBtnPreset': '预设审计',
      'opsBtnCopy': '复制结果',
      'opsCopied': '已复制',
      'opsEmpty': '点上方按钮运行一次诊断（只读，不改任何状态）。',
      'opsBusy': '诊断中…',
      'opsFailed': '诊断失败：',
      'opsDoctorHint': '检查引擎目录、Python、账本、网关、预设与最近错误；失败项以 ✗ 标记。',
      'opsLogHint': '读取 ctfpilot-errors.log 尾部；完整机器可读事件在 ctfpilot-ops.log。',
      'opsPresetHint': '检查 agentPresets 服务、roster 与三个 CTF 预设的 resolve 结果。',
      'researchTitle': '知识研究队列',
      'researchSub': '人引导的学习专题：两条证据指针且草稿通过校验后才入知识库',
      'researchEmpty': '暂无研究专题：与助手对话，让引擎 add 一个专题',
      'researchEvidence': '证据',
      'researchStQueued': '排队中',
      'researchStDrafting': '草拟中',
      'researchStReady': '待入库',
      'researchStIngested': '已入库',
      'rsiTitle': 'RSI 提案流',
      'rsiSub': '引擎提案待人审：采纳后强制跑 eval-suite 回归',
      'rsiEmpty': '暂无提案：触发一次 rsi cycle 让引擎生成新提案',
      'rsiBtnApprove': '批准',
      'rsiBtnReject': '驳回',
      'rsiBtnAdopt': '采纳',
      'rsiAdoptedNote': '已采纳：按纪律请在终端运行 eval-suite 验证（强制）',
      'rsiDecisionOk': '决策已记入提案台账，列表已刷新',
      'rsiDecisionFailed': '决策失败：请查看下方引擎返回的信息并重试',
      'rsiStProposed': '提案中',
      'rsiStApproved': '已批准',
      'rsiStAdopted': '已采纳',
      'rsiStRejected': '已驳回',
      // —— 状态面 ——
      'loadingState': '正在读取引擎数据…',
      'errorState': '引擎数据暂时不可用（请确认引擎目录配置正确）',
      'retry': '重试',
      'emptyState': '暂无数据',
      // —— 提交确认卡 ——
      'cardTitleFallback': '提交确认',
      'cardHint': '将以下 Flag 记录到 CTFPilot 引擎账本',
      'submitConfirm': '确认提交并记账',
      'skip': '暂不处理',
      'submittingStatus': '正在记录到引擎…',
      'recordedStatus': '已确认：已把 Flag 记入引擎账本',
      'recordedDetail': '已确认提交：{flag}（{category}）已记入引擎账本。',
      'retryAfterFail': '重试',
      'submitFailed': '提交失败：无法把该 Flag 记入引擎账本。',
      'cardNoSession': '打开任意会话后可确认提交',
      'recordedByOther': '已在另一处确认提交',
      // —— 三界面：解题表单 / 正在解题状态板 / 工具管理 ——
      'modeQuick': '快解',
      'modeDeep': '深度解析',
      'formTitle': '开始解题',
      'formSub': '选好模式与题面：默认新建工作区+对话并自动应用所选模式预设，再注入题面开始解题',
      'formMode': '模式',
      'formNewSessionNote': '点「开始解题」默认新建工作区与对话，自动应用所选模式的 agent 预设，再注入题面开始解题',
      'formModeNote': '模式切换的是新对话的 agent 预设：快解＝竞赛口径；深度解析＝实验室口径（含委派 / web / 目标管理工具）；新建工作区+对话时预设直接应用在该新对话上',
      'formBody': '题面',
      'formBodyPh': '粘贴题目描述（也可只填附件路径）',
      'formAttach': '附件路径',
      'formAttachPh': '多个路径用 ; 或换行分隔；也可以直接把文件拖进来',
      'formAttachPick': '选择…',
      'formCat': '题型（可选）',
      'formCatPh': '如 crypto / web / pwn …',
      'formTarget': '靶机地址（可选，扫描/连接类题目强烈建议填写）',
      'formTargetPh': '如 127.0.0.1 / 192.168.1.10 / example.com —— 只填你已授权的靶标',
      'formStart': '开始解题',
      'formStartBusy': '正在切换预设并注入题面…',
      'formShortWarn': '题面只有几个字、也没有附件路径——看起来不是有效题目。确定开跑吗？（占位/测试请点取消，避免 agent 全盘搜索白烧时钟）',
      'formShortGo': '就是测试，开跑',
      'formShortCancel': '取消',
      'formStarted': '已完成：已按所选模式切换本会话的 agent 预设，题面已注入，模型已开始解题。可在「正在解题」标签查看实时进度。',
      'formNeedInput': '请至少填写「题面」或「附件路径」之一。',
      'formNoSession': '请先打开任意一个对话，再开始解题。',
      'formNoBridge': '当前客户端缺少会话工厂服务，无法自动开会话：请复制下方提示词，直接粘贴到对话框发送。',
      'formStartFail': '开启会话失败：检查客户端状态后重试，或直接复制下方提示词手动发送。',
      'formWsFallback': '新建工作区未成功，已降级为在当前对话继续（{err}）。',
      'formWsNoService': '客户端未提供会话服务（sessions），已降级为在当前对话继续。',
      'formWsReuse': '自动建目录未成，已用既有工作区「{name}」新建对话（原因：{err}）。',
      'formWsWorkerReused': '（已复用固定工作区 CTF-worker 新建对话）',
      'formManualHint': '（以下提示词也已尝试复制到剪贴板，如未生效请手动复制。）',
      'formClipboardFail': '剪贴板不可用：请手动复制上方文本发送。',
      'solvingTab': '正在解题',
      'solvingTabSub': '各会话的解题状态快照；点卡片跳转到对应对话',
      'solvingRefresh': '刷新快照',
      'solvingEmpty': '暂无在解题的会话：用主面板的「开始解题」标签发起一题即可。',
      'solvingRunning': '运行中',
      'solvingIdle': '本轮已结束',
      'solvingFlagsTitle': '已出现过的 Flag（只增不覆盖）',
      'solvingNoFlags': '暂未抽到 flag',
      'btnSolved': '已解出',
      'btnUnsolved': '未解出',
      'startFailedBadge': '启动失败',
      'solvedQNote': '终止当前对话轮，并在同一会话上写 writeup 总结归档。',
      'unsolvedQNote': '在同一会话开新回合继续解题：上下文与进度全部保留，可换策略再攻（不迁移、不重开对话）。',
      'unsolvedWhileRunning': '对话进行中，不能标记未解出——本轮结束后再点。',
      'solvedMarked': '已标记解出：活动轮已终止，并在同会话请求 writeup 归档。',
      'unsolvedContinueQueued': '已标记未解出：已在同一会话开新回合继续解题（上下文与进度保留）。',
      'jumpHint': '跳转到该对话',
      'solvingDelHint': '删除这条解题记录',
      'confirmAction': '确认执行',
      'tabSolve': '开始解题',
      'tabTools': '工具管理',
      'toolsTitle': 'CTF 工具管理',
      'toolsSub': '常用解题工具的健康快检与装/更/卸；「一键安装」把全部未装工具合并进一个新对话串行装完；条目常驻，卸载后只回到「安装」态。',
      'toolsStatus': '一键快检',
      'toolsInstallAll': '一键安装',
      'toolsInstallAllCount': '一键安装（{n} 个未装）',
      'toolsInstallAllHint': '新开一个对话，把所有未安装工具的安装请求一次性交给该对话的 agent 按清单串行安装（缺装集以 Host 快检为准）',
      'toolsStOk': '可用',
      'toolsStMissing': '未安装',
      'toolsActInstall': '安装',
      'toolsActUpdate': '更新',
      'toolsActUninstall': '卸载',
      'toolsBusy': '处理中…',
      'toolsResult': '最近结果：',
      'toolsAllOk': '全部工具就绪。',
      'toolsStatusDone': '快检完成。',
      'toolColName': '工具',
      'toolColKind': '类型',
      'toolColState': '状态',
      'toolColActs': '操作',
      'kindMirror': '系统包',
      'kindPip': 'Python',
      'kindMcp': 'MCP',
    }

    /** 英文副语言字典（与 zh 键集完全一致）。 */
    const en = {
      'sidePanelLabel': 'CTFPilot',
      'panelTitle': 'CTFPilot Solving Workbench',
      'panelSub': 'Library, experience and harness in one place; the local engine runs offline with SQLite memory',
      'refresh': 'Refresh',
      'refreshing': 'Refreshing…',
      'totalChalls': 'Challenges',
      'solved': 'Solved',
      'rate': 'Solve rate',
      'lessons': 'Lessons',
      'unitChalls': '',
      'catsTitle': 'Category Progress',
      'noCategory': 'No category data yet',
      // —— 解题模式 / 失败画像（概览新增）——
      'attemptsTotal': 'Total attempts',
      'unitAttempts': '',
      'modesTitle': 'Solve modes',
      'noModes': 'No mode data yet',
      'errorsTitle': 'Failure profile',
      'noErrors': 'No failures recorded',
      'errClasses': '{n} classes total',
      'colErrClass': 'Failure class',
      'colErrCount': 'Occurrences',
      'colErrSuc': 'Successes',
      'errClassTitle': 'Actionable signal (semantic class): repeat work, sleep idling, premature surrender — the engine prioritizes these in failure attribution',
      'ledgerTitle': 'Challenge Ledger',
      'searchPlaceholder': 'Search challenge / category / flag…',
      'noLedgerMatch': 'No matching challenges',
      'colCat': 'Category',
      'colMode': 'Mode',
      'unsolvedMark': 'open',
      'filterAll': 'All categories',
      'showLearned': 'With learn imports',
      'learnBadge': 'Learn import',
      'learnOnlyTitle': 'writeup import only, never actually solved',
      'noChallenges': 'No challenges yet',
      // —— Knowledge (merged overview / ledger / detectors / lessons) ——
      'tabKnowledge': 'Knowledge',
      'knowledgeTitle': 'Knowledge',
      'knowledgeSub': 'Challenges, lesson cards and detector hits — all solving memory in one place',
      'knowledgeTabChalls': 'Challenges',
      'knowledgeTabLessons': 'Lessons',
      'knowledgeTabObserve': 'Engine Signals',
      'knowledgeLessonsSub': 'Search past lessons by technique / category / source',
      'lessonSearchPh': 'Search lesson / technique / category / source…',
      'lessonsCount': '{n} cards',
      'noLessonsMatch': 'No matching lessons',
      'knowledgeObserveSub': 'Engine internals: detector hits, failure profile and run modes — use them to judge library quality and the next re-drill direction',
      'obsDetectorsSub': 'Fast/deep detector attempts vs hits; a high hit rate means the technique is already one-shot solvable',
      'obsErrorsSub': 'Failure attribution (semantic:* classes are actionable signals)',
      'obsModesSub': 'Run-mode distribution actually used by the engine',
      'tabBadgeRunning': '{n} sessions running',
      'tabBadgeMissing': '{n} tools missing',
      'tabBadgeRedrill': 'exam running',
      'tabBadgeRsi': '{n} proposals pending',
      'ledgerCount': '{n} total',
      // —— 题目详情页 ——
      'detailBack': '← Back to board',
      'detailTitle': 'Challenge Detail',
      'detailMeta': 'Created {t}',
      'detailModeLabel': 'Mode',
      'attemptsTitle': 'Solve Timeline',
      'attemptsCount': '{n} attempts',
      'noAttempts': 'No attempts recorded',
      'attemptHit': 'hit',
      'attemptMiss': 'miss',
      'lessonsForTitle': 'Lessons from this challenge',
      'tagsLabel': 'Tags',
      'detailFailed': 'Failed to load this challenge detail. Please retry.',
      'detectorsTitle': 'Detector Hits',
      'noDetectors': 'No detector hits yet',
      'colDetector': 'Detector',
      'colAttempts': 'Attempts',
      'colHits': 'Hits',
      'colHitRate': 'Hit rate',
      'colPhase': 'Phase',
      'noHitRate': '—',
      'lessonsTitle': 'Lesson Cards',
      'noLessons': 'No lesson cards yet',
      // —— Library refinery (formerly Training: closed-loop practice over the existing library) ——
      'tabRefinery': 'Library Refinery',
      'refineryTitle': 'Library Refinery',
      'refinerySub': 'Closed-loop practice over the existing library: cross re-drill (closed-book exam) and batch re-drill to distil more trustworthy lesson cards and better solutions — library assets only, never the plugin itself.',
      'refineryRoleNote': 'Division of labour: this tab refines challenges / lesson cards / detectors; plugin, presets and runtime wiring live in System Evolution.',
      'arenaTitle': 'Practice Ledger',
      'arenaSub': 'Practice data is kept separate from the production ledger; only deliberate drill results land here',
      'arenaFormTitle': 'Manual drill entry',
      'arenaFormSub': 'Backfill one drill (the engine requires both challenge name and category)',
      'arenaNamePh': 'challenge name',
      'arenaCatPh': 'category (crypto / web / misc …)',
      'arenaRecFail': 'solved this attempt: no',
      'arenaRecFast': 'solved via fast path only',
      'arenaRecordBtn': 'Record this result',
      'arenaMissing': 'Fill in both challenge name and category first; either missing blocks recording',
      'arenaRecordFailed': 'Record failed: see the engine output below and retry',
      'arenaNoBoard': 'No leaderboard data yet: pull a challenge first, or record a drill below',
      'arenaOverall': 'Overall drills',
      'arenaColStat': 'sol/total',
      'arenaColRate': 'win rate',
      'arenaColTier': 'tier(e/n/h)',
      'arenaRecordOk': 'Logged into the practice ledger; the leaderboard refreshes automatically',
      // —— k-fold closed-book redrill (2026-11-26) ——
      'redrillTitle': 'Cross re-drill exam (k-fold, closed-book)',
      'redrillSub': 'Exam folds drawn from the solved pool: anonymized slots, private verifier for flags, engine-evidence-gated credit; exam-fold knowledge is isolated, sessions audited',
      'redrillStartBtn': '🔄 Start cross re-drill',
      'redrillResumeBtn': '🔄 Resume / continue exam',
      'redrillBusy': 'Planning…',
      'redrillHint': 'Open a dedicated exam chat: blind copies staged into the workspace, the agent talks to the engine ONLY through the redrill tool (raw ctfpilot locked); per slot begin → fast-try/recall → own analysis → verify → record; fold-done closes the fold',
      'redrillFailed': 'Cross re-drill action failed: see below',
      'redrillNoWsDir': 'CTF-redrill-worker workspace dir not found (retry or check the workspace list)',
      'redrillProgressFold': 'Fold progress',
      'redrillPass': 'Passed',
      'redrillSuspicious': 'Suspicious',
      'redrillColSlot': 'slot',
      'redrillColCat': 'fold/cat',
      'redrillColState': 'state',
      'redrillColVia': 'via',
      'redrillNoCampaign': 'No campaign yet: press the button above (folds draw from the solved pool)',
      'redrillPauseBtn': '⏸ Pause',
      'redrillContinueBtn': '▶ Continue',
      'redrillAbortBtn': '⏹ Abort',
      'redrillAuditBtn': '🛡 Audit campaign',
      'redrillAuditHint': 'Scan the exam session event stream: ground-truth provenance / banned paths / solution search; findings land in the training report',
      'redrillFinalizeBtn': '📊 Finalize + report',
      'redrillDiagBtn': '🩺 Diagnose',
      'redrillDiagHint': 'One-shot ops snapshot: engine env/state/sandbox/ops-log tail/recent errors + host-side staging & injection events',
      'redrillRecentErrors': 'recent engine errors',
      // —— Batch re-drill (2026-11-25): re-solve → self-verify → arena record → learn improved writeups ——
      'arenaDrillTitle': 'Batch re-drill (re-solve → self-verify → learn)',
      'arenaDrillSub': 'Re-solve ledger challenges one by one (fast path first), self-verify against historical flags, auto-record drills, learn improved writeups',
      'arenaDrillBtn': 'Batch re-drill ledger (new chat)',
      'arenaDrillBusy': 'Dispatching batch re-drill…',
      'arenaDrillHint': 'Open one quick-mode chat running the drill protocol: locate each challenge → re-solve via the engine fast path → verify against the historical flag → arena record → learn-writeup for improved solutions → rebuild-knowledge and a summary at the end (latest 10 by default; more via /ctf-drill-start --limit)',
      'arenaDrillFailed': 'Batch re-drill dispatch failed: see the info below',
      // —— System Evolution: harness diagnostics + research queue + RSI proposal stream (6th tab) ——
      'tabEvolution': 'System Evolution',
      'evolutionTitle': 'System Evolution',
      'evolutionSub': 'Evolve the harness itself: health diagnostics for plugin and presets, plus human-reviewed research and RSI proposals.',
      'evolutionRoleNote': 'Division of labour: library / lesson-card / detector refinement lives in Library Refinery; this tab only covers plugin, presets and runtime wiring.',
      'opsTitle': 'Harness Health',
      'opsSub': 'Read-only diagnostics: engine / interpreter / ledger / presets / logs at a glance; the output is ready to hand to an AI operator.',
      'opsBtnDoctor': 'Run checkup',
      'opsBtnLogs': 'Error log',
      'opsBtnPreset': 'Preset audit',
      'opsBtnCopy': 'Copy result',
      'opsCopied': 'Copied',
      'opsEmpty': 'Run a checkup above (read-only; changes nothing).',
      'opsBusy': 'Checking…',
      'opsFailed': 'Checkup failed: ',
      'opsDoctorHint': 'Checks engine dir, Python, ledger, gateway, presets and recent errors; failures are marked ✗.',
      'opsLogHint': 'Tails ctfpilot-errors.log; full machine-readable events live in ctfpilot-ops.log.',
      'opsPresetHint': 'Checks the agentPresets service, roster, and resolve results for the three CTF presets.',
      'researchTitle': 'Knowledge Research Queue',
      'researchSub': 'Human-guided study topics: only enter the knowledge base once two evidence pointers exist and the draft passes validation',
      'researchEmpty': 'No research topics yet: ask the assistant to have the engine add one',
      'researchEvidence': 'evidence',
      'researchStQueued': 'queued',
      'researchStDrafting': 'drafting',
      'researchStReady': 'ready-to-ingest',
      'researchStIngested': 'ingested',
      'rsiTitle': 'Engine RSI Proposals',
      'rsiSub': 'Engine proposals await human review: adopting forces an eval-suite regression run',
      'rsiEmpty': 'No proposals yet: trigger an rsi cycle so the engine generates new ones',
      'rsiBtnApprove': 'Approve',
      'rsiBtnReject': 'Reject',
      'rsiBtnAdopt': 'Adopt',
      'rsiAdoptedNote': 'Adopted: per discipline, run eval-suite in a terminal now (mandatory)',
      'rsiDecisionOk': 'Decision recorded in the proposal ledger; list refreshed',
      'rsiDecisionFailed': 'Decision failed: see the engine output below and retry',
      'rsiStProposed': 'proposed',
      'rsiStApproved': 'approved',
      'rsiStAdopted': 'adopted',
      'rsiStRejected': 'rejected',
      'loadingState': 'Reading engine data…',
      'errorState': 'Engine data is temporarily unavailable (check the configured engine directory)',
      'retry': 'Retry',
      'emptyState': 'Nothing here yet',
      'cardTitleFallback': 'Submit confirmation',
      'cardHint': 'Record the Flag below into the CTFPilot engine ledger',
      'submitConfirm': 'Confirm and record',
      'skip': 'Skip for now',
      'submittingStatus': 'Recording into the engine…',
      'recordedStatus': 'Confirmed: the Flag has been recorded in the engine ledger',
      'recordedDetail': 'Confirmed: {flag} ({category}) has been recorded in the engine ledger.',
      'retryAfterFail': 'Retry',
      'submitFailed': 'Submit failed: the engine did not record this Flag.',
      'cardNoSession': 'Open a conversation to confirm this submission',
      'recordedByOther': 'Already confirmed elsewhere',
      // —— Three UIs: solve form / live solving board / tools manager ——
      'modeQuick': 'Quick solve',
      'modeDeep': 'Deep analysis',
      'formTitle': 'Start Solving',
      'formSub': 'Pick mode + challenge text; by default a new workspace + conversation is created with the selected-mode preset applied, then the challenge is injected to start solving',
      'formMode': 'Mode',
      'formNewSessionNote': 'Clicking "Start solving" creates a new workspace + conversation by default, applies the selected mode\u2019s agent preset, then injects the challenge text to begin solving',
      'formModeNote': 'Mode picks the conversation\u2019s agent preset: quick = contest pace (recall first, fast path, minimal round-trips); deep = lab pace (trace-driven, parallel delegation, writeup loop). When starting a new session, the preset applies directly to that conversation',
      'formBody': 'Challenge',
      'formBodyPh': 'Paste the challenge statement (or only attachment paths)',
      'formAttach': 'Attachments',
      'formAttachPh': 'Separate paths with ; or newlines; dragging a file works too',
      'formAttachPick': 'Pick…',
      'formCat': 'Category (optional)',
      'formCatPh': 'e.g. crypto / web / pwn …',
      'formTarget': 'Target host (optional; strongly recommended for scan/recon challenges)',
      'formTargetPh': 'e.g. 127.0.0.1 / 192.168.1.10 / example.com — only targets you are authorized to test',
      'formStart': 'Start solving',
      'formStartBusy': 'Switching preset and injecting the challenge…',
      'formShortWarn': 'The challenge text is only a few characters with no attachments — it does not look like a real challenge. Start anyway? (Cancel if this is just a placeholder/test, to avoid a pointless disk-wide hunt.)',
      'formShortGo': 'Just testing — start anyway',
      'formShortCancel': 'Cancel',
      'formStarted': 'Done: the preset matched to the mode is active, the challenge text is injected, the model is solving — follow it on the Solving tab',
      'formNeedInput': 'Fill in the challenge text or at least one attachment path.',
      'formNoBridge': 'The client has no session factory; copy the challenge text below and paste it into the composer to send manually.',
      'formStartFail': 'Starting the session failed: retry, or copy the challenge text below and send manually.',
      'formWsFallback': 'New-workspace creation failed; continuing in the current conversation ({err}).',
      'formWsNoService': 'The client provides no session service (sessions); continuing in the current conversation.',
      'formWsReuse': 'Auto directory creation failed; created the conversation inside the existing workspace "{name}" (reason: {err}).',
      'formWsWorkerReused': '(Reused the fixed CTF-worker workspace for the new conversation)',
      'formNoSession': 'Open any conversation first, then start solving here.',
      'formManualHint': '(The brief below was also copied to the clipboard; copy manually if that failed.)',
      'formClipboardFail': 'Clipboard unavailable: copy the text above and send manually.',
      'solvingTab': 'Solving',
      'solvingTabSub': 'Live status per conversation; click a card to jump to that conversation',
      'solvingRefresh': 'Refresh snapshot',
      'solvingEmpty': 'No snapshot yet: launch a challenge via the Start-Solving tab above',
      'solvingRunning': 'running',
      'solvingIdle': 'round ended',
      'solvingFlagsTitle': 'Flags seen so far (append-only)',
      'solvingNoFlags': 'no flag yet',
      'btnSolved': 'Solved',
      'btnUnsolved': 'Not solved',
      'startFailedBadge': 'Start failed',
      'solvedQNote': 'Stop the active round, then ask the same conversation to write up the solution.',
      'unsolvedQNote': 'Continue in the same conversation: a new turn keeps all context and progress so the agent can retry with a fresh strategy.',
      'unsolvedWhileRunning': 'Cannot mark not-solved while the round runs — click after the round ends.',
      'solvedMarked': 'Marked solved: round stopped, writeup requested on the same conversation.',
      'unsolvedContinueQueued': 'Marked not-solved: queued a new turn in the same conversation to keep solving (context and progress preserved).',
      'jumpHint': 'Open this conversation',
      'solvingDelHint': 'Remove this solve record',
      'confirmAction': 'Confirm',
      'tabSolve': 'Start Solving',
      'tabTools': 'Tools',
      'toolsTitle': 'CTF Tools',
      'toolsSub': 'Health probe plus install/update/uninstall; Install-all merges every missing tool into one new conversation. Rows persist; uninstalled ones return to Install state.',
      'toolsStatus': 'Probe now',
      'toolsInstallAll': 'Install all',
      'toolsInstallAllCount': 'Install all ({n} missing)',
      'toolsInstallAllHint': 'Open one new conversation and hand every missing tool to its agent for sequential installation (the Host probe decides the missing set)',
      'toolsStOk': 'available',
      'toolsStMissing': 'not installed',
      'toolsActInstall': 'Install',
      'toolsActUpdate': 'Update',
      'toolsActUninstall': 'Uninstall',
      'toolsBusy': 'Working…',
      'toolsResult': 'last result: ',
      'toolsAllOk': 'All tools ready.',
      'toolsStatusDone': 'Probe finished.',
      'kindMirror': 'system pkg',
      'kindPip': 'Python',
      'kindMcp': 'MCP',
      'toolColName': 'Tool',
      'toolColKind': 'Type',
      'toolColState': 'State',
      'toolColActs': 'Actions',
    }

    /* 其余六种内置语言共享英文副本（同一对象引用即可）。 */
    const enCopy = en

    const DICTS8 = {
      zh,
      en,
      ja: enCopy,
      es: enCopy,
      fr: enCopy,
      de: enCopy,
      ru: enCopy,
      pt: enCopy,
    }

    /* ============================================================
     * 宿主桥接（apply 时写入；组件只读）
     * ============================================================ */

    /** 当前会话 id：由回合尾部席位注入捕获；解题表单/确认卡按它定位目标会话。 */
    let currentSessionId = ''
    /** 稳定翻译器：字典注册完成后捕获，供 props.t 缺位时兜底（同样走绑定翻译）。 */
    let boundTranslator = null
    /** 会话观察源：任何会话的回合尾部渲染即更新 currentSessionId 并通知订阅者。 */
    let sessionListeners = []
    function notifySessionChanged() {
      for (const fn of [...sessionListeners]) fn()
    }
    /** 订阅「当前会话已确定」事件；返回取消订阅函数。 */
    function subscribeCurrentSession(fn) {
      sessionListeners.push(fn)
      return () => {
        sessionListeners = sessionListeners.filter((x) => x !== fn)
      }
    }
    /** 看板数据全局缓存（模块级，跨组件挂载/跨会话存活——数据是引擎全局账本，
     *  与会话无关，不再按 sessionId 键控）：{ data, fingerprint, at }。
     *  fingerprint = JSON.stringify(data)，用于「账本没变就不重渲染」的内容级比较；
 *  at = 拉取时刻，再校验节流窗口的基准。 */
    let dashboardCache = null
    /** 看板请求防重入：同一时刻只保留一个进行中的请求。 */
    let dashboardInFlight = false
    /** 后台再校验节流窗口：窗口内缓存命中不再发请求（防 sessions.list 快照
     *  订阅风暴）；手动刷新与记账作废不受节流。窗口过期后的一次 revalidate
     *  在 Host 签名命中时 <50ms 往返，用户无感。 */
    const DASHBOARD_REVALIDATE_MS = 30_000
    /** localStorage 持久化键：跨 UI 重启恢复首帧（看板 JSON 实测 66KB，
     *  远低于 localStorage 配额）；写入失败（隐私模式/超限）降级为仅内存缓存。 */
    const DASHBOARD_STORE_KEY = 'ctfpilot.dashboard.v1'

    /** 恢复持久化的看板缓存：结构性防御——旧版/损坏数据过 normalizeBlob
     *  归一化后才可用，形状不对一律丢弃（宁可重新拉取，不用污染数据）。 */
    function readPersistedDashboard() {
      try {
        const raw = localStorage.getItem(DASHBOARD_STORE_KEY)
        if (!raw) return null
        const saved = JSON.parse(raw)
        if (!saved || typeof saved !== 'object') return null
        const data = normalizeBlob(saved.data)
        const at = typeof saved.at === 'number' && Number.isFinite(saved.at) ? saved.at : 0
        return { data, fingerprint: typeof saved.fingerprint === 'string' ? saved.fingerprint : JSON.stringify(data), at }
      } catch {
        return null // localStorage 不可用（Node 测试环境/隐私模式）：内存缓存仍然有效
      }
    }

    /** 把当前看板缓存写入 localStorage（66KB stringify 约 1ms，拉取路径专用）。 */
    function persistDashboard() {
      if (dashboardCache === null) return
      try {
        localStorage.setItem(DASHBOARD_STORE_KEY, JSON.stringify({
          data: dashboardCache.data,
          fingerprint: dashboardCache.fingerprint,
          at: dashboardCache.at,
        }))
      } catch {
        /* 配额/隐私模式：静默降级为仅内存缓存（数据本身已上屏） */
      }
    }

    /* ============================================================
     * 小工具
     * ============================================================ */

    const isNonEmptyString = (v) => typeof v === 'string' && v.length > 0
    const num = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback)
    const str = (v, fallback) => {
      if (typeof v === 'string') return v
      if (typeof v === 'number' && Number.isFinite(v)) return String(v)
      return fallback
    }
    const truthy = (v) => v === true || v === 'true' || v === 1 || v === '1'

    /**
     * 远端结果安全解包：{ok:boolean, value?:{commandId, result:{kind, text}}};
     * 成功取 result.text（pretty-printed JSON 字符串）。
     */
    function resultTextOf(out) {
      if (!out || typeof out !== 'object' || out.ok !== true) return undefined
      const value = out.value
      if (!value || typeof value !== 'object') return undefined
      const result = value.result
      if (!result || typeof result !== 'object') return undefined
      if (typeof result.text === 'string') return result.text
      return undefined
    }

    /** 百分数格式化：数字按 0–100 展示；0–1 之间视为小数倍率并换算成百分。 */
    function fmtPercent(v) {
      if (typeof v === 'number' && Number.isFinite(v)) {
        const scaled = v > 1 ? v : v * 100
        return `${+(+scaled).toFixed(1)}%`
      }
      if (isNonEmptyString(v)) return /%\s*$/.test(v) ? v.trim() : `${v}%`
      return '—'
    }

    /** slash 参数引号化：内嵌双引号降为单引号、控制符压空白；含空白/引号时
     * 整段双引号包裹（Host 侧 shellSplitTokens 按引号边界取值，含空格内容
     * 完整进值，不再被裸切词）。 */
    function shellArg(v) {
      const s = String(v == null ? '' : v).replace(/[\r\n\t]+/g, ' ').trim().replace(/"/g, "'")
      return s && /[\s']/.test(s) ? `"${s}"` : s
    }

    /** 始终包双引号（题面正文：即使单词也进引号，防正文首词被位置模式词误吞）。 */
    function shellQuote(v) {
      const s = String(v == null ? '' : v).replace(/[\r\n\t]+/g, ' ').trim().replace(/"/g, "'")
      return `"${s}"`
    }

    /** 构建 /ctf-record-win 的命令文本（三个参数按需引号，含空格完整入值）。 */
    function buildRecordWinCmd(name, category, flag) {
      return `/ctf-record-win --name ${shellArg(name)} --category ${shellArg(category)} --flag ${shellArg(flag)}`
    }

    /** 解析插值翻译的兜底实现（正常路径由运行时的 t 提供，支持 {name}）。 */
    const identityT = (key) => key

    /* ============================================================
     * 样式（组件内 <style> 渲染：同一元素在各树中复用，
     * 内容幂等，重复渲染无副作用；颜色走 DSW 变量并带中性兜底）
     * ============================================================ */

    const STYLE_ID = 'dsh-ctfpilot-styles'
    const STYLE_TEXT = `
/* —— 设计令牌：优先宿主官方 --dsw-*（与 DSH 桌面端 UI 同源），带本地兑底 —— */
:root{--ctfp-radius:var(--dsw-radius-md,10px);--ctfp-radius-sm:var(--dsw-radius-sm,8px);--ctfp-radius-lg:var(--dsw-radius-lg,12px);--ctfp-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Microsoft YaHei',monospace}
/* 滚动修复（用户实锤「UI 不能往下滑动」）：宿主侧栏面板槽是定高盒子，
 * .ctfp-root 必须自己成为有界滚动容器：height:100% 对齐父盒定高，
 * overflow-y:auto 让内容超出时内部滚动；min-height:0 允许 flex 子项收缩。 */
.ctfp-root{display:flex;flex-direction:column;gap:16px;padding:20px 24px 28px;color:var(--dsw-alias-label-primary,#1a1a1a);font-size:14px;line-height:1.62;font-family:inherit;font-variant-numeric:tabular-nums;min-width:0;box-sizing:border-box;min-height:0;height:100%;flex:1 1 auto;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:var(--dsw-alias-scrollbar-bg-l2,rgba(128,128,128,.4)) transparent}
/* 细滚动条：官方 scrollbar 令牌，hover 反馈与宿主一致 */
.ctfp-root::-webkit-scrollbar{width:8px;height:8px}
.ctfp-root::-webkit-scrollbar-thumb{background:var(--dsw-alias-scrollbar-bg-l2,rgba(128,128,128,.38));border-radius:999px}
.ctfp-root::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-scrollbar-hover-l2,rgba(128,128,128,.55))}
.ctfp-root::-webkit-scrollbar-track{background:transparent}
.ctfp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.ctfp-title{margin:0;font-size:17px;font-weight:680;line-height:1.35;letter-spacing:-.01em}
.ctfp-sub{margin:4px 0 0;font-size:12px;color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-btn{font:inherit;font-size:13px;font-weight:500;cursor:pointer;color:var(--dsw-alias-label-primary,#1a1a1a);background:transparent;border:1px solid var(--dsw-alias-border-l4,rgba(128,128,128,.45));border-radius:var(--ctfp-radius-sm);padding:6px 14px;display:inline-flex;align-items:center;gap:6px;transition:background-color .15s ease,border-color .15s ease,opacity .15s ease,transform .1s ease}
.ctfp-btn:active:not(:disabled){transform:translateY(1px)}
.ctfp-btn:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.14))}
.ctfp-btn:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,#3b82f6);outline-offset:2px}
.ctfp-btn:disabled{opacity:.55;cursor:default}
.ctfp-spin{display:inline-block;animation:ctfp-spin .9s linear infinite}
@keyframes ctfp-spin{to{transform:rotate(360deg)}}
.ctfp-note{padding:14px 16px;border:1px dashed var(--dsw-alias-border-l2,rgba(128,128,128,.4));border-radius:var(--ctfp-radius-lg);color:var(--dsw-alias-label-secondary,#667085);background:var(--dsw-alias-bg-layer-1,#f7f8fa)}
/* loading 态：实线边框 + 内联 spinner（CSS 图形，无 DOM 改动成本） */
.ctfp-note--loading{display:flex;align-items:center;gap:10px;border-style:solid}
.ctfp-note--loading::before{content:"";flex:none;width:12px;height:12px;border:2px solid var(--dsw-alias-state-business-primary,#3b82f6);border-top-color:transparent;border-radius:50%;animation:ctfp-spin .8s linear infinite;opacity:.8}
.ctfp-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.ctfp-kpi{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:var(--ctfp-radius);background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:12px 14px;display:flex;flex-direction:column;gap:4px;min-width:0;transition:border-color .15s ease}
/* 数字与单位基线分排：大数字主视觉，单位小号灰字随基线（不挤排） */
.ctfp-kpi__v{display:flex;align-items:baseline;gap:3px;font-size:23px;font-weight:680;line-height:1.1;font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:var(--dsw-alias-label-primary,#1a1a1a);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-kpi__u{font-size:11.5px;font-weight:500;letter-spacing:0;color:var(--dsw-alias-label-secondary,#667085);flex:none}
.ctfp-kpi__l{font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);letter-spacing:.02em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-section{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));border-radius:var(--ctfp-radius);background:var(--dsw-alias-bg-layer-2,#eef1f6);padding:12px 14px;min-width:0}
/* section 标题：小号加粗次级色，字距微收（中文友好，无大写强制） */
.ctfp-section h2{margin:0 0 10px;font-size:12.5px;font-weight:600;letter-spacing:.02em;color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-empty{padding:6px 2px;color:var(--dsw-alias-label-secondary,#667085);font-size:13px}
/* —— 训练场：记练表单 + 排行榜行（窄屏仍可读）—— */
.ctfp-cb{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-cb input{margin:0}
.ctfp-arena-form{display:flex;flex-direction:column;gap:8px;padding:10px;border:1px dashed var(--dsw-alias-border-l2,rgba(128,128,128,.4));border-radius:10px;margin-top:14px}
.ctfp-arena-form .ctfp-search{margin-bottom:0}
.ctfp-arena-rows{display:flex;flex-direction:column;gap:6px;overflow:auto;max-height:300px}
.ctfp-arena-head{display:grid;grid-template-columns:110px 78px 70px 74px;align-items:center;gap:8px;font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);padding:2px 10px 4px}
.ctfp-arena-row{display:grid;grid-template-columns:110px 78px 70px 74px;align-items:center;gap:8px;font-size:12.5px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:8px;padding:6px 10px}
.ctfp-arena-row b{font-weight:600}
/* —— 自进化：研究队列卡 + RSI 提案流卡（生命周期配色）—— */
.ctfp-sev-rows{display:flex;flex-direction:column;gap:6px;overflow:auto;max-height:340px}
.ctfp-sev-row{display:grid;grid-template-columns:minmax(160px,1.4fr) minmax(120px,.8fr) auto;align-items:center;gap:10px;font-size:12.5px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:8px;padding:6px 10px}
.ctfp-sev-row b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-sev-btns{display:inline-flex;gap:6px;flex-wrap:wrap}
.ctfp-btn--sm{font-size:12px;padding:3px 10px}
/* RSI 生命周期配色：提案=灰 → 批准=黄 → 采纳=绿；驳回=划线红 */
.ctfp-sev{font-size:11.5px;font-weight:500;border-radius:999px;padding:1px 8px;white-space:nowrap;flex:none}
.ctfp-sev--off{color:var(--dsw-alias-label-secondary,#667085);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.4))}
.ctfp-sev--yel{color:#92600a;background:#fff6d5;border:1px solid #e6c25a}
.ctfp-sev--grn{color:#1e7a34;background:#e2f5e7;border:1px solid #9fd9ae}
.ctfp-sev--rej{color:#b3261e;text-decoration:line-through;background:#fdeceb;border:1px solid #e7a9a5}
.ctfp-cats{display:flex;flex-direction:column;gap:8px}
.ctfp-catrow{display:grid;grid-template-columns:minmax(72px,120px) 1fr auto;align-items:center;gap:10px;font-size:13px}
.ctfp-catname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-bar{height:6px;border-radius:999px;background:color-mix(in srgb, var(--dsw-alias-label-primary,#1a1a1a) 9%, transparent);overflow:hidden;min-width:70px}
.ctfp-barfill{height:100%;border-radius:999px;background:var(--dsw-alias-state-business-primary,#3b82f6);min-width:0;transition:width .45s cubic-bezier(.4,0,.2,1)}
.ctfp-catcount{font-variant-numeric:tabular-nums;color:var(--dsw-alias-label-secondary,#667085);white-space:nowrap}
.ctfp-search{width:100%;font:inherit;font-size:13px;color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l4,rgba(128,128,128,.45));border-radius:var(--ctfp-radius-sm);padding:7px 10px;margin-bottom:10px;box-sizing:border-box;transition:border-color .15s ease}
.ctfp-search:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,#3b82f6);outline-offset:1px}
/* 分类下拉预选 */
.ctfp-filterbar{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.ctfp-count{font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085);font-variant-numeric:tabular-nums;margin-left:auto;white-space:nowrap}
.ctfp-filter-sel{font:inherit;font-size:12.5px;color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l4,rgba(128,128,128,.45));border-radius:var(--ctfp-radius-sm);padding:4px 8px;max-width:220px}
.ctfp-filter-sel:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,#3b82f6);outline-offset:1px}
/* 功能标签栏：active 下划线 + muted pill 背景（shadcn TabsList 口径） */
.ctfp-tabs{display:flex;gap:4px;border-bottom:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));flex-wrap:wrap}
.ctfp-tab{font:inherit;font-size:13px;font-weight:500;cursor:pointer;color:var(--dsw-alias-label-secondary,#667085);background:none;border:none;border-bottom:2px solid transparent;border-radius:var(--ctfp-radius-sm) var(--ctfp-radius-sm) 0 0;padding:6px 12px;margin-bottom:-1px;transition:color .15s ease,border-color .15s ease,background-color .15s ease}
.ctfp-tab:hover:not(.ctfp-tab--on){color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.07))}
.ctfp-tab--on{color:var(--dsw-alias-state-business-primary,#3b82f6);border-bottom-color:var(--dsw-alias-state-business-primary,#3b82f6);font-weight:600;background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.10))}
/* 标签角标：运行中/缺装/待审用小圆标；考核进行中用脉冲点（不抢标题视觉） */
.ctfp-tab__badge{display:inline-flex;align-items:center;justify-content:center;min-width:16px;height:16px;padding:0 4px;margin-left:5px;border-radius:999px;font-size:10.5px;font-weight:600;line-height:1;font-variant-numeric:tabular-nums;vertical-align:1px}
.ctfp-tab__badge--run{color:#8a4b00;background:#ffe8bf;border:1px solid #e8b665}
.ctfp-tab__badge--warn{color:var(--dsw-alias-label-secondary,#667085);background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.14));border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35))}
.ctfp-tab__dot{display:inline-block;width:7px;height:7px;margin-left:6px;border-radius:50%;background:var(--dsw-alias-state-success-primary,#22c55e);box-shadow:0 0 0 3px color-mix(in srgb,var(--dsw-alias-state-success-primary,#22c55e) 18%,transparent);vertical-align:1px;animation:ctfp-pulse 2.2s ease-in-out infinite}
/* 知识库二级分段（不复用主标签下划线，避免两排 tab 视觉混淆） */
.ctfp-seg{display:inline-flex;align-items:center;gap:4px;padding:3px;border-radius:999px;background:var(--dsw-alias-bg-layer-2,#eef1f6);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));align-self:flex-start;max-width:100%;flex-wrap:wrap}
.ctfp-seg-btn{font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;color:var(--dsw-alias-label-secondary,#667085);background:transparent;border:1px solid transparent;border-radius:999px;padding:4px 12px;display:inline-flex;align-items:center;gap:6px;transition:background-color .15s ease,color .15s ease,box-shadow .15s ease}
.ctfp-seg-btn:hover:not(.ctfp-seg-btn--on){color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.10))}
.ctfp-seg-btn--on{color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border-color:var(--dsw-alias-border-l2,rgba(128,128,128,.28));box-shadow:0 1px 2px rgba(0,0,0,.06);font-weight:600}
.ctfp-seg-count{font-size:10.5px;font-weight:600;color:var(--dsw-alias-label-secondary,#667085);background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.12));border-radius:999px;padding:0 6px;font-variant-numeric:tabular-nums}
[data-ds-dark-theme] .ctfp-seg{background:var(--dsw-alias-bg-layer-2,#18181b)}
[data-ds-dark-theme] .ctfp-seg-btn--on{background:var(--dsw-alias-bg-layer-1,#27272a)}
/* 说明性提示条：虚线容器里的纯说明文字（非错误/非加载） */
.ctfp-note--hint{padding:9px 12px;border-style:dashed;border-radius:var(--ctfp-radius-sm);font-size:12.5px}
/* 角色分工说明：两处演进标签各放一条，明确「改什么、不改什么」 */
.ctfp-role-note{font-size:12px;line-height:1.55;color:var(--dsw-alias-label-secondary,#667085);background:var(--dsw-alias-bg-layer-1,#f7f8fa);border:1px dashed var(--dsw-alias-border-l2,rgba(128,128,128,.35));border-radius:var(--ctfp-radius-sm);padding:8px 10px;margin:0 0 10px;white-space:pre-wrap}
/* 诊断输出：等宽、可横向滚动、有界高度（AI 运维可直接整段复制） */
.ctfp-pre{font-family:var(--ctfp-mono);font-size:11.5px;line-height:1.55;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));border-radius:var(--ctfp-radius-sm);padding:9px 11px;margin:8px 0 0;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word}
[data-ds-dark-theme] .ctfp-pre{background:var(--dsw-alias-bg-base,#18181b);color:var(--dsw-alias-label-primary,#e5e7eb)}
/* 题目详情页 */
.ctfp-detail{display:flex;flex-direction:column;gap:14px;padding:4px 2px 8px}
.ctfp-back{display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;font-weight:500;color:var(--dsw-alias-state-business-primary,#3b82f6);background:none;border:none;padding:4px 0;font-family:inherit;text-align:left}
.ctfp-back:hover:not(:disabled){text-decoration:underline}
.ctfp-back:disabled{opacity:.55;cursor:default}
.ctfp-detail__hero{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));border-radius:var(--ctfp-radius-lg);background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:14px 16px;display:flex;flex-direction:column;gap:6px}
.ctfp-detail__nm{font-size:17px;font-weight:680;line-height:1.4;letter-spacing:-.01em}
.ctfp-detail__meta{font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfp-flagbox{display:inline-flex;align-items:center;gap:6px;max-width:100%;flex-wrap:wrap}
.ctfp-detail__flag{font-family:var(--ctfp-mono);font-size:12.5px;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.3));border-radius:6px;padding:2px 9px;display:inline-block;max-width:100%;vertical-align:bottom;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr}
.ctfp-chipok{font-size:11.5px;color:var(--dsw-alias-state-success-primary,#16a34a);border:1px solid transparent;background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 13%, transparent);border-radius:999px;padding:2px 9px;white-space:nowrap}
.ctfp-summary{margin:0;font-size:13px;line-height:1.6;color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));border-radius:8px;padding:8px 10px;white-space:pre-wrap;word-break:break-all;max-height:220px;overflow:auto}
.ctfp-detail__secs h2{margin:0 0 8px;font-size:13.5px;font-weight:600}
.ctfp-attempt{display:grid;grid-template-columns:76px 1fr auto;gap:10px;align-items:start;border-bottom:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.18));padding:7px 2px;font-size:12.5px}
.ctfp-attempt:last-child{border-bottom:none}
.ctfp-att__det{font-weight:600;font-size:12px}
.ctfp-att__detid{color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-att__time{color:var(--dsw-alias-label-secondary,#667085);font-size:11.5px;white-space:nowrap}
.ctfp-att__body{display:flex;flex-direction:column;gap:3px;min-width:0;word-break:break-word}
/* 详情页长列表有界滚动：时间线/经验超出视口时内部滚动，不撑破面板 */
.ctfp-detail__list{display:flex;flex-direction:column;gap:6px;max-height:340px;overflow:auto;padding-right:2px}
.ctfp-att__detail{color:var(--dsw-alias-label-primary,#1a1a1a)}
.ctfp-att__verdict{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap;align-self:flex-start;font-size:11.5px}
.ctfp-lesson{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:8px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:7px 10px;font-size:12.5px;display:flex;flex-direction:column;gap:3px}
.ctfp-lesson b{font-weight:600}
.ctfp-tagbox{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.ctfp-tags{display:inline-flex;gap:5px;flex-wrap:wrap}
.ctfp-tag{font-size:11px;color:var(--dsw-alias-label-secondary,#667085);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35));border-radius:999px;padding:0 8px}
.ctfp-ledger{display:flex;flex-direction:column;gap:6px;max-height:440px;overflow:auto;padding-right:2px}
.ctfp-row--click{cursor:pointer}
.ctfp-row--click:hover{border-color:var(--dsw-alias-state-business-primary,#3b82f6);background:var(--dsw-alias-card-fill-hover,var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.07)))}
.ctfp-row{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:var(--ctfp-radius-sm);background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:8px 10px;display:flex;flex-direction:column;gap:5px;transition:background-color .15s ease,border-color .15s ease}
.ctfp-row--solved{background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 10%, transparent);border-color:var(--dsw-alias-state-success-primary,#22c55e);box-shadow:inset 3px 0 0 var(--dsw-alias-state-success-primary,#22c55e)}
.ctfp-rowtop{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfp-rownm{font-weight:600;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
/* 徽标：shadcn Badge secondary 口径——muted 实底替代描边，语义色仅在小元素上 */
.ctfp-badge{font-size:11px;font-weight:500;border-radius:999px;padding:2px 9px;border:1px solid transparent;background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.12));color:var(--dsw-alias-label-secondary,#667085);white-space:nowrap}
.ctfp-badge--mode{border-color:transparent;background:color-mix(in srgb, var(--dsw-alias-state-business-primary,#3b82f6) 13%, transparent);color:var(--dsw-alias-state-business-primary,#2563eb)}
.ctfp-badge--learn{border-color:transparent;background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.12));color:var(--dsw-alias-label-tertiary,#667085);font-style:italic}
.ctfp-badge--off{opacity:.55}
.ctfp-badge--ok{color:var(--dsw-alias-state-success-primary,#16a34a);background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 13%, transparent);border-color:transparent}
.ctfp-learn-toggle{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--dsw-alias-label-secondary,#667085);cursor:pointer;font-weight:400}
.ctfp-learn-toggle input{margin:0}
.ctfp-dot{width:8px;height:8px;border-radius:50%;background:var(--dsw-alias-label-secondary,#98a2b3);flex:none}
.ctfp-dot--ok{background:var(--dsw-alias-state-success-primary,#22c55e)}
.ctfp-rowfoot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfp-flag{font-family:var(--ctfp-mono);font-size:12.5px;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.3));border-radius:6px;padding:1px 7px;display:inline-block;max-width:100%;vertical-align:bottom;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr}
.ctfp-time{font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);margin-left:auto;white-space:nowrap}
.ctfp-dets{display:flex;flex-direction:column;gap:6px;font-size:12.5px}
/* 失败画像行：3 列网格；语义类（可行动信号）琥珀色强调 */
.ctfp-erow{display:grid;grid-template-columns:minmax(140px,1.6fr) 64px 56px;gap:8px;align-items:center;padding:5px 2px;font-size:12.5px;border-bottom:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.18))}
.ctfp-erow:last-child{border-bottom:none}
.ctfp-erow--sem{color:var(--dsw-alias-state-warning-primary,#b45309)}
.ctfp-detrow{display:grid;grid-template-columns:minmax(96px,1.6fr) 66px 56px 56px 72px;gap:8px;align-items:center;padding:5px 2px;border-bottom:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.18))}
.ctfp-detrow:last-child{border-bottom:none}
.ctfp-dethead{color:var(--dsw-alias-label-secondary,#667085);font-weight:600}
.ctfp-lessons{display:flex;flex-direction:column;gap:6px;max-height:320px;overflow:auto;padding-right:2px}
.ctfp-lesson-dim{color:var(--dsw-alias-label-secondary,#667085);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
/* 工具行：操作按钮 + 计数徽标的标准布局（收编三处 inline style） */
.ctfp-toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.ctfp-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px}
@media (max-width:480px){.ctfp-root{padding:14px}.ctfp-head{flex-direction:column}.ctfp-kpi__v{font-size:19px}.ctfp-catrow{grid-template-columns:84px 1fr auto}.ctfp-detrow{grid-template-columns:1fr 52px 48px 54px 60px;font-size:11.5px}
/* 训练场窄屏：排行列收紧，表单仍整行堆叠 */
.ctfp-arena-head,.ctfp-arena-row{grid-template-columns:86px 64px 58px 60px;font-size:11px}}
/* —— 提交确认卡 —— */
.ctfc-wrap{max-width:560px;width:100%;margin:0 auto;padding:0 4px}
.ctfc-card{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35));border-radius:var(--ctfp-radius-lg);background:var(--dsw-alias-bg-layer-1,#f7f8fa);box-shadow:var(--dsw-elevation-panel);padding:14px 16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.ctfc-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfc-title{font-weight:650;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.ctfc-chip{font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35));border-radius:999px;padding:1px 9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px}
.ctfc-hint{font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085);margin:0}
.ctfc-flag{font-family:var(--ctfp-mono);font-size:12.5px;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.3));border-radius:6px;padding:2px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr}
.ctfc-meta{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:0;font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085)}
.ctfc-meta b{font-weight:600;color:var(--dsw-alias-label-primary,#1a1a1a);word-break:break-all}
.ctfc-footer{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfc-btn{font:inherit;font-size:13px;cursor:pointer;border-radius:var(--ctfp-radius-sm);padding:6px 16px;transition:background-color .15s ease,border-color .15s ease,color .15s ease,opacity .15s ease,transform .1s ease}
.ctfc-btn:active:not(:disabled){transform:translateY(1px)}
.ctfc-btn--primary{border:1px solid transparent;background:var(--dsw-alias-state-business-primary,#3b82f6);color:#fff;font-weight:500}
.ctfc-btn--primary:hover:not(:disabled){filter:brightness(.95)}
/* 深色主题下强调色变浅（business-primary 在 dark 为浅蓝），按钮文字反转为深灰保证对比度。 */
body[data-ds-dark-theme] .ctfc-btn--primary{color:var(--dsw-static-neutral-bluish-900,#111318)}
.ctfc-btn--ghost{border:1px solid var(--dsw-alias-border-l4,rgba(128,128,128,.45));background:transparent;color:var(--dsw-alias-label-primary,#1a1a1a)}
.ctfc-btn--ghost:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.14))}
.ctfc-btn:disabled{opacity:.55;cursor:default}
.ctfc-status{font-size:12.5px;border-radius:8px;padding:6px 10px;margin:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap;white-space:pre-wrap}
.ctfc-status__detail{opacity:.85;font-size:12px}
.ctfc-status--ok{background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 12%, transparent);color:var(--dsw-alias-state-success-primary,#22c55e)}
.ctfc-status--bad{background:rgba(220,38,38,.10);color:#dc2626}
.ctfc-status--wait{color:var(--dsw-alias-label-secondary,#667085)}
.ctfc-status b{font-weight:650}
@media (max-width:390px){.ctfc-chip{max-width:140px}.ctfc-flag{font-size:12px}}

/* —— 三界面：解题表单 / 正在解题状态板 / 工具管理 —— */
.ctfp-dock-card{display:flex;flex-direction:column;gap:8px;padding:10px 12px;margin-top:8px;border:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.25));border-radius:var(--ctfp-radius-lg);background:var(--dsw-alias-bg-layer-02,var(--dsw-alias-bg-layer-01,#fff))}
.ctfp-dock-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.ctfp-dock-title{font-size:13px;font-weight:600;color:var(--dsw-alias-label-primary,#1a1a1a)}
.ctfp-formgrid{display:grid;grid-template-columns:104px 1fr;gap:6px 8px;align-items:start}
.ctfp-formgrid>span{font-size:12px;color:var(--dsw-alias-label-secondary,#667085);padding-top:4px}
.ctfp-formgrid textarea{min-height:58px;resize:vertical;line-height:1.6;font-family:inherit}
.ctfp-attrow{display:flex;align-items:center;gap:8px}
.ctfp-flags{display:flex;flex-wrap:wrap;gap:6px}
.ctfp-sflag{font-family:var(--ctfp-mono);font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.28));color:var(--dsw-alias-label-primary,#1a1a1a);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;overflow-wrap:anywhere}
.ctfp-boards{display:flex;flex-direction:column;gap:10px}
.ctfp-scard{border:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.25));border-radius:var(--ctfp-radius-lg);padding:10px 12px;display:flex;flex-direction:column;gap:8px;cursor:pointer;transition:background-color .15s ease,border-color .15s ease}
.ctfp-scard:hover{border-color:var(--dsw-alias-state-brand-primary,var(--dsw-alias-brand-primary,#3b82f6));background:var(--dsw-alias-card-fill-hover,var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.06)))}
.ctfp-scard__top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfp-scard__meta{font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085);overflow-wrap:anywhere}
.ctfp-scard__acts{display:flex;gap:8px;flex-wrap:wrap}
.ctfp-actions-note{font-size:12px;color:var(--dsw-alias-label-secondary,#667085)}
/* 状态行：圆点 + 状态文字的组合容器（点归点、字归字，两者不可塞进同一元素） */
.ctfp-runstate{display:inline-flex;align-items:center;gap:6px;flex:none;white-space:nowrap}
.ctfp-dot--run{width:9px;height:9px;border-radius:50%;background:var(--dsw-alias-state-warning-primary,#f59e0b);animation:ctfp-pulse 1.8s ease-in-out infinite}
.ctfp-dot--off{background:var(--dsw-alias-label-secondary,#98a2b3)}
@keyframes ctfp-pulse{0%,100%{box-shadow:0 0 0 3px color-mix(in srgb,var(--dsw-alias-state-warning-primary,#f59e0b) 22%,transparent)}50%{box-shadow:0 0 0 6px color-mix(in srgb,var(--dsw-alias-state-warning-primary,#f59e0b) 8%,transparent)}}
.ctfp-toolhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:6px 0 4px;font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);border-bottom:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.25))}
.ctfp-toolrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-bottom:1px dashed var(--dsw-alias-border-l3,rgba(128,128,128,.18));padding:8px 0;font-size:12.5px}
.ctfp-toolbtn{padding:2px 10px;font-size:12px}
.ctfp-toolrow .ctfp-toolname{min-width:128px}
.ctfp-toolrow>.ctfp-badge{flex:none}
.ctfp-toolrow>span.ctfp-toolstate{flex:none;display:inline-flex;align-items:center;gap:6px}
.ctfp-toolrow>.ctfp-tooldetail{flex-basis:100%;margin-top:2px;opacity:.85;word-break:break-all}
@media (max-width:390px){.ctfp-formgrid{grid-template-columns:1fr}.ctfp-toolrow .ctfp-toolname{min-width:104px}.ctfp-sflag{max-width:170px}}
/* v3：状态卡整卡可点（同 dsh-context 卡片交互）；题面/flag 长文溢出收敛；历史删除键 */
.ctfp-scard{position:relative}
.ctfp-scard--done{cursor:default}
.ctfp-scard--done:hover{border-color:var(--dsw-alias-border-l3,rgba(128,128,128,.25))}
.ctfp-scard__del{position:absolute;top:8px;right:10px;font-size:11px;line-height:1;opacity:.5;padding:4px 6px}
.ctfp-scard__del:hover{opacity:1}
/* v3：开始解题表单深色跟随——.ctfp-formlab/.ctfp-input-area 原无样式规则，
 * 原生控件不受主题 token 管辖，补齐 token 化规则后才随深色模式切换 */
.ctfp-formlab{display:block;font-size:12px;color:var(--dsw-alias-label-secondary,#667085);margin-top:6px}
.ctfp-input-area{width:100%;min-height:58px;resize:vertical;line-height:1.6;font-family:inherit;font-size:13.5px;color:var(--dsw-alias-label-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-l4,rgba(128,128,128,.45));border-radius:var(--ctfp-radius-sm);padding:7px 10px;box-sizing:border-box;transition:border-color .15s ease}
.ctfp-input-area:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,#3b82f6);outline-offset:1px}
/* v3 修复：深色模式强制覆盖——即使 DSW token 未在 dark 下重新赋值也能显式跟随。
 * 现有代码仅在 .ctfc-btn--primary 上有一条 data-ds-dark-theme 规则（line 676），
 * 表单其余原生控件缺少覆盖，导致深色下回退到 #fff 兜底白底。 */
[data-ds-dark-theme] .ctfp-dock-card{background:var(--dsw-alias-bg-layer-02,#1e1e2e);border-color:var(--dsw-alias-border-l3,#3f3f46)}
[data-ds-dark-theme] .ctfp-dock-title{color:var(--dsw-alias-label-primary,#e5e7eb)}
[data-ds-dark-theme] .ctfp-dock-note{color:var(--dsw-alias-label-secondary,#9ca3af)}
[data-ds-dark-theme] .ctfp-formlab{color:var(--dsw-alias-label-secondary,#9ca3af)}
[data-ds-dark-theme] .ctfp-formgrid>span{color:var(--dsw-alias-label-secondary,#9ca3af)}
[data-ds-dark-theme] .ctfp-input-area{background:var(--dsw-alias-bg-base,#1e1e2e);color:var(--dsw-alias-label-primary,#e5e7eb);border-color:var(--dsw-alias-border-l4,#3f3f46)}
[data-ds-dark-theme] .ctfp-input-area:focus-visible{outline-color:var(--dsw-alias-state-business-primary,#60a5fa)}
[data-ds-dark-theme] .ctfp-attrow .ctfp-search,[data-ds-dark-theme] .ctfp-filter-sel,[data-ds-dark-theme] .ctfp-search{background:var(--dsw-alias-bg-base,#1e1e2e);color:var(--dsw-alias-label-primary,#e5e7eb);border-color:var(--dsw-alias-border-l4,#3f3f46)}
[data-ds-dark-theme] .ctfp-attrow .ctfp-search:focus-visible,[data-ds-dark-theme] .ctfp-filter-sel:focus-visible,[data-ds-dark-theme] .ctfp-search:focus-visible{outline-color:var(--dsw-alias-state-business-primary,#60a5fa)}
[data-ds-dark-theme] .ctfp-flags code{background:var(--dsw-alias-bg-base,#1e1e2e);color:var(--dsw-alias-label-primary,#e5e7eb);border-color:var(--dsw-alias-border-l2,#3f3f46)}
[data-ds-dark-theme] .ctfp-tooldetail{color:var(--dsw-alias-label-secondary,#9ca3af)}
[data-ds-dark-theme] .ctfp-scard{border-color:var(--dsw-alias-border-l3,#3f3f46)}
[data-ds-dark-theme] .ctfp-scard--done:hover{border-color:var(--dsw-alias-border-l3,#3f3f46)}
[data-ds-dark-theme] .ctfp-scard__del{color:var(--dsw-alias-label-secondary,#9ca3af)}
[data-ds-dark-theme] .ctfp-scard__del:hover{color:var(--dsw-alias-label-primary,#e5e7eb)}
[data-ds-dark-theme] .ctfp-sflag{color:var(--dsw-alias-label-primary,#e5e7eb);border-color:var(--dsw-alias-border-l3,#3f3f46)}
`

    /* <style> 描述同一元素在各树间复用：内容幂等，多份渲染无副作用。 */
    let styleElement = null
    function Styles() {
      if (styleElement === null) {
        styleElement = h('style', { id: STYLE_ID, 'data-plugin': 'dsh-ctfpilot' }, STYLE_TEXT)
      }
      return styleElement
    }

    /* ============================================================
     * 侧栏面板图标：雷达罗盘 + 旗帜/终端混合标记（纯填充图形）
     * ============================================================ */

    function CtfpilotPanelIcon(props) {
      const size = props && props.size ? num(props.size, 24) : 24
      // currentColor 跟随宿主图标配色：亮暗主题自动适配（原先硬编码 #3b82f6 不随主题）。
      return h(
        'svg',
        { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': 'true', style: { color: 'inherit' } },
        // 罗盘外圈
        h('circle', { cx: 12, cy: 12, r: 8.6, stroke: 'currentColor', 'stroke-width': 2 }),
        // 雷达扇形扫描（当前色降透明度）
        h('path', { d: 'M12 12L16.45 7.55A6.3 6.3 0 0 1 16.45 16.45Z', fill: 'currentColor', opacity: 0.22 }),
        // 扫描回波亮点
        h('circle', { cx: 14.8, cy: 10.4, r: 1, fill: 'currentColor' }),
        h('circle', { cx: 10.2, cy: 14.8, r: 1.25, fill: 'currentColor' }),
        // 旗杆 + 三角旗（终端窗口意象）
        h('rect', { x: 14.8, y: 5.2, width: 1.5, height: 11, rx: 0.75, fill: 'currentColor' }),
        h('path', { d: 'M16.35 6.6L19.6 8.15L16.35 9.7Z', fill: 'currentColor' }),
      )
    }

    /* ============================================================
     * 看板数据归一（Host /ctf-dashboard 结果的防御性解析）
     * ============================================================ */

    function normalizeBlob(raw) {
      const root = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
      const rawStats = root.stats && typeof root.stats === 'object' ? root.stats : {}
      const challs = Array.isArray(root.challs)
        ? root.challs
            .filter((c) => c && typeof c === 'object')
            .map((c) => ({
              id: str(c.id, ''),
              name: str(c.name, str(c.id, '')),
              cat: str(c.cat, ''),
              mode: str(c.mode, ''),
              solved: truthy(c.solved),
              flag: str(c.flag, ''),
              time: str(c.time, ''),
              // 'learn' = 只有学 writeup 的导入记录，不是真实解题；台账默认隐藏。
              kind: c.kind === 'learn' ? 'learn' : 'solve',
            }))
        : []
      const bycat = Array.isArray(root.bycat)
        ? root.bycat
            .filter((c) => c && typeof c === 'object')
            .map((c) => ({ cat: str(c.cat, ''), total: num(c.total, 0), solved: num(c.solved, 0) }))
        : []
      const lessons = Array.isArray(root.lessons)
        ? root.lessons
            .filter((l) => l && typeof l === 'object')
            .map((l) => ({
              cat: str(l.cat, ''),
              tech: str(l.tech, ''),
              src: str(l.src, ''),
              snippet: str(l.snippet, ''),
              time: str(l.time, ''),
            }))
        : []
      const detectors = Array.isArray(root.detectors)
        ? root.detectors
            .filter((d) => d && typeof d === 'object')
            .map((d) => ({
              phase: str(d.phase, ''),
              detector: str(d.detector, ''),
              attempts: num(d.attempts, 0),
              hits: num(d.hits, 0),
            }))
        : []
      const modes = Array.isArray(root.modes)
        ? root.modes.filter((m) => m && typeof m === 'object')
        : []
      const errors = Array.isArray(root.errors)
        ? root.errors
            .filter((e) => e && typeof e === 'object')
            .map((e) => ({
              cls: str(e.cls, e.class || ''),
              count: num(e.count, 0),
              ok: num(e.ok, 0),
            }))
        : []

      // 缺项兜底：总数/已解从台账回填；通过率缺失时按已解/总数计算。
      const fallbackSolved = challs.reduce((n, c) => n + (c.solved ? 1 : 0), 0)
      const total = num(rawStats.total, challs.length)
      const solved = num(rawStats.solved, fallbackSolved)
      const rate =
        rawStats.rate === undefined || rawStats.rate === null || rawStats.rate === ''
          ? total > 0
            ? (solved / total) * 100
            : 0
          : num(rawStats.rate, 0)
      const stats = {
        total,
        solved,
        rate,
        checkpoints: num(rawStats.checkpoints, 0),
        lessons: num(rawStats.lessons, lessons.length),
        attempts: num(rawStats.attempts, 0),
        distinct: num(rawStats.distinct, 0),
        challs: num(rawStats.challs, challs.length),
      }
      return { stats, bycat, challs, lessons, detectors, modes, errors }
    }

    /** 台账搜索串缓存：每行 name/cat/mode/flag 只小写拼接一次，输入时纯
     *  includes；WeakMap 不改数据形状，归一化对象被替换后自动回收。 */
    const challSearchCache = new WeakMap()
    function challSearchText(c) {
      let s = challSearchCache.get(c)
      if (s === undefined) {
        s = [c.name, c.cat, c.mode, c.flag].join('\n').toLowerCase()
        challSearchCache.set(c, s)
      }
      return s
    }

    /** 经验卡搜索串缓存：技术/分类/来源/摘要只拼接一次；输入时纯 includes。 */
    const lessonSearchCache = new WeakMap()
    function lessonSearchText(les) {
      let s = lessonSearchCache.get(les)
      if (s === undefined) {
        s = [les.cat, les.tech, les.src, les.snippet].join('\n').toLowerCase()
        lessonSearchCache.set(les, s)
      }
      return s
    }

    // 模块加载即恢复持久化看板：UI 重启后首帧直接渲染上次数据（stale-while-
    // revalidate 首步），后台再校验由 loadPanel 节流控制。注意必须放在
    // num/str/truthy 等 const 小工具与 normalizeBlob 定义之后执行——
    // normalizeBlob 内部引用它们，提前调用会踩 TDZ（浏览器二次启动必炸）。
    dashboardCache = readPersistedDashboard()

    // —— 通用面板数据缓存（2026-11-20 全界面推广：官方 sidebar-files 的
    // ready+refreshing 语义——数据在模块级 store 不在组件状态，重挂只读快照；
    // stale-while-revalidate：缓存命中立即上屏，节流窗口外后台静默再校验，
    // 内容级比较替换；有缓存时后台失败保留旧数据显示，无缓存才 error 显形）。
    // research-list / rsi-list / arena-stats / tools-status 持久化到 localStorage
    // （UI 重启后首帧直接渲染）；detail:<题> 仅内存（数据大且点击式进入）。
    const PANEL_REVALIDATE_MS = 30_000
    const PANELS_STORE_KEY = 'ctfpilot.panels.v1'
    const PERSISTED_PANEL_KEYS = new Set(['research-list', 'rsi-list', 'arena-stats', 'tools-status'])
    /** 主标签持久化键：交付级体验——重开面板落在上次所在屏；首访默认「开始解题」。 */
    const TAB_STORE_KEY = 'ctfpilot.tab.v1'
    const TAB_KEYS = new Set(['solve', 'solving', 'tools', 'knowledge', 'refinery', 'evolution'])
    /** key → { data: 归一化结果, fingerprint: string, at: number }。 */
    const panelCache = readPersistedPanels()
    /** 各 key 在途请求防重入。 */
    const panelInFlight = new Set()
    /** 面板缓存订阅者：主面板标签角标（运行中/缺装/考核/待审）据此即时刷新。 */
    let panelCacheListeners = []
    function notifyPanelCacheChanged() {
      for (const fn of [...panelCacheListeners]) fn()
    }
    function subscribePanelCache(fn) {
      panelCacheListeners.push(fn)
      return () => { panelCacheListeners = panelCacheListeners.filter((x) => x !== fn) }
    }
    function readPersistedTab() {
      try {
        const v = localStorage.getItem(TAB_STORE_KEY)
        return TAB_KEYS.has(String(v)) ? String(v) : 'solve'
      } catch { return 'solve' }
    }
    function persistTab(key) {
      try { localStorage.setItem(TAB_STORE_KEY, String(key)) } catch { /* 隐私模式：仅本次会话 */ }
    }

    function readPersistedPanels() {
      try {
        const raw = localStorage.getItem(PANELS_STORE_KEY)
        const saved = raw ? JSON.parse(raw) : null
        const out = new Map()
        if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
          for (const [k, v] of Object.entries(saved)) {
            if (!PERSISTED_PANEL_KEYS.has(k) || !v || typeof v !== 'object') continue
            const data = v.data
            const shapeOk = Array.isArray(data) || (data && typeof data === 'object')
            if (!shapeOk || typeof v.at !== 'number' || !Number.isFinite(v.at)) continue
            out.set(k, {
              data,
              fingerprint: typeof v.fingerprint === 'string' ? v.fingerprint : JSON.stringify(data),
              at: v.at,
            })
          }
        }
        return out
      } catch {
        return new Map() // localStorage 不可用（Node 测试/隐私模式）：仅内存缓存
      }
    }

    function persistPanels() {
      try {
        const out = {}
        for (const key of PERSISTED_PANEL_KEYS) {
          const cur = panelCache.get(key)
          if (cur !== undefined) out[key] = { data: cur.data, fingerprint: cur.fingerprint, at: cur.at }
        }
        localStorage.setItem(PANELS_STORE_KEY, JSON.stringify(out))
      } catch { /* 配额/隐私模式：内存缓存仍有效 */ }
    }

    /** 读缓存：命中返回 { data, fingerprint, at }，无则 null。 */
    function panelCacheGet(key) {
      const cur = panelCache.get(key)
      return cur === undefined ? null : cur
    }

    /** 写缓存并持久化；返回内容是否变化（未变时调用方跳过 setState 防抖动）。 */
    function panelCacheSet(key, data) {
      const fingerprint = JSON.stringify(data)
      const prev = panelCache.get(key)
      const changed = prev === undefined || prev.fingerprint !== fingerprint
      panelCache.set(key, { data, fingerprint, at: Date.now() })
      if (changed) notifyPanelCacheChanged()
      persistPanels()
      return changed
    }

    // 测试钩子（verify-client 专用）：清空面板缓存 + 持久层，模拟「无缓存
    // 首挂」场景。真实环境无人调用；挂 window（client.js 无导出面），
    // 与 lib/index.js 的 resetPkgManagerCache 同款「仅测试用」惯例。
    try {
      window.__ctfpilotPanelCacheReset__ = () => {
        panelCache.clear()
        try { localStorage.removeItem(PANELS_STORE_KEY) } catch (_e) { /* localStorage 不可用 */ }
      }
    } catch (_e2) { /* window 不可用：跳过 */ }

    /* ============================================================
     * Feature 1 —— 侧栏全局面板（CtfpilotPanel）
     * ============================================================ */

    function KpiCard(props) {
      // 数字与单位基线对齐分排：大数字 23px + 单位 11.5px 灰（原先 `${value}${unit}`
      // 同字号同字重拼在一起，是排版最丑的一处）。
      return h(
        'div',
        { className: 'ctfp-kpi' },
        h(
          'div',
          { className: 'ctfp-kpi__v' },
          props.value,
          props.unit ? h('span', { className: 'ctfp-kpi__u' }, props.unit) : null,
        ),
        h('span', { className: 'ctfp-kpi__l' }, props.label),
      )
    }

    function CategoryRow(props) {
      const row = props.row
      const t = props.t
      const total = num(row.total, 0)
      const solved = num(row.solved, 0)
      const pct = total > 0 ? Math.min(100, (solved / total) * 100) : 0
      return h(
        'div',
        { className: 'ctfp-catrow' },
        h(
          'span',
          { className: 'ctfp-catname', title: row.cat || t('colCat') },
          row.cat || t('colCat'),
        ),
        h('div', { className: 'ctfp-bar' }, h('div', { className: 'ctfp-barfill', style: { width: `${pct}%` } })),
        h('span', { className: 'ctfp-catcount' }, `${solved}/${total}`),
      )
    }

    function ModeRow(props) {
      const row = props.row
      const t = props.t
      const pct = Math.min(100, (num(row.count, 0) / Math.max(1, num(props.total, 0))) * 100)
      return h(
        'div',
        { className: 'ctfp-catrow' },
        h('span', { className: 'ctfp-catname', title: row.mode || t('colMode') }, row.mode || t('colMode')),
        h('div', { className: 'ctfp-bar' }, h('div', { className: 'ctfp-barfill', style: { width: `${pct}%` } })),
        h('span', { className: 'ctfp-catcount' }, String(num(row.count, 0))),
      )
    }

    function ErrorRow(props) {
      const er = props.er
      const t = props.t
      const count = num(er.count, 0)
      const ok = num(er.ok, 0)
      // 语义类（semantic:*）是引擎 taxonomy v2 的可行动信号，用琥珀色；
      // 纯过程类（exec:* 等）为浅灰，视觉上区分优先级。
      const semantic = er.cls.indexOf('semantic:') === 0
      return h(
        'div',
        {
          className: 'ctfp-erow' + (semantic ? ' ctfp-erow--sem' : ''),
          title: semantic ? t('errClassTitle') : er.cls,
        },
        h('span', { title: er.cls }, er.cls || '—'),
        h('span', null, String(count)),
        h('span', null, ok ? String(ok) : t('noHitRate')),
      )
    }

    function LedgerRow(props) {
      const ch = props.ch
      const t = props.t
      const clickable = typeof props.onClick === 'function'
      return h(
        'div',
        {
          className:
            (ch.solved ? 'ctfp-row ctfp-row--solved' : 'ctfp-row') +
            (clickable ? ' ctfp-row--click' : ''),
          onClick: clickable ? () => { props.onClick(ch) } : undefined,
          role: clickable ? 'button' : undefined,
          tabIndex: clickable ? 0 : undefined,
          onKeyDown: clickable
            ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); props.onClick(ch) } }
            : undefined,
        },
        h(
          'div',
          { className: 'ctfp-rowtop' },
          h('span', { className: ch.solved ? 'ctfp-dot ctfp-dot--ok' : 'ctfp-dot', 'aria-label': ch.solved ? t('solved') : t('unsolvedMark') }),
          h('span', { className: 'ctfp-rownm', title: ch.name }, ch.name),
          ch.cat ? h('span', { className: 'ctfp-badge', title: t('colCat') }, ch.cat) : null,
          ch.mode ? h('span', { className: 'ctfp-badge ctfp-badge--mode', title: t('colMode') }, ch.mode) : null,
          ch.kind === 'learn' ? h('span', { className: 'ctfp-badge ctfp-badge--learn', title: t('learnOnlyTitle') }, t('learnBadge')) : null,
        ),
        h(
          'div',
          { className: 'ctfp-rowfoot' },
          ch.flag
            ? h('code', { className: 'ctfp-flag', title: ch.flag }, ch.flag)
            : h('span', { className: 'ctfp-flag' }, '—'),
          ch.time ? h('span', { className: 'ctfp-time' }, ch.time) : null,
        ),
      )
    }

    function DetectorRow(props) {
      const det = props.det
      const t = props.t
      const attempts = det.attempts
      const hits = det.hits
      const hitRate = attempts > 0 ? `${Math.round((hits / attempts) * 100)}%` : t('noHitRate')
      return h(
        'div',
        { className: 'ctfp-detrow' },
        h('span', { title: det.detector }, det.detector || '—'),
        h('span', { title: t('colPhase') }, det.phase || '—'),
        h('span', null, String(attempts)),
        h('span', null, String(hits)),
        h('span', { className: 'ctfp-catcount' }, hitRate),
      )
    }

    function LessonItem(props) {
      const les = props.les
      const head = [les.cat, les.tech].filter(Boolean).join(' · ') || les.src || '—'
      const detail = [les.src, les.time].filter(Boolean).join(' · ')
      return h(
        'div',
        { className: 'ctfp-lesson' },
        h('div', null, h('b', null, head)),
        detail ? h('div', { className: 'ctfp-lesson-dim' }, detail) : null,
        les.snippet ? h('div', { className: 'ctfp-lesson-dim' }, les.snippet) : null,
      )
    }

    /**
     * 单题详情归一化：/ctf-challenge-detail 返回的原始记录的防御性解析。
     */
    function normalizeRecord(raw) {
      const root = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
      const attempts = Array.isArray(root.attempts)
        ? root.attempts
            .filter((a) => a && typeof a === 'object')
            .map((a) => ({
              phase: str(a.phase, ''),
              detector: str(a.detector, ''),
              detail: str(a.detail, ''),
              success: truthy(a.success),
              flag: str(a.flag, ''),
              error_class: str(a.error_class, ''),
              time: str(a.time, ''),
            }))
        : []
      const lessons = Array.isArray(root.lessons)
        ? root.lessons
            .filter((l) => l && typeof l === 'object')
            .map((l) => ({
              tech: str(l.tech, ''),
              src: str(l.src, ''),
              snippet: str(l.snippet, ''),
              tags: str(l.tags, ''),
              time: str(l.time, ''),
            }))
        : []
      return {
        id: str(root.id, ''),
        name: str(root.name, ''),
        category: str(root.category, ''),
        mode: str(root.mode, ''),
        solved: truthy(root.solved),
        flag: str(root.flag, ''),
        summary: str(root.summary, ''),
        createdAt: str(root.created_at, ''),
        attempts,
        lessons,
        attemptCount: num(root.attempt_count, attempts.length),
      }
    }

    /**
     * 题目详情页：该题元数据 + 解题时间线 + 本题经验。
     * 数据来自 /ctf-challenge-detail 命令（按 id 或 name 取最新一条记录），
     * 显式 loading / error 状态面；返回看板由 onBack 驱动。
     */
    function ChallengeDetail(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const ch = props.challenge && typeof props.challenge === 'object' ? props.challenge : null
      const [record, setRecord] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok
      // 重取计数器：失败后点「重试」递增，驱动 useEffect 重跑。
      const [attempt, setAttempt] = useState(0)

      useEffect(() => {
        let alive = true
        // 详情缓存（仅内存）：返回看板再进同一题零等待；重试（attempt 递增）强制再校验。
        const cacheKey = `detail:${isNonEmptyString(ch.id) ? 'id:' + ch.id : 'name:' + String(ch.name || '')}`
        const cached = panelCacheGet(cacheKey)
        if (cached !== null) {
          setRecord(cached.data)
          setStatus('ok')
        }
        const force = attempt > 0
        if (!force && cached !== null && Date.now() - cached.at < PANEL_REVALIDATE_MS) {
          return () => { alive = false }
        }
        ;(async () => {
          try {
            const cmd = isNonEmptyString(ch.id)
              ? `/ctf-challenge-detail --id ${ch.id}`
              : `/ctf-challenge-detail --name "${String(ch.name || '').replace(/"/g, '')}"`
            const out = await panelDataFetch(cmd)
            if (!alive) return
            const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
            const result = value && value.result && typeof value.result === 'object' ? value.result : null
            if (result && result.kind === 'success' && result.text !== undefined) {
              const parsed = JSON.parse(result.text)
              const rec = normalizeRecord(parsed)
              const changed = panelCacheSet(cacheKey, rec)
              if (!alive) return
              if (changed) setRecord(rec)
              setStatus('ok')
            } else {
              // 无缓存：失败显形；有缓存：保留旧数据（后台校验失败不打断显示）。
              if (alive && cached === null) setStatus('error')
            }
          } catch (_err) {
            if (alive) {
              if (cached === null) setStatus('error')
              else { try { console.error('[ctfpilot] detail revalidate failed:', _err) } catch (_c) { /* console 不可用 */ } }
            }
          }
        })()
        return () => { alive = false }
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [ch, attempt])

      if (status === 'loading' && !record) {
        return h(
          'div',
          { className: 'ctfp-note', role: 'status' },
          h(
            'button',
            { className: 'ctfc-btn ctfc-btn--ghost', type: 'button', onClick: () => { if (props.onBack) props.onBack() } },
            t('detailBack'),
          ),
        )
      }
      if (status === 'error' && !record) {
        return h(
          'div',
          { className: 'ctfp-note', role: 'alert' },
          h(
            'button',
            { className: 'ctfc-btn ctfc-btn--ghost', type: 'button', onClick: () => { if (props.onBack) props.onBack() } },
            t('detailBack'),
          ),
          h('div', null, t('detailFailed')),
          h(
            'button',
            { className: 'ctfp-btn', style: { marginLeft: '10px' }, type: 'button', onClick: () => { setAttempt((n) => n + 1) } },
            t('retry'),
          ),
        )
      }

      const rec = record
      return h(
        'div',
        { className: 'ctfp-detail' },
        h(
          'button',
          {
            className: 'ctfp-back',
            type: 'button',
            onClick: () => { if (props.onBack) props.onBack() },
          },
          t('detailBack'),
        ),
        h(
          'div',
          { className: 'ctfp-detail__hero' },
          h('div', { className: 'ctfp-detail__nm' }, rec.name || ch.name || t('detailTitle')),
          h(
            'div',
            { className: 'ctfp-detail__meta' },
            rec.category ? h('span', { className: 'ctfp-badge', title: t('colCat') }, rec.category) : null,
            rec.mode ? h('span', { className: 'ctfp-badge ctfp-badge--mode' }, `${t('detailModeLabel')}: ${rec.mode}`) : null,
            rec.createdAt ? h('span', null, t('detailMeta', { t: rec.createdAt })) : null,
            rec.solved ? h('span', { className: 'ctfp-chipok' }, `✓ ${t('solved')}`) : null,
          ),
          rec.flag
            ? h('div', { className: 'ctfp-flagbox' },
                h('code', { className: 'ctfp-detail__flag' }, rec.flag))
            : null,
          rec.summary ? h('p', { className: 'ctfp-summary' }, rec.summary) : null,
        ),
        h(
          'section',
          { className: 'ctfp-section' },
          h(
            'h2',
            null,
            t('attemptsTitle'),
            h('span', { className: 'ctfp-catcount' }, t('attemptsCount', { n: rec.attemptCount })),
          ),
          rec.attempts.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('noAttempts'))
            : h(
                'div',
                { className: 'ctfp-detail__list' },
                rec.attempts.map((a, i) =>
                h(
                  'div',
                  { key: `att-${i}-${a.detector || 'x'}`, className: 'ctfp-attempt' },
                  h('div', null,
                    h('span', { className: 'ctfp-att__det' }, a.detector || '—'),
                    h('div', { className: 'ctfp-att__detid' }, a.phase || '—')),
                  h('div', { className: 'ctfp-att__body' },
                    a.detail ? h('span', { className: 'ctfp-att__detail' }, a.detail) : null,
                    h('span', { className: 'ctfp-att__verdict' },
                      a.success
                        ? h('span', { className: 'ctfp-badge ctfp-badge--ok' }, `✓ ${t('attemptHit')}`)
                        : h('span', { className: 'ctfp-badge' }, `✗ ${t('attemptMiss')}`),
                      a.error_class ? h('span', { className: 'ctfp-badge' }, a.error_class) : null,
                      a.flag ? h('code', { className: 'ctfp-flag' }, a.flag) : null)),
                  a.time ? h('span', { className: 'ctfp-att__time' }, a.time) : null,
                ),
              ),
              ),
        ),
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('lessonsForTitle')),
          rec.lessons.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('noLessons'))
            : h(
                'div',
                { className: 'ctfp-detail__list' },
                rec.lessons.map((les, i) =>
                h(
                  'div',
                  { key: `dles-${i}-${les.tech || 'x'}`, className: 'ctfp-lesson' },
                  h('div', null, h('b', null, les.tech || t('lessonsTitle'))),
                  les.src ? h('div', { className: 'ctfp-lesson-dim' }, les.src) : null,
                  les.snippet ? h('div', { className: 'ctfp-lesson-dim' }, les.snippet) : null,
                  les.tags ? h('div', { className: 'ctfp-tagbox' },
                    h('span', { className: 'ctfp-tags' },
                      parseLessonTags(les.tags).map((tag) => h('span', { key: `tag-${tag}`, className: 'ctfp-tag' }, tag)))) : null,
                ),
              ),
              ),
          ),
      )
    }

    /**
     * 引擎练兵榜原始文本归一化（/ctf-arena stats 的 stdout 形态）。
     * 返回 { overall, items:[{category,solved,attempted,rate,easy,normal,hard}] }，
     * 缺失字段一律兜底（防御解析，与主面板 normalizeBlob 同纪律）。
     */
    function normalizeArena(rawText) {
      const text = String(rawText == null ? '' : rawText)
      const lines = text.split(/\r?\n/)
      let overall = null
      const items = []
      for (const line of lines) {
        if (/^\s*==.*==/.test(line)) continue
        const ov = line.match(/^\s*overall\s*:\s*(\d+)\/(\d+)\s*solved\s*\(([\d.]+)%\)/i)
        if (ov) {
          overall = { solved: +ov[1], attempted: +ov[2], rate: +ov[3] }
          continue
        }
        const cat = line.match(
          /^\s*([A-Za-z][\w-]*)\s+(\d+)\/(\d+)\s+solved\s*\(([\d.]+)%\)\s+tiers\s+easy=(\d+)\s+normal=(\d+)\s+hard=(\d+)/,
        )
        if (cat) {
          items.push({
            category: cat[1].toLowerCase(),
            solved: +cat[2],
            attempted: +cat[3],
            rate: +cat[4],
            easy: +cat[5],
            normal: +cat[6],
            hard: +cat[7],
          })
        }
      }
      items.sort((a, b) => b.solved - a.solved || b.attempted - a.attempted)
      return { overall, items }
    }

    /** 研究专题状态 → i18n 键（queued/drafting/ready-ingest/ingested，research.py TOPIC_STATUSES）。 */
    const RESEARCH_STATUSES = {
      queued: 'researchStQueued',
      drafting: 'researchStDrafting',
      'ready-ingest': 'researchStReady',
      ingested: 'researchStIngested',
    }

    /** RSI 提案状态 → 生命周期配色类（灰→黄→绿；rejected 划线红）。 */
    const RSI_STATUSES = {
      proposed: 'rsiStProposed',
      approved: 'rsiStApproved',
      adopted: 'rsiStAdopted',
      rejected: 'rsiStRejected',
    }
    function rsiBadgeCls(status) {
      if (status === 'proposed') return 'ctfp-sev ctfp-sev--off'
      if (status === 'approved') return 'ctfp-sev ctfp-sev--yel'
      if (status === 'adopted') return 'ctfp-sev ctfp-sev--grn'
      if (status === 'rejected') return 'ctfp-sev ctfp-sev--rej'
      return 'ctfp-sev ctfp-sev--off'
    }

    /** 归一化 /ctf-research list 输出：- [id] topic | 分类 | evidence N/2 | 状态 */
    function normalizeResearch(rawText) {
      const text = String(rawText == null ? '' : rawText)
      const items = []
      for (const line of text.split(/\r?\n/)) {
        const hit = line.match(
          /^\s*-\s*\[([^\]]+)\]\s*(.*?)\s*\|\s*(.*?)\s*\|\s*evidence\s*(\d+)\/(\d+)\s*\|\s*([\w-]+)/i,
        )
        if (!hit) continue
        items.push({
          id: hit[1],
          topic: hit[2],
          category: String(hit[3] || '').toLowerCase(),
          evidence: +hit[4],
          evidenceNeed: +hit[5],
          status: hit[6],
        })
      }
      return items
    }

    /** 归一化 /ctf-rsi list 输出：- [id] type | target -> status | why（why 截断保留，供悬停提示）。 */
    function normalizeRsi(rawText) {
      const text = String(rawText == null ? '' : rawText)
      const items = []
      for (const line of text.split(/\r?\n/)) {
        const head = line.match(/^\s*-\s*\[([^\]]+)\]\s*(.*)$/)
        if (!head) continue
        const segs = String(head[2]).split(/\s*\|\s*/)
        const mid = segs[1] || ''
        const arrow = mid.match(/^(.*?)\s*->\s*([\w-]+)$/)
        items.push({
          id: head[1],
          type: segs[0] || '',
          target: arrow ? arrow[1] : mid,
          status: arrow ? arrow[2] : '',
          why: segs.slice(2).join(' | ').trim().slice(0, 200),
        })
      }
      return items
    }

    /**
     * 系统演进（第六标签）：工装体检 + RSI 提案流 + 知识研究队列。
     * 定位：只演进工装本身（插件 / 预设 / 运行时接线），题库资产的优化在
     * 「题库精炼」。数据来自 /ctf-doctor、/ctf-rsi list、/ctf-research list；
     * 人审决策走 /ctf-rsi approve|reject|adopt PID --note …（采纳强制附带 eval-suite 提示）。
     * 自加载组件（ArenaPanel/ChallengeDetail 同款模式），不扰动主面板拉取；
     * 决策按钮走 state+ref 双写防双击；任一数据源失败只该卡内联报错，另一卡照常渲染；
     * 决策失败显式上屏引擎返回信息并附同动作重试（lastDecision）。
     */
    function SelfEvoPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [researchRows, setResearchRows] = useState([])
      const [rsiRows, setRsiRows] = useState([])
      const [researchErr, setResearchErr] = useState('')
      const [rsiErr, setRsiErr] = useState('')
      const [loaded, setLoaded] = useState(false)
      const [okMsg, setOkMsg] = useState('')
      const [adoptedNote, setAdoptedNote] = useState('')
      const [busy, setBusy] = useState(false)
      const busyRef = useRef(false) // 一次性守卫：state+ref 双写，不随闭包过期
      const lastDecision = useRef(null) // {action, pid}：失败后同动作重试
      // 工装体检（只读）：doctor / 错误日志 / 预设审计三键共用一块输出区。
      const [opsText, setOpsText] = useState('')
      const [opsKind, setOpsKind] = useState('') // '' | 'ok' | 'bad'
      const [opsBusy, setOpsBusy] = useState(false)
      const [opsCopied, setOpsCopied] = useState(false)

      async function runOps(cmd, label) {
        if (opsBusy) return
        setOpsBusy(true)
        setOpsKind('')
        setOpsText('')
        setOpsCopied(false)
        try {
          // 带当前会话：预设审计能读到 turnBoundary 锁定态；doctor/logs 无副作用。
          const out = await panelDataFetch(cmd, { sid: currentSessionId })
          const oc = remoteOutcome(out)
          if (oc.ok) {
            setOpsText(String(oc.text || '(no output)'))
            setOpsKind('ok')
          } else {
            setOpsText(`${t('opsFailed')}${String(oc.text || '(no detail)')}`)
            setOpsKind('bad')
          }
        } catch (e) {
          setOpsText(`${t('opsFailed')}${String((e && e.message) || e)}`)
          setOpsKind('bad')
        } finally {
          setOpsBusy(false)
          try { console.log('[ctfpilot] ops action:', label) } catch (_c) { /* console 不可用 */ }
        }
      }

      async function copyOps() {
        if (!opsText) return
        try {
          if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(opsText)
            setOpsCopied(true)
            setTimeout(() => { try { setOpsCopied(false) } catch (_e) { /* 已卸载 */ } }, 2000)
          }
        } catch (_e) { /* 剪贴板不可用：文本仍可直接选中复制 */ }
      }

      async function loadCard(cmd, setRows, setErr, force) {
        const key = cmd === '/ctf-research list' ? 'research-list'
          : cmd === '/ctf-rsi list' ? 'rsi-list' : cmd
        // 缓存命中：立即上屏归一化行并清该卡错误条（旧数据已可见）。
        const cached = panelCacheGet(key)
        if (cached !== null) {
          setRows((prev) => (prev === cached.data ? prev : cached.data))
          setErr('')
        }
        // 节流窗口内跳过（防订阅风暴）；force（决策/重试后）绕过。
        if (!force && cached !== null && Date.now() - cached.at < PANEL_REVALIDATE_MS) {
          return cached.data
        }
        if (panelInFlight.has(key)) return cached !== null ? cached.data : []
        panelInFlight.add(key)
        try {
          const out = await panelDataFetch(cmd)
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          // 非 success 视为拉取失败：无缓存时该卡内联报错，绝不静默清空另一卡的数据。
          if (!result || result.kind !== 'success') throw new Error(String(result && result.text ? result.text : 'self-evolution call failed'))
          const text = result.text
          const norm = String(cmd).startsWith('/ctf-research') ? normalizeResearch(text) : normalizeRsi(text)
          const changed = panelCacheSet(key, norm)
          if (changed) setRows(norm)
          return norm
        } catch (err) {
          // 有缓存：保留已上屏数据，失败记 console（Host 侧 logError 已落盘双通道）；
          // 无缓存：单源失败只亮该卡错误条，另一卡照常渲染。
          if (cached === null) setErr(err && err.message ? err.message : t('errorState'))
          else { try { console.error('[ctfpilot] ' + key + ' revalidate failed:', err) } catch (_c) { /* console 不可用 */ } }
          return []
        } finally {
          panelInFlight.delete(key)
        }
      }

      async function loadBoth(force) {
        // 缓存先行：任一卡有缓存即立即出标签内容（loaded），后台静默校验。
        const hasCache = panelCacheGet('research-list') !== null || panelCacheGet('rsi-list') !== null
        if (hasCache) setLoaded(true)
        // 不闪屏：刷新路径不清空已上屏数据（离线桩下每次渲染都会重跑 effect，
        // 若刷新前先置 loading，任意中间渲染都会把面板打回 loading 帧）。
        // 两卡独立拉取：Promise.all 下任一失败不阻断另一卡（各自 setErr 已兜住）。
        await Promise.all([
          loadCard('/ctf-research list', setResearchRows, setResearchErr, force),
          loadCard('/ctf-rsi list', setRsiRows, setRsiErr, force),
        ])
        setLoaded(true)
      }

      useEffect(() => { void loadBoth() }, [])

      async function doDecide(actionName) {
        if (busyRef.current) return // 反双击
        const decision = lastDecision.current
        const pid = decision && decision.pid
        if (!pid) return
        busyRef.current = true
        setBusy(true)
        setOkMsg('')
        try {
          // 显式 --pid 形式：位置词解析在桥接里历史上会把动词误当 PID（生产实锤
          // '[error] no such proposal: adopt'），显式 flag 无歧义且人眼可读；
          // shellArg 防注入/引号断裂。
          const out = await panelDataFetch(
            `/ctf-rsi ${actionName} --pid ${shellArg(pid)} --note ${shellArg('from-panel')}`,
            { method: 'POST' },
          )
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (!result || result.kind !== 'success') {
            lastDecision.current = decision // 保留决策上下文供重试
            throw new Error(String(result && result.text ? result.text : 'rsi decision call failed'))
          }
          lastDecision.current = null
          setAdoptedNote(actionName === 'adopt' ? t('rsiAdoptedNote') : '')
          setOkMsg(t('rsiDecisionOk'))
          void loadBoth(true) // 决策后强制刷新提案流（绕节流；Host 侧已作废重查）
        } catch (_err) {
          lastDecision.current = decision // 重试同动作
          setAdoptedNote('')
          setRsiErr(_err.message || t('rsiDecisionFailed'))
        } finally {
          busyRef.current = false
          setBusy(false)
        }
      }

      if (!loaded) {
        return h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
      }
      return h(React.Fragment, null,
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('evolutionTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('evolutionSub')),
          h('p', { className: 'ctfp-role-note' }, t('evolutionRoleNote')),
        ),
        // —— 工装体检：只读诊断，三键共用输出区（AI 运维可直接复制）——
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('opsTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('opsSub')),
          h('div', { className: 'ctfp-actions', style: { marginTop: '0' } }, [
            h('button', {
              className: 'ctfp-btn', type: 'button', disabled: opsBusy,
              onClick: () => { void runOps('/ctf-doctor', t('opsBtnDoctor')) },
              title: t('opsDoctorHint'),
            }, opsBusy ? t('opsBusy') : t('opsBtnDoctor')),
            h('button', {
              className: 'ctfp-btn', type: 'button', disabled: opsBusy,
              onClick: () => { void runOps('/ctf-logs tail 40', t('opsBtnLogs')) },
              title: t('opsLogHint'),
            }, t('opsBtnLogs')),
            h('button', {
              className: 'ctfp-btn', type: 'button', disabled: opsBusy,
              onClick: () => { void runOps('/ctf-preset-audit', t('opsBtnPreset')) },
              title: t('opsPresetHint'),
            }, t('opsBtnPreset')),
            opsText
              ? h('button', {
                  className: 'ctfp-btn', type: 'button', disabled: opsBusy,
                  onClick: () => { void copyOps() },
                }, opsCopied ? t('opsCopied') : t('opsBtnCopy'))
              : null,
          ]),
          opsText
            ? h('pre', {
                className: 'ctfp-pre',
                role: 'status',
                style: opsKind === 'bad' ? { color: 'var(--dsw-alias-state-error-primary, #dc2626)' } : undefined,
              }, opsText)
            : h('div', { className: 'ctfp-empty' }, t('opsEmpty')),
        ),
        okMsg ? h('div', { className: 'ctfc-status ctfc-status--ok', role: 'status' }, okMsg) : null,
        adoptedNote ? h('div', { className: 'ctfc-status ctfc-status--wait', role: 'alert' }, adoptedNote) : null,
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('rsiTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('rsiSub')),
          rsiErr
            ? h('div', { className: 'ctfp-note', role: 'alert' }, [
                h('div', null, rsiErr),
                // 运营细节：重试 = 先重载提案流再重发刚才失败的那个决策（引擎若已变化，
                // 决策会再次以引擎返回的信息显式报错，绝不静默吞掉）。
                h('button', {
                  className: 'ctfp-btn',
                  style: { marginLeft: '10px' },
                  type: 'button',
                  onClick: async () => {
                    await loadBoth(true)
                    const dec = lastDecision.current
                    if (dec) void doDecide(dec.action)
                  },
                }, t('retry')),
              ])
            : null,
          rsiRows.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('rsiEmpty'))
            : h(
                'div',
                { className: 'ctfp-sev-rows' },
                rsiRows.map((row) => {
                  // 已决策的提案不再显示三键；仅 proposed 状态可审批（引擎状态机单向前进）。
                  const pending = row.status === 'proposed'
                  return h('div', { key: `sev-p-${row.id}`, className: 'ctfp-sev-row' }, [
                    h('b', null, row.target),
                    h('span', { className: 'ctfp-catcount' }, row.type + ' · ' + row.id),
                    h('span', { className: rsiBadgeCls(row.status), title: row.why || undefined }, t(RSI_STATUSES[row.status] || row.status)),
                    pending
                      ? h('span', { className: 'ctfp-sev-btns' }, [
                          h('button', { className: 'ctfp-btn', type: 'button', onClick: () => { lastDecision.current = { action: 'approve', pid: row.id }; void doDecide('approve') } }, t('rsiBtnApprove')),
                          h('button', { className: 'ctfp-btn', type: 'button', onClick: () => { lastDecision.current = { action: 'reject', pid: row.id }; void doDecide('reject') } }, t('rsiBtnReject')),
                          h('button', { className: 'ctfp-btn', type: 'button', onClick: () => { lastDecision.current = { action: 'adopt', pid: row.id }; void doDecide('adopt') } }, t('rsiBtnAdopt')),
                        ])
                      : null,
                  ])
                }),
              ),
        ),
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('researchTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('researchSub')),
          researchErr
            ? h('div', { className: 'ctfp-note', role: 'alert' }, [
                h('div', null, researchErr),
                h('button', {
                  className: 'ctfp-btn',
                  style: { marginLeft: '10px' },
                  type: 'button',
                  onClick: () => { void loadBoth(true) },
                }, t('retry')),
              ])
            : null,
          researchRows.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('researchEmpty'))
            : h(
                'div',
                { className: 'ctfp-sev-rows' },
                researchRows.map((row) =>
                  h('div', { key: `sev-r-${row.id}`, className: 'ctfp-sev-row' }, [
                    h('b', null, row.topic),
                    h('span', { className: 'ctfp-catcount' }, t('researchEvidence') + ' ' + row.evidence + '/' + row.evidenceNeed),
                    h('span', { className: 'ctfp-badge' }, t(RESEARCH_STATUSES[row.status] || row.status)),
                  ]),
                ),
              ),
        ),
      )
    }

    /**
     * 训练场（竞技演练）：练兵排行榜只读 + 一键「记练」。
     * 数据来自 /ctf-arena stats；记录走 /ctf-arena record --name --category [--fail|--fast]。
     * 自加载组件（ChallengeDetail 同款模式），不扰动主面板拉取；
     * 防双击走 state+ref 双写；失败态显式渲染引擎返回的信息并附重试。
     */
    function ArenaPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [board, setBoard] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok | empty
      // 手动记练表单（练兵独立于生产台账，引擎强制要求 name+category）
      const [form, setForm] = useState({ name: '', category: '', fail: false, fast: false })
      const [recordErr, setRecordErr] = useState('')
      const [okMsg, setOkMsg] = useState('')
      const [busy, setBusy] = useState(false)
      const busyRef = useRef(false) // 一次性守卫：state+ref 双写，不随闭包过期
      // 批量重刷（2026-11-25）：派发回执与状态面（ToolsPanel 同款 banner 形态）。
      const [drillMsg, setDrillMsg] = useState('')
      const [drillKind, setDrillKind] = useState('')

      async function loadBoard(force) {
        // 缓存命中：立即上屏旧榜并修状态面，后台静默校验。
        const cached = panelCacheGet('arena-stats')
        if (cached !== null) {
          setBoard((prev) => (prev === cached.data ? prev : cached.data))
          if (status === 'loading' || status === 'error') setStatus('ok')
        }
        if (!force && cached !== null && Date.now() - cached.at < PANEL_REVALIDATE_MS) return
        if (panelInFlight.has('arena-stats')) return
        panelInFlight.add('arena-stats')
        try {
          const out = await panelDataFetch('/ctf-arena stats', { method: 'POST' })
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          // 非 success（如引擎退出码≠0）视为拉取失败，绝不静默当空榜。
          if (!result || result.kind !== 'success') throw new Error('arena call failed')
          const text = result.text
          const norm = normalizeArena(text)
          if (!norm.overall && norm.items.length === 0) {
            // 合法空榜：也缓存（Host 侧同样有快照）；有缓存时保持当前状态面。
            panelCacheSet('arena-stats', norm)
            if (cached === null) setStatus('empty')
            return
          }
          const changed = panelCacheSet('arena-stats', norm)
          if (changed) setBoard(norm)
          if (cached === null) setStatus('ok')
        } catch (_err) {
          // 无缓存：失败显式上屏；有缓存：保留旧榜（后台校验失败不打断显示）。
          if (cached === null) setStatus('error')
          else { try { console.error('[ctfpilot] arena revalidate failed:', _err) } catch (_c) { /* console 不可用 */ } }
        } finally {
          panelInFlight.delete('arena-stats')
        }
      }

      useEffect(() => { void loadBoard() }, [])

      async function doRecord() {
        if (busyRef.current) return // 反双击
        const name = String(form.name || '').trim()
        const category = String(form.category || '').trim().toLowerCase()
        if (!name || !category) {
          setRecordErr(t('arenaMissing'))
          return
        }
        busyRef.current = true
        setBusy(true)
        setRecordErr('')
        setOkMsg('')
        try {
          const parts = [`/ctf-arena record --name ${shellArg(name)} --category ${shellArg(category)}`]
          if (form.fail) parts.push('--fail')
          else if (form.fast) parts.push('--fast')
          const out = await panelDataFetch(parts.join(' '), { method: 'POST' })
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (result && result.kind === 'success') {
            setOkMsg(t('arenaRecordOk'))
            setForm({ name: '', category: '', fail: false, fast: false })
            void loadBoard(true) // 引擎记完强制刷新排行榜（Host 侧已作废；保留旧榜不闪屏）
            return
          }
          const detail = result && isNonEmptyString(result.text) ? result.text : undefined
          setRecordErr(detail || t('arenaRecordFailed'))
        } catch (_err) {
          setRecordErr(t('arenaRecordFailed'))
        } finally {
          busyRef.current = false
          setBusy(false)
        }
      }

      /** 批量重刷（2026-11-25 用户令）：与「开始解题」同款三层链——新开 CTF-worker
       *  对话 → /ctf-drill-start 路由到新会话 → Host 切快解预设 + 注入整条练兵
       *  协议（逐题重解→自验→记练→更优入库→末尾汇总）。成功才跳新对话。 */
      async function startDrill() {
        if (busyRef.current) return
        busyRef.current = true
        setBusy(true)
        setDrillMsg('')
        setDrillKind('')
        try {
          let targetSid = ''
          let wsNote = ''
          try {
            const ensured = await ensureSolveSession(currentSessionId, t)
            targetSid = ensured.sid
            wsNote = ensured.note
          } catch (e2) {
            targetSid = ''
            wsNote = t('formWsFallback', { err: String((e2 && e2.message) || e2).slice(0, 120) })
          }
          const routeSid = isNonEmptyString(targetSid) ? targetSid : currentSessionId
          const out = await panelDataFetch('/ctf-drill-start', { method: 'POST', sid: routeSid })
          const oc = remoteOutcome(out)
          if (oc.ok) {
            setDrillMsg(wsNote ? `${oc.text}\n（新建会话降级备注：${wsNote}）` : oc.text)
            setDrillKind(wsNote ? 'bad' : 'ok')
            // 成功才跳新对话页——agent 正在那里逐题执行重刷协议。
            if (isNonEmptyString(targetSid) && targetSid !== currentSessionId) {
              currentSessionId = targetSid
              notifySessionChanged()
              void openTargetSession(targetSid)
            }
          } else {
            setDrillMsg(oc.text || t('arenaDrillFailed'))
            setDrillKind('bad')
          }
        } catch (_err) {
          setDrillMsg(t('arenaDrillFailed'))
          setDrillKind('bad')
        } finally {
          busyRef.current = false
          setBusy(false)
        }
      }

      /** 面板动作（审计/收尾）：独立命令，不经 control。 */
      async function redrillMisc(cmd, label) {
        if (rdBusyRef.current) return
        rdBusyRef.current = true
        setRdMsg('')
        try {
          const out = await panelDataFetch(`/ctf-redrill-${cmd}`, { method: 'POST' })
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (result && result.kind === 'success') {
            setRdMsg(`${label} ✓\n${String(result.text || '').slice(0, 1500)}`)
            setRdKind('ok')
          } else {
            setRdMsg(`${label} 失败：${result ? String(result.text || '').slice(0, 600) : '无响应'}`)
            setRdKind('bad')
          }
          await loadRedrill()
        } catch (e) {
          setRdMsg(`${label} 失败：${String((e && e.message) || e).slice(0, 120)}`)
          setRdKind('bad')
        } finally {
          rdBusyRef.current = false
        }
      }

      const disabledBtn = busy

      // —— 交叉重刷（k-fold 闭卷，2026-11-26）：campaign 状态轮询 + 干预。 ——
      const [rdState, setRdState] = useState(null) // status blob（null = 无）
      const [rdMsg, setRdMsg] = useState('')
      const [rdKind, setRdKind] = useState('')
      const [rdErr, setRdErr] = useState('')
      const rdBusyRef = useRef(false)

      async function loadRedrill() {
        // 缓存先行：切回标签页立刻显示上次状态；后台仍会刷新。
        const cached = panelCacheGet('redrill-status')
        if (cached !== null) {
          setRdState((prev) => (JSON.stringify(prev) === JSON.stringify(cached.data) ? prev : cached.data))
        }
        try {
          const out = await panelDataFetch('/ctf-redrill-status', { method: 'POST' })
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (!result || result.kind !== 'success' || !isNonEmptyString(result.text)) {
            if (cached === null) setRdErr(result && isNonEmptyString(result.text) ? String(result.text).slice(0, 200) : t('errorState'))
            return
          }
          const blob = JSON.parse(result.text)
          setRdErr('')
          panelCacheSet('redrill-status', blob)
          setRdState((prev) => (JSON.stringify(prev) === result.text ? prev : blob))
        } catch (e) {
          // 后台轮询失败不闪屏，但状态面上必须可见（AI 运维可据此定位网关/引擎）。
          if (cached === null) setRdErr(String((e && e.message) || e).slice(0, 200))
          else { try { console.error('[ctfpilot] redrill status revalidate failed:', e) } catch (_c) { /* console 不可用 */ } }
        }
      }

      useEffect(() => {
        void loadRedrill()
        const iv = setInterval(() => { void loadRedrill() }, 30000)
        return () => { clearInterval(iv) }
      }, [])

      async function redrillControl(flag, label) {
        if (rdBusyRef.current) return
        rdBusyRef.current = true
        setRdMsg('')
        try {
          const out = await panelDataFetch(`/ctf-redrill-control ${flag}`, { method: 'POST' })
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (result && result.kind === 'success') setRdMsg(`${label} ✓ ${String(result.text || '').slice(0, 120)}`)
          else setRdMsg(`${label} 失败：${result ? String(result.text || '').slice(0, 160) : '无响应'}`)
          await loadRedrill()
        } catch (e) {
          setRdMsg(`${label} 失败：${String((e && e.message) || e).slice(0, 120)}`)
        } finally {
          rdBusyRef.current = false
        }
      }

      /** 开始/恢复交叉重刷：三层链（新开 CTF-redrill-worker 会话 → stage 盲副本→
       *  切预设+注入协议）。与 startDrill 同款：成功才跳新对话。 */
      /** 从工作区快照推导 CTF-redrill-worker 目录（ensure 刚建过，快照可查）。
      *  浏览器环境无 os/path 模块——与 ensureSolveSession 同源不引入新依赖。 */
      function redrillWorkspacePath() {
        try {
          const wssvc = workspacesHolder
          const snap = wssvc && wssvc.list && typeof wssvc.list.getSnapshot === 'function' ? wssvc.list.getSnapshot() : null
          const items = snap && Array.isArray(snap.items) ? snap.items : []
          const hit = items.find((it) => isNonEmptyString(it && it.path) && /[\\/]CTF-redrill-worker$/i.test(String(it.path).trim()))
          return hit ? String(hit.path).trim() : ''
        } catch { return '' }
      }

      async function startRedrill(resume) {
        if (busyRef.current) return
        busyRef.current = true
        setBusy(true)
        setRdMsg('')
        try {
          let targetSid = ''
          let wsNote = ''
          let wsDirHint = ''
          try {
            const ensured = await ensureSolveSession(currentSessionId, t, 'CTF-redrill-worker')
            targetSid = ensured.sid
            wsNote = ensured.note
            wsDirHint = isNonEmptyString(ensured.dir) ? ensured.dir : ''
          } catch (e2) {
            targetSid = ''
            wsNote = t('formWsFallback', { err: String((e2 && e2.message) || e2).slice(0, 120) })
          }
          const routeSid = isNonEmptyString(targetSid) ? targetSid : currentSessionId
          // ensure 返回的目录优先（建区后快照可能有毫秒级时序空窗）；旧快照推导兜底。
          const wsDir = wsDirHint || redrillWorkspacePath()
          if (!wsDir) {
            setRdMsg(t('redrillNoWsDir'))
            setRdKind('bad')
            return
          }
          const out = await panelDataFetch(`/ctf-redrill-start ${resume ? '--resume' : ''} --workspace ${shellArg(wsDir)}`, { method: 'POST', sid: routeSid })
          const oc = remoteOutcome(out)
          if (oc.ok) {
            setRdMsg(wsNote ? `${oc.text}\n（降级备注：${wsNote}）` : oc.text)
            setRdKind(wsNote ? 'bad' : 'ok')
            if (isNonEmptyString(targetSid) && targetSid !== currentSessionId) {
              currentSessionId = targetSid
              notifySessionChanged()
              void openTargetSession(targetSid)
            }
          } else {
            setRdMsg(oc.text || t('redrillFailed'))
            setRdKind('bad')
          }
        } catch (_err) {
          setRdMsg(t('redrillFailed'))
          setRdKind('bad')
        } finally {
          busyRef.current = false
          setBusy(false)
          void loadRedrill()
        }
      }
      const formCard = h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('arenaFormTitle')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('arenaFormSub')),
        h(
          'div',
          { className: 'ctfp-arena-form' },
          h('input', {
            className: 'ctfp-search',
            type: 'text',
            value: form.name,
            placeholder: t('arenaNamePh'),
            'aria-label': t('arenaNamePh'),
            onChange: (e) => setForm({ ...form, name: e.target.value }),
          }),
          h('input', {
            className: 'ctfp-search',
            type: 'text',
            value: form.category,
            placeholder: t('arenaCatPh'),
            'aria-label': t('arenaCatPh'),
            onChange: (e) => setForm({ ...form, category: e.target.value }),
          }),
          h(
            'label',
            { className: 'ctfp-cb' },
            h('input', {
              type: 'checkbox',
              checked: form.fail,
              onChange: (e) => setForm({ ...form, fail: e.target.checked }),
            }),
            t('arenaRecFail'),
          ),
          h(
            'label',
            { className: 'ctfp-cb' },
            h('input', {
              type: 'checkbox',
              checked: form.fast,
              onChange: (e) => setForm({ ...form, fast: e.target.checked }),
            }),
            t('arenaRecFast'),
          ),
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              onClick: () => { void doRecord() },
              disabled: disabledBtn,
            },
            t('arenaRecordBtn'),
          ),
        ),
        okMsg
          ? h('div', { className: 'ctfc-status ctfc-status--ok', role: 'status' }, okMsg)
          : null,
        recordErr
          ? h(
              'div',
              { className: 'ctfc-status ctfc-status--bad', role: 'alert', 'aria-live': 'assertive' },
              h('span', null, recordErr),
              h(
                'button',
                { className: 'ctfc-btn ctfc-btn--ghost', type: 'button', style: { marginLeft: '10px' }, onClick: () => { void doRecord() } },
                t('retryAfterFail'),
              ),
            )
          : null,
      )

      const drillCard = h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('arenaDrillTitle')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('arenaDrillSub')),
        h('button', {
          className: 'ctfp-btn',
          type: 'button',
          onClick: () => { void startDrill() },
          disabled: busy,
          title: t('arenaDrillHint'),
        }, busy ? t('arenaDrillBusy') : t('arenaDrillBtn')),
        drillMsg
          ? h('div', {
              className: drillKind === 'ok' ? 'ctfc-status ctfc-status--ok' : 'ctfc-status ctfc-status--bad',
              role: drillKind === 'ok' ? 'status' : 'alert',
            }, drillMsg)
          : null,
      )

      const rd = rdState
      const rdActive = rd && rd.status === 'active'
      const rdSlots = (rd && Array.isArray(rd.slots)) ? rd.slots : []
      const rdPass = rdSlots.filter((s) => s.via === 'fast' || s.via === 'manual').length
      const rdSusp = rdSlots.filter((s) => s.suspicious).length
      const SLOT_SYM = { recorded: '✔', verified: '⊙', running: '✏', pending: '·', missing: '⊘', skipped: '⏭', failed: '✖' }
      const redrillCard = h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('redrillTitle')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('redrillSub')),
        h('div', { className: 'ctfp-arena-form' },
          h('button', {
            className: 'ctfp-btn',
            type: 'button',
            // 2026-11-26 修正：rdActive 时按钮是「恢复考核」，必须带 --resume。
            // 此前传 !rdActive，active 时走 plan 分支被引擎拒（campaign already active），
            // 表现为点按钮无任何反应。
            onClick: () => { void startRedrill(rdActive) },
            disabled: busy,
            title: t('redrillHint'),
          }, busy ? t('redrillBusy') : (rdActive ? t('redrillResumeBtn') : t('redrillStartBtn'))),
          rdActive || (rd && rd.status === 'done')
            ? h('button', {
                className: 'ctfp-btn', type: 'button',
                onClick: () => { void redrillControl(rdActive ? '--pause' : '--continue', t(rdActive ? 'redrillPauseBtn' : 'redrillContinueBtn')) },
              }, rdActive ? t('redrillPauseBtn') : t('redrillContinueBtn'))
            : null,
          rdActive
            ? h('button', {
                className: 'ctfp-btn', type: 'button',
                onClick: () => { void redrillControl('--abort', t('redrillAbortBtn')) },
              }, t('redrillAbortBtn'))
            : null,
        ),
        rd && (rd.status === 'active' || rd.status === 'done')
          ? h('div', { className: 'ctfp-kpis', style: { margin: '10px 0' } }, [
              { label: t('redrillProgressFold'), value: `${rd.fold}/${rd.folds}`, unit: '' },
              { label: t('redrillPass'), value: `${rdPass}/${rdSlots.length}`, unit: '' },
              { label: t('redrillSuspicious'), value: String(rdSusp), unit: '' },
            ].map((kpi, idx) => h(KpiCard, { key: `rdk-${idx}`, label: kpi.label, value: kpi.value, unit: kpi.unit })))
          : null,
        // 运维（2026-11-26）：引擎 ops 日志的最近错误直接上屏，无需进日志目录。
        rd && Array.isArray(rd.last_errors) && rd.last_errors.length > 0
          ? h('div', { className: 'ctfc-status ctfc-status--bad', role: 'status', style: { marginTop: '6px', fontSize: '12px', whiteSpace: 'pre-wrap' } },
              `${t('redrillRecentErrors')}（${rd.last_errors.length}）：` + rd.last_errors.slice(-2).map((e) => `[${e.action}] ${String((e.detail && e.detail.error) || e.ts || '').slice(0, 160)}`).join(' · '))
          : null,
        rdSlots.length > 0
          ? h('div', null,
              h('div', { className: 'ctfp-arena-head' },
                h('span', null, t('redrillColSlot')),
                h('span', null, t('redrillColCat')),
                h('span', null, t('redrillColState')),
                h('span', null, t('redrillColVia')),
              ),
              h('div', { className: 'ctfp-arena-rows' },
                rdSlots.map((s) =>
                  h('div', { key: `rd-${s.slot}`, className: 'ctfp-arena-row' }, [
                    h('b', null, `${SLOT_SYM[s.state] || '?'} ${s.slot}`),
                    h('span', null, `F${s.fold} ${s.category || ''}`),
                    h('span', null, String(s.state) + (s.suspicious ? ' ⚠' : '')),
                    h('span', null, s.via || '—'),
                  ]))))
          : h('div', { className: 'ctfp-empty' }, t('redrillNoCampaign')),
        h('div', { className: 'ctfp-arena-form', style: { marginTop: '8px' } },
          h('button', {
            className: 'ctfp-btn', type: 'button',
            onClick: () => { void redrillMisc('audit', t('redrillAuditBtn')) },
            title: t('redrillAuditHint'),
          }, t('redrillAuditBtn')),
          h('button', {
            className: 'ctfp-btn', type: 'button',
            onClick: () => { void redrillMisc('diag', t('redrillDiagBtn')) },
            title: t('redrillDiagHint'),
          }, t('redrillDiagBtn')),
          rd && rd.status !== 'active'
            ? h('button', {
                className: 'ctfp-btn', type: 'button',
                onClick: () => { void redrillMisc('finalize', t('redrillFinalizeBtn')) },
              }, t('redrillFinalizeBtn'))
            : null,
        ),
        rdErr
          ? h('div', {
              className: 'ctfc-status ctfc-status--bad',
              role: 'status',
              style: { marginTop: '6px', fontSize: '12px' },
            }, `${t('errorState')} · ${rdErr}`)
          : null,
        rdMsg
          ? h('div', {
              className: rdKind === 'ok' ? 'ctfc-status ctfc-status--ok' : 'ctfc-status ctfc-status--bad',
              role: rdKind === 'ok' ? 'status' : 'alert',
            }, rdMsg)
          : null,
      )

      if (status === 'loading' && !board) {
        return h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
      }

      const rows = (board && board.items) || []
      // 档位合计按分类行汇总（引擎 overall 行不含档位分布）
      const tierTotals = { easy: 0, normal: 0, hard: 0 }
      for (const r of rows) {
        tierTotals.easy += num(r.easy, 0)
        tierTotals.normal += num(r.normal, 0)
        tierTotals.hard += num(r.hard, 0)
      }
      return h(
        React.Fragment,
        null,
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('refineryTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('refinerySub')),
          h('p', { className: 'ctfp-role-note' }, t('refineryRoleNote')),
        ),
        // 考核优先：题库精炼的主入口（有证据守门、结果直接回写经验卡）。
        redrillCard,
        drillCard,
        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('arenaTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('arenaSub')),
          status === 'error'
            ? h(
                'div',
                { className: 'ctfp-note', role: 'alert' },
                h('div', null, t('errorState')),
                h(
                  'button',
                  { className: 'ctfp-btn', style: { marginLeft: '10px' }, type: 'button', onClick: () => { void loadBoard(false) } },
                  t('retry'),
                ),
              )
            : null,
          board && board.overall
            ? h('div', { className: 'ctfp-kpis', style: { marginBottom: '12px' } }, [
                { label: t('arenaOverall'), value: `${board.overall.solved}/${board.overall.attempted}`, unit: t('unitChalls') },
                { label: t('arenaColRate'), value: fmtPercent(board.overall.rate), unit: '' },
                { label: t('arenaColTier'), value: `${tierTotals.easy}/${tierTotals.normal}/${tierTotals.hard}`, unit: '' },
              ].map((kpi, idx) =>
                h(KpiCard, { key: `kpi-${idx}`, label: kpi.label, value: kpi.value, unit: kpi.unit }),
              ))
            : null,
          status === 'error'
            ? null
            : rows.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('arenaNoBoard'))
            : h(
                'div',
                null,
                h(
                  'div',
                  { className: 'ctfp-arena-head' },
                  h('span', null, t('colCat')),
                  h('span', null, t('arenaColStat')),
                  h('span', null, t('arenaColRate')),
                  h('span', null, t('arenaColTier')),
                ),
                h(
                  'div',
                  { className: 'ctfp-arena-rows' },
                  rows.map((r) =>
                    h('div', { key: `ar-${r.category}`, className: 'ctfp-arena-row' }, [
                      h('b', null, r.category),
                      h('span', null, `${num(r.solved, 0)}/${num(r.attempted, 0)}`),
                      h('span', null, fmtPercent(r.rate)),
                      h('span', null, `${num(r.easy,0)}/${num(r.normal,0)}/${num(r.hard,0)}`),
                    ]),
                  ),
                ),
              ),
        ),
        formCard,
      )
    }

    /**
     * 知识库屏（合并原「概览 / 题目台账 / 探测器 / 经验卡」四屏）：
     * 顶部保留 KPI 与分类覆盖（一眼看题库家底），主体用二级分段：
     *   题目     —— 搜索 + 分类过滤 + 点击进详情（原台账，交付最常用）
     *   经验卡   —— 按技术/分类/来源检索历史经验
     *   引擎观察 —— 探测器命中、失败画像、解题模式（原概览的诊断信息）
     * 数据全部来自同一份看板 blob，不额外拉取。
     */
    function KnowledgePanel(props) {
      const t = props.t
      const blob = props.blob
      const sub = props.sub
      const setSub = props.setSub
      const query = props.query
      const setQuery = props.setQuery
      const catFilter = props.catFilter
      const setCatFilter = props.setCatFilter
      const showLearned = props.showLearned
      const setShowLearned = props.setShowLearned
      const lessonQuery = props.lessonQuery
      const setLessonQuery = props.setLessonQuery
      const onOpenChallenge = props.onOpenChallenge

      // Top-12：单遍插入有序小数组（O(n·12)），避免全量 slice+sort
      // （O(n log n) + 两次数组复制）；detectors 随账本增长时可感知。
      const detSorted = useMemo(() => {
        const arr = blob === null || !Array.isArray(blob.detectors) ? [] : blob.detectors
        const top = []
        for (const d of arr) {
          let i = 0
          while (i < top.length &&
            (top[i].hits > d.hits || (top[i].hits === d.hits && top[i].attempts >= d.attempts))) i++
          if (i >= 12) continue
          top.splice(i, 0, d)
          if (top.length > 12) top.pop()
        }
        return top
      }, [blob])

      const filtered = useMemo(() => {
        if (!blob) return []
        const q = query.trim().toLowerCase()
        const cat = catFilter
        return blob.challs.filter((c) =>
          // kind='learn' 只有学 writeup 的导入记录，非真实解题：默认隐藏。
          (!showLearned && c.kind === 'learn' ? false :
            (!cat || c.cat === cat) &&
            (!q || challSearchText(c).includes(q))),
        )
      }, [blob, query, catFilter, showLearned])

      const lessonFiltered = useMemo(() => {
        if (!blob) return []
        const q = lessonQuery.trim().toLowerCase()
        if (!q) return blob.lessons
        return blob.lessons.filter((les) => lessonSearchText(les).includes(q))
      }, [blob, lessonQuery])

      const kpis = useMemo(
        () =>
          blob
            ? [
                { label: t('totalChalls'), value: blob.stats.total, unit: t('unitChalls') },
                { label: t('solved'), value: blob.stats.solved, unit: t('unitChalls') },
                { label: t('rate'), value: fmtPercent(blob.stats.rate), unit: '' },
                { label: t('attemptsTotal'), value: blob.stats.attempts, unit: t('unitAttempts') },
                { label: t('lessons'), value: blob.stats.lessons, unit: t('unitChalls') },
              ]
            : [],
        [blob, t],
      )

      const segBtn = (key, label, count) =>
        h('button', {
          key: `seg-${key}`,
          type: 'button',
          role: 'tab',
          'aria-selected': sub === key,
          className: sub === key ? 'ctfp-seg-btn ctfp-seg-btn--on' : 'ctfp-seg-btn',
          onClick: () => { setSub(key) },
        }, label, count !== null && count !== undefined ? h('span', { className: 'ctfp-seg-count' }, String(count)) : null)

      const catCount = Array.isArray(blob.bycat) ? blob.bycat.length : 0
      const lessonCount = Array.isArray(blob.lessons) ? blob.lessons.length : 0
      const detCount = Array.isArray(blob.detectors) ? blob.detectors.length : 0

      return h(React.Fragment, null,
        h('div', { className: 'ctfp-kpis' }, kpis.map((k, i) => h(KpiCard, { key: `kpi-${i}`, label: k.label, value: k.value, unit: k.unit }))),

        h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('catsTitle'), h('span', { className: 'ctfp-catcount' }, `${catCount}`)),
          blob.bycat.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('noCategory'))
            : h(
                'div',
                { className: 'ctfp-cats' },
                blob.bycat.map((row) => h(CategoryRow, { key: `cat-${row.cat || '?'}`, row, t })),
              ),
        ),

        h(
          'div',
          { className: 'ctfp-seg', role: 'tablist', 'aria-label': t('knowledgeTitle') },
          [
            segBtn('challs', t('knowledgeTabChalls'), filtered.length),
            segBtn('lessons', t('knowledgeTabLessons'), lessonCount),
            segBtn('observe', t('knowledgeTabObserve'), detCount),
          ],
        ),

        // —— 题目：分类下拉预选 + 搜索；点击行进入详情页 ——
        sub === 'challs' && h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('knowledgeTabChalls'), h('span', { className: 'ctfp-catcount' }, t('ledgerCount', { n: filtered.length }))),
          h('div', { className: 'ctfp-filterbar' },
            h('select', {
              className: 'ctfp-filter-sel',
              value: catFilter,
              onChange: (e) => { setCatFilter(e.target.value) },
              'aria-label': t('colCat'),
            }, [
              h('option', { value: '' }, t('filterAll')),
              ...[...new Set(blob.challs.map((c) => c.cat).filter(Boolean))]
                .sort()
                .map((cat) => h('option', { key: `opt-${cat}`, value: cat }, cat)),
            ]),
            blob.challs.some((c) => c.kind === 'learn')
              ? h('label', { className: 'ctfp-learn-toggle' },
                  h('input', {
                    type: 'checkbox',
                    checked: showLearned,
                    onChange: (e) => { setShowLearned(e.target.checked) },
                  }),
                  t('showLearned'))
              : null,
          ),
          h('input', {
            className: 'ctfp-search',
            type: 'search',
            placeholder: t('searchPlaceholder'),
            value: query,
            onChange: (e) => { setQuery(e.target.value) },
            'aria-label': t('searchPlaceholder'),
          }),
          filtered.length === 0
            ? h('div', { className: 'ctfp-empty' }, t('noLedgerMatch'))
            : h(
                'div',
                { className: 'ctfp-ledger' },
                filtered.map((ch) =>
                  h(LedgerRow, {
                    key: `ledger-${ch.id || ch.name || 'x'}`,
                    ch,
                    t,
                    onClick: () => { onOpenChallenge(ch) },
                  }),
                ),
              ),
        ),

        // —— 经验卡：按技术 / 分类 / 来源检索 ——
        sub === 'lessons' && h(
          'section',
          { className: 'ctfp-section' },
          h('h2', null, t('knowledgeTabLessons'), h('span', { className: 'ctfp-catcount' }, t('lessonsCount', { n: lessonFiltered.length }))),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('knowledgeLessonsSub')),
          h('input', {
            className: 'ctfp-search',
            type: 'search',
            placeholder: t('lessonSearchPh'),
            value: lessonQuery,
            onChange: (e) => { setLessonQuery(e.target.value) },
            'aria-label': t('lessonSearchPh'),
          }),
          lessonFiltered.length === 0
            ? h('div', { className: 'ctfp-empty' }, blob.lessons.length === 0 ? t('noLessons') : t('noLessonsMatch'))
            : h(
                'div',
                { className: 'ctfp-lessons' },
                lessonFiltered.slice(0, 60).map((les) =>
                  h(LessonItem, { key: `les-${les.cat}:${les.tech}:${les.time}`, les, t }),
                ),
              ),
        ),

        // —— 引擎观察：探测器命中 + 失败画像 + 解题模式（诊断信息集中一处）——
        sub === 'observe' && h(React.Fragment, null,
          h('p', { className: 'ctfp-note ctfp-note--hint' }, t('knowledgeObserveSub')),
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('detectorsTitle'), h('span', { className: 'ctfp-catcount' }, `${detCount}`)),
            h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('obsDetectorsSub')),
            blob.detectors.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noDetectors'))
              : h(
                  'div',
                  { className: 'ctfp-dets' },
                  h(
                    'div',
                    { className: 'ctfp-detrow ctfp-dethead' },
                    h('span', null, t('colDetector')),
                    h('span', null, t('colPhase')),
                    h('span', null, t('colAttempts')),
                    h('span', null, t('colHits')),
                    h('span', null, t('colHitRate')),
                  ),
                  detSorted.map((det) => h(DetectorRow, { key: `det-${det.detector || det.phase || '?'}`, det, t })),
                ),
          ),
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null,
              t('errorsTitle'),
              h('span', { className: 'ctfp-catcount' }, t('errClasses', { n: blob.errors.length }))),
            h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('obsErrorsSub')),
            blob.errors.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noErrors'))
              : h(
                  'div',
                  { className: 'ctfp-dets' },
                  h(
                    'div',
                    { className: 'ctfp-detrow ctfp-dethead' },
                    h('span', null, t('colErrClass')),
                    h('span', null, t('colErrCount')),
                    h('span', null, t('colErrSuc')),
                  ),
                  blob.errors.slice(0, 12).map((er) => h(ErrorRow, { key: `err-${er.cls}`, er, t })),
                ),
          ),
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('modesTitle')),
            h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('obsModesSub')),
            blob.modes.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noModes'))
              : h(
                  'div',
                  { className: 'ctfp-cats' },
                  blob.modes.slice(0, 6).map((row) =>
                    h(ModeRow, {
                      key: `mode-${row.mode || '?'}`,
                      row,
                      t,
                      total: blob.stats.total,
                    }),
                  ),
                ),
          ),
        ),
      )
    }

    /**
     * 主面板：标签顺序按日常动线固定——
     *   开始解题 → 正在解题 → 工具管理 → 知识库 → 题库精炼 → 系统演进。
     * 前三个是操作面；知识库是记忆面；题库精炼与系统演进分别只动
     * 「题库资产」与「工装本身」，文案互相指路，杜绝名称混淆。
     * 标签持久化（重开回上次所在屏）；解题/工具/考核/提案角标由模块级
     * 订阅驱动（零额外轮询）。
     */
    function CtfpilotPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [blob, setBlob] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok | empty
      const [query, setQuery] = useState('')
      /** 详情页目标题目；非 null 时整屏渲染该题详情页。 */
      const [detailCh, setDetailCh] = useState(null)
      /** 分类下拉预选值（'' = 全部分类）。 */
      const [catFilter, setCatFilter] = useState('')
      /** 是否在台账中展示「只有学习导入记录」的题（默认隐藏）。 */
      const [showLearned, setShowLearned] = useState(false)
      /** 知识库二级分段：challs | lessons | observe。 */
      const [knowledgeSub, setKnowledgeSub] = useState('challs')
      /** 经验卡检索词（与题目检索分开，互不干扰）。 */
      const [lessonQuery, setLessonQuery] = useState('')
      /** 主标签：solve | solving | tools | knowledge | refinery | evolution；首访默认开始解题。 */
      const [tab, setTabRaw] = useState(() => readPersistedTab())
      /** 模块级订阅（看板缓存/解题板）驱动的角标刷新计数。 */
      const [, setBadgeTick] = useState(0)

      const selectTab = (key) => {
        setTabRaw(key)
        persistTab(key)
      }

      useEffect(() => {
        const a = subscribeSolveBoard(() => setBadgeTick((n) => n + 1))
        const b = subscribePanelCache(() => setBadgeTick((n) => n + 1))
        return () => { a(); if (typeof b === 'function') b() }
      }, [])

      // 数据上屏与状态面判定（拉取结果与缓存命中共用）。
      // setBlob 用函数式更新：sessions.list 每次快照变更都会触发一次 loadPanel，
      // 缓存命中时同引用不重设，真实 React 对同引用 state 自动 bail out（防渲染风暴）。
      const present = (data) => {
        setBlob((prev) => (prev === data ? prev : data))
        setStatus(
          data.challs.length === 0 &&
            data.lessons.length === 0 &&
            data.detectors.length === 0 &&
            data.bycat.length === 0
            ? 'empty'
            : 'ok',
        )
      }

      // 拉取引擎看板并写入模块级缓存。stale-while-revalidate（官方 sidebar-files
      // 的 ready+refreshing 语义）：缓存命中立即上屏旧数据，后台静默再校验，
      // 新数据到达后内容级比较替换；只有真正无缓存时才显示 loading。
      const loadPanel = useCallback(async (opts) => {
        const force = Boolean(opts && opts.force)
        // ① 缓存命中（含 UI 重启后的 localStorage 恢复）：立即上屏，零等待。
        if (dashboardCache !== null) present(dashboardCache.data)
        // ② 后台再校验：节流窗口内跳过（防 sessions.list 快照订阅风暴）；
        //    强制刷新不受节流。
        if (
          !force &&
          dashboardCache !== null &&
          Date.now() - dashboardCache.at < DASHBOARD_REVALIDATE_MS
        ) return
        if (dashboardInFlight) return
        dashboardInFlight = true
        try {
          const out = await panelDataFetch('/ctf-dashboard')
          const text = resultTextOf(out)
          if (text === undefined) throw new Error('dashboard call failed')
          const normalized = normalizeBlob(JSON.parse(text))
          const fingerprint = JSON.stringify(normalized)
          // 内容级比较：账本未变（Host 侧签名命中直接回快照）时不重渲染；
          // 变了才替换上屏。看板数据全局，与会话无关，切会话不作废。
          const changed = dashboardCache === null || dashboardCache.fingerprint !== fingerprint
          dashboardCache = { data: normalized, fingerprint, at: Date.now() }
          persistDashboard()
          if (changed) present(normalized)
        } catch (_err) {
          // 无缓存：失败必须显形（error 态 + 重试按钮）；有缓存：保留旧数据
          // 继续显示（refreshing 语义），失败记入 console 诊断——Host 侧命令
          // 包装层已把异常落盘 ctfpilot-errors.log（第二显形通道）。
          if (dashboardCache === null) {
            setBlob(null)
            setStatus('error')
          } else {
            try { console.error('[ctfpilot] dashboard revalidate failed:', _err) } catch (_c) { /* console 不可用 */ }
          }
        } finally {
          dashboardInFlight = false
        }
      }, [])

      useEffect(() => {
        void loadPanel()
      }, [loadPanel])

      // 会话切换（任意会话的回合尾部渲染过）触发一次再校验：
      // 事件订阅而非定时器，无轮询成本。
      const loadLatest = useRef(loadPanel)
      loadLatest.current = loadPanel
      useEffect(() => {
        return subscribeCurrentSession(() => {
          void loadLatest.current()
        })
      }, [])

      // sessions 列表快照变更也驱动一次再校验：切工作区/会话可能伴随记账
      // 更新。loadPanel 有防重入 + 同引用 present bail out，无渲染风暴风险。
      useEffect(() => {
        const s = sessionsHolder
        if (!s || !s.list || typeof s.list.subscribe !== 'function') return () => {}
        return s.list.subscribe(() => {
          void loadLatest.current()
        })
      }, [])

      const refreshButton = h(
        'button',
        {
          className: 'ctfp-btn',
          onClick: () => {
            // 手动刷新：绕过节流强制再校验；在途请求未落地时由其完成后
            // 内容比较自然刷新，不重复并发。
            void loadPanel({ force: true })
          },
          'aria-label': t('refresh'),
        },
        [h('span', null, '↻'), h('span', null, t('refresh'))],
      )

      // —— 标签角标：只读模块级缓存，不额外发请求 ——
      const board = readSolveBoard()
      let runningCount = 0
      for (const entry of Object.values(board)) {
        if (entry && typeof entry === 'object' && entry.running) runningCount++
      }
      const toolsCache = panelCacheGet('tools-status')
      const toolsRows = toolsCache && Array.isArray(toolsCache.data) ? toolsCache.data : []
      const missingTools = toolsRows.filter((r) => r && !r.ok && r.kind !== 'mcp').length
      const rdCache = panelCacheGet('redrill-status')
      const redrillActive = Boolean(rdCache && rdCache.data && rdCache.data.status === 'active')
      const rsiCache = panelCacheGet('rsi-list')
      const rsiRows = rsiCache && Array.isArray(rsiCache.data) ? rsiCache.data : []
      const rsiPending = rsiRows.filter((r) => r && r.status === 'proposed').length

      const badge = (text, kind, title) =>
        text
          ? h('span', { className: 'ctfp-tab__badge' + (kind ? ` ctfp-tab__badge--${kind}` : ''), title: title || text }, String(text))
          : null

      const tabDefs = [
        { key: 'solve', label: t('tabSolve') },
        { key: 'solving', label: t('solvingTab'), badgeNode: runningCount > 0 ? badge(runningCount, 'run', t('tabBadgeRunning', { n: runningCount })) : null },
        { key: 'tools', label: t('tabTools'), badgeNode: missingTools > 0 ? badge(missingTools, 'warn', t('tabBadgeMissing', { n: missingTools })) : null },
        { key: 'knowledge', label: t('tabKnowledge') },
        { key: 'refinery', label: t('tabRefinery'), badgeNode: redrillActive ? h('span', { className: 'ctfp-tab__dot', title: t('tabBadgeRedrill') }) : null },
        { key: 'evolution', label: t('tabEvolution'), badgeNode: rsiPending > 0 ? badge(rsiPending, 'warn', t('tabBadgeRsi', { n: rsiPending })) : null },
      ]

      return h(
        'div',
        { className: 'ctfp-root' },
        Styles(),
        // 详情页优先：选中题目后整屏渲染该题（返回时保留知识库分段与检索词）。
        detailCh
          ? h(ChallengeDetail, {
              challenge: detailCh,
              onBack: () => { setDetailCh(null) },
            })
          : h(React.Fragment, null,
              h(
                'header',
                { className: 'ctfp-head' },
                h('div', null, h('h1', { className: 'ctfp-title' }, t('panelTitle')), h('p', { className: 'ctfp-sub' }, t('panelSub'))),
                refreshButton,
              ),

              // 状态面：失败时全局横幅（带重试）。加载/空态不再霸占整屏——
              // 工具管理/题库精炼/系统演进不依赖账本数据，账本为空或读取失败
              // 时用户仍必须能进入这三屏；知识库自己的提示落在屏内。
              status === 'error'
                ? h(
                    'div',
                    { className: 'ctfp-note', role: 'alert' },
                    h('span', null, t('errorState')),
                    h(
                      'button',
                      { className: 'ctfp-btn', style: { marginLeft: '10px' }, onClick: () => { void loadPanel() } },
                      t('retry'),
                    ),
                  )
                : null,

              h(React.Fragment, null,
                // 主标签栏：操作三屏在前，记忆与两类演进在后（顺序是用户动线）。
                h('div', { className: 'ctfp-tabs', role: 'tablist' },
                  tabDefs.map((def) =>
                    h('button', {
                      key: `tab-${def.key}`,
                      role: 'tab',
                      type: 'button',
                      'aria-selected': tab === def.key,
                      className: tab === def.key ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab',
                      onClick: () => { selectTab(def.key) },
                    }, def.label, def.badgeNode || null),
                  ),
                ),

                // 知识库依赖看板数据：仅在无数据时于屏内提示，不拦其他标签。
                tab === 'knowledge'
                  ? (blob
                      ? h(KnowledgePanel, {
                          blob,
                          t,
                          sub: knowledgeSub,
                          setSub: setKnowledgeSub,
                          query,
                          setQuery,
                          catFilter,
                          setCatFilter,
                          showLearned,
                          setShowLearned,
                          lessonQuery,
                          setLessonQuery,
                          onOpenChallenge: (ch) => { setDetailCh(ch) },
                        })
                      : status === 'loading'
                        ? h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
                        : null)
                  : null,
                // —— 题库精炼：交叉重刷 + 批量重刷 + 演练台账（只动题库资产）——
                tab === 'refinery' ? h(ArenaPanel, { t }) : null,
                // —— 系统演进：工装体检 + RSI 提案 + 研究队列（只动插件/预设）——
                tab === 'evolution' ? h(SelfEvoPanel, { t }) : null,
                tab === 'solving' ? h(SolvingBoard, { t }) : null,
                tab === 'solve' ? h(SolveFormPanel, { t }) : null,
                tab === 'tools' ? h(ToolsPanel, { t }) : null,
              ),
            ),
      )
    }

    /* ============================================================
     * Feature 2 —— 提交确认卡（SUBMIT CONFIRMATION CARD）
     * ============================================================ */

    function asSnap(raw) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
      const cand =
        raw.proposals && typeof raw.proposals === 'object' && !Array.isArray(raw.proposals)
          ? raw
          : raw.value && typeof raw.value === 'object' && !Array.isArray(raw.value) && raw.value.proposals
            ? raw.value
            : null
      if (!cand) return null
      return {
        proposals: cand.proposals && typeof cand.proposals === 'object' && !Array.isArray(cand.proposals)
          ? cand.proposals
          : {},
        confirmed: Array.isArray(cand.confirmed) ? cand.confirmed : [],
      }
    }

    /**
     * 挑出 THE proposal：本回合（p.turn === turn.turn）、已准入、
     * 多个时取 callId 最大者（字符串比较，最新胜出）。
     */
    function pickProposal(proposals, turnIndex) {
      if (typeof turnIndex !== 'number' || !Number.isFinite(turnIndex)) return null
      let best = null
      for (const p of Object.values(proposals)) {
        if (!p || typeof p !== 'object') continue
        // Admission is structural: the Host fold keeps only genuinely-logged
        // propose_submit tool/call events with a flag argument. No admitted
        // marker field is needed anymore.
        if (num(p.turn, NaN) !== turnIndex) continue
        const callId = isNonEmptyString(p.callId) ? p.callId : null
        if (
          best === null ||
          (callId !== null && callId > (isNonEmptyString(best.callId) ? best.callId : ''))
        ) {
          best = p
        }
      }
      return best
    }

    /**
     * 经验卡 tags 安全解析：引擎 lessons.tags 存 JSON 字符串（对象四键）；
     * 历史/异常文本（非 JSON 或解析失败）回退按逗号切。
     */
    function parseLessonTags(raw) {
      const text = str(raw, '')
      if (!text) return []
      if (text.charCodeAt(0) === 123 /* '{' */) {
        try {
          const obj = JSON.parse(text)
          if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
            return Object.values(obj).map((v) => String(v == null ? '' : v)).filter(Boolean)
          }
        } catch (_e) { /* fallthrough */ }
      }
      return text.split(',').map((s) => s.trim()).filter(Boolean)
    }

    /** 提交确认卡：读会话投影 'ctfpilot'，仅在存在本回合准入 proposal 时渲染。
     * phase ∈ '' | 'pending' | 'done' | 'failed' | 'dismissed'（本地状态）；
     * confirmedMatch 到达（他端已确认）时直接显示完成行，无需点击。
     */
    function SubmitConfirmCard(props) {
      const t = props && typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const turn = props && props.turn ? props.turn : null
      const sessionId = isNonEmptyString(props.sessionId) ? props.sessionId : ''
      const useProjection = props && typeof props.useProjection === 'function' ? props.useProjection : null

      // 投影快照：首事件前为 undefined，按空快照处理（防御性解包两种形态）。
      const snap = asSnap(useProjection ? useProjection(PROJECTION_KEY) : null)
      const snapshotValid = Boolean(snap && turn && typeof turn.turn === 'number')

      const proposal = useMemo(
        () => (snapshotValid && snap ? pickProposal(snap.proposals, turn.turn) : null),
        [snap, turn, snapshotValid],
      )

      const confirmedMatch = useMemo(
        () =>
          snapshotValid && snap && proposal
            ? snap.confirmed.find(
                (c) => c && c.flag === proposal.flag && c.category === proposal.category,
              ) || null
            : null,
        [snap, proposal, snapshotValid],
      )

      const [phase, setPhaseRaw] = useState('')
      const [errorText, setErrorText] = useState('')
      // 相位双写（state + ref）：保证「一次性触发」守卫不随闭包过期。
      const phaseRef = useRef('')
      const setPhase = (next) => {
        phaseRef.current = next
        setPhaseRaw(next)
      }

      const p = proposal
      if (!snapshotValid || !p) return null
      if (phase === 'dismissed') return null

      const flag = isNonEmptyString(p.flag) ? p.flag : ''
      const category = isNonEmptyString(p.category) ? p.category : ''
      const title = isNonEmptyString(p.title) ? p.title : t('cardTitleFallback')
      const started = phase !== ''

      async function doSubmit() {
        // 一次性守卫：已有相位（含 pending）时不重复触发。
        if (phaseRef.current !== '') return
        setErrorText('')
        setPhase('pending')
          try {
            const out = await panelDataFetch(
              buildRecordWinCmd(p.challenge || title, category, flag),
              { method: 'POST', sid: sessionId },
            )
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (result && result.kind === 'success') {
            setPhase('done')
            invalidateDashboard()
            return
          }
          const detail = result && isNonEmptyString(result.text) ? result.text : undefined
          setPhase('failed')
          setErrorText(detail || t('submitFailed'))
        } catch (_err) {
          setPhase('failed')
          setErrorText(t('submitFailed'))
        }
      }

      // 首次触发 + 失败后的重试：先清相位再发起，保证守卫放行。
      function submitOrRetry() {
        if (phaseRef.current !== '' && phaseRef.current !== 'failed') return
        setPhase('')
        void doSubmit()
      }

      const challengeChip = isNonEmptyString(p.challenge)
        ? p.challenge
        : isNonEmptyString(p.brief)
          ? p.brief
          : null

      const settled = phase === 'done' || Boolean(confirmedMatch)

      let footer = null
      if (settled) {
        const recordedByEngine = Boolean(confirmedMatch)
        footer = h(
          'div',
          { className: 'ctfc-status ctfc-status--ok', role: 'status', 'aria-live': 'polite' },
          h('span', null, '✓'),
          h('span', null, recordedByEngine ? t('recordedByOther') : t('recordedStatus')),
          h('span', { className: 'ctfc-status__detail' }, t('recordedDetail', { flag, category })),
        )
      } else if (phase === 'pending') {
        footer = h(
          'p',
          { className: 'ctfc-status ctfc-status--wait', role: 'status', 'aria-live': 'polite' },
          h('span', { className: 'ctfp-spin' }, '⟳'),
          h('span', null, t('submittingStatus')),
        )
      } else if (phase === 'failed') {
        footer = h(
          'div',
          { className: 'ctfc-status ctfc-status--bad', role: 'alert', 'aria-live': 'assertive' },
          h('span', null, errorText || t('submitFailed')),
          h(
            'button',
            {
              className: 'ctfc-btn ctfc-btn--ghost',
              type: 'button',
              onClick: () => {
                void submitOrRetry()
              },
            },
            t('retryAfterFail'),
          ),
        )
      } else {
        footer = h(
          'div',
          { className: 'ctfc-footer' },
          h(
            'button',
            {
              className: 'ctfc-btn ctfc-btn--primary',
              type: 'button',
              onClick: () => {
                void submitOrRetry()
              },
              disabled: started || !sessionId,
            },
            t('submitConfirm'),
          ),
          h(
            'button',
            {
              className: 'ctfc-btn ctfc-btn--ghost',
              type: 'button',
              onClick: () => { setPhase('dismissed') },
              disabled: !sessionId,
            },
            t('skip'),
          ),
          !sessionId ? h('span', { className: 'ctfc-status__detail', style: { color: 'var(--dsw-alias-label-secondary, #667085)' } }, t('cardNoSession')) : null,
        )
      }

      return h(
        'div',
        { className: 'ctfc-wrap' },
        Styles(),
        h(
          'div',
          { className: 'ctfc-card' },
          h(
            'div',
            { className: 'ctfc-top' },
            h('span', { className: 'ctfc-title' }, title),
            category ? h('span', { className: 'ctfp-badge', title: t('colCat') }, category) : null,
            challengeChip ? h('span', { className: 'ctfc-chip', title: challengeChip }, challengeChip) : null,
          ),
          h(
            'p',
            { className: 'ctfc-hint' },
            t('cardHint'),
            ' ',
            h('code', { className: 'ctfc-flag' }, flag || '—'),
          ),
          h(
            'p',
            { className: 'ctfc-meta' },
            category ? h('span', null, `${t('colCat')}：`, h('b', null, category)) : null,
            p.challenge || title
              ? h('span', null, `${t('formBody')}：`, h('b', { title: p.challenge || title }, String(p.challenge || title).slice(0, 60)))
              : null,
          ),
          footer,
        ),
      )
    }

    /** 作废看板缓存新鲜度：提交确认卡记账成功后调用。数据已确定陈旧，但保留
     *  data 先行显示（下次进面板立即出旧数据 + 强制后台再校验，Host 侧快照
     *  已被 /ctf-record-win 作废重查，内容比较后静默替换），不再退回 loading。 */
    function invalidateDashboard() {
      if (dashboardCache !== null) dashboardCache = { ...dashboardCache, at: 0 }
    }

    /* ============================================================
     * 三界面共享状态与工具
     * ============================================================ */

    /** 工作区服务持有者（跳转会话 / 目录选择；缺服务时安全降级）。 */
    let uiWorkspaceHolder = null
    /** Workspace Controller 服务持有者（新建工作区注册：create({path})；缺服务时安全降级）。 */
    let workspacesHolder = null
    /** Session Controller 客户端服务持有者：sessions.open 聚焦会话（openTargetSession）。 */
    let sessionsHolder = null
    /** 状态板持久化键（localStorage：跨会话、跨重载存活）。 */
    const SOLVEBOARD_KEY = 'ctfpilot.solveboard.v1'

    function readSolveBoard() {
      try {
        const raw = JSON.parse(localStorage.getItem(SOLVEBOARD_KEY) || '{}')
        const sessions = raw && raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {}
        return sessions
      } catch (_e) { return {} }
    }

    function writeSolveBoard(sessions) {
      try { localStorage.setItem(SOLVEBOARD_KEY, JSON.stringify({ version: 1, sessions })) }
      catch (_e) { /* 隐私模式 / 配额：降级为纯内存态 */ }
    }

    const solveBoardListeners = []
    function notifySolveBoardChanged() { for (const fn of [...solveBoardListeners]) fn() }
    function subscribeSolveBoard(fn) {
      solveBoardListeners.push(fn)
      return () => { solveBoardListeners.splice(solveBoardListeners.indexOf(fn), 1) }
    }

    function defaultSolveEntry(mode) {
      return {
        mode: mode === 'deep' ? 'deep' : 'quick',
        cat: '', target: '', attaches: [], body: '',
        startedAt: 0, running: true, flags: [], status: 'running',
      }
    }

    /** upsert 合并写入并广播（合并保留既有字段，绝不整条覆盖）。 */
    function upsertSolveEntry(sid, patch) {
      const all = readSolveBoard()
      const base = Object.assign(
        {},
        defaultSolveEntry((patch && patch.mode) || (all[sid] && all[sid].mode) || 'quick'),
        all[sid] || {},
        patch || {},
      )
      if (!Array.isArray(base.flags)) base.flags = []
      all[sid] = base
      writeSolveBoard(all)
      notifySolveBoardChanged()
      return base
    }

    /** 把投影快照并入状态板：running 翻转 + flags 只追加计数（上限 99），
     *  一次读写 + 一次广播。轮询每 4s 一次，按会话逐条 read/write 会把
     *  JSON.parse/stringify 放大成 O(会话数) 次。 */
    function applySolveBoardSnapshot(snapSessions) {
      if (!snapSessions || typeof snapSessions !== 'object') return
      const all = readSolveBoard()
      let touched = false
      for (const sid of Object.keys(snapSessions)) {
        const cur = all[sid]
        const ss = snapSessions[sid]
        if (!cur || !ss || typeof ss !== 'object') continue
        if (typeof ss.running === 'boolean' && !!cur.running !== ss.running) {
          cur.running = ss.running
          touched = true
        }
        if (Array.isArray(ss.flags) && ss.flags.length > 0) {
          const flags = Array.isArray(cur.flags) ? cur.flags : (cur.flags = [])
          for (const f of ss.flags) {
            const i = flags.findIndex((x) => x && x.f === f)
            if (i < 0) { flags.push({ f, n: 1 }); touched = true }
            else if ((flags[i].n || 1) < 99) { flags[i].n = (flags[i].n || 1) + 1; touched = true }
          }
        }
      }
      if (!touched) return
      writeSolveBoard(all)
      notifySolveBoardChanged()
    }

    /** 历史记录删除（板清理）：已完成/失败的解题条目可清，板只余活跃项。 */
    function removeSolveEntry(sid) {
      if (!sid) return false
      const all = readSolveBoard()
      if (!Object.prototype.hasOwnProperty.call(all, sid)) return false
      delete all[sid]
      writeSolveBoard(all)
      notifySolveBoardChanged()
      return true
    }

    function solveLabel(t, mode) { return mode === 'quick' ? t('modeQuick') : t('modeDeep') }

    function pad2(v) { return String(v).padStart(2, '0') }
    function fmtClock(ts) {
      const d = new Date(Number(ts) || 0)
      return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
    }

    /** 解投影 Snap 的两种包装形态（与 asSnap 同款防御解包）。 */
    function asSolveSnap(raw) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
      const cand = raw.sessions && typeof raw.sessions === 'object' && !Array.isArray(raw.sessions)
        ? raw
        : raw.value && raw.value.sessions && typeof raw.value.sessions === 'object' && !Array.isArray(raw.value.sessions)
          ? raw.value
          : null
      if (!cand) return null
      return { sessions: cand.sessions || {} }
    }

    /** 远端结果解包：{ok, value:{result:{kind,text}}}。
     *  2026-11-19 用户令「删光吞错降级」：传输失败（out.ok===false）时把
     *  out.error（code+message）完整取出——此前返回空串，一切失败都成了
     *  面板上的「无事发生」。 */
    function remoteOutcome(out) {
      const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
      const result = value && value.result && typeof value.result === 'object' ? value.result : null
      if (result) {
        return { ok: result.kind === 'success', text: isNonEmptyString(result.text) ? result.text : '' }
      }
      if (out && out.ok === false) {
        const err = out.error
        const code = err && isNonEmptyString(err.code) ? err.code : 'transport-error'
        const message = err && isNonEmptyString(err.message) ? err.message : (isNonEmptyString(out.message) ? out.message : '远端命令执行失败（无详情）')
        return { ok: false, text: `[${code}] ${message}` }
      }
      return { ok: false, text: '命令结果结构异常（无 result 字段）' }
    }

    /* ============================================================
     * HTTP 数据网关（2026-11-20）：面板↔Host 的数据与动作全走
     * fetch('/ctfpilot/data/<cmd>')——官方 webServer 命名路由，经
     * connection 认证（与 /api 同款 owner cookie，同源自动携带）。
     * 官方命令通道（remoteCommands.execute）每执行一次必写
     * command/run + command/done 两个 log-only 事件，24KB 看板 JSON 会
     * 永久落进会话日志并折成对话卡片（用户令：对话里只允许解题上下文）。
     * 返回值刻意保持与 remoteCommands.execute 相同的信封形状
     * {ok, value:{result:{kind,text}}} / {ok:false, error:{code,message}}，
     * 下游 remoteOutcome / resultTextOf / 手工解包零改动。
     * GET 走 ?args=（URL 编码）；POST 走请求体（题面正文/含反斜杠路径
     * 零转码）。会话作用域命令（solve-start/writeup/record-win）带 ?sid=，
     * Host 侧经 ctx.agents 解析 agent——与命令通道同源语义。
     * ============================================================ */
    async function panelDataFetch(command, opts) {
      const o = opts || {}
      const line = String(command || '').trim()
      const sp = line.indexOf(' ')
      const head = (sp < 0 ? line : line.slice(0, sp)).replace(/^\//, '')
      const args = sp < 0 ? '' : line.slice(sp + 1).trim()
      if (!head) throw new Error('数据网关：空命令')
      const method = o.method === 'POST' ? 'POST' : 'GET'
      const qs = []
      if (isNonEmptyString(o.sid)) qs.push('sid=' + encodeURIComponent(String(o.sid)))
      if (method === 'GET' && args) qs.push('args=' + encodeURIComponent(args))
      const url = '/ctfpilot/data/' + encodeURIComponent(head) + (qs.length ? '?' + qs.join('&') : '')
      let res = null
      try {
        res = await fetch(url, method === 'POST' ? { method, body: args } : { method })
      } catch (e) {
        // 网络层失败（Host 不可达/路由未挂）：显式带修复指引，绝不静默。
        throw new Error('数据网关不可达（fetch 失败）：' + String((e && e.message) || e) +
          '——若刚更新插件，请完全退出 DSH（任务管理器确认进程清零）后重启，使 Host 挂载 /ctfpilot/data 路由。')
      }
      let payload = null
      try { payload = await res.json() } catch (_e) { payload = null }
      if (!res.ok) {
        return {
          ok: false,
          error: {
            code: 'gateway-' + String(res.status),
            message: (payload && isNonEmptyString(payload.text) ? String(payload.text) : '') ||
              (res.status === 404
                ? 'Host 未挂 /ctfpilot/data 路由（旧版 Host 未加载新插件）——请完全退出 DSH（确认进程清零）后重启。'
                : `HTTP ${String(res.status)}`),
          },
        }
      }
      if (!payload || typeof payload !== 'object' || typeof payload.kind !== 'string') {
        return { ok: false, error: { code: 'gateway-bad-json', message: '网关返回非 CommandResult JSON' } }
      }
      return { ok: true, value: { result: payload } }
    }

    function toolsKindLabel(t, kind) {
      return kind === 'pip' ? t('kindPip') : kind === 'mcp' ? t('kindMcp') : t('kindMirror')
    }

    /** 工具详情消毒：去 ANSI/控制符、折成单行、截断 120（治 UI-3 乱码观感）。 */
    function sanitizeToolDetail(raw) {
      const s = String(raw == null ? '' : raw)
        .replace(/\u001b\[[0-9;]*m/g, '')
        .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
      const oneLine = s.replace(/\r?\n/g, ' ⏎ ').trim()
      return oneLine.length > 120 ? `${oneLine.slice(0, 117)}…` : oneLine
    }

    async function openTargetSession(sid) {
      // 聚焦新会话优先走 sessions.open（官方选择当前会话的标准方法，同步 select）；
      // uiWorkspace.openSession 作兜底。2026-11-19 删光吞错：两路都失败时
      // console.error 显形（此前静默——新会话建好却没跳转，用户看到「无事发生」）。
      const ss = sessionsHolder
      if (ss && typeof ss.open === 'function') {
        try { ss.open(sid); return } catch (e1) {
          try { console.error('[ctfpilot] sessions.open failed:', e1) } catch (_c) { /* console 不可用 */ }
        }
      }
      const ws = uiWorkspaceHolder
      if (ws && typeof ws.openSession === 'function') {
        try { await ws.openSession(sid) } catch (e2) {
          try { console.error('[ctfpilot] uiWorkspace.openSession failed:', e2) } catch (_c2) { /* console 不可用 */ }
        }
      }
    }

    async function pickAttachmentDir() {
      const ws = uiWorkspaceHolder
      if (ws && typeof ws.pickDirectory === 'function') {
        try { const picked = await ws.pickDirectory(); return picked || '' } catch (_e) { return '' }
      }
      return ''
    }

    function mergeAttachUnique(prev, incoming) {
      // Set 去重：原 indexOf 在附件列表上是 O(n²)（粘贴整段路径时可能几十项）。
      const seen = new Set()
      const out = []
      for (const p of [...(prev || []), ...(incoming || [])]) {
        const v = String(p == null ? '' : p).trim()
        if (v && !seen.has(v)) { seen.add(v); out.push(v) }
      }
      return out
    }

    function buildStartCommand(mode, cat, attaches, body, target) {
      const parts = ['/ctf-solve-start', mode === 'deep' ? 'deep' : 'quick']
      const c = String(cat || '').trim()
      if (c) parts.push('--category', shellArg(c))
      // 靶机地址（2026-11-24）：扫描/连接类题目的授权靶标——session-2193d69d
      // 实锤：题面只说「nmap 扫出来的所有端口」没给目标，agent 无法使用已装好的
      // nmap 只能反问。显式传 --target 后题面自带靶标，agent 可直接开扫。
      const tg = String(target || '').trim()
      if (tg) parts.push('--target', shellArg(tg))
      for (const a of attaches) if (isNonEmptyString(a)) parts.push('--attach', shellArg(a))
      const b = String(body || '').trim()
      // 正文永远带引号：单词正文也不被 Host 的位置模式词（quick/deep）误吞。
      if (b) parts.push(shellQuote(b))
      return parts.join(' ')
    }

    /** 「未解出 → 同会话续攻」命令（2026-11-25 用户方案）：字段拼装同
     *  buildStartCommand，但命令头是 /ctf-continue——Host 不切预设、不 fork，
     *  直接对原会话 followup 开新回合（上下文与进度全保留）。 */
    function buildContinueCommand(cat, attaches, body, target) {
      const parts = ['/ctf-continue']
      const c = String(cat || '').trim()
      if (c) parts.push('--category', shellArg(c))
      const tg = String(target || '').trim()
      if (tg) parts.push('--target', shellArg(tg))
      for (const a of (attaches || [])) if (isNonEmptyString(a)) parts.push('--attach', shellArg(a))
      const b = String(body || '').trim()
      // 正文永远带引号：单词正文也不被 Host 的位置模式词（quick/deep）误吞。
      if (b) parts.push(shellQuote(b))
      return parts.join(' ')
    }

    /* ============================================================
     * UI-1 + UI-2 卡片：composer.dock 内的解题表单与该会话的实时
     * 状态卡；「正在解题」标签复用同一张卡片实现。
     * 双按钮均先展开内联确认面板再执行（避免误点）。
     * ============================================================ */

    /** 单会话状态卡：flags 只增不减；双按钮流转规则同用户口径。 */
    function SolveCardInner(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const entry = props.entry || null
      const sid = isNonEmptyString(props.sid) ? props.sid : currentSessionId
      const onJump = typeof props.onJump === 'function' ? props.onJump : null
      const onDelete = typeof props.onDelete === 'function' ? props.onDelete : null
      const [confirming, setConfirming] = useState('') // '' | 'solved' | 'unsolved'
      const [feedback, setFeedback] = useState('')
      const [feedbackKind, setFeedbackKind] = useState('') // '' | 'ok' | 'bad'
      const [busy, setBusy] = useState(false)
      // 反双击守卫：state 更新是异步的，窗口内第二次点击会读到旧 busy=false；
      // ref 同步生效，保证一次操作只发一条命令。
      const busyRef = useRef(false)

      async function actSolved() {
        if (!sid || busyRef.current) return
        busyRef.current = true
        setBusy(true)
        try {
          const name = String(entry.body || '').slice(0, 60) || String(entry.cat || '')
          // 2026-11-25 用户实锤「已解出按钮不好使」：旧实现把「标记解出」与
          // 「writeup 归档注入」捆成一个事务——归档请求失败（会话已关/无活动
          // agent/网关瞬断/401）时 catch 直接吃掉点击，本地状态也不落，用户
          // 观感就是按钮没反应。现拆开：标记解出永远落地（用户点按钮的本意），
          // 归档注入是增值链，失败以警示文案附上原始原因，绝不回滚用户意图。
          let warn = ''
          try {
            const out = await panelDataFetch(
              '/ctf-writeup' + (name ? ' --name ' + shellArg(name) : ''),
              { method: 'POST', sid })
            const oc = remoteOutcome(out)
            if (!oc.ok) warn = String(oc.text || '')
          } catch (_err) {
            warn = String((_err && _err.message) || _err || '')
          }
          upsertSolveEntry(sid, { status: 'solved', running: false })
          if (warn) {
            setFeedback(`${t('solvedMarked')}（writeup 归档未注入：${warn}）`)
            setFeedbackKind('bad')
          } else {
            setFeedback(t('solvedMarked'))
            setFeedbackKind('ok')
          }
        } finally { busyRef.current = false; setBusy(false) }
      }

      async function actUnsolved() {
        if (!sid || busyRef.current) return
        busyRef.current = true
        setBusy(true)
        try {
          if (entry.running) throw new Error(t('unsolvedWhileRunning'))
          // 2026-11-25 用户方案定稿：fork 出的新会话不能切换 agent 预设
          // （回合历史被继承 → 预设锁定），也无法正确接续原对话；放弃 fork
          // 迁移，改为在同一会话开新回合继续解题——上下文与进度全保留。
          // 注入必须 await + 检查结果：旧实现 `void panelDataFetch(...)`
          // 发射后不管，网关瞬断/agent 未注册时失败被吞成「点了没反应」。
          const merged = upsertSolveEntry(sid, { status: 'unsolved', running: false })
          const cmd = buildContinueCommand(merged.cat, merged.attaches, merged.body, merged.target)
          // 先确保该会话在 Host 活动表中：从看板点击「已关闭会话」的卡片续攻时，
          // agent 可能尚未注册（POST 会撞「无活动会话 agent」）。sessions.open
          // 聚焦并触发会话加载；打不开也继续走注入 + 重试兜底。
          try { await openTargetSession(sid) } catch (_e) { /* 打不开也先尝试注入 */ }
          let lastText = ''
          for (let attempt = 0; attempt < 2; attempt++) {
            try {
              const out = await panelDataFetch(cmd, { method: 'POST', sid })
              const oc = remoteOutcome(out)
              if (oc.ok) {
                upsertSolveEntry(sid, { status: 'unsolved', running: true })
                setFeedback(t('unsolvedContinueQueued'))
                setFeedbackKind('ok')
                return
              }
              lastText = String(oc.text || '')
              // 会话刚重建/agent 尚未注册的竞态：等 1.5s 重试一次。
              if (attempt === 0 && /无活动会话 agent|agent 不存在/.test(lastText)) {
                await new Promise((r) => setTimeout(r, 1500))
                continue
              }
              break
            } catch (_err) {
              lastText = String((_err && _err.message) || _err || '')
              if (attempt === 0) { await new Promise((r) => setTimeout(r, 1500)); continue }
              break
            }
          }
          setFeedback(`✗ 继续解题注入失败：${lastText || t('submitFailed')}`)
          setFeedbackKind('bad')
        } catch (_err) {
          setFeedback(_err && _err.message ? _err.message : t('submitFailed'))
          setFeedbackKind('bad')
        } finally {
          busyRef.current = false
          setBusy(false)
          // 动作落地即收起确认面板：避免「成功后又点一次确认」重复入队续攻。
          setConfirming('')
        }
      }

      if (!entry) return null
      const modeLbl = solveLabel(t, entry.mode)
      const titlePreview = String(entry.body || '').slice(0, 80)
      const flagsView = Array.isArray(entry.flags) ? entry.flags : []

      return h(
        'div',
        {
          className: 'ctfp-scard' + (entry.status === 'solved' ? ' ctfp-scard--done' : ''),
          ...(onJump ? {
            // 整卡可点：同 dsh-context 卡片交互；完成态不可点（终态不再跳转）。
            role: 'button',
            tabIndex: 0,
            onClick: () => { void onJump(sid) },
            onKeyDown: (e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void onJump(sid) }
            },
          } : {}),
        },
        h(
          'div',
          { className: 'ctfp-scard__top' },
          h('span', { className: 'ctfp-badge' }, modeLbl),
          // 点归点、字归字：.ctfp-dot 是 8px 圆点容器，文字必须作兄弟元素
          // 塞进圆点会溢出压排（2026-11-25 用户实锤「运行中/本轮已结束
          // 乱排」的根因；正确范式同工具管理行 ctfp-toolstate）。
          h('span', { className: 'ctfp-runstate' },
            h('span', { className: 'ctfp-dot ' + (entry.running ? 'ctfp-dot--run' : 'ctfp-dot--off') }),
            h('span', null, entry.running ? t('solvingRunning') : t('solvingIdle')),
          ),
          entry.status === 'solved' ? h('span', { className: 'ctfp-badge ctfp-badge--ok' }, t('solved')) : null,
          entry.status === 'unsolved' ? h('span', { className: 'ctfp-badge ctfp-badge--off' }, t('btnUnsolved')) : null,
          entry.status === 'failed' ? h('span', { className: 'ctfp-badge ctfp-badge--off' }, t('startFailedBadge')) : null,
          h('span', { style: { marginLeft: 'auto', fontSize: '11.5px', opacity: '.75' } }, fmtClock(entry.startedAt)),
          onDelete !== null ? h('button', {
            type: 'button',
            className: 'ctfp-btn ctfp-toolbtn ctfp-scard__del',
            'aria-label': t('solvingDelHint'),
            title: t('solvingDelHint'),
            onClick: (e) => { e.stopPropagation(); onDelete() },
          }, '✕') : null,
        ),
        titlePreview ? h('div', { className: 'ctfp-scard__meta' }, titlePreview) : null,
        h('div', { className: 'ctfp-flags' }, [
          h('span', { style: { fontSize: '11.5px', opacity: '.8' } }, t('solvingFlagsTitle')),
          flagsView.length === 0
            ? h('span', { style: { fontSize: '11.5px', opacity: '.55' } }, t('solvingNoFlags'))
            : flagsView.map((fv) =>
                h('code', { key: `sf-${fv.f}`, className: 'ctfp-sflag', title: String(fv.f) }, `${fv.f}×${fv.n || 1}`)),
        ]),
        h(
          'div',
          { className: 'ctfp-scard__acts' },
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              // 2026-11-25 用户实锤「点按钮跳到原会话然后无事发生」：整张卡片
              // 绑定了 onClick=跳转会话，按钮点击默认冒泡到卡片 → 点「已解出/
              // 未解出」瞬间被导航带走，确认面板根本来不及操作。卡片内所有
              // 交互按钮必须 stopPropagation（删除键早已有，其余键补齐）。
              onClick: (e) => { e.stopPropagation(); setFeedback(''); setConfirming('solved') },
              disabled: busy || entry.status === 'solved',
            },
            t('btnSolved'),
          ),
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              onClick: (e) => { e.stopPropagation(); setFeedback(''); setConfirming('unsolved') },
              // 2026-11-25 同会话续攻：status='unsolved' 不再永久禁用——一轮续攻
              // 结束仍未解出时，用户可再次点按发起下一轮（仅运行中禁用）。
              disabled: busy || entry.running,
              title: entry.running ? t('unsolvedWhileRunning') : undefined,
            },
            t('btnUnsolved'),
          ),
          // 跳转按钮归位操作行（曾误插在标题行与 flag 行之间，缩进/位置
          // 突兀，构成「乱排字样」的一部分）；整卡可点跳转仍是主入口。
          onJump ? h('button', {
            className: 'ctfp-btn ctfp-toolbtn',
            type: 'button',
            'aria-label': t('jumpHint'),
            title: t('jumpHint'),
            onClick: (e) => { e.stopPropagation(); void onJump(sid) },
          }, t('jumpHint')) : null,
          confirming
            ? h('div', { className: 'ctfp-tooldetail', role: 'alertdialog' }, [
                h('p', null, confirming === 'solved' ? t('solvedQNote') : t('unsolvedQNote')),
                h('div', { style: { marginTop: '8px', display: 'flex', gap: '8px' } }, [
                  h('button', {
                    className: 'ctfp-btn',
                    type: 'button',
                    disabled: busy,
                    onClick: (e) => {
                      e.stopPropagation()
                      void (confirming === 'solved' ? actSolved() : actUnsolved())
                    },
                  }, t('confirmAction')),
                  h('button', {
                    className: 'ctfp-btn',
                    type: 'button',
                    disabled: busy,
                    onClick: (e) => { e.stopPropagation(); setConfirming('') },
                  }, t('skip')),
                ]),
              ])
            : null,
          (feedback || feedbackKind)
            ? h('div', {
                className: feedbackKind === 'ok'
                  ? 'ctfc-status ctfc-status--ok'
                  : 'ctfc-status ctfc-status--bad',
                role: feedbackKind === 'ok' ? 'status' : 'alert',
              }, h('span', null, feedback))
            : null,
        ),
      )
    }

    /* ============================================================
     * 「新建工作区 + 对话」解析器：三层降级链（模块级，单一职责可测）。
     * 建会话一律走 ClientSessions.create({workspaceId})——官方 New Session
     * 按钮同款 API（直发 Host session.create RPC；resolve 时新会话已在列
     * 表，可直接寻址/open）。uiWorkspace.connectWorkspace 是导航方法：只在
     * 零会话工作区里才播种新会话，复用既有工作区时只会聚焦旧会话，永远建
     * 不出新对话（旧版「不能创建新对话」的根源）——已退役。
     *   层1 全新工作区：listDirectory → createDirectory(ctf-stamp) →
     *        workspaces.create({path}) → sessions.create({workspaceId})
     *   层2 既有工作区新对话：workspaces.list 快照取一行 → sessions.create({workspaceId})
     *   层3 当前会话（全部失败，附原始错误）
     * 返回 { sid, note }：note 非空 = 非致命降级需上屏提示。
     * ============================================================ */

    async function ensureSolveSession(currentSid, t, workerKind = 'CTF-worker') {
      // 层1 解析出的工作区目录（复用或新建）。调用方（交叉重刷 staging）直接
      // 使用，不再二次依赖 workspaces 快照的时序（2026-11-26 resume 无反应根因之二）。
      let resolvedDir = ''
      const wsvc = uiWorkspaceHolder
      const ssvc = sessionsHolder
      const wssvc = workspacesHolder
      const pathRx = new RegExp(`[\\\\/]${workerKind}$`, 'i')
      const titleRx = new RegExp(`^${workerKind}$`, 'i')
      // 建会话的服务是硬依赖（workspaces/uiWorkspace 缺失自动降层2/层3）。
      if (!(ssvc && typeof ssvc.create === 'function')) {
        return { sid: currentSid, note: t('formWsNoService') }
      }
      const brief = (e) => String((e && e.message) || e).slice(0, 120)
      const createSessionIn = async (wid) => {
        const sid2 = await ssvc.create({ workspaceId: wid })
        return isNonEmptyString(sid2) ? String(sid2) : ''
      }

      // 层2：目录/注册/建会话任一环节失败时，退到「既有工作区里新建对话」；
      // 新对话是核心诉求，工作区复用既有目录可接受（官方 New Session 同口径）。
      // 2026-11-19：catch 不再吞原因——复用失败时带出原始错误，层3 文案含全链失败原因。
      async function reuseExistingWorkspace() {
        try {
          const snap = wssvc && wssvc.list && typeof wssvc.list.getSnapshot === 'function'
            ? wssvc.list.getSnapshot() : null
          const items = snap && Array.isArray(snap.items) ? snap.items : []
          const first = items.find((it) => it && isNonEmptyString(it.workspaceId)) || null
          if (!first) return { sid: '', name: '', err: '既有工作区列表为空（无可复用行）' }
          const wid = String(first.workspaceId)
          const nsid = await createSessionIn(wid)
          const name = String(first.title || first.path || wid)
          return isNonEmptyString(nsid)
            ? { sid: nsid, name, err: '' }
            : { sid: '', name: '', err: `sessions.create 未返回会话 id（workspaceId=${wid}）` }
        } catch (e) {
          return { sid: '', name: '', err: brief(e) }
        }
      }

      // —— 层1：固定 CTF-worker 工作区（2026-11-20 用户令改造）——
      // 不再按时间戳建新工作区：全部解题对话固定住在唯一的 CTF-worker。
      // 顺序：workspaces.list 已有 CTF-worker（path 尾段/标题匹配，大小写不
      // 敏感——Windows 文件系统同口径）→ 直接复用其 workspaceId 建新对话；
      // 没有 → /ctf-ensure-workspace 用 fs.mkdir 建目录（绕开 directoryPicker
      // ——它在 Windows 上永远路由 browse 交互对、capability 链接不上，
      // "list needs the browse capability" 即层1 历史上从未成功的真因）→
      // workspaces.create 注册 → 建对话。
      if (wssvc && typeof wssvc.create === 'function' &&
          wssvc.list && typeof wssvc.list.getSnapshot === 'function') {
        let wid = ''
        let reusedWorker = false
        try {
          const snap = wssvc.list.getSnapshot()
          const items = snap && Array.isArray(snap.items) ? snap.items : []
          const worker = items.find((it) => it && isNonEmptyString(it.workspaceId) &&
            ((isNonEmptyString(it.path) && pathRx.test(String(it.path).trim())) ||
             (isNonEmptyString(it.title) && titleRx.test(String(it.title).trim()))))
          if (worker) {
            wid = String(worker.workspaceId)
            reusedWorker = true
            if (isNonEmptyString(worker.path)) resolvedDir = String(worker.path).trim()
          } else {
            const withPath = items.find((it) => it && isNonEmptyString(it.workspaceId) && isNonEmptyString(it.path))
            const parent = withPath
              ? String(withPath.path).replace(/[\\/][^\\/]+$/, '')
              : ''
            if (!isNonEmptyString(parent)) throw new Error('无法推导工作区根目录（既有工作区列表为空或无 path）')
            const wantDir = `${parent}\\${workerKind}`
            const out = await panelDataFetch(`/ctf-ensure-workspace --dir ${shellArg(wantDir)}`, { method: 'POST' })
            const oc = remoteOutcome(out)
            if (!oc.ok) throw new Error(oc.text)
            let payload = null
            try { payload = JSON.parse(String(oc.text || '{}')) } catch { /* 下方兜底报错 */ }
            const dir = payload && isNonEmptyString(payload.dir) ? String(payload.dir) : ''
            if (!isNonEmptyString(dir)) throw new Error(`ctf-ensure-workspace 未返回目录（${String(oc.text).slice(0, 120)}）`)
            const wv = await wssvc.create({ path: dir })
            resolvedDir = dir
            wid = wv && isNonEmptyString(wv.workspaceId) ? String(wv.workspaceId) : ''
            if (!isNonEmptyString(wid)) throw new Error(`workspaces.create(${dir}) 未返回 workspaceId`)
          }
        } catch (e) {
          const reused = await reuseExistingWorkspace()
          if (isNonEmptyString(reused.sid)) {
            return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: `workspace: ${brief(e)}` }) }
          }
          return { sid: currentSid, note: t('formWsFallback', { err: `workspace: ${brief(e)}；层2 复用也失败：${reused.err}` }) }
        }
        try {
          const nsid = await createSessionIn(wid)
          if (isNonEmptyString(nsid)) {
            return { sid: nsid, note: reusedWorker ? t('formWsWorkerReused') : '', dir: resolvedDir }
          }
          return { sid: currentSid, note: t('formWsFallback', { err: `sessions.create(${workerKind} ${wid}) 未返回会话 id` }) }
        } catch (e) {
          const reused = await reuseExistingWorkspace()
          if (isNonEmptyString(reused.sid)) {
            return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: `session: ${brief(e)}` }) }
          }
          return { sid: currentSid, note: t('formWsFallback', { err: `session: ${brief(e)}；层2 复用也失败：${reused.err}` }) }
        }
      }

      // —— 层2：既有工作区里新建对话 ——
      const reused = await reuseExistingWorkspace()
      if (isNonEmptyString(reused.sid)) {
        return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: 'no new workspace registered' }) }
      }
      // —— 层3：当前会话 ——
      return { sid: currentSid, note: t('formWsFallback', { err: `无工作区服务/注册失败：${reused.err}` }) }
    }

    /* ============================================================
     * UI-1 主件：「开始解题」标签内的表单。
     * 与 Skill Explorer 创建页同形态：字段纵排 + primaryButton +
     * feedback banner。提交即向当前会话执行 /ctf-solve-start：
     * Host 先按所选模式切换该对话的 agent 预设，再注入题面。
     * ============================================================ */

    function SolveFormPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const sid = isNonEmptyString(props.sid) ? props.sid : currentSessionId

      const [body, setBody] = useState('')
      const [cat, setCat] = useState('')
      const [target, setTarget] = useState('')
      const [mode, setMode] = useState('quick')
      const [newSession, setNewSession] = useState(true)
      const [attaches, setAttaches] = useState([])
      const [pickMsg, setPickMsg] = useState('')
      const [startMsg, setStartMsg] = useState('')
      const [startKind, setStartKind] = useState('')
      const [busy, setBusy] = useState(false)
      const [entry, setEntry] = useState(null)
      /** 短题面二次确认（时钟纪律：占位文本开跑前拦一道，防无界全盘搜索） */
      const [shortConfirm, setShortConfirm] = useState(false)

      // 看板广播 → 本表单下方的实时条目卡刷新。
      useEffect(() => {
        const unsub = subscribeSolveBoard(() => { setEntry(readSolveBoard()[sid] || null) })
        return () => { if (typeof unsub === 'function') unsub() }
      }, [sid])

      // 快捷入口：整段粘贴到附件框时也按 ; /换行拆分入库。
      function commitAttachText(v) {
        const parts = String(v == null ? '' : v)
          .split(/[;\n]/).map((x) => x.trim()).filter(Boolean)
        if (parts.length === 0) return
        setAttaches((prev) => mergeAttachUnique(prev, parts))
      }

      async function doStart(forceShort) {
        if (busy) return
        const b = String(body || '').trim()
        if (!b && attaches.length === 0) {
          setStartMsg(t('formNeedInput'))
          setStartKind('bad')
          return
        }
        // 短题面防呆（2026-11-23 实锤：题面「测试」两字开跑后 agent 在空
        // 工作区把整块磁盘当靶场，20 步全盘搜索白烧 4 分钟）——开跑前拦一道
        // 二次确认；点「就是测试，开跑」后 forceShort 放行。
        if (!forceShort && b && b.length < 8 && attaches.length === 0) {
          setShortConfirm(true)
          setStartMsg('')
          setStartKind('')
          return
        }
        setShortConfirm(false)
        setBusy(true)
        setStartMsg('')
        // 提到 try 外：catch 分支要按实际目标会话标 failed；
        // 原实现声明在 try 内，catch 访问会抛 ReferenceError 并吞掉真实异常。
        let targetSid = sid
        try {
          const cmd = buildStartCommand(mode, cat, attaches, body, target)
          let wsFallbackNote = ''
          if (newSession) {
            // 「新建工作区 + 对话」：三层链每一层失败都显式提示（绝不静默）：
            //   层1 全新工作区：listDirectory() 取 Host home → createDirectory 建
            //      新目录 → workspaces.create({path}) 注册 → sessions.create
            //      ({workspaceId}) 开新对话（官方 New Session 同款 API）；
            //   层2 既有工作区新对话：目录/注册/建会话失败时，用 workspaces.list
            //      快照里的既有工作区 sessions.create——新对话（核心诉求）仍达成；
            //   层3 当前会话：全部失败才退，附原始错误。
            // 服务一律走模块级持有者；agent 预设由 Host /ctf-solve-start 在新
            // 会话（生而为 blank）上切换，前端不介入。
            const ensured = await ensureSolveSession(sid, t)
            targetSid = ensured.sid
            wsFallbackNote = ensured.note
            if (isNonEmptyString(targetSid) && targetSid !== sid) {
              // 只登记 currentSessionId（状态板路由用）；**跳转推迟到命令成功后**——
              // 2026-11-19 实锤：先跳新会话页会把面板里的错误 banner 冲掉，
              // 失败时用户只看到「跳过去后无事发生」。
              currentSessionId = targetSid
              notifySessionChanged()
            }
          }
          upsertSolveEntry(targetSid, {
            mode, cat, target, attaches, body,
            startedAt: Date.now(), running: true, flags: [], status: 'running',
          })
          const out = await panelDataFetch(cmd, { method: 'POST', sid: targetSid })
          const oc = remoteOutcome(out)
          if (oc.ok) {
            // 成功才跳转新会话页（见上）。
            if (isNonEmptyString(targetSid) && targetSid !== sid) void openTargetSession(targetSid)
            // 新建工作区降级但注入仍成功：成功文案后附降级原因（红色警示），不静默。
            setStartMsg(wsFallbackNote ? `${t('formStarted')} ${wsFallbackNote}` : t('formStarted'))
            setStartKind(wsFallbackNote ? 'bad' : 'ok')
          } else {
            // Host 返回 error：不跳转（用户停留在面板上，错误 banner 可见）。
            // 原文完整上屏（remoteOutcome 已携带 code+message），不再折成通用文案。
            // 状态板同拍把条目标为 failed（2026-11-19 实锤：否则板子永远挂一张
            // running 幽灵卡，用户点「正在解题」看到的就是它）。
            upsertSolveEntry(targetSid, { status: 'failed', running: false })
            setStartMsg(`✗ ${oc.text || t('formStartFail')}${wsFallbackNote ? `\n（新建会话降级备注：${wsFallbackNote}）` : ''}`)
            setStartKind('bad')
          }
        } catch (_err) {
          // 2026-11-19 用户令「删光吞错降级」：原始异常全文上屏，不再映射通用文案。
          // 通用指引仅作追加行，真因永远可见。
          const raw = String((_err && _err.message) || _err || '(unknown error)')
          const stack = String((_err && _err.stack) || '').slice(0, 400)
          const noRoute = _err && _err.message === 'no-route-session'
          const extra = noRoute ? t('formNoSession') : `${t('formNoBridge')}`
          setStartMsg(`✗ 启动异常：${raw}${stack ? `\n${stack}` : ''}\n${extra}`)
          setStartKind('bad')
          upsertSolveEntry(isNonEmptyString(targetSid) ? targetSid : sid, { status: 'failed', running: false })
          try { console.error('[ctfpilot] doStart failed:', _err) } catch (_c0) { /* console 不可用时忽略 */ }
          if (!noRoute) {
            try {
              if (typeof navigator !== 'undefined' && navigator.clipboard &&
                  typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(String(body || ''))
              }
            } catch (_ce) { /* 剪贴板失败：提示已含 manualHint */ }
          }
        } finally {
          setBusy(false)
        }
      }

      // 表单永远渲染：新建工作区+对话流程本身不依赖已有会话（此前把无会话
      // 用户直接挡在空态外，与「开始解题」的语义自相矛盾）。层3 降级时若路由
      // 池也空，doStart 会显式提示先打开对话。
      return h(
        'div',
        { className: 'ctfp-dock-card' },
        h(
          'div',
          { className: 'ctfp-dock-head' },
          h('span', { className: 'ctfp-dock-title' }, t('formTitle')),
          h('span', { className: 'ctfp-dock-note' }, t('formSub')),
        ),
        h(
          'label',
          { className: 'ctfp-formlab' },
          t('formMode'),
          h('select', {
            className: 'ctfp-filter-sel',
            value: mode,
            onChange: (e) => { setMode(e.target.value) },
          }, [
            h('option', { value: 'quick' }, t('modeQuick')),
            h('option', { value: 'deep' }, t('modeDeep')),
          ]),
        ),
        h('p', { className: 'ctfp-dock-note', style: { marginBottom: '8px' } }, t('formModeNote')),
        h('p', { className: 'ctfp-dock-note', style: { marginBottom: '8px' } }, t('formNewSessionNote')),
        h(
          'textarea', {
            className: 'ctfp-input-area',
            placeholder: t('formBodyPh'),
            value: body,
            onChange: (e) => { setBody(e.target.value) },
            rows: 3,
          },
        ),
        h(
          'div',
          { className: 'ctfp-attrow' },
          h('input', {
            className: 'ctfp-search',
            placeholder: t('formAttachPh'),
            value: attaches.join('; '),
            onChange: (e) => { commitAttachText(e.target.value) },
            onKeyDown: (e) => { if (e.key === 'Enter') { e.preventDefault(); commitAttachText(e.target.value) } },
            'aria-label': t('formAttach'),
          }),
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              onClick: async () => {
                const dir = await pickAttachmentDir()
                if (dir) {
                  setAttaches((prev) => mergeAttachUnique(prev, [dir]))
                  setPickMsg(`${dir} ✓`)
                } else {
                  setPickMsg(t('formAttachPh'))
                }
              },
            },
            t('formAttachPick'),
          ),
        ),
        attaches.length > 0
          ? h('div', { className: 'ctfp-flags' }, attaches.map((a, i) =>
              h('code', { key: `att-${i}`, className: 'ctfp-sflag' }, a)))
          : null,
        pickMsg ? h('div', { className: 'ctfp-tooldetail' }, pickMsg) : null,
        h(
          'label',
          { className: 'ctfp-formlab' },
          t('formCat'),
          h('input', {
            className: 'ctfp-search',
            placeholder: t('formCatPh'),
            value: cat,
            onChange: (e) => { setCat(e.target.value) },
          }),
        ),
        // 靶机地址（2026-11-24）：扫描/连接类题目无靶标时 agent 无法开扫
        // （session-2193d69d 实锤）。显式填写后随 --target 注入题面。
        h(
          'label',
          { className: 'ctfp-formlab' },
          t('formTarget'),
          h('input', {
            className: 'ctfp-search',
            placeholder: t('formTargetPh'),
            value: target,
            onChange: (e) => { setTarget(e.target.value) },
          }),
        ),
        h(
          'div',
          { className: 'ctfp-actions' }, [
            h(
              'button', {
                className: 'ctfp-btn ctfc-btn--primary',
                type: 'button',
                onClick: () => { void doStart() },
                disabled: busy,
              }, busy ? t('formStartBusy') : t('formStart'),
            ),
            (startMsg || startKind)
              ? h('span', {
                  className: startKind === 'ok'
                    ? 'ctfc-status ctfc-status--ok'
                    : 'ctfc-status ctfc-status--bad',
                  role: startKind === 'ok' ? 'status' : 'alert',
                }, h('span', null, startMsg))
              : null,
            // 短题面二次确认：确认才真正注入开跑，取消回到表单。
            shortConfirm
              ? h('div', { className: 'ctfp-tooldetail', role: 'alertdialog' }, [
                  h('p', { style: { margin: '0 0 8px' } }, t('formShortWarn')),
                  h('div', { style: { display: 'flex', gap: '8px' } }, [
                    h('button', {
                      className: 'ctfp-btn ctfc-btn--primary',
                      type: 'button',
                      onClick: () => { void doStart(true) },
                    }, t('formShortGo')),
                    h('button', {
                      className: 'ctfp-btn',
                      type: 'button',
                      onClick: () => { setShortConfirm(false) },
                    }, t('formShortCancel')),
                  ]),
                ])
              : null,
          ],
        ),
        entry ? h(SolveCardInner, { key: 'dock-live-' + sid, entry, sid, t }) : null,
      )
    }

    /* ============================================================
     * UI-2：「正在解题」多会话状态板（主面板标签）。
     * ============================================================ */

    function SolvingBoard(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [sessions, setSessions] = useState(() => readSolveBoard())

      useEffect(() => {
        const unsub = subscribeSolveBoard(() => { setSessions(readSolveBoard()) })
        return () => { if (typeof unsub === 'function') unsub() }
      }, [])

// 4 秒快照轮询：Host /ctf-solving-state 实时回各会话的 running 与
      // flag 扫出结果——对话回合一结束 running 立刻翻 false（修复「未解出
      // 不随对话结束可点」），flag 只追加合并（修复提取遗漏）。
      // 2026-11-25 用户实锤「卡片不能正确读取到会话结束」：旧实现不带参数，
      // Host 只能回自折叠镜像（该折叠曾因不存在的 event.sessionId 恒空）。
      // 现把本地看板里的会话 id 全带上，Host 逐会话读官方 turnBoundary
      // 投影（openTurnStartSeq = 回合进行中），running 的翻转不再依赖
      // 任何自折叠状态。
      // 失败静默，下一拍重试；当前会话 id 走模块级持有者（闭包读活值）。
      useEffect(() => {
        async function poll() {
          try {
            const known = Object.keys(readSolveBoard())
            const out = await panelDataFetch('/ctf-solving-state' + (known.length ? ' ' + known.join(',') : ''))
            const oc = remoteOutcome(out)
            if (!oc.ok || !oc.text) return
            const snap = asSolveSnap(JSON.parse(oc.text))
            if (!snap) return
            // 一次读写 + 一次广播：running 与 flags 同拍合并（原实现两次遍历、
            // 每个会话各一次 localStorage 读写）。
            applySolveBoardSnapshot(snap.sessions)
          } catch (_e) { /* 静默重试 */ }
        }
        const timer = setInterval(() => { void poll() }, 4000)
        void poll()
        return () => clearInterval(timer)
      }, [])

      const entries = Object.entries(sessions || {})
        .map(([sid, entry]) => ({ sid, entry }))
        .filter((r) => r.entry && typeof r.entry === 'object')
        .sort((a, b) => (b.entry.startedAt || 0) - (a.entry.startedAt || 0))

      return h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('solvingTab')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('solvingTabSub')),
        h(
          'div',
          { className: 'ctfp-toolbar' }, [
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              onClick: () => { setSessions(readSolveBoard()) },
            },
            t('solvingRefresh'),
          ),
          h('span', { className: 'ctfp-catcount' }, String(entries.length)),
        ]),
        entries.length === 0
          ? h('div', { className: 'ctfp-empty' }, t('solvingEmpty'))
          : h(
              'div',
              { className: 'ctfp-boards' },
              entries.map((r) =>
                h(SolveCardInner, {
                  key: `sb-${r.sid}`,
                  entry: r.entry, sid: r.sid, t,
                  onJump: (targetSid) => { void openTargetSession(targetSid) },
                  onDelete: () => { removeSolveEntry(r.sid) },
                }),
              ),
            ),
      )
    }

    /* ============================================================
     * UI-3：CTF 工具管理（主面板标签）—— /ctf-tools status 行
     * 列表 + 装/更/卸；条目常驻，卸载后只回安装态。
     * ============================================================ */

    function ToolsPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [rows, setRows] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok | empty
      const [msg, setMsg] = useState('')
      const [msgKind, setMsgKind] = useState('')
      const [busy, setBusy] = useState(false)
      const busyRef = useRef(false)

      async function loadStatus(force) {
        // 缓存命中：立即上屏旧快检行并修状态面，后台静默校验。
        const cached = panelCacheGet('tools-status')
        if (cached !== null) {
          setRows((prev) => (prev === cached.data ? prev : cached.data))
          if (status === 'loading' || status === 'error') setStatus('ok')
        }
        if (!force && cached !== null && Date.now() - cached.at < PANEL_REVALIDATE_MS) return
        if (panelInFlight.has('tools-status')) return
        panelInFlight.add('tools-status')
        try {
          // force 时带 --fresh：跳过 Host 侧探针快照 TTL，刷新必拿新状态。
          const out = await panelDataFetch(`/ctf-tools status${force ? ' --fresh' : ''}`, { method: 'POST' })
          const oc = remoteOutcome(out)
          if (!oc.ok) throw new Error(oc.text || t('errorState'))
          const idx = String(oc.text).indexOf('[')
          if (idx < 0) throw new Error('malformed status output')
          const parsed = JSON.parse(String(oc.text).slice(idx))
          if (!Array.isArray(parsed)) throw new Error('malformed status output')
          const rowsNext = parsed
          const changed = panelCacheSet('tools-status', rowsNext)
          if (changed) setRows(rowsNext)
          if (cached === null) setStatus(rowsNext.length === 0 ? 'empty' : 'ok')
        } catch (_err) {
          // 无缓存：失败显式上屏；有缓存：保留旧行（后台校验失败不打断显示）。
          if (cached === null) setStatus('error')
          else { try { console.error('[ctfpilot] tools revalidate failed:', _err) } catch (_c) { /* console 不可用 */ } }
        } finally {
          panelInFlight.delete('tools-status')
        }
      }

      useEffect(() => { void loadStatus() }, [])

      // 一键安装按钮的未装计数（仅展示用；真正缺装集由 Host 侧 install-all
      // 实探决定，面板行有 30s TTL/本地缓存滞后，不作为注入依据）。
      const missingCount = rows ? rows.filter((r) => r && !r.ok && r.kind !== 'mcp').length : 0

      /** 装更卸动作共用链（单工具 act 与一键安装 installAll 同路）：
       * 新开 CTF-worker 对话 → 命令路由到新会话 → Host 注入该对话的 agent。 */
      async function runToolAction(cmdLine) {
        busyRef.current = true
        setBusy(true)
        setMsg('')
        setMsgKind('')
        try {
          // 2026-11-20 用户方案：点装/更/卸 → 自动开新对话（与「开始解题」
          // 同款三层链，固定 CTF-worker 工作区）→ /ctf-tools <verb> <id> 路由到
          // 新会话 → Host 把安装请求注入该对话的 agent 代跑包管理器。
          // agent 能看真实输出、自适应处理（winget GUI 卸载器、PATH 快照
          // 过期等坑），错误天然显示在对话里。跳转推迟到命令成功后——失败
          // 时 banner 停在本面板可读（solve 表单同款纪律）。
          // 2026-11-24 一键安装同链路：/ctf-tools install-all 由 Host 实探
          // 全部工具，把「所有未装工具」的安装请求合并成一条注入这**一个**
          // 新对话，agent 按清单串行装完——不再每个工具各开一个对话。
          let targetSid = ''
          let wsNote = ''
          try {
            const ensured = await ensureSolveSession(currentSessionId, t)
            targetSid = ensured.sid
            wsNote = ensured.note
          } catch (e2) {
            // ensureSolveSession 内部各层自带降级 note，正常不抛；真抛出（代码
            // 缺陷）时如实带出，命令退到当前会话路由（不静默吞错）。
            targetSid = ''
            wsNote = t('formWsFallback', { err: String((e2 && e2.message) || e2).slice(0, 120) })
          }
          const routeSid = isNonEmptyString(targetSid) ? targetSid : currentSessionId
          const out = await panelDataFetch(cmdLine, { method: 'POST', sid: routeSid })
          const oc = remoteOutcome(out)
          if (oc.ok) {
            setMsg(wsNote ? `${oc.text}\n（新建会话降级备注：${wsNote}）` : oc.text)
            setMsgKind(wsNote ? 'bad' : 'ok')
            // 成功才跳新对话页——agent 正在那里执行安装。
            if (isNonEmptyString(targetSid) && targetSid !== currentSessionId) {
              currentSessionId = targetSid
              notifySessionChanged()
              void openTargetSession(targetSid)
            }
          } else {
            setMsg(oc.text || t('toolsResult'))
            setMsgKind('bad')
          }
        } catch (_err) {
          setMsg(t('toolsResult'))
          setMsgKind('bad')
        } finally {
          busyRef.current = false
          setBusy(false)
          void loadStatus(true)
        }
      }

      async function act(row, verb) {
        if (busyRef.current || !row || !row.id) return
        await runToolAction(`/ctf-tools ${verb} ${row.id}`)
      }

      /** 一键安装（2026-11-24 用户令）：点一下 → 新开**一个**对话，把全部
       *  未安装工具的安装请求一次性交给该对话的 agent 按清单串行装完。 */
      async function installAll() {
        if (busyRef.current) return
        await runToolAction('/ctf-tools install-all')
      }

      if (status === 'loading' && rows === null) {
        return h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
      }
      return h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('toolsTitle')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('toolsSub')),
        h(
          'div',
          { className: 'ctfp-toolbar' }, [
          h(
            'button', {
              className: 'ctfp-btn',
              type: 'button',
              onClick: () => { void loadStatus(true) },
              disabled: busy,
            }, busy ? t('toolsBusy') : t('toolsStatus'),
          ),
          h(
            'button', {
              className: 'ctfp-btn',
              type: 'button',
              onClick: () => { void installAll() },
              disabled: busy,
              title: t('toolsInstallAllHint'),
            }, busy ? t('toolsBusy') : (missingCount > 0 ? t('toolsInstallAllCount', { n: missingCount }) : t('toolsInstallAll')),
          ),
          rows ? h('span', { className: 'ctfp-catcount' }, String(rows.length)) : null,
        ]),
        status === 'error'
          ? h('div', { className: 'ctfp-note', role: 'alert' }, [
              h('div', null, t('errorState')),
              h('button', {
                className: 'ctfp-btn', style: { marginLeft: '10px' }, type: 'button',
                onClick: () => { void loadStatus(true) },
              }, t('retry')),
            ])
          : null,
        (msg || msgKind)
          ? h('div', {
              className: msgKind === 'ok' ? 'ctfc-status ctfc-status--ok' : 'ctfc-status ctfc-status--bad',
              role: msgKind === 'ok' ? 'status' : 'alert',
            }, h('span', null, msg))
          : null,
        rows && rows.length === 0
          ? h('div', { className: 'ctfp-empty' }, t('toolsAllOk'))
          : null,
        rows && rows.length > 0
          ? h('div', { className: 'ctfp-tools' }, [
              h(
                'div',
                { className: 'ctfp-toolhead' }, [
                  h('span', { className: 'ctfp-toolname' }, t('toolColName')),
                  h('span', { className: 'ctfp-toolkind' }, t('toolColKind')),
                  h('span', { className: 'ctfp-toolstate' }, t('toolColState')),
                  h('span', { style: { marginLeft: 'auto' } }, t('toolColActs')),
                ],
              ),
              rows.map((row) =>
                h(
                  'div',
                  { key: `tool-${row.id || row.name || '?'}`, className: 'ctfp-toolrow' }, [
                    h('b', { className: 'ctfp-toolname' }, row.name || row.id || '?'),
                    h('span', { className: 'ctfp-badge' }, toolsKindLabel(t, row.kind)),
                    h(
                      'span', { className: 'ctfp-toolstate' + (row.ok ? ' ctfp-toolstate--ok' : ' ctfp-toolstate--off') }, [
                        h('span', { className: 'ctfp-dot ' + (row.ok ? 'ctfp-dot--ok' : 'ctfp-dot--off') }),
                        h('span', null, row.ok ? t('toolsStOk') : t('toolsStMissing')),
                      ]),
                    h(
                      'span',
                      { style: { marginLeft: 'auto', display: 'flex', gap: '6px' } }, [
                      h('button', {
                        className: 'ctfp-btn',
                        type: 'button',
                        disabled: busy || row.kind === 'mcp',
                        onClick: () => { void act(row, 'install') },
                        title: row.kind === 'mcp' ? t('kindMcp') : undefined,
                      }, t('toolsActInstall')),
                      h('button', {
                        className: 'ctfp-btn',
                        type: 'button',
                        disabled: busy || !row.ok,
                        onClick: () => { void act(row, 'update') },
                      }, t('toolsActUpdate')),
                      h('button', {
                        className: 'ctfp-btn',
                        type: 'button',
                        disabled: busy || !row.ok || row.kind === 'mcp',
                        onClick: () => { void act(row, 'uninstall') },
                      }, t('toolsActUninstall')),
                    ]),
                    row.detail
                      ? h('div', { className: 'ctfp-tooldetail' }, sanitizeToolDetail(row.detail))
                      : null,
                  ],
                ),
              ),
            ])
          : null,
      )
    }

    /* ============================================================
     * 插件装配（registry：slots + locale + remote + remote.commands）
     * ============================================================ */

    return {
      inject: ['slots', 'locale', 'uiWorkspace', 'sessions', 'workspaces'],
      apply(ctx) {
        const t = ctx.locale.bind(NS)
        // 捕获绑定翻译器：props.t 缺位（未注入场景）时组件仍能取到文案。
        boundTranslator = t

        ctx.effect(
          () => ctx.locale.register(NS, DICTS8),
          'dsh-ctfpilot: dictionaries',
        )

        ctx.effect(() => {
          // —— Feature 1：侧栏全局面板 ——
          const stopPanelIcon = ctx.slots.inject(
            'sidebar.panellist',
            () =>
              ctx.slots.register({
                name: 'sidebar.panellist',
                id: PANEL_ID,
                order: 90,
                label: () => t('sidePanelLabel'),
                locale: NS,
              }, CtfpilotPanelIcon),
          )
          const stopMainPanel = ctx.slots.inject(
            'main',
            () =>
              ctx.slots.register({
                name: 'main',
                key: PANEL_ID,
                locale: NS,
              }, CtfpilotPanel),
          )

          // —— Feature 2：回合尾部提交确认卡 ——
          const stopTurnTail = ctx.slots.inject(
            'conversation.chat.turnTail',
            () =>
              ctx.slots.register({
                name: 'conversation.chat.turnTail',
                id: TURN_TAIL_ID,
                locale: NS,
                inject: (sessionId) => {
                  // 顺带把当前会话 id 记到模块级持有者，供主面板调用引擎命令。
                  const sid = String(sessionId == null ? '' : sessionId)
                  currentSessionId = sid
                  notifySessionChanged()
                  return { sessionId: sid }
                },
              }, SubmitConfirmCard),
          )

          // 捕获 uiWorkspace / workspaces 服务（跳转会话 / 目录选择 /
          // 新建工作区注册）；缺失时「开始解题」表单降级为当前会话并显式提示。
          try {
            const uiws = ctx.uiWorkspace || (ctx.get ? ctx.get('uiWorkspace') : null)
            if (uiws) uiWorkspaceHolder = uiws
            const wssvc = ctx.workspaces || (ctx.get ? ctx.get('workspaces') : null)
            if (wssvc) workspacesHolder = wssvc
            const ss = ctx.sessions || (ctx.get ? ctx.get('sessions') : null)
            if (ss) sessionsHolder = ss
          } catch (_e) { /* 无此服务：表单走降级路径 */ }

          return () => {
            stopPanelIcon()
            stopMainPanel()
            stopTurnTail()
            currentSessionId = ''
            uiWorkspaceHolder = null
            workspacesHolder = null
            sessionsHolder = null
          }
        }, 'dsh-ctfpilot: registrations')
      },
    }
  },
})