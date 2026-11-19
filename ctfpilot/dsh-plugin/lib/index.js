// dsh-ctfpilot: bridge the CTFPilot Python engine as a model-facing DSH tool.
// Engine: https://github.com/juuege/ctf-agent (ctfpilot/CTFPilot-v0.1), pure stdlib, offline.
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import z from '@deepseek-ai/schemastery'

//#region config
const Config = z.object({
  python: z.string().default(''),
  engineDir: z.string().default(''),
  timeoutSeconds: z.number().step(1).min(30).max(3600).default(300),
  maxOutputChars: z.number().step(1).min(1000).max(100000).default(8000),
})

const name = 'ctfpilot'
const inject = ['tools', 'sessionProjections', 'commands']
//#endregion

//#region command mapping
const COMMANDS = ['solve', 'recall', 'learn-writeup', 'rebuild-knowledge', 'eval-retrieval', 'stats', 'selftest', 'fixations', 'dashboard', 'bench-pull', 'eval-suite', 'rsi', 'arena', 'research']

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

function resolveEngine(configured) {
  if (configured) {
    const p = path.resolve(configured)
    if (!existsSync(path.join(p, 'ctfpilot.py'))) {
      throw new Error(`engineDir ${p} does not contain ctfpilot.py; clone juuege/ctf-agent or set config.engineDir`)
    }
    return p
  }
  for (const candidate of ENGINE_CANDIDATES) {
    const p = candidate()
    if (p && existsSync(path.join(p, 'ctfpilot.py'))) return p
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

    const lines = output.split('\n')
    let inEvidence = false
    for (const line of lines) {
      if (line.startsWith('[triage]')) { inEvidence = true; continue }
      if (line.startsWith('[')) { inEvidence = false; continue }
      if (inEvidence) {
        const em = EVIDENCE_LINE.exec(line)
        if (em) result.evidence.push({ tag: em[1], text: em[2] })
      }
    }

    const fastSection = output.split('[deep]')[0]
    const fastLines = fastSection.split('\n').filter(l => l && !l.startsWith('[') && !l.startsWith('   ') && !l.startsWith('=='))
    for (const fl of fastLines) {
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
    for (const dl of output.split('\n')) {
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
  const last = output.trimEnd().split('\n').filter(Boolean)
    .reverse().find(l => !l.startsWith('[meta]')) || ''
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

//#region solve modes + flag scanning (interfaces 1 & 2)
// 模式即真实 agent 预设：cordis.patch.yml 的 preset-ctf-quick /
// preset-ctf-deep 各有 persona 与工具集；/ctf-solve-start 先经
// agentPresets 服务 select(agent, agentPreset) 切换 blank 会话预设，
// 再注入纯题面文本（切换失败降级为仅注入）。
const PRESETS_BY_MODE = { quick: 'ctf-quick', deep: 'ctf-deep' }

/** 把 blank 会话切到指定预设（select(agent, agentPreset)）；任何失败静默降级。 */
async function switchSolvePreset(ctx, agent, preset) {
  // agent 缺失（headless/无会话情境）时无预设可切，提前降级。
  if (!agent) return false
  try {
    // 预设切换住在 agentPresets 服务（@Remote('select')，签名 select(agent, agentPreset)）；
    // sessionController 只有 selectModel（模型选择），没有 select——历史上找错服务名，
    // 导致每次「开始解题」的预设切换都静默降级（quick/deep 实际从未生效）。
    const svc = (ctx && ctx.agentPresets) || (ctx && typeof ctx.get === 'function' && ctx.get('agentPresets')) || null
    if (!svc || typeof svc.select !== 'function') return false
    await svc.select(agent, preset)
    return true
  } catch { return false }
}

const MODES = {
  quick: { id: 'quick', label: '快解' },
  deep: { id: 'deep', label: '深度解析' },
}
function modeLabel(id) { const m = MODES[String(id || '')]; return m ? m.label : '深度解析' }

// Same shape as workflows/ctf-solve-pipeline.js FLAG_RE — text/CoT scanner.
const FLAG_SCAN_RE = /\b(?:flag|FLAG|ctf|CTF|DASCTF|NSSCTF|SUCTF|hgame)\s*\{[^}\s]{4,256}\}/g
function scanFlags(text) {
  const src = String(text || '')
  const re = new RegExp(FLAG_SCAN_RE.source, 'gi')
  const out = []
  let m
  while ((m = re.exec(src)) !== null) {
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
  const out = { mode: '', category: '', attaches: [], text: [] }
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i].v
    if (p === '--mode') { out.mode = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--mode=')) { out.mode = p.slice(7); continue }
    if (p === '--category') { out.category = String((parts[++i] || {}).v || ''); continue }
    if (p.startsWith('--category=')) { out.category = p.slice(11); continue }
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
  if (args.attaches.length) lines.push(`附件路径：${args.attaches.join('; ')}`)
  if (args.category) lines.push(`题型（可选信息）：${args.category}`)
  lines.push(
    '',
    `按当前预设口径（${modeLabel(mode)}）推进；拿到候选 flag 后走 propose_submit 人审卡片确认，确认后才计入台账。`,
  )
  return { mode, text: lines.join('\n') }
}
//#endregion

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

function injectAgentMessage(agent, text, { steer = false } = {}) {
  if (!agent || typeof agent.followup !== 'function') return false
  try {
    const message = pluginUserMessage(text)
    if (steer && typeof agent.steer === 'function') {
      agent.steer(message)
    } else {
      agent.followup(message)
    }
    return true
  } catch {
    return false
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
    const r = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command', `${head}; ${line}`], { windowsHide: true, encoding: 'utf8', timeout: timeoutMs })
    return { code: r.status ?? 1, out: asciiOnly(String(r.stdout || '').trim()), err: asciiOnly(String(r.stderr || '').trim()) }
  } catch (e) {
    return { code: 1, out: '', err: asciiOnly(e.message || String(e)) }
  }
}
/** 异步版探针：status 快检 7 项并行（串行 spawnSync 最坏 7×15s 卡死面板标签）。 */
function shellRun(line, { timeoutMs = 30000 } = {}) { return Promise.resolve(shellRunSync(line, { timeoutMs })) }
// Stable catalog: even uninstalled tools keep their row (interface 3 spec).
const TOOLS = [
  { id: 'nmap', name: 'nmap（端口扫描，Kali 式镜像）', kind: 'mirror', probe: 'where.exe nmap', install: 'choco install nmap -y', update: 'choco upgrade nmap -y', uninstall: 'choco uninstall nmap -y' },
  { id: 'sqlmap', name: 'sqlmap（SQL 注入）', kind: 'mirror', probe: 'where.exe sqlmap.py', install: 'choco install sqlmap -y', update: 'choco upgrade sqlmap -y', uninstall: 'choco uninstall sqlmap -y' },
  { id: 'john', name: 'John the Ripper（口令破解）', kind: 'mirror', probe: 'where.exe john', install: 'choco install john -y', update: 'choco upgrade john -y', uninstall: 'choco uninstall john -y' },
  { id: 'hashcat', name: 'hashcat（GPU 口令破解）', kind: 'mirror', probe: 'where.exe hashcat', install: 'choco install hashcat -y', update: 'choco upgrade hashcat -y', uninstall: 'choco uninstall hashcat -y' },
  { id: 'binwalk', name: 'binwalk（固件/镜像提取）', kind: 'mirror', probe: 'where.exe binwalk', install: 'choco install binwalk -y', update: 'choco upgrade binwalk -y', uninstall: 'choco uninstall binwalk -y' },
  { id: 'volatility3', name: 'volatility3（内存取证）', kind: 'mirror', probe: 'python -c "import volatility3"', install: 'pip install volatility3', update: 'pip install --upgrade volatility3', uninstall: 'pip uninstall volatility3 -y' },
  { id: 'pwntools', name: 'pwntools（二进制利用）', kind: 'mirror', probe: 'python -c "import pwn"', install: 'pip install pwntools', update: 'pip install --upgrade pwntools', uninstall: 'pip uninstall pwntools -y' },
  { id: 'protocol-mcp', name: 'ProtocolMCP（协议安全 MCP 服务）', kind: 'mcp', probe: '', install: '# 在 MCP Manager 页面添加 protocol-mcp 服务器。', update: '# 在 MCP Manager 页面重配 protocol-mcp。', uninstall: '# 在 MCP Manager 页面移除 protocol-mcp。' },
]
/**
 * probe 输出消毒：剔除控制字符与 ANSI 转义序列，压缩空白，上限截断。
 * 工具管理界面的 detail 列此前直接透传 pwsh stderr，乱码根因在此。
 */
function sanitizeText(s, max = 120) {
  return String(s == null ? '' : s)
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
    .replace(/\u001B\[[0-9;]*[A-Za-z]/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, max)
}
function findTool(id) { return TOOLS.find(t => t.id === id || t.name === id) || null }
async function toolsStatusRow(tool) {
  if (tool.kind === 'mcp') {
    const dir = process.env.PROTOCOLMCP_DIR
    return { id: tool.id, name: tool.name, kind: tool.kind, ok: Boolean(dir), detail: dir ? `配置目录：${dir}` : '未配置（在 MCP Manager 中添加 protocol-mcp）' }
  }
  const r = shellRun(tool.probe, { timeoutMs: 15000 })
  return { id: tool.id, name: tool.name, kind: tool.kind, ok: r.code === 0, detail: sanitizeText(r.out || r.err || '(probe 无输出)') }
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
    'Typical loop: recall --category <cat> --query <signals> -> solve --mode auto --write-trace -> if NOT SOLVED, analyze value.steps/value.output and continue yourself -> learn-writeup when a path works -> rebuild-knowledge after adding writeups; after any detector/card change run eval-suite. Prefer recall before deep diving; solve before re-implementing base64/caesar/xor/zip logic by hand.',
  ].join(' ')
}

function renderResult(value) {
  switch (value.command) {
    case 'solve':
      if (value.solved) {
        return `SOLVED in ${value.elapsed || '?'}s — ${value.flag || '(flag not printed)'}`
      }
      const fastN = value.steps?.filter(s => s.phase === 'fast').length || 0
      const deepN = value.steps?.filter(s => s.phase !== 'fast').length || 0
      const hint = value.steps?.length > 0
        ? `${value.steps.length} 步尝试（fast=${fastN}, deep=${deepN}）`
        : '引擎无确定性路径'
      return `NOT SOLVED${value.elapsed ? ` in ${value.elapsed}s` : ''} — ${hint}。检查 value.output 中的 attempts，或自己继续分析（先 recall 同题型经验）`
    case 'eval-retrieval': {
      const pass = /RESULT\s*:\s*PASS/.test(value.output || '')
      const m = /signal\s+top1=([\d.]+%?) top3=([\d.]+%?)/.exec(value.output || '')
      const scores = m ? `top1=${m[1]} top3=${m[2]}` : 'no scores parsed'
      return pass
        ? `eval-retrieval PASS — ${scores}（检索质量在基线容差内）`
        : `eval-retrieval FAIL — ${scores}。检索回归超容差：见 value.output 的 drift 细节；属意外回退需查检索/卡片变更，故意改进后可 --save-baseline 更新基线`
    }
    case 'eval-suite': {
      const pass = /RESULT\s*:\s*PASS/.test(value.output || '')
      const smoke = /\bsmoke set: (\d+)\/(\d+) solved\b/.exec(value.output || '')
      return pass
        ? `eval-suite PASS — 能力地板保持（smoke ${smoke ? smoke[1] + '/' + smoke[2] : '?'}），六维指标全部在容差内`
        : `eval-suite FAIL — 有指标回归（smoke floor 或知识不退等）：见 value.output 的 per-metric 行；故意改进后可 --save-baseline 记新基线`
    }
    case 'rsi': {
      const cycleDone = /cycle done/i.test(value.output || '')
      return `${value.command} ok — ${cycleDone ? '引擎已产出待审提案（人审后 adopt 会强制跟进 eval-suite）' : '提案状态机已更新'}；见 value.output 的提案明细`
    }
    case 'arena': {
      const tiers = /tiers? recomputed/.test(value.output || '') ? '分级已重算' : ''
      return `${value.command} ok — 竞技台账动作完成${tiers ? '，数据驱动分级已重算（练兵数据独立于生产轨迹）' : ''}；见 value.output`
    }
    case 'research': {
      if (/INGEST BLOCKED/.test(value.output || '')) {
        return 'research 入库被双门拦截：需 ≥2 个证据指针 + 草稿通过 writeup-learner 结构校验（见 value.output 提示）。离线诚实：引擎不编知识'
      }
      return `${value.command} ok — 研究队列状态已更新（证据指针 / 状态流转）；见 value.output`
    }
    default:
      return `${value.command} ok — see value.output`
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
    try { return JSON.parse ? JSON.parse(String(raw || '')) : null } catch { return null }
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
    async execute(args, exec) {
      // NOTE: do NOT session.append tool/call or tool/result here — the tool
      // executor batch logs both around this body (batch.ts start/commit);
      // duplicate entries would corrupt the log. The fold below reads the
      // framework-logged events; nothing else is needed for admission.
      const agent = exec && exec.agent
      const session = agent && agent.session
      void session
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

  // 正在解题状态板（界面 2）：按会话折叠 operating mode 与抽出的 flag。
  // flag 只追加不覆盖；running 随 turn 边界翻转。浏览器半边经
  // useProjection('ctfsolve') 读取。
  if (ctx.sessionProjections && typeof ctx.sessionProjections.register === 'function') {
    try {
      await ctx.sessionProjections.register({
        key: 'ctfsolve',
        stateSchema: zod.object({}),
        stateVersion: 1,
        init: () => { const s = { sessions: {} }; solvingState = s; return s },
        apply: (state, event) => {
          const sid = String((event && event.sessionId) || '')
          if (!sid) return state
          const sessions = Object.assign({}, state.sessions)
          let cur = sessions[sid]
          if (!cur) { cur = { mode: '', label: '', running: false, flags: [], flagsCount: [] }; sessions[sid] = cur }
          let changed = false
          if (event.type === 'command/run') {
            const d = event.data || {}
            if (d.name === 'ctf-solve-start') {
              const a = parseSolveStartArgs(String(d.args || ''))
              const m = MODES[a.mode] ? a.mode : 'deep'
              if (cur.mode !== m) { cur.mode = m; cur.label = modeLabel(m); changed = true }
            }
          } else if (event.type === 'turn/start' || event.type === 'turn/end') {
            const running = event.type === 'turn/start'
            if (cur.running !== running) { cur.running = running; changed = true }
          }
          if (event.type === 'assistant/message' || event.type === 'tool/result') {
            const d = event.data || {}
            const text = typeof d === 'string' ? d
              : (typeof d.text === 'string' ? d.text
                : Array.isArray(d.content) ? d.content.map(c => (c && typeof c.text === 'string') ? c.text : '').join('')
                : '')
            for (const f of scanFlags(text)) {
              const i = cur.flags.indexOf(f)
              if (i < 0) { cur.flags.push(f); cur.flagsCount.push(1); changed = true }
              else if ((cur.flagsCount[i] || 1) < 99) { cur.flagsCount[i] = (cur.flagsCount[i] || 1) + 1; changed = true }
            }
          }
          if (!changed) return state
          const next = { sessions }
          solvingState = next // 界面 2 轮询源：折叠到新值时同步镜像
          return next
        },
        wire: {
          viewSchema: zod.object({}),
          view: s => ({ sessions: s.sessions || {} }),
        },
      })
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

  /* 看板快照缓存：面板反复打开、引擎每次冷启动约 0.9s（13 万行 attempts 的全量
   * 聚合），TTL 内直接返回快照；记账成功的命令会主动作废，规避数据陈旧。 */
  const DASHBOARD_TTL_MS = 15_000
  let dashboardSnapshot = null // { atMs: number, blob: object }

  /** 作废看板快照，下次读取重新查询引擎。 */
  function invalidateDashboardSnapshot() {
    dashboardSnapshot = null
  }

  async function engineDashboard() {
    const now = Date.now()
    if (dashboardSnapshot !== null && now - dashboardSnapshot.atMs < DASHBOARD_TTL_MS) {
      return dashboardSnapshot.blob
    }
    const dir = engineRoot()
    const python = config.python || resolvePython(dir)
    const { stdout } = await runEngine(['dashboard', '--json'], {
      python, engineDir: dir, timeoutMs: 30000, maxOutputChars: 500000,
    })
    let blob
    try {
      blob = JSON.parse(stdout)
    } catch {
      // 新数据解析失败时保留旧快照（若有），避免把可用页面打崩。
      if (dashboardSnapshot !== null) return dashboardSnapshot.blob
      throw new Error('engine dashboard JSON parse failed')
    }
    dashboardSnapshot = { atMs: Date.now(), blob }
    return blob
  }

  if (ctx.commands && typeof ctx.commands.register === 'function') {
  ctx.commands.register({
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

  ctx.commands.register({
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
  ctx.commands.register({
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
        const python = config.python || resolvePython(dir)
        const argv = ['challenge-detail']
        if (idM) argv.push('--id', idM[1])
        if (name) argv.push('--name', name)
        const { stdout } = await runEngine(argv, {
          python, engineDir: dir, timeoutMs: 15000, maxOutputChars: 200000,
        })
        return { kind: 'success', text: stdout }
      } catch (err) {
        return { kind: 'error', text: `CTFPilot 详情读取失败：${err.message || err}` }
      }
    },
  })

  ctx.commands.register({
    name: 'ctf-stats',
    description: 'Show compact CTFPilot engine statistics.',
    handler: async () => {
      const engineDir = engineRoot()
      const python = config.python || resolvePython(engineDir)
      try {
        const { stdout } = await runEngine(['stats'], { python, engineDir, timeoutMs: 30000, maxOutputChars: 4000 })
        return { kind: 'success', text: String(stdout || '').trim() }
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

  /** 从 rawInput 里抽 flag 值：--flag <v> 或 --flag=<v>，支持双引号包裹的含空格值。 */
  function flagArg(line, name) {
    const eq = String(line || '').match(new RegExp(`${name}=(?:"([^"]*)"|([^\\s]+))`))
    if (eq) return eq[1] !== undefined ? eq[1] : eq[2]
    const sp = String(line || '').match(new RegExp(`${name}\\s+(?:"([^"]*)"|([^\\s]+))`))
    if (sp) return sp[1] !== undefined ? sp[1] : sp[2]
    return ''
  }

  /** 被门拦下的动作必须可见：stdout 含 [error] 行、BLOCKED 字样或评估门
   * RESULT: FAIL 时按错误回报，不让拦截结果在会话里伪装成绿色成功。 */
  function outcome(stdout) {
    const text = String(stdout || '').trim()
    const flagged = /(^|\n)\[error\]/.test(text) || /BLOCKED/i.test(text) || /\bRESULT\s*:\s*FAIL\b/.test(text)
    return flagged ? { kind: 'error', text } : { kind: 'success', text }
  }

  /** 引号感知取选项值：--key "quoted span" 或 --key token（含 CJK/连字符）。
   * 前端 shellArg 对含空格值包双引号发来，旧正则 (\S+) 会把引号值截断。 */
  function optArg(line, name) {
    const m = String(line || '').match(new RegExp(`--${name}\\s+(?:"([^"]*)"|(\\S+))`))
    return m ? (m[1] !== undefined ? m[1] : m[2]) : null
  }
  function optArgAll(line, name) {
    const out = []
    const re = new RegExp(`--${name}(?:\\s+|=)(?:"([^"]*)"|(\\S+))`, 'g')
    let m
    while ((m = re.exec(String(line || ''))) !== null) out.push(m[1] !== undefined ? m[1] : m[2])
    return out
  }


  ctx.commands.register({
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
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 60000, maxOutputChars: 12000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        return crashed
          ? { kind: 'error', text: `竞技台账动作失败（${action}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
          : outcome(stdout)
      } catch (err) {
        return { kind: 'error', text: `竞技台账动作失败（${action}）：${err.message || err}` }
      }
    },
  })

  ctx.commands.register({
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

  ctx.commands.register({
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
        return outcome(parts.join('\n'))
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
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 60000, maxOutputChars: 12000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        return crashed
          ? { kind: 'error', text: `研究队列动作失败（${sub}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
          : outcome(stdout)
      } catch (err) {
        return { kind: 'error', text: `研究队列动作失败（${sub}）：${err.message || err}` }
      }
    },
  })

  ctx.commands.register({
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
      const python = config.python || resolvePython(engineDir)
      try {
        const { code, stdout, stderr } = await runEngine(argv, { python, engineDir, timeoutMs: 120000, maxOutputChars: 20000, signal })
        const crashed = code !== 0 && !/^\[/m.test(String(stdout || ''))
        return crashed
          ? { kind: 'error', text: `RSI 提案流动作失败（${sub}）：${String(stderr || stdout || '(no output)').slice(0, 400)}` }
          : outcome(stdout)
      } catch (err) {
        return { kind: 'error', text: `RSI 提案流动作失败（${sub}）：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 界面 1：开始解题 —— 先把会话预设切到所选模式（quick/deep，真实
  // agent 预设差异），再注入纯题面文本（无 brief 前缀）；agent 缺失时
  // 明确报错（不再伪装 success —— 题面要么真注入，要么告诉用户没注入）。
  // ------------------------------------------------------------------
  ctx.commands.register({
    name: 'ctf-solve-start',
    description: 'Start a solve in the current session: switch its agent preset (quick/deep), then inject body + attachment paths as the next user message.',
    input: { hint: 'quick|deep [--category C] [--attach P …] 题目描述文字' },
    handler: async ({ agent, rawInput, signal }) => {
      const args = parseSolveStartArgs(rawInput)
      const modeOk = MODES[args.mode] ? args.mode : 'deep'
      const label = modeLabel(modeOk)
      const switched = await switchSolvePreset(ctx, agent, PRESETS_BY_MODE[modeOk])
      const composed = solvePromptText(args)
      if (!agent || typeof agent.followup !== 'function') {
        // 无 live agent：无法注入也无预设可切。给出题面全文让用户手动发送，
        // 并显式标记为失败降级（用户能分辨「已开始」与「未开始」）。
        return { kind: 'error', text: `无活动会话 agent，题面未能注入。请手动发送：\n${composed.text}` }
      }
      const injected = injectAgentMessage(agent, composed.text)
      // 注入后命令被取消：followup 无法撤回（官方 inbox 契约），
      // 消息仍会作为下一轮执行，如实告知。
      if (signal && signal.aborted) {
        return { kind: 'error', text: '命令已取消（题面消息可能仍在收件箱，下一轮会执行）。' }
      }
      if (!injected) {
        return { kind: 'error', text: `题面注入失败（followup 被拒）。请手动发送：\n${composed.text}` }
      }
      // 预设切换失败的两个真相：手动在该会话里跑命令（已开始过对话→锁定），
      // 或 agentPresets 服务缺席。诚实区分「已切到」与「没切到」，不谎称保持。
      const presetLine = switched
        ? `会话预设已切到「${label}」`
        : `会话预设未切换到「${label}」（已开始过对话的会话预设锁定；面板「开始解题」新建的对话不受此限），按当前预设口径推进`
      return { kind: 'success', text: `✓ ${presetLine}，题面已注入并开始执行（${composed.text.length} 字符）。\n原始题面（供核对或手动重发）：\n${composed.text}` }
    },
  })

  // ------------------------------------------------------------------
  // 界面 2：实时状态板轮询源 —— 返回 solvingState 镜像的 JSON
  // （会话 id → mode/label/running/flags×flagsCount）。主面板拿不到
  // useProjection，靠本命令每 4s 拉取合并驱动运行态与 flag chip。
  // ------------------------------------------------------------------
  ctx.commands.register({
    name: 'ctf-solving-state',
    description: 'Return the live solving-board snapshot (per-session mode / running / append-only flags) as JSON.',
    input: { hint: '(no input)' },
    handler: async () => {
      try {
        const snap = JSON.parse(JSON.stringify(solvingState))
        return { kind: 'success', text: JSON.stringify(snap) }
      } catch (err) {
        return { kind: 'error', text: `读取解题状态失败：${err.message || err}` }
      }
    },
  })

  // ------------------------------------------------------------------
  // 界面 2：已解出按钮确认后 —— 终止活动轮，保留日志与队列，
  // 在同一会话上发一条 steer 消息请模型写 writeup 归档学习闭环。
  // ------------------------------------------------------------------
  ctx.commands.register({
    name: 'ctf-writeup',
    description: 'After marking solved: stop the active round, then on the same conversation ask the model to summarize into a writeup (learn-writeup + rebuild-knowledge).',
    input: { hint: '[--name "挑战名"]' },
    handler: async ({ agent, rawInput, signal }) => {
      const name = optArg(String(rawInput || ''), 'name') || ''
      let stopped = false
      // agent.cancel(cause, options) 为同步 void（runtime-types.ts 契约）：
      // 不再 await；keepInbox 保留队列与 steering 收件。
      if (agent && typeof agent.cancel === 'function') {
        try { agent.cancel({ kind: 'user' }, { keepInbox: true }); stopped = true } catch { stopped = false }
      }
      if (signal && signal.aborted) return { kind: 'error', text: '已取消。' }
      const dir = path.join(resolveEngine(config.engineDir || ''), 'writeups')
      const ask = [
        `本轮「${name || '挑战'}」已解出。请把本次解题过程归档为 writeup：`,
        `1) 写到 ${dir}\\<挑战名>.md（目录不存在先创建；内容用标准模板：分类/载体/识别信号/核心技术链/有效技术/陷阱/可迁移模式/结果）。`,
        '2) 完成后调用 ctfpilot learn-writeup "<该文件路径>" --name "<挑战名>"，再 ctfpilot rebuild-knowledge "' + path.dirname(dir) + '"。',
      ].join('\n')
      const steered = injectAgentMessage(agent, ask, { steer: true })
      if (!stopped && !steered) {
        return { kind: 'error', text: 'ctf-writeup 需要一个活动中的会话（agent 不存在或不可注入）。' }
      }
      return { kind: 'success', text: `✓ 活动轮${stopped ? '已终止（上下文保留）' : '无活动轮'}，${steered ? '已在同会话请求 writeup 归档' : '注入失败'}。目标目录：${dir}` }
    },
  })

  // ------------------------------------------------------------------
  // 界面 3：工具管理。目录稳定——卸载后行仍在，只弹安装按钮。
  // mirror 类走 choco/pip pwsh 执行；mcp 类指向 MCP Manager 页面。
  // ------------------------------------------------------------------
  ctx.commands.register({
    name: 'ctf-tools',
    description: 'Manage CTF tooling availability: status probe, install / update / uninstall (choco / pip / MCP-manager guidance). Uninstalled tools stay listed.',
    input: { hint: '--tools status | install <id> | update <id> | uninstall <id>' },
    handler: async ({ rawInput, signal }) => {
      const words = String(rawInput || '').trim().split(/\s+/).filter(Boolean)
      const verb = ['status', 'install', 'update', 'uninstall'].find((v) => words.includes(v)) || ''
      if (!verb) {
        return { kind: 'error', text: 'ctf-tools 需要动词 status|install|update|uninstall，例如 `/ctf-tools status` 或 `/ctf-tools install nmap`' }
      }
      if (verb === 'status') {
        // 7 项探针并行（串行 spawnSync 最坏 7×15s，面板标签会卡死）。
        // spawnSync 本身同步阻塞事件循环，并行化受限于其实现；每项
        // 15s 超时上限仍在，最坏总耗时 = 最慢一项而非累加。
        try {
          const rows = []
          for (const t of TOOLS) rows.push(toolsStatusRow(t))
          const settled = await Promise.all(rows)
          return { kind: 'success', text: `工具快检（共 ${settled.length} 项）：\n` + JSON.stringify(settled, null, 2) }
        } catch (err) {
          return { kind: 'error', text: `工具快检失败：${err.message || err}` }
        }
      }
      const idWord = words.find((w) => w !== verb && !w.startsWith('--'))
      if (!idWord) {
        return { kind: 'error', text: `ctf-tools ${verb} 需要目标 id，例如 \`/ctf-tools ${verb} nmap\`（可选 id：nmap sqlmap john hashcat binwalk volatility3 pwntools protocol-mcp）` }
      }
      const tool = findTool(idWord)
      if (!tool) {
        return { kind: 'error', text: `未知工具 id ${idWord}。可选：nmap sqlmap john hashcat binwalk volatility3 pwntools protocol-mcp` }
      }
      if (tool.kind === 'mcp') {
        return { kind: 'success', text: `${tool.name} 为 MCP 类工具，请在 MCP Manager 页面${verb === 'install' ? '添加' : verb === 'update' ? '重配' : '移除'} protocol-mcp 服务器；本命令不代为操作。` }
      }
      const cmdLine = tool[verb] || ''
      const r = await shellRun(cmdLine, { timeoutMs: 240000 })
      const verdict = r.code === 0
        ? `✓ ${verb} 完成（${tool.name}）。`
        : `✗ ${verb} 失败：${(r.err || r.out || '(no output)').slice(0, 500)}`
      const after = verb === 'uninstall' ? '（该工具行仍保留，快检后会显示安装按钮）' : verb === 'install' || verb === 'update' ? '可再跑 `/ctf-tools status` 复检。' : ''
      return { kind: verdict.startsWith('✗') ? 'error' : 'success', text: `${verdict}\n执行：${cmdLine}${after ? '\n' + after : ''}` }
    },
  })

  }
}

/** Probe a python that can actually run the engine (3.10+, stdlib only). */
function resolvePython(engineDir) {
  const candidates = PYTHON_CANDIDATES()
  for (const candidate of candidates) {
    const ok = testPython(candidate, engineDir)
    if (ok) return candidate
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

export { Config, apply, name, inject, COMMANDS }



