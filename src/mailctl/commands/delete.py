"""Delete command — move messages to Trash or permanently delete.

This module implements ``mailctl messages delete``, which is safe by default:
the default behaviour moves messages to the Trash mailbox (reversible).
Permanent deletion requires ``--permanent`` AND interactive confirmation
(default No), matching the safety pattern from compose's confirmation prompt.

Architecture follows the established build / perform / register pattern:

- :func:`build_delete_messages_script` — generates batched AppleScript to
  move/delete one or more messages.
- :func:`perform_delete` — orchestrates via engine and returns result dict.
- :func:`register` — thin Typer wrapper.

Safety notes:
- Default delete = move to Trash (non-destructive, reversible)
- ``--permanent`` = permanent deletion (requires confirmation)
- ``--permanent --yes`` = skip confirmation prompt
- ``--yes`` alone does nothing (still moves to Trash, no prompt shown)
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
# AppleScript generation — delete messages (move to Trash)
# --------------------------------------------------------------------------- #


def build_delete_messages_script(
    *,
    message_ids: list[str],
    permanent: bool = False,
    account: str | None = None,
) -> str:
    """Return AppleScript that deletes messages.

    When *permanent* is ``False`` (default), messages are moved to the Trash
    mailbox — this is a non-destructive, reversible operation.

    When *permanent* is ``True``, messages are permanently deleted using
    the AppleScript ``delete`` verb.

    Parameters
    ----------
    message_ids:
        One or more message IDs to delete.
    permanent:
        If ``True``, permanently delete; if ``False``, move to Trash.
    account:
        If provided, scope the message lookup to this account.
    """
    # Build the message search scope
    if account:
        scope = f'every mailbox of account "{account}"'
    else:
        scope = "every mailbox of every account"

    # Build list of IDs as AppleScript list
    id_literals = ", ".join(f'"{mid}"' for mid in message_ids)

    if permanent:
        # Permanent delete uses the AppleScript `delete` verb directly
        action_line = "delete msg"
    else:
        # Default: move to Trash (safe, reversible)
        if account:
            action_line = f'move msg to mailbox "Trash" of account "{account}"'
        else:
            action_line = 'set acctName to name of account of mbox\n                move msg to mailbox "Trash" of account acctName'

    return f'''\
tell application "Mail"
    set targetIds to {{{id_literals}}}
    repeat with mbox in ({scope})
        set msgs to every message of mbox
        repeat with msg in msgs
            set msgId to id of msg as string
            if targetIds contains msgId then
                {action_line}
            end if
        end repeat
    end repeat
end tell'''


# --------------------------------------------------------------------------- #
# Perform operation — orchestration layer
# --------------------------------------------------------------------------- #


def perform_delete(
    *,
    message_ids: list[str],
    permanent: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """Execute the delete operation via AppleScript.

    Returns a result dict describing what was done.
    Raises :class:`AppleScriptError` on failure.
    """
    script = build_delete_messages_script(
        message_ids=message_ids,
        permanent=permanent,
        account=account,
    )
    run_applescript(script)

    return {
        "action": "deleted" if permanent else "trashed",
        "message_ids": list(message_ids),
        "permanent": permanent,
    }


# --------------------------------------------------------------------------- #
# Human-readable output helpers
# --------------------------------------------------------------------------- #


def _render_delete_human(result: dict[str, Any], *, no_color: bool = False) -> None:
    """Print a human-readable confirmation for a delete operation."""
    console = Console(no_color=no_color)
    ids = ", ".join(result["message_ids"])
    if result["permanent"]:
        console.print(f"Permanently deleted message(s) {ids}.")
    else:
        console.print(f"Moved message(s) {ids} to Trash.")


def _render_delete_dry_run(
    message_ids: list[str],
    *,
    permanent: bool,
    no_color: bool = False,
) -> None:
    """Print what a delete operation WOULD do."""
    console = Console(no_color=no_color)
    ids = ", ".join(message_ids)
    if permanent:
        console.print(f"[dry-run] Would permanently delete message(s) {ids}.")
    else:
        console.print(f"[dry-run] Would move message(s) {ids} to Trash.")


# --------------------------------------------------------------------------- #
# Confirmation prompt
# --------------------------------------------------------------------------- #


def _prompt_permanent_confirmation(
    message_ids: list[str],
) -> bool:
    """Ask the user to confirm permanent deletion.  Defaults to **No**.

    Only a literal ``y`` or ``yes`` (case-insensitive) returns True.
    """
    ids = ", ".join(message_ids)
    sys.stdout.write(
        f"About to PERMANENTLY DELETE message(s): {ids}\n"
        f"This cannot be undone.\n"
        f"Proceed? [y/N]: "
    )
    sys.stdout.flush()

    try:
        answer = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False

    answer = (answer or "").strip().lower()
    return answer in ("y", "yes")


# --------------------------------------------------------------------------- #
# Typer command handler
# --------------------------------------------------------------------------- #


def register(messages_app: typer.Typer) -> None:
    """Register the ``messages delete`` command."""

    @messages_app.command(
        "delete",
        help=(
            "Delete messages. Default behaviour moves to Trash (safe). "
            "Use --permanent for irreversible deletion (requires confirmation)."
        ),
    )
    def messages_delete(
        ctx: typer.Context,
        message_ids: List[str] = typer.Argument(
            ...,
            help="One or more message IDs to delete.",
        ),
        permanent: bool = typer.Option(
            False, "--permanent",
            help="Permanently delete (irreversible). Requires confirmation unless --yes.",
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y",
            help="Skip confirmation prompt for --permanent. Without --permanent, has no effect.",
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope to a specific account name.",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Show what would be deleted without executing.",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Delete one or more messages (move to Trash by default)."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        # -- Dry-run -----------------------------------------------------------
        if dry_run:
            if json_mode:
                dry_result = {
                    "action": "deleted" if permanent else "trashed",
                    "dry_run": True,
                    "message_ids": list(message_ids),
                    "permanent": permanent,
                }
                sys.stdout.write(json.dumps(dry_result, indent=2) + "\n")
            else:
                _render_delete_dry_run(
                    list(message_ids),
                    permanent=permanent,
                    no_color=no_color,
                )
            raise typer.Exit(0)

        # -- Confirmation for permanent delete ---------------------------------
        if permanent and not yes:
            confirmed = _prompt_permanent_confirmation(list(message_ids))
            if not confirmed:
                sys.stdout.write("Delete cancelled. No messages were deleted.\n")
                raise typer.Exit(code=0)

        # -- Execute -----------------------------------------------------------
        try:
            result = perform_delete(
                message_ids=list(message_ids),
                permanent=permanent,
                account=account,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        # -- Output ------------------------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_delete_human(result, no_color=no_color)
