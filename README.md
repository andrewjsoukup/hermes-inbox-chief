# Inbox Chief — Hermes plugin

An Outlook (Microsoft 365) inbox chief of staff for Hermes. Inbox Chief:

- routes each new email to **one owner lane** (quotes, accounting, sales, or whatever lanes you define)
- catches customer replies on threads you started, supplier replies to your questions, replies you
  owe, and follow-ups that are overdue
- connects a supplier's answer to **the customer who is waiting on it**, so the cut sheet goes to the
  right thread
- stages **reply-all** drafts and sends one **only after you explicitly say go**

> Status: v0.1 scaffold. Safety rules, the Graph client, config, staging, and the gated send are
> implemented and tested. Lane scanning, thread linking, and account notes are in progress (see
> `SPEC.md` §9).

## Safety rules (enforced in code)
1. **Sends only on your explicit go.** By default every send goes through Hermes's own approval prompt.
   Optional `explicit_go` mode: your latest chat message has to be a go for that draft. There is no
   auto-send setting.
2. **Never sends from unattended runs** (cron, subagents, kanban workers).
3. **Sends exactly the draft you reviewed.** If the draft changed after it was staged, it is refused.
4. **Reply-all is the default.** Sender-only replies are an explicit opt-out.
5. **Never forwards supplier pricing.** A draft that would carry a supplier's quote, price list or
   price figures to anyone outside your company (or that supplier) is refused.
6. No deletes, moves or rules. The only mailbox writes are drafts, draft attachments, the
   reply/reply-all icon, and sending drafts you approved.

## Setup
1. **Register an Entra ID app**, one per organisation. Platform: *Mobile and desktop applications*.
   Redirect URI: `http://127.0.0.1`. Turn on *Allow public client flows*. Add these delegated
   Microsoft Graph permissions: `Mail.ReadWrite`, `Mail.Send`, `User.Read`, `offline_access`.
   Your tenant may require admin consent.
2. Set `INBOX_CHIEF_MS_CLIENT_ID` to the app's client ID (Desktop → Plugins → Inbox Chief, or `hermes config`).
3. Sign in from a terminal: `hermes auth add inbox-chief:microsoft`. Hermes stores and refreshes the
   token. The plugin never sees it.
4. Copy `lanes.example.yaml` to a **private** location, edit it for your company, then check it:
   `hermes inbox-chief check-config /path/to/lanes.yaml`.
5. Set `lanes_file` (and optionally `notes_dir`, a folder of shared account notes) in the plugin settings.
6. Optional: schedule the **Inbox Chief scan** blueprint with `/blueprint inbox-chief:scan` or from Desktop → Cron.

**Try it offline first:** set `mailbox: fixture`. Inbox Chief then runs against a bundled, fully
fictional demo mailbox and never touches real mail.

## Teams
Each person installs the plugin and signs in with their own Microsoft account. The grant and all
runtime state are per user and per Hermes profile. The **lanes file** and the **account-notes
folder** can be shared, for example in a synced folder. Neither holds credentials.

## Disclosures
- **Network:** Microsoft Graph (`graph.microsoft.com`) and Microsoft sign-in
  (`login.microsoftonline.com`). These are two Microsoft domains, which is why the validator warns
  about the OAuth hosts. There is no telemetry.
- **Credentials:** a Microsoft OAuth grant that Hermes stores and refreshes (`oauth:` in `plugin.yaml`).
  The plugin never reads `.env`, `auth.json`, or any token file.
- **Reads outside plugin data:** your `lanes_file` and `notes_dir`.
- **Writes outside plugin data:** account-note log lines in `notes_dir`. In your mailbox: drafts, draft
  attachments, the reply-icon property, and sends you approved.
- **Background work:** none. Scanning only runs if you schedule the cron blueprint.
- **Shell commands:** none.

## Development
```bash
uv run --isolated --no-project --python 3.12 --with "pytest>=8,<9" --with "ruamel.yaml>=0.18,<0.19" python -m pytest
hermes plugins validate .
hermes plugins doctor . --ci
```
Tests are offline-only. A guard fails any test that opens a network connection.

License: MIT
