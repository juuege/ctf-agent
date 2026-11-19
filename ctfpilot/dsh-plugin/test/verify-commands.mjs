/**
 * 训练场 slash 命令（ctf-arena / ctf-eval-suite / ctf-research / ctf-rsi）的
 * 真实引擎冒烟测试：
 *   - 在引擎目录的临时副本上跑（不动生产账本）；
 *   - apply(ctx) 用假注册表收集已注册命令，逐个驱动 handler(rawInput)；
 *   - 断言：stdout 落词契约、错误路径文案、研究入库双门拦截。
 * 运行：$env:CTFPILOT_HOME + $DSH_NODE_EXECUTABLE verify-commands.mjs
 */
import { cpSync, existsSync, writeFileSync, appendFileSync, readFileSync, mkdirSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import * as MOD from '../lib/index.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ENGINE_SRC = process.env.CTFPILOT_HOME || ''
// 与桥接同款的可用性探测（与 runEngine 同为异步 spawn，管道语义一致）：
// 必须要求 stdout 带标记——Windows 应用商店桩以退出码 0 静默退出，不能被选作解释器。
function spawnAsync(cmd, args) {
  return new Promise((resolve) => {
    let p
    try {
      p = spawn(cmd, args, { windowsHide: true })
    } catch { resolve({ status: null, stdout: '', error: true }); return }
    let out = ''
    let done = false
    const finish = (status) => { if (!done) { done = true; resolve({ status, stdout: out }) } }
    try {
      p.stdout.setEncoding('utf8').on('data', (d) => { out += d })
      p.on('error', () => finish(null))
      p.on('close', (c) => finish(c))
    } catch { finish(null) }
    setTimeout(() => finish(null), 20_000)
  })
}
async function pythonUsableAsync(c) {
  const r = await spawnAsync(c, ['-c', 'print("py-ok"); import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'])
  return r.status === 0 && /py-ok/.test(String(r.stdout || ''))
}
// 已知运行时兜底排在通用候选之后（保持通用优先）。
const PY_CANDIDATES = [
  process.env.CTFPILOT_PYTHON,
  'python3', 'python3.12', 'python3.11', 'python3.10', 'python',
  'C:/Users/Administrator/.local/share/TeleAgent/runtimes/python/python.exe',
].filter(Boolean)
let PYTHON = ''
for (const c of PY_CANDIDATES) { if (await pythonUsableAsync(c)) { PYTHON = c; break } }
if (!PYTHON) {
  const msg = 'SKIP: no usable Python found (bridge would reject stubs too)'
  console.error(msg)
  if (process.env.CTFPILOT_VERIFY_OUT) { try { writeFileSync(process.env.CTFPILOT_VERIFY_OUT, 'SKIP ' + msg + '\n') } catch {} }
  process.exit(2)
}

let passed = 0
let failed = 0
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}`) }
  else { failed++; console.error(`  FAIL ${name}${detail ? ` :: ${String(detail).slice(0, 200)}` : ''}`) }
}

// 进度落盘：套件启动即写 BEGIN、每步推进、结束写 END——任何结局都可观测
// （工具通道偶发吞输出/后台化时，结果文件是唯一的真相回读路径）。
const PROG_OUT = process.env.CTFPILOT_VERIFY_OUT || ''
function prog(msg) {
  if (!PROG_OUT) return
  try { appendFileSync(PROG_OUT, `[${new Date().toISOString()}] ${msg}\n`) } catch {}
}
try { writeFileSync(PROG_OUT, `[${new Date().toISOString()}] BEGIN psmoke pid=${process.pid}\n`) } catch {}

if (!existsSync(path.join(ENGINE_SRC, 'ctfpilot.py'))) {
  const msg = `SKIP: engine not found at ${ENGINE_SRC}`
  console.error(msg)
  if (process.env.CTFPILOT_VERIFY_OUT) { try { writeFileSync(process.env.CTFPILOT_VERIFY_OUT, 'SKIP ' + msg + '\n') } catch {} }
  process.exit(2)
}

// 临时引擎副本：保留 memory/ 下的 .py 包模块（ctfpilot.py 顶层 import 需要）
// 与 skills/（chain/snapshot 路径需要），只清掉生产轨迹库文件——隔离又不破坏导入。
// 引擎目录实测 380MB+（.temp 手术备份 + 生产库），无过滤整包复制会分钟级挂起，
// 必须在复制层就排除：.temp 目录、trajectories* 非 .py/.json 文件（含 -wal/-shm/
// .holdout/pre-* 备份）、__pycache__。
const dst = path.join(os.tmpdir(), `dsh-cmd-smoke-${process.pid}`)
rmSync(dst, { recursive: true, force: true })
function engineCopyFilter(src) {
  const base = path.basename(src)
  if (base === '.temp' || base === '__pycache__') return false
  if (/^trajectories/i.test(base) && !/\.(py|json)$/i.test(base)) return false
  return true
}
cpSync(ENGINE_SRC, dst, { recursive: true, filter: engineCopyFilter })
for (const f of ['trajectories.sqlite', 'trajectories.sqlite-wal', 'trajectories.sqlite-shm']) {
  rmSync(path.join(dst, 'memory', f), { force: true })
}
prog('phase:engine-copy done')

async function main() {
  const commands = []
  const ctx = {
    tools: { register: () => {} },
    commands: { register: (c) => commands.push(c) },
  }
  await MOD.apply(ctx, { python: PYTHON, engineDir: dst })

  const byName = Object.fromEntries(commands.map((c) => [c.name, c]))
  check('four training commands registered',
    ['ctf-arena', 'ctf-eval-suite', 'ctf-research', 'ctf-rsi'].every((n) => byName[n]),
    'missing: ' + commands.map((c) => c.name).join(','))
  prog('phase:engine-cmds done')

  // ---- ctf-arena ----
  prog('step:arena-stats-before')
  const arenaStats = await byName['ctf-arena'].handler({ rawInput: 'stats' })
  check('arena stats contract', arenaStats.kind === 'success' && /tiers recomputed/.test(arenaStats.text),
    `kind=${arenaStats.kind} text=${String(arenaStats.text || '').slice(0, 120)}`)
  prog('step:arena-stats-after')
  const arenaReg = await byName['ctf-arena'].handler({
    rawInput: 'register --name smoke-arena --category misc --source smoke-test',
  })
  check('arena register contract', arenaReg.kind === 'success' && /registered/.test(arenaReg.text),
    `kind=${arenaReg.kind} text=${String(arenaReg.text || '').slice(0, 140)}`)
  prog('step:arena-reg-after')
  const arenaRec = await byName['ctf-arena'].handler({
    rawInput: 'record --name smoke-arena --category misc --fail',
  })
  check('arena record contract', arenaRec.kind === 'success' && /solves=/.test(arenaRec.text),
    `kind=${arenaRec.kind} text=${String(arenaRec.text || '').slice(0, 140)}`)
  prog('step:arena-rec-after')
  const arenaBad = await byName['ctf-arena'].handler({ rawInput: 'bogus' })
  check('arena error path', arenaBad.kind === 'error' && /需要子动作/.test(arenaBad.text),
    `kind=${arenaBad.kind} text=${String(arenaBad.text || '').slice(0, 100)}`)
  prog('phase:arena done')

  // ---- ctf-research：入库双门拦截 ----
  const topic = 'smoke-topic-' + process.pid
  prog('step:r-add-before')
  const rAdd = await byName['ctf-research'].handler({ rawInput: `add ${topic}` })
  check('research add contract', rAdd.kind === 'success' && /queued/.test(rAdd.text),
    `kind=${rAdd.kind} text=${String(rAdd.text || '').slice(0, 140)}`)
  prog('step:r-add-after')
  prog('step:r-ingest0-before')
  const rIngest0 = await byName['ctf-research'].handler({ rawInput: `ingest --topic ${topic}` })
  check('research ingest blocked at 0 pointers', rIngest0.kind === 'error' && /INGEST BLOCKED/.test(rIngest0.text),
    `kind=${rIngest0.kind} text=${String(rIngest0.text || '').slice(0, 160)}`)
  const ptrA = path.join(dst, 'README.md')
  const ptrB = 'https://example.org/smoke-writeup'
  const rEv = await byName['ctf-research'].handler(
    { rawInput: `evidence --topic ${topic} --pointer ${ptrA} --pointer ${ptrB}` })
  check('research evidence x2 contract', rEv.kind === 'success' && /topics ready for ingest: 1/.test(rEv.text),
    `kind=${rEv.kind} text=${String(rEv.text || '').slice(0, 140)}`)
  prog('step:r-ev-after')
  prog('step:r-ingest1-before')
  const rIngest1 = await byName['ctf-research'].handler({ rawInput: `ingest --topic ${topic}` })
  // 引擎契约：证据门（≥2 指针）通过、草稿结构含标签行即视为 ready-to-ingest，
  // 输出可执行的下一步路由命令（learn-writeup + rebuild-knowledge）；
  // "内容是否写完"由人判断，不在结构检查范围内。未过证据门时才是 INGEST BLOCKED。
  const routed = /ready to ingest/.test(String(rIngest1.text || '')) &&
    /learn-writeup/.test(String(rIngest1.text || '')) &&
    !/INGEST BLOCKED/.test(String(rIngest1.text || ''))
  check('research double gate: evidence gate passes with 2 pointers', routed,
    `text=${String(rIngest1.text || '').slice(0, 200)}`)
  prog('phase:research done')

  // ---- ctf-eval-suite：隔离内存上完整跑一遍 ----
  prog('step:es-before')
  const es = await byName['ctf-eval-suite'].handler({ rawInput: '' })
  const esText = String(es.text || '')
  check('eval-suite gate contract', /\bRESULT\s*:\s*(PASS|FAIL)\b/.test(esText) && /\[eval-suite\]/.test(esText),
    `kind=${es.kind} text=${esText.slice(0, 160)}`)
  // outcome 硬化契约：PASS 只允许 success，FAIL 必须显形为 error。
  const failSeen = /\bRESULT\s*:\s*FAIL\b/.test(esText)
  const passSeen = /\bRESULT\s*:\s*PASS\b/.test(esText)
  check('eval-suite outcome hardening', (failSeen ? es.kind === 'error' : true) &&
    (!passSeen || es.kind === 'success'),
    `kind=${es.kind} tail=${esText.slice(-280)}`)

  // ---- ctf-rsi ----
  prog('phase:rsi-list start')
  const rsiList = await byName['ctf-rsi'].handler({ rawInput: 'list' })
  check('rsi list contract', rsiList.kind === 'success' && /rsi proposals/.test(rsiList.text),
    `kind=${rsiList.kind} text=${String(rsiList.text || '').slice(0, 140)}`)
  const rsiBad = await byName['ctf-rsi'].handler({ rawInput: 'bogus' })
  check('rsi error path', rsiBad.kind === 'error' && /需要子动作/.test(rsiBad.text),
    `kind=${rsiBad.kind} text=${String(rsiBad.text || '').slice(0, 100)}`)
  prog('phase:rsi-list done')

  // ---- ctf-rsi 决策动词 → PID argv 级回归（生产实锤 20261117：
  //      [error] no such proposal: adopt —— 桥接曾把动词当 PID 发进引擎）----
  prog('phase:argv-regression start')
  const propPid = 'rsi-reg-' + process.pid
  mkdirSync(path.join(dst, 'skills', 'rsi'), { recursive: true })
  const rec = { id: propPid, type: 'detector', target: 'misc/xor-re',
    status: 'proposed', why: 'regression fixture probe', ts: '2000-01-01 00:00:00' }
  writeFileSync(path.join(dst, 'skills', 'rsi', 'proposals.jsonl'),
    JSON.stringify(rec) + '\n', 'utf8')
  // 位置词形式（client 旧口径）：动词在前、PID 在后。桥接现在必须跳过动词，
  // 错误不得回显「no such proposal: <verb>」。
  prog('step:argv-adopt-pos-before')
  const adoptPos = await byName['ctf-rsi'].handler({ rawInput: `adopt ${propPid}` })
  const adoptOut = String(adoptPos.text || '')
  check('argv 回归: 位置词采纳不再把动词当 PID（错误不得回显动词）',
    !/no such proposal: (?:adopt|approve|reject)\b/.test(adoptOut),
    adoptOut.slice(0, 160))
  // 显式 --pid 形式：幂等双写安全（引擎链行已存在→只追加历史）。
  prog('step:argv-adopt-flag-before')
  const adoptFlag = await byName['ctf-rsi'].handler({ rawInput: `adopt --pid ${propPid}` })
  const ledAfter = JSON.parse(readFileSync(path.join(dst, 'skills', 'rsi', 'ledger.json'), 'utf8'))
  const row = ledAfter.proposals.find((p) => p.id === propPid)
  check('argv 回归: --pid 形式采纳记入 ledger 且状态为 adopted',
    Boolean(row) && row.status === 'adopted',
    `ledger-proposals=${ledAfter.proposals.length}`)
  check('argv 回归: --pid 形式成功回报不含 [error] / FAIL',
    adoptFlag.kind === 'success' && !/\[error\]|\bFAIL\b/i.test(String(adoptFlag.text || '')),
    String(adoptFlag.text || '').slice(0, 160))
  prog('step:argv-approve-before')
  const approveFlag = await byName['ctf-rsi'].handler({ rawInput: `approve --pid ${propPid}` })
  check('argv 回归: approve 状态词与动词全集共存（-p 短前缀不误吞）',
    approveFlag.kind === 'success' && /approved/.test(String(approveFlag.text || '')),
    String(approveFlag.text || '').slice(0, 120))

  // ---- ctf-solve-start：官方 UserMessage 契约级回归 ----
  // 生产实锤 20261119：followup 消息缺 id 与 source（UserMessage 必填，官方
  // schedule 插件用 createUserMessage + source:{kind:'plugin'}），消息在
  // inbox/投影层被拒又被 catch 吞掉 → 题面从未真正进入会话。
  const sent = []
  const fakeAgent = {
    followup: (m) => { sent.push({ via: 'followup', m }) },
    steer: (m) => { sent.push({ via: 'steer', m }) },
  }
  const solveOk = await byName['ctf-solve-start'].handler({
    agent: fakeAgent,
    rawInput: 'quick --category "crypto challenge" --attach "C:/my dir/a.zip" "题目：解出这个 RSA 题" flag{contains_space} 检查',
  })
  check('solve-start 成功路径: followup 收到消息且回报 success',
    solveOk.kind === 'success' && sent.length === 1 && sent[0].via === 'followup',
    `kind=${solveOk.kind} sent=${sent.length}`)
  const msg = sent[0] && sent[0].m
  check('solve-start: 消息带 id 与 producer-owned source.kind（format v4：非 plugin、非空）',
    Boolean(msg) && typeof msg.id === 'string' && /^[0-9a-f-]{36}$/i.test(msg.id) &&
      msg.source && msg.source.kind === 'ctfpilot' && msg.source.plugin === undefined,
    JSON.stringify(msg && { id: msg.id, source: msg.source }))
  check('solve-start: content 是 text block 数组（官方消息形态）',
    Boolean(msg && Array.isArray(msg.content) && msg.content[0] &&
      msg.content[0].type === 'text' && typeof msg.content[0].text === 'string'),
    JSON.stringify(msg && msg.content))
  check('solve-start: 引号感知 tokenizer——含空格路径/分类/题面不被切碎',
    msg && /C:\/my dir\/a\.zip/.test(msg.content[0].text) &&
      /crypto challenge/.test(msg.content[0].text) &&
      /RSA/.test(msg.content[0].text),
    msg && msg.content[0].text.slice(0, 200))
  check('solve-start: 首个位置词被当模式词消费（quick 不泄漏进题面正文）',
    msg && !/题目描述：\s*quick/.test(msg.content[0].text),
    msg && msg.content[0].text.slice(0, 120))
  // 面板同口径回放：正文整段带引号（即使单词也包裹），位置模式词在前。
  sent.length = 0
  await byName['ctf-solve-start'].handler({
    agent: fakeAgent,
    rawInput: 'quick "quick 单词正文测试"',
  })
  const quotedMsg = sent[0] && sent[0].m
  check('solve-start: 引号内正文的 quick 单词不被模式词消费（面板同口径）',
    quotedMsg && /quick 单词正文测试/.test(quotedMsg.content[0].text),
    quotedMsg && quotedMsg.content[0].text.slice(0, 160))
  const solveNoAgent = await byName['ctf-solve-start'].handler({
    rawInput: 'quick 题面文字',
  })
  check('solve-start: 无 agent 时显式 error（不再伪装 success 返回文本）',
    solveNoAgent.kind === 'error' && /手动发送/.test(solveNoAgent.text),
    `kind=${solveNoAgent.kind}`)

  // ---- ctf-writeup：cancel 同步契约 + steer 链路 ----
  const cancelLog = []
  const fakeAgent2 = {
    followup: (m) => { sent.push({ via: 'followup', m }) },
    steer: (m) => { sent.push({ via: 'steer', m }) },
    cancel: (cause, opts) => { cancelLog.push({ cause, opts }); return undefined },
  }
  const wu = await byName['ctf-writeup'].handler({ agent: fakeAgent2, rawInput: '--name smoketest' })
  check('writeup: agent.cancel 收到 {kind:user}+keepInbox，steer 注入完整消息',
    cancelLog.length === 1 && cancelLog[0].cause.kind === 'user' &&
      cancelLog[0].opts && cancelLog[0].opts.keepInbox === true &&
      wu.kind === 'success' &&
      sent.some((s) => s.via === 'steer' && s.m.source && s.m.source.kind === 'ctfpilot'),
    `cancel=${JSON.stringify(cancelLog)} kind=${wu.kind}`)

  console.log(`\ncommand bridge smoke：${passed} 通过，${failed} 失败`)
  return failed === 0
}

main()
  .then((ok) => {
    rmSync(dst, { recursive: true, force: true })
    // append（不是 write）：保留 BEGIN→END 全轨迹供回读
    prog('SUMMARY ' + `command bridge smoke：${passed} 通过，${failed} 失败`)
    if (process.env.CTFPILOT_VERIFY_OUT) {
      try { appendFileSync(process.env.CTFPILOT_VERIFY_OUT, `command bridge smoke：${passed} 通过，${failed} 失败\n`) } catch {}
    }
    process.exit(ok ? 0 : 1)
  })
  .catch((e) => {
    console.error('smoke crashed:', e)
    prog('END CRASHED ' + String(e && e.stack ? e.stack : e).slice(0, 400))
    if (process.env.CTFPILOT_VERIFY_OUT) {
      try { appendFileSync(process.env.CTFPILOT_VERIFY_OUT, `CRASHED: ${String(e && e.stack ? e.stack : e).slice(0, 600)}\n`) } catch {}
    }
    rmSync(dst, { recursive: true, force: true })
    process.exit(1)
  })