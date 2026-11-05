// Headless check of desktop/plugin.js: load it the way the Desktop loader does (only
// '@hermes/plugin-sdk', 'react', 'react/jsx-runtime' resolvable), register it against a fake ctx,
// mount the pane in jsdom with a stub SDK, and click Go / Change / Skip.
//
// Exit 0 = pass ("RENDER OK"), 1 = failure, 3 = skipped (react / react-dom / jsdom not found).
// React is borrowed from a Hermes checkout (it is not a plugin dependency): set
// INBOX_CHIEF_NODE_BASE to a folder whose node_modules has react, react-dom and jsdom.
import { createRequire } from 'node:module'
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')

const bases = [
  process.env.INBOX_CHIEF_NODE_BASE,
  join(homedir(), 'AppData', 'Local', 'hermes', 'hermes-agent'),
  join(homedir(), '.hermes', 'hermes-agent'),
  ROOT
].filter(Boolean)

let req = null
for (const base of bases) {
  if (!existsSync(join(base, 'node_modules', 'react'))) continue
  const r = createRequire(join(base, 'package.json'))
  try {
    r.resolve('react')
    r.resolve('react-dom/client')
    r.resolve('jsdom')
    req = r
    break
  } catch {}
}
if (!req) {
  console.log('SKIP: react, react-dom and jsdom not found (set INBOX_CHIEF_NODE_BASE)')
  process.exit(3)
}

const { JSDOM } = req('jsdom')
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true })
for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event', 'MouseEvent', 'getComputedStyle']) {
  if (!(k in globalThis) || k === 'window' || k === 'document') {
    Object.defineProperty(globalThis, k, { value: dom.window[k], configurable: true, writable: true })
  }
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const reactUrl = pathToFileURL(req.resolve('react')).href
const jsxUrl = pathToFileURL(req.resolve('react/jsx-runtime')).href
const React = (await import(reactUrl)).default
const { createRoot } = req('react-dom/client')
const { act } = React

// ── fixture data the stub backend returns ─────────────────────────────────────────────────────────
const DRAFT = {
  draft_id: 'AAMkAD-draft-000abc123',
  short_id: 'abc123',
  go_message: 'go abc123',
  change_message: 'change abc123: ',
  subject: 'RE: KP-450 cut sheet',
  summary: 'Send KP-450 cut sheet to Lakeshore',
  to: ['buyer@lakeshore-creamery.example'],
  kind: 'reply_all',
  lane: 'customers',
  attachments: ['KP-450-cut-sheet.pdf']
}
const QUEUE = {
  ok: true,
  mailbox: 'fixture',
  pending_count: 1,
  item_count: 1,
  warnings: [],
  lanes: [
    {
      id: 'customers',
      label: 'Customer inquiries',
      owner: 'self',
      drafts: [DRAFT],
      items: [
        {
          key: 'k1',
          kind: 'supplier_reply',
          lane: 'customers',
          message_id: 'm003',
          sender: 'rep@granite-fittings.example',
          sender_name: 'Granite Rep',
          subject: 'KP-450 cut sheet',
          received: '2026-11-10T15:00',
          preview: 'Attached is the cut sheet you asked for.',
          attachments: ['KP-450-cut-sheet.pdf'],
          notes: ['Lakeshore Creamery']
        }
      ]
    },
    { id: 'empty', label: 'Empty lane', owner: 'self', drafts: [], items: [] }
  ]
}
const RESPONSES = {
  queue: QUEUE,
  summary: { ok: true, pending_count: 1, mailbox: 'fixture', lanes: 2 },
  draft: { ok: true, ...DRAFT, body: 'Hi, cut sheet attached.' },
  note: { ok: true, name: 'Lakeshore Creamery', body: '## Summary\nDemo account.' },
  notes: { ok: true, names: ['Lakeshore Creamery', 'Granite Fittings'] }
}

// ── stub SDK ──────────────────────────────────────────────────────────────────────────────────────
const calls = { submit: [], insert: [], focus: 0, notify: [], rest: [], invalidate: 0 }
globalThis.__IC_TEST__ = { React, calls, RESPONSES }

const sdkSource = `
const { React, calls, RESPONSES } = globalThis.__IC_TEST__
const h = React.createElement
const atom = v => ({ get: () => v })
export const PANES_AREA = 'panes'
export const PALETTE_AREA = 'palette'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const cn = (...a) => a.filter(Boolean).join(' ')
export const useValue = a => a.get()
export const queryClient = { invalidateQueries: () => { calls.invalidate++ } }
export function useQuery({ queryKey }) {
  const kind = queryKey[1]
  return { data: RESPONSES[kind], isLoading: false, isFetching: false, error: null }
}
export const host = {
  state: { focusedSessionId: atom('sess-1'), busyBySession: atom({}) },
  composer: {
    submit: (sid, text) => { calls.submit.push([sid, text]); return true },
    insertText: async (sid, text, opts) => { calls.insert.push([sid, text, opts]); return true },
    focus: () => { calls.focus++ }
  },
  notify: n => { calls.notify.push(n) },
  notifyError: (e, m) => { calls.notify.push({ kind: 'error', message: m }) },
  paneVisibility: () => atom(true)
}
const pass = tag => ({ children, onClick, disabled, title, ...rest }) =>
  h(tag, { onClick, disabled, title, 'data-stub': rest['aria-label'] || undefined }, children)
export const Button = pass('button')
export const Badge = ({ children }) => h('span', { 'data-badge': '1' }, children)
export const Codicon = ({ name }) => h('i', { 'data-icon': name })
export const EmptyState = ({ title, description }) => h('div', { 'data-empty': '1' }, title, ' ', description)
export const Input = props => h('input', { value: props.value, onChange: props.onChange, placeholder: props.placeholder })
export const SegmentedControl = ({ options, value, onChange }) =>
  h('div', null, options.map(o => h('button', { key: o.id, 'data-seg': o.id, onClick: () => onChange(o.id) }, o.label)))
export const ConfirmDialog = ({ open, onConfirm, onClose, title, description, confirmLabel }) =>
  open ? h('div', { role: 'dialog' }, h('p', null, title), h('p', { 'data-desc': '1' }, description),
         h('button', { 'data-confirm': '1', onClick: async () => { await onConfirm(); onClose() } }, confirmLabel)) : null
`

const work = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'inbox-chief-harness-'))
const sdkFile = join(work, 'sdk.mjs')
writeFileSync(sdkFile, sdkSource)
const src = readFileSync(join(ROOT, 'desktop', 'plugin.js'), 'utf8')
const rewritten = src.replace(/from\s+(['"])([^'"]+)\1/g, (m, q, spec) => {
  if (spec === '@hermes/plugin-sdk') return `from '${pathToFileURL(sdkFile).href}'`
  if (spec === 'react') return `from '${reactUrl}'`
  if (spec === 'react/jsx-runtime') return `from '${jsxUrl}'`
  throw new Error(`plugin.js imports a specifier the Desktop loader refuses: ${spec}`)
})
const pluginFile = join(work, 'plugin.mjs')
writeFileSync(pluginFile, rewritten)

const fail = msg => {
  console.log('FAIL: ' + msg)
  process.exit(1)
}
const assert = (cond, msg) => cond || fail(msg)

const plugin = (await import(pathToFileURL(pluginFile).href)).default
assert(plugin && plugin.id === 'inbox-chief', 'default export id must be inbox-chief')

const contributions = []
const events = {}
const storage = new Map()
const ctx = {
  source: 'plugin:inbox-chief',
  register: c => (contributions.push(c), () => {}),
  registerMany: cs => (contributions.push(...cs), () => {}),
  rest: async (path, opts) => {
    calls.rest.push([path, opts])
    if (path === '/skip') return { ok: true, outcome: 'skipped' }
    return RESPONSES[path.replace(/^\//, '').split('?')[0]] || { ok: false }
  },
  onEvent: (type, fn) => ((events[type] = fn), () => {}),
  onDispose: () => {},
  storage: { get: (k, d) => (storage.has(k) ? storage.get(k) : d), set: (k, v) => storage.set(k, v), remove: k => storage.delete(k) }
}
plugin.register(ctx)

const areas = contributions.map(c => c.area).sort()
assert(JSON.stringify(areas) === JSON.stringify(['palette', 'panes', 'statusBar.right']), 'contributions: ' + areas)
const pane = contributions.find(c => c.area === 'panes')
const chip = contributions.find(c => c.area === 'statusBar.right')

const container = document.getElementById('root')
const root = createRoot(container)
await act(async () => root.render(React.createElement('div', null, pane.render(), chip.render())))

const html = container.innerHTML
assert(html.includes('Customer inquiries'), 'lane label rendered')
assert(!html.includes('Empty lane'), 'empty lanes are hidden')
assert(html.includes('Awaiting your go') && html.includes('Send KP-450 cut sheet to Lakeshore'), 'draft rendered')
assert(html.includes('Reply all → buyer@lakeshore-creamery.example'), 'reply-all recipients rendered')
assert(html.includes('supplier reply') && html.includes('Granite Rep'), 'queue item rendered')
assert(html.includes('Lakeshore Creamery'), 'note chip rendered')
assert(html.includes('demo mailbox'), 'fixture badge rendered')
assert(html.includes('data-icon="mail"'), 'status chip rendered')

const byText = (label, sel = 'button') => [...container.querySelectorAll(sel)].find(b => b.textContent.trim() === label)
const click = async el => {
  assert(el, 'element to click exists')
  await act(async () => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })))
}

// Go: opens a confirmation first, nothing submitted yet.
await click(byText('Go'))
assert(calls.submit.length === 0, 'Go must not submit before confirmation')
const dialog = container.querySelector('[role="dialog"]')
assert(dialog && dialog.textContent.includes('buyer@lakeshore-creamery.example'), 'confirmation names the recipients')
await click(container.querySelector('[data-confirm]'))
assert(calls.submit.length === 1, 'confirming Go submits once')
assert(calls.submit[0][0] === 'sess-1' && calls.submit[0][1] === 'go abc123', 'Go posts "go <short id>" to the focused chat: ' + JSON.stringify(calls.submit))
assert(!calls.rest.some(([p]) => /send/i.test(p)), 'panel never calls a send route')

// Change: seeds the composer, does not submit.
await click(byText('Change'))
assert(calls.insert.length === 1 && calls.insert[0][1] === 'change abc123: ', 'Change seeds the composer')
assert(calls.submit.length === 1, 'Change does not submit')

// Skip: POST /skip with the draft id, never a send.
await click(byText('Skip'))
const skip = calls.rest.find(([p]) => p === '/skip')
assert(skip && skip[1].method === 'POST' && skip[1].body.draft_id === DRAFT.draft_id, 'Skip posts /skip')

// Expanding a draft loads its preview.
await click(container.querySelector('button[aria-expanded]'))
assert(container.innerHTML.includes('Hi, cut sheet attached.'), 'draft preview rendered')

// Note chip opens the note body.
await click([...container.querySelectorAll('button')].find(b => b.textContent.includes('Lakeshore Creamery')))
assert(container.innerHTML.includes('Demo account.'), 'note body rendered')

// Notes tab lists notes.
await click(container.querySelector('[data-seg="notes"]'))
assert(container.innerHTML.includes('Granite Fittings'), 'notes tab lists notes')
assert(storage.get('tab') === 'notes', 'tab choice persisted in ctx.storage')

// A finished Inbox Chief tool refreshes the panel; other tools don't.
const before = calls.invalidate
events['tool.complete']({ payload: { name: 'terminal' } })
events['tool.complete']({ payload: { name: 'inbox_chief_send' } })
assert(calls.invalidate === before + 1, 'refresh only on inbox_chief_* tools')

await act(async () => root.unmount())
console.log('RENDER OK')
process.exit(0)
