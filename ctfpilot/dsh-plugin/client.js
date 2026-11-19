/**
 * dsh-ctfpilot 浏览器半边 —— CTFPilot 解题看板（侧栏主面板）+ 提交确认卡（回合尾部）
 *
 * 数据流：
 *  - 主面板：CtfpilotPanel 挂载时调用模块级 loadPanel()，经注入的
 *    remoteCommands.execute(currentSessionId, '/ctf-dashboard', [])
 *    拉取引擎看板 JSON（KPI / 分类进度 / 题目台账 / 探测器命中 / 经验卡），
 *    「刷新」按钮手动重取；无定时器、无网络层直连。
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
      'panelTitle': 'CTFPilot 解题看板',
      'panelSub': '本地引擎记忆：题目、方法与成绩来自离线 SQLite 账本',
      'refresh': '刷新',
      'refreshing': '正在刷新…',
      // —— KPI ——
      'totalChalls': '总挑战',
      'solved': '已解',
      'rate': '通过率',
      'lessons': '经验教训',
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
      'tabOverview': '概览',
      'tabLedger': '题目台账',
      'tabDetectors': '探测器',
      'tabLessons': '经验卡',
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
      // —— 训练场（竞技场）——
      'tabArena': '训练场',
      'arenaTitle': '竞技演练',
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
      // —— 自进化：研究队列 + RSI 提案流（第六标签）——
      'tabSelfEvo': '自进化',
      'researchTitle': '研究队列',
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
      'panelNeedsSession': '打开任意会话后即可加载引擎数据',
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
      'formStart': '开始解题',
      'formStartBusy': '正在切换预设并注入题面…',
      'formStarted': '已完成：已按所选模式切换本会话的 agent 预设，题面已注入，模型已开始解题。可在「正在解题」标签查看实时进度。',
      'formNeedInput': '请至少填写「题面」或「附件路径」之一。',
      'formNoSession': '请先打开任意一个对话，再开始解题。',
      'formNoBridge': '当前客户端缺少会话工厂服务，无法自动开会话：请复制下方提示词，直接粘贴到对话框发送。',
      'formStartFail': '开启会话失败：检查客户端状态后重试，或直接复制下方提示词手动发送。',
      'formWsFallback': '新建工作区未成功，已降级为在当前对话继续（{err}）。',
      'formWsNoService': '客户端未提供会话服务（sessions），已降级为在当前对话继续。',
      'formWsReuse': '自动建目录未成，已用既有工作区「{name}」新建对话（原因：{err}）。',
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
      'solvedQNote': '终止当前对话轮，并在同一会话上写 writeup 总结归档。',
      'unsolvedQNote': '快解：把全部上下文迁移到新会话并转深度解析；深度解析：在原地继续深挖。',
      'unsolvedWhileRunning': '对话进行中，不能标记未解出——本轮结束后再点。',
      'solvedMarked': '已标记解出：活动轮已终止，并在同会话请求 writeup 归档。',
      'unsolvedMarkedQuick': '已标记未解出：正在迁移全部上下文并启动深度解析…',
      'unsolvedMarkedDeep': '已标记未解出：正在原地继续深挖…',
      'migratedDone': '深度解析会话已就绪，正在切换过去。',
      'forkFallbackNote': '（无法 fork 新会话，改在原会话原地以深度解析继续）',
      'jumpHint': '跳转到该对话',
      'solvingDelHint': '删除这条解题记录',
      'confirmAction': '确认执行',
      'tabSolve': '开始解题',
      'tabTools': '工具管理',
      'toolsTitle': 'CTF 工具管理',
      'toolsSub': '常用解题工具的健康快检与装/更/卸；条目常驻，卸载后只回到「安装」态。',
      'toolsStatus': '一键快检',
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
      'panelTitle': 'CTFPilot Solving Board',
      'panelSub': 'Local engine memory: challenges, methods, and results come from the offline SQLite ledger',
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
      'tabOverview': 'Overview',
      'tabLedger': 'Challenges',
      'tabDetectors': 'Detectors',
      'tabLessons': 'Lessons',
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
      // —— 训练场（竞技场）——
      'tabArena': 'Training',
      'arenaTitle': 'Arena drill ground',
      'arenaSub': 'A practice ledger kept separate from the production ledger',
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
      // —— Self-evolution: research queue + RSI proposal stream (6th tab) ——
      'tabSelfEvo': 'Self-Evolution',
      'researchTitle': 'Research Queue',
      'researchSub': 'Human-guided study topics: only enter the knowledge base once two evidence pointers exist and the draft passes validation',
      'researchEmpty': 'No research topics yet: ask the assistant to have the engine add one',
      'researchEvidence': 'evidence',
      'researchStQueued': 'queued',
      'researchStDrafting': 'drafting',
      'researchStReady': 'ready-to-ingest',
      'researchStIngested': 'ingested',
      'rsiTitle': 'RSI Proposal Stream',
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
      'panelNeedsSession': 'Open any conversation to load the engine data',
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
      'formStart': 'Start solving',
      'formStartBusy': 'Switching preset and injecting the challenge…',
      'formStarted': 'Done: the preset matched to the mode is active, the challenge text is injected, the model is solving — follow it on the Solving tab',
      'formNeedInput': 'Fill in the challenge text or at least one attachment path.',
      'formNoBridge': 'The client has no session factory; copy the challenge text below and paste it into the composer to send manually.',
      'formStartFail': 'Starting the session failed: retry, or copy the challenge text below and send manually.',
      'formWsFallback': 'New-workspace creation failed; continuing in the current conversation ({err}).',
      'formWsNoService': 'The client provides no session service (sessions); continuing in the current conversation.',
      'formWsReuse': 'Auto directory creation failed; created the conversation inside the existing workspace "{name}" (reason: {err}).',
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
      'solvedQNote': 'Stop the active round, then ask the same conversation to write up the solution.',
      'unsolvedQNote': 'Quick mode migrates all context to a fresh deep-analysis conversation; deep mode continues in place.',
      'unsolvedWhileRunning': 'Cannot mark not-solved while the round runs — click after the round ends.',
      'solvedMarked': 'Marked solved: round stopped, writeup requested on the same conversation.',
      'unsolvedMarkedQuick': 'Marked not-solved: migrating context to deep analysis…',
      'unsolvedMarkedDeep': 'Marked not-solved: continuing in place…',
      'migratedDone': 'Deep-analysis session ready; switching to it now.',
      'forkFallbackNote': '(fork failed: continuing deep analysis in place instead)',
      'jumpHint': 'Open this conversation',
      'solvingDelHint': 'Remove this solve record',
      'confirmAction': 'Confirm',
      'tabSolve': 'Start Solving',
      'tabTools': 'Tools',
      'toolsTitle': 'CTF Tools',
      'toolsSub': 'Health probe plus install/update/uninstall; rows persist, uninstalled ones return to Install state.',
      'toolsStatus': 'Probe now',
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

    /** ctx.remote.commands（apply 期间赋值；缺失时所有远程调用安全降级为错误态）。 */
    let remoteCommands = null
    /** 当前会话 id：由回合尾部席位注入捕获，主面板据此调用引擎命令。 */
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
    /** 看板数据按会话记忆（模块级、跨组件挂载存活）：同会话重开面板直接复用，
     *  不重复拉取——彻底消除「每次点进侧边栏都要重新加载」。 */
    let dashboardCache = null // { sessionId: string, data: object }
    /** 看板请求防重入：同一时刻只保留一个进行中的请求。 */
    let dashboardInFlight = false
    /** 作废看板缓存：提交确认卡记账成功、或手动「刷新」时调用。 */
    function invalidateDashboard() {
      dashboardCache = null
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
.ctfp-lesson{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:8px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:8px 10px;font-size:12.5px;display:flex;flex-direction:column;gap:3px}
.ctfp-lesson b{font-weight:600}
.ctfp-tagbox{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.ctfp-tags{display:inline-flex;gap:5px;flex-wrap:wrap}
.ctfp-tag{font-size:11px;color:var(--dsw-alias-label-secondary,#667085);border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35));border-radius:999px;padding:0 8px}
.ctfp-ledger{display:flex;flex-direction:column;gap:6px;max-height:440px;overflow:auto;padding-right:2px}
.ctfp-row--click{cursor:pointer}
.ctfp-row--click:hover{border-color:var(--dsw-alias-state-business-primary,#3b82f6);background:var(--dsw-alias-card-fill-hover,var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.07)))}
.ctfp-row{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:var(--ctfp-radius-sm);background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:8px 10px;display:flex;flex-direction:column;gap:5px;transition:background-color .15s ease,border-color .15s ease}
.ctfp-row--solved{background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 10%, transparent);border-color:var(--dsw-alias-state-success-primary,#22c55e);box-shadow:inset 3px 0 0 var(--dsw-alias-state-success-primary,#22c55e)}
.ctfp-row--solved{background:color-mix(in srgb, var(--dsw-alias-state-success-primary,#22c55e) 10%, transparent);border-color:var(--dsw-alias-state-success-primary,#22c55e)}
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
.ctfp-lesson{border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.22));border-radius:8px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:7px 10px;font-size:12.5px}
.ctfp-lesson b{font-weight:600}
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
.ctfc-status{font-size:12.5px;border-radius:8px;padding:6px 10px;margin:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
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
.ctfp-sflag{font-family:var(--ctfp-mono);font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.28));color:var(--dsw-alias-label-primary,#1a1a1a);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-boards{display:flex;flex-direction:column;gap:10px}
.ctfp-scard{border:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.25));border-radius:var(--ctfp-radius-lg);padding:10px 12px;display:flex;flex-direction:column;gap:8px;cursor:pointer;transition:background-color .15s ease,border-color .15s ease}
.ctfp-scard:hover{border-color:var(--dsw-alias-state-brand-primary,var(--dsw-alias-brand-primary,#3b82f6));background:var(--dsw-alias-card-fill-hover,var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.06)))}
.ctfp-scard__top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfp-scard__meta{font-size:12.5px;color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-scard__acts{display:flex;gap:8px;flex-wrap:wrap}
.ctfp-actions-note{font-size:12px;color:var(--dsw-alias-label-secondary,#667085)}
.ctfp-dot--run{width:9px;height:9px;border-radius:50%;background:var(--dsw-alias-state-warning-primary,#f59e0b);animation:ctfp-pulse 1.8s ease-in-out infinite}
@keyframes ctfp-pulse{0%,100%{box-shadow:0 0 0 3px color-mix(in srgb,var(--dsw-alias-state-warning-primary,#f59e0b) 22%,transparent)}50%{box-shadow:0 0 0 6px color-mix(in srgb,var(--dsw-alias-state-warning-primary,#f59e0b) 8%,transparent)}}
.ctfp-toolhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:6px 0 4px;font-size:11.5px;color:var(--dsw-alias-label-secondary,#667085);border-bottom:1px solid var(--dsw-alias-border-l3,rgba(128,128,128,.25))}
.ctfp-toolrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-bottom:1px dashed var(--dsw-alias-border-l3,rgba(128,128,128,.18));padding:8px 0;font-size:12.5px}
.ctfp-toolbtn{padding:2px 10px;font-size:12px}
.ctfp-toolrow .ctfp-toolname{min-width:128px}
.ctfp-toolrow>.ctfp-badge{flex:none}
.ctfp-toolrow>span.ctfp-toolstate{flex:none;display:inline-flex;align-items:center;gap:6px}
.ctfp-toolrow>.ctfp-tooldetail{flex-basis:100%;margin-top:2px;opacity:.85;word-break:break-all}
.ctfp-badge--off{opacity:.55}
@media (max-width:390px){.ctfp-formgrid{grid-template-columns:1fr}.ctfp-toolrow .ctfp-toolname{min-width:104px}.ctfp-sflag{max-width:170px}}
/* v3：状态卡整卡可点（同 dsh-context 卡片交互）；题面/flag 长文溢出收敛；历史删除键 */
.ctfp-scard{position:relative}
.ctfp-scard--done{cursor:default}
.ctfp-scard--done:hover{border-color:var(--dsw-alias-border-l3,rgba(128,128,128,.25))}
.ctfp-scard__meta{overflow-wrap:anywhere}
.ctfp-sflag{overflow-wrap:anywhere}
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
        const sid = routeSessionId()
        if (!sid || !remoteCommands || typeof remoteCommands.execute !== 'function') {
          if (alive) setStatus('error')
          return () => { alive = false }
        }
        ;(async () => {
          try {
            const cmd = isNonEmptyString(ch.id)
              ? `/ctf-challenge-detail --id ${ch.id}`
              : `/ctf-challenge-detail --name "${String(ch.name || '').replace(/"/g, '')}"`
            const out = await remoteCommands.execute(sid, cmd, [])
            if (!alive) return
            const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
            const result = value && value.result && typeof value.result === 'object' ? value.result : null
            if (result && result.kind === 'success' && result.text !== undefined) {
              const parsed = JSON.parse(result.text)
              const rec = normalizeRecord(parsed)
              setRecord(rec)
              setStatus('ok')
            } else {
              setStatus('error')
            }
          } catch (_err) {
            if (alive) setStatus('error')
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
     * 自进化（第六标签）：研究队列卡 + RSI 提案流卡。
     * 数据来自 /ctf-research list 与 /ctf-rsi list；人审决策走
     * /ctf-rsi approve|reject|adopt PID --note ...（采纳强制附带 eval-suite 提示）。
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

      async function loadCard(cmd, setRows, setErr) {
        const sid = routeSessionId()
        if (!sid || !remoteCommands || typeof remoteCommands.execute !== 'function') {
          setErr(t('errorState'))
          return []
        }
        try {
          const out = await remoteCommands.execute(sid, cmd, [])
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          // 非 success 视为拉取失败：该卡内联报错，绝不静默清空另一卡的数据。
          if (!result || result.kind !== 'success') throw new Error(String(result && result.text ? result.text : 'self-evolution call failed'))
          const text = result.text
          const norm = String(cmd).startsWith('/ctf-research') ? normalizeResearch(text) : normalizeRsi(text)
          setRows(norm)
          return norm
        } catch (err) {
          // 单源失败只亮该卡错误条，另一卡照常渲染（双源互不影响）。
          setErr(err && err.message ? err.message : t('errorState'))
          return []
        }
      }

      async function loadBoth() {
        if (!routeSessionId()) {
          setResearchErr(t('errorState'))
          setRsiErr(t('errorState'))
          setLoaded(true)
          return
        }
        // 不闪屏：刷新路径不清空已上屏数据（离线桩下每次渲染都会重跑 effect，
        // 若刷新前先置 loading，任意中间渲染都会把面板打回 loading 帧）。
        // 两卡独立拉取：Promise.all 下任一失败不阻断另一卡（各自 setErr 已兜住）。
        await Promise.all([
          loadCard('/ctf-research list', setResearchRows, setResearchErr),
          loadCard('/ctf-rsi list', setRsiRows, setRsiErr),
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
        const sid = routeSessionId()
        try {
          if (!sid || !remoteCommands || typeof remoteCommands.execute !== 'function') {
            throw new Error('transport')
          }
          // 显式 --pid 形式：位置词解析在桥接里历史上会把动词误当 PID（生产实锤
          // '[error] no such proposal: adopt'），显式 flag 无歧义且人眼可读；
          // shellArg 防注入/引号断裂。
          const out = await remoteCommands.execute(
            sid,
            `/ctf-rsi ${actionName} --pid ${shellArg(pid)} --note ${shellArg('from-panel')}`,
            [],
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
          void loadBoth(false) // 决策后自动刷新提案流（loaded 控制不闪屏）
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
          h('h2', null, t('researchTitle')),
          h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('researchSub')),
          researchErr
            ? h('div', { className: 'ctfp-note', role: 'alert' }, [
                h('div', null, researchErr),
                h('button', {
                  className: 'ctfp-btn',
                  style: { marginLeft: '10px' },
                  type: 'button',
                  onClick: () => { void loadBoth(false) },
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
                    await loadBoth(false)
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

      async function loadBoard(first) {
        const sid = currentSessionId
        if (!sid || !remoteCommands || typeof remoteCommands.execute !== 'function') {
          setStatus('error')
          return
        }
        if (first) setStatus('loading') // 刷新路径保留旧榜上屏，不闪 loading
        try {
          const out = await remoteCommands.execute(sid, '/ctf-arena stats', [])
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          // 非 success（如引擎退出码≠0）视为拉取失败，绝不静默当空榜。
          if (!result || result.kind !== 'success') throw new Error('arena call failed')
          const text = result.text
          const norm = normalizeArena(text)
          if (!norm.overall && norm.items.length === 0) {
            if (first) setStatus('empty') // 合法空榜：empty 态而非 error 态，表单仍可操作
            return
          }
          setBoard(norm)
          if (first) setStatus('ok')
        } catch (_err) {
          setStatus('error') // 失败显式上屏，绝不静默留在旧数据上
        }
      }

      useEffect(() => { void loadBoard(true) }, [])

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
        const sid = routeSessionId()
        try {
          if (!sid || !remoteCommands || typeof remoteCommands.execute !== 'function') {
            throw new Error('transport')
          }
          const parts = [`/ctf-arena record --name ${shellArg(name)} --category ${shellArg(category)}`]
          if (form.fail) parts.push('--fail')
          else if (form.fast) parts.push('--fast')
          const out = await remoteCommands.execute(sid, parts.join(' '), [])
          const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
          const result = value && value.result && typeof value.result === 'object' ? value.result : null
          if (result && result.kind === 'success') {
            setOkMsg(t('arenaRecordOk'))
            setForm({ name: '', category: '', fail: false, fast: false })
            void loadBoard(false) // 引擎记完自动刷新排行榜（保留旧榜不闪屏）
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

      const disabledBtn = busy || !routeSessionId()
      const formCard = h(
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('arenaTitle')),
        h('p', { className: 'ctfp-sub', style: { marginBottom: '8px' } }, t('arenaSub')),
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
        'section',
        { className: 'ctfp-section' },
        h('h2', null, t('tabArena')),
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
        formCard,
      )
    }

    /**
     * 看板页：KPI / 分类进度 / 题目台账（下拉预选 + 搜索 + 分页） / 探测器命中 / 经验卡，
     * 点击题目行进详情页；显式 loading / error / empty / 需要会话 状态面。
     */
    function CtfpilotPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [blob, setBlob] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok | empty | noSession
      const [query, setQuery] = useState('')
      /** 详情页目标题目；非 null 时整屏渲染该题详情页。 */
      const [detailCh, setDetailCh] = useState(null)
      /** 分类下拉预选值（'' = 全部分类）。 */
      const [catFilter, setCatFilter] = useState('')
      /** 是否在台账中展示「只有学习导入记录」的题（默认隐藏）。 */
      const [showLearned, setShowLearned] = useState(false)
      /** 功能标签（分页）：overview | ledger | detectors | lessons | arena。 */
      const [tab, setTab] = useState('overview')

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

      // 拉取引擎看板并写入模块级缓存；缓存命中或请求进行中时不再发起。
      const loadPanel = useCallback(async () => {
        const sid = routeSessionId()
        if (!sid) {
          setBlob(null)
          setStatus('noSession')
          return
        }
        if (dashboardCache !== null && dashboardCache.sessionId === sid) {
          // 同会话缓存命中：重挂零请求，直接上屏。
          present(dashboardCache.data)
          return
        }
        if (dashboardInFlight) return
        dashboardInFlight = true
        try {
          if (!remoteCommands || typeof remoteCommands.execute !== 'function') {
            throw new Error('remote commands unavailable')
          }
          const out = await remoteCommands.execute(sid, '/ctf-dashboard', [])
          // 会话已切换：本次结果作废，交给新会话的那次刷新。
          if (routeSessionId() !== sid) return
          const text = resultTextOf(out)
          if (text === undefined) throw new Error('dashboard call failed')
          const normalized = normalizeBlob(JSON.parse(text))
          dashboardCache = { sessionId: sid, data: normalized }
          present(normalized)
        } catch (_err) {
          if (routeSessionId() !== sid) return
          setBlob(null)
          setStatus('error')
        } finally {
          dashboardInFlight = false
        }
      }, [])

      useEffect(() => {
        void loadPanel()
      }, [loadPanel])

      // 会话 id 就绪（任意会话的回合尾部渲染过）后触发首次拉取；
      // 用订阅而非定时器：事件到达才触发，无轮询。
      const loadLatest = useRef(loadPanel)
      loadLatest.current = loadPanel
      useEffect(() => {
        return subscribeCurrentSession(() => {
          void loadLatest.current()
        })
      }, [])

      // sessions 列表快照就绪/变更也驱动一次拉取：零会话首帧（DSSH 刚启动、
      // 还没打开任何对话）时 routeSessionId 依赖该快照——快照从 pending 变
      // ready 那一刻必须有人叫醒 loadPanel，否则面板停在 noSession。
      // loadPanel 有防重入 + 同引用 present bail out，无渲染风暴风险。
      useEffect(() => {
        const s = sessionsHolder
        if (!s || !s.list || typeof s.list.subscribe !== 'function') return () => {}
        return s.list.subscribe(() => {
          void loadLatest.current()
        })
      }, [])

      const sessionReady = isNonEmptyString(routeSessionId())

      const filtered = useMemo(() => {
        if (!blob) return []
        const q = query.trim().toLowerCase()
        const cat = catFilter
        return blob.challs.filter((c) =>
          // kind='learn' 只有学 writeup 的导入记录，非真实解题：默认隐藏。
          (!showLearned && c.kind === 'learn' ? false :
            (!cat || c.cat === cat) &&
            (!q || [c.name, c.cat, c.mode, c.flag].some((v) => v.toLowerCase().includes(q)))),
        )
      }, [blob, query, catFilter, showLearned])
      const detSorted = useMemo(
        () =>
          blob === null || !Array.isArray(blob.detectors)
            ? []
            : blob.detectors
              .slice()
              .sort((a, b) => b.hits - a.hits || b.attempts - a.attempts)
              .slice(0, 12),
        [blob],
      )

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
        [blob],
      )

      const refreshButton = h(
        'button',
        {
          className: 'ctfp-btn',
          onClick: () => {
            // 手动刷新：作废模块级缓存后强制重取（路由池全空时不动作，按钮亦禁用）。
            if (!routeSessionId()) return
            invalidateDashboard()
            dashboardInFlight = false
            void loadPanel()
          },
          disabled: !sessionReady,
          'aria-label': t('refresh'),
        },
        [h('span', null, '↻'), h('span', null, t('refresh'))],
      )

      return h(
        'div',
        { className: 'ctfp-root' },
        Styles(),
        // 详情页优先：选中题目后整屏渲染该题（返回看板时清空恢复）。
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

        // 状态面：需要会话 / 加载中 / 失败 / 空态
        !sessionReady && status === 'noSession'
          ? h('div', { className: 'ctfp-note', role: 'status' }, t('panelNeedsSession'))
          : status === 'loading' && !blob
            ? h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
            : status === 'error'
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
              : status === 'empty'
                ? h('div', { className: 'ctfp-note', role: 'status' }, t('emptyState'))
                : null,

        blob && status === 'ok' && h(React.Fragment, null,
          // 功能标签栏：概览 / 题目台账 / 探测器 / 经验（四屏互斥）。
          h('div', { className: 'ctfp-tabs', role: 'tablist' }, [
            h('button', { key: 'tab-overview', role: 'tab', 'aria-selected': tab === 'overview', className: tab === 'overview' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('overview') }, t('tabOverview')),
            h('button', { key: 'tab-ledger', role: 'tab', 'aria-selected': tab === 'ledger', className: tab === 'ledger' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('ledger') }, t('tabLedger')),
            h('button', { key: 'tab-detectors', role: 'tab', 'aria-selected': tab === 'detectors', className: tab === 'detectors' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('detectors') }, t('tabDetectors')),
            h('button', { key: 'tab-lessons', role: 'tab', 'aria-selected': tab === 'lessons', className: tab === 'lessons' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('lessons') }, t('tabLessons')),
            h('button', { key: 'tab-arena', role: 'tab', 'aria-selected': tab === 'arena', className: tab === 'arena' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('arena') }, t('tabArena')),
            h('button', { key: 'tab-selfEvo', role: 'tab', 'aria-selected': tab === 'selfEvo', className: tab === 'selfEvo' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('selfEvo') }, t('tabSelfEvo')),
            h('button', { key: 'tab-solving', role: 'tab', 'aria-selected': tab === 'solving', className: tab === 'solving' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('solving') }, t('solvingTab')),
            h('button', { key: 'tab-solve', role: 'tab', 'aria-selected': tab === 'solve', className: tab === 'solve' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('solve') }, t('tabSolve')),
            h('button', { key: 'tab-tools', role: 'tab', 'aria-selected': tab === 'tools', className: tab === 'tools' ? 'ctfp-tab ctfp-tab--on' : 'ctfp-tab', type: 'button', onClick: () => setTab('tools') }, t('tabTools')),
          ]),

          // —— 概览（KPI + 分类进度 + 解题模式 + 失败画像）——
          tab === 'overview' && h(React.Fragment, null,
            // KPI 条
            h('div', { className: 'ctfp-kpis' }, kpis.map((k, i) => h(KpiCard, { key: `kpi-${i}`, label: k.label, value: k.value, unit: k.unit }))),

          // 分类进度
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('catsTitle')),
            blob.bycat.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noCategory'))
              : h(
                  'div',
                  { className: 'ctfp-cats' },
                  blob.bycat.map((row) => h(CategoryRow, { key: `cat-${row.cat || '?'}`, row, t })),
                ),
          ),
          // 解题模式（引擎 run mode 分布：auto / fast / deep / manual …）
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('modesTitle')),
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
          // 失败画像（taxonomy v2：语义类 = 可行动信号，琥珀色；按次数排序）
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null,
              t('errorsTitle'),
              h('span', { className: 'ctfp-catcount' }, t('errClasses', { n: blob.errors.length }))),
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
          ),

          // —— 题目台账屏：分类下拉预选 + 搜索；点击行进入详情页 ——
          tab === 'ledger' && h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('ledgerTitle')),
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
              h('span', { className: 'ctfp-count' }, t('ledgerCount', { n: filtered.length })),
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
                      onClick: () => { setDetailCh(ch) },
                    }),
                  ),
                ),
          ),

          // —— 探测器屏：按命中排序（前 12 行）——
          tab === 'detectors' && h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('detectorsTitle')),
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

          // —— 经验屏：最近 30 张经验卡 ——
          tab === 'lessons' && h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('lessonsTitle')),
            blob.lessons.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noLessons'))
              : h(
                  'div',
                  { className: 'ctfp-lessons' },
                  blob.lessons.slice(0, 30).map((les) =>
                    h(LessonItem, { key: `les-${les.cat}:${les.tech}:${les.time}`, les, t }),
                  ),
                ),
          ),

          // —— 训练场：竞技演练排行榜（只读）+ 一键「记练」（自加载组件）——
          tab === 'arena' ? h(ArenaPanel, { t }) : null,
          // —— 自进化：研究队列卡 + RSI 提案流卡（自加载组件）——
          tab === 'selfEvo' ? h(SelfEvoPanel, { t }) : null,
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
          if (!remoteCommands || typeof remoteCommands.execute !== 'function') {
            throw new Error('transport')
          }
          const out = await remoteCommands.execute(
            sessionId,
            buildRecordWinCmd(p.challenge || title, category, flag),
            [],
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

    /** 作废看板缓存：提交确认卡记账成功、或手动「刷新」时调用。 */
    function invalidateDashboard() {
      dashboardCache = null
    }

    /* ============================================================
     * 三界面共享状态与工具
     * ============================================================ */

    /** 工作区服务持有者（跳转会话 / 目录选择 / fork 迁移；缺服务时安全降级）。 */
    let uiWorkspaceHolder = null
    /** Workspace Controller 服务持有者（新建工作区注册：create({path})；缺服务时安全降级）。 */
    let workspacesHolder = null
    /** Session Controller 客户端服务持有者：命令路由的会话 id 池（引擎数据与会话无关）。 */
    let sessionsHolder = null
    /** 最近成功路由的会话 id（localStorage 跨启动兑底：零会话首帧也能拉数据）。 */
    const ROUTE_SID_KEY = 'ctfpilot.routeSid.v1'
    /** 「正在解题」投影键（与 Host 侧 ctfsolve 单元对应）。 */
    const PROJECTION_SOLVE_KEY = 'ctfsolve'
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
        cat: '', attaches: [], body: '',
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

    function markSolveRunning(sid, running) {
      const all = readSolveBoard()
      if (!all[sid]) return
      all[sid].running = running
      writeSolveBoard(all)
      notifySolveBoardChanged()
    }

    /** 把投影扫出的 flag 并入条目：只追加、重复计数（上限 99），绝不覆盖旧值。 */
    function applySolveFlagsFromSnap(sid, snapSession) {
      if (!snapSession || !Array.isArray(snapSession.flags) || snapSession.flags.length === 0) return
      const all = readSolveBoard()
      const cur = all[sid]
      if (!cur) return
      const flags = Array.isArray(cur.flags) ? cur.flags.slice() : []
      let dirty = false
      for (const f of snapSession.flags) {
        const i = flags.findIndex((x) => x && x.f === f)
        if (i < 0) { flags.push({ f, n: 1 }); dirty = true }
        else if ((flags[i].n || 1) < 99) { flags[i].n = (flags[i].n || 1) + 1; dirty = true }
      }
      if (!dirty) return
      cur.flags = flags
      all[sid] = cur
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

    /** 远端结果解包：{ok, value:{result:{kind,text}}}。 */
    function remoteOutcome(out) {
      const value = out && out.ok === true && out.value && typeof out.value === 'object' ? out.value : null
      const result = value && value.result && typeof value.result === 'object' ? value.result : null
      if (!result) return { ok: false, text: '' }
      return { ok: result.kind === 'success', text: isNonEmptyString(result.text) ? result.text : '' }
    }

    /* ============================================================
     * 命令路由会话 id 池：引擎数据是全局 SQLite 账本，与会话无关，但命令
     * 桥需要「任一存在的会话」作为路由键（执行时 resolveAgent）。打开面板
     * 不应要求先开对话：当前会话 → sessions 列表快照 → 最近成功缓存。
     * ============================================================ */

    /** 取一个可用于命令路由的会话 id：池全空返回 ''。 */
    function routeSessionId() {
      if (isNonEmptyString(currentSessionId)) return currentSessionId
      try {
        const s = sessionsHolder
        const snap = s && s.list && typeof s.list.getSnapshot === 'function' ? s.list.getSnapshot() : null
        const cur = snap && snap.current
        if (isNonEmptyString(String(cur || ''))) return String(cur)
        const ids = snap && Array.isArray(snap.ids) ? snap.ids : []
        // ids 按时间序（旧→新），取最新一个：命令落到最近活动的会话上。
        if (ids.length > 0) return String(ids[ids.length - 1])
      } catch (_e) { /* 快照不可读：落最近缓存 */ }
      try {
        const cached = localStorage.getItem(ROUTE_SID_KEY)
        if (isNonEmptyString(cached)) return cached
      } catch (_e2) { /* localStorage 不可用（隐私模式） */ }
      return ''
    }

    /** 记住最近一次成功路由的会话 id，供下一次零会话首帧兑底。 */
    function rememberRouteSid(sid) {
      if (!isNonEmptyString(sid)) return
      try { localStorage.setItem(ROUTE_SID_KEY, sid) } catch (_e) { /* 写失败静默 */ }
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

    async function executeRemote(command, sid) {
      if (!remoteCommands || typeof remoteCommands.execute !== 'function') throw new Error('transport')
      // 路由 id 池化：显式传入的 sid 优先；引擎类命令（dashboard/stats 等）
      // 与会话无关，任一存在会话即可路由；池全空才报 no-route-session。
      const target = isNonEmptyString(sid) ? sid : routeSessionId()
      if (!isNonEmptyString(target)) throw new Error('no-route-session')
      rememberRouteSid(target)
      return remoteCommands.execute(target, command, [])
    }

    async function openTargetSession(sid) {
      // 聚焦新会话优先走 sessions.open（官方选择当前会话的标准方法，同步 select）；
      // uiWorkspace.openSession 作兜底；都不在时静默（新会话已在列表，用户可手点）。
      const ss = sessionsHolder
      if (ss && typeof ss.open === 'function') {
        try { ss.open(sid); return } catch (_e) { /* 落 openSession 兜底 */ }
      }
      const ws = uiWorkspaceHolder
      if (ws && typeof ws.openSession === 'function') {
        try { await ws.openSession(sid) } catch (_e2) { /* 跳转失败不影响状态流转 */ }
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
      const set = []
      for (const p of [...(prev || []), ...(incoming || [])]) {
        const v = String(p == null ? '' : p).trim()
        if (v && set.indexOf(v) < 0) set.push(v)
      }
      return set
    }

    function buildStartCommand(mode, cat, attaches, body) {
      const parts = ['/ctf-solve-start', mode === 'deep' ? 'deep' : 'quick']
      const c = String(cat || '').trim()
      if (c) parts.push('--category', shellArg(c))
      for (const a of attaches) if (isNonEmptyString(a)) parts.push('--attach', shellArg(a))
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

      async function actSolved() {
        if (!sid || busy) return
        busyRefGuard_setTrue(setBusy)
        try {
          const name = String(entry.body || '').slice(0, 60) || String(entry.cat || '')
          const out = await executeRemote(
            '/ctf-writeup' + (name ? ' --name ' + shellArg(name) : ''), sid)
          const oc = remoteOutcome(out)
          if (!oc.ok) throw new Error(oc.text || t('submitFailed'))
          upsertSolveEntry(sid, { status: 'solved', running: false })
          setFeedback(t('solvedMarked'))
          setFeedbackKind('ok')
        } catch (_err) {
          setFeedback(_err && _err.message ? _err.message : t('submitFailed'))
          setFeedbackKind('bad')
        } finally { busyRefGuard_setFalse(setBusy) }
      }

      async function actUnsolved() {
        if (!sid || busy) return
        busyRefGuard_setTrue(setBusy)
        try {
          if (entry.running) throw new Error(t('unsolvedWhileRunning'))
          const wasQuick = entry.mode !== 'deep'
          const migrated = upsertSolveEntry(sid, { status: 'unsolved', running: false })
          if (wasQuick) {
            setFeedback(t('unsolvedMarkedQuick'))
            setFeedbackKind('ok')
            const ws = uiWorkspaceHolder
            if (ws && typeof ws.forkSession === 'function') {
              try {
                const newSid = String(await ws.forkSession(sid) || '')
                if (newSid) {
                  upsertSolveEntry(newSid, {
                    mode: 'deep', cat: migrated.cat, attaches: migrated.attaches,
                    body: migrated.body, startedAt: Date.now(),
                    running: true, flags: [], status: 'running',
                  })
                  void openTargetSession(newSid)
                  void executeRemote(
                    buildStartCommand('deep', migrated.cat, migrated.attaches, migrated.body), newSid)
                  setFeedback(t('migratedDone'))
                  return
                }
              } catch (_e) { /* 落到下方兜底文案 */ }
            }
            setFeedback(t('forkFallbackNote'))
            setFeedbackKind('bad')
            void executeRemote(
              buildStartCommand('deep', migrated.cat, migrated.attaches, migrated.body), sid)
          } else {
            upsertSolveEntry(sid, { status: 'unsolved', running: true })
            setFeedback(t('unsolvedMarkedDeep'))
            setFeedbackKind('ok')
            void executeRemote(
              buildStartCommand('deep', migrated.cat, migrated.attaches, migrated.body), sid)
          }
        } catch (_err) {
          setFeedback(_err && _err.message ? _err.message : t('submitFailed'))
          setFeedbackKind('bad')
        } finally { busyRefGuard_setFalse(setBusy) }
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
          h('span', { className: 'ctfp-dot ' + (entry.running ? 'ctfp-dot--run' : 'ctfp-dot--off') },
            entry.running ? t('solvingRunning') : t('solvingIdle')),
          entry.status === 'solved' ? h('span', { className: 'ctfp-badge ctfp-badge--ok' }, t('solved')) : null,
          entry.status === 'unsolved' ? h('span', { className: 'ctfp-badge ctfp-badge--off' }, t('btnUnsolved')) : null,
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
        onJump ? h(
          'button',
          {
            className: 'ctfp-btn ctfp-toolbtn',
            type: 'button',
            'aria-label': t('jumpHint'),
            onClick: () => { void onJump(sid) },
          },
          t('jumpHint'),
        ) : null,
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
              onClick: () => { setFeedback(''); setConfirming('solved') },
              disabled: busy || entry.status === 'solved',
            },
            t('btnSolved'),
          ),
          h(
            'button',
            {
              className: 'ctfp-btn',
              type: 'button',
              onClick: () => { setFeedback(''); setConfirming('unsolved') },
              disabled: busy || entry.running || entry.status === 'unsolved',
              title: entry.running ? t('unsolvedWhileRunning') : undefined,
            },
            t('btnUnsolved'),
          ),
          confirming
            ? h('div', { className: 'ctfp-tooldetail', role: 'alertdialog' }, [
                h('p', null, confirming === 'solved' ? t('solvedQNote') : t('unsolvedQNote')),
                h('div', { style: { marginTop: '8px', display: 'flex', gap: '8px' } }, [
                  h('button', {
                    className: 'ctfp-btn',
                    type: 'button',
                    disabled: busy,
                    onClick: () => { void (confirming === 'solved' ? actSolved() : actUnsolved()) },
                  }, t('confirmAction')),
                  h('button', {
                    className: 'ctfp-btn',
                    type: 'button',
                    disabled: busy,
                    onClick: () => { setConfirming('') },
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

    async function ensureSolveSession(currentSid, t) {
      const wsvc = uiWorkspaceHolder
      const wssvc = workspacesHolder
      const ssvc = sessionsHolder
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
      async function reuseExistingWorkspace() {
        try {
          const snap = wssvc && wssvc.list && typeof wssvc.list.getSnapshot === 'function'
            ? wssvc.list.getSnapshot() : null
          const items = snap && Array.isArray(snap.items) ? snap.items : []
          const first = items.find((it) => it && isNonEmptyString(it.workspaceId)) || null
          if (!first) return { sid: '', name: '' }
          const wid = String(first.workspaceId)
          const nsid = await createSessionIn(wid)
          const name = String(first.title || first.path || wid)
          return isNonEmptyString(nsid) ? { sid: nsid, name } : { sid: '', name: '' }
        } catch (_e) {
          return { sid: '', name: '' }
        }
      }

      // —— 层1：Host home 下建 ctf-<stamp> 新目录，注册后在其中开新对话 ——
      if (wsvc && wssvc && typeof wsvc.listDirectory === 'function' &&
          typeof wsvc.createDirectory === 'function' && typeof wssvc.create === 'function') {
        let wid = ''
        try {
          const listing = await wsvc.listDirectory()
          const home = listing && isNonEmptyString(listing.home) ? listing.home : ''
          const now = new Date()
          const stamp = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}-${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`
          const dir = home ? await wsvc.createDirectory(home, `ctf-${stamp}`) : ''
          const wv = dir ? await wssvc.create({ path: dir }) : null
          wid = wv && isNonEmptyString(wv.workspaceId) ? String(wv.workspaceId) : ''
        } catch (e) {
          const reused = await reuseExistingWorkspace()
          if (isNonEmptyString(reused.sid)) {
            return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: `workspace: ${brief(e)}` }) }
          }
          return { sid: currentSid, note: t('formWsFallback', { err: `workspace: ${brief(e)}` }) }
        }
        if (isNonEmptyString(wid)) {
          try {
            const nsid = await createSessionIn(wid)
            if (isNonEmptyString(nsid)) return { sid: nsid, note: '' }
          } catch (e) {
            const reused = await reuseExistingWorkspace()
            if (isNonEmptyString(reused.sid)) {
              return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: `session: ${brief(e)}` }) }
            }
            return { sid: currentSid, note: t('formWsFallback', { err: `session: ${brief(e)}` }) }
          }
        }
      }

      // —— 层2：既有工作区里新建对话 ——
      const reused = await reuseExistingWorkspace()
      if (isNonEmptyString(reused.sid)) {
        return { sid: reused.sid, note: t('formWsReuse', { name: reused.name, err: 'no new workspace registered' }) }
      }
      // —— 层3：当前会话 ——
      return { sid: currentSid, note: t('formWsFallback', { err: 'no workspace available' }) }
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
      const [mode, setMode] = useState('quick')
      const [newSession, setNewSession] = useState(true)
      const [attaches, setAttaches] = useState([])
      const [pickMsg, setPickMsg] = useState('')
      const [startMsg, setStartMsg] = useState('')
      const [startKind, setStartKind] = useState('')
      const [busy, setBusy] = useState(false)
      const [entry, setEntry] = useState(null)

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

      async function doStart() {
        if (busy) return
        const b = String(body || '').trim()
        if (!b && attaches.length === 0) {
          setStartMsg(t('formNeedInput'))
          setStartKind('bad')
          return
        }
        setBusy(true)
        setStartMsg('')
        try {
          const cmd = buildStartCommand(mode, cat, attaches, body)
          let targetSid = sid
          let wsFallbackNote = ''
          if (newSession) {
            // 「新建工作区 + 对话」：三层降级链，每一层失败都显式提示（绝不静默）：
            //   层1 全新工作区：listDirectory() 取 Host home → createDirectory 建
            //      新目录 → workspaces.create({path}) 注册 → sessions.create
            //      ({workspaceId}) 开新对话（官方 New Session 同款 API）；
            //   层2 既有工作区新对话：目录/注册/建会话失败时，用 workspaces.list
            //      快照里的既有工作区 sessions.create——新对话（核心诉求）仍达成；
            //   层3 当前会话：全部失败才降级，附原始错误。
            // 服务一律走模块级持有者；agent 预设由 Host /ctf-solve-start 在新
            // 会话（生而为 blank）上切换，前端不介入。
            const ensured = await ensureSolveSession(sid, t)
            targetSid = ensured.sid
            wsFallbackNote = ensured.note
            if (isNonEmptyString(targetSid) && targetSid !== sid) {
              currentSessionId = targetSid
              notifySessionChanged()
              void openTargetSession(targetSid)
            }
          }
          upsertSolveEntry(targetSid, {
            mode, cat, attaches, body,
            startedAt: Date.now(), running: true, flags: [], status: 'running',
          })
          const out = await executeRemote(cmd, targetSid)
          const oc = remoteOutcome(out)
          if (oc.ok) {
            // 新建工作区降级但注入仍成功：成功文案后附降级原因（红色警示），不静默。
            setStartMsg(wsFallbackNote ? `${t('formStarted')} ${wsFallbackNote}` : t('formStarted'))
            setStartKind(wsFallbackNote ? 'bad' : 'ok')
          } else {
            setStartMsg(oc.text || t('formStartFail'))
            setStartKind('bad')
          }
        } catch (_err) {
          // 区分两类硬失败：路由池全空（零会话且建不了新会话）→ 指引先开对话；
          // 其余（无桥接/传输）→ 复制题面手动发送的指引。绝不静默吞掉。
          const noRoute = _err && _err.message === 'no-route-session'
          setStartMsg(noRoute ? t('formNoSession') : t('formNoBridge'))
          setStartKind('bad')
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
      // 失败静默，下一拍重试；当前会话 id 走模块级持有者（闭包读活值）。
      useEffect(() => {
        async function poll() {
          const rsid = routeSessionId()
          if (!isNonEmptyString(rsid)) return
          try {
            const out = await executeRemote('/ctf-solving-state', rsid)
            const oc = remoteOutcome(out)
            if (!oc.ok || !oc.text) return
            const snap = asSolveSnap(JSON.parse(oc.text))
            if (!snap) return
            const all = readSolveBoard()
            let touched = false
            for (const sid of Object.keys(snap.sessions || {})) {
              const cur = all[sid]
              const ss = snap.sessions[sid]
              if (cur && ss && !!cur.running !== !!ss.running) {
                cur.running = ss.running
                touched = true
              }
            }
            if (touched) { writeSolveBoard(all); notifySolveBoardChanged() }
            for (const sid of Object.keys(snap.sessions || {})) {
              applySolveFlagsFromSnap(sid, snap.sessions[sid])
            }
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
      const [status, setStatus] = useState('loading') // loading | error | ok | empty | noSession
      const [msg, setMsg] = useState('')
      const [msgKind, setMsgKind] = useState('')
      const [busy, setBusy] = useState(false)
      const busyRef = useRef(false)

      async function loadStatus(first) {
        const sid = routeSessionId()
        if (!sid) { setStatus('noSession'); return }
        if (first) setStatus('loading')
        try {
          const out = await executeRemote('/ctf-tools status', currentSessionId)
          const oc = remoteOutcome(out)
          if (!oc.ok) throw new Error(oc.text || t('errorState'))
          const idx = String(oc.text).indexOf('[')
          if (idx < 0) throw new Error('malformed status output')
          const parsed = JSON.parse(String(oc.text).slice(idx))
          if (!Array.isArray(parsed) || parsed.length === 0) { setRows([]); setStatus('empty'); return }
          setRows(parsed)
          setStatus('ok')
        } catch (_err) {
          setStatus('error')
        }
      }

      useEffect(() => { void loadStatus(true) }, [])

      async function act(row, verb) {
        if (busyRef.current || !row || !row.id) return
        busyRef.current = true
        setBusy(true)
        setMsg('')
        setMsgKind('')
        try {
          const out = await executeRemote(`/ctf-tools ${verb} ${row.id}`, routeSessionId())
          const oc = remoteOutcome(out)
          if (oc.ok) {
            setMsg(t('toolsStatusDone'))
            setMsgKind('ok')
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
          void loadStatus(false)
        }
      }

      if (status === 'loading' && rows === null) {
        return h('div', { className: 'ctfp-note ctfp-note--loading', role: 'status' }, t('loadingState'))
      }
      if (status === 'noSession') {
        return h('div', { className: 'ctfp-note', role: 'status' }, t('panelNeedsSession'))
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
              onClick: () => { void loadStatus(false) },
              disabled: busy,
            }, busy ? t('toolsBusy') : t('toolsStatus'),
          ),
          rows ? h('span', { className: 'ctfp-catcount' }, t('toolsStatusDone')) : null,
        ]),
        status === 'error'
          ? h('div', { className: 'ctfp-note', role: 'alert' }, [
              h('div', null, t('errorState')),
              h('button', {
                className: 'ctfp-btn', style: { marginLeft: '10px' }, type: 'button',
                onClick: () => { void loadStatus(false) },
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
      inject: ['slots', 'locale', 'remote', 'remote.commands', 'uiWorkspace', 'sessions', 'workspaces'],
      apply(ctx) {
        const t = ctx.locale.bind(NS)
        // 捕获绑定翻译器：props.t 缺位（未注入场景）时组件仍能取到文案。
        boundTranslator = t

        ctx.effect(
          () => ctx.locale.register(NS, DICTS8),
          'dsh-ctfpilot: dictionaries',
        )

        ctx.effect(() => {
          // 宿主命令桥：组件经模块级持有者访问（不进 React 依赖图）。
          remoteCommands = ctx.remote && ctx.remote.commands ? ctx.remote.commands : null

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

          // 捕获 uiWorkspace / workspaces 服务（跳转会话 / 目录选择 / fork 迁移 /
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
            remoteCommands = null
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