"""End-to-end safety through the draft/send orchestration on the fixture mailbox."""
from inbox_chief import drafts
from inbox_chief.drafts import Settings
from inbox_chief.graph import address_of


def test_hero_cut_sheet_to_customer_thread_reply_all(client, store, cfg):
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id="m001",
                           body="Hi Dana, the KP-450 cut sheet is attached.", attach_from_message_id="m003",
                           attachment_names=["KP-450-cut-sheet.pdf"], summary="Cut sheet to Dana", session_id="s1")
    assert r["ok"], r
    assert r["reply_all"] is True
    d = client.get_message(r["staged"]["draft_id"])
    assert {address_of(x) for x in d["toRecipients"] + d["ccRecipients"]} == {
        "dana.okafor@lakeshorecreamery.example", "lee.marsh@lakeshorecreamery.example"}
    assert [a["name"] for a in client.list_attachments(d["id"])] == ["KP-450-cut-sheet.pdf"]
    assert d["body"]["content"].startswith("Hi Dana")  # new text above quoted history
    assert client.sends() == []  # drafting never sends


def test_copying_all_attachments_from_supplier_is_blocked(client, store, cfg):
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id="m001", body="Attached.",
                           attach_from_message_id="m003")  # includes Quote-Q-77812.pdf
    assert not r["ok"] and "pricing" in r["error"]
    assert not [e for e in client.log if e[0].startswith("create")]  # refused before any draft was written
    assert store.pending() == []


def test_signature_images_are_not_copied(client, store, cfg):
    r = drafts.draft_reply(client=client, store=store, cfg=cfg, message_id="m001", body="x",
                           attach_from_message_id="m003", attachment_names=["KP-450-cut-sheet.pdf", "image001.png"])
    names = [a["name"] for a in client.list_attachments(r["staged"]["draft_id"])]
    assert "image001.png" not in names


def _stage(client, store, cfg, session="s1"):
    return drafts.draft_reply(client=client, store=store, cfg=cfg, message_id="m001", body="Cut sheet attached.",
                              attach_from_message_id="m003", attachment_names=["KP-450-cut-sheet.pdf"],
                              session_id=session)["staged"]["draft_id"]


def test_send_after_staging_in_approval_mode(client, store, cfg):
    did = _stage(client, store, cfg)
    r = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="s1",
                    unattended=False)
    assert r["sent"], r
    assert len(client.sends()) == 1 and store.pending() == []


def test_send_refused_when_unattended(client, store, cfg):
    did = _stage(client, store, cfg)
    r = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="s1",
                    unattended=True)
    assert not r["sent"] and client.sends() == []


def test_send_refused_for_unstaged_draft(client, store, cfg):
    r = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id="m004", session_id="s1",
                    unattended=False)
    assert not r["sent"] and "not staged" in r["error"]


def test_send_refused_when_draft_edited_after_staging(client, store, cfg):
    did = _stage(client, store, cfg)
    client.update_message(did, {"body": {"contentType": "Text", "content": "Net price $4,180.00 each"}})
    r = drafts.send(client=client, store=store, cfg=cfg, settings=Settings(), draft_id=did, session_id="s1",
                    unattended=False)
    assert not r["sent"] and "changed" in r["error"]


def test_explicit_go_mode_needs_go_message(client, store, cfg):
    s = Settings(send_confirmation="explicit_go")
    did = _stage(client, store, cfg)
    store.record_user_message("s1", "can you tweak it?", "t")
    assert not drafts.send(client=client, store=store, cfg=cfg, settings=s, draft_id=did, session_id="s1",
                           unattended=False)["sent"]
    store.record_user_message("s1", "go", "t")
    assert drafts.send(client=client, store=store, cfg=cfg, settings=s, draft_id=did, session_id="s1",
                       unattended=False)["sent"]


def test_prune_drops_drafts_user_sent_himself(client, store, cfg):
    """Live bug: stale pending drafts after the user sent from Outlook."""
    did = _stage(client, store, cfg)
    client.send_draft(did)  # user hits Send in Outlook
    assert drafts.prune(client=client, store=store) == 1
    assert store.pending() == []


def test_resolve(client, store, cfg):
    did = _stage(client, store, cfg)
    assert drafts.resolve(store=store, draft_id=did, outcome="dropped")["ok"]
    assert store.pending() == [] and client.get_message(did)  # not deleted from Outlook
