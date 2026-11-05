// Inbox Chief — Desktop panel (Hermes Desktop Plugin SDK, plain ESM, no build step).
//
// Shows each lane's queue (items the scan found), the drafts staged for your go / no-go, and the
// account notes that match each item. Data comes from this package's own backend
// (dashboard/plugin_api.py → /api/plugins/inbox-chief/) through ctx.rest.
//
// Safety: the panel can NOT send email. "Go" asks you to confirm, then posts `go <id>` into the
// chat you are looking at, so the send runs through the inbox_chief_send tool and every gate
// behind it (staged + unchanged draft, no unattended sends, pricing check, Hermes approval prompt).
// "Change" puts `change <id>: ` in the composer for you to finish. "Skip" removes the draft from
// the go / no-go list; the draft itself stays in Outlook.

import {
  Badge,
  Button,
  Codicon,
  ConfirmDialog,
  EmptyState,
  Input,
  PALETTE_AREA,
  PANES_AREA,
  STATUSBAR_AREAS,
  SegmentedControl,
  cn,
  host,
  queryClient,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'inbox-chief'
const PANE_ID = `${PLUGIN_ID}:pane`
const QK = {
  queue: [PLUGIN_ID, 'queue'],
  summary: [PLUGIN_ID, 'summary'],
  draft: id => [PLUGIN_ID, 'draft', id],
  note: name => [PLUGIN_ID, 'note', name],
  notes: q => [PLUGIN_ID, 'notes', q]
}
const QUEUE_POLL_MS = 120000 // the queue scans the mailbox: keep it slow, refresh by hand
const SUMMARY_POLL_MS = 60000

const KIND_LABEL = {
  new: 'new',
  customer_reply: 'customer reply',
  supplier_reply: 'supplier reply',
  replies_owed: 'reply owed',
  overdue: 'overdue',
  replied: 'replied'
}
const KIND_VARIANT = {
  customer_reply: 'warn',
  supplier_reply: 'success',
  replies_owed: 'warn',
  overdue: 'destructive',
  replied: 'success'
}

// ── helpers ──────────────────────────────────────────────────────────────────────────────────────

function text(value) {
  return value == null ? '' : String(value)
}

function joinTo(list) {
  return Array.isArray(list) ? list.join(', ') : text(list)
}

function backendHint(error) {
  const msg = text(error && (error.message || error))
  if (/404|not found/i.test(msg)) {
    return 'The Inbox Chief backend is not loaded. Add inbox-chief to plugins.enabled in config.yaml and restart the gateway.'
  }
  return msg || 'Inbox Chief is unavailable.'
}

function duplicateShortIds(lanes) {
  const seen = new Map()
  for (const lane of lanes || []) {
    for (const d of lane.drafts || []) seen.set(d.short_id, (seen.get(d.short_id) || 0) + 1)
  }
  return new Set([...seen].filter(([, n]) => n > 1).map(([id]) => id))
}

function focusedSession() {
  return host.state.focusedSessionId.get()
}

function submitToChat(message) {
  const sid = focusedSession()
  let ok = host.composer.submit(sid ?? null, message)
  if (!ok && sid == null) ok = host.composer.submit('new', message)
  return ok
}

function refreshAll() {
  queryClient.invalidateQueries({ queryKey: [PLUGIN_ID] })
}

// ── small building blocks ────────────────────────────────────────────────────────────────────────

function Muted({ children, className }) {
  return jsx('div', { className: cn('text-[0.6875rem] text-(--ui-text-tertiary)', className), children })
}

function SectionTitle({ children, count }) {
  return jsxs('div', {
    className: 'mt-2 flex items-center gap-1.5 text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)',
    children: [jsx('span', { children }), count ? jsx(Badge, { size: 'xs', variant: 'muted', children: String(count) }) : null]
  })
}

function NoteBody({ api, name }) {
  const q = useQuery({ queryKey: QK.note(name), queryFn: () => api.note(name), staleTime: 60000 })
  if (q.isLoading) return jsx(Muted, { children: 'Loading note…' })
  if (q.error || !q.data?.ok) return jsx(Muted, { children: q.data?.error || backendHint(q.error) })
  return jsx('pre', {
    className:
      'mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-[4px] border border-(--ui-stroke-secondary) p-2 text-[0.6875rem] text-(--ui-text-secondary)',
    children: text(q.data.body) || '(empty note)'
  })
}

function NoteChips({ api, names }) {
  const [open, setOpen] = useState(null)
  if (!names || !names.length) return null
  return jsxs('div', {
    className: 'mt-1',
    children: [
      jsx('div', {
        className: 'flex flex-wrap gap-1',
        children: names.map(name =>
          jsxs(
            Button,
            {
              size: 'xs',
              variant: open === name ? 'secondary' : 'ghost',
              title: 'Account note',
              onClick: () => setOpen(open === name ? null : name),
              children: [jsx(Codicon, { name: 'note' }), name]
            },
            name
          )
        )
      }),
      open ? jsx(NoteBody, { api, name: open }) : null
    ]
  })
}

// ── queue item ───────────────────────────────────────────────────────────────────────────────────

function ItemRow({ api, item }) {
  const who = item.sender_name || item.sender || 'unknown sender'
  return jsxs('div', {
    className: 'rounded-[4px] border border-(--ui-stroke-secondary) p-2',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          jsx(Badge, { size: 'xs', variant: KIND_VARIANT[item.kind] || 'outline', children: KIND_LABEL[item.kind] || text(item.kind) }),
          jsx('span', { className: 'truncate text-xs font-medium', title: item.sender, children: who }),
          jsx('span', { className: 'ml-auto shrink-0 text-[0.6875rem] text-(--ui-text-quaternary)', children: text(item.received).replace('T', ' ') })
        ]
      }),
      jsx('div', { className: 'mt-1 truncate text-xs', title: item.subject, children: item.subject || '(no subject)' }),
      item.detail ? jsx(Muted, { className: 'mt-0.5', children: item.detail }) : null,
      !item.detail && item.preview ? jsx(Muted, { className: 'mt-0.5 line-clamp-2', children: item.preview }) : null,
      item.attachments && item.attachments.length
        ? jsxs(Muted, { className: 'mt-0.5 flex items-center gap-1', children: [jsx(Codicon, { name: 'paperclip' }), item.attachments.join(', ')] })
        : null,
      jsx(NoteChips, { api, names: item.notes })
    ]
  })
}

// ── staged draft with Go / Change / Skip ─────────────────────────────────────────────────────────

function DraftPreview({ api, draftId }) {
  const q = useQuery({ queryKey: QK.draft(draftId), queryFn: () => api.draft(draftId), staleTime: 15000 })
  if (q.isLoading) return jsx(Muted, { className: 'mt-1', children: 'Loading draft…' })
  if (q.error || !q.data?.ok) return jsx(Muted, { className: 'mt-1', children: q.data?.error || backendHint(q.error) })
  return jsx('pre', {
    className:
      'mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-[4px] border border-(--ui-stroke-secondary) p-2 text-[0.6875rem] text-(--ui-text-secondary)',
    children: text(q.data.body) || '(empty draft)'
  })
}

function DraftRow({ api, draft, ambiguous, busy }) {
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [skipping, setSkipping] = useState(false)

  const go = () => {
    const ok = submitToChat(draft.go_message)
    if (ok) {
      host.notify({ kind: 'info', message: `Sent "${draft.go_message}" to your chat. Hermes will confirm before the email goes out.` })
    } else {
      host.notify({ kind: 'warning', message: 'Open a chat first: Go is approved in the conversation, not in this panel.' })
    }
  }

  const change = async () => {
    const sid = focusedSession()
    const ok = await host.composer.insertText(sid ?? null, draft.change_message, { mode: 'block' })
    if (ok) host.composer.focus(sid ?? null)
    else host.notify({ kind: 'warning', message: 'Open a chat first, then say what to change.' })
  }

  const skip = async () => {
    setSkipping(true)
    try {
      const r = await api.skip(draft.draft_id)
      if (r && r.ok) host.notify({ kind: 'info', message: 'Skipped. The draft is still in your Outlook Drafts folder.' })
      else host.notify({ kind: 'warning', message: (r && r.error) || 'Could not skip that draft.' })
    } catch (err) {
      host.notifyError(err, 'Could not skip that draft.')
    } finally {
      setSkipping(false)
      refreshAll()
    }
  }

  const goDisabled = ambiguous || busy
  const goTitle = ambiguous
    ? 'Two drafts share this short id. Approve this one in chat by naming its subject.'
    : busy
      ? 'Wait for the current reply to finish.'
      : `Confirm, then post "${draft.go_message}" to your chat`

  return jsxs('div', {
    className: 'rounded-[4px] border border-(--ui-stroke-secondary) p-2',
    children: [
      jsxs('button', {
        type: 'button',
        className: 'flex w-full items-start gap-1.5 text-left',
        onClick: () => setExpanded(!expanded),
        'aria-expanded': expanded,
        children: [
          jsx(Codicon, { name: expanded ? 'chevron-down' : 'chevron-right', className: 'mt-0.5 text-(--ui-text-tertiary)' }),
          jsxs('div', {
            className: 'min-w-0 flex-1',
            children: [
              jsx('div', { className: 'truncate text-xs font-medium', title: draft.subject, children: draft.summary || draft.subject || '(no subject)' }),
              jsxs(Muted, {
                className: 'truncate',
                children: [
                  draft.kind === 'reply_all' ? 'Reply all → ' : draft.kind === 'reply' ? 'Reply → ' : 'To ',
                  joinTo(draft.to) || '(no recipients)'
                ]
              })
            ]
          }),
          jsx(Badge, { size: 'xs', variant: 'outline', title: 'Short id used in chat', children: draft.short_id })
        ]
      }),
      draft.attachments && draft.attachments.length
        ? jsxs(Muted, { className: 'mt-1 flex items-center gap-1', children: [jsx(Codicon, { name: 'paperclip' }), draft.attachments.join(', ')] })
        : null,
      expanded ? jsx(DraftPreview, { api, draftId: draft.draft_id }) : null,
      jsxs('div', {
        className: 'mt-2 flex gap-1.5',
        children: [
          jsx(Button, { size: 'xs', disabled: goDisabled, title: goTitle, onClick: () => setConfirming(true), children: 'Go' }),
          jsx(Button, { size: 'xs', variant: 'secondary', disabled: busy, title: 'Tell Hermes what to change', onClick: change, children: 'Change' }),
          jsx(Button, {
            size: 'xs',
            variant: 'ghost',
            disabled: skipping,
            title: 'Take it off the go / no-go list (stays in Outlook Drafts)',
            onClick: skip,
            children: skipping ? 'Skipping…' : 'Skip'
          })
        ]
      }),
      jsx(ConfirmDialog, {
        open: confirming,
        onClose: () => setConfirming(false),
        onConfirm: go,
        title: 'Approve this email?',
        description: `"${draft.subject || draft.summary || ''}" to ${joinTo(draft.to)}. This posts "${draft.go_message}" in your chat; Hermes re-checks the draft and asks once more before sending.`,
        confirmLabel: 'Go'
      })
    ]
  })
}

// ── lanes ────────────────────────────────────────────────────────────────────────────────────────

function LaneCard({ api, lane, ambiguous, busy }) {
  const drafts = lane.drafts || []
  const items = lane.items || []
  if (!drafts.length && !items.length) return null
  return jsxs('section', {
    className: 'flex flex-col gap-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5 border-b border-(--ui-stroke-secondary) pb-1',
        children: [
          jsx('span', { className: 'text-xs font-semibold', children: lane.label || lane.id }),
          lane.owner ? jsx(Muted, { children: `→ ${lane.owner}` }) : null
        ]
      }),
      drafts.length ? jsx(SectionTitle, { count: drafts.length, children: 'Awaiting your go' }) : null,
      ...drafts.map(d => jsx(DraftRow, { api, draft: d, ambiguous: ambiguous.has(d.short_id), busy }, d.draft_id)),
      items.length ? jsx(SectionTitle, { count: items.length, children: 'Queue' }) : null,
      ...items.map(i => jsx(ItemRow, { api, item: i }, i.key || i.message_id))
    ]
  })
}

function QueueView({ api, enabled }) {
  const sessionId = useValue(host.state.focusedSessionId)
  const busyBySession = useValue(host.state.busyBySession)
  const busy = Boolean(sessionId && busyBySession && busyBySession[sessionId])
  const q = useQuery({ queryKey: QK.queue, queryFn: () => api.queue(), refetchInterval: QUEUE_POLL_MS, enabled })

  if (q.isLoading) return jsx(Muted, { className: 'p-3', children: 'Checking your inbox…' })
  if (q.error) return jsx(EmptyState, { title: 'Inbox Chief is unavailable', description: backendHint(q.error) })
  const data = q.data || {}
  if (!data.ok) {
    const title = data.sign_in_needed ? 'Sign in to Microsoft' : data.setup_needed ? 'Finish setup' : 'Inbox Chief is not ready'
    return jsx(EmptyState, { title, description: text(data.error) })
  }
  const lanes = data.lanes || []
  const ambiguous = duplicateShortIds(lanes)
  const anything = lanes.some(l => (l.drafts || []).length || (l.items || []).length)

  return jsxs('div', {
    className: 'flex flex-col gap-3',
    children: [
      jsxs(Muted, {
        className: 'flex items-center gap-1.5',
        children: [
          jsx('span', { children: `${data.pending_count || 0} awaiting go · ${data.item_count || 0} in queue` }),
          data.mailbox === 'fixture' ? jsx(Badge, { size: 'xs', variant: 'muted', children: 'demo mailbox' }) : null,
          q.isFetching ? jsx('span', { children: '· refreshing…' }) : null
        ]
      }),
      ...(data.warnings || []).map((w, i) => jsx(Muted, { children: w }, `w${i}`)),
      anything
        ? lanes.map(l => jsx(LaneCard, { api, lane: l, ambiguous, busy }, l.id))
        : jsx(EmptyState, { title: 'Inbox is clear', description: 'Nothing new in your lanes and no drafts waiting.' })
    ]
  })
}

// ── notes ────────────────────────────────────────────────────────────────────────────────────────

function NotesView({ api }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(null)
  const q = useQuery({ queryKey: QK.notes(query), queryFn: () => api.notes(query), staleTime: 30000 })
  const data = q.data || {}
  return jsxs('div', {
    className: 'flex flex-col gap-2',
    children: [
      jsx(Input, {
        value: query,
        placeholder: 'Find an account note (name, contact, domain)…',
        onChange: e => setQuery(e.target.value),
        'aria-label': 'Search account notes'
      }),
      q.isLoading
        ? jsx(Muted, { children: 'Loading notes…' })
        : q.error
          ? jsx(Muted, { children: backendHint(q.error) })
          : !data.ok
            ? jsx(EmptyState, { title: 'Account notes are off', description: text(data.error) })
            : !(data.names || []).length
              ? jsx(Muted, { children: query ? 'No matching notes.' : 'No notes yet.' })
              : jsx('div', {
                  className: 'flex flex-col gap-1',
                  children: data.names.map(name =>
                    jsxs(
                      'div',
                      {
                        children: [
                          jsxs(Button, {
                            size: 'xs',
                            variant: open === name ? 'secondary' : 'ghost',
                            className: 'w-full justify-start',
                            onClick: () => setOpen(open === name ? null : name),
                            children: [jsx(Codicon, { name: 'note' }), name]
                          }),
                          open === name ? jsx(NoteBody, { api, name }) : null
                        ]
                      },
                      name
                    )
                  )
                })
    ]
  })
}

// ── pane + status chip ───────────────────────────────────────────────────────────────────────────

function usePaneVisible() {
  const atom = typeof host.paneVisibility === 'function' ? host.paneVisibility(PANE_ID) : null
  return atom ? Boolean(useValue(atom)) : true
}

function InboxPane({ api, prefs }) {
  const [tab, setTab] = useState(prefs.get('tab', 'queue'))
  const visible = usePaneVisible()
  const pick = id => {
    setTab(id)
    prefs.set('tab', id)
  }
  return jsxs('div', {
    className: 'flex h-full flex-col text-sm',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx(SegmentedControl, {
            value: tab,
            onChange: pick,
            options: [
              { id: 'queue', label: 'Queue' },
              { id: 'notes', label: 'Notes' }
            ]
          }),
          jsx(Button, {
            size: 'icon-xs',
            variant: 'ghost',
            className: 'ml-auto',
            title: 'Refresh (re-checks the mailbox)',
            'aria-label': 'Refresh',
            onClick: refreshAll,
            children: jsx(Codicon, { name: 'refresh' })
          })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-auto p-3',
        children: tab === 'notes' ? jsx(NotesView, { api }) : jsx(QueueView, { api, enabled: visible })
      })
    ]
  })
}

function StatusChip({ api }) {
  const q = useQuery({ queryKey: QK.summary, queryFn: () => api.summary(), refetchInterval: SUMMARY_POLL_MS })
  const d = q.data
  if (!d || !d.ok) return null
  const n = d.pending_count || 0
  return jsxs('button', {
    type: 'button',
    className: cn('inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem]', n ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-tertiary)'),
    title: n ? `${n} email draft${n === 1 ? '' : 's'} awaiting your go (Inbox Chief panel)` : 'Inbox Chief: no drafts waiting',
    onClick: refreshAll,
    children: [jsx(Codicon, { name: 'mail' }), n ? jsx('span', { children: String(n) }) : null]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Inbox Chief',
  register(ctx) {
    const post = (path, body) => ctx.rest(path, { method: 'POST', body: body || {} })
    const api = {
      queue: () => ctx.rest('/queue?scan=1'),
      summary: () => ctx.rest('/summary'),
      draft: draftId => post('/draft', { draft_id: draftId }),
      skip: draftId => post('/skip', { draft_id: draftId, outcome: 'skipped' }),
      note: name => post('/note', { name }),
      notes: q => post('/notes', { q })
    }
    const prefs = { get: (k, d) => ctx.storage.get(k, d), set: (k, v) => ctx.storage.set(k, v) }

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Inbox',
        data: { placement: 'right', width: '360px' },
        render: () => jsx(InboxPane, { api, prefs })
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 125,
        render: () => jsx(StatusChip, { api })
      },
      {
        id: 'refresh',
        area: PALETTE_AREA,
        data: {
          id: 'inbox-chief.refresh',
          label: 'Inbox Chief: Refresh queue',
          keywords: ['inbox', 'email', 'outlook', 'drafts'],
          run: refreshAll
        }
      }
    ])

    // A scheduled scan or a send in chat changes the queue: refresh when an Inbox Chief tool finishes.
    ctx.onEvent('tool.complete', event => {
      const name = text(event && event.payload && event.payload.name)
      if (name.startsWith('inbox_chief_')) refreshAll()
    })
  }
}
