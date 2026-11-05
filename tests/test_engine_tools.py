"""The engine through the registered tool handlers (what the model actually calls), fixture mailbox."""
from __future__ import annotations

import json

from conftest import FIXTURE, LANES, load_plugin_package
from test_plugin import FakeCtx


def _ctx(tmp_path, **extra):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES),
                   "notes_dir": str(tmp_path), **extra})
    pkg.register(ctx)
    return ctx


def call(ctx, name, args=None, **kw):
    return json.loads(ctx.tools[name](args or {}, **kw))


def test_scan_thread_draft_go_flow(tmp_path):
    ctx = _ctx(tmp_path)
    r = call(ctx, "inbox_chief_scan")
    assert r["ok"] and r["count"] >= 1
    kinds = {(i["kind"], i["message_id"]) for g in r["lanes"].values() for i in g["items"]}
    assert ("supplier_reply", "m003") in kinds and ("new", "m001") in kinds
    assert call(ctx, "inbox_chief_scan")["count"] == 0  # no duplicates on re-scan
    assert call(ctx, "inbox_chief_scan", {"scheduled": True})["result"] in ("[SILENT]",)

    t = call(ctx, "inbox_chief_thread", {"message_id": "m003"})
    assert t["possibly_waiting_on_this"][0]["message_id"] == "m001"

    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "Cut sheet attached.",
                                             "attach_from_message_id": "m003",
                                             "attachment_names": ["KP-450-cut-sheet.pdf"]}, session_id="s1")
    assert d["ok"] and d["reply_all"]
    p = call(ctx, "inbox_chief_pending")
    assert [x["draft_id"] for x in p["pending"]] == [d["staged"]["draft_id"]] and p["checked_live"]
    st = call(ctx, "inbox_chief_status", {"live": True})
    assert st["tracked"]["draft"] == 1

    s = call(ctx, "inbox_chief_send", {"draft_id": d["staged"]["draft_id"]}, session_id="s1")
    assert s["sent"] and s["reply_icon_set"]
    assert call(ctx, "inbox_chief_pending")["pending"] == []


def test_send_refused_from_cron_even_via_tool(tmp_path):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)}, cron=object())
    pkg.register(ctx)
    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "x"})
    s = call(ctx, "inbox_chief_send", {"draft_id": d["staged"]["draft_id"]})
    assert not s["sent"] and "unattended" in s["error"]


def test_explicit_go_mode_via_hook_and_tool(tmp_path):
    ctx = _ctx(tmp_path, send_confirmation="explicit_go")
    (pre_llm,) = ctx.hooks["pre_llm_call"]
    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "x"}, session_id="s1")
    did = d["staged"]["draft_id"]
    pre_llm(session_id="s1", user_message="hmm, change the second line")
    assert not call(ctx, "inbox_chief_send", {"draft_id": did}, session_id="s1")["sent"]
    pre_llm(session_id="s1", user_message="go")
    assert call(ctx, "inbox_chief_send", {"draft_id": did}, session_id="s1")["sent"]


def test_notes_tool(tmp_path):
    ctx = _ctx(tmp_path)
    assert call(ctx, "inbox_chief_notes", {"action": "add", "name": "Lakeshore Creamery",
                                           "text": "waiting on cut sheet", "aliases": "Dana Okafor"})["ok"]
    assert call(ctx, "inbox_chief_notes", {"action": "match", "text": "Dana Okafor"})["matches"] == ["Lakeshore Creamery"]
    sh = call(ctx, "inbox_chief_notes", {"action": "show", "name": "lakeshore creamery"})
    assert "waiting on cut sheet" in sh["body"] and "(default)" in sh["body"]
    assert call(ctx, "inbox_chief_notes", {"action": "list"})["notes"][0]["name"] == "Lakeshore Creamery"
    assert not call(ctx, "inbox_chief_notes", {"action": "bogus"})["ok"]
    assert not call(ctx, "inbox_chief_notes", {"action": "add", "name": "x"})["ok"]


def test_notes_off_explains(tmp_path):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)})
    pkg.register(ctx)
    r = call(ctx, "inbox_chief_notes", {"action": "list"})
    assert not r["ok"] and "notes_dir" in r["error"]


def test_slash_inbox(tmp_path):
    ctx = _ctx(tmp_path)
    out = ctx.commands["inbox"]("")
    assert "Inbox Chief" in out and "Drafts awaiting your go: 0" in out
