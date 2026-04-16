# mailctl — Product Specification

## 1. Product Overview

**mailctl** is a command-line interface for Apple Mail.app on macOS, giving power users and automation scripts programmatic control over their email without leaving the terminal. It bridges the gap between Mail.app's GUI-only workflow and the kind of composable, scriptable interface that CLI-native developers expect.

The tool uses a **hybrid backend**: reads go directly against Mail.app's underlying SQLite Envelope Index (`~/Library/Mail/V*/MailData/Envelope Index`), giving sub-second list and search even on six-figure mailboxes. Writes (compose, reply, forward, draft edit, delete, mark, move) go through AppleScript via `osascript` — Mail.app remains the authoritative writer, so sync, offline queueing, and IMAP/Exchange server interaction behave exactly as they do in the GUI. The SQLite path is read-only; it can't mutate Mail's state.

Every write operation is designed around a **draft-first safety model**: compose, reply, and forward create drafts by default and never send unless the user explicitly passes `--dangerously-send` on every invocation. There is no config file, environment variable, or alias that can bypass this gate. This makes mailctl safe to wire into automated pipelines — the worst a runaway script can do is fill your Drafts folder.

Built with Python 3.11+, Typer, and Rich, mailctl produces human-readable colourised tables by default and machine-readable JSON on request. It's packaged as a standard Python project installable via `pipx install .` and designed to feel like a first-class Unix citizen: composable, predictable, and honest about errors.

## 2. Target Users

### Power User / Developer
A macOS developer who lives in the terminal and resents context-switching to Mail.app to check messages or fire off a quick reply. They want to triage their inbox from the command line, search across accounts, and compose messages without touching a mouse. They value speed, keyboard-driven workflows, and scriptability.

### Automation Builder
An engineer or sysadmin who needs to integrate email into shell scripts, cron jobs, or agent pipelines — send build notifications, process incoming reports, or route messages between mailboxes. They need machine-readable output (`--json`), predictable exit codes, and absolute certainty that a script won't accidentally send an email. The draft-first safety model is their primary requirement.

### CLI Tool Author
A developer building higher-level tools on top of email (e.g., an AI email assistant, a ticket router, a CRM sync). They need a reliable subprocess they can call, parse JSON from, and trust not to do anything destructive without explicit flags.

## 3. Feature Specification

### 3.1 AppleScript Engine (Internal)

**Description**: Core layer for every **write** operation. Executes AppleScript via `osascript` subprocess and parses results.

**Why it matters**: Writes go through Mail.app (the authoritative writer) so that compose, send, move, and mark behave exactly as they do in the GUI — same server interaction, offline queueing, sync rules, and rule evaluation. The engine exists to make that subprocess layer predictable: batched, tested, and translating raw AppleScript errors into actionable CLI messages.

**Key behaviours**:
- Execute AppleScript strings via `subprocess.run` calling `osascript -e`
- Support multi-statement scripts to batch operations in a single `osascript` call
- Parse AppleScript return values (lists, records, strings, dates) into Python types
- Detect and categorise errors: Mail.app not running, automation permission denied, account not found, mailbox not found, message not found
- Normalise typographic apostrophes in Mail.app's error strings so pattern matching (e.g. "can't get") works reliably
- Timeout handling for unresponsive Mail.app
- All subprocess calls go through a single function so tests can mock one seam

**Dependencies**: None (foundational layer).

### 3.1b Envelope Index Engine (Internal)

**Description**: Parallel seam for all **read** operations. Queries Mail.app's SQLite Envelope Index directly in read-only mode.

**Why it matters**: The previous AppleScript-only read path couldn't keep up with real inboxes — `every message of mailbox` on a 130k-message Gmail INBOX serialises thousands of IPC round-trips and regularly fails with AppleScript error -1741. SQLite queries against the same data return in tens of milliseconds because Mail.app's index carries 53 purpose-built indexes covering every filter mailctl exposes.

**Key behaviours**:
- Open `~/Library/Mail/V*/MailData/Envelope Index` in read-only URI mode (`mode=ro`) so Mail.app's WAL-based writes are never blocked and we cannot accidentally mutate
- Resolve the versioned data directory (V10, V11, …) at runtime — new macOS releases pick up automatically
- Translate OS-level failures into domain exceptions with actionable hints: `EnvelopeIndexMissingError`, `FullDiskAccessError` (pointing at the specific System Settings pane), `EnvelopeIndexError`
- Expose a single `run_query(sql, params)` function — the mock seam for the read path, parallel to `run_applescript`
- Provide a `resolve_target_mailboxes(account_uuid, mailbox_name)` helper that handles Gmail's label indirection: Gmail's visible "INBOX" is a virtual mailbox whose messages actually live in `[Gmail]/All Mail`, joined via a `labels` table. The helper returns storage ROWIDs (for `messages.mailbox`) and label ROWIDs (for `labels.mailbox_id`) separately so read commands can OR the two membership tests
- Provide a `check_schema()` function listing any expected tables that are missing — surfaces Apple-side schema drift as a doctor-visible error rather than a vague SQL failure at query time

**Dependencies**: None (foundational). Used by all read-path commands.

### 3.1c Account Map (Internal)

**Description**: One-call bridge between AppleScript's friendly account names (e.g. "Personal") and the Envelope Index's UUID-in-URL identity model.

**Why it matters**: SQL queries return `mailboxes.url` values like `imap://{account-uuid}/INBOX`. Users type `--account Personal`. The account map makes one AppleScript call per CLI invocation to fetch `(id, name)` pairs for every configured account, caches them in module scope, and provides `uuid_for_name()` / `name_for_uuid()` translators.

**Key behaviours**:
- Single `tell application "Mail"` round-trip returning `id||name` pairs for every account
- `functools.lru_cache` so subsequent lookups in the same process are free
- Used by every SQLite-backed read command for UUID↔name translation

**Dependencies**: AppleScript Engine.

### 3.1d `.emlx` Reader (Internal)

**Description**: Parse Mail.app's on-disk message files to recover full message bodies that aren't stored in the Envelope Index.

**Why it matters**: The Envelope Index stores headers, preview, and metadata — not the full body. Bodies live as `.emlx` files under each mailbox's on-disk directory. For `messages show`, mailctl locates the right `.emlx` by account UUID and mailbox path, parses it with Python's stdlib `email` module, and extracts the plain-text body (or strips HTML if only HTML is present).

**Key behaviours**:
- Locate `.emlx` files via `glob` pattern scoped to the message's account and mailbox
- Handle both `.emlx` (fully downloaded) and `.partial.emlx` (IMAP partial download) suffixes
- Fall back to AppleScript for the body if the file is absent
- Escape `[` and `]` in mailbox paths so Gmail's `[Gmail]/All Mail` doesn't collapse under glob's character-class semantics

**Dependencies**: None.

### 3.2 Accounts List

**Description**: `mailctl accounts list` — enumerate all configured Mail.app accounts.

**Why it matters**: Users need to see which accounts are available before targeting commands at specific accounts. This is also the simplest smoke test that Mail.app integration works.

**Key behaviours**:
- List all accounts showing: name, email address(es), account type (IMAP, Exchange, iCloud, POP), enabled/disabled state
- Rich table output by default, `--json` for machine-readable
- Graceful error if Mail.app isn't running or not scriptable
- Exit code 0 on success, non-zero on failure

**Backend**: AppleScript. Accounts list is the one read command still on AppleScript — it's cheap (~200 ms against any number of accounts), and it also doubles as the UUID↔name source for the SQLite-backed commands.

**Dependencies**: AppleScript Engine.

### 3.3 Mailboxes List

**Description**: `mailctl mailboxes list` — list mailboxes/folders per account with unread counts.

**Why it matters**: Users need to discover folder structure before listing messages. Unread counts give an at-a-glance triage view.

**Key behaviours**:
- List all mailboxes for a given account, or all accounts if no account specified
- Show: account name, mailbox name (last path segment), unread count, total message count
- `--account` filter to scope to one account; unknown account produces a clear error listing known accounts
- Derive account names by parsing UUIDs out of `mailboxes.url` and cross-referencing with the account map
- Unread counts use `unread_count_adjusted_for_duplicates` — the field Mail.app's UI uses, which avoids counting the same Gmail message twice when it appears under multiple labels
- Rich table and `--json` output

**Backend**: SQLite. Sub-second even when an Exchange account is slow to script (no longer pays the Mail.app per-mailbox enumeration cost).

**Dependencies**: Envelope Index Engine, Account Map.

### 3.4 Message Listing

**Description**: `mailctl messages list` — list messages in a mailbox with filtering.

**Why it matters**: The core read operation. Users need to scan their inbox, find specific messages, and triage efficiently from the terminal.

**Key behaviours**:
- `--mailbox` (default INBOX) and optionally `--account`
- Filters: `--unread`, `--from <substring>`, `--subject <substring>`, `--since <YYYY-MM-DD>`, `--before <YYYY-MM-DD>`
- `--limit` to cap results (default: 25)
- All filters push down into the SQL `WHERE` clause — no "fetch everything then filter in Python" fallback
- Unknown mailbox produces a clear "not found" error pointing at `mailctl mailboxes list`; unknown account does the same with the list of known accounts
- Gmail label indirection is handled transparently: `--mailbox INBOX` against a Gmail account matches both the storage mailbox and any messages labelled INBOX via the `labels` table
- Date strings are parsed in the caller's local timezone and compared against the index's Unix-epoch `date_received` column
- Display: date, from, subject, read/unread status, flagged status, message ID (ROWID)
- Sort by date descending (newest first) — uses the `messages_mailbox_date_received_index`
- Rich table and `--json` output

**Backend**: SQLite. Typical latency ~250 ms against a 130k-message INBOX; the previous AppleScript path took 20–30 s and frequently failed outright with error -1741 on large IMAP mailboxes.

**Dependencies**: Envelope Index Engine, Account Map.

### 3.5 Message Show

**Description**: `mailctl messages show <message-id>` — display a single message in full.

**Why it matters**: After scanning a list, users need to read the actual content of a message including headers, body, and attachment info.

**Key behaviours**:
- Show full message: date, from, to, cc, bcc, subject, body (plain text preferred, HTML stripped if necessary)
- Attachments section listing: filename, attachment ID
- Message ID for use in reply/forward/update commands
- `--headers` flag to show all headers
- `--raw` flag to show unprocessed body
- Rich formatted output by default, `--json` for structured data
- Unknown or non-numeric ID produces a clear error

**Backend**: Hybrid. Headers, recipients, and attachment metadata come from SQLite (one query each, joining `subjects`/`addresses`/`recipients`/`attachments`). Body comes from the `.emlx` file on disk, parsed with Python's stdlib `email` module. If the `.emlx` file is absent (IMAP partial download), falls back to AppleScript for the body so the user still sees useful output.

**Fix note**: The previous AppleScript path hardcoded `mailbox "INBOX"` when fetching a message by ID, so `messages show` failed for any message outside INBOX. The SQLite path looks up the message by ROWID and derives its mailbox URL from the same row — mailbox-agnostic.

**Dependencies**: Envelope Index Engine, Account Map, `.emlx` Reader, AppleScript Engine (body fallback only).

### 3.6 Cross-Account Search

**Description**: `mailctl messages search <query>` — search messages across all accounts.

**Why it matters**: Users often don't know which account or mailbox contains the message they're looking for. Cross-account search eliminates the need to manually check each one.

**Key behaviours**:
- Search by: `--from`, `--subject`, `--since`, `--before`
- `--account` to scope to a single account
- `--mailbox` to scope to a single mailbox within that account
- `--limit` to cap results (default: 25)
- Results show account + mailbox context alongside message metadata
- Single SQL query across all accounts — no per-account iteration
- Rich table and `--json` output
- `--body` (full-text body search) is accepted for CLI compatibility but returns a clear "not yet supported" error. Bodies live in separate `.emlx` files and a fast body-scan path is out of scope for the current release; users should use `--subject` or `--from` for fast search

**Backend**: SQLite. Typical cross-account search completes in ~300–400 ms; the previous AppleScript path made N+1 `osascript` calls (one per account) and took 1–2 minutes on real mailboxes.

**Dependencies**: Envelope Index Engine, Account Map.

### 3.7 Compose (Draft Creation)

**Description**: `mailctl compose` — create a new email draft (or send with explicit flag).

**Why it matters**: The primary write operation. Creating emails from the CLI enables automation, scripting, and keyboard-driven workflows.

**Key behaviours**:
- Required: `--to <address>` (repeatable for multiple recipients), `--subject <text>`
- Optional: `--cc`, `--bcc` (repeatable), `--from <account>` (target account), `--attach <path>` (repeatable)
- Body: `--body <text>`, `--body-file <path>`, or read from stdin if neither provided and stdin is not a TTY
- **Default behaviour**: creates a draft in the Drafts folder. Does NOT send.
- `--dangerously-send`: the ONLY way to send. Not configurable. Not aliasable. Not env-var-able. Every invocation must explicitly include this flag.
- When `--dangerously-send` is used: print one-line summary of what will be sent, prompt for confirmation (y/N), or skip with `--yes`
- `--dry-run`: print what would happen without executing
- Output: confirmation message with draft/sent message ID, or `--json` with structured result

**Dependencies**: AppleScript Engine, Accounts List (for --from resolution).

### 3.8 Reply & Forward

**Description**: `mailctl reply <message-id>` and `mailctl forward <message-id>` — reply to or forward an existing message.

**Why it matters**: Reply and forward are natural follow-ups to reading a message. Having these in the CLI means users never need to context-switch to Mail.app.

**Key behaviours**:
- `mailctl reply <message-id>`: reply to sender only
- `mailctl reply <message-id> --all`: reply to all recipients
- `mailctl forward <message-id> --to <address>`: forward to specified recipient(s)
- Body provided via `--body`, `--body-file`, or stdin (prepended to quoted original)
- `--attach` for additional attachments
- Same safety model as compose: draft by default, `--dangerously-send` to send, confirmation prompt, `--dry-run`
- Reply/forward preserves original message threading where Mail.app supports it

**Dependencies**: AppleScript Engine, Message Show (for original message retrieval), Compose (shared safety model).

### 3.8.1 Drafts List

**Description**: `mailctl drafts list` — enumerate drafts across accounts.

**Why it matters**: Users need to see what drafts are sitting around — both from the Mail.app UI and from prior `mailctl compose` invocations — before editing or sending them.

**Key behaviours**:
- List every draft with: account, ID, date, recipients (To only, for brevity), subject
- `--account` filter to scope to one account
- Single SQL query finding every mailbox whose URL path ends in `/Drafts`, plus a batched recipient fetch
- Rich table and `--json` output

**Backend**: SQLite.

**Dependencies**: Envelope Index Engine, Account Map.

### 3.9 Draft Editing

**Description**: `mailctl drafts edit <message-id>` — modify an existing draft.

**Why it matters**: Since compose creates drafts by default, users need a way to iterate on a draft before sending — update recipients, fix the subject, add attachments.

**Key behaviours**:
- `--subject <text>`: replace subject
- `--body <text>` / `--body-file <path>` / stdin: replace body
- `--to`, `--cc`, `--bcc`: replace recipient lists (use `--add-to`, `--remove-to` for incremental changes)
- `--attach <path>`: add attachment, `--remove-attach <filename>`: remove attachment
- `--dry-run`: show what would change without modifying
- Never contains a `send` verb — by design there is no send path on this command
- Output: confirmation of changes made

**Backend**: AppleScript (write).

**Dependencies**: AppleScript Engine, Compose, Message Show.

### 3.10 Message Flags & Move

**Description**: `mailctl messages mark` and `mailctl messages move` — update message state.

**Why it matters**: Triage workflow: scan inbox, mark as read, flag important ones, file into folders — all without leaving the terminal.

**Key behaviours**:
- `mailctl messages mark <message-id> --read` / `--unread`
- `mailctl messages mark <message-id> --flagged` / `--unflagged`
- `mailctl messages move <message-id> --to <mailbox>` with optional `--account`
- Support multiple message IDs in one command for bulk operations
- `--dry-run` on all operations
- Batch AppleScript for bulk operations

**Dependencies**: AppleScript Engine, Message Listing (for message ID resolution), Mailboxes List (for move target resolution).

### 3.11 Delete

**Description**: `mailctl messages delete <message-id>` — move to Trash or permanently delete.

**Why it matters**: Complete mailbox management requires the ability to clean up messages.

**Key behaviours**:
- Default: move to Trash (non-destructive, recoverable)
- `--permanent`: permanently delete. Prints one-line summary, requires interactive confirmation (y/N) or `--yes` to skip
- Support multiple message IDs for bulk delete
- `--dry-run`: show what would be deleted
- Batch AppleScript for bulk operations

**Dependencies**: AppleScript Engine, Message Listing, Message Flags & Move (shared patterns).

### 3.12 Doctor Command

**Description**: `mailctl doctor` — diagnose the Mail.app integration environment.

**Why it matters**: When things don't work, users need clear guidance on what's wrong and how to fix it. AppleScript automation permissions and Full Disk Access are notoriously confusing on modern macOS.

**Key behaviours** (eight checks total):

AppleScript side:
- `osascript` is available and functional
- Mail.app is installed
- Mail.app is running (and offer to launch if not)
- Terminal/iTerm has automation permission for Mail.app
- At least one account is configured

SQLite side (added with the hybrid backend):
- Envelope Index file is present (reports which V* version was found)
- Envelope Index is readable — if blocked by TCC, the error message points at the exact System Settings pane (Privacy & Security → Full Disk Access)
- Envelope Index schema matches expectations — lists any missing required tables. Covers the case where Apple changes the schema in a future macOS release so the failure mode is a clean, human-readable check rather than vague SQL errors at query time

Each check shows pass/fail with actionable fix instructions on failure. Exit code 0 if all pass, non-zero if any fail. `--json` output for programmatic health checks.

**Dependencies**: AppleScript Engine, Envelope Index Engine.

### 3.13 Output System

**Description**: Consistent output formatting across all commands.

**Why it matters**: A CLI tool lives or dies by its output. Consistent, readable tables for humans and parseable JSON for machines make mailctl a reliable building block.

**Key behaviours**:
- Rich tables with column alignment, truncation for long fields, colourised status indicators
- `--json` flag on every command that returns data — outputs valid JSON to stdout
- TTY detection: colour when stdout is a terminal, plain when piped
- `--no-color` flag to force plain output
- Error messages to stderr, data to stdout
- Consistent exit codes: 0 success, 1 general error, 2 usage error

**Dependencies**: None (utility layer, built alongside first commands).

### 3.14 Shell Completions

**Description**: Zsh completion support for all commands and options.

**Why it matters**: Tab completion dramatically improves discoverability and speed for CLI power users.

**Key behaviours**:
- Typer's built-in completion generation for zsh
- `mailctl --install-completion` to set up
- Complete command names, option names, and where feasible account/mailbox names

**Dependencies**: All commands (completions reflect the full command tree).

### 3.15 Testing Infrastructure

**Description**: Comprehensive test suite with mocked seams at both backends.

**Why it matters**: Tests that require Mail.app can't run in CI. Two clean mock boundaries — one at the `osascript` subprocess layer for writes, one at the SQLite connection layer for reads — let the entire CLI be tested without Mail.app. The SQLite side uses a **real synthetic database** rather than string mocks, which is a deliberate move away from mock-only testing that was shown to hide platform-specific bugs.

**Key behaviours**:
- pytest as the test framework
- `mock_osascript` fixture that intercepts all `osascript` subprocess calls and returns canned AppleScript output — covers write paths
- `envelope_db` fixture that builds an in-memory SQLite database matching the real Envelope Index schema and wires `sqlite_engine.envelope_index_path` + `account_map.get_account_map` to point at it. Tests that want seeded data call helper methods like `add_mailbox()` and `add_message(sender=..., subject=..., labels=[...])` to construct exact scenarios
- Autouse empty-DB fixture so tests that don't opt in still get a predictable empty database, never the user's real mail store
- Unit tests for every command covering success, error, and edge cases
- Unit tests for the safety model: verify compose/reply/forward never send without `--dangerously-send`, verify no env-var / config-file / alias bypass exists (code-inspection tripwire)
- Unit tests for the SQL path: Gmail label indirection, account/mailbox scoping, filter pushdown, schema-drift detection
- Separate integration test directory (`tests/integration/`) with `@pytest.mark.integration` marker
- `pyproject.toml` config to skip integration tests by default
- Integration tests clearly documented as requiring Mail.app

**Dependencies**: All features (tests cover all commands).

## 4. Visual Design Language

Not applicable — this is a CLI tool. The "visual design" is the terminal output:

- **Tables**: Clean, minimal Rich tables. No box-drawing overkill. Use `box=rich.box.SIMPLE_HEAVY` or similar for a professional look. Column headers in bold, not caps.
- **Colours**: Minimal palette. Unread messages in bold white, read messages in dim. Flagged messages get a coloured indicator. Errors in red, warnings in yellow, success in green. No gratuitous colour.
- **Density**: Show useful information, not decorative padding. Truncate long subjects/addresses with ellipsis rather than wrapping.
- **Consistency**: Every command that lists things uses the same table formatting. Every confirmation prompt uses the same pattern. Every error message follows the same structure: `Error: <what went wrong>. <what to do about it>.`
- **Personality**: Dry, professional. No emoji in output. No "awesome!" confirmations. `Draft created in "Work" account.` not `✨ Your draft has been created! ✨`.

## 5. Technical Architecture

### Stack
- **Language**: Python 3.11+
- **CLI framework**: Typer (with Click underneath)
- **Terminal output**: Rich (tables, colours, markup)
- **AppleScript execution (writes)**: `subprocess.run` calling `osascript`
- **Envelope Index access (reads)**: Python stdlib `sqlite3` in read-only URI mode
- **Message body parsing**: Python stdlib `email` + `.emlx` file layer
- **Packaging**: `pyproject.toml` with `[project.scripts]` entry point
- **Installation**: `pipx install .`
- **Testing**: pytest with subprocess mocking (write path) and synthetic in-memory SQLite (read path)

### Data Flow

**Read path** (accounts, mailboxes, messages list/show/search, drafts list):
```
User CLI input
  → Typer command parser
    → Command handler (validates args, resolves account UUID via account map)
      → Envelope Index engine (sqlite3, read-only URI, SQL WHERE pushdown)
        → Optional .emlx read for message show body
          → Output formatter (Rich table or JSON)
            → stdout
```

**Write path** (compose, reply, forward, drafts edit, mark, move, delete):
```
User CLI input
  → Typer command parser
    → Safety gate (--dangerously-send requirement, confirmation, --dry-run)
      → Command handler (validates args, applies defaults)
        → AppleScript engine (builds script, executes via osascript, parses result)
          → Output formatter (Rich table or JSON)
            → stdout
```

### Key Architectural Decisions

1. **Separate engines for reads and writes**: Two parallel seams (`sqlite_engine.py` for reads, `engine.py` for AppleScript writes) rather than one unified engine. The two have fundamentally different failure modes — TCC/Full Disk Access vs. automation permission, schema drift vs. Mail.app runtime state — and trying to unify them at the seam would have forced every caller to handle both sets.

2. **Reads are read-only at the database level**: the SQLite connection is opened with `mode=ro` URI. We can't corrupt the index even by accident. Mail.app holds the write lock and remains the sole writer.

3. **Writes stay in Mail.app's hands**: All state changes (send, move, mark, delete) go through AppleScript. This means send queueing, offline behaviour, and server interaction are whatever Mail.app would do — not a reimplementation that could subtly diverge.

4. **Single execution seam per backend**: `run_applescript(script)` and `run_query(sql, params)` are each the one-and-only function that talks to their respective subsystem. This is the mock boundary for tests and the place to add logging, timing, and error translation.

5. **Filter pushdown into SQL**: Every filter mailctl exposes (`--unread`, `--from`, `--subject`, `--since`, `--before`, `--mailbox`, `--account`) becomes a `WHERE` clause condition. No "fetch everything and filter in Python" fallback — it would defeat the performance gains and diverge from what users can reason about from a SQL query log.

6. **Gmail label indirection in the SQL, not the command**: `resolve_target_mailboxes` (in `sqlite_engine.py`) transparently splits a mailbox-name scope into storage ROWIDs and label ROWIDs. Commands don't need to know about Gmail's quirks; they just pass the user's `--mailbox` string through.

7. **Message ID scheme**: SQLite ROWIDs (the `messages.ROWID` column) are the canonical message identifier throughout the tool. IDs that `list`/`search` output can be passed to `show`, `mark`, `move`, `delete`, `reply`, `forward` without translation.

8. **Safety as architecture, not policy**: The `--dangerously-send` flag is enforced in the compose/reply/forward command handlers. There is no configuration system, no environment variable reader, no "default flags" mechanism that could bypass it. The absence of these features IS the safety model. Unit tests grep the codebase for bypass patterns as a standing regression net.

9. **Output mode as a cross-cutting concern**: A shared output context (table vs JSON, colour vs plain) is set once at CLI entry and threaded through all commands. Commands produce data structures; the output layer decides how to render them.

10. **One AppleScript round-trip per invocation for identity**: `account_map.get_account_map()` makes a single call at startup (cached for the process lifetime) to fetch the UUID↔name mapping for every account. This is the only "cost" that SQLite-backed commands pay to the AppleScript layer.

### Entity Model
- **Account**: UUID (from `id of account`), name, email addresses, type (IMAP/Exchange/iCloud/POP), enabled state
- **Mailbox**: SQLite ROWID, URL (encoding account UUID + path), name (last path segment), unread count, message count, `source` (non-null for Gmail-style virtual label mailboxes)
- **Message**: SQLite ROWID, date_received (Unix epoch), from (sender address + display-name comment), to/cc/bcc (from `recipients` table joined to `addresses`), subject (`subject_prefix` + joined `subjects.subject`), body (from `.emlx` file on disk), read/flagged/deleted flags, mailbox reference, attachments
- **Attachment**: SQLite ROWID, message reference, attachment_id, name (metadata only — mailctl doesn't download attachments in v1)

### Environment Prerequisites
- macOS with Mail.app launched at least once (so the Envelope Index exists)
- Full Disk Access granted to the terminal or process running `mailctl` — the Envelope Index lives in a TCC-protected location
- Automation permission for Mail.app — granted on first write command, confirmable via `mailctl doctor`

## 6. Sprint Decomposition

### Sprint 1: Project Scaffolding & AppleScript Engine
**Theme**: Build the foundation — project structure, CLI skeleton, and the core AppleScript execution layer that everything else depends on.

**Features**: AppleScript Engine (3.1), partial Output System (3.13)

**Scope**:
- `pyproject.toml` with metadata, dependencies, and `mailctl` entry point
- Typer app with `--version` and `--help`
- AppleScript engine: execute scripts via osascript, parse results, handle errors
- Error categories: Mail.app not running, permission denied, script error
- pytest infrastructure with osascript mock fixture
- Unit tests for the AppleScript engine

**Dependencies**: None
**Estimated Complexity**: Medium

---

### Sprint 2: Accounts & Mailboxes
**Theme**: First real commands — list accounts and mailboxes with full output formatting.

**Features**: Accounts List (3.2), Mailboxes List (3.3), Output System (3.13)

**Scope**:
- `mailctl accounts list` command
- `mailctl mailboxes list` command with `--account` filter
- Rich table output with column formatting
- `--json` flag on both commands
- TTY detection and `--no-color`
- Stderr for errors, stdout for data
- Unit tests for both commands and the output system

**Dependencies**: Sprint 1
**Estimated Complexity**: Medium

---

### Sprint 3: Message Listing & Show
**Theme**: Read email from the terminal — list messages with filters and show individual messages.

**Features**: Message Listing (3.4), Message Show (3.5)

**Scope**:
- `mailctl messages list` with `--mailbox`, `--account`, `--unread`, `--from`, `--subject`, `--since`, `--before`, `--limit`
- `mailctl messages show <id>` with headers, body, attachment metadata
- Batch AppleScript for message metadata fetching
- `--headers` and `--raw` flags on show
- Rich formatted output and `--json` for both commands
- Unit tests for listing with various filter combinations

**Dependencies**: Sprint 2
**Estimated Complexity**: High

---

### Sprint 4: Cross-Account Search
**Theme**: Find any message across all accounts without knowing where it lives.

**Features**: Cross-Account Search (3.6)

**Scope**:
- `mailctl messages search` with `--from`, `--subject`, `--body`, `--since`, `--before`
- `--account` and `--mailbox` scoping
- `--limit` with default
- Results include account and mailbox context
- Batch search across accounts
- Rich table and `--json` output
- Unit tests

**Dependencies**: Sprint 3
**Estimated Complexity**: Medium

---

### Sprint 5: Compose & Safety Model
**Theme**: Create emails with an ironclad draft-first safety model.

**Features**: Compose (3.7), core Safety Model

**Scope**:
- `mailctl compose` with `--to`, `--cc`, `--bcc`, `--subject`, `--from`, `--attach`
- Body from `--body`, `--body-file`, or stdin
- Default: creates draft, never sends
- `--dangerously-send`: the only path to sending. Enforced in code, no bypass mechanism.
- Confirmation prompt when sending (y/N default), `--yes` to skip
- `--dry-run` showing what would be created/sent
- Unit tests specifically verifying the safety model cannot be bypassed

**Dependencies**: Sprint 2
**Estimated Complexity**: High

---

### Sprint 6: Reply & Forward
**Theme**: Respond to and forward existing messages with the same safety guarantees.

**Features**: Reply & Forward (3.8)

**Scope**:
- `mailctl reply <id>` — reply to sender
- `mailctl reply <id> --all` — reply to all
- `mailctl forward <id> --to <address>`
- Body via `--body`, `--body-file`, or stdin
- `--attach` for additional attachments
- Same safety model: draft by default, `--dangerously-send`, confirmation, `--dry-run`
- Threading support where Mail.app allows
- Unit tests including safety model verification

**Dependencies**: Sprint 3, Sprint 5
**Estimated Complexity**: Medium

---

### Sprint 7: Message Updates (Mark, Flag, Move)
**Theme**: Triage your inbox — mark, flag, and file messages without leaving the terminal.

**Features**: Message Flags & Move (3.10)

**Scope**:
- `mailctl messages mark <id> --read/--unread/--flagged/--unflagged`
- `mailctl messages move <id> --to <mailbox>` with `--account`
- Bulk operations: accept multiple message IDs
- `--dry-run` on all operations
- Batch AppleScript for bulk operations
- Unit tests

**Dependencies**: Sprint 3
**Estimated Complexity**: Medium

---

### Sprint 8: Draft Editing & Delete
**Theme**: Complete the write story — edit drafts and delete messages.

**Features**: Draft Editing (3.9), Delete (3.11)

**Scope**:
- `mailctl drafts edit <id>` with `--subject`, `--body`, `--body-file`, `--to`, `--cc`, `--bcc`, `--add-to`, `--remove-to`, `--attach`, `--remove-attach`
- `mailctl messages delete <id>` — move to Trash by default
- `--permanent` with confirmation prompt, `--yes` to skip
- Bulk delete with multiple IDs
- `--dry-run` on all operations
- Unit tests

**Dependencies**: Sprint 5, Sprint 7
**Estimated Complexity**: High

---

### Sprint 9: Doctor Command & Error Polish
**Theme**: Make failures helpful — diagnostics command and polished error messages everywhere.

**Features**: Doctor Command (3.12), Error handling polish across all commands

**Scope**:
- `mailctl doctor` with checks: Mail.app installed, running, scriptable, osascript available, accounts configured
- Actionable fix instructions for each failure
- `--json` output for programmatic checks
- Review and improve error messages across all existing commands
- Consistent error format: `Error: <problem>. <solution>.`
- Unit tests for doctor command

**Dependencies**: Sprint 2
**Estimated Complexity**: Low

---

### Sprint 10: Shell Completions, Help & Integration Tests
**Theme**: Polish and professional finish — completions, help text, and real-world testing.

**Features**: Shell Completions (3.14), Testing Infrastructure (3.15), Help text quality

**Scope**:
- Zsh shell completions via Typer
- `mailctl --install-completion` command
- Review and improve all `--help` text for clarity and usefulness
- Integration test suite in `tests/integration/` with `@pytest.mark.integration`
- Integration tests for accounts, mailboxes, message listing (read-only operations)
- pytest config to skip integration tests by default (`-m "not integration"`)
- Documentation in test directory explaining how to run integration tests

**Dependencies**: All prior sprints
**Estimated Complexity**: Medium

---

### Post-Sprint 10: SQLite Read Backend
**Theme**: Fix the performance floor. Real-world testing exposed that AppleScript reads were unusable on large mailboxes (20–60 s for `messages list` on a 130k-message Gmail INBOX, frequent error -1741 on unsynced messages). This sprint replaced every read path with direct SQLite queries against Mail.app's Envelope Index while leaving writes on AppleScript.

**Features**: Envelope Index Engine (3.1b), Account Map (3.1c), `.emlx` Reader (3.1d), plus re-implementation of every read command (3.3–3.6, 3.9a) against the new backend.

**Scope**:
- `sqlite_engine.py` with `run_query`, `envelope_index_path`, `check_schema`, `resolve_target_mailboxes`
- `account_map.py` with cached UUID↔name resolver
- `emlx_reader.py` for `.emlx` and `.partial.emlx` body parsing (falls back to AppleScript if the file is absent)
- New exception hierarchy: `EnvelopeIndexError`, `EnvelopeIndexMissingError`, `FullDiskAccessError` — the last carrying an actionable System Settings pointer
- Swap the internals of `fetch_mailboxes`, `fetch_messages`, `fetch_message`, `fetch_search_results`, `fetch_drafts` to SQLite while preserving return shapes so the Typer handlers and output layer are unchanged
- Handle Gmail label indirection via the `labels` table
- Three new `doctor` checks: Envelope Index present, readable (Full Disk Access), schema matches
- New `envelope_db` pytest fixture building an in-memory SQLite database from the real schema, replacing mock-only read tests
- Legacy AppleScript-mock tests skipped at the module level with a pointer to the replacement suite

**Measured impact on a real store (130k Gmail INBOX + 10k Exchange Inbox)**:

| Command | Before | After |
|---|---|---|
| `messages list --account Gmail` | 22 s | 0.25 s |
| `messages list --since today` | 30 s | 0.23 s |
| `messages search --subject X` (cross-account) | 74 s | 0.33 s |
| `mailboxes list --account Exchange` | 19 s | 0.25 s |

**Dependencies**: All prior sprints
**Estimated Complexity**: High
