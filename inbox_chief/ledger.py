"""Draft tracker (SPEC.md §2.5): draft → sent → replied / overdue / discarded / closed.

Port of the live ``email_tracker.py``. Each tracked item lives in plugin state (per profile). The
scheduled scan and the live checks (``inbox_chief_pending``/``status live=true``) call :func:`poll`,
so status always comes from the mailbox, never from memory.

Events (``sent``/``replied``/``overdue``/``discarded``) carry a stable key; the ``pre_llm_call`` hook
injects unseen ones once (seen-state is kept separately in plugin state).

The reply icon: Graph-created replies don't get Outlook's "replied" arrow, so after a reply draft is
sent we set PidTagLastVerbExecuted (0x1081 = 102 reply / 103 reply-all) and PidTagLastVerbExecutionTime
(0x1082) on the message that was answered.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from .config import LanesConfig
from .graph import GraphClient, address_of, when
from .lanes import dedupe_key, is_us
from .state import Store
from .util import add_business_days, iso, now_of, parse_ts

OPEN = ("draft", "sent")
VERB_REPLY, VERB_REPLY_ALL = 102, 103
PROP_VERB, PROP_VERB_TIME = "Integer 0x1081", "SystemTime 0x1082"
DISCARD_AFTER_DAYS = 2


def track(*, store: Store, client: GraphClient, draft_id: str, owner: str, kind: str, reply_to: str | None,
          due_business_days: int, reply_needed: bool = True, note: str = "") -> dict:
    items = store.ledger()
    hit = next((i for i in items if i["draft_id"] == draft_id), None)
    if hit:
        return hit
    d = client.get_message(draft_id) or {}
    item = {"draft_id": draft_id, "conversation_id": d.get("conversationId") or "", "owner": owner,
            "subject": d.get("subject") or "", "kind": kind, "reply_to": reply_to,
            "to": [address_of(r) for r in (d.get("toRecipients") or []) + (d.get("ccRecipients") or [])],
            "created": d.get("createdDateTime") or iso(now_of(client)), "status": "draft",
            "reply_needed": bool(reply_needed), "due_business_days": int(due_business_days), "note": note,
            "sent_at": None, "sent_id": None, "due_at": None, "replied_at": None, "reply_id": None,
            "reply_from": None, "icon_set": False, "events": []}
    items.append(item)
    store.save_ledger(items)
    return item


def track_sent(*, store: Store, client: GraphClient, message_id: str, owner: str, due_business_days: int,
               reply_needed: bool = True, note: str = "") -> dict | None:
    """Follow an email that was already sent (e.g. sent from Outlook, or a demo seed)."""
    m = client.get_message(message_id)
    if not m or m.get("isDraft"):
        return None
    items = store.ledger()
    hit = next((i for i in items if i.get("sent_id") == message_id or i["draft_id"] == message_id), None)
    if hit:
        return hit
    sent_at = m.get("sentDateTime") or when(m)
    t = parse_ts(sent_at)
    item = {"draft_id": message_id, "conversation_id": m.get("conversationId") or "", "owner": owner,
            "subject": m.get("subject") or "", "kind": "new", "reply_to": None,
            "to": [address_of(r) for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])],
            "created": sent_at, "status": "sent" if reply_needed else "closed", "reply_needed": bool(reply_needed),
            "due_business_days": int(due_business_days), "note": note, "sent_at": sent_at, "sent_id": message_id,
            "due_at": iso(add_business_days(t, due_business_days)) if (t and reply_needed) else None,
            "replied_at": None, "reply_id": None, "reply_from": None, "icon_set": False, "events": []}
    items.append(item)
    store.save_ledger(items)
    return item


def _event(item: dict, kind: str, at: str, text: str, message_id: str | None = None) -> None:
    key = dedupe_key(item.get("conversation_id") or item["draft_id"], kind, at)
    if any(e["key"] == key for e in item.setdefault("events", [])):
        return
    item["events"].append({"key": key, "kind": kind, "at": at[:16], "text": text, "message_id": message_id,
                           "owner": item.get("owner"), "draft_id": item["draft_id"]})


def mark_replied_icon(*, cfg: LanesConfig, client: GraphClient, item: dict) -> bool:
    """Set the reply / reply-all icon on the message this sent reply answered."""
    try:
        me = client.me().get("address", "")
        sent_at = item.get("sent_at") or ""
        target = client.get_message(item["reply_to"]) if item.get("reply_to") else None
        if not target:
            prior = [m for m in client.conversation(item.get("conversation_id") or "")
                     if not m.get("isDraft") and not is_us(cfg, address_of(m.get("from")), me) and when(m) <= sent_at]
            target = max(prior, key=when) if prior else None
        if not target:
            return False
        sent = client.get_message(item["sent_id"]) if item.get("sent_id") else None
        n = len((sent or {}).get("toRecipients") or []) + len((sent or {}).get("ccRecipients") or [])
        verb = VERB_REPLY_ALL if (item.get("kind") == "reply_all" or n > 1) else VERB_REPLY
        if item.get("kind") == "reply":
            verb = VERB_REPLY
        client.set_extended_properties(target["id"], [{"id": PROP_VERB, "value": str(verb)},
                                                      {"id": PROP_VERB_TIME, "value": sent_at}])
        return True
    except Exception:
        return False


def _ours_sent(cfg: LanesConfig, msgs: Iterable[dict], me: str, since: str) -> list[dict]:
    return [x for x in msgs if not x.get("isDraft") and is_us(cfg, address_of(x.get("from")), me)
            and x.get("sentDateTime") and x["sentDateTime"] >= since[:19]]


def poll_one(*, cfg: LanesConfig, client: GraphClient, item: dict, sent_hint: dict | None = None) -> None:
    """Advance one item from the mailbox. ``sent_hint``: the sent message when we just sent it ourselves."""
    if item["status"] not in OPEN:
        return
    me = client.me().get("address", "")
    now = now_of(client)
    if item["status"] == "draft":
        d = client.get_message(item["draft_id"])
        if d and d.get("isDraft"):
            return
        sent = [sent_hint] if sent_hint else _ours_sent(cfg, client.conversation(item.get("conversation_id") or ""),
                                                        me, item.get("created") or "")
        if sent:
            s = min(sent, key=lambda x: x["sentDateTime"])
            item.update(status="sent", sent_at=s["sentDateTime"], sent_id=s["id"])
            if item.get("reply_needed"):
                t = parse_ts(s["sentDateTime"])
                item["due_at"] = iso(add_business_days(t, item.get("due_business_days") or 3)) if t else None
            else:
                item["status"] = "closed"
            _event(item, "sent", s["sentDateTime"], f"sent \"{item['subject']}\" to {', '.join(item.get('to') or [])}",
                   s["id"])
            if item.get("reply_to") or (item.get("subject") or "").lower().startswith(("re:", "re :")):
                item["icon_set"] = mark_replied_icon(cfg=cfg, client=client, item=item)
        elif d is None:
            created = parse_ts(item.get("created"))
            if created and now - created > timedelta(days=DISCARD_AFTER_DAYS):
                item["status"] = "discarded"
                _event(item, "discarded", iso(now), f"draft \"{item['subject']}\" was deleted without sending")
        return
    # sent: first reply from someone else after we sent
    replies = [x for x in client.conversation(item.get("conversation_id") or "")
               if not x.get("isDraft") and address_of(x.get("from"))
               and not is_us(cfg, address_of(x.get("from")), me) and when(x) > (item.get("sent_at") or "")]
    if replies:
        r = min(replies, key=when)
        item.update(status="replied", replied_at=when(r), reply_id=r["id"], reply_from=address_of(r.get("from")))
        _event(item, "replied", when(r), f"{item['reply_from']} replied to \"{item['subject']}\"", r["id"])
    elif item.get("due_at") and iso(now) > item["due_at"] and not item.get("overdue_flagged"):
        item["overdue_flagged"] = True
        _event(item, "overdue", item["due_at"],
               f"no reply to \"{item['subject']}\" from {', '.join(item.get('to') or [])} since {item['sent_at'][:10]}; "
               f"draft a follow-up for the user to approve", item.get("sent_id"))


def poll(*, cfg: LanesConfig, client: GraphClient, store: Store, draft_ids: Iterable[str] | None = None) -> list[dict]:
    """Advance every open item (or just *draft_ids*); returns the items that changed status."""
    items = store.ledger()
    wanted = set(draft_ids) if draft_ids is not None else None
    changed = []
    for it in items:
        if wanted is not None and it["draft_id"] not in wanted:
            continue
        before = it["status"]
        try:
            poll_one(cfg=cfg, client=client, item=it)
        except Exception as exc:  # one bad item never stops the poll
            it["last_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        if it["status"] != before:
            changed.append(it)
    store.save_ledger(items)
    return changed


def record_sent(*, cfg: LanesConfig, client: GraphClient, store: Store, draft_id: str,
                sent_message: dict | None = None) -> dict | None:
    items = store.ledger()
    it = next((i for i in items if i["draft_id"] == draft_id), None)
    if not it:
        return None
    poll_one(cfg=cfg, client=client, item=it, sent_hint=sent_message if (sent_message or {}).get("sentDateTime") else None)
    store.save_ledger(items)
    return it


def open_conversations(store: Store) -> frozenset:
    """Threads with a SENT tracked item awaiting a reply — their replies are reported as `replied`."""
    return frozenset(i["conversation_id"] for i in store.ledger()
                     if i.get("status") == "sent" and i.get("reply_needed") and i.get("conversation_id"))


def reported_reply_messages(store: Store) -> frozenset:
    return frozenset(e["message_id"] for i in store.ledger() for e in i.get("events", [])
                     if e.get("kind") == "replied" and e.get("message_id"))


def events(store: Store, *, kinds: Iterable[str] = ("sent", "replied", "overdue", "discarded"),
           owner: str | None = None, unseen_only: bool = True) -> list[dict]:
    """Events across the ledger, one per dedupe key (several tracked drafts in one thread → one event)."""
    seen = store.seen() if unseen_only else set()
    kinds = set(kinds)
    out, keys = [], set()
    for it in store.ledger():
        if owner and it.get("owner") not in (owner, "self"):
            continue
        for e in it.get("events", []):
            if e["kind"] not in kinds or e["key"] in seen or e["key"] in keys:
                continue
            keys.add(e["key"])
            out.append(dict(e, subject=it.get("subject"), conversation_id=it.get("conversation_id")))
    return sorted(out, key=lambda e: e["at"])
