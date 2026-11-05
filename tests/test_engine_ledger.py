"""Tracker: sent/replied/overdue/discarded, reply icon, stale pending drafts, send-only-after-go."""
from __future__ import annotations

import json

from inbox_chief import drafts, ledger
from inbox_chief.drafts import Settings
from inbox_chief.state import MemoryState, Store

from mailbox_builder import DANA, LEE, ME, SAM, Box


def _setup(now="2026-11-06T15:00:00Z"):
    b = Box(now=now)
    m = b.inbound("c-q", DANA, "Spec question", "2026-11-06T14:00:00Z", "Which elastomer for 80C?", cc=(LEE,))
    return b, b.client(), Store(MemoryState()), m


def test_send_sets_reply_all_icon_on_answered_message(cfg):
    b, client, store, m = _setup()
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="EPDM is fine to 120C.",
                           session_id="s")
    assert r["reply_all"] and store.ledger()[0]["status"] == "draft"
    out = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=r["staged"]["draft_id"],
                      session_id="s", unattended=False)
    assert out["sent"] and out["reply_icon_set"] and out["tracking"] == "sent"
    props = {p["id"]: p["value"] for p in client.messages[m]["singleValueExtendedProperties"]}
    assert props == {ledger.PROP_VERB: "103", ledger.PROP_VERB_TIME: "2026-11-06T15:00:00Z"}
    ev = ledger.events(store)
    assert [e["kind"] for e in ev] == ["sent"]


def test_sender_only_reply_sets_reply_icon_102(cfg):
    b, client, store, m = _setup()
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="Private note.", sender_only=True)
    assert r["reply_all"] is False
    drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=r["staged"]["draft_id"],
                session_id="", unattended=False)
    props = {p["id"]: p["value"] for p in client.messages[m]["singleValueExtendedProperties"]}
    assert props[ledger.PROP_VERB] == "102"


def test_reply_all_default_includes_cc(cfg):
    b, client, store, m = _setup()
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")
    assert set(r["staged"]["to"]) == {DANA[0], LEE[0]}
    assert ("create_reply_all", r["staged"]["draft_id"], m) in client.log


def test_user_sends_manually_pending_pruned_and_tracker_advanced(cfg):
    """Live bug: stale pending drafts after the user sent from Outlook."""
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")["staged"]["draft_id"]
    client.send_draft(did)  # user hits Send in Outlook
    assert drafts.prune(client=client, store=store, cfg=cfg) == 1
    assert store.pending() == []
    it = store.ledger()[0]
    assert it["status"] == "sent" and it["icon_set"]


def test_send_of_already_sent_draft_refused_and_pruned(cfg):
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")["staged"]["draft_id"]
    client.send_draft(did)
    out = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="",
                      unattended=False)
    assert not out["sent"] and "no longer in Drafts" in out["error"]
    assert len(client.sends()) == 1 and store.pending() == []


def test_replied_then_overdue_lifecycle(cfg):
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")["staged"]["draft_id"]
    drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="", unattended=False)
    it = store.ledger()[0]
    assert it["due_at"] == "2026-11-11T15:00:00Z"  # Fri + 3 business days = Wed
    client.now = "2026-11-11T16:00:00Z"
    ledger.poll(cfg=cfg, client=client, store=store)
    assert [e["kind"] for e in ledger.events(store)] == ["sent", "overdue"]
    ledger.poll(cfg=cfg, client=client, store=store)  # overdue fires once
    assert [e["kind"] for e in ledger.events(store)] == ["sent", "overdue"]
    client.deliver({"id": "r1", "parentFolder": "inbox", "conversationId": "c-q", "subject": "RE: Spec question",
                    "from": {"emailAddress": {"address": DANA[0]}}, "receivedDateTime": "2026-11-11T17:00:00Z"})
    ledger.poll(cfg=cfg, client=client, store=store)
    it = store.ledger()[0]
    assert it["status"] == "replied" and it["reply_id"] == "r1" and it["reply_from"] == DANA[0]


def test_deleted_draft_discarded_after_two_days(cfg):
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")["staged"]["draft_id"]
    del client.messages[did]
    ledger.poll(cfg=cfg, client=client, store=store)
    assert store.ledger()[0]["status"] == "draft"
    client.now = "2026-11-09T16:00:00Z"
    ledger.poll(cfg=cfg, client=client, store=store)
    assert store.ledger()[0]["status"] == "discarded"


def test_no_reply_needed_closes_on_send(cfg):
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="FYI", reply_needed=False,
                             )["staged"]["draft_id"]
    drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="", unattended=False)
    assert store.ledger()[0]["status"] == "closed"
    assert ledger.open_conversations(store) == frozenset()


def test_resolve_marks_dropped_and_keeps_outlook_draft(cfg):
    b, client, store, m = _setup()
    did = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x")["staged"]["draft_id"]
    assert drafts.resolve(store=store, draft_id=did, outcome="dropped")["ok"]
    assert store.ledger()[0]["status"] == "dropped" and client.get_message(did)


def test_signature_appended_without_double_signoff(cfg):
    b, client, store, m = _setup()
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="EPDM works.\n\nThanks,\nPat",
                           signature="Thanks,\nPat Rivera\nNorthwind Process Supply")
    body = client.get_message(r["staged"]["draft_id"])["body"]["content"]
    assert body.startswith("EPDM works.\n\nThanks,\nPat Rivera\nNorthwind")
    assert body.count("Thanks,") == 1


def test_nothing_is_sent_without_explicit_send(cfg):
    """Drafting, scanning, polling and context never send (S1)."""
    from inbox_chief import context, scan
    from inbox_chief.engine import Engine
    b, _, store, m = _setup()
    client = b.client(allow_send=False)
    drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body="x", session_id="s")
    scan.run(cfg=cfg, client=client, store=store, owner="default")
    ledger.poll(cfg=cfg, client=client, store=store)
    context.build(Engine(Settings(), cfg, client, store, "fixture"), owner="default", session_id="s",
                  user_message="go")
    assert client.sends() == []
