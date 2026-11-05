"""Mailbox access behind one small interface (SPEC.md §4.1).

* :class:`GraphClient` — the protocol the engine codes against (high-level operations, not URLs).
* :class:`HermesGraphClient` — Microsoft Graph through Hermes's ``credentialed_provider_request``:
  Hermes owns the OAuth grant and attaches the bearer; this code never sees a token.
* :class:`FixtureGraphClient` — a JSON mailbox held in memory. Writes (drafts, sends, property
  patches) change only the in-memory copy and are appended to ``log``. Never touches the network.

There is intentionally no delete/move method: the plugin never deletes or files mail (SPEC S6).
"""
from __future__ import annotations

import copy
import json
import itertools
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
OAUTH_ID = "microsoft"  # matches plugin.yaml oauth[].id
MESSAGE_FIELDS = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,bodyPreview,receivedDateTime,"
    "sentDateTime,lastModifiedDateTime,isDraft,hasAttachments,parentFolderId,webLink"
)
WELL_KNOWN_FOLDERS = ("inbox", "sentitems", "drafts")


class GraphError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}")
        self.status = status


class NotSignedIn(RuntimeError):
    """No usable Microsoft grant; message tells the user what to run."""


class GraphClient(Protocol):
    def me(self) -> dict: ...
    def list_messages(self, folder: str, *, since: str | None = None, top: int = 100) -> list[dict]: ...
    def get_message(self, message_id: str) -> dict | None: ...
    def conversation(self, conversation_id: str) -> list[dict]: ...
    def search_sent(self, query: str, *, since: str | None = None, top: int = 15) -> list[dict]: ...
    def list_attachments(self, message_id: str) -> list[dict]: ...
    def create_draft(self, message: dict) -> dict: ...
    def create_reply_draft(self, message_id: str, *, reply_all: bool = True) -> dict: ...
    def update_message(self, message_id: str, patch: dict) -> dict: ...
    def add_attachment(self, message_id: str, attachment: dict) -> dict: ...
    def send_draft(self, message_id: str) -> None: ...
    def set_extended_properties(self, message_id: str, props: list[dict]) -> None: ...


def address_of(recipient: Any) -> str:
    return (((recipient or {}).get("emailAddress") or {}).get("address") or "").lower()


def when(message: dict) -> str:
    """Best timestamp for ordering: sent for our mail, received otherwise."""
    return message.get("sentDateTime") or message.get("receivedDateTime") or message.get("lastModifiedDateTime") or ""


# ── Microsoft Graph via Hermes ───────────────────────────────────────────────────────────────────

class HermesGraphClient:
    """Graph v1.0 using the plugin's ``oauth: microsoft`` grant. Requires Hermes >= 0.22.3."""

    def __init__(self, request=None, timeout: float = 30.0):
        self._request = request  # injectable for tests; default resolved lazily
        self._timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, headers: dict | None = None) -> Any:
        request = self._request
        if request is None:
            from hermes_cli.plugin_provider_requests import ProviderNotSignedIn, credentialed_provider_request
            try:
                resp = credentialed_provider_request(OAUTH_ID, method, GRAPH_BASE + path if path.startswith("/") else path,
                                                     json=body, headers=headers, timeout=self._timeout)
            except ProviderNotSignedIn as exc:
                raise NotSignedIn(str(exc)) from None
        else:
            resp = request(OAUTH_ID, method, GRAPH_BASE + path if path.startswith("/") else path,
                           json=body, headers=headers, timeout=self._timeout)
        if resp.status == 404 or (resp.status >= 400 and "ErrorItemNotFound" in resp.text):
            return None
        if resp.status >= 400:
            raise GraphError(resp.status, resp.text[:300])
        if resp.status in (202, 204) or not resp.body:
            return {}
        return resp.json()

    def _pages(self, path: str, limit: int) -> list[dict]:
        out: list[dict] = []
        url: str | None = path
        while url and len(out) < limit:
            page = self._call("GET", url) or {}
            out += page.get("value", [])
            url = page.get("@odata.nextLink")
        return out[:limit]

    def me(self) -> dict:
        r = self._call("GET", "/me?$select=displayName,mail,userPrincipalName") or {}
        return {"address": (r.get("mail") or r.get("userPrincipalName") or "").lower(), "name": r.get("displayName") or ""}

    def list_messages(self, folder: str, *, since: str | None = None, top: int = 100) -> list[dict]:
        order = "sentDateTime" if folder == "sentitems" else "receivedDateTime"
        q = f"/me/mailFolders/{quote(folder)}/messages?$top={min(top, 200)}&$orderby={order} desc&$select={MESSAGE_FIELDS}"
        if since:
            q += f"&$filter={order} ge {since}"
        return self._pages(q, top)

    def get_message(self, message_id: str) -> dict | None:
        return self._call("GET", f"/me/messages/{quote(message_id)}?$select={MESSAGE_FIELDS},uniqueBody,body")

    def conversation(self, conversation_id: str) -> list[dict]:
        flt = quote(f"conversationId eq '{conversation_id}'", safe="")
        msgs = self._pages(f"/me/messages?$top=50&$select={MESSAGE_FIELDS},uniqueBody&$filter={flt}", 200)
        return sorted(msgs, key=when)

    def search_sent(self, query: str, *, since: str | None = None, top: int = 15) -> list[dict]:
        q = quote(f'"{query}"', safe="")
        msgs = self._call("GET", f"/me/mailFolders/sentitems/messages?$top={top}&$search={q}"
                                 f"&$select={MESSAGE_FIELDS},uniqueBody",
                          headers={"ConsistencyLevel": "eventual"}) or {}
        rows = msgs.get("value", [])
        return [m for m in rows if not since or when(m) >= since]

    def list_attachments(self, message_id: str) -> list[dict]:
        return (self._call("GET", f"/me/messages/{quote(message_id)}/attachments") or {}).get("value", [])

    def create_draft(self, message: dict) -> dict:
        return self._call("POST", "/me/messages", message)

    def create_reply_draft(self, message_id: str, *, reply_all: bool = True) -> dict:
        action = "createReplyAll" if reply_all else "createReply"
        return self._call("POST", f"/me/messages/{quote(message_id)}/{action}", {})

    def update_message(self, message_id: str, patch: dict) -> dict:
        return self._call("PATCH", f"/me/messages/{quote(message_id)}", patch)

    def add_attachment(self, message_id: str, attachment: dict) -> dict:
        payload = {"@odata.type": "#microsoft.graph.fileAttachment", **attachment}
        return self._call("POST", f"/me/messages/{quote(message_id)}/attachments", payload)

    def send_draft(self, message_id: str) -> None:
        self._call("POST", f"/me/messages/{quote(message_id)}/send", {})

    def set_extended_properties(self, message_id: str, props: list[dict]) -> None:
        self._call("PATCH", f"/me/messages/{quote(message_id)}", {"singleValueExtendedProperties": props})


# ── Offline fixture mailbox ──────────────────────────────────────────────────────────────────────

class FixtureGraphClient:
    """In-memory mailbox loaded from ``fixtures/<name>/mailbox.json`` (SPEC.md §4.2).

    ``now`` in the fixture pins the clock for deterministic detection. Every write is recorded in
    ``self.log`` as ``(operation, message_id, detail)`` so tests can assert, e.g., that nothing was
    sent. ``allow_send=False`` makes ``send_draft`` raise, for tests that must prove no send happens.
    """

    def __init__(self, data: dict, *, allow_send: bool = True):
        self._data = copy.deepcopy(data)
        self.me_info = dict(self._data.get("me") or {"address": "me@demo.example", "name": "Demo User"})
        self.me_info["address"] = self.me_info["address"].lower()
        self.now = self._data.get("now") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.messages: dict[str, dict] = {}
        for m in self._data.get("messages", []):
            m = dict(m)
            m.setdefault("isDraft", m.get("parentFolder") == "drafts")
            m.setdefault("hasAttachments", bool(m.get("attachments")))
            self.messages[m["id"]] = m
        self.log: list[tuple[str, str, Any]] = []
        self.allow_send = allow_send
        self._ids = itertools.count(1)

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "FixtureGraphClient":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), **kw)

    # helpers
    @staticmethod
    def _public(m: dict) -> dict:
        out = {k: copy.deepcopy(v) for k, v in m.items() if k != "attachments"}
        if "uniqueBody" not in out and "body" in out:
            out["uniqueBody"] = copy.deepcopy(out["body"])
        return out

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):04d}"

    # reads
    def me(self) -> dict:
        return dict(self.me_info)

    def list_messages(self, folder: str, *, since: str | None = None, top: int = 100) -> list[dict]:
        rows = [m for m in self.messages.values() if m.get("parentFolder") == folder]
        if since:
            rows = [m for m in rows if when(m) >= since]
        rows.sort(key=when, reverse=True)
        return [self._public(m) for m in rows[:top]]

    def get_message(self, message_id: str) -> dict | None:
        m = self.messages.get(message_id)
        return self._public(m) if m else None

    def conversation(self, conversation_id: str) -> list[dict]:
        rows = [m for m in self.messages.values() if m.get("conversationId") == conversation_id]
        return [self._public(m) for m in sorted(rows, key=when)]

    def search_sent(self, query: str, *, since: str | None = None, top: int = 15) -> list[dict]:
        words = [w.lower() for w in query.split() if w]
        rows = []
        for m in self.list_messages("sentitems", since=since, top=10_000):
            text = f"{m.get('subject', '')} {(m.get('body') or {}).get('content', '')}".lower()
            if all(w in text for w in words):
                rows.append(m)
        return rows[:top]

    def list_attachments(self, message_id: str) -> list[dict]:
        m = self.messages.get(message_id)
        return copy.deepcopy((m or {}).get("attachments", []))

    # writes (in memory only)
    def create_draft(self, message: dict) -> dict:
        mid = self._new_id("draft")
        m = {**copy.deepcopy(message), "id": mid, "conversationId": f"conv-{mid}", "isDraft": True,
             "parentFolder": "drafts", "from": {"emailAddress": dict(self.me_info)},
             "createdDateTime": self.now, "lastModifiedDateTime": self.now}
        self.messages[mid] = m
        self.log.append(("create_draft", mid, message.get("subject")))
        return self._public(m)

    def create_reply_draft(self, message_id: str, *, reply_all: bool = True) -> dict:
        src = self.messages.get(message_id)
        if not src:
            raise GraphError(404, "ErrorItemNotFound")
        me = self.me_info["address"]
        to = [src["from"]] if src.get("from") else []
        cc: list = []
        if reply_all:
            others = [r for r in (src.get("toRecipients") or []) + (src.get("ccRecipients") or [])
                      if address_of(r) and address_of(r) != me and address_of(r) != address_of(src.get("from"))]
            cc = others
        subject = src.get("subject") or ""
        if not subject.lower().startswith("re:"):
            subject = "RE: " + subject
        quoted = (src.get("body") or {}).get("content", "")
        mid = self._new_id("draft")
        m = {"id": mid, "conversationId": src.get("conversationId"), "subject": subject, "isDraft": True,
             "parentFolder": "drafts", "from": {"emailAddress": dict(self.me_info)},
             "toRecipients": copy.deepcopy(to), "ccRecipients": copy.deepcopy(cc),
             "body": {"contentType": "Text", "content": "\n\n-----Original Message-----\n" + quoted},
             "createdDateTime": self.now, "lastModifiedDateTime": self.now, "replyTo": message_id}
        self.messages[mid] = m
        self.log.append(("create_reply_all" if reply_all else "create_reply", mid, message_id))
        return self._public(m)

    def update_message(self, message_id: str, patch: dict) -> dict:
        m = self.messages.get(message_id)
        if not m:
            raise GraphError(404, "ErrorItemNotFound")
        m.update(copy.deepcopy(patch))
        m["lastModifiedDateTime"] = self.now
        self.log.append(("update", message_id, sorted(patch)))
        return self._public(m)

    def add_attachment(self, message_id: str, attachment: dict) -> dict:
        m = self.messages.get(message_id)
        if not m:
            raise GraphError(404, "ErrorItemNotFound")
        att = {"id": self._new_id("att"), "isInline": False, **copy.deepcopy(attachment)}
        m.setdefault("attachments", []).append(att)
        m["hasAttachments"] = True
        self.log.append(("add_attachment", message_id, att.get("name")))
        return {k: v for k, v in att.items() if k != "contentBytes"}

    def send_draft(self, message_id: str) -> None:
        if not self.allow_send:
            raise AssertionError(f"send_draft({message_id}) called on a no-send fixture client")
        m = self.messages.get(message_id)
        if not m or not m.get("isDraft"):
            raise GraphError(404, "ErrorItemNotFound")
        sent_id = self._new_id("sent")
        sent = {**m, "id": sent_id, "isDraft": False, "parentFolder": "sentitems", "sentDateTime": self.now}
        del self.messages[message_id]
        self.messages[sent_id] = sent
        self.log.append(("send", message_id, sent_id))

    def set_extended_properties(self, message_id: str, props: list[dict]) -> None:
        m = self.messages.get(message_id)
        if not m:
            raise GraphError(404, "ErrorItemNotFound")
        ext = m.setdefault("singleValueExtendedProperties", [])
        for p in props:
            ext[:] = [e for e in ext if e.get("id") != p.get("id")] + [dict(p)]
        self.log.append(("set_extended_properties", message_id, [p.get("id") for p in props]))

    # test conveniences
    def sends(self) -> list[tuple[str, str, Any]]:
        return [e for e in self.log if e[0] == "send"]

    def deliver(self, message: dict) -> None:
        """Simulate a new message arriving (tests / demo timeline)."""
        m = dict(message)
        m.setdefault("isDraft", False)
        self.messages[m["id"]] = m
