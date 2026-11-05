"""Tool handlers: JSON string in/out, never raise (Hermes handler contract).

Scaffold: status, draft_reply, draft_new, pending, send and resolve are wired end-to-end (they carry
the safety rules). scan, thread and notes return ``not_implemented`` until the core-engine card
lands lanes.py / linking.py / notes.py behind the same signatures.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from . import drafts, safety
from .engine import EngineHolder, NotConfigured
from .graph import GraphError, NotSignedIn

log = logging.getLogger("inbox_chief.tools")


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Tools:
    def __init__(self, holder: EngineHolder, *, profile: str = "default",
                 cron_execution: Callable[[], object] = lambda: None):
        self.holder = holder
        self.profile = profile
        self.cron_execution = cron_execution

    def _run(self, fn: Callable[..., dict], args: dict, kw: dict) -> str:
        try:
            return _j(fn(args or {}, **kw))
        except NotConfigured as exc:
            return _j({"ok": False, "setup_needed": True, "error": str(exc)})
        except NotSignedIn as exc:
            return _j({"ok": False, "sign_in_needed": True,
                       "error": f"{exc} Run `hermes auth add inbox-chief:microsoft` in a terminal (not from chat)."})
        except GraphError as exc:
            return _j({"ok": False, "error": str(exc)})
        except Exception as exc:  # never let a tool call crash the turn
            log.exception("inbox-chief tool failed")
            return _j({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ---- handlers (signature: (args: dict, **kwargs) -> str) ----
    def status(self, args: dict, **kw) -> str:
        return self._run(self._status, args, kw)

    def scan(self, args: dict, **kw) -> str:
        return _j({"ok": False, "not_implemented": True, "error": "scan lands with the core-engine card"})

    def thread(self, args: dict, **kw) -> str:
        return _j({"ok": False, "not_implemented": True, "error": "thread context lands with the core-engine card"})

    def notes(self, args: dict, **kw) -> str:
        return _j({"ok": False, "not_implemented": True, "error": "account notes land with the core-engine card"})

    def draft_reply(self, args: dict, **kw) -> str:
        return self._run(self._draft_reply, args, kw)

    def draft_new(self, args: dict, **kw) -> str:
        return self._run(self._draft_new, args, kw)

    def pending(self, args: dict, **kw) -> str:
        return self._run(self._pending, args, kw)

    def send(self, args: dict, **kw) -> str:
        return self._run(self._send, args, kw)

    def resolve(self, args: dict, **kw) -> str:
        return self._run(self._resolve, args, kw)

    # ---- implementations ----
    def _status(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        out = {"ok": True, "mailbox": e.mailbox, "company": e.cfg.company_name, "profile": self.profile,
               "send_confirmation": e.settings.send_confirmation,
               "lanes": [{"id": l.id, "label": l.label, "owner": l.owner, "priority": l.priority,
                          "detect": list(l.detect)} for l in e.cfg.lanes_by_priority()],
               "notes": bool(e.notes_dir)}
        if args.get("live"):
            out["pruned"] = drafts.prune(client=e.client, store=e.store)
            out["me"] = e.client.me().get("address")
        out["pending"] = len(e.store.pending())
        return out

    def _draft_reply(self, args: dict, session_id: str = "", **kw) -> dict:
        e = self.holder.get()
        if not args.get("message_id") or not isinstance(args.get("body"), str):
            return {"ok": False, "error": "message_id and body are required"}
        return drafts.draft_reply(client=e.client, store=e.store, cfg=e.cfg, message_id=args["message_id"],
                                  body=args["body"], sender_only=args.get("sender_only") is True,
                                  attach_from_message_id=args.get("attach_from_message_id"),
                                  attachment_names=args.get("attachment_names"), summary=args.get("summary", ""),
                                  session_id=session_id or "")

    def _draft_new(self, args: dict, session_id: str = "", **kw) -> dict:
        e = self.holder.get()
        return drafts.draft_new(client=e.client, store=e.store, cfg=e.cfg, to=args.get("to", ""),
                                cc=args.get("cc", ""), subject=args.get("subject", ""), body=args.get("body", ""),
                                attach_from_message_id=args.get("attach_from_message_id"),
                                attachment_names=args.get("attachment_names"), summary=args.get("summary", ""),
                                session_id=session_id or "")

    def _pending(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        pruned = drafts.prune(client=e.client, store=e.store)
        rows = [{k: p.get(k) for k in ("draft_id", "subject", "to", "summary", "kind", "staged_at")}
                for p in e.store.pending()]
        return {"ok": True, "pending": rows, "pruned_already_sent_or_deleted": pruned}

    def _send(self, args: dict, session_id: str = "", platform: str = "", parent_session_id: str = "", **kw) -> dict:
        e = self.holder.get()
        unattended = safety.is_unattended(platform=platform, cron_execution=self.cron_execution(),
                                          parent_session_id=parent_session_id or "")
        if not args.get("draft_id"):
            return {"ok": False, "error": "draft_id is required"}
        return drafts.send(client=e.client, store=e.store, cfg=e.cfg, settings=e.settings,
                           draft_id=args["draft_id"], session_id=session_id or "", unattended=unattended)

    def _resolve(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        return drafts.resolve(store=e.store, draft_id=str(args.get("draft_id") or ""),
                              outcome=str(args.get("outcome") or "dropped"))
