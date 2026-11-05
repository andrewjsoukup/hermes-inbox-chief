"""Offline-only test harness (SPEC S7).

* Any socket connection fails the test, so nothing can reach Microsoft Graph or a real mailbox.
* The plugin directory is importable as package ``inbox_chief_plugin`` (mirrors how Hermes loads it
  under a namespaced name), and the engine package as ``inbox_chief``.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "fixtures" / "demo" / "mailbox.json"
LANES = ROOT / "lanes.example.yaml"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("tests must not open network connections (SPEC S7)")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)  # tests are not kanban workers


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client(fixture_data):
    from inbox_chief.graph import FixtureGraphClient
    return FixtureGraphClient(fixture_data)


@pytest.fixture
def cfg():
    from inbox_chief.config import load
    return load(LANES)


@pytest.fixture
def store():
    from inbox_chief.state import MemoryState, Store
    return Store(MemoryState())


@pytest.fixture
def engine(cfg, client, store):
    from inbox_chief.drafts import Settings
    from inbox_chief.engine import Engine
    return Engine(Settings(), cfg, client, store, "fixture")


def load_plugin_package():
    """Import the plugin root (``__init__.py`` + ``inbox_chief/``) as package ``inbox_chief_plugin``."""
    if "inbox_chief_plugin" in sys.modules:
        return sys.modules["inbox_chief_plugin"]
    spec = importlib.util.spec_from_file_location("inbox_chief_plugin", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["inbox_chief_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod
