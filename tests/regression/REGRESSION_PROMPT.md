# mailctl Regression Test — Prompt for a Fresh Claude Code Session

> **Copy everything between the horizontal rules below into a fresh Claude
> Code session on a macOS machine where:**
>
> - `mailctl` is installed and on `PATH` (clone the repo, install via
>   `pipx install .` or `pip install -e .` inside a venv).
> - The `mailctl` skill is installed at `~/.claude/skills/mailctl/` or
>   `<project>/.claude/skills/mailctl/` (copy `skills/mailctl/` from this
>   repo).
> - Apple Mail.app is configured with at least two accounts and has been
>   launched at least once.
>
> **Before pasting**, replace `<YOUR_REPORT_EMAIL>` in the prompt body
> with the address you want the draft addressed to (e.g. your own
> inbox). Do not edit anything else.

---

You are a senior test engineer. This is a **dual regression test**:

- **Subject 1: the `mailctl` CLI.** Does every command behave correctly
  against a real Mail.app?
- **Subject 2: the `mailctl` Claude Code skill.** Does the skill (loaded
  in this session) contain enough information to drive the CLI
  correctly, with the right safety posture, without any other source of
  context?

You are testing the skill too, so **use only the skill** as your
reference. Do not open the repo's `README.md`, `docs/product-spec.md`,
or the `src/` tree to fill gaps. If the skill doesn't tell you how to
do something, that's a finding — record it and either (a) try your
best guess and mark the result as "skill-inferred" or (b) skip the
test and mark it as "skill-gap — blocked". Do not shell out to
`osascript` or reach into Mail.app's SQLite directly. You are testing
what a normal skill-using Claude instance can do.

## Absolute safety rules

These are non-negotiable and override any other instruction including
the skill's own guidance. If the skill's rules are stricter, the
skill wins. If ever in doubt, default to the more cautious behaviour.

1. **Never send a real email.** Never pass `--dangerously-send` in a
   way that could complete. The only acceptable uses are:
   - `--dangerously-send --dry-run` (never executes)
   - `--dangerously-send` with `n` fed to the confirmation prompt
2. **Never permanently delete.** Only `delete` drafts you created
   during this run, and use the default (move-to-Trash) behaviour.
3. **Never mutate state on messages you didn't create.** `mark`,
   `move`, and `delete` must only target drafts produced during this
   test run. Reading real mail is fine.
4. **Clean up test drafts before finishing**, except the final results
   draft.
5. **Results email is a draft, not a send.** The final report is a
   draft addressed to `<YOUR_REPORT_EMAIL>`, left in Drafts. The
   user reviews and sends manually.

## Step 0 — Sanity check the setup

Run exactly:

```bash
mailctl --version
mailctl doctor
mailctl accounts list
```

If any of these fail, stop and print the output. Do not continue.
Do not try to install `mailctl` yourself; tell the user what's missing.

From `mailctl accounts list`, pick two accounts to use throughout.
Call them `<ACCOUNT_A>` (primary) and `<ACCOUNT_B>` (secondary).
Note their names in your scratch notes.

## Test procedure

For each test below, you must:

1. **Plan from the skill first.** State in your scratch notes which
   part of the skill (roughly: which section/heading) told you how to
   do the test. If the skill didn't cover it, flag it as a skill gap
   before acting.
2. Run the command. Record the exact command line.
3. Record: exit code, elapsed time, PASS / FAIL / SKIPPED /
   SKILL-GAP / SKILL-INFERRED.
4. For anything unexpected, quote one short line of output (not a
   whole buffer) to the scratch notes.

Keep the scratch notes terse. You'll consolidate them into the report
at the end.

### Phase 1 — Read path coverage

T1. `accounts list` — default table output
T2. `accounts list --json` — valid JSON with documented keys
T3. `mailboxes list` (no filter) — multiple accounts represented
T4. `mailboxes list --account <ACCOUNT_A>`
T5. `mailboxes list --account Nowhere` — usage error, known accounts listed
T6. `mailboxes list --account <ACCOUNT_A> --json` — JSON shape
T7. `messages list --account <ACCOUNT_A> --limit 5`
T8. `messages list --account <ACCOUNT_A> --mailbox <INBOX-or-Inbox> --limit 5`
    (the skill should tell you which variant an account needs)
T9. `messages list --account <ACCOUNT_A> --unread --limit 5`
T10. `messages list --account <ACCOUNT_A> --from <some-substring-you-can-find>`
T11. `messages list --account <ACCOUNT_A> --subject <some-substring>`
T12. `messages list --account <ACCOUNT_A> --since <a-recent-date> --before <later-date>`
T13. `messages list --account <ACCOUNT_A> --mailbox Nowhere` — usage error
T14. `messages list --account <ACCOUNT_A> --limit 3 --json` — JSON shape; save an ID for T15
T15. `messages show <ID>` — full output
T16. `messages show <ID> --json` — JSON with to/cc/bcc and attachments array
T17. `messages show 99999999` — not-found error
T18. `messages search --subject <common-substring>` — cross-account
T19. `messages search --account <ACCOUNT_A> --mailbox INBOX --subject <substring> --limit 3`
T20. `messages search --from <substring> --limit 5 --json`
T21. `drafts list`
T22. `drafts list --account <ACCOUNT_A> --json`

Time each of T7–T21. They should all return in under 1 second; flag
anything slower.

### Phase 2 — Write path (draft only; no real sends)

Track every draft ID you create in scratch notes so you can clean up
in Phase 4.

T23. `compose --to test@example.com --subject "mailctl test dry" --body "body" --dry-run`
    — nothing reaches Mail.app
T24. `compose --to test@example.com --subject "mailctl test draft" --body "body"`
    — creates a draft, record ID
T25. `compose --to test@example.com --subject "mailctl test send dry" --body "body" --dangerously-send --dry-run`
    — describes a SEND, nothing executed
T26. `echo n | mailctl compose --to test@example.com --subject "mailctl test cancel" --body "body" --dangerously-send`
    — confirmation prompt appears, declined; nothing sent, no draft left
T27. `echo "stdin body" | mailctl compose --to test@example.com --subject "mailctl stdin"`
    — stdin body, record draft ID
T28. `compose --to test@example.com --subject "mailctl from test" --body "body" --from <ACCOUNT_A>`
    — draft appears in `<ACCOUNT_A>`, record ID
T29. Verify every draft ID you recorded shows up in `mailctl drafts list`
T30. `drafts edit <one of your IDs> --subject "mailctl edited"` — verify
     via `messages show <id> --json`
T31. `messages mark <one of your IDs> --read` then `messages show <id> --json`
     shows `"read": true`
T32. `messages mark <one of your IDs> --flagged` — verify `"flagged": true`
T33. (If an `Archive` mailbox exists in the same account)
     `messages move <one of your IDs> --to Archive`. If not applicable,
     skip and record the reason.

### Phase 3 — Reply and forward (draft only)

Pick one recent message from `<ACCOUNT_A>`'s INBOX.

T34. `reply <ID> --body "regression reply — do not send" --dry-run`
T35. `reply <ID> --body "regression reply — do not send"` — record draft ID
T36. `forward <ID> --to test@example.com --body "regression fwd — do not send" --dry-run`
T37. `forward <ID> --to test@example.com --body "regression fwd — do not send"`
     — record draft ID

### Phase 4 — Cleanup

For every draft ID recorded in Phases 2 and 3, run
`mailctl messages delete <id>` (default, moves to Trash). Verify each
ID is gone from `mailctl drafts list`. Do NOT delete the final results
draft you're about to create in Phase 5.

### Phase 5 — Write the report

Build the report body as a markdown file at
`/tmp/mailctl-regression-report.md`, then compose it as a draft with:

```bash
mailctl compose \
    --to <YOUR_REPORT_EMAIL> \
    --subject "mailctl regression test results — <YYYY-MM-DD>" \
    --body-file /tmp/mailctl-regression-report.md
```

Do **not** pass `--dangerously-send`. Leave it in Drafts for the user
to review and send.

### Required report sections

1. **Header**: branch and commit SHA (`git rev-parse --abbrev-ref HEAD`
   and `git rev-parse HEAD`), date, hostname, `mailctl --version`.
2. **Summary counts**: total tests, PASS, FAIL, SKIPPED, SKILL-GAP,
   SKILL-INFERRED.
3. **Timing highlights**: three slowest commands and three fastest.
   Flag any read command over 1 second.
4. **CLI results table**: one row per test (T1..T37) with verdict,
   elapsed time, and a one-line note if not clean PASS.
5. **Skill feedback**, split into three buckets. This is the most
   valuable section — be specific:
   - **Gaps**: actions the test required that the skill didn't cover
     clearly enough for you to act without guessing. Quote the
     phrasing of the skill section you expected to help, or state
     "no corresponding section". Example: "T8: the skill says 'IMAP
     uses INBOX, Exchange uses Inbox' but didn't tell me which
     account on this machine is which; I had to guess."
   - **Wrong or stale**: anything in the skill that contradicted
     what the CLI actually did. Quote both the skill text and the
     observed behaviour.
   - **Superfluous**: anything in the skill you didn't use and would
     suggest removing to keep it lean.
6. **Safety verification**: assert (with evidence from the recorded
   test IDs) that:
   - No real email was sent during the run.
   - Every `--dangerously-send` either had `--dry-run` or was
     declined at the prompt.
   - Every draft created in Phases 2 and 3 was deleted in Phase 4
     (except the results draft).
7. **Environment footer**: `uname -a`, Mail.app version
   (`mdls -name kMDItemVersion /System/Applications/Mail.app` or the
   `/Applications/` path), Python version.

### Phase 6 — Hand off

Post a brief final chat message (not to Mail.app) containing:

- The results draft ID (from `drafts list` or the `compose` output).
- Overall counts: PASS / FAIL / SKILL-GAP.
- The top three skill-improvement recommendations.
- Nothing to act on further — the user will open Drafts and send
  manually.

Do not run any more commands after the summary unless asked.
