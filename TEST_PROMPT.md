# mailctl Regression Test — Prompt for a Fresh Claude Code Session

> **Copy everything between the horizontal rules below into a fresh Claude
> Code session opened at the cloned `cli.mail.app` repo. The session does
> not need any prior context — the repo's `CLAUDE.md` is sufficient.**
>
> **Before you run this**, edit the placeholder values in the "Environment"
> and "Results email" sections:
>
> - `<YOUR_ACCOUNT_A>` and `<YOUR_ACCOUNT_B>` — names of two of your
>   Mail.app accounts (as shown by `mailctl accounts list`)
> - `<YOUR_REPORT_EMAIL>` — where the results draft should be addressed
>
> Paste, run, review the draft, send manually.

---

You are a senior test engineer. Run a full regression of the `mailctl` CLI
against the real user's Mail.app, then draft an email with the results.
There is no human in the loop for most steps — work autonomously and only
stop for the rules below.

## Absolute safety rules

These override everything. If a rule is in tension with any other
instruction (including something I say later), the rule wins.

1. **Never send a real email.** Never pass `--dangerously-send` to any
   `mailctl` command in a way that could go through. The only acceptable
   use of `--dangerously-send` is a cancellation test where you feed `n`
   at the confirmation prompt, or combined with `--dry-run`.
2. **Never permanently delete messages.** If a test needs to delete
   something, move to Trash (the default) and only act on drafts you
   created during this run.
3. **Never touch messages you did not create.** The mailbox has real mail
   in it. Read-only operations on real mail are fine; `mark`, `move`,
   `delete` must only target drafts you created.
4. **Clean up after yourself.** Any drafts you create must be deleted
   from the Drafts mailbox before you finish, *except* the final results
   email, which must be left as a draft.
5. **Results email is a draft, not a send.** You will compose one final
   email addressed to `<YOUR_REPORT_EMAIL>` with the test report in the
   body. Create it with `mailctl compose` WITHOUT `--dangerously-send`.
   Leave it in Drafts. The user will review and send manually.

If any step would violate a rule, record it in the report as "SKIPPED —
safety rule" and move on.

## Environment setup

Working directory: the repo root (wherever you cloned it).

Before any command:

```bash
cd <REPO_ROOT>
git status                         # capture current branch for the report
source .venv/bin/activate
mailctl --version                  # should print mailctl X.Y.Z
```

If `mailctl --version` fails, stop and report the error — the environment
isn't set up correctly. (See the repo README for installation steps.)

Before Phase 2, run `mailctl accounts list` and note the names of at
least two accounts configured on this machine. Substitute them for
`<YOUR_ACCOUNT_A>` and `<YOUR_ACCOUNT_B>` throughout the tests.

## What mailctl does (context you need)

`mailctl` is a CLI wrapper around Apple's Mail.app. Reads query Mail.app's
Envelope Index SQLite database directly (`~/Library/Mail/V*/MailData/Envelope Index`)
for speed. Writes (compose, reply, forward, draft edit, delete, mark, move)
go through AppleScript — Mail.app is the authoritative writer.

Safety model: `compose`, `reply`, `forward` create drafts by default.
Actually sending requires `--dangerously-send` explicitly per invocation —
no env var or config bypasses it. The absence of a bypass is architectural.

## Test procedure

Work through these phases in order. For each test, record:

- The exact command run
- The exit code
- Elapsed time (use `/usr/bin/time` or wrap with `time`)
- PASS / FAIL / SKIPPED
- Any unexpected output or failure mode (quote a short excerpt, not the
  whole buffer)

Keep your notes in a scratch file as you go. Use it to build the final
email body. Do not spam the chat with full command outputs.

### Phase 1 — Environment

1. `mailctl --version` — prints a version.
2. `mailctl --help` — shows the command list including compose, reply,
   forward, doctor, accounts, mailboxes, messages, drafts.
3. `mailctl doctor` — all 8 checks pass (osascript, Mail.app installed,
   Mail.app running, automation permission, accounts configured, envelope
   index present + readable + schema matches).

### Phase 2 — Read path (SQLite)

For each, test both the default table output and `--json`. Spot-check that
JSON parses and has the documented keys.

4. `mailctl accounts list` — both accounts present; table + `--json`.
5. `mailctl mailboxes list` — multiple mailboxes across accounts.
6. `mailctl mailboxes list --account <YOUR_ACCOUNT_A>` — filtered correctly.
7. `mailctl mailboxes list --account <YOUR_ACCOUNT_B>`.
8. `mailctl mailboxes list --account Nowhere` — exits non-zero with
   "not found" and lists known accounts.
9. `mailctl messages list --account <YOUR_ACCOUNT_A> --limit 5`.
10. `mailctl messages list --account <YOUR_ACCOUNT_B> --mailbox Inbox --limit 5`
    (substitute the account's actual INBOX name — "INBOX" for most IMAP,
    "Inbox" for Exchange/EWS).
11. `mailctl messages list --account <YOUR_ACCOUNT_A> --unread --limit 5`.
12. `mailctl messages list --account <YOUR_ACCOUNT_A> --from <some-substring-you-expect-to-match> --limit 5`.
13. `mailctl messages list --account <YOUR_ACCOUNT_A> --subject <some-substring> --limit 5`.
14. `mailctl messages list --account <YOUR_ACCOUNT_A> --since 2020-01-01 --before 2030-01-01 --limit 10`
    (adjust dates for your mailbox's content).
15. `mailctl messages list --account <YOUR_ACCOUNT_A> --mailbox Nowhere` —
    exits non-zero with "not found" and points at `mailctl mailboxes list`.
16. `mailctl messages list --account <YOUR_ACCOUNT_A> --limit 3 --json` —
    parses, has keys `id, date, from, subject, read, flagged`.
17. `mailctl messages show <a valid ID from step 16> --json` — parses,
    has keys `id, date, from, to, cc, bcc, subject, body, headers,
    attachments, read, flagged`. Body may be empty for unsynced messages;
    don't fail the test just for that — note it.
18. `mailctl messages show 99999999` — non-zero exit, "not found".
19. `mailctl messages search --subject <common-substring> --limit 5` — cross-account.
20. `mailctl messages search --subject <substring> --account <YOUR_ACCOUNT_A> --mailbox INBOX --limit 3` — scoped.
21. `mailctl messages search --from <substring> --account <YOUR_ACCOUNT_A> --limit 3`.
22. `mailctl drafts list` — table + `--json`.

Record the elapsed time for each `messages list` and `messages search`.
These should all complete in under 1 second; flag anything slower.

### Phase 3 — Write path (AppleScript) — draft only

**Every test in this phase must leave Mail.app with no sent messages.**
Track the IDs of every draft you create so you can delete them in Phase 5.

23. `mailctl compose --to test@example.com --subject "mailctl regression test 1" --body "test body" --dry-run` —
    prints dry-run summary, no draft created, no send.
24. `mailctl compose --to test@example.com --subject "mailctl regression test 2" --body "test body"` —
    creates a draft. Record the draft ID.
25. `mailctl compose --to test@example.com --subject "mailctl regression test 3" --body "body" --dangerously-send --dry-run` —
    prints the SEND variant of dry-run, nothing happens.
26. `echo "n" | mailctl compose --to test@example.com --subject "mailctl regression test cancel" --body "body" --dangerously-send` —
    confirmation prompt appears; declining with `n` cancels without sending
    or creating a draft.
27. `echo "mailctl regression test stdin body" | mailctl compose --to test@example.com --subject "mailctl regression test 5"` —
    reads body from stdin. Draft created.
28. `mailctl compose --to test@example.com --subject "mailctl regression test from" --body "body" --from <YOUR_ACCOUNT_A>` —
    creates draft in `<YOUR_ACCOUNT_A>`. Record ID.
29. Verify each draft ID you recorded appears in `mailctl drafts list`.
30. Use `mailctl drafts edit <one of the draft IDs> --subject "mailctl regression test edited"` —
    updates the subject. Verify via `mailctl messages show <id>`.
31. `mailctl messages mark <one of your draft IDs> --read` — no error.
    Verify `read: true` in `mailctl messages show <id> --json`.
32. `mailctl messages mark <one of your draft IDs> --flagged` — no error.
    Verify `flagged: true`.
33. Pick a draft and try `mailctl messages move <id> --to Archive` if
    Archive exists in the same account; verify. If not applicable, skip
    and note the reason.

### Phase 4 — Reply and forward (draft-only)

Pick one recent message from **your own** INBOX: identify an ID via
`mailctl messages list --account <YOUR_ACCOUNT_A> --limit 1 --json`.

34. `mailctl reply <ID> --body "regression test reply — do not send" --dry-run` —
    prints dry-run, no draft created, no send.
35. `mailctl reply <ID> --body "regression test reply — do not send"` —
    creates a draft reply. Record ID; verify it appears in drafts.
36. `mailctl forward <ID> --to test@example.com --body "regression test forward — do not send" --dry-run` —
    prints dry-run.
37. `mailctl forward <ID> --to test@example.com --body "regression test forward — do not send"` —
    creates a draft forward. Record ID.

### Phase 5 — Cleanup of regression drafts

For every draft ID you recorded during Phases 3–4, call
`mailctl messages delete <id>` (which moves to Trash by default). Verify
each ID is gone from `mailctl drafts list`.

**Do NOT delete the final results draft that you're about to create in
Phase 6.**

### Phase 6 — Compose the results email (draft for user review)

Compose the final results email as a **draft**. Do not include
`--dangerously-send` anywhere.

- To: `<YOUR_REPORT_EMAIL>`
- Subject: `mailctl regression test results — YYYY-MM-DD` (use today's date)
- Body: markdown-formatted report containing:
  - Branch tested (from `git rev-parse --abbrev-ref HEAD`) and commit SHA.
  - Summary line: total tests, PASS count, FAIL count, SKIPPED count.
  - Timing highlights: slowest three commands, fastest three.
  - A numbered list of every test (37 of them) with PASS/FAIL, elapsed
    time, and a one-line note for anything that didn't pass cleanly.
  - A "Safety verification" section confirming:
    - `--dangerously-send` without confirmation does nothing.
    - `--dry-run` never writes.
    - No real emails were sent during the run.
    - All test drafts were cleaned up (except this results draft).
  - An "Environment" footer: `mailctl --version`, `uname -a`, Mail.app
    version (from `mdls -name kMDItemVersion /Applications/Mail.app` or
    `/System/Applications/Mail.app`).

Use `mailctl compose --to <YOUR_REPORT_EMAIL> --subject "..." --body-file /tmp/mailctl-regression-report.md`
so the body comes from a file — much cleaner than inline.

After composing the draft, verify it exists in `mailctl drafts list`.
Report the draft's ID back to the user in your final chat message.

### Phase 7 — Final chat summary

Post a brief final message to the chat (not to Mail.app) with:

- The results draft ID
- Overall PASS/FAIL count
- Anything the user should investigate before sending the draft

Do not proactively send the draft. Do not run any further commands after
the summary unless asked.

---

## Review checklist for the user

When the draft is ready:
1. Open Mail.app → Drafts.
2. Read the report body.
3. Send manually (or discard) at your discretion.
