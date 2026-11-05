"""Inbox Chief backend for the Desktop panel, mounted at ``/api/plugins/inbox-chief/``.

Read-mostly by design (SPEC.md §1): the panel can list lanes, queued items, staged drafts and account
notes, preview a staged draft, and SKIP a draft (remove it from the go/no-go list; the draft stays in
Outlook). There is no send route. The panel's Go button types ``go <id>`` into the user's chat, so every
send goes through the ``inbox_chief_send`` tool and its gates (S1–S5 + Hermes approval prompt).

Public surfaces only: ``hermes_cli.config`` (read settings), ``hermes_cli.plugins_state.PluginState``
(the same profile-scoped state ``ctx.state`` uses), ``hermes_constants.get_hermes_home``. Microsoft Graph
calls go through the engine's ``HermesGraphClient`` → ``credentialed_provider_request``; this file never
sees a token.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

PLUGIN_ID = "inbox-chief"
PLUGIN_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = PLUGIN_DIR / "inbox_chief"

router = APIRouter()
_lock = threading.Lock()
_own: dict[str, Any] = {}


def _engine_module(name: str):
    """The engine module already loaded by the agent half (same file), else a private copy."""
    target = (ENGINE_DIR / f"{name}.py").resolve()
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        try:
            if f and Path(f).resolve() == target:
                return mod
        except (OSError, ValueError):
            continue
    pkg_name = "_inbox_chief_panel_engine"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(pkg_name, ENGINE_DIR / "__init__.py",
                                                      submodule_search_locations=[str(ENGINE_DIR)])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = pkg
        spec.loader.exec_module(pkg)
    return importlib.import_module(f"{pkg_name}.{name}")


def _settings_getter():
    from hermes_cli.config import load_config_readonly

    def get(key: str, default: Any = None) -> Any:
        cfg = load_config_readonly() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
        for tree in ("settings", "config"):
            sub = entry.get(tree)
            if isinstance(sub, dict) and key in sub:
                return sub[key]
        return default
    return get


def _profile() -> str:
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        return home.name if home.parent.name == "profiles" else "default"
    except Exception:
        return "default"


def _published_panel():
    """Any loaded copy of inbox_chief/panel.py whose agent half published its Tools (the same file can be
    imported under more than one package name)."""
    target = (ENGINE_DIR / "panel.py").resolve()
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        try:
            same = bool(f) and Path(f).resolve() == target
        except (OSError, ValueError):
            same = False
        if same and callable(getattr(mod, "published_tools", None)) and mod.published_tools() is not None:
            return mod
    return None


def _panel():
    """The agent half's published Tools when it lives in this process; else our own over the same state."""
    shared_mod = _published_panel()
    if shared_mod is not None:
        return shared_mod.Panel(shared_mod.published_tools(), shared_process=True)
    panel_mod = _engine_module("panel")
    with _lock:
        tools = _own.get(_profile())
        if tools is None:
            from hermes_cli.plugins_state import PluginState
            engine_mod, tools_mod = _engine_module("engine"), _engine_module("tools")
            holder = engine_mod.EngineHolder(_settings_getter(), PluginState(PLUGIN_ID))
            tools = _own[_profile()] = tools_mod.Tools(holder, profile=_profile())
    return panel_mod.Panel(tools, shared_process=False)


def _safe(fn) -> dict:
    try:
        return fn()
    except Exception as exc:  # never 500 the panel; it renders the error
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/queue")
def queue(scan: int = 1) -> dict:
    return _safe(lambda: _panel().queue(scan=bool(scan)))


@router.get("/summary")
def summary() -> dict:
    return _safe(lambda: _panel().summary())


@router.post("/draft")
def draft(body: dict = Body(default_factory=dict)) -> dict:
    return _safe(lambda: _panel().draft(str(body.get("draft_id") or "")))


@router.post("/skip")
def skip(body: dict = Body(default_factory=dict)) -> dict:
    return _safe(lambda: _panel().skip(str(body.get("draft_id") or ""), str(body.get("outcome") or "skipped")))


@router.post("/note")
def note(body: dict = Body(default_factory=dict)) -> dict:
    return _safe(lambda: _panel().note(str(body.get("name") or "")))


@router.post("/notes")
def notes(body: dict = Body(default_factory=dict)) -> dict:
    return _safe(lambda: _panel().notes(str(body.get("q") or "")))
