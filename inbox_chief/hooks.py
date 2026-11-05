"""Hook callbacks.

* ``pre_tool_call``: the hard send gate. Unattended → block. ``approval_gate`` mode → escalate the
  send to Hermes's human-approval prompt (fails closed when no human is present).
* ``pre_llm_call``: records the user's latest message per session (``explicit_go`` mode) and injects
  pending drafts (pruned live), unseen tracker events and matching account notes (``context.build``).

Both swallow their own errors except the send gate, which fails CLOSED (blocks) when it cannot decide.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from . import context, safety
from .engine import EngineHolder, NotConfigured

log = logging.getLogger("inbox_chief.hooks")
SEND_TOOL = "inbox_chief_send"
MAX_CONTEXT = context.MAX_CONTEXT


class Hooks:
    def __init__(self, holder: EngineHolder, cron_execution: Callable[[], object] = lambda: None,
                 profile: str = "default"):
        self.holder = holder
        self.cron_execution = cron_execution
        self.profile = profile

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
            owner = e.settings.owner or self.profile
            ctx_text, event_keys = context.build(e, owner=owner, session_id=session_id or "", user_message=text)
            if event_keys:
                e.store.mark_seen(event_keys)
            return {"context": ctx_text} if ctx_text else None
        except NotConfigured:
            return None
        except Exception as exc:
            log.debug("inbox-chief pre_llm_call skipped: %s", exc)
            return None
