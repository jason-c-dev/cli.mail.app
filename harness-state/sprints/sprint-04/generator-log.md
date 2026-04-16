# Sprint 04: Cross-Account Search — Generator Log

## Summary

Implemented `mailctl messages search` command that searches messages across all
Mail.app accounts with flexible filtering, scoping, and output options.

## Changes Made

### `src/mailctl/commands/messages.py`
- Added `build_account_names_script()` — generates AppleScript to list account names
- Added `parse_account_names_output()` — parses account names from raw output
- Added `build_search_script()` — generates AppleScript to search messages across
  mailboxes of one account, with optional body content inclusion and mailbox scoping
- Added `parse_search_output()` — parses `||`-delimited search output into dicts
  with account, mailbox, id, date, from, subject, read, flagged fields
- Added `_apply_search_filters()` — post-fetch filtering by from, subject, body,
  since, before (all conjunctive AND logic, case-insensitive substring matching)
- Added `fetch_search_results()` — orchestrates cross-account search: lists accounts
  (1 call), searches each account (1 call each), merges/filters/sorts/limits
- Added `SEARCH_COLUMNS` — Rich table column definitions for search output
- Registered `messages search` command with all options: --from, --subject, --body,
  --since, --before, --account, --mailbox, --limit, --json
- Requires at least one search filter; exits with usage error if none given

### `tests/conftest.py`
- Extended `OsascriptMock` with `set_outputs()` method for sequential outputs,
  enabling multi-call scenarios (account list + per-account search calls)
- Modified `__call__` to support output sequences while maintaining backward
  compatibility with `set_output()` for single-call tests

### `tests/unit/test_search.py` (new)
- 55 test cases covering all 25 criteria (C-081 through C-105):
  - Command existence and basic execution (C-081)
  - No-filter usage error (C-082)
  - --from filter with case-insensitivity (C-083)
  - --subject filter with case-insensitivity (C-084)
  - --body filter with case-insensitivity (C-085)
  - --since / --before date range filters (C-086)
  - Combined filters AND logic (C-087)
  - Cross-account search with account field (C-088)
  - --account scoping (C-089)
  - --mailbox scoping (C-090)
  - Mailbox field in results (C-091)
  - --limit and default of 25 (C-092)
  - Rich table output (C-093)
  - --json output with required fields (C-094)
  - Date-descending sort interleaved across accounts (C-095)
  - Batch call efficiency (N+1 calls for N accounts) (C-096)
  - Empty results handling (C-097)
  - Mail.app not running error (C-098)
  - stdout/stderr separation (C-099)
  - Help text (C-100, C-101)
  - Architecture pattern verification (C-104)

## Architecture Decisions

1. **Body filtering**: Body content is only fetched via AppleScript when `--body`
   is specified. AppleScript newlines are replaced with `@@NL@@` markers to keep
   one-line-per-message format. Body is stored as `_body` (internal field) and
   stripped before returning results.

2. **Batch efficiency**: One `osascript` call lists account names, then one call
   per account fetches all messages across that account's mailboxes. With
   `--account`, the account-list call is skipped entirely (1 call total).

3. **Reuse of existing infrastructure**: Search reuses `_sort_by_date_descending()`,
   `_is_on_or_after()`, `_is_before()`, `_parse_date()` from the existing messages
   module. Follows the same build/parse/fetch/render architecture pattern.

## Test Results

```
229 passed in 0.21s (174 existing + 55 new)
```

No regressions in Sprint 1, 2, or 3 tests.
