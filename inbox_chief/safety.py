"""Safety rules S1–S5 (SPEC.md §1), as plain functions the tools and hooks call.

Nothing here talks to the network or to Hermes; callers pass in what they know (the staged draft,
the fresh copy of the draft from the mailbox, the user's last message, whether this is an
unattended run). Every check returns a :class:`Verdict` instead of raising, so a tool can turn a
refusal into a clear JSON error for the model.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

from .config import LanesConfig, Party
from .graph import address_of

SEND_MODES = ("approval_gate", "explicit_go")
UNATTENDED_PLATFORMS = frozenset({"cron", "kanban", "subagent", "oneshot", "batch"})

DEFAULT_GO_PHRASES = (
    "go", "go ahead", "send", "send it", "send that", "yes send", "yes, send", "yes send it",
    "yes, send it", "approved", "approve", "ship it", "looks good send", "looks good, send it",
)

# Pricing language (generic). Company lanes files add their own via safety.pricing_patterns.
PRICING_TEXT = (
    r"\$\s?\d[\d,]*(\.\d{2})?",
    r"\b\d[\d,]*(\.\d{2})?\s?(usd|eur|gbp|cad)\b",
    r"\b(unit|net|list|your|dealer|distributor|cost)\s+price\b",
    r"\bprice\s*(list|sheet|book|each|ea\.?)\b",
    r"\b(discount|multiplier)\s*(of|:)?\s*\d+(\.\d+)?\s?%",
    r"\b(quotation|quote)\s*(#|no\.?|number)\s*[A-Z0-9-]+",
    r"\bnet\s*\d{1,3}\s*(days)?\b.*\b(price|cost|each)\b",
)
PRICING_ATTACHMENT = (
    r"(quote|quotation|price|pricing|pricelist|price[-_ ]list|proposal|cost|invoice)",
)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    findings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "findings": list(self.findings)}


ALLOW = Verdict(True)


# ── S4: reply-all default ────────────────────────────────────────────────────────────────────────

def reply_action(sender_only: bool = False) -> str:
    """Graph action for a reply draft. Reply-all unless the caller explicitly opts out."""
    return "createReply" if sender_only is True else "createReplyAll"


# ── S3: fingerprint of what the human reviewed ───────────────────────────────────────────────────

def _norm_body(text: str) -> str:
    t = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def draft_fingerprint(draft: dict, attachment_names: Iterable[str] = ()) -> str:
    """Stable hash of recipients, subject, body text and attachment names."""
    parts = [
        ",".join(sorted(address_of(r) for r in draft.get("toRecipients") or [])),
        ",".join(sorted(address_of(r) for r in draft.get("ccRecipients") or [])),
        ",".join(sorted(address_of(r) for r in draft.get("bccRecipients") or [])),
        (draft.get("subject") or "").strip(),
        _norm_body((draft.get("body") or {}).get("content", "")),
        ",".join(sorted(attachment_names)),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


# ── S1/S2: the send gate ─────────────────────────────────────────────────────────────────────────

def is_go(message: str, extra_phrases: Iterable[str] = ()) -> bool:
    """True when the user's message is an unambiguous go (whole message, punctuation-insensitive)."""
    if "?" in (message or ""):
        return False  # a question is never a go
    t = re.sub(r"[^a-z0-9 ,']+", " ", (message or "").lower())
    t = re.sub(r"\s+", " ", t).strip(" ,")
    if not t or len(t) > 80:
        return False
    if re.search(r"\b(don'?t|do not|no|not|wait|hold|stop|change|edit|but|instead|later|after|tomorrow|"
                 r"draft|me|to)\b", t):
        return False
    phrases = {p.lower().strip() for p in (*DEFAULT_GO_PHRASES, *extra_phrases) if p.strip()}
    if t in phrases:
        return True
    # "go on the Acme one", "send the valve reply" -> go + a selector; selector checked by caller
    return bool(re.match(r"^(go|send|yes send|approved?)\b", t))


def go_selects(message: str, draft_id: str, subject: str) -> bool:
    """Does a go message name this draft (short id or two+ distinctive subject words)?"""
    t = (message or "").lower()
    if draft_id and draft_id[-6:].lower() in t:
        return True
    words = {w for w in re.findall(r"[a-z0-9][a-z0-9-]{3,}", (subject or "").lower())
             if w not in {"re", "fw", "fwd", "with", "from", "your", "this", "that", "request"}}
    return len([w for w in words if w in t]) >= 2


def send_gate(*, mode: str, staged: dict | None, live_draft: dict | None, live_attachment_names: Iterable[str],
              unattended: bool, last_user_message: str = "", pending_in_session: int = 0,
              extra_go_phrases: Iterable[str] = ()) -> Verdict:
    """Decide whether ``inbox_chief_send`` may send this draft.

    ``staged``: the pending entry (``draft_id``, ``subject``, ``fingerprint``, ``session_id``).
    ``live_draft``: the draft as it is in the mailbox right now (None if gone).
    In ``approval_gate`` mode the human confirmation itself happens in Hermes's approval prompt
    (``pre_tool_call`` → approve); this gate still enforces S2/S3 and staging.
    """
    if mode not in SEND_MODES:
        return Verdict(False, f"unknown send_confirmation mode {mode!r}; refusing to send")
    if unattended:
        return Verdict(False, "refusing to send from an unattended run (cron, worker or subagent); "
                              "stage the draft and ask the user for a go")
    if not staged:
        return Verdict(False, "this draft was not staged by Inbox Chief; stage it with a draft tool and get a go first")
    if live_draft is None or not live_draft.get("isDraft", False):
        return Verdict(False, "the draft is no longer in Drafts (already sent or deleted); nothing sent")
    fp = draft_fingerprint(live_draft, live_attachment_names)
    if staged.get("fingerprint") and fp != staged["fingerprint"]:
        return Verdict(False, "the draft changed after it was staged; show the user the new text, re-stage it "
                              "and get a fresh go")
    if mode == "explicit_go":
        if not is_go(last_user_message, extra_go_phrases):
            return Verdict(False, "the user's latest message is not an explicit go for this draft")
        if pending_in_session != 1 and not go_selects(last_user_message, staged.get("draft_id", ""),
                                                       staged.get("subject", "")):
            return Verdict(False, "several drafts are waiting; ask the user which one to send")
    return ALLOW


# ── S5: never forward supplier pricing ───────────────────────────────────────────────────────────

def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


_PRICING_TEXT = _compile(PRICING_TEXT)
_PRICING_ATT = _compile(PRICING_ATTACHMENT)


def pricing_hits(text: str, cfg: LanesConfig | None = None) -> list[str]:
    pats = _PRICING_TEXT + list(cfg.safety.pricing_patterns if cfg else ())
    hits = []
    for p in pats:
        m = p.search(text or "")
        if m:
            hits.append(m.group(0)[:40])
    return hits


def pricing_attachment(name: str, cfg: LanesConfig | None = None) -> bool:
    pats = _PRICING_ATT + list(cfg.safety.pricing_attachment_names if cfg else ())
    return any(p.search(name or "") for p in pats)


def check_outbound(*, cfg: LanesConfig, recipients: Iterable[str], body_text: str,
                   attachments: Iterable[tuple[str, str]] = (), source_messages: Iterable[dict] = ()) -> Verdict:
    """Block supplier pricing from reaching anyone outside the company and the supplier itself.

    ``attachments``: ``(file name, sender address of the message it came from)``.
    ``source_messages``: messages whose content is quoted/forwarded into this draft (e.g. the
    thread being replied to, or the message attachments are copied from).
    A recipient is *safe* for a given supplier when it is internal, in ``allow_pricing_to``, or a
    domain of that same supplier (sending a supplier its own price back is fine).
    """
    rcpts = [r.lower() for r in recipients if r]
    allowed = set(cfg.safety.allow_pricing_to)

    def unsafe_for(supplier: Party) -> list[str]:
        return [r for r in rcpts if not cfg.is_internal(r) and r.rsplit("@", 1)[-1] not in allowed
                and not supplier.owns(r)]

    findings: list[str] = []
    for name, origin in attachments:
        sup = cfg.supplier_for(origin)
        if sup and pricing_attachment(name, cfg):
            bad = unsafe_for(sup)
            if bad:
                findings.append(f"attachment {name!r} from {sup.name} looks like pricing; recipients outside "
                                f"{sup.name}/your company: {', '.join(bad)}")
    for src in source_messages:
        sup = cfg.supplier_for(address_of(src.get("from")))
        if not sup:
            continue
        src_text = f"{src.get('subject', '')} {_norm_body((src.get('body') or src.get('uniqueBody') or {}).get('content', ''))}"
        hits = pricing_hits(src_text, cfg)
        if hits and unsafe_for(sup):
            quoted = [h for h in hits if h.lower() in (body_text or "").lower()]
            # Quoting the supplier's message (reply/forward history) or restating its figures leaks it.
            if quoted or src.get("_included_in_draft"):
                findings.append(f"draft carries {sup.name} pricing ({', '.join((quoted or hits)[:3])}) to "
                                f"{', '.join(unsafe_for(sup))}")
    if findings:
        return Verdict(False, "never forward supplier pricing: remove the pricing (or the supplier's quoted "
                              "message/attachment) and write the customer price yourself", tuple(findings))
    return ALLOW


# ── unattended detection helper for hooks/tools ──────────────────────────────────────────────────

def is_unattended(*, platform: str = "", cron_execution: object = None, parent_session_id: str = "",
                  environ: dict | None = None) -> bool:
    """Cron run, subagent, kanban worker or a batch/oneshot platform. ``pre_tool_call`` does not receive
    ``platform``, so the kanban worker marker (a plain task id, not a credential) is checked too."""
    import os
    env = os.environ if environ is None else environ
    return (bool(cron_execution) or bool(parent_session_id) or bool(env.get("HERMES_KANBAN_TASK"))
            or (platform or "").lower() in UNATTENDED_PLATFORMS)
