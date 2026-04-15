# Sprint 02: Accounts & Mailboxes — Generator Log

## Summary

Implemented the first two real commands (`accounts list`, `mailboxes list`) and the shared output system. All 25 contract criteria (C-026..C-050) are addressed.

## What was built

### New files
- **`src/mailctl/output.py`** — Shared output module. `render_output()` renders data as either a Rich table or JSON. `handle_mail_error()` provides consistent error rendering to stderr. `ColumnDef` dataclass defines table columns with headers, alignment, max_width, and ellipsis overflow.
- **`src/mailctl/commands/__init__.py`** — Commands package.
- **`src/mailctl/commands/accounts.py`** — `accounts list` command. Generates a single AppleScript that queries `every account` for name, email addresses, account type, and enabled state. Uses `||`-delimited output format for reliable parsing. `register()` function wires the Typer command.
- **`src/mailctl/commands/mailboxes.py`** — `mailboxes list` command. Single AppleScript fetches all mailboxes across all accounts (name, unread count, message count). `--account` filter applied in Python after fetch. Unknown account produces a named error.
- **`tests/unit/test_accounts.py`** — 28 tests covering: multi-account success, single account, all four table columns, AppleScript property queries, JSON output (valid JSON, all fields, correct values, global flag), error handling (Mail not running), batch call assertion, stdout/stderr separation, parsing, and script generation.
- **`tests/unit/test_mailboxes.py`** — 30 tests covering: success, all-accounts-with-attribution, account filter (includes/excludes), JSON output (valid, fields, values, global flag), errors (nonexistent account, Mail not running), batch call assertion, stream separation, parsing, and script generation.

### Modified files
- **`src/mailctl/cli.py`** — Added `--no-color` global option. Added `ctx.ensure_object(dict)` to main callback for state propagation. Wired `register_accounts()` and `register_mailboxes()` to sub-apps. `--json` stored in `ctx.obj` for global access.

## Design decisions

1. **Single osascript call for both commands** — The mailboxes command fetches all mailboxes across all accounts in one call, filtering in Python. This exceeds the contract threshold (at most one per account) while simplifying the mock setup.
2. **Click CliRunner for tests** — Used Click's native `CliRunner` (via `typer.main.get_command()`) instead of Typer's wrapper. Click 8.x separates stdout/stderr by default, enabling proper C-043 testing.
3. **Dual --json placement** — Defined `--json` on the main callback (global) AND on each command. This lets `mailctl --json accounts list` and `mailctl accounts list --json` both work.
4. **`||`-delimited AppleScript output** — Chose double-pipe delimiters instead of AppleScript's native list format for simpler, more reliable parsing.

## Test results

```
93 passed in 0.10s
```

- Sprint 1 tests: 35 passed (regression check)
- Sprint 2 tests: 58 passed (28 accounts + 30 mailboxes)

## Criteria coverage

| ID | Category | Status |
|----|----------|--------|
| C-026 | accounts-command | PASS — exits 0, both accounts visible |
| C-027 | accounts-command | PASS — script queries all four properties |
| C-028 | accounts-command | PASS — table shows name, email, type, enabled |
| C-029 | accounts-command | PASS — valid JSON with all fields |
| C-030 | accounts-command | PASS — graceful error, non-zero exit |
| C-031 | accounts-command | PASS — exactly one osascript call |
| C-032 | mailboxes-command | PASS — exits 0, mailbox data visible |
| C-033 | mailboxes-command | PASS — table shows name, unread, messages |
| C-034 | mailboxes-command | PASS — --account filters correctly |
| C-035 | mailboxes-command | PASS — all accounts shown with attribution |
| C-036 | mailboxes-command | PASS — valid JSON with all fields |
| C-037 | mailboxes-command | PASS — one osascript call total |
| C-038 | mailboxes-command | PASS — unknown account error with name |
| C-039 | output-system | PASS — shared output.py used by both |
| C-040 | output-system | PASS — global --json on callback + per-command |
| C-041 | output-system | PASS — --no-color flag, Rich TTY auto-detection |
| C-042 | output-system | PASS — headers, alignment, ellipsis overflow |
| C-043 | output-system | PASS — stdout for data, stderr for errors |
| C-044 | output-system | PASS — exit 0 success, non-zero failure |
| C-045 | testing | PASS — 28 accounts tests, all passing |
| C-046 | testing | PASS — 30 mailboxes tests, all passing |
| C-047 | testing | PASS — 93 total, 0 failures |
| C-048 | cli-help | PASS — all help outputs informative |
| C-049 | code-quality | PASS — separate modules, clean layers |
| C-050 | regression | PASS — --version, --help, subcommands all work |
