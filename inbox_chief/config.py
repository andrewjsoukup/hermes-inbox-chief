"""Lanes configuration: load and validate ``lanes.yaml`` (schema ``version: 1``, see SPEC.md §5.2).

Pure Python; no Hermes imports. Errors are collected (not raised one at a time) so ``hermes
inbox-chief check-config`` can show every problem in one pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
DETECTIONS = ("new", "customer_reply", "supplier_reply", "replies_owed")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Built-in noise filters (generic parts of the live router's NOISE / AUTOMATED / AUTO_SUBJECT /
# SUBSCRIPTION patterns). A lanes file adds to these unless ``noise.replace: true``.
DEFAULT_NOISE_SENDERS = (
    r"no-?reply", r"do-?not-?reply", r"notifications?@", r"mailer", r"newsletter", r"marketing@",
    r"calendar", r"teams\.mail", r"docusign\.net", r"zoom\.us", r"linkedin", r"invoicing@", r"billing@",
    r"success@", r"support@", r"hello@", r"team@", r"updates?@", r"alerts?@", r"accounts?@",
)
DEFAULT_NOISE_SUBJECTS = (
    r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|undeliverable|delivery status|"
    r"read:|accepted:|declined:|tentative:)",
    r"\b(subscription|renewed|renewal|receipt|your order|statement available|trial (ends|expires)|unsubscribe)\b",
)


class ConfigError(ValueError):
    """Raised by :func:`load` with every problem found, one per line."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Party:
    name: str
    domains: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    parent: str | None = None

    def owns(self, address: str) -> bool:
        dom = address.lower().rsplit("@", 1)[-1]
        return any(dom == d or dom.endswith("." + d) for d in self.domains)


@dataclass(frozen=True)
class Lane:
    id: str
    label: str
    owner: str
    priority: int
    detect: tuple[str, ...]
    text: tuple[re.Pattern, ...] = ()
    from_: tuple[re.Pattern, ...] = ()
    exclude_from: tuple[re.Pattern, ...] = ()
    exclude_text: tuple[re.Pattern, ...] = ()
    owed_after_business_days: int = 1
    owed_max_days: int = 10


@dataclass(frozen=True)
class Hours:
    timezone: str = "UTC"
    days: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")
    start: str = "06:00"
    end: str = "18:00"


@dataclass(frozen=True)
class Safety:
    pricing_patterns: tuple[re.Pattern, ...] = ()
    pricing_attachment_names: tuple[re.Pattern, ...] = ()
    allow_pricing_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class LanesConfig:
    company_name: str
    internal_domains: tuple[str, ...]
    my_addresses: tuple[str, ...]
    hours: Hours
    noise_senders: tuple[re.Pattern, ...]
    noise_subjects: tuple[re.Pattern, ...]
    suppliers: tuple[Party, ...]
    customers: tuple[Party, ...]
    lanes: tuple[Lane, ...]
    followup_due_business_days: int = 3
    safety: Safety = field(default_factory=Safety)

    def is_internal(self, address: str) -> bool:
        a = (address or "").lower()
        dom = a.rsplit("@", 1)[-1]
        return bool(a) and (dom in self.internal_domains or a in self.my_addresses)

    def supplier_for(self, address: str) -> Party | None:
        return next((s for s in self.suppliers if s.owns(address or "")), None)

    def customer_for(self, address: str) -> Party | None:
        return next((c for c in self.customers if c.owns(address or "")), None)

    def lanes_by_priority(self) -> list[Lane]:
        return sorted(self.lanes, key=lambda lane: (lane.priority, lane.id))


# ── parsing helpers ──────────────────────────────────────────────────────────────────────────────

def _yaml_load(text: str) -> Any:
    try:
        from ruamel.yaml import YAML  # Hermes core dependency
        return YAML(typ="safe", pure=True).load(text)
    except ImportError:  # pragma: no cover - test envs without ruamel
        import yaml  # type: ignore
        return yaml.safe_load(text)


def _strs(value: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{where}: must be a list of strings")
        return ()
    return tuple(v.strip() for v in value if v.strip())


def _regexes(value: Any, where: str, problems: list[str]) -> tuple[re.Pattern, ...]:
    out = []
    for i, pat in enumerate(_strs(value, where, problems)):
        try:
            out.append(re.compile(pat, re.I))
        except re.error as exc:
            problems.append(f"{where}[{i}]: invalid regex {pat!r}: {exc}")
    return tuple(out)


def _domains(value: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    doms = tuple(d.lower().lstrip("@") for d in _strs(value, where, problems))
    for d in doms:
        if "." not in d or "@" in d or " " in d:
            problems.append(f"{where}: {d!r} is not a domain")
    return doms


def _parties(value: Any, where: str, problems: list[str], *, allow_parent: bool) -> tuple[Party, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.append(f"{where}: must be a list")
        return ()
    out = []
    for i, raw in enumerate(value):
        w = f"{where}[{i}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
            problems.append(f"{w}: needs a name")
            continue
        parent = raw.get("parent") if allow_parent else None
        if parent is not None and not isinstance(parent, str):
            problems.append(f"{w}.parent: must be a string")
            parent = None
        out.append(Party(raw["name"].strip(), _domains(raw.get("domains"), f"{w}.domains", problems),
                         _strs(raw.get("aliases"), f"{w}.aliases", problems), parent))
    return tuple(out)


def _int(value: Any, default: int, where: str, problems: list[str], *, minimum: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        problems.append(f"{where}: must be an integer >= {minimum}")
        return default
    return value


_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse(data: Any) -> LanesConfig:
    """Validate a decoded lanes mapping; raises :class:`ConfigError` listing every problem."""
    problems: list[str] = []
    if not isinstance(data, dict):
        raise ConfigError(["lanes file: top level must be a mapping"])
    if data.get("version") != SCHEMA_VERSION:
        problems.append(f"version: must be {SCHEMA_VERSION}")

    company = data.get("company") or {}
    if not isinstance(company, dict):
        problems.append("company: must be a mapping")
        company = {}
    internal = _domains(company.get("internal_domains"), "company.internal_domains", problems)
    if not internal:
        problems.append("company.internal_domains: at least one domain is required")
    mine = tuple(a.lower() for a in _strs(company.get("my_addresses"), "company.my_addresses", problems))

    h = data.get("hours") or {}
    raw_days = _strs(h.get("days"), "hours.days", problems)
    full = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    for d in raw_days:
        if d.lower() not in DAYS and d.lower() not in full:
            problems.append(f"hours.days: {d!r} is not one of {', '.join(DAYS)}")
    days = tuple(d.lower()[:3] for d in raw_days) or Hours.days
    start, end = str(h.get("start", Hours.start)), str(h.get("end", Hours.end))
    for k, v in (("start", start), ("end", end)):
        if not _HHMM.match(v):
            problems.append(f"hours.{k}: must be HH:MM (24h)")
    hours = Hours(str(h.get("timezone") or "UTC"), days, start, end)

    noise = data.get("noise") or {}
    replace = bool(noise.get("replace"))
    senders = _regexes(noise.get("senders"), "noise.senders", problems)
    subjects = _regexes(noise.get("subjects"), "noise.subjects", problems)
    if not replace:
        senders = tuple(re.compile(p, re.I) for p in DEFAULT_NOISE_SENDERS) + senders
        subjects = tuple(re.compile(p, re.I) for p in DEFAULT_NOISE_SUBJECTS) + subjects

    suppliers = _parties(data.get("suppliers"), "suppliers", problems, allow_parent=True)
    customers = _parties(data.get("customers"), "customers", problems, allow_parent=False)

    lanes: list[Lane] = []
    raw_lanes = data.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        problems.append("lanes: at least one lane is required")
        raw_lanes = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_lanes):
        w = f"lanes[{i}]"
        if not isinstance(raw, dict):
            problems.append(f"{w}: must be a mapping")
            continue
        lid = str(raw.get("id") or "")
        if not _SLUG.match(lid):
            problems.append(f"{w}.id: must be a lowercase slug")
        elif lid in seen_ids:
            problems.append(f"{w}.id: {lid!r} is used twice")
        seen_ids.add(lid)
        detect = _strs(raw.get("detect"), f"{w}.detect", problems) or ("new",)
        for d in detect:
            if d not in DETECTIONS:
                problems.append(f"{w}.detect: {d!r} is not one of {', '.join(DETECTIONS)}")
        match = raw.get("match") or {}
        if not isinstance(match, dict):
            problems.append(f"{w}.match: must be a mapping")
            match = {}
        owed = raw.get("replies_owed") or {}
        lanes.append(Lane(
            id=lid, label=str(raw.get("label") or lid), owner=str(raw.get("owner") or "self"),
            priority=_int(raw.get("priority"), 100, f"{w}.priority", problems),
            detect=detect,
            text=_regexes(match.get("text"), f"{w}.match.text", problems),
            from_=_regexes(match.get("from"), f"{w}.match.from", problems),
            exclude_from=_regexes(match.get("exclude_from"), f"{w}.match.exclude_from", problems),
            exclude_text=_regexes(match.get("exclude_text"), f"{w}.match.exclude_text", problems),
            owed_after_business_days=_int(owed.get("after_business_days"), 1, f"{w}.replies_owed.after_business_days", problems),
            owed_max_days=_int(owed.get("max_days"), 10, f"{w}.replies_owed.max_days", problems, minimum=1),
        ))

    fu = data.get("followups") or {}
    s = data.get("safety") or {}
    safety = Safety(
        pricing_patterns=_regexes(s.get("pricing_patterns"), "safety.pricing_patterns", problems),
        pricing_attachment_names=_regexes(s.get("pricing_attachment_names"), "safety.pricing_attachment_names", problems),
        allow_pricing_to=_domains(s.get("allow_pricing_to"), "safety.allow_pricing_to", problems),
    )
    due = _int(fu.get("due_business_days"), 3, "followups.due_business_days", problems, minimum=1)
    if problems:
        raise ConfigError(problems)
    return LanesConfig(
        company_name=str(company.get("name") or ""), internal_domains=internal, my_addresses=mine,
        hours=hours, noise_senders=senders, noise_subjects=subjects, suppliers=suppliers,
        customers=customers, lanes=tuple(lanes), followup_due_business_days=due, safety=safety,
    )


def load(path: str | Path) -> LanesConfig:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError([f"lanes file not found: {p}"])
    try:
        data = _yaml_load(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # YAML scanner/parser errors
        raise ConfigError([f"lanes file does not parse: {exc}"]) from None
    return parse(data)


def lane_ids(cfg: LanesConfig) -> Iterable[str]:
    return (lane.id for lane in cfg.lanes_by_priority())
