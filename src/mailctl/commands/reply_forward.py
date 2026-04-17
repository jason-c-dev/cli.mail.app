"""Reply & Forward commands — respond to or forward existing messages.

This module implements reply and forward with the same ironclad draft-first
safety model as compose (see ``compose.py`` docstring for the full safety
architecture).  The five rules apply identically:

1. ``--dangerously-send`` is the **only** way to produce AppleScript that
   includes the ``send`` verb.  There is no environment variable, no
   config-file bypass, no alias, no ``envvar=`` on the Typer option.
2. Without ``--dangerously-send`` the generated AppleScript never contains
   ``send``.
3. Interactive confirmation defaults to **No**.
4. ``--dry-run`` prints a summary without executing compose/send AppleScript.
5. All send-path tests use mocked ``osascript`` only.

Architecture (mirrors compose.py's build / perform / register pattern):

- :func:`build_fetch_message_script` — generate AppleScript to fetch the
  original message's metadata (sender, to, cc, subject, date, body).
- :func:`parse_fetch_message_output` — parse the delimited output into a dict.
- :func:`build_reply_script` — generate the reply AppleScript.
- :func:`build_forward_script` — generate the forward AppleScript.
- :func:`perform_reply` / :func:`perform_forward` — orchestrate fetch + compose.
- :func:`register` — thin Typer wrappers for ``reply`` and ``forward``.

Shared utilities reused from compose:

- :func:`~mailctl.commands.compose._escape_applescript_string`
- :func:`~mailctl.commands.compose.resolve_body`
- :func:`~mailctl.commands.compose._prompt_confirmation`

(These are imported directly — no duplication.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from mailctl.engine import run_applescript
from mailctl.errors import (
    AppleScriptError,
    EXIT_GENERAL_ERROR,
    EXIT_USAGE_ERROR,
)
from mailctl import message_lookup
from mailctl.output import handle_mail_error, render_error

# Reuse shared utilities from compose — no duplication.
from mailctl.commands.compose import (
    _escape_applescript_string,
    resolve_body,
)


# --------------------------------------------------------------------------- #
# AppleScript generation — fetch user's own email addresses
# --------------------------------------------------------------------------- #

def build_user_emails_script() -> str:
    """Return AppleScript that lists all email addresses across accounts.

    Output format: one email address per line.
    This is a read-only operation — safe to execute anytime.
    """
    return '''\
tell application "Mail"
    set output to ""
    set accts to every account
    repeat with acct in accts
        set addrs to email addresses of acct
        repeat with addr in addrs
            if output is not "" then set output to output & linefeed
            set output to output & (addr as string)
        end repeat
    end repeat
    return output
end tell'''


def parse_user_emails_output(raw: str) -> list[str]:
    """Parse the newline-delimited email-addresses output into a list."""
    if not raw.strip():
        return []
    return [line.strip().lower() for line in raw.strip().split("\n") if line.strip()]


def fetch_user_emails() -> list[str]:
    """Return the list of the user's own email addresses from Mail.app.

    This is a read-only operation.  Used to exclude the user's own
    address from reply-all recipient lists.
    """
    script = build_user_emails_script()
    raw = run_applescript(script)
    return parse_user_emails_output(raw)


# --------------------------------------------------------------------------- #
# AppleScript generation — fetch original message for reply / forward
# --------------------------------------------------------------------------- #

def build_fetch_message_script(
    message_id: str,
    *,
    account: str,
    mailbox: str,
) -> str:
    """Return AppleScript that fetches an original message's metadata.

    Looks up the message in ``mailbox`` of ``account`` — the caller is
    expected to have resolved those via :func:`resolve_message_location`
    (or an equivalent) before invoking. Passing the wrong scope is how
    the "Message not found" error surfaces.

    Output format (``||``-delimited, single line)::

        sender||to_list||cc_list||subject||date||body

    This is a read-only operation — safe to execute even in dry-run mode.
    """
    acct = _escape_applescript_string(account)
    mbox = _escape_applescript_string(mailbox)
    return f'''\
tell application "Mail"
    set targetMsg to first message of mailbox {mbox} of account {acct} whose id is {message_id}
    set msgSender to sender of targetMsg

    set toList to ""
    repeat with addr in (every to recipient of targetMsg)
        if toList is not "" then set toList to toList & ", "
        set toList to toList & (address of addr as string)
    end repeat

    set ccList to ""
    repeat with addr in (every cc recipient of targetMsg)
        if ccList is not "" then set ccList to ccList & ", "
        set ccList to ccList & (address of addr as string)
    end repeat

    set msgSubject to subject of targetMsg
    set msgDate to date received of targetMsg as string
    set msgBody to content of targetMsg

    return msgSender & "||" & toList & "||" & ccList & "||" & msgSubject & "||" & msgDate & "||" & msgBody
end tell'''


def parse_fetch_message_output(raw: str) -> dict[str, str]:
    """Parse the ``||``-delimited original-message output into a dict.

    Returns a dict with keys: ``sender``, ``to``, ``cc``, ``subject``,
    ``date``, ``body``.
    """
    # Split into at most 6 parts so body (which may contain ||) stays intact.
    parts = raw.split("||", 5)
    return {
        "sender": parts[0].strip() if len(parts) > 0 else "",
        "to": parts[1].strip() if len(parts) > 1 else "",
        "cc": parts[2].strip() if len(parts) > 2 else "",
        "subject": parts[3].strip() if len(parts) > 3 else "",
        "date": parts[4].strip() if len(parts) > 4 else "",
        "body": parts[5].strip() if len(parts) > 5 else "",
    }


# --------------------------------------------------------------------------- #
# Quoted content formatting
# --------------------------------------------------------------------------- #

def _build_quoted_body(
    *,
    new_body: str,
    original: dict[str, str],
) -> str:
    """Combine new body text with quoted original content.

    Produces a body like::

        <new_body>

        On <date>, <sender> wrote:
        > <original body line 1>
        > <original body line 2>
    """
    attribution = f"On {original['date']}, {original['sender']} wrote:"
    original_lines = original.get("body", "").split("\n")
    quoted = "\n".join(f"> {line}" for line in original_lines)
    return f"{new_body}\n\n{attribution}\n{quoted}"


# --------------------------------------------------------------------------- #
# AppleScript generation — reply
# --------------------------------------------------------------------------- #

def build_reply_script(
    *,
    message_id: str,
    account: str,
    mailbox: str,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    include_send: bool = False,
) -> str:
    """Return AppleScript that creates a reply to the original message.

    The script uses Mail.app's ``reply`` verb on the original message to
    preserve threading.  It then customises the recipients and body.

    When *include_send* is ``True`` it appends a ``send`` verb — this is
    the ONLY code path that produces the send verb, guarded by the CLI's
    ``--dangerously-send`` flag.
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
    recip_block = "\n        ".join(recip_lines) if recip_lines else "-- recipients set by reply"

    # --- Build attachments block ----------------------------------------
    attach_lines: list[str] = []
    for path in attachments:
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
    if include_send:
        finale = "send replyMsg\n    return (id of replyMsg) as string"
    else:
        finale = "save replyMsg\n    return (id of replyMsg) as string"

    acct = _escape_applescript_string(account)
    mbox = _escape_applescript_string(mailbox)
    script = f'''\
tell application "Mail"
    set originalMsg to first message of mailbox {mbox} of account {acct} whose id is {message_id}
    set replyMsg to reply originalMsg with opening window
    tell replyMsg
        set subject to {subj}
        set content to {body_expr}
        delete every to recipient
        delete every cc recipient
        {recip_block}
        {attach_block}
    end tell
    {finale}
end tell'''
    return script


# --------------------------------------------------------------------------- #
# AppleScript generation — forward
# --------------------------------------------------------------------------- #

def build_forward_script(
    *,
    message_id: str,
    account: str,
    mailbox: str,
    to: list[str],
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    include_send: bool = False,
) -> str:
    """Return AppleScript that creates a forward of the original message.

    The script uses Mail.app's ``forward`` verb on the original message,
    then customises the recipients and body.

    When *include_send* is ``True`` it appends a ``send`` verb — this is
    the ONLY code path that produces the send verb, guarded by the CLI's
    ``--dangerously-send`` flag.
    """
    attachments = attachments or []

    # --- Build recipient blocks -----------------------------------------
    recip_lines: list[str] = []
    for addr in to:
        recip_lines.append(
            f'make new to recipient at end of to recipients '
            f'with properties {{address:{_escape_applescript_string(addr)}}}'
        )
    recip_block = "\n        ".join(recip_lines) if recip_lines else "-- no recipients (unexpected)"

    # --- Build attachments block ----------------------------------------
    attach_lines: list[str] = []
    for path in attachments:
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
    if include_send:
        finale = "send fwdMsg\n    return (id of fwdMsg) as string"
    else:
        finale = "save fwdMsg\n    return (id of fwdMsg) as string"

    acct = _escape_applescript_string(account)
    mbox = _escape_applescript_string(mailbox)
    script = f'''\
tell application "Mail"
    set originalMsg to first message of mailbox {mbox} of account {acct} whose id is {message_id}
    set fwdMsg to forward originalMsg with opening window
    tell fwdMsg
        set subject to {subj}
        set content to {body_expr}
        {recip_block}
        {attach_block}
    end tell
    {finale}
end tell'''
    return script


# --------------------------------------------------------------------------- #
# High-level orchestration — reply
# --------------------------------------------------------------------------- #

def fetch_original_message(
    message_id: str,
    *,
    account: str,
    mailbox: str,
) -> dict[str, str]:
    """Fetch the original message's metadata via AppleScript.

    The caller must provide the account + mailbox where the message
    lives — use :func:`resolve_message_location` to derive them from
    the ID. This is a read-only operation. Raises
    :class:`AppleScriptError` on failure (including message not found).
    """
    script = build_fetch_message_script(
        message_id,
        account=account,
        mailbox=mailbox,
    )
    raw = run_applescript(script)
    return parse_fetch_message_output(raw)


def _compute_reply_recipients(
    original: dict[str, str],
    *,
    reply_all: bool,
    user_email: str | None = None,
) -> tuple[list[str], list[str]]:
    """Compute reply To and Cc lists from the original message.

    For a plain reply: To = [original sender].
    For reply-all: To = [original sender] + original To (minus user),
                   Cc = original Cc (minus user).

    The *user_email* is used to exclude the user's own address from the
    recipient lists.  If not provided, a best-effort approach is used
    (the user might appear in the list, which is acceptable).
    """
    sender = original["sender"].strip()
    to_list = [sender] if sender else []

    if not reply_all:
        return to_list, []

    # Reply-all: add original To recipients (minus user, minus sender).
    original_to = [
        addr.strip() for addr in original.get("to", "").split(",")
        if addr.strip()
    ]
    for addr in original_to:
        if user_email and addr.lower() == user_email.lower():
            continue
        if addr.lower() == sender.lower():
            continue  # Sender is already in to_list
        to_list.append(addr)

    # Cc: original Cc recipients (minus user, minus anyone already in To).
    original_cc = [
        addr.strip() for addr in original.get("cc", "").split(",")
        if addr.strip()
    ]
    to_lower = {a.lower() for a in to_list}
    cc_list: list[str] = []
    for addr in original_cc:
        if user_email and addr.lower() == user_email.lower():
            continue
        if addr.lower() in to_lower:
            continue
        cc_list.append(addr)

    return to_list, cc_list


def perform_reply(
    *,
    message_id: str,
    account: str,
    mailbox: str,
    original: dict[str, str],
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[str],
    dangerously_send: bool,
) -> dict[str, Any]:
    """Execute the reply AppleScript and return a structured result dict.

    The *dangerously_send* parameter maps 1:1 to the CLI flag.  It is the
    ONLY way this function can produce a script containing the ``send``
    verb.
    """
    script = build_reply_script(
        message_id=message_id,
        account=account,
        mailbox=mailbox,
        to=to,
        cc=cc,
        subject=subject,
        body=body,
        attachments=attachments,
        include_send=dangerously_send,
    )
    raw = run_applescript(script)
    reply_id = raw.strip().strip('"')

    return {
        "action": "sent" if dangerously_send else "draft",
        "to": to,
        "cc": cc,
        "subject": subject,
        "id": reply_id,
        "original_message_id": message_id,
    }


# --------------------------------------------------------------------------- #
# High-level orchestration — forward
# --------------------------------------------------------------------------- #

def perform_forward(
    *,
    message_id: str,
    account: str,
    mailbox: str,
    original: dict[str, str],
    to: list[str],
    subject: str,
    body: str,
    attachments: list[str],
    dangerously_send: bool,
) -> dict[str, Any]:
    """Execute the forward AppleScript and return a structured result dict.

    The *dangerously_send* parameter maps 1:1 to the CLI flag.  It is the
    ONLY way this function can produce a script containing the ``send``
    verb.
    """
    script = build_forward_script(
        message_id=message_id,
        account=account,
        mailbox=mailbox,
        to=to,
        subject=subject,
        body=body,
        attachments=attachments,
        include_send=dangerously_send,
    )
    raw = run_applescript(script)
    fwd_id = raw.strip().strip('"')

    return {
        "action": "sent" if dangerously_send else "draft",
        "to": to,
        "subject": subject,
        "id": fwd_id,
        "original_message_id": message_id,
    }


# --------------------------------------------------------------------------- #
# Dry-run summaries
# --------------------------------------------------------------------------- #

def _dry_run_reply_summary(
    *,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[str],
    dangerously_send: bool,
    original_message_id: str,
) -> str:
    """Render a human-readable dry-run summary for reply."""
    verb = "SEND" if dangerously_send else "create a DRAFT"
    lines: list[str] = [f"[dry-run] Would {verb} a reply to message {original_message_id} with:"]
    lines.append(f"  To:      {', '.join(to)}")
    if cc:
        lines.append(f"  Cc:      {', '.join(cc)}")
    lines.append(f"  Subject: {subject}")
    lines.append(f"  Body:    {body[:200]}{'...' if len(body) > 200 else ''}")
    if attachments:
        lines.append(f"  Attach:  {', '.join(attachments)}")
    if dangerously_send:
        lines.append("  (--dangerously-send was supplied; no reply was actually sent because --dry-run.)")
    else:
        lines.append("  (No --dangerously-send; this would only create a draft reply.)")
    return "\n".join(lines)


def _dry_run_forward_summary(
    *,
    to: list[str],
    subject: str,
    body: str,
    attachments: list[str],
    dangerously_send: bool,
    original_message_id: str,
) -> str:
    """Render a human-readable dry-run summary for forward."""
    verb = "SEND" if dangerously_send else "create a DRAFT"
    lines: list[str] = [f"[dry-run] Would {verb} a forwarded message from {original_message_id} with:"]
    lines.append(f"  To:      {', '.join(to)}")
    lines.append(f"  Subject: {subject}")
    lines.append(f"  Body:    {body[:200]}{'...' if len(body) > 200 else ''}")
    if attachments:
        lines.append(f"  Attach:  {', '.join(attachments)}")
    if dangerously_send:
        lines.append("  (--dangerously-send was supplied; no forward was actually sent because --dry-run.)")
    else:
        lines.append("  (No --dangerously-send; this would only create a draft forward.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Confirmation prompt (reuses the same default-to-No pattern as compose)
# --------------------------------------------------------------------------- #

def _prompt_send_confirmation(
    *,
    command: str,
    to: list[str],
    cc: list[str] | None = None,
    subject: str,
) -> bool:
    """Ask the user to confirm a real send.  Defaults to **No**.

    Same default-to-No safety pattern as compose.
    """
    sys.stdout.write(
        f"About to SEND a real {command}:\n"
        f"  To:      {', '.join(to)}\n"
    )
    if cc:
        sys.stdout.write(f"  Cc:      {', '.join(cc)}\n")
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

def _render_reply_human_output(result: dict[str, Any]) -> None:
    """Render a human-readable confirmation of the reply result."""
    action = result.get("action", "draft")
    subject = result.get("subject", "")
    msg_id = result.get("id", "")

    if action == "sent":
        sys.stdout.write(
            f"Reply sent. Subject: {subject!r}. Message id: {msg_id}\n"
        )
    else:
        sys.stdout.write(
            f"Reply draft created. Subject: {subject!r}. Draft id: {msg_id}\n"
        )


def _render_forward_human_output(result: dict[str, Any]) -> None:
    """Render a human-readable confirmation of the forward result."""
    action = result.get("action", "draft")
    subject = result.get("subject", "")
    msg_id = result.get("id", "")

    if action == "sent":
        sys.stdout.write(
            f"Forwarded message sent. Subject: {subject!r}. Message id: {msg_id}\n"
        )
    else:
        sys.stdout.write(
            f"Forward draft created. Subject: {subject!r}. Draft id: {msg_id}\n"
        )


# --------------------------------------------------------------------------- #
# Typer command registration
# --------------------------------------------------------------------------- #

def register(app: typer.Typer) -> None:
    """Register the ``reply`` and ``forward`` commands on *app*.

    Both follow the same safety model as compose: draft-first, no env var
    bypass, default-to-No confirmation.
    """

    # ------------------------------------------------------------------- #
    # reply command
    # ------------------------------------------------------------------- #

    @app.command(
        "reply",
        help=(
            "Reply to an existing message. Default behaviour creates a "
            "DRAFT reply in Mail.app's Drafts folder. Use --dangerously-send "
            "to actually send (requires explicit flag on every invocation "
            "-- there is no env var or config file override)."
        ),
    )
    def reply(
        ctx: typer.Context,
        message_id: str = typer.Argument(
            ...,
            help="The ID of the message to reply to.",
        ),
        reply_all: bool = typer.Option(
            False,
            "--all",
            help="Reply to all recipients (sender + To + Cc), not just the sender.",
        ),
        body: Optional[str] = typer.Option(
            None,
            "--body",
            help="Inline body text for the reply.",
        ),
        body_file: Optional[str] = typer.Option(
            None,
            "--body-file",
            help="Read reply body text from a file path.",
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
                "DANGER: actually sends the reply. Without this flag a draft "
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
            help="Print what would happen without executing the reply AppleScript.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Output the result as JSON.",
        ),
    ) -> None:
        """Reply to a message (draft by default, send with --dangerously-send)."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)
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

        # --- Validate attachments ----------------------------------------
        for path in attach_list:
            if not Path(path).is_file():
                render_error(
                    f"Attachment '{path}' does not exist or is not a file.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)

        # --- Resolve message location + fetch original (read-only, safe) -
        try:
            msg_account, msg_mailbox = message_lookup.resolve_message_location(message_id)
            original = fetch_original_message(
                message_id,
                account=msg_account,
                mailbox=msg_mailbox,
            )
        except AppleScriptError as exc:
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
            if "not found" in exc_str or "can't get" in exc_str:
                render_error(
                    f'Message "{message_id}" not found. '
                    f"Verify the message ID with 'mailctl messages list'.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable

        # --- Compute recipients ------------------------------------------
        # For reply-all, fetch the user's own email addresses to exclude
        # them from the recipient list.
        user_email = None
        if reply_all:
            try:
                user_emails = fetch_user_emails()
                # Find the user's address that appears in the original To list
                original_to_lower = [
                    a.strip().lower()
                    for a in original.get("to", "").split(",")
                    if a.strip()
                ]
                for ue in user_emails:
                    if ue in original_to_lower:
                        user_email = ue
                        break
                # If no match, use the first user email as fallback
                if not user_email and user_emails:
                    user_email = user_emails[0]
            except AppleScriptError:
                pass  # Best-effort; if we can't get emails, proceed without

        to_list, cc_list = _compute_reply_recipients(
            original,
            reply_all=reply_all,
            user_email=user_email,
        )

        # --- Build full body with quoted original ------------------------
        full_body = _build_quoted_body(
            new_body=resolved_body,
            original=original,
        )

        # --- Build subject -----------------------------------------------
        orig_subject = original.get("subject", "")
        if orig_subject.lower().startswith("re:"):
            reply_subject = orig_subject
        else:
            reply_subject = f"Re: {orig_subject}"

        # --- Dry-run short-circuits before compose AppleScript -----------
        if dry_run:
            summary = _dry_run_reply_summary(
                to=to_list,
                cc=cc_list,
                subject=reply_subject,
                body=full_body,
                attachments=attach_list,
                dangerously_send=dangerously_send,
                original_message_id=message_id,
            )
            if json_mode:
                payload = {
                    "action": "dry-run",
                    "would_send": bool(dangerously_send),
                    "to": to_list,
                    "cc": cc_list,
                    "subject": reply_subject,
                    "original_message_id": message_id,
                    "attachments": attach_list,
                }
                sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            else:
                sys.stdout.write(summary + "\n")
            raise typer.Exit(code=0)

        # --- Confirmation prompt (only when sending) --------------------
        if dangerously_send and not yes:
            confirmed = _prompt_send_confirmation(
                command="reply",
                to=to_list,
                cc=cc_list,
                subject=reply_subject,
            )
            if not confirmed:
                sys.stdout.write("Send cancelled. No reply was sent.\n")
                raise typer.Exit(code=0)

        # --- Execute reply AppleScript -----------------------------------
        try:
            result = perform_reply(
                message_id=message_id,
                account=msg_account,
                mailbox=msg_mailbox,
                original=original,
                to=to_list,
                cc=cc_list,
                subject=reply_subject,
                body=full_body,
                attachments=attach_list,
                dangerously_send=dangerously_send,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable

        # --- Render result -----------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_reply_human_output(result)

    # ------------------------------------------------------------------- #
    # forward command
    # ------------------------------------------------------------------- #

    @app.command(
        "forward",
        help=(
            "Forward an existing message. Default behaviour creates a "
            "DRAFT forward in Mail.app's Drafts folder. Use --dangerously-send "
            "to actually send (requires explicit flag on every invocation "
            "-- there is no env var or config file override)."
        ),
    )
    def forward(
        ctx: typer.Context,
        message_id: str = typer.Argument(
            ...,
            help="The ID of the message to forward.",
        ),
        to: list[str] = typer.Option(
            ...,
            "--to",
            help="Recipient email address (repeatable). Required.",
        ),
        body: Optional[str] = typer.Option(
            None,
            "--body",
            help="Inline body text to prepend to the forwarded message.",
        ),
        body_file: Optional[str] = typer.Option(
            None,
            "--body-file",
            help="Read body text from a file path.",
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
                "DANGER: actually sends the forwarded message. Without this "
                "flag a draft is created. This flag must be supplied on every "
                "invocation; it cannot be set by env var, config file, or "
                "alias. Sending is irreversible."
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
            help="Print what would happen without executing the forward AppleScript.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Output the result as JSON.",
        ),
    ) -> None:
        """Forward a message (draft by default, send with --dangerously-send)."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)
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

        # --- Validate attachments ----------------------------------------
        for path in attach_list:
            if not Path(path).is_file():
                render_error(
                    f"Attachment '{path}' does not exist or is not a file.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)

        # --- Resolve message location + fetch original (read-only, safe) -
        try:
            msg_account, msg_mailbox = message_lookup.resolve_message_location(message_id)
            original = fetch_original_message(
                message_id,
                account=msg_account,
                mailbox=msg_mailbox,
            )
        except AppleScriptError as exc:
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
            if "not found" in exc_str or "can't get" in exc_str:
                render_error(
                    f'Message "{message_id}" not found. '
                    f"Verify the message ID with 'mailctl messages list'.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable

        # --- Build full body with original content -----------------------
        full_body = _build_quoted_body(
            new_body=resolved_body,
            original=original,
        )

        # --- Build subject -----------------------------------------------
        orig_subject = original.get("subject", "")
        if orig_subject.lower().startswith("fwd:") or orig_subject.lower().startswith("fw:"):
            fwd_subject = orig_subject
        else:
            fwd_subject = f"Fwd: {orig_subject}"

        # --- Dry-run short-circuits before compose AppleScript -----------
        if dry_run:
            summary = _dry_run_forward_summary(
                to=list(to),
                subject=fwd_subject,
                body=full_body,
                attachments=attach_list,
                dangerously_send=dangerously_send,
                original_message_id=message_id,
            )
            if json_mode:
                payload = {
                    "action": "dry-run",
                    "would_send": bool(dangerously_send),
                    "to": list(to),
                    "subject": fwd_subject,
                    "original_message_id": message_id,
                    "attachments": attach_list,
                }
                sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            else:
                sys.stdout.write(summary + "\n")
            raise typer.Exit(code=0)

        # --- Confirmation prompt (only when sending) --------------------
        if dangerously_send and not yes:
            confirmed = _prompt_send_confirmation(
                command="forward",
                to=list(to),
                subject=fwd_subject,
            )
            if not confirmed:
                sys.stdout.write("Send cancelled. No forward was sent.\n")
                raise typer.Exit(code=0)

        # --- Execute forward AppleScript ---------------------------------
        try:
            result = perform_forward(
                message_id=message_id,
                account=msg_account,
                mailbox=msg_mailbox,
                original=original,
                to=list(to),
                subject=fwd_subject,
                body=full_body,
                attachments=attach_list,
                dangerously_send=dangerously_send,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable

        # --- Render result -----------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_forward_human_output(result)
