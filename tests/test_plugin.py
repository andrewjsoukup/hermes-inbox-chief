"""Registration contract: register() against a recording ctx, manifest ↔ registrations, hooks, repo hygiene."""
import json
import re
from pathlib import Path

import pytest

from conftest import FIXTURE, LANES, ROOT, load_plugin_package


class FakeState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return json.loads(json.dumps(self.d.get(k, default)))

    def set(self, k, v):
        self.d[k] = json.loads(json.dumps(v))


class FakeCtx:
    def __init__(self, settings=None, cron=None):
        self.settings = settings or {}
        self.tools, self.hooks, self.commands, self.cli, self.blueprints, self.skills = {}, {}, {}, {}, {}, {}
        self.state = FakeState()
        self.profile_name = "default"
        self._cron = cron

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def current_cron_execution(self):
        return self._cron

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert name not in self.tools
        self.tools[name] = handler

    def register_hook(self, name, cb, **kw):
        self.hooks.setdefault(name, []).append(cb)

    def register_command(self, name, handler, description="", **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn, **kw):
        self.cli[name] = (setup_fn, handler_fn)

    def register_automation_blueprint(self, key, **kw):
        assert ":" not in key
        self.blueprints[key] = kw

    def register_skill(self, name, path, description="", **kw):
        assert Path(path).is_file()
        self.skills[name] = path


def _manifest():
    from inbox_chief.config import _yaml_load
    return _yaml_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def _registered(settings=None, cron=None):
    pkg = load_plugin_package()
    ctx = FakeCtx(settings, cron)
    pkg.register(ctx)
    return ctx


def test_register_matches_manifest_and_does_no_io():
    ctx = _registered()
    m = _manifest()
    assert set(ctx.tools) == set(m["provides_tools"])
    assert set(ctx.hooks) == set(m["provides_hooks"])
    assert "inbox" in ctx.commands and "inbox-chief" in ctx.cli
    assert "scan" in ctx.blueprints and "triage" in ctx.skills
    assert ctx.state.d == {}  # nothing written during register()


def test_manifest_oauth_and_floor():
    m = _manifest()
    assert m["requires_hermes"] == ">=0.22.3"
    (o,) = m["oauth"]
    assert o["id"] == "microsoft"
    assert set(o["scopes"]) >= {"Mail.ReadWrite", "Mail.Send", "offline_access"}
    assert o["hosts"] == ["graph.microsoft.com"]
    for k in ("authorize_url", "token_url"):
        assert o[k].startswith("https://login.microsoftonline.com/")
    assert "client_secret_env" not in o  # public PKCE client
    assert o["client_id_env"] in {e["name"] for e in m["requires_env"]}
    from inbox_chief import graph
    assert graph.OAUTH_ID == o["id"]


def test_unconfigured_status_explains_setup():
    ctx = _registered()
    out = json.loads(ctx.tools["inbox_chief_status"]({}))
    assert out["ok"] is False and out["setup_needed"] and "lanes_file" in out["error"]


def test_fixture_mode_end_to_end_through_tools_and_hooks():
    ctx = _registered({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)})
    st = json.loads(ctx.tools["inbox_chief_status"]({}))
    assert st["ok"] and st["mailbox"] == "fixture"
    r = json.loads(ctx.tools["inbox_chief_draft_reply"](
        {"message_id": "m001", "body": "Cut sheet attached.", "attach_from_message_id": "m003",
         "attachment_names": ["KP-450-cut-sheet.pdf"]}, session_id="s1"))
    assert r["ok"], r
    did = r["staged"]["draft_id"]
    # pre_llm_call injects the pending draft
    (pre_llm,) = ctx.hooks["pre_llm_call"]
    injected = pre_llm(session_id="s1", user_message="what's waiting?")
    assert did in injected["context"] and "go/no-go" in injected["context"]
    # pre_tool_call escalates the send to Hermes's approval prompt
    (pre_tool,) = ctx.hooks["pre_tool_call"]
    d = pre_tool(tool_name="inbox_chief_send", args={"draft_id": did})
    assert d["action"] == "approve" and "dana.okafor" in d["message"]
    assert pre_tool(tool_name="read_file", args={}) is None
    assert pre_tool(tool_name="inbox_chief_send", args={"draft_id": "unknown"})["action"] == "block"


def test_send_gate_blocks_in_cron_run():
    ctx = _registered({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)}, cron=object())
    (pre_tool,) = ctx.hooks["pre_tool_call"]
    d = pre_tool(tool_name="inbox_chief_send", args={"draft_id": "anything"})
    assert d["action"] == "block" and "unattended" in d["message"]


def test_handlers_never_raise_and_return_json():
    ctx = _registered({"mailbox": "fixture", "fixture_path": str(FIXTURE), "lanes_file": str(LANES)})
    for name, h in ctx.tools.items():
        out = h({"message_id": "does-not-exist", "draft_id": "nope", "body": "x", "to": "", "subject": ""})
        assert isinstance(json.loads(out), dict), name


def test_cli_check_config(capsys):
    ctx = _registered()
    setup, run = ctx.cli["inbox-chief"]
    import argparse
    p = argparse.ArgumentParser()
    setup(p)
    assert run(p.parse_args(["check-config", str(LANES)])) == 0
    assert "OK" in capsys.readouterr().out


# ── repo hygiene (catalog rules 11, 17; no real data) ──
TEXT_EXT = {".py", ".yaml", ".yml", ".md", ".json", ".toml", ".js", ".txt", ".cfg", ".ini"}


_SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".venv", "private"}


def _repo_files():
    """Files that would ship: tracked + untracked-but-not-ignored (git), so gitignored scratch such as
    private/ sandboxes and .venv never count. Falls back to a filtered walk without git."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             capture_output=True, timeout=30, check=True).stdout.decode("utf-8")
        for rel in filter(None, out.split("\0")):
            p = ROOT / rel
            if p.is_file() and not any(part in _SKIP_PARTS for part in p.relative_to(ROOT).parts):
                yield p
        return
    except Exception:
        pass
    for p in ROOT.rglob("*"):
        if p.is_file() and not any(part in _SKIP_PARTS for part in p.relative_to(ROOT).parts):
            yield p


def test_no_binaries_or_bytecode():
    for p in _repo_files():
        assert p.suffix not in {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".wasm"}, p
        head = p.read_bytes()[:4]
        assert not head.startswith((b"MZ", b"\x7fELF", b"\0asm", b"\xcf\xfa\xed\xfe")), p


def test_no_credential_file_reads():
    bad = re.compile(r"(auth\.json|\.env\b|token_cache|os\.environ\[|getenv\(\s*[\"']INBOX_CHIEF_MS)", re.I)
    for p in _repo_files():
        if p.suffix == ".py" and "tests" not in p.parts:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                assert not bad.search(line), f"{p.name}:{i}: {line.strip()}"


def test_public_files_use_fictional_domains_only():
    """Every email address in shipped data must be on a reserved .example domain."""
    addr = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    for p in _repo_files():
        if p.suffix in {".json", ".yaml", ".yml"}:
            for dom in addr.findall(p.read_text(encoding="utf-8")):
                assert dom.endswith(".example"), f"{p.name}: {dom}"
