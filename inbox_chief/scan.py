"""``inbox_chief_scan`` orchestration: poll the tracker, run detections, apply ownership + dedupe.

Order matters (live bug: duplicate alerts):

1. ``ledger.poll`` first, so a reply to a tracked draft is recorded as a ``replied`` event *before*
   detections run; detections then skip that message (and any thread the tracker still follows).
2. ``lanes.detect`` → at most one item per conversation.
3. Tracker events (``replied``/``overdue``) become items routed to the draft's owner.
4. Filter to this profile's lanes / owner and the requested kinds, drop keys already handled,
   and (``mark_seen``) remember the returned keys (last 500). Tracker events reported here are also
   marked seen for context injection, so the same reply is never surfaced twice.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from . import ledger
from .config import Hours, LanesConfig
from .graph import GraphClient
from .lanes import KINDS, DetectContext, Item, detect, owns
from .state import Store
from .util import UTC, now_of

SILENT = "[SILENT]"


def within_hours(hours: Hours, now: datetime) -> bool:
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(hours.timezone))
    except Exception:  # unknown zone / no tz database: fall back to UTC rather than never scanning
        local = now.astimezone(UTC)
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[local.weekday()]
    hhmm = local.strftime("%H:%M")
    return day in hours.days and hours.start <= hhmm <= hours.end


def _event_items(cfg: LanesConfig, store: Store) -> list[Item]:
    out = []
    by_draft = {i["draft_id"]: i for i in store.ledger()}
    for e in ledger.events(store, kinds=("replied", "overdue"), unseen_only=False):
        it = by_draft.get(e["draft_id"], {})
        lane = next((p for p in store.pending() if p.get("draft_id") == e["draft_id"]), {}).get("lane", "")
        if e["kind"] == "replied":
            detail = (f"{it.get('reply_from')} replied to the draft we tracked; read the thread and tell the user, "
                      f"staging a reply if one is needed")
        else:
            detail = (f"no reply since {(it.get('sent_at') or '')[:10]} (due {(it.get('due_at') or '')[:10]}); "
                      f"draft a follow-up for the user to approve")
        out.append(Item(key=e["key"], kind=e["kind"], lane=lane or "tracker", lane_label="Tracked drafts",
                        owner=it.get("owner") or "self", message_id=e.get("message_id") or it.get("sent_id") or "",
                        conversation_id=it.get("conversation_id") or "",
                        sender=(it.get("reply_from") or "") if e["kind"] == "replied" else ", ".join(it.get("to") or []),
                        sender_name="", subject=it.get("subject") or "", received=e["at"], preview="", detail=detail))
    return out


def run(*, cfg: LanesConfig, client: GraphClient, store: Store, owner: str, lookback_days: int = 2,
        lane: str | None = None, kinds: Iterable[str] | None = None, scheduled: bool = False,
        mark_seen: bool = True) -> dict:
    now = now_of(client)
    if scheduled and not within_hours(cfg.hours, now):
        return {"ok": True, "silent": True, "result": SILENT, "reason": "outside configured hours"}
    wanted = set(kinds or KINDS)
    bad = wanted - set(KINDS)
    if bad:
        return {"ok": False, "error": f"unknown kinds: {', '.join(sorted(bad))}"}
    lane_ids = {l.id for l in cfg.lanes}
    if lane and lane not in lane_ids:
        return {"ok": False, "error": f"unknown lane {lane!r}; lanes: {', '.join(sorted(lane_ids))}"}

    ledger.poll(cfg=cfg, client=client, store=store)
    me = client.me()
    ctx = DetectContext(cfg=cfg, client=client, now=now, lookback_days=lookback_days,
                        tracked_open_conversations=ledger.open_conversations(store),
                        tracker_reported_messages=ledger.reported_reply_messages(store),
                        me=me.get("address", ""), me_name=me.get("name", ""))
    items = detect(ctx) + _event_items(cfg, store)

    lanes_by_id = {l.id: l for l in cfg.lanes}
    handled = store.handled()
    seen_in_chat = store.seen()
    out: list[Item] = []
    for it in items:
        if it.kind not in wanted or it.key in handled:
            continue
        if it.kind in ("replied", "overdue"):
            # already surfaced to the user in chat (pre_llm_call) → don't alert again
            if it.key in seen_in_chat or it.owner not in ("self", owner) or (lane and it.lane != lane):
                continue
        else:
            ln = lanes_by_id.get(it.lane)
            if lane and it.lane != lane:
                continue
            if not lane and (ln is None or not owns(ln, owner)):
                continue
        out.append(it)

    if mark_seen and out:
        store.mark_handled([i.key for i in out])
        store.mark_seen([i.key for i in out if i.kind in ("replied", "overdue")])
    if not out:
        return {"ok": True, "silent": scheduled, "result": SILENT if scheduled else "no new items",
                "count": 0, "lanes": {}}
    grouped: dict[str, dict] = {}
    for it in out:
        g = grouped.setdefault(it.lane, {"label": it.lane_label, "owner": it.owner, "items": []})
        g["items"].append(it.as_dict())
    return {"ok": True, "silent": False, "count": len(out), "lanes": grouped,
            "next": "For each item: inbox_chief_thread(message_id) first, then stage a reply with "
                    "inbox_chief_draft_reply if one is due, and ask the user go / change / skip. Never send unasked."}
