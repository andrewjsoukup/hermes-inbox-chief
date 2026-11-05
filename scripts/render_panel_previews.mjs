// Render desktop/plugin.js (real code) against the stub SDK + fixture data into static HTML pages
// for README screenshots: node scripts/render_panel_previews.mjs <queue|confirm|preview|notes>
// Writes <mode>.html to $OUT (default: system temp); screenshot it with any headless browser.
// Derived from tests/desktop/render_harness.mjs.
// Dev-only (not loaded by Hermes). The generated HTML pulls the Tailwind browser build from
// cdn.jsdelivr.net when opened, purely to style the preview; nothing here runs inside the plugin.
// React is borrowed from a Hermes checkout (it is not a plugin dependency): set
// INBOX_CHIEF_NODE_BASE to a folder whose node_modules has react, react-dom and jsdom.
import { createRequire } from 'node:module'
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

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
const click = async el => { await act(async () => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))) }
const mode = process.argv[2] || 'queue'
if (mode === 'confirm') await click([...container.querySelectorAll('button')].find(b => b.textContent.trim() === 'Go'))
if (mode === 'preview') await click(container.querySelector('button[aria-expanded]'))
if (mode === 'notes') { await click([...container.querySelectorAll('button')].find(b => b.textContent.includes('Lakeshore Creamery'))) }
const body = container.innerHTML
const page = `<!doctype html><html class="dark"><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<style>:root{--ui-text-primary:#e6e6e6;--ui-text-secondary:#c2c2c2;--ui-text-tertiary:#9a9a9a;--ui-text-quaternary:#777;--ui-stroke-secondary:#3a3a3a;--ui-bg:#1e1e1e}
body{background:#1e1e1e;color:#e6e6e6;font:13px/1.4 "Segoe UI",system-ui,sans-serif;margin:0}
#w{width:380px;border-left:1px solid #333;min-height:100vh}
button{margin:0 4px 0 0;background:#2d2d2d;color:#e6e6e6;border:1px solid #444;border-radius:4px;padding:2px 8px;font-size:12px;cursor:pointer}
[data-badge]{background:#333;border-radius:4px;padding:0 5px;font-size:11px}
[role=dialog]{position:fixed;left:16px;top:150px;width:316px;outline:9999px solid rgba(0,0,0,.55);background:#252526;border:1px solid #555;border-radius:6px;padding:12px;box-shadow:0 8px 30px #000}
i[data-icon]::before{content:"✉";font-style:normal;margin-right:4px}</style></head><body><div id="w">${body}</div></body></html>`
writeFileSync(join(process.env.OUT || tmpdir(), mode + '.html'), page)
await act(async () => root.unmount()); console.log('wrote', mode); process.exit(0)
