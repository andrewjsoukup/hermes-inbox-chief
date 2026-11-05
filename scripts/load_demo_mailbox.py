"""Load the fictional Northwind demo mailbox into a real **test** Microsoft 365 / Outlook mailbox.

Never sends mail. Never deletes or moves anything. Default mode is a dry run that makes no network
calls at all; ``--apply`` is required to write.

How messages are created (Microsoft Graph, delegated, through Hermes):
* ``POST /me/mailFolders/{inbox|sentitems|drafts}/messages`` with the fixture's from/to/cc, subject,
  body and file attachments (inline in the create payload).
* Inbox / Sent items are created as normal (non-draft) items by setting ``PR_MESSAGE_FLAGS``
  (0x0E07) at creation; ``PR_CLIENT_SUBMIT_TIME`` (0x0039) and ``PR_MESSAGE_DELIVERY_TIME`` (0x0E06)
  back-date them to the fixture timeline.
* Threads: every message of a fixture conversation gets the same ``PR_CONVERSATION_TOPIC`` (0x0070)
  and a ``PR_CONVERSATION_INDEX`` (0x0071) sharing one root GUID, which is what Exchange uses to group
  a conversation. After loading, the script reads every message back and reports any fixture
  conversation that did not end up as exactly one Graph ``conversationId``.
* Every loaded message gets the Outlook category ``Inbox Chief demo`` so the demo data can be found
  (and removed by hand) later. This script has no delete function.

Safety rails:
* ``--expect-mailbox ADDRESS`` is required and must equal the signed-in mailbox (``/me``).
* Refuses a mailbox whose Inbox already has more than ``--max-existing`` (default 25) messages: a
  real, used mailbox is not a test mailbox.
* Refuses to load twice (finds messages already in the ``Inbox Chief demo`` category) unless
  ``--allow-reload``.
* Only Graph endpoints for create/read are called; the request function is wrapped so any URL
  containing ``/send``, ``/move``, ``/copy``, ``createReply`` or any DELETE raises before it is sent.

Prerequisites (once a test mailbox exists, see SPEC.md §7):
1. The plugin is installed in Hermes (``hermes plugins install …`` or a dev symlink under
   ``$HERMES_HOME/plugins/inbox-chief``) — Hermes only attaches the sign-in for code that lives in an
   installed plugin, so run THIS copy of the script from the installed plugin directory.
2. ``hermes auth add inbox-chief:microsoft`` signed in **as the test mailbox**.
3. Run with Hermes's Python, e.g.::

     <hermes-venv-python> <plugin>/scripts/load_demo_mailbox.py --expect-mailbox demo@yourtenant.example
     <hermes-venv-python> <plugin>/scripts/load_demo_mailbox.py --expect-mailbox demo@yourtenant.example --apply

Time shift: the fixture is pinned to ``now`` = Tue 10 Nov 2026 10:30 CT. By default all timestamps
are shifted by whole days so the fixture's ``now`` falls on today (same clock time); pass
``--shift-days N`` to override (0 = keep the fixture dates). A warning is printed when the shift
changes weekdays, because business-day detections (replies owed, overdue) count weekdays.
The id map (fixture id -> Graph id, conversation -> Graph conversationId) is written to
``--map-out`` (default ``private/demo-load-map.json``, git-ignored) for the demo script and tracker seed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = ROOT / "fixtures" / "northwind" / "mailbox.json"
DEFAULT_MAP = ROOT / "private" / "demo-load-map.json"
CATEGORY = "Inbox Chief demo"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
FORBIDDEN_URL_PARTS = ("/send", "/move", "/copy", "createreply", "createforward", "/forward", "/reply")

PROP_FLAGS = "Integer 0x0E07"          # PR_MESSAGE_FLAGS: 1 = read, 0 = unread; absent = draft
PROP_SUBMIT = "SystemTime 0x0039"      # PR_CLIENT_SUBMIT_TIME
PROP_DELIVERY = "SystemTime 0x0E06"    # PR_MESSAGE_DELIVERY_TIME
PROP_TOPIC = "String 0x0070"           # PR_CONVERSATION_TOPIC
PROP_INDEX = "Binary 0x0071"           # PR_CONVERSATION_INDEX
PROP_FIXTURE_ID = "String {6f1c3e5a-0d7b-4f43-9c1e-6a7d2b8e4c10} Name InboxChiefFixtureId"

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class LoadRefused(RuntimeError):
    """A safety rail stopped the load; nothing (more) was written."""


# ── pure helpers (unit-tested offline) ───────────────────────────────────────────────────────────

def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_ts(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def message_time(m: dict) -> str:
    return m.get("sentDateTime") or m.get("receivedDateTime") or m.get("lastModifiedDateTime") or ""


def auto_shift_days(fixture_now: str, today: datetime) -> int:
    return (today.astimezone(timezone.utc).date() - parse_ts(fixture_now).date()).days


def filetime(d: datetime) -> int:
    return int((d - _FILETIME_EPOCH).total_seconds() * 10_000_000)


def strip_prefixes(subject: str) -> str:
    s = subject or ""
    while True:
        low = s.lower().lstrip()
        for p in ("re:", "fw:", "fwd:", "automatic reply:"):
            if low.startswith(p):
                s = s.lstrip()[len(p):].lstrip()
                break
        else:
            return s.strip()


def conversation_root(conv_id: str, first: datetime) -> bytes:
    """22-byte PR_CONVERSATION_INDEX header: reserved 0x01, 5 bytes of FILETIME, 16-byte GUID.

    The GUID is derived from the fixture conversation id so a reload produces the same thread key.
    """
    ft = filetime(first)
    guid = uuid.UUID(bytes=hashlib.sha256(f"inbox-chief-demo:{conv_id}".encode()).digest()[:16])
    return b"\x01" + struct.pack(">Q", ft)[1:6] + guid.bytes


def conversation_child(root_time: datetime, child_time: datetime, seq: int) -> bytes:
    """5-byte child block per MS-OXOMSG §2.2.1.3 (time delta, random nibble, sequence nibble)."""
    delta = max(0, filetime(child_time) - filetime(root_time))
    if delta < (1 << 49):
        code, bits = 0, (delta >> 18) & 0x7FFFFFFF
    else:
        code, bits = 1, (delta >> 23) & 0x7FFFFFFF
    word = (code << 31) | bits
    return struct.pack(">I", word) + bytes([((seq * 7) & 0x0F) << 4 | (seq & 0x0F)])


def conversation_indexes(messages: list[dict]) -> dict[str, bytes]:
    """fixture message id -> PR_CONVERSATION_INDEX, one root per conversation, one child block per reply."""
    by_conv: dict[str, list[dict]] = {}
    for m in messages:
        by_conv.setdefault(m["conversationId"], []).append(m)
    out: dict[str, bytes] = {}
    for conv, rows in by_conv.items():
        rows = sorted(rows, key=message_time)
        root_t = parse_ts(message_time(rows[0]))
        index = conversation_root(conv, root_t)
        out[rows[0]["id"]] = index
        for n, m in enumerate(rows[1:], start=1):
            index = index + conversation_child(root_t, parse_ts(message_time(m)), n)
            out[m["id"]] = index
    return out


def build_payload(m: dict, *, index: bytes, shift: timedelta) -> tuple[str, dict]:
    """(folder, Graph create body) for one fixture message, time-shifted."""
    folder = m.get("parentFolder") or "inbox"
    if folder not in ("inbox", "sentitems", "drafts"):
        raise ValueError(f"{m['id']}: unsupported folder {folder!r}")
    t = fmt_ts(parse_ts(message_time(m)) + shift)
    props = [
        {"id": PROP_TOPIC, "value": strip_prefixes(m.get("subject", ""))},
        {"id": PROP_INDEX, "value": base64.b64encode(index).decode()},
        {"id": PROP_FIXTURE_ID, "value": m["id"]},
    ]
    if folder != "drafts":
        props += [{"id": PROP_FLAGS, "value": "1" if m.get("isRead", True) else "0"},
                  {"id": PROP_SUBMIT, "value": t}, {"id": PROP_DELIVERY, "value": t}]
    body = {
        "subject": m.get("subject", ""),
        "body": {"contentType": (m.get("body") or {}).get("contentType", "Text"),
                 "content": (m.get("body") or {}).get("content", "")},
        "from": m.get("from"), "sender": m.get("from"),
        "toRecipients": m.get("toRecipients") or [], "ccRecipients": m.get("ccRecipients") or [],
        "categories": [CATEGORY],
        "singleValueExtendedProperties": props,
    }
    if folder != "drafts":
        body["isRead"] = bool(m.get("isRead", True))
    atts = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": a["name"],
             "contentType": a.get("contentType", "application/octet-stream"),
             "isInline": bool(a.get("isInline")), "contentBytes": a["contentBytes"]}
            for a in m.get("attachments") or []]
    if atts:
        body["attachments"] = atts
    return folder, body


def plan(data: dict, shift_days: int) -> list[tuple[dict, str, dict]]:
    """[(fixture message, folder, payload)] oldest first."""
    msgs = sorted(data["messages"], key=message_time)
    idx = conversation_indexes(msgs)
    shift = timedelta(days=shift_days)
    return [(m, *build_payload(m, index=idx[m["id"]], shift=shift)) for m in msgs]


# ── Graph side ───────────────────────────────────────────────────────────────────────────────────

def guarded(request: Callable) -> Callable:
    """Wrap a Graph request function so no send/move/delete/reply call can ever leave this script."""
    def call(method: str, url: str, **kw):
        low = url.lower().split("?", 1)[0]
        if method.upper() == "DELETE" or any(p in low for p in FORBIDDEN_URL_PARTS):
            raise LoadRefused(f"refusing {method} {url}: the demo loader only creates and reads messages")
        if not url.startswith(GRAPH_BASE + "/"):
            raise LoadRefused(f"refusing non-Graph URL {url}")
        return request(method, url, **kw)
    return call


def hermes_request() -> Callable:
    """Graph via Hermes's credentialed_provider_request (the plugin's oauth: microsoft grant)."""
    sys.path.insert(0, str(ROOT))
    from inbox_chief.graph import OAUTH_ID  # noqa: E402
    from hermes_cli.plugin_provider_requests import credentialed_provider_request  # noqa: E402

    def call(method: str, url: str, *, json: Any = None, headers: dict | None = None):
        return credentialed_provider_request(OAUTH_ID, method, url, json=json, headers=headers, timeout=60)
    return call


def _json(resp) -> dict:
    if resp.status >= 400:
        raise RuntimeError(f"Graph {resp.status}: {resp.text[:300]}")
    return resp.json() if resp.body else {}


def load(data: dict, request: Callable, *, expect_mailbox: str, shift_days: int, max_existing: int = 25,
         allow_reload: bool = False, log: Callable[[str], None] = print) -> dict:
    """Create every fixture message in the signed-in test mailbox. Returns the id map."""
    req = guarded(request)
    me = _json(req("GET", f"{GRAPH_BASE}/me?$select=mail,userPrincipalName"))
    address = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    if address != expect_mailbox.lower():
        raise LoadRefused(f"signed-in mailbox is {address or '(unknown)'}, not {expect_mailbox}: nothing loaded")
    inbox = _json(req("GET", f"{GRAPH_BASE}/me/mailFolders/inbox?$select=totalItemCount"))
    if int(inbox.get("totalItemCount") or 0) > max_existing:
        raise LoadRefused(f"{address} already has {inbox['totalItemCount']} messages in the Inbox; this looks like a "
                          f"real mailbox, not a test one (raise --max-existing only if you are sure)")
    flt = f"categories/any(c:c eq '{CATEGORY}')"
    existing = _json(req("GET", f"{GRAPH_BASE}/me/messages?$top=1&$select=id&$filter={flt}"))
    if existing.get("value") and not allow_reload:
        raise LoadRefused(f"{address} already contains '{CATEGORY}' messages; pass --allow-reload to add a second copy")

    rows = plan(data, shift_days)
    ids: dict[str, str] = {}
    for m, folder, payload in rows:
        created = _json(req("POST", f"{GRAPH_BASE}/me/mailFolders/{folder}/messages", json=payload))
        ids[m["id"]] = created.get("id", "")
        log(f"  {m['id']} -> {folder:<9} {m.get('subject', '')[:70]}")

    # read back: one Graph conversation per fixture conversation, timestamps applied
    convs: dict[str, set[str]] = {}
    problems: list[str] = []
    for m, folder, payload in rows:
        got = _json(req("GET", f"{GRAPH_BASE}/me/messages/{ids[m['id']]}?$select=conversationId,isDraft,"
                               f"receivedDateTime,sentDateTime"))
        convs.setdefault(m["conversationId"], set()).add(got.get("conversationId", ""))
        if folder != "drafts" and got.get("isDraft"):
            problems.append(f"{m['id']} was stored as a draft (PR_MESSAGE_FLAGS not honoured)")
    for conv, gids in convs.items():
        if len(gids) != 1:
            problems.append(f"{conv} split into {len(gids)} Graph conversations")
    return {"mailbox": address, "shift_days": shift_days, "loaded_at": fmt_ts(datetime.now(timezone.utc)),
            "messages": ids, "conversations": {k: sorted(v)[0] if len(v) == 1 else sorted(v) for k, v in convs.items()},
            "tracker_seed": [{**t, "graph_id": ids.get(t["message_id"], "")}
                             for t in (data.get("_demo") or {}).get("tracker_seed", [])],
            "problems": problems}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load the fictional demo mailbox into a TEST Outlook mailbox (never sends).")
    ap.add_argument("--expect-mailbox", required=True, help="address of the signed-in TEST mailbox (must match /me)")
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--apply", action="store_true", help="actually write to the mailbox (default: dry run, no network)")
    ap.add_argument("--shift-days", type=int, default=None, help="shift all dates by N days (default: fixture now -> today)")
    ap.add_argument("--max-existing", type=int, default=25)
    ap.add_argument("--allow-reload", action="store_true")
    ap.add_argument("--map-out", default=str(DEFAULT_MAP))
    a = ap.parse_args(argv)

    data = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc)
    shift = a.shift_days if a.shift_days is not None else auto_shift_days(data["now"], today)
    demo_now = parse_ts(data["now"]) + timedelta(days=shift)
    print(f"fixture: {a.fixture} ({len(data['messages'])} messages)")
    print(f"shift: {shift:+d} days -> demo 'now' = {fmt_ts(demo_now)} ({demo_now:%a})")
    if shift % 7:
        print("WARNING: shift is not a whole number of weeks; weekdays move and business-day detections "
              "(replies owed, overdue) may differ from the scripted demo.")
    if demo_now > today + timedelta(minutes=5):
        print("WARNING: the newest messages would be in the future right now; load after "
              f"{fmt_ts(demo_now)} or pass --shift-days {shift - 1}.")
    rows = plan(data, shift)
    if not a.apply:
        for m, folder, payload in rows:
            stamp = next((p["value"] for p in payload["singleValueExtendedProperties"] if p["id"] == PROP_SUBMIT),
                         "(draft)")
            print(f"  {m['id']}  {folder:<9} {stamp}  {m.get('subject', '')[:60]}")
        print(f"dry run: {len(rows)} messages would be created in {a.expect_mailbox}. Re-run with --apply to load.")
        return 0
    try:
        result = load(data, hermes_request(), expect_mailbox=a.expect_mailbox, shift_days=shift,
                      max_existing=a.max_existing, allow_reload=a.allow_reload)
    except LoadRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    out = Path(a.map_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"loaded {len(result['messages'])} messages into {result['mailbox']}; id map -> {out}")
    for p in result["problems"]:
        print(f"PROBLEM: {p}")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
