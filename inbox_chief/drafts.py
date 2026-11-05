"""Stage drafts, prune the pending list against the mailbox, and send only through the gate.

Reply-all/new drafts with attachments copied from a source message, S5 check, fingerprint +
staging, tracker (ledger) registration, live prune against the mailbox, gated send, the reply /
reply-all icon on the answered message (via the ledger), optional signature, and resolve.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable

from . import ledger, safety
from .config import LanesConfig
from .graph import GraphClient, address_of
from .state import Store
from .util import now_iso


@dataclass
class Settings:
    send_confirmation: str = "approval_gate"
    go_phrases: tuple[str, ...] = ()
    scan_lookback_days: int = 2
    owner: str = ""
    signature: str = ""


_SIGNOFF = re.compile(r"\n\s*(thanks|thank you|best|regards|best regards|cheers|sincerely)[,!.]?(\s*\n[^\n]{0,60}){0,2}\s*\Z",
                      re.I)


def _with_signature(body: str, signature: str) -> str:
    """Append the configured signature, dropping a drafted sign-off so there is no double "Thanks"."""
    if not (signature or "").strip():
        return body
    trimmed = _SIGNOFF.sub("", (body or "").rstrip())
    return trimmed.rstrip() + "\n\n" + signature.strip()


def _recipients(csv_or_list: Any) -> list[dict]:
    items = csv_or_list if isinstance(csv_or_list, list) else str(csv_or_list or "").replace(";", ",").split(",")
    return [{"emailAddress": {"address": a.strip()}} for a in items if str(a).strip()]


def _all_recipients(draft: dict) -> list[str]:
    return [address_of(r) for k in ("toRecipients", "ccRecipients", "bccRecipients") for r in draft.get(k) or []]


def _text_to_html(text: str) -> str:
    paras = [p for p in (text or "").replace("\r\n", "\n").split("\n\n")]
    return "".join(f"<p>{'<br>'.join(html.escape(line) for line in p.split(chr(10)))}</p>" for p in paras if p.strip())


def _compose(body: str, generated: dict) -> dict:
    """Prepend the new text to Graph's generated reply body, keeping the quoted history intact."""
    content = generated.get("content") or ""
    if (generated.get("contentType") or "").lower() == "html":
        new = _text_to_html(body)
        lower = content.lower()
        i = lower.find("<body")
        if i >= 0:
            j = content.find(">", i) + 1
            return {"contentType": "HTML", "content": content[:j] + new + "<p>&nbsp;</p>" + content[j:]}
        return {"contentType": "HTML", "content": new + "<p>&nbsp;</p>" + content}
    return {"contentType": "Text", "content": (body or "").rstrip() + ("\n\n" + content if content else "")}


def _copy_attachments(client: GraphClient, source_id: str | None, names: Iterable[str] | None) -> list[tuple[dict, str]]:
    """Non-inline, non-signature attachments of *source_id* (optionally filtered by name) + their sender."""
    if not source_id:
        return []
    src = client.get_message(source_id)
    if not src:
        raise ValueError(f"attachment source message {source_id} not found")
    origin = address_of(src.get("from"))
    wanted = {n.lower() for n in names or []}
    out = []
    for att in client.list_attachments(source_id):
        name = att.get("name") or "attachment"
        if att.get("isInline"):
            continue
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")) and (att.get("size") or 0) < 150_000:
            continue  # signature images
        if wanted and name.lower() not in wanted:
            continue
        if not att.get("contentBytes"):
            continue
        out.append(({"name": name, "contentType": att.get("contentType") or "application/octet-stream",
                     "contentBytes": att["contentBytes"]}, origin))
    return out


def _stage(store: Store, client: GraphClient, draft_id: str, *, summary: str, kind: str, session_id: str,
           reply_to: str | None, lane: str = "", owner: str = "") -> dict:
    live = client.get_message(draft_id) or {}
    names = [a.get("name", "") for a in client.list_attachments(draft_id)]
    entry = {"draft_id": draft_id, "subject": live.get("subject", ""), "to": _all_recipients(live),
             "summary": summary, "kind": kind, "reply_to": reply_to, "lane": lane, "owner": owner or "self",
             "session_id": session_id, "staged_at": now_iso(client), "attachments": names,
             "fingerprint": safety.draft_fingerprint(live, names)}
    store.stage(entry)
    return entry


def _track(store: Store, client: GraphClient, cfg: LanesConfig, entry: dict, reply_needed: bool) -> None:
    try:
        ledger.track(store=store, client=client, draft_id=entry["draft_id"], owner=entry.get("owner") or "self",
                     kind=entry["kind"], reply_to=entry.get("reply_to"),
                     due_business_days=cfg.followup_due_business_days, reply_needed=reply_needed,
                     note=entry.get("summary", ""))
    except Exception:
        pass  # tracking is best effort; the staged draft is what gates sending


def draft_reply(*, client: GraphClient, store: Store, cfg: LanesConfig, message_id: str, body: str,
                sender_only: bool = False, attach_from_message_id: str | None = None,
                attachment_names: list[str] | None = None, summary: str = "", session_id: str = "",
                lane: str = "", owner: str = "", reply_needed: bool = True, signature: str = "") -> dict:
    """Reply-all (default) draft on *message_id*; S5-checked before anything is written."""
    original = client.get_message(message_id)
    if not original:
        return {"ok": False, "error": f"message {message_id} not found"}
    body = _with_signature(body, signature)
    atts = _copy_attachments(client, attach_from_message_id, attachment_names)
    # Predict recipients the same way Graph will, so S5 can refuse before a draft exists.
    me = client.me().get("address", "")
    rcpts = [address_of(original.get("from"))]
    if not sender_only:
        rcpts += [address_of(r) for r in (original.get("toRecipients") or []) + (original.get("ccRecipients") or [])]
    rcpts = [r for r in dict.fromkeys(rcpts) if r and r != me]
    thread = client.conversation(original.get("conversationId") or "")
    sources = [dict(m, _included_in_draft=True) for m in thread if not m.get("isDraft")]
    verdict = safety.check_outbound(cfg=cfg, recipients=rcpts, body_text=body,
                                    attachments=[(a["name"], origin) for a, origin in atts], source_messages=sources)
    if not verdict.ok:
        return {"ok": False, "error": verdict.reason, "findings": list(verdict.findings)}
    action = safety.reply_action(sender_only)
    draft = client.create_reply_draft(message_id, reply_all=(action == "createReplyAll"))
    generated = (client.get_message(draft["id"]) or {}).get("body") or {}
    client.update_message(draft["id"], {"body": _compose(body, generated)})
    for att, _ in atts:
        client.add_attachment(draft["id"], att)
    entry = _stage(store, client, draft["id"], summary=summary or f"Reply: {original.get('subject', '')}",
                   kind="reply_all" if action == "createReplyAll" else "reply", session_id=session_id,
                   reply_to=message_id, lane=lane, owner=owner)
    _track(store, client, cfg, entry, reply_needed)
    return {"ok": True, "staged": entry, "reply_all": action == "createReplyAll",
            "next": "Show the user the draft and ask for go / change / skip. Do not send without a go."}


def draft_new(*, client: GraphClient, store: Store, cfg: LanesConfig, to: Any, subject: str, body: str,
              cc: Any = "", attach_from_message_id: str | None = None, attachment_names: list[str] | None = None,
              summary: str = "", session_id: str = "", lane: str = "", owner: str = "",
              reply_needed: bool = True, signature: str = "") -> dict:
    to_r, cc_r = _recipients(to), _recipients(cc)
    if not to_r:
        return {"ok": False, "error": "at least one recipient is required"}
    body = _with_signature(body, signature)
    atts = _copy_attachments(client, attach_from_message_id, attachment_names)
    sources = []
    if attach_from_message_id:
        src = client.get_message(attach_from_message_id)
        if src:
            sources.append(src)
    verdict = safety.check_outbound(cfg=cfg, recipients=[address_of(r) for r in to_r + cc_r], body_text=body,
                                    attachments=[(a["name"], origin) for a, origin in atts], source_messages=sources)
    if not verdict.ok:
        return {"ok": False, "error": verdict.reason, "findings": list(verdict.findings)}
    draft = client.create_draft({"subject": subject, "body": {"contentType": "Text", "content": body},
                                 "toRecipients": to_r, "ccRecipients": cc_r})
    for att, _ in atts:
        client.add_attachment(draft["id"], att)
    entry = _stage(store, client, draft["id"], summary=summary or subject, kind="new", session_id=session_id,
                   reply_to=None, lane=lane, owner=owner)
    _track(store, client, cfg, entry, reply_needed)
    return {"ok": True, "staged": entry,
            "next": "Show the user the draft and ask for go / change / skip. Do not send without a go."}


def prune(*, client: GraphClient, store: Store, cfg: LanesConfig | None = None) -> int:
    """Drop staged drafts that are no longer drafts (the user sent or deleted them). Live check.

    Live bug this fixes: a draft the user sent from Outlook stayed on the go/no-go list. With *cfg*
    the tracker is advanced for those drafts too, so the manual send is recorded (and the reply icon set).
    """
    keep, gone_ids = [], []
    for p in store.pending():
        try:
            m = client.get_message(p["draft_id"])
            still = bool(m and m.get("isDraft"))
        except Exception:
            still = True  # unknown: keep it rather than lose a pending go
        if still:
            keep.append(p)
        else:
            gone_ids.append(p["draft_id"])
    if gone_ids:
        store.replace_pending(keep)
        if cfg is not None:
            try:
                ledger.poll(cfg=cfg, client=client, store=store, draft_ids=gone_ids)
            except Exception:
                pass
    return len(gone_ids)


def send(*, client: GraphClient, store: Store, cfg: LanesConfig, settings: Settings, draft_id: str,
         session_id: str, unattended: bool) -> dict:
    staged = next((p for p in store.pending() if p.get("draft_id") == draft_id), None)
    live = client.get_message(draft_id)
    names = [a.get("name", "") for a in client.list_attachments(draft_id)] if live else []
    pending_here = len([p for p in store.pending() if p.get("session_id") == session_id]) if session_id else 0
    verdict = safety.send_gate(mode=settings.send_confirmation, staged=staged, live_draft=live,
                               live_attachment_names=names, unattended=unattended,
                               last_user_message=store.last_user_message(session_id),
                               pending_in_session=pending_here, extra_go_phrases=settings.go_phrases)
    if not verdict.ok:
        if staged and not unattended and not (live or {}).get("isDraft"):
            prune(client=client, store=store, cfg=cfg)  # sent/deleted outside Inbox Chief: drop the stale entry
        return {"ok": False, "sent": False, "error": verdict.reason}
    # S5 again at send time: the draft may have been edited in Outlook after staging (fingerprint
    # catches edits, but recipients added to an unchanged body are covered here too).
    if staged and staged.get("reply_to"):
        src = client.get_message(staged["reply_to"]) or {}
        thread = client.conversation(src.get("conversationId") or "")
        sources = [dict(m, _included_in_draft=True) for m in thread if not m.get("isDraft")]
    else:
        sources = []
    v2 = safety.check_outbound(cfg=cfg, recipients=_all_recipients(live),
                               body_text=(live.get("body") or {}).get("content", ""),
                               attachments=[], source_messages=sources)
    if not v2.ok:
        return {"ok": False, "sent": False, "error": v2.reason, "findings": list(v2.findings)}
    client.send_draft(draft_id)
    store.unstage(draft_id)
    tracked = None
    try:
        tracked = ledger.record_sent(cfg=cfg, client=client, store=store, draft_id=draft_id)
    except Exception:
        pass
    return {"ok": True, "sent": True, "subject": live.get("subject"), "to": _all_recipients(live),
            "reply_icon_set": bool(tracked and tracked.get("icon_set")),
            "tracking": (tracked or {}).get("status")}


def resolve(*, store: Store, draft_id: str, outcome: str) -> dict:
    hit = store.unstage(draft_id)
    if hit:
        items = store.ledger()
        for it in items:
            if it["draft_id"] == draft_id and it["status"] == "draft":
                it["status"] = "dropped"
        store.save_ledger(items)
    return {"ok": bool(hit), "outcome": outcome, "draft_id": draft_id,
            **({} if hit else {"error": "no staged draft with that id"})}
