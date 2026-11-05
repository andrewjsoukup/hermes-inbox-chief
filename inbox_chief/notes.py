"""Shared account notes (SPEC.md §2.6): one markdown file per account in a configurable folder.

Format::

    ---
    aliases: [Other Name, contact person, domain.example]
    type: account
    ---
    # Name
    ## Summary
    ## Open items
    ## Log
    - 2026-11-07 (agent): newest line first

The folder may be team-synced (OneDrive/SharePoint, Git, an Obsidian vault). Concurrency model:
log lines are inserted newest-first and the file is replaced atomically (write temp + ``os.replace``);
there is no cross-machine locking (documented limitation). The log keeps ``MAX_LOG`` lines; older
lines roll into ``_archive/<Name>.md``.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_LOG = 40
MAX_BODY = 1800
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_ALIASES = re.compile(r"^aliases:\s*\[(.*?)\]\s*$", re.M)
_ALIASES_BLOCK = re.compile(r"^aliases:\s*\n((?:\s*-\s*.+\n?)+)", re.M)

TEMPLATE = """---
aliases: [{aliases}]
type: account
---
# {name}

## Summary

## Open items

## Log
"""


@dataclass(frozen=True)
class Note:
    name: str
    path: Path
    terms: tuple[str, ...]   # name + aliases (names, contacts, domains)
    mtime: float

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(t.lower().lstrip("@") for t in self.terms if "." in t and " " not in t and "@" not in t)


def safe_name(name: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip().strip(".")
    return n[:120]


def _parse_aliases(text: str) -> list[str]:
    m = _FRONT.match(text)
    if not m:
        return []
    fm = m.group(1) + "\n"
    a = _ALIASES.search(fm)
    if a:
        return [x.strip().strip("'\"") for x in a.group(1).split(",") if x.strip()]
    b = _ALIASES_BLOCK.search(fm)
    if b:
        return [ln.strip()[1:].strip().strip("'\"") for ln in b.group(1).splitlines() if ln.strip().startswith("-")]
    return []


def _pattern(term: str) -> re.Pattern | None:
    term = term.strip()
    if len(term) < 3:
        return None
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".inbox-chief-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Notes:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory).expanduser() if directory else None

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    def all(self) -> dict[str, Note]:
        if not self.dir or not self.dir.is_dir():
            return {}
        out: dict[str, Note] = {}
        for p in sorted(self.dir.glob("*.md")):
            if p.name.startswith(("_", ".")):
                continue
            try:
                text = p.read_text(encoding="utf-8-sig")
                st = p.stat()
            except OSError:
                continue
            out[p.stem] = Note(p.stem, p, tuple([p.stem] + _parse_aliases(text)), st.st_mtime)
        return out

    def find(self, name: str) -> Note | None:
        notes = self.all()
        if name in notes:
            return notes[name]
        low = (name or "").lower()
        return next((n for k, n in notes.items() if k.lower() == low), None)

    def match(self, text: str) -> list[str]:
        hits = []
        for name, note in self.all().items():
            for t in note.terms:
                pat = _pattern(t)
                if pat and pat.search(text or ""):
                    hits.append(name)
                    break
        return hits

    def for_address(self, address: str) -> list[str]:
        """Notes whose aliases list this address or its domain."""
        a = (address or "").lower()
        dom = a.rsplit("@", 1)[-1]
        out = []
        for name, note in self.all().items():
            terms = {t.lower().lstrip("@") for t in note.terms}
            if a in terms or dom in terms:
                out.append(name)
        return out

    def body(self, name: str, max_chars: int = MAX_BODY) -> str:
        note = self.find(name)
        if not note:
            return ""
        t = _FRONT.sub("", note.path.read_text(encoding="utf-8-sig"), count=1).strip()
        return t if len(t) <= max_chars else t[:max_chars] + "…"

    def add(self, name: str, *, agent: str, text: str, aliases: str | list[str] = "", today: str) -> Note:
        if not self.dir:
            raise ValueError("account notes are off (set notes_dir)")
        if not (text or "").strip():
            raise ValueError("text is required")
        existing = self.find(safe_name(name))
        name = existing.name if existing else safe_name(name)
        if not name:
            raise ValueError("a note name is required")
        path = self.dir / f"{name}.md"
        new_aliases = [a.strip() for a in (aliases.split(",") if isinstance(aliases, str) else aliases) if a.strip()]
        if path.exists():
            t = path.read_text(encoding="utf-8-sig")
        else:
            t = TEMPLATE.format(name=name, aliases=", ".join(new_aliases))
            new_aliases = []
        if new_aliases:
            cur = _parse_aliases(t)
            merged = cur + [a for a in new_aliases if a.lower() not in {c.lower() for c in cur}]
            if _FRONT.match(t) and _ALIASES.search(_FRONT.match(t).group(1) + "\n"):
                t = _ALIASES.sub("aliases: [" + ", ".join(merged) + "]", t, count=1)
            elif _FRONT.match(t):
                t = t.replace("---\n", "---\naliases: [" + ", ".join(merged) + "]\n", 1)
            else:
                t = f"---\naliases: [{', '.join(merged)}]\n---\n" + t
        line = f"- {today} ({safe_name(agent) or 'agent'}): {' '.join(text.split())}"
        if "## Log" not in t:
            t = t.rstrip() + "\n\n## Log\n"
        head, log = t.split("## Log", 1)
        lines = [ln for ln in log.strip().splitlines() if ln.strip()]
        lines.insert(0, line)
        if len(lines) > MAX_LOG:
            arch = self.dir / "_archive" / f"{name}.md"
            arch.parent.mkdir(parents=True, exist_ok=True)
            with arch.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines[MAX_LOG:]) + "\n")
            lines = lines[:MAX_LOG]
        _atomic_write(path, head + "## Log\n" + "\n".join(lines) + "\n")
        return self.find(name)  # type: ignore[return-value]
