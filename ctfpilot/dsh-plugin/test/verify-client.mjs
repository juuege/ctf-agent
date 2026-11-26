#!/usr/bin/env node
/**
 * client.js 离线运行时验证 —— 不依赖浏览器/DOM。
 *
 * 用最小 React 桩（hooks 按组件函数分桶、跨渲染持久、序号一致）与
 * ModuleLoader 桩加载真实 client.js，驱动：
 *   1. 模块级 inject 列表与字典注册（zh/en 键集一致）；
 *   2. 三个席位注册（panellist / main / turnTail）及注入回调；
 *   3. 提交确认卡端到端：准入修复（无 admitted）行为、渲染确认按钮、
 *      点确认 → 远端命令格式 → 成功态；
 *   4. 他端已确认态（confirmedMatch）与跳过（dismissed）态；
 *   5. 看板面板归一化后渲染：功能标签四屏互斥（概览/题目台账/探测器/经验）、
 *      分类下拉与搜索过滤、点击题行军进入详情页（远程 /ctf-challenge-detail）。
 *
 * 运行：node test/verify-client.mjs（与宿主半边 smoke.mjs 同目录放置）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const CLIENT = path.resolve(here, '..', 'client.js')

let passed = 0
let failed = 0
const failures = []
function check(label, cond, extra) {
  if (cond) { passed++ } else { failed++; failures.push(label + (extra ? ` — ${extra}` : '')) }
}

/* ------------------------------------------------------------------ *
 * 最小 React 桩（hooks 按组件函数分桶，按调用序号跨渲染持久）：
 * 同一次 render 内 hooks 按序数消费 buckets[idx]（首次渲染创建）；
 * 不同组件函数各自独立桶；reset() 清空以隔离场景。
 * ------------------------------------------------------------------ */

function makeReact() {
  const store = new Map() // componentFn -> { states:[], refs:[] }
  let active = null // 当前正在执行的组件函数（hooks 按它分桶）
  let cursor = 0 // 本次调用内第几个 hook

  function ensure(fn) {
    if (!store.has(fn)) store.set(fn, { states: [], refs: [] })
    return store.get(fn)
  }

  return {
    createElement(type, props, ...children) {
      return { type, props: props || {}, children }
    },
    useState(init) {
      const s = ensure(active).states
      if (!s[cursor]) s[cursor] = { value: typeof init === 'function' ? init() : init }
      const box = s[cursor++]
      return [
        box.value,
        (v) => { box.value = typeof v === 'function' ? v(box.value) : v },
      ]
    },
    useRef(v) {
      const r = ensure(active).refs
      if (!r[cursor]) r[cursor] = { current: v }
      const ref = r[cursor++]
      return ref
    },
    useEffect(fn) {
      // 同步执行一次（单次渲染验证够用）；忽略依赖数组。
      try { fn() } catch (_e) { /* 副作用失败不影响同步断言 */ }
      return () => {}
    },
    useMemo(fn) { return fn() },
    useCallback(fn) { return fn },

    /** 以组件函数为键渲染（hooks 按调用序持久，跨 render 共享状态）。 */
    render(fn, props) {
      const prev = active
      active = fn
      cursor = 0
      try { return fn(props) } finally { active = prev }
    },

    /** 清空全部 hooks 状态桶：场景间状态必须互相隔离。 */
    reset() { store.clear(); cursor = 0 },
  }
}

/** 展开元素树中函数类型的组件元素（等同 React 调用子组件）。
 *  2026-11-20 修正：必须经 React.render 切换 active/cursor——此前直接裸调
 *  el.type(props)，组件的 hooks 全写进 active=null 的错桶，组件状态与
 *  effects 从未真正落到自身桶上（面板缓存改造的 inFlight 全局去重让错桶
 *  请求抢跑、真桶永远 loading，才暴露此缺陷）。 */
function expand(el) {
  if (!el || typeof el !== 'object') return el
  if (Array.isArray(el)) return el.map(expand)
  if (typeof el.type === 'function') return expand(React.render(el.type, el.props || {}))
  const out = { ...el, children: (el.children || []).map(expand) }
  return out
}

/** 遍历展开后的元素树，收集出现的 className（跳过 <style> 描述）。 */
function classesOf(el, acc = new Set()) {
  el = expand(el)
  if (!el || typeof el !== 'object') return acc
  if (Array.isArray(el)) { for (const c of el) classesOf(c, acc); return acc }
  if (el.type === 'style') return acc
  const cls = el.props && el.props.className
  if (typeof cls === 'string') for (const c of cls.split(/\s+/)) if (c) acc.add(c)
  for (const c of el.children || []) classesOf(c, acc)
  return acc
}

/** 深度查找第一个匹配 className 的后代元素。 */
function findByClass(root, want) {
  let hit = null
  function walk(el) {
    if (hit || !el || typeof el !== 'object' || el.type === 'style') return
    if (Array.isArray(el)) { for (const c of el) walk(c); return }
    const cls = el.props && el.props.className
    if (typeof cls === 'string' && cls.split(/\s+/).includes(want)) { hit = el; return }
    for (const c of el.children || []) walk(c)
  }
  walk(expand(root))
  return hit
}

/** 收集全部匹配 className 的后代元素（按文档序）。 */
function findAllByClass(root, want) {
  const out = []
  function walk(el) {
    if (!el || typeof el !== 'object' || el.type === 'style') return
    if (Array.isArray(el)) { for (const c of el) walk(c); return }
    const cls = el.props && el.props.className
    if (typeof cls === 'string' && cls.split(/\s+/).includes(want)) out.push(el)
    for (const c of el.children || []) walk(c)
  }
  walk(expand(root))
  return out
}

/** 转储元素树（调试用：跳过 <style> 内容，标注文本子节点）。 */
function dumpTree(el, depth) {
  if (!el || typeof el !== 'object') { console.log(' '.repeat(depth) + String(el)); return }
  if (Array.isArray(el)) { for (const c of el) dumpTree(c, depth); return }
  if (el.type === 'style') return
  const cls = el.props && el.props.className ? ` class="${el.props.className}"` : ''
  const oneTxt = el.children?.length === 1 && typeof el.children[0] === 'string'
    ? JSON.stringify(el.children[0]) : null
  console.log(' '.repeat(depth) + `<${String(el.type)}${cls}${oneTxt ? ` => ${oneTxt}` : ''}`)
  for (const c of el.children || []) dumpTree(c, depth + 2)
}

/* ------------------------------------------------------------------ *
 * 引擎看板 JSON（Host /ctf-dashboard 命令的真实输出形态）
 * ------------------------------------------------------------------ */

const engineJson = {
  meta: { generated: 'now' },
  stats: { total: 622, solved: 206, rate: 33.1, checkpoints: 12, lessons: 281, attempts: 131000, distinct: 60, challs: 200 },
  bycat: [{ cat: 'crypto', total: 80, solved: 40 }, { cat: 'web', total: 70, solved: 30 }],
  modes: [{ mode: 'auto', count: 118 }],
  challs: [
    { id: 822, name: 'b64chain', cat: 'crypto', mode: 'auto', flag: 'flag{f4st_p4th_b64_w1n}', solved: true, time: '2026-11-13 11:00' },
    { id: 823, name: 'unf', cat: 'misc', mode: 'fast', flag: '', solved: false, time: '' },
    { id: 824, name: 'learnonly-mock', cat: 'crypto', mode: 'learn', solved: false, flag: '', time: '', kind: 'learn' },
  ],
  lessons: [{ cat: 'crypto', tech: 'base64', src: 'juuege/ctf-agent', snippet: 'aGVsbG8=', time: '2026-11-13 11:00' }],
  detectors: [{ phase: 'fast', detector: 'base64-chain', attempts: 34110, hits: 10 }],
  errors: [
    { class: 'semantic:no-progress', count: 128, ok: 0 },
    { class: 'exec:nonzero-exit', count: 89, ok: 0 },
  ],
}

/** /ctf-arena stats 的引擎 stdout 形态（与 lib/index.js 透传一致）。 */
const arenaBoardText = [
  '== arena leaderboard (practice only; production untouched) ==',
  '  overall : 3/5 solved (60.0%)',
  '  crypto   1/2 solved (50.0%) tiers easy=0 normal=1 hard=0',
  '  web      2/3 solved (66.7%) tiers easy=1 normal=1 hard=0',
  '[arena] tiers recomputed: 0 changed; items=5',
  '',
].join('\n')

/** /ctf-challenge-detail 单题详情的引擎输出形态（与 lib/index.js → 引擎子命令一致）。 */
const detailJson = {
  id: 822,
  name: 'b64chain',
  category: 'crypto',
  mode: 'auto',
  solved: true,
  flag: 'flag{f4st_p4th_b64_w1n}',
  summary: 'base64 链解码题',
  created_at: '2026-11-13 11:00',
  attempts: [
    { phase: 'fast', detector: 'base64-chain', detail: '解码命中', success: true, flag: 'flag{f4st_p4th_b64_w1n}', error_class: '', time: '2026-11-13 11:01' },
    { phase: 'deep', detector: 'xor', detail: '爆破未中', success: false, flag: '', error_class: 'timeout', time: '2026-11-13 11:05' },
  ],
  lessons: [{ tech: 'base64', src: 'juuege/ctf-agent', snippet: 'aGVsbG8=', tags: 'encoding,decode', time: '2026-11-13 11:02' }],
  attempt_count: 2,
}

/* ------------------------------------------------------------------ *
 * 1. 加载真实 client.js（ModuleLoader 桩捕获 factory）
 * ------------------------------------------------------------------ */

const loaded = []
globalThis.window = {
  __ModuleLoader__: { load(spec) { loaded.push(spec); return undefined } },
}
// localStorage 桩（内存 Map）：驱动看板持久层三组断言——拉取写入、模块重载恢复、
// 损坏数据防御（真实浏览器里配额/隐私模式由 try/catch 降级为仅内存缓存）。
const lsStore = new Map()
globalThis.localStorage = {
  getItem: (k) => (lsStore.has(k) ? lsStore.get(k) : null),
  setItem: (k, v) => { lsStore.set(k, String(v)) },
  removeItem: (k) => { lsStore.delete(k) },
}
// eslint-disable-next-line no-new-func
new Function(fs.readFileSync(CLIENT, 'utf8'))()
check('module loader: client.js 被调用 load', loaded.length === 1, `loaded=${loaded.length}`)

if (loaded.length !== 1) {
  console.error(`FAILED early: factory not captured (${loaded.length})`)
  process.exit(1)
}
const factory = loaded[0].factory

/* ------------------------------------------------------------------ *
 * 2. 假 require（react 桩）+ apply 到假 ctx
 * ------------------------------------------------------------------ */

const React = makeReact()

let localeDict = {}
let slotRegistrations = []
let remoteCalls = [] // fetch 桩重构记录：{ sessionId, line, method, url }（line 与旧命令通道同形，既有断言零改动）

/** 网关响应桩开关与业务桩开关（2026-11-20：面板↔Host 全走 HTTP 数据网关
 *  fetch('/ctfpilot/data/<cmd>')，不再走命令通道——命令通道每次必写
 *  command/run+done 会污染会话日志。桩校验客户端用对 HTTP 动词与 sid 携带。 */
let arenaRecordBroken = false // /ctf-arena record 失败模拟开关（引擎退出码≠0 → kind:'error'）
let arenaStatsBroken = false // /ctf-arena stats 失败模拟开关（返回 kind:'error'，验证错误不拦死表单）
let researchBroken = false // /ctf-research list 失败模拟（单卡隔离验证）
let rsiListBroken = false // /ctf-rsi list 失败模拟（单卡隔离验证）
let rsiDecisionBroken = false // /ctf-rsi 决策动词失败模拟（可行动错误 + 重试路径）
let ensureWorkspaceBroken = false // /ctf-ensure-workspace 失败模拟（层1 → 层2 退退）
let ensureWorkspaceDirs = [] // 层1 发往 Host 的 --dir 路径回录
let toolsActionBroken = false // /ctf-tools install 注入失败模拟（错误 banner 不跳转）
let continueBroken = false // /ctf-continue 注入失败模拟（未解出续攻必须显式上屏）
let gateway404 = false // 网关路由未挂模拟（旧 Host + 新 client 版本偏斜 → HTTP 404）
let dashboardEmpty = false // 空账本模拟（新装/清库：看板全零，操作屏必须仍可达）
let redrillStatusBlob = null // /ctf-redrill-status 测试响应（null → {}；设对象 → 原样 JSON）


/** /ctf-research list 与 /ctf-rsi list 的引擎 stdout 形态（当前引擎 do_research/do_rsi 行格式）。 */
const researchListText = [
  '== research queue (1 topics) ==',
  '- [rsch-001] PNG 多帧隐写 (APNG) | forensics | evidence 0/2 | queued',
  '',
].join('\n')
const rsiListText = [
  '== rsi proposals (4) ==',
  '- [rsi-001] detector | misc/chain-decode -> proposed | fixation trigger reached (hits+successes=262): compile into a fast-path detector',
  '- [rsi-002] detector | crypto/RSA modulus/factoring analysis -> approved | fixation trigger reached (hits+successes=45): compile into a fast-path detector',
  '- [rsi-003] prompt | lessons channel wording -> rejected | wording caused two agent misfires',
  '- [rsi-004] detector | web/sqlmap chain -> adopted | wins=12 rows=40',
  '',
].join('\n')

function fakeRemoteExecute(sessionId, line) {
  if (String(line).startsWith('/ctf-continue')) {
    // Host 真实口径（2026-11-25 用户方案）：不切预设、不 fork，直接在原会话
    // followup 开新回合；成功回执带注入全文。
    if (continueBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '✗ 继续解题未注入：stub gateway unavailable' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: '✓ 已在同一会话开新回合继续解题（320 字符）。\n注入内容（供核对或手动重发）：\n上一轮未解出……' } } })
  }
  if (String(line).startsWith('/ctf-drill-start')) {
    // Host 真实口径：切快解预设后注入整条练兵协议（stub 回执带清单摘要）。
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: '✓ 会话预设已切到「快解」，批量重刷已注入：本轮 3 道已解题（台账共 49 道已解，筛选命中 3 道）。清单：chall-a、chall-b、chall-c。\n注入内容（供核对或手动重发）：\n请在本对话中执行「批量重刷训练」……' } } })
  }
  if (String(line).startsWith('/ctf-arena stats')) {
    if (arenaStatsBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] sqlite unavailable' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: arenaBoardText } } })
  }
  if (String(line).startsWith('/ctf-redrill-status')) {
    // 面板 30s 轮询同源：测试用可切换 blob（active / done / 空态）。
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(redrillStatusBlob || {}) } } })
  }
  if (String(line).startsWith('/ctf-arena record')) {
    if (arenaRecordBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] arena record needs --name and --category' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: "[arena] 'mockdrill': solves=1 fails=0 tier=normal" } } })
  }
  if (String(line).startsWith('/ctf-research list')) {
    if (researchBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] research list unavailable' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: researchListText } } })
  }
  if (String(line).startsWith('/ctf-rsi list')) {
    if (rsiListBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] rsi list unavailable' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: rsiListText } } })
  }
  if (/^\/ctf-rsi (approve|reject|adopt)\b/.test(String(line))) {
    if (rsiDecisionBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] rsi decision failed: transient' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: "[rsi] approved: rsi-001 (detector)" } } })
  }
  if (String(line).startsWith('/ctf-doctor')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: 'CTFPilot 体检 2026-11-26T00:00:00.000Z · 插件 0.4.0\n结论：PASS（8 通过 / 0 警告 / 0 失败）\n✓ engine-dir：C:/mock/engine\n✓ python：C:/mock/python.exe (Python 3.12.4)' } } })
  }
  if (String(line).startsWith('/ctf-logs')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: 'C:/mock/engine/ctfpilot-errors.log\n[error] mock-log-line' } } })
  }
  if (String(line).startsWith('/ctf-preset-audit')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: 'service: reachable\nresolve(ctf-quick): ok\nresolve(ctf-deep): ok\nresolve(ctf-redrill): ok' } } })
  }
  if (String(line).startsWith('/ctf-dashboard')) {
    if (dashboardEmpty) {
      const emptyJson = {
        stats: { total: 0, solved: 0, rate: 0, checkpoints: 0, lessons: 0, attempts: 0, distinct: 0, challs: 0 },
        bycat: [], modes: [], challs: [], lessons: [], detectors: [], errors: [],
      }
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(emptyJson) } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(engineJson) } } })
  }
  if (String(line).startsWith('/ctf-challenge-detail')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(detailJson) } } })
  }
  if (String(line).startsWith('/ctf-ensure-workspace')) {
    if (ensureWorkspaceBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] ensure-workspace boom' } } })
    }
    const m = /--dir\s+(?:"([^"]+)"|(\S+))/.exec(String(line))
    const dir = m ? (m[1] !== undefined ? m[1] : m[2]) : ''
    ensureWorkspaceDirs.push(dir)
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify({ dir }) } } })
  }
  if (String(line).startsWith('/ctf-tools status')) {
    // Host 真实口径：安装通道头部 + 8 行快检 JSON（client 从首个 '[' 起解析）。
    const rows = [
      { id: 'nmap', name: 'nmap（端口扫描，Kali 式镜像）', kind: 'mirror', ok: false, detail: '(probe 无输出)' },
      { id: 'sqlmap', name: 'sqlmap（SQL 注入）', kind: 'mirror', ok: false, detail: '(probe 无输出)' },
      { id: 'protocol-mcp', name: 'ProtocolMCP（协议安全 MCP 服务）', kind: 'mcp', ok: false, detail: '未配置（在 MCP Manager 中添加 protocol-mcp）' },
    ]
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: `安装通道：winget（模拟桩）\n工具快检（共 ${rows.length} 项）：\n` + JSON.stringify(rows, null, 2) } } })
  }
  if (String(line).startsWith('/ctf-tools install-all')) {
    if (toolsActionBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '✗ install-all 注入失败（followup 被拒）：stub boom' } } })
    }
    // Host 真实口径（agent 注入模式）：一键安装把全部未装工具合并成**一条**注入
    // **一个**对话（stub 快检行 nmap/sqlmap 未装 → 2 个工具）。
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: '✓ 已在当前对话请求 agent 一键安装 2 个工具（nmap、sqlmap，通道：winget）。请切到对话查看执行进展；agent 完成后会自动跑 /ctf-tools status 复检。\n注入内容（供核对或手动重发）：\n请在本机一次性安装以下 2 个 CTF 工具……' } } })
  }
  if (/^\/ctf-tools (install|update|uninstall) /.test(String(line))) {
    const m = /^\/ctf-tools (install|update|uninstall) (\S+)/.exec(String(line))
    const verb = m ? m[1] : ''
    const id = m ? m[2] : ''
    if (toolsActionBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: `✗ ${verb} 注入失败（followup 被拒）：stub boom` } } })
    }
    // Host 真实口径（agent 注入模式）：成功回执含「请求 agent」+ 注入内容。
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: `✓ 已在当前对话请求 agent 安装 ${id}（通道：winget）。请切到对话查看执行进展；agent 完成后会自动跑 /ctf-tools status 复检。\n注入内容（供核对或手动重发）：\n请在本机安装 CTF 工具：${id}。` } } })
  }
  return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: '已记入引擎账本' } } })
}

/** HTTP 数据网关桩（与 Host 侧 /ctfpilot/data 同形协议）：解析
 *  GET ?args= / POST body / ?sid=，重构旧 {sessionId, line, method}
 *  记录形状供既有断言复用；响应体 = CommandResult JSON。 */
async function fakeFetch(input, init) {
  const u = new URL(String(input), 'http://ctfpilot.local')
  const method = String((init && init.method) || 'GET')
  const body = init && typeof init.body === 'string' ? init.body : ''
  const m = /^\/ctfpilot\/data\/([A-Za-z0-9-]+)$/.exec(u.pathname)
  if (!m) {
    return { ok: false, status: 404, json: async () => ({ kind: 'error', text: `stub: 非网关 URL ${String(input)}` }) }
  }
  const cmd = m[1]
  const args = method === 'POST' ? body : (u.searchParams.get('args') || '')
  const sid = u.searchParams.get('sid') || ''
  const line = '/' + cmd + (args ? ' ' + args : '')
  remoteCalls.push({ sessionId: sid, line, method, url: String(input) })
  if (gateway404) {
    return { ok: false, status: 404, json: async () => ({ kind: 'error', text: 'gateway: 路由未挂载（模拟旧 Host）' }) }
  }
  const out = await fakeRemoteExecute(sid, line)
  const result = out && out.ok === true && out.value && out.value.result ? out.value.result
    : { kind: 'error', text: 'stub: malformed envelope' }
  return { ok: true, status: 200, json: async () => result }
}
globalThis.fetch = fakeFetch
// 全局禁真定时器（2026-11-26）：ArenaPanel 现带 redrill campaign 30s 轮询，
// 渲染期间任何窗口外的 mount 都会注册真 interval 把 node 进程吊死
// （桩环境无 unmount 语义）。离线测试全程零真定时器——原 9.5 段的
// 禁用/恢复窗口保留（重复禁用幂等，其"恢复"恢复的也是本桩）。
globalThis.setInterval = () => 0

const ctx = {
  locale: {
    bind(ns) {
      return (key, params) => {
        const pack = localeDict[ns] || {}
        // 真实运行时按当前语言选表；此处默认取 zh，缺则 en。
        const table = pack.zh || pack.en || {}
        let v = table[key] ?? pack[key] ?? key
        return params ? Object.entries(params).reduce((s, [k, val]) => s.replace(`{${k}}`, String(val)), v) : v
      }
    },
    register(ns, dict) { localeDict[ns] = dict },
  },
  effect(fn) { return fn() },
  reflect: { provide: () => () => {} },
  slots: {
    // 真实运行：inject(name, factory) 先挂席位，factory 内 register 才产出生效定义。
    inject(_name, cb) { const def = cb(); slotRegistrations.push(def); return () => {} },
    register(opts, comp) { return { ...opts, Component: comp } },
  },
}

const requireShim = (mod) => (mod === 'react' ? React : { throwErr: new Error(`unknown module ${mod}`) })

// 从 package.json 读取 dsh.client.inject 作为权威对照
const pkg = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '..', 'package.json'), 'utf8'))

const plugin = factory(requireShim)
check('factory: 返回 {inject, apply}', Boolean(plugin && Array.isArray(plugin.inject) && typeof plugin.apply === 'function'))
check('inject: 服务键列表含 sessions/workspaces/uiWorkspace（会话+工作区工厂）',
  plugin.inject.includes('workspaces') && plugin.inject.includes('uiWorkspace') && plugin.inject.includes('sessions'),
  JSON.stringify(plugin.inject))

// package.json dsh.client.inject 是「包依赖边」，必须全部真实存在且覆盖模块
// 注入的每个服务键的提供者包（曾把不存在的 ui-workspaces 写进清单，且缺
// api-session-controller / api-workspace-controller / ui-slots 三个提供者）。
const PLUGIN_CLIENT_INJECT = pkg.dsh?.client?.inject ?? []
const SERVICE_PROVIDERS = {
  'slots': ['@deepseek-ai/dsh-client-ui-slots'],
  'locale': ['@deepseek-ai/dsh-client-locale'],
  'uiWorkspace': ['@deepseek-ai/dsh-client-ui-workspace'],
  'sessions': ['@deepseek-ai/dsh-api-session-controller'],
  'workspaces': ['@deepseek-ai/dsh-api-workspace-controller'],
}
const missingProviders = []
for (const [service, providers] of Object.entries(SERVICE_PROVIDERS)) {
  if (!plugin.inject.includes(service)) continue
  for (const p of providers) if (!PLUGIN_CLIENT_INJECT.includes(p)) missingProviders.push(`${service} 需要 ${p}`)
}
check('inject: 模块服务键的提供者包全部在 package.json depend 边内（含 slots/sessions/workspaces 提供者）',
  missingProviders.length === 0, missingProviders.join('; '))
check('inject: package.json 不含幽灵包名（ui-workspaces 复数不存在于官方 client 包列表）',
  !PLUGIN_CLIENT_INJECT.includes('@deepseek-ai/dsh-client-ui-workspaces'),
  JSON.stringify(PLUGIN_CLIENT_INJECT.filter((n) => n.includes('workspace'))))

plugin.apply(ctx)
check('apply: 字典注册命名空间 ctfpilot', Object.keys(localeDict).includes('ctfpilot'))
check('apply: 三个席位注册（面板图标/主面板/回合尾部卡）', slotRegistrations.length === 3, `n=${slotRegistrations.length}`)
check('apply: UI-1 已并入主面板「开始解题」标签（无 composer.dock 席位）', !slotRegistrations.some(s => s.id === 'ctfpilot-solve-dock'))

/* ------------------------------------------------------------------ *
 * 3. 字典键集一致性（zh/en 完全一致）
 * ------------------------------------------------------------------ */

const dict = localeDict['ctfpilot']
const zhKeys = Object.keys(dict.zh).sort()
const enKeys = Object.keys(dict.en).sort()
check('字典: zh 键集非空', zhKeys.length >= 40, `n=${zhKeys.length}`)
check('字典: zh 与 en 键集完全一致', JSON.stringify(zhKeys) === JSON.stringify(enKeys))

/* ------------------------------------------------------------------ *
 * 3b. 详情页溢出修复的 CSS 静态断言（内联 <style> 文本）
 * ------------------------------------------------------------------ */

const cssText = fs.readFileSync(CLIENT, 'utf8')
check('CSS: flag 元素 display:inline-block（内联元素 ellipsis 无效，需块级化）', /display:inline-block/.test(cssText))
check('CSS: 长列表有界滚动 .ctfp-detail__list max-height:340px',
  cssText.includes('.ctfp-detail__list{') && cssText.includes('max-height:340px'))
/* 面板整体滚动修复（用户实锤「UI 不能往下滑动」）：根容器必须自成为有界滚动列 */
check('CSS: 面板根有界滚动 .ctfp-root height:100% + overflow-y:auto',
  /\.ctfp-root\{[^}]*height:100%/.test(cssText) && /\.ctfp-root\{[^}]*overflow-y:auto/.test(cssText))

/* ------------------------------------------------------------------ *
 * 4. 席位注册结构
 * ------------------------------------------------------------------ */

const byName = (name) => slotRegistrations.find((r) => r.name === name)
check('panellist: 注册名与 id', byName('sidebar.panellist')?.id === 'ctfpilot')
check('main: 注册名与 key', byName('main')?.key === 'ctfpilot')
check('turnTail: 注册名与 id', byName('conversation.chat.turnTail')?.id === 'dsh-ctfpilot')

const tailDef = byName('conversation.chat.turnTail')
const injectedFace = tailDef.inject('sess-42')
check('turnTail inject: 返回会话 id', injectedFace && injectedFace.sessionId === 'sess-42')
check('turnTail inject: locale 命名空间', tailDef.locale === 'ctfpilot')
check('turnTail: 组件可执行（非 null）', typeof tailDef.Component === 'function')

const t = ctx.locale.bind('ctfpilot')

const snapshot = {
  proposals: {
    'c1': { callId: 'c1', turn: 3, title: 'Crypto-Base64', brief: 'fast path', flag: 'flag{f4st_p4th_b64_w1n}', category: 'crypto', challenge: 'b64chain' },
    'c2': { callId: 'c2', turn: 7, title: 'Misc-QR', flag: 'flag{qr}', category: 'misc', challenge: 'qrpic' },
    'c3': { callId: 'c3', turn: 3, title: 'Web-JWT forge', flag: 'flag{jwt_3}', category: 'web', challenge: 'jwt' },
  },
  confirmed: [],
}

/** 运行时的 PropsRuntime 自动注入 t/sessionId/useProjection；卡片防御读取兼容。 */
function cardProps(turn, snap = snapshot, sessionId = 'sess-42') {
  return {
    turn: { turn },
    seq: 99,
    openFile: () => {},
    sessionId,
    t,
    useProjection: () => snap,
  }
}

const Card = tailDef.Component
const flushat = async () => { await new Promise((r) => setTimeout(r, 5)) }

/* ------------------------------------------------------------------ *
 * 5. 提交确认卡端到端
 * ------------------------------------------------------------------ */

// —— 场景 A：本回合匹配且无 admitted 字段也能准入；callId 最大者胜出（c3/jwt） ——
React.reset()
const elA = React.render(Card, cardProps(3))
check('卡片A: 无 proposal 匹配时返回 null（回合 999）', React.render(Card, cardProps(999)) === null)
check('卡片A: 渲染外壳与主按钮（准入修复后不丢卡）', classesOf(elA).has('ctfc-wrap') && classesOf(elA).has('ctfc-card'))

// —— 场景 B：点确认 → 远端命令格式（name/challenge 为 c3→jwt） ——
const btnB = findByClass(elA, 'ctfc-btn--primary')
if (!btnB) throw new Error('primary button not found in scenario A')
btnB.props.onClick()
await flushat()
const lastCall = remoteCalls[remoteCalls.length - 1]
// 新 shellArg 智能引号：无空白单 token 不加引号；含空白值整段双引号（下一条
    // 场景同样验证 Host 侧 optArg/flagArg 引号感知解析）。
const expectedLine = '/ctf-record-win --name jwt --category web --flag flag{jwt_3}'
check('确认: 远端命令为 /ctf-record-win 含转义后的 flag/name/category 参数（网关 POST + 会话 sid）',
  String(lastCall?.line) === expectedLine && lastCall?.method === 'POST' && String(lastCall?.sessionId) === 'sess-42',
  JSON.stringify(lastCall))

// —— 成功态：state 跨渲染持久后重渲染 ——
const elB2 = React.render(Card, cardProps(3))
check('成功态: 显示已记录状态行', classesOf(elB2).has('ctfc-status--ok'))

// —— 场景 C：他端已确认（confirmedMatch 直接显示完成行，无需点击） ——
React.reset()
const snapC = JSON.parse(JSON.stringify(snapshot))
snapC.confirmed = [{ flag: 'flag{jwt_3}', category: 'web', name: 'jwt', at: 55 }]
const elC = React.render(Card, cardProps(3, snapC))
check('他端确认态: 未点击即显示完成行', classesOf(elC).has('ctfc-status--ok'))

// —— 场景 D：跳过（dismissed）跨渲染生效 ——
React.reset()
const elD = React.render(Card, cardProps(3))
const ghostD = findByClass(elD, 'ctfc-btn--ghost')
check('跳过态: 幽灵按钮存在', Boolean(ghostD))
if (ghostD) ghostD.props.onClick()
const elD2 = React.render(Card, cardProps(3))
check('跳过态: dismissed 后组件返回 null', elD2 === null)

/* ------------------------------------------------------------------ *
 * 6. 看板面板：引擎 JSON 归一化 → 渲染
 * ------------------------------------------------------------------ */

React.reset()
remoteCalls.length = 0
tailDef.inject('sess-panel') // 模拟会话尾部注入：设模块级 currentSessionId（解题表单/确认卡定位目标会话用；数据读取已走网关与会话解耦）
const Panel = byName('main').Component
React.render(Panel, { t })
check('面板: 挂载触发网关拉取 /ctf-dashboard（GET，非命令通道）',
  remoteCalls.some((c) => c.line === '/ctf-dashboard' && c.method === 'GET'),
  JSON.stringify(remoteCalls.slice(-2)))
await flushat()

// —— 持久化与节流（2026-11-20 看板缓存改造门控：stale-while-revalidate）——
const storedDash = lsStore.get('ctfpilot.dashboard.v1')
check('看板: 拉取成功后持久化到 localStorage（UI 重启可恢复）',
  Boolean(storedDash) && String(storedDash).includes('"fingerprint"'),
  `len=${storedDash ? String(storedDash).length : 0}`)
remoteCalls.length = 0
React.render(Panel, { t }) // 缓存命中 + 30s 节流窗口内：不发请求
await flushat()
React.render(Panel, { t })
check('看板: 节流窗口内重挂零请求（缓存命中+节流，防 sessions.list 订阅风暴）',
  !remoteCalls.some((c) => c.line === '/ctf-dashboard'),
  JSON.stringify(remoteCalls))

/** 按标签文字点击切换（外层 ctfp-tab 与子屏 ctfp-seg-btn 同号匹配）。
 *  labels 支持数组：2026-11-26 另一会话重构了标签体系（题目台账→知识库、
 *  训练场→题库精炼、自进化→系统演进、四子屏合并为 题目/经验卡/引擎观察），
 *  按候选顺序兜底，两线并发时测试不互相踩。 */
function clickTab(el, labels) {
  const cands = Array.isArray(labels) ? labels : [labels]
  for (const label of cands) {
    const btn = [...findAllByClass(el, 'ctfp-tab'), ...findAllByClass(el, 'ctfp-seg-btn')]
      .find((b) => String(Array.isArray(b.children) ? b.children[0] : '') === label)
    if (btn) { btn.props.onClick(); return }
  }
  throw new Error(`tab "${cands.join('|')}" not found`)
}

/** 在原始元素树中按组件名抓取组件元素（不执行其渲染体）。 */
function grabComponent(root, nameMatch) {
  let hit = null
  ;(function walk(el) {
    if (hit || !el || typeof el !== 'object') return
    if (Array.isArray(el)) { for (const c of el) walk(c); return }
    if (typeof el.type === 'function' && nameMatch(el.type.name)) { hit = el; return }
    for (const c of el.children || []) walk(c)
  })(root)
  return hit
}

const htmlOf = (el) => JSON.stringify(expand(el), (k, v) => (typeof v === 'function' ? `[fn]` : v))

const panelEl = React.render(Panel, { t })
const clsP = classesOf(panelEl)
const htmlLike = htmlOf(panelEl)
check('面板: 功能标签栏渲染（6 主标签）',
  clsP.has('ctfp-tabs') && ['开始解题', '正在解题', '工具管理', '知识库', '题库精炼', '系统演进'].every((l) => htmlLike.includes(l)))
const mainTabLabels = findAllByClass(panelEl, 'ctfp-tab').map((b) => String(b.children[0]))
check('面板: 主标签顺序固定为 开始解题 → 正在解题 → 工具管理 → 知识库 → 题库精炼 → 系统演进',
  JSON.stringify(mainTabLabels) === JSON.stringify(['开始解题', '正在解题', '工具管理', '知识库', '题库精炼', '系统演进']),
  JSON.stringify(mainTabLabels))
check('面板: 首访默认落在「开始解题」（操作动线最短）',
  Boolean(grabComponent(panelEl, (n) => /SolveForm/.test(n))) && htmlLike.includes('CTFPilot 解题工作台'),
  htmlLike.slice(0, 160))
// 2026-11-26 另一会话重构：首访默认「开始解题」；知识库合并原四屏为「题目/经验卡/引擎观察」。
clickTab(panelEl, '知识库')
const kb1 = React.render(Panel, { t })
const clsKb = classesOf(kb1)
const kbHtml = htmlOf(kb1)
check('面板: 知识库屏渲染 KPI（总数 622）与分类进度（crypto 40/80）',
  clsKb.has('ctfp-kpis') && kbHtml.includes('622') && kbHtml.includes('40/80'))
check('面板: KPI 新增「累计尝试」（131000 次）', kbHtml.includes('131000'))
check('面板: 知识库三子屏（题目/经验卡/引擎观察）就位',
  ['题目', '经验卡', '引擎观察'].every((l) => kbHtml.includes(l)))

// —— 切到「题目」子屏：分类下拉预选 + 计数 + 搜索 + 行 ——
clickTab(React.render(Panel, { t }), ['题目台账', '知识库'])
clickTab(React.render(Panel, { t }), ['题目'])
const panelLedger = React.render(Panel, { t })
const ledgerHtml = htmlOf(panelLedger)
check('面板: 台账屏分类下拉与结果计数（真实解题 2 条，学习导入默认隐藏）',
  classesOf(panelLedger).has('ctfp-filter-sel') && ledgerHtml.includes('共 2 条') &&
  !ledgerHtml.includes('learnonly-mock'))
check('面板: 存在学习导入时有「含学习导入」开关', Boolean(findAllByClass(panelLedger, 'ctfp-learn-toggle').length))
// 打开开关：学习导入条目上屏并带「学习导入」徽标
const toggle = findAllByClass(panelLedger, 'ctfp-learn-toggle')[0]
if (toggle) {
  const input = Array.isArray(toggle.children) ? toggle.children.find((c) => c.type === 'input') : null
  if (!input || typeof input.props.onChange !== 'function') throw new Error('toggle input not found')
  input.props.onChange({ target: { checked: true } })
  const panelWithLearned = React.render(Panel, { t })
  const learnedHtml = htmlOf(panelWithLearned)
  check('面板: 开启后学习导入条目上屏（learnonly-mock / 学习导入徽标）',
    learnedHtml.includes('learnonly-mock') && learnedHtml.includes('学习导入'))
}
check('面板: 台账屏渲染题目行（b64chain 与 flag）',
  ledgerHtml.includes('b64chain') && ledgerHtml.includes('flag{f4st_p4th_b64_w1n}'))

// —— 切到「引擎观察」子屏：探测器命中 + 失败画像 + 解题模式 ——
clickTab(panelLedger, ['探测器', '引擎观察'])
const panelDets = React.render(Panel, { t })
const detHtml = htmlOf(panelDets)
check('面板: 引擎观察屏渲染探测器命中统计（34110 次尝试 / base64-chain）',
  detHtml.includes('34110') && detHtml.includes('base64-chain'))
check('面板: 引擎观察屏渲染解题模式（auto 118）与失败画像（semantic:no-progress 128）',
  detHtml.includes('118') && detHtml.includes('semantic:no-progress') && detHtml.includes('128'))
check('面板: 失败画像标题含类别计数（共 2 类）', detHtml.includes('共 2 类'))

// —— 切到「经验卡」子屏 ——
clickTab(panelDets, '经验卡')
const panelLessons = React.render(Panel, { t })
const lesHtml = htmlOf(panelLessons)
check('面板: 经验屏渲染经验卡（技术 / 来源）',
  lesHtml.includes('base64') && lesHtml.includes('juuege/ctf-agent'))

// —— 点击题目行 → 详情页链路：客户端 → /ctf-challenge-detail --id 822 → 渲染 ——
clickTab(React.render(Panel, { t }), ['题目台账', '知识库'])
clickTab(React.render(Panel, { t }), ['题目'])
const panelForClick = React.render(Panel, { t })
const rowEls = findAllByClass(panelForClick, 'ctfp-row--click')
check('面板: 台账行可点击进入详情', rowEls.length >= 2, `rows=${rowEls.length}`)
if (rowEls.length > 0) rowEls[0].props.onClick()
const panelDetail = React.render(Panel, { t })
const detailEl = grabComponent(panelDetail, (n) => /ChallengeDetail/.test(n))
check('面板: 选中题后整屏切换到 ChallengeDetail（看板内容不叠显）', Boolean(detailEl))

if (detailEl) {
  const DetailFn = detailEl.type
  React.render(DetailFn, detailEl.props) // loading 首帧，effect 发起详情拉取
  await flushat()
  const dHtml = htmlOf(React.render(DetailFn, detailEl.props))
  check('详情页: 引擎单题记录落地（b64chain / flag / 创建时间）',
    dHtml.includes('b64chain') && dHtml.includes('flag{f4st_p4th_b64_w1n}') && dHtml.includes('2026-11-13 11:00'))
  check('详情页: 解题时间线含两次尝试（命中与未命中时刻）',
    dHtml.includes('2026-11-13 11:01') && dHtml.includes('2026-11-13 11:05'))
  check('详情页: 本题经验标签 chips', dHtml.includes('encoding') && dHtml.includes('decode'))
  check('详情页: 触发网关 GET /ctf-challenge-detail --id 822',
    remoteCalls.some((c) => c.line === '/ctf-challenge-detail --id 822' && c.method === 'GET'))
}

/* ------------------------------------------------------------------ *
 * 7. 训练场（第五标签）：竞技榜自加载组件 + 一键记练
 * ------------------------------------------------------------------ */

// —— 先从详情页返回看板（整屏详情会遮住标签栏与训练场组件）——
const afterDetail = React.render(Panel, { t })
const detailEl2 = grabComponent(afterDetail, (n) => /ChallengeDetail/.test(n))
if (detailEl2 && typeof detailEl2.props.onBack === 'function') detailEl2.props.onBack()

// —— 切到「训练场」标签：ArenaPanel 自加载 /ctf-arena stats，排行榜归一化上屏 ——
// （ArenaPanel 现带 redrill campaign 30s 轮询：桩环境不会 unmount，真
//  setInterval 句柄会把 node 进程吊死 + 30s 齐射——本段与 9.5 段同款，
//  训练场整段禁用，一次性 mount 拉取仍会照常发出。）
const _realSetIntervalArena = globalThis.setInterval
globalThis.setInterval = () => 0
clickTab(panelLessons, ['训练场', '题库精炼'])
const panelArena = React.render(Panel, { t })
check('训练场: 功能标签栏含「训练场/题库精炼」（第 5 个）',
  findAllByClass(panelArena, 'ctfp-tab').some((b) => ['训练场', '题库精炼'].includes(String(b.children[0]))))
check('训练场: 自加载触发 /ctf-arena stats', remoteCalls.some((c) => c.line === '/ctf-arena stats'))

/** 挂载 ArenaPanel 并返回其展开后的元素树（stub effect 同步发起一次 stats 拉取）。 */
function arenaMount() {
  const raw = React.render(Panel, { t })
  const rawComp = grabComponent(raw, (n) => /Arena/.test(n))
  if (!rawComp) throw new Error('ArenaPanel missing')
  return React.render(rawComp.type, rawComp.props)
}

const arenaEl0 = arenaMount()
await flushat() // 让 stats Promise 落地并写入 board 状态
const arenaHtml = htmlOf(arenaMount())

check('训练场: 排行榜总览与档位 KPI（3/5、60%、easy 1/normal 2/hard 0）',
  arenaHtml.includes('3/5') && arenaHtml.includes('60%') && arenaHtml.includes('1/2/0'),
  arenaHtml.slice(0, 500))
check('训练场: 分类排行按解出数排序（web 2/3 在前）与档位列',
  arenaHtml.indexOf('web') < arenaHtml.indexOf('crypto') && arenaHtml.includes('2/3') && arenaHtml.includes('1/2'),
  arenaHtml.slice(0, 500))

// —— 训练场缓存（2026-11-20 改造门控）：节流窗口内重挂零请求；后台刷新失败保留旧榜 ——
remoteCalls.length = 0
arenaMount()
await flushat()
arenaMount()
check('训练场缓存: 节流窗口内重挂零请求（缓存命中 + 节流）',
  !remoteCalls.some((c) => c.line === '/ctf-arena stats'),
  JSON.stringify(remoteCalls))
arenaStatsBroken = true
await fillAndSubmit('cache-keep', 'web') // record 成功 → force 刷新 stats（broken）→ 保留旧榜
const keepHtml = htmlOf(arenaMount())
check('训练场缓存: 后台刷新失败时旧榜保留（refreshing 语义，不闪错误条）',
  keepHtml.includes('3/5') && !keepHtml.includes('引擎数据暂时不可用'),
  keepHtml.slice(0, 300))
arenaStatsBroken = false

/** 填表并点「记练」。桩环境没有受控输入的逐字段自动重渲染，故每填一个字段就
 *  重新挂载一帧，让下一次 onChange 的闭包读到最新表单值（否则旧闭包会把前一个字段覆盖回空）。 */
async function fillAndSubmit(name, category, opts = {}) {
  /** 在指定帧内填第 idx 个文本输入框。 */
  const fillText = (root, idx, value) => {
    const ins = findAllByClass(root, 'ctfp-search').filter((x) => x.type === 'input')
    if (ins.length <= idx) throw new Error(`arena input[${idx}] not found`)
    ins[idx].props.onChange({ target: { value } })
  }
  fillText(arenaMount(), 0, name)
  fillText(arenaMount(), 1, category) // 重渲染后再填，闭包持有含 name 的最新 form
  if (opts.fast) {
    const mark = String(opts.mark || '仅快速路径解出')
    const elFast = arenaMount()
    const cbEl = findAllByClass(elFast, 'ctfp-cb').find((x) => x.type === 'label' &&
      String(x.children[x.children.length - 1]) === mark)
    if (!cbEl) throw new Error(`checkbox "${mark}" not found`)
    cbEl.children.find((c) => c.type === 'input').props.onChange({ target: { checked: true } })
  }
  const submit = findAllByClass(arenaMount(), 'ctfp-btn')
    .find((b) => b.type === 'button' && String(b.children[0]) === '记录本次结果')
  if (!submit) throw new Error('arena submit button not found')
  submit.props.onClick()
  await flushat()
}

// —— 记练成功：引擎命令格式 + 成功提示 + 记账后自动刷新排行榜 ——
await fillAndSubmit('mockdrill', 'misc', { fast: true })
const lastRecCall = [...remoteCalls].reverse().find((c) => String(c.line).startsWith('/ctf-arena record'))
check('记练: 远端命令为 /ctf-arena record 含转义后的 name/category 与 --fast（网关 POST）',
  String(lastRecCall?.line) === '/ctf-arena record --name mockdrill --category misc --fast' && lastRecCall?.method === 'POST',
  JSON.stringify(lastRecCall))

const okHtml = htmlOf(arenaMount())
check('记练: 成功提示「已记入练兵台账」上屏', okHtml.includes('已记入练兵台账'))
check('记练: 记账成功后紧接着的 arena 拉取是 stats 刷新（容忍 redrill 状态轮询插入）',
  remoteCalls.length > 0 && String(remoteCalls[remoteCalls.length - 1].line).startsWith('/ctf-arena stats') ||
  (() => { // 允许其后紧随 /ctf-redrill-status 轮询（arena 轮询插进来的同窗口）
    const idx = remoteCalls.map((c) => String(c.line)).lastIndexOf('/ctf-arena stats')
    return idx >= 0 && remoteCalls.slice(idx + 1).every((c) => String(c.line).startsWith('/ctf-redrill-status'))
  })())

// —— 失败路径：引擎返回 kind:'error' → 可行动错误信息上屏 + 重试按钮 ——
arenaRecordBroken = true
await fillAndSubmit('broken-case', 'pwn')
const failHtml = htmlOf(arenaMount())
check('记练失败: 显式错误态（引擎返回信息上屏）', failHtml.includes('[error] arena record needs'))
check('记练失败: 提供「重试」按钮（不静默吞掉失败）', failHtml.includes('重试'))

// —— 运营纪律：排行榜读取失败不拦死操作——错误提示与「记练」表单仍同屏 ——
arenaRecordBroken = false
arenaStatsBroken = true
// 清面板缓存：模拟「无缓存首挂」——有缓存时新设计保留旧数据不亮错误条，
// 错误态路径需从无缓存状态验证。
try { globalThis.window.__ctfpilotPanelCacheReset__() } catch (_e) {}
React.reset() // 清空 hooks 桶：干净挂载 ArenaPanel 走 loadBoard 首次拉取
const p1 = React.render(Panel, { t })
await flushat() // dashboard 落地后标签栏才上屏（首帧为 loading）
const p2 = React.render(Panel, { t })
clickTab(p2, ['训练场', '题库精炼'])
const brokenRaw = React.render(Panel, { t })
const brokenComp = grabComponent(brokenRaw, (n) => /Arena/.test(n))
if (!brokenComp) throw new Error('ArenaPanel missing on stats failure')
// 次序关键：先渲染 ArenaPanel（effect 发起 stats，返回 loading 首帧）→
// 再 flushat 让失败的 stats 落地 status='error' → 最后重渲染取错误态。
React.render(brokenComp.type, brokenComp.props)
await flushat()
const brokenHtml2 = htmlOf(React.render(brokenComp.type, brokenComp.props))
check('统计失败: 显式错误态 + 重试按钮（不被误当空榜）',
  brokenHtml2.includes('引擎数据暂时不可用') && brokenHtml2.includes('重试'))
check('统计失败: 「记练」表单仍可操作（错误不拦死操作路径）',
  brokenHtml2.includes('题目名') && brokenHtml2.includes('记录本次结果'))
globalThis.setInterval = _realSetIntervalArena // 训练场段结束：恢复（后续 9.5 段自有禁用窗口）

/* ------------------------------------------------------------------ *
 * 8. 自进化（第六标签）：研究队列卡 + RSI 提案流卡（生命周期配色 + 人审决策）
 * ------------------------------------------------------------------ */

// —— 从统计失败场景回到干净挂载（清 hooks 桶，重新走整链路）——
React.reset()

/** 挂载面板并切到「自进化」标签（dashboard 异步落地，须先 flushat 让标签栏上屏）。 */
async function openSelfEvoTab() {
  React.render(Panel, { t })
  await flushat()
  const p2 = React.render(Panel, { t })
  clickTab(p2, ['自进化', '系统演进'])
}

/** 挂载 SelfEvoPanel 并返回其展开后的元素树（首帧 loading，effect 双源拉取）。 */
function sevMount() {
  const raw = React.render(Panel, { t })
  const rawComp = grabComponent(raw, (n) => /^SelfEvo/.test(n))
  if (!rawComp) throw new Error('SelfEvoPanel missing')
  return React.render(rawComp.type, rawComp.props)
}

// —— 双源落地：先挂载 SelfEvoPanel（loading 首帧，effect 发起双源拉取）→ flushat 落地 → 再挂载取数据帧 ——
await openSelfEvoTab()
sevMount()
await flushat()
const sevHtml1 = htmlOf(sevMount())
check('自进化: 标签栏含「自进化/系统演进」（第 6 个）',
  findAllByClass(React.render(Panel, { t }), 'ctfp-tab').some((b) => ['自进化', '系统演进'].includes(String(b.children[0]))))
check('自进化: 双源远端调用（/ctf-research list 与 /ctf-rsi list）',
  remoteCalls.some((c) => c.line === '/ctf-research list') && remoteCalls.some((c) => c.line === '/ctf-rsi list'))
check('系统演进: 工装体检卡与三键（一键体检 / 错误日志 / 预设审计）',
  sevHtml1.includes('工装体检') && ['一键体检', '错误日志', '预设审计'].every((l) => sevHtml1.includes(l)))
remoteCalls.length = 0
const doctorBtn = findAllByClass(sevMount(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '一键体检')
if (!doctorBtn) throw new Error('doctor button not found')
doctorBtn.props.onClick()
await flushat()
const docHtml = htmlOf(sevMount())
check('工装体检: 网关 GET /ctf-doctor 被调用（只读白名单）',
  remoteCalls.some((c) => c.line === '/ctf-doctor' && c.method === 'GET'),
  JSON.stringify(remoteCalls.slice(-3)))
check('工装体检: 诊断结论与检查项上屏（结论 PASS / engine-dir）',
  docHtml.includes('结论：PASS') && docHtml.includes('engine-dir'))
check('自进化: 研究队列卡渲染专题与证据门槛（PNG 多帧隐写 / 0/2 / 排队中）',
  sevHtml1.includes('PNG 多帧隐写') && sevHtml1.includes('证据 0/2') && sevHtml1.includes('排队中'))
check('自进化: 研究队列卡双门槛明示（两条证据才入知识库）',
  sevHtml1.includes('两条证据指针'))
check('自进化: RSI 提案流卡渲染四状态与 why 悬停提示',
  ['misc/chain-decode', 'crypto/RSA modulus/factoring analysis', 'lessons channel wording', 'web/sqlmap chain'].every((s) => sevHtml1.includes(s)) &&
  sevHtml1.includes('提案中') && sevHtml1.includes('已批准') && sevHtml1.includes('已采纳') && sevHtml1.includes('已驳回'))
check('自进化: RSI 生命周期配色类（灰/黄/绿/划线红）共存',
  (() => {
    const cls = classesOf(sevMount())
    return cls.has('ctfp-sev--off') && cls.has('ctfp-sev--yel') && cls.has('ctfp-sev--grn') && cls.has('ctfp-sev--rej')
  })())

// —— 自进化缓存（2026-11-20 改造门控）：双卡节流窗口内重挂零请求 ——
remoteCalls.length = 0
sevMount()
await flushat()
sevMount()
check('自进化缓存: 节流窗口内重挂零请求（双卡缓存命中 + 节流）',
  !remoteCalls.some((c) => c.line === '/ctf-research list' || c.line === '/ctf-rsi list'),
  JSON.stringify(remoteCalls))

// —— 决策按钮只在 proposed 行出现：全文档仅批准/驳回/采纳三个（其他状态无按钮）
check('自进化: 仅 proposed 行出现三个决策按钮（其他状态无按钮）',
  (() => {
    const decBtns = findAllByClass(sevMount(), 'ctfp-btn')
      .filter((b) => b.type === 'button' && ['批准', '驳回', '采纳'].includes(String(b.children[0])))
    return decBtns.length === 3
  })())

// —— 人审决策：点「批准」→ 远端命令格式（动作+PID+note）→ 成功提示 → 自动刷新 ——
React.reset()
await openSelfEvoTab()
sevMount()
await flushat()
const approveBtn = findAllByClass(sevMount(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '批准')
if (!approveBtn) throw new Error('approve button not found')
approveBtn.props.onClick()
await flushat()
const decCall = [...remoteCalls].reverse().find((c) => /^\/ctf-rsi approve --pid rsi-001\b/.test(String(c.line)))
check('决策: 远端命令为 /ctf-rsi approve --pid rsi-001 --note ...（网关 POST）',
  Boolean(decCall) && String(decCall.line).startsWith('/ctf-rsi approve --pid rsi-001 --note ') && decCall.method === 'POST',
  JSON.stringify(decCall))
check('决策: 成功提示「决策已记入提案台账」上屏', htmlOf(sevMount()).includes('决策已记入提案台账'))
check('决策: 成功后紧接着的远端调用是 rsi list 刷新',
  remoteCalls.length > 0 && remoteCalls[remoteCalls.length - 1].line === '/ctf-rsi list')

// —— 采纳：成功后强制 eval-suite 提示上屏（运营纪律可视化）——
React.reset()
await openSelfEvoTab()
sevMount()
await flushat()
const adoptBtn = findAllByClass(sevMount(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '采纳')
if (!adoptBtn) throw new Error('adopt button not found')
adoptBtn.props.onClick()
await flushat()
const adoptHtml = htmlOf(sevMount())
check('采纳: 成功提示 + 强制 eval-suite 提示上屏', adoptHtml.includes('决策已记入提案台账') && adoptHtml.includes('eval-suite'))

// —— 单源隔离：research 失败只亮研究卡错误条，RSI 卡照常渲染 ——
React.reset()
// 清面板缓存：模拟无缓存首挂——有缓存时新设计保留旧数据不亮错误条，
// 错误路径需从无缓存状态验证。
try { globalThis.window.__ctfpilotPanelCacheReset__() } catch (_e) {}
researchBroken = true
await openSelfEvoTab()
sevMount()
await flushat()
const isoHtml = htmlOf(sevMount())
check('隔离: research 源失败 → 研究卡错误条 + 重试按钮（engine 返回信息上屏）',
  isoHtml.includes('research list unavailable') && isoHtml.includes('重试'))
check('隔离: research 失败不影响 RSI 提案流卡（另一卡照常渲染）',
  isoHtml.includes('misc/chain-decode') && isoHtml.includes('提案中'))
researchBroken = false

// —— 决策失败：可行动错误上屏 + 「重试」重发同一决策（不静默吞掉）——
React.reset()
rsiDecisionBroken = true
await openSelfEvoTab()
sevMount()
await flushat()
const rejectBtn = findAllByClass(sevMount(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '驳回')
if (!rejectBtn) throw new Error('reject button not found')
rejectBtn.props.onClick()
await flushat()
const decFailHtml = htmlOf(sevMount())
check('决策失败: 引擎返回信息显式上屏 + 重试按钮', decFailHtml.includes('rsi decision failed') && decFailHtml.includes('重试'))
check('决策失败: 失败后提案流数据仍在（列表未被清空）',
  decFailHtml.includes('misc/chain-decode'))
// 点重试：先刷 list 再重发同一决策动作
const retryBtn = findAllByClass(sevMount(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '重试')
if (retryBtn) {
  const callsBefore = remoteCalls.length
  retryBtn.props.onClick()
  await flushat()
  const retried = [...remoteCalls].slice(callsBefore).reverse().find((c) => /^\/ctf-rsi reject --pid rsi-001\b/.test(String(c.line)))
  check('决策失败: 重试 = 先刷新再重发同一决策（/ctf-rsi reject --pid rsi-001，仅 proposed 行可决策）',
    Boolean(retried) && String(retried.line).startsWith('/ctf-rsi reject --pid rsi-001'),
    String(retried?.line))
}
rsiDecisionBroken = false

// —— 双源同时失败：双卡各亮错误条 + 各自重试（loaded 落地后不卡 loading）——
React.reset()
// 清面板缓存：同上——双源错误路径需无缓存状态验证。
try { globalThis.window.__ctfpilotPanelCacheReset__() } catch (_e) {}
researchBroken = true
rsiListBroken = true
await openSelfEvoTab()
sevMount()
await flushat()
const bothErrHtml = htmlOf(sevMount())
check('双源失败: 研究卡与提案流卡各亮错误条（含引擎返回信息）',
  bothErrHtml.includes('research list unavailable') && bothErrHtml.includes('rsi list unavailable'))
check('双源失败: 两卡各自提供「重试」按钮（不静默吞掉）',
  findAllByClass(sevMount(), 'ctfp-btn').filter((b) => b.type === 'button' && String(b.children[0]) === '重试').length >= 2)
check('双源失败: 不再停留在 loading 占位（loaded 落地）',
  !bothErrHtml.includes('正在读取引擎数据…'))
researchBroken = false
rsiListBroken = false

// —— 双源失败场景后恢复双卡缓存（本场景的 reset 清掉了 research/rsi 数据；
//    后续「面板缓存持久化」断言与重启场景依赖完整持久层）——
await openSelfEvoTab()
sevMount()
await flushat()
check('双源恢复: broken 开关复位后重挂拉回双卡数据并重建缓存',
  (() => {
    const html = htmlOf(sevMount())
    return html.includes('PNG 多帧隐写') && html.includes('misc/chain-decode')
  })())

/* ------------------------------------------------------------------ *
 * 9. 「开始解题」：新建工作区 + 新会话端到端（doStart 四处 bug 的回归门禁）
 *    覆盖：① 服务经模块级持有者（组件内引用 ctx 必 ReferenceError）；
 *          ② createDirectory 在 Host home 下建 ctf-<stamp> 新目录（create 幂等，
 *             path:'.' 会永远复用同一工作区）；③ connectWorkspace 真实签名
 *             只收 workspaceId 单参；④ 任一步失败降级为当前会话且显式提示。
 * ------------------------------------------------------------------ */

const wsCalls = { createDirectory: [], create: [], createSession: [], open: [], openSession: [], forkSession: [] }
let sessListSnap = { ids: [], byId: {}, current: undefined, phase: 'ready' } // sessions 列表快照（零会话段重置）
const wsStub = {
  listDirectory: async () => ({ path: 'C:/Users/mock', home: 'C:/Users/mock', entries: [], crumbs: [] }),
  createDirectory: async (parent, name) => { wsCalls.createDirectory.push({ parent, name }); return `${parent}/${name}` },
  openSession: (sid) => { wsCalls.openSession.push(sid) },
  pickDirectory: async () => null,
  forkSession: async () => { wsCalls.forkSession.push('called'); return 'sess-fork-1' },
}
const wssvcStub = {
  create: async (input) => { wsCalls.create.push(input); return { workspaceId: 'ws-777', path: input.path } },
  list: { getSnapshot: () => reuseList },
}
let reuseList = { items: [{ workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock/default-ws' }] } // workspaces 列表快照（层1 推 parent 与层2 复用共用）
const sessionsStub = {
  // 官方 New Session 同款 API：建会话（层1/层2 都走它）
  create: async (opts) => { wsCalls.createSession.push(opts); return 'sess-new-1' },
  open: (sid) => { wsCalls.open.push(sid) },
  list: { getSnapshot: () => sessListSnap, subscribe: () => () => {} },
}

// 注入服务桩并重新 apply：让浏览器半边把三个服务捕获进模块级持有者。
// 注意：此处不再 tailDef.inject 新会话 id——旧渲染帧的 loadPanel 订阅者会被
// notifySessionChanged 唤醒，与新帧拉取发生 dashboardInFlight 竞态（旧 setter
// 写废弃桶），面板会永远停在 loading。保持 currentSessionId 为第 6 章的
// 'sess-panel'，模块级 dashboardCache 直接命中同步上屏。
ctx.uiWorkspace = wsStub
ctx.workspaces = wssvcStub
ctx.sessions = sessionsStub
slotRegistrations.length = 0
plugin.apply(ctx)

React.reset()
remoteCalls.length = 0
wsCalls.createDirectory.length = 0; wsCalls.create.length = 0; wsCalls.createSession.length = 0
wsCalls.open.length = 0; wsCalls.openSession.length = 0

/** 挂载面板并切到「开始解题」标签，返回该帧元素树。 */
function openSolveTab() {
  const p = React.render(Panel, { t })
  const rawComp = grabComponent(p, (n) => /SolveForm/.test(n))
  if (!rawComp) throw new Error('SolveFormPanel missing (tab not active?)')
  return React.render(rawComp.type, rawComp.props)
}
React.render(Panel, { t })
await flushat() // dashboard 异步落地后标签栏才上屏
clickTab(React.render(Panel, { t }), '开始解题')

// 填题面（受控 textarea；onChange 后重渲染取最新闭包）
const sf0 = openSolveTab()
const areaTa = findByClass(sf0, 'ctfp-input-area')
if (!areaTa || typeof areaTa.props.onChange !== 'function') throw new Error('challenge textarea not found')
areaTa.props.onChange({ target: { value: '题目：解出 flag{mock_case}' } })

// 点「开始解题」主按钮 → 走 listDirectory → createDirectory → create → connectWorkspace
const startBtn = findAllByClass(openSolveTab(), 'ctfc-btn--primary')
  .find((b) => b.type === 'button' && String(b.children[0]) === '开始解题')
if (!startBtn) throw new Error('start button not found')
startBtn.props.onClick()
await flushat()
await flushat()

check('开始解题: 无 CTF-worker 时层1 发 /ctf-ensure-workspace 建固定目录（父 = 既有工作区父，目录名 CTF-worker）',
  ensureWorkspaceDirs.length === 1 &&
    /[\\/]CTF-worker$/i.test(ensureWorkspaceDirs[0]) &&
    ensureWorkspaceDirs[0].replace(/[\\/]CTF-worker$/i, '') === 'C:/Users/mock',
  JSON.stringify(ensureWorkspaceDirs))
check('开始解题: workspaces.create 注册 fs.mkdir 返回的目录（不再走 directoryPicker）',
  wsCalls.create.length === 1 && wsCalls.create[0]?.path === ensureWorkspaceDirs[0],
  JSON.stringify(wsCalls.create))
check('开始解题: sessions.create({workspaceId}) 建新对话（官方 New Session 同款 API，非导航方法）',
  wsCalls.createSession.length === 1 && wsCalls.createSession[0].workspaceId === 'ws-777',
  JSON.stringify(wsCalls.createSession))
check('开始解题: sessions.open 聚焦新会话 sess-new-1（openSession 兑底未被触发）',
  wsCalls.open.length === 1 && wsCalls.open[0] === 'sess-new-1' && wsCalls.openSession.length === 0,
  JSON.stringify({ open: wsCalls.open, openSession: wsCalls.openSession }))
check('开始解题: /ctf-solve-start 发往新会话 sess-new-1（网关 POST + sid）',
  remoteCalls.some((c) => c.sessionId === 'sess-new-1' && c.method === 'POST' && /^\/ctf-solve-start quick /.test(String(c.line))),
  JSON.stringify(remoteCalls.slice(-3)))
check('开始解题: 成功提示上屏', htmlOf(openSolveTab()).includes('已完成'))

// —— 固定 CTF-worker 复用：列表里已有 CTF-worker → 不建不注册直接用 ——
// （2026-11-20 用户令：只建唯一 CTF-worker，已存在则直接复用。）
reuseList = { items: [
  { workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' },
  { workspaceId: 'ws-ctf-worker', title: 'CTF-worker', path: 'C:/Users/mock/CTF-worker' },
] }
ensureWorkspaceDirs.length = 0
wsCalls.create.length = 0; wsCalls.createSession.length = 0; wsCalls.open.length = 0
const sfWorker0 = openSolveTab()
findByClass(sfWorker0, 'ctfp-input-area').props.onChange({ target: { value: '题目：CTF-worker 已存在场景' } })
const startBtnW = findAllByClass(openSolveTab(), 'ctfc-btn--primary')
  .find((b) => b.type === 'button' && String(b.children[0]) === '开始解题')
if (!startBtnW) throw new Error('start button (worker-reuse case) not found')
startBtnW.props.onClick()
await flushat()
await flushat()
check('CTF-worker 复用: 已存在时不发 ensure-workspace、不注册新工作区',
  ensureWorkspaceDirs.length === 0 && wsCalls.create.length === 0,
  JSON.stringify({ ensure: ensureWorkspaceDirs, create: wsCalls.create }))
check('CTF-worker 复用: sessions.create 直接指向 ws-ctf-worker',
  wsCalls.createSession.length === 1 && wsCalls.createSession[0].workspaceId === 'ws-ctf-worker',
  JSON.stringify(wsCalls.createSession))
check('CTF-worker 复用: 成功提示附复用说明',
  htmlOf(openSolveTab()).includes('已复用固定工作区 CTF-worker'),
  htmlOf(openSolveTab()).slice(0, 300))

// —— 层2 复用：ensure-workspace 失败 + 既有工作区存在 → 复用工作区新建对话 ——
// （2026-11-19 改造：层1 不再走 directoryPicker，改为 /ctf-ensure-workspace；
//   破坏开关也随之迁移。）
reuseList = { items: [{ workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' }] }
ensureWorkspaceBroken = true
wsCalls.createDirectory.length = 0; wsCalls.create.length = 0; wsCalls.createSession.length = 0; wsCalls.open.length = 0
const sfReuse0 = openSolveTab()
findByClass(sfReuse0, 'ctfp-input-area').props.onChange({ target: { value: '题目：复用工作区场景' } })
const startBtn3 = findAllByClass(openSolveTab(), 'ctfc-btn--primary')
  .find((b) => b.type === 'button' && String(b.children[0]) === '开始解题')
if (!startBtn3) throw new Error('start button (reuse case) not found')
startBtn3.props.onClick()
await flushat()
await flushat()

const reuseHtml = htmlOf(openSolveTab())
check('层2复用: 建目录环节失败时改在既有工作区新建对话（提示含工作区名与原因）',
  reuseHtml.includes('既有工作区') && reuseHtml.includes('Home') && reuseHtml.includes('ensure-workspace boom'),
  reuseHtml.slice(0, 400))
check('层2复用: sessions.create 指向既有工作区 ws-home（真新对话，非聚焦旧会话）',
  wsCalls.createSession.length === 1 && wsCalls.createSession[0].workspaceId === 'ws-home',
  JSON.stringify(wsCalls.createSession))
check('层2复用: /ctf-solve-start 发往复用工作区的新会话 sess-new-1',
  remoteCalls.length > 0 && remoteCalls[remoteCalls.length - 1].sessionId === 'sess-new-1' &&
    /^\/ctf-solve-start quick /.test(String(remoteCalls[remoteCalls.length - 1].line)),
  JSON.stringify(remoteCalls.slice(-2)))

// —— 层3 全失败：sessions.create 也抛错 → 降级当前会话 + 显式提示（绝不静默） ——
ensureWorkspaceBroken = false
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); throw new Error('mock-sess-boom') }
wsCalls.createDirectory.length = 0; wsCalls.create.length = 0; wsCalls.createSession.length = 0; wsCalls.open.length = 0
const sfFail0 = openSolveTab()
findByClass(sfFail0, 'ctfp-input-area').props.onChange({ target: { value: '题目：全失败降级场景' } })
const startBtn4 = findAllByClass(openSolveTab(), 'ctfc-btn--primary')
  .find((b) => b.type === 'button' && String(b.children[0]) === '开始解题')
if (!startBtn4) throw new Error('start button (fallback case) not found')
startBtn4.props.onClick()
await flushat()
await flushat()

const fallbackHtml = htmlOf(openSolveTab())
check('层3降级: 建会话失败提示显式上屏（含 session 错误信息）',
  fallbackHtml.includes('降级') && fallbackHtml.includes('mock-sess-boom'), fallbackHtml.slice(0, 400))
check('层3降级: 层1 与层2 都尝试过 sessions.create（各 1 次，均未能建成）',
  wsCalls.createSession.length === 2, JSON.stringify(wsCalls.createSession))
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-new-1' } // 还原桩

// —— 零会话（2026-11-20 网关改造后）：数据读取走 HTTP 网关，与会话完全
// 解耦——currentSessionId 清空也照常拉数据（旧 routeSessionId 池已删）。
sessListSnap = { ids: ['sess-x'], byId: {}, current: undefined, phase: 'ready' }
byName('conversation.chat.turnTail').inject('') // 清空 currentSessionId：模拟「还没打开任何对话」
await flushat() // 让 inject 唤醒的旧渲染帧 loadPanel 先 settle（防 dashboardInFlight 竞态）

React.reset()
remoteCalls.length = 0
React.render(Panel, { t }) // 新帧：网关拉取，零会话依赖
await flushat()
// 2026-11-26：首访默认标签已改为「开始解题」，KPI 在知识库屏——先切标签再断言。
clickTab(React.render(Panel, { t }), '知识库')
const zeroFrame0 = React.render(Panel, { t })
const zeroHtml = htmlOf(zeroFrame0)
check('零会话: currentSessionId 为空时面板仍上屏引擎数据（网关拉取，无会话要求）',
  zeroHtml.includes('622') && !zeroHtml.includes('打开任意会话'),
  zeroHtml.slice(0, 200))
// 点「刷新」强制重拉：GET /ctfpilot/data/ctf-dashboard 且不带 sid（零会话耦合）
const refreshBtn = findAllByClass(zeroFrame0, 'ctfp-btn')
  .find((b) => b.type === 'button' && b.props && b.props['aria-label'] === t('refresh'))
if (!refreshBtn) throw new Error('refresh button not found (zero-session case)')
refreshBtn.props.onClick()
await flushat()
check('零会话: 强制刷新走网关 GET 且无 sid（数据读取零会话耦合）',
  remoteCalls.some((c) => c.line === '/ctf-dashboard' && c.method === 'GET' && !c.sessionId),
  JSON.stringify(remoteCalls.slice(-3)))
check('零会话: 「开始解题」表单仍可渲染（不因无会话被空态拦截）',
  (() => {
    clickTab(React.render(Panel, { t }), '开始解题')
    return Boolean(grabComponent(React.render(Panel, { t }), (n) => /SolveForm/.test(n)))
  })())

/* ------------------------------------------------------------------ *
 * 9b. 工具管理：点装/更/卸 → 新对话 → agent 注入模式（2026-11-20 用户方案）
 *     覆盖：① 点安装先走 ensureSolveSession（固定 CTF-worker 复用链）；
 *           ② /ctf-tools install <id> POST 路由到新会话 sid；③ 成功回执上屏
 *              并跳新对话；④ 注入失败 error banner 停在本面板（不跳转）。
 * ------------------------------------------------------------------ */

// 复用第 9 章的服务桩（wsStub/wssvcStub/sessionsStub 已注入模块级持有者）：
// 列表里已放 CTF-worker → 点装应直接复用 ws-ctf-worker 建新对话。
reuseList = { items: [
  { workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' },
  { workspaceId: 'ws-ctf-worker', title: 'CTF-worker', path: 'C:/Users/mock/CTF-worker' },
] }
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-tools-1' }
wsCalls.create.length = 0; wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
ensureWorkspaceDirs.length = 0

/** 挂载面板并切到「工具管理」标签，返回该帧元素树。 */
function openToolsTab() {
  const p = React.render(Panel, { t })
  const rawComp = grabComponent(p, (n) => /ToolsPanel/.test(n))
  if (!rawComp) throw new Error('ToolsPanel missing (tab not active?)')
  return React.render(rawComp.type, rawComp.props)
}
clickTab(React.render(Panel, { t }), '工具管理')
openToolsTab() // 首次挂载：useEffect 同步发起 status 网关拉取
await flushat()
await flushat() // status 拉取落地后行才上屏（fake React 每次 render 都重跑 effect，取数前必须 flush）
const toolsHtml = htmlOf(openToolsTab())
check('工具管理: 快检行上屏（status 网关拉取 + 目录行）',
  toolsHtml.includes('nmap') && toolsHtml.includes('sqlmap'), toolsHtml.slice(0, 200))

// 点第一行「安装」→ ensureSolveSession（复用 CTF-worker）→ POST /ctf-tools install nmap 到新会话。
wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
const toolsFrame = openToolsTab() // 挂载取按钮帧（effect 已在前一轮 settle）
await flushat()
const installBtn = findAllByClass(toolsFrame, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '安装')
if (!installBtn) throw new Error('install button not found')
installBtn.props.onClick()
await flushat()
await flushat()
const actFrame = openToolsTab() // act 完成 + loadStatus 复检落地后的结果帧

check('工具管理点装: 先经 ensureSolveSession 复用 CTF-worker 建新对话（不建目录不注册）',
  ensureWorkspaceDirs.length === 0 && wsCalls.create.length === 0 &&
    wsCalls.createSession.some((c) => c && c.workspaceId === 'ws-ctf-worker'),
  JSON.stringify({ ensure: ensureWorkspaceDirs, create: wsCalls.create, createSession: wsCalls.createSession }))
check('工具管理点装: /ctf-tools install POST 路由到新会话 sess-tools-1',
  remoteCalls.some((c) => c.method === 'POST' && c.sessionId === 'sess-tools-1' && /^\/ctf-tools install nmap$/.test(String(c.line))),
  JSON.stringify(remoteCalls.slice(-5)))
check('工具管理点装: agent 注入回执上屏（含「请求 agent」与注入内容）',
  htmlOf(actFrame).includes('请求 agent') && htmlOf(actFrame).includes('注入内容'),
  htmlOf(actFrame).slice(0, 400))
check('工具管理点装: 成功后聚焦新对话 sess-tools-1（openSession 导航兜底未触发）',
  wsCalls.open.length === 1 && wsCalls.open[0] === 'sess-tools-1' && wsCalls.openSession.length === 0,
  JSON.stringify({ open: wsCalls.open, openSession: wsCalls.openSession }))

// —— 注入失败场景：error banner 停在本面板、不跳转 ——
toolsActionBroken = true
wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
const toolsFrame2 = openToolsTab()
await flushat()
const installBtn2 = findAllByClass(toolsFrame2, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '安装')
if (!installBtn2) throw new Error('install button (broken case) not found')
installBtn2.props.onClick()
await flushat()
await flushat()
const brokenFrame2 = openToolsTab()

check('工具管理失败: 注入失败的 error banner 上屏（含 stub 原因）',
  htmlOf(brokenFrame2).includes('注入失败') && htmlOf(brokenFrame2).includes('stub boom'),
  htmlOf(brokenFrame2).slice(0, 400))
check('工具管理失败: 失败不跳转（sessions.open 未被调用，banner 可读）',
  wsCalls.open.length === 0, JSON.stringify({ open: wsCalls.open }))
toolsActionBroken = false

// —— 一键安装（2026-11-24 用户令）：点一下 → 一个新对话装完全部未装工具 ——
// 桩改回新会话 id（sess-tools-2）：上一段点装后 currentSessionId 已是
// sess-tools-1，若仍回同 id 会命中「目标=当前」而不跳转——换 id 才能验跳转链。
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-tools-2' }
wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
const toolsFrame3 = openToolsTab()
await flushat()
const installAllBtn = findAllByClass(toolsFrame3, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]).startsWith('一键安装'))
if (!installAllBtn) throw new Error('install-all button not found')
check('工具管理一键安装: 按钮带未装计数（stub 行 nmap/sqlmap 缺装 → 2 个未装）',
  String(installAllBtn.children[0]).includes('一键安装（2 个未装）'), String(installAllBtn.children[0]))
installAllBtn.props.onClick()
await flushat()
await flushat()
const allFrame = openToolsTab()
check('工具管理一键安装: POST /ctf-tools install-all 路由到新会话（一个对话装全部，非逐工具开对话）',
  remoteCalls.some((c) => c.method === 'POST' && c.sessionId === 'sess-tools-2' && String(c.line) === '/ctf-tools install-all'),
  JSON.stringify(remoteCalls.slice(-5)))
check('工具管理一键安装: 成功回执上屏并聚焦新对话 sess-tools-2',
  htmlOf(allFrame).includes('请求 agent 一键安装') &&
    wsCalls.open.length === 1 && wsCalls.open[0] === 'sess-tools-2',
  JSON.stringify({ open: wsCalls.open }) + ' | ' + htmlOf(allFrame).slice(0, 200))

// —— 批量重刷（2026-11-25 用户令）：训练场新按钮 → 新开快解对话 → 一条协议注入 ——
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-drill-1' }
wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
clickTab(React.render(Panel, { t }), ['训练场', '题库精炼'])
const arenaRaw = grabComponent(React.render(Panel, { t }), (n) => /ArenaPanel/.test(n))
if (!arenaRaw) throw new Error('ArenaPanel missing (arena tab not active?)')
React.render(arenaRaw.type, arenaRaw.props) // 首挂：effect 发起 stats 拉取
await flushat()
await flushat() // stats 落地写 board（缓存命中 + 后台再校验）
const arenaFrame = React.render(arenaRaw.type, arenaRaw.props) // 落定帧
const drillBtn = findAllByClass(arenaFrame, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '批量重刷题库（新对话）')
if (!drillBtn) throw new Error('drill button not found; buttons=' +
  findAllByClass(arenaFrame, 'ctfp-btn').map((b) => String(b.children && b.children[0])).join('|'))
drillBtn.props.onClick()
await flushat()
await flushat()
const drillFrame = React.render(arenaRaw.type, arenaRaw.props)
check('训练场批量重刷: POST /ctf-drill-start 路由到新会话 sess-drill-1',
  remoteCalls.some((c) => c.method === 'POST' && c.sessionId === 'sess-drill-1' && String(c.line) === '/ctf-drill-start'),
  JSON.stringify(remoteCalls.slice(-5)))
check('训练场批量重刷: 成功回执上屏并聚焦新对话',
  htmlOf(drillFrame).includes('批量重刷已注入') &&
    wsCalls.open.length === 1 && wsCalls.open[0] === 'sess-drill-1',
  JSON.stringify({ open: wsCalls.open }) + ' | ' + htmlOf(drillFrame).slice(0, 200))

// —— 交叉重刷（2026-11-26 修复回归）：active campaign 点「恢复/继续考核」必须发 --resume ——
// 修复前 startRedrill(!rdActive) 方向反：active 时传 resume=false → Host 走 plan
// 分支被引擎拒（campaign already active），表现为点按钮无任何反应。
// 同段覆盖 ensureSolveSession 显式返回 dir 的修复：--workspace 必须取自 ensure
// 返回值（快照二次推导有时序空窗）。
redrillStatusBlob = {
  campaign: 'c-test', status: 'active', fold: 1, folds: 3,
  slots: [{ fold: 1, slot: 'S01', category: 'crypto', state: 'pending', via: null, suspicious: false }],
  last_errors: [{ ts: '2026-11-26T14:00:00+0800', action: 'begin', phase: 'error', detail: { error: 'stub recent error' } }],
}
reuseList = { items: [
  { workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' },
  { workspaceId: 'ws-redrill-worker', title: 'CTF-redrill-worker', path: 'C:/Users/mock/CTF-redrill-worker' },
] }
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-redrill-1' }
wsCalls.createSession.length = 0; wsCalls.open.length = 0
remoteCalls.length = 0
const rdMount = () => {
  const raw = grabComponent(React.render(Panel, { t }), (n) => /ArenaPanel/.test(n))
  if (!raw) throw new Error('ArenaPanel missing (redrill scenario)')
  return { raw, frame: React.render(raw.type, raw.props) }
}
rdMount() // 首挂：effect 发起 /ctf-redrill-status
await flushat()
await flushat()
const rdFrameA = rdMount().frame
const resumeBtn = findAllByClass(rdFrameA, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]).includes('恢复/继续考核'))
if (!resumeBtn) throw new Error('resume button not found; buttons=' +
  findAllByClass(rdFrameA, 'ctfp-btn').map((b) => String(b.children && b.children[0])).join('|'))
resumeBtn.props.onClick()
await flushat()
await flushat()
const rdStartCall = [...remoteCalls].reverse().find((c) => String(c.line).startsWith('/ctf-redrill-start'))
check('交叉重刷恢复: active 点「恢复/继续考核」→ 发 --resume 且路由到新训练会话（2026-11-26 修复回归）',
  Boolean(rdStartCall) && rdStartCall.line.includes('--resume') && rdStartCall.method === 'POST' &&
    rdStartCall.sessionId === 'sess-redrill-1',
  JSON.stringify({ line: rdStartCall && rdStartCall.line, sid: rdStartCall && rdStartCall.sessionId }))
check('交叉重刷恢复: --workspace 取自 ensureSolveSession 返回的目录（不依赖快照二次推导）',
  Boolean(rdStartCall) && rdStartCall.line.includes('--workspace') && rdStartCall.line.includes('CTF-redrill-worker'),
  String(rdStartCall && rdStartCall.line))
const rdFrameA2 = rdMount().frame
check('交叉重刷: 进度区直显最近引擎错误（last_errors 运维通道）',
  htmlOf(rdFrameA2).includes('最近引擎错误') && htmlOf(rdFrameA2).includes('stub recent error'),
  htmlOf(rdFrameA2).slice(0, 400))
check('交叉重刷: 「诊断」按钮就位（/ctf-redrill-diag 入口）',
  findAllByClass(rdFrameA2, 'ctfp-btn').some((b) => b.type === 'button' && String(b.children[0]).includes('诊断')))

// 反向：非 active（done）→ 按钮「开始交叉重刷」，不得带 --resume（新建而非恢复）。
redrillStatusBlob = { campaign: 'c-old', status: 'done', fold: 3, folds: 3, slots: [] }
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-redrill-2' }
remoteCalls.length = 0
rdMount()
await flushat()
await flushat()
const rdFrameB = rdMount().frame
const startBtn2 = findAllByClass(rdFrameB, 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]).includes('开始交叉重刷'))
if (!startBtn2) throw new Error('start button not found; buttons=' +
  findAllByClass(rdFrameB, 'ctfp-btn').map((b) => String(b.children && b.children[0])).join('|'))
startBtn2.props.onClick()
await flushat()
await flushat()
const rdStartCallB = [...remoteCalls].reverse().find((c) => String(c.line).startsWith('/ctf-redrill-start'))
check('交叉重刷开始: 非 active 点「开始交叉重刷」→ 不带 --resume（不会误走恢复分支）',
  Boolean(rdStartCallB) && !rdStartCallB.line.includes('--resume') && rdStartCallB.sessionId === 'sess-redrill-2',
  JSON.stringify({ line: rdStartCallB && rdStartCallB.line, sid: rdStartCallB && rdStartCallB.sessionId }))
redrillStatusBlob = null
reuseList = { items: [
  { workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' },
  { workspaceId: 'ws-ctf-worker', title: 'CTF-worker', path: 'C:/Users/mock/CTF-worker' },
] }
sessionsStub.create = async (opts) => { wsCalls.createSession.push(opts); return 'sess-tools-2' }
clickTab(React.render(Panel, { t }), '工具管理') // 还原标签：后续工具页缓存段仍假设 工具管理 激活

// —— 工具页缓存（2026-11-20 改造门控）：节流零请求 + force 刷新发 --fresh ——
remoteCalls.length = 0
openToolsTab()
await flushat()
openToolsTab()
check('工具页缓存: 节流窗口内重挂零请求（缓存命中 + 节流）',
  !remoteCalls.some((c) => /^\/ctf-tools status/.test(String(c.line))),
  JSON.stringify(remoteCalls))
const toolsRefreshBtn = findAllByClass(openToolsTab(), 'ctfp-btn')
  .find((b) => b.type === 'button' && String(b.children[0]) === '一键快检')
if (!toolsRefreshBtn) throw new Error('tools refresh button not found')
toolsRefreshBtn.props.onClick()
await flushat()
check('工具页缓存: force 刷新绕节流且带 --fresh（Host 侧探针快照 TTL 跳过）',
  remoteCalls.some((c) => String(c.line) === '/ctf-tools status --fresh' && c.method === 'POST'),
  JSON.stringify(remoteCalls.slice(-3)))
// —— 面板缓存持久化（localStorage panels.v1）：UI 重启可恢复（arena-stats 已在
//    训练场段单独验证——统计失败场景的 reset 会清掉它）——
const panelsStored = lsStore.get('ctfpilot.panels.v1')
check('面板缓存: 拉取成功后持久化到 localStorage（panels.v1，重启可恢复）',
  Boolean(panelsStored) && ['"tools-status"', '"research-list"', '"rsi-list"']
    .every((k) => String(panelsStored).includes(k)),
  `len=${panelsStored ? String(panelsStored).length : 0}`)

/* ------------------------------------------------------------------ *
 * 9.5 「未解出 → 同会话续攻」：卡片按钮端到端（2026-11-25 用户方案）
 *     旧实现快解模式 fork 新会话——fork 出的会话继承回合历史导致预设锁定，
 *     也无法正确接续原对话。现改为在原会话 POST /ctf-continue 开新回合：
 *      ① 确认后只发 /ctf-continue，绝不再调用 forkSession；
 *      ② 命令带齐题面/分类/靶机/附件，sid 指向原会话；
 *      ③ 成功回执上屏，本地条目保持未解出并置 running（等 Host 快照接管）；
 *      ④ 注入失败显式上屏（旧实现 void 发射后不管 → 点了没反应）。
 * ------------------------------------------------------------------ */

const boardEntry = (over) => ({
  mode: 'quick', cat: 'web', target: 'http://10.0.0.5:8080',
  attaches: ['C:/tmp/a.zip'], body: '帮我扫这个站并拿到 flag',
  startedAt: Date.now(), running: false, flags: [], status: 'running',
  ...over,
})
/** 挂载面板（等缓存上屏）→ 切「正在解题」→ 返回该帧元素树。 */
async function openSolvingBoardPanel() {
  React.render(Panel, { t })
  await flushat() // 标签栏只在 blob && status==='ok' 时渲染：等 loadPanel 落地
  const p0 = React.render(Panel, { t })
  clickTab(p0, '正在解题')
  return React.render(Panel, { t })
}
/** 在元素树里按按钮文字取第一个可点按钮。 */
function findButtonByText(root, label) {
  return findAllByClass(root, 'ctfp-btn')
    .find((b) => b.type === 'button' && String(Array.isArray(b.children) ? b.children[0] : '') === label)
}

// SolvingBoard 的 4s 轮询在 React 桩里拿不到 cleanup（useEffect 返回值被丢弃），
// 会把 Node 事件循环钉住 → 本场景临时禁用定时器，场景结束恢复。
const realSetInterval = globalThis.setInterval
globalThis.setInterval = () => 0
lsStore.set('ctfpilot.solveboard.v1', JSON.stringify({ version: 1, sessions: { 'sess-board-1': boardEntry() } }))
React.reset()
remoteCalls.length = 0
wsCalls.forkSession.length = 0
const boardA = await openSolvingBoardPanel()
const unsolvedBtnA = findButtonByText(boardA, '未解出')
if (!unsolvedBtnA) throw new Error('未解出 button missing')
unsolvedBtnA.props.onClick({ stopPropagation() {} })
const boardA2 = React.render(Panel, { t })
check('续攻: 确认面板文案说明「同一会话开新回合」（不再提 fork）',
  htmlOf(boardA2).includes('在同一会话开新回合继续解题') && !htmlOf(boardA2).includes('fork'),
  htmlOf(boardA2).slice(0, 200))
const confirmBtnA = findButtonByText(boardA2, '确认执行')
if (!confirmBtnA) throw new Error('确认执行 button missing')
confirmBtnA.props.onClick({ stopPropagation() {} })
await flushat()
const contCall = remoteCalls.find((c) => String(c.line).startsWith('/ctf-continue'))
check('续攻: 确认后先聚焦原会话再发 /ctf-continue，且绝不 fork 新会话',
  Boolean(contCall) && wsCalls.forkSession.length === 0 && wsCalls.open.includes('sess-board-1'),
  `calls=${JSON.stringify(remoteCalls.map((c) => c.line))} open=${JSON.stringify(wsCalls.open)} fork=${wsCalls.forkSession.length}`)
check('续攻: 命令带齐题面/分类/靶机/附件且 sid 指向原会话',
  Boolean(contCall) && contCall.sessionId === 'sess-board-1' && contCall.method === 'POST' &&
    contCall.line.includes('--category web') &&
    contCall.line.includes('--target') && contCall.line.includes('10.0.0.5') &&
    contCall.line.includes('--attach') && contCall.line.includes('a.zip') &&
    contCall.line.includes('扫这个站'),
  contCall ? String(contCall.line).slice(0, 260) : 'no /ctf-continue call')
const boardAok = htmlOf(React.render(Panel, { t }))
check('续攻: 成功回执上屏（同会话开新回合，上下文与进度保留）',
  boardAok.includes('已在同一会话开新回合继续解题'))
const boardStoreA = JSON.parse(String(lsStore.get('ctfpilot.solveboard.v1') || '{}'))
check('续攻: 本地条目保持未解出并置 running（Host 快照随后接管）',
  Boolean(boardStoreA.sessions) && boardStoreA.sessions['sess-board-1'].status === 'unsolved' &&
    boardStoreA.sessions['sess-board-1'].running === true,
  JSON.stringify(boardStoreA.sessions && boardStoreA.sessions['sess-board-1']))
// —— 多轮续攻：一轮结束（Host 快照翻 running=false）后按钮必须重新可点 ——
boardStoreA.sessions['sess-board-1'].running = false
lsStore.set('ctfpilot.solveboard.v1', JSON.stringify(boardStoreA))
React.reset()
const boardA3 = await openSolvingBoardPanel()
const unsolvedBtnA3 = findButtonByText(boardA3, '未解出')
check('续攻: 一轮结束（running=false）后「未解出」按钮重新可点（支持多轮续攻）',
  Boolean(unsolvedBtnA3) && unsolvedBtnA3.props.disabled === false,
  unsolvedBtnA3 ? `disabled=${unsolvedBtnA3.props.disabled}` : 'button missing')
// —— 失败路径：注入失败必须显式上屏（旧实现静默吞掉）——
continueBroken = true
lsStore.set('ctfpilot.solveboard.v1', JSON.stringify({ version: 1, sessions: { 'sess-board-1': boardEntry() } }))
React.reset()
const boardB = await openSolvingBoardPanel()
const unsolvedBtnB = findButtonByText(boardB, '未解出')
if (!unsolvedBtnB) throw new Error('未解出 button missing (failure case)')
unsolvedBtnB.props.onClick({ stopPropagation() {} })
const boardB2 = React.render(Panel, { t })
const confirmBtnB = findButtonByText(boardB2, '确认执行')
if (!confirmBtnB) throw new Error('确认执行 button missing (failure case)')
confirmBtnB.props.onClick({ stopPropagation() {} })
await flushat()
const boardBfail = htmlOf(React.render(Panel, { t }))
check('续攻: 注入失败显式上屏（带 Host 原始原因，不再静默）',
  boardBfail.includes('继续解题注入失败') && boardBfail.includes('stub gateway unavailable'),
  boardBfail.slice(0, 240))
continueBroken = false

/* ------------------------------------------------------------------ *
 * 9.6 解题卡状态行结构回归（2026-11-25 用户实锤「运行中/本轮已结束乱排」）：
 *     根因 = 状态文字被塞进 8px 的 .ctfp-dot 圆点容器，溢出压排。正确结构
 *     = .ctfp-runstate 组合容器下两个兄弟元素：空圆点 + 文字 span。
 *     （本段在 9.5 的 setInterval 禁用作用域内挂载「正在解题」板。）
 * ------------------------------------------------------------------ */

check('状态行 CSS: ctfp-runstate 组合容器与 ctfp-dot--off 定义齐备（无悬空引用）',
  /\.ctfp-runstate\{display:inline-flex/.test(cssText) && /\.ctfp-dot--off\{/.test(cssText),
  'runstate/off rules missing in STYLE_TEXT')
lsStore.set('ctfpilot.solveboard.v1', JSON.stringify({ version: 1, sessions: {
  'sess-run-1': boardEntry({ running: true, status: 'running' }),
  'sess-run-2': boardEntry({ running: false, status: 'unsolved' }),
} }))
React.reset()
const boardState = await openSolvingBoardPanel()
const runstates = findAllByClass(boardState, 'ctfp-runstate')
check('状态行: 圆点与文字分置两个兄弟元素（点空、字有文，杜绝文字进 8px 圆点）',
  runstates.length === 2 && runstates.every((rs) => {
    const kids = (Array.isArray(rs.children) ? rs.children : []).filter(Boolean)
    const dot = kids.find((k) => String((k.props && k.props.className) || '').includes('ctfp-dot'))
    return kids.length === 2 && Boolean(dot) && (dot.children || []).length === 0
  }),
  `n=${runstates.length}`)
const stateHtml = htmlOf(boardState)
check('状态行: 运行中/本轮已结束按 running 分支上屏',
  stateHtml.includes('运行中') && stateHtml.includes('本轮已结束'),
  stateHtml.slice(0, 120))
check('状态行: 「跳转到该对话」按钮归位操作行（acts 内，不再插在标题与 flag 行之间）',
  findAllByClass(boardState, 'ctfp-scard__acts').length === 2 &&
    findAllByClass(boardState, 'ctfp-scard__acts')
      .every((acts) => (Array.isArray(acts.children) ? acts.children : [])
        .filter(Boolean)
        .some((k) => String((Array.isArray(k.children) ? k.children[0] : k.children) || '') === '跳转到该对话')),
  'jump button not in acts row')

globalThis.setInterval = realSetInterval

/* ------------------------------------------------------------------ *
 * 10. UI 重启模拟：localStorage 持久层恢复（stale-while-revalidate 首步）
 * ------------------------------------------------------------------ */

// —— 合法恢复：重载 client.js（新 factory、模块级缓存清零）→ 模块加载即从
// localStorage 恢复上次看板 → 首帧零远程调用直接上屏（重启后打开面板即见数据，
// 不再转「正在读取引擎」）。
remoteCalls.length = 0
loaded.length = 0
new Function(fs.readFileSync(CLIENT, 'utf8'))()
const pluginReboot = loaded[0].factory(requireShim)
slotRegistrations.length = 0
pluginReboot.apply(ctx)
React.reset()
const PanelReboot = byName('main').Component
React.render(PanelReboot, { t }) // 恢复的缓存命中 → present 同步写桶 + 节流 return
await flushat()
clickTab(React.render(PanelReboot, { t }), '知识库') // 默认标签已是「开始解题」，KPI 在知识库屏
const rebootHtml = htmlOf(React.render(PanelReboot, { t }))
check('重启: 模块重载后首帧零请求上屏持久化看板（localStorage 恢复生效）',
  rebootHtml.includes('622') && !remoteCalls.some((c) => c.line === '/ctf-dashboard'),
  `calls=${JSON.stringify(remoteCalls)} html=${rebootHtml.slice(0, 120)}`)

// —— 损坏数据防御：持久层被写坏（旧版残留/截断）→ 归一化丢弃 → 重新拉取，
// 不炸不污染。
lsStore.set('ctfpilot.dashboard.v1', '{corrupted!! not json')
loaded.length = 0
new Function(fs.readFileSync(CLIENT, 'utf8'))()
const pluginBad = loaded[0].factory(requireShim)
slotRegistrations.length = 0
pluginBad.apply(ctx)
React.reset()
remoteCalls.length = 0
const PanelBad = byName('main').Component
React.render(PanelBad, { t }) // 恢复失败 → 无缓存 → 发请求重新拉取
await flushat()
clickTab(React.render(PanelBad, { t }), '知识库') // 默认标签已是「开始解题」，KPI 在知识库屏
check('重启: 持久层损坏数据被丢弃并重新拉取（防御性归一化，不炸不污染）',
  remoteCalls.some((c) => c.line === '/ctf-dashboard') && htmlOf(React.render(PanelBad, { t })).includes('622'),
  `calls=${JSON.stringify(remoteCalls)}`)

/* ------------------------------------------------------------------ *
 * 10b. 空账本防御（2026-11-26 用户令：操作屏不被数据门槛拦住）
 * ------------------------------------------------------------------ */

dashboardEmpty = true
lsStore.delete('ctfpilot.dashboard.v1')
loaded.length = 0
new Function(fs.readFileSync(CLIENT, 'utf8'))()
const pluginEmpty = loaded[0].factory(requireShim)
slotRegistrations.length = 0
pluginEmpty.apply(ctx)
React.reset()
remoteCalls.length = 0
const PanelEmpty = byName('main').Component
React.render(PanelEmpty, { t })
await flushat()
const emptyPanel = React.render(PanelEmpty, { t })
const emptyHtml = htmlOf(emptyPanel)
check('空账本: 六个主标签仍渲染（数据门槛不再拦操作屏）',
  classesOf(emptyPanel).has('ctfp-tabs') &&
    ['开始解题', '正在解题', '工具管理', '知识库', '题库精炼', '系统演进'].every((l) => emptyHtml.includes(l)),
  emptyHtml.slice(0, 160))
clickTab(React.render(PanelEmpty, { t }), '工具管理')
/** 与 9b 同款：直接渲染工具屏内部组件，flush 后再取帧（缓存命中即首帧出标题）。 */
const openEmptyTools = () => {
  const raw = grabComponent(React.render(PanelEmpty, { t }), (n) => /ToolsPanel/.test(n))
  if (!raw) throw new Error('ToolsPanel missing (tab not active?)')
  return React.render(raw.type, raw.props)
}
openEmptyTools()
await flushat()
await flushat()
const emptyToolsHtml = htmlOf(openEmptyTools())
check('空账本: 工具管理屏可进入并渲染（缓存命中或重新拉取均可，不依赖台账）',
  emptyToolsHtml.includes('CTF 工具管理'),
  `html=${emptyToolsHtml.slice(0, 200)}`)

/* ------------------------------------------------------------------ *
 * 汇总
 * ------------------------------------------------------------------ */

console.log(`\nclient.js 离线运行时验证：${passed} 通过，${failed} 失败`)
// 通道不稳时的兜底：结果无条件落盘（writeFileSync 在 process.exit 之前完成）
if (process.env.CTFPILOT_VERIFY_OUT) {
  try {
    fs.writeFileSync(
      process.env.CTFPILOT_VERIFY_OUT,
      `PASS=${passed}\nFAIL=${failed}` + (failures.length ? `\nFAILURES:\n  - ${failures.join('\n  - ')}` : '') + '\n',
      'utf8',
    )
  } catch (_e) { /* 文件写入失败不掩盖原断言结果 */ }
}
if (failed > 0) {
  console.error('FAILED:\n  - ' + failures.join('\n  - '))
  process.exit(1)
}
console.log('ALL CLIENT-SIDE CHECKS PASSED')



