// dsh-ctfpilot: bridge the CTFPilot Python engine as a model-facing DSH tool.
// Engine: https://github.com/juuege/ctf-agent (ctfpilot/CTFPilot-v0.1), pure stdlib, offline.
import { spawn, spawnSync } from 'node:child_process'
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, statSync, readdirSync } from 'node:fs'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import z from '@deepseek-ai/schemastery'

//#region error log（2026-11-19 用户令：删光吞错降级，异常必须落盘）
// 所有命令错误的双通道之一：error 上屏之外，同时追加到
// <engineDir>/ctfpilot-errors.log（不可写时退 os.tmpdir()）。查看：/ctf-logs。
const ERROR_LOG_NAME = 'ctfpilot-errors.log'

/** 追加一条异常日志；返回实际写入路径（失败返回 ''）。 */
function writeErrorLog(dir, entry) {
  const line = `[${new Date().toISOString()}] [${entry.source}] via=${entry.via} :: ${entry.message}\n${entry.stack ? entry.stack.slice(0, 800) + '\n' : ''}`
  const candidates = [dir && path.join(dir, ERROR_LOG_NAME), path.join(os.tmpdir(), ERROR_LOG_NAME)].filter(Boolean)
  for (const file of candidates) {
    try {
      appendFileSync(file, line, 'utf8')
      return file
    } catch { /* 换下一个候选路径 */ }
  }
  return ''
}

/** 统一异常记录：命令包装层与 solve-start 等主动报错处都走这里。 */
function logError(dir, source, err, via, extra = '') {
  const e = err instanceof Error ? err : new Error(String((err && err.message) || err || '(no error object)'))
  try { writeErrorLog(dir, { source, via: via || 'unknown', message: (extra ? extra + ' | ' : '') + e.message, stack: String(e.stack || '') }) } catch { /* 日志自身失败不掩盖原错误 */ }
}
//#endregion

//#region config
const Config = z.object({
  python: z.string().default(''),
  engineDir: z.string().default(''),
  timeoutSeconds: z.number().step(1).min(30).max(3600).default(300),
  maxOutputChars: z.number().step(1).min(1000).max(100000).default(8000),
})

const name = 'ctfpilot'
// 'agentPresets' 必须声明（2026-11-19 18:42 ctfpilot-errors.log 实锤：cordis 服务
// 属性访问要求 inject 先行，未声明时直接 throw
// "cannot get property \"agentPresets\" without inject"——宿主组合必含该服务，
// 官方 @deepseek-ai/dsh-agent-preset 声明行同款硬依赖写法）。
// webServer / connection / agents 三项不进静态声明：它们只服务 HTTP 数据网关
// （2026-11-20），非 Web profile 缺服务时经运行时 ctx.inject 自然不挂载，
// 命令通道照常可用——官方 client-connection 的同款动态注入写法。
const inject = ['tools', 'sessionProjections', 'commands', 'agentPresets']
//#endregion

//#region command mapping
const COMMANDS = ['solve', 'recall', 'learn-writeup', 'rebuild-knowledge', 'eval-retrieval', 'stats', 'selftest', 'fixations', 'dashboard', 'bench-pull', 'eval-suite', 'rsi', 'arena', 'research']

/** 会写引擎账本（trajectories.sqlite）的子命令：成功返回后主动作废看板快照。
 *  原则宁可多作废不失新鲜——作废只是下次读取重查（约 1.8s），不作废则是陈旧数据。 */
const WRITE_COMMANDS = new Set(['solve', 'learn-writeup', 'rebuild-knowledge', 'eval-retrieval', 'rsi', 'arena', 'bench-pull'])

//#region 签名缓存层（通用）
/* 通用签名缓存：失效由数据源签名驱动（官方 session-projection-cache 模式）——
 * 数据源文件不变 → 缓存永远有效；任何写入改文件 mtime+size → 签名变 → 立即失效。
 * Host 常驻：缓存跨 UI 重启存活。写命令成功后 cacheDel 主动作废是双保险
 * （消除同毫秒 mtime 粒度碰撞的极小概率陈旧窗口）。
 * 条目软上限 300：超出删最旧（Map 迭代序=插入序），防 challenge-detail 按题
 * 缓存无限增长。 */
const SIGNED_CACHE_MAX = 300
const signedCache = new Map() // key → { sig, value }

/** 单文件签名：mtimeMs+size。文件不存在返回 '∅'（合法状态——首次写入会
 *  创建文件，签名随之变化自动失效；不再因缺文件全盘禁缓存）。 */
function fileSig(p) {
  try { const st = statSync(p); return `${st.mtimeMs}|${st.size}` } catch { return '\u2205' }
}

/** 目录递归签名（限深 4 层、限 200 文件）：任一文件增删改即变。
 *  排除 __pycache__ 与点开头项；空目录返回 'empty'；不可读返回 null。 */
function dirSigDeep(dir) {
  const parts = []
  try {
    const walk = (d, depth) => {
      if (depth > 4 || parts.length > 200) return
      let entries
      try { entries = readdirSync(d, { withFileTypes: true }) } catch { return }
      for (const e of entries) {
        if (e.name === '__pycache__' || e.name.startsWith('.')) continue
        const p = path.join(d, e.name)
        if (e.isDirectory()) walk(p, depth + 1)
        // fileSig 不可读时返回 '∅'（合法状态），不再做恒真判断。
        else parts.push(`${e.name}:${fileSig(p)}`)
      }
    }
    walk(dir, 0)
  } catch { return null }
  if (parts.length === 0) return 'empty'
  return parts.join('|')
}

/** 读缓存：签名相同返回缓存值，否则 undefined（MISS）；sig=null 一律 MISS。 */
function cacheGet(key, sig) {
  if (sig === null || sig === undefined) return undefined
  const cur = signedCache.get(key)
  return cur && cur.sig === sig ? cur.value : undefined
}

/** 写缓存（软上限：新 key 且超上限时删最旧条目）。 */
function cacheSet(key, sig, value) {
  if (sig === null || sig === undefined) return
  if (!signedCache.has(key) && signedCache.size >= SIGNED_CACHE_MAX) {
    const oldest = signedCache.keys().next().value
    if (oldest !== undefined) signedCache.delete(oldest)
  }
  signedCache.set(key, { sig, value })
}

/** 主动作废（写命令成功后调用；签名失效是主通道，这是双保险）。 */
function cacheDel(key) { signedCache.delete(key) }

/** 账本签名（dashboard / challenge-detail / stats 共用数据源）。 */
function ledgerSig(dir) {
  const db = process.env.CTFPILOT_DB
    ? path.resolve(dir, process.env.CTFPILOT_DB)
    : path.join(dir, 'memory', 'trajectories.sqlite')
  return fileSig(db)
}

/** arena 数据源签名：registry.json 的内容签名（剥离 updated 字段——引擎 stats
 *  每次跑都回写 registry 刷 updated，mtime 签名永不命中；剥离后内容不变即命中，
 *  真实变更（register 加题 / record 改档位）才失效）+ attempts.jsonl 的 mtime 签名
 *  （stats 不写它，只在 record 时追加）。读/解析失败退 '∅'（文件损坏修好后
 *  签名自然变化失效）。 */
function arenaSig(dir) {
  const d = path.join(dir, 'skills', 'arena')
  let regSig = '\u2205'
  try {
    const reg = JSON.parse(readFileSync(path.join(d, 'registry.json'), 'utf8'))
    delete reg.updated
    regSig = JSON.stringify(reg)
  } catch { /* registry 缺失/损坏：'∅' 签名，真跑成功仍可缓存 */ }
  return `${regSig}|${fileSig(path.join(d, 'attempts.jsonl'))}`
}

/** rsi 数据源签名：skills/rsi 下 ledger/proposals + version-chain（rsi.py 实锤
 *  LEDGER_DIR='skills/rsi'，链在 skills/arena/state/version-chain.jsonl）。 */
function rsiSig(dir) {
  const d = path.join(dir, 'skills', 'rsi')
  return [
    fileSig(path.join(d, 'ledger.json')),
    fileSig(path.join(d, 'proposals.jsonl')),
    fileSig(path.join(dir, 'skills', 'arena', 'state', 'version-chain.jsonl')),
  ].join('|')
}

/** research 数据源签名：queue.jsonl + topics/ 递归。 */
function researchSig(dir) {
  const d = path.join(dir, 'skills', 'research')
  return `${fileSig(path.join(d, 'queue.jsonl'))}|${dirSigDeep(path.join(d, 'topics'))}`
}
//#endregion

function buildArgs(args) {
  switch (args.command) {
    case 'solve': {
      const argv = ['solve', args.challenge]
      if (args.mode) argv.push('--mode', args.mode)
      if (args.write_trace) argv.push('--write-trace')
      if (args.flag_regex) argv.push('--flag-regex', args.flag_regex)
      return argv
    }
    case 'recall': {
      const argv = ['recall']
      if (args.category) argv.push('--category', args.category)
      if (args.query) argv.push('--query', args.query)
      return argv
    }
    case 'learn-writeup':
      return ['learn-writeup', args.writeup, ...(args.name ? ['--name', args.name] : [])]
    case 'rebuild-knowledge':
      return ['rebuild-knowledge', args.writeups_dir, ...(args.dry_run ? ['--dry-run'] : [])]
    case 'eval-retrieval':
      return ['eval-retrieval', ...(args.writeups_dir ? [args.writeups_dir] : []), ...(args.save_baseline ? ['--save-baseline'] : [])]
    case 'fixations':
      return ['fixations', ...(typeof args.min === 'number' ? ['--min', String(args.min)] : [])]
    case 'dashboard':
      return ['dashboard', ...(args.out_html ? ['--out', args.out_html] : [])]
    case 'bench-pull': {
      const argv = ['bench-pull']
      if (args.source) argv.push('--source', args.source)
      if (args.pool) argv.push('--pool', args.pool)
      if (args.dest) argv.push('--dest', args.dest)
      if (args.tag) argv.push('--tag', args.tag)
      return argv
    }
    case 'eval-suite':
      return ['eval-suite', ...(args.save_baseline ? ['--save-baseline'] : [])]
    case 'rsi': {
      const argv = ['rsi', args.action || 'cycle']
      if (args.pid) argv.push(args.pid)
      if (args.status) argv.push('--status', args.status)
      if (args.note) argv.push('--note', args.note)
      if (typeof args.limit === 'number') argv.push('--limit', String(args.limit))
      return argv
    }
    case 'arena': {
      const argv = ['arena', args.action || 'stats']
      if (args.name) argv.push('--name', args.name)
      if (args.category) argv.push('--category', args.category)
      if (args.source) argv.push('--source', args.source)
      if (args.tier) argv.push('--tier', args.tier)
      if (args.dest) argv.push('--dest', args.dest)
      if (args.fail) argv.push('--fail')
      if (args.fast) argv.push('--fast')
      return argv
    }
    case 'research': {
      const argv = ['research', args.action || 'list']
      if (args.topic) argv.push(args.topic)
      if (args.category) argv.push('--category', args.category)
      if (args.source) argv.push('--source', args.source)
      if (args.pointer) argv.push('--pointer', args.pointer)
      if (args.kind && args.kind !== 'local-file') argv.push('--kind', args.kind)
      if (args.status) argv.push('--status', args.status)
      if (typeof args.limit === 'number') argv.push('--limit', String(args.limit))
      return argv
    }
    default:
      return [args.command]
  }
}
//#endregion

//#region engine resolution
const pluginDir = path.dirname(fileURLToPath(import.meta.url))

// Default engine location: sibling of this plugin's parent (repo layout
// ctfpilot/{CTFPilot-v0.1, dsh-plugin}) plus common local spots.
const ENGINE_CANDIDATES = [
  () => process.env.CTFPILOT_HOME,
  () => path.resolve(pluginDir, '..', 'CTFPilot-v0.1'),  // published layout
  () => path.resolve(pluginDir, '..', '..', 'CTFPilot-v0.1'),  // repo workspace layout
]

/** 默认引擎路径缓存：Host 常驻，每次命令/网关请求都 stat 1-3 个候选路径
 *  是纯重复 IO；env 或插件位置变化时自动失效（key 对齐探测输入）。
 *  configured 分支不缓存——显式配置每次都要校验，便于用户改错后立即报错。 */
let engineResolveCache = { key: '', dir: '' }
function resolveEngine(configured) {
  if (configured) {
    const p = path.resolve(configured)
    if (!existsSync(path.join(p, 'ctfpilot.py'))) {
      throw new Error(`engineDir ${p} does not contain ctfpilot.py; clone juuege/ctf-agent or set config.engineDir`)
    }
    return p
  }
  const key = `${process.env.CTFPILOT_HOME || ''}|${pluginDir}`
  if (engineResolveCache.key === key && engineResolveCache.dir) return engineResolveCache.dir
  for (const candidate of ENGINE_CANDIDATES) {
    const p = candidate()
    if (p && existsSync(path.join(p, 'ctfpilot.py'))) {
      engineResolveCache = { key, dir: p }
      return p
    }
  }
  throw new Error('CTFPilot engine not found: set config.engineDir to the CTFPilot-v0.1 directory (contains ctfpilot.py)')
}

const PYTHON_CANDIDATES = () => {
  const list = []
  if (process.env.CTFPILOT_PYTHON) list.push(process.env.CTFPILOT_PYTHON)
  list.push('python3', 'python3.12', 'python3.11', 'python3.10', 'python')
  return list
}
//#endregion

//#region result parsing (engine stdout convention: "[result] SOLVED in Xs" / "FLAG: ...")
const RESULT_LINE = /^\[result\]\s+(SOLVED|NOT SOLVED)(?:\s+in\s+([0-9.]+)s)?/m
const FLAG_LINE = /\[result\]\s+FLAG:\s*(\S+)/

// ---------- structured timeline extraction ----------
// Engine log format:
//   [triage] scanning <dir>
//   [triage] category=<cat> confidence=<N.NN>
//      [<cat>] <evidence>
//   [fast] running offline detectors...
//   <detector>: <detail> -> flag{...}        (or just: <detail>)
//   [deep] entering hypothesis loop...
//   [deep] step N: <strategy>
//   <action> -> <err_class> | <head_text>
//   [deep] FLAG FOUND at step N via <strategy>

const TRIAGE_LINE = /\[triage\]\s+category=(\w+)\s+confidence=([\d.]+)/
// Machine-readable summary emitted by the engine after every solve:
//   [meta] {"category":"crypto","confidence":0.87,"evidence":[...],
//           "steps":[{"phase":"fast","name":"base64","detail":"...","success":false}, ...]}
const META_LINE = /^\[meta\]\s+(\{.*\})\s*$/m

/**
 * Parse steps from the engine's machine-readable [meta] line.
 * Falls back to None so the caller can degrade gracefully.
 */
function parseMetaSteps(metaJson) {
  try {
    const m = JSON.parse(metaJson)
    const steps = Array.isArray(m.steps) ? m.steps : []
    return {
      category: typeof m.category === 'string' ? m.category : '',
      confidence: typeof m.confidence === 'number' ? m.confidence : 0,
      evidence: Array.isArray(m.evidence) ? m.evidence.map(t => String(t)) : [],
      steps: steps.map(s => ({
        phase: String(s.phase || ''),
        name: String(s.name || ''),
        detail: String(s.detail || ''),
        success: !!s.success,
        step: 0,
        result: s.success ? 'success' : 'failed',
        error_class: '',
      })),
    }
  } catch {
    return null
  }
}
const EVIDENCE_LINE = /^   \[([\w-]+)\] (.+)$/
const FAST_DETECTOR_LINE = /^(\S+):\s*(.+?)(?:\s*->\s*(flag\{[^}]+\}|FLAG:\S+))?\s*$/
const DEEP_STEP_LINE = /\[deep\]\s+step\s+(\d+):\s*(.+)$/
const DEEP_FOUND_LINE = /\[deep\]\s+FLAG FOUND at step (\d+) via (.+)$/

/**
 * Parse the full engine stdout into a structured result object.
 *
 * Extracts:
 *  - triage: category, confidence, evidence[]
 *  - fastPath: per-detector attempt results
 *  - deepLoop: per-step strategy + outcome
 *  - overall: solved, flag, elapsed
 */
function parseEngineResult(stdout) {
  const output = String(stdout || '')
  const result = {
    command: null, solved: false, flag: '', elapsed: '',
    summary: '', output,
    // NEW: structured metadata for UI consumption
    category: '',
    confidence: 0,
    evidence: [],
    steps: [],       // timeline of every phase/detector/step
  }

  // 一次 split 复用给 fallback 解析与 summary；原实现最多 split 三次
  // （evidence / fast 段 / deep 段 / summary），长 stdout 上是纯重复扫描。
  const lines = output.split('\n')

  // --- preferred source: engine's machine-readable [meta] line ---
  const mm = META_LINE.exec(output)
  if (mm) {
    const parsed = parseMetaSteps(mm[1])
    if (parsed) {
      result.category = parsed.category
      result.confidence = parsed.confidence
      result.evidence = parsed.evidence
      result.steps = parsed.steps
    }
  } else {
    // --- fallback: regex over the human-readable log ---
    const tm = TRIAGE_LINE.exec(output)
    if (tm) {
      result.category = tm[1]
      result.confidence = parseFloat(tm[2])
    }

    let fastEnd = lines.length
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].startsWith('[deep]')) { fastEnd = i; break }
    }

    let inEvidence = false
    for (const line of lines) {
      if (line.startsWith('[triage]')) { inEvidence = true; continue }
      if (line.startsWith('[')) { inEvidence = false; continue }
      if (inEvidence) {
        const em = EVIDENCE_LINE.exec(line)
        if (em) result.evidence.push({ tag: em[1], text: em[2] })
      }
    }

    for (let i = 0; i < fastEnd; i++) {
      const fl = lines[i]
      if (!fl || fl.startsWith('[') || fl.startsWith('   ') || fl.startsWith('==')) continue
      const fm = FAST_DETECTOR_LINE.exec(fl)
      if (!fm) continue
      const found = fm[2] ? (fm[0].includes('->') && fm[0].includes('flag')) : false
      const flagMatch = /(flag\{[^}]+\}|FLAG:\S+)/i.exec(fm[0])
      result.steps.push({
        phase: 'fast', name: fm[1], detail: fm[2],
        success: !!found, flag: flagMatch ? flagMatch[1].replace(/^FLAG:\s*/, '') : '',
        time: '',
      })
    }

    let currentStep = null
    for (const dl of lines) {
      const dsm = DEEP_STEP_LINE.exec(dl)
      if (dsm) {
        currentStep = { phase: 'deep', step: parseInt(dsm[1]), strategy: dsm[2], result: 'pending' }
        result.steps.push(currentStep)
        continue
      }
      const dfm = DEEP_FOUND_LINE.exec(dl)
      if (dfm && currentStep) {
        currentStep.result = 'solved'
        currentStep.success = true
        break
      }
      if (currentStep && dl.trim() && !dl.startsWith('[')) {
        if (/->\s*(timeout|nonzero-exit|empty-output|sandbox-reject|no-result)/.test(dl)) {
          currentStep.result = 'failed'
          currentStep.errorClass = dl.match(/->\s*(\w[\w-]*)/)?.[1] || ''
        }
      }
    }
  }

  // --- final result ---
  const m = RESULT_LINE.exec(output)
  if (m) {
    result.solved = m[1] === 'SOLVED'
    result.elapsed = m[2] ?? ''
  }
  const f = FLAG_LINE.exec(output)
  if (f) result.flag = f[1]
  // Summary = last *meaningful* line; skip machine-readable [meta] lines.
  // 反向扫描代替 filter+reverse（后者复制整个行数组）。
  let last = ''
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i].replace(/\r$/, '')
    // 原实现先 trimEnd 再 filter(Boolean)：末尾纯空白行不算 summary。
    if (l.trim() && !l.startsWith('[meta]')) { last = l; break }
  }
  result.summary = last ? last.slice(0, 300) : ''
  return result
}

function truncate(text, max) {
  const s = String(text || '')
  return s.length <= max ? s : s.slice(0, max) + `\n…[truncated ${s.length - max} chars]`
}
//#endregion

//#region subprocess bridge
function runEngine(argv, { python, engineDir, timeoutMs, maxOutputChars, signal }) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-X', 'utf8', 'ctfpilot.py', ...argv], {
      cwd: engineDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    })
    let stderr = ''
    let killed = false
    const onAbort = () => { killed = true; child.kill() }
    if (signal) {
      if (signal.aborted) { killed = true; child.kill() }
      else signal.addEventListener('abort', onAbort, { once: true })
    }
    const timer = timeoutMs > 0 ? setTimeout(() => { if (!killed) { killed = true; child.kill() } }, timeoutMs) : null

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    // 不能再按事件数封顶（历史上 `if (chunks.length < 64)` 一满就丢后续输出，
    // 尾部 RESULT / module selftests 结论行会整段消失）。这里始终保留
    // "前 headN + 后 tailN" 两段，只对中间部分计数，内存有界且尾部可靠。
    // 截断标记本身也要占额度：head + 标记 + tail 必须 <= limit，否则下游
    // truncate(value.output, maxOutputChars) 会从头切掉尾部结论（实测丢 22 字符）。
    const limit = Number(maxOutputChars) || 8000
    const budget = Math.max(200, limit - 64)
    const headN = Math.floor(budget * 0.7)
    const tailN = budget - headN
    let head = ''
    let tail = ''
    let total = 0
    child.stdout.on('data', d => {
      total += d.length
      if (head.length < headN) head += d.slice(0, headN - head.length)
      tail = (tail + d).slice(-tailN)
    })
    child.stderr.on('data', d => { stderr += d })
    child.on('error', err => {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
      reject(new Error(`failed to start engine "${python}": ${err.message}. Set config.python / CTFPILOT_PYTHON to a Python 3.10+ executable.`))
    })
    child.on('close', code => {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
      // 头尾拼接：总量未超限时把两段接回原文，超限时插入截断标记，
      // 大头在前、关键结论在后。
      let stdout
      if (total <= limit) {
        const headLen = Math.min(total, head.length)
        stdout = head + tail.slice(Math.max(0, tail.length - (total - headLen)))
      } else {
        stdout = `${head}\n…(截断 ${total - limit} 字符，尾部保留)…\n${tail}`
      }
      if (killed) {
        reject(new Error(`engine aborted (call cancelled or timeout > ${Math.round(timeoutMs / 1000)}s); partial stderr: ${truncate(stderr, 500)}`))
        return
      }
      resolve({ code, stdout, stderr })
    })
  })
}
//#endregion

// 正在解题状态镜像（界面 2 轮询源）：sessionProjections 折叠到新值时同步替换，
// /ctf-solving-state 直接序列化它返回（主面板拿不到 useProjection，需命令补齐）。
  let solvingState = { sessions: {} }
  /** ctfsolve 投影的注册 handle（官方 `await register()` 返回 {stateOf(session)}）：
   *  /ctf-solving-state 按 args 传入的 sid 列表逐个读该会话的真实状态。 */
  let solveProjHandle = null
  /** 运行时注入的官方服务（与网关同一次 ctx.inject）：sessions 按 id 取会话
   *  对象；agents.turnBoundaryOf(session) 读官方 turnBoundary 投影——
   *  openTurnStartSeq 非 null = 回合进行中，是 running 的权威信号。 */
  let liveSessions = null
  let liveAgents = null

//#region solve modes + flag scanning (interfaces 1 & 2)
// 模式即真实 agent 预设：cordis.patch.yml 的 preset-ctf-quick /
// preset-ctf-deep 各有 persona 与工具集；/ctf-solve-start 先经
// agentPresets 服务 select(agent, agentPreset) 切换 blank 会话预设，
// 再注入纯题面文本（切换失败即终止，不降级注入）。
const PRESETS_BY_MODE = { quick: 'ctf-quick', deep: 'ctf-deep' }

/** 取 agentPresets 服务。2026-11-19 18:42 实锤（ctfpilot-errors.log
 * via=throw）：cordis 服务属性访问要求插件 `inject` 列表先声明——
 * 未声明时 `ctx.agentPresets` 直接抛
 * `cannot get property "agentPresets" without inject`（历史上正是这个
 * 抛错被静默 catch 折叠成 false，谎称「会话锁定」）。修复分两层：
 * ① 模块级 inject 加 'agentPresets'（官方 @deepseek-ai/dsh-agent-preset
 *    声明行同款硬依赖，宿主组合必含该服务）；
 * ② 属性访问色 try 包裹（假 ctx / 无代理环境不炸），ctx.get/root 仍兜底。 */
function ctxServiceProperty(ctx, name) {
  try {
    const svc = ctx[name]
    return svc !== undefined ? svc : null
  } catch {
    return null
  }
}

function agentPresetsService(ctx) {
  if (!ctx) return null
  const direct = ctxServiceProperty(ctx, 'agentPresets')
  if (direct && typeof direct.select === 'function') return direct
  if (typeof ctx.get === 'function') {
    try {
      const svc = ctx.get('agentPresets')
      if (svc && typeof svc.select === 'function') return svc
    } catch { /* get 不做 inject 校验，但同样防御 */ }
  }
  if (ctx.root) {
    const rootDirect = ctxServiceProperty(ctx.root, 'agentPresets')
    if (rootDirect && typeof rootDirect.select === 'function') return rootDirect
    if (typeof ctx.root.get === 'function') {
      try {
        const svc = ctx.root.get('agentPresets')
        if (svc && typeof svc.select === 'function') return svc
      } catch { /* 防御 */ }
    }
  }
  return null
}

/** 把 blank 会话切到指定预设（select(agent, agentPreset)）。
 * 2026-11-19 实锤复盘：此前一切失败一律折叠成 false，调用方文案统一谎称
 * 「会话锁定」，把两类真实故障（preset 未注册 not-found、服务不可达
 * no-service）全部掩盖了。现在返回结构化结果，由调用方如实分类上屏。
 * reason 与官方 RemoteError code 对齐（asar 实装 AgentPresetRegistry：
 * agent-preset/locked | not-found | invalid）。 */
async function switchSolvePresetEx(ctx, agent, preset) {
  if (!agent) return { ok: false, reason: 'no-agent', detail: 'no live agent' }
  const svc = agentPresetsService(ctx)
  if (!svc) {
    return {
      ok: false, reason: 'no-service',
      detail: 'agentPresets 服务不可达（ctx/ctx.root 均未解析到该服务；宿主组合或版本不符）',
    }
  }
  try {
    await svc.select(agent, preset)
    return { ok: true, reason: 'ok', detail: '' }
  } catch (err) {
    const code = String((err && err.code) || '')
    const detail = String((err && err.message) || err).slice(0, 300)
    if (code === 'agent-preset/locked') return { ok: false, reason: 'locked', detail }
    if (code === 'agent-preset/not-found') return { ok: false, reason: 'not-found', detail }
    if (code === 'agent-preset/invalid') return { ok: false, reason: 'invalid', detail }
    return { ok: false, reason: 'error', detail: code ? `${code}: ${detail}` : detail }
  }
}

const MODES = {
  quick: { id: 'quick', label: '快解' },
  deep: { id: 'deep', label: '深度解析' },
}
function modeLabel(id) { const m = MODES[String(id || '')]; return m ? m.label : '深度解析' }

// Same shape as workflows/ctf-solve-pipeline.js FLAG_RE — text/CoT scanner.
// i 必须保留：原 scanFlags 每次用 'gi' 重建，大小写混合的 Flag{} 也能命中。
// 2026-11-25 放宽下限 4→3：端口/计数类题目的答案常是 3 位数字
// （如本机实测 nmap 端口 135/445/9002 → flag{149}），旧下限 4 永远抽不出。
// 3 字符下限仍能挡住 flag{a}/flag{} 这类占位碎片，误报风险可忽略。
const FLAG_SCAN_RE = /\b(?:flag|FLAG|ctf|CTF|DASCTF|NSSCTF|SUCTF|hgame)\s*\{[^}\s]{3,256}\}/gi
function scanFlags(text) {
  const src = String(text || '')
  // 快速失败：flag 必含 '{'；绝大多数普通消息不进入正则引擎。
  if (src.indexOf('{') < 0) return []
  // matchAll 克隆正则游标，不修改模块级 FLAG_SCAN_RE 的 lastIndex；
  // 原实现每次 new RegExp 重新编译（每条 assistant/tool 事件都会调一次）。
  const out = []
  for (const m of src.matchAll(FLAG_SCAN_RE)) {
    if (out.length >= 30) break
    out.push(m[0])
  }
  return out
}

/** 引号感知 tokenizer：返回 [{v, q}]（q=是否被引号包赛）。
 * 前端把含空格的值（题面/附件路径/分类）用双引号包裹发来，
 * 引号内的空白不切分、引号剥除。q 供上层区分「裸位置词」与「正文段」。 */
function shellSplitTokens(raw) {
  const out = []
  let cur = ''
  let curQuoted = false
  let quote = ''
  const src = String(raw || '')
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (quote) {
      if (ch === quote) quote = ''
      else cur += ch
    } else if (ch === '"' || ch === "'") {
      quote = ch
      curQuoted = true
    } else if (/\s/.test(ch)) {
      if (cur) { out.push({ v: cur, q: curQuoted }); cur = ''; curQuoted = false }
    } else {
      cur += ch
    }
  }
  if (cur) out.push({ v: cur, q: curQuoted })
  return out
}

// One tokenizer shared by /ctf-solve-start and its projection fold.
function parseSolveStartArgs(raw) {
  const parts = shellSplitTokens(raw)
  const out = { mode: '', category: '', target: '', attaches: [], text: [] }
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i].v
    if (p === '--mode') { out.mode = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--mode=')) { out.mode = p.slice(7); continue }
    if (p === '--category') { out.category = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--category=')) { out.category = p.slice(11); continue }
    // 靶机地址（2026-11-24）：扫描/连接类题目的授权靶标。会话 2193d69d 实锤：
    // 题面只说「nmap 扫出来的所有端口」，没给目标 → agent 无法使用已装好的
    // nmap，只能 fast-fail 反问。表单/命令行显式传入后，题面自带靶标，
    // agent 可直接开扫（仅限用户声明授权的靶机，与 persona 的授权口径一致）。
    if (p === '--target') { out.target = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--target=')) { out.target = p.slice(9); continue }
    if (p === '--attach') { out.attaches.push(String((parts[++i] || {}).v || '')); continue }
    if (p.startsWith('--attach=')) { out.attaches.push(p.slice(9)); continue }
    if (!p.startsWith('--')) {
      // 首个「未被引号包裹」的位置词是模式（input hint quick|deep 承诺，
      // 面板 buildStartCommand 同口径）：仅为引号内的正文永不误吞。
      if (out.mode === '' && out.text.length === 0 && !parts[i].q &&
          (p === 'quick' || p === 'deep')) {
        out.mode = p
        continue
      }
      out.text.push(p)
    }
  }
  // 词间单空格重组：shellSplitTokens 的引号感知保证含空格路径/词汇完整入列，
  // join(' ') 只压缩多处连续空格，对题面语义无损。
  if (!out.mode) out.mode = 'quick'
  return out
}

function solvePromptText(args) {
  const mode = MODES[args.mode] ? args.mode : 'deep'
  const lines = []
  const body = args.text.join(' ').trim()
  lines.push(body ? `题目描述：${body}` : '（未提供题面文字，关注下方附件路径。）', '')
  if (args.target) lines.push(`靶机地址（用户声明已授权的靶标）：${args.target}`)
  if (args.attaches.length) lines.push(`附件路径：${args.attaches.join('; ')}`)
  if (args.category) lines.push(`题型（可选信息）：${args.category}`)
  lines.push(
    '',
    `按当前预设口径（${modeLabel(mode)}）推进；拿到候选 flag 后走 propose_submit 人审卡片确认，确认后才计入台账。`,
  )
  return { mode, text: lines.join('\n') }
}
//#endregion

/** /ctf-drill-start 参数解析（纯函数）：--category 筛选 / --name 精选（可多次）/
 *  --limit 上限（默认 10，0=不限）/ --dir 题目根目录。引号感知，复用 solve 同款 tokenizer。 */
function parseDrillArgs(raw) {
  const parts = shellSplitTokens(raw)
  const out = { category: '', names: [], limit: 10, dir: '' }
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i].v
    if (p === '--category') { out.category = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--category=')) { out.category = p.slice(11); continue }
    if (p === '--name') { out.names.push(String((parts[++i] || {}).v || '')); continue }
    if (p.startsWith('--name=')) { out.names.push(p.slice(7)); continue }
    if (p === '--limit') { const n = parseInt(String((parts[++i] || {}).v || ''), 10); if (Number.isFinite(n) && n >= 0) out.limit = n; continue }
    if (p.startsWith('--limit=')) { const n = parseInt(p.slice(8), 10); if (Number.isFinite(n) && n >= 0) out.limit = n; continue }
    if (p === '--dir') { out.dir = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--dir=')) { out.dir = p.slice(6); continue }
  }
  out.names = out.names.filter(Boolean)
  return out
}

/**
 * 批量重刷请求文本（纯函数，测试直接断言）：把台账已解题清单 + 完整练兵协议
 * 合成一条自包含注入（2026-11-25 用户令：把「重解→自验→记练→更优解法入库→
 * 末尾汇总 + RSI 线索」整条训练闭环自动化）。与 install-all 同源纪律：
 * 串行、诚实跳过、止损、认证边界硬停；引擎调用带完整前缀（本机无
 * ctfpilot PATH 垫片，agent 零上下文可直接执行）。
 */
function buildDrillRequestText(list, args, engPrefix) {
  const lines = []
  lines.push(`请在本对话中执行「批量重刷训练」：依次重解以下 ${list.length} 道历史已解题，逐题走完整练兵协议，不要另开对话。`)
  lines.push('')
  lines.push(`引擎调用统一形态（本机无 ctfpilot PATH 垫片，请用完整调用）：${engPrefix} <子命令> [参数]`)
  lines.push(`示例：${engPrefix} arena record --name "<题名>" --category "<分类>" --fast`)
  lines.push('')
  lines.push('题目清单（按台账时间倒序；历史 flag 仅用于解出后自验，不是让你直接抄）：')
  list.forEach((c, i) => {
    lines.push(`${i + 1}. ${c.name}（分类：${c.cat || 'n/a'}；历史解法模式：${c.mode || 'n/a'}；解出于 ${c.time || 'n/a'}；历史 flag：${c.flag || '(未知)'}）`)
  })
  lines.push('')
  if (args.dir) {
    lines.push(`题目根目录（用户指定）：${args.dir}（优先在此按题名定位题目文件）`)
    lines.push('')
  }
  lines.push('逐题流程（串行，做完一题再下一题）：')
  lines.push(`1) 定位题目文件：${args.dir ? '优先【题目根目录】；' : ''}先 ${engPrefix} recall --query "<题名>" 从历史轨迹找原始目录线索，再查本对话工作区子目录与 C:\\Users\\Administrator\\Downloads。只在这些范围找，禁止全盘扫描；找不到 → 记「原件缺失」跳过，末尾汇总。`)
  lines.push(`2) 重解：先跑引擎快通道：${engPrefix} solve "<题目目录>" --mode auto；未命中再按题型路由用本机工具链手工解。单题预算 ≤ 10 分钟，超时记失败跳过。`)
  lines.push('3) 自验：候选 flag 与清单历史 flag 归一化比对（大小写/包装差异自行判断）。一致 → 通过；不一致 → 如实报差异并记失败，禁止硬凑、禁止直接抄历史 flag 交差。')
  lines.push('4) 记练（每题必做，练兵台账与生产轨迹隔离）：')
  lines.push(`   ${engPrefix} arena register --name "<题名>" --category "<分类>" --source redrill（已注册的报错忽略，不纠缠）`)
  lines.push(`   ${engPrefix} arena record --name "<题名>" --category "<分类>" <标志>`)
  lines.push('   标志：快通道一发命中 → --fast；手工/深通道解出 → 不带标志；未解出或自验不一致 → --fail。')
  lines.push(`5) 对比与入库：${engPrefix} recall --query "<题名>" 查历史解法，对比方法/步数/工具：`)
  lines.push('   - 与历史同路同长 → 不重复入库；')
  lines.push(`   - 更快/更优/用了新工具 → 写 writeup（本对话工作区 writeups-redrill\\<题名>.md），然后 ${engPrefix} learn-writeup "<该文件路径>" --name "<题名>"。`)
  lines.push('6) 每题完成即报一行小结：题名 | 结果（快解/解出/失败/原件缺失） | 新解法相对历史（同路/更快/新工具/更差） | 是否入库。')
  lines.push('')
  lines.push('全部题目处理完后收尾：')
  lines.push(`- 若期间有任何新 writeup 入库：${engPrefix} rebuild-knowledge（只跑一次）。`)
  lines.push('- 输出逐题汇总表与对比结论；若某新打法在多题重复出现，列出打法特征并提示用户：可到「自进化」标签页看 RSI 是否已生成对应 detector 提案（提案采纳后才变秒解）。')
  lines.push('')
  lines.push('纪律：')
  lines.push('- 这些题已在生产台账：不要执行 record-win（重复记账会被引擎拒；被拒不纠缠）；')
  lines.push('- 任何接口 401/403 或要求凭据 = 设计认证边界，立即停；')
  lines.push('- 任一题的任一子目标连续失败 3 次即跳过该题，绝不死磕；')
  lines.push('- 所有结论以真实执行输出为准，禁止编造「已解出」。')
  return lines.join('\n')
}

// 把一条插件消息注入 live agent 的会话：followup() 唤醒普通下一轮，
// steer() 直接喂下一步输入。两个解题命令共用这一个实现。
// 契约（部署版 runtime format v4 + 官方 schedule 插件实测）：
//   UserMessage = { id, role:'user', content:[...], source }；
//   source 的 kind 必须是 producer 自有名字且非 'plugin' —— v4 校验器
//   明文拒绝 kind==='plugin'（retired plugin wrapper），官方写法是
//   { kind: 'schedule' }。本插件注册名 'ctfpilot' 即 kind。
//   followup/steer 为同步 void。
import { randomUUID } from 'node:crypto'

function pluginUserMessage(text) {
  return {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: String(text) }],
    source: { kind: 'ctfpilot' },
  }
}

// 注入失败必须携带原始异常（2026-11-19 用户令：删光吞错降级——
// catch {} 折叠成 false 会把 v4 校验器等真实拒绝原因全部埋掉）。
function injectAgentMessage(agent, text, { steer = false } = {}) {
  if (!agent || typeof agent.followup !== 'function') {
    return { injected: false, error: 'agent 缺失或无 followup 方法' }
  }
  try {
    const message = pluginUserMessage(text)
    if (steer && typeof agent.steer === 'function') {
      agent.steer(message)
    } else {
      agent.followup(message)
    }
    return { injected: true, error: '' }
  } catch (err) {
    return { injected: false, error: String((err && err.message) || err) }
  }
}

//#region tool manager (interface 3) — registry + pwsh runner
/** 只留 ASCII：探针 detail 仅作「找到/没找到」的信号，非 ASCII 字节一律丢弃——
 * where.exe 在中文系统输出 GBK 本地化错误串（如「系统找不到指定的文件。」），
 * 若被按 UTF-8/1252 解读即乱码上屏；源头丢弃是唯一可靠消毒点，不依赖控制台编码真生效。 */
function asciiOnly(s) {
  return String(s == null ? '' : s).replace(/[^\u0000-\u007F]/g, '')
}
function shellRunSync(line, { timeoutMs = 30000 } = {}) {
  try {
    // 双保险：固定 pwsh 控制台 UTF-8 输出编码 + 回读端 ASCII 化。
    const head = '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()'
    // 以引号开头的可执行路径必须用 & 调用（PowerShell 命令模式会把
    // "C:\x\python.exe" -c ... 解析成字符串减法）；where.exe 等裸命令不受影响。
    const cmd = /^["']/.test(line) ? `& ${line}` : line
    const r = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command', `${head}; ${cmd}`], { windowsHide: true, encoding: 'utf8', timeout: timeoutMs })
    // 超时/启动失败必须显式成文本：r.error 存在时 stdout/stderr 常为空，
    // 落回 '(no output)' 会把「超时/进程起不来」吞成一句无输出失败（2026-11-20 修复）。
    const errText = r.error
      ? `pwsh 进程异常（${String(r.error.code || r.error.message || 'spawn error')}${/timed?\s?out|ETIMEDOUT/i.test(String(r.error.message || '') + String(r.error.code || '')) ? `，已超 ${Math.round(timeoutMs / 1000)}s 超时上限` : ''}）`
      : asciiOnly(String(r.stderr || '').trim())
    return { code: r.status ?? 1, out: asciiOnly(String(r.stdout || '').trim()), err: errText }
  } catch (e) {
    return { code: 1, out: '', err: asciiOnly(e.message || String(e)) }
  }
}
/** 异步探针：status 快检 7 项并行。原实现 `Promise.resolve(shellRunSync(...))`
 *  在调用时已同步跑完，Promise.all 只是收集结果——实际仍串行阻塞事件循环，
 *  最坏耗时是 7 项累加。这里用 spawn 真并行，最坏 = 最慢一项。 */
function shellRun(line, { timeoutMs = 30000 } = {}) {
  return new Promise((resolve) => {
    const head = '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()'
    const cmd = /^["']/.test(line) ? `& ${line}` : line
    let child
    try {
      child = spawn('pwsh', ['-NoProfile', '-NonInteractive', '-Command', `${head}; ${cmd}`], { windowsHide: true })
    } catch (e) {
      resolve({ code: 1, out: '', err: asciiOnly(e.message || String(e)) })
      return
    }
    let stdout = ''
    let stderr = ''
    let settled = false
    let timer = null
    const finish = (code, errText) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      resolve({
        code,
        out: asciiOnly(String(stdout || '').trim()),
        err: errText === undefined ? asciiOnly(String(stderr || '').trim()) : errText,
      })
    }
    timer = setTimeout(() => {
      try { child.kill() } catch { /* 已退出 */ }
      finish(1, `pwsh 进程异常（timeout，已超 ${Math.round(timeoutMs / 1000)}s 超时上限）`)
    }, timeoutMs)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (d) => { stdout += d })
    child.stderr.on('data', (d) => { stderr += d })
    child.on('error', (err) => {
      const msg = String((err && (err.code || err.message)) || 'spawn error')
      const timedOut = /timed?\s?out|ETIMEDOUT/i.test(msg)
      finish(1, `pwsh 进程异常（${msg}${timedOut ? `，已超 ${Math.round(timeoutMs / 1000)}s 超时上限` : ''}）`)
    })
    child.on('close', (code) => finish(code ?? 1))
  })
}
// Stable catalog: even uninstalled tools keep their row (interface 3 spec).
// 通道字段（2026-11-20 修复「无 choco 机器点安装必炸」）：
//   via      —— 'choco'：装/更/卸走包管理器（有 choco 用 choco，无 choco 自动回退
//              winget，winget 也没包则给人工指引，不硬跑必败命令）
//              'pip'  ：Python 生态工具，命令与包管理器无关，原样执行
//   wingetId —— winget 源包 ID。2026-11-25 实测 winget 源：nmap 有 Insecure.Nmap，
//              tshark 有 WiresharkFoundation.Wireshark（4.6.10，官方包自带 tshark CLI），
//              exiftool 有 OliverBetz.ExifTool（13.64）；sqlmap/john/hashcat/
//              binwalk/radare2 官方不发布 winget 包，留空（→ 无 choco 时给指引）。
//   pip 类工具（volatility3/pwntools/dirsearch/scapy）与包管理器无关。
//   2026-11-25 补齐五类空白（用户令）：tshark——pcap 流量分析；scapy——数据包
//   构造与解析；dirsearch——Web 目录爆破；exiftool——文件元数据取证；radare2——
//   逆向工程。通道均经实测验证（winget search / pip index / choco 官网）。
// 探测降级字段（2026-11-20 ②「装了但漏检」修复，配合 ① 注册表 PATH 合并）：
//   paths —— where.exe PATH 未命中后的常见安装位置（fs.existsSync 绝对路径
//            存在性，零开销不依赖 PATH）：官方 zip 解压型（hashcat/john）、
//            winget/choco 默认目录、git clone 常见落点。
//   alt   —— 备用探测命令（如 binwalk 的 python import 形态：pip 装进
//            python 但 Scripts 目录不在 PATH 时 where.exe 永远找不到）。
const TOOLS = [
  { id: 'nmap', name: 'nmap（端口扫描，Kali 式镜像）', kind: 'mirror', via: 'choco', wingetId: 'Insecure.Nmap', probe: 'where.exe nmap', paths: ['C:\\Program Files (x86)\\Nmap\\nmap.exe', 'C:\\Program Files\\Nmap\\nmap.exe', 'C:\\ProgramData\\chocolatey\\bin\\nmap.exe'], install: 'choco install nmap -y', update: 'choco upgrade nmap -y', uninstall: 'choco uninstall nmap -y' },
  { id: 'tshark', name: 'tshark（pcap 流量分析，Wireshark CLI）', kind: 'mirror', via: 'choco', wingetId: 'WiresharkFoundation.Wireshark', probe: 'where.exe tshark', paths: ['C:\\Program Files\\Wireshark\\tshark.exe', 'C:\\Program Files (x86)\\Wireshark\\tshark.exe', 'C:\\ProgramData\\chocolatey\\bin\\tshark.exe'], install: 'choco install wireshark -y', update: 'choco upgrade wireshark -y', uninstall: 'choco uninstall wireshark -y' },
  { id: 'scapy', name: 'scapy（数据包构造与解析）', kind: 'mirror', via: 'pip', wingetId: '', probe: 'python -c "import scapy"', install: 'python -m pip install scapy', update: 'python -m pip install --upgrade scapy', uninstall: 'python -m pip uninstall scapy -y' },
  { id: 'sqlmap', name: 'sqlmap（SQL 注入）', kind: 'mirror', via: 'choco', wingetId: '', probe: 'where.exe sqlmap.py', paths: ['C:\\ProgramData\\chocolatey\\lib\\sqlmap\\tools\\sqlmap\\sqlmap.py', 'C:\\sqlmap\\sqlmap.py', path.join(os.homedir(), 'sqlmap', 'sqlmap.py')], install: 'choco install sqlmap -y', update: 'choco upgrade sqlmap -y', uninstall: 'choco uninstall sqlmap -y' },
  { id: 'dirsearch', name: 'dirsearch（Web 目录爆破）', kind: 'mirror', via: 'pip', wingetId: '', probe: 'python -c "import dirsearch"', install: 'python -m pip install dirsearch', update: 'python -m pip install --upgrade dirsearch', uninstall: 'python -m pip uninstall dirsearch -y' },
  { id: 'john', name: 'John the Ripper（口令破解）', kind: 'mirror', via: 'choco', wingetId: '', probe: 'where.exe john', paths: ['C:\\john\\run\\john.exe', 'C:\\john-the-ripper\\run\\john.exe', 'C:\\Program Files\\John the Ripper\\run\\john.exe', 'C:\\ProgramData\\chocolatey\\bin\\john.exe'], install: 'choco install john -y', update: 'choco upgrade john -y', uninstall: 'choco uninstall john -y' },
  { id: 'hashcat', name: 'hashcat（GPU 口令破解）', kind: 'mirror', via: 'choco', wingetId: '', probe: 'where.exe hashcat', paths: ['C:\\Program Files\\hashcat\\hashcat.exe', 'C:\\Program Files\\hashcat\\hashcat64.exe', 'C:\\Program Files (x86)\\hashcat\\hashcat.exe', 'C:\\Program Files (x86)\\hashcat\\hashcat64.exe', 'C:\\ProgramData\\chocolatey\\bin\\hashcat.exe'], install: 'choco install hashcat -y', update: 'choco upgrade hashcat -y', uninstall: 'choco uninstall hashcat -y' },
  { id: 'binwalk', name: 'binwalk（固件/镜像提取）', kind: 'mirror', via: 'choco', wingetId: '', probe: 'where.exe binwalk', alt: 'python -c "import binwalk"', paths: ['C:\\ProgramData\\chocolatey\\bin\\binwalk.exe'], install: 'choco install binwalk -y', update: 'choco upgrade binwalk -y', uninstall: 'choco uninstall binwalk -y' },
  { id: 'exiftool', name: 'exiftool（文件元数据取证）', kind: 'mirror', via: 'choco', wingetId: 'OliverBetz.ExifTool', probe: 'where.exe exiftool', paths: ['C:\\Program Files\\ExifTool\\exiftool.exe', 'C:\\ProgramData\\chocolatey\\bin\\exiftool.exe'], install: 'choco install exiftool -y', update: 'choco upgrade exiftool -y', uninstall: 'choco uninstall exiftool -y' },
  { id: 'volatility3', name: 'volatility3（内存取证）', kind: 'mirror', via: 'pip', wingetId: '', probe: 'python -c "import volatility3"', install: 'python -m pip install volatility3', update: 'python -m pip install --upgrade volatility3', uninstall: 'python -m pip uninstall volatility3 -y' },
  { id: 'pwntools', name: 'pwntools（二进制利用）', kind: 'mirror', via: 'pip', wingetId: '', probe: 'python -c "import pwn"', install: 'python -m pip install pwntools', update: 'python -m pip install --upgrade pwntools', uninstall: 'python -m pip uninstall pwntools -y' },
  { id: 'radare2', name: 'radare2（逆向工程 CLI）', kind: 'mirror', via: 'choco', wingetId: '', probe: 'where.exe radare2', paths: ['C:\\ProgramData\\chocolatey\\bin\\radare2.exe', 'C:\\radare2\\bin\\radare2.exe'], install: 'choco install radare2 -y', update: 'choco upgrade radare2 -y', uninstall: 'choco uninstall radare2 -y' },
  { id: 'protocol-mcp', name: 'ProtocolMCP（协议安全 MCP 服务）', kind: 'mcp', via: 'mcp', wingetId: '', probe: '', install: '# 在 MCP Manager 页面添加 protocol-mcp 服务器。', update: '# 在 MCP Manager 页面重配 protocol-mcp。', uninstall: '# 在 MCP Manager 页面移除 protocol-mcp。' },
]
/** 进程内包管理器探测缓存：Host 常驻，探测一次（where.exe <1s）即免重复。
 *  CTFPILOT_PKG_MANAGER=choco|winget|none 可强制覆盖（测试钩子/特殊环境），
 *  非法值忽略走真实探测。「无包管理器」不缓存——用户中途装好 choco 后，
 *  下一次探测自动转正，无需重启 DSH。 */
let pkgManagerCache = ''
function resolvePkgManager() {
  if (pkgManagerCache) return pkgManagerCache
  const forced = String(process.env.CTFPILOT_PKG_MANAGER || '').trim().toLowerCase()
  if (forced === 'choco' || forced === 'winget') { pkgManagerCache = forced; return forced }
  if (forced === 'none') return 'none'
  const has = (exe) => shellRunSync(`where.exe ${exe}`, { timeoutMs: 5000 }).code === 0
  let pm = 'none'
  if (has('choco')) pm = 'choco'
  else if (has('winget')) pm = 'winget'
  if (pm !== 'none') pkgManagerCache = pm
  return pm
}
/** 仅测试用：清空探测缓存（生产路径不调用）。 */
function resetPkgManagerCache() { pkgManagerCache = '' }
/**
 * ① 注册表 PATH 合并（2026-11-20「装了但漏检」修复）。
 * 根因：winget/choco/官方安装器装完只写注册表（Machine+User PATH），
 * 常驻 Host 进程的 PATH 是启动时的快照——不合并，新装工具永远漏检
 * （实锤：nmap 装好、注册表已含 Nmap 条目，Host 进程里 where.exe 找不到）。
 * 做法：pwsh 经 [Environment]::GetEnvironmentVariable 拉注册表两段
 * （REG_EXPAND_SZ 由 .NET API 自动展开），Machine→User→进程旧值顺序合并
 * （去重：斜杠归一 + 大小写不敏感，Windows 文件系统同口径），写回
 * process.env.PATH——node 子进程继承 env，已实测 where.exe 立即命中。
 * 不走 shellRunSync：其 asciiOnly 会剥掉 PATH 条目里的非 ASCII 目录名
 * （中文路径将永久损坏）；此处独立 spawn + 纯 UTF-8 解码。
 * pull 参数仅供测试注入假注册表（返回 { ok, machine, user, error }）；
 * 生产留空走真实拉取。返回 { ok, added, error }：失败保留原 PATH 不动。
 */
function pullRegistryPath() {
  const head = '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()'
  const line = "[Environment]::GetEnvironmentVariable('Path','Machine'); '===USER==='; [Environment]::GetEnvironmentVariable('Path','User')"
  let r
  try {
    r = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command', `${head}; ${line}`], { windowsHide: true, encoding: 'utf8', timeout: 8000 })
  } catch (e) {
    return { ok: false, machine: '', user: '', error: String((e && e.message) || e) }
  }
  if (r.error) return { ok: false, machine: '', user: '', error: `pwsh 进程异常（${String(r.error.code || r.error.message || 'spawn error')}）` }
  const out = String(r.stdout || '')
  const idx = out.indexOf('===USER===')
  if (r.status !== 0 || idx < 0) return { ok: false, machine: '', user: '', error: sanitizeText(String(r.stderr || out || '(no output)'), 160) || `pwsh 退出码 ${r.status}` }
  return { ok: true, machine: out.slice(0, idx), user: out.slice(idx + 10), error: '' }
}
function mergeRegistryPath(pull) {
  const p = typeof pull === 'function' ? pull() : pullRegistryPath()
  if (!p || !p.ok) return { ok: false, added: 0, error: (p && p.error) || '注册表 PATH 拉取不可用' }
  const normKey = (s) => String(s).trim().replace(/[\\/]+/g, '\\').toLowerCase()
  const oldEntries = String(process.env.PATH || '').split(';').map((s) => String(s).trim()).filter(Boolean)
  const oldCount = new Set(oldEntries.map(normKey)).size
  // 顺序契约（2026-11-20 实锤修复）：进程现有条目在前，注册表条目只做**增量
  // 补充**在后——补丁语义。反之（注册表段前置）会被真实注册表里的
  // WindowsApps 段劫持解析：pwsh 的 python 从 TeleAgent 3.12 变成商店桩
  // （Get-Command python → WindowsApps，静默 exit 9009），python 类探针全红，
  // 比不合并更糟。旧值优先 = 用户启动配置是基线，注册表是新装工具的增量。
  const parts = [...oldEntries, ...String(p.machine || '').split(';'), ...String(p.user || '').split(';')]
  const seen = new Set()
  const merged = []
  for (const raw of parts) {
    const entry = String(raw).trim()
    if (!entry) continue
    const key = normKey(entry)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(entry)
  }
  if (merged.length === 0) return { ok: false, added: 0, error: '合并结果为空（保留原 PATH）' }
  process.env.PATH = merged.join(';')
  return { ok: true, added: merged.length - oldCount, error: '' }
}
/** winget 通道命令构造（实测 winget v1.29.380 支持全部参数）。
 *  uninstall 不带 --accept-*：该动词没有这两个参数，带上直接报错。 */
function wingetVerb(verb, wingetId) {
  if (verb === 'install') {
    return `winget install --id ${wingetId} -e --silent --disable-interactivity --accept-package-agreements --accept-source-agreements`
  }
  if (verb === 'update') {
    return `winget upgrade --id ${wingetId} -e --silent --disable-interactivity --include-unknown --accept-package-agreements --accept-source-agreements`
  }
  return `winget uninstall --id ${wingetId} -e --silent --disable-interactivity`
}
/** 无包管理器可用时的指引（给到可直接粘贴的命令，绝不静默降级）。 */
function noPmGuidance() {
  return [
    '本机未检测到任何包管理器（choco / winget 都不在 PATH），自动安装走不通。两条路任选：',
    '1) Windows 10 1809+ 一般自带 winget：从 Microsoft Store 安装「应用安装程序（App Installer）」后重试；',
    '2) 以管理员身份运行 PowerShell 安装 Chocolatey（一条命令）：',
    "   Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))",
    '装好后回到本面板再点安装（探测对失败不缓存，无需重启 DSH）。',
  ].join('\n')
}
/** 有 winget 但该工具没有 winget 包：choco 路线 + 官方手动获取路线。 */
function chocoGuidance(tool) {
  const hint = {
    sqlmap: 'git clone https://github.com/sqlmapproject/sqlmap.git（纯 Python 3，python sqlmap.py 即用）',
    john: 'https://www.openwall.com/john/ 下载 Windows jumbo 预编译版（解压后把目录加进 PATH）',
    hashcat: 'https://hashcat.net/hashcat/ 下载官方 Windows 二进制 zip（解压即用）',
    binwalk: 'python -m pip install binwalk（v3 含 Rust 扩展，需可用编译环境）',
    nmap: 'https://nmap.org/download.html 下载官方 Windows 安装器',
    radare2: 'https://github.com/radareorg/radare2/releases 下载 w64 预编译 zip（解压后把 bin 目录加进 PATH）',
  }[tool.id] || '见该工具官方网站'
  return [
    `${tool.name}：winget 源没有该工具的包（2026-11-25 实测：winget 有包 nmap/tshark(Wireshark)/exiftool，无包 sqlmap/john/hashcat/binwalk/radare2），本机又未装 choco，无法自动安装。两条路任选：`,
    '1) 以管理员身份运行 PowerShell 安装 Chocolatey（一条命令）：',
    "   Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))",
    '   装完后回来再点安装（探测对失败不缓存，无需重启 DSH）。',
    `2) 手动获取：${hint}`,
  ].join('\n')
}
/**
 * 装/更/卸命令解析（纯函数，测试直接断言，不执行任何命令）：
 *  - pip 工具与包管理器无关，原命令直通；
 *  - 有 choco → choco 命令；无 choco 有 winget 且该工具有 winget 包 → 自动回退 winget；
 *  - winget 也没包 / 什么包管理器都没有 → blocked + 人工指引。
 * 返回 { line, via } 或 { blocked: true, guidance }。
 */
function resolveToolAction(tool, verb, pm) {
  if (!tool || !tool[verb]) return { blocked: true, guidance: `工具 ${tool && tool.id}（${verb}）缺少命令定义，请反馈给插件维护者。` }
  if (tool.via === 'pip') return { line: tool[verb], via: 'pip' }
  if (pm === 'choco') return { line: tool[verb], via: 'choco' }
  if (pm === 'winget') {
    if (tool.wingetId) return { line: wingetVerb(verb, tool.wingetId), via: 'winget' }
    return { blocked: true, guidance: chocoGuidance(tool) }
  }
  return { blocked: true, guidance: noPmGuidance() }
}
/** verb → 中文动作名（注入文本与回执共用）。 */
const TOOL_VERB_ZH = { install: '安装', update: '更新', uninstall: '卸载' }
/**
 * 构造发给 agent 的工具装/更/卸请求文本（2026-11-20 用户方案：
 * 面板点安装 → 自动开新对话 → agent 代跑包管理器）。
 * Host 不再直接执行安装命令——实测硬跑的坑一箩筐：winget 卸载器弹 GUI、
 * 安装只写注册表 PATH 而常驻 Host 进程看不见、无 choco 时必败且错误带
 * ANSI 乱码。agent 能看真实输出、自适应处理、错误天然在对话里可读。
 * 文本自包含：通道实测 + 建议命令 + 验证 + 复检闭环，agent 零上下文可执行。
 * 2026-11-24 复检口径修正（session-8f0176a7 实锤：agent 被要求执行 GUI
 * 斜杠命令 /ctf-tools status，撞上数据网关 401 认证边界后烧了 11 分钟
 * 死磕绕过——绝对路径验不到 + 没有止损纪律）：
 *   ① 复检改为会话内可完成的「where.exe / 绝对路径 / 版本命令」等效链，
 *      常见安装位置直接随文本下发（TOOLS[].paths）；
 *   ② 明确告知 /ctf-tools status 是 GUI 斜杠命令、会话内无法代跑，
 *      官方复检由用户在工具面板点「快检」完成——不再派不可达目标；
 *   ③ 追加认证边界硬停 + 止损纪律（连续失败 3 次即停）。
 */
function buildToolRequestText(tool, verb, pm) {
  const zh = TOOL_VERB_ZH[verb] || verb
  const action = resolveToolAction(tool, verb, pm)
  const channelLine = pm === 'choco' ? 'choco（已检出）'
    : pm === 'winget' ? 'winget（未检出 choco；2026-11-25 实测 winget 源有包：nmap/tshark(Wireshark)/exiftool，其余工具无包）'
    : '未检出任何包管理器（choco / winget 都不在 PATH）'
  const cmdLine = action.blocked
    ? `自动通道不可用，请按以下指引处理：\n${action.guidance}`
    : `建议执行（PowerShell）：\n${action.line}`
  const lines = [
    `请在本机${zh} CTF 工具：${tool.name}（id：${tool.id}）。`,
    '',
    `本机通道实测：${channelLine}。`,
    cmdLine,
  ]
  const cands = Array.isArray(tool.paths) ? tool.paths.filter(Boolean) : []
  if (cands.length) {
    lines.push(
      '',
      `该工具常见安装位置（where.exe 未命中时逐个用绝对路径验证）：`,
      ...cands.map((p) => `- ${p}`),
    )
  }
  lines.push(
    '',
    '要求：',
    `1) 如实执行并报告输出：失败不要盲目重试超过 2 次，把原始错误完整贴出。`,
    `2) ${zh}完成后验证可用性：${tool.probe}${/where\.exe/i.test(tool.probe) ? '，能显示版本的话再跑一次版本命令' : '（python 生态，验证 import 即可）'}。`,
    '3) 若验证时找不到命令（新装工具只写注册表 PATH，当前进程是旧快照）：改用上面列出的常见安装位置做绝对路径验证——绝对路径能执行即视为已装好、可用，如实汇报并建议用户把该目录加入 PATH 或重启 DSH；不要谎报失败。',
    '4) 结束前做一次等效复检（where.exe / 绝对路径 / 版本命令任一命中即 ok），并汇报该工具的 ok 状态与结论。注意：/ctf-tools status 是 GUI 斜杠命令，会话内无法代跑、也不要尝试代跑；官方复检由用户在工具管理面板点「快检」完成。',
    '5) 止损纪律：任一子目标连续失败 3 次即停，如实汇报后收尾；任何接口返回 401/403 或要求浏览器凭据都是设计认证边界——立即停止尝试、如实汇报，严禁寻找/复用/绕过认证凭据（不挖 cookie/token，不硬闯网关）。',
  )
  return lines.join('\n')
}
/**
 * probe 输出消毒：剔除控制字符与 ANSI 转义序列，压缩空白，上限截断。
 * 工具管理界面的 detail 列此前直接透传 pwsh stderr，乱码根因在此。
 */
function sanitizeText(s, max = 120) {
  return String(s == null ? '' : s)
    // 顺序即正确性：必须先剥 ANSI 转义（ESC+[+参数+字母）。若先跑下面的
    // 控制字符剥离，ESC(\u001B) 会被单独删掉，转义残骸「[31;1m」永久留在
    // 文本里——用户面板上「✗ install 失败：[31;1mchoco…」乱码即此形状。
    .replace(/\u001B\[[0-9;]*[A-Za-z]/g, '')
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, max)
}
function findTool(id) { return TOOLS.find(t => t.id === id || t.name === id) || null }
/**
 * 一键安装目标解析（纯函数，测试直接断言）：把 status 快检行联表 TOOLS 目录，
 * 分出三堆——missing（未装且非 MCP：含 install/probe 等完整目录定义，供请求
 * 文本构造）、missingMcp（未配置的 MCP 行：只在文本里给 MCP Manager 指引，
 * 不进自动安装清单）、okNames（已可用行名：文本里如实声明「无需处理」）。
 * 快检行只带 {id,name,kind,ok,detail}，不含命令字段——必须联表还原完整项。
 */
function resolveInstallAllTargets(rows) {
  const missing = []
  const missingMcp = []
  const okNames = []
  for (const row of Array.isArray(rows) ? rows : []) {
    if (row.kind === 'mcp') {
      if (!row.ok) missingMcp.push(findTool(row.id) || row)
      continue
    }
    if (row.ok) { okNames.push(String(row.name || row.id || '?')); continue }
    missing.push(findTool(row.id) || row)
  }
  return { missing, missingMcp, okNames }
}
/**
 * 一键安装请求文本（2026-11-24 用户令：工具管理加「一键安装」，点一下
 * **一个对话**装完所有未安装工具）。与 buildToolRequestText 同源约定：
 * 通道实测 + 每工具建议命令（或 blocked 指引）+ 验证 + 复检闭环，agent
 * 零上下文可执行。批量语义三条增量：① 清单顺序串行执行（包管理器锁
 * 冲突）；② 单工具失败不阻塞后续（跳过继续，末尾统一汇报）；③ 末尾只
 * 做**一次**等效复检（不是每工具一次，也不是会话内代跑不了的
 * /ctf-tools status——官方复检由用户在工具面板点「快检」完成）。
 * 止损纪律与认证边界硬停同 buildToolRequestText 第 5 条（session-
 * 8f0176a7 实锤：不可达目标 + 无止损 = 11 分钟死磕）。
 */
function buildToolsInstallAllText(missing, pm, { okNames = [], missingMcp = [] } = {}) {
  const channelLine = pm === 'choco' ? 'choco（已检出）'
    : pm === 'winget' ? 'winget（未检出 choco；2026-11-25 实测 winget 源有包：nmap/tshark(Wireshark)/exiftool，其余工具无包）'
    : '未检出任何包管理器（choco / winget 都不在 PATH）'
  const lines = []
  lines.push(`请在本机一次性安装以下 ${missing.length} 个 CTF 工具（在当前这一个对话里逐个完成，不要另开对话）。`)
  lines.push('')
  lines.push(`本机通道实测：${channelLine}。`)
  lines.push('')
  lines.push('待安装清单（按顺序执行；包管理器命令串行跑，避免并发锁冲突）：')
  let blockedSeen = false
  missing.forEach((tool, i) => {
    const action = resolveToolAction(tool, 'install', pm)
    lines.push('')
    lines.push(`${i + 1}. ${tool.name}（id：${tool.id}）`)
    if (action.blocked) {
      blockedSeen = true
      lines.push('   自动通道不可用，请按以下指引处理：')
      for (const g of String(action.guidance || '').split('\n')) lines.push(`   ${g}`)
    } else {
      lines.push(`   建议执行（PowerShell）：${action.line}`)
    }
    const verifyNote = /where\.exe/i.test(tool.probe)
      ? `${tool.probe}，能显示版本的话再跑一次版本命令`
      : `${tool.probe}（python 生态，验证 import 即可）`
    lines.push(`   装完验证：${verifyNote}`)
    const cands = Array.isArray(tool.paths) ? tool.paths.filter(Boolean) : []
    if (cands.length) {
      lines.push(`   常见安装位置（where.exe 未命中时逐个用绝对路径验证）：`)
      for (const p of cands) lines.push(`   - ${p}`)
    }
  })
  if (blockedSeen) {
    lines.push('')
    lines.push('注：多个工具的指引都指向「安装 Chocolatey」时，choco 只需安装一次，装完后对上述每个工具补跑 choco install <id> -y。')
  }
  lines.push('')
  if (okNames.length) lines.push(`以下工具快检已可用，无需处理：${okNames.join('、')}。`)
  if (missingMcp.length) lines.push(`${missingMcp.map((t) => t.name).join('、')} 为 MCP 类工具，请在 MCP Manager 页面手动配置，不在本次自动安装范围。`)
  lines.push('')
  lines.push('要求：')
  lines.push('1) 按清单顺序逐个安装：每个工具完成后先跑「装完验证」命令，通过再进入下一个。')
  lines.push('2) 如实执行并报告输出：失败不要盲目重试超过 2 次，把原始错误完整贴出；某个工具失败不要阻塞其余工具，跳过并继续，最后统一汇报。')
  lines.push('3) 若验证时找不到命令（新装工具只写注册表 PATH，当前进程是旧快照）：改用每个工具列出的常见安装位置做绝对路径验证——绝对路径能执行即视为已装好、可用，如实汇报并建议用户把该目录加入 PATH 或重启 DSH；不要谎报失败。')
  lines.push('4) 全部处理完后做一次等效复检（每个工具：where.exe / 绝对路径 / 版本命令任一命中即 ok），逐项汇报 ok 状态与总结论。注意：/ctf-tools status 是 GUI 斜杠命令，会话内无法代跑、也不要尝试代跑；官方复检由用户在工具管理面板点「快检」完成。')
  lines.push('5) 止损纪律：任一工具的任一子目标连续失败 3 次即跳过该项，绝不死磕；任何接口返回 401/403 或要求浏览器凭据都是设计认证边界——立即停止尝试、如实汇报，严禁寻找/复用/绕过认证凭据（不挖 cookie/token，不硬闯网关）。')
  return lines.join('\n')
}
/**
 * 单行快检（2026-11-20 ② 多形态回退链，治「装了但漏检」）：
 *   1) 主 probe（where.exe / python import）——PATH 命中即绿；
 *   2) paths 绝对路径存在性（fs.existsSync，零开销不依赖 PATH）——官方 zip
 *      解压型（hashcat/john）与装好未进 PATH 的场景在此兜住，detail 如实
 *      标注「不在 PATH」并给加入 PATH/重启的指引；
 *   3) alt 备用命令（binwalk 的 python import 形态）——pip 装进 python 但
 *      Scripts 目录不在 PATH 时 where.exe 永远找不到，import 才是真话。
 *   全链未命中 → ok:false + 主 probe 的消毒输出。
 */
async function toolsStatusRow(tool) {
  if (tool.kind === 'mcp') {
    const dir = process.env.PROTOCOLMCP_DIR
    return { id: tool.id, name: tool.name, kind: tool.kind, ok: Boolean(dir), detail: dir ? `配置目录：${dir}` : '未配置（在 MCP Manager 中添加 protocol-mcp）' }
  }
  const r = await shellRun(tool.probe, { timeoutMs: 15000 })
  if (r.code === 0) {
    return { id: tool.id, name: tool.name, kind: tool.kind, ok: true, detail: sanitizeText(r.out || '(PATH 命中)', 120) }
  }
  const candidates = Array.isArray(tool.paths) ? tool.paths : []
  for (const p of candidates) {
    if (existsSync(p)) {
      return { id: tool.id, name: tool.name, kind: tool.kind, ok: true, detail: `已安装：${p}（不在 PATH；加入 PATH 或重启 DSH 后快检直接命中）` }
    }
  }
  // alt 探测失败原因不丢弃：全链未命中时附到 detail 尾部——「装了但探测失败」
  // 时 alt 为何失败（ParserError/超时/解释器崩）直接可见，不再只回显主 probe 输出。
  let altErr = ''
  if (tool.alt) {
    // 调用运算符 &：PS 语言规则下字符串字面量后直接跟参数会 ParserError
    // （"C:\…\python.exe" -c "…" 形态）。alt 已显式带 & 时原样执行（调用方
    // 自管调用形态，避免叠加成 &&），裸命令与带引号路径两种写法都兼容。
    const altLine = /^\s*&/.test(tool.alt) ? tool.alt : `& ${tool.alt}`
    const r2 = await shellRun(altLine, { timeoutMs: 15000 })
    if (r2.code === 0) {
      return { id: tool.id, name: tool.name, kind: tool.kind, ok: true, detail: `已安装：备用探测命中（${tool.alt}）` }
    }
    altErr = String(r2.err || r2.out || '').trim()
  }
  const miss = sanitizeText(r.out || r.err || '(probe 无输出)')
  return { id: tool.id, name: tool.name, kind: tool.kind, ok: false, detail: altErr ? `${miss}｜alt 探测亦未命中：${sanitizeText(altErr, 90)}` : miss }
}
//#endregion

//#region tool definition
function toolDescription() {
  return [
    'Run the local CTFPilot engine for CTF challenges (authorized competitions / local practice only).',
    'Fast deterministic path solves encoding chains, Caesar, XOR, gzip/zip unpacking and PNG LSB stego in seconds with zero LLM calls; the deep channel injects top-3 relevant case cards (signal-scored) and re-retrieves every 4 steps / 2 failures; flags identical to original challenge files are rejected as raw-echo and do not count.',
    'Commands: solve <challenge_dir> (auto routes fast then deep; --mode fast for known-trivial encodings; write_trace saves a replayable trace json on success AND failure),',
    'recall [--category] [--query <signal keywords>] (query past experience, ranked by signal relevance — pass magic numbers / protocol names / technique names e.g. "NTRU lattice LLL"; case cards carry evidence pointers: drill down into the writeup source when a card lacks detail, its source is a failed case, two cards conflict, or the previous action failed),',
    'learn-writeup <file> (ingest a writeup into versioned skill cards + checkpoints; parses template fields, tags, and failure paths),',
    'rebuild-knowledge <writeups_dir> (batch rebuild the case-card knowledge base from a directory of writeups — 4-format parser, idempotent, safe to rerun after adding writeups),',
    'eval-retrieval [writeups_dir] (retrieval regression gate — MANDATORY after changing retrieval/cards: top1/top3/MRR vs baseline, FAIL when drifting beyond 5pp; --save-baseline stores a new baseline after intentional improvements),',
    'stats (global bookkeeping), selftest (verify the engine install, isolated memory), fixations --min N (techniques repeatedly used/succeeded — hard-coding candidates, frequency from the trajectory store),',
    'bench-pull --source <local:dir|gh-org:CTF-Archives|gh:owner/repo> --dest <dir> [--tag t] (pull one unused challenge from a local pool or GitHub CTF-Archives repo/org into a working dir, with a _bench_ledger.json pull ledger; legacy --pool <dir> equals --source local:<dir>)',
    'dashboard (generate interactive HTML dashboard from SQLite trajectory data — open in browser)',
    'eval-suite [--save-baseline] (six-dimension regression gate vs baseline — smoke floor + knowledge non-shrink are hard gates, retrieval ±5pp; run MANDATORY after changing detectors/knowledge; --save-baseline records intentional improvements)',
    'rsi [action cycle|list|approve|reject|adopt + --pid] (self-improvement ring: engine PROPOSES, human + eval-suite ADMIT — proposals never auto-mutate detectors/cards/prompts; adopting a proposal forces a follow-up eval-suite)',
    'arena [action register|pull|record|stats] (practice arena: data-driven difficulty tiers easy/normal/hard + win-rate board; attempts live in a SEPARATE ledger — training data never pollutes production recall/lessons)',
    'research [action add|evidence|ingest|list] (offline-honest study queue: add writes a writeup-aligned draft; ingest requires >=2 evidence pointers AND the draft parsing against the learner grammar; the engine never invents knowledge)',
    'Typical loop: recall --category <cat> --query <signals> -> solve --mode auto --write-trace -> if NOT SOLVED, analyze the engine output/steps attached to the tool result and continue yourself -> learn-writeup when a path works -> rebuild-knowledge after adding writeups; after any detector/card change run eval-suite. Prefer recall before deep diving; solve before re-implementing base64/caesar/xor/zip logic by hand.',
  ].join(' ')
}

/** 模型可见的引擎原始输出节选（2026-11-24 实锤修复）。
 *  render 是模型唯一可见通道：value.output 不拼进渲染文本时，recall 的
 *  案例卡、solve 的尝试明细、rsi/arena/research 的台账流水对模型永远是
 *  黑屏——工具描述里所有「见 value.output」的指引全部落空（session-
 *  2193d69d 实锤：模型只收到一行 "recall ok — see value.output"，真实
 *  召回内容不可见，白烧一步）。
 *  头 70% + 尾 30%：引擎输出的关键内容在头部（lessons/卡片清单）与
 *  尾部（[result] 结论行），中段长列表可牺牲。默认上限 2600 字符——
 *  预设 tool-result-pruner 阈值 8192（头 4096 尾 1024），节选拼入渲染
 *  文本后仍在阈内，不会被会话侧二次剪裁。 */
function renderExcerpt(output, max = 2600) {
  const s = String(output == null ? '' : output).trim()
  if (!s) return ''
  if (s.length <= max) return s
  const headN = Math.floor(max * 0.7)
  const tailN = Math.floor(max * 0.3)
  return `${s.slice(0, headN)}\n…[render 节选：省略中段 ${s.length - headN - tailN} 字符]…\n${s.slice(-tailN)}`
}

function renderResult(value) {
  const output = value.output || ''
  switch (value.command) {
    case 'solve':
      if (value.solved) {
        return `SOLVED in ${value.elapsed || '?'}s — ${value.flag || '(flag not printed)'}`
      }
      const steps = Array.isArray(value.steps) ? value.steps : []
      // 单次遍历统计 fast/deep（原实现两次 filter，各分配一个数组）。
      let fastN = 0
      for (const s of steps) if (s.phase === 'fast') fastN++
      const deepN = steps.length - fastN
      const hint = steps.length > 0
        ? `${steps.length} 步尝试（fast=${fastN}, deep=${deepN}）`
        : '引擎无确定性路径'
      return `NOT SOLVED${value.elapsed ? ` in ${value.elapsed}s` : ''} — ${hint}。引擎尝试明细（继续分析前先看这里，再 recall 同题型经验）：\n${renderExcerpt(output, 1800) || '(引擎未产出明细)'}`
    case 'eval-retrieval': {
      const pass = /RESULT\s*:\s*PASS/.test(output)
      const m = /signal\s+top1=([\d.]+%?) top3=([\d.]+%?)/.exec(output)
      const scores = m ? `top1=${m[1]} top3=${m[2]}` : 'no scores parsed'
      return pass
        ? `eval-retrieval PASS — ${scores}（检索质量在基线容差内）`
        : `eval-retrieval FAIL — ${scores}。检索回归超容差（意外回退需查检索/卡片变更；故意改进后可 --save-baseline 更新基线），drift 细节：\n${renderExcerpt(output, 1600)}`
    }
    case 'eval-suite': {
      const pass = /RESULT\s*:\s*PASS/.test(output)
      const smoke = /\bsmoke set: (\d+)\/(\d+) solved\b/.exec(output)
      return pass
        ? `eval-suite PASS — 能力地板保持（smoke ${smoke ? smoke[1] + '/' + smoke[2] : '?'}），六维指标全部在容差内`
        : `eval-suite FAIL — 有指标回归（smoke floor 或知识不退等；故意改进后可 --save-baseline 记新基线），per-metric 明细：\n${renderExcerpt(output, 1600)}`
    }
    case 'rsi': {
      const cycleDone = /cycle done/i.test(output)
      return `${value.command} ${cycleDone ? '引擎已产出待审提案（人审后 adopt 会强制跟进 eval-suite）' : '提案状态机已更新'}。提案明细：\n${renderExcerpt(output, 1600) || '(无输出)'}`
    }
    case 'arena': {
      const tiers = /tiers? recomputed/.test(output) ? '，数据驱动分级已重算（练兵数据独立于生产轨迹）' : ''
      return `${value.command} 竞技台账动作完成${tiers}。明细：\n${renderExcerpt(output, 1600) || '(无输出)'}`
    }
    case 'research': {
      if (/INGEST BLOCKED/.test(output)) {
        return `research 入库被双门拦截：需 ≥2 个证据指针 + 草稿通过 writeup-learner 结构校验。离线诚实：引擎不编知识。明细：\n${renderExcerpt(output, 1600)}`
      }
      return `${value.command} 研究队列状态已更新（证据指针 / 状态流转）。明细：\n${renderExcerpt(output, 1600) || '(无输出)'}`
    }
    default:
      return `${value.command} ok。引擎输出：\n${renderExcerpt(output) || '(引擎未产出输出)'}`
  }
}

function commandWireName(args) {
  switch (args.command) {
    case 'solve': return `ctfpilot solve ${args.challenge ?? ''} ${args.mode ? '(' + args.mode + ')' : ''}`.trim()
    case 'learn-writeup': return `ctfpilot learn-writeup ${args.writeup ?? ''}`
    case 'rebuild-knowledge': return `ctfpilot rebuild-knowledge ${args.writeups_dir ?? ''}${args.dry_run ? ' (dry-run)' : ''}`.trim()
    default: return `ctfpilot ${args.command}`
  }
}

async function registerCtfpilot(ctx, config = {}) {
  // Normalize config: cordis validates via exported Config before apply(), but
  // direct programmatic calls pass raw objects, so defaults live here too.
  const cfg = {
    python: config.python || '',
    engineDir: config.engineDir || '',
    timeoutSeconds: config.timeoutSeconds ?? 300,
    maxOutputChars: config.maxOutputChars ?? 8000,
  }
  ctx.tools.register({
    name: 'ctfpilot',
    description: toolDescription(),
    parameters: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'Engine subcommand.' },
        challenge: { type: 'string', description: 'solve: challenge directory (absolute path).' },
        mode: { type: 'string', description: 'solve: engine mode. fast = deterministic detectors only; deep = strategy ladder; auto = fast then deep. Default auto.' },
        write_trace: { type: 'boolean', description: 'solve: write _ctfpilot_trace.json for replay/retro. Default false.' },
        flag_regex: { type: 'string', description: 'solve: extra flag pattern for non-flag{} formats.' },
        category: { type: 'string', description: 'Category filter (crypto|web|reverse|misc|forensics|pwn|osint): recall --category; arena register/record category; research add category (default misc).' },
        query: { type: 'string', description: 'recall: signal keywords (magic numbers / protocol names / key filenames / technique names) that rank case cards by relevance, e.g. "NTRU lattice LLL" or "pcap Modbus".' },
        writeup: { type: 'string', description: 'learn-writeup: path to the markdown writeup file.' },
        name: { type: 'string', description: 'Challenge / writeup / arena name (context-dependent).' },
        writeups_dir: { type: 'string', description: 'rebuild-knowledge / eval-retrieval: directory of writeup markdown files (e.g. the ctf-20min output dir).' },
        dry_run: { type: 'boolean', description: 'rebuild-knowledge: parse and report without writing anything. Default false.' },
        save_baseline: { type: 'boolean', description: 'eval-retrieval: store current scores as the new baseline (after intentional improvements). Default false.' },
        min: { type: 'number', description: 'fixations: minimum hit count filter. Default from engine.' },
        out_html: { type: 'string', description: 'dashboard: output HTML file path (default: CTFPilot-看板.html in engineDir)' },
        source: { type: 'string', description: 'bench-pull: source spec — local:<pool_dir>, gh-org:<org> (e.g. gh-org:CTF-Archives), or gh:<owner>/<repo>. Legacy --pool <dir> is equivalent to local:<dir>.' },
        pool: { type: 'string', description: 'bench-pull (legacy): local challenge pool dir (one challenge per subdirectory); same as --source local:<dir>.' },
        dest: { type: 'string', description: 'bench-pull / arena pull: destination directory; pulled challenges plus the _bench_ledger.json ledger land here.' },
        tag: { type: 'string', description: 'bench-pull: optional batch tag recorded in the ledger.' },
        action: { type: 'string', description: 'Subaction: rsi = cycle|list|approve|reject|adopt; arena = register|pull|record|stats; research = add|evidence|ingest|list. Omit eval-suite.' },
        pid: { type: 'string', description: 'rsi: proposal id (from rsi list) to approve / reject / adopt.' },
        topic: { type: 'string', description: 'research: topic title for add, or topic to attach evidence / ingest.' },
        tier: { type: 'string', description: 'arena register/record: difficulty tier override (easy|normal|hard); stats recomputes data-driven tiers.' },
        pointer: { type: 'string', description: 'research evidence: source pointer (local file path / writeup / URL). Minimum 2 pointers gate ingest.' },
        kind: { type: 'string', description: 'research evidence: pointer kind — local-file | url | writeup. Default local-file.' },
        status: { type: 'string', description: 'rsi list filter: proposed|approved|rejected|adopted. research list filter: queued|drafting|ready-ingest|ingested.' },
        note: { type: 'string', description: 'rsi approve/reject/adopt: free-text note recorded in the ledger history.' },
        fail: { type: 'boolean', description: 'arena record: outcome was a failure (default solve).' },
        fast: { type: 'boolean', description: 'arena record: solved via fast path only (feeds difficulty recompute).' },
        limit: { type: 'number', description: 'rsi/research list: max rows. Default 50.' },
      },
      required: ['command'],
      additionalProperties: false,
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          command: { type: 'string' },
          solved: { type: 'boolean' },
          flag: { type: 'string' },
          elapsed: { type: 'string' },
          summary: { type: 'string' },
          output: { type: 'string' },
          category: { type: 'string' },
          confidence: { type: 'number' },
          evidence_count: { type: 'number' },
          steps: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                phase: { type: 'string' },
                name: { type: 'string' },
                detail: { type: 'string' },
                success: { type: 'boolean' },
                step: { type: 'number' },
                strategy: { type: 'string' },
                result: { type: 'string' },
                error_class: { type: 'string' },
              },
            },
          },
        },
      },
      render: (_args, value) => [{ type: 'text', text: renderResult(value) }],
    },
    async execute(args, exec) {
      if (COMMANDS.indexOf(args.command) < 0) {
        throw new Error(`unknown command ${JSON.stringify(args.command)}; expected one of: ${COMMANDS.join(', ')}`)
      }
      if (args.command === 'solve' && !args.challenge) {
        throw new Error('solve requires `challenge`: absolute path to the challenge directory')
      }
      if (args.command === 'learn-writeup' && !args.writeup) {
        throw new Error('learn-writeup requires `writeup`: path to the markdown writeup file')
      }
      if (args.command === 'rebuild-knowledge' && !args.writeups_dir) {
        throw new Error('rebuild-knowledge requires `writeups_dir`: directory of writeup markdown files')
      }
      if (args.command === 'bench-pull' && !args.dest) {
        throw new Error('bench-pull requires `dest`: destination directory for pulled challenges and the _bench_ledger.json ledger')
      }
      const engineDir = resolveEngine(cfg.engineDir)
      const python = cfg.python || resolvePython(engineDir)
      const argv = buildArgs(args)
      const { code, stdout, stderr } = await runEngine(argv, {
        python,
        engineDir,
        timeoutMs: cfg.timeoutSeconds * 1000,
        maxOutputChars: cfg.maxOutputChars,
        signal: exec.signal,
      })
      const value = parseEngineResult(stdout)
      value.command = args.command
      value.output = truncate(value.output, cfg.maxOutputChars)
      // Engine prints its own errors distinctly; non-zero exit without a
      // [result] line means infrastructure failure (bad path, missing python pkg).
      // Exception: eval-retrieval / eval-suite report a domain-level regression
      // FAIL with exit 1 — normal values, not infrastructure errors.
      if (code !== 0 && !RESULT_LINE.test(stdout) && !/^\[eval-retrieval\]/m.test(stdout) && !/^\[eval-suite\]/m.test(stdout)) {
        throw new Error(`engine exited ${code}: ${truncate(stderr || stdout || '(no output)', 500)}`)
      }
      // 写账本子命令成功：作废看板快照（mtime 签名本会自动失效，这里是
      // 写入后立即读取也拿新数据的即时通道——双保险，见 engineDashboard）。
      if (WRITE_COMMANDS.has(args.command)) invalidateDashboardSnapshot()
      return value
    },
    presentCall(args) {
      return { card: 'terminal', title: commandWireName(args), description: 'CTFPilot engine (local, offline)' }
    },
    presentResult(args, { isError, content }) {
      const value = typeof content === 'object' ? content : {}
      // Build a readable process timeline for the UI card.
      // Phases from the engine [meta] line: fast / deep-pre / exec / plan.
      const PHASE_LABEL = { fast: '快通道', 'deep-pre': '深预检', exec: '深执行', plan: '规划', deep: '深通道' }
      const timeline = (value.steps || []).map(s => {
        const label = PHASE_LABEL[s.phase] || s.phase || '?'
        if (s.phase === 'fast' || s.phase === 'deep-pre') {
          const icon = s.success ? '[✓]' : '[ ]'
          return `${icon} ${label}/${s.name}: ${(s.detail || '').slice(0, 120)}`
        }
        // exec / plan / deep steps
        const icon = s.result === 'success' || s.result === 'solved' ? '[✓]'
          : s.result === 'failed' ? '[!]' : '[·]'
        return `${icon} ${label}/${s.step ? 'step' + s.step + ' ' : ''}${s.strategy || s.name}: ${s.error_class || s.result || '...'}`
      }).filter(Boolean).join('\n')

      const extra = timeline ? `\n\n── 过程时间线 ──\n${timeline}` : ''
      const base = typeof content === 'string' ? content : JSON.stringify(content, null, 0)
      const rawOutput = base.slice(0, 4000) + extra
      return {
        card: 'terminal',
        title: commandWireName(args),
        rawOutput,
        exit: { code: isError ? 1 : 0 },
      }
    },
  })

  // ------------------------------------------------------------------
  // submit-confirmation gate: model proposes a submit; a human card
  // under the answer confirms before anything reaches the ledger.
  // ------------------------------------------------------------------

  // The plugin cannot import zod (not installed next to it); the registry
  // only duck-types `.parse()` on the schemas, and the wire payloads are
  // built by hand from session events below, so a minimal shim is enough.
  const zod = {
    object: fields => ({ parse: v => v }),
    string: () => ({ parse: v => v }),
    number: () => ({ parse: v => v }),
    boolean: () => ({ parse: v => v }),
    array: item => ({ parse: v => v }),
    record: (key, value) => ({ parse: v => v }),
    union: parts => ({ parse: v => v }),
    null: () => ({ parse: v => v }),
    literal: () => ({ parse: v => v }),
  }

  /** Parse tool arguments JSON (best-effort; the fold treats parse errors as unadmitted). */
  function parseArguments(raw) {
    try { return JSON.parse(String(raw || '')) } catch { return null }
  }

  ctx.tools.register({
    name: 'propose_submit',
    description: [
      'Submit one confirmed flag to CTFPilot for human-verified accounting.',
      'Call this when you hold a flag the user should confirm before it is recorded into the engine ledger.',
      'After the call, a confirmation card appears below your answer; the user confirms there before anything is recorded.',
      'Do NOT print the flag again in prose afterwards — the card carries it.',
      'Parameters: flag (the flag string, required), title (short card heading), brief (one-line description of what was solved),',
      'category (one of crypto/web/reverse/misc/forensics/pwn/osint), challenge (challenge name/path for the ledger row).',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        flag: { type: 'string', description: 'The flag string to submit.' },
        title: { type: 'string', description: 'Short card heading (e.g. "Crypto Easy").' },
        brief: { type: 'string', description: 'One-line description of what was solved.' },
        category: { type: 'string', description: 'Category: crypto|web|reverse|misc|forensics|pwn|osint.' },
        challenge: { type: 'string', description: 'Challenge name or path, used for the ledger row.' },
      },
      required: ['flag', 'title', 'brief', 'category'],
      additionalProperties: false,
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          title: { type: 'string' },
          invitation: { type: 'string' },
        },
      },
      render: (_args, value) => [{ type: 'text', text: 'Card shown below your answer. Do not restate it. ' + value.invitation }],
    },
    async execute(args) {
      // NOTE: do NOT session.append tool/call or tool/result here — the tool
      // executor batch logs both around this body (batch.ts start/commit);
      // duplicate entries would corrupt the log. The fold below reads the
      // framework-logged events; nothing else is needed for admission.
      const title = String(args.title || '提交确认')
      return {
        action: 'submit',
        title,
        invitation: 'The user confirms the flag via the card below before it is recorded.',
      }
    },
    presentCall(args) {
      const flagPart = args.flag ? ` ${String(args.flag).slice(0, 60)}` : ''
      return { card: 'terminal', title: 'propose_submit · ' + (args.title || ''), description: 'propose a submit ' + flagPart }
    },
    presentResult(args, { isError, content }) {
      const value = typeof content === 'object' ? content : {}
      return {
        card: 'terminal',
        title: 'propose_submit · ' + (args.title || ''),
        rawOutput: `${value.invitation || ''}`,
        exit: { code: isError ? 1 : 0 },
      }
    },
  })

  // Session projection unit keyed `ctfpilot`: live proposals folded from
  // tool/call + tool/result, confirmed rows folded from command/run + done.
  // The browser half reads it through useProjection('ctfpilot'). Guarded:
  // a non-web profile may not expose the registry.
  if (ctx.sessionProjections && typeof ctx.sessionProjections.register === 'function') {
    await ctx.sessionProjections.register({
      key: 'ctfpilot',
    stateSchema: zod.object({}),
    stateVersion: 1,
    init: () => ({ live: {}, confirmed: [] }),
    apply: (state, event) => {
      switch (event.type) {
        case 'tool/call': {
          // The executor logs this BEFORE execute runs (batch.ts start), with
          // the model's raw arguments — folding it admits the proposal.
          const d = event.data || {}
          if (d.name !== 'propose_submit') return state
          const args = parseArguments(d.arguments)
          if (!args || !args.flag) return state
          const id = String(d.callId)
          const cur = state.live[id]
          if (!cur || !cur.proposal) {
            const entry = { callId: id, turn: d.turn, title: String(args.title || '提交确认'),
              brief: String(args.brief || ''), flag: String(args.flag), category: String(args.category || ''),
              challenge: String(args.challenge || '') }
            const live = Object.assign({}, state.live)
            live[id] = { proposal: entry }
            return { live, confirmed: state.confirmed }
          }
          return state
        }
        case 'command/run': {
          // The human gate executes /ctf-record-win; its lifecycle events are
          // folded into the confirmed ledger that drives card settlement.
          // Logged name = the slash command name (dsh-commands:375).
          const d = event.data || {}
          if (d.name !== 'ctf-record-win') return state
          const line = String(d.args || '')
          const flagVal = optArg(line, 'flag')
          if (!flagVal) return state
          const catVal = optArg(line, 'category') || ''
          const nameVal = optArg(line, 'name') || ''
          const at = event.seq ?? 0
          const confirmed = [].concat(state.confirmed)
          let found = false
          for (const c of confirmed) {
            if (c.flag === flagVal && c.category === catVal) { c.at = at; found = true }
          }
          if (!found) {
            confirmed.push({ flag: flagVal, category: catVal, name: nameVal, at })
          }
          return { live: state.live, confirmed }
        }
        default:
          return state
      }
    },
    wire: {
      viewSchema: zod.object({}),
      view: s => ({
        proposals: Object.fromEntries(
          Object.entries(s.live || {})
            .filter(([, r]) => r && r.proposal)
            .map(([callId, r]) => [callId, r.proposal])),
        confirmed: s.confirmed || [],
      }),
    },
  })

  // 正在解题状态板（界面 2）：投影是【按会话】单元——官方 API：init(header)
  // 每会话调一次、apply(state, event) 喂该会话的原始事件；会话事件从不带
  // sessionId（也没必要带）。2026-11-25 用户实锤「正在解题界面的卡片不能
  // 正确读取到会话结束」的根因就在这里：旧实现以 event.sessionId 为门槛，
  // 折叠恒为空 → /ctf-solving-state 永远返回空 sessions → 卡片的 running
  // 永远不会随 turn/end 翻 false。同源的第二个 bug：flag 扫描只读
  // data.text / data.content，而真实事件正文在 data.message.content——
  // flag 也从未被折叠出来。现语义：
  //   state = 单会话 {mode, label, flags, flagsCount}（不再全局多会话镜像）；
  //   running 一律以官方 turnBoundary 投影为准（agents.turnBoundaryOf /
  //   sessionProjections.stateOf(session,'turnBoundary')，openTurnStartSeq
  //   非 null = 运行中），本投影不再自折叠 running。
  // stateVersion 保持 1：旧检查点形状 {sessions:{...}} 经恒等 parse 后由
  // apply/view 的默认值自愈，无需版本迁移抛错。
  if (ctx.sessionProjections && typeof ctx.sessionProjections.register === 'function') {
    try {
      solveProjHandle = await ctx.sessionProjections.register({
        key: 'ctfsolve',
        stateSchema: zod.object({}),
        stateVersion: 1,
        init: (header) => {
          // header 是会话启动头（含 agentPreset）：直接恢复模式，不等事件。
          const preset = String((header && header.agentPreset) || '')
          const m = preset === 'ctf-quick' ? 'quick' : preset === 'ctf-deep' ? 'deep' : ''
          return { mode: m, label: m ? modeLabel(m) : '', flags: [], flagsCount: [] }
        },
        apply: (state, event) => {
          const prev = state && typeof state === 'object' ? state : {}
          let mode = typeof prev.mode === 'string' ? prev.mode : ''
          let label = typeof prev.label === 'string' ? prev.label : ''
          let flags = Array.isArray(prev.flags) ? prev.flags : []
          let flagsCount = Array.isArray(prev.flagsCount) ? prev.flagsCount : []
          let changed = false

          if (event.type === 'command/run') {
            const d = event.data || {}
            if (d.name === 'ctf-solve-start') {
              const a = parseSolveStartArgs(String(d.args || ''))
              const m = MODES[a.mode] ? a.mode : 'deep'
              if (mode !== m) { mode = m; label = modeLabel(m); changed = true }
            }
          }
          // 网关通道的解题启动不写 command/run（这正是走数据网关的原因），
          // 但预设切换会落 agent-preset/selected 事件——同样能恢复模式口径。
          if (event.type === 'agent-preset/selected') {
            const preset = String((event.data && event.data.agentPreset) || '')
            const m = preset === 'ctf-quick' ? 'quick' : preset === 'ctf-deep' ? 'deep' : ''
            if (m && mode !== m) { mode = m; label = modeLabel(m); changed = true }
          }
          if (event.type === 'assistant/message' || event.type === 'tool/result') {
            // 正文在 data.message.content（官方 v4 事件形态）；兼容字符串与
            // 旧形态 data.text/data.content。
            const d = event.data || {}
            const msg = d.message && typeof d.message === 'object' ? d.message : null
            const content = (msg && Array.isArray(msg.content)) ? msg.content
              : (Array.isArray(d.content) ? d.content : null)
            const text = typeof d.text === 'string' ? d.text
              : content ? content.map((c) => (c && typeof c.text === 'string') ? c.text : '').join('')
                : ''
            if (text) {
              let cloned = false
              const ensureFlags = () => {
                if (cloned) return
                flags = flags.slice()
                flagsCount = flagsCount.slice()
                cloned = true
              }
              for (const f of scanFlags(text)) {
                const i = flags.indexOf(f)
                if (i < 0) { ensureFlags(); flags.push(f); flagsCount.push(1); changed = true }
                else if ((flagsCount[i] || 1) < 99) { ensureFlags(); flagsCount[i] = (flagsCount[i] || 1) + 1; changed = true }
              }
            }
          }
          if (!changed) return state
          return { mode, label, flags, flagsCount }
        },
        wire: {
          viewSchema: zod.object({}),
          view: (s) => {
            const st = s && typeof s === 'object' ? s : {}
            return {
              mode: typeof st.mode === 'string' ? st.mode : '',
              label: typeof st.label === 'string' ? st.label : '',
              flags: Array.isArray(st.flags) ? st.flags : [],
              flagsCount: Array.isArray(st.flagsCount) ? st.flagsCount : [],
            }
          },
        },
      })
      // 变更镜像：onChanged(session, key, value, seq) —— 维护模块级镜像，
      // 供无参轮询（旧客户端）与未显式传 sid 的会话兜底展示。
      if (typeof ctx.sessionProjections.onChanged === 'function') {
        ctx.sessionProjections.onChanged((session, key, value) => {
          if (key !== 'ctfsolve' || !session || !session.id) return
          solvingState = { sessions: { ...solvingState.sessions, [String(session.id)]: value } }
        })
      }
    } catch { /* HMR 重复激活：保留首个注册 */ }
  }
  }

  // Human commands (browser-invocable): the confirmation card executes
  // `/ctf-record-win --name ... --category ... --flag ...` through
  // ctx.remote.commands.execute; `/ctf-dashboard` feeds the sidebar panel.
  // Guarded like the projection above — non-web profiles lack the registry.
  function engineRoot() {
    return resolveEngine(config.engineDir || '')
  }

  /* 看板快照：接入通用签名缓存层（见模块级 signedCache）。失效由「数据源签名」
   * 驱动（官方投影/缓存模式——数据源不变则缓存永远有效，变了立即失效），替代
   * 旧的 15s TTL。引擎 dashboard 的唯一数据源是账本 SQLite（build_dashboard_data
   * 全部查询落在这一个库），一切写入（solve/learn/record-win/rsi/arena）都改文件
   * mtime+size → 签名完备。实测全量聚合约 1.8-2s（13 万行 attempts），面板反复
   * 打开不应反复付出这笔冷启动。 */

  /** 作废看板快照，下次读取重新查询引擎。 */
  function invalidateDashboardSnapshot() {
    cacheDel('dashboard')
  }

  async function engineDashboard() {
    const dir = engineRoot()
    // 取样在跑引擎之前：若引擎执行期间账本被并发写入，签名记录的是旧值，
    // 下次读取签名变化 → 自动重查，不会把并发写入后的陈旧数据长期缓存。
    const sig = ledgerSig(dir)
    const hit = cacheGet('dashboard', sig)
    if (hit !== undefined) return hit
    const python = config.python || resolvePython(dir)
    const { stdout } = await runEngine(['dashboard', '--json'], {
      python, engineDir: dir, timeoutMs: 30000, maxOutputChars: 500000,
    })
    let blob
    try {
      blob = JSON.parse(stdout)
    } catch {
      // 新数据解析失败时保留旧快照（若有），避免把可用页面打崩。
      // 注意 cacheGet('dashboard', sig) 在本次调用开头已确认 MISS（同签名
      // 再查仍 MISS 是恒真），必须直接取最近一次快照的 value。
      const stale = signedCache.get('dashboard')
      if (stale !== undefined) return stale.value
      throw new Error('engine dashboard JSON parse failed')
    }
    cacheSet('dashboard', sig, blob)
    return blob
  }

  // 命令 handler 注册表（2026-11-20）：命令通道与 HTTP 数据网关共用同一批
  // 包装后 handler——网关按 <cmd> 名取用，零逻辑重复，错误日志双通道同源。
  const commandHandlerRegistry = new Map()

  if (ctx.commands && typeof ctx.commands.register === 'function') {
  // 命令注册统一包装：**吞错降级禁用**（2026-11-19 用户令）。任何命令返回
  // {kind:'error'} 或直接抛异常，都自动落盘 ctfpilot-errors.log（error 上屏
  // 之外的第二通道）；抛异常再补一条 error 结果上屏——失败必须显形，绝不静默。
  // via 标记区分来源：'command'（会话命令通道，command/run+done 落会话日志）/
  // 'gateway'（HTTP 数据网关，零会话事件）——错误日志里一眼看出失败在哪条通道。
  const registerCommand = (def) => {
    const wrapped = async (args) => {
      const label = `cmd:${def.name || '(unnamed)'}`
      const via = args && args.via === 'gateway' ? 'gateway' : 'command'
      try {
        const out = await def.handler(args)
        if (out && out.kind === 'error') {
          logError(engineRoot(), label, new Error(String(out.text || '(error without text)')), via === 'gateway' ? 'gateway-error-result' : 'error-result')
        }
        return out
      } catch (err) {
        logError(engineRoot(), label, err, via === 'gateway' ? 'gateway-throw' : 'throw')
        return { kind: 'error', text: `${def.name} 内部异常：${String((err && err.message) || err)}` }
      }
    }
    commandHandlerRegistry.set(def.name, wrapped)
    return ctx.commands.register({ ...def, handler: wrapped })
  }

  registerCommand({
    name: 'ctf-record-win',
    description: 'Record a user-confirmed solve into the CTFPilot engine ledger.',
    input: { hint: '--name N --category C --flag F [--mode M] [--summary S]' },
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const name = optArg(line, 'name')
      const cat = optArg(line, 'category')
      const flag = optArg(line, 'flag')
      if (!name || !cat || !flag) {
        return { kind: 'error', text: 'ctf-record-win 需要 --name、--category 和 --flag；卡片会自动补齐。' }
      }
      const argv = ['record-win', '--name', name, '--category', cat, '--flag', flag]
      const mode = optArg(line, 'mode')
      if (mode) argv.push('--mode', mode)
      const sumRe = line.match(/--summary\s+(?:"([^"]*)"|(.+))\s*$/)
      if (sumRe) argv.push('--summary', (sumRe[1] !== undefined ? sumRe[1] : sumRe[2]).trim().replace(/["']+$/g, ''))
      const engineDir = engineRoot()
      const python = config.python || resolvePython(engineDir)
      try {
        await runEngine(argv, { python, engineDir, timeoutMs: 120000, maxOutputChars: 8000, signal })
        invalidateDashboardSnapshot()
        return { kind: 'success', text: `已记入引擎账本：${name}（${cat}）— ${flag}` }
      } catch (err) {
        return { kind: 'error', text: `记录失败：${err.message || err}` }
      }
    },
  })

  registerCommand({
    name: 'ctf-dashboard',
    description: 'Load the CTFPilot engine dashboard data (challenges, detectors, lessons) as JSON.',
    handler: async () => {
      try {
        const blob = await engineDashboard()
        return { kind: 'success', text: JSON.stringify(blob) }
      } catch (err) {
        return { kind: 'error', text: `CTFPilot 数据读取失败：${err.message || err}` }
      }
    },
  })

  // 单题详情：元数据 + 尝试时间线 + 该题经验，供侧栏详情页渲染。
  // 签名缓存：key 按题（id/name），sig 用账本签名（同 dashboard 数据源）——
  // 任何写账本（record-win/solve/learn）都改 mtime → 所有题的详情缓存整体失效，
  // 不需逐题作废；Host 常驻下同一题反复点开（返回看板再进）零引擎冷启动。
  registerCommand({
    name: 'ctf-challenge-detail',
    description: 'Load one challenge\'s full ledger record (metadata, attempt timeline, lessons) as JSON.',
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const idM = line.match(/--id\s+(\d+)/)
      const name = optArg(line, 'name')
      if (!idM && !name) {
        return { kind: 'error', text: 'ctf-challenge-detail 需要 --id N 或 --name NAME' }
      }
      try {
        const dir = engineRoot()
        const sig = ledgerSig(dir)
        const cacheKey = `detail:${idM ? 'id:' + idM[1] : 'name:' + name}`
        const hit = cacheGet(cacheKey, sig)
        if (hit !== undefined) return { kind: 'success', text: hit }
        const python = config.python || resolvePython(dir)
        const argv = ['challenge-detail']
        if (idM) argv.push('--id', idM[1])
        if (name) argv.push('--name', name)
        const { stdout } = await runEngine(argv, {
          python, engineDir: dir, timeoutMs: 15000, maxOutputChars: 200000,
        })
        cacheSet(cacheKey, sig, stdout)
        return { kind: 'success', text: stdout }
      } catch (err) {
        return { kind: 'error', text: `CTFPilot 详情读取失败：${err.message || err}` }
      }
    },
  })

  registerCommand({
    name: 'ctf-stats',
    description: 'Show compact CTFPilot engine statistics.',
    handler: async ({ rawInput, signal }) => {
      const engineDir = engineRoot()
      // 签名缓存：同 dashboard 数据源（账本库），写入必改 mtime 自动失效。
      const sig = ledgerSig(engineDir)
      const hit = cacheGet('stats', sig)
      if (hit !== undefined) return { kind: 'success', text: hit }
      const python = config.python || resolvePython(engineDir)
      try {
        const { stdout } = await runEngine(['stats'], { python, engineDir, timeoutMs: 30000, maxOutputChars: 4000, signal })
        const text = String(stdout || '').trim()
        cacheSet('stats', sig, text)
        return { kind: 'success', text }
      } catch (err) {
        return { kind: 'error', text: `stats 读取失败：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 训练场动作卡（侧栏「训练」标签的后端）：arena / research / rsi /
  // eval-suite 的薄封装。引擎纪律不变：提案由人审、adopt 强制跟进
  // eval-suite、研究入库双门；这里只做参数转发 + 中文摘要。
  // ------------------------------------------------------------------

  // 决策动词全集（含过去分词形态，防调用方传状态词时误吞位置词）。
  const RSI_VERBS = new Set(['cycle', 'list', 'approve', 'reject', 'adopt',
    'approved', 'rejected', 'adopted'])

  /** 引号感知取选项值（flagArg / optArg 共用一套正则语义）：
   *  --key "quoted span" / --key token；flagArg 额外支持 --key=value。
   *  前端 shellArg 对含空格值包双引号发来，旧的 (\S+) 会把引号值截断。 */
  function argValue(line, flag, { allowEq = false, emptyAsNull = false } = {}) {
    const src = String(line || '')
    if (allowEq) {
      const eq = src.match(new RegExp(`${flag}=(?:"([^"]*)"|(\\S+))`))
      if (eq) return eq[1] !== undefined ? eq[1] : eq[2]
    }
    const m = src.match(new RegExp(`${flag}\\s+(?:"([^"]*)"|(\\S+))`))
    if (!m) return emptyAsNull ? null : ''
    return m[1] !== undefined ? m[1] : m[2]
  }
  /** 从 rawInput 里抽 flag 值：--flag <v> 或 --flag=<v>。 */
  function flagArg(line, name) { return argValue(line, name, { allowEq: true }) }

  /** 被门拦下的动作必须可见：stdout 含 [error] 行、BLOCKED 字样或评估门
   * RESULT: FAIL 时按错误回报，不让拦截结果在会话里伪装成绿色成功。 */
  function outcome(stdout) {
    const text = String(stdout || '').trim()
    const flagged = /(^|\n)\[error\]/.test(text) || /BLOCKED/i.test(text) || /\bRESULT\s*:\s*FAIL\b/.test(text)
    return flagged ? { kind: 'error', text } : { kind: 'success', text }
  }

  /** 引号感知取选项值：--key "quoted span" 或 --key token（含 CJK/连字符）。
   * 前端 shellArg 对含空格值包双引号发来，旧正则 (\S+) 会把引号值截断。 */
  function optArg(line, name) { return argValue(line, `--${name}`, { emptyAsNull: true }) }


  registerCommand({
    name: 'ctf-arena',
    description: 'Practice arena: leaderboard, register / pull / record practice attempts (data separate from production ledger).',
    input: { hint: '--arena stats|register|record|pull [--name N] [--category C] [--tier easy|normal|hard] [--source S] [--dest D] [--fail] [--fast]' },
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const action = ['stats', 'register', 'record', 'pull'].find((a) => line === a || new RegExp(`(^|\\s)${a}(\\s|$)`).test(line)) || ''
      if (!action) {
        return { kind: 'error', text: 'ctf-arena 需要子动作 stats|register|record|pull（例如 `/ctf-arena stats`）' }
      }
      const argv = ['arena', action]
      for (const name of ['name', 'category', 'tier', 'source', 'dest']) {
        const v = flagArg(line, `--${name}`)
        if (v) argv.push('--' + name, v)
      }
      if (/(^|\s)--fail(\s|$)/.test(line)) argv.push('--fail')
      if (/(^|\s)--fast(\s|$)/.test(line)) argv.push('--fast')
      const engineDir = engineRoot()
      // stats 是纯读：签名缓存（registry.json + attempts.jsonl）；写动作
      // （register/record/pull）成功后主动作废，双保险消除同毫秒碰撞窗口。
      const isStats = action === 'stats'
      let sig = null
      if (isStats) sig = arenaSig(engineDir)
      const hit = isStats ? cacheGet('arena-stats', sig) : undefined
      if (hit !== undefined) return outcome(hit)
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 60000, maxOutputChars: 12000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        if (crashed) {
          return { kind: 'error', text: `竞技台账动作失败（${action}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
        }
        const out = outcome(stdout)
        if (out.kind === 'success') {
          if (isStats) cacheSet('arena-stats', sig, stdout)
          else cacheDel('arena-stats') // 写动作成功：作废排行榜缓存
        }
        return out
      } catch (err) {
        return { kind: 'error', text: `竞技台账动作失败（${action}）：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 批量重刷训练场（2026-11-25 用户令：把「重解历史题→自验→记练→更优解法
  // 入库→末尾汇总+RSI 线索」整条训练闭环自动化）。数据源 = 引擎台账已解题
  // （solved 题，自带历史 flag 供自验）；与 install-all 同款注入模式：面板/
  // 斜杠命令 → 新开 CTF-worker 对话 → 切快解预设 → 一条自包含协议注入该
  // 对话，agent 串行逐题执行。Host 绝不代跑引擎写动作——记练/入库全部由
  // agent 在对话里按协议真实执行并如实汇报。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-drill-start',
    description: 'Batch re-drill solved challenges from the engine ledger in ONE quick-mode conversation: re-solve each (engine fast path first), self-verify against the historical flag, record arena drills, learn improved writeups, rebuild knowledge and report at the end.',
    input: { hint: '[--category C] [--name N …] [--limit N] [--dir 题目根目录]  （默认最近 10 道已解题；--limit 0 = 不限）' },
    handler: async ({ agent, rawInput, signal }) => {
      const args = parseDrillArgs(rawInput)
      let blob
      try {
        blob = await engineDashboard()
      } catch (err) {
        return { kind: 'error', text: `读取引擎台账失败，批量重刷未派发：${err.message || err}` }
      }
      const solvedAll = (blob && Array.isArray(blob.challs) ? blob.challs : []).filter((c) => c && c.solved === true)
      let list = solvedAll
      if (args.category) list = list.filter((c) => String(c.cat || '').toLowerCase() === args.category.toLowerCase())
      if (args.names.length) {
        const want = new Set(args.names.map((n) => String(n).trim().toLowerCase()))
        list = list.filter((c) => want.has(String(c.name || '').trim().toLowerCase()))
      }
      const matched = list.length
      if (args.limit > 0) list = list.slice(0, args.limit)
      if (list.length === 0) {
        return { kind: 'error', text: `没有可重刷的已解题（台账已解 ${solvedAll.length} 道，筛选后 ${matched} 道${args.limit > 0 ? `，上限 ${args.limit}` : ''}）。请检查 --category/--name 参数，或先用解题流程积累已解题。` }
      }
      const engineDir = engineRoot()
      const python = config.python || resolvePython(engineDir)
      const engPrefix = `& "${python}" -X utf8 "${path.join(engineDir, 'ctfpilot.py')}"`
      const text = buildDrillRequestText(list, args, engPrefix)
      if (!agent || typeof agent.followup !== 'function') {
        return { kind: 'error', text: `无活动会话 agent，批量重刷请求未能注入。请手动在对话中发送：\n${text}` }
      }
      // 预设切换失败 → error 终止（不注入）——与 solve-start 同款删降级纪律。
      const sw = await switchSolvePresetEx(ctx, agent, PRESETS_BY_MODE.quick)
      if (!sw.ok) {
        const hints = {
          locked: `该会话已开始过对话、预设锁定——面板「批量重刷」新建的对话不受此限`,
          'not-found': `预设 '${PRESETS_BY_MODE.quick}' 未注册到 roster（preset 行没挂上或加载失败）`,
          invalid: `预设组合损坏`,
          'no-service': `agentPresets 服务不可达`,
        }
        const hint = hints[sw.reason] ? hints[sw.reason] : `${sw.reason}`
        return {
          kind: 'error',
          text: `✗ 预设未切到「快解」，批量重刷已终止（不降级注入）。\n原因：${hint}\n详情：${sw.detail || '(no detail)'}\n定位：跑 /ctf-preset-audit 看服务与 roster 状态。\n请求未发送（供核对或手动重发）：\n${text}`,
        }
      }
      const injected = injectAgentMessage(agent, text)
      if (signal && signal.aborted) {
        return { kind: 'error', text: '命令已取消（批量重刷请求可能仍在收件箱，下一轮会执行）。' }
      }
      if (!injected.injected) {
        return { kind: 'error', text: `批量重刷注入失败（followup 被拒）：${injected.error}\n请手动在对话中发送：\n${text}` }
      }
      return { kind: 'success', text: `✓ 会话预设已切到「快解」，批量重刷已注入：本轮 ${list.length} 道已解题（台账共 ${solvedAll.length} 道已解，筛选命中 ${matched} 道${args.limit > 0 && matched > list.length ? `，受 --limit ${args.limit} 截取` : ''}）。清单：${list.map((c) => c.name).join('、')}。\n请切到对话查看逐题执行；agent 会按协议记练、对比历史、更优解法入库，收尾给汇总表与 RSI 线索。\n注入内容（供核对或手动重发）：\n${text}` }
    },
  })

  registerCommand({
    name: 'ctf-eval-suite',
    description: 'Run the six-dimension regression gate (smoke floor + knowledge non-shrink hard; retrieval ±5pp). Optional --save-baseline.',
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const argv = []
      if (/(^|\s)--save-baseline(\s|$)/.test(line)) argv.push('--save-baseline')
      const engineDir = engineRoot()
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(['eval-suite', ...argv], { python, engineDir, timeoutMs: cfg.timeoutSeconds * 1000, maxOutputChars: 20000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        return crashed
          ? { kind: 'error', text: `eval-suite 运行失败：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
          : outcome(stdout)
      } catch (err) {
        return { kind: 'error', text: `eval-suite 运行失败：${err.message || err}` }
      }
    },
  })

  registerCommand({
    name: 'ctf-research',
    description: 'Offline study queue: add topic, attach evidence pointers, ingest route (double gate).',
    input: { hint: '--research add TOPIC | evidence TOPIC --pointer P [--kind local-file|url|writeup] | ingest TOPIC | list [--status queued|drafting|ready-ingest|ingested]' },
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const sub = ['add', 'evidence', 'ingest', 'list'].find((a) => line === a || new RegExp(`(^|\\s)${a}(\\s|$)`).test(line)) || ''
      if (!sub) {
        return { kind: 'error', text: 'ctf-research 需要子动作 add|evidence|ingest|list，例如 `/ctf-research add RSA 分解专题`…' }
      }
      if (sub === 'evidence') {
        // 引擎 --pointer 是单值选项：同一次调用里重复传参会互相覆盖。
        // 逐个指针调用（引擎 register_evidence 每次 +1）并拼接输出。
        const words = line.split(/\s+/).filter(Boolean)
        const topicWords = []
        const pointers = []
        let kind = ''
        for (let i = 0; i < words.length; i++) {
          const w = words[i]
          if (i === 0 && w === 'evidence') continue
          if (w === '--pointer') {
            const v = words[i + 1]
            if (v && !v.startsWith('--')) pointers.push(String(words[++i]).replace(/^["']|["']$/g, ''))
          } else if (w.startsWith('--pointer=')) {
            pointers.push(w.slice(10).replace(/^["']|["']$/g, ''))
          } else if (w === '--kind') {
            const v = words[i + 1]
            if (v && !v.startsWith('--')) kind = String(words[++i])
          } else if (w.startsWith('--kind=')) {
            kind = w.slice(7)
          } else if (w === '--topic') {
            const v = words[++i]
            if (v) topicWords.push(String(v).replace(/^["']|["']$/g, ''))
          } else if (w.startsWith('--topic=')) {
            topicWords.push(w.slice(8).replace(/^["']|["']$/g, ''))
          } else if (!w.startsWith('--')) {
            topicWords.push(w.replace(/^["']|["']$/g, ''))
          }
        }
        const topic = topicWords.join(' ').trim()
        if (!topic || !pointers.length) {
          return { kind: 'error', text: 'ctf-research evidence 需要 TOPIC 和至少一个 --pointer，例如 `/ctf-research evidence RSA专题 --pointer p1.md --pointer p2.md`' }
        }
        const engineDir = engineRoot()
        const python = config.python || resolvePython(engineDir)
        const parts = []
        for (const ptr of pointers) {
          const argvOne = ['research', 'evidence', topic, '--pointer', ptr]
          if (kind) argvOne.push('--kind', kind)
          try {
            const r = await runEngine(argvOne, { python, engineDir, timeoutMs: 60000, maxOutputChars: 12000, signal })
            const crashed = r.code !== 0 && !/^\[/m.test(String(r.stdout || ''))
            if (crashed) {
              return { kind: 'error', text: `研究队列动作失败（evidence）：${String(r.stderr || r.stdout || '(no output)').slice(0, 400)}` }
            }
            parts.push(String(r.stdout || '').trim())
          } catch (err) {
            return { kind: 'error', text: `研究队列动作失败（evidence）：${err.message || err}` }
          }
        }
        const out = outcome(parts.join('\n'))
        if (out.kind === 'success') cacheDel('research-list') // evidence 写入成功：作废队列缓存
        return out
      }
      const argv = ['research', sub]
      // 统一 tokenizer：--flag=val / --flag val 成对；其余非 flag 词归入
      // 自由位置参数（TOPIC），连续词以空格相连、首尾引号剥除。
      const words = line.split(/\s+/).filter(Boolean)
      const topicWords = []
      for (let i = 0; i < words.length; i++) {
        const w = words[i]
        // 子动作词本身不算位置参数（'add'/'evidence' 等由 argv 首元素承担）。
        if (i === 0 && w === sub) continue
        if (!w.startsWith('--')) { topicWords.push(w.replace(/^["']|["']$/g, '')); continue }
        // 引擎 research 用位置参数承载 TOPIC：--topic <v> 的值并入位置，
        // 不作为 --topic 选项名传下去（argparse 无此选项）。
        if (w === '--topic' || w.slice(0, 8) === '--topic=') {
          const v = w === '--topic' ? (words[i + 1] || '').replace(/^["']|["']$/g, '') : w.slice(8)
          if (v) topicWords.push(v)
          if (w === '--topic') i++
          continue
        }
        if (w.includes('=')) {
          argv.push(w.slice(0, w.indexOf('=')), w.slice(w.indexOf('=') + 1))
        } else if (i + 1 < words.length && !words[i + 1].startsWith('--')) {
          argv.push(w, words[i + 1]); i++
        }
      }
      if (topicWords.length) argv.push(topicWords.join(' '))
      const engineDir = engineRoot()
      // list 是纯读：签名缓存（queue.jsonl + topics/ 递归）；写动作
      // （add/ingest）成功后主动作废，双保险消除同毫秒碰撞窗口。
      const isList = sub === 'list'
      let sig = null
      if (isList) sig = researchSig(engineDir)
      const hit = isList ? cacheGet('research-list', sig) : undefined
      if (hit !== undefined) return outcome(hit)
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 60000, maxOutputChars: 12000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        if (crashed) {
          return { kind: 'error', text: `研究队列动作失败（${sub}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
        }
        const out = outcome(stdout)
        if (out.kind === 'success') {
          if (isList) cacheSet('research-list', sig, stdout)
          else cacheDel('research-list') // add/ingest 写动作成功：作废队列缓存
        }
        return out
      } catch (err) {
        return { kind: 'error', text: `研究队列动作失败（${sub}）：${err.message || err}` }
      }
    },
  })

  registerCommand({
    name: 'ctf-rsi',
    description: 'RSI self-improvement ring: run signal cycle, list proposals, approve / reject / adopt by id (adoption forces a follow-up eval-suite run).',
    input: { hint: '--rsi cycle | list [--status proposed|approved|rejected|adopted] | approve|reject|adopt PID [--note "..."]' },
    handler: async ({ rawInput, signal }) => {
      const line = String(rawInput || '').trim()
      const sub = ['cycle', 'list', 'approve', 'reject', 'adopt'].find((a) => line === a || new RegExp(`(^|\\s)${a}(\\s|$)`).test(line)) || ''
      if (!sub) {
        return { kind: 'error', text: 'ctf-rsi 需要子动作 cycle|list|approve|reject|adopt，例如 `/ctf-rsi cycle` 或 `/ctf-rsi list`' }
      }
      const argv = ['rsi', sub]
      if (sub !== 'cycle' && sub !== 'list') {
        // PID：显式 --pid，或动作后第一个【非动词】位置词（'/ctf-rsi adopt rsi-001 --note ...'）。
        // 动词永远排在位置词之前，必须显式跳过——否则会把动词本身当 PID 发进
        // 引擎，引擎回 '[error] no such proposal: <verb>'（生产实锤 20261117：
        // [error] no such proposal: adopt）。数值形状态词（approved 等）不走这里。
        const pid = flagArg(line, '--pid')
        if (pid) {
          argv.push(pid)
        } else {
          const words = line.split(/\s+/).filter(Boolean)
            .filter((w) => !RSI_VERBS.has(w) && !w.startsWith('--'))
          const pos = words[0]
          if (pos) argv.push(pos.replace(/^["']|["']$/g, ''))
        }
      }
      for (const name of ['status']) {
        const v = flagArg(line, `--${name}`)
        if (v) argv.push('--' + name, v)
      }
      const note = flagArg(line, '--note')
      if (note) argv.push('--note', note)
      const engineDir = engineRoot()
      // list 是纯读：签名缓存（state 下 ledger/proposals/version-chain 三文件）；
      // 写动作（cycle/approve/reject/adopt）成功后主动作废——写入必改签名（主
      // 通道），主动删是消除同毫秒 mtime 粒度碰撞的双保险。
      const isList = sub === 'list'
      let sig = null
      if (isList) sig = rsiSig(engineDir)
      const hit = isList ? cacheGet('rsi-list', sig) : undefined
      if (hit !== undefined) return outcome(hit)
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 120000, maxOutputChars: 20000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        if (crashed) {
          return { kind: 'error', text: `RSI 提案流动作失败（${sub}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
        }
        const out = outcome(stdout)
        if (out.kind === 'success') {
          if (isList) cacheSet('rsi-list', sig, stdout)
          else cacheDel('rsi-list') // cycle/决策类写动作成功：作废提案流缓存
        }
        return out
      } catch (err) {
        return { kind: 'error', text: `RSI 提案流动作失败（${sub}）：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 界面 1：开始解题 —— 先把会话预设切到所选模式（quick/deep，真实
  // agent 预设差异），再注入纯题面文本（无 brief 前缀）。
  // 2026-11-19 用户令「删光吞错降级」：预设切换失败 = **终止**，不再
  // 「按当前预设口径推进」继续注入——用错预设假装修题就是降级伪装成功。
  // 每种失败原因如实分类上屏，并经 registerCommand 包装层落盘错误日志。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-solve-start',
    description: 'Start a solve in the current session: switch its agent preset (quick/deep), then inject body + target + attachment paths as the next user message.',
    input: { hint: 'quick|deep [--category C] [--target 靶机地址] [--attach P …] 题目描述文字' },
    handler: async ({ agent, rawInput, signal }) => {
      const args = parseSolveStartArgs(rawInput)
      const modeOk = MODES[args.mode] ? args.mode : 'deep'
      const label = modeLabel(modeOk)
      const composed = solvePromptText(args)
      if (!agent || typeof agent.followup !== 'function') {
        return { kind: 'error', text: `无活动会话 agent，题面未能注入。请手动发送：\n${composed.text}` }
      }
      // 预设切换失败 → error 终止（不注入）。reason 对齐官方 RemoteError code。
      const sw = await switchSolvePresetEx(ctx, agent, PRESETS_BY_MODE[modeOk])
      if (!sw.ok) {
        const hints = {
          locked: `该会话已开始过对话、预设锁定——面板「开始解题」新建的对话不受此限`,
          'not-found': `预设 '${PRESETS_BY_MODE[modeOk]}' 未注册到 roster（preset 行没挂上或加载失败）`,
          invalid: `预设组合损坏`,
          'no-service': `agentPresets 服务不可达`,
        }
        const hint = hints[sw.reason] ? hints[sw.reason] : `${sw.reason}`
        return {
          kind: 'error',
          text: `✗ 预设未切到「${label}」，本次解题已终止（不降级注入）。\n原因：${hint}\n详情：${sw.detail || '(no detail)'}\n定位：跑 /ctf-preset-audit 看服务与 roster 状态；跑 /ctf-logs 看错误日志。\n题面未发送（供核对或手动重发）：\n${composed.text}`,
        }
      }
      const injected = injectAgentMessage(agent, composed.text)
      // 注入后命令被取消：followup 无法撤回（官方 inbox 契约），
      // 消息仍会作为下一轮执行，如实告知。
      if (signal && signal.aborted) {
        return { kind: 'error', text: '命令已取消（题面消息可能仍在收件箱，下一轮会执行）。' }
      }
      if (!injected.injected) {
        return { kind: 'error', text: `题面注入失败（followup 被拒）：${injected.error}\n请手动发送：\n${composed.text}` }
      }
      return { kind: 'success', text: `✓ 会话预设已切到「${label}」，题面已注入并开始执行（${composed.text.length} 字符）。\n原始题面（供核对或手动重发）：\n${composed.text}` }
    },
  })

  // ------------------------------------------------------------------
  // 界面 1b：预设切换诊断 —— 一次性实弹探针。上屏四件事：
  //   ① agentPresets 服务可达性；② roster 全量行（含 broken 与注册状态）；
  //   ③ ctf-quick / ctf-deep 逐个 resolve；④ 当前会话 turnBoundary 锁定态。
  // 背景（2026-11-19）：solve-start 曾把所有切换失败折叠成「会话锁定」
  // 一句假话，真故障（preset 行未挂上 not-found / 服务不可达）被掩盖。
  // 失败原因已改分类上屏（switchSolvePresetEx），本命令用于一眼定位。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-preset-audit',
    description: "Diagnose agent-preset switching: service reachability, roster rows, ctf preset resolve, and this session's turn boundary.",
    input: { hint: '(no input)' },
    handler: async ({ agent }) => {
      const lines = []
      const svc = agentPresetsService(ctx)
      lines.push(`service: ${svc ? 'reachable' : 'unreachable (absent on ctx and ctx.root)'}`)
      if (svc && typeof svc.list === 'function') {
        try {
          const rows = await svc.list()
          lines.push(`roster(${rows.length}): ${rows.map((r) => `${String(r && r.id)}${r && r.broken ? ' [broken: ' + String(r.broken).slice(0, 100) + ']' : ''}`).join(', ')}`)
        } catch (err) {
          lines.push(`roster: list failed: ${String((err && err.message) || err).slice(0, 200)}`)
        }
      } else {
        lines.push('roster: n/a (no list method)')
      }
      for (const preset of Object.values(PRESETS_BY_MODE)) {
        if (svc && typeof svc.resolve === 'function') {
          try {
            const row = await svc.resolve(preset)
            lines.push(`resolve(${preset}): ok${row && row.broken ? ` [broken: ${String(row.broken).slice(0, 140)}]` : ''}`)
          } catch (err) {
            lines.push(`resolve(${preset}): ${String((err && err.code) || 'error')} ${String((err && err.message) || err).slice(0, 200)}`)
          }
        } else {
          lines.push(`resolve(${preset}): n/a (svc unreachable)`)
        }
      }
      let boundaryText = 'turnBoundary: n/a (no live agent in this context)'
      if (agent && agent.session && ctx.sessionProjections && typeof ctx.sessionProjections.stateOf === 'function') {
        try {
          const b = ctx.sessionProjections.stateOf(agent.session, 'turnBoundary')
          boundaryText = `turnBoundary: ${JSON.stringify(b)}（locked = openTurnStartSeq 非 null 或 lastTurn > 0）`
        } catch (err) {
          boundaryText = `turnBoundary: read failed: ${String((err && err.message) || err).slice(0, 160)}`
        }
      }
      lines.push(boundaryText)
      return { kind: 'success', text: lines.join('\n') }
    },
  })

  // ------------------------------------------------------------------
  // 界面 1c：错误日志查看 —— registerCommand 包装层把每条命令错误落盘
  // 到 <engineDir>/ctfpilot-errors.log；本命令 tail / clear，失败无处可藏。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-logs',
    description: 'Read or clear the ctfpilot error log (every command error is appended there by the registration wrapper).',
    input: { hint: '[tail [N] | clear]  （默认 tail 30 条）' },
    handler: async ({ rawInput }) => {
      const line = String(rawInput || '').trim()
      const dir = engineRoot()
      const file = path.join(dir, ERROR_LOG_NAME)
      const fallback = path.join(os.tmpdir(), ERROR_LOG_NAME)
      const m = /^tail(?:\s+(\d+))?$/.exec(line)
      if (m) {
        const n = m[1] ? Math.max(1, Math.min(200, parseInt(m[1], 10))) : 30
        for (const f of [file, fallback]) {
          if (existsSync(f)) {
            const text = readFileSync(f, 'utf8').trimEnd()
            if (!text) return { kind: 'success', text: `${f}\n（空）` }
            const parts = text.split('\n')
            return { kind: 'success', text: `${f}\n${parts.slice(-n).join('\n')}` }
          }
        }
        return { kind: 'success', text: `${file}\n（尚无错误记录）` }
      }
      if (line === 'clear') {
        let cleared = 0
        for (const f of [file, fallback]) {
          try { if (existsSync(f)) { rmSync(f, { force: true }); cleared++ } } catch { /* 逐个尝试 */ }
        }
        return { kind: 'success', text: `已清除 ${cleared} 个日志文件。` }
      }
      return { kind: 'error', text: 'ctf-logs 用法：`/ctf-logs`（tail 30）| `/ctf-logs tail 100` | `/ctf-logs clear`' }
    },
  })

  // ------------------------------------------------------------------
  // 界面 1d：建工作区目录 —— 面板层1 用它绕开 directoryPicker。
  // 背景（2026-11-19 实锤）：directoryPicker 在 Windows 上永远路由 browse
  // 交互对（dsh-host-directory-picker-auto resolve.ts：platform!=='linux' ⇒
  // 'browse'），其 capability 需要 client surface 反向驱动，插件经
  // uiWorkspace.listDirectory/createDirectory 调用时链接不上（报
  // "directoryPicker.list needs the browse capability"，层1 从未成功）。
  // Host 进程本来就有 fs：直接 mkdir，返回绝对路径给 workspaces.create()
  // （官方契约：create 要求已存在的绝对路径）。
  // 2026-11-20 需求更新（用户令）：不再按时间戳建新工作区——全部解题对话
  // 都固定住在唯一的 CTF-worker 工作区里（客户端先查 workspaces.list，
  // 已存在直接复用；本命令只负责首次把它建出来）。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-ensure-workspace',
    description: 'Ensure the fixed CTF-worker workspace directory exists on the Host filesystem (fs.mkdir, bypasses directoryPicker); returns {dir} for workspaces.create().',
    input: { hint: '--dir "C:\\…\\deepseek-harness\\CTF-worker"' },
    handler: async ({ rawInput }) => {
      const dirArg = optArg(String(rawInput || ''), 'dir')
      const dir = typeof dirArg === 'string' && dirArg.trim() ? dirArg.trim() : ''
      if (!dir) {
        return { kind: 'error', text: 'ctf-ensure-workspace 需要 --dir 绝对路径（面板自动传入）。' }
      }
      if (!path.isAbsolute(dir)) {
        return { kind: 'error', text: `ctf-ensure-workspace 拒绝相对路径：${dir}` }
      }
      // 防呆：只允许建固定的 CTF-worker 目录，避免任意路径创建。
      if (!/[\\/]CTF-worker$/i.test(dir)) {
        return { kind: 'error', text: `ctf-ensure-workspace 路径形态不符（须为 …\\CTF-worker）：${dir}` }
      }
      try {
        mkdirSync(dir, { recursive: true })
        return { kind: 'success', text: JSON.stringify({ dir }) }
      } catch (err) {
        return { kind: 'error', text: `建目录失败：${dir} :: ${String((err && err.message) || err)}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 界面 2：实时状态板轮询源 —— 返回 {sessions: {sid: state}} JSON。
  // 轮询请求可带 args=<sid1,sid2,…>（客户端把本地看板里的会话 id 全带上）：
  // Host 逐会话读两路权威源——
  //   running          ← 官方 turnBoundary 投影：openTurnStartSeq 非 null =
  //                      回合进行中；turn/end 后必为 null。这是「卡片读不到
  //                      会话结束」的修复核心（2026-11-25 实锤：旧实现的
  //                      自折叠以不存在的 event.sessionId 为门槛，恒空）。
  //   mode/label/flags ← 本插件按会话的 ctfsolve 投影（修复后的事件折叠）。
  // 无 args 时退回模块级镜像（旧客户端兼容；镜像由 onChanged 维护）。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-solving-state',
    description: 'Return the live solving-board snapshot (per-session mode / running / append-only flags) as JSON. Optional comma-separated session ids as args are resolved against the authoritative turn-boundary projection.',
    input: { hint: '[sid1,sid2,…]（客户端看板已知的会话 id；省略返回镜像）' },
    handler: async ({ rawInput }) => {
      try {
        const ids = String(rawInput || '').split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
        const fresh = {}
        for (const sid of ids) {
          try {
            const session = liveSessions && typeof liveSessions.get === 'function' ? liveSessions.get(sid) : undefined
            if (!session) {
              // 会话不在 Host 活动表（已关闭/未打开）：不可能存在进行中的
              // 回合，如实回 running=false（mode/flags 沿用镜像兜底）——
              // 否则看板上被关掉的旧会话会永远停在「运行中」幽灵态。
              const mir = (solvingState.sessions || {})[sid] || {}
              fresh[sid] = {
                mode: typeof mir.mode === 'string' ? mir.mode : '',
                label: typeof mir.label === 'string' ? mir.label : '',
                running: false,
                flags: Array.isArray(mir.flags) ? mir.flags : [],
                flagsCount: Array.isArray(mir.flagsCount) ? mir.flagsCount : [],
              }
              continue
            }
            let proj = null
            if (solveProjHandle && typeof solveProjHandle.stateOf === 'function') {
              try { proj = solveProjHandle.stateOf(session) } catch { proj = null }
            }
            const st = proj && typeof proj === 'object' ? proj : {}
            // running 权威读：agents.turnBoundaryOf(session)；服务缺席时退回
            // sessionProjections.stateOf(session, 'turnBoundary')（同一官方
            // 投影，preset-audit 同款读法）。读失败一律按「未在运行」呈现，
            // 绝不因单会话异常拖垮整板。
            let running = false
            try {
              const tb = liveAgents && typeof liveAgents.turnBoundaryOf === 'function'
                ? liveAgents.turnBoundaryOf(session)
                : (ctx.sessionProjections && typeof ctx.sessionProjections.stateOf === 'function'
                  ? ctx.sessionProjections.stateOf(session, 'turnBoundary')
                  : null)
              if (tb && typeof tb === 'object') {
                running = tb.openTurnStartSeq !== null && tb.openTurnStartSeq !== undefined
              }
            } catch { running = false }
            fresh[sid] = {
              mode: typeof st.mode === 'string' ? st.mode : '',
              label: typeof st.label === 'string' ? st.label : '',
              running,
              flags: Array.isArray(st.flags) ? st.flags : [],
              flagsCount: Array.isArray(st.flagsCount) ? st.flagsCount : [],
            }
          } catch { /* 单会话读失败不拖垮整板 */ }
        }
        const merged = { sessions: { ...(solvingState.sessions || {}), ...fresh } }
        if (Object.keys(fresh).length > 0) solvingState = merged
        return { kind: 'success', text: JSON.stringify(merged) }
      } catch (err) {
        return { kind: 'error', text: `读取解题状态失败：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 界面 2：已解出按钮确认后 —— 终止活动轮，保留日志与队列，
  // 在同一会话上发一条 steer 消息请模型写 writeup 归档学习闭环。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-writeup',
    description: 'After marking solved: stop the active round, then on the same conversation ask the model to summarize into a writeup (learn-writeup + rebuild-knowledge).',
    input: { hint: '[--name "挑战名"]' },
    handler: async ({ agent, rawInput, signal }) => {
      const name = optArg(String(rawInput || ''), 'name') || ''
      // 仅当确有进行中的回合才 cancel（2026-11-25 用户实锤「已解出不写
      // writeup」根因）：对空闲 agent 先 cancel 会把驱动推入「活动取消」
      // 阶段，官方 runtime-types 明确「cancellation or disposal may discard
      // pending steering」——紧随其后的 steer 被丢弃，writeup 请求永远发不动。
      // turnBoundary 读不到时（无 agents 服务/桩环境）保持旧行为（先取消）。
      const turnRunning = (() => {
        try {
          if (!(liveAgents && typeof liveAgents.turnBoundaryOf === 'function' && agent && agent.session)) return true
          const tb = liveAgents.turnBoundaryOf(agent.session)
          return Boolean(tb && tb.openTurnStartSeq !== null && tb.openTurnStartSeq !== undefined)
        } catch { return true }
      })()
      let stopped = false
      // agent.cancel(cause, options) 为同步 void（runtime-types.ts 契约）：
      // 不再 await；keepInbox 保留队列与 steering 收件。
      if (agent && typeof agent.cancel === 'function' && turnRunning) {
        try { agent.cancel({ kind: 'user' }, { keepInbox: true }); stopped = true } catch { stopped = false }
      }
      if (signal && signal.aborted) return { kind: 'error', text: '已取消。' }
      const dir = path.join(resolveEngine(config.engineDir || ''), 'writeups')
      const ask = [
        `本轮「${name || '挑战'}」已解出。请把本次解题过程归档为 writeup：`,
        `1) 写到 ${dir}\\<挑战名>.md（目录不存在先创建；内容用标准模板：分类/载体/识别信号/核心技术链/有效技术/陷阱/可迁移模式/结果）。`,
        '2) 完成后调用 ctfpilot learn-writeup "<该文件路径>" --name "<挑战名>"，再 ctfpilot rebuild-knowledge "' + path.dirname(dir) + '"。',
      ].join('\n')
      // followup 而非 steer（2026-11-25 实锤）：官方语义 followup =「排队一个
      // 普通后续回合并唤醒驱动」，空闲/取消后都必然开新回合；steer 面向
      // 运行中回合的下一步，空闲时可能停放、取消后可能被丢弃。
      const steered = injectAgentMessage(agent, ask)
      if (!steered.injected) {
        return { kind: 'error', text: `ctf-writeup 注入失败：${steered.error || 'agent 不存在或不可注入'}` }
      }
      return { kind: 'success', text: `✓ 活动轮${stopped ? '已终止（上下文保留）' : '无活动轮'}，已在同会话请求 writeup 归档。目标目录：${dir}` }
    },
  })

  // ------------------------------------------------------------------
  // 界面 2b：未解出后「继续解题」—— 2026-11-25 用户方案定稿。
  // fork 出的新会话不能切换 agent 预设（回合历史被继承 → 预设锁定），
  // 也无法正确接续原对话（上下文/工具轨迹断裂）；因此放弃 fork 迁移，
  // 改为：在原会话直接 followup 开一个新回合，让模型带着全部上下文续攻。
  // 不切预设、不 fork、不动活动轮（入口处客户端已保证运行中不可点）。
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-continue',
    description: 'Continue an unfinished solve in the SAME conversation: inject a follow-up turn that keeps all context and asks the agent to press on with a fresh strategy (no preset switch, no fork).',
    input: { hint: '[--category C] [--target 靶机地址] [--attach P …] 题目描述文字' },
    handler: async ({ agent, rawInput, signal }) => {
      const args = parseSolveStartArgs(rawInput)
      const body = args.text.join(' ').trim()
      const info = []
      if (body) info.push(`题面：${body}`)
      if (args.category) info.push(`题型：${args.category}`)
      if (args.target) info.push(`靶机地址（用户声明已授权的靶标）：${args.target}`)
      if (args.attaches.length) info.push(`附件路径：${args.attaches.join('; ')}`)
      const ask = [
        '上一轮未解出，现在在同一会话继续解题：上下文、已跑轨迹和工具输出全部保留，不换预设、不重开对话。',
        '请接着上一轮的进度推进，不要从头重做已完成的工作：',
        '1) 先用一两句话回顾上一轮已确认的结论（开放端口/入口/已试路径/卡点），作为续攻基线；',
        '2) 针对卡点换策略：同一路径最多再试一次，仍不行就换攻击面（换协议/换入口/换工具/读源码比对/换字典口径/检查编码与隐写）；',
        '3) 扫描与爆破保持有界（先 -F/常用端口，再按需全端口；注意单命令超时上限，临近超时就拆批或后台化）；',
        '4) 每步结论以真实命令输出为依据，禁止编造；',
        '5) 拿到候选 flag 后走 propose_submit 人审卡片确认，确认后才计入台账。',
      ]
      if (info.length) ask.push('', '题目信息（续攻定位用）：', ...info)
      const text = ask.join('\n')
      // followup（排队新回合并唤醒驱动）而非 steer：本命令用于「回合已结束、
      // 用户点未解出后重启一轮」——steer 面向运行中回合，空闲时会被停放甚至丢弃。
      const injected = injectAgentMessage(agent, text)
      if (!injected.injected) {
        return { kind: 'error', text: `继续解题未注入：${injected.error || 'agent 不存在或不可注入'}\n请手动发送：\n${text}` }
      }
      if (signal && signal.aborted) {
        return { kind: 'error', text: '命令已取消（续攻消息可能仍在收件箱，下一轮会执行）。' }
      }
      return { kind: 'success', text: `✓ 已在同一会话开新回合继续解题（${text.length} 字符）。\n注入内容（供核对或手动重发）：\n${text}` }
    },
  })

  // ------------------------------------------------------------------
  // 界面 3：工具管理。目录稳定——卸载后行仍在，只弹安装按钮。
  // 2026-11-20 用户方案改造：装/更/卸不再由 Host 直接跑包管理器（实测硬跑
  // 坑多：winget 卸载器弹 GUI 不静默、装完 PATH 只写注册表常驻进程看不见、
  // 无 choco 必败且错误 ANSI 乱码），改为注入请求给 agent 代跑——模型能看
  // 真实输出、自适应处理、错误在对话里天然可读。面板点击时 client 会先
  // 开新对话再路由本命令（与「开始解题」同拍），此处只负责注入。
  // status 快检与 mcp 指引保持原样。
  // ------------------------------------------------------------------
  // 工具探针快照：探针结果反映系统 PATH 状态，无文件签名可用 → 短 TTL。
  // 30s 内重复挂载直接回快照（探针串行 spawnSync 实测 0.5-2s，反复打开
  // 工具页不应反复付出）；`--fresh` 跳过（面板刷新按钮用）；装/更/卸注入
  // 成功后作废——agent 复检必须拿新状态，否则刚装完复检命中旧快照会误报。
  let toolsStatusSnapshot = null // { atMs: number, text: string }
  const TOOLS_STATUS_TTL_MS = 30_000
  // ------------------------------------------------------------------
  registerCommand({
    name: 'ctf-tools',
    description: 'Manage CTF tooling: status probe; install-all hands every missing tool to the conversation agent in ONE request (one conversation installs everything); install / update / uninstall delegate per tool to the agent (choco / winget fallback / pip), which runs the package manager and reports back. Uninstalled tools stay listed.',
    input: { hint: 'status | install-all | install <id> | update <id> | uninstall <id>' },
    handler: async ({ agent, rawInput, signal }) => {
      const words = String(rawInput || '').trim().split(/\s+/).filter(Boolean)
      const verb = ['status', 'install-all', 'install', 'update', 'uninstall'].find((v) => words.includes(v)) || ''
      if (!verb) {
        return { kind: 'error', text: 'ctf-tools 需要动词 status|install-all|install|update|uninstall，例如 `/ctf-tools status`、`/ctf-tools install nmap`，或 `/ctf-tools install-all`（一键安装全部未装工具）' }
      }
      if (verb === 'status') {
        // TTL 快照命中：--fresh 跳过（面板刷新按钮强制重探）。
        const fresh = /(^|\s)--fresh(\s|$)/.test(String(rawInput || ''))
        if (!fresh && toolsStatusSnapshot !== null &&
            Date.now() - toolsStatusSnapshot.atMs < TOOLS_STATUS_TTL_MS) {
          return { kind: 'success', text: toolsStatusSnapshot.text }
        }
        // 12 项探针并行（串行 spawnSync 最坏 12×15s，面板标签会卡死）。
        // spawnSync 本身同步阻塞事件循环，并行化受限于其实现；每项
        // 15s 超时上限仍在，最坏总耗时 = 最慢一项而非累加。
        try {
          // ① 注册表 PATH 合并（「装了但漏检」根修复）：安装器只写注册表，
          // Host 进程是启动快照——不合并，新装工具永远探测不到。必须在
          // 探针与包管理器判定之前执行（node 子进程继承 env，实测立即生效）。
          const merged = mergeRegistryPath()
          const rows = []
          for (const t of TOOLS) rows.push(toolsStatusRow(t))
          const settled = await Promise.all(rows)
          // 头部带安装通道行（client 从首个 '[' 起解析 JSON，此行不影响解析）。
          const pm = resolvePkgManager()
          const pmLine = pm === 'choco' ? 'choco'
            : pm === 'winget' ? 'winget（未检测到 choco；nmap/tshark(Wireshark)/exiftool 有 winget 包，sqlmap/john/hashcat/binwalk/radare2 无包，点装会给指引）'
            : '未检测到（choco/winget 都不在 PATH；点安装会给指引）'
          const pathLine = merged.ok
            ? `PATH 合并：注册表并入 ${merged.added} 条新条目`
            : `PATH 合并失败：${merged.error}（探测沿用进程旧快照）`
          const text = `安装通道：${pmLine}\n${pathLine}\n工具快检（共 ${settled.length} 项）：\n` + JSON.stringify(settled, null, 2)
          toolsStatusSnapshot = { atMs: Date.now(), text }
          return { kind: 'success', text }
        } catch (err) {
          return { kind: 'error', text: `工具快检失败：${err.message || err}` }
        }
      }
      // ----------------------------------------------------------------
      // install-all（2026-11-24 用户令：工具管理「一键安装」——点一下，一个
      // 对话装完所有未安装工具）。缺装集以 Host 侧实探为准（面板行有 30s
      // TTL/本地缓存滞后，不可作为依据）：先合并注册表 PATH（用户刚装好的
      // choco 立即可见），全套件并行快检，再合并成**一条**自包含请求注入
      // 当前会话 agent——批量纪律（串行、失败不阻塞、末尾统一复检）写在
      // 请求文本里，由 agent 在这一个对话里串行代跑。绝不真实执行安装。
      // ----------------------------------------------------------------
      if (verb === 'install-all') {
        const extras = words.filter((w) => w !== verb && !w.startsWith('--'))
        if (extras.length) {
          return { kind: 'error', text: 'install-all 不接受工具 id：它自动安装全部未安装的工具；要装单个工具请用 `/ctf-tools install <id>`。' }
        }
        try {
          mergeRegistryPath()
          const probeRows = TOOLS.map((t) => toolsStatusRow(t))
          const settled = await Promise.all(probeRows)
          const { missing, missingMcp, okNames } = resolveInstallAllTargets(settled)
          if (missing.length === 0) {
            const mcpNote = missingMcp.length
              ? `\n${missingMcp.map((t) => t.name).join('、')} 为 MCP 类工具，请在 MCP Manager 页面手动配置。`
              : ''
            return { kind: 'success', text: `✓ 全部 CTF 工具快检已就绪，无需一键安装。${mcpNote}` }
          }
          const pm = resolvePkgManager()
          const requestText = buildToolsInstallAllText(missing, pm, { okNames, missingMcp })
          if (!agent || typeof agent.followup !== 'function') {
            return { kind: 'error', text: `无活动会话 agent，一键安装请求未能注入。请手动在对话中发送：\n${requestText}` }
          }
          const injected = injectAgentMessage(agent, requestText)
          if (signal && signal.aborted) {
            return { kind: 'error', text: '命令已取消（一键安装请求可能仍在收件箱，下一轮会执行）。' }
          }
          if (!injected.injected) {
            return { kind: 'error', text: `✗ install-all 注入失败（followup 被拒）：${injected.error}\n请手动在对话中发送：\n${requestText}` }
          }
          // 注入成功即作废探针快照：agent 复检必须拿装后的新状态（同单工具纪律）。
          toolsStatusSnapshot = null
          return { kind: 'success', text: `✓ 已在当前对话请求 agent 一键安装 ${missing.length} 个工具（${missing.map((t) => t.id).join('、')}，通道：${pm}）。请切到对话查看执行进展；agent 会做等效复检（where.exe / 绝对路径 / 版本命令），官方 /ctf-tools status 快检由你在工具管理面板点「快检」完成。\n注入内容（供核对或手动重发）：\n${requestText}` }
        } catch (err) {
          return { kind: 'error', text: `一键安装快检失败：${err.message || err}` }
        }
      }
      const idWord = words.find((w) => w !== verb && !w.startsWith('--'))
      if (!idWord) {
        return { kind: 'error', text: `ctf-tools ${verb} 需要目标 id，例如 \`/ctf-tools ${verb} nmap\`（可选 id：nmap tshark scapy sqlmap dirsearch john hashcat binwalk exiftool volatility3 pwntools radare2 protocol-mcp）` }
      }
      const tool = findTool(idWord)
      if (!tool) {
        return { kind: 'error', text: `未知工具 id ${idWord}。可选：nmap tshark scapy sqlmap dirsearch john hashcat binwalk exiftool volatility3 pwntools radare2 protocol-mcp；一键安装全部未装工具用 \`/ctf-tools install-all\`` }
      }
      if (tool.kind === 'mcp') {
        return { kind: 'success', text: `${tool.name} 为 MCP 类工具，请在 MCP Manager 页面${verb === 'install' ? '添加' : verb === 'update' ? '重配' : '移除'} protocol-mcp 服务器；本命令不代为操作。` }
      }
      // 注入模式：构造自包含请求文本（通道实测 + 建议命令 + 验证 + 复检闭环），
      // followup 交给会话 agent 代跑（与 ctf-solve-start 同链路）。
      // ① 先合并注册表 PATH 再判通道：用户按指引刚装好 choco（只写了注册表），
      // 下一次点安装立即可见（包管理器探测对失败不缓存）。
      mergeRegistryPath()
      const pm = resolvePkgManager()
      const requestText = buildToolRequestText(tool, verb, pm)
      if (!agent || typeof agent.followup !== 'function') {
        return { kind: 'error', text: `无活动会话 agent，${TOOL_VERB_ZH[verb]}请求未能注入。请手动在对话中发送：\n${requestText}` }
      }
      const injected = injectAgentMessage(agent, requestText)
      if (signal && signal.aborted) {
        return { kind: 'error', text: `命令已取消（${TOOL_VERB_ZH[verb]}请求可能仍在收件箱，下一轮会执行）。` }
      }
      if (!injected.injected) {
        return { kind: 'error', text: `✗ ${verb} 注入失败（followup 被拒）：${injected.error}\n请手动在对话中发送：\n${requestText}` }
      }
      // 注入成功：作废探针快照——agent 完成后会做等效复检（where.exe /
      // 绝对路径 / 版本命令），官方 /ctf-tools status 快检由用户在工具
      // 面板点击完成（会话内无法代跑）。命中旧快照会把刚装好的工具误报为未装。
      toolsStatusSnapshot = null
      return { kind: 'success', text: `✓ 已在当前对话请求 agent ${TOOL_VERB_ZH[verb]} ${tool.name}（通道：${pm}）。请切到对话查看执行进展；agent 会做等效复检（where.exe / 绝对路径 / 版本命令），官方 /ctf-tools status 快检由你在工具管理面板点「快检」完成。\n注入内容（供核对或手动重发）：\n${requestText}` }
    },
  })

  }

  // ------------------------------------------------------------------
  // HTTP 数据网关（2026-11-20 用户令：对话里只允许解题上下文）。
  // 官方命令通道 dsh-commands.execute() 无静默开关：每执行一次必写
  // command/run + command/done 两个 log-only 事件，结果全文进
  // command/done——面板拉一次 24KB 看板就会永久落进会话日志并折成
  // 对话卡片。面板↔Host 的数据与动作全部改走 webServer 命名路由
  // /ctfpilot/data/<cmd>：复用上面注册表里的同一批 handler（零逻辑
  // 重复），认证挂官方 connection.admit（与 /api 同款 Host/Origin 栅栏
  // + 浏览器 owner cookie），agent 按 ?sid= 经 ctx.agents 解析（与
  // 会话命令通道同源语义）。手动 slash 命令保留注册不变——会话里的
  // 命令卡片从此只来自用户手敲。
  // GET 白名单 = 纯读命令；其余（写账本/装工具/解题动作）一律 POST。
  // ------------------------------------------------------------------
  const GATEWAY_GET = new Set(['ctf-dashboard', 'ctf-challenge-detail', 'ctf-stats', 'ctf-solving-state', 'ctf-logs', 'ctf-preset-audit'])
  const GATEWAY_BODY_LIMIT = 1024 * 1024
  if (typeof ctx.inject === 'function') {
    ctx.inject(['webServer', 'connection', 'agents', 'sessions'], (webCtx) => {
      // 正在解题状态板的两路权威源（同一注入批次捕获，网关与状态板共用）：
      // agents.turnBoundaryOf(session) 读官方回合边界投影；sessions.get(sid)
      // 按 id 取活会话对象。注入失败时保持 null，状态板退回镜像。
      liveAgents = webCtx.agents || null
      liveSessions = webCtx.sessions || null
      webCtx.effect(() => webCtx.webServer.register({
        kind: 'prefix',
        path: '/ctfpilot/data',
        handler: async (req, res) => {
          const send = (status, payload) => {
            if (res.writableEnded || res.destroyed) return
            try {
              res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
              res.end(JSON.stringify(payload))
            } catch { /* 连接已断：无处可送 */ }
          }
          try {
            // 官方 /api 路由同款认证：Host/Origin 栅栏 + 浏览器 owner
            // cookie；401/403 原样回传，绝不放行未认证请求。
            const admission = webCtx.connection.admit(req)
            if ('rejection' in admission) {
              send(admission.rejection, { kind: 'error', text: admission.rejection === 401 ? 'unauthorized' : 'forbidden' })
              return
            }
            const target = new URL(req.url ?? '/', 'http://ctfpilot.local')
            const m = /^\/ctfpilot\/data\/([A-Za-z0-9][A-Za-z0-9-]*)$/.exec(target.pathname)
            const cmd = m ? m[1] : ''
            const handler = cmd ? commandHandlerRegistry.get(cmd) : undefined
            if (!handler) {
              send(404, { kind: 'error', text: `gateway: 未注册的命令 '${cmd}'` })
              return
            }
            const method = req.method ?? 'GET'
            if (method !== 'GET' && method !== 'POST') {
              send(405, { kind: 'error', text: `gateway: 不支持的方法 ${method}（仅 GET/POST）` })
              return
            }
            if (method === 'GET' && !GATEWAY_GET.has(cmd)) {
              send(405, { kind: 'error', text: `gateway: '${cmd}' 是写/动作类命令，请用 POST` })
              return
            }
            // 参数面：GET 走 ?args=（URL 编码由客户端负责）；POST 走请求体
            // （text/plain，题面正文/含反斜杠路径不做任何转码）。
            let rawInput = target.searchParams.get('args') ?? ''
            if (method === 'POST') {
              const chunks = []
              let size = 0
              for await (const chunk of req) {
                size += chunk.length
                if (size > GATEWAY_BODY_LIMIT) {
                  send(413, { kind: 'error', text: `gateway: 请求体超过 ${GATEWAY_BODY_LIMIT} 字节上限` })
                  return
                }
                chunks.push(chunk)
              }
              const body = Buffer.concat(chunks).toString('utf8')
              if (body.trim()) rawInput = body
            }
            const sid = target.searchParams.get('sid') ?? ''
            // 客户端断开 → 中止在途引擎子进程（res 'close' 先于 writableEnded
            // 即异常断开；官方 http-bridge 同款判定）。
            const abort = new AbortController()
            res.on('close', () => { if (!res.writableEnded) abort.abort() })
            const agent = sid ? webCtx.agents.get(sid) : undefined
            const out = await handler({ rawInput, signal: abort.signal, agent, via: 'gateway' })
            send(200, out)
          } catch (err) {
            logError(engineRoot(), 'gateway', err, 'throw')
            send(500, { kind: 'error', text: `gateway 内部异常：${String((err && err.message) || err)}` })
          }
        },
      }), 'ctfpilot: /ctfpilot/data 网关路由')
    })
  }
}

/** 进程内探测缓存：Host 常驻（UI 重启不影响），解释器候选与目录无关，
 *  探测成功一次后免重复 spawnSync（每次约 100-300ms）。失败不缓存。 */
let resolvedPythonCache = ''

/** Probe a python that can actually run the engine (3.10+, stdlib only). */
function resolvePython(engineDir) {
  if (resolvedPythonCache) return resolvedPythonCache
  const candidates = PYTHON_CANDIDATES()
  for (const candidate of candidates) {
    const ok = testPython(candidate, engineDir)
    if (ok) {
      resolvedPythonCache = candidate
      return candidate
    }
  }
  throw new Error(`no usable Python 3.10+ found among [${candidates.join(', ')}]; set config.python or CTFPILOT_PYTHON`)
}

function testPython(candidate) {
  try {
    const r = spawnSync(candidate, ['-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'], { stdio: 'ignore', windowsHide: true, timeout: 10_000 })
    return r.status === 0
  } catch {
    return false
  }
}

function apply(ctx, config = {}) {
  return registerCtfpilot(ctx, config)
}

// 工具管理内部函数导出：供 verify-commands.mjs 做纯函数级断言（不执行真实安装）。
const TOOL_INTERNALS = { TOOLS, resolveToolAction, wingetVerb, resolvePkgManager, resetPkgManagerCache, buildToolRequestText, sanitizeText, mergeRegistryPath, pullRegistryPath, toolsStatusRow, resolveInstallAllTargets, buildToolsInstallAllText }

export { Config, apply, name, inject, COMMANDS, TOOL_INTERNALS }



