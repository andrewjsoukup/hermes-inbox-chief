"""The Northwind demo mailbox and its Graph loader (offline only)."""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from inbox_chief.config import load as load_lanes
from inbox_chief.graph import FixtureGraphClient, address_of, when

ROOT = Path(__file__).resolve().parent.parent
NW = ROOT / "fixtures" / "northwind"
DATA = json.loads((NW / "mailbox.json").read_text(encoding="utf-8"))
SC = {s["id"]: s for s in DATA["_demo"]["scenarios"]}


def _loader():
    spec = importlib.util.spec_from_file_location("load_demo_mailbox", ROOT / "scripts" / "load_demo_mailbox.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def nw():
    return FixtureGraphClient(DATA)


def _msg(mid):
    return next(m for m in DATA["messages"] if m["id"] == mid)


# ── dataset integrity ────────────────────────────────────────────────────────────────────────────

def test_fixture_is_reproducible_from_generator(tmp_path):
    def norm():  # git autocrlf may rewrite line endings on checkout; compare content only
        return (NW / "mailbox.json").read_bytes().replace(b"\r\n", b"\n")
    before = norm()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_demo_dataset.py")], check=True, capture_output=True)
    assert norm() == before, "fixture is stale: rerun scripts/build_demo_dataset.py"


def test_spans_two_weeks_and_nothing_after_now():
    times = sorted(when(m) for m in DATA["messages"])
    first, last = (datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") for t in (times[0], times[-1]))
    assert (last - first).days >= 14
    assert times[-1] <= DATA["now"]
    assert len(DATA["messages"]) >= 40 and len({m["conversationId"] for m in DATA["messages"]}) >= 20


def test_unique_ids_and_graph_shape():
    ids = [m["id"] for m in DATA["messages"]]
    assert len(ids) == len(set(ids))
    for m in DATA["messages"]:
        assert m["parentFolder"] in ("inbox", "sentitems", "drafts")
        assert address_of(m["from"]) and m["subject"] and m["body"]["content"]
        assert m["isDraft"] == (m["parentFolder"] == "drafts")
        for a in m.get("attachments", []):
            base64.b64decode(a["contentBytes"], validate=True)
        if m["parentFolder"] in ("sentitems", "drafts"):
            assert address_of(m["from"]) == DATA["me"]["address"]


def test_only_fictional_example_domains_and_no_real_contact_data():
    blob = (NW / "mailbox.json").read_text(encoding="utf-8") + (NW / "lanes.yaml").read_text(encoding="utf-8")
    domains = set(re.findall(r"@([a-z0-9.-]+\.[a-z]+)", blob.lower()))
    assert domains and all(d.endswith(".example") for d in domains), domains - {d for d in domains if d.endswith(".example")}
    phones = re.findall(r"\(\d{3}\) \d{3}-\d{4}", blob)
    assert all(p.startswith("(555) 01") for p in phones)  # 555-01xx is reserved for fiction


def test_lanes_file_is_valid_and_covers_every_supplier_domain():
    cfg = load_lanes(NW / "lanes.yaml")
    sup_domains = {m["from"]["emailAddress"]["address"].split("@")[1] for m in DATA["messages"]
                   if re.search(r"kestrel|bluefin|granite", m["from"]["emailAddress"]["address"])}
    for d in sup_domains:
        assert cfg.supplier_for("x@" + d), d
    assert {s.parent for s in cfg.suppliers} >= {"Harbor Flow Group"}


def test_scenario_ids_point_at_real_messages():
    ids = {m["id"] for m in DATA["messages"]}
    for s in DATA["_demo"]["scenarios"]:
        for k, v in s.items():
            if k in ("customer_message", "our_ask", "supplier_reply", "tracked_message", "message", "reply"):
                assert v in ids, (s["id"], k, v)
            if k in ("our_messages", "replies"):
                assert set(v) <= ids
    for t in DATA["_demo"]["tracker_seed"]:
        assert _msg(t["message_id"])["parentFolder"] == "sentitems"


# ── hero scenarios are actually present in the data ──────────────────────────────────────────────

def test_h1_supplier_cut_sheet_and_customer_ask(nw):
    s = SC["H1_cut_sheet_link"]
    ask, ours, reply = _msg(s["customer_message"]), _msg(s["our_ask"]), _msg(s["supplier_reply"])
    assert "cut sheet" in ask["body"]["content"].lower() and address_of(ask["ccRecipients"][0])  # reply-all matters
    assert when(ask) < when(ours) < when(reply)
    names = [a["name"] for a in nw.list_attachments(reply["id"])]
    assert s["attachment_to_send"] in names and s["attachment_never_send"] in names
    assert "$" in reply["body"]["content"]  # pricing present -> S5 must keep it out of the customer draft
    assert ask["conversationId"] != reply["conversationId"]  # the link is cross-thread


def test_h1_safety_blocks_supplier_quote_to_customer(nw):
    from inbox_chief.safety import check_outbound
    cfg = load_lanes(NW / "lanes.yaml")
    s = SC["H1_cut_sheet_link"]
    rcpts = ["dana.okafor@lakeshorecreamery.example", "lee.marsh@lakeshorecreamery.example"]
    sup = address_of(_msg(s["supplier_reply"])["from"])
    assert check_outbound(cfg=cfg, recipients=rcpts, body_text="Cut sheet attached.",
                          attachments=[(s["attachment_to_send"], sup)]).ok
    assert not check_outbound(cfg=cfg, recipients=rcpts, body_text="Cut sheet attached.",
                              attachments=[(s["attachment_never_send"], sup)]).ok


def test_h2_customer_replies_on_self_started_thread(nw):
    s = SC["H2_reply_on_self_started_thread"]
    conv = nw.conversation(s["thread"])
    assert address_of(conv[0]["from"]) == DATA["me"]["address"]  # we started it
    inbound = [m for m in conv if m["id"] in s["replies"]]
    assert len(inbound) == 2 and inbound[-1]["id"] == conv[-1]["id"]  # customer wrote last, twice
    assert all(not m["isDraft"] for m in conv)


def test_h3_supplier_answers_two_of_three():
    s = SC["H3_supplier_half_answer"]
    reply = _msg(s["supplier_reply"])["body"]["content"]
    assert re.search(r"^1\)", reply, re.M) and re.search(r"^3\)", reply, re.M)
    assert not re.search(r"^2\)", reply, re.M) and "177.2600" not in reply
    assert "Granite and O Malley" in reply  # fuzzy alias, not the configured name


def test_h4_follow_up_is_overdue_and_unanswered(nw):
    s = SC["H4_overdue_follow_up"]
    conv = nw.conversation(s["thread"])
    assert conv[-1]["id"] == s["tracked_message"]  # nothing after our follow-up
    sent = datetime.strptime(when(conv[-1]), "%Y-%m-%dT%H:%M:%SZ")
    now = datetime.strptime(DATA["now"], "%Y-%m-%dT%H:%M:%SZ")
    weekdays = sum(1 for d in range(1, (now - sent).days + 1)
                   if (sent.toordinal() + d) % 7 not in (6, 0))  # ordinal%7: 6=Sat, 0=Sun
    assert weekdays > 3


def test_parent_company_trap_present():
    blue = next(m for m in DATA["messages"] if "bluefinvalve" in address_of(m["from"]) and m["parentFolder"] == "inbox")
    assert "Harbor Flow Group" in blue["body"]["content"]
    assert "kestrel" not in blue["body"]["content"].lower()


# ── loader (pure parts + a fake Graph; never the network) ────────────────────────────────────────

def test_loader_conversation_index_shares_root_per_thread():
    L = _loader()
    idx = L.conversation_indexes(DATA["messages"])
    by_conv = {}
    for m in DATA["messages"]:
        by_conv.setdefault(m["conversationId"], []).append(idx[m["id"]])
    for conv, blobs in by_conv.items():
        roots = {b[:22] for b in blobs}
        assert len(roots) == 1 and all(len(b) == 22 + 5 * n for n, b in enumerate(sorted(blobs, key=len)))
    assert len({b[:22] for b in idx.values()}) == len(by_conv)


def test_loader_payload_shape_and_shift():
    L = _loader()
    rows = L.plan(DATA, shift_days=7)
    assert len(rows) == len(DATA["messages"])
    m, folder, p = next(r for r in rows if r[0]["id"] == SC["H1_cut_sheet_link"]["supplier_reply"])
    assert folder == "inbox" and p["isRead"] is False and p["categories"] == [L.CATEGORY]
    props = {x["id"]: x["value"] for x in p["singleValueExtendedProperties"]}
    assert props[L.PROP_FLAGS] == "0" and props[L.PROP_SUBMIT] == "2026-11-16T19:05:00Z"
    assert props[L.PROP_TOPIC] == "KP-450 cut sheet + net pricing"
    assert [a["name"] for a in p["attachments"]][:2] == ["KP-450-cut-sheet-rev-D.pdf", "Kestrel-Quote-KQ-77812.pdf"]
    d = next(r for r in rows if r[1] == "drafts")[2]
    assert "isRead" not in d and L.PROP_FLAGS not in {x["id"] for x in d["singleValueExtendedProperties"]}


def test_loader_auto_shift_lands_on_today():
    L = _loader()
    assert L.auto_shift_days("2026-11-10T16:30:00Z", datetime(2026, 11, 17, 3, tzinfo=timezone.utc)) == 7


class _Resp:
    def __init__(self, status, payload):
        self.status, self.body = status, json.dumps(payload).encode()

    @property
    def text(self):
        return self.body.decode()

    def json(self):
        return json.loads(self.body)


class FakeGraph:
    def __init__(self, me="demo@tenant.example", inbox_count=0, existing=False):
        self.me, self.inbox_count, self.existing = me, inbox_count, existing
        self.calls, self.created = [], {}

    def __call__(self, method, url, *, json=None, headers=None):
        self.calls.append((method, url))
        if url.endswith("/me?$select=mail,userPrincipalName"):
            return _Resp(200, {"mail": self.me})
        if url.endswith("/mailFolders/inbox?$select=totalItemCount"):
            return _Resp(200, {"totalItemCount": self.inbox_count})
        if "$filter=categories" in url:
            return _Resp(200, {"value": [{"id": "x"}] if self.existing else []})
        if method == "POST":
            gid = f"G{len(self.created):03d}"
            topic = next(p["value"] for p in json["singleValueExtendedProperties"] if p["id"].endswith("0x0070"))
            self.created[gid] = {"conversationId": "CONV-" + topic, "isDraft": "isRead" not in json}
            return _Resp(201, {"id": gid})
        gid = url.split("/me/messages/")[1].split("?")[0]
        return _Resp(200, self.created[gid])


def test_loader_loads_everything_and_threads_match():
    L = _loader()
    g = FakeGraph()
    out = L.load(DATA, g, expect_mailbox="demo@tenant.example", shift_days=0, log=lambda *_: None)
    assert out["problems"] == [] and len(out["messages"]) == len(DATA["messages"])
    assert {m for m, _ in g.calls} <= {"GET", "POST"}
    assert all("/send" not in u and "/move" not in u for _, u in g.calls)
    assert {t["graph_id"] for t in out["tracker_seed"]} <= set(out["messages"].values())


@pytest.mark.parametrize("kw,needle", [
    ({"me": "owner@realcompany.example"}, "not demo@tenant.example"),
    ({"inbox_count": 400}, "real mailbox"),
    ({"existing": True}, "--allow-reload"),
])
def test_loader_refuses_wrong_or_real_mailbox(kw, needle):
    L = _loader()
    g = FakeGraph(**kw)
    with pytest.raises(L.LoadRefused, match=re.escape(needle)):
        L.load(DATA, g, expect_mailbox="demo@tenant.example", shift_days=0, log=lambda *_: None)
    assert not [c for c in g.calls if c[0] == "POST"]


@pytest.mark.parametrize("method,path", [("POST", "/me/messages/x/send"), ("DELETE", "/me/messages/x"),
                                         ("POST", "/me/messages/x/move"), ("POST", "/me/messages/x/createReplyAll")])
def test_loader_guard_blocks_send_move_delete(method, path):
    L = _loader()
    call = L.guarded(lambda *a, **k: pytest.fail("request must not be made"))
    with pytest.raises(L.LoadRefused):
        call(method, L.GRAPH_BASE + path)


@pytest.mark.parametrize("domain", ["outlook.com", "hotmail.com", "tenant.example"])
def test_loader_rewrites_persona_address_to_signed_in_mailbox(domain):
    L = _loader()
    mailbox = f"demo@{domain}"  # synthetic account; the transport is entirely offline
    g = FakeGraph(me=mailbox)
    posted = []

    def spy(method, url, *, json=None, headers=None):
        if method == "POST":
            posted.append(json)
        return g(method, url, json=json, headers=headers)

    L.load(DATA, spy, expect_mailbox=mailbox, shift_days=0, log=lambda *_: None)
    blob = json.dumps(posted)
    assert DATA["me"]["address"] not in blob and mailbox in blob
    assert DATA["me"]["address"] in json.dumps(DATA)  # input not mutated


def test_loader_dry_run_makes_no_requests(capsys, monkeypatch):
    L = _loader()
    monkeypatch.setattr(L, "hermes_request", lambda: pytest.fail("dry run must not touch Hermes/Graph"))
    assert L.main(["--expect-mailbox", "demo@tenant.example", "--shift-days", "0"]) == 0
    assert "dry run:" in capsys.readouterr().out
