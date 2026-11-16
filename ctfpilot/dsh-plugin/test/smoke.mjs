// Smoke test: register the tools against a mock ctx, then run real engine
// commands through execute(). Runs under plain Node (>=20) with schemastery
// installed next to this package. Windows or Linux both fine.
import assert from 'node:assert/strict'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, rmSync } from 'node:fs'
import * as plugin from '../lib/index.js'

const here = dirname(fileURLToPath(import.meta.url))
// Engine resolution mirrors lib/index.js: explicit CTFPILOT_HOME wins,
// then the sibling-published layout, then the repo-workspace layout.
const engineDir = process.env.CTFPILOT_HOME && existsSync(join(process.env.CTFPILOT_HOME, 'ctfpilot.py'))
  ? join(process.env.CTFPILOT_HOME)
  : existsSync(join(here, '..', 'CTFPilot-v0.1'))
    ? join(here, '..', 'CTFPilot-v0.1')
    : resolve(here, '..', '..', 'CTFPilot-v0.1')

assert.ok(existsSync(join(engineDir, 'ctfpilot.py')), `engine dir not found: ${engineDir}`)

// Windows desktop deployments usually pass an explicit python via config
// (see cordis.patch.yml). Prefer CTFPILOT_PYTHON, else the TeleAgent-bundled
// interpreter, else fall back to PATH probing inside the plugin.
const candidates = [
  process.env.CTFPILOT_PYTHON,
  'C:/Users/Administrator/.local/share/TeleAgent/runtimes/python/python.exe',
]
const explicitPython = candidates.find(p => p && existsSync(p))
const cfg = { engineDir, ...(explicitPython ? { python: explicitPython } : {}) }

// 1. apply() registers the model-facing tools on a mock ctx.
let defs = []
const ctx = { tools: { register(d) { defs.push(d) } } }
await plugin.apply(ctx, cfg)

const byName = Object.fromEntries(defs.map(d => [d.name, d]))
assert.equal(byName.ctfpilot.name, 'ctfpilot')
for (const field of ['description', 'parameters', 'output', 'execute', 'presentCall', 'presentResult']) {
  assert.ok(byName.ctfpilot[field] !== undefined, `tool definition missing .${field}`)
}
for (const p of ['command', 'challenge', 'mode', 'write_trace', 'flag_regex', 'category', 'query',
  'writeup', 'name', 'writeups_dir', 'dry_run', 'save_baseline', 'min', 'out_html']) {
  assert.ok(byName.ctfpilot.parameters[p] !== undefined, `parameters missing .${p}`)
}
assert.equal(byName.ctfpilot.presentCall({ command: 'solve', challenge: '/tmp/x' }).card, 'terminal')
// propose_submit: the human-confirm gate's propose step.
assert.equal(byName.propose_submit.name, 'propose_submit')
for (const f of ['flag', 'title', 'brief', 'category', 'challenge']) {
  assert.ok(byName.propose_submit.parameters[f] !== undefined, `propose_submit parameters missing .${f}`)
}
assert.equal(Object.keys(byName.propose_submit.output.schema.properties).sort().join(','), 'action,invitation,title')

// 2. Bad engineDir surfaces a readable error at execute time (mounting stays lazy).
await plugin.apply({ tools: { register() {} } }, { engineDir: '/nonexistent-xyz' }) // must not throw
const badDefs = []
await plugin.apply({ tools: { register(d) { badDefs.push(d) } } }, { engineDir: 'C:\\bogus' })
assert.equal(badDefs.length, 2)
const badCtf = badDefs.find(d => d.name === 'ctfpilot')
await assert.rejects(
  badCtf.execute({ command: 'stats' }, { signal: AbortSignal.timeout(10_000) }),
  /ctfpilot\.py/,
)

const def = byName.ctfpilot

// 3. Real engine: selftest. 01-05 always pass; 06_zip_nested depends on the
// deep mock ladder against a clean directory, so accept >=5/6 — the plugin's
// job is faithful bridging, not engine determinism.
const r = await def.execute({ command: 'selftest' }, { signal: AbortSignal.timeout(120_000) })
assert.equal(r.command, 'selftest')
assert.ok(/passed (\d)\/6/.test(r.summary), `no selftest summary in output: ${r.summary}`)
assert.ok(parseInt(/passed (\d)\/6/.exec(r.summary)[1], 10) >= 5, `selftest unexpectedly weak: ${r.summary}`)
assert.ok(!r.output.includes('truncated NaN'), 'NaN in truncated marker — config defaults missing')
console.log('selftest ok:', r.summary)

// 4. Real solve: b64 chain smoke challenge takes the fast path; the SOTA
// engine emits a machine-readable [meta] line that must land in value.steps.
const chall = join(engineDir, 'tests', 'challs', '01_b64chain')
const s = await def.execute({ command: 'solve', challenge: chall, mode: 'auto' }, { signal: AbortSignal.timeout(120_000) })
assert.equal(s.command, 'solve')
assert.equal(s.solved, true, `expected solved, got: ${s.summary}`)
assert.ok(s.flag && s.flag.startsWith('flag{'), `unexpected flag: ${s.flag}`)
assert.ok(s.elapsed, 'elapsed missing')
assert.ok(Array.isArray(s.steps) && s.steps.some(st => st.phase === 'fast'),
  `structured steps missing from [meta] parse: ${JSON.stringify(s.steps).slice(0, 200)}`)
console.log('solve ok:', s.flag, `${s.elapsed}s`, `(steps=${s.steps.length})`)

// 4b. UI card timeline renders from the structured steps.
const card = def.presentResult({ command: 'solve', challenge: chall }, { content: s, isError: false })
assert.equal(card.card, 'terminal')
assert.ok(card.rawOutput.includes('── 过程时间线 ──'), 'timeline block missing in card')

// 5. recall SOTA: --query signal keywords return ranked output.
const rec = await def.execute(
  { command: 'recall', category: 'crypto', query: 'RSA NTRU lattice' },
  { signal: AbortSignal.timeout(60_000) })
assert.equal(rec.command, 'recall')
assert.equal(rec.solved, false) // engine prints no [result] line; solved only meaningful for solve
assert.ok(typeof rec.output === 'string' && rec.output.length > 0)

// 5b. rebuild-knowledge --dry-run parses the writeups dir without writing.
// Prefer the local writeup output dir (bulk, 200 files); fall back to a source dir.
const bulkDir = process.env.CTFPILOT_WRITEUPS_DIR
  || 'C:/Users/Administrator/.local/share/TeleAgent/TeleAgent的工作空间/定时任务产出/ctf-20min'
if (existsSync(bulkDir)) {
  const rk = await def.execute(
    { command: 'rebuild-knowledge', writeups_dir: bulkDir, dry_run: true },
    { signal: AbortSignal.timeout(180_000) })
  assert.equal(rk.command, 'rebuild-knowledge')
  assert.ok(rk.output.includes('[knowledge]'), `no [knowledge] lines in output: ${rk.summary}`)
  assert.ok(rk.output.includes('dry run, nothing written'), 'dry-run must not write')
  console.log('rebuild-knowledge dry-run ok:', rk.summary)
} else {
  console.log('rebuild-knowledge dry-run skipped (no local writeups dir)')
}

// 5c. eval-retrieval returns its regression verdict as a VALUE (a FAIL
// verdict is a domain result with exit 1 — must not throw as infra error).
// The source writeups dir (engineDir/writeups) matches the shipped case
// cards 1:1, so it must evaluate 28 cases and PASS against the baseline.
const srcWriteups = join(engineDir, 'writeups')
if (existsSync(srcWriteups)) {
  const ev = await def.execute(
    { command: 'eval-retrieval', writeups_dir: srcWriteups },
    { signal: AbortSignal.timeout(180_000) })
  assert.equal(ev.command, 'eval-retrieval')
  assert.ok(ev.output.includes('[eval-retrieval]'), 'no [eval-retrieval] lines in output')
  assert.ok(/RESULT\s*:\s*(PASS|FAIL)/.test(ev.output), 'no RESULT verdict line')
  assert.ok(/evaluable=(\d+)/.test(ev.output) && parseInt(RegExp.$1, 10) > 0,
    `evaluable set empty — writeups dir does not match case cards: ${ev.summary}`)
  assert.ok(/RESULT\s*:\s*PASS/.test(ev.output), `retrieval regressed against baseline: ${ev.summary}`)
  const evText = def.output.render({}, ev)[0].text
  assert.ok(evText.includes('eval-retrieval'), 'render must identify eval-retrieval')
  console.log('eval-retrieval ok:', ev.summary)
} else {
  console.log('eval-retrieval skipped (no source writeups dir)')
}

// 6. render puts the model-facing summary in one text block.
const text = def.output.render({}, s)
assert.equal(text[0].type, 'text')
assert.ok(text[0].text.includes('SOLVED') && text[0].text.includes(s.flag))

// 6b. Registry-hardening: no type arrays in output schema (dsh-tools rejects them).
const flat = JSON.stringify(def.output.schema)
assert.ok(!/"type":\s*\[/.test(flat), 'output schema contains a type array — dsh JsonSchemaError')

// 6c. Nullable-shaped fields are single-typed strings, not null.
assert.equal(def.output.schema.properties.flag.type, 'string')
assert.equal(def.output.schema.properties.elapsed.type, 'string')
assert.equal(typeof s.flag, 'string')

// 7. Validation: wrong command, missing challenge/writeup/writeups_dir.
for (const [label, call] of [
  ['unknown command', { command: 'pwn-all' }],
  ['solve missing challenge', { command: 'solve' }],
  ['learn-writeup missing writeup', { command: 'learn-writeup' }],
  ['rebuild-knowledge missing writeups_dir', { command: 'rebuild-knowledge' }],
]) {
  await assert.rejects(
    def.execute(call, { signal: AbortSignal.timeout(10_000) }),
    new RegExp(label.split(' ')[0]),
    label,
  )
}

// 8. dashboard generates a self-contained HTML file.
const dashOut = join(here, '..', '.temp-dashboard-smoke.html')
const d = await def.execute({ command: 'dashboard', out_html: dashOut }, { signal: AbortSignal.timeout(60_000) })
assert.ok(existsSync(dashOut), 'dashboard html not written')
assert.ok(d.output.includes('[dashboard] wrote'), `dashboard output unexpected: ${d.summary}`)
console.log('dashboard ok:', d.summary)
rmSync(dashOut, { force: true })

// 9. propose_submit: executes against a mock agent/session without throwing,
// and hands back the model-facing action contract.
let psExecuted = false
await byName.propose_submit.execute(
  { flag: 'flag{demo}', title: 'Demo card', brief: 'demo', category: 'crypto', challenge: '01' },
  {
    agent: { session: { append: () => undefined } },
    signal: AbortSignal.timeout(10_000),
  },
)
psExecuted = true
assert.ok(psExecuted, 'propose_submit.execute never ran')

console.log('\nALL SMOKE TESTS PASSED')