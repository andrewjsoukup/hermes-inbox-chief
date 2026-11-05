# Inbox Chief — specification (v0.1)

Inbox Chief turns an Outlook (Microsoft 365) inbox into a small, safe chief-of-staff queue inside
Hermes: it routes every new email to exactly one **lane** (owner), notices replies you owe and replies
you are waiting on, connects a supplier's answer to the customer who asked for it, stages reply drafts,
and sends a draft **only after an explicit human go**.

It is a port of a working personal system (a router script, an email tracker, an alert/pending-draft
store, shared account notes and three `pre_llm_call` plugins) into one installable, catalog-clean
Hermes plugin that any Hermes user can configure for their own company.

This document is the contract for the follow-up cards (core engine, demo dataset, desktop panel,
catalog PR). Decisions marked **DECIDED** are not to be re-litigated inside a child card; change them
here first.

---

## 1. Safety rules (first-class, enforced in code — not just prompts)

| # | Rule | Where enforced |
|---|---|---|
| S1 | **Send only on explicit go.** No code path sends mail without a human signal for that exact draft. | `safety.send_gate()` + `pre_tool_call` hook on `inbox_chief_send` |
| S2 | **Never sends unattended.** A send inside a cron run, subagent or kanban worker is refused. | `safety.send_gate()` (uses `ctx.current_cron_execution()` and the hook `platform`) |
| S3 | **Send the draft that was reviewed.** A staged draft records a content fingerprint; if the draft changed after staging, the send is refused until it is re-staged and re-approved. | `safety.draft_fingerprint()` / `send_gate()` |
| S4 | **Reply-all by default.** Reply drafts use Graph `createReplyAll`; `sender_only=true` is the explicit opt-out. | `safety.reply_action()` |
| S5 | **Never forward supplier pricing.** Outbound drafts to anyone outside the company *and* outside the supplier's own domains are checked: forwarding a supplier message, quoting supplier history that contains pricing, or attaching a supplier file that looks like a quote/price list is blocked. | `safety.check_outbound()` at draft time and again at send time |
| S6 | **Read-mostly.** The only mailbox writes are: create/patch drafts, add attachments to drafts, set the reply/reply-all icon (extended property on the answered message), and send an approved draft. No deletes, moves or rules. | `graph.GraphClient` surface (there is no delete method) |
| S7 | **Tests never touch a real mailbox.** The test suite uses `FixtureGraphClient`; a conftest guard makes any network socket or Hermes credential call fail the test. | `tests/conftest.py` |

`send_confirmation` setting (S1):

- `approval_gate` (default): `inbox_chief_send` is escalated to Hermes's own approval prompt via
  `pre_tool_call` → `{"action": "approve"}`. Works in CLI, TUI, Desktop and messaging approval buttons;
  under cron/unattended Hermes refuses, which is the desired fail-closed behaviour.
- `explicit_go`: for chat-first users (Telegram "go"). The `pre_llm_call` hook records the user's latest
  message per session; the send is allowed only if that message is a go phrase (`go`, `send it`,
  `yes send`, `approved`, configurable) **and** exactly one pending draft is staged in that session or
  the message names the draft (short id or subject words). Ambiguous → refused with "ask which draft".

Neither mode can be turned into "auto-send". There is no setting for it.

## 2. Features

### 2.1 Lanes (routing) — engine card
- Configurable lanes: `id`, `label`, `owner`, `priority`, match rules (regex on subject+preview,
  sender, exclusions), which detections the lane runs, and replies-owed thresholds.
- **One owner per email** (DECIDED): lanes are evaluated in ascending `priority`; first match wins.
  Every profile that runs Inbox Chief evaluates the same file, so ownership is deterministic and no
  email is reported by two owners.
- Global noise filters (automated senders, auto-replies, receipts/subscriptions, the user's own
  personal addresses) run before lanes.
- "Already handled" rule: a thread is owned once anyone at the company has sent in it or a draft
  exists in it. New-item detection skips handled threads.
- Business hours window (`hours`) gates scheduled scans only; manual scans always run.

### 2.2 Detections — engine card
| Kind | Definition (ported from live system) |
|---|---|
| `new` | Newest message of a thread, external sender, not noise, thread not handled, matches a lane. |
| `customer_reply` | External non-supplier sender wrote last in a thread where we wrote earlier; no draft in thread; same-day (no business-day wait). Must catch replies on **threads we started ourselves** (live bug). |
| `supplier_reply` | A supplier-domain sender replied in a thread where we asked them something; includes attachment names; flags for "did they answer every question". |
| `replies_owed` | External sender wrote last, we wrote earlier, nobody answered for ≥ N business days. |
| `overdue` | A sent tracked draft that needs a reply got none within its due window. |
| `replied` | A tracked sent draft received a reply (routes to the owner who drafted it). |

Dedupe (DECIDED): alert key = `conversationId[-16:] | kind | <timestamp of the triggering message to the minute>`.
One alert **per reply**, not per thread (a second reply in the same thread is a new alert); several
tracked drafts in one thread produce one alert. Handled keys are kept (last 500) per profile.
Threads followed by the tracker are excluded from `customer_reply`/`supplier_reply` lanes so the same
reply is not reported twice (live bug: duplicate alerts).

### 2.3 Thread context + "possibly waiting on this" — engine card
- `inbox_chief_thread(message_id)` returns the whole conversation (oldest→newest unique bodies, drafts
  marked), related threads we started in the last 21 days that share distinctive subject words, and
  linked threads.
- Linking signals and scores (ported): account-note tie (+3), supplier name/alias mentioned with fuzzy
  matching (+3; e.g. "Granite and O Malley" ≈ "Granite & O'Malley"), shared part/quote tokens (+2 each, max
  +4), timing — customer asked within 3 days before we asked the supplier (+1). Threshold 3. When an
  account-note tie exists, drop weaker name-only coincidences (<5).
- **Parent-company rule** (live bug): a supplier's `parent` name is never a link signal on its own and
  is excluded from alias matching; only the supplier's own names/aliases/domains count.
- Hero flow: supplier sends a cut sheet → thread shows "possibly waiting on this: <customer thread>" →
  agent stages a reply-all draft on the customer thread with `attach_from_message_id` → user says go.

### 2.4 Drafts, pending list, send — engine card (safety primitives in this card)
- `inbox_chief_draft_reply` (reply-all default, optional attachments copied from a source message,
  optional signature) and `inbox_chief_draft_new`.
- Every draft is (a) checked by `safety.check_outbound()`, (b) tracked in the ledger, (c) staged in
  the profile's pending list with summary + fingerprint.
- Pending list is **pruned live against the mailbox** before it is shown or injected (a draft the user
  sent or deleted himself disappears — live bug: stale pending drafts).
- `inbox_chief_send(draft_id)` → S1–S5 → Graph `send` → ledger `sent` → reply icon on the answered
  message (PidTagLastVerbExecuted 0x1081 = 102 reply / 103 reply-all, 0x1082 = time).
- `inbox_chief_resolve(draft_id, outcome=dropped)` for "skip".

### 2.5 Tracker (ledger) — engine card
Port of `email_tracker.py`: track drafts → `sent` → `replied` / `overdue` / `discarded`, polled by the
scheduled scan. Live check on demand (`inbox_chief_pending` / `inbox_chief_status` with
`live=true`) — agents must never state sent/unsent from memory.

### 2.6 Account notes (team-shareable) — engine card
- A directory of markdown notes, one per account (`<Name>.md`), YAML front matter `aliases: [..]`
  (names, contact names, domains), sections `## Summary`, `## Open items`, `## Log` (newest first,
  capped at 40 lines, overflow appended to `_archive/<Name>.md`).
- The directory is configured (`notes_dir`) and may be a synced team folder (OneDrive/SharePoint,
  Git, Obsidian vault). Concurrent writers: append-only log lines, atomic replace, no locking beyond
  that (documented limitation).
- `pre_llm_call` injects up to 3 matching notes per turn, each at most once per session unless it
  changed (mtime).

### 2.7 Context injection (`pre_llm_call`)
One hook, three blocks, each skipped when empty, total ≤ 6,000 chars (under Hermes's 10k spill cap):
1. **Pending drafts awaiting go** for this profile (pruned live, max 6).
2. **Tracker events** since last turn for this profile (sent / replied / overdue; seen-state stored
   in plugin state, never in the shared ledger).
3. **Account notes** matching the user message.
Skipped for subagent turns (`parent_session_id`) and when the plugin is unconfigured.

### 2.8 Scheduling
DECIDED: an **Automation Blueprint** `inbox-chief:scan` (`ctx.register_automation_blueprint`) the user
schedules from `/blueprint` or Desktop Cron (default `*/15` during configured hours). The cron prompt
calls `inbox_chief_scan(scheduled=true)`, which returns `NO_NEW_ITEMS` when nothing is new, so the
agent stays silent. No background threads or daemons in the plugin. Push (Graph change
notifications) is out of scope for v0.1.

### 2.9 Surfaces
- Tools (below), one `pre_llm_call` hook, one `pre_tool_call` hook.
- Slash command `/inbox` (queue summary: per-lane new items, pending drafts, overdue).
- CLI `hermes inbox-chief status | check-config [FILE]` (scaffold); `demo` (full scan against the
  bundled fixture mailbox, never a real one) lands with the demo-dataset card. Note: in
  `plugins.isolation: host` mode Hermes skips `register_cli_command`; the slash command and tools
  still work.
- Desktop panel (`desktop/plugin.js`, SDK only) — desktop card.
- Bundled skill `inbox-chief:triage` — how to work the queue (research → stage → ask go/no-go).

### 2.10 Out of scope for v0.1
Session-continuity (generic, not email; stays a separate plugin), Telegram/specialist bot routing
(Hermes delivery handles where cron output goes), LLM classifier (regex lanes; classifier is a later
drop-in behind `lanes.classify()`), CRM/QuickBooks enrichment, Graph webhooks, shared/delegated
mailboxes.

## 3. Tools (DECIDED names; all return JSON strings, never raise)

| Tool | Purpose | Writes? |
|---|---|---|
| `inbox_chief_status` | Config + sign-in state, lanes, counts; `live=true` re-polls tracked drafts. | no |
| `inbox_chief_scan` | Run detections; returns unseen items grouped by lane; `lane=`, `kinds=`, `scheduled=`, `mark_seen=`. | plugin state |
| `inbox_chief_thread` | Thread context + related + "possibly waiting on this" for a message id. | no |
| `inbox_chief_draft_reply` | Reply-all draft (default), `sender_only`, `body`, `attach_from_message_id`, `summary`; safety-checked, tracked, staged. | draft |
| `inbox_chief_draft_new` | New draft (`to`, `cc`, `subject`, `body`, attachments from a message); same checks. | draft |
| `inbox_chief_pending` | Staged drafts awaiting go (pruned live). | plugin state |
| `inbox_chief_send` | Send one staged draft after the go (S1–S5). | **sends** |
| `inbox_chief_resolve` | Mark a staged draft dropped/skipped. | plugin state |
| `inbox_chief_notes` | Account notes: `action=match|show|add|list`. | notes dir (add) |

## 4. Architecture

```
plugin.yaml / __init__.py        register(): tools, hooks, /inbox, CLI, blueprint, skill
inbox_chief/                     engine package (pure Python, no Hermes imports at module top)
  config.py      load + validate lanes.yaml → LanesConfig (dataclasses); built-in defaults
  graph.py       GraphClient protocol; HermesGraphClient (credentialed_provider_request);
                 FixtureGraphClient (JSON mailbox, in-memory writes, never network)
  safety.py      send_gate, reply_action, draft_fingerprint, check_outbound, pricing detectors
  state.py       per-profile JSON state (handled keys, pending, seen, ledger) with atomic writes
  lanes.py       noise filters, classify(), detections            (engine card)
  linking.py     thread context, related threads, link scoring   (engine card)
  ledger.py      draft tracking, poll, reply icon                 (engine card)
  notes.py       account notes read/match/add                     (engine card)
  drafts.py      draft create/stage/prune/send orchestration      (engine card)
  context.py     pre_llm_call block builder                       (engine card)
  tools.py       tool handlers (JSON in/out) → engine
  schemas.py     tool schemas (what the model sees)
  hooks.py       pre_llm_call / pre_tool_call callbacks
  cli.py         hermes inbox-chief …
skills/triage/SKILL.md
desktop/plugin.js                                                 (desktop card)
fixtures/demo/mailbox.json      fictional mailbox (demo card expands it)
tests/                           pytest, offline only
```

Runtime: one `Engine` object per process holds `(config, graph_client, state)`; built lazily on first
use from plugin settings, so `register()` does no I/O and no network (the validator's probe and
Plugin Doctor block sockets during `register()`).

### 4.1 Graph client interface (DECIDED)
High-level operations, not raw URLs, so the fixture client needs no OData parser:

```python
class GraphClient(Protocol):
    def me(self) -> dict                                   # {"address", "name"}
    def list_messages(self, folder, *, since=None, top=100) -> list[dict]   # newest first
    def get_message(self, message_id) -> dict | None       # None when deleted / not found
    def conversation(self, conversation_id) -> list[dict]  # all folders incl. drafts, oldest first
    def search_sent(self, query, *, since=None, top=15) -> list[dict]
    def list_attachments(self, message_id) -> list[dict]   # includes contentBytes
    def create_draft(self, message: dict) -> dict
    def create_reply_draft(self, message_id, *, reply_all=True) -> dict
    def update_message(self, message_id, patch: dict) -> dict
    def add_attachment(self, message_id, attachment: dict) -> dict
    def send_draft(self, message_id) -> None
    def set_extended_properties(self, message_id, props: list[dict]) -> None
```
Messages are Graph-shaped dicts (`id`, `conversationId`, `subject`, `from`, `toRecipients`,
`ccRecipients`, `bodyPreview`, `uniqueBody`, `body`, `receivedDateTime`, `sentDateTime`,
`lastModifiedDateTime`, `isDraft`, `hasAttachments`, `parentFolder`). `HermesGraphClient` calls
`credentialed_provider_request("microsoft", …)` against `https://graph.microsoft.com/v1.0` and maps
404/`ErrorItemNotFound` to `None`.

### 4.2 Fixture format (DECIDED — demo card builds on this)
`fixtures/<name>/mailbox.json`:
```json
{
  "me": {"address": "pat@northwind-demo.example", "name": "Pat Rivera"},
  "now": "2026-11-10T15:00:00Z",
  "messages": [ { "id": "m001", "parentFolder": "inbox", "conversationId": "c001", ... ,
                  "attachments": [{"name": "cut-sheet.pdf", "contentType": "application/pdf",
                                   "size": 120000, "isInline": false, "contentBytes": "<base64>"}] } ]
}
```
`now` pins the clock for deterministic tests. Writes (drafts, sends, property patches) mutate the
in-memory copy only and are recorded in `client.log` for assertions. All names/domains are
fictional and use the reserved `.example` TLD.

## 5. Configuration

### 5.1 Plugin settings (`plugins.entries.inbox-chief.settings`, Desktop form via `config_schema`)
| Key | Type | Default | Meaning |
|---|---|---|---|
| `lanes_file` | str | `""` | Path to your lanes YAML (private; keep it outside the plugin repo). Empty → plugin runs in "unconfigured" mode and tools explain setup. |
| `notes_dir` | str | `""` | Account-notes directory (may be a team-synced folder). Empty → notes off. |
| `mailbox` | str (`graph`/`fixture`) | `graph` | `fixture` reads `fixture_path` instead of Microsoft Graph. |
| `fixture_path` | str | `""` | Fixture mailbox JSON (demo/testing). Empty → bundled demo mailbox. |
| `send_confirmation` | str (`approval_gate`/`explicit_go`) | `approval_gate` | See S1. |
| `go_phrases` | list | see code | Extra go phrases for `explicit_go`. |
| `scan_lookback_days` | int | `2` | Window for new items / customer replies. |
| `owner` | str | `""` | Which lane owner this profile acts as; empty = profile name, and lanes with `owner: self` always match. |

Secrets: only `INBOX_CHIEF_MS_CLIENT_ID` (public client id — PKCE, no client secret), declared under
`requires_env` and read by Hermes, never by plugin code.

### 5.2 Lanes file schema (`lanes.yaml`, `version: 1`)
See `lanes.example.yaml` for a complete, commented example (fictional "Northwind Process Supply").
```yaml
version: 1
company:
  name: str
  internal_domains: [domain, ...]        # "us": anyone @ these domains
  my_addresses: [email, ...]             # the mailbox owner's addresses (and personal ones to ignore)
hours: {timezone: IANA, days: [mon..sun], start: "HH:MM", end: "HH:MM"}
noise:                                   # merged with built-in defaults unless replace: true
  replace: false
  senders: [regex]
  subjects: [regex]
suppliers:
  - name: str
    domains: [domain]
    aliases: [str]                       # other names people write ("Steel and O brian")
    parent: str | null                   # parent company — never a link signal by itself
customers:                               # optional; anything external & not a supplier is a customer
  - {name: str, domains: [domain], aliases: [str]}
lanes:
  - id: slug
    label: str
    owner: self | <profile name>
    priority: int                        # lower = evaluated first
    detect: [new, customer_reply, supplier_reply, replies_owed]
    match:
      text: [regex]                      # subject + first 500 chars of preview
      from: [regex]
      exclude_from: [regex]
      exclude_text: [regex]
    replies_owed: {after_business_days: 1, max_days: 10}
followups: {due_business_days: 3}
safety:
  pricing_patterns: [regex]              # added to built-ins
  pricing_attachment_names: [regex]      # added to built-ins
  allow_pricing_to: [domain]             # e.g. your own reps' domains
```

## 6. Data: where things live, per-user vs team-shared

| Data | Location | Scope |
|---|---|---|
| Microsoft sign-in (refresh token) | Hermes `auth.json` (section `plugin_oauth`, key `inbox-chief:microsoft`), managed by `hermes auth add inbox-chief:microsoft` | **per user, per Hermes profile** |
| Handled alert keys, pending drafts, seen events, tracker ledger | `ctx.state` (`<HERMES_HOME>/plugin-data/…`, profile-scoped, ≤10 MiB) | **per user, per profile** |
| Lanes file | wherever `lanes_file` points | **team-shareable** (same file for Andy, Sam, Jason) — no secrets in it |
| Account notes | wherever `notes_dir` points | **team-shared** by design |
| Fixtures | plugin dir (read-only) | public, fictional |

Each colleague installs the plugin in their own Hermes, signs in with their own Microsoft account,
and points `lanes_file`/`notes_dir` at the shared copies. Nothing about one user's mailbox is written
to a shared location except account-note log lines the agent chooses to add.

**Multi-profile note (Andy's setup):** the grant is per profile. Specialist profiles (one per lane
owner) each need `hermes auth add inbox-chief:microsoft` once, or Andy runs every lane in one
profile with `owner: self`. Pilot recommendation: Andy keeps his live system; the plugin pilots in
one profile against the test mailbox first.

## 7. Microsoft sign-in (`oauth:`) and the scope-echo risk

```yaml
oauth:
  - id: microsoft
    authorize_url: https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize
    token_url: https://login.microsoftonline.com/organizations/oauth2/v2.0/token
    scopes: [Mail.ReadWrite, Mail.Send, User.Read, offline_access]
    client_id_env: INBOX_CHIEF_MS_CLIENT_ID
    hosts: [graph.microsoft.com]
```
Each organisation (or user) registers its own Entra ID app: platform "Mobile and desktop
applications", redirect URI `http://127.0.0.1` (Entra accepts any loopback port), "Allow public client
flows" on, delegated Graph permissions Mail.ReadWrite, Mail.Send, User.Read, offline_access. Admin
consent may be required by the tenant.

**Open risk (must be proven on the first real sign-in):** Hermes (`hermes_cli/plugin_oauth.py`,
`_require_granted_scopes`) refuses to save a grant when the token response's `scope` string does not
contain every declared scope verbatim. Microsoft does not reliably echo `offline_access`, and may
return Graph scopes either short (`Mail.ReadWrite`) or resource-qualified/lower-cased
(`https://graph.microsoft.com/mail.readwrite`). Andy's existing Graph token cache shows short names
and no `offline_access`. If the first sign-in fails with `oauth_scope_not_granted`, the fix is a small
upstream Hermes PR (normalise case and the resource prefix; treat `offline_access`/`openid`/`profile`
as not-echoed), not a plugin workaround — the plugin may not hold or refresh tokens itself (catalog
rule 11). Dropping `offline_access` is not an option: without it Microsoft issues no refresh token
and every scan would need a new sign-in after an hour.

## 8. Catalog compliance checklist
- Public surfaces only: `ctx.register_tool/hook/command/cli_command/automation_blueprint/skill`,
  `ctx.state`, `ctx.get_config`, `ctx.current_cron_execution`, `credentialed_provider_request`.
  No core patching, no private imports.
- Credentials only via `oauth:` + `credentialed_provider_request`; never reads `.env`/`auth.json`.
- `requires_hermes: ">=0.22.3"` (first stable release with plugin `oauth:`), same floor in the catalog entry.
- No Python dependencies beyond Hermes core (`httpx` is Hermes's; YAML via `hermes_yaml` at runtime,
  PyYAML/ruamel fallback only in tests).
- No binaries, no `.pyc` (`.gitignore` + test that scans the tree).
- `desktop/plugin.js` SDK-only (desktop card), validated by `hermes plugins validate`.
- Disclosures (README + PR): network calls to Microsoft Graph and login.microsoftonline.com; reads
  `lanes_file` and `notes_dir` outside plugin data; writes account notes; creates drafts, sets reply
  icons and sends approved drafts in the user's mailbox; stores a Microsoft grant via Hermes; no
  telemetry; no background processes (scheduling is an opt-in cron blueprint).
- Public repo contains only fictional data. Real company config lives in a private file outside the repo.

## 9. Card map
| Card | Owns |
|---|---|
| Spec + scaffold (this) | SPEC, manifest, package skeleton, config, Graph client + fixture client, safety primitives, test scaffold |
| Core engine | lanes, linking, ledger, notes, drafts, context, tool handlers; tests for every live bug |
| Demo dataset | expand `fixtures/demo` (2–3 weeks, hero scenarios) + loader script for a real **test** mailbox |
| Desktop panel | `desktop/plugin.js` queue + Go/Change/Skip + notes |
| Catalog PR + demo | public repo, validate in CI, `plugin-catalog/inbox-chief.yaml`, screenshots, demo script |

Live bugs the engine tests must cover: duplicate alerts across lanes/tracker, per-reply (not
per-thread) dedupe, stale pending drafts after the user sends manually, missed customer replies on
self-started threads, supplier parent-company false links.
