/* ============================================================
 * ctf-solve-pipeline.js —— CTFPilot 端到端解题工作流脚本（解耦版）
 *
 * 在 DSH workflow 工具中把本文件内容作为 script 传入即可运行；
 * 每步由子代理执行真实引擎命令（ctfpilot 模型工具优先，
 * pwsh + python 兜底），脚本自身零 IO、零 Node API。
 *
 * 【解耦原则】本脚本不内嵌任何 CTF 知识：
 *   - 七类技能卡知识住在引擎的 skills/<cat>.md，由子代理按路径
 *     运行时读取——卡被 learn-writeup 持续增厚，本脚本永远自动受益；
 *   - 题型分类不靠目录名猜，来自引擎 solve stdout 的
 *     [triage] category= 行（引擎侧契约，带 engine 版本稳定）；
 *   - 脚本只携带控制流、stdout 行解析契约与人审/入库纪律；
 *   - 引擎确定性链够不着的打法（Web payload 构造、pwn 的 exp 源码、
 *     crypto 格攻击脚本等）由 ctf-manual 子代理按技能卡亲自执行，
 *     脚本只规定验证纪律，不规定技术内容。
 *
 * 入参 args：
 *   challenge  <题目目录>        必填
 *   name       <题名>          可选（缺省取目录末段）
 *   category   <cat>           可选：crypto|web|reverse|pwn|misc|osint|forensics；
 *                              仅作分类缺失时的兜底（引擎 triage 优先）
 *   signals    <关键词串>      可选，召回查询词
 *   mode       auto|fast|deep  默认 auto
 *   writeTrace bool            默认 true
 *   maxRetry   int             失败后最多重试次数，默认 2（上限 3）
 *   skillDir   <目录>          可选，技能卡目录；缺省取 $env:CTFPILOT_HOME\skills
 *   preloadCard bool           可选，在首次求解前先读卡（默认 false，快题零开销）
 *   practice   bool            可选，解题后把结果记入练兵台账（arena record，默认关）
 *   researchTopic <话题串>     可选，解题后把学习专题放入研究队列（默认关）
 *   rsiCycle   bool            可选，解题后触发一次 RSI 提案循环（默认关，重型低频）
 *
 * 返回：{ ok, solved, flag, challenge, category, dir, attempts, stages[],
 *         proposed, proposalNote, artifacts[], skillCard, notes[],
 *         tracks{practice?, research?, rsi?} }
 * ============================================================ */

const A = (args && typeof args === 'object') ? args : {}
const CH_DIR = String(A.challenge || '').trim()
const NAME = String(A.name || '').trim()
const MODE = A.mode === 'fast' || A.mode === 'deep' ? A.mode : 'auto'
const TRACE = A.writeTrace !== false
const MAX_RETRY = Math.min(3, Math.max(1, parseInt(A.maxRetry, 10) || 2))
const SKILL_DIR = String(A.skillDir || '').trim()
const PRELOAD_CARD = A.preloadCard === true
const OUT = []
const STAGES = []
let MANUAL_FLAG = '' // 手工补位产出的 Flag 候选（仅人审通过后入账）
const MANUAL_LOG = []

const CATS = ['crypto', 'web', 'reverse', 'pwn', 'misc', 'osint', 'forensics']

/* ---------------- 工具函数 ---------------- */

// 引擎 CLI stdout 契约（随引擎版本演进，不内嵌知识，只内嵌协议）
const FLAG_RE = /\b(?:flag|FLAG|ctf|CTF|DASCTF|NSSCTF|SUCTF|hgame)\s*\{[^}\s]{3,256}\}/i
const SOLVED_LINE = /\[result\]\s+SOLVED/
const TRIAGE_RE = /\[triage\]\s+category=([a-z]+)/i

function findFlag(text) {
  const m = String(text || '').match(FLAG_RE)
  return m ? m[0] : ''
}

/** 解析求解子代理回报 → { solved, flag, attempts, steps, triage, raw }。 */
function parseSolve(text) {
  const s = String(text || '')
  let obj = null
  const i = s.indexOf('[pipeline-solve]')
  if (i >= 0) {
    const frag = s.slice(i + '[pipeline-solve]'.length).replace(/^\s*[:=]?\s*/, '')
    const j = frag.indexOf('}')
    try {
      if (j >= 0) obj = JSON.parse(frag.slice(0, j + 1))
    } catch (_e) { obj = null }
  }
  const flag = (obj && obj.flag) ? findFlag(String(obj.flag)) : findFlag(s)
  const solved = (obj && obj.solved === true) || Boolean(flag) || SOLVED_LINE.test(s)
  const tm = s.match(TRIAGE_RE)
  return {
    solved: solved,
    flag: flag || '',
    attempts: (obj && parseInt(obj.attempts, 10)) || null,
    steps: (obj && parseInt(obj.steps, 10)) || null,
    triage: tm ? tm[1].toLowerCase() : '',
    raw: s,
  }
}

function basename(p) {
  const parts = String(p || '').split(/[\\\/]+/).filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

function normalizeCat(raw) {
  const c = String(raw || '').trim().toLowerCase()
  return CATS.indexOf(c) >= 0 ? c : ''
}

/** writeups 目录 = 题目目录去掉最后一段（同一赛事目录天然聚多题 writeup）。 */
function writeupsDirOf(dir) {
  const s = String(dir || '').replace(/[\\\/]+$/, '')
  const cut = s.lastIndexOf('\\') >= 0 ? s.lastIndexOf('\\') : s.lastIndexOf('/')
  return cut > 0 ? s.slice(0, cut) : s
}

function skillDirInstruction() {
  return SKILL_DIR
    ? '技能卡目录固定为：' + SKILL_DIR
    : '技能卡目录：$env:CTFPILOT_HOME 下的 skills 子目录（插件桥接环境已设好 CTFPILOT_HOME；若为空则找本机引擎安装目录下的 skills/）。'
}

/** 子代理统一引擎调用口径（桥接工具优先，pwsh 兜底）。 */
function engineBrief() {
  return [
    '引擎调用方式：',
    '① 首选模型工具 ctfpilot（插件桥接，直接传子命令，如 ctfpilot solve ...）；',
    '② 若无此工具，用 pwsh：$env:CTFPILOT_HOME 即含 ctfpilot.py 的引擎目录（可另设',
    '   CTFPILOT_PYTHON 指向 python），然后 python -X utf8 "$env:CTFPILOT_HOME\\ctfpilot.py" <子命令>；',
    '③ 两种都失败时回报写明 FAILED。',
  ].join('\n')
}

/* ---------------- 各阶段 ---------------- */

async function recallStage() {
  STAGES.push('recall')
  const q = SIGNALS_STR()
  const p = [
    '第一步：从引擎经验库召回与本题相关的旧案与教训，为求解提供先验。',
    '命令：recall' + (A.category ? ' --category ' + normalizeCat(A.category) : '') + q,
    engineBrief(),
    '回报：命中几张卡 + 每卡 technique / when_to_use / snippet（≤120 字），最多 5 条；',
    '零命中也如实回 NO-CARDS。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：召回经验…')
  const out = await agent(p, { label: 'ctf-recall', phase: '召回经验' })
  return String(out || '')
}

function SIGNALS_STR() {
  const sig = String(A.signals || '').trim()
  return sig ? ' --query "' + sig + '"' : ''
}

/** 读取技能卡（运行时读引擎文件，脚本零知识）。返回 { body, note }。 */
async function cardStage(cat) {
  STAGES.push('card:' + cat)
  const p = [
    '技能卡路由：把题型打到引擎技能卡上，然后回报卡片内容供后续步骤参考。',
    skillDirInstruction() + '；',
    '读取 <skillDir>\\' + cat + '.md；若不存在则读 <skillDir>\\misc.md（引擎回落口径）。',
    '同时列出 <skillDir>\\learned\\ 下属于该题型的最新版本技能卡文件名，最多 5 个。',
    '回报格式：<卡片全文原文（若超长取前 4000 字符）> 单独一段，空行，最后一行 LEARNED: n 个；',
    '文件都不存在时回 NO-CARD。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：按题型读取技能卡…')
  const out = await agent(p, { label: 'ctf-card', phase: '路由技能卡' })
  const t = String(out || '')
  if (/NO-CARD/i.test(t)) return { body: '', note: 'no card' }
  return { body: t.replace(/\n+LEARNED:.*$/is, '').trim(), note: t.trim().slice(-160) }
}

/** 后续提示词里的技能卡摘录（截断控量；原文在子代理手里没被丢弃，只是传输节流）。 */
function cardExcerpt(body) {
  return body ? '\n技能卡摘录（运行时读自引擎 skills/' + CAT_RESOLVED + '.md 的原文，非脚本内置）：\n' + body.slice(0, 900) : ''
}

async function solveStage(attempt, extra) {
  STAGES.push('solve#' + attempt)
  const tr = TRACE ? ' --write-trace' : ''
  const p = [
    '执行引擎求解，这是第 ' + attempt + ' 次尝试。',
    '命令：solve ' + CH_DIR + ' --mode ' + MODE + tr,
    'stdout 有 [triage] category=…、[result] SOLVED / NOT SOLVED 与 [meta] 行，原样保留在回报里。',
    '未解出也会落盘轨迹 _ctfpilot_trace.json。',
    extra ? '附加指引（第 ' + attempt + ' 次，依据失败复盘与技能卡）：' + extra : '',
    '最后一轮回复的末行必须严格是：[pipeline-solve] {"solved":bool,"flag":"字符串或空","attempts":int,"steps":int}。',
    engineBrief(),
  ].join('\n')
  log('[ctf-pipeline] 阶段：第 ' + attempt + ' 次求解…')
  const out = await agent(p, { label: 'ctf-solve#' + attempt, phase: '执行求解' })
  return parseSolve(out)
}

async function deepDiveStage(res, cardBody) {
  STAGES.push('deep-dive')
  const p = [
    '首次求解未中。现在做失败复盘（taxonomy v2 归因），供下一次尝试引用。',
    '读题目目录内 _ctfpilot_trace.json（每步 phase/detector/detail/success/error_class），',
    '必要时 ctfpilot recall --query 按信号词深挖相似旧案（其证据指针要下钻核对）。',
    '产出：疑似根因 ≤3 条（每条注明错误类别 + 证据），并给 ≤3 条可执行的下次打法建议。',
    '报错类别速查：semantic:no-progress（重复观察）/ semantic:repeat-strategy（策略循环）/',
    'semantic:premature-giveup（过早放弃）/ exec:timeout-sleep（挂机）/ exec:nonzero-exit 等。',
    cardExcerpt(cardBody),
    '注意：引擎深通道自身也会加载技能卡并按其打法规划；你在这里拿到的摘录用于放大这一先验。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：失败深挖…')
  const out = await agent(p, { label: 'ctf-deep-dive', phase: '失败深挖' })
  return String(out || '')
}

/** 引擎确定性链够不着时的手工补位：按技能卡亲自执行卡内打法。
 *  可行性自检在子代理内做；离线不可执行（web 发包、pwn 交互等）则回 NO-MANUAL，脚本不嗅探。
 *  手工产出的 Flag 也只进候选（setManualFlag），后续仍须过人审 gate 才入账。 */
async function manualStage(cardBody, diag) {
  STAGES.push('manual:' + CAT_RESOLVED)
  const p = [
    '引擎确定性链未解出本题。现在由你亲手按技能卡打法手工补位。',
    '技能卡摘录：',
    cardExcerpt(cardBody),
    '失败复盘参考：' + diag.slice(0, 400),
    engineBrief(),
    '第一步先做可行性自检：本题打法在此离线沙箱内是否可执行（只读题面/附件、沙箱白名单内工具）。',
    '若不可执行（需要联网发包、需要运行 ELF 交互、需要非白名单工具），立即回 NO-MANUAL，不要硬试。',
    '若可执行：按卡打法构造脚本或命令并执行——每步产物必须过 flag 正则校验再进入下一步；',
    '解题成功判据 = flag 正则命中且与题目文件内容无逐字相同来源。',
    '最后一行回报：[pipeline-manual] {"flag":"字符串或空","method":"一句话打法"}；不做则只回 NO-MANUAL。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：手工补位…')
  const out = await agent(p, { label: 'ctf-manual', phase: '手工补位' })
  const t = String(out || '')
  if (/NO-MANUAL/i.test(t)) {
    MANUAL_LOG.push('manual:' + CAT_RESOLVED + ' → no-feedback（离线不可执行，未动手）')
    return t
  }
  let flag = ''
  const i = t.indexOf('[pipeline-manual]')
  if (i >= 0) {
    const frag = t.slice(i + '[pipeline-manual]'.length).replace(/^\s*[:=]?\s*/, '')
    const j = frag.indexOf('}')
    try {
      const o = JSON.parse(frag.slice(0, j + 1))
      if (o && o.flag) flag = findFlag(String(o.flag))
    } catch (_e) { /* fallthrough */ }
  }
  if (!flag) flag = findFlag(t)
  if (flag) {
    MANUAL_FLAG = flag // 仅候选；仍须过人审 gate
    MANUAL_LOG.push('manual:' + CAT_RESOLVED + ' → flag-candidate ' + flag)
  } else {
    MANUAL_LOG.push('manual:' + CAT_RESOLVED + ' → no-flag（已执行但未出 flag）')
  }
  return t
}

async function gateStage(res) {
  STAGES.push('gate')
  const title = NAME || basename(CH_DIR)
  const p = [
    '人审环节：把找到的 Flag 交给人类确认（确认后才入账，防作弊回声）。',
    '若有 propose_submit 工具：flag=' + res.flag + '；title「' + title + '」；',
    'brief 一句话（' + CAT_RESOLVED + ' 解法汇总）；category 取 crypto|web|reverse|pwn|forensics|misc|osint 之一（缺失则用 CAT_RESOLVED）；',
    'challenge 用题名。若无该工具，输出 PROPOSAL: <flag> 占位。',
    '回报三种状态之一：已创建确认卡 / 用户拒绝 / 失败。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：人审提交…')
  const out = await agent(p, { label: 'ctf-propose', phase: '人工把关' })
  const t = String(out || '')
  return {
    created: /创建|确认卡|卡片|PROPOSAL/i.test(t),
    note: t.trim().slice(0, 200),
  }
}

async function writeupStage(res, recallText) {
  STAGES.push('writeup')
  const name = NAME || basename(CH_DIR)
  const p = [
    '复盘固化：把本次完整过程写成 writeup 供知识库消费（只固化事实，不写评价）。',
    '写文件：' + CH_DIR + '\\_ctfpilot_writeup.md（若不可写则放题目目录同级）。',
    '模板字段（与引擎 writeup_learner 对齐，中文皆可）：分类=' + CAT_RESOLVED + '、载体、识别信号、核心技术链、',
    '有效技术、陷阱类型、可迁移模式、变体预测、失败原因、适用限制、结果（solved/未解出）、最后一行 Flag: <flag 或空>。',
    '材料：本引擎 stdout 时间线（含 [triage] 分类）、_ctfpilot_trace.json 步骤、' + (recallText ? '召回旧案摘要（转述而非照抄）' : '无召回卡') + '。',
    '回报：写入的绝对路径，或 FAILED。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：撰写复盘 writeup…')
  const out = await agent(p, { label: 'ctf-writeup', phase: '复盘固化' })
  const t = String(out || '')
  const m = t.match(/[A-Za-z]:\\[^\s"']+\.md/i)
  if (m) OUT.push(m[0]) // 仅真实路径进 artifacts；FAILED 等杂讯丢弃
  return m ? m[0] : ''
}

async function learnStage(writeupPath, name) {
  if (!writeupPath) return { ok: false, note: 'no writeup path' } // 跳过不计入时间线
  STAGES.push('learn')
  const p = [
    '把复盘入库（版本化技能卡 + 账本行 + 教训，仅限本次落盘的文件）：',
    '命令：learn-writeup "' + writeupPath + '" --name "' + name + '"',
    engineBrief(),
    '回报：ingested / FAILED，以及卡片落点（若可见）。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：入库学习…')
  const out = await agent(p, { label: 'ctf-learn', phase: '复盘固化' })
  return { ok: /ingest|入库|ok|成功/i.test(String(out)), note: String(out || '').trim().slice(0, 160) }
}

async function rebuildStage(writeupsDir) {
  STAGES.push('rebuild')
  const p = [
    '重建案例知识库（幂等，可安全重跑）：',
    '命令：rebuild-knowledge "' + writeupsDir + '"',
    engineBrief(),
    '回报：cases 数、solved 数，或 FAILED。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：重建知识库…')
  const out = await agent(p, { label: 'ctf-rebuild', phase: '复盘固化' })
  return { ok: !/FAIL/i.test(String(out)), note: String(out || '').trim().slice(0, 160) }
}

/* ---------------- 可选轨道（默认全关，互不阻塞） ---------------- */

/** 解题后记入练兵台账（arena record）。未解出自动带 --fail；快速路径解出带 --fast。
 *  台账分离是引擎契约——这里只是把结果同步进演练账本，绝不污染生产台账。 */
async function practiceStage(res) {
  const name = NAME || basename(CH_DIR)
  const parts = ['record', '--name', name, '--category', CAT_RESOLVED || 'misc']
  if (!res.solved) parts.push('--fail')
  else if (A.trackFastPath === true) parts.push('--fast')
  STAGES.push('practice')
  const p = [
    '把本题结果记入 CTFPilot 练兵台账（arena record，与生产账本隔离）：',
    '命令：' + parts.join(' ') + '（' + (res.solved ? '解题成功记账' : '本次未解出，按 --fail 记账') + '）',
    engineBrief(),
    '回报：solves=/fails= 一行，或 FAILED。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：练兵记账…')
  const out = await agent(p, { label: 'ctf-practice', phase: '复盘固化' })
  const t = String(out || '')
  // 注意词边界：引擎成功行也含「fails=N」统计子串，小写匹配会全误伤。
  return { ok: !/\bFAIL(?:ED)?\b/i.test(t), note: t.trim().slice(0, 160) }
}

/** 把本次学习的专题放入研究队列（研究队列 add；草稿文件已写好，需人工补证据指针）。 */
async function researchStage() {
  const topic = String(A.researchTopic || '').trim()
  if (!topic) return { skipped: true }
  STAGES.push('research')
  const p = [
    '把专题加入引擎研究队列（研究队列 add）：',
    '命令：add "' + topic + '"（分类默认 misc，如需指定可补 --category）',
    engineBrief(),
    '回报：queued 一行，或 FAILED。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：研究队列入队…')
  const out = await agent(p, { label: 'ctf-research-add', phase: '复盘固化' })
  const t = String(out || '')
  return { ok: !/FAIL/i.test(t), note: t.trim().slice(0, 160) }
}

/** 触发一次 RSI 提案循环（引擎自分析轨迹生成人审提案；重型低频，仅显式开启）。 */
async function rsiCycleStage() {
  STAGES.push('rsi-cycle')
  const p = [
    '触发 RSI 提案循环（引擎按近期轨迹生成检测器/prompt 等人审提案）：',
    '命令：cycle',
    engineBrief(),
    '回报：proposal ids 与数量，或 FAILED。',
  ].join('\n')
  log('[ctf-pipeline] 阶段：RSI 提案循环…')
  const out = await agent(p, { label: 'ctf-rsi-cycle', phase: '复盘固化' })
  const t = String(out || '')
  return { ok: !/FAIL/i.test(t), note: t.trim().slice(0, 160) }
}

/* ---------------- 主流程 ---------------- */

let CAT_RESOLVED = '' // 分类一次解析，全程注入各子代理提示词

async function orchestrate() {
  const name = NAME || basename(CH_DIR)
  const wDir = writeupsDirOf(CH_DIR)
  const recallText = await recallStage()

  let res = null
  let cardBody = ''
  let cardNote = ''

  if (PRELOAD_CARD) {
    // 快路径预载：仅在显式要求时发生（默认快题零开销）
    const hint = normalizeCat(A.category) || 'misc'
    const c = await cardStage(hint)
    cardBody = c.body
    cardNote = c.note
  }

  res = await solveStage(1, cardExcerpt(cardBody))
  CAT_RESOLVED = normalizeCat(res.triage) || normalizeCat(A.category) || 'misc' // 分类以引擎 triage 为准

  let lastDiag = ''
  if (!res.solved) {
    for (let attempt = 2; attempt <= MAX_RETRY; attempt++) {
    if (!cardBody) { // 仅在失手后按需读卡（命中路径零开销）
      const c = await cardStage(CAT_RESOLVED)
      cardBody = c.body
      cardNote = c.note
    }
    lastDiag = await deepDiveStage(res, cardBody)
    // 引擎确定性链够不着时，手工按卡补位（Web payload / exp 源码 / 格脚本等）。
    // 可行性由该子代理自检（离线不可执行则回 NO-MANUAL），脚本不做文本嗅探。
    await manualStage(cardBody, lastDiag)
    if (MANUAL_FLAG) {
      // 手工 flag 候选已出：停止引擎重试，直接进人审 gate
      res = { solved: true, flag: MANUAL_FLAG, attempts: res.attempts, steps: res.steps, triage: CAT_RESOLVED, manual: true }
      break
    }
    res = await solveStage(attempt, (cardExcerpt(cardBody) + (lastDiag ? '\n上次失败复盘：' + lastDiag.slice(0, 500) : '')).trim())
    if (res.solved) break
    }
  }

  let gate = null
  if (res.solved) gate = await gateStage(res)

  const writeupPath = await writeupStage(res, recallText)
  const learn = await learnStage(writeupPath, name)
  const kb = await rebuildStage(wDir)

  const artifacts = writeupPath ? [writeupPath] : []
  const notes = [] // 轨道块（下方）也可能 push notes，须声明在前避免 TDZ
  if (recallText && recallText.toUpperCase() !== 'NO-CARDS') notes.push('召回卡已并入复盘上下文')
  if (gate) notes.push(gate.created ? '人审卡已创建' : '人审未通过：' + gate.note)
  if (!cardBody) notes.push('技能卡缺失，未注入打法（' + (CAT_RESOLVED === 'misc' ? 'misc 无卡属异常' : 'NO-CARD') + '）')
  if (!learn.ok) notes.push('入库失败：' + learn.note)
  if (!kb.ok) notes.push('知识库重建异常：' + kb.note)

  // —— 可选轨道（默认全关；各轨独立，失败只进 notes，绝不改写主返回的 ok/solved）——
  const tracks = {}
  if (A.practice === true) {
    try {
      tracks.practice = await practiceStage(res)
    } catch (e) {
      tracks.practice = { ok: false, note: 'crashed: ' + String((e && e.message) || e).slice(0, 140) }
    }
    // 失败显式上屏（崩溃或引擎回报 FAILED 均算），绝不静默
    if (tracks.practice && tracks.practice.ok === false) notes.push('练兵记账异常：' + tracks.practice.note)
  }
  if (A.researchTopic) {
    try {
      tracks.research = await researchStage()
      if (tracks.research.skipped) notes.push('研究专题未填写，跳过入队')
    } catch (e) {
      tracks.research = { ok: false, note: 'crashed: ' + String((e && e.message) || e).slice(0, 140) }
    }
    if (tracks.research && tracks.research.ok === false) notes.push('研究队列入队异常：' + tracks.research.note)
  }
  if (A.rsiCycle === true) {
    try {
      tracks.rsi = await rsiCycleStage()
    } catch (e) {
      tracks.rsi = { ok: false, note: 'crashed: ' + String((e && e.message) || e).slice(0, 140) }
    }
    if (tracks.rsi && tracks.rsi.ok === false) notes.push('RSI 提案循环异常：' + tracks.rsi.note)
  }

  return {
    ok: res.solved,
    solved: res.solved,
    flag: res.flag,
    challenge: name,
    category: CAT_RESOLVED || normalizeCat(res.triage) || 'misc',
    dir: CH_DIR,
    attempts: res.attempts,
    stages: STAGES.slice(),
    proposed: gate ? gate.created : false,
    proposalNote: gate ? gate.note : '',
    artifacts: artifacts,
    skillCard: cardBody ? 'loaded' : cardNote,
    manual: Boolean(res.manual),
    manualLog: MANUAL_LOG.slice(),
    notes: notes,
    tracks: tracks,
  }
}

/* ---------------- 入口 ---------------- */

phase('CTF 解题流水线')
log('[ctf-pipeline] 开始：' + (NAME || '(unnamed)') + ' @ ' + CH_DIR)

if (!CH_DIR) {
  return { ok: false, stage: 'validate', error: '缺少必填参数 challenge（题目目录）' }
}

try {
  return await orchestrate()
} catch (e) {
  return {
    ok: false,
    stage: 'runtime',
    error: String((e && e.message) || e),
    stages: STAGES.slice(),
  }
}