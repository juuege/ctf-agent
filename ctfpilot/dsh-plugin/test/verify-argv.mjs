/**
 * ctf-rsi 决策动词 → PID 的 argv 级回归（生产实锤 20261117：
 * '[error] no such proposal: adopt' —— 桥接曾把动词当 PID 发进引擎）。
 *
 * 与 verify-commands.mjs 的区别：本文件只走 rsi 决策段，运行时数秒、
 * 不跑 eval-suite 全量门——在易吞输出的会话通道里可独立完成回读。
 *   - apply(真实 lib/index.js) + 过滤副本引擎 + proposed 提案 fixture；
 *   - 回放「动词在前」位置词形式（旧 client 口径）与显式 --pid 形式；
 *   - 回读 ledger.json / proposals.jsonl 验证状态落账与历史追加；
 *   - 全程进度落盘（CTFPILOT_VERIFY_OUT），任何结局可观测。
 * 运行：$DSH_NODE_EXECUTABLE verify-argv.mjs（设 CTFPILOT_HOME）
 */
import { cpSync, mkdirSync, writeFileSync, readFileSync, appendFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'

const PROG = process.env.CTFPILOT_VERIFY_OUT || ''
function prog(m) {
  if (!PROG) return
  try { appendFileSync(PROG, `[${new Date().toISOString()}] ${m}\n`) } catch {}
}

// —— 兜底：任何结局都留痕（吞错通道下 crash 行是唯一的死亡证据）——
process.on('unhandledRejection', (reason) => {
  prog(`END CRASHED unhandledRejection ${String(reason && reason.message ? reason.message : reason).slice(0, 200)}`)
  process.exit(9)
})
process.on('uncaughtException', (err) => {
  prog(`END CRASHED uncaughtException ${String(err && err.stack ? err.stack : err).slice(0, 300)}`)
  process.exit(9)
})

const HOME = String(process.env.CTFPILOT_HOME || '').trim()
if (!HOME) {
  prog('SKIP CTFPILOT_HOME not set')
  process.exit(2)
}
const PY_CANDIDATES = [
  process.env.CTFPILOT_PYTHON,
  'python3', 'python3.12', 'python3.11', 'python3.10', 'python',
  // 已知运行时兜底排在通用候选之后（Windows PATH 常无 python）
  'C:/Users/Administrator/.local/share/TeleAgent/runtimes/python/python.exe',
].filter(Boolean)

let passed = 0
let failed = 0
function check(name, cond, detail) {
  if (cond) { passed++; prog(`ok   ${name}`) }
  else { failed++; prog(`FAIL ${name}${detail ? ` :: ${String(detail).slice(0, 180)}` : ''}`) }
}

try { writeFileSync(PROG, `[${new Date().toISOString()}] BEGIN pid=${process.pid}\n`) } catch {}

// —— 可用 Python 探测（stdout 必须带标记，防 Windows 应用商店桩）——
let PY = ''
for (const c of PY_CANDIDATES) {
  const probe = await new Promise((resolve) => {
    let p
    try { p = spawn(c, ['-c', 'print("py-ok"); import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'], { windowsHide: true }) }
    catch { resolve(null); return }
    let out = ''
    p.stdout.setEncoding('utf8').on('data', (d) => { out += d })
    p.on('error', () => resolve(null))
    p.on('close', (code) => resolve(code === null ? null : { status: code, stdout: out }))
    setTimeout(() => resolve(null), 15_000)
  })
  if (probe && probe.status === 0 && /py-ok/.test(String(probe.stdout))) { PY = c; break }
}
if (!PY) {
  prog('SKIP no usable Python found')
  process.exit(2)
}
prog(`python=${PY.slice(0, 70)}`)

// —— 过滤副本：排除 .temp / __pycache__ / trajectories* 库文件，秒级完成 ——
const dst = path.join(os.tmpdir(), `dsh-argvreg-${process.pid}`)
cpSync(HOME, dst, {
  recursive: true,
  filter: (src) => {
    const b = path.basename(src)
    return !(b === '.temp' || b === '__pycache__' || /^trajectories/i.test(b))
  },
})
prog('engine-copy done')

const MOD = await import('../lib/index.js')
const cmds = []
await MOD.apply(
  { tools: { register: () => {} }, commands: { register: (c) => cmds.push(c) } },
  { python: PY, engineDir: dst },
)
const byName = Object.fromEntries(cmds.map((c) => [c.name, c]))
if (!byName['ctf-rsi']) {
  prog('ABORT ctf-rsi not registered')
  process.exit(4)
}
prog('bridge applied')

// —— fixture：一条 proposed 提案（引擎流格式原样）——
const propPid = 'rsi-reg-' + process.pid
mkdirSync(path.join(dst, 'skills', 'rsi'), { recursive: true })
writeFileSync(
  path.join(dst, 'skills', 'rsi', 'proposals.jsonl'),
  JSON.stringify({
    id: propPid, type: 'detector', target: 'misc/xor-re',
    status: 'proposed', why: 'argv-level regression fixture', ts: '2000-01-01 00:00:00',
  }) + '\n',
  'utf8',
)
prog('fixture written')

// ① approve 位置词（旧 client 口径）且 approved 落账
prog('call:approve positional start')
const r1 = await byName['ctf-rsi'].handler({ rawInput: `approve ${propPid}` })
prog(`call:approve positional end kind=${r1.kind} text=${String(r1.text || '').slice(0, 120)}`)
check('位置词 approve 不回显动词错误', !/no such proposal: (?:adopt|approve|reject)\b/.test(String(r1.text || '')),
  String(r1.text || '').slice(0, 140))

// ② adopt 位置词：全新采纳应写链 + ledger 行（关键回归点）
prog('call:adopt positional start')
const r2 = await byName['ctf-rsi'].handler({ rawInput: `adopt ${propPid}` })
prog(`call:adopt positional end kind=${r2.kind} text=${String(r2.text || '').slice(0, 120)}`)
check('位置词 adopt 成功采纳（不再 no such proposal: adopt）',
  r2.kind === 'success' && /ADOPTED/.test(String(r2.text || '')),
  String(r2.text || '').slice(0, 140))

// ③ adopt --pid：幂等重采纳（链行已存在 → 仅追加历史，不重复链行）
prog('call:adopt --pid start')
const r3 = await byName['ctf-rsi'].handler({ rawInput: `adopt --pid ${propPid}` })
prog(`call:adopt --pid end kind=${r3.kind} text=${String(r3.text || '').slice(0, 120)}`)
check('--pid adopt 幂等成功', r3.kind === 'success' && /ADOPTED/.test(String(r3.text || '')),
  String(r3.text || '').slice(0, 140))

// ④ 回读账本：状态终态 adopted 且历史两行；版本链中本提案恰一行（幂等，无重复链行）
prog('ledger-verify start')
const ledPath = path.join(dst, 'skills', 'rsi', 'ledger.json')
const led = JSON.parse(readFileSync(ledPath, 'utf8'))
const row = led.proposals.find((p) => p.id === propPid)
const chainPath = path.join(dst, 'skills', 'arena', 'state', 'version-chain.jsonl')
const chainText = readFileSync(chainPath, 'utf8')
// 存量链可能非空：统计本提案自己的链行数（两次 adopt 应只写一行）
const mine = chainText.split('\n').filter((l) => l.trim() && l.includes(propPid))
prog(`ledger row=${JSON.stringify(row && { status: row.status, historyLen: row.history ? row.history.length : undefined })} myChainLines=${mine.length}`)
check('ledger 状态终态 adopted 且决策历史 ≥2 行',
  Boolean(row) && row.status === 'adopted' && Array.isArray(row.history) && row.history.length >= 2,
  JSON.stringify(row && { status: row.status, historyLen: row.history && row.history.length }))
check('版本链中本提案恰一行（重采纳无重复链行）', mine.length === 1, `myChainLines=${mine.length}`)
prog(`RESULT passed=${passed} failed=${failed}`)
process.exit(failed === 0 ? 0 : 1)