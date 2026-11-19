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

/** 展开元素树中函数类型的组件元素（等同 React 调用子组件），纯函数无副作用。 */
function expand(el) {
  if (!el || typeof el !== 'object') return el
  if (Array.isArray(el)) return el.map(expand)
  if (typeof el.type === 'function') return expand(el.type(el.props || {}))
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
let remoteCalls = []

/** 远端命令桩：/ctf-dashboard 返回引擎看板 JSON；/ctf-challenge-detail 返回单题详情；record-win 一律成功。 */
let arenaRecordBroken = false // /ctf-arena record 失败模拟开关（引擎退出码≠0 → 桥返回 kind:'error'）
let arenaStatsBroken = false // /ctf-arena stats 失败模拟开关（返回 kind:'error'，验证错误不拦死表单）
let researchBroken = false // /ctf-research list 失败模拟（单卡隔离验证）
let rsiListBroken = false // /ctf-rsi list 失败模拟（单卡隔离验证）
let rsiDecisionBroken = false // /ctf-rsi 决策动词失败模拟（可行动错误 + 重试路径）

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
  remoteCalls.push({ sessionId, line })
  if (String(line).startsWith('/ctf-arena stats')) {
    if (arenaStatsBroken) {
      return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'error', text: '[error] sqlite unavailable' } } })
    }
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: arenaBoardText } } })
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
  if (String(line).startsWith('/ctf-dashboard')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(engineJson) } } })
  }
  if (String(line).startsWith('/ctf-challenge-detail')) {
    return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: JSON.stringify(detailJson) } } })
  }
  return Promise.resolve({ ok: true, value: { commandId: `cmd-${remoteCalls.length}`, result: { kind: 'success', text: '已记入引擎账本' } } })
}

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
  remote: {
    commands: { execute: fakeRemoteExecute },
  },
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
  'remote': ['@deepseek-ai/dsh-api-remotes'],
  'remote.commands': ['@deepseek-ai/dsh-api-remotes'],
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
check('确认: 远端命令为 /ctf-record-win 含转义后的 flag/name/category 参数',
  String(lastCall?.line) === expectedLine, String(lastCall?.line))

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
tailDef.inject('sess-panel') // 模拟会话尾部注入：设模块级 currentSessionId（详情拉取依赖它）
const Panel = byName('main').Component
React.render(Panel, { t })
check('面板: 挂载触发远程拉取 /ctf-dashboard', remoteCalls.some((c) => c.line === '/ctf-dashboard'))
await flushat()

/** 按标签文字点击切换（四个功能屏互斥；文字在元素树 .children 上）。 */
function clickTab(el, label) {
  const btn = findAllByClass(el, 'ctfp-tab')
    .find((b) => String(Array.isArray(b.children) ? b.children[0] : '') === label)
  if (!btn) throw new Error(`tab "${label}" not found`)
  btn.props.onClick()
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
check('面板: 功能标签栏渲染（概览/题目台账/探测器/经验卡四标签）',
  clsP.has('ctfp-tabs') && ['概览', '题目台账', '探测器', '经验卡'].every((l) => htmlLike.includes(l)))
check('面板: 默认概览屏渲染 KPI（总数 622）与分类进度（crypto 40/80）',
  clsP.has('ctfp-kpis') && htmlLike.includes('622') && htmlLike.includes('40/80'))
check('面板: KPI 新增「累计尝试」（131000 次）', htmlLike.includes('131000'))
check('面板: 概览屏渲染解题模式（auto 118）与失败画像（semantic:no-progress 128）',
  htmlLike.includes('118') && htmlLike.includes('semantic:no-progress') && htmlLike.includes('128'))
check('面板: 失败画像标题含类别计数（共 2 类）', htmlLike.includes('共 2 类'))

// —— 切到「题目台账」标签：分类下拉预选 + 计数 + 搜索 + 行 ——
clickTab(panelEl, '题目台账')
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

// —— 切到「探测器」标签 ——
clickTab(panelLedger, '探测器')
const panelDets = React.render(Panel, { t })
const detHtml = htmlOf(panelDets)
check('面板: 探测器屏渲染命中统计（34110 次尝试 / base64-chain）',
  detHtml.includes('34110') && detHtml.includes('base64-chain'))

// —— 切到「经验」标签 ——
clickTab(panelDets, '经验卡')
const panelLessons = React.render(Panel, { t })
const lesHtml = htmlOf(panelLessons)
check('面板: 经验屏渲染经验卡（技术 / 来源）',
  lesHtml.includes('base64') && lesHtml.includes('juuege/ctf-agent'))

// —— 点击台账行 → 详情页链路：客户端 → /ctf-challenge-detail --id 822 → 渲染 ——
clickTab(panelLessons, '题目台账')
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
  check('详情页: 触发远端 /ctf-challenge-detail --id 822',
    remoteCalls.some((c) => c.line === '/ctf-challenge-detail --id 822'))
}

/* ------------------------------------------------------------------ *
 * 7. 训练场（第五标签）：竞技榜自加载组件 + 一键记练
 * ------------------------------------------------------------------ */

// —— 先从详情页返回看板（整屏详情会遮住标签栏与训练场组件）——
const afterDetail = React.render(Panel, { t })
const detailEl2 = grabComponent(afterDetail, (n) => /ChallengeDetail/.test(n))
if (detailEl2 && typeof detailEl2.props.onBack === 'function') detailEl2.props.onBack()

// —— 切到「训练场」标签：ArenaPanel 自加载 /ctf-arena stats，排行榜归一化上屏 ——
clickTab(panelLessons, '训练场')
const panelArena = React.render(Panel, { t })
check('训练场: 功能标签栏含「训练场」（第 5 个）',
  findAllByClass(panelArena, 'ctfp-tab').some((b) => String(b.children[0]) === '训练场'))
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
  arenaHtml.includes('3/5') && arenaHtml.includes('60%') && arenaHtml.includes('1/2/0'))
check('训练场: 分类排行按解出数排序（web 2/3 在前）与档位列',
  arenaHtml.indexOf('web') < arenaHtml.indexOf('crypto') && arenaHtml.includes('2/3') && arenaHtml.includes('1/2'))

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
check('记练: 远端命令为 /ctf-arena record 含转义后的 name/category 与 --fast',
  String(lastRecCall?.line) === '/ctf-arena record --name mockdrill --category misc --fast',
  String(lastRecCall?.line))

const okHtml = htmlOf(arenaMount())
check('记练: 成功提示「已记入练兵台账」上屏', okHtml.includes('已记入练兵台账'))
check('记练: 记账成功后紧接着的远端调用是 stats 刷新',
  remoteCalls.length > 0 && remoteCalls[remoteCalls.length - 1].line === '/ctf-arena stats')

// —— 失败路径：引擎返回 kind:'error' → 可行动错误信息上屏 + 重试按钮 ——
arenaRecordBroken = true
await fillAndSubmit('broken-case', 'pwn')
const failHtml = htmlOf(arenaMount())
check('记练失败: 显式错误态（引擎返回信息上屏）', failHtml.includes('[error] arena record needs'))
check('记练失败: 提供「重试」按钮（不静默吞掉失败）', failHtml.includes('重试'))

// —— 运营纪律：排行榜读取失败不拦死操作——错误提示与「记练」表单仍同屏 ——
arenaRecordBroken = false
arenaStatsBroken = true
React.reset() // 清空 hooks 桶：干净挂载 ArenaPanel 走 loadBoard(true) 首次拉取
const p1 = React.render(Panel, { t })
await flushat() // dashboard 落地后标签栏才上屏（首帧为 loading）
const p2 = React.render(Panel, { t })
clickTab(p2, '训练场')
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
  clickTab(p2, '自进化')
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
check('自进化: 标签栏含「自进化」（第 6 个）',
  findAllByClass(React.render(Panel, { t }), 'ctfp-tab').some((b) => String(b.children[0]) === '自进化'))
check('自进化: 双源远端调用（/ctf-research list 与 /ctf-rsi list）',
  remoteCalls.some((c) => c.line === '/ctf-research list') && remoteCalls.some((c) => c.line === '/ctf-rsi list'))
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
check('决策: 远端命令为 /ctf-rsi approve --pid rsi-001 --note ...',
  Boolean(decCall) && String(decCall.line).startsWith('/ctf-rsi approve --pid rsi-001 --note '),
  String(decCall?.line))
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

/* ------------------------------------------------------------------ *
 * 9. 「开始解题」：新建工作区 + 新会话端到端（doStart 四处 bug 的回归门禁）
 *    覆盖：① 服务经模块级持有者（组件内引用 ctx 必 ReferenceError）；
 *          ② createDirectory 在 Host home 下建 ctf-<stamp> 新目录（create 幂等，
 *             path:'.' 会永远复用同一工作区）；③ connectWorkspace 真实签名
 *             只收 workspaceId 单参；④ 任一步失败降级为当前会话且显式提示。
 * ------------------------------------------------------------------ */

const wsCalls = { createDirectory: [], create: [], createSession: [], open: [], openSession: [] }
let sessListSnap = { ids: [], byId: {}, current: undefined, phase: 'ready' } // sessions 列表快照（零会话段重置）
const wsStub = {
  listDirectory: async () => ({ path: 'C:/Users/mock', home: 'C:/Users/mock', entries: [], crumbs: [] }),
  createDirectory: async (parent, name) => { wsCalls.createDirectory.push({ parent, name }); return `${parent}/${name}` },
  openSession: (sid) => { wsCalls.openSession.push(sid) },
  pickDirectory: async () => null,
  forkSession: async () => 'sess-fork-1',
}
const wssvcStub = {
  create: async (input) => { wsCalls.create.push(input); return { workspaceId: 'ws-777', path: input.path } },
}
let reuseList = null // workspaces 列表快照（既有工作区复用段）
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

check('开始解题: createDirectory 在 Host home 下建 ctf-<时间戳> 新目录',
  wsCalls.createDirectory.length === 1 &&
  wsCalls.createDirectory[0].parent === 'C:/Users/mock' &&
  /^ctf-\d{8}-\d{6}$/.test(wsCalls.createDirectory[0].name),
  JSON.stringify(wsCalls.createDirectory))
check('开始解题: workspaces.create 注册全新目录（path 不是 "."）',
  wsCalls.create.length === 1 &&
  String(wsCalls.create[0]?.path) === `C:/Users/mock/${wsCalls.createDirectory[0]?.name}`,
  JSON.stringify(wsCalls.create))
check('开始解题: sessions.create({workspaceId}) 建新对话（官方 New Session 同款 API，非导航方法）',
  wsCalls.createSession.length === 1 && wsCalls.createSession[0].workspaceId === 'ws-777',
  JSON.stringify(wsCalls.createSession))
check('开始解题: sessions.open 聚焦新会话 sess-new-1（openSession 兑底未被触发）',
  wsCalls.open.length === 1 && wsCalls.open[0] === 'sess-new-1' && wsCalls.openSession.length === 0,
  JSON.stringify({ open: wsCalls.open, openSession: wsCalls.openSession }))
check('开始解题: /ctf-solve-start 发往新会话 sess-new-1',
  remoteCalls.some((c) => c.sessionId === 'sess-new-1' && /^\/ctf-solve-start quick /.test(String(c.line))),
  JSON.stringify(remoteCalls.slice(-3)))
check('开始解题: 成功提示上屏', htmlOf(openSolveTab()).includes('已完成'))

// —— 层2 复用降级：listDirectory 失败 + 既有工作区存在 → 复用工作区新建对话 ——
let listDirBroken = false
wsStub.listDirectory = async () => { if (listDirBroken) throw new Error('mock-listdir-boom'); return { path: 'C:/Users/mock', home: 'C:/Users/mock', entries: [], crumbs: [] } }
wssvcStub.list = { getSnapshot: () => reuseList }
reuseList = { items: [{ workspaceId: 'ws-home', title: 'Home', path: 'C:/Users/mock' }] }
listDirBroken = true
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
check('层2复用: 目录环节失败时改在既有工作区新建对话（提示含工作区名与原因）',
  reuseHtml.includes('既有工作区') && reuseHtml.includes('Home') && reuseHtml.includes('mock-listdir-boom'),
  reuseHtml.slice(0, 400))
check('层2复用: sessions.create 指向既有工作区 ws-home（真新对话，非聚焦旧会话）',
  wsCalls.createSession.length === 1 && wsCalls.createSession[0].workspaceId === 'ws-home',
  JSON.stringify(wsCalls.createSession))
check('层2复用: /ctf-solve-start 发往复用工作区的新会话 sess-new-1',
  remoteCalls.length > 0 && remoteCalls[remoteCalls.length - 1].sessionId === 'sess-new-1' &&
    /^\/ctf-solve-start quick /.test(String(remoteCalls[remoteCalls.length - 1].line)),
  JSON.stringify(remoteCalls.slice(-2)))

// —— 层3 全失败：sessions.create 也抛错 → 降级当前会话 + 显式提示（绝不静默） ——
listDirBroken = false
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

// —— 零会话路由：currentSessionId 清空后，sessions 快照兜底路由引擎命令 ——
// （打开面板不应要求先开对话：引擎数据是全局账本，任一存在会话即可路由。）
sessListSnap = { ids: ['sess-x'], byId: {}, current: undefined, phase: 'ready' }
byName('conversation.chat.turnTail').inject('') // 清空 currentSessionId：模拟「还没打开任何对话」
await flushat() // 让 inject 唤醒的旧渲染帧 loadPanel 先 settle（防 dashboardInFlight 竞态）

React.reset()
remoteCalls.length = 0
React.render(Panel, { t }) // 新帧：routeSessionId 从 sessions 快照拿 sess-x
await flushat()
const zeroFrame0 = React.render(Panel, { t })
const zeroHtml = htmlOf(zeroFrame0)
check('零会话: currentSessionId 为空时面板仍上屏引擎数据（非「打开会话」空态）',
  zeroHtml.includes('622') && !zeroHtml.includes('打开任意会话后即可加载引擎数据'),
  zeroHtml.slice(0, 200))
// 点「刷新」强制重拉：新请求必须路由到 sessions 快照里的 sess-x
const refreshBtn = findAllByClass(zeroFrame0, 'ctfp-btn')
  .find((b) => b.type === 'button' && b.props && b.props['aria-label'] === t('refresh'))
if (!refreshBtn) throw new Error('refresh button not found (zero-session case)')
refreshBtn.props.onClick()
await flushat()
check('零会话: 强制刷新的引擎命令路由到 sessions 快照会话 sess-x',
  remoteCalls.some((c) => c.sessionId === 'sess-x' && c.line === '/ctf-dashboard'),
  JSON.stringify(remoteCalls.slice(-3)))
check('零会话: 「开始解题」表单仍可渲染（不因无会话被空态拦截）',
  (() => {
    clickTab(React.render(Panel, { t }), '开始解题')
    return Boolean(grabComponent(React.render(Panel, { t }), (n) => /SolveForm/.test(n)))
  })())

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



