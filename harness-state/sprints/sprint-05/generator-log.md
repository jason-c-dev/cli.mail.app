# Sprint 5 — Compose & Safety Model — Generator Log

## What was built

- **`src/mailctl/commands/compose.py`** — the compose command and its
  supporting helpers (`build_compose_script`, `build_account_names_script`,
  `parse_account_names_output`, `resolve_body`, `fetch_account_names`,
  `perform_compose`, `_dry_run_summary`, `_prompt_confirmation`,
  `_render_human_output`). The module follows the same
  build/parse/fetch/perform/register pattern used in the prior sprints.
- **`src/mailctl/cli.py`** — registers the compose command at the top
  level (sibling to the `accounts` / `mailboxes` / `messages` groups).
- **`tests/unit/test_compose.py`** — 46 tests covering required args,
  recipient repetition, all three body sources, attachments, `--from`
  account selection and validation, default-draft behaviour,
  `--dangerously-send` via mocked osascript, confirmation accept and
  decline, `--yes`, `--dry-run`, JSON output, Mail.app-down error
  handling, stdout/stderr separation, help-text content, and direct
  unit tests for `build_compose_script` and `parse_account_names_output`.
- **`tests/unit/test_safety_model.py`** — 13 tests comprising a
  code-inspection suite (C-115) and a parametrised bypass-attempt
  matrix (C-120). The parametrised test covers seven scenarios
  (bare, yes-only, dry-run, all-recipient-types, attach, stdin-body,
  from-account) and is paired with a complementary test asserting that
  `--dangerously-send --yes` DOES produce a `send` verb in the
  generated script.

## Design decisions

### Safety model enforcement
The `--dangerously-send` flag is the *only* path that reaches the
`include_send=True` branch of `build_compose_script`. The Typer option
deliberately omits any `envvar=` parameter. There is no config-file
reader, no alias mechanism, and no environment-variable fallback
anywhere in the command. This is tested structurally via the
code-inspection suite in `test_safety_model.py` — the absence of those
features is the safety model, and the tests lock that absence in place.

### Confirmation prompt defaults to No
`_prompt_confirmation` calls `readline()` and only returns `True` when
the stripped-lower answer is exactly `y` or `yes`. Empty input,
whitespace, EOF, and any other text all return `False`. `--yes` skips
the prompt entirely, but only matters when `--dangerously-send` is also
present (otherwise the prompt is never shown in the first place).

### Body sourcing priority
`resolve_body` accepts at most one of `--body` or `--body-file`, falls
back to stdin when neither is given and stdin is not a TTY, and raises a
`typer.BadParameter` otherwise. This produces clean usage errors
(exit code 2) that go to stderr via the shared `render_error` helper.

### Account validation
When `--from` is supplied, the command fetches account names via
`build_account_names_script` (read-only) before attempting the compose.
Unknown account names produce an actionable error listing the valid
accounts — matching the pattern used by `mailboxes list --account`.
Account validation is skipped entirely in dry-run mode, so `--dry-run`
invokes zero osascript calls in the typical case.

### AppleScript escaping
`_escape_applescript_string` handles both single-line and multi-line
values, emitting `"line1" & return & "line2"` for multi-line bodies so
newlines survive the round-trip through osascript. Backslashes and
double-quotes are escaped to keep the AppleScript syntactically valid.

### Architecture conformance
The compose module mirrors the shape of `accounts.py`, `mailboxes.py`,
and `messages.py`: small pure functions for script generation and
parsing, a `perform_compose` orchestrator that delegates to the engine,
and a thin Typer handler that uses `render_error` / `handle_mail_error`
for consistent error handling. Human output uses `sys.stdout.write`
directly (no Rich table involvement) because the result is a single
confirmation line; JSON output uses `json.dumps` matching the pattern
in `messages show`.

## Test counts

| Module                        | Tests |
| ----------------------------- | ----- |
| `tests/unit/test_compose.py`  | 46    |
| `tests/unit/test_safety_model.py` | 13 |
| **Sprint 5 total**            | **59** |
| Total suite (after sprint 5)  | **288** |

All tests pass (`pytest` exit 0, 288 passed).

## Safety confirmation

No test in this sprint invokes the real `osascript` binary or causes a
real email to be delivered. Every send-path assertion is string
inspection of the AppleScript that *would* have been passed to
`osascript`, using the existing `mock_osascript` fixture that patches
`mailctl.engine.subprocess.run`. The `tests/integration/` directory is
not used by this sprint.

## Notes for the evaluator

- To verify the compose command end-to-end against real Mail.app, the
  safe operation is `mailctl compose --to <your-own-address>
  --subject Test --body Test` (no `--dangerously-send`). This will
  create a draft in your Drafts folder that you can visually confirm
  and then delete. Do **not** pass `--dangerously-send` against real
  Mail.app — that would send a real email and violate the guardrails
  in `CLAUDE.md`.
- `--dry-run` is always safe and never touches Mail.app even when
  combined with `--dangerously-send --yes`.
- The code-inspection tests in `test_safety_model.py` walk the entire
  `src/` tree with a regex, so adding an env-var bypass in a future
  sprint would break those tests — the safety model has a tripwire.
