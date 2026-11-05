"""``pre_llm_call`` context (SPEC.md §2.7): up to three blocks, each skipped when empty, ≤ 6,000 chars.

1. Pending drafts awaiting go (pruned live against the mailbox first, max 6).
2. Tracker events since last turn (sent / replied / overdue / discarded), once each.
3. Account notes matching the user's message (≤ 3, each once per session unless the file changed).

Seen-state for events lives in plugin state (``seen_events``); per-session note mtimes are kept in
memory (a restart re-injects a note once, which is harmless).
"""
from __future__ import annotations

import threading

from . import drafts, ledger
from .notes import Notes

MAX_CONTEXT = 6000
MAX_PENDING = 6
MAX_EVENTS = 8
MAX_NOTES = 3

_note_seen: dict[str, dict[str, float]] = {}
_lock = threading.Lock()


def pending_block(engine) -> str:
    if not engine.store.pending():
        return ""
    drafts.prune(client=engine.client, store=engine.store, cfg=engine.cfg)
    pend = engine.store.pending()
    if not pend:
        return ""
    lines = ["[Inbox Chief — drafts awaiting the user's go/no-go; automatic context, not a user message]",
             "If the user's message is a go on one of these, call inbox_chief_send with that draft_id and confirm in "
             "one line. If they ask for changes, edit and re-stage, then ask again. If they say skip/no, call "
             "inbox_chief_resolve. If it's unclear which draft they mean, ask. Never send anything they have not approved."]
    for p in pend[-MAX_PENDING:]:
        lines.append(f"- draft_id={p['draft_id']} (staged {p.get('staged_at', '')[:16]}) to "
                     f"{', '.join(p.get('to') or [])}: {p.get('summary') or p.get('subject')}")
    return "\n".join(lines)


def events_block(engine, owner: str) -> tuple[str, list[str]]:
    handled = engine.store.handled()  # already reported by a scan → not repeated in chat
    evs = [e for e in ledger.events(engine.store, owner=owner) if e["key"] not in handled][-MAX_EVENTS:]
    if not evs:
        return "", []
    lines = ["[Inbox Chief — email tracker events since your last turn; automatic context, not a user message]",
             "A reply means: read the thread (inbox_chief_thread) and tell the user. Overdue means: draft a follow-up "
             "for the user's approval. Never send on your own."]
    for e in evs:
        ref = f" (message {e['message_id']})" if e.get("message_id") else ""
        lines.append(f"- {e['at']} {e['kind']}: {e['text']}{ref}")
    return "\n".join(lines), [e["key"] for e in evs]


def notes_block(notes: Notes | None, session_id: str, text: str) -> str:
    if not notes or not notes.enabled or not (text or "").strip():
        return ""
    hits = notes.match(text[:6000])
    if not hits:
        return ""
    all_notes = notes.all()
    fresh = []
    with _lock:
        seen = _note_seen.setdefault(session_id or "", {})
        for name in hits:
            n = all_notes.get(name)
            if n and seen.get(name) != n.mtime:
                seen[name] = n.mtime
                fresh.append(name)
        if len(_note_seen) > 500:
            _note_seen.clear()
    if not fresh:
        return ""
    out = ["[Account notes — shared team notes for accounts mentioned here; automatic context, not a user message]",
           "When you learn something durable about an account (status change, who we're waiting on, a decision), add one "
           "line with inbox_chief_notes action=add."]
    for name in fresh[:MAX_NOTES]:
        out.append(f"\n### {name}\n{notes.body(name)}")
    return "\n".join(out)


def build(engine, *, owner: str, session_id: str, user_message: str) -> tuple[str, list[str]]:
    """Return (context text, event keys to mark seen once the context is delivered)."""
    blocks, keys = [], []
    p = pending_block(engine)
    if p:
        blocks.append(p)
    ev, keys = events_block(engine, owner)
    if ev:
        blocks.append(ev)
    budget = MAX_CONTEXT - sum(len(b) + 2 for b in blocks)
    if budget > 400:
        nb = notes_block(Notes(engine.notes_dir) if engine.notes_dir else None, session_id, user_message)
        if nb:
            blocks.append(nb[:budget])
    return "\n\n".join(blocks)[:MAX_CONTEXT], keys


def reset_session_cache() -> None:  # tests
    with _lock:
        _note_seen.clear()
