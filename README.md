# mailctl

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![Mail.app](https://img.shields.io/badge/Mail.app-Envelope%20Index%20V10-important.svg)](#architecture)

A fast, safe command-line interface for Apple Mail.app on macOS.

`mailctl` talks to Mail.app's underlying SQLite index for reads, keeping
list / search / show operations under a second even on 100k-message
mailboxes. Writes (compose, reply, forward) go through AppleScript —
Mail.app remains the authoritative writer, which means sync and server
interaction behave exactly as they do in the GUI.

It's also explicitly intended as an **Apple Mail.app CLI for agent
harnesses that prefer local bash over MCP servers** — Claude Code,
scripted pipelines, cron jobs, anything that can shell out. The
draft-first safety model means a runaway script can fill your Drafts
folder but can't send. For agents that understand the skill format,
see the bundled [`skills/mailctl/`](skills/mailctl/SKILL.md) — drop it
into Claude Code's `.claude/skills/` (or the equivalent directory any
other harness that supports skills expects) and the agent will pick up
how to use `mailctl` without needing any context from this repo.

A short sample of what that feels like:

```console
$ mailctl messages list --account Personal --since 2026-04-10 --limit 5
                                 Messages
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Date             ┃ From               ┃ Subject                         ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2026-04-16 14:…  │ Friends of Tortoi… │ April newsletter                │
│ 2026-04-15 09:…  │ Acme Billing <bi…  │ Your receipt from Acme          │
│ 2026-04-14 16:…  │ GitHub <noreply@…  │ Security alert for a sign-in    │
...
$ time mailctl messages list --account Personal --since today | head
real    0m0.251s
```

## Table of contents

- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [The safety model](#the-safety-model)
- [Commands](#commands)
  - [doctor](#mailctl-doctor)
  - [accounts](#mailctl-accounts-list)
  - [mailboxes](#mailctl-mailboxes-list)
  - [messages list](#mailctl-messages-list)
  - [messages show](#mailctl-messages-show)
  - [messages search](#mailctl-messages-search)
  - [compose](#mailctl-compose)
  - [reply](#mailctl-reply) and [forward](#mailctl-forward)
  - [drafts](#mailctl-drafts-list-and-mailctl-drafts-edit)
  - [messages mark / move / delete](#mailctl-messages-mark--move--delete)
- [Architecture](#architecture)
- [Development](#development)
- [License](#license)

## Requirements

- macOS with Apple Mail.app configured and running at least once
- Python 3.11 or newer
- **Full Disk Access** granted to your terminal (or the process running
  `mailctl`). This is what lets the CLI read Mail.app's Envelope Index:
  System Settings → Privacy & Security → Full Disk Access → add Terminal
  / iTerm2 / your shell. You only need to do this once.
- **Automation permission** for Mail.app. The first time `mailctl` runs
  a write operation, macOS will prompt you to allow it. If you denied
  it earlier, re-enable it under Privacy & Security → Automation.

Verify both with:

```console
$ mailctl doctor
```

## Install

### Option A — `pipx` (recommended for end users)

```bash
git clone https://github.com/jason-c-dev/cli.mail.app.git
cd cli.mail.app
pipx install .
```

`mailctl` is now on your `PATH` with its own isolated environment.

### Option B — virtualenv (recommended for development)

```bash
git clone https://github.com/jason-c-dev/cli.mail.app.git
cd cli.mail.app
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
mailctl --version
```

From now on, activate the venv in each new shell (`source .venv/bin/activate`)
and use `mailctl` as normal.

### Shell completions

Zsh:

```bash
mailctl --install-completion zsh
# or manually:
mailctl --show-completion zsh >> ~/.zshrc
```

## Quick start

```bash
# Enter the venv
source .venv/bin/activate

# Verify setup
mailctl doctor
mailctl accounts list

# List the five most recent messages in your main account's INBOX
mailctl messages list --account Personal --limit 5

# Today's unread mail
mailctl messages list --account Personal --since 2026-04-16 --unread

# Cross-account search
mailctl messages search --subject receipt --limit 10

# Read a specific message (IDs come from `messages list --json`)
mailctl messages show 12345

# Draft an email — never sends without --dangerously-send
mailctl compose --to friend@example.com --subject "Hello" --body "Hi!"
```

Tip: every command that returns data also takes `--json`, which emits
machine-readable output suitable for `jq`:

```bash
mailctl messages list --account Personal --limit 3 --json | jq '.[].subject'
```

## The safety model

`mailctl` treats sending email as a dangerous operation and makes it
hard to do by accident.

- `compose`, `reply`, and `forward` create a **draft** in Mail.app by
  default. They never send.
- To actually send, you must pass `--dangerously-send` on every single
  invocation. There is no config file, environment variable, or alias
  that can bypass this flag. The absence of a bypass is the safety
  model — not a policy we enforce at the boundary.
- `--dangerously-send` also triggers an interactive confirmation prompt
  that defaults to "No". Pass `--yes` to skip the prompt, but `--yes`
  on its own does nothing — it only matters in combination with
  `--dangerously-send`.
- Every write command supports `--dry-run`, which prints what would
  happen without calling Mail.app at all.

Examples:

```bash
# Draft only (default) — creates a draft, never sends
mailctl compose --to friend@example.com --subject "Hi" --body "Hello"

# Dry run — doesn't touch Mail.app at all
mailctl compose --to friend@example.com --subject "Hi" --body "Hello" --dry-run

# Actual send — requires explicit flag, prompts for confirmation
mailctl compose --to friend@example.com --subject "Hi" --body "Hello" --dangerously-send

# Scripted send — explicit flag AND --yes to skip the prompt
mailctl compose --to friend@example.com --subject "Hi" --body "Hello" --dangerously-send --yes
```

Permanent deletion has the same shape: `mailctl messages delete <id>`
moves to Trash; `--permanent` requires confirmation before actually
deleting.

## Commands

### `mailctl doctor`

Checks that your environment is ready. Verifies:

1. `osascript` is available
2. Mail.app is installed
3. Mail.app is running
4. Automation permission for Mail.app is granted
5. At least one email account is configured
6. Mail.app's Envelope Index file is present
7. The Envelope Index is readable (Full Disk Access)
8. The Envelope Index schema matches the version `mailctl` was built for

Run this first if anything seems wrong.

```console
$ mailctl doctor
mailctl doctor — checking Mail.app integration

  ✔ osascript is available and functional.
  ✔ Mail.app is installed (/System/Applications/Mail.app).
  ✔ Mail.app is running.
  ✔ Automation permission granted for Mail.app.
  ✔ 2 account(s) configured in Mail.app.
  ✔ Envelope Index found (V10): ~/Library/Mail/V10/MailData/Envelope Index
  ✔ Envelope Index is readable (Full Disk Access granted).
  ✔ Envelope Index schema matches expectations.

All checks passed.
```

### `mailctl accounts list`

List all Mail.app accounts, their primary email address, type, and enabled
state.

```bash
mailctl accounts list
mailctl accounts list --json
```

### `mailctl mailboxes list`

List all mailboxes with unread and total counts. Optionally scope to one
account.

```bash
mailctl mailboxes list                       # all accounts
mailctl mailboxes list --account Personal
mailctl mailboxes list --account Personal --json
```

### `mailctl messages list`

List messages in a mailbox with rich filters. All filters combine
(boolean AND); results are sorted newest-first.

```bash
# Default: first 25 messages in INBOX of your primary account
mailctl messages list

# Scope to a specific account
mailctl messages list --account Personal

# Other mailbox (use `mailctl mailboxes list --account X` to find the name)
mailctl messages list --account Personal --mailbox "Sent Mail"

# Filter by unread / sender / subject (all case-insensitive substring)
mailctl messages list --unread
mailctl messages list --from acme
mailctl messages list --subject receipt

# Date range (inclusive start, exclusive end; YYYY-MM-DD)
mailctl messages list --since 2026-01-01 --before 2026-02-01

# Combine filters
mailctl messages list --account Personal --unread --from acme --limit 50

# JSON for piping
mailctl messages list --limit 10 --json | jq '.[] | {id, subject}'
```

Output keys (`--json`): `id`, `date`, `from`, `subject`, `read`, `flagged`.

### `mailctl messages show`

Show one message by ID. IDs come from the `id` field of `messages list`
or `messages search`.

```bash
mailctl messages show 12345
mailctl messages show 12345 --json
```

Prints From / To / Cc / Bcc / Date / Subject / Read / Flagged, then
attachment metadata, then the plain-text body. Bodies come from the
`.emlx` file on disk; if a message hasn't been fully downloaded by IMAP
yet (partial), `mailctl` falls back to asking Mail.app via AppleScript
so you still see the text.

JSON keys: `id`, `date`, `from`, `to`, `cc`, `bcc`, `subject`, `body`,
`headers`, `attachments`, `read`, `flagged`.

### `mailctl messages search`

Cross-account search with the same filters as `list`. One SQLite query
under the hood — sub-second even across hundreds of thousands of
messages.

```bash
# Cross-account by subject
mailctl messages search --subject receipt

# Scope to one account/mailbox
mailctl messages search --account Personal --mailbox INBOX --subject invoice

# By sender
mailctl messages search --from "vendor@acme.com"

# By date range + sender
mailctl messages search --from acme --since 2025-01-01 --before 2026-01-01

# JSON output
mailctl messages search --subject receipt --limit 20 --json
```

Note: `--body` (body-substring search) is not supported by the SQLite
backend because bodies live in separate `.emlx` files. The flag is
accepted for CLI compatibility but errors out; use `--subject` or
`--from` for fast search.

### `mailctl compose`

Create a draft or (with `--dangerously-send`) send an email. See
[The safety model](#the-safety-model) above.

```bash
# Draft with inline body
mailctl compose --to friend@example.com --subject "Hi" --body "Hello!"

# Multiple recipients (repeat the flag)
mailctl compose --to a@example.com --to b@example.com --cc c@example.com --subject "..." --body "..."

# Body from a file
mailctl compose --to friend@example.com --subject "Report" --body-file report.md

# Body from stdin
cat report.md | mailctl compose --to friend@example.com --subject "Report"

# Attachments (repeatable)
mailctl compose --to friend@example.com --subject "Photos" --body "Enjoy" \
    --attach ~/Pictures/one.jpg --attach ~/Pictures/two.jpg

# Send from a specific account
mailctl compose --to friend@example.com --subject "Hi" --body "..." --from Personal

# Actually send (with confirmation prompt)
mailctl compose --to friend@example.com --subject "Hi" --body "..." --dangerously-send

# Scripted send (skip prompt; still requires --dangerously-send)
mailctl compose --to friend@example.com --subject "Hi" --body "..." --dangerously-send --yes
```

### `mailctl reply`

Reply to an existing message. Creates a draft by default.

```bash
# Reply (to sender only)
mailctl reply 12345 --body "Thanks!"

# Reply-all
mailctl reply 12345 --all --body "Thanks everyone!"

# Actually send the reply
mailctl reply 12345 --body "Thanks!" --dangerously-send
```

### `mailctl forward`

Forward a message to a new set of recipients. Creates a draft by default.

```bash
mailctl forward 12345 --to friend@example.com --body "FYI"
```

### `mailctl drafts list` and `mailctl drafts edit`

List drafts across accounts, or modify an existing draft.

```bash
# List all drafts
mailctl drafts list
mailctl drafts list --json

# Scope to one account
mailctl drafts list --account Personal

# Edit a draft (modify any field)
mailctl drafts edit 9876 --subject "New subject"
mailctl drafts edit 9876 --body-file updated.md
mailctl drafts edit 9876 --add-to another@example.com
mailctl drafts edit 9876 --remove-to wrong@example.com
```

`drafts edit` never sends — there is no send path on this command by
design. To send a draft after editing, use the Mail.app UI or
`mailctl compose` a fresh message.

### `mailctl messages mark / move / delete`

Triage operations. All act via AppleScript so they stay consistent with
what the Mail.app GUI would do.

```bash
# Mark read / unread
mailctl messages mark 12345 --read
mailctl messages mark 12345 --unread

# Flag / unflag
mailctl messages mark 12345 --flagged
mailctl messages mark 12345 --unflagged

# Move to another mailbox (same account)
mailctl messages move 12345 --to Archive

# Delete — default moves to Trash; restore from Mail.app if needed
mailctl messages delete 12345

# Permanent delete — requires confirmation
mailctl messages delete 12345 --permanent
```

## Architecture

Mail.app keeps an authoritative SQLite index at
`~/Library/Mail/V*/MailData/Envelope Index`. All the expensive list /
search / show operations hit that index directly — milliseconds to tens
of milliseconds per query.

Message bodies are not in the index; they live as `.emlx` files under
each account's mailbox directory. `mailctl messages show` reads those
files and parses them with Python's stdlib `email` module.

Writes (compose, reply, forward, draft edit, delete, mark, move) all
flow through `osascript` targeting Mail.app. Mail.app is the system of
record for outgoing mail — writing through AppleScript means we get the
same server interaction, offline queueing, and IMAP/Exchange sync
behaviour the GUI has. No separate SMTP implementation.

Expected performance on a store with a 130k-message Gmail INBOX and a
10k-message Exchange Inbox:

| Command                                     | Typical  |
|---------------------------------------------|----------|
| `accounts list`                             | ~250 ms  |
| `mailboxes list`                            | ~250 ms  |
| `messages list --account X --limit 25`      | ~250 ms  |
| `messages search --subject X` (all acts)    | ~350 ms  |
| `messages show 12345`                       | ~300 ms  |
| `compose` (draft)                           | ~1–3 s (Mail.app roundtrip)  |

The one AppleScript round-trip on every CLI invocation is a tiny
`id / name` map of your configured accounts, used to translate UUIDs
in the SQLite rows back to user-friendly account names. That's cached
in-process for the run.

## Development

### Running the tests

```bash
source .venv/bin/activate
pip install -e .
pip install pytest
pytest -v
```

Tests are split into three layers:

- **Unit tests** (default) — hundreds of fast tests. The read path is
  exercised against a synthetic SQLite database built in-memory with
  the real Envelope Index schema (`tests/conftest.py::envelope_db`).
  The write path is exercised against a mocked `osascript` subprocess
  (`tests/conftest.py::mock_osascript`). Neither touches Mail.app.
- **Integration tests** (opt-in, marker `integration`) —
  `tests/integration/`. Marked with `@pytest.mark.integration`,
  deselected by default. Run them with `pytest -m integration`.
- **Legacy AppleScript-mock tests** — kept for historical documentation,
  skipped at the module level. `tests/unit/test_reads_sqlite.py` is the
  current coverage for read-path behaviour.

### Project layout

```
src/mailctl/
├── cli.py              # Typer entry point, command registration
├── engine.py           # AppleScript seam (osascript subprocess)
├── sqlite_engine.py    # SQLite seam (Envelope Index queries)
├── account_map.py      # UUID ↔ account-name bridge
├── emlx_reader.py      # .emlx message body parser
├── errors.py           # Exception hierarchy
├── output.py           # Rich table / JSON rendering
├── completions.py      # Shell completion helpers
└── commands/
    ├── doctor.py
    ├── accounts.py
    ├── mailboxes.py
    ├── messages.py     # list / show / search
    ├── compose.py
    ├── reply_forward.py
    ├── drafts.py       # list + edit
    ├── mark_move.py
    └── delete.py
docs/
└── product-spec.md     # detailed feature spec
tests/
├── unit/               # fast, no Mail.app
└── integration/        # opt-in, real Mail.app
```

### Contributing

This project follows the safety model strictly. Any change that
introduces a send-capable path must:

- Require an explicit per-invocation flag (no env var, no config file)
- Default to "cancel" at confirmation prompts
- Have corresponding unit tests asserting the bypass-resistance

### Using mailctl with Claude Code (or any skill-aware harness)

The supported agent integration is the skill at
[`skills/mailctl/SKILL.md`](skills/mailctl/SKILL.md). Copy the
`skills/mailctl/` directory into:

- Claude Code: a project's `.claude/skills/` or your user-level
  `~/.claude/skills/`.
- Any other harness that supports the skill format: whatever directory
  that harness expects skills to live in (see its docs).

The skill documents every command, the safety model, common workflows,
and gotchas — enough for a fresh agent to wield `mailctl` competently
without needing this repo's source tree.

There is deliberately no `CLAUDE.md` at the repo root. If you're
using Claude Code (or any agent) to modify `mailctl` itself, the README
plus `docs/product-spec.md` and the existing test suite are the
reference points — the safety-model tests under
`tests/unit/test_safety_model.py` make bypass-regressions hard to miss.

## License

Released under the [MIT License](LICENSE) — see the `LICENSE` file for
the full text.
