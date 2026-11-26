/**
 * 训练场 slash 命令（ctf-arena / ctf-eval-suite / ctf-research / ctf-rsi）的
 * 真实引擎冒烟测试：
 *   - 在引擎目录的临时副本上跑（不动生产账本）；
 *   - apply(ctx) 用假注册表收集已注册命令，逐个驱动 handler(rawInput)；
 *   - 断言：stdout 落词契约、错误路径文案、研究入库双门拦截。
 * 运行：$env:CTFPILOT_HOME + $DSH_NODE_EXECUTABLE verify-commands.mjs
 */
import { cpSync, existsSync, writeFileSync, appendFileSync, readFileSync, mkdirSync, rmSync, utimesSync } from 'node:fs'
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

  // ---- arena stats 签名缓存（2026-11-20 改造：registry 内容签名 + attempts mtime）----
  // 上面的 record 已作废缓存 → 此处首查真跑并重建；stats 每次回写 registry 刷
  // updated 字段——内容签名剥离 updated 后命中不受影响（生产实锤修复点）。
  const arenaCT0 = Date.now()
  await byName['ctf-arena'].handler({ rawInput: 'stats' })
  const arenaCT1 = Date.now() - arenaCT0
  check('arena stats 缓存: 记练后首查真跑（引擎级耗时）', arenaCT1 > 150, `ms=${arenaCT1}`)
  const arenaCT2 = Date.now()
  const arenaCached = await byName['ctf-arena'].handler({ rawInput: 'stats' })
  const arenaCT3 = Date.now() - arenaCT2
  check('arena stats 缓存: stats 回写 registry 不破坏内容签名（命中毫秒级）',
    arenaCached.kind === 'success' && arenaCT3 < 100, `ms=${arenaCT3}`)

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

  // ---- rsi list 签名缓存（2026-11-20 改造）：argv 回归段刚写过
  //      proposals.jsonl、approve 又是写动作（主动作废）→ 首查必真跑；
  //      随后追加 fixture 验证纯签名失效（无写动作）；最后新签名下再命中。
  const rsiCT0 = Date.now()
  const rsiListCached1 = await byName['ctf-rsi'].handler({ rawInput: 'list' })
  const rsiCT1 = Date.now() - rsiCT0
  check('rsi list 缓存: argv 回归写动作后首查真跑',
    rsiListCached1.kind === 'success' && rsiCT1 > 100, `ms=${rsiCT1}`)
  const rsiCT2 = Date.now()
  const rsiListCached2 = await byName['ctf-rsi'].handler({ rawInput: 'list' })
  const rsiCT3 = Date.now() - rsiCT2
  check('rsi list 缓存: 数据源未动时命中（毫秒级，不重跑 Python）', rsiCT3 < 100, `ms=${rsiCT3}`)
  const propPid2 = 'rsi-reg2-' + process.pid
  writeFileSync(path.join(dst, 'skills', 'rsi', 'proposals.jsonl'),
    JSON.stringify({ id: propPid2, type: 'detector', target: 'misc/cache-fixture',
      status: 'proposed', why: 'cache invalidation fixture', ts: '2000-01-01 00:00:00' }) + '\n',
    { flag: 'a', encoding: 'utf8' })
  const rsiCT4 = Date.now()
  const rsiListAfterWrite = await byName['ctf-rsi'].handler({ rawInput: 'list' })
  const rsiCT5 = Date.now() - rsiCT4
  // 引擎契约：rsi list 显示层是 ledger.json 提案视图；proposals.jsonl 只是
  // append-only 原始流，新行不进 list 输出——断言只验签名失效真跑。
  check('rsi list 缓存: proposals 追加后签名失效真跑',
    rsiListAfterWrite.kind === 'success' && rsiCT5 > 100, `ms=${rsiCT5}`)
  const rsiCT6 = Date.now()
  await byName['ctf-rsi'].handler({ rawInput: 'list' })
  const rsiCT7 = Date.now() - rsiCT6
  check('rsi list 缓存: 新签名下再次命中', rsiCT7 < 100, `ms=${rsiCT7}`)

  // ---- ctf-solve-start：官方 UserMessage 契约级回归 ----
  // 生产实锤 20261119：followup 消息缺 id 与 source（UserMessage 必填，官方
  // schedule 插件用 createUserMessage + source:{kind:'plugin'}），消息在
  // inbox/投影层被拒又被 catch 吞掉 → 题面从未真正进入会话。
  // 2026-11-19 删降级后：成功路径必须有 agentPresets 服务（预设切不过去
  // 就是 error 终止，不再「照常注入」），故此处用带服务的 ctx 重新驱动。
  const sent = []
  const fakeAgent = {
    followup: (m) => { sent.push({ via: 'followup', m }) },
    steer: (m) => { sent.push({ via: 'steer', m }) },
  }
  const presetSvcOk = {
    select: async () => 'ctf-quick',
    list: async () => [{ id: 'ctf-quick' }, { id: 'ctf-deep' }],
    resolve: async (id) => ({ id }),
  }
  const okCmds = []
  await MOD.apply({
    tools: { register: () => {} },
    commands: { register: (c) => okCmds.push(c) },
    agentPresets: presetSvcOk,
  }, { python: PYTHON, engineDir: dst })
  const solveOkCmd = okCmds.find((c) => c.name === 'ctf-solve-start')
  const solveOk = await solveOkCmd.handler({
    agent: fakeAgent,
    rawInput: 'quick --category "crypto challenge" --attach "C:/my dir/a.zip" "题目：解出这个 RSA 题" flag{contains_space} 检查',
  })
  check('solve-start 成功路径: 预设已切到 + followup 收到消息 + 回报 success',
    solveOk.kind === 'success' && /已切到「快解」/.test(String(solveOk.text || '')) &&
      sent.length === 1 && sent[0].via === 'followup',
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
  await solveOkCmd.handler({
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

  // ---- 预设切换失败 = 终止（2026-11-19 用户令：删光吞错降级）----
  // 场景 A：服务不可达（假 ctx 无 agentPresets，root 兜底也空）→ error 终止不注入。
  sent.length = 0
  const solveNoSvc = await byName['ctf-solve-start'].handler({
    agent: { followup: (m) => { sent.push({ via: 'followup', m }) }, steer: () => {} },
    rawInput: 'quick 题面',
  })
  check('solve-start 删降级 A: 服务不可达 → error 终止、不注入、真因上屏',
    solveNoSvc.kind === 'error' && /已终止/.test(solveNoSvc.text) &&
      /服务不可达/.test(solveNoSvc.text) && !/已切到/.test(solveNoSvc.text) &&
      sent.length === 0,
    `kind=${solveNoSvc.kind} sent=${sent.length} text=${String(solveNoSvc.text || '').slice(0, 200)}`)

  // 场景 A2：服务只挂在 ctx.root —— root 兜底解析链必须命中并真切换成功。
  {
    const called = []
    const cmdsRoot = []
    await MOD.apply({
      tools: { register: () => {} },
      commands: { register: (c) => cmdsRoot.push(c) },
      root: {
        agentPresets: {
          select: async (a, p) => { called.push(p); return p },
          list: async () => [{ id: 'ctf-quick' }, { id: 'ctf-deep' }],
          resolve: async (id) => ({ id }),
        },
      },
    }, { python: PYTHON, engineDir: dst })
    const solveRoot = await cmdsRoot.find((c) => c.name === 'ctf-solve-start').handler({
      agent: { followup: () => {}, steer: () => {} }, rawInput: 'quick 题面',
    })
    check('solve-start 失败分类 A2: 服务挂在 ctx.root 时兜底命中、切到 ctf-quick',
      /已切到「快解」/.test(String(solveRoot.text || '')) && called.length === 1 && called[0] === 'ctf-quick',
      String(solveRoot.text || '').slice(0, 200))
  }

  // 场景 B/C：服务可达但 select 抛错 → locked / not-found 各自如实上屏。
  async function driveSolveWithPresetSvc(errMaker) {
    const cmds = []
    await MOD.apply({
      tools: { register: () => {} },
      commands: { register: (c) => cmds.push(c) },
      agentPresets: {
        select: errMaker,
        list: async () => [{ id: 'ctf-quick' }, { id: 'ctf-deep' }],
        resolve: async (id) => ({ id }),
      },
    }, { python: PYTHON, engineDir: dst })
    const c = cmds.find((x) => x.name === 'ctf-solve-start')
    return c.handler({ agent: { followup: () => {}, steer: () => {} }, rawInput: 'quick 题面' })
  }
  const lockedErr = new Error('This session has already started')
  lockedErr.code = 'agent-preset/locked'
  const solveLocked = await driveSolveWithPresetSvc(async () => { throw lockedErr })
  check('solve-start 删降级 B: locked → error 终止、不注入、上屏「已开始过对话」',
    solveLocked.kind === 'error' && /已终止/.test(solveLocked.text) &&
      /已开始过对话/.test(solveLocked.text) && !/已切到/.test(solveLocked.text) &&
      !/未注册|服务不可达/.test(solveLocked.text),
    String(solveLocked.text || '').slice(0, 200))
  const nfErr = new Error('Unknown agent preset: ctf-quick (available: standard)')
  nfErr.code = 'agent-preset/not-found'
  const solve404 = await driveSolveWithPresetSvc(async () => { throw nfErr })
  check('solve-start 删降级 C: not-found → error 终止、上屏「未注册到 roster」而非「锁定」',
    solve404.kind === 'error' && /已终止/.test(solve404.text) && /未注册到 roster/.test(solve404.text) &&
      !/已开始过对话/.test(solve404.text) && !/已切到/.test(solve404.text),
    String(solve404.text || '').slice(0, 200))

  // ---- ctf-preset-audit：诊断探针四段输出 ----
  const audit = await byName['ctf-preset-audit'].handler({})
  const auditText = String(audit.text || '')
  check('preset-audit: 输出 service / roster / resolve / turnBoundary 四段',
    audit.kind === 'success' && /^service: /m.test(auditText) &&
      /^roster: /m.test(auditText) && /^resolve\(ctf-quick\): /m.test(auditText) &&
      /^resolve\(ctf-deep\): /m.test(auditText) && /^turnBoundary: /m.test(auditText),
    auditText.slice(0, 240))
  check('preset-audit: 服务缺席时如实报 unreachable（不崩溃不谎报）',
    /service: unreachable \(absent on ctx and ctx\.root\)/.test(auditText) && /resolve\(ctf-quick\): n\/a/.test(auditText),
    auditText.slice(0, 240))
  const auditSvcCmds = []
  await MOD.apply({
    tools: { register: () => {} },
    commands: { register: (c) => auditSvcCmds.push(c) },
    agentPresets: {
      select: async () => {},
      list: async () => [{ id: 'standard' }, { id: 'ctf-quick', broken: 'mount failed: x' }],
      resolve: async (id) => (id === 'ctf-quick' ? { id, broken: 'mount failed: x' } : { id }),
    },
    root: {},
    sessionProjections: { stateOf: () => ({ openTurnStartSeq: null, lastTurn: 0 }) },
  }, { python: PYTHON, engineDir: dst })
  const audit2 = await auditSvcCmds.find((c) => c.name === 'ctf-preset-audit').handler({
    agent: { followup: () => {}, session: { id: 'sess-x' } },
  })
  const audit2Text = String(audit2.text || '')
  check('preset-audit: 服务可达时报 roster 行（含 broken 标记）与 turnBoundary 锁定态规则',
    /service: reachable/.test(audit2Text) && /ctf-quick \[broken:/.test(audit2Text) &&
      /resolve\(ctf-quick\): ok \[broken:/.test(audit2Text) &&
      /turnBoundary: \{"openTurnStartSeq":null/.test(audit2Text),
    audit2Text.slice(0, 300))

  // ---- ctf-writeup：cancel 同步契约 + steer 链路 ----
  const cancelLog = []
  const fakeAgent2 = {
    followup: (m) => { sent.push({ via: 'followup', m }) },
    steer: (m) => { sent.push({ via: 'steer', m }) },
    cancel: (cause, opts) => { cancelLog.push({ cause, opts }); return undefined },
  }
  const wu = await byName['ctf-writeup'].handler({ agent: fakeAgent2, rawInput: '--name smoketest' })
  check('writeup: agent.cancel 收到 {kind:user}+keepInbox，followup 注入完整消息（steer 取消后会被丢弃）',
    cancelLog.length === 1 && cancelLog[0].cause.kind === 'user' &&
      cancelLog[0].opts && cancelLog[0].opts.keepInbox === true &&
      wu.kind === 'success' &&
      sent.some((s) => s.via === 'followup' && s.m.source && s.m.source.kind === 'ctfpilot'),
    `cancel=${JSON.stringify(cancelLog)} kind=${wu.kind} sent=${JSON.stringify(sent.map((s) => s.via))}`)
  // writeup 注入失败路径：原始错误必须带出（删光吞错）。
  const wuBad = await byName['ctf-writeup'].handler({ rawInput: '--name smoketest' })
  check('writeup: 无 agent 时 error 且带原始原因（不再静默折叠）',
    wuBad.kind === 'error' && /注入失败/.test(String(wuBad.text || '')),
    String(wuBad.text || '').slice(0, 200))

  // ---- ctf-continue：未解出 → 同会话开新回合续攻（fork 废弃后的唯一通道）----
  // 2026-11-25 用户方案：fork 出的新会话不能切换 agent 预设（回合历史被继承
  // → 预设锁定），也无法正确接续原对话；改为在原会话直接 followup 新回合。
  const contSent = []
  let contCanceled = 0
  const contAgent = {
    followup: (m) => { contSent.push({ via: 'followup', m }) },
    steer: (m) => { contSent.push({ via: 'steer', m }) },
    cancel: () => { contCanceled++ },
    session: { id: 'sess-continue' },
  }
  const cont = await byName['ctf-continue'].handler({
    agent: contAgent,
    rawInput: '--category web --target "http://10.0.0.5:8080" --attach "C:/tmp/a.zip" "扫描目标并拿到 flag"',
  })
  const contMsg = contSent[0] && contSent[0].m
  const contText = contMsg && Array.isArray(contMsg.content) && contMsg.content[0] ? String(contMsg.content[0].text || '') : ''
  check('continue 成功: 只发 1 条 followup（不 steer、不 cancel、不 fork），回报 success',
    cont.kind === 'success' && contSent.length === 1 && contSent[0].via === 'followup' && contCanceled === 0,
    `kind=${cont.kind} sent=${JSON.stringify(contSent.map((s) => s.via))} cancel=${contCanceled}`)
  check('continue 文本: 续攻纪律（不重做/换策略/有界扫描/禁编造/propose_submit）+ 题目信息四项',
    /同一会话继续解题/.test(contText) && /不要从头重做/.test(contText) &&
      /换攻击面/.test(contText) && /保持有界/.test(contText) && /禁止编造/.test(contText) &&
      /propose_submit/.test(contText) &&
      /题面：扫描目标并拿到 flag/.test(contText) && /题型：web/.test(contText) &&
      /靶机地址（用户声明已授权的靶标）：http:\/\/10\.0\.0\.5:8080/.test(contText) &&
      /附件路径：C:\/tmp\/a\.zip/.test(contText),
    contText.slice(0, 300))
  check('continue 消息形态: id + producer-owned source.kind=ctfpilot + text block（同 solve-start 契约）',
    Boolean(contMsg) && typeof contMsg.id === 'string' && /^[0-9a-f-]{36}$/i.test(contMsg.id) &&
      contMsg.source && contMsg.source.kind === 'ctfpilot' && contMsg.source.plugin === undefined &&
      Array.isArray(contMsg.content) && contMsg.content[0] && contMsg.content[0].type === 'text',
    JSON.stringify(contMsg && { id: contMsg.id, source: contMsg.source }))
  const contBad = await byName['ctf-continue'].handler({ rawInput: '继续' })
  check('continue 无 agent: 显式 error 且回显可手动发送全文（不静默）',
    contBad.kind === 'error' && /继续解题未注入/.test(String(contBad.text || '')) &&
      /请手动发送/.test(String(contBad.text || '')) && /同一会话继续解题/.test(String(contBad.text || '')),
    String(contBad.text || '').slice(0, 200))

  // ---- 错误日志双通道（2026-11-19 用户令）----
  // 前面 A/B/C 场景的 error 已经 wrapper 落盘 dst/ctfpilot-errors.log；
  // 验证落盘内容 + /ctf-logs tail 读回 + clear 清空。
  const errLogFile = path.join(dst, 'ctfpilot-errors.log')
  const logRaw = existsSync(errLogFile) ? readFileSync(errLogFile, 'utf8') : ''
  check('错误落盘: registerCommand 包装层把 error 写入 ctfpilot-errors.log（via=error-result + rid）',
    logRaw.includes('[cmd:ctf-solve-start]') && logRaw.includes('via=error-result') && logRaw.includes('rid='),
    `exists=${existsSync(errLogFile)} len=${logRaw.length} head=${logRaw.slice(0, 160)}`)

  // /ctf-logs tail 读回刚才的落盘记录；clear 清空。（tail 按行取，条目含
  // stack 行，故取 60 行保证覆盖全部条目。）
  const logsTail = await byName['ctf-logs'].handler({ rawInput: 'tail 60' })
  check('ctf-logs: tail 读回错误日志（含命令名与 via 标记）',
    logsTail.kind === 'success' && /ctfpilot-errors\.log/.test(String(logsTail.text || '')) &&
      /cmd:ctf-solve-start/.test(String(logsTail.text || '')) && /via=error-result/.test(String(logsTail.text || '')),
    String(logsTail.text || '').slice(0, 200))
  const logsOps = await byName['ctf-logs'].handler({ rawInput: 'ops 5' })
  check('ctf-logs ops: 读回结构化 JSONL 事件尾部（含 ctfpilot-ops.log 路径）',
    logsOps.kind === 'success' && /ctfpilot-ops\.log/.test(String(logsOps.text || '')) &&
      /(event|尚无 ops 事件)/.test(String(logsOps.text || '')),
    String(logsOps.text || '').slice(0, 200))
  const logsBad = await byName['ctf-logs'].handler({ rawInput: 'bogus' })
  check('ctf-logs: 未知子动作 error 且给出用法',
    logsBad.kind === 'error' && /用法/.test(String(logsBad.text || '')),
    String(logsBad.text || '').slice(0, 160))
  const logsClear = await byName['ctf-logs'].handler({ rawInput: 'clear' })
  check('ctf-logs: clear 清空后 tail 显示「尚无错误记录」',
    logsClear.kind === 'success' && /清除/.test(String(logsClear.text || '')) &&
      (await byName['ctf-logs'].handler({ rawInput: 'tail' })).kind === 'success',
    `${String(logsClear.text || '').slice(0, 100)}`)

  // ---- ctf-dashboard：看板签名快照（2026-11-20 改造：数据源签名失效替代旧 15s TTL）----
  // 前置：record-win 一笔建库建表（隔离副本的 trajectories.sqlite 已被复制层删除），
  // 同时让 dashboard 有数据可比。断言序列：首查反映新记账 → 未动命中 →
  // touch mtime 触发重查 → 记账后即时反映 → 新签名下再命中。
  prog('step:dashboard-seq start')
  const rwDash1 = await byName['ctf-record-win'].handler({
    rawInput: '--name dash-cache-p1 --category misc --flag flag{dash_cache_p1}',
  })
  check('dashboard 前置: record-win 建库并记一笔', rwDash1.kind === 'success', `kind=${rwDash1.kind}`)
  const dashDb = path.join(dst, 'memory', 'trajectories.sqlite')
  const dashCall = async () => {
    const t0 = Date.now()
    const out = await byName['ctf-dashboard'].handler({})
    let blob = null
    try { blob = JSON.parse(out.text) } catch { /* error path */ }
    return { ms: Date.now() - t0, out, blob }
  }
  const dashA = await dashCall()
  check('dashboard: 隔离库首查成功且反映刚记的一笔',
    dashA.out.kind === 'success' && dashA.blob && dashA.blob.stats.total === 1,
    `kind=${dashA.out.kind} total=${dashA.blob && dashA.blob.stats && dashA.blob.stats.total}`)
  const dashB = await dashCall()
  check('dashboard: 账本未动时快照命中（不重跑 Python，ms 级）', dashB.ms < 100, `ms=${dashB.ms}`)
  const bump = new Date(Date.now() + 2000)
  utimesSync(dashDb, bump, bump)
  const dashC = await dashCall()
  check('dashboard: 账本 mtime 变化触发重查（真跑引擎，耗时回到秒级）',
    dashC.out.kind === 'success' && dashC.ms > dashB.ms * 10 && dashC.ms > 80,
    `ms=${dashC.ms} prevHit=${dashB.ms}`)
  const rwDash2 = await byName['ctf-record-win'].handler({
    rawInput: '--name dash-cache-p2 --category misc --flag flag{dash_cache_p2}',
  })
  const dashD = await dashCall()
  check('dashboard: 记账后立即读取反映新数据（主动作废 + 签名失效双通道）',
    dashD.out.kind === 'success' && dashD.blob && dashD.blob.stats.total === 2,
    `rw=${rwDash2.kind} kind=${dashD.out.kind} total=${dashD.blob && dashD.blob.stats && dashD.blob.stats.total}`)
  const dashE = await dashCall()
  check('dashboard: 新签名下再次快照命中', dashE.ms < 100, `ms=${dashE.ms}`)

  // ---- challenge-detail 签名缓存（2026-11-20 改造）：按题 key + 账本签名 ——
  // 隔离库首笔 id=1（rwDash1）；同题命中；record-win 写账本 → 签名变 → 所有题
  // 详情缓存整体失效（不逐题作废）。
  const detT0 = Date.now()
  const det1 = await byName['ctf-challenge-detail'].handler({ rawInput: '--id 1' })
  const detT1 = Date.now() - detT0
  check('detail 缓存: 同题首查真跑', det1.kind === 'success' && detT1 > 150, `ms=${detT1}`)
  const detT2 = Date.now()
  const det2 = await byName['ctf-challenge-detail'].handler({ rawInput: '--id 1' })
  const detT3 = Date.now() - detT2
  check('detail 缓存: 同题命中（毫秒级）', detT3 < 100, `ms=${detT3}`)
  const rwDash3 = await byName['ctf-record-win'].handler({
    rawInput: '--name dash-cache-p3 --category misc --flag flag{dash_cache_p3}',
  })
  const detT4 = Date.now()
  const det3 = await byName['ctf-challenge-detail'].handler({ rawInput: '--id 1' })
  const detT5 = Date.now() - detT4
  check('detail 缓存: 账本写入后整体失效（签名驱动，不逐题作废）',
    rwDash3.kind === 'success' && det3.kind === 'success' && detT5 > 100,
    `rw=${rwDash3.kind} ms=${detT5}`)
  const detT6 = Date.now()
  const det4 = await byName['ctf-challenge-detail'].handler({ rawInput: '--id 1' })
  const detT7 = Date.now() - detT6
  check('detail 缓存: 新签名下同题再次命中', detT7 < 100, `ms=${detT7}`)
  prog('phase:dashboard done')

  // ---- ctf-drill-start：批量重刷训练场（2026-11-25 用户令：重解→自验→记练→
  //      更优入库→末尾汇总的自动化闭环）。前置：dashboard 段已记 3 道已解题
  //      （dash-cache-p1/p2/p3，均 misc）；台账时间倒序 → p3 最新在前。
  check('drill-start: 命令已注册', Boolean(byName['ctf-drill-start']),
    'missing: ' + commands.map((c) => c.name).join(','))
  // 删降级：主 ctx 无 agentPresets → 预设切换失败 → error 终止、零注入。
  const drillSent0 = []
  const drillNoSvc = await byName['ctf-drill-start'].handler({
    agent: { followup: (m) => { drillSent0.push(m) }, steer: () => {} },
    rawInput: '',
  })
  check('drill-start 删降级: 无 agentPresets 服务 → error 终止、零注入',
    drillNoSvc.kind === 'error' && /已终止/.test(String(drillNoSvc.text || '')) &&
      /服务不可达/.test(String(drillNoSvc.text || '')) && drillSent0.length === 0,
    String(drillNoSvc.text || '').slice(0, 160))
  const drillNoAgent = await byName['ctf-drill-start'].handler({ rawInput: '' })
  check('drill-start: 无 agent 时 error + 手动文本（含引擎完整调用前缀与题清单）',
    drillNoAgent.kind === 'error' && /未能注入/.test(String(drillNoAgent.text || '')) &&
      /ctfpilot\.py/.test(String(drillNoAgent.text || '')) && /dash-cache-p/.test(String(drillNoAgent.text || '')),
    String(drillNoAgent.text || '').slice(0, 140))
  // 成功路径：带 agentPresets 服务的 ctx（solve-start 同款桩），真读引擎台账。
  const drillCmds = []
  await MOD.apply({
    tools: { register: () => {} },
    commands: { register: (c) => drillCmds.push(c) },
    agentPresets: {
      select: async (a, p) => p,
      list: async () => [{ id: 'standard' }, { id: 'ctf-quick' }, { id: 'ctf-deep' }],
      resolve: async (id) => ({ id }),
    },
  }, { python: PYTHON, engineDir: dst })
  const drillCmd = drillCmds.find((c) => c.name === 'ctf-drill-start')
  const drillSent = []
  const drillAgent = { followup: (m) => { drillSent.push(m) }, steer: (m) => { drillSent.push(m) } }
  const drillOut = await drillCmd.handler({ agent: drillAgent, rawInput: '' })
  check('drill-start 成功: 切快解预设 + followup 只收 1 条 + 回执带 3 题清单',
    drillOut.kind === 'success' && drillSent.length === 1 &&
      /已切到「快解」/.test(String(drillOut.text || '')) &&
      /dash-cache-p1/.test(String(drillOut.text || '')) && /dash-cache-p2/.test(String(drillOut.text || '')) &&
      /dash-cache-p3/.test(String(drillOut.text || '')),
    `kind=${drillOut.kind} sent=${drillSent.length} text=${String(drillOut.text || '').slice(0, 160)}`)
  const drillMsgText = String((drillSent[0] && drillSent[0].content[0] && drillSent[0].content[0].text) || '')
  check('drill-start 注入文本: 完整练兵协议（重解/自验/记练/入库/汇总/纪律）',
    /批量重刷训练/.test(drillMsgText) && /--mode auto/.test(drillMsgText) &&
      /arena record/.test(drillMsgText) && /--fast/.test(drillMsgText) && /--fail/.test(drillMsgText) &&
      /learn-writeup/.test(drillMsgText) && /rebuild-knowledge/.test(drillMsgText) &&
      /不要执行 record-win/.test(drillMsgText) && /401\/403/.test(drillMsgText) &&
      /禁止编造/.test(drillMsgText),
    drillMsgText.slice(0, 300))
  check('drill-start 注入文本: 历史 flag 供自验 + 引擎完整前缀（本机无 PATH 垫片）',
    drillMsgText.includes('flag{dash_cache_p1}') && drillMsgText.includes('flag{dash_cache_p3}') &&
      /& ".*" -X utf8 ".*ctfpilot\.py"/.test(drillMsgText),
    drillMsgText.slice(0, 200))
  drillSent.length = 0
  const drillLimit = await drillCmd.handler({ agent: drillAgent, rawInput: '--limit 1' })
  const limitText = String((drillSent[0] && drillSent[0].content[0] && drillSent[0].content[0].text) || '')
  check('drill-start --limit 1: 只取最新一道（台账倒序 → dash-cache-p3）',
    drillLimit.kind === 'success' && /dash-cache-p3/.test(limitText) &&
      !/dash-cache-p1/.test(limitText) && !/dash-cache-p2/.test(limitText),
    limitText.slice(0, 160))
  drillSent.length = 0
  const drillName = await drillCmd.handler({ agent: drillAgent, rawInput: '--name dash-cache-p1' })
  const nameText = String((drillSent[0] && drillSent[0].content[0] && drillSent[0].content[0].text) || '')
  check('drill-start --name: 精选指定题（只含 dash-cache-p1）',
    drillName.kind === 'success' && /dash-cache-p1/.test(nameText) &&
      !/dash-cache-p2/.test(nameText) && !/dash-cache-p3/.test(nameText),
    nameText.slice(0, 160))
  const drillBadCat = await drillCmd.handler({ agent: drillAgent, rawInput: '--category nonexistent-zz' })
  check('drill-start 筛空: 显式 error（不伪装 success）',
    drillBadCat.kind === 'error' && /没有可重刷的已解题/.test(String(drillBadCat.text || '')),
    String(drillBadCat.text || '').slice(0, 140))

  // ---- ctf-ensure-workspace：fs.mkdir 建目录（绕开 directoryPicker 的 browse capability）----
  // 2026-11-20 用户令：固定唯一 CTF-worker 工作区，不再按时间戳建新目录。
  const wantDir = path.join(dst, 'CTF-worker')
  const ensureOk = await byName['ctf-ensure-workspace'].handler({ rawInput: `--dir "${wantDir}"` })
  check('ensure-workspace: fs.mkdir 建 CTF-worker 目录并返回 {dir}',
    ensureOk.kind === 'success' && existsSync(wantDir) &&
      JSON.parse(String(ensureOk.text || '{}')).dir === wantDir,
    `kind=${ensureOk.kind} exists=${existsSync(wantDir)} text=${String(ensureOk.text || '').slice(0, 120)}`)
  const ensureIdem = await byName['ctf-ensure-workspace'].handler({ rawInput: `--dir "${wantDir}"` })
  check('ensure-workspace: 幂等（重复调用不报错）',
    ensureIdem.kind === 'success', String(ensureIdem.kind))
  const ensureNoArg = await byName['ctf-ensure-workspace'].handler({ rawInput: '' })
  check('ensure-workspace: 缺 --dir 显式 error',
    ensureNoArg.kind === 'error' && /--dir/.test(String(ensureNoArg.text || '')),
    String(ensureNoArg.text || '').slice(0, 120))
  const ensureRelative = await byName['ctf-ensure-workspace'].handler({ rawInput: '--dir relative/ctf-path' })
  check('ensure-workspace: 拒绝相对路径',
    ensureRelative.kind === 'error' && /相对路径/.test(String(ensureRelative.text || '')),
    String(ensureRelative.text || '').slice(0, 120))
  const ensureBadShape = await byName['ctf-ensure-workspace'].handler({ rawInput: `--dir "${path.join(dst, 'ctf-20261120-000000')}"` })
  check('ensure-workspace: 防呆——仅接受固定 CTF-worker 目录（旧 ctf-<stamp> 形态不再放行）',
    ensureBadShape.kind === 'error' && /形态不符/.test(String(ensureBadShape.text || '')),
    String(ensureBadShape.text || '').slice(0, 140))

  // ---- ctf-tools：agent 注入模式（2026-11-20 用户方案：面板点装 → 新对话 → agent 代跑包管理器）----
  // 覆盖：status 契约（通道头部 + 13 行）、参数防呆、mcp 指引、注入成功路径
  // （消息形态 + 请求文本闭环）、无 agent 显式 error、纯函数三通道命令构造、
  // pwsh 错误流 ANSI 色码剥离。所有 install 断言只验注入——绝不真实安装。
  const TI = MOD.TOOL_INTERNALS
  check('ctf-tools: 命令已注册', Boolean(byName['ctf-tools']),
    'missing: ' + commands.map(c => c.name).join(','))
  // ---- tools status TTL 缓存（2026-11-20 改造）：系统状态无文件签名可用 →
  //      30s TTL + --fresh 跳过；注入成功后作废（agent 复检必拿新状态）----
  // 交错修复：此前另有一次重复 status 调用先填了快照，导致本段「首查真跑」
  // 命中缓存毫秒返回（ms=1 假失败）——删重复调用，首查即全套件第一次 status，
  // 其结果同时供下方 13 行快检断言复用。
  const toolsCT0 = Date.now()
  const tsOut = await byName['ctf-tools'].handler({ rawInput: 'status' })
  const toolsCT1 = Date.now() - toolsCT0
  check('tools status 缓存: 首查真跑探针（引擎级耗时）',
    tsOut.kind === 'success' && toolsCT1 > 150, `ms=${toolsCT1}`)
  const toolsCT2 = Date.now()
  const tsCached = await byName['ctf-tools'].handler({ rawInput: 'status' })
  const toolsCT3 = Date.now() - toolsCT2
  check('tools status 缓存: TTL 内命中（毫秒级，不重跑探针）',
    tsCached.kind === 'success' && toolsCT3 < 100, `ms=${toolsCT3}`)
  const toolsCT4 = Date.now()
  const tsFresh = await byName['ctf-tools'].handler({ rawInput: 'status --fresh' })
  const toolsCT5 = Date.now() - toolsCT4
  check('tools status 缓存: --fresh 绕过 TTL 真跑探针',
    tsFresh.kind === 'success' && toolsCT5 > toolsCT3 * 3 && toolsCT5 > 150,
    `ms=${toolsCT5} prevHit=${toolsCT3}`)
  const tsText = String(tsOut.text || '')
  const tsJson = tsText.indexOf('[') >= 0 ? JSON.parse(tsText.slice(tsText.indexOf('['))) : []
  check('ctf-tools: status 返回安装通道头部 + 13 行快检',
    tsOut.kind === 'success' && /安装通道/.test(tsText) && tsJson.length === 13,
    `kind=${tsOut.kind} rows=${tsJson.length} text=${tsText.slice(0, 120)}`)
  const tvNoVerb = await byName['ctf-tools'].handler({ rawInput: '' })
  check('ctf-tools: 缺动词显式 error', tvNoVerb.kind === 'error' && /需要动词/.test(String(tvNoVerb.text || '')),
    String(tvNoVerb.text || '').slice(0, 120))
  const tvNoId = await byName['ctf-tools'].handler({ rawInput: 'install' })
  check('ctf-tools: 缺目标 id 显式 error', tvNoId.kind === 'error' && /需要目标 id/.test(String(tvNoId.text || '')),
    String(tvNoId.text || '').slice(0, 120))
  const tvUnknown = await byName['ctf-tools'].handler({ rawInput: 'install nope' })
  check('ctf-tools: 未知 id 显式 error', tvUnknown.kind === 'error' && /未知工具/.test(String(tvUnknown.text || '')),
    String(tvUnknown.text || '').slice(0, 120))
  const tvMcp = await byName['ctf-tools'].handler({ rawInput: 'install protocol-mcp' })
  check('ctf-tools: mcp 类给指引不动手', tvMcp.kind === 'success' && /MCP Manager/.test(String(tvMcp.text || '')),
    String(tvMcp.text || '').slice(0, 120))
  // 注入成功路径：fake agent 收集 followup 消息，验证形态与请求文本闭环。
  const toolSent = []
  const toolAgent = {
    followup: (m) => { toolSent.push({ via: 'followup', m }) },
    steer: (m) => { toolSent.push({ via: 'steer', m }) },
  }
  const tvInst = await byName['ctf-tools'].handler({ agent: toolAgent, rawInput: 'install nmap' })
  check('ctf-tools: install 走 agent 注入（followup 收到 1 条，回报 success）',
    tvInst.kind === 'success' && toolSent.length === 1 && toolSent[0].via === 'followup',
    `kind=${tvInst.kind} sent=${toolSent.length}`)
  const toolMsg = toolSent[0] && toolSent[0].m
  check('ctf-tools: 注入消息 format v4 形态（id + source.kind=ctfpilot + text block）',
    Boolean(toolMsg) && typeof toolMsg.id === 'string' && /^[0-9a-f-]{36}$/i.test(toolMsg.id) &&
      toolMsg.source && toolMsg.source.kind === 'ctfpilot' && toolMsg.source.plugin === undefined &&
      Array.isArray(toolMsg.content) && toolMsg.content[0] && toolMsg.content[0].type === 'text',
    JSON.stringify(toolMsg && { id: toolMsg.id, source: toolMsg.source }))
  const reqText = String((toolMsg && toolMsg.content[0] && toolMsg.content[0].text) || '')
  check('ctf-tools: 请求文本自包含闭环（工具名/通道实测/建议命令/验证/等效复检/止损）',
    /nmap/.test(reqText) && /本机通道实测/.test(reqText) && /建议执行/.test(reqText) &&
      /Insecure\.Nmap/.test(reqText) && /where\.exe nmap/.test(reqText) &&
      /常见安装位置/.test(reqText) && /等效复检/.test(reqText) && /止损纪律/.test(reqText) && /认证边界/.test(reqText),
    reqText.slice(0, 200))
  check('ctf-tools: 请求文本不再派发会话内不可达的 GUI 斜杠命令（session-8f0176a7 实锤回归）',
    !/先执行一次 \/ctf-tools status/.test(reqText) && !/结束前执行 \/ctf-tools status/.test(reqText) &&
      /GUI 斜杠命令，会话内无法代跑/.test(reqText),
    reqText.slice(0, 300))
  check('ctf-tools: 回执含「请求 agent」与注入内容（供核对）',
    /请求 agent 安装/.test(String(tvInst.text || '')) && /注入内容/.test(String(tvInst.text || '')) && /等效复检/.test(String(tvInst.text || '')),
    String(tvInst.text || '').slice(0, 160))
  const tvNoAgent = await byName['ctf-tools'].handler({ rawInput: 'install nmap' })
  check('ctf-tools: 无 agent 时 error + 手动可发文本（不降级不吞错）',
    tvNoAgent.kind === 'error' && /无活动会话 agent/.test(String(tvNoAgent.text || '')) && /nmap/.test(String(tvNoAgent.text || '')),
    String(tvNoAgent.text || '').slice(0, 140))
  // pm=none：注入文本带无包管理器指引（agent 会照指引装 choco 或手动获取）。
  process.env.CTFPILOT_PKG_MANAGER = 'none'
  TI.resetPkgManagerCache()
  toolSent.length = 0
  const tvNoPm = await byName['ctf-tools'].handler({ agent: toolAgent, rawInput: 'install nmap' })
  const noPmText = String((toolSent[0] && toolSent[0].m.content[0].text) || '')
  check('ctf-tools: 无包管理器时注入文本含 choco 安装指引（agent 代跑路线）',
    tvNoPm.kind === 'success' && toolSent.length === 1 && /未检出任何包管理器/.test(noPmText) && /Chocolatey/.test(noPmText),
    `kind=${tvNoPm.kind} text=${noPmText.slice(0, 140)}`)
  // pm=winget + 工具无 winget 包 → 注入文本带 choco 路线 + 手动获取双路线。
  process.env.CTFPILOT_PKG_MANAGER = 'winget'
  TI.resetPkgManagerCache()
  toolSent.length = 0
  const tvNoWg = await byName['ctf-tools'].handler({ agent: toolAgent, rawInput: 'install sqlmap' })
  const noWgText = String((toolSent[0] && toolSent[0].m.content[0].text) || '')
  check('ctf-tools: winget 无包工具注入文本含 choco 指引 + 手动获取',
    tvNoWg.kind === 'success' && toolSent.length === 1 && /sqlmap/.test(noWgText) && /Chocolatey/.test(noWgText) && /git clone/.test(noWgText),
    `kind=${tvNoWg.kind} text=${noWgText.slice(0, 140)}`)
  // 恢复现场：清 env + 缓存，让真实探测接管。
  delete process.env.CTFPILOT_PKG_MANAGER
  TI.resetPkgManagerCache()
  // 纯函数：三通道命令构造。
  const nmapTool = TI.TOOLS.find((t) => t.id === 'nmap')
  const volTool = TI.TOOLS.find((t) => t.id === 'volatility3')
  const raChoco = TI.resolveToolAction(nmapTool, 'install', 'choco')
  check('ctf-tools: choco 通道命令原样', raChoco.line === 'choco install nmap -y' && raChoco.via === 'choco',
    JSON.stringify(raChoco))
  const raWg = TI.resolveToolAction(nmapTool, 'install', 'winget')
  check('ctf-tools: winget 回退命令（id 精确 + 静默 + 双协议 + 禁交互）',
    raWg.via === 'winget' && /^winget install --id Insecure\.Nmap -e --silent --disable-interactivity --accept-package-agreements --accept-source-agreements$/.test(raWg.line),
    JSON.stringify(raWg))
  const raWgUp = TI.resolveToolAction(nmapTool, 'update', 'winget')
  check('ctf-tools: winget upgrade 带 --include-unknown', /^winget upgrade --id Insecure\.Nmap -e --silent --disable-interactivity --include-unknown/.test(raWgUp.line),
    JSON.stringify(raWgUp))
  const raWgRm = TI.resolveToolAction(nmapTool, 'uninstall', 'winget')
  check('ctf-tools: winget uninstall 不带 accept 参数（该动词没有）',
    /^winget uninstall --id Insecure\.Nmap -e --silent --disable-interactivity$/.test(raWgRm.line) && !/--accept/.test(raWgRm.line),
    JSON.stringify(raWgRm))
  const raPip = TI.resolveToolAction(volTool, 'install', 'none')
  check('ctf-tools: pip 工具与包管理器无关（python -m pip 形态）', raPip.line === 'python -m pip install volatility3' && raPip.via === 'pip',
    JSON.stringify(raPip))
  // ANSI 消毒：pwsh 错误流的红色码不许再以「[31;1m…」形态上屏。
  const dirty = 'choco : \u001b[31;1m无法将\u001b[0m "choco" 识别'
  check('ctf-tools: 错误文本 ANSI 色码剥离', TI.sanitizeText(dirty, 500) === 'choco : 无法将 "choco" 识别',
    JSON.stringify(TI.sanitizeText(dirty, 500)))

  // ---- ctf-tools ③：2026-11-25 五类空白补齐（tshark/scapy/dirsearch/exiftool/radare2）----
  // 通道全部经实测：winget search（WiresharkFoundation.Wireshark 4.6.10、
  // OliverBetz.ExifTool 13.64）、pip index（scapy 2.8.0 / dirsearch 0.5.0）、
  // choco 官网（radare2 6.2.6）。断言只验目录与命令构造，绝不真实安装。
  const tsTool = TI.TOOLS.find((t) => t.id === 'tshark')
  const exTool = TI.TOOLS.find((t) => t.id === 'exiftool')
  const r2Tool = TI.TOOLS.find((t) => t.id === 'radare2')
  const dsTool = TI.TOOLS.find((t) => t.id === 'dirsearch')
  const scTool = TI.TOOLS.find((t) => t.id === 'scapy')
  check('ctf-tools ③: 目录扩至 13 项（五个新 id 就位）',
    TI.TOOLS.length === 13 && [tsTool, exTool, r2Tool, dsTool, scTool].every(Boolean),
    `n=${TI.TOOLS.length} ids=${TI.TOOLS.map((t) => t.id).join(',')}`)
  const raTsWg = TI.resolveToolAction(tsTool, 'install', 'winget')
  check('ctf-tools ③: tshark 走 winget Wireshark 官方包，choco 形态是 wireshark 包名',
    raTsWg.via === 'winget' && /--id WiresharkFoundation\.Wireshark -e/.test(raTsWg.line) &&
      tsTool.install === 'choco install wireshark -y',
    JSON.stringify({ action: raTsWg, choco: tsTool.install }))
  const raExWg = TI.resolveToolAction(exTool, 'install', 'winget')
  check('ctf-tools ③: exiftool 走 winget OliverBetz.ExifTool',
    raExWg.via === 'winget' && /--id OliverBetz\.ExifTool -e/.test(raExWg.line),
    JSON.stringify(raExWg))
  const raR2 = TI.resolveToolAction(r2Tool, 'install', 'winget')
  check('ctf-tools ③: radare2 无 winget 包 → blocked 指引含 choco 路线与 GitHub 手动路线',
    raR2.blocked === true && /安装 Chocolatey/.test(String(raR2.guidance || '')) &&
      /github\.com\/radareorg\/radare2\/releases/.test(String(raR2.guidance || '')) &&
      !/仅 nmap 有/.test(String(raR2.guidance || '')),
    JSON.stringify(raR2).slice(0, 200))
  const raDsNone = TI.resolveToolAction(dsTool, 'install', 'none')
  const raScWg = TI.resolveToolAction(scTool, 'install', 'winget')
  check('ctf-tools ③: dirsearch/scapy 为 pip 类，命令与包管理器无关',
    raDsNone.line === 'python -m pip install dirsearch' && raDsNone.via === 'pip' &&
      raScWg.line === 'python -m pip install scapy' && raScWg.via === 'pip',
    JSON.stringify({ ds: raDsNone, sc: raScWg }))
  check('ctf-tools ③: 新工具多形态字段就位（tshark/exiftool/radare2 绝对路径回退）',
    (tsTool.paths || []).some((p) => /Wireshark\\tshark\.exe$/.test(p)) &&
      (exTool.paths || []).some((p) => /ExifTool\\exiftool\.exe$/.test(p)) &&
      (r2Tool.paths || []).some((p) => /chocolatey\\bin\\radare2\.exe$/.test(p)),
    JSON.stringify({ ts: tsTool.paths, ex: exTool.paths, r2: r2Tool.paths }))

  // ---- ctf-tools install-all：一键安装（2026-11-24 用户令：工具管理加「一键
  //      安装」——点一下，一个对话装完所有未装工具）----
  // 纯函数两枚 + 端到端注入。端到端的缺装集从真实 status --fresh 动态推导
  // （机器态无关，任何机器上断言都成立）；install 断言只验注入，绝不真实安装。
  const riaRows = [
    { id: 'nmap', name: 'nmap（端口扫描，Kali 式镜像）', kind: 'mirror', ok: true, detail: 'ok' },
    { id: 'sqlmap', name: 'sqlmap（SQL 注入）', kind: 'mirror', ok: false, detail: '(probe 无输出)' },
    { id: 'volatility3', name: 'volatility3（内存取证）', kind: 'mirror', ok: false, detail: '(probe 无输出)' },
    { id: 'protocol-mcp', name: 'ProtocolMCP（协议安全 MCP 服务）', kind: 'mcp', ok: false, detail: '未配置' },
  ]
  const ria = TI.resolveInstallAllTargets(riaRows)
  check('install-all 纯函数: rows 联表目录 → missing(2)/missingMcp(1)/okNames(1)，missing 含完整 install 定义',
    ria.missing.length === 2 && ria.missing.every((t) => t && typeof t.install === 'string') &&
      ria.missingMcp.length === 1 && ria.missingMcp[0].id === 'protocol-mcp' &&
      ria.okNames.length === 1 && ria.okNames[0].includes('nmap'),
    JSON.stringify({ m: ria.missing.map((t) => t.id), mc: ria.missingMcp.map((t) => t.id), ok: ria.okNames }))
  const iaText = TI.buildToolsInstallAllText(ria.missing, 'choco', { okNames: ria.okNames, missingMcp: ria.missingMcp })
  check('install-all 文本: 全部待装工具+各自命令+装完验证+已可用声明+MCP 指引',
    /一次性安装以下 2 个/.test(iaText) && /sqlmap（SQL 注入）/.test(iaText) && /choco install sqlmap -y/.test(iaText) &&
      /python -m pip install volatility3/.test(iaText) && /装完验证/.test(iaText) &&
      /已可用，无需处理/.test(iaText) && /MCP Manager/.test(iaText),
    iaText.slice(0, 300))
  check('install-all 文本: 批量纪律（串行、失败不阻塞、末尾一次统一等效复检）',
    /串行/.test(iaText) && /不要阻塞/.test(iaText) && /统一汇报/.test(iaText) && /等效复检/.test(iaText) && /止损纪律/.test(iaText),
    iaText.slice(0, 300))
  const sqlTool = TI.TOOLS.find((t) => t.id === 'sqlmap')
  const iaBlocked = TI.buildToolsInstallAllText([sqlTool], 'winget', { okNames: [], missingMcp: [] })
  check('install-all 文本: blocked 工具带指引 + choco 指引去重提示（只需装一次）',
    /自动通道不可用/.test(iaBlocked) && /Chocolatey/.test(iaBlocked) && /只需安装一次/.test(iaBlocked),
    iaBlocked.slice(0, 300))
  // 端到端：缺装集与注入文本一致性（followup 只收 1 条 = 一个对话装全部）。
  const iaSent = []
  const iaAgent = { followup: (m) => { iaSent.push(m) }, steer: (m) => { iaSent.push(m) } }
  const iaStatus = await byName['ctf-tools'].handler({ rawInput: 'status --fresh' })
  const iaStatusText = String(iaStatus.text || '')
  const iaStatusJson = iaStatusText.indexOf('[') >= 0 ? JSON.parse(iaStatusText.slice(iaStatusText.indexOf('['))) : []
  const iaExpect = iaStatusJson.filter((r) => !r.ok && r.kind !== 'mcp').map((r) => r.id)
  const iaOut = await byName['ctf-tools'].handler({ agent: iaAgent, rawInput: 'install-all' })
  if (iaExpect.length === 0) {
    check('install-all 端到端: 全部就绪时不注入、如实回执（本机工具全齐分支）',
      iaOut.kind === 'success' && /已就绪，无需一键安装/.test(String(iaOut.text || '')) && iaSent.length === 0,
      `kind=${iaOut.kind} sent=${iaSent.length} text=${String(iaOut.text || '').slice(0, 120)}`)
  } else {
    check('install-all 端到端: followup 只收 1 条（全部未装工具合并成一条注入）',
      iaOut.kind === 'success' && iaSent.length === 1,
      `kind=${iaOut.kind} sent=${iaSent.length} expect=[${iaExpect.join(',')}]`)
    const iaMsgText = String((iaSent[0] && iaSent[0].content[0] && iaSent[0].content[0].text) || '')
    check('install-all 端到端: 注入文本覆盖 status --fresh 实探的全部未装工具',
      iaExpect.every((id) => iaMsgText.includes(id)) && /一次性安装以下/.test(iaMsgText) && /等效复检/.test(iaMsgText) && /认证边界/.test(iaMsgText),
      `expect=[${iaExpect.join(',')}] ${iaMsgText.slice(0, 200)}`)
    check('install-all 端到端: 回执带数量与 id 清单（供核对）',
      new RegExp(`一键安装 ${iaExpect.length} 个工具`).test(String(iaOut.text || '')) &&
        iaExpect.every((id) => String(iaOut.text || '').includes(id)),
      String(iaOut.text || '').slice(0, 160))
  }
  const iaExtra = await byName['ctf-tools'].handler({ rawInput: 'install-all nmap' })
  check('install-all: 不接受工具 id（防呆 error 指引单工具用法）',
    iaExtra.kind === 'error' && /install <id>/.test(String(iaExtra.text || '')),
    String(iaExtra.text || '').slice(0, 120))
  const iaTwoWords = await byName['ctf-tools'].handler({ rawInput: 'install all' })
  check('install-all: 「install all」两词误用 → 未知 id 错误带一键安装指引',
    iaTwoWords.kind === 'error' && /install-all/.test(String(iaTwoWords.text || '')),
    String(iaTwoWords.text || '').slice(0, 120))

  // ---- ctf-tools ①②：「装了但漏检」根修复（2026-11-20）----
  // ① 注册表 PATH 合并：注入假 pull 断言合并语义（前置、去重、added 计数、
  //    失败不动 PATH）；② 多形态 probe 回退：合成工具断言回退链
  //    （where 失败 → 绝对路径命中 → alt 命中 → 全链未命中如实 false）。
  const pathBackup = String(process.env.PATH || '')
  const fakePull = () => ({ ok: true, machine: 'C:\\__fake_m__;C:\\Windows', user: 'C:\\__fake_u__', error: '' })
  const m1 = TI.mergeRegistryPath(fakePull)
  check('ctf-tools ①: 注册表 Machine+User 条目并入进程 PATH（added 计数正确）',
    m1.ok === true && m1.added === 2 &&
      /__fake_m__/.test(String(process.env.PATH)) && /__fake_u__/.test(String(process.env.PATH)),
    JSON.stringify(m1))
  const m2 = TI.mergeRegistryPath(fakePull)
  check('ctf-tools ①: 重复合并去重（added=0，条目不翻倍）',
    m2.ok === true && m2.added === 0, JSON.stringify(m2))
  const pathAfterM2 = String(process.env.PATH || '')
  const m3 = TI.mergeRegistryPath(() => ({ ok: false, machine: '', user: '', error: 'registry boom' }))
  check('ctf-tools ①: 拉取失败如实返回且 PATH 不被破坏',
    m3.ok === false && /registry boom/.test(String(m3.error)) && String(process.env.PATH || '') === pathAfterM2,
    JSON.stringify(m3))
  process.env.PATH = pathBackup // 还原现场，不影响后续断言
  // ② 回退链 A：where 失败 → 绝对路径存在性命中（引擎副本里必存在的真实文件）。
  const realFile = path.join(dst, 'ctfpilot.py')
  const rowA = await TI.toolsStatusRow({ id: 'fake-zz', name: '合成工具A', kind: 'mirror', probe: 'where.exe definitely-missing-cmd-zzz', paths: [realFile], alt: '' })
  check('ctf-tools ②: where 未命中后绝对路径回退命中（ok=true，detail 带路径与 PATH 提示）',
    rowA.ok === true && /已安装/.test(String(rowA.detail)) && /ctfpilot\.py/.test(String(rowA.detail)) && /PATH/.test(String(rowA.detail)),
    JSON.stringify(rowA))
  // ② 回退链 B：路径不存在 → alt 备用命令命中（python import 形态）。
  // 注意 pwsh 语法：命令名带引号必须用调用操作符 &（"python" -c 是 ParserError）。
  const rowB = await TI.toolsStatusRow({ id: 'fake-zz2', name: '合成工具B', kind: 'mirror', probe: 'where.exe definitely-missing-cmd-zzz', paths: ['C:\\__no_such_dir__\\x.exe'], alt: `& "${PYTHON}" -c "import sys"` })
  check('ctf-tools ②: 路径未命中后备用命令命中（python import 形态）',
    rowB.ok === true && /备用探测命中/.test(String(rowB.detail)), JSON.stringify(rowB))
  // ② 回退链 C：全链未命中 → 如实 ok=false（不谎报）。
  const rowC = await TI.toolsStatusRow({ id: 'fake-zz3', name: '合成工具C', kind: 'mirror', probe: 'where.exe definitely-missing-cmd-zzz', paths: ['C:\\__no_such_dir__\\y.exe'], alt: `& "${PYTHON}" -c "import no_such_module_zzz"` })
  check('ctf-tools ②: 全链未命中如实 ok=false（不谎报）',
    rowC.ok === false, JSON.stringify(rowC))
  // ② 目录契约：多形态字段就位（nmap/hashcat/john 带 paths，binwalk 带 alt）。
  const catNmap = TI.TOOLS.find((t) => t.id === 'nmap')
  const catHash = TI.TOOLS.find((t) => t.id === 'hashcat')
  const catJohn = TI.TOOLS.find((t) => t.id === 'john')
  const catBin = TI.TOOLS.find((t) => t.id === 'binwalk')
  check('ctf-tools ②: 目录多形态字段就位（nmap/hashcat/john 带 paths，binwalk 带 alt import）',
    (catNmap.paths || []).some((p) => /Nmap/i.test(p)) &&
      (catHash.paths || []).some((p) => /hashcat(64)?\.exe$/i.test(p)) &&
      (catJohn.paths || []).some((p) => /john\.exe$/i.test(p)) &&
      /import binwalk/.test(String(catBin.alt || '')),
    JSON.stringify({ nmap: catNmap.paths, binwalkAlt: catBin.alt }))
  // ① status 头部带 PATH 合并行（真实环境成功/失败都必有该行，且不破坏 13 行 JSON）。
  const ts2 = await byName['ctf-tools'].handler({ rawInput: 'status --fresh' })
  const ts2Text = String(ts2.text || '')
  const ts2Json = ts2Text.indexOf('[') >= 0 ? JSON.parse(ts2Text.slice(ts2Text.indexOf('['))) : []
  check('ctf-tools ①: status 头部带 PATH 合并行（成功或失败均如实）',
    ts2.kind === 'success' && /PATH 合并/.test(ts2Text) && ts2Json.length === 13,
    ts2Text.slice(0, 200))

  // ---- HTTP 数据网关：/ctfpilot/data/<cmd> 契约（2026-11-20 用户令：对话只允许解题上下文）----
  // 背景：dsh-commands.execute() 每执行一次必写 command/run + command/done 两个
  // log-only 事件（无静默开关）——面板拉数据也会永久落进会话日志并折成对话卡片。
  // 网关经官方 webServer 命名路由复用同一批命令 handler（commandHandlerRegistry），
  // 认证挂 connection.admit（与 /api 同款）。假 ctx 带 inject/webServer/connection/
  // agents 桩直接驱动路由 handler，契约级断言含真命令端到端（ensure-workspace 真建
  // 目录、solve-start 真注入、dashboard 真跑引擎）。
  const gwRoutes = []
  const gwAgents = new Map()
  let gwAdmit = () => ({ peer: { id: 'peer-owner' } }) // 默认放行：owner 浏览器
  const gwCmds = []
  const gwSent = []
  await MOD.apply({
    tools: { register: () => {} },
    commands: { register: (c) => gwCmds.push(c) },
    agentPresets: {
      select: async () => 'ctf-quick',
      list: async () => [{ id: 'ctf-quick' }, { id: 'ctf-deep' }],
      resolve: async (id) => ({ id }),
    },
    inject(_names, cb) {
      // 官方 ctx.inject([...services], fiber) 的测试近似：服务齐全立即激活。
      cb({
        webServer: { register: (route) => { gwRoutes.push(route); return () => {} } },
        connection: { admit: (req) => gwAdmit(req) },
        agents: { get: (sid) => gwAgents.get(String(sid)) },
        effect(fn) { fn(); return () => {} },
      })
    },
  }, { python: PYTHON, engineDir: dst })
  check('网关: 命令注册与网关同源（同一批 handler，网关实例注册全命令）',
    ['ctf-dashboard', 'ctf-solve-start', 'ctf-ensure-workspace', 'ctf-logs'].every((n) => gwCmds.some((c) => c.name === n)),
    `cmds=${gwCmds.map((c) => c.name).join(',')}`)

  function gwReq({ method = 'GET', url = '/ctfpilot/data/ctf-logs', body = '', headers = { host: '127.0.0.1:3080' } } = {}) {
    const chunks = body ? [Buffer.from(body, 'utf8')] : []
    return {
      method, url, headers,
      async *[Symbol.asyncIterator]() { for (const c of chunks) yield c },
    }
  }
  function gwRes() {
    const state = { status: 0, headers: null, body: '', writableEnded: false, destroyed: false }
    return {
      state,
      get writableEnded() { return state.writableEnded },
      get destroyed() { return state.destroyed },
      writeHead(status, headers) { state.status = status; state.headers = headers || null },
      end(text) { state.writableEnded = true; state.body = String(text == null ? '' : text) },
      on() {}, off() {},
    }
  }
  async function gwHit(opts) {
    const route = gwRoutes.find((r) => r.path === '/ctfpilot/data')
    if (!route) throw new Error('gateway route not registered')
    const req = gwReq(opts)
    const res = gwRes()
    await route.handler(req, res)
    let json = null
    try { json = JSON.parse(res.state.body) } catch { /* 非 JSON 体 */ }
    return { status: res.state.status, json, raw: res.state.body }
  }

  const route0 = gwRoutes[0]
  check('网关: 注册 prefix 路由 /ctfpilot/data（handler 就绪）',
    Boolean(route0) && route0.kind === 'prefix' && route0.path === '/ctfpilot/data' && typeof route0.handler === 'function',
    JSON.stringify(gwRoutes.map((r) => `${r.kind} ${r.path}`)))

  gwAdmit = () => ({ rejection: 401 })
  const gw401 = await gwHit({})
  check('网关: 未认证请求 401 拒绝（connection.admit → unauthorized）',
    gw401.status === 401 && gw401.json && gw401.json.text === 'unauthorized',
    `status=${gw401.status} body=${String(gw401.raw).slice(0, 120)}`)
  gwAdmit = () => ({ rejection: 403 })
  const gw403 = await gwHit({})
  check('网关: 不可信来源 403 拒绝（forbidden）',
    gw403.status === 403 && gw403.json && gw403.json.text === 'forbidden',
    `status=${gw403.status}`)
  gwAdmit = () => ({ peer: { id: 'peer-owner' } })

  const gwUnknown = await gwHit({ url: '/ctfpilot/data/ctf-nope' })
  check('网关: 未知命令 404（不伪装 success）',
    gwUnknown.status === 404 && /未注册的命令/.test(String((gwUnknown.json || {}).text || '')),
    `status=${gwUnknown.status}`)
  const gwPut = await gwHit({ method: 'PUT', url: '/ctfpilot/data/ctf-logs' })
  check('网关: 不支持的方法 405（仅 GET/POST）',
    gwPut.status === 405 && /不支持的方法/.test(String((gwPut.json || {}).text || '')),
    `status=${gwPut.status}`)
  const gwGetWrite = await gwHit({ url: '/ctfpilot/data/ctf-tools?args=status' })
  check('网关: 写/动作类命令 GET 拒绝 405（ctf-tools 须 POST）',
    gwGetWrite.status === 405 && /请用 POST/.test(String((gwGetWrite.json || {}).text || '')),
    `status=${gwGetWrite.status}`)

  const gwLogs = await gwHit({ url: '/ctfpilot/data/ctf-logs?args=tail%205' })
  check('网关: GET 白名单命令直通（ctf-logs → CommandResult JSON）',
    gwLogs.status === 200 && gwLogs.json && gwLogs.json.kind === 'success',
    `status=${gwLogs.status} body=${String(gwLogs.raw).slice(0, 120)}`)

  // —— ctf-doctor：交付运维体检（文本 + --json）——
  const doctorCmd = gwCmds.find((c) => c.name === 'ctf-doctor')
  check('doctor: 命令已注册', Boolean(doctorCmd))
  if (doctorCmd) {
    const docText = await doctorCmd.handler({ rawInput: '' })
    check('doctor 文本: 结论/引擎/Python/预设/网关五段可见',
      docText.kind === 'success' && /CTFPilot 体检/.test(String(docText.text || '')) &&
        /结论：(PASS|FAIL)/.test(String(docText.text || '')) &&
        /engine-dir/.test(String(docText.text || '')) &&
        /python/.test(String(docText.text || '')) &&
        /presets/.test(String(docText.text || '')) &&
        /gateway/.test(String(docText.text || '')),
      String(docText.text || '').slice(0, 260))
    const docJsonOut = await doctorCmd.handler({ rawInput: '--json' })
    let docJson = null
    try { docJson = JSON.parse(String(docJsonOut.text || '')) } catch { /* 下方断言显形 */ }
    check('doctor --json: 机器可读结构（checks / engine / python / presets / logs）',
      docJsonOut.kind === 'success' && docJson && Array.isArray(docJson.checks) &&
        docJson.engine && docJson.engine.dir === dst &&
        docJson.python && docJson.python.ok === true &&
        docJson.presets && docJson.presets.service === true &&
        docJson.logs && docJson.logs.ops,
      JSON.stringify(docJson && { ok: docJson.ok, checks: docJson.checks.length, py: docJson.python && docJson.python.ok }))
    check('doctor --json: 预设三项 resolve 全 ok（ctf-quick/deep/redrill）',
      docJson && Array.isArray(docJson.presets.results) &&
        ['ctf-quick', 'ctf-deep', 'ctf-redrill'].every((id) => docJson.presets.results.some((r) => r.id === id && r.ok)),
      JSON.stringify(docJson && docJson.presets.results))
    check('doctor --json: checks 含 engine/file/python/presets/gateway 五项核心',
      docJson && ['engine-dir', 'engine-file', 'python', 'presets', 'gateway'].every((id) => docJson.checks.some((c) => c.id === id)),
      JSON.stringify(docJson && docJson.checks.map((c) => c.id)))
  }

  const gwDoctor = await gwHit({ url: '/ctfpilot/data/ctf-doctor' })
  check('网关: GET ctf-doctor 只读白名单直通（运维体检无需 POST）',
    gwDoctor.status === 200 && gwDoctor.json && gwDoctor.json.kind === 'success' &&
      /CTFPilot 体检/.test(String((gwDoctor.json || {}).text || '')),
    `status=${gwDoctor.status} body=${String(gwDoctor.raw).slice(0, 120)}`)

  // —— ops 日志（AI 运维机读通道）：JSONL 一行一事件，含 rid/ms/name ——
  const opsFile = path.join(dst, 'ctfpilot-ops.log')
  const opsEvents = existsSync(opsFile)
    ? readFileSync(opsFile, 'utf8').split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean)
    : []
  check('ops 日志: 命令事件已落盘且为 JSONL（event/name/rid/ms）',
    existsSync(opsFile) && opsEvents.some((e) => e.event === 'command' && e.name && e.rid && typeof e.ms === 'number'),
    `exists=${existsSync(opsFile)} n=${opsEvents.length}`)
  check('ops 日志: 失败事件带 level=error 与 status/rid（可关联错误日志）',
    opsEvents.some((e) => e.level === 'error' && (e.event === 'command' || e.event === 'gateway-reject' || e.event === 'gateway-throw') && e.rid),
    JSON.stringify(opsEvents.slice(-3)))

  const gwEnsureDir = path.join(dst, 'CTF-worker')
  const gwEnsure = await gwHit({ method: 'POST', url: '/ctfpilot/data/ctf-ensure-workspace', body: `--dir "${gwEnsureDir}"` })
  check('网关: POST ensure-workspace 端到端真建目录（fs.mkdir 复用命令 handler）',
    gwEnsure.status === 200 && gwEnsure.json && gwEnsure.json.kind === 'success' &&
      JSON.parse(String((gwEnsure.json || {}).text || '{}')).dir === gwEnsureDir && existsSync(gwEnsureDir),
    `status=${gwEnsure.status} body=${String(gwEnsure.raw).slice(0, 140)}`)

  const gwOversize = await gwHit({ method: 'POST', url: '/ctfpilot/data/ctf-logs', body: 'x'.repeat(1024 * 1024 + 100) })
  check('网关: POST 请求体超 1MiB 上限 → 413（防滥用上限生效）',
    gwOversize.status === 413 && /字节上限/.test(String((gwOversize.json || {}).text || '')),
    `status=${gwOversize.status}`)

  gwAgents.set('sess-gw', {
    followup: (m) => { gwSent.push({ via: 'followup', m }) },
    steer: (m) => { gwSent.push({ via: 'steer', m }) },
  })
  const gwSolve = await gwHit({
    method: 'POST',
    url: '/ctfpilot/data/ctf-solve-start?sid=sess-gw',
    body: 'quick 网关题面：解出 flag{gw_e2e}',
  })
  const gwSolveText = String((gwSolve.json || {}).text || '')
  check('网关: POST solve-start 端到端（?sid= 经 agents.get 解析 agent → 切预设 + 注入题面）',
    gwSolve.status === 200 && gwSolve.json && gwSolve.json.kind === 'success' &&
      /已切到「快解」/.test(gwSolveText) &&
      gwSent.length === 1 && gwSent[0].via === 'followup' &&
      /flag\{gw_e2e\}/.test(String((gwSent[0].m.content || []).map((c) => (c && c.text) || '').join(''))),
    `status=${gwSolve.status} sent=${gwSent.length} body=${gwSolveText.slice(0, 160)}`)

  const gwSolveMiss = await gwHit({
    method: 'POST',
    url: '/ctfpilot/data/ctf-solve-start?sid=sess-missing',
    body: 'quick 题面',
  })
  check('网关: sid 无对应活动 agent → 显式 error（不静默）',
    gwSolveMiss.status === 200 && gwSolveMiss.json && gwSolveMiss.json.kind === 'error' &&
      /无活动会话 agent/.test(String((gwSolveMiss.json || {}).text || '')),
    `status=${gwSolveMiss.status} body=${String(gwSolveMiss.raw).slice(0, 160)}`)

  const gwLogRaw2 = existsSync(path.join(dst, 'ctfpilot-errors.log'))
    ? readFileSync(path.join(dst, 'ctfpilot-errors.log'), 'utf8') : ''
  check('网关: via=gateway 错误落盘 ctfpilot-errors.log（双通道第二路照旧）',
    gwLogRaw2.includes('via=gateway-error-result'),
    `len=${gwLogRaw2.length} tail=${gwLogRaw2.slice(-200)}`)

  const gwDash = await gwHit({ url: '/ctfpilot/data/ctf-dashboard' })
  let gwDashBlob = null
  try { gwDashBlob = JSON.parse(String((gwDash.json || {}).text || 'null')) } catch { /* error path */ }
  check('网关: GET dashboard 端到端真跑引擎（CommandResult.text = 看板 JSON，含已记台账）',
    gwDash.status === 200 && gwDash.json && gwDash.json.kind === 'success' &&
      Boolean(gwDashBlob && gwDashBlob.stats && gwDashBlob.stats.total >= 2),
    `status=${gwDash.status} kind=${gwDash.json && gwDash.json.kind} total=${gwDashBlob && gwDashBlob.stats && gwDashBlob.stats.total} body=${String(gwDash.raw).slice(0, 280)}`)
  prog('phase:gateway done')

  // ---- ctf-solving-state：按 sid 逐会话读权威运行态（2026-11-25 实锤修复）----
  // 旧实现以 event.sessionId 为门槛自折叠——而会话事件从不带 sessionId，
  // 折叠恒空 → /ctf-solving-state 永远空 → 看板卡片读不到「会话结束」。
  // 新实现：running = 官方 turnBoundary 投影（openTurnStartSeq 非 null =
  // 运行中）；mode/flags = 按会话的 ctfsolve 投影（正文路径 data.message.content）。
  const ssCmds = []
  let ssDef = null
  let ssChangedCb = null
  const ssSessions = new Map()
  const ssSessRun = { id: 'sess-run' }
  const ssSessDone = { id: 'sess-done' }
  ssSessions.set('sess-run', ssSessRun)
  ssSessions.set('sess-done', ssSessDone)
  const ssBoundary = {
    'sess-run': { openTurnStartSeq: 42, lastStepStartSeq: 50, lastStepBoundary: { kind: 'start', seq: 50 }, lastTurn: 1 },
    'sess-done': { openTurnStartSeq: null, lastStepStartSeq: 60, lastStepBoundary: { kind: 'end', seq: 61 }, lastTurn: 2 },
  }
  await MOD.apply({
    tools: { register: () => {} },
    commands: { register: (c) => ssCmds.push(c) },
    agentPresets: { select: async () => {}, list: async () => [], resolve: async (id) => ({ id }) },
    sessionProjections: {
      register: async (def) => {
        ssDef = def
        return {
          stateOf: (session) => (session.id === 'sess-done'
            ? { mode: 'quick', label: '快解', flags: ['flag{149}'], flagsCount: [1] }
            : { mode: 'quick', label: '快解', flags: [], flagsCount: [] }),
        }
      },
      onChanged: (cb) => { ssChangedCb = cb; return () => {} },
    },
    inject(_names, cb) {
      cb({
        webServer: { register: () => () => {} },
        connection: { admit: () => ({ peer: { id: 'peer-owner' } }) },
        agents: { get: () => undefined, turnBoundaryOf: (session) => ssBoundary[session.id] },
        sessions: { get: (sid) => ssSessions.get(String(sid)) },
        effect(fn) { fn(); return () => {} },
      })
    },
  }, { python: PYTHON, engineDir: dst })
  const ssHandler = ssCmds.find((c) => c.name === 'ctf-solving-state').handler
  const ssOut = await ssHandler({ rawInput: 'sess-run,sess-done,sess-missing' })
  const ssJson = JSON.parse(String(ssOut.text || '{}'))
  check('solving-state: 运行中会话 running=true（turnBoundary openTurnStartSeq 非 null）',
    ssJson.sessions['sess-run'] && ssJson.sessions['sess-run'].running === true,
    JSON.stringify(ssJson).slice(0, 240))
  check('solving-state: 已结束会话 running=false（turnBoundary 翻 null —— 卡片能读到会话结束）',
    ssJson.sessions['sess-done'] && ssJson.sessions['sess-done'].running === false,
    JSON.stringify(ssJson).slice(0, 240))
  check('solving-state: 已关闭/未知 sid 如实回 running=false（不留幽灵运行中）',
    Boolean(ssJson.sessions['sess-missing']) && ssJson.sessions['sess-missing'].running === false,
    JSON.stringify(ssJson.sessions['sess-missing']))
  check('solving-state: mode/flags 来自按会话 ctfsolve 投影',
    ssJson.sessions['sess-done'].mode === 'quick' &&
      Array.isArray(ssJson.sessions['sess-done'].flags) && ssJson.sessions['sess-done'].flags.includes('flag{149}'),
    JSON.stringify(ssJson.sessions['sess-done']))
  // 投影纯函数直断：init 从 header.agentPreset 恢复模式；flag 从
  // data.message.content 抽取（旧实现读 data.text/data.content 恒空）。
  const ssInit = ssDef && ssDef.init({ agentPreset: 'ctf-quick' })
  check('solving-state: 投影 init 从 header.agentPreset 恢复模式',
    Boolean(ssInit) && ssInit.mode === 'quick', JSON.stringify(ssInit))
  const ssApplied = ssDef.apply(ssInit, {
    type: 'assistant/message', seq: 1,
    data: { message: { content: [{ type: 'text', text: '答案是 flag{149}，请确认。' }] } },
  })
  check('solving-state: 投影从 data.message.content 抽 flag（旧路径恒空）',
    Boolean(ssApplied) && Array.isArray(ssApplied.flags) && ssApplied.flags.includes('flag{149}'),
    JSON.stringify(ssApplied))
  const ssPresetEv = ssDef.apply(ssApplied, { type: 'agent-preset/selected', seq: 2, data: { agentPreset: 'ctf-deep' } })
  check('solving-state: 投影从 agent-preset/selected 恢复模式（网关通道无 command/run）',
    Boolean(ssPresetEv) && ssPresetEv.mode === 'deep', JSON.stringify(ssPresetEv))
  // 旧检查点自愈：形状不符的历史状态（{sessions:{...}}）不得炸折叠。
  const ssHealed = ssDef.apply({ sessions: { legacy: true } }, {
    type: 'assistant/message', seq: 2,
    data: { message: { content: [{ type: 'text', text: 'flag{heal}' }] } },
  })
  check('solving-state: 旧检查点形状不炸折叠（默认值自愈）',
    Boolean(ssHealed) && Array.isArray(ssHealed.flags) && ssHealed.flags.includes('flag{heal}'),
    JSON.stringify(ssHealed))
  // onChanged 镜像：无参轮询（旧客户端）仍能拿到镜像。
  if (ssChangedCb) ssChangedCb(ssSessRun, 'ctfsolve', { mode: 'deep', label: '深度解析', flags: [], flagsCount: [] })
  const ssMirror = JSON.parse(String((await ssHandler({})).text || '{}'))
  check('solving-state: onChanged 镜像写入后无参轮询可见（旧客户端兼容）',
    ssMirror.sessions['sess-run'] && ssMirror.sessions['sess-run'].mode === 'deep',
    JSON.stringify(ssMirror).slice(0, 240))
  prog('phase:solving-state done')

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