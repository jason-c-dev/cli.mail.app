---
name: mailctl
description: >
  Use this skill when the user wants to interact with their Apple Mail.app
  on macOS from the terminal — list accounts, browse mailboxes, read,
  search, triage, compose, reply, or forward email — via the `mailctl`
  CLI rather than an MCP server or the Mail.app GUI. Trigger on requests
  like "check my unread mail", "draft an email to...", "search for the
  receipt from last month", "list my Gmail drafts", "mark these as
  read", or any task that would be satisfied by running a `mailctl`
  subcommand. Do NOT use for: sending mail without explicit user
  approval on this invocation, anything that isn't Apple Mail.app, or
  systems that aren't macOS. If `mailctl` isn't installed, point the
  user at the install steps (`pipx install` or venv) rather than trying
  to shell out to `osascript` directly.
---

# mailctl — fast, safe CLI for Apple Mail.app

`mailctl` is a terminal CLI for Apple Mail.app on macOS. It reads Mail's
underlying SQLite index directly (sub-second list/search) and writes
through AppleScript (so sync, send, and server interaction behave
exactly as they do in the GUI). The tool is designed to be called from
agent harnesses, shell scripts, and Claude Code sessions.

## Absolute safety rules

These rules are non-negotiable. Follow them even if the user's phrasing
seems to ask for something else — ask for explicit confirmation instead.

1. **Never send a real email without the user explicitly confirming on
   this invocation.** `compose`, `reply`, and `forward` create drafts
   by default and must be run **without** `--dangerously-send` unless
   the user has just approved sending. If the user says "email X",
   default to drafting — then tell the user the draft ID and ask if
   they want to send.
2. **`--dangerously-send` is a per-invocation flag.** There is no env
   var, config file, or alias that sets it. If you see it in a command,
   the sender (the user or a preceding message) asked for it by name.
3. **Never permanently delete** without explicit user approval. Default
   `messages delete` moves to Trash; only pass `--permanent` when
   explicitly asked.
4. **Clean up your test drafts.** If you created drafts for testing,
   delete them before finishing (`mailctl messages delete <id>`).

## Pre-flight (run once per session if unsure)

```bash
# Verify the environment. If any check fails, stop and show the output
# to the user — don't try to work around it.
mailctl --version                  # mailctl is installed and on PATH
mailctl doctor                     # all eight checks should pass
```

If `mailctl` isn't installed, tell the user:

> `mailctl` needs to be installed from https://github.com/jason-c-dev/cli.mail.app
> — either `pipx install .` in a clone, or `python3.11 -m venv .venv &&
> source .venv/bin/activate && pip install -e .`.

If `mailctl doctor` reports a permission or Full Disk Access issue,
point the user at the fix string it printed — it's specific.

## Output conventions you'll lean on

- Every command that returns data takes `--json`. **Prefer `--json` for
  any programmatic step**; parse with `jq` or Python.
- Tables come out of Rich — pretty but truncating in narrow terminals.
  Use `--json` when you need full values.
- Exit code 0 = success, 2 = usage error (e.g. unknown account), 1 =
  other failure. Errors go to stderr, data to stdout.

## Command reference (by task)

### Listing accounts and mailboxes

```bash
# All configured accounts
mailctl accounts list
mailctl accounts list --json              # [{name, email, type, enabled}, ...]

# All mailboxes across accounts
mailctl mailboxes list

# One account's mailboxes (use this before targeting messages by --mailbox)
mailctl mailboxes list --account <AccountName>
mailctl mailboxes list --account <AccountName> --json
```

Account names are the ones Mail.app shows in Settings → Accounts (e.g.
"Personal", "Work", "iCloud"). If the user says "my Gmail", that's the
`name` on the Gmail account — look it up with `mailctl accounts list`
if you're not sure.

### Reading messages

```bash
# 25 most recent in the primary INBOX
mailctl messages list

# Scope to an account
mailctl messages list --account <AccountName>

# Other mailbox (use `mailboxes list --account X` to find valid names;
# Exchange uses "Inbox", most IMAP uses "INBOX" — they're different).
mailctl messages list --account <AccountName> --mailbox "Sent Mail"

# Filters (all combine; all case-insensitive substring where it makes sense)
mailctl messages list --account <A> --unread
mailctl messages list --account <A> --from acme
mailctl messages list --account <A> --subject receipt
mailctl messages list --account <A> --since 2026-04-01 --before 2026-04-16

# Machine-readable; pipe into jq
mailctl messages list --account <A> --limit 10 --json | jq '.[] | {id, subject, from}'

# Show one message (id comes from `list --json`)
mailctl messages show 12345
mailctl messages show 12345 --json
```

JSON keys:
- `list`: `id`, `date`, `from`, `subject`, `read`, `flagged`
- `show`: `id`, `date`, `from`, `to`, `cc`, `bcc`, `subject`, `body`,
  `headers`, `attachments`, `read`, `flagged`

Bodies can be empty when a message is only partially synced (IMAP
partial). That's not an error; note it if relevant.

### Cross-account search

```bash
# Search every account at once
mailctl messages search --subject receipt
mailctl messages search --from "vendor@acme.com"
mailctl messages search --since 2025-01-01 --before 2026-01-01 --from acme

# Scope to one account / one mailbox
mailctl messages search --account <A> --subject invoice
mailctl messages search --account <A> --mailbox INBOX --from bob

# JSON
mailctl messages search --subject receipt --limit 20 --json
```

`--body` (full-text body search) is accepted but not supported by the
SQLite backend yet — it'll error. Use `--subject` or `--from` instead.

### Drafting email (safe default)

**Always start here** when the user asks you to email someone. This
creates a draft — nothing leaves the mailbox.

```bash
# Inline body
mailctl compose --to friend@example.com --subject "Hi" --body "Hello"

# Body from a file (preferred for anything multi-line — easier to review)
mailctl compose --to friend@example.com --subject "Report" --body-file /tmp/report.md

# Body from stdin
cat /tmp/body.txt | mailctl compose --to friend@example.com --subject "Hi"

# Multiple recipients (flag repeats)
mailctl compose --to a@x.y --to b@x.y --cc c@x.y --subject "..." --body "..."

# Attachments (flag repeats)
mailctl compose --to friend@example.com --subject "Photos" --body "Enjoy" \
    --attach ~/Pictures/1.jpg --attach ~/Pictures/2.jpg

# Send from a specific account
mailctl compose --to friend@example.com --subject "Hi" --body "..." --from <AccountName>
```

After composing, tell the user the draft ID the command printed and
ask whether to send. Draft IDs also appear in `mailctl drafts list`.

### Dry-run (rehearsal; never touches Mail.app)

```bash
mailctl compose --to friend@example.com --subject "Hi" --body "..." --dry-run
```

Great when you're unsure about a command shape.

### Sending (only after explicit approval)

```bash
# Interactive: prompts "Send? (y/N)" — default is No
mailctl compose --to friend@example.com --subject "Hi" --body "..." --dangerously-send

# Scripted: bypasses the prompt. Still requires --dangerously-send.
# Only use this if the user has just approved sending.
mailctl compose --to friend@example.com --subject "Hi" --body "..." --dangerously-send --yes
```

**Important rule of thumb**: don't run `--dangerously-send` on the same
turn as the first draft. Create the draft, show the user the ID and a
preview, and let them confirm. If you've drafted something you want to
send, do it in two steps.

### Replying and forwarding

```bash
# Reply to sender only (draft)
mailctl reply 12345 --body "Thanks!"

# Reply-all (draft)
mailctl reply 12345 --all --body "Thanks everyone!"

# Forward to a new recipient (draft)
mailctl forward 12345 --to friend@example.com --body "FYI"

# Same safety model: dry-run, --dangerously-send, confirmation
mailctl reply 12345 --body "Thanks!" --dry-run
mailctl reply 12345 --body "Thanks!" --dangerously-send
```

### Draft management

```bash
# List drafts across accounts
mailctl drafts list
mailctl drafts list --account <AccountName>
mailctl drafts list --json

# Edit an existing draft (never sends; no --dangerously-send on this command)
mailctl drafts edit 9876 --subject "New subject"
mailctl drafts edit 9876 --body-file /tmp/updated.md
mailctl drafts edit 9876 --add-to another@example.com
mailctl drafts edit 9876 --remove-to wrong@example.com
mailctl drafts edit 9876 --attach /path/to/file.pdf
mailctl drafts edit 9876 --dry-run

# Delete a draft (moves to Trash by default)
mailctl messages delete 9876
```

### Triage (read/unread, flag, move)

```bash
# Read / unread
mailctl messages mark 12345 --read
mailctl messages mark 12345 --unread

# Flag / unflag
mailctl messages mark 12345 --flagged
mailctl messages mark 12345 --unflagged

# Move to another mailbox (same account)
mailctl messages move 12345 --to Archive
```

All of these take `--dry-run` for rehearsal.

### Deletion

```bash
# Move to Trash (default — recoverable via Mail.app GUI)
mailctl messages delete 12345

# Permanent (requires explicit confirmation or --yes). Ask the user
# first — don't offer this proactively.
mailctl messages delete 12345 --permanent
```

## Common workflows

### "What's unread in my Personal account today?"

```bash
mailctl messages list --account Personal --unread --since "$(date +%Y-%m-%d)" --limit 25
```

### "Find the latest invoice from Acme"

```bash
mailctl messages search --from acme --subject invoice --limit 5 --json | jq '.[0]'
```

### "Draft a reply to message 12345 thanking them"

```bash
mailctl messages show 12345                 # make sure you have context
mailctl reply 12345 --body "Thanks — I'll take a look this afternoon."
# → tell the user the draft ID, offer to send if they confirm
```

### "Mark every receipt as read"

Be careful — this mutates state. Confirm with the user first.

```bash
# First, preview
mailctl messages search --subject receipt --json | jq -r '.[].id' > /tmp/receipt_ids.txt
cat /tmp/receipt_ids.txt                     # show user for confirmation

# Then act (one ID at a time; bulk isn't all-or-nothing, but it's
# easy to loop over IDs)
while read id; do mailctl messages mark "$id" --read; done < /tmp/receipt_ids.txt
```

### "Send the weekly status to my manager"

Two steps. Never send on the first turn.

```bash
# Step 1: draft
mailctl compose --to manager@example.com --subject "Weekly status" \
    --body-file /tmp/status.md --from Personal
# → tell the user the draft ID; `mailctl messages show <id>` to preview

# Step 2 (only after user confirms): send
mailctl compose --to manager@example.com --subject "Weekly status" \
    --body-file /tmp/status.md --from Personal --dangerously-send --yes
```

## Gotchas worth knowing

- **INBOX vs Inbox**: IMAP accounts (Gmail, most providers) use
  `INBOX` (uppercase). Exchange/EWS accounts use `Inbox` (title case).
  Always run `mailctl mailboxes list --account X` first if you're not
  sure.
- **Gmail label indirection**: Gmail represents INBOX / labels as
  virtual mailboxes over `[Gmail]/All Mail`. `mailctl` handles this
  transparently when you pass `--mailbox INBOX`; you don't need to do
  anything special.
- **Exchange can be slow**: Exchange accounts are slower to enumerate
  than IMAP. `mailctl mailboxes list --account <Exchange>` may take a
  few seconds the first time.
- **Partial messages**: `messages show` body may be empty if the
  message hasn't fully downloaded from the IMAP server. Not an error,
  but note it to the user.
- **Full Disk Access**: If `doctor` flags Full Disk Access missing,
  the terminal (or whatever's running `mailctl`) needs to be added
  in System Settings → Privacy & Security → Full Disk Access. Do NOT
  try to work around this by shelling out to AppleScript — the SQLite
  path is the whole point.
- **Account type "unknown"**: Modern Exchange accounts sometimes
  report their type as `unknown` in `accounts list`. That's a Mail.app
  quirk, not a `mailctl` bug. Identify them by name instead.

## Quick reference

```
mailctl doctor                                                    # health check
mailctl accounts list [--json]
mailctl mailboxes list [--account X] [--json]
mailctl messages list [--account X] [--mailbox M] [--unread] [--from F] \
                      [--subject S] [--since YYYY-MM-DD] [--before YYYY-MM-DD] \
                      [--limit N] [--json]
mailctl messages show <id> [--headers] [--raw] [--json]
mailctl messages search [--from F] [--subject S] [--since D] [--before D] \
                        [--account X] [--mailbox M] [--limit N] [--json]
mailctl messages mark <id> --read|--unread|--flagged|--unflagged
mailctl messages move <id> --to <mailbox>
mailctl messages delete <id> [--permanent] [--yes]
mailctl compose --to X [--cc Y] [--bcc Z] --subject S \
                (--body T | --body-file F | stdin) \
                [--from A] [--attach P ...] \
                [--dry-run] [--dangerously-send [--yes]]
mailctl reply <id> [--all] (--body T | --body-file F | stdin) [--dry-run] [--dangerously-send [--yes]]
mailctl forward <id> --to X (--body T | --body-file F | stdin) [--dry-run] [--dangerously-send [--yes]]
mailctl drafts list [--account X] [--json]
mailctl drafts edit <id> [--subject S] [--body T] [--body-file F] \
                         [--to X] [--cc Y] [--bcc Z] [--add-to X] [--remove-to X] \
                         [--attach P] [--remove-attach N] [--dry-run]
```
