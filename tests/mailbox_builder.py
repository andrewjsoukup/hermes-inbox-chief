"""Tiny builder for scenario mailboxes used by the engine tests (fictional, .example domains only)."""
from __future__ import annotations

import base64
import itertools

ME = ("pat@northwind-demo.example", "Pat Rivera")
COLLEAGUE = ("jo@northwind-demo.example", "Jo Park")
DANA = ("dana.okafor@lakeshorecreamery.example", "Dana Okafor")
LEE = ("lee.marsh@lakeshorecreamery.example", "Lee Marsh")
SAM = ("sam.ito@prairiebotanicals.example", "Sam Ito")
RAVI = ("ravi.lund@kestrelpumps.example", "Ravi Lund")          # Kestrel Pump Works (parent Harbor Flow Group)
BLUE = ("mo.chen@bluefinvalve.example", "Mo Chen")               # Bluefin Valve Co (parent Harbor Flow Group)
GRANITE = ("ali.ruiz@granite-omalley.example", "Ali Ruiz")       # Granite & O'Malley Seals
NEWCO = ("kim.holt@orchardjuice.example", "Kim Holt")            # unknown customer
NOREPLY = ("no-reply@saas-notify.example", "SaaS")

NOW = "2026-11-10T15:00:00Z"  # Tuesday


def addr(p):
    return {"emailAddress": {"address": p[0], "name": p[1]}}


class Box:
    def __init__(self, now: str = NOW):
        self.now = now
        self.messages: list[dict] = []
        self._n = itertools.count(1)

    def _id(self):
        return f"x{next(self._n):03d}"

    def inbound(self, conv, frm, subject, at, body="", to=(ME,), cc=(), attachments=None, mid=None) -> str:
        mid = mid or self._id()
        m = {"id": mid, "parentFolder": "inbox", "conversationId": conv, "subject": subject, "from": addr(frm),
             "toRecipients": [addr(t) for t in to], "ccRecipients": [addr(c) for c in cc],
             "receivedDateTime": at, "bodyPreview": body[:250], "body": {"contentType": "Text", "content": body}}
        if attachments:
            m["attachments"] = [{"name": n, "contentType": "application/pdf", "size": 90_000, "isInline": False,
                                 "contentBytes": base64.b64encode(f"FIXTURE {n}".encode()).decode()} for n in attachments]
            m["hasAttachments"] = True
        self.messages.append(m)
        return mid

    def sent(self, conv, to, subject, at, body="", frm=ME, cc=(), mid=None) -> str:
        mid = mid or self._id()
        self.messages.append({"id": mid, "parentFolder": "sentitems", "conversationId": conv, "subject": subject,
                              "from": addr(frm), "toRecipients": [addr(t) for t in to],
                              "ccRecipients": [addr(c) for c in cc], "sentDateTime": at,
                              "receivedDateTime": at, "bodyPreview": body[:250],
                              "body": {"contentType": "Text", "content": body}})
        return mid

    def draft(self, conv, to, subject, body="", mid=None) -> str:
        mid = mid or self._id()
        self.messages.append({"id": mid, "parentFolder": "drafts", "conversationId": conv, "subject": subject,
                              "from": addr(ME), "toRecipients": [addr(t) for t in to], "isDraft": True,
                              "lastModifiedDateTime": self.now, "body": {"contentType": "Text", "content": body}})
        return mid

    def data(self) -> dict:
        return {"me": {"address": ME[0], "name": ME[1]}, "now": self.now, "messages": self.messages}

    def client(self, **kw):
        from inbox_chief.graph import FixtureGraphClient
        return FixtureGraphClient(self.data(), **kw)
