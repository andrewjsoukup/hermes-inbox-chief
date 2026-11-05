import pytest

from inbox_chief import safety


# ── S4 reply-all default ──
def test_reply_all_is_default():
    assert safety.reply_action() == "createReplyAll"
    assert safety.reply_action(sender_only=False) == "createReplyAll"
    assert safety.reply_action(sender_only="yes") == "createReplyAll"  # only literal True opts out
    assert safety.reply_action(sender_only=True) == "createReply"


# ── S1 go detection ──
@pytest.mark.parametrize("msg", ["go", "Go!", "send it", "yes, send it", "approved", "ship it", "go on the KP-450 one"])
def test_go_phrases(msg):
    assert safety.is_go(msg)


@pytest.mark.parametrize("msg", ["", "go?", "should I send it?", "don't send", "not yet", "wait", "go ahead but change the "
                                 "greeting", "send it tomorrow", "send me the draft", "draft a reply", "no",
                                 "looks good, what about pricing?", "yes"])
def test_not_go(msg):
    assert not safety.is_go(msg)


def test_go_selects_by_id_or_subject():
    assert safety.go_selects("go on 1a2b3c", "AAMkAD...1a2b3c", "anything")
    assert safety.go_selects("send the KP-450 cut sheet one", "x", "RE: KP-450 cut sheet for line 3")
    assert not safety.go_selects("send it", "x", "RE: KP-450 cut sheet")


# ── S1/S2/S3 send gate ──
DRAFT = {"isDraft": True, "subject": "RE: hi", "toRecipients": [{"emailAddress": {"address": "a@c.example"}}],
         "body": {"content": "<p>Hello</p>"}}


def _staged(draft=DRAFT, names=()):
    return {"draft_id": "d1", "subject": draft["subject"], "fingerprint": safety.draft_fingerprint(draft, names)}


def gate(**kw):
    base = dict(mode="approval_gate", staged=_staged(), live_draft=DRAFT, live_attachment_names=(),
                unattended=False)
    base.update(kw)
    return safety.send_gate(**base)


def test_gate_allows_staged_unchanged_draft():
    assert gate().ok


def test_gate_refuses_unattended():
    v = gate(unattended=True)
    assert not v.ok and "unattended" in v.reason


def test_gate_refuses_unstaged():
    assert not gate(staged=None).ok


def test_gate_refuses_already_sent_or_deleted():
    assert not gate(live_draft=None).ok
    assert not gate(live_draft={**DRAFT, "isDraft": False}).ok


def test_gate_refuses_changed_after_staging():
    changed = {**DRAFT, "body": {"content": "<p>Hello, net price $10</p>"}}
    assert not gate(live_draft=changed).ok
    more_rcpts = {**DRAFT, "ccRecipients": [{"emailAddress": {"address": "x@other.example"}}]}
    assert not gate(live_draft=more_rcpts).ok
    assert not gate(live_attachment_names=["Quote.pdf"]).ok


def test_fingerprint_ignores_html_whitespace_noise():
    a = {**DRAFT, "body": {"content": "<div><p>Hello</p></div>\n\n"}}
    assert safety.draft_fingerprint(a) == safety.draft_fingerprint(DRAFT)


def test_explicit_go_mode():
    assert not gate(mode="explicit_go", last_user_message="looks fine?", pending_in_session=1).ok
    assert gate(mode="explicit_go", last_user_message="go", pending_in_session=1).ok
    v = gate(mode="explicit_go", last_user_message="go", pending_in_session=2)
    assert not v.ok and "which one" in v.reason
    assert gate(mode="explicit_go", last_user_message="send the hi one d1", pending_in_session=2).ok


def test_unknown_mode_refuses():
    assert not gate(mode="auto").ok


def test_is_unattended():
    assert safety.is_unattended(platform="cron", environ={})
    assert safety.is_unattended(cron_execution=object(), environ={})
    assert safety.is_unattended(parent_session_id="p", environ={})
    assert safety.is_unattended(environ={"HERMES_KANBAN_TASK": "t_1"})
    assert not safety.is_unattended(platform="telegram", environ={})


# ── S5 never forward supplier pricing ──
SUP_MSG = {"from": {"emailAddress": {"address": "ravi.lund@kestrelpumps.example"}}, "subject": "RE: KP-450",
           "body": {"content": "Your net price is $4,180.00 each, quote Q-77812."}}


def test_blocks_supplier_quote_attachment_to_customer(cfg):
    v = safety.check_outbound(cfg=cfg, recipients=["dana.okafor@lakeshorecreamery.example"], body_text="See attached.",
                              attachments=[("Quote-Q-77812.pdf", "ravi.lund@kestrelpumps.example")])
    assert not v.ok and "Quote-Q-77812.pdf" in v.findings[0]


def test_allows_supplier_cut_sheet_to_customer(cfg):
    v = safety.check_outbound(cfg=cfg, recipients=["dana.okafor@lakeshorecreamery.example"], body_text="Cut sheet attached.",
                              attachments=[("KP-450-cut-sheet.pdf", "ravi.lund@kestrelpumps.example")])
    assert v.ok


def test_blocks_restating_supplier_price(cfg):
    v = safety.check_outbound(cfg=cfg, recipients=["dana.okafor@lakeshorecreamery.example"],
                              body_text="Price is $4,180.00 each.", source_messages=[SUP_MSG])
    assert not v.ok


def test_blocks_quoting_supplier_thread_history(cfg):
    v = safety.check_outbound(cfg=cfg, recipients=["dana.okafor@lakeshorecreamery.example"], body_text="FYI below.",
                              source_messages=[dict(SUP_MSG, _included_in_draft=True)])
    assert not v.ok


def test_pricing_back_to_same_supplier_or_internal_is_fine(cfg):
    for r in ("ravi.lund@kestrelpumps.example", "ops@northwind-demo.example"):
        v = safety.check_outbound(cfg=cfg, recipients=[r], body_text="Re your $4,180.00 each",
                                  attachments=[("Quote-Q-77812.pdf", "ravi.lund@kestrelpumps.example")],
                                  source_messages=[dict(SUP_MSG, _included_in_draft=True)])
        assert v.ok, r


def test_sister_brand_under_same_parent_is_still_outside(cfg):
    # Bluefin shares Kestrel's parent company; Kestrel pricing must not go to Bluefin.
    v = safety.check_outbound(cfg=cfg, recipients=["sales@bluefinvalve.example"], body_text="x",
                              attachments=[("Quote-Q-77812.pdf", "ravi.lund@kestrelpumps.example")])
    assert not v.ok


def test_custom_pricing_attachment_pattern(cfg):
    v = safety.check_outbound(cfg=cfg, recipients=["dana.okafor@lakeshorecreamery.example"], body_text="x",
                              attachments=[("Kestrel_NPS-2026.xlsx", "ravi.lund@kestrelpumps.example")])
    assert not v.ok
