# Northwind demo mailbox (fictional)

The full demo dataset for Inbox Chief. It is the mailbox of **Pat Rivera**, owner of
**Northwind Process Supply**, a made-up distributor of sanitary pumps, valves and gaskets. It covers
Mon 26 Oct to Tue 10 Nov 2026 and is pinned to `now = 2026-11-10T16:30:00Z` (10:30 CT).

Every person, company, domain, part number, price and quote number here is invented. Domains use
the reserved `.example` TLD and phone numbers use 555-01xx. `tests/test_demo_dataset.py` enforces this.

| File | What |
|---|---|
| `mailbox.json` | 47 messages in 26 conversations (inbox, sent, drafts), SPEC §4.2 format, plus `_demo` metadata |
| `lanes.yaml` | lanes file for this company (3 suppliers, 6 customers, quotes/accounting/sales lanes) |

Regenerate the fixture with `python scripts/build_demo_dataset.py`. The output is deterministic, and a test fails if the
committed JSON is stale.

## Cast
- **Us:** Pat Rivera (mailbox), Jordan Blake (inside sales), Morgan Tate (accounting), all at `northwind-demo.example`
- **Suppliers:** Kestrel Pump Works (Ravi Lund), Bluefin Valve Co (Mara Quist), and Granite & O'Malley Seals
  (Tess Okonkwo). Kestrel and Bluefin share the parent company Harbor Flow Group.
- **Customers:** Lakeshore Creamery (Dana Okafor, Lee Marsh), Cedar Ridge Brewing (Riley Vance), Harbor Bay
  Foods (Chris Delgado), Copperline Distilling (Quinn Harlow), Bellwether Bottling (Casey Lindqvist), Prairie
  Botanicals (Sam Ito), and Ellis Fermentation (Robin Achterberg, new)

## Hero scenarios (`_demo.scenarios` holds the exact message ids)
1. **H1: the cut sheet a customer asked for.** Dana asks for the KP-450 cut sheet, cc Lee (`c-lakeshore-kp450`).
   Pat asks Kestrel. Ravi sends a holding reply, then on Nov 9 sends the rev D cut sheet **and** a priced quote
   (`c-kestrel-kp450`). The thread view should show "possibly waiting on this: Lakeshore". The agent then stages a
   reply-all to Dana and Lee that attaches only `KP-450-cut-sheet-rev-D.pdf`. Copying the quote or restating
   $4,180 is blocked (S5).
2. **H2: a customer replies on a thread we started.** Pat sent a CIP proposal to Cedar Ridge (`c-cedar-cip`) and
   followed up. Riley replies twice on Nov 10 (per-reply dedupe), with Jordan on cc.
3. **H3: a supplier half-answers.** Chris asks three gasket questions (`c-harborbay-gaskets`) and Pat forwards
   them to Granite & O'Malley. Tess answers 1 and 3 and skips the FDA letter (2), puts a price in the body, and
   signs as "Granite and O Malley", which tests fuzzy alias matching.
4. **H4: an overdue follow-up.** Pat's Nov 2 follow-up on quote NW-Q-2210 to Prairie Botanicals
   (`c-prairie-quote`) never got an answer. It is tracked as needs-reply and is overdue after 3 business days.

## Traps for the live bugs
- **Tracked-thread reply** (`c-lakeshore-startup`). Lee answers a tracked mail. This should raise one `replied`
  event and must not also raise a `customer_reply`.
- **Parent-company false link** (`c-bluefin-bv200`). Bluefin's reply mentions Harbor Flow Group, which is also
  Kestrel's parent. It must not link to the Lakeshore/Kestrel threads. Copperline names Bluefin BV-200, so that
  is the right link.
- **Already handled** (`c-copperline-valve`). A draft already exists, so this is not a new item.
- **Replies owed** (`c-bellwether-visit`). The customer wrote last on Nov 6.
- **Noise:** a newsletter, receipts, a webinar, a social notification, an out-of-office auto-reply, the supplier's
  price-increase blast (it also carries `Kestrel_NPS-2027.pdf`, which must never be forwarded), and the owner's
  personal address.
- **Internal mail:** Jordan and Morgan write to Pat. These are not customer items.
- **Accounting lane:** a supplier statement (past due) and a customer remittance.

`_demo.tracker_seed` lists the sent mails the tracker should already follow when the demo starts.

## Loading it into a real test mailbox
`scripts/load_demo_mailbox.py` creates these messages in a **test** Outlook / Microsoft 365 mailbox through
Microsoft Graph, using the plugin's Hermes sign-in. It never sends, deletes or moves mail. Read its docstring
before use. In short:

```bash
# 1. plugin installed in Hermes; 2. `hermes auth add inbox-chief:microsoft` signed in AS THE TEST MAILBOX
python scripts/load_demo_mailbox.py --expect-mailbox demo@yourtenant.example          # dry run, no network
python scripts/load_demo_mailbox.py --expect-mailbox demo@yourtenant.example --apply  # write
```

Safety rails:
- The signed-in mailbox must equal `--expect-mailbox`.
- It refuses an Inbox with more than 25 messages.
- It refuses to load twice.
- A URL guard blocks send, move, copy, reply, forward and DELETE.

Messages are back-dated using MAPI properties and threaded using a shared `PR_CONVERSATION_INDEX`. The script then
reads every message back and reports any thread that did not come out as one Graph conversation. Every message is
tagged with the category `Inbox Chief demo`. By default the dates shift so the fixture's `now` falls on today; use a
whole number of weeks to keep the weekdays the same. The id map is written to `private/demo-load-map.json`, which is
git-ignored.
