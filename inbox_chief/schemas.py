"""Tool schemas — what the model reads. Names are fixed by SPEC.md §3."""

_ID = {"type": "string", "description": "Outlook message id (from inbox_chief_scan or inbox_chief_thread)."}

STATUS = {
    "name": "inbox_chief_status",
    "description": "Inbox Chief setup and health: whether lanes and the Microsoft sign-in are configured, "
                   "lanes and owners, counts of staged drafts. Use first when unsure whether Inbox Chief is set up. "
                   "Never answer whether a draft was sent from memory: call with live=true.",
    "parameters": {"type": "object", "properties": {
        "live": {"type": "boolean", "description": "Re-check staged/tracked drafts against the mailbox now."}}},
}

SCAN = {
    "name": "inbox_chief_scan",
    "description": "Scan the inbox and return NEW items grouped by lane: new inquiries, customer replies, supplier "
                   "replies, replies owed, overdue follow-ups. Each email has exactly one owner lane. Items already "
                   "reported are not repeated. In a scheduled run, if the result says [SILENT], reply exactly [SILENT].",
    "parameters": {"type": "object", "properties": {
        "lane": {"type": "string", "description": "Only this lane id (default: lanes this profile owns)."},
        "kinds": {"type": "array", "items": {"type": "string", "enum": ["new", "customer_reply", "supplier_reply",
                                                                       "replies_owed", "overdue", "replied"]}},
        "scheduled": {"type": "boolean", "description": "True when called from the scheduled scan job."},
        "mark_seen": {"type": "boolean", "description": "Mark returned items as reported (default true)."}}},
}

THREAD = {
    "name": "inbox_chief_thread",
    "description": "Full context for one email before drafting: the whole thread, related threads you started, "
                   "and 'possibly waiting on this' — other threads (e.g. the customer who asked) that this message "
                   "may answer. Always call before drafting a reply.",
    "parameters": {"type": "object", "properties": {"message_id": _ID}, "required": ["message_id"]},
}

DRAFT_REPLY = {
    "name": "inbox_chief_draft_reply",
    "description": "Create a reply draft (REPLY-ALL by default) on a message and stage it for the user's go/no-go. "
                   "Can copy attachments from another message (e.g. a supplier's cut sheet into the customer's thread). "
                   "Refuses drafts that would forward supplier pricing outside the company. Never sends.",
    "parameters": {"type": "object", "properties": {
        "message_id": _ID,
        "body": {"type": "string", "description": "Your reply text (plain text; quoted history is kept)."},
        "sender_only": {"type": "boolean", "description": "Reply to the sender only. Default false = reply-all."},
        "attach_from_message_id": {"type": "string", "description": "Copy attachments from this message."},
        "attachment_names": {"type": "array", "items": {"type": "string"}, "description": "Only these files."},
        "summary": {"type": "string", "description": "One line for the user's go/no-go list."}},
        "required": ["message_id", "body"]},
}

DRAFT_NEW = {
    "name": "inbox_chief_draft_new",
    "description": "Create a new email draft and stage it for the user's go/no-go. Same pricing safety check. Never sends.",
    "parameters": {"type": "object", "properties": {
        "to": {"type": "string", "description": "Comma-separated addresses."},
        "cc": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "attach_from_message_id": {"type": "string"},
        "attachment_names": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"}},
        "required": ["to", "subject", "body"]},
}

PENDING = {
    "name": "inbox_chief_pending",
    "description": "Drafts staged and waiting for the user's go/no-go, checked live against the mailbox (drafts "
                   "the user already sent or deleted are removed).",
    "parameters": {"type": "object", "properties": {}},
}

SEND = {
    "name": "inbox_chief_send",
    "description": "Send ONE staged draft, unchanged, ONLY after the user explicitly said go for that draft in this "
                   "conversation. Never call on your own initiative, from a scheduled run, or for a draft the user has "
                   "not seen. If the user asked for changes, edit, re-stage and ask again instead.",
    "parameters": {"type": "object", "properties": {
        "draft_id": {"type": "string", "description": "draft_id from inbox_chief_pending."}}, "required": ["draft_id"]},
}

RESOLVE = {
    "name": "inbox_chief_resolve",
    "description": "Remove a staged draft from the go/no-go list when the user says skip/no (outcome=dropped). "
                   "Does not delete the draft from Outlook.",
    "parameters": {"type": "object", "properties": {
        "draft_id": {"type": "string"},
        "outcome": {"type": "string", "enum": ["dropped", "skipped"]}}, "required": ["draft_id"]},
}

NOTES = {
    "name": "inbox_chief_notes",
    "description": "Shared account notes (customers, suppliers, projects). action=match finds notes for some text; "
                   "show reads one; add appends a dated log line (status changes, who we are waiting on, decisions); "
                   "list lists all.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["match", "show", "add", "list"]},
        "name": {"type": "string"},
        "text": {"type": "string"},
        "aliases": {"type": "string", "description": "Comma-separated names/domains to add (action=add)."}},
        "required": ["action"]},
}

ALL = [STATUS, SCAN, THREAD, DRAFT_REPLY, DRAFT_NEW, PENDING, SEND, RESOLVE, NOTES]
