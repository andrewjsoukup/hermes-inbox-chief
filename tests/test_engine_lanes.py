"""Lanes, detections, ownership and dedupe — including every live bug (SPEC.md §9)."""
from __future__ import annotations

import pytest

from inbox_chief import drafts, scan
from inbox_chief.config import parse
from inbox_chief.drafts import Settings
from inbox_chief.lanes import classify, dedupe_key, is_noise
from inbox_chief.state import MemoryState, Store

from mailbox_builder import BLUE, COLLEAGUE, DANA, GRANITE, LEE, ME, NEWCO, NOREPLY, RAVI, SAM, Box


def _scan(cfg, client, store, owner="default", **kw):
    return scan.run(cfg=cfg, client=client, store=store, owner=owner, **kw)


def _items(result):
    return [i for g in result.get("lanes", {}).values() for i in g["items"]]


def _kinds(result):
    return sorted((i["kind"], i["message_id"]) for i in _items(result))


# ── routing / noise ──────────────────────────────────────────────────────────────────────────────

def test_new_item_routed_to_first_matching_lane(cfg):
    b = Box()
    mid = b.inbound("c-new", NEWCO, "Quote request: 2 impeller kits", "2026-11-10T13:00:00Z",
                    "Hi, please quote 2 impeller kits and lead time. Also our invoice address changed.")
    r = _scan(cfg, b.client(), Store(MemoryState()))
    (item,) = _items(r)
    # matches quotes (priority 10) AND accounting (20): one owner, the first by priority
    assert item["kind"] == "new" and item["lane"] == "quotes" and item["message_id"] == mid


def test_noise_and_auto_replies_are_ignored(cfg):
    b = Box()
    b.inbound("c-n1", NOREPLY, "Your quote export is ready", "2026-11-10T13:00:00Z", "quote")
    b.inbound("c-n2", NEWCO, "Automatic reply: Quote request", "2026-11-10T13:05:00Z", "I am out of office")
    assert _items(_scan(cfg, b.client(), Store(MemoryState()))) == []
    assert is_noise(cfg, {"from": {"emailAddress": {"address": "billing@cloudhost-billing.example"}}, "subject": "x"})


def test_supplier_quote_is_not_a_new_rfq(cfg):
    """exclude_from keeps a supplier's unsolicited quote out of the quotes lane's `new` items."""
    b = Box()
    b.inbound("c-sup-new", RAVI, "Updated price list for 2027", "2026-11-10T13:00:00Z", "New pricing attached")
    assert _items(_scan(cfg, b.client(), Store(MemoryState()))) == []


def test_handled_threads_are_not_new(cfg):
    b = Box()
    b.inbound("c-h1", NEWCO, "Quote request: seals", "2026-11-10T10:00:00Z", "please quote seals")
    b.sent("c-h1", [NEWCO], "RE: Quote request: seals", "2026-11-10T11:00:00Z", "On it", frm=COLLEAGUE)
    b.inbound("c-h2", NEWCO, "Quote request: gaskets", "2026-11-10T10:00:00Z", "please quote gaskets")
    b.draft("c-h2", [NEWCO], "RE: Quote request: gaskets")
    assert _items(_scan(cfg, b.client(), Store(MemoryState()))) == []


def test_old_new_items_outside_lookback_are_skipped(cfg):
    b = Box()
    b.inbound("c-old", NEWCO, "Quote request: seals", "2026-11-05T10:00:00Z", "please quote seals")
    assert _items(_scan(cfg, b.client(), Store(MemoryState()), lookback_days=2)) == []


# ── live bug: missed customer replies on threads we started ourselves ───────────────────────────

def test_customer_reply_on_self_started_thread_same_day(cfg):
    b = Box()
    b.sent("c-samples", [SAM], "Samples for the new line", "2026-11-10T09:00:00Z",
           "Sam, want us to send two samples for the new line?")
    mid = b.inbound("c-samples", SAM, "RE: Samples for the new line", "2026-11-10T14:00:00Z",
                    "Yes please send two to the plant.")
    (item,) = _items(_scan(cfg, b.client(), Store(MemoryState())))
    assert item["kind"] == "customer_reply" and item["message_id"] == mid and item["lane"] == "sales"
    assert "two samples" in item["our_last_message"]


def test_customer_reply_with_no_lane_keywords_is_still_reported(cfg):
    b = Box()
    b.sent("c-chat", [SAM], "Tuesday", "2026-11-10T09:00:00Z", "Does Tuesday work?")
    b.inbound("c-chat", SAM, "RE: Tuesday", "2026-11-10T14:00:00Z", "Tuesday works.")
    (item,) = _items(_scan(cfg, b.client(), Store(MemoryState())))
    assert item["kind"] == "customer_reply"


def test_no_customer_reply_when_we_answered_or_are_drafting(cfg):
    b = Box()
    b.sent("c-a", [SAM], "Samples", "2026-11-10T09:00:00Z", "samples?")
    b.inbound("c-a", SAM, "RE: Samples", "2026-11-10T10:00:00Z", "yes")
    b.sent("c-a", [SAM], "RE: Samples", "2026-11-10T11:00:00Z", "shipping today")
    b.sent("c-b", [DANA], "Spec sheet", "2026-11-10T09:00:00Z", "spec?")
    b.inbound("c-b", DANA, "RE: Spec sheet", "2026-11-10T10:00:00Z", "thanks")
    b.draft("c-b", [DANA], "RE: Spec sheet")
    assert _items(_scan(cfg, b.client(), Store(MemoryState()))) == []


# ── supplier replies ─────────────────────────────────────────────────────────────────────────────

def test_supplier_reply_lists_attachments_and_our_questions(cfg):
    b = Box()
    b.sent("c-g", [GRANITE], "FKM seal option", "2026-11-09T16:00:00Z",
           "Ali, can you confirm the FKM option for the GS-12? What is the lead time?")
    mid = b.inbound("c-g", GRANITE, "RE: FKM seal option", "2026-11-10T13:00:00Z", "FKM is fine. Drawing attached.",
                    attachments=["GS-12-drawing.pdf"])
    (item,) = _items(_scan(cfg, b.client(), Store(MemoryState())))
    assert item["kind"] == "supplier_reply" and item["message_id"] == mid and item["lane"] == "quotes"
    assert item["attachments"] == ["GS-12-drawing.pdf"]
    assert item["our_questions"] == ["Ali, can you confirm the FKM option for the GS-12?", "What is the lead time?"]


# ── replies owed ─────────────────────────────────────────────────────────────────────────────────

def test_replies_owed_after_business_days(cfg):
    b = Box()
    b.sent("c-owed", [SAM], "Line expansion proposal", "2026-11-04T15:00:00Z", "Proposal attached.")
    mid = b.inbound("c-owed", SAM, "RE: Line expansion proposal", "2026-11-05T15:00:00Z", "Questions on page 3.")
    (item,) = _items(_scan(cfg, b.client(), Store(MemoryState())))
    assert item["kind"] == "replies_owed" and item["message_id"] == mid and item["waiting_business_days"] == 3


# ── live bug: per-reply (not per-thread) dedupe ─────────────────────────────────────────────────

def test_dedupe_is_per_reply_not_per_thread(cfg):
    b = Box()
    b.sent("c-samples", [SAM], "Samples for the new line", "2026-11-10T09:00:00Z", "two samples?")
    b.inbound("c-samples", SAM, "RE: Samples for the new line", "2026-11-10T14:00:00Z", "Yes please.")
    client, store = b.client(), Store(MemoryState())
    first = _items(_scan(cfg, client, store))
    assert len(first) == 1
    assert _items(_scan(cfg, client, store)) == []  # same reply → never alerted twice
    client.now = "2026-11-10T16:00:00Z"
    client.deliver({"id": "x-second", "parentFolder": "inbox", "conversationId": "c-samples",
                    "subject": "RE: Samples for the new line", "from": {"emailAddress": {"address": SAM[0]}},
                    "receivedDateTime": "2026-11-10T15:30:00Z", "bodyPreview": "Actually make it three."})
    second = _items(_scan(cfg, client, store))
    assert [i["message_id"] for i in second] == ["x-second"]  # a second reply in the same thread is a new alert
    assert first[0]["key"] != second[0]["key"]


def test_dedupe_key_shape():
    assert dedupe_key("AAAAAAAAAAAAAAAAAAAAconv-0123456789", "customer_reply", "2026-11-10T14:00:59Z") == \
        "Aconv-0123456789|customer_reply|2026-11-10T14:00"  # last 16 chars of the conversation id


def test_mark_seen_false_does_not_consume(cfg):
    b = Box()
    b.inbound("c-new", NEWCO, "Quote request: seals", "2026-11-10T13:00:00Z", "please quote")
    client, store = b.client(), Store(MemoryState())
    assert len(_items(_scan(cfg, client, store, mark_seen=False))) == 1
    assert len(_items(_scan(cfg, client, store))) == 1


# ── live bug: duplicate alerts (tracker vs lanes; chat vs scan) ──────────────────────────────────

def _tracked_reply_setup(cfg):
    b = Box(now="2026-11-09T15:00:00Z")  # the fixture stamps sends with its pinned clock
    b.sent("c-t", [SAM], "Samples for the new line", "2026-11-09T09:00:00Z", "two samples?")
    m = b.inbound("c-t", SAM, "RE: Samples for the new line", "2026-11-09T14:00:00Z", "yes, two")
    client, store = b.client(), Store(MemoryState())
    store.mark_handled([dedupe_key("c-t", "customer_reply", "2026-11-09T14:00:00Z")])  # already worked
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="Shipping two today.",
                           session_id="s1", owner="default")
    did = r["staged"]["draft_id"]
    drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="s1",
                unattended=False)
    client.now = "2026-11-10T15:00:00Z"
    return b, client, store


def test_reply_to_tracked_draft_is_reported_once_as_replied_not_also_customer_reply(cfg):
    b, client, store = _tracked_reply_setup(cfg)
    client.deliver({"id": "x-reply", "parentFolder": "inbox", "conversationId": "c-t",
                    "subject": "RE: Samples for the new line", "from": {"emailAddress": {"address": SAM[0]}},
                    "receivedDateTime": "2026-11-10T14:00:00Z", "bodyPreview": "Got them, thanks"})
    r = _scan(cfg, client, store)
    assert _kinds(r) == [("replied", "x-reply")]
    assert _items(_scan(cfg, client, store)) == []


def test_tracker_event_seen_in_chat_is_not_rescanned(cfg):
    from inbox_chief import context
    from inbox_chief.engine import Engine
    b, client, store = _tracked_reply_setup(cfg)
    client.deliver({"id": "x-reply", "parentFolder": "inbox", "conversationId": "c-t",
                    "subject": "RE: Samples for the new line", "from": {"emailAddress": {"address": SAM[0]}},
                    "receivedDateTime": "2026-11-10T14:00:00Z", "bodyPreview": "Got them"})
    from inbox_chief import ledger
    ledger.poll(cfg=cfg, client=client, store=store)
    e = Engine(Settings(), cfg, client, store, "fixture")
    text, keys = context.build(e, owner="default", session_id="s9", user_message="hi")
    assert "replied" in text and keys
    store.mark_seen(keys)
    assert _items(_scan(cfg, client, store)) == []
    text2, keys2 = context.build(e, owner="default", session_id="s9", user_message="hi")
    assert keys2 == [] and "replied" not in text2


def test_several_tracked_drafts_in_one_thread_one_alert(cfg):
    b = Box(now="2026-11-09T15:00:00Z")
    m = b.inbound("c-two", DANA, "Spec question", "2026-11-09T14:00:00Z", "spec?", cc=(LEE,))
    client, store = b.client(), Store(MemoryState())
    store.mark_handled([dedupe_key("c-two", "new", "2026-11-09T14:00:00Z")])
    for body in ("Answer part 1", "Answer part 2"):
        r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body=body, session_id="s")
        drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=r["staged"]["draft_id"],
                    session_id="s", unattended=False)
    client.now = "2026-11-10T15:00:00Z"
    client.deliver({"id": "x-r", "parentFolder": "inbox", "conversationId": "c-two", "subject": "RE: Spec question",
                    "from": {"emailAddress": {"address": DANA[0]}}, "receivedDateTime": "2026-11-10T14:00:00Z"})
    assert _kinds(_scan(cfg, client, store)) == [("replied", "x-r")]


# ── one owner per email across profiles ─────────────────────────────────────────────────────────

TEAM = {
    "version": 1, "company": {"name": "Northwind", "internal_domains": ["northwind-demo.example"],
                              "my_addresses": ["pat@northwind-demo.example"]},
    "hours": {"timezone": "UTC", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "06:00", "end": "18:00"},
    "suppliers": [{"name": "Kestrel Pump Works", "domains": ["kestrelpumps.example"], "parent": "Harbor Flow Group"}],
    "lanes": [
        {"id": "quotes", "owner": "parts", "priority": 10, "detect": ["new", "customer_reply", "supplier_reply"],
         "match": {"text": [r"\b(quote|price|lead time)\b"]}},
        {"id": "accounting", "owner": "books", "priority": 20, "detect": ["new", "customer_reply"],
         "match": {"text": [r"\b(invoice|payment|statement)\b"]}},
        {"id": "sales", "owner": "sales", "priority": 30, "detect": ["new", "customer_reply", "replies_owed"],
         "match": {"text": [r"\b(project|samples?)\b"]}},
    ],
}


def test_one_owner_per_email_across_profiles():
    cfg = parse(TEAM)
    b = Box()
    b.inbound("c1", NEWCO, "Quote for the invoice line items", "2026-11-10T13:00:00Z", "quote and invoice")
    b.inbound("c2", SAM, "Past due invoice", "2026-11-10T13:10:00Z", "payment")
    b.inbound("c3", DANA, "New project samples", "2026-11-10T13:20:00Z", "project")
    seen = {}
    for owner in ("parts", "books", "sales", "nobody"):
        r = _scan(cfg, b.client(), Store(MemoryState()), owner=owner)
        seen[owner] = sorted(i["conversation_id"] for i in _items(r))
    assert seen == {"parts": ["c1"], "books": ["c2"], "sales": ["c3"], "nobody": []}
    assert classify(cfg, b.messages[0]).id == "quotes"


def test_lane_filter_and_unknown_kind(cfg):
    b = Box()
    b.inbound("c1", NEWCO, "Quote request: seals", "2026-11-10T13:00:00Z", "please quote")
    r = _scan(cfg, b.client(), Store(MemoryState()), lane="sales")
    assert _items(r) == []
    assert not _scan(cfg, b.client(), Store(MemoryState()), kinds=["bogus"])["ok"]
    assert not _scan(cfg, b.client(), Store(MemoryState()), lane="nope")["ok"]


# ── scheduling ───────────────────────────────────────────────────────────────────────────────────

def test_scheduled_scan_silent_outside_hours_and_when_nothing_new():
    cfg = parse(TEAM)
    sat = Box(now="2026-11-14T12:00:00Z")
    sat.inbound("c1", NEWCO, "Quote request", "2026-11-14T11:00:00Z", "quote")
    r = _scan(cfg, sat.client(), Store(MemoryState()), owner="parts", scheduled=True)
    assert r["silent"] and r["result"] == scan.SILENT
    # manual scans always run
    assert len(_items(_scan(cfg, sat.client(), Store(MemoryState()), owner="parts"))) == 1
    tue = Box()
    r = _scan(cfg, tue.client(), Store(MemoryState()), owner="parts", scheduled=True)
    assert r["silent"] and r["result"] == scan.SILENT


@pytest.mark.parametrize("now,expected", [("2026-11-10T05:59:00Z", False), ("2026-11-10T06:00:00Z", True),
                                          ("2026-11-10T18:00:00Z", True), ("2026-11-10T18:01:00Z", False)])
def test_within_hours_edges(now, expected):
    from inbox_chief.util import parse_ts
    cfg = parse(TEAM)
    assert scan.within_hours(cfg.hours, parse_ts(now)) is expected
