"""Thread context + "possibly waiting on this" linking, incl. the parent-company live bug."""
from __future__ import annotations

from inbox_chief import linking
from inbox_chief.notes import Notes

from mailbox_builder import BLUE, DANA, GRANITE, LEE, ME, NEWCO, RAVI, SAM, Box


def _hero_box():
    """Customer asks for a pump cut sheet → we ask the supplier → supplier replies with the cut sheet."""
    b = Box()
    cust = b.inbound("c-cust", DANA, "Cut sheet for the KP-450 transfer pump?", "2026-11-06T14:05:00Z",
                     "Hi Pat, could you send the cut sheet for the Kestrel KP-450 we discussed for line 3?", cc=(LEE,))
    b.sent("c-sup", [RAVI], "KP-450 cut sheet + net pricing", "2026-11-06T15:10:00Z",
           "Ravi, can you send the KP-450 cut sheet and our net price for qty 2?")
    sup = b.inbound("c-sup", RAVI, "RE: KP-450 cut sheet + net pricing", "2026-11-07T16:20:00Z",
                    "Pat, cut sheet attached. Net price $4,180.00 each, quote Q-77812.",
                    attachments=["KP-450-cut-sheet.pdf", "Quote-Q-77812.pdf"])
    # noise customer thread: unrelated
    b.inbound("c-other", SAM, "Lunch next week?", "2026-11-06T12:00:00Z", "Are you free for lunch?")
    return b, cust, sup


def test_hero_supplier_reply_links_to_waiting_customer(cfg):
    b, cust, sup = _hero_box()
    lk = linking.links(cfg=cfg, client=b.client(), message_id=sup)
    assert [x.message_id for x in lk] == [cust]
    why = " ".join(lk[0].why)
    assert "mentions Kestrel" in why and "kp450" in why and "asked shortly before" in why
    assert lk[0].score == 3 + 2 + 1


def test_thread_context_shape_and_next_step(cfg):
    b, cust, sup = _hero_box()
    ctx = linking.thread_context(cfg=cfg, client=b.client(), message_id=sup)
    assert ctx["ok"] and ctx["sender_is_supplier"] == "Kestrel Pump Works" and ctx["ball_is"] == "with us"
    assert [m["ours"] for m in ctx["messages"]] == [True, False]
    assert ctx["possibly_waiting_on_this"][0]["message_id"] == cust
    assert "attach_from_message_id" in ctx["next"]
    assert linking.thread_context(cfg=cfg, client=b.client(), message_id="nope")["ok"] is False


def test_related_threads_we_started(cfg):
    b, cust, sup = _hero_box()
    ctx = linking.thread_context(cfg=cfg, client=b.client(), message_id=cust)
    rel = ctx["related_threads_we_started"]
    assert rel and rel[0]["conversation_id"] == "c-sup"
    assert rel[0]["last_message"]["from"].startswith("Ravi Lund")  # the supplier answered → ball is with us


def test_parent_company_is_never_a_link_signal(cfg):
    """Live bug: a customer mentioning the PARENT company (or a sister brand) linked to the wrong supplier."""
    b = Box()
    b.sent("c-sup", [RAVI], "Spare seals", "2026-11-09T10:00:00Z", "Ravi, do you have spare seals in stock?")
    sup = b.inbound("c-sup", RAVI, "RE: Spare seals", "2026-11-10T10:00:00Z", "Yes, 4 in stock.")
    b.inbound("c-parent", NEWCO, "Harbor Flow Group valve question", "2026-11-09T09:00:00Z",
              "We have Harbor Flow Group valves on line 2 — who services them?")
    b.inbound("c-sister", SAM, "Bluefin actuator", "2026-11-09T09:30:00Z", "Need a Bluefin actuator.")
    lk = linking.links(cfg=cfg, client=b.client(), message_id=sup)
    assert lk == []
    kp = next(s for s in cfg.suppliers if s.name == "Kestrel Pump Works")
    assert "Harbor Flow Group" not in linking.supplier_terms(kp, RAVI[0])


def test_fuzzy_supplier_name_matching():
    assert linking.mentions("can you check with Granite and O Malley?", "Granite & O'Malley")
    assert linking.mentions("granite omalley seals quote", "Granite & O'Malley Seals")
    assert linking.mentions("waiting on Kestral pumps", "Kestrel Pumps")  # edit-tolerant
    assert not linking.mentions("granite countertops", "Granite & O'Malley Seals")
    assert linking.mentions("G&O Seals quote", "G&O Seals") and not linking.mentions("seals quote", "G&O Seals")


def test_part_tokens_ignore_years():
    assert linking.tokens("KP-450 and Q-77812 for 2026") == {"kp450", "q77812"}


def test_account_note_tie_wins_and_drops_name_only_coincidences(cfg, tmp_path):
    """A note ties the customer to the supplier contact; a name-only mention elsewhere is dropped (<5)."""
    (tmp_path / "Prairie Botanicals.md").write_text(
        "---\naliases: [prairiebotanicals.example, Ali Ruiz]\n---\n# Prairie Botanicals\n## Log\n"
        "- 2026-11-08 (pat): waiting on Ali Ruiz at G&O for the GS-12 seal drawing\n", encoding="utf-8")
    b = Box()
    b.sent("c-sup", [GRANITE], "GS seal drawing", "2026-11-09T10:00:00Z", "Ali, can you send the seal drawing?")
    sup = b.inbound("c-sup", GRANITE, "RE: GS seal drawing", "2026-11-10T10:00:00Z", "Drawing attached.",
                    attachments=["drawing.pdf"])
    tied = b.inbound("c-tied", SAM, "Seal drawing status", "2026-11-08T09:00:00Z", "Any update on our drawing?")
    b.inbound("c-coinc", NEWCO, "Granite & O'Malley catalog", "2026-11-08T09:00:00Z",
              "Do you carry Granite & O'Malley products?")
    lk = linking.links(cfg=cfg, client=b.client(), message_id=sup, notes=Notes(tmp_path))
    assert [x.message_id for x in lk] == [tied]
    assert "account note" in " ".join(lk[0].why)
    # without notes, the name-only coincidence is the (weak) link
    assert [x.conversation_id for x in linking.links(cfg=cfg, client=b.client(), message_id=sup)] == ["c-coinc"]
