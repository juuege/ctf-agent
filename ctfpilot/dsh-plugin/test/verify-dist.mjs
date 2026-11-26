// 交付验证（2026-11-26）：模拟“他人机器”——插件复制到隔离目录，无任何
// sibling 引擎，验证三条路径：
//   A. 随包 engine/ 被自动找到，stats 可跑、账本落在 <plugin>/engine/memory；
//   B. 历史配置里的作者机绝对路径失效 → 大声告警一次后回落随包引擎（面板不废）；
//   C. 连随包引擎都没有 → 报错信息列出搜索路径并给出可行动指引（不吞错）。
// 运行：node test/verify-dist.mjs   （无需 CTFPILOT_HOME；全程不碰生产引擎）
import assert from 'node:assert/strict'
import { cpSync, rmSync, mkdirSync, existsSync } from 'node:fs'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '..')
const tmp = join(here, '.temp-dist')

function makeCopy(name, withEngine) {
  const dst = join(tmp, name)
  mkdirSync(dst, { recursive: true })
  for (const item of ['lib', 'package.json', 'node_modules', ...(withEngine ? ['engine'] : [])]) {
    cpSync(join(src, item), join(dst, item), { recursive: true })
  }
  return dst
}

function loadPlugin(dir) {
  return import(pathToFileURL(join(dir, 'lib', 'index.js')).href)
}

function bindTool(plugin) {
  const defs = []
  return plugin.apply({ tools: { register(d) { defs.push(d) } } }, {}).then(() => {
    const def = defs.find(d => d.name === 'ctfpilot')
    if (!def) throw new Error('ctfpilot tool not registered')
    return def
  })
}

const savedHome = process.env.CTFPILOT_HOME
const savedPython = process.env.CTFPILOT_PYTHON
delete process.env.CTFPILOT_HOME
delete process.env.CTFPILOT_PYTHON
rmSync(tmp, { recursive: true, force: true })
mkdirSync(tmp, { recursive: true })

try {
  // —— A. 随包引擎自动发现 ——
  const a = makeCopy('pkg-bundled', true)
  const pa = await loadPlugin(a)
  const defA = await bindTool(pa)
  const ra = await defA.execute({ command: 'stats' }, { signal: AbortSignal.timeout(120_000) })
  assert.ok(String(ra.output).includes('[stats]'), `stats output unexpected: ${ra.summary}`)
  assert.ok(existsSync(join(a, 'engine', 'memory', 'trajectories.sqlite')),
    'engine did not initialize its ledger under <plugin>/engine/memory')

  // —— B. 坏配置降级（作者机绝对路径在他机不存在）——
  const b = makeCopy('pkg-stale-config', true)
  const pb = await loadPlugin(b)
  const defsB = []
  const warnings = []
  const origError = console.error
  console.error = (...args) => { warnings.push(args.join(' ')) }
  try {
    await pb.apply({ tools: { register(d) { defsB.push(d) } } }, { engineDir: 'C:/definitely-not-here-xyz/CTFPilot-v0.1' })
    const defB = defsB.find(d => d.name === 'ctfpilot')
    const rb = await defB.execute({ command: 'stats' }, { signal: AbortSignal.timeout(120_000) })
    assert.ok(String(rb.output).includes('[stats]'), `stale-config fallback failed: ${rb.summary}`)
  } finally {
    console.error = origError
  }
  assert.ok(warnings.some(w => w.includes('falling back to auto-discovery')),
    `missing fallback warning: ${JSON.stringify(warnings)}`)

  // —— C. 无引擎报错可行动 ——
  const c = makeCopy('pkg-no-engine', false)
  const pc = await loadPlugin(c)
  const defC = await bindTool(pc)
  await assert.rejects(
    defC.execute({ command: 'stats' }, { signal: AbortSignal.timeout(30_000) }),
    (err) => /engine not found/.test(String(err && err.message)) &&
      /Looked in:/.test(String(err && err.message)) &&
      /engine/.test(String(err && err.message)),
    'no-engine error must list searched paths',
  )

  console.log('dist checks ok: bundled engine / stale-config fallback / actionable missing-engine error')
  rmSync(tmp, { recursive: true, force: true })
  console.log('\nALL DIST CHECKS PASSED')
} finally {
  if (savedHome === undefined) delete process.env.CTFPILOT_HOME
  else process.env.CTFPILOT_HOME = savedHome
  if (savedPython === undefined) delete process.env.CTFPILOT_PYTHON
  else process.env.CTFPILOT_PYTHON = savedPython
}