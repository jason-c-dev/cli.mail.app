"""Mark and move commands — triage your inbox without leaving the terminal.

This module implements two message-management subcommands:

- ``mailctl messages mark <message-ids> --read/--unread/--flagged/--unflagged``
  Changes the read or flagged status of one or more messages.

- ``mailctl messages move <message-ids> --to <mailbox>``
  Moves one or more messages to a target mailbox within the same account.

Architecture follows the established build / perform / register pattern:

- :func:`build_mark_messages_script` — generates batched AppleScript to
  set read/flagged status on one or more messages.
- :func:`build_move_messages_script` — generates batched AppleScript to
  move one or more messages to a target mailbox.
- :func:`perform_mark` — orchestrates mark via engine and returns result dict.
- :func:`perform_move` — orchestrates move via engine and returns result dict.
- :func:`register` — Typer command handlers (thin layer).

These are state-change operations (NOT send operations) and are NOT subject
to the ``--dangerously-send`` safety model.  They do not generate AppleScript
containing a ``send`` verb.
"""

from __future__ import annotations

import json
import sys
from typing import Any, List, Optional

import typer
from rich.console import Console

from mailctl import message_lookup
from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError, EXIT_GENERAL_ERROR, EXIT_USAGE_ERROR
from mailctl.output import handle_mail_error, render_error


def _escape_applescript_string(value: str) -> str:
    """Escape a string for inclusion inside an AppleScript double-quoted literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
# AppleScript generation — mark messages
# --------------------------------------------------------------------------- #


def build_mark_messages_script(
    *,
    locations: list[tuple[str, str, str]],
    read: bool | None = None,
    flagged: bool | None = None,
) -> str:
    """Return AppleScript that sets read/flagged status on messages.

    Parameters
    ----------
    locations:
        List of ``(message_id, account_name, mailbox_path)`` triples.
        Each message is looked up directly by id in its owning mailbox —
        no ``every mailbox of every account`` iteration, which is
        unreliable for system mailboxes (Notes → ``-1728``) and large
        IMAP mailboxes like Gmail's All Mail (``-1741``).
    read:
        If ``True`` set read status to true; if ``False`` set to false;
        if ``None`` leave unchanged.
    flagged:
        If ``True`` set flagged status to true; if ``False`` set to false;
        if ``None`` leave unchanged.

    Returns
    -------
    str
        A complete AppleScript string ready for ``osascript -e``.
    """
    set_lines: list[str] = []
    if read is not None:
        set_lines.append(
            f"set read status of targetMsg to {'true' if read else 'false'}"
        )
    if flagged is not None:
        set_lines.append(
            f"set flagged status of targetMsg to {'true' if flagged else 'false'}"
        )

    blocks: list[str] = []
    for message_id, account, mailbox in locations:
        acct = _escape_applescript_string(account)
        mbox = _escape_applescript_string(mailbox)
        block_body = "\n    ".join(set_lines) if set_lines else "-- no-op"
        blocks.append(
            f'set targetMsg to first message of mailbox {mbox} of account {acct} whose id is {message_id}\n'
            f'    {block_body}'
        )

    body = "\n    ".join(blocks) if blocks else "-- no messages"
    return f'''\
tell application "Mail"
    {body}
end tell'''


# --------------------------------------------------------------------------- #
# AppleScript generation — move messages
# --------------------------------------------------------------------------- #


def build_move_messages_script(
    *,
    locations: list[tuple[str, str, str]],
    target_mailbox: str,
) -> str:
    """Return AppleScript that moves messages to a target mailbox.

    The target mailbox is resolved within each message's **own**
    account. Cross-account moves aren't supported by Mail.app's
    ``move`` verb; a message's account is determined by where it
    currently lives.

    Parameters
    ----------
    locations:
        List of ``(message_id, account_name, mailbox_path)`` triples.
    target_mailbox:
        The name of the destination mailbox (e.g. ``Archive``,
        ``Trash``). Resolved against each message's own account.
    """
    mbox_target = _escape_applescript_string(target_mailbox)

    blocks: list[str] = []
    for message_id, account, mailbox in locations:
        acct = _escape_applescript_string(account)
        src_mbox = _escape_applescript_string(mailbox)
        blocks.append(
            f'set targetMsg to first message of mailbox {src_mbox} of account {acct} whose id is {message_id}\n'
            f'    move targetMsg to mailbox {mbox_target} of account {acct}'
        )

    body = "\n    ".join(blocks) if blocks else "-- no messages"
    return f'''\
tell application "Mail"
    {body}
end tell'''


# --------------------------------------------------------------------------- #
# Perform operations — orchestration layer
# --------------------------------------------------------------------------- #


def perform_mark(
    *,
    message_ids: list[str],
    read: bool | None = None,
    flagged: bool | None = None,
) -> dict[str, Any]:
    """Execute the mark operation via AppleScript.

    Resolves each ID to its owning account + mailbox via SQLite, then
    generates targeted AppleScript (no mailbox iteration). Raises
    :class:`AppleScriptError` if any ID cannot be resolved or if the
    AppleScript call fails.
    """
    locations = [
        (mid, *message_lookup.resolve_message_location(mid))
        for mid in message_ids
    ]
    script = build_mark_messages_script(
        locations=locations,
        read=read,
        flagged=flagged,
    )
    run_applescript(script)

    changes: dict[str, bool] = {}
    if read is not None:
        changes["read"] = read
    if flagged is not None:
        changes["flagged"] = flagged

    return {
        "action": "mark",
        "message_ids": list(message_ids),
        "changes": changes,
    }


def _verify_target_mailbox(account: str, target_mailbox: str) -> None:
    """Raise :class:`AppleScriptError` if *target_mailbox* does not exist in
    *account*.

    Uses the Envelope Index (SQLite) rather than AppleScript because
    Mail.app's ``move`` verb silently accepts a non-existent mailbox
    for Gmail accounts and creates a label — leaving the user with a
    duplicated message they didn't ask for. A pre-flight check blocks
    that path and surfaces a useful error listing valid names.
    """
    from mailctl.account_map import uuid_for_name
    from mailctl.sqlite_engine import resolve_target_mailboxes, run_query

    account_uuid = uuid_for_name(account)
    if account_uuid is None:
        # Shouldn't happen — resolver returned this account name — but
        # guard anyway.
        raise AppleScriptError(
            f'Account "{account}" not found for move target validation.'
        )

    storage, labels = resolve_target_mailboxes(
        account_uuid=account_uuid,
        mailbox_name=target_mailbox,
    )
    if storage or labels:
        return

    # Produce an actionable error listing the account's actual mailboxes.
    rows = run_query(
        "SELECT url FROM mailboxes WHERE url LIKE ? OR url LIKE ? OR url LIKE ?",
        (
            f"imap://{account_uuid}/%",
            f"ews://{account_uuid}/%",
            f"local://{account_uuid}/%",
        ),
    )
    from mailctl.sqlite_engine import parse_mailbox_url
    names = sorted({
        parse_mailbox_url(r["url"] or "")[2].rsplit("/", 1)[-1]
        for r in rows
    } - {""})
    suggestion = ", ".join(names) if names else "(none)"
    raise AppleScriptError(
        f'Mailbox "{target_mailbox}" not found in account "{account}". '
        f"Mailboxes in that account: {suggestion}."
    )


def perform_move(
    *,
    message_ids: list[str],
    target_mailbox: str,
) -> dict[str, Any]:
    """Execute the move operation via AppleScript.

    Resolves each ID to its owning account + mailbox. The destination
    is resolved within each message's own account — cross-account
    moves aren't supported by Mail.app. Pre-flights the target mailbox
    via SQLite before running AppleScript so a missing target can't
    silently duplicate into a Gmail label or succeed-with-no-effect.
    """
    locations = [
        (mid, *message_lookup.resolve_message_location(mid))
        for mid in message_ids
    ]
    # Validate target mailbox per unique source account before doing
    # anything. We want the error to fire before any AppleScript runs.
    for account in {acct for (_mid, acct, _mbox) in locations}:
        _verify_target_mailbox(account, target_mailbox)
    script = build_move_messages_script(
        locations=locations,
        target_mailbox=target_mailbox,
    )
    run_applescript(script)

    return {
        "action": "move",
        "message_ids": list(message_ids),
        "target_mailbox": target_mailbox,
    }


# --------------------------------------------------------------------------- #
# Human-readable output helpers
# --------------------------------------------------------------------------- #


def _render_mark_human(result: dict[str, Any], *, no_color: bool = False) -> None:
    """Print a human-readable confirmation for a mark operation."""
    console = Console(no_color=no_color)
    ids = ", ".join(result["message_ids"])
    changes = result["changes"]
    parts: list[str] = []
    if "read" in changes:
        parts.append("read" if changes["read"] else "unread")
    if "flagged" in changes:
        parts.append("flagged" if changes["flagged"] else "unflagged")
    change_desc = " and ".join(parts)
    console.print(f"Marked message(s) {ids} as {change_desc}.")


def _render_move_human(result: dict[str, Any], *, no_color: bool = False) -> None:
    """Print a human-readable confirmation for a move operation."""
    console = Console(no_color=no_color)
    ids = ", ".join(result["message_ids"])
    target = result["target_mailbox"]
    console.print(f"Moved message(s) {ids} to {target}.")


# --------------------------------------------------------------------------- #
# Dry-run output helpers
# --------------------------------------------------------------------------- #


def _render_mark_dry_run(
    message_ids: list[str],
    *,
    read: bool | None,
    flagged: bool | None,
    no_color: bool = False,
) -> None:
    """Print what a mark operation WOULD do."""
    console = Console(no_color=no_color)
    ids = ", ".join(message_ids)
    parts: list[str] = []
    if read is not None:
        parts.append("read" if read else "unread")
    if flagged is not None:
        parts.append("flagged" if flagged else "unflagged")
    change_desc = " and ".join(parts)
    console.print(f"[dry-run] Would mark message(s) {ids} as {change_desc}.")


def _render_move_dry_run(
    message_ids: list[str],
    *,
    target_mailbox: str,
    no_color: bool = False,
) -> None:
    """Print what a move operation WOULD do."""
    console = Console(no_color=no_color)
    ids = ", ".join(message_ids)
    console.print(f"[dry-run] Would move message(s) {ids} to {target_mailbox}.")


# --------------------------------------------------------------------------- #
# Typer command handlers
# --------------------------------------------------------------------------- #


def register(messages_app: typer.Typer) -> None:
    """Register the ``messages mark`` and ``messages move`` commands."""

    @messages_app.command(
        "mark",
        help="Mark messages as read/unread or flagged/unflagged.",
    )
    def messages_mark(
        ctx: typer.Context,
        message_ids: List[str] = typer.Argument(
            ...,
            help="One or more message IDs to update.",
        ),
        read: bool = typer.Option(
            False, "--read",
            help="Mark message(s) as read.",
        ),
        unread: bool = typer.Option(
            False, "--unread",
            help="Mark message(s) as unread.",
        ),
        flagged: bool = typer.Option(
            False, "--flagged",
            help="Mark message(s) as flagged.",
        ),
        unflagged: bool = typer.Option(
            False, "--unflagged",
            help="Mark message(s) as unflagged.",
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope to a specific account name.",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Show what would be changed without executing.",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Mark one or more messages as read/unread and/or flagged/unflagged."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        # -- Validation: at least one flag required ----------------------------
        if not any([read, unread, flagged, unflagged]):
            render_error(
                "At least one flag is required: --read, --unread, --flagged, or --unflagged.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Validation: contradictory flags -----------------------------------
        if read and unread:
            render_error(
                "Contradictory flags: --read and --unread cannot be used together.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        if flagged and unflagged:
            render_error(
                "Contradictory flags: --flagged and --unflagged cannot be used together.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Resolve flags to tri-state values ---------------------------------
        read_value: bool | None = None
        if read:
            read_value = True
        elif unread:
            read_value = False

        flagged_value: bool | None = None
        if flagged:
            flagged_value = True
        elif unflagged:
            flagged_value = False

        # -- Dry-run -----------------------------------------------------------
        if dry_run:
            if json_mode:
                dry_result = {
                    "action": "mark",
                    "dry_run": True,
                    "message_ids": list(message_ids),
                    "changes": {},
                }
                if read_value is not None:
                    dry_result["changes"]["read"] = read_value
                if flagged_value is not None:
                    dry_result["changes"]["flagged"] = flagged_value
                sys.stdout.write(json.dumps(dry_result, indent=2) + "\n")
            else:
                _render_mark_dry_run(
                    list(message_ids),
                    read=read_value,
                    flagged=flagged_value,
                    no_color=no_color,
                )
            raise typer.Exit(0)

        # -- Execute -----------------------------------------------------------
        try:
            result = perform_mark(
                message_ids=list(message_ids),
                read=read_value,
                flagged=flagged_value,
            )
        except AppleScriptError as exc:
            # Provide clear message-not-found error if applicable.
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
            if "not found" in exc_str:
                ids = ", ".join(message_ids)
                render_error(
                    f'Message(s) "{ids}" not found. '
                    f"Verify message IDs with 'mailctl messages list'.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_GENERAL_ERROR)
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        # -- Output ------------------------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_mark_human(result, no_color=no_color)

    @messages_app.command(
        "move",
        help="Move messages to a different mailbox.",
    )
    def messages_move(
        ctx: typer.Context,
        message_ids: List[str] = typer.Argument(
            ...,
            help="One or more message IDs to move.",
        ),
        to: Optional[str] = typer.Option(
            None, "--to",
            help="Target mailbox name (required).",
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope to a specific account name.",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Show what would be moved without executing.",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Move one or more messages to a target mailbox."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        # -- Validation: --to is required --------------------------------------
        if not to:
            render_error(
                "--to is required. Specify the target mailbox name.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Dry-run -----------------------------------------------------------
        if dry_run:
            if json_mode:
                dry_result = {
                    "action": "move",
                    "dry_run": True,
                    "message_ids": list(message_ids),
                    "target_mailbox": to,
                }
                sys.stdout.write(json.dumps(dry_result, indent=2) + "\n")
            else:
                _render_move_dry_run(
                    list(message_ids),
                    target_mailbox=to,
                    no_color=no_color,
                )
            raise typer.Exit(0)

        # -- Execute -----------------------------------------------------------
        try:
            result = perform_move(
                message_ids=list(message_ids),
                target_mailbox=to,
            )
        except AppleScriptError as exc:
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
            # Preferred path: the SQLite pre-flight validator (see
            # `_verify_target_mailbox`) produces a message of the
            # shape `Mailbox "X" not found in account "Y". Mailboxes
            # in that account: ...`. That's already actionable — pass
            # it through unmodified rather than rewrapping it in the
            # generic "not found" text.
            if "not found in account" in exc_str:
                render_error(str(exc), no_color=no_color)
                raise typer.Exit(code=EXIT_GENERAL_ERROR)
            # Fallback: generic mailbox-not-found from AppleScript
            # (shouldn't happen post-validator, but belt-and-braces).
            if "mailbox" in exc_str and ("not found" in exc_str or "doesn't exist" in exc_str or "can't get" in exc_str):
                render_error(
                    f'Mailbox "{to}" not found. '
                    f"Use 'mailctl mailboxes list' to see available mailboxes.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_GENERAL_ERROR)
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        # -- Output ------------------------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_move_human(result, no_color=no_color)
