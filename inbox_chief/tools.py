"""Tool handlers: JSON string in/out, never raise (Hermes handler contract)."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from . import drafts, ledger, linking, safety, scan as scan_mod
from .engine import EngineHolder, NotConfigured
from .graph import GraphError, NotSignedIn, address_of
from .lanes import classify, route_reply
from .notes import Notes
from .util import now_of

log = logging.getLogger("inbox_chief.tools")


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _kinds(value: Any) -> list[str] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()]
    return [str(k) for k in value]


class Tools:
    def __init__(self, holder: EngineHolder, *, profile: str = "default",
                 cron_execution: Callable[[], object] = lambda: None):
        self.holder = holder
        self.profile = profile
        self.cron_execution = cron_execution

    def _run(self, fn: Callable[..., dict], args: dict, kw: dict) -> str:
        try:
            return _j(fn(args if isinstance(args, dict) else {}, **kw))
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

    def owner(self, e) -> str:
        return e.settings.owner or self.profile

    # ---- handlers (signature: (args: dict, **kwargs) -> str) ----
    def status(self, args: dict, **kw) -> str:
        return self._run(self._status, args, kw)

    def scan(self, args: dict, **kw) -> str:
        return self._run(self._scan, args, kw)

    def thread(self, args: dict, **kw) -> str:
        return self._run(self._thread, args, kw)

    def notes(self, args: dict, **kw) -> str:
        return self._run(self._notes, args, kw)

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
               "owner": self.owner(e), "send_confirmation": e.settings.send_confirmation,
               "lanes": [{"id": l.id, "label": l.label, "owner": l.owner, "priority": l.priority,
                          "detect": list(l.detect)} for l in e.cfg.lanes_by_priority()],
               "notes": bool(e.notes_dir)}
        if args.get("live"):
            out["pruned"] = drafts.prune(client=e.client, store=e.store, cfg=e.cfg)
            out["tracker_changes"] = [{"subject": i["subject"], "status": i["status"]}
                                      for i in ledger.poll(cfg=e.cfg, client=e.client, store=e.store)]
            out["me"] = e.client.me().get("address")
        items = e.store.ledger()
        out["pending"] = len(e.store.pending())
        out["tracked"] = {s: sum(1 for i in items if i.get("status") == s)
                          for s in ("draft", "sent", "replied", "closed", "discarded", "dropped")}
        out["tracked_open"] = [{"draft_id": i["draft_id"], "subject": i["subject"], "status": i["status"],
                                "to": i.get("to"), "due_at": i.get("due_at")}
                               for i in items if i.get("status") in ledger.OPEN][-10:]
        return out

    def _scan(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        return scan_mod.run(cfg=e.cfg, client=e.client, store=e.store, owner=self.owner(e),
                            lookback_days=e.settings.scan_lookback_days, lane=args.get("lane") or None,
                            kinds=_kinds(args.get("kinds")), scheduled=args.get("scheduled") is True,
                            mark_seen=args.get("mark_seen") is not False)

    def _thread(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        if not args.get("message_id"):
            return {"ok": False, "error": "message_id is required"}
        notes = Notes(e.notes_dir) if e.notes_dir else None
        return linking.thread_context(cfg=e.cfg, client=e.client, message_id=str(args["message_id"]), notes=notes)

    def _notes(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        if not e.notes_dir:
            return {"ok": False, "error": "account notes are off: set plugins.entries.inbox-chief.settings.notes_dir "
                                          "to a folder (a team-synced folder works)."}
        n = Notes(e.notes_dir)
        action = str(args.get("action") or "")
        name, text = str(args.get("name") or ""), str(args.get("text") or "")
        if action == "list":
            return {"ok": True, "notes": [{"name": k, "matches": list(v.terms)} for k, v in n.all().items()]}
        if action == "match":
            return {"ok": True, "matches": n.match(text or name)}
        if action == "show":
            note = n.find(name)
            if not note:
                return {"ok": False, "error": f"no note named {name!r}", "similar": n.match(name)}
            return {"ok": True, "name": note.name, "aliases": list(note.terms[1:]), "body": n.body(note.name, 100_000)}
        if action == "add":
            if not name or not text:
                return {"ok": False, "error": "name and text are required for action=add"}
            note = n.add(name, agent=self.owner(e), text=text, aliases=str(args.get("aliases") or ""),
                         today=now_of(e.client).strftime("%Y-%m-%d"))
            return {"ok": True, "saved": note.name, "aliases": list(note.terms[1:])}
        return {"ok": False, "error": "action must be one of match, show, add, list"}

    def _lane_for(self, e, message_id: str) -> tuple[str, str]:
        """Lane + owner of the message being answered (so the tracker routes the reply to that owner)."""
        try:
            m = e.client.get_message(message_id) or {}
            kind = "supplier_reply" if e.cfg.supplier_for(address_of(m.get("from"))) else "customer_reply"
            lane = classify(e.cfg, m) or route_reply(e.cfg, m, kind)
            if lane:
                return lane.id, (self.owner(e) if lane.owner == "self" else lane.owner)
        except Exception:
            pass
        return "", self.owner(e)

    def _draft_reply(self, args: dict, session_id: str = "", **kw) -> dict:
        e = self.holder.get()
        if not args.get("message_id") or not isinstance(args.get("body"), str):
            return {"ok": False, "error": "message_id and body are required"}
        lane, owner = self._lane_for(e, args["message_id"])
        return drafts.draft_reply(client=e.client, store=e.store, cfg=e.cfg, message_id=args["message_id"],
                                  body=args["body"], sender_only=args.get("sender_only") is True,
                                  attach_from_message_id=args.get("attach_from_message_id"),
                                  attachment_names=args.get("attachment_names"), summary=args.get("summary", ""),
                                  session_id=session_id or "", lane=lane, owner=owner,
                                  reply_needed=args.get("reply_needed") is not False,
                                  signature=e.settings.signature)

    def _draft_new(self, args: dict, session_id: str = "", **kw) -> dict:
        e = self.holder.get()
        if not str(args.get("subject") or "").strip():
            return {"ok": False, "error": "subject is required"}
        return drafts.draft_new(client=e.client, store=e.store, cfg=e.cfg, to=args.get("to", ""),
                                cc=args.get("cc", ""), subject=args.get("subject", ""), body=args.get("body", ""),
                                attach_from_message_id=args.get("attach_from_message_id"),
                                attachment_names=args.get("attachment_names"), summary=args.get("summary", ""),
                                session_id=session_id or "", owner=self.owner(e),
                                reply_needed=args.get("reply_needed") is not False, signature=e.settings.signature)

    def _pending(self, args: dict, **kw) -> dict:
        e = self.holder.get()
        pruned = drafts.prune(client=e.client, store=e.store, cfg=e.cfg)
        rows = [{k: p.get(k) for k in ("draft_id", "subject", "to", "summary", "kind", "lane", "attachments",
                                       "staged_at")}
                for p in e.store.pending()]
        return {"ok": True, "pending": rows, "pruned_already_sent_or_deleted": pruned, "checked_live": True}

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
