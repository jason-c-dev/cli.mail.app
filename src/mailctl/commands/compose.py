"""Compose command — create Mail.app drafts, or (opt-in) send real messages.

This module implements the ironclad draft-first safety model described in
the product spec and CLAUDE.md.  The rules are enforced *in code*, not by
policy:

1. ``--dangerously-send`` is the **only** way to produce AppleScript that
   includes the ``send`` verb.  There is no environment variable, no
   config-file bypass, no alias, no ``envvar=`` on the Typer option.  The
   absence of those features is the safety model.
2. Without ``--dangerously-send`` the generated AppleScript always ends at
   ``make new outgoing message`` (plus attachments / recipients).  It never
   contains ``send``.
3. Interactive confirmation defaults to **No**.  ``--yes`` skips the
   prompt but *only* in combination with ``--dangerously-send``; on its
   own, ``--yes`` does nothing dangerous.
4. ``--dry-run`` prints a summary and returns without constructing a
   compose/send AppleScript call (account-lookup read scripts are fine).
5. All send-path tests use mocked ``osascript`` subprocess calls and
   assert on the generated script string.  No test in this codebase
   invokes a real send.

Architecture (mirrors the build / parse / fetch / register pattern used in
``accounts``, ``mailboxes``, and ``messages``):

- :func:`build_compose_script` — generate the AppleScript for the default
  (draft-only) path or, when explicitly requested via *include_send*, for
  the send path.  The *include_send* parameter is flowing straight from
  the CLI's ``--dangerously-send`` flag — no other caller sets it.
- :func:`build_account_names_script` / :func:`parse_account_names_output`
  — account-lookup helpers used for ``--from`` validation.
- :func:`perform_compose` — orchestrates engine calls and returns a
  result dict describing what happened.
- :func:`register` — thin Typer wrapper that parses args, handles
  confirmation, delegates to :func:`perform_compose`, and renders output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import typer
from rich.console import Console

from mailctl.engine import run_applescript
from mailctl.errors import (
    AppleScriptError,
    EXIT_GENERAL_ERROR,
    EXIT_USAGE_ERROR,
)
from mailctl.output import handle_mail_error, render_error


# --------------------------------------------------------------------------- #
# AppleScript escaping
# --------------------------------------------------------------------------- #

def _escape_applescript_string(value: str) -> str:
    """Escape a Python string for safe inclusion in an AppleScript literal.

    Backslashes and double-quotes are escaped.  Newlines are converted to
    AppleScript ``& return &`` concatenation so multi-line bodies survive
    the journey into Mail.app.
    """
    # Escape backslashes first, then double quotes.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')

    # Replace CR/LF with AppleScript concatenation of `return` so that
    # newlines in the body carry through to Mail.app.  We split, wrap
    # each line in quotes, and join with ``& return &``.
    # First normalise CRLF -> LF.
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in escaped:
        return f'"{escaped}"'

    lines = escaped.split("\n")
    quoted = [f'"{line}"' for line in lines]
    return " & return & ".join(quoted)


# --------------------------------------------------------------------------- #
# AppleScript generation — account names lookup
# --------------------------------------------------------------------------- #

def build_account_names_script() -> str:
    """Return AppleScript listing all Mail.app account names, one per line."""
    return '''\
tell application "Mail"
    set output to ""
    set acctNames to name of every account
    repeat with n in acctNames
        if output is not "" then set output to output & linefeed
        set output to output & (n as string)
    end repeat
    return output
end tell'''


def parse_account_names_output(raw: str) -> list[str]:
    """Parse the newline-delimited account-names output into a list."""
    if not raw.strip():
        return []
    return [line.strip() for line in raw.strip().split("\n") if line.strip()]


# --------------------------------------------------------------------------- #
# AppleScript generation — compose / draft / send
# --------------------------------------------------------------------------- #

def build_compose_script(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    from_account: str | None = None,
    attachments: list[str] | None = None,
    include_send: bool = False,
) -> str:
    """Return AppleScript that creates a new outgoing Mail.app message.

    The generated script always builds an outgoing message with recipients,
    subject, body, and optional attachments.  When *include_send* is
    ``True`` it appends a ``send`` verb to the block — this is the ONLY
    code path that produces the send verb, and it is only called when the
    literal ``--dangerously-send`` CLI flag has been supplied.

    When *include_send* is ``False``, the script finishes by saving the
    message as a draft (Mail.app's ``make new outgoing message`` with
    ``visible:true`` leaves the draft in the Drafts folder, but we add an
    explicit ``save`` call for belt-and-braces safety so the draft is
    persisted even without the Mail.app compose window being brought to
    the foreground).

    Output format (on success, the script prints a single line containing
    the message id, which the caller parses).

    Attachment paths MUST be validated by the caller before reaching this
    function — a missing attachment would otherwise surface as a runtime
    AppleScript error.
    """
    attachments = attachments or []

    # --- Build recipient blocks -----------------------------------------
    recip_lines: list[str] = []
    for addr in to:
        recip_lines.append(
            f'make new to recipient at end of to recipients '
            f'with properties {{address:{_escape_applescript_string(addr)}}}'
        )
    for addr in cc:
        recip_lines.append(
            f'make new cc recipient at end of cc recipients '
            f'with properties {{address:{_escape_applescript_string(addr)}}}'
        )
    for addr in bcc:
        recip_lines.append(
            f'make new bcc recipient at end of bcc recipients '
            f'with properties {{address:{_escape_applescript_string(addr)}}}'
        )
    recip_block = "\n        ".join(recip_lines)

    # --- Build sender binding (if --from given) -------------------------
    if from_account:
        # Mail.app's `sender` property on an outgoing message is a string
        # that must match one of the account's email addresses.  We also
        # keep a human-readable reference to the account name for the
        # output summary.
        sender_block = (
            f'set senderAcct to account {_escape_applescript_string(from_account)}\n'
            f'        set senderEmail to item 1 of (email addresses of senderAcct)\n'
            f'        set sender of newMessage to senderEmail as string'
        )
    else:
        sender_block = "-- no explicit sender account"

    # --- Build attachments block ----------------------------------------
    attach_lines: list[str] = []
    for path in attachments:
        # Paths may contain quotes; escape defensively.  AppleScript's
        # POSIX file coercion handles the rest.
        escaped = _escape_applescript_string(path)
        attach_lines.append(
            f'make new attachment with properties '
            f'{{file name:(POSIX file {escaped})}} '
            f'at after the last paragraph'
        )
    attach_block = "\n        ".join(attach_lines) if attach_lines else "-- no attachments"

    subj = _escape_applescript_string(subject)
    body_expr = _escape_applescript_string(body)

    # --- Finale: save as draft, and optionally send ---------------------
    # Note: the `send newMessage` line is the ONLY place `send` appears in
    # the generated AppleScript, and it is guarded by include_send.
    if include_send:
        finale = "send newMessage\n    return (id of newMessage) as string"
    else:
        # Default path — save draft and return the draft message id.
        # `save newMessage` ensures the draft is persisted to Drafts even
        # if visible:false.  We leave visible:true so the user can see it
        # in Mail.app, but save it anyway for safety.
        finale = "save newMessage\n    return (id of newMessage) as string"

    script = f'''\
tell application "Mail"
    set newMessage to make new outgoing message with properties {{subject:{subj}, content:{body_expr}, visible:true}}
    tell newMessage
        {sender_block}
        {recip_block if recip_block else "-- no recipients (unexpected)"}
        {attach_block}
    end tell
    {finale}
end tell'''
    return script


# --------------------------------------------------------------------------- #
# Body sourcing
# --------------------------------------------------------------------------- #

def resolve_body(
    *,
    body: str | None,
    body_file: str | None,
    stdin_is_tty: bool,
    stdin_reader,
) -> str:
    """Determine the body text from CLI args / stdin; raise on conflict/empty.

    Exactly one of ``--body``, ``--body-file``, or piped stdin must be
    supplied.  Passing more than one is a usage error.  Passing none with
    an interactive stdin (TTY) is also a usage error.

    Raises :class:`typer.BadParameter` on invalid input.
    """
    sources: list[str] = []
    if body is not None:
        sources.append("--body")
    if body_file is not None:
        sources.append("--body-file")
    stdin_available = not stdin_is_tty
    # We only count stdin as a source if neither --body nor --body-file
    # is given; users piping a body while also passing --body would
    # otherwise surprise themselves.  If they pass stdin AND a flag,
    # prefer the flag silently (common Unix behaviour).

    if len(sources) > 1:
        raise typer.BadParameter(
            "Specify exactly one of --body or --body-file, not both."
        )

    if body is not None:
        return body

    if body_file is not None:
        path = Path(body_file)
        if not path.is_file():
            raise typer.BadParameter(
                f"--body-file '{body_file}' does not exist or is not a file."
            )
        return path.read_text()

    if stdin_available:
        data = stdin_reader()
        if data:
            return data
        # Empty stdin — treat as missing body.
        raise typer.BadParameter(
            "No body supplied. Pass --body <text>, --body-file <path>, "
            "or pipe body text on stdin."
        )

    raise typer.BadParameter(
        "No body supplied. Pass --body <text>, --body-file <path>, "
        "or pipe body text on stdin."
    )


# --------------------------------------------------------------------------- #
# Account validation
# --------------------------------------------------------------------------- #

def fetch_account_names() -> list[str]:
    """Return the list of configured Mail.app account names."""
    script = build_account_names_script()
    raw = run_applescript(script)
    return parse_account_names_output(raw)


# --------------------------------------------------------------------------- #
# High-level compose orchestration
# --------------------------------------------------------------------------- #

def perform_compose(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    from_account: str | None,
    attachments: list[str],
    dangerously_send: bool,
) -> dict[str, Any]:
    """Execute the compose AppleScript and return a structured result dict.

    The *dangerously_send* parameter maps 1:1 to the CLI flag.  It is the
    ONLY way this function can produce a script containing the ``send``
    verb — see :func:`build_compose_script`.

    Returns a dict with keys: ``action`` (``"draft"`` or ``"sent"``),
    ``account``, ``to``, ``cc``, ``bcc``, ``subject``, and ``id`` (the
    Mail.app message id returned by the AppleScript).
    """
    script = build_compose_script(
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body=body,
        from_account=from_account,
        attachments=attachments,
        include_send=dangerously_send,
    )
    raw = run_applescript(script)
    # The script returns the message id as a bare string; strip any
    # surrounding whitespace or quotes.
    message_id = raw.strip().strip('"')

    return {
        "action": "sent" if dangerously_send else "draft",
        "account": from_account,
        "to": list(to),
        "cc": list(cc),
        "bcc": list(bcc),
        "subject": subject,
        "id": message_id,
    }


# --------------------------------------------------------------------------- #
# Dry-run summary
# --------------------------------------------------------------------------- #

def _dry_run_summary(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    from_account: str | None,
    attachments: list[str],
    dangerously_send: bool,
) -> str:
    """Render a human-readable dry-run summary."""
    verb = "SEND" if dangerously_send else "create a DRAFT"
    lines: list[str] = [f"[dry-run] Would {verb} a message with:"]
    lines.append(f"  From:    {from_account or '(default Mail.app account)'}")
    lines.append(f"  To:      {', '.join(to)}")
    if cc:
        lines.append(f"  Cc:      {', '.join(cc)}")
    if bcc:
        lines.append(f"  Bcc:     {', '.join(bcc)}")
    lines.append(f"  Subject: {subject}")
    lines.append(f"  Body:    {body[:200]}{'...' if len(body) > 200 else ''}")
    if attachments:
        lines.append(f"  Attach:  {', '.join(attachments)}")
    if dangerously_send:
        lines.append("  (--dangerously-send was supplied; no message was actually sent because --dry-run.)")
    else:
        lines.append("  (No --dangerously-send; this would only create a draft.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Typer command registration
# --------------------------------------------------------------------------- #

def register(app: typer.Typer) -> None:
    """Register the ``compose`` command on *app*.

    Note the help strings are deliberately blunt about what
    ``--dangerously-send`` does: it SENDS a real email.  No environment
    variable or config file can set this flag implicitly.
    """

    @app.command(
        "compose",
        help=(
            "Compose a new email. Default behaviour creates a DRAFT in "
            "Mail.app's Drafts folder. Use --dangerously-send to actually "
            "send (requires explicit flag on every invocation — there is "
            "no env var or config file override)."
        ),
    )
    def compose(
        ctx: typer.Context,
        to: list[str] = typer.Option(
            ...,
            "--to",
            help="Recipient email address (repeatable).",
        ),
        subject: str = typer.Option(
            ...,
            "--subject",
            help="Subject line for the message.",
        ),
        cc: list[str] = typer.Option(
            None,
            "--cc",
            help="Cc recipient email address (repeatable).",
        ),
        bcc: list[str] = typer.Option(
            None,
            "--bcc",
            help="Bcc recipient email address (repeatable).",
        ),
        body: Optional[str] = typer.Option(
            None,
            "--body",
            help="Inline body text.",
        ),
        body_file: Optional[str] = typer.Option(
            None,
            "--body-file",
            help="Read body text from a file path.",
        ),
        from_account: Optional[str] = typer.Option(
            None,
            "--from",
            help="Name of the Mail.app account to compose from.",
        ),
        attach: list[str] = typer.Option(
            None,
            "--attach",
            help="Path to a file to attach (repeatable).",
        ),
        dangerously_send: bool = typer.Option(
            False,
            "--dangerously-send",
            help=(
                "DANGER: actually sends the email. Without this flag a draft "
                "is created. This flag must be supplied on every invocation; "
                "it cannot be set by env var, config file, or alias. "
                "Sending is irreversible."
            ),
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help=(
                "Skip the interactive confirmation prompt. Only meaningful "
                "in combination with --dangerously-send; on its own it does "
                "nothing dangerous."
            ),
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Print what would happen without running the compose AppleScript.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Output the result as JSON.",
        ),
    ) -> None:
        """Create a new Mail.app draft (default) or send with --dangerously-send."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        cc_list = list(cc or [])
        bcc_list = list(bcc or [])
        attach_list = list(attach or [])

        # --- Resolve body ------------------------------------------------
        try:
            resolved_body = resolve_body(
                body=body,
                body_file=body_file,
                stdin_is_tty=sys.stdin.isatty(),
                stdin_reader=lambda: sys.stdin.read(),
            )
        except typer.BadParameter as exc:
            render_error(str(exc), no_color=no_color)
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # --- Validate attachments ---------------------------------------
        for path in attach_list:
            if not Path(path).is_file():
                render_error(
                    f"Attachment '{path}' does not exist or is not a file.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)

        # --- Dry-run short-circuits before any compose AppleScript ------
        if dry_run:
            summary = _dry_run_summary(
                to=list(to),
                cc=cc_list,
                bcc=bcc_list,
                subject=subject,
                body=resolved_body,
                from_account=from_account,
                attachments=attach_list,
                dangerously_send=dangerously_send,
            )
            if json_mode:
                payload = {
                    "action": "dry-run",
                    "would_send": bool(dangerously_send),
                    "account": from_account,
                    "to": list(to),
                    "cc": cc_list,
                    "bcc": bcc_list,
                    "subject": subject,
                    "attachments": attach_list,
                }
                sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            else:
                sys.stdout.write(summary + "\n")
            raise typer.Exit(code=0)

        # --- Validate --from against real Mail.app accounts -------------
        resolved_account = from_account
        if from_account is not None:
            try:
                known = fetch_account_names()
            except AppleScriptError as exc:
                handle_mail_error(exc, no_color=no_color)
                return  # unreachable
            if from_account not in known:
                render_error(
                    f"Account '{from_account}' not found. "
                    f"Known accounts: {', '.join(known) or '(none)'}",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)

        # --- Confirmation prompt (only when sending) --------------------
        # The prompt is ONLY shown when dangerously_send is True.  Without
        # it, --yes is a no-op.  This is the architectural belt that
        # makes --yes alone unable to authorise a send.
        if dangerously_send and not yes:
            confirmed = _prompt_confirmation(
                to=list(to),
                cc=cc_list,
                bcc=bcc_list,
                subject=subject,
                from_account=resolved_account,
            )
            if not confirmed:
                sys.stdout.write("Send cancelled. No message was sent.\n")
                raise typer.Exit(code=0)

        # --- Actually invoke the compose AppleScript -------------------
        try:
            result = perform_compose(
                to=list(to),
                cc=cc_list,
                bcc=bcc_list,
                subject=subject,
                body=resolved_body,
                from_account=resolved_account,
                attachments=attach_list,
                dangerously_send=dangerously_send,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable

        # --- Render result ----------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_human_output(result)


# --------------------------------------------------------------------------- #
# Confirmation prompt
# --------------------------------------------------------------------------- #

def _prompt_confirmation(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    from_account: str | None,
) -> bool:
    """Ask the user to confirm a real send.  Defaults to **No**.

    The default-to-No behaviour is the architectural safety feature: if
    the user presses Enter without typing anything, the send is aborted.
    Only a literal ``y`` or ``yes`` (case-insensitive) returns True.
    """
    sys.stdout.write(
        f"About to SEND a real email:\n"
        f"  From:    {from_account or '(default Mail.app account)'}\n"
        f"  To:      {', '.join(to)}\n"
    )
    if cc:
        sys.stdout.write(f"  Cc:      {', '.join(cc)}\n")
    if bcc:
        sys.stdout.write(f"  Bcc:     {', '.join(bcc)}\n")
    sys.stdout.write(f"  Subject: {subject}\n")
    sys.stdout.write("Proceed? [y/N]: ")
    sys.stdout.flush()

    try:
        answer = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False

    answer = (answer or "").strip().lower()
    return answer in ("y", "yes")


# --------------------------------------------------------------------------- #
# Human output
# --------------------------------------------------------------------------- #

def _render_human_output(result: dict[str, Any]) -> None:
    """Render a human-readable confirmation of the compose result."""
    action = result.get("action", "draft")
    account = result.get("account") or "(default)"
    subject = result.get("subject", "")
    msg_id = result.get("id", "")

    if action == "sent":
        sys.stdout.write(
            f"Message sent from account '{account}'. "
            f"Subject: {subject!r}. Message id: {msg_id}\n"
        )
    else:
        sys.stdout.write(
            f"Draft created in account '{account}'. "
            f"Subject: {subject!r}. Draft id: {msg_id}\n"
        )
