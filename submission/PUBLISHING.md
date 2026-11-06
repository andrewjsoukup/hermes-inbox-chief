# Publishing Inbox Chief to the Hermes plugin catalog

These are the steps only Andy can do. A9 has no `gh` CLI, so everything below uses the GitHub
website plus plain `git`. Allow about 15 minutes.

## 0. Before you push (one-time decision)
Commits in this repo are authored with your personal Gmail address, and a public repo shows
that address. If you'd rather hide it, use your GitHub noreply address
(GitHub → Settings → Emails → "Keep my email addresses private" shows `<id>+<user>@users.noreply.github.com`)
and tell the agent. It can rewrite the local commits before the first push. Otherwise, skip this step.

## 1. Create the public repo (browser)
1. Go to <https://github.com/new>.
2. Owner: **your personal account**. Repository name: **`hermes-inbox-chief`**.
3. Visibility: **Public**. Do **not** add a README, .gitignore or license; the repo already has them.
4. Click **Create repository**.

## 2. Push (Git Bash or PowerShell on A9)
```bash
cd ~/Projects/hermes-plugins/inbox-chief
git status --short                      # should show nothing tracked as modified
git remote add origin https://github.com/<GITHUB_USER>/hermes-inbox-chief.git
git branch -M main
git push -u origin main                 # Git Credential Manager opens a browser sign-in the first time
git rev-parse HEAD                      # copy this 40-character SHA
```
The `private/` folder (test Hermes home) and `*.private.yaml` are git-ignored and are **not** pushed.
After pushing, open the repo page and confirm there is no `private/` folder.

## 3. Hand back to the agent
Comment on the card (or tell Apollo) with your **GitHub username** and the **SHA**. The agent then:
- fills `<GITHUB_USER>` and `<sha>` in `submission/plugin-catalog/inbox-chief.yaml` and `submission/PR_DESCRIPTION.md`
- re-runs `hermes plugins validate` on a fresh `git clone` at that SHA, which is what catalog CI does
- checks that the screenshot URLs resolve

## 4. Open the catalog PR (browser, about 3 minutes)
1. Go to <https://github.com/NousResearch/hermes-agent> and click **Fork** (keep the default name).
2. In your fork, go to **Add file → Create new file**.
   - Path: `plugin-catalog/inbox-chief.yaml`
   - Contents: paste the filled-in `submission/plugin-catalog/inbox-chief.yaml`.
   - Choose **Create a new branch** named `catalog-inbox-chief`, then **Propose changes**.
3. On the compare page, the base must be `NousResearch/hermes-agent` `main`.
   - Title: `plugin-catalog: add inbox-chief (Outlook inbox chief of staff)`
   - Body: paste the filled-in `submission/PR_DESCRIPTION.md`, and add the demo video link once it's posted.
4. Click **Create pull request**, then wait for the catalog CI job to go green.

Deadline recorded for this project: PR and demo by **November 11, 2026, 5 PM ET (Wednesday)**.
The original task said Tuesday; November 11 falls on Wednesday in 2026. Confirm the organizer's
deadline before submitting.

## Not required for the PR, but needed for the recorded demo
- Create a personal Outlook.com test mailbox (or a dedicated M365 test user), configure a dual-audience
  Entra app, and complete the first sign-in (`docs/DEMO_MAILBOX_SETUP.md`). No M365 trial is required
  for a personal test mailbox. The app registration still needs access to an Entra directory.
  The first real sign-in is also the live check of the `offline_access` scope-echo risk (SPEC.md §7).
