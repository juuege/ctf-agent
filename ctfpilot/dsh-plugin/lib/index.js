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
const COMMANDS = ['solve', 'recall', 'learn-writeup', 'stats', 'selftest', 'fixations']

function buildArgs(args) {
  switch (args.command) {
    case 'solve': {
      const argv = ['solve', args.challenge]
      if (args.mode) argv.push('--mode', args.mode)
      if (args.write_trace) argv.push('--write-trace')
      if (args.flag_regex) argv.push('--flag-regex', args.flag_regex)
      return argv
    }
    case 'recall':
      return ['recall', ...(args.category ? ['--category', args.category] : [])]
    case 'learn-writeup':
      return ['learn-writeup', args.writeup, ...(args.name ? ['--name', args.name] : [])]
    case 'fixations':
      return ['fixations', ...(typeof args.min === 'number' ? ['--min', String(args.min)] : [])]
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

function parseEngineResult(stdout) {
  // flag/elapsed default to '' (single-type schema; dsh registry rejects type arrays)
  const result = { command: null, solved: false, flag: '', elapsed: '', summary: '', output: String(stdout || '') }
  const m = RESULT_LINE.exec(result.output)
  if (m) {
    result.solved = m[1] === 'SOLVED'
    result.elapsed = m[2] ?? ''
  }
  const f = FLAG_LINE.exec(result.output)
  if (f) result.flag = f[1]
  const last = result.output.trimEnd().split('\n').filter(Boolean).slice(-1)[0]
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
    'Fast deterministic path solves encoding chains, Caesar, XOR, gzip/zip unpacking and PNG LSB stego in seconds with zero LLM calls; if it misses, read its attempts output and continue yourself.',
    'Commands: solve <challenge_dir> (auto routes fast then deep; --mode fast for known-trivial encodings, deep for strategy ladder, default auto; write_trace saves a replayable trace json),',
    'recall (query past experience checkpoints by category before attacking a new challenge), learn-writeup <file> (ingest a solve writeup into checkpoints/skill cards after finishing a challenge),',
    'stats (global bookkeeping), selftest (verify the engine install, 6 smoke challenges), fixations (list repeatedly-used techniques worth hard-coding next).',
    'Typical loop: recall -> solve --mode auto --write_trace -> if NOT SOLVED, analyze the engine output yourself -> learn-writeup when a path works. Prefer recall before deep diving; solve before re-implementing base64/caesar/xor/zip logic by hand.',
  ].join(' ')
}

function renderResult(value) {
  switch (value.command) {
    case 'solve':
      return value.solved
        ? `SOLVED in ${value.elapsed || '?'}s — ${value.flag || '(flag not printed)'}`
        : `NOT SOLVED${value.elapsed ? ` in ${value.elapsed}s` : ''} — engine had no deterministic path; check value.output attempts, then analyze yourself (recall first for similar solved cases)`
    default:
      return `${value.command} ok — see value.output`
  }
}

function commandWireName(args) {
  switch (args.command) {
    case 'solve': return `ctfpilot solve ${args.challenge ?? ''} ${args.mode ? '(' + args.mode + ')' : ''}`.trim()
    case 'learn-writeup': return `ctfpilot learn-writeup ${args.writeup ?? ''}`
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
      write_trace: { type: 'boolean', description: 'solve: write _ctfpilot_trace.json for replay/retro. Default false.' },
      flag_regex: { type: 'string', description: 'solve: extra flag pattern for non-flag{} formats.' },
      category: { type: 'string', description: 'recall: category filter (crypto|web|reverse|misc|forensics|pwn|osint).' },
      writeup: { type: 'string', description: 'learn-writeup: path to the markdown writeup file.' },
      name: { type: 'string', description: 'learn-writeup: challenge name used for the stored card.' },
      min: { type: 'number', description: 'fixations: minimum hit count filter. Default from engine.' },
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
      if (code !== 0 && !RESULT_LINE.test(stdout)) {
        throw new Error(`engine exited ${code}: ${truncate(stderr || stdout || '(no output)', 500)}`)
      }
      return value
    },
    presentCall(args) {
      return { card: 'terminal', title: commandWireName(args), description: 'CTFPilot engine (local, offline)' }
    },
    presentResult(args, { isError, content }) {
      return {
        card: 'terminal',
        title: commandWireName(args),
        rawOutput: typeof content === 'string' ? content : '',
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
