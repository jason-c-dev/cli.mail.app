# Sprint 07 Generator Log — Message Updates (Mark, Flag, Move)

## Summary

Implemented `mailctl messages mark` and `mailctl messages move` as subcommands of the existing `messages` command group. Both commands follow the established build/perform/register architecture from prior sprints.

## Files Changed

- **`src/mailctl/commands/mark_move.py`** (new) — Full implementation of mark and move commands:
  - `build_mark_messages_script()` — Generates batched AppleScript to set read/flagged status
  - `build_move_messages_script()` — Generates batched AppleScript to move messages to target mailbox
  - `perform_mark()` / `perform_move()` — Orchestration layer calling engine
  - Human-readable and dry-run output helpers
  - Typer `register()` with full validation (no-flags, contradictory flags, missing --to)
  
- **`src/mailctl/cli.py`** — Added `register_mark_move` import and registration on `messages_app`

- **`tests/unit/test_mark_move.py`** (new) — 63 unit tests covering all 28 contract criteria

## Criteria Coverage

| Category | Criteria | Status |
|----------|----------|--------|
| mark-command | C-161, C-162, C-163, C-164 | PASS |
| mark-combined | C-165 | PASS |
| mark-validation | C-166, C-167 | PASS |
| bulk-operations | C-168, C-173 | PASS |
| mark-account | C-169 | PASS |
| move-command | C-170, C-171 | PASS |
| move-validation | C-172 | PASS |
| dry-run | C-174, C-175, C-176 | PASS |
| output | C-177, C-178 | PASS |
| error-handling | C-179, C-180 | PASS |
| cli-help | C-181, C-182, C-183 | PASS |
| architecture | C-184 | PASS |
| testing | C-185, C-186, C-187 | PASS |
| regression | C-188 | PASS |

## Test Results

- **63 new tests** for Sprint 7 (mark and move)
- **432 total tests passing** (all Sprint 1-6 tests unchanged)
- **0 failures, 0 errors**

## Design Decisions

1. **Separate module**: Created `mark_move.py` rather than extending `messages.py` — the existing file is read-only queries; mark/move are state-change operations with different validation patterns.

2. **Batched AppleScript**: Both mark and move accept multiple message IDs and process them in a single `osascript` call, iterating over mailboxes to find messages by ID.

3. **No send verb**: The generated AppleScript never contains a `send` verb — these are pure mailbox-management operations.

4. **Consistent error handling**: Uses `handle_mail_error()` from `mailctl.output` for AppleScript errors, matching all prior commands.

5. **Tri-state flags**: `--read`/`--unread` map to `read=True/False/None`; same for `--flagged`/`--unflagged`. Validation rejects contradictory combinations early.
