"""Per-profile plugin state (SPEC.md §6): handled alert keys, staged drafts, seen events, the
tracker ledger and each session's latest user message.

In Hermes this wraps ``ctx.state`` (profile-scoped, atomic, ≤10 MiB). Tests use :class:`MemoryState`.
Keys are fixed here so the engine, hooks and desktop panel agree on them.
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Protocol

K_HANDLED = "handled"        # list[str] alert keys, newest last, capped
K_PENDING = "pending"        # list[dict] staged drafts awaiting go
K_SEEN = "seen_events"       # list[str] tracker event keys already injected
K_LEDGER = "ledger"          # list[dict] tracked drafts (SPEC §2.5)
K_LAST_USER = "last_user"    # dict[session_id -> {"text", "at"}]

HANDLED_CAP = 500
SEEN_CAP = 1000
LEDGER_CAP = 400
SESSIONS_CAP = 50


class StateBackend(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


class MemoryState:
    def __init__(self, initial: dict | None = None):
        self._d: dict[str, Any] = copy.deepcopy(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self._d.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._d[key] = copy.deepcopy(value)


class Store:
    """Typed helpers over a backend. One lock per process; ``ctx.state`` is itself cross-process safe."""

    def __init__(self, backend: StateBackend):
        self.b = backend
        self._lock = threading.Lock()

    # handled alert keys
    def handled(self) -> set[str]:
        return set(self.b.get(K_HANDLED, []) or [])

    def mark_handled(self, keys: list[str]) -> None:
        with self._lock:
            cur = list(self.b.get(K_HANDLED, []) or [])
            cur += [k for k in keys if k not in cur]
            self.b.set(K_HANDLED, cur[-HANDLED_CAP:])

    # pending (staged) drafts
    def pending(self) -> list[dict]:
        return list(self.b.get(K_PENDING, []) or [])

    def stage(self, entry: dict) -> None:
        with self._lock:
            cur = [p for p in self.b.get(K_PENDING, []) or [] if p.get("draft_id") != entry["draft_id"]]
            cur.append(entry)
            self.b.set(K_PENDING, cur)

    def unstage(self, draft_id: str) -> dict | None:
        with self._lock:
            cur = list(self.b.get(K_PENDING, []) or [])
            hit = next((p for p in cur if p.get("draft_id") == draft_id), None)
            self.b.set(K_PENDING, [p for p in cur if p.get("draft_id") != draft_id])
            return hit

    def replace_pending(self, entries: list[dict]) -> None:
        with self._lock:
            self.b.set(K_PENDING, entries)

    # tracker ledger
    def ledger(self) -> list[dict]:
        return list(self.b.get(K_LEDGER, []) or [])

    def save_ledger(self, items: list[dict]) -> None:
        with self._lock:
            open_ = [i for i in items if i.get("status") in ("draft", "sent")]
            closed = [i for i in items if i.get("status") not in ("draft", "sent")]
            self.b.set(K_LEDGER, (closed + open_)[-LEDGER_CAP:] if len(items) > LEDGER_CAP else items)

    # seen tracker events
    def seen(self) -> set[str]:
        return set(self.b.get(K_SEEN, []) or [])

    def mark_seen(self, keys: list[str]) -> None:
        with self._lock:
            cur = list(self.b.get(K_SEEN, []) or [])
            cur += [k for k in keys if k not in cur]
            self.b.set(K_SEEN, cur[-SEEN_CAP:])

    # latest user message per session (explicit_go mode)
    def record_user_message(self, session_id: str, text: str, at: str) -> None:
        if not session_id:
            return
        with self._lock:
            cur = dict(self.b.get(K_LAST_USER, {}) or {})
            cur[session_id] = {"text": (text or "")[:500], "at": at}
            if len(cur) > SESSIONS_CAP:
                for sid in sorted(cur, key=lambda s: cur[s].get("at", ""))[:-SESSIONS_CAP]:
                    cur.pop(sid, None)
            self.b.set(K_LAST_USER, cur)

    def last_user_message(self, session_id: str) -> str:
        return ((self.b.get(K_LAST_USER, {}) or {}).get(session_id) or {}).get("text", "")
