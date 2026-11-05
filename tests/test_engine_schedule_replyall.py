"""Acceptance evidence: scheduled scanning via the public automation-blueprint surface, and reply-all default."""
from __future__ import annotations

import json

from conftest import FIXTURE, LANES, load_plugin_package
from test_plugin import FakeCtx


def _ctx(**settings):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES), **settings})
    pkg.register(ctx)
    return pkg, ctx


def test_scan_blueprint_registered_via_public_surface_and_never_sends():
    pkg, ctx = _ctx()
    bp = ctx.blueprints["scan"]  # ctx.register_automation_blueprint("scan", ...) → /blueprint inbox-chief:scan
    assert bp["schedule_template"].startswith("*/15 ")
    assert "inbox_chief_scan with scheduled=true" in bp["prompt_template"]
    assert "[SILENT]" in bp["prompt_template"] and "Never call inbox_chief_send" in bp["prompt_template"]
    assert bp["skills"] == ["inbox-chief:triage"]


def test_scheduled_run_end_to_end_reports_then_goes_silent(tmp_path):
    """What the cron job does: scan(scheduled=true) → items once → [SILENT]; send refused in the cron run."""
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)}, cron=object())
    pkg.register(ctx)
    # fixture clock (2026-11-07 18:00Z) is a Saturday; the example lanes file runs Mon-Fri → silent by hours
    r = json.loads(ctx.tools["inbox_chief_scan"]({"scheduled": True}))
    assert r["ok"] and r["silent"] and r["result"] == "[SILENT]"
    # a weekday mailbox: first scheduled run reports, second is silent
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["now"] = "2026-11-09T15:00:00Z"  # Monday 09:00 America/Chicago
    weekday = tmp_path / "weekday.json"
    try:
        weekday.write_text(json.dumps(data), encoding="utf-8")
        ctx2 = FakeCtx({"mailbox": "fixture", "fixture_path": str(weekday), "lanes_file": str(LANES)}, cron=object())
        pkg.register(ctx2)
        first = json.loads(ctx2.tools["inbox_chief_scan"]({"scheduled": True}))
        assert first["ok"] and not first["silent"] and first["count"] >= 1
        assert json.loads(ctx2.tools["inbox_chief_scan"]({"scheduled": True}))["result"] == "[SILENT]"
        d = json.loads(ctx2.tools["inbox_chief_draft_reply"]({"message_id": "m001", "body": "x"}))
        s = json.loads(ctx2.tools["inbox_chief_send"]({"draft_id": d["staged"]["draft_id"]}))
        assert not s["sent"]  # S2: a scheduled (cron) run can stage but never send
        (pre_tool,) = ctx2.hooks["pre_tool_call"]
        assert pre_tool(tool_name="inbox_chief_send", args={"draft_id": d["staged"]["draft_id"]})["action"] == "block"
    finally:
        weekday.unlink(missing_ok=True)


def test_reply_all_is_the_default_through_the_tool():
    pkg, ctx = _ctx()
    d = json.loads(ctx.tools["inbox_chief_draft_reply"]({"message_id": "m001", "body": "Cut sheet attached."}))
    assert d["ok"] and d["reply_all"] is True and d["staged"]["kind"] == "reply_all"
    # original: from Dana, cc Lee → both on the draft
    assert set(d["staged"]["to"]) == {"dana.okafor@lakeshorecreamery.example", "lee.marsh@lakeshorecreamery.example"}
    only = json.loads(ctx.tools["inbox_chief_draft_reply"]({"message_id": "m001", "body": "x", "sender_only": True}))
    assert only["reply_all"] is False and only["staged"]["to"] == ["dana.okafor@lakeshorecreamery.example"]
    # a non-boolean sender_only never downgrades reply-all
    odd = json.loads(ctx.tools["inbox_chief_draft_reply"]({"message_id": "m001", "body": "x", "sender_only": "yes"}))
    assert odd["reply_all"] is True
