"""Account notes (team-shareable markdown dir) and pre_llm_call context injection."""
from __future__ import annotations

import json

from inbox_chief import context, drafts
from inbox_chief.drafts import Settings
from inbox_chief.engine import Engine
from inbox_chief.notes import MAX_LOG, Notes
from inbox_chief.state import MemoryState, Store

from conftest import LANES, load_plugin_package
from mailbox_builder import DANA, Box


def test_notes_add_match_show_and_aliases(tmp_path):
    n = Notes(tmp_path)
    n.add("Lakeshore Creamery", agent="pat", text="Waiting on Kestrel for KP-450 cut sheet",
          aliases="lakeshorecreamery.example, Dana Okafor", today="2026-11-07")
    n.add("lakeshore creamery", agent="jo", text="Cut sheet sent", today="2026-11-08")  # case-insensitive reuse
    assert list(n.all()) == ["Lakeshore Creamery"]
    assert n.match("Dana Okafor asked again") == ["Lakeshore Creamery"]
    assert n.match("mail from someone@lakeshorecreamery.example") == ["Lakeshore Creamery"]
    assert n.match("Lakeshores") == []  # word-boundary
    body = n.body("Lakeshore Creamery")
    assert body.index("2026-11-08 (jo)") < body.index("2026-11-07 (pat)")  # newest first
    assert n.for_address("dana.okafor@lakeshorecreamery.example") == ["Lakeshore Creamery"]
    assert not list(tmp_path.glob(".inbox-chief-*"))  # atomic temp files cleaned up


def test_notes_log_capped_with_archive(tmp_path):
    n = Notes(tmp_path)
    for i in range(MAX_LOG + 5):
        n.add("Acme Demo", agent="pat", text=f"line {i}", today="2026-11-07")
    log = (tmp_path / "Acme Demo.md").read_text(encoding="utf-8").split("## Log", 1)[1]
    assert len([l for l in log.splitlines() if l.startswith("- ")]) == MAX_LOG
    arch = (tmp_path / "_archive" / "Acme Demo.md").read_text(encoding="utf-8")
    assert "line 0" in arch and "line 4" in arch and "line 5" not in arch


def test_notes_ignore_unsafe_names(tmp_path):
    n = Notes(tmp_path)
    note = n.add("../evil:name", agent="pat", text="x", today="2026-11-07")
    assert note.path.parent == tmp_path and ".." not in note.name


def test_notes_block_injects_once_per_session_unless_changed(tmp_path):
    context.reset_session_cache()
    n = Notes(tmp_path)
    n.add("Lakeshore Creamery", agent="pat", text="open: cut sheet", aliases="Dana Okafor", today="2026-11-07")
    first = context.notes_block(n, "s1", "Dana Okafor wrote back")
    assert "### Lakeshore Creamery" in first and "open: cut sheet" in first
    assert context.notes_block(n, "s1", "Dana Okafor again") == ""
    assert context.notes_block(n, "s2", "Dana Okafor") != ""  # other session
    import os, time
    p = tmp_path / "Lakeshore Creamery.md"
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert context.notes_block(n, "s1", "Dana Okafor") != ""  # changed → re-injected


def test_context_build_all_blocks_and_cap(cfg, tmp_path):
    context.reset_session_cache()
    Notes(tmp_path).add("Lakeshore Creamery", agent="pat", text="x" * 1500, aliases="Dana Okafor", today="2026-11-07")
    b = Box(now="2026-11-06T15:00:00Z")
    m = b.inbound("c-q", DANA, "Spec question", "2026-11-06T14:00:00Z", "spec?")
    client, store = b.client(), Store(MemoryState())
    for i in range(8):
        drafts.draft_reply(client=client, store=store, cfg=cfg, message_id=m, body=f"draft {i}", session_id="s")
    e = Engine(Settings(), cfg, client, store, "fixture", str(tmp_path))
    text, keys = context.build(e, owner="default", session_id="s", user_message="what did Dana Okafor want?")
    assert text.count("draft_id=") == context.MAX_PENDING
    assert "Account notes" in text and len(text) <= context.MAX_CONTEXT


def test_pre_llm_call_skips_subagents_and_unconfigured():
    pkg = load_plugin_package()
    from test_plugin import FakeCtx
    ctx = FakeCtx()
    pkg.register(ctx)
    (hook,) = ctx.hooks["pre_llm_call"]
    assert hook(session_id="s", user_message="hi") is None  # unconfigured
    ctx2 = FakeCtx({"mailbox": "fixture", "lanes_file": str(LANES)})
    pkg.register(ctx2)
    (hook2,) = ctx2.hooks["pre_llm_call"]
    json.loads(ctx2.tools["inbox_chief_draft_reply"]({"message_id": "m001", "body": "hi"}, session_id="s"))
    assert hook2(session_id="s", user_message="hi", parent_session_id="p") is None
    assert "draft_id=" in hook2(session_id="s", user_message="hi")["context"]
