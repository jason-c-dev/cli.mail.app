# Sprint 03: Message Listing & Show — Generator Log

## Attempt: 1
## Date: 2026-04-15

## Summary

Implemented `mailctl messages list` and `mailctl messages show` commands with comprehensive filtering, output options, and error handling. Followed the established 4-layer architecture pattern from Sprint 2 (accounts/mailboxes).

## Files Changed

- **`src/mailctl/commands/messages.py`** (new) — Messages command module with:
  - `build_messages_list_script()` — AppleScript generation for listing messages
  - `parse_messages_list_output()` — Parses `||`-delimited osascript output
  - `_apply_filters()` — Post-fetch filtering (unread, from, subject, date range)
  - `_sort_by_date_descending()` — Sorts messages newest-first
  - `fetch_messages()` — Orchestrates script + engine + parse + filter + sort + limit
  - `build_message_show_script()` — AppleScript for single message with body/headers/attachments
  - `parse_message_show_output()` — Parses structured show output with `@@BODY@@`/`@@HEADERS@@`/`@@ATTACHMENTS@@` delimiters
  - `fetch_message()` — Orchestrates single-message fetch
  - `register()` — Typer command registration for `list` and `show`

- **`src/mailctl/cli.py`** (modified) — Added `register_messages(messages_app)` import and call

- **`tests/unit/test_messages.py`** (new) — 81 unit tests covering:
  - List: success, all 6 table columns, default INBOX, --mailbox, --account, --unread, --from, --subject, --since, --before, --limit, --json, sorting, batch call, combined filters
  - Show: full display, attachments metadata, --headers, --raw, --json, positional arg, no-ID error
  - Error handling: message not found, Mail.app not running, stdout/stderr separation
  - Help text: subcommand listing, option descriptions
  - Parsing: direct unit tests for both parsers and both script builders

## Criteria Coverage

| ID | Category | Status | Notes |
|----|----------|--------|-------|
| C-051 | messages-list | PASS | Command runs, exit 0, data visible |
| C-052 | messages-list | PASS | Table has date, from, subject, read, flagged, ID columns |
| C-053 | messages-list | PASS | Default INBOX, --mailbox overrides AppleScript target |
| C-054 | messages-list | PASS | --account scopes to specific account in AppleScript |
| C-055 | messages-list | PASS | --unread filters to read=false messages only |
| C-056 | messages-list | PASS | --from case-insensitive substring filter |
| C-057 | messages-list | PASS | --subject case-insensitive substring filter |
| C-058 | messages-list | PASS | --since/--before date range filtering works |
| C-059 | messages-list | PASS | --limit caps output, default 25 |
| C-060 | messages-list | PASS | Sorted newest-first by date |
| C-061 | messages-list | PASS | --json valid array with all 6 fields per message |
| C-062 | messages-list | PASS | Exactly 1 osascript call for list |
| C-063 | messages-show | PASS | Show displays all core fields |
| C-064 | messages-show | PASS | Attachment name, size, MIME type visible |
| C-065 | messages-show | PASS | --headers shows distinct headers section |
| C-066 | messages-show | PASS | --raw outputs unprocessed body |
| C-067 | messages-show | PASS | --json with all fields including attachments array |
| C-068 | messages-show | PASS | Positional arg, no-ID gives usage error |
| C-069 | error-handling | PASS | Nonexistent ID → non-zero exit + error message |
| C-070 | error-handling | PASS | Mail.app not running → non-zero exit + error |
| C-071 | error-handling | PASS | stdout for data, stderr for errors |
| C-072 | testing | PASS | 50+ list tests, all passing |
| C-073 | testing | PASS | 28+ show tests, all passing |
| C-074 | testing | PASS | Full suite 174 tests, 0 failures |
| C-075 | code-quality | PASS | Separate module, clean layering, shared render_output/handle_mail_error |
| C-076 | regression | PASS | --version, --help, accounts list, mailboxes list all work |
| C-077 | messages-list | PASS | Combined filters applied conjunctively |
| C-078 | cli-help | PASS | Help text lists subcommands and all options |

## Design Decisions

1. **Post-fetch filtering** — Filters (unread, from, subject, date) are applied in Python after fetching all messages from AppleScript, keeping the AppleScript simple and maintaining the single-call batch pattern (C-062).

2. **Date parsing** — Multiple format patterns attempted for AppleScript's locale-dependent date strings. Unparseable dates are included by default rather than silently dropped.

3. **Structured show output** — Used `@@BODY@@`, `@@HEADERS@@`, `@@ATTACHMENTS@@` delimiters in the AppleScript output to separate sections cleanly, avoiding ambiguity with `||` delimiters in body text.

4. **Rich table truncation** — Tests that verify table column presence use JSON mode to avoid Rich's ellipsis truncation in narrow terminal contexts.

## Test Results

```
174 passed in 0.19s
```
