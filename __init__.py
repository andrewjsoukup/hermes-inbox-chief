"""Inbox Chief — Hermes plugin registration.

Everything here uses public plugin surfaces only (SPEC.md §8): ``ctx.register_tool``,
``ctx.register_hook``, ``ctx.register_command``, ``ctx.register_cli_command``,
``ctx.register_automation_blueprint``, ``ctx.register_skill``, ``ctx.state``, ``ctx.get_config``,
``ctx.current_cron_execution`` and (inside the Graph client) ``credentialed_provider_request``.
``register()`` performs no I/O and no network access.
"""
from __future__ import annotations

import logging
from pathlib import Path

try:  # Hermes loads this directory as a namespaced package
    from .inbox_chief import schemas
    from .inbox_chief.config import ConfigError, load as load_lanes
    from .inbox_chief.engine import EngineHolder
    from .inbox_chief.hooks import Hooks
    from .inbox_chief.tools import Tools
except ImportError:  # imported as a top-level module (pytest collecting the repo root)
    from inbox_chief import schemas  # type: ignore[no-redef]
    from inbox_chief.config import ConfigError, load as load_lanes  # type: ignore[no-redef]
    from inbox_chief.engine import EngineHolder  # type: ignore[no-redef]
    from inbox_chief.hooks import Hooks  # type: ignore[no-redef]
    from inbox_chief.tools import Tools  # type: ignore[no-redef]

log = logging.getLogger("inbox_chief")
TOOLSET = "inbox_chief"
_HERE = Path(__file__).resolve().parent

SCAN_PROMPT = (
    "Run inbox_chief_scan with scheduled=true. If it returns [SILENT], reply exactly [SILENT] and stop. "
    "Otherwise, for each item: read it with inbox_chief_thread, decide what is needed, stage a reply with "
    "inbox_chief_draft_reply when a reply is due (reply-all by default; never include supplier pricing), and "
    "report a short list: who, what they need, what you staged, and ask the user for go / change / skip on "
    "each draft. Never call inbox_chief_send from this scheduled run."
)


class _LazyState:
    """Defers ``ctx.state`` until a tool or hook actually needs plugin state."""

    def __init__(self, ctx):
        self._ctx = ctx

    def get(self, key, default=None):
        return self._ctx.state.get(key, default)

    def set(self, key, value):
        self._ctx.state.set(key, value)


def _cron(ctx):
    def current():
        try:
            return ctx.current_cron_execution()
        except Exception:
            return None
    return current


def _slash_inbox(holder: EngineHolder, tools: Tools):
    def handler(raw_args: str = "", **_kw) -> str:
        import json
        st = json.loads(tools.status({"live": False}))
        if not st.get("ok"):
            return st.get("error", "Inbox Chief is not ready.")
        pend = json.loads(tools.pending({}))
        lines = [f"Inbox Chief — {st.get('company') or 'unnamed company'} ({st['mailbox']} mailbox)",
                 "Lanes: " + ", ".join(f"{l['label']} → {l['owner']}" for l in st["lanes"])]
        rows = pend.get("pending", []) if pend.get("ok") else []
        lines.append(f"Drafts awaiting your go: {len(rows)}")
        for p in rows[:8]:
            lines.append(f"  • {p['summary'] or p['subject']}  (to {', '.join(p['to'] or [])})")
        return "\n".join(lines)
    return handler


def _cli(holder: EngineHolder, tools: Tools):
    def setup(sub):
        sp = sub.add_subparsers(dest="inbox_chief_cmd")
        sp.add_parser("status", help="Show configuration and pending drafts")
        c = sp.add_parser("check-config", help="Validate a lanes YAML file")
        c.add_argument("file", nargs="?", default="")
        sub.set_defaults(func=run)

    def run(args):
        import json
        cmd = getattr(args, "inbox_chief_cmd", None)
        if cmd == "check-config":
            path = args.file or str(_HERE / "lanes.example.yaml")
            try:
                cfg = load_lanes(path)
            except ConfigError as exc:
                print(f"{path}: {len(exc.problems)} problem(s)")
                for p in exc.problems:
                    print(f"  - {p}")
                return 1
            print(f"{path}: OK — {len(cfg.lanes)} lanes, {len(cfg.suppliers)} suppliers, "
                  f"{len(cfg.customers)} customers")
            return 0
        if cmd == "status":
            print(json.dumps(json.loads(tools.status({})), indent=2))
            return 0
        print("usage: hermes inbox-chief {status|check-config [FILE]}")
        return 0
    return setup, run


def register(ctx):
    # ctx.state is read on first use, not here: register() stays side-effect free (and the catalog
    # validator's isolated probe ctx has no state facade).
    holder = EngineHolder(lambda key, default=None: ctx.get_config(key, default=default), _LazyState(ctx))
    cron = _cron(ctx)
    tools = Tools(holder, profile=getattr(ctx, "profile_name", "default"), cron_execution=cron)
    hooks = Hooks(holder, cron_execution=cron, profile=getattr(ctx, "profile_name", "default"))

    handlers = {
        "inbox_chief_status": tools.status,
        "inbox_chief_scan": tools.scan,
        "inbox_chief_thread": tools.thread,
        "inbox_chief_draft_reply": tools.draft_reply,
        "inbox_chief_draft_new": tools.draft_new,
        "inbox_chief_pending": tools.pending,
        "inbox_chief_send": tools.send,
        "inbox_chief_resolve": tools.resolve,
        "inbox_chief_notes": tools.notes,
    }
    for schema in schemas.ALL:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handlers[schema["name"]])

    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)

    ctx.register_command("inbox", handler=_slash_inbox(holder, tools),
                         description="Inbox Chief: lanes and drafts awaiting your go")
    setup, run = _cli(holder, tools)
    ctx.register_cli_command(name="inbox-chief", help="Inbox Chief (Outlook lanes + go/no-go drafts)",
                             setup_fn=setup, handler_fn=run)

    try:
        ctx.register_automation_blueprint(
            "scan",
            title="Inbox Chief scan",
            description="Every 15 minutes during your hours, route new email to its lane, stage reply drafts, and "
                        "ask you for go/no-go. Silent when nothing is new. Never sends.",
            category="email",
            schedule_template="*/15 {start_hour}-{end_hour} * * {dow}",
            prompt_template=SCAN_PROMPT,
            slots=[
                {"name": "start_hour", "type": "text", "label": "First hour (0-23)", "default": "6"},
                {"name": "end_hour", "type": "text", "label": "Last hour (0-23)", "default": "18"},
                {"name": "recurrence", "type": "weekdays", "label": "Repeat on", "default": "weekdays"},
                {"name": "deliver", "type": "enum", "label": "Where to deliver?", "default": "origin",
                 "options": ["origin", "local", "telegram"], "strict": False},
            ],
            skills=["inbox-chief:triage"],
            tags=["email", "outlook"],
        )
    except Exception as exc:  # older/newer hosts: the plugin still works without the blueprint
        log.warning("inbox-chief: blueprint not registered: %s", exc)

    skill = _HERE / "skills" / "triage" / "SKILL.md"
    if skill.is_file():
        try:
            ctx.register_skill("triage", skill, description="Work the Inbox Chief queue: research, stage, go/no-go.")
        except Exception as exc:
            log.warning("inbox-chief: skill not registered: %s", exc)
