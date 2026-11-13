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
const inject = ['tools']
//#endregion

//#region command mapping
const COMMANDS = ['solve', 'recall', 'learn-writeup', 'rebuild-knowledge', 'eval-retrieval', 'stats', 'selftest', 'fixations', 'dashboard']

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
    const chunks = []
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
    child.stdout.on('data', d => { if (chunks.length < 64) chunks.push(d) })
    child.stderr.on('data', d => { stderr += d })
    child.on('error', err => {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
      reject(new Error(`failed to start engine "${python}": ${err.message}. Set config.python / CTFPILOT_PYTHON to a Python 3.10+ executable.`))
    })
    child.on('close', code => {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
      const stdout = chunks.join('')
      if (killed) {
        reject(new Error(`engine aborted (call cancelled or timeout > ${Math.round(timeoutMs / 1000)}s); partial stderr: ${truncate(stderr, 500)}`))
        return
      }
      resolve({ code, stdout, stderr })
    })
  })
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
    'dashboard (generate interactive HTML dashboard from SQLite trajectory data — open in browser)',
    'Typical loop: recall --category <cat> --query <signals> -> solve --mode auto --write-trace -> if NOT SOLVED, analyze value.steps/value.output and continue yourself -> learn-writeup when a path works -> rebuild-knowledge after adding writeups. Prefer recall before deep diving; solve before re-implementing base64/caesar/xor/zip logic by hand.',
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

function registerCtfpilot(ctx, config = {}) {
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
      command: { type: 'string', required: true, description: 'Engine subcommand.' },
      challenge: { type: 'string', description: 'solve: challenge directory (absolute path).' },
      mode: { type: 'string', description: 'solve: engine mode. fast = deterministic detectors only; deep = strategy ladder; auto = fast then deep. Default auto.' },
      write_trace: { type: 'boolean', description: 'solve: write _ctfpilot_trace.json for replay/retro (written on success AND failure). Default false.' },
      flag_regex: { type: 'string', description: 'solve: extra flag pattern for non-flag{} formats.' },
      category: { type: 'string', description: 'recall: category filter (crypto|web|reverse|misc|forensics|pwn|osint).' },
      query: { type: 'string', description: 'recall: signal keywords (magic numbers / protocol names / key filenames / technique names) that rank case cards by relevance, e.g. "NTRU lattice LLL" or "pcap Modbus".' },
      writeup: { type: 'string', description: 'learn-writeup: path to the markdown writeup file.' },
      name: { type: 'string', description: 'learn-writeup: challenge name used for the stored card.' },
      writeups_dir: { type: 'string', description: 'rebuild-knowledge / eval-retrieval: directory of writeup markdown files (e.g. the ctf-20min output dir).' },
      dry_run: { type: 'boolean', description: 'rebuild-knowledge: parse and report without writing anything. Default false.' },
      save_baseline: { type: 'boolean', description: 'eval-retrieval: store current scores as the new baseline (after intentional improvements). Default false.' },
      min: { type: 'number', description: 'fixations: minimum hit count filter. Default from engine.' },
      out_html: { type: 'string', description: 'dashboard: output HTML file path (default: CTFPilot-看板.html in engineDir)' },
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
        throw new Error('rebuild-knowledge requires `writeups_dir`: directory containing writeup markdown files')
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
      // Exception: eval-retrieval reports a domain-level regression FAIL with
      // exit 1 — that is a normal value, not an infrastructure error.
      if (code !== 0 && !RESULT_LINE.test(stdout) && !/^\[eval-retrieval\]/m.test(stdout)) {
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
  registerCtfpilot(ctx, config)
}

export { Config, apply, name, inject, COMMANDS }