"""Thread context and "possibly waiting on this" linking (SPEC.md §2.3).

Signals (ported from the live router) when a message from a supplier arrives — which customer
thread is waiting on it?

====================================  =====
account-note tie                       +3
supplier name / alias (fuzzy)          +3
shared part / quote tokens             +2 each, max +4
timing: customer asked ≤3 days before
we first asked the supplier            +1
====================================  =====

Threshold 3. When any candidate has an account-note tie, weaker candidates (< 5) without one are
dropped. **Parent-company rule:** a supplier's ``parent`` is never used as a term — only the
supplier's own name, aliases and domain stem — so another brand under the same parent does not link.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable

from .config import LanesConfig, Party
from .graph import GraphClient, address_of, when
from .lanes import is_noise, is_us
from .notes import Notes
from .util import iso, now_of, parse_ts, text_of, who

PARTNUM = re.compile(r"\b(?=[A-Z0-9\-.]*\d)(?=[A-Z0-9\-.]*[A-Z])[A-Z0-9][A-Z0-9\-.]{3,}\b", re.I)
GENERIC_TOKENS = {"2024", "2025", "2026", "2027", "2028", "ext", "usa", "inc", "llc", "qty2", "line3"}
STOP_WORDS = {"re", "fw", "fwd", "rfq", "quote", "the", "and", "for", "from", "with", "sales", "external",
              "request", "your", "this", "that", "please", "question", "follow", "following", "update", "info"}
NAME_STOP = {"and", "the", "inc", "co", "llc", "ltd", "corp", "company", "group"}
THRESHOLD = 3
RELATED_DAYS = 21
LINK_DAYS = 14


def norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"[\u2019'`]", "", (text or "").lower()))


def mentions(text: str, term: str) -> bool:
    """Fuzzy multi-word match: every distinctive word of *term* appears (edit-tolerant) in *text*.

    "Granite and O Malley" ≈ "Granite & O'Malley": apostrophes are dropped and adjacent words are
    also joined, so "o malley" matches "omalley".
    """
    raw = [w for w in norm_words((term or "").replace("&", " ")) if w not in NAME_STOP]
    words = [w for w in raw if len(w) > 2]
    if not words:
        return False
    if len(words) < 2 and len(raw) > len(words):
        # e.g. "G&O Seals": dropping the short words would leave just "seals" → require the compact form
        def compact(s: str) -> str:
            return "".join(w for w in norm_words((s or "").replace("&", " ")) if w != "and")
        return compact(term) in compact(text)
    nw = norm_words(text)
    pool = set(nw) | {a + b for a, b in zip(nw, nw[1:])}
    return all(any(w == x or (len(w) > 4 and difflib.SequenceMatcher(None, w, x).ratio() >= 0.8) for x in pool)
               for w in words)


def tokens(text: str) -> set[str]:
    out = set()
    for t in PARTNUM.findall(text or ""):
        k = re.sub(r"[^a-z0-9]", "", t.lower())
        if len(k) >= 4 and k not in GENERIC_TOKENS:
            out.add(k)
    return out


def supplier_terms(supplier: Party | None, address: str) -> list[str]:
    """The supplier's own names only — never ``parent`` (live bug: parent-company false links)."""
    if supplier is None:
        stem = (address or "").rsplit("@", 1)[-1].split(".")[0]
        return [stem] if len(stem) >= 4 else []
    terms = [supplier.name, *supplier.aliases]
    terms += [d.split(".")[0] for d in supplier.domains if len(d.split(".")[0]) >= 4]
    parent = (supplier.parent or "").strip().lower()
    out = []
    for t in terms:
        if t and t.strip().lower() != parent and t not in out:
            out.append(t)
    return out


@dataclass
class Link:
    score: int
    message_id: str
    conversation_id: str
    sender: str
    subject: str
    received: str
    why: list[str] = field(default_factory=list)
    preview: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _note_domains(cfg: LanesConfig, notes: Notes | None, text: str, terms: list[str], supplier_dom: str) -> set[str]:
    """Customer domains whose account note mentions this supplier/contact/part (account-note tie)."""
    if not notes or not notes.enabled:
        return set()
    out = set()
    for name in notes.match(text + " " + " ".join(terms)):
        note = notes.find(name)
        if not note:
            continue
        for d in note.domains:
            if d == supplier_dom or d.endswith("." + supplier_dom) or cfg.supplier_for("x@" + d) or cfg.is_internal("x@" + d):
                continue
            out.add(d)
    return out


def links(*, cfg: LanesConfig, client: GraphClient, message_id: str, notes: Notes | None = None,
          days: int = LINK_DAYS, max_out: int = 4) -> list[Link]:
    """Other (customer) threads that this message may answer."""
    m = client.get_message(message_id)
    if not m:
        return []
    me = client.me().get("address", "")
    sender = address_of(m.get("from"))
    sdom = sender.rsplit("@", 1)[-1]
    supplier = cfg.supplier_for(sender)
    thread = client.conversation(m.get("conversationId") or "") or [m]
    participants = " ".join(((x.get("from") or {}).get("emailAddress") or {}).get("name") or "" for x in thread)
    text = " ".join(text_of(x) for x in thread) + " " + (m.get("subject") or "") + " " + participants
    toks = tokens(text)
    ours = [x for x in thread if not x.get("isDraft") and is_us(cfg, address_of(x.get("from")), me)]
    first_ask = min((when(x) for x in ours), default="")
    terms = supplier_terms(supplier, sender)
    noted = _note_domains(cfg, notes, text, terms, sdom)
    since = iso(now_of(client) - timedelta(days=days))
    lo = ""
    if first_ask:
        t = parse_ts(first_ask)
        lo = iso(t - timedelta(days=3)) if t else ""

    # Group candidate inbound mail by conversation: the customer's actual ask is often an older message
    # in the thread (the newest may just say "also price an actuator"), so score the thread as a whole.
    groups: dict[str, list[dict]] = {}
    for c in client.list_messages("inbox", since=since, top=1200):
        a = address_of(c.get("from"))
        conv = c.get("conversationId") or c["id"]
        if (conv == m.get("conversationId") or not a or is_us(cfg, a, me) or a.endswith("@" + sdom)
                or a.endswith("." + sdom) or cfg.supplier_for(a) or is_noise(cfg, c)):
            continue
        groups.setdefault(conv, []).append(c)  # newest first

    scored: list[Link] = []
    for conv, msgs in groups.items():
        c = msgs[0]
        a = address_of(c.get("from"))
        body = " ".join(text_of(x) + " " + (x.get("subject") or "") for x in msgs[:6])
        score, why = 0, []
        if a.rsplit("@", 1)[-1] in noted:
            score += 3
            why.append("account note ties this customer to the supplier thread")
        named = next((t for t in terms if mentions(body, t)), None)
        if named:
            score += 3
            why.append(f"mentions {named}")
        shared = toks & tokens(body)
        if shared:
            score += 2 * min(len(shared), 2)
            why.append("shares " + ", ".join(sorted(shared)[:3]))
        if lo and any(lo <= when(x) <= first_ask for x in msgs):
            score += 1
            why.append("asked shortly before we asked the supplier")
        if score >= THRESHOLD:
            scored.append(Link(score, c["id"], conv or "", who(c.get("from")), c.get("subject") or "",
                               when(c)[:16], why, text_of(c)[:300]))
    scored.sort(key=lambda r: (-r.score, r.received))
    if any(any("account note" in w for w in r.why) for r in scored):
        scored = [r for r in scored if any("account note" in w for w in r.why) or r.score >= 5]
    return scored[:max_out]


def _distinctive_words(subject: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9#\-]{4,}", subject or "") if w.lower() not in STOP_WORDS][:4]


def related(*, cfg: LanesConfig, client: GraphClient, message: dict, days: int = RELATED_DAYS) -> list[dict]:
    """Our own recent sent mail sharing distinctive subject words (e.g. the supplier we asked for them)."""
    words = _distinctive_words(message.get("subject") or "")
    if not words:
        return []
    since = iso(now_of(client) - timedelta(days=days))
    rows: list[dict] = []
    seen = {message.get("conversationId")}
    for q in ([" ".join(words[:2])] + words[:2]) if len(words) > 1 else words[:1]:
        try:
            hits = client.search_sent(q, since=since, top=15)
        except Exception:
            hits = []
        for r in hits:
            conv = r.get("conversationId")
            if conv in seen:
                continue
            seen.add(conv)
            msgs = client.conversation(conv or "")
            last = max(msgs, key=when) if msgs else {}
            rows.append({"conversation_id": conv, "subject": r.get("subject"), "sent": when(r)[:16],
                         "to": [who(x) for x in (r.get("toRecipients") or [])[:3]],
                         "last_message": {"from": who(last.get("from")), "at": when(last)[:16],
                                          "id": last.get("id")} if last else None,
                         "excerpt": text_of(r)[:400]})
        if len(rows) >= 6:
            break
    return rows[:6]


def thread_context(*, cfg: LanesConfig, client: GraphClient, message_id: str, notes: Notes | None = None,
                   max_chars: int = 900) -> dict:
    m = client.get_message(message_id)
    if not m:
        return {"ok": False, "error": f"message {message_id} not found"}
    me = client.me().get("address", "")
    msgs = client.conversation(m.get("conversationId") or "") or [m]
    out_msgs = []
    for x in sorted(msgs, key=when):
        frm = address_of(x.get("from"))
        out_msgs.append({"id": x.get("id"), "at": when(x)[:16], "draft": bool(x.get("isDraft")),
                         "from": who(x.get("from")), "ours": is_us(cfg, frm, me),
                         "to": [who(r) for r in (x.get("toRecipients") or [])[:4]],
                         "cc": [who(r) for r in (x.get("ccRecipients") or [])[:4]],
                         "text": text_of(x)[:max_chars]})
    last = out_msgs[-1] if out_msgs else {}
    sender = address_of(m.get("from"))
    supplier = cfg.supplier_for(sender)
    ball = ("with us" if last and not last["ours"] and not last["draft"] else
            "draft in progress" if last.get("draft") else "with them")
    note_names = []
    if notes and notes.enabled:
        note_names = list(dict.fromkeys(notes.for_address(sender) + notes.match(f"{m.get('subject', '')} {text_of(m)[:2000]}")))[:3]
    lk = links(cfg=cfg, client=client, message_id=message_id, notes=notes)
    result = {"ok": True, "subject": m.get("subject"), "conversation_id": m.get("conversationId"),
              "sender": who(m.get("from")), "sender_is_supplier": supplier.name if supplier else None,
              "ball_is": ball, "messages": out_msgs,
              "related_threads_we_started": related(cfg=cfg, client=client, message=m),
              "possibly_waiting_on_this": [x.as_dict() for x in lk],
              "account_notes": note_names}
    if lk:
        result["next"] = ("If one of the 'possibly waiting on this' threads is waiting on what arrived, reply on THAT "
                          "thread with inbox_chief_draft_reply(message_id=<their id>, attach_from_message_id="
                          f"{message_id}, attachment_names=[the non-pricing files]). Never forward supplier pricing.")
    return result
