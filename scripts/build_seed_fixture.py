"""Regenerate fixtures/demo/mailbox.json (the small seed mailbox used by tests and the offline demo).

Fully fictional: people, companies and domains are made up and use the reserved .example TLD.
The demo-dataset card replaces this with a larger 2-3 week timeline built on the same format
(SPEC.md §4.2). Usage: python scripts/build_seed_fixture.py
"""
import base64
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "mailbox.json"


def who(addr, name):
    return {"emailAddress": {"address": addr, "name": name}}


def b64(s):
    return base64.b64encode(s.encode()).decode()


ME = who("pat@northwind-demo.example", "Pat Rivera")
CUST = who("dana.okafor@lakeshorecreamery.example", "Dana Okafor")
CUST2 = who("lee.marsh@lakeshorecreamery.example", "Lee Marsh")
SUP = who("ravi.lund@kestrelpumps.example", "Ravi Lund")
PRAIRIE = who("sam.ito@prairiebotanicals.example", "Sam Ito")

MESSAGES = [
    {"id": "m001", "parentFolder": "inbox", "conversationId": "c-lakeshore-pump",
     "subject": "Cut sheet for the KP-450 transfer pump?", "from": CUST, "toRecipients": [ME], "ccRecipients": [CUST2],
     "receivedDateTime": "2026-11-06T14:05:00Z",
     "bodyPreview": "Hi Pat, could you send the cut sheet for the Kestrel KP-450 we discussed for line 3?",
     "body": {"contentType": "Text", "content": "Hi Pat,\nCould you send the cut sheet for the Kestrel KP-450 we "
                                                "discussed for line 3? Lee is copied.\nThanks, Dana"}},
    {"id": "m002", "parentFolder": "sentitems", "conversationId": "c-kestrel-ask",
     "subject": "KP-450 cut sheet + net pricing", "from": ME, "toRecipients": [SUP],
     "sentDateTime": "2026-11-06T15:10:00Z", "bodyPreview": "Ravi, can you send the KP-450 cut sheet and our net price?",
     "body": {"contentType": "Text", "content": "Ravi,\nCan you send the KP-450 cut sheet and our net price for qty 2?\nThanks, Pat"}},
    {"id": "m003", "parentFolder": "inbox", "conversationId": "c-kestrel-ask",
     "subject": "RE: KP-450 cut sheet + net pricing", "from": SUP, "toRecipients": [ME],
     "receivedDateTime": "2026-11-07T16:20:00Z", "hasAttachments": True,
     "bodyPreview": "Pat, cut sheet attached. Your net price is $4,180.00 each, quote Q-77812.",
     "body": {"contentType": "Text", "content": "Pat,\nCut sheet attached. Your net price is $4,180.00 each, "
                                                "quote Q-77812, 3 weeks ARO.\nRavi"},
     "attachments": [
         {"name": "KP-450-cut-sheet.pdf", "contentType": "application/pdf", "size": 120000, "isInline": False,
          "contentBytes": b64("FIXTURE: fictional KP-450 cut sheet")},
         {"name": "Quote-Q-77812.pdf", "contentType": "application/pdf", "size": 80000, "isInline": False,
          "contentBytes": b64("FIXTURE: fictional quote Q-77812")},
         {"name": "image001.png", "contentType": "image/png", "size": 4000, "isInline": True,
          "contentBytes": b64("FIXTURE: signature logo")}]},
    {"id": "m004", "parentFolder": "drafts", "conversationId": "c-prairie-samples", "isDraft": True,
     "subject": "Following up on samples", "from": ME, "toRecipients": [PRAIRIE],
     "lastModifiedDateTime": "2026-11-05T10:00:00Z",
     "body": {"contentType": "Text", "content": "Hi Sam, just checking the samples arrived."}},
]


def main():
    data = {"_comment": "Fictional seed mailbox for tests and the offline demo. All people, companies and domains "
                        "are made up (.example). Regenerate with scripts/build_seed_fixture.py.",
            "me": {"address": "pat@northwind-demo.example", "name": "Pat Rivera"},
            "now": "2026-11-07T18:00:00Z", "messages": MESSAGES}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(MESSAGES)} messages)")


if __name__ == "__main__":
    main()
