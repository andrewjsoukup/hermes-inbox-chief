"""Lazily-built runtime: (settings, lanes config, mailbox client, state store).

``register()`` never builds this; the first tool call or hook does, so plugin load performs no I/O
or network access (the validator and Plugin Doctor block sockets during ``register()``).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError, LanesConfig, load
from .drafts import Settings
from .graph import FixtureGraphClient, GraphClient, HermesGraphClient
from .state import MemoryState, StateBackend, Store

PLUGIN_DIR = Path(__file__).resolve().parent.parent
DEMO_FIXTURE = PLUGIN_DIR / "fixtures" / "demo" / "mailbox.json"
DEMO_LANES = PLUGIN_DIR / "lanes.example.yaml"


class NotConfigured(RuntimeError):
    pass


@dataclass
class Engine:
    settings: Settings
    cfg: LanesConfig
    client: GraphClient
    store: Store
    mailbox: str
    notes_dir: str = ""


def settings_from(get: Callable[[str, Any], Any]) -> tuple[Settings, dict]:
    raw = {
        "lanes_file": str(get("lanes_file", "") or ""),
        "notes_dir": str(get("notes_dir", "") or ""),
        "mailbox": str(get("mailbox", "graph") or "graph"),
        "fixture_path": str(get("fixture_path", "") or ""),
    }
    go = get("go_phrases", []) or []
    s = Settings(
        send_confirmation=str(get("send_confirmation", "approval_gate") or "approval_gate"),
        go_phrases=tuple(str(x) for x in go) if isinstance(go, list) else (),
        scan_lookback_days=int(get("scan_lookback_days", 2) or 2),
        owner=str(get("owner", "") or ""),
        signature=str(get("signature", "") or ""),
    )
    return s, raw


def build(get: Callable[[str, Any], Any], state: StateBackend | None) -> Engine:
    settings, raw = settings_from(get)
    mailbox = raw["mailbox"] if raw["mailbox"] in ("graph", "fixture") else "graph"
    lanes_file = raw["lanes_file"]
    if not lanes_file:
        if mailbox != "fixture":
            raise NotConfigured(
                "Inbox Chief is not configured yet: set plugins.entries.inbox-chief.settings.lanes_file to your "
                "lanes YAML (start from lanes.example.yaml), set INBOX_CHIEF_MS_CLIENT_ID, then run "
                "`hermes auth add inbox-chief:microsoft` in a terminal. To try it offline first, set mailbox: fixture.")
        lanes_file = str(DEMO_LANES)
    try:
        cfg = load(lanes_file)
    except ConfigError as exc:
        raise NotConfigured("lanes file has problems:\n" + str(exc)) from None
    if mailbox == "fixture":
        client: GraphClient = FixtureGraphClient.from_file(raw["fixture_path"] or DEMO_FIXTURE)
    else:
        client = HermesGraphClient()
    return Engine(settings, cfg, client, Store(state or MemoryState()), mailbox, raw["notes_dir"])


class EngineHolder:
    """Process-wide cache; rebuilt when settings change (cheap compare of the raw settings)."""

    def __init__(self, get: Callable[[str, Any], Any], state: StateBackend | None):
        self._get, self._state = get, state
        self._engine: Engine | None = None
        self._key: tuple | None = None
        self._lock = threading.Lock()

    def _settings_key(self) -> tuple:
        keys = ("lanes_file", "notes_dir", "mailbox", "fixture_path", "send_confirmation", "go_phrases",
                "scan_lookback_days", "owner", "signature")
        return tuple(repr(self._get(k, None)) for k in keys)

    def get(self) -> Engine:
        with self._lock:
            key = self._settings_key()
            if self._engine is None or key != self._key:
                self._engine = build(self._get, self._state)
                self._key = key
            return self._engine

    def set_engine(self, engine: Engine) -> None:  # tests
        with self._lock:
            self._engine, self._key = engine, self._settings_key()
