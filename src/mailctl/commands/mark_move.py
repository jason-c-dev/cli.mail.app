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

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError, EXIT_GENERAL_ERROR, EXIT_USAGE_ERROR
from mailctl.output import handle_mail_error, render_error


# --------------------------------------------------------------------------- #
# AppleScript generation — mark messages
# --------------------------------------------------------------------------- #


def build_mark_messages_script(
    *,
    message_ids: list[str],
    read: bool | None = None,
    flagged: bool | None = None,
    account: str | None = None,
) -> str:
    """Return AppleScript that sets read/flagged status on messages.

    Parameters
    ----------
    message_ids:
        One or more message IDs to update.
    read:
        If ``True`` set read status to true; if ``False`` set to false;
        if ``None`` leave unchanged.
    flagged:
        If ``True`` set flagged status to true; if ``False`` set to false;
        if ``None`` leave unchanged.
    account:
        If provided, scope the message lookup to this account.

    Returns
    -------
    str
        A complete AppleScript string ready for ``osascript -e``.
    """
    # Build the property-setting lines
    set_lines: list[str] = []
    if read is not None:
        set_lines.append(
            f"set read status of msg to {'true' if read else 'false'}"
        )
    if flagged is not None:
        set_lines.append(
            f"set flagged status of msg to {'true' if flagged else 'false'}"
        )

    set_block = "\n            ".join(set_lines)

    # Build the message search scope
    if account:
        scope = f'every mailbox of account "{account}"'
    else:
        scope = "every mailbox of every account"

    # Build list of IDs as AppleScript list
    id_literals = ", ".join(f'"{mid}"' for mid in message_ids)

    return f'''\
tell application "Mail"
    set targetIds to {{{id_literals}}}
    repeat with mbox in ({scope})
        set msgs to every message of mbox
        repeat with msg in msgs
            set msgId to id of msg as string
            if targetIds contains msgId then
                {set_block}
            end if
        end repeat
    end repeat
end tell'''


# --------------------------------------------------------------------------- #
# AppleScript generation — move messages
# --------------------------------------------------------------------------- #


def build_move_messages_script(
    *,
    message_ids: list[str],
    target_mailbox: str,
    account: str | None = None,
) -> str:
    """Return AppleScript that moves messages to a target mailbox.

    Parameters
    ----------
    message_ids:
        One or more message IDs to move.
    target_mailbox:
        The name of the mailbox to move messages to.
    account:
        If provided, scope both source and target to this account.

    Returns
    -------
    str
        A complete AppleScript string ready for ``osascript -e``.
    """
    # Build the message search scope and target
    if account:
        scope = f'every mailbox of account "{account}"'
        target = f'mailbox "{target_mailbox}" of account "{account}"'
    else:
        scope = "every mailbox of every account"
        target = f'mailbox "{target_mailbox}"'

    # Build list of IDs as AppleScript list
    id_literals = ", ".join(f'"{mid}"' for mid in message_ids)

    return f'''\
tell application "Mail"
    set targetIds to {{{id_literals}}}
    set destMailbox to {target}
    repeat with mbox in ({scope})
        set msgs to every message of mbox
        repeat with msg in msgs
            set msgId to id of msg as string
            if targetIds contains msgId then
                move msg to destMailbox
            end if
        end repeat
    end repeat
end tell'''


# --------------------------------------------------------------------------- #
# Perform operations — orchestration layer
# --------------------------------------------------------------------------- #


def perform_mark(
    *,
    message_ids: list[str],
    read: bool | None = None,
    flagged: bool | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Execute the mark operation via AppleScript.

    Returns a result dict describing what was done.
    Raises :class:`AppleScriptError` on failure.
    """
    script = build_mark_messages_script(
        message_ids=message_ids,
        read=read,
        flagged=flagged,
        account=account,
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


def perform_move(
    *,
    message_ids: list[str],
    target_mailbox: str,
    account: str | None = None,
) -> dict[str, Any]:
    """Execute the move operation via AppleScript.

    Returns a result dict describing what was done.
    Raises :class:`AppleScriptError` on failure.
    """
    script = build_move_messages_script(
        message_ids=message_ids,
        target_mailbox=target_mailbox,
        account=account,
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
                account=account,
            )
        except AppleScriptError as exc:
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
                account=account,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        # -- Output ------------------------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_move_human(result, no_color=no_color)
