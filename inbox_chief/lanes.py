"""Routing and detections (SPEC.md §2.1–2.2).

* Noise filters run first (built-in + lanes file).
* :func:`classify` — the ``new``-item owner: lanes in ascending priority, first full match wins
  (text/from rules AND exclusions). One owner per email.
* :func:`route_reply` — owner for a reply kind (``customer_reply``/``supplier_reply``/``replies_owed``):
  the first lane that lists the kind and whose text/from rules match; if none match, the first lane that
  lists the kind (so a reply is never silently dropped — live bug: missed customer replies). Lane
  ``exclude_*`` rules apply to ``new`` only (they exist to stop e.g. a supplier's quote being filed as a
  new RFQ, not to hide that supplier's reply).
* :func:`detect` — one pass over the inbox → at most ONE item per conversation (its newest inbound
  message), with a stable dedupe key ``conversationId[-16:] | kind | <minute of that message>``.

Pure functions over a GraphClient; state (handled keys) is applied by the caller (``scan``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .config import Lane, LanesConfig
from .graph import GraphClient, address_of, when
from .util import business_days_between, clean, iso, parse_ts, text_of

KINDS = ("new", "customer_reply", "supplier_reply", "replies_owed", "overdue", "replied")
REPLY_KINDS = ("customer_reply", "supplier_reply", "replies_owed")
_REPLY_SUBJECT = re.compile(r"^\s*(re|fw|fwd|aw|sv)\s*:", re.I)
_SIGNATURE_IMG = re.compile(r"\.(png|jpe?g|gif|bmp)$", re.I)


def dedupe_key(conversation_id: str, kind: str, timestamp: str) -> str:
    return f"{(conversation_id or '')[-16:]}|{kind}|{(timestamp or '')[:16]}"


# ── noise + ownership ────────────────────────────────────────────────────────────────────────────

def is_noise(cfg: LanesConfig, message: dict) -> bool:
    frm = address_of(message.get("from"))
    subj = message.get("subject") or ""
    if not frm:
        return True
    return any(p.search(frm) for p in cfg.noise_senders) or any(p.search(subj) for p in cfg.noise_subjects)


def is_us(cfg: LanesConfig, address: str, me: str = "") -> bool:
    a = (address or "").lower()
    return bool(a) and (a == (me or "").lower() or cfg.is_internal(a))


def _text(message: dict) -> str:
    return f"{message.get('subject') or ''} {clean(message.get('bodyPreview') or '')[:500]}"


def _rules_match(lane: Lane, message: dict) -> bool:
    """Positive rules only. A lane with no positive rules matches everything (catch-all)."""
    if not lane.text and not lane.from_:
        return True
    frm, text = address_of(message.get("from")), _text(message)
    return any(p.search(text) for p in lane.text) or any(p.search(frm) for p in lane.from_)


def _excluded(lane: Lane, message: dict) -> bool:
    frm, text = address_of(message.get("from")), _text(message)
    return any(p.search(frm) for p in lane.exclude_from) or any(p.search(text) for p in lane.exclude_text)


def classify(cfg: LanesConfig, message: dict) -> Lane | None:
    """Owner lane for a NEW inbound message (first full match by priority among lanes detecting ``new``)."""
    for lane in cfg.lanes_by_priority():
        if "new" not in lane.detect:
            continue
        if _rules_match(lane, message) and not _excluded(lane, message):
            return lane
    return None


def route_reply(cfg: LanesConfig, message: dict, kind: str) -> Lane | None:
    lanes = [lane for lane in cfg.lanes_by_priority() if kind in lane.detect]
    for lane in lanes:
        if (lane.text or lane.from_) and _rules_match(lane, message):
            return lane
    return next((lane for lane in lanes if not lane.text and not lane.from_), lanes[0] if lanes else None)


def owns(lane: Lane, owner: str) -> bool:
    return lane.owner == "self" or lane.owner == owner


# ── items ────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Item:
    key: str
    kind: str
    lane: str
    lane_label: str
    owner: str
    message_id: str
    conversation_id: str
    sender: str
    sender_name: str
    subject: str
    received: str
    preview: str
    attachments: list[str] = field(default_factory=list)
    waiting_business_days: int | None = None
    our_last_message: str = ""
    our_questions: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [])}


def visible_attachments(client: GraphClient, message: dict) -> list[str]:
    if not message.get("hasAttachments"):
        return []
    try:
        atts = client.list_attachments(message["id"])
    except Exception:
        return []
    return [a.get("name") or "attachment" for a in atts
            if not a.get("isInline") and not (_SIGNATURE_IMG.search(a.get("name") or "") and (a.get("size") or 0) < 150_000)]


def questions_in(text: str, limit: int = 6) -> list[str]:
    """Sentences of our ask that end in '?' — so the agent can check every question was answered."""
    parts = re.split(r"(?<=[?.!])\s+|\n+", text or "")
    return [p.strip()[:200] for p in parts if p.strip().endswith("?")][:limit]


def _item(kind: str, lane: Lane, m: dict, **extra) -> Item:
    frm = (m.get("from") or {}).get("emailAddress") or {}
    return Item(key=dedupe_key(m.get("conversationId") or m["id"], kind, when(m)), kind=kind, lane=lane.id,
                lane_label=lane.label, owner=lane.owner, message_id=m["id"],
                conversation_id=m.get("conversationId") or "", sender=(frm.get("address") or "").lower(),
                sender_name=frm.get("name") or "", subject=m.get("subject") or "", received=when(m)[:16],
                preview=clean(m.get("bodyPreview") or text_of(m))[:300], **extra)


# ── detection pass ───────────────────────────────────────────────────────────────────────────────

@dataclass
class DetectContext:
    cfg: LanesConfig
    client: GraphClient
    now: datetime
    lookback_days: int = 2
    tracked_open_conversations: frozenset = frozenset()   # tracker follows these → reported as `replied`
    tracker_reported_messages: frozenset = frozenset()     # replies the tracker already reported
    me: str = ""
    me_name: str = ""


def _quotes_us(ctx: DetectContext, m: dict) -> bool:
    """A reply chain whose preview quotes us is a reply owed, not a new lane item (live rule)."""
    if not _REPLY_SUBJECT.search(m.get("subject") or ""):
        return False
    t = (m.get("bodyPreview") or "").lower()
    names = [ctx.me, ctx.me_name, *ctx.cfg.my_addresses]
    return any(n and len(n) >= 4 and n.lower() in t for n in names)


def detect(ctx: DetectContext) -> list[Item]:
    cfg, client = ctx.cfg, ctx.client
    owed_window = max([lane.owed_max_days for lane in cfg.lanes if "replies_owed" in lane.detect] or [0])
    window = max(ctx.lookback_days, owed_window)
    since = iso(ctx.now - timedelta(days=window))
    lookback_cut = iso(ctx.now - timedelta(days=ctx.lookback_days))
    items: list[Item] = []
    seen_conv: set[str] = set()
    for m in client.list_messages("inbox", since=since, top=500):
        conv = m.get("conversationId") or m["id"]
        if conv in seen_conv:
            continue
        seen_conv.add(conv)  # newest inbound message per thread only
        frm = address_of(m.get("from"))
        if m.get("isDraft") or is_us(cfg, frm, ctx.me) or is_noise(cfg, m):
            continue
        thread = client.conversation(conv) or [m]
        has_draft = any(x.get("isDraft") for x in thread)
        ours = [x for x in thread if not x.get("isDraft") and is_us(cfg, address_of(x.get("from")), ctx.me)]
        received = when(m)
        if not ours:
            # brand-new thread → `new` (if unhandled and in the lookback window)
            if has_draft or received < lookback_cut or _quotes_us(ctx, m):
                continue
            lane = classify(cfg, m)
            if lane:
                items.append(_item("new", lane, m))
            continue
        last_ours = max(when(x) for x in ours)
        if received <= last_ours or has_draft:
            continue  # we already answered, or a reply is being drafted
        if conv in ctx.tracked_open_conversations or m["id"] in ctx.tracker_reported_messages:
            continue  # the tracker reports this reply as `replied` (live bug: duplicate alerts)
        our_last = max(ours, key=when)
        supplier = cfg.supplier_for(frm)
        extra = {"our_last_message": text_of(our_last)[:600]}
        if supplier:
            lane = route_reply(cfg, m, "supplier_reply")
            if lane and received >= lookback_cut:
                items.append(_item("supplier_reply", lane, m, attachments=visible_attachments(client, m),
                                   our_questions=questions_in(text_of(our_last)),
                                   detail=f"{supplier.name} replied to what we asked", **extra))
            continue
        lane = route_reply(cfg, m, "customer_reply")
        if lane and received >= lookback_cut:
            items.append(_item("customer_reply", lane, m, attachments=visible_attachments(client, m), **extra))
            continue
        lane = route_reply(cfg, m, "replies_owed")
        if lane:
            t = parse_ts(received)
            bd = business_days_between(t, ctx.now) if t else 0
            if bd >= lane.owed_after_business_days and received >= iso(ctx.now - timedelta(days=lane.owed_max_days)):
                items.append(_item("replies_owed", lane, m, waiting_business_days=bd, **extra))
    return items
