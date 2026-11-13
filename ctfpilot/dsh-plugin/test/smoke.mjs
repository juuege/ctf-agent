// Smoke test: register the tool against a mock ctx, then run real engine
// commands through execute(). Runs under plain Node (>=20) with schemastery
// installed next to this package.
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

// 1. apply() registers exactly one model-facing tool on a mock ctx.
let def = null
const ctx = { tools: { register(d) { def = d } } }
plugin.apply(ctx, cfg)

assert.equal(def.name, 'ctfpilot')
for (const field of ['description', 'parameters', 'output', 'execute', 'presentCall', 'presentResult']) {
  assert.ok(def[field] !== undefined, `tool definition missing .${field}`)
}
// SOTA parameter surface: solve / recall --query / learn-writeup /
// rebuild-knowledge / fixations / dashboard.
for (const p of ['command', 'challenge', 'mode', 'write_trace', 'flag_regex', 'category',
  'query', 'writeup', 'name', 'writeups_dir', 'dry_run', 'min', 'out_html']) {
  assert.ok(def.parameters[p] !== undefined, `parameters missing .${p}`)
}
assert.ok(plugin.COMMANDS.includes('rebuild-knowledge'), 'COMMANDS must expose rebuild-knowledge')
assert.ok(plugin.COMMANDS.includes('dashboard'), 'COMMANDS must expose dashboard')
assert.ok(def.description.includes('rebuild-knowledge'), 'description must document rebuild-knowledge')
assert.ok(def.description.includes('--query'), 'description must document recall --query')
assert.equal(def.presentCall({ command: 'solve', challenge: '/tmp/x' }).card, 'terminal')

// 2. Bad engineDir surfaces a readable error at execute time (mounting stays lazy).
plugin.apply({ tools: { register() {} } }, { engineDir: '/nonexistent-xyz' }) // must not throw
let badDef = null
plugin.apply({ tools: { register(d) { badDef = d } } }, { engineDir: 'C:\\bogus' })
await assert.rejects(badDef.execute({ command: 'stats' }, { signal: AbortSignal.timeout(10_000) }), /ctfpilot\.py/)

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
const writeupsDir = 'C:/Users/Administrator/.local/share/TeleAgent/TeleAgent的工作空间/定时任务产出/ctf-20min'
if (existsSync(writeupsDir)) {
  const rk = await def.execute(
    { command: 'rebuild-knowledge', writeups_dir: writeupsDir, dry_run: true },
    { signal: AbortSignal.timeout(180_000) })
  assert.equal(rk.command, 'rebuild-knowledge')
  assert.ok(rk.output.includes('[knowledge]'), `no [knowledge] lines in output: ${rk.summary}`)
  assert.ok(rk.output.includes('dry run, nothing written'), 'dry-run must not write')
  console.log('rebuild-knowledge dry-run ok:', rk.summary)

  // 5c. eval-retrieval returns its regression verdict as a VALUE (a FAIL
  // verdict is a domain result with exit 1 — must not throw as infra error).
  const ev = await def.execute(
    { command: 'eval-retrieval', writeups_dir: writeupsDir },
    { signal: AbortSignal.timeout(180_000) })
  assert.equal(ev.command, 'eval-retrieval')
  assert.ok(ev.output.includes('[eval-retrieval]'), 'no [eval-retrieval] lines in output')
  assert.ok(/RESULT\s*:\s*(PASS|FAIL)/.test(ev.output), 'no RESULT verdict line')
  const evText = def.output.render({}, ev)[0].text
  assert.ok(evText.includes('eval-retrieval'), 'render must identify eval-retrieval')
  console.log('eval-retrieval ok:', ev.summary)
} else {
  console.log('rebuild-knowledge/eval-retrieval skipped (no local writeups dir)')
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

console.log('\nALL SMOKE TESTS PASSED')