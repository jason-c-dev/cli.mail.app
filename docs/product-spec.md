# mailctl — Product Specification

## 1. Product Overview

**mailctl** is a command-line interface for Apple Mail.app on macOS, giving power users and automation scripts programmatic control over their email without leaving the terminal. It bridges the gap between Mail.app's GUI-only workflow and the kind of composable, scriptable interface that CLI-native developers expect.

The tool speaks to Mail.app through AppleScript via `osascript`, the only sanctioned automation path that doesn't require private APIs or entitlements. Every write operation is designed around a **draft-first safety model**: compose, reply, and forward create drafts by default and never send unless the user explicitly passes `--dangerously-send` on every invocation. There is no config file, environment variable, or alias that can bypass this gate. This makes mailctl safe to wire into automated pipelines — the worst a runaway script can do is fill your Drafts folder.

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

**Description**: Core layer that executes AppleScript via `osascript` subprocess and parses results.

**Why it matters**: Every command depends on this. Performance and reliability of the entire CLI hinge on this layer doing its job well — batching operations to minimise `osascript` process startup overhead, parsing AppleScript's quirky output formats, and translating AppleScript errors into actionable CLI error messages.

**Key behaviours**:
- Execute AppleScript strings via `subprocess.run` calling `osascript -e`
- Support multi-statement scripts to batch operations in a single `osascript` call
- Parse AppleScript return values (lists, records, strings, dates) into Python types
- Detect and categorise errors: Mail.app not running, automation permission denied, account not found, mailbox not found, message not found
- Timeout handling for unresponsive Mail.app
- All subprocess calls go through a single function so tests can mock one seam

**Dependencies**: None (foundational layer).

### 3.2 Accounts List

**Description**: `mailctl accounts list` — enumerate all configured Mail.app accounts.

**Why it matters**: Users need to see which accounts are available before targeting commands at specific accounts. This is also the simplest smoke test that Mail.app integration works.

**Key behaviours**:
- List all accounts showing: name, email address(es), account type (IMAP, Exchange, iCloud, POP), enabled/disabled state
- Rich table output by default, `--json` for machine-readable
- Graceful error if Mail.app isn't running or not scriptable
- Exit code 0 on success, non-zero on failure

**Dependencies**: AppleScript Engine.

### 3.3 Mailboxes List

**Description**: `mailctl mailboxes list` — list mailboxes/folders per account with unread counts.

**Why it matters**: Users need to discover folder structure before listing messages. Unread counts give an at-a-glance triage view.

**Key behaviours**:
- List all mailboxes for a given account, or all accounts if no account specified
- Show: mailbox name, full path (for nested folders), unread count, total message count
- `--account` filter to scope to one account
- Batch AppleScript to fetch all mailbox data in one `osascript` call per account
- Rich table and `--json` output

**Dependencies**: AppleScript Engine, Accounts List (for account resolution).

### 3.4 Message Listing

**Description**: `mailctl messages list` — list messages in a mailbox with filtering.

**Why it matters**: The core read operation. Users need to scan their inbox, find specific messages, and triage efficiently from the terminal.

**Key behaviours**:
- Required: `--mailbox` (or default to INBOX) and optionally `--account`
- Filters: `--unread` (unread only), `--from <sender>`, `--subject <text>`, `--since <date>`, `--before <date>`
- `--limit` to cap results (default: 25)
- Display: date, from, subject, read/unread status, flagged status, message ID
- Sort by date descending (newest first)
- Batch fetch message metadata in a single AppleScript call
- Rich table and `--json` output

**Dependencies**: AppleScript Engine, Mailboxes List (for mailbox resolution).

### 3.5 Message Show

**Description**: `mailctl messages show <message-id>` — display a single message in full.

**Why it matters**: After scanning a list, users need to read the actual content of a message including headers, body, and attachment info.

**Key behaviours**:
- Show full message: date, from, to, cc, bcc, subject, body (plain text preferred, HTML stripped if necessary)
- Attachments section listing: filename, size, MIME type
- Message ID for use in reply/forward/update commands
- `--headers` flag to show all headers
- `--raw` flag to show unprocessed body
- Rich formatted output by default, `--json` for structured data

**Dependencies**: AppleScript Engine, Message Listing (for message ID scheme).

### 3.6 Cross-Account Search

**Description**: `mailctl messages search <query>` — search messages across all accounts.

**Why it matters**: Users often don't know which account or mailbox contains the message they're looking for. Cross-account search eliminates the need to manually check each one.

**Key behaviours**:
- Search by: `--from`, `--subject`, `--body` (content search), `--since`, `--before`
- `--account` to scope to a single account
- `--mailbox` to scope to a single mailbox
- `--limit` to cap results (default: 25)
- Results show account + mailbox context alongside message metadata
- Batch search operations where possible
- Rich table and `--json` output

**Dependencies**: AppleScript Engine, Message Listing.

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

### 3.9 Draft Editing

**Description**: `mailctl drafts edit <message-id>` — modify an existing draft.

**Why it matters**: Since compose creates drafts by default, users need a way to iterate on a draft before sending — update recipients, fix the subject, add attachments.

**Key behaviours**:
- `--subject <text>`: replace subject
- `--body <text>` / `--body-file <path>` / stdin: replace body
- `--to`, `--cc`, `--bcc`: replace recipient lists (use `--add-to`, `--remove-to` for incremental changes)
- `--attach <path>`: add attachment, `--remove-attach <filename>`: remove attachment
- `--dry-run`: show what would change without modifying
- Output: confirmation of changes made

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

**Why it matters**: When things don't work, users need clear guidance on what's wrong and how to fix it. AppleScript automation permissions are notoriously confusing on modern macOS.

**Key behaviours**:
- Check: Mail.app is installed
- Check: Mail.app is running (and offer to launch if not)
- Check: Terminal/iTerm has automation permission for Mail.app
- Check: at least one account is configured
- Check: `osascript` is available and functional
- Each check shows pass/fail with actionable fix instructions on failure
- Exit code 0 if all checks pass, non-zero if any fail
- `--json` output for programmatic health checks

**Dependencies**: AppleScript Engine.

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

**Description**: Comprehensive test suite with mocked AppleScript layer.

**Why it matters**: Tests that require Mail.app can't run in CI. A clean mock boundary at the `osascript` subprocess layer lets the entire CLI be tested without Mail.app.

**Key behaviours**:
- pytest as the test framework
- Mock fixture that intercepts all `osascript` subprocess calls and returns canned AppleScript output
- Unit tests for every command covering success, error, and edge cases
- Unit tests for the safety model: verify compose/reply/forward never send without `--dangerously-send`
- Separate integration test directory (`tests/integration/`) with `@pytest.mark.integration` marker
- `pytest.ini` / `pyproject.toml` config to skip integration tests by default
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
- **AppleScript execution**: `subprocess.run` calling `osascript`
- **Packaging**: `pyproject.toml` with `[project.scripts]` entry point
- **Installation**: `pipx install .`
- **Testing**: pytest with subprocess mocking

### Data Flow
```
User CLI input
  → Typer command parser
    → Command handler (validates args, applies defaults)
      → AppleScript engine (builds script, executes via osascript, parses result)
        → Output formatter (Rich table or JSON)
          → stdout
```

### Key Architectural Decisions

1. **Single AppleScript execution seam**: All `osascript` calls go through one function. This is the mock boundary for tests and the place to add logging, timing, and error translation.

2. **Batch by default**: Each command builds the most efficient AppleScript possible — fetching all needed data in one `osascript` invocation rather than one per message/mailbox/account.

3. **Message ID scheme**: Mail.app's internal message IDs are used as-is. Commands that accept message IDs should accept the same IDs that list/show commands output.

4. **Safety as architecture, not policy**: The `--dangerously-send` flag is enforced in the compose/reply/forward command handlers. There is no configuration system, no environment variable reader, no "default flags" mechanism that could bypass it. The absence of these features IS the safety model.

5. **Output mode as a cross-cutting concern**: A shared output context (table vs JSON, colour vs plain) is set once at CLI entry and threaded through all commands. Commands produce data structures; the output layer decides how to render them.

### Entity Model
- **Account**: name, email addresses, type (IMAP/Exchange/iCloud/POP), enabled state
- **Mailbox**: name, full path, account reference, unread count, message count
- **Message**: ID, date, from, to, cc, bcc, subject, body, read state, flagged state, mailbox reference, attachments
- **Attachment**: name, size, MIME type (metadata only — mailctl doesn't download attachments in v1)

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
