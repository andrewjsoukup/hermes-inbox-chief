# Demo mailbox setup (personal Outlook.com or Microsoft 365)

Use a **new test mailbox**, never anyone's real mailbox. The Northwind fixture contains
47 messages in 26 threads. `scripts/load_demo_mailbox.py` defaults to a no-network dry run and
never sends, moves or deletes mail.

## What changed

The plugin uses Microsoft's **`/common`** authority and **`prompt=select_account`**, rather than
work/school-only `/organizations`. Personal Outlook.com, Hotmail and Live mailboxes can use the
same Graph integration as Microsoft 365 work/school mailboxes. A personal demo mailbox does not
need a Microsoft 365 subscription.

**Separate requirements:** the mailbox can be a free personal account; the **OAuth app registration**
still needs an Entra directory and an owner with registration rights. The mailbox user need not
belong to that directory. This version is bring-your-own-app: no publisher client ID is bundled,
and we never borrow another vendor's app ID. A centrally registered publisher app would be a
separate, owner-approved distribution decision.

**Live compatibility is not yet verified.** No test account has been authorized. Offline tests
cannot prove Microsoft consent, refresh, seeding or conversation grouping. Known Hermes OAuth
limitations are below. Do not buy an M365 trial to work around them.

## 1. Andy: create the test mailbox

1. In a new **InPrivate / Incognito** window, open `https://outlook.com` → **Create free account**.
   Choose a new Outlook.com address, not an existing work or personal address.
2. Suggested address: **`inboxchief.demo.pat@outlook.com`**; if taken, choose an available variant.
   Availability has not been checked. Suggested display name: **Pat Rivera**, the fictional demo
   persona. Supply truthful details wherever Microsoft requires them; do not bypass eligibility checks.
3. Complete the password, CAPTCHA, phone/recovery verification yourself. Save the credentials in
   your password manager. **Do not paste passwords, codes or recovery details into chat/the card.**
4. Open Outlook once to provision the mailbox; it should hold only welcome mail. Do not send a test
   message. Comment on the card with the **exact test address only**.

An existing dedicated Microsoft 365 **test user** is an alternative, not a requirement. Never
use the production mailbox. The loader replaces the fixture persona's `.example` address with
the authorized test address.

## 2. Andy / app owner: register the OAuth client

1. Open `https://entra.microsoft.com` with an account allowed to register apps. Go to **Entra ID →
   App registrations → New registration**. If you have no directory or registration rights, stop
   and report that blocker; a personal mailbox alone does not provide them. Do not purchase Azure
   or M365 or change an existing business app as a workaround.
2. Name: **Inbox Chief demo**. Supported account types: **Any Entra ID Tenant + Personal Microsoft
   accounts**, also worded **Accounts in any organizational directory and personal Microsoft
   accounts**. The manifest value is `signInAudience: AzureADandPersonalMicrosoftAccount`.
   Leave the redirect URI empty for now and click **Register**.
3. **API permissions → Add a permission → Microsoft Graph → Delegated permissions**: add
   **Mail.ReadWrite**, **Mail.Send**, **User.Read**, **offline_access**. Keep User.Read if already
   present. No application permissions, client secret or certificate. Mail.Send is for the plugin's
   explicit-go feature; setup and the demo must never send.
4. Copy the **Application (client) ID** from Overview; it is a public identifier, not a secret.
   No tenant ID is needed for `/common`. Keep the app's **Manifest** page open for step 4.

Personal users consent for their own mailbox. Work tenants may require administrator approval;
let the administrator decide rather than changing policy. Do not authorize the real work mailbox
just because that account owns the app. If you already made a single-tenant **demo** app, change
its audience to the choice above or create a fresh dedicated app. Do not modify production apps.

## 3. Select the isolated Hermes demo home

The prepared machine has an installed copy under `private/hermes-home/plugins/inbox-chief`.
Everything in `private/` is git-ignored. Never copy credentials from the live Hermes home or use
the normal work-mail connector. Open a **new PowerShell terminal yourself**, then run:

```powershell
Set-Location "<path-to-this-repo>"
$Repo = (Get-Location).Path
$env:HERMES_HOME = Join-Path $Repo "private/hermes-home"
$env:PYTHONDONTWRITEBYTECODE = "1"
$HermesPython = (Get-ChildItem "$env:HERMES_HOME/installs/*/environments/*/venv/Scripts/python.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
if (-not $HermesPython) { throw "Sandbox interpreter missing; ask the agent to prepare it." }
$Runtime = Split-Path (Split-Path (Split-Path $HermesPython -Parent) -Parent) -Parent
$env:PYTHONPATH = Join-Path $Runtime "workspace"
if (-not (Test-Path "$env:PYTHONPATH/hermes_cli/main.py")) { throw "Sandbox source missing." }
& $HermesPython -B -m hermes_cli.main plugins show inbox-chief
& $HermesPython -B -m hermes_cli.main config set INBOX_CHIEF_MS_CLIENT_ID "<application-client-id>"
```

Verify the plugin is under **this repo's sandbox**, not the live home, and its authorize/token URLs
use **`/common/`**. An older `/organizations/` installation needs updating before sign-in. These
commands pin both the sandbox interpreter and its source workspace; an inherited PYTHONPATH can
otherwise import the live installation even from a sandbox Python. Hermes owns credential storage through its public config/auth commands and the
plugin's `oauth:` declaration; do not edit or read token files.

The sandbox is not shipped in a fresh clone. On another machine, prepare an isolated Hermes home.
For a local developer checkout, clone this repository into `<isolated-home>/plugins/inbox-chief`
with Git, then run `hermes plugins enable inbox-chief` under that home. For a published release,
use the catalog or its Git URL. Do not pass a Windows filesystem path to `hermes plugins install`:
the current CLI interprets it as a GitHub repository shorthand, not a local directory.

## 4. Start sign-in, register its exact redirect, then authorize

Hermes refuses browser sign-in from agent, gateway or cron sessions. **Andy must run this in his
own terminal.** In the same PowerShell window:

```powershell
& $HermesPython -B -m hermes_cli.main auth add inbox-chief:microsoft --no-browser --timeout 900
```

While that command is waiting:

1. Find **Waiting for callback on**, for example `http://127.0.0.1:54321/callback`. Copy the URI
   **with the actual printed port**, not the example port.
2. In the dedicated app's **Manifest** (Microsoft Graph format), set `publicClient.redirectUris`
   to that exact URI. Preserve the rest of the manifest. The fragment is:
   ```json
   "publicClient": {
     "redirectUris": ["http://127.0.0.1:54321/callback"]
   }
   ```
   Save after substituting the actual port. This is **Mobile and desktop / public client**, not
   Web or SPA. Some portal versions reject `http://127.0.0.1` in the Authentication text box; the
   manifest is Microsoft's documented route. In the older Azure AD manifest format the equivalent
   is a `replyUrlsWithType` item with `type: "InstalledClient"`. Do not mix manifest formats.
3. Open the **authorization URL from that same still-running command** in the private browser
   window. Do not post that URL, state, codes or callback response in chat. Select **Use another
   account** if needed. Check the consent screen shows the **new test mailbox**, not the app owner
   or your work account. Approve only for the test account and let the loopback redirect finish.
4. Wait for the terminal's successful sign-in message, then run:
   ```powershell
   & $HermesPython -B -m hermes_cli.main auth status inbox-chief:microsoft
   ```
   Report the status and test address. Status alone does not verify mailbox identity or Graph access.

**Port caveat:** Microsoft documents ignored-port matching specifically for `localhost`. Current
Hermes plugin OAuth uses a random `127.0.0.1` port and exposes no manifest option to pin it. The old
guide's assertion that Entra always ignores the port was too broad. Register the exact emitted URI.
A restarted login may need a new URI; replace the dedicated demo redirect with the new one. A grant
created under `/organizations` needs a fresh sign-in because the declaration changed.

## 5. Agent: load and verify after authorization

Run the installed copy of the loader so Hermes recognizes the calling plugin. In the same isolated
PowerShell environment, substitute the address Andy actually created:

```powershell
$TestMailbox = "<exact-test-address>"
$Loader = Join-Path $env:HERMES_HOME "plugins/inbox-chief/scripts/load_demo_mailbox.py"
$Map = Join-Path $Repo "private/demo-load-map.json"
# No-network preview first:
& $HermesPython -B $Loader --expect-mailbox $TestMailbox --map-out $Map
# Only for the approved dedicated test account:
& $HermesPython -B $Loader --expect-mailbox $TestMailbox --map-out $Map --apply
```

The loader checks `/me` before reading message contents or writing; a different identity is refused.
It also refuses more than 25 existing Inbox messages or an already loaded demo. Do not override these
checks to use a real mailbox. Its guard forbids send/reply/forward/move/delete calls.

Acceptance after a real load (still pending):
- Read back all message IDs, folder placement, subject, sender/recipients and attachment metadata.
- Check Inbox/Sent items are not drafts, dates match the intended shift, and 26 fixture threads
  become 26 distinct Graph conversations. The loader reports split threads and non-draft failures,
  but **does not fully compare timestamps, folders or merged conversations**; verify those separately.
- Update `company.my_addresses` in a **private** copy of the demo lanes file with the test address.
  Never classify all of `outlook.com` or `hotmail.com` as an internal company domain.
- Verify the four hero scenarios and keep the ID map private. Do not send anything. Use fixture
  mode if a recording needs to show a simulated Go/send interaction.

Microsoft documents message creation as **draft creation**. The extended-property technique for
back-dating, non-drafts and threading remains unverified on both account types. If it fails, retain
the evidence and stop; do not send fixture emails as a workaround.

**Timing:** fixture "now" is Tuesday, November 10, 2026, 10:30 AM Central. Dates shift by whole days
to the load day by default. Load after 10:30 AM Central or choose `--shift-days`. Shifts not divisible
by seven change weekdays, so check the business-day scenarios for the actual recording date.

## Troubleshooting / when to stop

| Observed result | Action |
|---|---|
| Personal accounts rejected | Check `/common` and app audience `AzureADandPersonalMicrosoftAccount`; changing only one is insufficient. |
| Redirect mismatch / AADSTS50011 | Register the full IP/port/path from the currently waiting command, not a prior invocation. |
| Consent shows work account/admin | Cancel and select the new test account in a private window. |
| No directory/app registration rights | Ask Andy for an approved dedicated app/client ID; do not buy a subscription or change business apps. |
| `oauth_scope_not_granted` | Share only the sanitized error. Current Hermes compares token-response scopes verbatim; Microsoft may omit `offline_access` or format Graph scopes differently. This requires an upstream provider-aware fix, not plugin-held tokens or weaker permission checks. |
| Logged out / declaration changed | Complete a new interactive sign-in; never copy/edit token files. |
| Startup stalls on runtime preparation | Stop and report it. Check the sandbox source path above; do not update or repair the live Hermes install for the demo. |
| Seeded items remain drafts, dates wrong, threads split/merge | Report failed acceptance checks. Never send, move or delete to hide the failure. |

## Official references (checked November 5, 2026)

- Microsoft audiences / `/common`: `https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc`
- App registration prerequisites: `https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app`
- Audience values: `https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation`
- Redirect rules: `https://learn.microsoft.com/en-us/entra/identity-platform/reply-url`
- Public-client redirects: `https://learn.microsoft.com/en-us/graph/api/resources/publicclientapplication?view=graph-rest-1.0`
- Personal-account Mail.ReadWrite: `https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0`
- Hermes plugin OAuth: `https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/`

Schedule correction: November 11, 2026 is **Wednesday**, not Tuesday. The task's target is 5 PM Eastern
on November 11; confirm the organizer's deadline separately.
