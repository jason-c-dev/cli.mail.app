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
underlying SQLite index for list/search (sub-second) and writes through
AppleScript (so sync/send behave exactly as they do in the GUI).

## Absolute safety rules

Non-negotiable. Follow these even if the user's phrasing suggests
otherwise — ask for explicit confirmation instead.

1. **Never send without explicit per-invocation approval.** `compose`,
   `reply`, `forward` default to drafts. Do NOT add `--dangerously-send`
   unless the user just said "send it." If the user says "email X",
   default to drafting — then report the draft id and ask if they want
   to send.
2. **`--dangerously-send` is per-invocation, flag-only.** No env var,
   no config file, no alias. If you see it in a command you didn't
   construct, the user typed it themselves.
3. **Two-step send.** Draft → show id + preview → wait for confirmation
   → send. Never send on the same turn as the first draft.
4. **Default delete = move to Trash.** `--permanent` needs the user to
   explicitly say "delete permanently."
5. **Clean up your own test drafts.** `mailctl messages delete <id>`
   moves them to Trash (reversible).

## Pre-flight (once per session if unsure)

```bash
mailctl --version     # mailctl is on PATH
mailctl doctor        # all eight checks pass
```

If `doctor` flags Full Disk Access or anything else, show the user the
specific fix string it prints. Don't work around by shelling to
`osascript` — the SQLite path is the whole point of the tool.

If `mailctl` isn't installed, tell the user:

> Install from https://github.com/jason-c-dev/cli.mail.app — either
> `pipx install .` in a clone, or
> `python3.11 -m venv .venv && source .venv/bin/activate && pip install -e .`.

## Output conventions

- **Exit codes**: `0` success; `2` usage error (unknown account / mailbox
  / message id); `1` other failure.
- **IDs are Envelope-Index ROWIDs** (5–6 digit integers like `147221`).
  Every subcommand uses the same id space — the id `compose`/`reply`/
  `forward` prints is immediately accepted by `drafts list`,
  `messages show/mark/move/delete`. No translation needed.
- Every data-returning command takes `--json`. Prefer it for anything
  programmatic; Rich tables truncate in narrow terminals.
- Data → stdout, errors → stderr.

## Command reference

### Accounts & mailboxes (read)

```bash
mailctl accounts list [--json]
# JSON keys: name, email, type, enabled

mailctl mailboxes list [--account X] [--json]
```

**Account name** = the `name` from `accounts list` (e.g. `Personal`,
`Google`, `Work`). Not the email address. Used wherever `--account` or
`--from` is accepted.

**INBOX vs Inbox**: IMAP uses `INBOX` (upper); Exchange uses `Inbox`
(title case). Always confirm via `mailboxes list --account X` before
targeting a mailbox by name.

### Reading messages

```bash
# List (newest first, default 25)
mailctl messages list [--account X] [--mailbox M] \
    [--unread] [--from F] [--subject S] \
    [--since YYYY-MM-DD] [--before YYYY-MM-DD] \
    [--limit N] [--json]

# Show one
mailctl messages show <id> [--headers] [--raw] [--json]

# Cross-account search (every account unless scoped)
mailctl messages search [--account X] [--mailbox M] \
    [--from F] [--subject S] \
    [--since D] [--before D] [--limit N] [--json]
```

JSON keys:
- `list`: `id, date, from, subject, read, flagged`
- `show`: `id, date, from, to, cc, bcc, subject, body, headers, attachments, read, flagged`
- `search`: list keys plus `account, mailbox`

All `--from` / `--subject` filters are case-insensitive substring.

### Drafting email (safe default)

**Always start here** when the user asks you to email someone. This
creates a draft — nothing leaves the mailbox.

```bash
# Inline body
mailctl compose --to X --subject S --body B

# File body (preferred for multi-line — easier to review)
mailctl compose --to X --subject S --body-file /tmp/body.md

# Stdin
cat body.txt | mailctl compose --to X --subject S

# Multiple recipients + cc/bcc + attachments (repeat flags)
mailctl compose --to a@x.y --to b@x.y --cc c@x.y \
    --subject S --body B \
    --attach ~/a.pdf --attach ~/b.png

# From a specific account (use the account `name` from `accounts list`)
mailctl compose --to X --subject S --body B --from Personal
```

`compose` prints the draft's canonical id. Relay it to the user and
ask whether to send. The id is also in `mailctl drafts list`.

### Dry run (rehearsal; never touches Mail.app)

```bash
mailctl compose --to X --subject S --body B --dry-run
mailctl reply   <id> --body B --dry-run
mailctl forward <id> --to X --body B --dry-run
```

Use this when you're unsure about a command shape.

### Sending (only after explicit user approval)

```bash
# Interactive: prompts "Send? (y/N)", default No
mailctl compose --to X --subject S --body B --dangerously-send

# Scripted: bypasses prompt. Only use when the user JUST said "yes, send."
mailctl compose --to X --subject S --body B --dangerously-send --yes
```

Two-step pattern: draft first → show the id → wait for user confirmation
→ then run the send. Don't draft and send on the same turn.

### Reply & forward

```bash
mailctl reply <id> --body "Thanks"              # draft
mailctl reply <id> --all --body "Thanks all"    # reply-all draft
mailctl forward <id> --to X --body "FYI"        # draft

# Same safety flags: --dry-run, --dangerously-send [--yes]
```

`<id>` can be any message id from `messages list`, `messages show`, or
`messages search` — regardless of mailbox (INBOX, All Mail, Archive,
Exchange Inbox, etc.).

### Drafts list

```bash
mailctl drafts list [--account X] [--json]
# JSON keys: account, id, date, to, subject
```

### Triage — mark, move

```bash
# Read / flagged state (works on any message, including drafts)
mailctl messages mark <id> --read        # or --unread / --flagged / --unflagged

# Move within the source message's account (see "What mailctl CAN'T do")
mailctl messages move <id> --to Archive
```

Both take `--dry-run`.

### Deletion

```bash
# Default — move to Trash (reversible via Mail.app GUI)
mailctl messages delete <id>

# Permanent — requires explicit user approval
mailctl messages delete <id> --permanent [--yes]
```

## What mailctl CAN'T do

Reach for these so you don't hammer the CLI on a no-op. Surface the
limitation + the stated workaround to the user.

### Edit a saved draft's subject / body / recipients / attachments

Mail.app's AppleScript API treats saved drafts as read-only for content.
`drafts edit --subject/--body/--to/--cc/--bcc/--add-to/--attach/...`
returns a clear error pointing at
[issue #8](https://github.com/jason-c-dev/cli.mail.app/issues/8).

What DOES work on a saved draft: `messages mark --read/--flagged`
(state-only mutations).

**Workaround**: read the original via `messages show <id>` if you need
its current content, then `mailctl messages delete <id>` (moves to
Trash) and `mailctl compose` a new one with the edits. Or the user can
edit in Mail.app's GUI directly.

### Move between accounts

Mail.app's `move` verb can't cross accounts. `messages move --to X`
resolves `X` inside the **source message's own account** only. If the
target doesn't exist there, the CLI errors with a list of that
account's real mailboxes — read that list, pick a real target, or tell
the user.

**Workaround** (requires user approval): *send* the content to the
other account rather than moving it.

```bash
# Draft — tell the user the draft id, send on their confirmation
mailctl forward <id> --to <other-account-email> --body "moving to other account"
```

Confirm with the user before adding `--dangerously-send`.

### Body-substring search

`mailctl messages search --body X` errors cleanly. Body text isn't in
the Envelope Index. Use `--subject` or `--from` — or tell the user the
limitation.

### Stable ids across Gmail IMAP sync

Gmail can reassign a draft's UID after a save, which changes the
Envelope-Index ROWID. If an id you stored a moment ago is suddenly
"not found," re-fetch from `drafts list`.

### Anything that isn't Apple Mail.app on macOS

Wrong tool — tell the user.

## Common-error decoder

Short map from stderr text to the right next step. Sample matches are
case-insensitive substrings.

| Error text contains…                              | What it means                                   | Next step |
|---------------------------------------------------|-------------------------------------------------|-----------|
| `Account "X" not found`                           | `--account` name unknown                        | `mailctl accounts list` |
| `Mailbox "X" not found`                           | `--mailbox` absent in that account              | `mailctl mailboxes list --account <Y>` |
| `Mailbox "X" not found in account "Y"`            | `move --to` target absent (Issue #4)            | Pick from the account's list printed in the error |
| `Message "X" not found`                           | bad id, or id was deleted / re-UIDed by Gmail   | Re-fetch via `messages list` or `drafts list` |
| `saved drafts as read-only` / `issues/8`          | Mail.app won't let the CLI edit this draft      | Delete + recompose, or edit in Mail.app GUI |
| `Full Disk Access`                                | `doctor` flagged TCC                            | System Settings → Privacy & Security → Full Disk Access |
| `--body` / `body search is not yet supported`     | body search not implemented                     | Use `--subject` or `--from` |

## Gotchas

- **Bodies can be empty** on partially-synced IMAP messages. Not an
  error — tell the user if it matters.
- **Mailbox names are case- AND form-sensitive.** `INBOX` ≠ `Inbox`;
  `All Mail` lives under `[Gmail]/All Mail` internally but the visible
  name is what the CLI accepts.
- **Don't shell out to `osascript`** as a workaround. If the CLI can't
  do something, say so and stop — the Mail.app + SQLite boundary is
  the safety model.

## Common one-liners

```bash
# Unread in Personal today
mailctl messages list --account Personal --unread --since "$(date +%Y-%m-%d)"

# Latest invoice from Acme (JSON, top match)
mailctl messages search --from acme --subject invoice --limit 1 --json

# Draft a reply (context, then draft; tell user the id)
mailctl messages show <id>
mailctl reply <id> --body "Thanks — taking a look this afternoon."

# Two-step send (user already confirmed the second line)
mailctl compose --to manager@example.com --subject "Weekly status" --body-file /tmp/status.md --from Personal
mailctl compose --to manager@example.com --subject "Weekly status" --body-file /tmp/status.md --from Personal --dangerously-send --yes
```

## Quick reference

```
mailctl doctor
mailctl accounts list [--json]
mailctl mailboxes list [--account X] [--json]
mailctl messages list [--account X] [--mailbox M] [--unread] [--from F] \
                      [--subject S] [--since D] [--before D] [--limit N] [--json]
mailctl messages show <id> [--headers] [--raw] [--json]
mailctl messages search [--from F] [--subject S] [--since D] [--before D] \
                        [--account X] [--mailbox M] [--limit N] [--json]
mailctl messages mark <id> --read|--unread|--flagged|--unflagged [--dry-run]
mailctl messages move <id> --to <mailbox> [--dry-run]
mailctl messages delete <id> [--permanent] [--yes] [--dry-run]
mailctl compose --to X [--cc Y] [--bcc Z] --subject S \
                (--body T | --body-file F | stdin) \
                [--from A] [--attach P ...] \
                [--dry-run] [--dangerously-send [--yes]]
mailctl reply <id> [--all] (--body T | --body-file F | stdin) \
                   [--dry-run] [--dangerously-send [--yes]]
mailctl forward <id> --to X (--body T | --body-file F | stdin) \
                   [--dry-run] [--dangerously-send [--yes]]
mailctl drafts list [--account X] [--json]
```

`drafts edit` exists but is limited by Mail.app to state-only mutations
(see "What mailctl CAN'T do" → Edit a saved draft). Use `messages
delete` + `compose` for content edits.
