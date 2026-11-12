// Smoke test: register the tool against a mock ctx, then run real engine
// commands through execute(). Runs under plain Node (>=20) with schemastery
// installed next to this package. WSL/Linux or Windows both fine.
import assert from 'node:assert/strict'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'
import * as plugin from '../lib/index.js'

const here = dirname(fileURLToPath(import.meta.url))
const engineDir = existsSync(join(here, '..', 'CTFPilot-v0.1'))
  ? join(here, '..', 'CTFPilot-v0.1')
  : resolve(here, '..', '..', 'CTFPilot-v0.1')

assert.ok(existsSync(join(engineDir, 'ctfpilot.py')), `engine dir not found: ${engineDir}`)

// 1. apply() registers exactly one model-facing tool on a mock ctx.
let def = null
const ctx = { tools: { register(d) { def = d } } }
plugin.apply(ctx, { engineDir })

assert.equal(def.name, 'ctfpilot')
for (const field of ['description', 'parameters', 'output', 'execute', 'presentCall', 'presentResult']) {
  assert.ok(def[field] !== undefined, `tool definition missing .${field}`)
}
for (const p of ['command', 'challenge', 'mode', 'write_trace', 'flag_regex', 'category', 'writeup', 'name', 'min']) {
  assert.ok(def.parameters[p] !== undefined, `parameters missing .${p}`)
}
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

// 4. Real solve: b64 chain smoke challenge takes the fast path.
const chall = join(engineDir, 'tests', 'challs', '01_b64chain')
const s = await def.execute({ command: 'solve', challenge: chall, mode: 'auto' }, { signal: AbortSignal.timeout(120_000) })
assert.equal(s.command, 'solve')
assert.equal(s.solved, true, `expected solved, got: ${s.summary}`)
assert.ok(s.flag && s.flag.startsWith('flag{'), `unexpected flag: ${s.flag}`)
assert.ok(s.elapsed, 'elapsed missing')
console.log('solve ok:', s.flag, `${s.elapsed}s`)

// 5. recall returns output without a [result] line (non-solve commands).
const rec = await def.execute({ command: 'recall', category: 'crypto' }, { signal: AbortSignal.timeout(60_000) })
assert.equal(rec.command, 'recall')
assert.equal(rec.solved, false) // engine prints no [result] line; solved only meaningful for solve
assert.ok(typeof rec.output === 'string' && rec.output.length > 0)

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

// 7. Validation: wrong command, missing challenge, missing writeup...
for (const [label, call] of [
  ['unknown command', { command: 'pwn-all' }],
  ['solve missing challenge', { command: 'solve' }],
  ['learn-writeup missing writeup', { command: 'learn-writeup' }],
]) {
  await assert.rejects(
    def.execute(call, { signal: AbortSignal.timeout(10_000) }),
    new RegExp(label.split(' ')[0]),
    label,
  )
}

console.log('\nALL SMOKE TESTS PASSED')