"""Read model for the Desktop panel (``desktop/plugin.js`` via ``dashboard/plugin_api.py``).

The panel shows, per lane: the queue (items the scan found), the drafts staged for a go/no-go, and the
account notes that match each item. Everything here is built from the same tool handlers the agent
uses (``Tools``), so the panel can never see or do more than the agent can.

Deliberately absent: any way to SEND. The panel's Go button submits ``go <short id>`` into the user's
chat, so the send runs through ``inbox_chief_send`` and its gates (S1–S5, ``pre_tool_call`` approval).
The only write here is ``skip`` (``inbox_chief_resolve``), which removes a draft from the go/no-go list
and leaves the draft itself in Outlook.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Callable

SHORT_ID_LEN = 6        # safety.go_selects matches draft_id[-6:]
MAX_NOTES_PER_ITEM = 3
MAX_PREVIEW = 4000
UNASSIGNED = "_unassigned"


def short_id(draft_id: str) -> str:
    return (draft_id or "")[-SHORT_ID_LEN:]


def go_message(draft_id: str) -> str:
    """The chat message the Go button submits. Must satisfy ``safety.is_go`` and ``safety.go_selects``."""
    return f"go {short_id(draft_id)}"


def change_message(draft_id: str) -> str:
    """Seeded into the composer by Change; the user finishes the sentence. Never a go (``change`` blocks it)."""
    return f"change {short_id(draft_id)}: "


def _call(handler: Callable[..., str], args: dict) -> dict:
    try:
        out = json.loads(handler(args))
        return out if isinstance(out, dict) else {"ok": False, "error": "unexpected tool result"}
    except Exception as exc:  # a broken handler must not take the panel down
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _items_from_scan(scan: dict) -> list[dict]:
    """Accept the scan result in any of the shapes SPEC §3 allows: ``items: [...]`` with a ``lane`` field,
    ``lanes: {lane_id: [...]}``, or ``lanes: [{id, items: [...]}]``."""
    items: list[dict] = []
    if isinstance(scan.get("items"), list):
        items += [i for i in scan["items"] if isinstance(i, dict)]
    lanes = scan.get("lanes")
    if isinstance(lanes, dict):
        for lane_id, rows in lanes.items():
            label = rows.get("label", "") if isinstance(rows, dict) else ""
            if isinstance(rows, dict):
                rows = rows.get("items") or []
            for i in rows or []:
                if isinstance(i, dict):
                    items.append({"lane": lane_id, "lane_label": label, **i})
    elif isinstance(lanes, list):
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            for i in lane.get("items") or []:
                if isinstance(i, dict):
                    items.append({"lane": lane.get("id") or lane.get("lane") or "", **i})
    return items


def _item_row(i: dict) -> dict:
    keep = ("key", "kind", "lane", "message_id", "conversation_id", "sender", "sender_name", "subject", "received",
            "preview", "attachments", "waiting_business_days", "detail", "possibly_waiting_on")
    row = {k: i[k] for k in keep if k in i}
    row["preview"] = str(row.get("preview") or "")[:300]
    return row


def _draft_row(p: dict) -> dict:
    keep = ("draft_id", "subject", "to", "summary", "kind", "lane", "owner", "staged_at", "attachments", "reply_to")
    row = {k: p.get(k) for k in keep if k in p}
    row["short_id"] = short_id(p.get("draft_id") or "")
    row["go_message"] = go_message(p.get("draft_id") or "")
    row["change_message"] = change_message(p.get("draft_id") or "")
    return row


def _note_names(match: dict) -> list[str]:
    names = match.get("matches") or match.get("notes") or match.get("names") or []
    out = []
    for n in names if isinstance(names, list) else []:
        name = n.get("name") if isinstance(n, dict) else n
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return out[:MAX_NOTES_PER_ITEM]


_published: dict[str, Any] = {}


def publish(tools: Any) -> None:
    """Called by ``register()`` so the panel backend, when it runs in the same process as the agent half,
    reuses the agent's ``Tools`` (same engine cache, same state) instead of building a second one."""
    _published["tools"] = tools


def published_tools() -> Any:
    return _published.get("tools")


class Panel:
    """Assemble the panel's view from a ``Tools`` instance (see ``inbox_chief.tools``)."""

    def __init__(self, tools: Any, *, shared_process: bool = False):
        self.tools = tools
        self.shared_process = shared_process

    def summary(self) -> dict:
        """Cheap counts for the status-bar chip: no mailbox scan, pending list pruned live."""
        status = _call(self.tools.status, {})
        if not status.get("ok"):
            return {"ok": False, "setup_needed": bool(status.get("setup_needed")),
                    "sign_in_needed": bool(status.get("sign_in_needed")), "error": status.get("error", "")}
        pending = _call(self.tools.pending, {})
        return {"ok": True, "pending_count": len(pending.get("pending") or []), "mailbox": status.get("mailbox"),
                "lanes": len(status.get("lanes") or [])}

    def queue(self, *, scan: bool = True) -> dict:
        status = _call(self.tools.status, {})
        if not status.get("ok"):
            return {"ok": False, "setup_needed": bool(status.get("setup_needed")),
                    "sign_in_needed": bool(status.get("sign_in_needed")),
                    "error": status.get("error") or "Inbox Chief is not ready."}
        lanes = [{"id": l.get("id"), "label": l.get("label") or l.get("id"), "owner": l.get("owner"),
                  "items": [], "drafts": []} for l in status.get("lanes") or [] if isinstance(l, dict)]
        by_id = {l["id"]: l for l in lanes}

        def lane_for(lane_id: str | None, label: str = "") -> dict:
            key = lane_id or UNASSIGNED
            if key not in by_id:
                by_id[key] = {"id": key, "label": label or ("Other" if key == UNASSIGNED else key),
                              "owner": "", "items": [], "drafts": []}
                lanes.append(by_id[key])
            return by_id[key]

        warnings: list[str] = []
        pending = _call(self.tools.pending, {})
        if pending.get("ok"):
            for p in pending.get("pending") or []:
                lane_for(p.get("lane")).setdefault("drafts", []).append(_draft_row(p))
        else:
            warnings.append(f"drafts: {pending.get('error', 'unavailable')}")

        notes_on = bool(status.get("notes"))
        if scan:
            # mark_seen=false: looking at the panel must not swallow items the agent has yet to report.
            result = _call(self.tools.scan, {"mark_seen": False})
            if result.get("ok") or result.get("items") is not None or result.get("lanes") is not None:
                for i in _items_from_scan(result):
                    row = _item_row(i)
                    if notes_on:
                        text = " ".join(str(row.get(k) or "") for k in ("sender", "sender_name", "subject"))
                        row["notes"] = _note_names(_call(self.tools.notes, {"action": "match", "text": text}))
                    lane_for(row.get("lane"), str(i.get("lane_label") or "")).setdefault("items", []).append(row)
            elif not result.get("not_implemented"):
                warnings.append(f"scan: {result.get('error', 'unavailable')}")

        total_drafts = sum(len(l["drafts"]) for l in lanes)
        return {"ok": True, "company": status.get("company") or "", "mailbox": status.get("mailbox"),
                "profile": status.get("profile"), "send_confirmation": status.get("send_confirmation"),
                "notes_enabled": notes_on, "lanes": lanes, "pending_count": total_drafts,
                "item_count": sum(len(l["items"]) for l in lanes), "warnings": warnings}

    def draft(self, draft_id: str) -> dict:
        """Read-only preview of a staged draft's current text (fresh from the mailbox)."""
        if not draft_id:
            return {"ok": False, "error": "draft_id is required"}
        pending = _call(self.tools.pending, {})
        staged = next((p for p in pending.get("pending") or [] if p.get("draft_id") == draft_id), None)
        if not staged:
            return {"ok": False, "error": "that draft is no longer awaiting a go"}
        try:
            msg = self.tools.holder.get().client.get_message(draft_id) or {}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, **_draft_row(staged), "body": body_text(msg)[:MAX_PREVIEW]}

    def skip(self, draft_id: str, outcome: str = "skipped") -> dict:
        if outcome not in ("skipped", "dropped"):
            outcome = "skipped"
        return _call(self.tools.resolve, {"draft_id": draft_id or "", "outcome": outcome})

    def note(self, name: str) -> dict:
        """One account note, normalised to ``{ok, name, body}`` whatever shape the notes tool returns."""
        if not name:
            return {"ok": False, "error": "name is required"}
        r = _call(self.tools.notes, {"action": "show", "name": name})
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "note not found"}
        body = r.get("body") or r.get("text") or r.get("content") or r.get("markdown") or ""
        note = r.get("note")
        if not body and isinstance(note, dict):
            body = note.get("body") or note.get("text") or ""
        return {"ok": True, "name": r.get("name") or (note or {}).get("name") or name, "body": str(body)[:MAX_PREVIEW]}

    def notes(self, query: str = "") -> dict:
        """Note names, normalised to ``{ok, names}``: all notes, or those matching *query*."""
        args = {"action": "match", "text": query} if query.strip() else {"action": "list"}
        r = _call(self.tools.notes, args)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "notes unavailable",
                    "not_implemented": bool(r.get("not_implemented"))}
        names: list[str] = []
        for key in ("matches", "notes", "names"):
            for n in r.get(key) or []:
                name = n.get("name") if isinstance(n, dict) else n
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
        return {"ok": True, "names": names[:200]}


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|tr|li|h[1-6])\b[^>]*>", re.I)


def body_text(message: dict) -> str:
    """Plain text of a message body (HTML stripped), for display only."""
    body = message.get("body") or {}
    content = body.get("content") or message.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        content = re.sub(r"(?is)<(style|head)\b.*?</\1>", "", content)
        content = _BLOCK.sub("\n", content)
        content = html.unescape(_TAG.sub("", content))
    lines = [ln.rstrip() for ln in content.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
