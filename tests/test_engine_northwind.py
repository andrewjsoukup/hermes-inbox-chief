"""The engine against the full fictional Northwind demo mailbox (fixtures/northwind): every hero
scenario and live-bug trap listed in its ``_demo`` metadata."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from inbox_chief import drafts, ledger, linking, scan
from inbox_chief.config import load
from inbox_chief.drafts import Settings
from inbox_chief.graph import FixtureGraphClient, address_of
from inbox_chief.state import MemoryState, Store

from conftest import ROOT

NW = ROOT / "fixtures" / "northwind"
pytestmark = pytest.mark.skipif(not (NW / "mailbox.json").is_file(), reason="northwind demo dataset not present")


@pytest.fixture
def nw():
    data = json.loads((NW / "mailbox.json").read_text(encoding="utf-8"))
    cfg = load(NW / "lanes.yaml")
    client = FixtureGraphClient(data)
    store = Store(MemoryState())
    for seed in data["_demo"]["tracker_seed"]:
        ledger.track_sent(store=store, client=client, message_id=seed["message_id"], owner="default",
                          due_business_days=cfg.followup_due_business_days, reply_needed=seed["needs_reply"],
                          note=seed.get("note", ""))
    return data, cfg, client, store


def _scenario(data, sid):
    return next(s for s in data["_demo"]["scenarios"] if s["id"] == sid)


def _items(result):
    return [i for g in result.get("lanes", {}).values() for i in g["items"]]


def _scan(nw, **kw):
    _, cfg, client, store = nw
    return scan.run(cfg=cfg, client=client, store=store, owner="default", **kw)


def test_full_scan_matches_scenarios(nw):
    data = nw[0]
    items = _items(_scan(nw))
    by_msg = {i["message_id"]: i["kind"] for i in items}
    # H1 + H3: supplier replies (with attachments / questions)
    assert by_msg[_scenario(data, "H1_cut_sheet_link")["supplier_reply"]] == "supplier_reply"
    assert by_msg[_scenario(data, "H3_supplier_half_answer")["supplier_reply"]] == "supplier_reply"
    # H2: one item per thread at demo time, the newest reply
    h2 = _scenario(data, "H2_reply_on_self_started_thread")
    assert by_msg[h2["replies"][-1]] == "customer_reply" and h2["replies"][0] not in by_msg
    # H4: overdue tracked follow-up
    assert by_msg[_scenario(data, "H4_overdue_follow_up")["tracked_message"]] == "overdue"
    # replies owed
    assert by_msg[_scenario(data, "replies_owed")["message"]] == "replies_owed"
    # tracked reply: exactly one `replied`, no customer_reply
    tr = _scenario(data, "tracked_reply_no_duplicate")
    assert by_msg[tr["reply"]] == "replied"
    assert sum(1 for i in items if i["conversation_id"] == tr["thread"]) == 1
    # new RFQ from a new customer
    assert by_msg["nw045"] == "new"
    # never: noise, internal mail, personal address, already-drafted thread, supplier price blast
    for mid in ("nw005", "nw006", "nw010", "nw012", "nw015", "nw018", "nw032", "nw033", "nw034", "nw035",
                "nw037", "nw047"):
        assert mid not in by_msg, mid
    # one item per conversation
    convs = [i["conversation_id"] for i in items]
    assert len(convs) == len(set(convs))
    # re-scan: nothing repeats
    assert _items(_scan(nw)) == []


def test_h1_thread_links_and_safe_draft(nw):
    data, cfg, client, store = nw
    h1 = _scenario(data, "H1_cut_sheet_link")
    ctx = linking.thread_context(cfg=cfg, client=client, message_id=h1["supplier_reply"])
    assert ctx["possibly_waiting_on_this"][0]["conversation_id"] == h1["customer_thread"]
    blocked = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=h1["customer_message"],
                                 body="Attached.", attach_from_message_id=h1["supplier_reply"])
    assert not blocked["ok"] and "pricing" in blocked["error"]
    ok = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=h1["customer_message"],
                            body="Dana, Lee: the rev D cut sheet is attached.",
                            attach_from_message_id=h1["supplier_reply"], attachment_names=[h1["attachment_to_send"]],
                            session_id="demo")
    assert ok["ok"] and ok["reply_all"]
    names = [a["name"] for a in client.list_attachments(ok["staged"]["draft_id"])]
    assert names == [h1["attachment_to_send"]]
    assert client.sends() == []


def test_h3_fuzzy_alias_link(nw):
    data, cfg, client, store = nw
    h3 = _scenario(data, "H3_supplier_half_answer")
    lk = linking.links(cfg=cfg, client=client, message_id=h3["supplier_reply"])
    assert lk and lk[0].conversation_id == h3["customer_thread"]


def test_parent_company_trap(nw):
    data, cfg, client, store = nw
    sc = _scenario(data, "parent_company_trap")
    reply = next(m["id"] for m in data["messages"] if m["conversationId"] == sc["thread"] and m["parentFolder"] == "inbox")
    lk = linking.links(cfg=cfg, client=client, message_id=reply)
    convs = [x.conversation_id for x in lk]
    assert "c-copperline-valve" in convs
    assert not {"c-lakeshore-kp450", "c-kestrel-kp450", "c-lakeshore-startup"} & set(convs)


def test_h2_per_reply_dedupe_over_time(nw):
    """Scan before the second reply arrives, then again after: two alerts, one per reply."""
    data, cfg, _, _ = nw
    h2 = _scenario(data, "H2_reply_on_self_started_thread")
    first, second = h2["replies"]
    early = dict(data, now="2026-11-10T15:00:00Z",
                 messages=[m for m in data["messages"] if m["id"] != second])
    client = FixtureGraphClient(early)
    store = Store(MemoryState())
    for seed in data["_demo"]["tracker_seed"]:
        ledger.track_sent(store=store, client=client, message_id=seed["message_id"], owner="default",
                          due_business_days=3, reply_needed=seed["needs_reply"])
    r1 = scan.run(cfg=cfg, client=client, store=store, owner="default", kinds=["customer_reply"])
    assert [i["message_id"] for i in _items(r1)] == [first]
    client.deliver(next(m for m in data["messages"] if m["id"] == second))
    client.now = data["now"]
    r2 = scan.run(cfg=cfg, client=client, store=store, owner="default", kinds=["customer_reply"])
    assert [i["message_id"] for i in _items(r2)] == [second]
    assert scan.run(cfg=cfg, client=client, store=store, owner="default", kinds=["customer_reply"])["count"] == 0


def test_h2_reply_all_keeps_colleague_on_cc(nw):
    data, cfg, client, store = nw
    h2 = _scenario(data, "H2_reply_on_self_started_thread")
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=h2["replies"][-1], body="Thanks Riley.")
    assert "jordan.blake@northwind-demo.example" in r["staged"]["to"]
