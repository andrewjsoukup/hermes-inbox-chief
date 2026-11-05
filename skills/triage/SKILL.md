---
name: triage
description: Work the Inbox Chief queue — research each email, stage a reply-all draft, ask for go/no-go.
---
# Inbox Chief triage

Use when working Inbox Chief items (a scheduled scan, `/inbox`, or "what's in my inbox").

## Loop for each item
1. `inbox_chief_thread(message_id)` — read the whole thread, related threads you started, and
   **"possibly waiting on this"**. If a supplier's message answers a customer's question, the reply
   goes to the **customer's thread**, with the supplier's file copied via `attach_from_message_id`.
2. Check account notes (`inbox_chief_notes action=match`) for who we are waiting on and open items.
3. Decide: reply now, follow up with a supplier, or FYI only.
4. Stage with `inbox_chief_draft_reply` (reply-all is the default; use `sender_only` only when the
   user asks for a private reply). Write the summary as one line the user can approve.
5. Report: who, what they need, what you staged — then ask **go / change / skip** for each draft.

## Rules
- Never call `inbox_chief_send` unless the user just said go for that specific draft. Never from a
  scheduled run.
- Never forward supplier pricing to a customer. Copy the cut sheet, not the quote; write the
  customer's price yourself only if the user gives it.
- Before saying a draft is sent or unsent, call `inbox_chief_pending` (live check). Never from memory.
- When you learn something durable about an account (status, who we're waiting on, a decision),
  add one line with `inbox_chief_notes action=add`.
