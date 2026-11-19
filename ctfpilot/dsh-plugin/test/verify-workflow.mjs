/**
 * ctf-solve-pipeline.js（解耦版）的离线桩测试：
 * - 语法层面：把整份脚本包进 async IIFE 编译（new Function），语法错误会抛出；
 * - 控制流：注入桩 agent/log/phase，逐场景验证编排顺序、stdout 契约解析与返回结构；
 * - 解耦护栏：断言脚本不内嵌技能卡知识、不按目录名猜分类。
 * 运行：$DSH_NODE_EXECUTABLE verify-workflow.mjs
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SCRIPT_PATH = path.join(ROOT, 'workflows', 'ctf-solve-pipeline.js')
const SRC = readFileSync(SCRIPT_PATH, 'utf8')

let passed = 0
let failed = 0
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}`) }
  else { failed++; console.error(`  FAIL ${name}${detail ? ` :: ${String(detail).slice(0, 160)}` : ''}`) }
}

/* ---------- 模拟子代理回报 ---------- */

const SOLVED_FLAG = 'flag{p1p3_l1n3_s0lv3d}'

function solvedReply(flag = SOLVED_FLAG, triage = 'misc') {
  return [
    '[triage] category=' + triage + ' confidence=0.80',
    '[result] SOLVED in 1.8s',
    '[result] FLAG: ' + flag,
    '[meta] {"category":"misc","steps":4,"attempts":4}',
    '',
    '[pipeline-solve] {"solved":true,"flag":"' + flag + '","attempts":4,"steps":4}',
  ].join('\n')
}

function unsolvedReply(attempts = 14, steps = 7, triage = 'crypto') {
  return [
    '[triage] category=' + triage + ' confidence=0.65',
    '[result] NOT SOLVED in 12.4s (' + attempts + ' attempts)',
    '',
    '[pipeline-solve] {"solved":false,"flag":"","attempts":' + attempts + ',"steps":' + steps + '}',
  ].join('\n')
}

const CARD_BODY = [
  '# web 技能卡（引擎原文）',
  '## 决策顺序',
  '有源码先静态读。',
  '[剩余内容省略]',
  '',
  'LEARNED: 3 个',
].join('\n')

async function makeRunner(args) {
  const calls = []
  const logs = []
  const phases = []
  const agentImpl = async (prompt, opts) => {
    const label = String((opts && opts.label) || '')
    calls.push({ label, prompt })
    const reply = args.agentMap[label]
    if (!reply) throw new Error('unexpected agent call label=' + label)
    return typeof reply === 'function' ? reply(prompt) : reply
  }
  const wrapper = new Function(
    'args', 'log', 'phase', 'agent', 'pipeline', 'parallel',
    'return (async () => {\n' + SRC + '\n})()',
  )
  const p = wrapper(args, (m) => logs.push(String(m)), (t) => phases.push(String(t)), agentImpl, async () => {}, async () => [])
  return { result: await p, calls, logs, phases }
}

const BASE_ARGS = {
  challenge: 'C:\\dsh-plugins\\dsh-ctfpilot\\tests\\challs\\06_zip_nested',
  name: 'demo-b64',
}

/* ---------- 场景 1：缺 challenge 参数 → validate 失败 ---------- */

console.log('# 场景 1：必填参数校验')
{
  const r = await makeRunner({ agentMap: {} })
  check('缺 challenge 时返回 ok:false 且 error 指明缺少题目目录',
    r.result.ok === false && /challenge/.test(r.result.error || ''))
  check('未调用任何子代理（无网络）', r.calls.length === 0)
}

/* ---------- 场景 2：一发命中（triage 分类驱动，人审/复盘拿到分类） ---------- */

console.log('# 场景 2：一发命中（无 category 参数，triage=misc）')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply(SOLVED_FLAG, 'misc'),
    'ctf-propose': () => '已创建确认卡：CTFPilot 提案，人审确认卡已发出',
    'ctf-writeup': () => '写入复盘：C:\\WS\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested: skills/learned/card.json 已入库',
    'ctf-rebuild': () => 'cases=17 solved=9 rebuilt ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('一次 solve 即成功', r.result.ok === true && r.result.solved === true)
  check('Flag 原样带回（regex 提取优先于末行 JSON）',
    r.result.flag === SOLVED_FLAG)
  check('题名与目录带回', r.result.challenge === 'demo-b64' && r.result.dir === BASE_ARGS.challenge)
  check('分类来自引擎 triage 行（misc），无 args.category 也解析出分类',
    r.result.category === 'misc')
  check('命中路径不触发技能卡读取（快题零开销）', !r.result.stages.some((s) => s.startsWith('card:')))
  check('recall 未收到 --category（未传 args.category 时不猜）',
    !r.calls.find((c) => c.label === 'ctf-recall').prompt.includes('--category'))
  check('proposed=true 且 proposalNote 为人审回报', r.result.proposed === true && /确认卡/.test(r.result.proposalNote))
  check('artifacts 含 writeup 路径', Array.isArray(r.result.artifacts) && r.result.artifacts.length === 1 &&
    r.result.artifacts[0].endsWith('_ctfpilot_writeup.md'))
  check('notes 无失败项', !r.result.notes.some((n) => n.startsWith('入库失败') || n.startsWith('知识库重建异常')))
}

/* ---------- 场景 3：末行 JSON 损坏时的兜底解析 ---------- */

console.log('# 场景 3：末行 JSON 截断 → 走 [result] 行解析')
{
  const brokenTail = [
    '[triage] category=misc confidence=0.9',
    '[result] SOLVED in 2.0s',
    '[result] FLAG: flag{broken_tail_ok}',
    '[pipeline-solve] {"solved": true, "flag"',
  ].join('\n')
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => brokenTail,
    'ctf-propose': () => 'PROPOSAL: flag{broken_tail_ok}（无工具，回人工）',
    'ctf-writeup': () => 'FAILED: 不可写',
    'ctf-rebuild': () => 'cases=17 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('JSON 截断时仍识别 SOLVED 并提取 Flag', r.result.solved === true && r.result.flag === 'flag{broken_tail_ok}')
  check('writeup 失败时不进 artifacts，后续学习自动跳过',
    r.result.artifacts.length === 0 && !r.result.stages.includes('learn'))
  check('失败不产出伪造 artifacts', r.result.flag === 'flag{broken_tail_ok}' && !r.result.artifacts.includes('FAILED'))
}

/* ---------- 场景 4：失手 → 归因 → 读卡 → 重试命中 ---------- */

console.log('# 场景 4：首轮失手（triage=crypto）→ 读技能卡 → 深挖 → 二轮命中')
{
  let solveCount = 0
  const agentMap = {
    'ctf-recall': () => '- CASE xx [solved] 信号:rsa 模数过小',
    'ctf-solve#1': () => { solveCount++; return unsolvedReply(14, 7, 'crypto') },
    'ctf-card': () => CARD_BODY.replace('# web 技能卡（引擎原文）', '# crypto 技能卡（引擎原文）\n已知高位走 Coppersmith。'),
    'ctf-deep-dive': () => '根因：exec:nonzero-exit 空指针，猜密钥位置错。建议：跑 pow 脚本。',
    'ctf-manual': () => 'NO-MANUAL（离线沙箱无可执行路径，回退引擎重试）',
    'ctf-solve#2': () => { solveCount++; return solvedReply('flag{r3tr13_r3tr13}', 'crypto') },
    'ctf-propose': () => '已创建确认卡：CTFPilot 提案，人审确认卡已发出',
    'ctf-writeup': () => 'C:\\WS2\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=18 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('两轮求解后成功', r.result.solved === true && solveCount === 2)
  check('分类来自 triage=crypto（非目录名、非卡片名）', r.result.category === 'crypto')
  check('stages 含 card:crypto、deep-dive 与 manual:crypto，顺序为 召回→求解→读卡→深挖→手工补位→二轮',
    JSON.stringify(r.result.stages) ===
      JSON.stringify(['recall', 'solve#1', 'card:crypto', 'deep-dive', 'manual:crypto', 'solve#2', 'gate', 'writeup', 'learn', 'rebuild']))
  const cardCall = r.calls.find((c) => c.label === 'ctf-card')
  check('读卡子代理被指向运行时技能卡目录并按 crypto 取卡（含 misc 回落口径）',
    cardCall.prompt.includes('skills 子目录') && cardCall.prompt.includes('crypto.md') && cardCall.prompt.includes('misc.md'))
  const solve2 = r.calls.find((c) => c.label === 'ctf-solve#2')
  check('二轮求解提示词携带技能卡摘录 + 上次失败复盘',
    solve2.prompt.includes('技能卡摘录') && solve2.prompt.includes('Coppersmith') && solve2.prompt.includes('上次失败复盘'))
  check('本轮 Flag 取自第二轮', r.result.flag === 'flag{r3tr13_r3tr13}')
}

/* ---------- 场景 5：重试耗尽止步于求解，不产提案但保留复盘 ---------- */

console.log('# 场景 5：重试耗尽止步于求解，不生成人类提案')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => unsolvedReply(),
    'ctf-card': () => CARD_BODY,
    'ctf-deep-dive': () => '根因不明：semantic:premature-giveup。',
    'ctf-manual': () => 'NO-MANUAL（离线不可执行）',
    'ctf-solve#2': () => unsolvedReply(),
    'ctf-writeup': () => 'C:\\WS3\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=18 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('ok:false（重试上限内两轮均失手）', r.result.ok === false)
  check('重试耗尽后无 gate（不造案入账），以重建收尾',
    r.result.proposed === false && !r.result.stages.includes('gate') &&
    r.result.stages[r.result.stages.length - 1] === 'rebuild')
  check('失败回合仍留下复盘与入库（无知识流失）',
    r.result.stages.includes('writeup') && r.result.stages.includes('learn') && r.result.stages.includes('rebuild'))
}

/* ---------- 场景 6：preloadCard 快路径预载 ---------- */

console.log('# 场景 6：preloadCard=true 在首次求解前读卡')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-card': () => CARD_BODY,
    'ctf-solve#1': () => solvedReply('flag{pr3l04d}', 'forensics'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-writeup': () => 'C:\\W7\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=20 ok',
  }
  const callsOrder = []
  const origAgent = async (prompt, opts) => {
    callsOrder.push(String((opts && opts.label) || ''))
    const reply = agentMap[callsOrder[callsOrder.length - 1]]
    return typeof reply === 'function' ? reply(prompt) : reply
  }
  const wrapper = new Function(
    'args', 'log', 'phase', 'agent', 'pipeline', 'parallel',
    'return (async () => {\n' + SRC + '\n})()',
  )
  const r = await wrapper(
    { ...BASE_ARGS, category: 'forensics', preloadCard: true },
    () => {}, () => {}, origAgent, async () => {}, async () => [],
  )
  check('预载发生在首次求解之前（card:forensics → solve#1）',
    callsOrder.indexOf('ctf-card') >= 0 && callsOrder.indexOf('ctf-card') < callsOrder.indexOf('ctf-solve#1'))
  check('命中后分类仍为 triage（forensics）且成功', r.category === 'forensics' && r.solved === true)
  check('不再二次读卡', callsOrder.filter((l) => l === 'ctf-card').length === 1)
}

/* ---------- 场景 7：解耦护栏（防再耦合） ---------- */

console.log('# 场景 7：解耦护栏——脚本零知识嵌入、零目录名猜分类')
{
  const EMBEDDED = ['Coppersmith', 'pohlig-hellman', 'smart\'s attack', 'union select', 'information_schema',
    'ret2text', 'ZipCrypto', 'bkcrack', 'LSB', 'EXIF', 'volatility', 'maketrans']
  const hits = EMBEDDED.filter((kw) => SRC.toLowerCase().includes(kw.toLowerCase()))
  check('脚本不含任何技能卡打法关键词（未被蒸馏进脚本）', hits.length === 0, 'found: ' + hits.join(', '))
  check('脚本不含按目录名猜分类的旧逻辑（crypto|reverse… 启发式）',
    !/base\.indexOf\(k\)|目录名推断/.test(SRC))
  // 分类解析优先级：引擎 triage > args.category > misc
  const catAgentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply('flag{c4t_triage_w1ns}', 'osint'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-writeup': () => 'C:\\W8\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=20 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, category: 'misc', agentMap: catAgentMap })
  check('引擎 triage=osint 压过 args.category=misc', r.result.category === 'osint')
  // 未知 args.category 被忽略
  const catAgentMap2 = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply('flag{c4t_inval1d}', 'web'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-writeup': () => 'C:\\W9\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=20 ok',
  }
  const r2 = await makeRunner({ ...BASE_ARGS, category: 'CRYPTO!!', agentMap: catAgentMap2 })
  check('非法 args.category 被忽略（不猜、不注入）且 triage=web 生效',
    r2.result.category === 'web')
}

/* ---------- 场景 8：手工补位出 flag → 短路进人审，不走第二轮引擎求解 ---------- */

console.log('# 场景 8：手工按卡打出手工 flag，直接短路进入人审 gate')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => unsolvedReply(14, 7, 'crypto'),
    'ctf-card': () => CARD_BODY.replace('# web 技能卡（引擎原文）', '# crypto 技能卡（引擎原文）\n已知高位走 Coppersmith。'),
    'ctf-deep-dive': () => '根因：无 Coppersmith 检测器。建议：手推模多项式上 flint LLL。',
    'ctf-manual': () => '[pipeline-manual] {"flag":"flag{manu4l_m4ster}","method":"手推模多项式 + flint LLL 一发命中"}',
    'ctf-propose': () => '已创建确认卡',
    'ctf-writeup': () => 'C:\\WA\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=21 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('手工 flag 被采纳且解题成功', r.result.solved === true && r.result.flag === 'flag{manu4l_m4ster}')
  check('manual:true 且日志记录候选来源', r.result.manual === true &&
    r.result.manualLog.some((l) => l.includes('flag-candidate') && l.includes('flag{manu4l_m4ster}')))
  check('未执行第二轮引擎求解（无 solve#2），直接进人审',
    !r.result.stages.includes('solve#2') && r.result.stages.includes('gate'))
  const gateCall = r.calls.find((c) => c.label === 'ctf-propose')
  check('人审提示词带手工 flag 与解题标记（不绕过 gate）',
    gateCall.prompt.includes('flag{manu4l_m4ster}'))
}

/* ---------- 场景 9：可选轨道默认全关（不调用任何轨道子代理，无额外开销） ---------- */

console.log('# 场景 9：可选轨道默认全关（practice/researchTopic/rsiCycle 均不触发）')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply('flag{tr4ck5_d3f4ult}', 'misc'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-writeup': () => 'C:\\WB\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=22 ok',
  }
  const r = await makeRunner({ ...BASE_ARGS, agentMap })
  check('默认无任何轨道子代理调用', !r.calls.some((c) => c.label.startsWith('ctf-practice') ||
    c.label === 'ctf-research-add' || c.label === 'ctf-rsi-cycle'))
  check('默认返回 tracks 为空对象（未开启的轨道不显形）', r.result.tracks && Object.keys(r.result.tracks).length === 0)
  check('主链结构不受轨道影响（gate→writeup→learn→rebuild）',
    JSON.stringify(r.result.stages.slice(-4)) ===
      JSON.stringify(['gate', 'writeup', 'learn', 'rebuild']))
}

/* ---------- 场景 10：practice=true 练兵记账 —— 成功不带 --fail，未解出带 --fail ---------- */

console.log('# 场景 10：practice 练兵记账（含 --fail / --fast 语义与轨道失败隔离）')
{
  // —— 未解出 → record 自动带 --fail，轨道记 ok，主 ok 不受连坐 ——
  const unsolvedAgentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => unsolvedReply(14, 7, 'crypto'),
    'ctf-card': () => CARD_BODY,
    'ctf-deep-dive': () => '根因不明。',
    'ctf-manual': () => 'NO-MANUAL（离线不可执行）',
    'ctf-solve#2': () => unsolvedReply(20, 8, 'crypto'),
    'ctf-practice': () => "[arena] 'demo-b64': solves=0 fails=1 tier=normal",
    'ctf-writeup': () => 'C:\\WC\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=23 ok',
  }
  const rFail = await makeRunner({ ...BASE_ARGS, practice: true, agentMap: unsolvedAgentMap })
  const pracCall = rFail.calls.find((c) => c.label === 'ctf-practice')
  check('未解出时练兵命令自动带 --fail 且记账成功', Boolean(pracCall) &&
    pracCall.prompt.includes('--fail') && rFail.result.tracks.practice.ok === true)
  check('主 ok 为 false 时轨道仍独立完成（不连坐改写 solved）', rFail.result.ok === false && rFail.result.solved === false)

  // —— 轨道自身失败（FAILED）只进 notes，不改主 ok ——
  const brokenPractice = {
    ...unsolvedAgentMap,
    'ctf-practice': () => 'FAILED: arena record rejected',
  }
  const rBroken = await makeRunner({ ...BASE_ARGS, practice: true, agentMap: brokenPractice })
  check('练兵轨失败显式进 notes（可行动错误，不静默吞掉）',
    rBroken.result.notes.some((n) => n.startsWith('练兵记账异常')) && rBroken.result.tracks.practice.ok === false)
  check('练兵轨失败不改写主 ok/solved', rBroken.result.ok === false && rBroken.result.solved === false)

  // —— 解出 + trackFastPath → 命令带 --fast ——
  const fastAgentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply('flag{f4st_tr4ck}', 'misc'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-practice': () => "[arena] 'demo-b64': solves=1 fails=0 tier=easy",
    'ctf-writeup': () => 'C:\\WD\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=24 ok',
  }
  const rFast = await makeRunner({ ...BASE_ARGS, practice: true, trackFastPath: true, agentMap: fastAgentMap })
  const fastCall = rFast.calls.find((c) => c.label === 'ctf-practice')
  check('trackFastPath 解出时练兵命令带 --fast（不含 --fail）', Boolean(fastCall) &&
    fastCall.prompt.includes('--fast') && !fastCall.prompt.includes('--fail'))
}

/* ---------- 场景 11：researchTopic 与 rsiCycle 两条低频轨道 ---------- */

console.log('# 场景 11：researchTopic 入队 + rsiCycle 提案循环')
{
  const agentMap = {
    'ctf-recall': () => 'NO-CARDS',
    'ctf-solve#1': () => solvedReply('flag{rs1_cyc13}', 'forensics'),
    'ctf-propose': () => '已创建确认卡',
    'ctf-practice': () => "[arena] 'demo-b64': solves=1 fails=0 tier=easy",
    'ctf-research-add': () => '[research] queued \'SQL 注入专题\' (misc); draft file already written',
    'ctf-rsi-cycle': () => '[rsi] proposals generated: rsi-101, rsi-102 (2)',
    'ctf-writeup': () => 'C:\\WE\\_ctfpilot_writeup.md',
    'ctf-learn': () => 'ingested',
    'ctf-rebuild': () => 'cases=25 ok',
  }
  const r = await makeRunner({
    ...BASE_ARGS, researchTopic: 'SQL 注入专题', rsiCycle: true, agentMap,
  })
  check('研究队列入队：命令含 add 与专题名，且记入 tracks.research',
    r.calls.some((c) => c.label === 'ctf-research-add' && /add "SQL 注入专题"/.test(c.prompt)) &&
    r.result.tracks.research.ok === true)
  check('RSI 提案循环被触发且记入 tracks.rsi', r.calls.some((c) => c.label === 'ctf-rsi-cycle') &&
    r.result.tracks.rsi.ok === true)
  check('两条轨道 stage 上屏（research / rsi-cycle）',
    r.result.stages.includes('research') && r.result.stages.includes('rsi-cycle'))
}

/* ---------- 汇总 ---------- */

console.log(`\nctf-solve-pipeline 桩测试：${passed} 通过，${failed} 失败`)
if (failed > 0) {
  console.error('FAILED')
  process.exit(1)
}
console.log('ALL WORKFLOW CHECKS PASSED')