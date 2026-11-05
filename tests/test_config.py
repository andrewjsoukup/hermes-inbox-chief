import copy

import pytest

from inbox_chief.config import ConfigError, _yaml_load, parse
from conftest import LANES


def _raw():
    return _yaml_load(LANES.read_text(encoding="utf-8"))


def test_example_lanes_file_is_valid(cfg):
    assert cfg.company_name == "Northwind Process Supply"
    assert [l.id for l in cfg.lanes_by_priority()] == ["quotes", "accounting", "sales"]
    assert cfg.is_internal("someone@northwind-demo.example")
    assert cfg.is_internal("pat.rivera.home@mail.example")  # my_addresses
    assert not cfg.is_internal("dana.okafor@lakeshorecreamery.example")


def test_supplier_lookup_and_parent_kept_separate(cfg):
    s = cfg.supplier_for("ravi.lund@kestrelpumps.example")
    assert s and s.name == "Kestrel Pump Works" and s.parent == "Harbor Flow Group"
    assert "Harbor Flow Group" not in s.aliases  # parent is never an alias (live false-link bug)
    assert cfg.supplier_for("dana@lakeshorecreamery.example") is None
    assert cfg.customer_for("dana@lakeshorecreamery.example").name == "Lakeshore Creamery"


def test_builtin_noise_merged_unless_replaced(cfg):
    assert any(p.search("no-reply@x.example") for p in cfg.noise_senders)
    raw = _raw()
    raw["noise"]["replace"] = True
    c2 = parse(raw)
    assert not any(p.search("no-reply@x.example") for p in c2.noise_senders)


def test_all_problems_reported_at_once():
    raw = _raw()
    raw["version"] = 2
    raw["company"]["internal_domains"] = []
    raw["lanes"][0]["detect"] = ["new", "telepathy"]
    raw["lanes"][1]["id"] = "quotes"
    raw["lanes"][2]["match"]["text"] = ["(unclosed"]
    with pytest.raises(ConfigError) as ei:
        parse(raw)
    text = "\n".join(ei.value.problems)
    for needle in ("version", "internal_domains", "telepathy", "used twice", "invalid regex"):
        assert needle in text, needle


def test_hours_validation():
    raw = copy.deepcopy(_raw())
    raw["hours"]["start"] = "25:00"
    raw["hours"]["days"] = ["mon", "funday"]
    with pytest.raises(ConfigError) as ei:
        parse(raw)
    assert any("hours.start" in p for p in ei.value.problems)
    assert any("funday" in p for p in ei.value.problems)
