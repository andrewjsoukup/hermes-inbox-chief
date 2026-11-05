"""Hook callbacks.

* ``pre_tool_call``: the hard send gate. Unattended → block. ``approval_gate`` mode → escalate the
  send to Hermes's human-approval prompt (fails closed when no human is present).
* ``pre_llm_call``: records the user's latest message per session (``explicit_go`` mode) and injects
  the pending-drafts block. Tracker events and account notes join this hook in the engine card.

Both swallow their own errors except the send gate, which fails CLOSED (blocks) when it cannot decide.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from . import drafts, safety
from .engine import EngineHolder, NotConfigured

log = logging.getLogger("inbox_chief.hooks")
SEND_TOOL = "inbox_chief_send"
MAX_CONTEXT = 6000


class Hooks:
    def __init__(self, holder: EngineHolder, cron_execution: Callable[[], object] = lambda: None):
        self.holder = holder
        self.cron_execution = cron_execution

    def pre_tool_call(self, tool_name: str = "", args: dict | None = None, **kw):
        if tool_name != SEND_TOOL:
            return None
        try:
            if safety.is_unattended(platform=kw.get("platform", ""), cron_execution=self.cron_execution(),
                                    parent_session_id=kw.get("parent_session_id", "")):
                return {"action": "block", "message": "Inbox Chief never sends from an unattended run. "
                                                      "Stage the draft and ask the user for a go."}
            e = self.holder.get()
            draft_id = str((args or {}).get("draft_id") or "")
            staged = next((p for p in e.store.pending() if p.get("draft_id") == draft_id), None)
            if not staged:
                return {"action": "block", "message": "That draft is not staged by Inbox Chief; stage it and "
                                                      "get the user's go first."}
            if e.settings.send_confirmation == "approval_gate":
                to = ", ".join(staged.get("to") or [])
                return {"action": "approve",
                        "message": f"Send email \"{staged.get('subject', '')}\" to {to}",
                        "rule_key": f"inbox_chief_send:{draft_id}"}
            return None  # explicit_go: decided inside the tool by safety.send_gate
        except Exception as exc:
            log.warning("inbox-chief send gate failed closed: %s", exc)
            return {"action": "block", "message": f"Inbox Chief could not verify this send ({exc}); not sent."}

    def pre_llm_call(self, session_id: str = "", user_message: str = "", **kw):
        try:
            if kw.get("parent_session_id"):
                return None
            e = self.holder.get()
            text = user_message if isinstance(user_message, str) else str(user_message or "")
            e.store.record_user_message(session_id or "", text,
                                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if not e.store.pending():
                return None
            drafts.prune(client=e.client, store=e.store)
            pend = e.store.pending()
            if not pend:
                return None
            lines = ["[Inbox Chief — drafts awaiting the user's go/no-go; automatic context, not a user message]",
                     "If the user's message is a go on one of these, call inbox_chief_send with that draft_id and "
                     "confirm in one line. If they ask for changes, edit and re-stage, then ask again. If they say "
                     "skip/no, call inbox_chief_resolve. If it's unclear which draft they mean, ask. Never send "
                     "anything they have not approved."]
            for p in pend[-6:]:
                lines.append(f"- draft_id={p['draft_id']} (staged {p.get('staged_at', '')[:16]}) to "
                             f"{', '.join(p.get('to') or [])}: {p.get('summary') or p.get('subject')}")
            return {"context": "\n".join(lines)[:MAX_CONTEXT]}
        except NotConfigured:
            return None
        except Exception as exc:
            log.debug("inbox-chief pre_llm_call skipped: %s", exc)
            return None
