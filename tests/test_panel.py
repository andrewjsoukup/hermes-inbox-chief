"""Desktop panel: read model (inbox_chief.panel), backend routes (dashboard/plugin_api.py), the
desktop surface lint and a headless render of desktop/plugin.js. Fixture mailbox only (SPEC S7)."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE, LANES, ROOT, load_plugin_package
from test_plugin import FakeCtx


def _ctx(tmp_path, **extra):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES),
                   "notes_dir": str(tmp_path), **extra})
    pkg.register(ctx)
    return pkg, ctx


def call(ctx, name, args=None, **kw):
    return json.loads(ctx.tools[name](args or {}, **kw))


def _panel_mod():
    return sys.modules["inbox_chief_plugin.inbox_chief.panel"]


def _panel(pkg):
    mod = _panel_mod()
    return mod.Panel(mod.published_tools())


def test_register_publishes_tools_for_the_backend(tmp_path):
    pkg, ctx = _ctx(tmp_path)
    assert _panel_mod().published_tools() is not None


def test_go_and_change_messages_pass_the_safety_gate():
    from inbox_chief import panel, safety
    did = "AAMkADdraft-0007abc123"
    go = panel.go_message(did)
    assert go == "go abc123"
    assert safety.is_go(go) and safety.go_selects(go, did, "anything")
    assert not safety.is_go(panel.change_message(did) + "make it shorter")


def test_queue_groups_items_and_drafts_by_lane_without_marking_seen(tmp_path):
    pkg, ctx = _ctx(tmp_path)
    call(ctx, "inbox_chief_notes", {"action": "add", "name": "Lakeshore Creamery", "text": "Asked for KP-450 cut sheet",
                                    "aliases": "lakeshore-creamery.example"})
    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "Cut sheet attached.", "summary": "KP-450 cut sheet"})
    assert d["ok"]
    p = _panel(pkg)
    q = p.queue()
    assert q["ok"] and q["mailbox"] == "fixture"
    assert q["pending_count"] == 1 and q["item_count"] >= 1
    drafts = [x for lane in q["lanes"] for x in lane["drafts"]]
    assert drafts[0]["draft_id"] == d["staged"]["draft_id"]
    assert drafts[0]["go_message"] == "go " + d["staged"]["draft_id"][-6:]
    items = {i["message_id"]: i for lane in q["lanes"] for i in lane["items"]}
    assert "m003" in items
    assert all(lane["label"] for lane in q["lanes"])
    # Looking at the panel must not consume items the agent has yet to report.
    assert call(ctx, "inbox_chief_scan")["count"] >= 1


def test_queue_attaches_matching_account_notes(tmp_path):
    pkg, ctx = _ctx(tmp_path)
    q0 = _panel(pkg).queue()
    item = next(i for lane in q0["lanes"] for i in lane["items"] if i["message_id"] == "m001")
    domain = item["sender"].split("@")[-1]
    call(ctx, "inbox_chief_notes", {"action": "add", "name": "Panel Test Account", "text": "note line", "aliases": domain})
    q = _panel(pkg).queue()
    item = next(i for lane in q["lanes"] for i in lane["items"] if i["message_id"] == "m001")
    assert "Panel Test Account" in item.get("notes", [])


def test_draft_preview_skip_and_notes(tmp_path):
    pkg, ctx = _ctx(tmp_path)
    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "Hello from the panel test."})
    did = d["staged"]["draft_id"]
    p = _panel(pkg)
    prev = p.draft(did)
    assert prev["ok"] and "Hello from the panel test." in prev["body"] and "<" not in prev["body"][:40]
    assert not p.draft("nope")["ok"]
    assert p.skip(did)["ok"]
    assert p.queue(scan=False)["pending_count"] == 0
    # skip never sends and never deletes the Outlook draft
    client = pkg_engine(ctx).client
    assert not any(op == "send" for op, *_ in client.log)
    assert (client.get_message(did) or {}).get("isDraft")

    call(ctx, "inbox_chief_notes", {"action": "add", "name": "Granite Fittings", "text": "Waiting on cut sheet"})
    assert "Granite Fittings" in p.notes()["names"]
    n = p.note("Granite Fittings")
    assert n["ok"] and "Waiting on cut sheet" in n["body"]
    assert not p.note("")["ok"]


def pkg_engine(ctx):
    return _panel_mod().published_tools().holder.get()


def test_summary_is_cheap_and_counts_pending(tmp_path):
    pkg, ctx = _ctx(tmp_path)
    call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "x"})
    s = _panel(pkg).summary()
    assert s == {"ok": True, "pending_count": 1, "mailbox": "fixture", "lanes": s["lanes"]} and s["lanes"] >= 1


def test_unconfigured_explains_setup():
    pkg = load_plugin_package()
    ctx = FakeCtx({})
    pkg.register(ctx)
    q = _panel(pkg).queue()
    assert not q["ok"] and q["setup_needed"] and "lanes_file" in q["error"]


def test_scan_shapes_are_all_accepted():
    from inbox_chief.panel import _items_from_scan
    a = _items_from_scan({"items": [{"lane": "x", "key": "1"}]})
    b = _items_from_scan({"lanes": {"x": {"label": "X", "items": [{"key": "1"}]}}})
    c = _items_from_scan({"lanes": [{"id": "x", "items": [{"key": "1"}]}]})
    assert a[0]["lane"] == b[0]["lane"] == c[0]["lane"] == "x" and b[0]["lane_label"] == "X"


# ── backend routes ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def loopback_only(monkeypatch):
    """TestClient's event loop needs a loopback socketpair on Windows (Proactor self-pipe). Allow
    127.0.0.1/::1 only; every other address still fails the test (SPEC S7)."""
    import socket
    def is_loopback(address):
        host = address[0] if isinstance(address, tuple) else address
        return str(host) in ("127.0.0.1", "::1", "localhost")

    def connect(self, address):
        if not is_loopback(address):
            raise AssertionError(f"tests must not open network connections (SPEC S7): {address!r}")
        return _orig_socket_connect(self, address)

    def create_connection(address, *a, **k):
        if not is_loopback(address):
            raise AssertionError(f"tests must not open network connections (SPEC S7): {address!r}")
        return _orig_create_connection(address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: connect(self, address) or 0)
    monkeypatch.setattr(socket, "create_connection", create_connection)


import socket as _socket  # noqa: E402  (captured before the autouse guard patches them)

_orig_socket_connect = _socket.socket.connect
_orig_create_connection = _socket.create_connection


def _load_api():
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("inbox_chief_plugin_api_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backend_has_no_send_route_and_serves_the_queue(tmp_path, loopback_only):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    pkg, ctx = _ctx(tmp_path)  # publishes Tools → backend reuses the agent half's engine
    api = _load_api()
    paths = {r.path for r in api.router.routes}
    assert paths == {"/queue", "/summary", "/draft", "/skip", "/note", "/notes"}
    assert not any("send" in p for p in paths)
    src = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert "send_draft" not in src and "inbox_chief_send" not in re.sub(r'""".*?"""', "", src, flags=re.S)

    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/inbox-chief")
    client = TestClient(app)
    d = call(ctx, "inbox_chief_draft_reply", {"message_id": "m001", "body": "Route test."})
    did = d["staged"]["draft_id"]
    q = client.get("/api/plugins/inbox-chief/queue").json()
    assert q["ok"] and q["pending_count"] == 1
    assert client.get("/api/plugins/inbox-chief/summary").json()["pending_count"] == 1
    assert "Route test." in client.post("/api/plugins/inbox-chief/draft", json={"draft_id": did}).json()["body"]
    assert client.post("/api/plugins/inbox-chief/skip", json={"draft_id": did}).json()["ok"]
    assert client.get("/api/plugins/inbox-chief/summary").json()["pending_count"] == 0
    assert client.post("/api/plugins/inbox-chief/notes", json={"q": ""}).json()["ok"]


def test_backend_errors_are_json_not_500(tmp_path, loopback_only):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _ctx(tmp_path)
    app = FastAPI()
    app.include_router(_load_api().router)
    r = TestClient(app).post("/skip", json={"draft_id": "missing"})
    assert r.status_code == 200 and r.json()["ok"] is False


# ── desktop/plugin.js ────────────────────────────────────────────────────────────────────────────

PLUGIN_JS = ROOT / "desktop" / "plugin.js"


def test_plugin_js_imports_only_the_sdk_and_react():
    src = PLUGIN_JS.read_text(encoding="utf-8")
    specs = set(re.findall(r"""from\s+['"]([^'"]+)['"]""", src))
    assert specs <= {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}, specs
    for banned in ("window.hermesDesktop", "document.querySelector", "eval(", "new Function", "fetch(", "localStorage"):
        assert banned not in src, banned
    assert "id: PLUGIN_ID" in src and "const PLUGIN_ID = 'inbox-chief'" in src


def test_desktop_surface_lint_passes():
    lint = _hermes_module("hermes_cli.plugin_validate_desktop")
    if lint is None:
        pytest.skip("Hermes source not importable here")
    assert lint.desktop_surface_hits(ROOT) == []
    assert lint.desktop_surface_hits(ROOT, lint.desktop_review_findings) == []


def _hermes_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        pass
    for base in (Path.home() / "AppData/Local/hermes/hermes-agent", Path.home() / ".hermes/hermes-agent"):
        if (base / "hermes_cli").is_dir():
            sys.path.insert(0, str(base))
            try:
                return importlib.import_module(name)
            except Exception:
                return None
            finally:
                sys.path.remove(str(base))
    return None


def test_plugin_js_renders_headless():
    node = shutil.which("node")
    harness = ROOT / "tests" / "desktop" / "render_harness.mjs"
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    if r.returncode == 3:
        pytest.skip(r.stdout.strip() or "react not resolvable for the harness")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RENDER OK" in r.stdout
