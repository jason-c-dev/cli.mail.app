# Sprint 09: Doctor Command & Error Polish — Generator Log

## Summary

Implemented the `mailctl doctor` diagnostic command and polished error messages across all commands. All 554 tests pass (503 Sprint 1–8 + 51 new).

## Implementation Details

### Doctor Command (C-220 through C-229, C-238, C-239)

Created `src/mailctl/commands/doctor.py` with 5 independently testable checks:

1. **osascript** — verifies osascript binary is available and functional via `osascript -e 'return "ok"'`
2. **mail_installed** — checks `/System/Applications/Mail.app` and `/Applications/Mail.app` exist
3. **mail_running** — queries System Events to check if Mail.app process is active
4. **scriptable** — runs a trivial AppleScript against Mail.app to test automation permission
5. **accounts** — counts configured email accounts via AppleScript

Architecture:
- Each check is an independent function returning a `CheckResult(name, status, message)` dataclass
- `run_all_checks()` runs all checks without short-circuiting on failure
- Registered as a top-level command via the standard `register(app)` pattern in `cli.py`
- Supports `--json` output with `{all_passed: bool, checks: [{name, status, message}]}`
- Supports `--no-color` via the global flag (Rich Console with `no_color=True`)
- Exit code 0 when all pass, 1 when any fail
- Uses checkmark (✔) and cross (✘) unicode indicators for pass/fail

### Error Message Polish (C-230 through C-237)

**C-230 — Error format consistency:**
- Updated `ScriptTimeoutError` message to include remedy: "try restarting Mail.app or check whether it is stuck on a dialog"
- All three typed errors now follow "problem. solution." format:
  - `MailNotRunningError`: "Mail.app is not running. Launch Mail.app and try again."
  - `PermissionDeniedError`: "Automation permission denied. Grant access in System Settings > Privacy & Security > Automation."
  - `ScriptTimeoutError`: "AppleScript timed out after Xs. Mail.app may be unresponsive — try restarting Mail.app..."

**C-231 — Consistent error rendering:**
- Fixed `mailboxes.py` account-not-found to use `EXIT_USAGE_ERROR` (2) instead of hardcoded `1`
- Verified all commands use `render_error` for user errors and `handle_mail_error` for runtime errors
- No ad-hoc `err_console.print` or `typer.echo` in command files

**C-232 — Account not found:**
- `messages list` catches AppleScript errors mentioning "account" and fetches known accounts for the error message
- `mailboxes list` already had this pattern (with exit code now fixed)
- `compose --from` already had this pattern from Sprint 5

**C-233 — Mailbox not found:**
- `messages list` catches mailbox-related AppleScript errors and suggests `mailctl mailboxes list`
- `messages move --to` catches mailbox-related errors and suggests `mailctl mailboxes list`

**C-234 — Message not found:**
- Added message-not-found error handling to: `messages show`, `messages mark`, `messages delete`, `reply`, `forward`, `drafts edit`
- Each mentions the message ID and suggests verifying with `mailctl messages list`

**C-235 — Empty results messaging:**
- `messages list` shows "No messages found." instead of an empty table
- `messages search` shows "No messages matched your search." instead of an empty table
- JSON mode returns `[]` for empty results

**C-236 — Empty stdin body:**
- Already implemented in Sprint 5 compose: "No body supplied. Pass --body <text>, --body-file <path>, or pipe body text on stdin."

**C-237 — Attachment not found:**
- Already implemented in Sprint 5/6: "Attachment '<path>' does not exist or is not a file."

### Tests (C-240, C-241)

**test_doctor.py — 36 test cases:**
- `TestDoctorCommandExists` (3): top-level command, exit 0, 5+ checks
- `TestDoctorCheckOsascript` (3): pass, fail (FileNotFoundError), fail (bad returncode)
- `TestDoctorCheckMailInstalled` (2): pass, fail
- `TestDoctorCheckMailRunning` (2): pass, fail
- `TestDoctorCheckScriptable` (2): pass, fail (permission denied)
- `TestDoctorCheckAccounts` (2): pass, fail (zero accounts)
- `TestDoctorOutputFormat` (3): all-pass indicators, no short-circuit, mixed pass/fail
- `TestDoctorExitCodes` (3): exit 0 all pass, exit 1 single fail, exit 1 multiple fails
- `TestDoctorJSON` (3): all-pass JSON, failure JSON, check structure
- `TestDoctorHelp` (2): diagnosis mention, --json listed
- `TestDoctorNoColor` (2): no ANSI codes, still readable
- `TestDoctorArchitecture` (4): module exists, register callable, checks mockable, top-level
- `TestDoctorComprehensive` (5): multiple failures, JSON booleans, run_all_checks, account count

**test_error_polish.py — 15 test cases:**
- `TestErrorFormatConsistency` (3): MailNotRunning, PermissionDenied, ScriptTimeout messages
- `TestAccountNotFound` (3): messages list, mailboxes list, compose --from
- `TestMailboxNotFound` (2): messages list, messages move
- `TestMessageNotFound` (3): messages show, mark, delete
- `TestEmptyResults` (2): messages list empty, search empty
- `TestEmptyStdinBody` (1): compose no body
- `TestAttachmentNotFound` (1): compose bad --attach

## Files Changed

- `src/mailctl/commands/doctor.py` — **NEW** (280 lines)
- `src/mailctl/cli.py` — register doctor command
- `src/mailctl/errors.py` — ScriptTimeoutError message polish
- `src/mailctl/commands/messages.py` — account/mailbox not found, empty results, message not found
- `src/mailctl/commands/mailboxes.py` — exit code fix (1 → EXIT_USAGE_ERROR)
- `src/mailctl/commands/mark_move.py` — message/mailbox not found
- `src/mailctl/commands/delete.py` — message not found
- `src/mailctl/commands/reply_forward.py` — message not found
- `src/mailctl/commands/drafts.py` — message not found
- `tests/unit/test_doctor.py` — **NEW** (36 tests)
- `tests/unit/test_error_polish.py` — **NEW** (15 tests)

## Test Results

```
554 passed in 0.51s
```

- Sprint 1–8 tests: 503 passed (zero regressions)
- Sprint 9 tests: 51 passed (36 doctor + 15 error polish)

## Commits

1. `harness(sprint-09): add doctor command and fix ScriptTimeoutError message [C-220,C-230]`
2. `harness(sprint-09): polish error messages across all commands [C-230-C-237]`
3. `harness(sprint-09): add comprehensive tests for doctor and error polish [C-240,C-241]`
4. `harness(sprint-09): fix mailboxes account-not-found exit code to EXIT_USAGE_ERROR [C-231]`
