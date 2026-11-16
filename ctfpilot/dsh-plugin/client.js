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
      // —— 题目台账 ——
      'ledgerTitle': '题目台账',
      'searchPlaceholder': '搜索题目名 / 分类 / flag…',
      'noLedgerMatch': '没有匹配的题目',
      'colCat': '分类',
      'colMode': '模式',
      'unsolvedMark': '未解',
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
      'ledgerTitle': 'Challenge Ledger',
      'searchPlaceholder': 'Search challenge / category / flag…',
      'noLedgerMatch': 'No matching challenges',
      'colCat': 'Category',
      'colMode': 'Mode',
      'unsolvedMark': 'open',
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
    /** 上一次成功拉到看板数据所用的会话 id：同会话不重复拉取（防渲染风暴）。 */
    let lastDashboardSession = null
    /** 看板请求防重入：同一时刻只保留一个进行中的请求。 */
    let dashboardInFlight = false

    /* ============================================================
     * 小工具
     * ============================================================ */

    const isNonEmptyString = (v) => typeof v === 'string' && v.length > 0
    const num = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback)
    const str = (v, fallback) => (typeof v === 'string' ? v : fallback)
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

    /** slash 参数转义：命令行引号/控制字符替换掉，避免打断 Host 解析。 */
    function shellArg(v) {
      return String(v == null ? '' : v)
        .replace(/["\r\n\t]/g, ' ')
        .trim()
    }

    /** 构建 /ctf-record-win 的命令文本（引号包裹，三个参数全部转义）。 */
    function buildRecordWinCmd(name, category, flag) {
      return (
        '/ctf-record-win --name "' +
        shellArg(name) +
        '" --category "' +
        shellArg(category) +
        '" --flag "' +
        shellArg(flag) +
        '"'
      )
    }

    /** 解析插值翻译的兜底实现（正常路径由运行时的 t 提供，支持 {name}）。 */
    const identityT = (key) => key

    /* ============================================================
     * 样式（组件内 <style> 渲染：同一元素在各树中复用，
     * 内容幂等，重复渲染无副作用；颜色走 DSW 变量并带中性兜底）
     * ============================================================ */

    const STYLE_ID = 'dsh-ctfpilot-styles'
    const STYLE_TEXT = `
.ctfp-root{display:flex;flex-direction:column;gap:16px;padding:20px 24px 28px;color:var(--dsw-alias-text-primary,#1a1a1a);font-size:14px;line-height:1.55;font-family:inherit;min-width:0}
.ctfp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.ctfp-title{margin:0;font-size:19px;font-weight:650;line-height:1.3}
.ctfp-sub{margin:4px 0 0;font-size:12.5px;color:var(--dsw-alias-text-secondary,#667085)}
.ctfp-btn{font:inherit;font-size:13px;font-weight:500;cursor:pointer;color:var(--dsw-alias-text-primary,#1a1a1a);background:transparent;border:1px solid var(--dsw-alias-border-button,rgba(128,128,128,.45));border-radius:8px;padding:6px 14px;display:inline-flex;align-items:center;gap:6px;transition:background-color .15s ease,border-color .15s ease,opacity .15s ease}
.ctfp-btn:hover:not(:disabled){background:var(--dsw-alias-border-interactive-hover,rgba(128,128,128,.14))}
.ctfp-btn:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#3b82f6);outline-offset:2px}
.ctfp-btn:disabled{opacity:.55;cursor:default}
.ctfp-spin{display:inline-block;animation:ctfp-spin .9s linear infinite}
@keyframes ctfp-spin{to{transform:rotate(360deg)}}
.ctfp-note{padding:14px 16px;border:1px dashed var(--dsw-alias-border-base,rgba(128,128,128,.4));border-radius:10px;color:var(--dsw-alias-text-secondary,#667085);background:var(--dsw-alias-bg-layer-1,#f7f8fa)}
.ctfp-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.ctfp-kpi{border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.28));border-radius:10px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:12px 14px;display:flex;flex-direction:column;gap:2px;min-width:0}
.ctfp-kpi b{font-size:21px;font-weight:650;line-height:1.2}
.ctfp-kpi span{font-size:12px;color:var(--dsw-alias-text-secondary,#667085);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-section{border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.28));border-radius:10px;background:var(--dsw-alias-bg-layer-2,#eef1f6);padding:12px 14px;min-width:0}
.ctfp-section h2{margin:0 0 10px;font-size:13.5px;font-weight:600}
.ctfp-empty{padding:6px 2px;color:var(--dsw-alias-text-secondary,#667085);font-size:13px}
.ctfp-cats{display:flex;flex-direction:column;gap:8px}
.ctfp-catrow{display:grid;grid-template-columns:minmax(72px,120px) 1fr auto;align-items:center;gap:10px;font-size:13px}
.ctfp-catname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctfp-bar{height:8px;border-radius:999px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);overflow:hidden;min-width:70px}
.ctfp-barfill{height:100%;border-radius:999px;background:var(--dsw-alias-brand-primary,#3b82f6);min-width:0}
.ctfp-catcount{font-variant-numeric:tabular-nums;color:var(--dsw-alias-text-secondary,#667085);white-space:nowrap}
.ctfp-search{width:100%;font:inherit;font-size:13px;color:var(--dsw-alias-text-primary,#1a1a1a);background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-button,rgba(128,128,128,.45));border-radius:8px;padding:7px 10px;margin-bottom:10px;box-sizing:border-box}
.ctfp-search:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#3b82f6);outline-offset:1px}
.ctfp-ledger{display:flex;flex-direction:column;gap:6px;max-height:420px;overflow:auto;padding-right:2px}
.ctfp-row{border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.22));border-radius:8px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:8px 10px;display:flex;flex-direction:column;gap:5px}
.ctfp-row--solved{background:var(--dsw-alias-state-success-bg,rgba(34,197,94,.10));border-color:var(--dsw-alias-state-success,#22c55e)}
.ctfp-rowtop{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfp-rownm{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.ctfp-badge{font-size:11.5px;font-weight:500;border-radius:999px;padding:1px 8px;border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.35));color:var(--dsw-alias-text-secondary,#667085);white-space:nowrap}
.ctfp-badge--mode{border-color:var(--dsw-alias-border-interactive-hover,rgba(128,128,128,.5));color:var(--dsw-alias-brand-primary,#3b82f6)}
.ctfp-dot{width:8px;height:8px;border-radius:50%;background:var(--dsw-alias-text-secondary,#98a2b3);flex:none}
.ctfp-dot--ok{background:var(--dsw-alias-state-success,#22c55e)}
.ctfp-rowfoot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfp-flag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.3));border-radius:6px;padding:1px 7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;direction:ltr}
.ctfp-time{font-size:11.5px;color:var(--dsw-alias-text-secondary,#667085);margin-left:auto;white-space:nowrap}
.ctfp-dets{display:flex;flex-direction:column;gap:6px;font-size:12.5px}
.ctfp-detrow{display:grid;grid-template-columns:minmax(96px,1.6fr) 66px 56px 56px 72px;gap:8px;align-items:center;padding:5px 2px;border-bottom:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.18))}
.ctfp-detrow:last-child{border-bottom:none}
.ctfp-dethead{color:var(--dsw-alias-text-secondary,#667085);font-weight:600}
.ctfp-lessons{display:flex;flex-direction:column;gap:6px;max-height:320px;overflow:auto;padding-right:2px}
.ctfp-lesson{border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.22));border-radius:8px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);padding:7px 10px;font-size:12.5px}
.ctfp-lesson b{font-weight:600}
.ctfp-lesson-dim{color:var(--dsw-alias-text-secondary,#667085);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@media (max-width:480px){.ctfp-root{padding:14px}.ctfp-head{flex-direction:column}.ctfp-kpi b{font-size:18px}.ctfp-catrow{grid-template-columns:84px 1fr auto}.ctfp-detrow{grid-template-columns:1fr 52px 48px 54px 60px;font-size:11.5px}}
/* —— 提交确认卡 —— */
.ctfc-wrap{max-width:560px;width:100%;margin:0 auto;padding:0 4px}
.ctfc-card{border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.35));border-radius:12px;background:var(--dsw-alias-bg-layer-1,#f7f8fa);box-shadow:var(--dsw-shadow-card,0 1px 2px rgba(0,0,0,.06),0 4px 14px rgba(0,0,0,.05));padding:14px 16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.ctfc-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.ctfc-title{font-weight:650;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.ctfc-chip{font-size:11.5px;color:var(--dsw-alias-text-secondary,#667085);border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.35));border-radius:999px;padding:1px 9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px}
.ctfc-hint{font-size:12.5px;color:var(--dsw-alias-text-secondary,#667085);margin:0}
.ctfc-flag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;background:var(--dsw-alias-bg-base,#fff);border:1px solid var(--dsw-alias-border-base,rgba(128,128,128,.3));border-radius:6px;padding:2px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr}
.ctfc-meta{font-size:12.5px;color:var(--dsw-alias-text-secondary,#667085);margin:0}
.ctfc-footer{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfc-btn{font:inherit;font-size:13px;cursor:pointer;border-radius:8px;padding:6px 16px;transition:background-color .15s ease,border-color .15s ease,color .15s ease,opacity .15s ease}
.ctfc-btn--primary{border:1px solid transparent;background:var(--dsw-alias-brand-primary,#3b82f6);color:#fff;font-weight:500}
.ctfc-btn--primary:hover:not(:disabled){filter:brightness(.95)}
.ctfc-btn--ghost{border:1px solid var(--dsw-alias-border-button,rgba(128,128,128,.45));background:transparent;color:var(--dsw-alias-text-primary,#1a1a1a)}
.ctfc-btn--ghost:hover:not(:disabled){background:var(--dsw-alias-border-interactive-hover,rgba(128,128,128,.14))}
.ctfc-btn:disabled{opacity:.55;cursor:default}
.ctfc-status{font-size:12.5px;border-radius:8px;padding:6px 10px;margin:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctfc-status__detail{opacity:.85;font-size:12px}
.ctfc-status--ok{background:var(--dsw-alias-state-success-bg,rgba(34,197,94,.12));color:var(--dsw-alias-state-success,#15803d)}
.ctfc-status--bad{background:rgba(220,38,38,.10);color:#dc2626}
.ctfc-status--wait{color:var(--dsw-alias-text-secondary,#667085)}
.ctfc-status b{font-weight:650}
@media (max-width:390px){.ctfc-chip{max-width:140px}.ctfc-flag{font-size:12px}}
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
      const accent = '#3b82f6'
      const accentSoft = '#93c5fd'
      return h(
        'svg',
        { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': 'true' },
        // 罗盘外圈
        h('circle', { cx: 12, cy: 12, r: 8.6, stroke: accent, 'stroke-width': 2 }),
        // 雷达扇形扫描（浅蓝）
        h('path', { d: 'M12 12L16.45 7.55A6.3 6.3 0 0 1 16.45 16.45Z', fill: accentSoft }),
        // 扫描回波亮点
        h('circle', { cx: 14.8, cy: 10.4, r: 1, fill: accent }),
        h('circle', { cx: 10.2, cy: 14.8, r: 1.25, fill: accent }),
        // 旗杆 + 三角旗（终端窗口意象）
        h('rect', { x: 14.8, y: 5.2, width: 1.5, height: 11, rx: 0.75, fill: accent }),
        h('path', { d: 'M16.35 6.6L19.6 8.15L16.35 9.7Z', fill: accent }),
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
      return { stats, bycat, challs, lessons, detectors, modes }
    }

    /* ============================================================
     * Feature 1 —— 侧栏全局面板（CtfpilotPanel）
     * ============================================================ */

    function KpiCard(props) {
      return h(
        'div',
        { className: 'ctfp-kpi' },
        h('b', null, `${props.value}${props.unit}`),
        h('span', null, props.label),
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

    function LedgerRow(props) {
      const ch = props.ch
      const t = props.t
      return h(
        'div',
        { className: ch.solved ? 'ctfp-row ctfp-row--solved' : 'ctfp-row' },
        h(
          'div',
          { className: 'ctfp-rowtop' },
          h('span', { className: ch.solved ? 'ctfp-dot ctfp-dot--ok' : 'ctfp-dot', 'aria-label': ch.solved ? t('solved') : t('unsolvedMark') }),
          h('span', { className: 'ctfp-rownm', title: ch.name }, ch.name),
          ch.cat ? h('span', { className: 'ctfp-badge', title: t('colCat') }, ch.cat) : null,
          ch.mode ? h('span', { className: 'ctfp-badge ctfp-badge--mode', title: t('colMode') }, ch.mode) : null,
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
     * 看板页：KPI / 分类进度 / 题目台账 / 探测器命中 / 经验卡，
     * 显式 loading / error / empty / 需要会话 状态面。
     */
    function CtfpilotPanel(props) {
      const t = typeof props.t === 'function' ? props.t : boundTranslator || identityT
      const [blob, setBlob] = useState(null)
      const [status, setStatus] = useState('loading') // loading | error | ok | empty | noSession
      const [query, setQuery] = useState('')

      // 看板缓存按会话记忆：同会话不重复拉取（防回合尾部渲染风暴）；
      // 「刷新」按钮清掉该记忆后强制重取。
      const loadPanel = useCallback(async () => {
        const sid = currentSessionId
        if (!sid) {
          setBlob(null)
          setStatus('noSession')
          return
        }
        if (sid === lastDashboardSession || dashboardInFlight) return
        dashboardInFlight = true
        try {
          if (!remoteCommands || typeof remoteCommands.execute !== 'function') {
            throw new Error('remote commands unavailable')
          }
          const out = await remoteCommands.execute(sid, '/ctf-dashboard', [])
          // 会话已切换：本次结果作废，交给新会话的那次刷新。
          if (currentSessionId !== sid) return
          const text = resultTextOf(out)
          if (text === undefined) throw new Error('dashboard call failed')
          const normalized = normalizeBlob(JSON.parse(text))
          setBlob(normalized)
          setStatus(
            normalized.challs.length === 0 &&
              normalized.lessons.length === 0 &&
              normalized.detectors.length === 0 &&
              normalized.bycat.length === 0
              ? 'empty'
              : 'ok',
          )
          lastDashboardSession = sid
        } catch (_err) {
          if (currentSessionId !== sid) return
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

      const sessionReady = Boolean(currentSessionId)

      const filtered = useMemo(() => {
        if (!blob) return []
        const q = query.trim().toLowerCase()
        if (!q) return blob.challs
        return blob.challs.filter((c) =>
          [c.name, c.cat, c.mode, c.flag].some((v) => v.toLowerCase().includes(q)),
        )
      }, [blob, query])

      const kpis = blob
        ? [
            { label: t('totalChalls'), value: blob.stats.total, unit: t('unitChalls') },
            { label: t('solved'), value: blob.stats.solved, unit: t('unitChalls') },
            { label: t('rate'), value: fmtPercent(blob.stats.rate), unit: '' },
            { label: t('lessons'), value: blob.stats.lessons, unit: t('unitChalls') },
          ]
        : []

      const refreshButton = h(
        'button',
        {
          className: 'ctfp-btn',
          onClick: () => {
            // 手动刷新：清掉按会话的记忆缓存后强制重取（无会话时不动作，按钮亦禁用）。
            if (!currentSessionId) return
            lastDashboardSession = null
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
            ? h('div', { className: 'ctfp-note', role: 'status' }, t('loadingState'))
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

          // 题目台账
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('ledgerTitle')),
            h('input', {
              className: 'ctfp-search',
              type: 'search',
              placeholder: t('searchPlaceholder'),
              value: query,
              onChange: (e) => setQuery(e.target.value),
              'aria-label': t('searchPlaceholder'),
            }),
            filtered.length === 0
              ? h('div', { className: 'ctfp-empty' }, t('noLedgerMatch'))
              : h(
                  'div',
                  { className: 'ctfp-ledger' },
                  filtered.slice(0, 50).map((ch) =>
                    h(LedgerRow, { key: `ledger-${ch.id || ch.name || 'x'}`, ch, t }),
                  ),
                ),
          ),

          // 探测器命中（按 hits desc → attempts desc，取前 12 行）
          h(
            'section',
            { className: 'ctfp-section' },
            h('h2', null, t('detectorsTitle')),
            (() => {
              const sorted = blob.detectors
                .slice()
                .sort((a, b) => b.hits - a.hits || b.attempts - a.attempts)
                .slice(0, 12)
              if (sorted.length === 0) {
                return h('div', { className: 'ctfp-empty' }, t('noDetectors'))
              }
              return h(
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
                sorted.map((det) => h(DetectorRow, { key: `det-${det.detector || det.phase || '?'}`, det, t })),
              )
            })(),
          ),

          // 经验卡（上限 30，可滚动）
          h(
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
     * 提交确认卡：读会话投影 'ctfpilot'，仅在存在本回合准入 proposal 时渲染。
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
          !sessionId ? h('span', { className: 'ctfc-status__detail', style: { color: 'var(--dsw-alias-text-secondary, #667085)' } }, t('cardNoSession')) : null,
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
            t('colCat'),
            ' ',
            '·',
            ' ',
            t('colMode'),
            ' ',
            ':',
            ' ',
            category,
            ' ',
            '·',
            ' ',
            p.challenge ? p.challenge : title,
          ),
          footer,
        ),
      )
    }

    /* ============================================================
     * 插件装配（registry：slots + locale + remote + remote.commands）
     * ============================================================ */

    return {
      inject: ['slots', 'locale', 'remote', 'remote.commands'],
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

          return () => {
            stopPanelIcon()
            stopMainPanel()
            stopTurnTail()
            remoteCommands = null
            currentSessionId = ''
          }
        }, 'dsh-ctfpilot: registrations')
      },
    }
  },
})