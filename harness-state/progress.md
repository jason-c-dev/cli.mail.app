# Harness Progress Log

**Project**: Build `mailctl`, a Python CLI for Apple Mail.app on macOS. Language: Python 3.11+, Typer for the CLI framework, Rich for terminal output. Interact with Mail.app by shelling out to `osascript` running AppleScript (not JXA, not ScriptingBridge). Batch AppleScript operations where possible to minimise osascript startup overhead. Package with `pyproject.toml`, installable via `pipx install .`, with a `mailctl` entry point.

Core capabilities:
- Accounts: `mailctl accounts list` enumerates all configured accounts with their email addresses, types (IMAP/Exchange/iCloud), and enabled state.
- Mailboxes: list mailboxes/folders per account, including unread counts.
- Read: list messages in a mailbox with flags for unread-only, sender, subject search, and date range. Show a single message by ID including headers, body, and attachments metadata. Search across accounts.
- Create: compose new mail with to/cc/bcc, subject, body (stdin or file), attachments, and target account. Reply and reply-all to a message ID. Forward a message ID.
- Update: edit an existing draft (subject/body/recipients/attachments). Mark read/unread, flag/unflag, move between mailboxes.
- Delete: move a message to Trash, or permanently delete with confirmation.

Safety model - this is a hard requirement:
- `compose`, `reply`, `forward` create a DRAFT by default. They never send.
- Sending requires `--dangerously-send` on the command, every time. There is no persistent config option, env var, or alias that bypasses this flag.
- All destructive operations (permanent delete, send) print a one-line summary and require `--yes` to skip interactive confirmation.
- Dry-run mode (`--dry-run`) on every write command that prints what would happen without executing.

Output:
- Human-readable table output by default (Rich tables), with `--json` for machine-readable output on every command that returns data.
- Colourised output when stdout is a TTY, plain when piped.
- Useful, specific error messages when Mail.app isn't running, accounts aren't configured, or AppleScript automation permissions haven't been granted.

Testing:
- Unit tests with pytest. Mock the osascript subprocess layer so tests run without Mail.app.
- Separate integration test suite that hits real Mail.app, clearly marked and skippable.

Ergonomics:
- `mailctl --help` and `mailctl <subcommand> --help` must be genuinely useful.
- Shell completions for zsh.
- A `mailctl doctor` command that checks Mail.app is installed, running, scriptable, and reports which automation permissions are missing.

Out of scope for v1: calendar, contacts, rules/filters, signatures, multi-device sync concerns.
**Started**: 2026-04-15T23:25:25Z
**Model**: opus
**Context strategy**: reset

---

