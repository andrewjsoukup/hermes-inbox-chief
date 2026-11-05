"""Generate fixtures/northwind/mailbox.json: the full demo mailbox for Inbox Chief.

A fictional distributor, "Northwind Process Supply", seen from the mailbox of its owner Pat Rivera,
over ~2 weeks (Mon 26 Oct -> Tue 10 Nov 2026). Every person, company, domain, part number, price and
quote number is invented; all domains use the reserved ``.example`` TLD.

The mailbox contains the four hero scenarios plus realistic background traffic (handled threads,
noise, accounting, internal mail) and the traps for every live bug the engine must not repeat.
Scenario -> message ids are written into the fixture under ``_demo.scenarios`` so engine tests and
the demo script can reference them; ``_demo.tracker_seed`` lists the sent mails the tracker should
already be following when the demo starts.

Usage:  python scripts/build_demo_dataset.py          (rewrites the fixture deterministically)
Format: SPEC.md §4.2 (``me``, ``now``, ``messages``); extra top-level keys are ignored by the client.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "northwind" / "mailbox.json"
NOW = "2026-11-10T16:30:00Z"  # Tue 10 Nov 2026, 10:30 America/Chicago


# ── people ───────────────────────────────────────────────────────────────────────────────────────

def who(addr: str, name: str) -> dict:
    return {"emailAddress": {"address": addr, "name": name}}


PAT = who("pat@northwind-demo.example", "Pat Rivera")              # mailbox owner
JORDAN = who("jordan.blake@northwind-demo.example", "Jordan Blake")  # inside sales
MORGAN = who("morgan.tate@northwind-demo.example", "Morgan Tate")    # accounting
PAT_HOME = who("pat.rivera.home@mail.example", "Pat Rivera")         # owner's personal address

# customers
DANA = who("dana.okafor@lakeshorecreamery.example", "Dana Okafor")
LEE = who("lee.marsh@lakeshorecreamery.example", "Lee Marsh")
LAKESHORE_AP = who("ap@lakeshorecreamery.example", "Lakeshore Creamery AP")
SAM = who("sam.ito@prairiebotanicals.example", "Sam Ito")
RILEY = who("riley.vance@cedarridgebrewing.example", "Riley Vance")
CHRIS = who("chris.delgado@harborbayfoods.example", "Chris Delgado")
QUINN = who("quinn.harlow@copperlinedistilling.example", "Quinn Harlow")
CASEY = who("casey.lindqvist@bellwetherbottling.example", "Casey Lindqvist")
ROBIN = who("robin.achterberg@ellisfermentation.example", "Robin Achterberg")

# suppliers (Kestrel and Bluefin share the parent company Harbor Flow Group)
RAVI = who("ravi.lund@kestrelpumps.example", "Ravi Lund")
KESTREL_AR = who("ar@kestrelpumps.example", "Kestrel Pump Works A/R")
KESTREL_NEWS = who("customer.care@kestrelpumps.example", "Kestrel Pump Works")
MARA = who("mara.quist@bluefinvalve.example", "Mara Quist")
TESS = who("tess.okonkwo@granite-omalley.example", "Tess Okonkwo")

# noise senders
MAG = who("news@processworld-mag.example", "ProcessWorld Weekly")
BILLING = who("no-reply@cloudhost-billing.example", "CloudHost Billing")
WEBINAR = who("events@saas-notify.example", "HygieneTech Events")
SOCIAL = who("notifications@socialnet.example", "SocialNet")


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def pdf(name: str, size: int, note: str) -> dict:
    return {"name": name, "contentType": "application/pdf", "size": size, "isInline": False,
            "contentBytes": b64(f"FIXTURE (fictional, not a real PDF): {note}")}


def logo(name: str = "image001.png") -> dict:
    return {"name": name, "contentType": "image/png", "size": 4100, "isInline": True,
            "contentBytes": b64("FIXTURE: signature logo")}


def preview(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()[:255]


MESSAGES: list[dict] = []
_seq = iter(range(1, 1000))


def add(conv: str, folder: str, subject: str, frm: dict, to: list, at: str, body: str, *,
        cc: list | None = None, attachments: list | None = None, read: bool = True, label: str = "") -> str:
    """Append one message; returns its id (``nw###``). ``at`` is UTC ``YYYY-MM-DDTHH:MM``."""
    mid = f"nw{next(_seq):03d}"
    ts = at + ":00Z"
    m: dict = {"id": mid, "parentFolder": folder, "conversationId": conv, "subject": subject,
               "from": frm, "toRecipients": to, "ccRecipients": cc or [],
               "bodyPreview": preview(body), "body": {"contentType": "Text", "content": body},
               "internetMessageId": f"<{mid}.{conv}@fixture.northwind-demo.example>"}
    if folder == "drafts":
        m.update(isDraft=True, lastModifiedDateTime=ts, createdDateTime=ts)
    else:
        m.update(isDraft=False, sentDateTime=ts, receivedDateTime=ts, isRead=read)
    if attachments:
        m["attachments"] = attachments
        m["hasAttachments"] = any(not a.get("isInline") for a in attachments)
    else:
        m["hasAttachments"] = False
    if label:
        m["_label"] = label
    MESSAGES.append(m)
    return mid


SIG_PAT = "\n\nPat Rivera\nNorthwind Process Supply\n(555) 010-2200"

# ════════════════════════════════════════════════════════════════════════════════════════════════
# Week 1 — background traffic (all handled or noise)
# ════════════════════════════════════════════════════════════════════════════════════════════════

c = "c-prairie-samples"
add(c, "inbox", "Samples arrived", SAM, [PAT], "2026-10-26T15:02",
    "Hi Pat,\nThe valve seat samples arrived this morning, thanks. Our engineer will test them on the "
    "extraction skid this week and I will come back to you with a quote request.\nSam")
add(c, "sentitems", "RE: Samples arrived", PAT, [SAM], "2026-10-26T16:10",
    "Great, glad they made it. Holler if the team needs anything else for the trial." + SIG_PAT)

c = "c-bellwether-visit"
v1 = add(c, "inbox", "Site visit for the filler line upgrade", CASEY, [PAT], "2026-10-27T14:31",
         "Hi Pat,\nWe are planning a filler line upgrade next spring and would like you to walk the line with "
         "our maintenance lead. Would a site visit in the next few weeks work?\nCasey Lindqvist\nBellwether Bottling")
v2 = add(c, "sentitems", "RE: Site visit for the filler line upgrade", PAT, [CASEY], "2026-10-27T15:05",
         "Hi Casey,\nAbsolutely. Send me two or three dates that work for your maintenance lead and I will lock "
         "one in." + SIG_PAT)

c = "c-kestrel-price-increase"
add(c, "inbox", "Kestrel Pump Works 2027 price adjustment", KESTREL_NEWS, [PAT], "2026-10-27T17:00",
    "Valued distributor,\nEffective January 1, 2027, list prices on KP-series pumps increase 4.5%. Your "
    "distributor multiplier of 0.62 is unchanged. The updated net price sheet is attached.\nKestrel Pump Works",
    attachments=[pdf("Kestrel_NPS-2027.pdf", 210000, "Kestrel net price sheet 2027")],
    label="supplier pricing announcement (must never be forwarded to a customer)")

c = "c-mag-1"
add(c, "inbox", "ProcessWorld Weekly: hygienic design trends for 2027", MAG, [PAT], "2026-10-28T11:00",
    "This week: drainable valve bodies, the new 3-A sanitary standards revision, and a look at CIP water "
    "savings. Read online | Unsubscribe", label="newsletter (noise)")

c = "c-prairie-quote"
q1 = add(c, "sentitems", "Quote NW-Q-2210: sanitary ball valves for the extraction skid", PAT, [SAM],
         "2026-10-28T20:15",
         "Hi Sam,\nAttached is quote NW-Q-2210 for the six 1.5\" sanitary ball valves with the PTFE seats you "
         "trialled. Lead time is about three weeks. Happy to walk your engineer through it." + SIG_PAT,
         attachments=[pdf("Northwind-Quote-NW-Q-2210.pdf", 95000, "Northwind quote NW-Q-2210")])

c = "c-gomalley-order"
add(c, "sentitems", "PO 55120 — 2in EPDM tri-clamp gaskets", PAT, [TESS], "2026-10-28T21:00",
    "Tess,\nPO 55120 attached for 100 pcs of the 2in EPDM tri-clamp gaskets. Please confirm ship date." + SIG_PAT,
    attachments=[pdf("Northwind-PO-55120.pdf", 40000, "Northwind PO 55120")])
add(c, "inbox", "RE: PO 55120 — 2in EPDM tri-clamp gaskets", TESS, [PAT], "2026-10-29T14:40",
    "Pat,\nConfirmed, ships Friday via ground. Tracking to follow.\nTess Okonkwo\nGranite & O'Malley Seals")

c = "c-billing-oct"
add(c, "inbox", "Your CloudHost receipt for October", BILLING, [PAT], "2026-10-29T06:12",
    "Thanks for your payment. Receipt #CH-55821. Amount charged to card ending 0000.",
    label="receipt (noise)")

c = "c-cedar-cip"
p1 = add(c, "sentitems", "Proposal: CIP spray ball upgrade for brite tanks 1-4", PAT, [RILEY], "2026-10-30T15:40",
         "Hi Riley,\nAs promised after the walk-through, attached is proposal NW-P-1043 to replace the fixed spray "
         "balls on brite tanks 1-4 with rotary spray heads. Install is about a day per two tanks and the water "
         "savings should pay it back in under a year. Happy to adjust scope.\nThanks," + SIG_PAT,
         cc=[JORDAN], attachments=[pdf("Northwind-Proposal-NW-P-1043.pdf", 180000, "Northwind proposal NW-P-1043")])
add(c, "inbox", "Automatic reply: Proposal: CIP spray ball upgrade for brite tanks 1-4", RILEY, [PAT],
    "2026-10-30T15:41",
    "I am out of the office until Monday Nov 2 with limited email. For urgent brewing issues call the "
    "brewhouse line.", label="auto-reply (noise)")

c = "c-harborbay-invoice-copy"
add(c, "inbox", "Copy of invoice NW-INV-88102?", CHRIS, [PAT], "2026-10-30T19:12",
    "Pat, our AP team cannot find invoice NW-INV-88102. Can you resend?\nChris")
add(c, "sentitems", "RE: Copy of invoice NW-INV-88102?", PAT, [CHRIS], "2026-10-30T19:30",
    "Chris, attached. Morgan on our side can help AP directly going forward." + SIG_PAT, cc=[MORGAN],
    attachments=[pdf("NW-INV-88102.pdf", 30000, "Northwind invoice NW-INV-88102")])

c = "c-social-1"
add(c, "inbox", "You appeared in 9 searches this week", SOCIAL, [PAT], "2026-10-31T13:00",
    "See who is looking at your profile. Unsubscribe from these notifications.", label="social notification (noise)")

# ════════════════════════════════════════════════════════════════════════════════════════════════
# Week 2
# ════════════════════════════════════════════════════════════════════════════════════════════════

c = "c-prairie-quote"
q2 = add(c, "sentitems", "RE: Quote NW-Q-2210: sanitary ball valves for the extraction skid", PAT, [SAM],
         "2026-11-02T16:05",
         "Hi Sam,\nFollowing up on NW-Q-2210. Did your engineer have any questions on the PTFE seats? If the "
         "timing works I can hold the current lead time through the end of next week." + SIG_PAT,
         label="H4: tracked follow-up that needs a reply; nothing comes back")

c = "c-cedar-cip"
p2 = add(c, "sentitems", "RE: Proposal: CIP spray ball upgrade for brite tanks 1-4", PAT, [RILEY],
         "2026-11-03T15:15",
         "Hi Riley, bumping this now that you are back. Any thoughts on the proposal?" + SIG_PAT, cc=[JORDAN])

c = "c-mag-2"
add(c, "inbox", "ProcessWorld Weekly: 5 CIP mistakes that waste water", MAG, [PAT], "2026-11-04T11:00",
    "This week: CIP return-line sizing, spray coverage testing and more. Read online | Unsubscribe",
    label="newsletter (noise)")

c = "c-lakeshore-startup"
s1 = add(c, "sentitems", "Line 3 start-up date?", PAT, [LEE], "2026-11-04T17:20",
         "Hi Lee,\nWhat is the current start-up date for line 3? I want to make sure the pump and spares land a "
         "week ahead." + SIG_PAT, label="tracked: a reply here is a `replied` tracker event, not a customer_reply")

c = "c-lakeshore-kp450"
h1a = add(c, "inbox", "KP-450 transfer pump for line 3 — cut sheet?", DANA, [PAT], "2026-11-05T15:12",
          "Hi Pat,\nFor the line 3 project, could you send the cut sheet for the Kestrel KP-450 transfer pump we "
          "discussed? Engineering wants to confirm the 3-A sanitary rating, the 5 HP TEFC motor and the curve at "
          "60 gpm / 40 psi before we release the PO. Lee is copied.\nThanks,\nDana Okafor\nLakeshore Creamery",
          cc=[LEE], label="H1: the customer asks for a cut sheet")
h1b = add(c, "sentitems", "RE: KP-450 transfer pump for line 3 — cut sheet?", PAT, [DANA], "2026-11-05T16:38",
          "Hi Dana,\nWill do. I am getting the current revision from Kestrel and will send it as soon as I have "
          "it." + SIG_PAT, cc=[LEE])

c = "c-kestrel-kp450"
h1c = add(c, "sentitems", "KP-450 cut sheet + net pricing", PAT, [RAVI], "2026-11-05T16:45",
          "Ravi,\nCan you send the current KP-450 cut sheet (3-A, 5 HP TEFC) and our net price for qty 2? It is "
          "for Lakeshore's line 3.\nThanks," + SIG_PAT, label="H1: we ask the supplier")

c = "c-harborbay-gaskets"
h3a = add(c, "inbox", "Gaskets for the CIP return line — 3 questions", CHRIS, [PAT], "2026-11-05T18:02",
          "Pat,\nWe are replacing the gaskets on the CIP return line. Before we order:\n"
          "1) Is EPDM OK with 2% caustic at 170F, or do we need FKM?\n"
          "2) Can you send the FDA 21 CFR 177.2600 compliance letter for the compound?\n"
          "3) What is the lead time for 40 pcs, 2in?\nThanks,\nChris Delgado\nHarbor Bay Foods",
          label="H3: the customer asks three technical questions")
add(c, "sentitems", "RE: Gaskets for the CIP return line — 3 questions", PAT, [CHRIS], "2026-11-05T18:30",
    "Chris, good questions. Checking with the manufacturer and will come back to you on all three." + SIG_PAT)

c = "c-kestrel-kp450"
add(c, "inbox", "RE: KP-450 cut sheet + net pricing", RAVI, [PAT], "2026-11-06T14:20",
    "Pat,\nEngineering is updating the cut sheet for the new seal option. Should have rev D to you early next "
    "week.\nRavi", label="H1: supplier holding reply (no attachment yet)")

c = "c-copperline-valve"
cv1 = add(c, "inbox", "3in butterfly valves for the still house", QUINN, [PAT], "2026-11-06T15:00",
          "Hi Pat,\nWe need four 3in sanitary butterfly valves with pull handles for the still house, same Bluefin "
          "BV-200 style as the ones you sold us for the mash tun. Price and lead time please?\nQuinn Harlow\n"
          "Copperline Distilling")
add(c, "sentitems", "RE: 3in butterfly valves for the still house", PAT, [QUINN], "2026-11-06T15:30",
    "Hi Quinn, on it. Checking stock with the manufacturer today." + SIG_PAT)

c = "c-bluefin-bv200"
add(c, "sentitems", "BV-200 3in butterfly valves x4 — stock?", PAT, [MARA], "2026-11-06T15:35",
    "Mara,\nDo you have four BV-200 3in butterfly valves with pull handles in stock? Need price and ship "
    "date." + SIG_PAT)

c = "c-gomalley-seals"
h3b = add(c, "sentitems", "EPDM vs FKM, FDA letter and lead time — 2in tri-clamp gaskets", PAT, [TESS],
          "2026-11-06T16:10",
          "Tess,\nThree questions on your 2in tri-clamp gaskets for a food customer's CIP return line:\n"
          "1) EPDM vs FKM with 2% caustic at 170F?\n"
          "2) Please send the FDA 21 CFR 177.2600 compliance letter for your EPDM compound.\n"
          "3) Lead time for 40 pcs?\nThanks," + SIG_PAT, label="H3: we forward the three questions to the supplier")

c = "c-kestrel-statement"
add(c, "inbox", "Statement of account — past due balance", KESTREL_AR, [PAT], "2026-11-06T20:00",
    "Hello,\nAttached is your October statement. Invoice K-30981 is 12 days past due. Please remit or let us "
    "know if there is a question.\nKestrel Pump Works A/R",
    attachments=[pdf("Kestrel-Statement-Oct-2026.pdf", 52000, "Kestrel statement")],
    label="accounting lane: supplier statement")

c = "c-bellwether-visit"
v3 = add(c, "inbox", "RE: Site visit for the filler line upgrade", CASEY, [PAT], "2026-11-06T21:15",
         "Hi Pat,\nSorry for the slow reply. Would Nov 17 or Nov 18 work? Also, could you bring the catalog for "
         "the filler valve rebuild kits?\nCasey", label="replies_owed: customer wrote last, nobody answered")

c = "c-webinar"
add(c, "inbox", "[Webinar] Hygienic design for dry processing", WEBINAR, [PAT], "2026-11-07T15:00",
    "Join us Thursday at 1pm CT. Register now.", label="webinar (noise)")

c = "c-billing-nov"
add(c, "inbox", "Your CloudHost subscription renewed", BILLING, [PAT], "2026-11-08T06:15",
    "Your subscription renewed. Receipt #CH-56010.", label="receipt (noise)")

c = "c-pat-home"
add(c, "inbox", "Fwd: dentist reschedule", PAT_HOME, [PAT], "2026-11-08T19:44",
    "Forwarding to my work email so I see it Monday.", label="owner's personal address (ignored)")

# ════════════════════════════════════════════════════════════════════════════════════════════════
# Monday 9 Nov — the answers start arriving
# ════════════════════════════════════════════════════════════════════════════════════════════════

c = "c-cedar-shutdown"
add(c, "inbox", "Cedar Ridge shutdown dates", JORDAN, [PAT], "2026-11-09T14:10",
    "Pat, Riley mentioned at the trade show that Cedar Ridge is down Nov 20-21 for maintenance. Might be a good "
    "install window if they bite on the spray ball proposal.\nJordan", label="internal mail (not a customer item)")
add(c, "sentitems", "RE: Cedar Ridge shutdown dates", PAT, [JORDAN], "2026-11-09T14:25",
    "Good to know, thanks. Let's keep Nov 20 open for the install crew.")

c = "c-copperline-valve"
add(c, "inbox", "RE: 3in butterfly valves for the still house", QUINN, [PAT], "2026-11-09T15:05",
    "Pat, while you are at it can you also price a pneumatic actuator option for two of the four?\nQuinn")
add(c, "drafts", "RE: 3in butterfly valves for the still house", PAT, [QUINN], "2026-11-09T15:40",
    "Hi Quinn,\nYes, I will include an actuator option for two of the valves in the quote. Waiting on the "
    "manufacturer's stock check." + SIG_PAT,
    label="handled: a draft already exists in this thread, so it is not a new item")

c = "c-lakeshore-ap"
add(c, "inbox", "Remittance advice — invoice NW-INV-88213", LAKESHORE_AP, [PAT], "2026-11-09T16:00",
    "Payment of invoice NW-INV-88213 was sent by ACH today. Remittance detail attached.",
    attachments=[pdf("Remittance-NW-INV-88213.pdf", 22000, "remittance advice")],
    label="accounting lane: customer remittance")

c = "c-kestrel-kp450"
h1d = add(c, "inbox", "RE: KP-450 cut sheet + net pricing", RAVI, [PAT], "2026-11-09T19:05",
          "Pat,\nRev D cut sheet attached: 3-A sanitary, 5 HP TEFC, curve covers 60 gpm at 40 psi with margin.\n"
          "Your net price is $4,180.00 each for qty 2, quote KQ-77812, 3 weeks ARO.\nRavi Lund\nKestrel Pump Works",
          attachments=[pdf("KP-450-cut-sheet-rev-D.pdf", 120000, "Kestrel KP-450 cut sheet rev D"),
                       pdf("Kestrel-Quote-KQ-77812.pdf", 80000, "Kestrel quote KQ-77812"), logo()],
          read=False, label="H1: supplier sends the cut sheet (+ a priced quote that must not be forwarded)")

c = "c-bluefin-bv200"
add(c, "inbox", "RE: BV-200 3in butterfly valves x4 — stock?", MARA, [PAT], "2026-11-09T20:10",
    "Pat,\nFour BV-200 in stock, ships in 2 days. As part of Harbor Flow Group we are moving orders to the new "
    "group portal on Dec 1, so please place this one by email.\nMara Quist\nBluefin Valve Co",
    read=False, label="parent-company trap: mentions Harbor Flow Group (Kestrel's parent) — must not link to "
                      "the Lakeshore/Kestrel threads; links to Copperline instead")

c = "c-gomalley-seals"
h3c = add(c, "inbox", "RE: EPDM vs FKM, FDA letter and lead time — 2in tri-clamp gaskets", TESS, [PAT],
          "2026-11-09T21:30",
          "Pat,\n1) EPDM is fine with 2% caustic up to 180F; FKM is not recommended in hot caustic.\n"
          "3) 40 pcs ship in 2 weeks, $6.85 each.\nChemical compatibility chart attached.\n"
          "Tess Okonkwo\nGranite and O Malley Seals",
          attachments=[pdf("GO-EPDM-chemical-chart.pdf", 64000, "chemical compatibility chart")],
          read=False, label="H3: supplier half-answers — skips question 2 (FDA letter); body has pricing")

c = "c-lakeshore-startup"
s2 = add(c, "inbox", "RE: Line 3 start-up date?", LEE, [PAT], "2026-11-09T22:10",
         "Pat, start-up moved to Dec 1. Pump on site by Nov 23 would be ideal.\nLee", read=False,
         label="reply on a TRACKED thread: one `replied` event, never also a customer_reply (duplicate-alert bug)")

# ════════════════════════════════════════════════════════════════════════════════════════════════
# Tuesday 10 Nov (demo morning)
# ════════════════════════════════════════════════════════════════════════════════════════════════

c = "c-cedar-cip"
h2a = add(c, "inbox", "RE: Proposal: CIP spray ball upgrade for brite tanks 1-4", RILEY, [PAT], "2026-11-10T14:48",
          "Hi Pat,\nSorry for the delay. We are approved for tanks 1-2 now and 3-4 after January. Can your crew "
          "install during our Nov 20-21 shutdown? PO to follow this week.\nRiley Vance\nCedar Ridge Brewing",
          cc=[JORDAN], read=False,
          label="H2: customer replies on a thread WE started (missed-reply bug) — customer_reply")

c = "c-ellis-rfq"
add(c, "inbox", "RFQ: 24x 2in EPDM tri-clamp gaskets + 6 clamps", ROBIN, [PAT], "2026-11-10T15:20",
    "Hello,\nWe are a new brewery in town. Please quote 24 pcs 2in EPDM tri-clamp gaskets and 6 heavy-duty "
    "2in clamps, delivered. Need them by Nov 24.\nRobin Achterberg\nEllis Fermentation Co.", read=False,
    label="new item, quotes lane (new customer)")

c = "c-cedar-cip"
h2b = add(c, "inbox", "RE: Proposal: CIP spray ball upgrade for brite tanks 1-4", RILEY, [PAT], "2026-11-10T15:55",
          "One more thing: can you add two spare rotary heads to the order?\nRiley", cc=[JORDAN], read=False,
          label="H2: second reply in the same thread — a second alert (per-reply dedupe)")

c = "c-credit-app"
add(c, "inbox", "Ellis Fermentation — credit application", MORGAN, [PAT], "2026-11-10T16:05",
    "Pat, if Ellis wants terms I can send our credit application today. Let me know.\nMorgan",
    label="internal mail (not a customer item)")


# ── demo metadata ────────────────────────────────────────────────────────────────────────────────

SCENARIOS = [
    {"id": "H1_cut_sheet_link", "title": "Supplier sends the cut sheet a customer asked for",
     "customer_thread": "c-lakeshore-kp450", "customer_message": h1a, "supplier_thread": "c-kestrel-kp450",
     "our_ask": h1c, "supplier_reply": h1d,
     "attachment_to_send": "KP-450-cut-sheet-rev-D.pdf", "attachment_never_send": "Kestrel-Quote-KQ-77812.pdf",
     "expect": [f"supplier_reply on {h1d} (attachments listed)",
                f"inbox_chief_thread({h1d}) -> possibly waiting on this: c-lakeshore-kp450 (KP-450 token, Lakeshore "
                f"alias, timing)",
                f"draft_reply(message_id={h1a}, attach_from_message_id={h1d}, "
                f"attachment_names=[KP-450-cut-sheet-rev-D.pdf]) -> reply-all to Dana + Lee",
                "copying all attachments or quoting the price is blocked (S5)"]},
    {"id": "H2_reply_on_self_started_thread", "title": "Customer replies on a thread we started",
     "thread": "c-cedar-cip", "our_messages": [p1, p2], "replies": [h2a, h2b],
     "expect": [f"customer_reply on {h2a} (if scanned before {h2b} arrived)",
                f"customer_reply on {h2b}: new dedupe key, so a second alert (per-reply dedupe); one scan at demo "
                f"time reports the thread once, newest message {h2b}",
                "reply-all draft keeps Jordan on cc"]},
    {"id": "H3_supplier_half_answer", "title": "Supplier answers two of three technical questions",
     "customer_thread": "c-harborbay-gaskets", "customer_message": h3a, "supplier_thread": "c-gomalley-seals",
     "our_ask": h3b, "supplier_reply": h3c, "unanswered": ["FDA 21 CFR 177.2600 compliance letter (question 2)"],
     "expect": [f"supplier_reply on {h3c} flagged: question 2 not answered",
                "links to c-harborbay-gaskets (\"Granite and O Malley\" fuzzy-matches Granite & O'Malley)",
                "draft to Chris answers 1 and 3 without the $6.85 price; follow-up draft to Tess asks for the letter"]},
    {"id": "H4_overdue_follow_up", "title": "Follow-up that never got an answer",
     "thread": "c-prairie-quote", "tracked_message": q2,
     "expect": [f"overdue: {q2} sent 2026-11-02, due after 3 business days, no reply by {NOW}",
                "agent stages a short second follow-up for go/no-go"]},
    {"id": "replies_owed", "thread": "c-bellwether-visit", "message": v3,
     "expect": [f"replies_owed on {v3} (sales lane, customer wrote last on Nov 6)"]},
    {"id": "tracked_reply_no_duplicate", "thread": "c-lakeshore-startup", "tracked_message": s1, "reply": s2,
     "expect": [f"tracker `replied` event for {s1}", "NOT also a customer_reply alert"]},
    {"id": "parent_company_trap", "thread": "c-bluefin-bv200",
     "expect": ["Bluefin reply mentions Harbor Flow Group (Kestrel's parent): no link to Lakeshore/Kestrel threads",
                "links to c-copperline-valve (BV-200 / butterfly / timing)"]},
]

TRACKER_SEED = [
    {"message_id": q1, "needs_reply": False, "note": "original quote email (FYI)"},
    {"message_id": q2, "needs_reply": True, "note": "H4 follow-up, expected overdue"},
    {"message_id": s1, "needs_reply": True, "note": "answered by Lee on Nov 9 -> replied"},
    {"message_id": p1, "needs_reply": False, "note": "proposal; replies arrive via customer_reply (H2)"},
]


def main() -> None:
    MESSAGES.sort(key=lambda m: m.get("sentDateTime") or m.get("lastModifiedDateTime"))
    data = {
        "_comment": "Fictional demo mailbox for Inbox Chief (Northwind Process Supply). Every person, company, "
                    "domain, part number, price and quote number is invented; domains use the reserved .example "
                    "TLD. Regenerate with scripts/build_demo_dataset.py; see fixtures/northwind/README.md.",
        "me": {"address": PAT["emailAddress"]["address"], "name": PAT["emailAddress"]["name"]},
        "now": NOW,
        "_demo": {"window": ["2026-10-26", "2026-11-10"], "lanes_file": "fixtures/northwind/lanes.yaml",
                  "scenarios": SCENARIOS, "tracker_seed": TRACKER_SEED},
        "messages": MESSAGES,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    convs = {m["conversationId"] for m in MESSAGES}
    print(f"wrote {OUT.relative_to(ROOT)}: {len(MESSAGES)} messages, {len(convs)} conversations")


if __name__ == "__main__":
    main()
