# Sprint 06: Reply & Forward — Generator Log

## Summary

Implemented `mailctl reply` and `mailctl forward` commands with the same
ironclad draft-first safety model as `compose`. Both are top-level commands
registered on the root Typer app.

## Architecture

### New files

- **`src/mailctl/commands/reply_forward.py`** — Complete reply & forward
  implementation following the build/perform/register pattern from compose.
- **`tests/unit/test_reply_forward.py`** — 81 unit tests covering all 27
  contract criteria.

### Modified files

- **`src/mailctl/cli.py`** — Added `register_reply_forward(app)` import and call.

### Code reuse

Shared utilities imported from `compose.py` (no duplication):
- `_escape_applescript_string()` — AppleScript string escaping
- `resolve_body()` — Body source resolution (--body, --body-file, stdin)

Shared utilities from the output module:
- `render_error()` — Error rendering to stderr
- `handle_mail_error()` — AppleScript error handling with exit codes

### Key design decisions

1. **Reply uses Mail.app's `reply` verb** — preserves threading by letting
   Mail.app handle In-Reply-To headers.
2. **Forward uses Mail.app's `forward` verb** — similarly delegates to
   Mail.app for header management.
3. **User email detection for reply-all** — fetches user's email addresses
   from Mail.app accounts to exclude the user's own address from reply-all
   recipient lists (C-133).
4. **Original content quoting** — new body text is prepended to a quoted
   attribution section: "On {date}, {sender} wrote:" followed by "> " prefixed
   original lines (C-135).
5. **Subject prefixing** — Reply adds "Re: " prefix (unless already present),
   forward adds "Fwd: " prefix.

## Safety model

Identical to compose — enforced in code, not by policy:

- `--dangerously-send` is the **only** way to produce the `send` verb
- No `envvar=` parameter on the Typer option (C-144)
- Default-to-No confirmation prompt when sending
- `--yes` without `--dangerously-send` is a no-op
- `--dry-run` prints summary without executing compose/send AppleScript
- Read-only original message fetch IS allowed in dry-run mode

## Criteria coverage

| ID | Category | Status | Notes |
|----|----------|--------|-------|
| C-131 | reply-command | PASS | Exit 0, 2+ osascript calls |
| C-132 | reply-recipients | PASS | Sender only (no To/Cc) |
| C-133 | reply-all | PASS | Sender + To + Cc, user excluded |
| C-134 | forward-command | PASS | --to required, repeatable |
| C-135 | original-content | PASS | Attribution + quoted body |
| C-136 | body-sources | PASS | --body, --body-file, stdin, conflict detection |
| C-137 | attachments | PASS | Repeatable, validated, in AppleScript |
| C-138 | safety-model | PASS | Reply draft — no send verb |
| C-139 | safety-model | PASS | Forward draft — no send verb |
| C-140 | safety-model | PASS | Reply send verb via mock |
| C-141 | safety-model | PASS | Forward send verb via mock |
| C-142 | safety-model | PASS | Confirmation decline aborts |
| C-143 | safety-model | PASS | --yes alone never sends |
| C-144 | safety-model | PASS | No envvar/config bypass (code inspection) |
| C-145 | dry-run | PASS | Reply dry-run, no compose script |
| C-146 | dry-run | PASS | Forward dry-run, no compose script |
| C-147 | threading | PASS | Reply references original message id |
| C-148 | output | PASS | Human-readable draft/sent distinction |
| C-149 | output | PASS | JSON with action/to/subject/id |
| C-150 | error-handling | PASS | Message not found error |
| C-151 | error-handling | PASS | Mail.app not running error |
| C-152 | cli-help | PASS | reply + forward in top-level help |
| C-153 | cli-help | PASS | All options listed, send warning |
| C-154 | testing | PASS | 8 reply + 8 forward safety tests |
| C-155 | testing | PASS | 369 tests, 0 failures |
| C-156 | code-quality | PASS | build_*/perform_*/register pattern |
| C-157 | regression | PASS | All Sprint 1-5 tests pass |

## Test results

```
369 passed in 0.33s
```

81 new tests in `tests/unit/test_reply_forward.py` + 288 existing tests.
Zero failures, zero errors.
