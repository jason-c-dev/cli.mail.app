# Sprint 08 Generator Log — Draft Editing & Delete

## Summary

Implemented two new features following the established build/perform/register architecture:

1. **`mailctl drafts edit`** — edit existing draft messages (subject, body, recipients, attachments)
2. **`mailctl messages delete`** — delete messages (move to Trash by default, permanent with confirmation)

## Files Created

- `src/mailctl/commands/drafts.py` — Draft editing command module (311 lines)
- `src/mailctl/commands/delete.py` — Delete command module (225 lines)
- `tests/unit/test_drafts_edit.py` — 35 test cases for draft editing
- `tests/unit/test_delete.py` — 36 test cases for deletion

## Files Modified

- `src/mailctl/cli.py` — Added `drafts_app` Typer group, registered `drafts` and `delete` commands

## Architecture

Both commands follow the established pattern:
- `build_*_script()` — generates AppleScript
- `perform_*()` — orchestrates via `run_applescript()`, returns result dict
- `register()` — thin Typer handler with validation, dry-run, error handling

Reuses from prior sprints:
- `_escape_applescript_string()` for safe string escaping
- `handle_mail_error()` / `render_error()` from `mailctl.output`
- `run_applescript()` from `mailctl.engine`
- `mock_osascript` fixture from `conftest.py`

## Safety

- **Draft edit** has NO send path — no `--dangerously-send`, no `send` verb in generated AppleScript
- **Delete** moves to Trash by default (safe, reversible)
- **Permanent delete** requires `--permanent` flag AND interactive confirmation (default N)
- `--yes` skips confirmation only when combined with `--permanent`; alone it does nothing

## Test Results

- 71 new tests (35 drafts edit + 36 delete)
- 503 total tests passing (432 existing + 71 new)
- Zero failures, zero regressions

## Criteria Coverage

| ID | Category | Status |
|----|----------|--------|
| C-189 | drafts-edit-command | ✅ |
| C-190 | drafts-edit-subject | ✅ |
| C-191 | drafts-edit-body | ✅ |
| C-192 | drafts-edit-body-file | ✅ |
| C-193 | drafts-edit-body-conflict | ✅ |
| C-194 | drafts-edit-to | ✅ |
| C-195 | drafts-edit-cc-bcc | ✅ |
| C-196 | drafts-edit-add-remove-to | ✅ |
| C-197 | drafts-edit-to-add-to-conflict | ✅ |
| C-198 | drafts-edit-attach | ✅ |
| C-199 | drafts-edit-remove-attach | ✅ |
| C-200 | drafts-edit-combined | ✅ |
| C-201 | drafts-edit-no-options | ✅ |
| C-202 | drafts-edit-dry-run | ✅ |
| C-203 | drafts-edit-json | ✅ |
| C-204 | delete-command | ✅ |
| C-205 | delete-permanent | ✅ |
| C-206 | delete-permanent-yes | ✅ |
| C-207 | delete-yes-alone | ✅ |
| C-208 | delete-bulk | ✅ |
| C-209 | delete-account | ✅ |
| C-210 | delete-dry-run | ✅ |
| C-211 | delete-output | ✅ |
| C-212 | error-handling | ✅ |
| C-213 | cli-help-drafts | ✅ |
| C-214 | cli-help-delete | ✅ |
| C-215 | architecture | ✅ |
| C-216 | testing-drafts | ✅ |
| C-217 | testing-delete | ✅ |
| C-218 | full-test-suite | ✅ |
| C-219 | regression | ✅ |

## Commits

1. `159924d` — Add drafts edit and messages delete commands [C-189, C-204]
2. `3c328b8` — Add comprehensive tests for drafts edit and delete [C-216, C-217]
