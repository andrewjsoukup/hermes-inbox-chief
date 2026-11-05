# Demo mailbox setup (recorded demo)

The tests use fixture data. The recorded demo needs a **real, empty Microsoft 365 test mailbox**,
which then gets the fictional Northwind dataset (`fixtures/northwind/`, 47 messages, 26 threads)
through `scripts/load_demo_mailbox.py`. The script never sends, moves or deletes.

## Why not a free outlook.com address

`plugin.yaml` signs in through `login.microsoftonline.com/organizations/...`. That endpoint accepts
work/school (Entra ID) accounts only and rejects personal Microsoft accounts (outlook.com,
hotmail.com). Also, since June 2024 a personal account can't register an app unless it is in a
directory (tenant). A consumer mailbox would mean changing the manifest to `/common` and requiring a
multitenant + personal-accounts app registration. That's a product decision, not a demo shortcut.
So the demo mailbox has to be a **work account in a Microsoft 365 tenant**.

## Pick a tenant (in order of preference)

| Option | Cost | Isolation | Notes |
|---|---|---|---|
| A. Microsoft 365 E5 developer sandbox | free | full (new tenant) | Only if you qualify (Visual Studio Pro/Enterprise subscriber, or partner program). Takes 2 minutes to check. |
| B. Microsoft 365 Business Basic **free trial**, new tenant | free 1 month (card + phone may be asked) | full (new tenant) | Name the tenant `northwinddemo`, so addresses read `…@northwinddemo.onmicrosoft.com` on camera. Cancel after the demo. |
| C. Dedicated test user in an existing company tenant | ~1 Exchange Online licence | partial | Last resort. The company domain would appear on camera, so use the `.onmicrosoft.com` address. |

**Mailbox address:** `pat.rivera@<tenant>.onmicrosoft.com`, display name **Pat Rivera**. This matches
the fixture persona. The loader swaps the persona's fictional address for the real one.

### A. Check the developer sandbox
1. Go to <https://developer.microsoft.com/microsoft-365/dev-program> and choose **Join now**. Sign in
   with any Microsoft account.
2. If the dashboard offers **Set up E5 subscription**, choose an instant sandbox. Pick the admin
   username `pat.rivera` and set a password. The domain is `<something>.onmicrosoft.com`.
3. If it says you don't qualify, use option B.

### B. Business Basic trial (new tenant)
1. In a private/InPrivate browser window, go to
   <https://www.microsoft.com/microsoft-365/business/microsoft-365-business-basic> and choose
   **Try free for one month**.
2. Use a personal email for contact. When it asks for the company name, enter `Northwind Demo`.
   For the domain, choose the free `northwinddemo.onmicrosoft.com` (or the closest one available).
3. Create the first user as `pat.rivera`. That user is the global admin and gets the Business
   Basic licence, which includes the mailbox.
4. Finish the phone/CAPTCHA/payment steps. Wait about 5 minutes, then open
   <https://outlook.office.com> once as Pat so the mailbox gets created. The inbox should be empty or
   hold only welcome mail.

### C. (fallback) Test user in an existing tenant
In <https://admin.microsoft.com>, go to **Users → Add a user**. Use the username `pat.rivera`
on the `.onmicrosoft.com` domain and assign a licence that includes Exchange Online.

## Register the app (in the same tenant, as Pat/admin)
1. Go to <https://entra.microsoft.com> and choose **Identity → Applications → App registrations →
   New registration**.
   - Name: `Inbox Chief (demo)`
   - Supported account types: **Single tenant**
   - Redirect URI: platform **Public client/native (mobile & desktop)**, value
     **`http://127.0.0.1/callback`**. Hermes listens on `http://127.0.0.1:<random port>/callback`.
     Entra ignores the port for loopback addresses, but the path must match.
2. On the app, go to **Authentication** and set **Allow public client flows** to **Yes**. Save.
3. Go to **API permissions → Add a permission → Microsoft Graph → Delegated** and add `Mail.ReadWrite`,
   `Mail.Send`, `User.Read`, and `offline_access`. Then choose **Grant admin consent for <tenant>**.
4. Copy the **Application (client) ID** and the **Directory (tenant) ID** from Overview.
   Neither is a secret, and there is no client secret.

## Sign in through Hermes (isolated test home, never the live install)
The demo uses its own Hermes home inside the repo (`private/hermes-home`, git-ignored), so the
live profiles never see this grant. The plugin is already installed there.

1. Put the client ID in the test home's `.env`, as a single line:
   `INBOX_CHIEF_MS_CLIENT_ID=<client id>`
   File: `private/hermes-home/.env`
2. Run the sign-in **yourself in a terminal**. Hermes refuses to start a sign-in from an agent
   session. PowerShell:
   ```powershell
   $env:HERMES_HOME = "C:\Users\AndySoukup\Projects\hermes-plugins\inbox-chief\private\hermes-home"
   hermes auth add inbox-chief:microsoft
   hermes auth status inbox-chief:microsoft
   ```
   In the browser, pick **Use another account** and sign in as `pat.rivera@…onmicrosoft.com`,
   **not your real account**. Accept the consent screen.
3. If it fails with `oauth_scope_not_granted`, Microsoft did not echo every scope (most likely
   `offline_access`; see SPEC.md §7). Paste the exact message on the card. The fix is an upstream
   Hermes change, not a plugin workaround.

## Load the dataset (agent or you)
```bash
HH=~/Projects/hermes-plugins/inbox-chief/private/hermes-home
PY=$(ls $HH/installs/*/environments/*/venv/Scripts/python.exe | head -1)
export HERMES_HOME=$HH
# dry run (no network), then the real load:
$PY $HH/plugins/inbox-chief/scripts/load_demo_mailbox.py --expect-mailbox pat.rivera@<tenant>.onmicrosoft.com
$PY $HH/plugins/inbox-chief/scripts/load_demo_mailbox.py --expect-mailbox pat.rivera@<tenant>.onmicrosoft.com --apply
```
Run the copy under `plugins/inbox-chief/`. Hermes attaches the grant only for code inside an
installed plugin. The loader refuses if the signed-in mailbox isn't the expected address, if the
Inbox already has more than 25 messages, or if the demo was already loaded. It writes the id map to
`private/demo-load-map.json` and reports any thread that Exchange split, or any message it stored as a draft.

**Timing:** by default the dates shift so the fixture's "now" (Tue 10 Nov, 10:30 CT) lands on the
load day. Load on the recording day, after 10:30 CT, or pass `--shift-days`. Keep the shift a
whole number of weeks if possible, so that business-day detections match the script.
