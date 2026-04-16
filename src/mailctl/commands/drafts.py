"""Draft editing command — modify existing drafts in Mail.app.

This module implements ``mailctl drafts edit``, allowing users to update the
subject, body, recipients, and attachments of an existing draft without
resending.

Architecture follows the established build / perform / register pattern:

- :func:`build_edit_draft_script` — generates batched AppleScript to modify
  one or more properties of a draft message.
- :func:`perform_edit_draft` — orchestrates via engine and returns result dict.
- :func:`register` — thin Typer wrapper.

Safety note: this command does NOT include a send path.  There is no
``--dangerously-send`` on the edit command.  Editing a draft only modifies
its content; it never sends.  If the generated AppleScript contains a
``send`` verb, that is a bug.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.console import Console

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError, EXIT_GENERAL_ERROR, EXIT_USAGE_ERROR
from mailctl.output import handle_mail_error, render_error


# --------------------------------------------------------------------------- #
# AppleScript escaping (reuse from compose)
# --------------------------------------------------------------------------- #

def _escape_applescript_string(value: str) -> str:
    """Escape a Python string for safe inclusion in an AppleScript literal.

    Backslashes and double-quotes are escaped.  Newlines are converted to
    AppleScript ``& return &`` concatenation so multi-line bodies survive
    the journey into Mail.app.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in escaped:
        return f'"{escaped}"'
    lines = escaped.split("\n")
    quoted = [f'"{line}"' for line in lines]
    return " & return & ".join(quoted)


# --------------------------------------------------------------------------- #
# AppleScript generation — edit draft
# --------------------------------------------------------------------------- #

def build_edit_draft_script(
    *,
    message_id: str,
    subject: str | None = None,
    body: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    add_to: list[str] | None = None,
    remove_to: list[str] | None = None,
    attach: list[str] | None = None,
    remove_attach: list[str] | None = None,
) -> str:
    """Return AppleScript that modifies properties of a draft message.

    The generated script locates the message by ID across all drafts
    mailboxes, then applies the requested changes in a single script.

    This function NEVER generates a ``send`` verb — editing a draft is
    always a safe, non-sending operation.
    """
    edit_lines: list[str] = []

    if subject is not None:
        edit_lines.append(
            f"set subject of targetMsg to {_escape_applescript_string(subject)}"
        )

    if body is not None:
        edit_lines.append(
            f"set content of targetMsg to {_escape_applescript_string(body)}"
        )

    # Replace recipients (clear existing, add new)
    if to is not None:
        edit_lines.append("delete every to recipient of targetMsg")
        for addr in to:
            edit_lines.append(
                f'make new to recipient at end of to recipients of targetMsg '
                f'with properties {{address:{_escape_applescript_string(addr)}}}'
            )

    if cc is not None:
        edit_lines.append("delete every cc recipient of targetMsg")
        for addr in cc:
            edit_lines.append(
                f'make new cc recipient at end of cc recipients of targetMsg '
                f'with properties {{address:{_escape_applescript_string(addr)}}}'
            )

    if bcc is not None:
        edit_lines.append("delete every bcc recipient of targetMsg")
        for addr in bcc:
            edit_lines.append(
                f'make new bcc recipient at end of bcc recipients of targetMsg '
                f'with properties {{address:{_escape_applescript_string(addr)}}}'
            )

    # Incremental recipient additions
    if add_to is not None:
        for addr in add_to:
            edit_lines.append(
                f'make new to recipient at end of to recipients of targetMsg '
                f'with properties {{address:{_escape_applescript_string(addr)}}}'
            )

    # Incremental recipient removals
    if remove_to is not None:
        for addr in remove_to:
            escaped_addr = _escape_applescript_string(addr)
            edit_lines.append(
                f'set recipList to every to recipient of targetMsg\n'
                f'            repeat with r in recipList\n'
                f'                if address of r is {escaped_addr} then\n'
                f'                    delete r\n'
                f'                end if\n'
                f'            end repeat'
            )

    # Add attachments
    if attach is not None:
        for path in attach:
            escaped = _escape_applescript_string(path)
            edit_lines.append(
                f'make new attachment with properties '
                f'{{file name:(POSIX file {escaped})}} '
                f'at after the last paragraph of targetMsg'
            )

    # Remove attachments by name
    if remove_attach is not None:
        for name in remove_attach:
            escaped_name = _escape_applescript_string(name)
            edit_lines.append(
                f'set attachList to every mail attachment of targetMsg\n'
                f'            repeat with att in attachList\n'
                f'                if name of att is {escaped_name} then\n'
                f'                    delete att\n'
                f'                end if\n'
                f'            end repeat'
            )

    edit_block = "\n            ".join(edit_lines)

    return f'''\
tell application "Mail"
    set targetId to "{message_id}"
    repeat with mbox in (every mailbox of every account)
        set msgs to every message of mbox
        repeat with msg in msgs
            set msgId to id of msg as string
            if msgId is targetId then
                set targetMsg to msg
                {edit_block}
                return "OK"
            end if
        end repeat
    end repeat
    error "Message not found: " & targetId
end tell'''


# --------------------------------------------------------------------------- #
# Perform operation — orchestration layer
# --------------------------------------------------------------------------- #


def perform_edit_draft(
    *,
    message_id: str,
    subject: str | None = None,
    body: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    add_to: list[str] | None = None,
    remove_to: list[str] | None = None,
    attach: list[str] | None = None,
    remove_attach: list[str] | None = None,
) -> dict[str, Any]:
    """Execute the draft edit operation via AppleScript.

    Returns a result dict describing what was done.
    Raises :class:`AppleScriptError` on failure.
    """
    script = build_edit_draft_script(
        message_id=message_id,
        subject=subject,
        body=body,
        to=to,
        cc=cc,
        bcc=bcc,
        add_to=add_to,
        remove_to=remove_to,
        attach=attach,
        remove_attach=remove_attach,
    )
    run_applescript(script)

    changes: dict[str, Any] = {}
    if subject is not None:
        changes["subject"] = subject
    if body is not None:
        changes["body"] = body
    if to is not None:
        changes["to"] = to
    if cc is not None:
        changes["cc"] = cc
    if bcc is not None:
        changes["bcc"] = bcc
    if add_to is not None:
        changes["add_to"] = add_to
    if remove_to is not None:
        changes["remove_to"] = remove_to
    if attach is not None:
        changes["attach"] = attach
    if remove_attach is not None:
        changes["remove_attach"] = remove_attach

    return {
        "action": "edited",
        "message_id": message_id,
        "changes": changes,
    }


# --------------------------------------------------------------------------- #
# Human-readable output helpers
# --------------------------------------------------------------------------- #


def _render_edit_human(result: dict[str, Any], *, no_color: bool = False) -> None:
    """Print a human-readable confirmation for a draft edit operation."""
    console = Console(no_color=no_color)
    msg_id = result["message_id"]
    changes = result.get("changes", {})
    parts: list[str] = list(changes.keys())
    change_desc = ", ".join(parts) if parts else "no fields"
    console.print(f"Draft {msg_id} edited: updated {change_desc}.")


def _render_edit_dry_run(
    message_id: str,
    changes: dict[str, Any],
    *,
    no_color: bool = False,
) -> None:
    """Print what a draft edit operation WOULD do."""
    console = Console(no_color=no_color)
    parts: list[str] = list(changes.keys())
    change_desc = ", ".join(parts) if parts else "no fields"
    console.print(f"[dry-run] Would edit draft {message_id}: update {change_desc}.")


# --------------------------------------------------------------------------- #
# Typer command handlers
# --------------------------------------------------------------------------- #


def register(drafts_app: typer.Typer) -> None:
    """Register the ``drafts edit`` command."""

    @drafts_app.command(
        "edit",
        help=(
            "Edit an existing draft message. Modify subject, body, "
            "recipients, and attachments. Does NOT send the draft."
        ),
    )
    def drafts_edit(
        ctx: typer.Context,
        message_id: str = typer.Argument(
            ...,
            help="Message ID of the draft to edit.",
        ),
        subject: Optional[str] = typer.Option(
            None, "--subject",
            help="Set the draft's subject to this value.",
        ),
        body: Optional[str] = typer.Option(
            None, "--body",
            help="Replace the draft's body with this text.",
        ),
        body_file: Optional[str] = typer.Option(
            None, "--body-file",
            help="Read body text from a file path (mutually exclusive with --body).",
        ),
        to: Optional[List[str]] = typer.Option(
            None, "--to",
            help="Replace all To recipients (repeatable). Mutually exclusive with --add-to.",
        ),
        cc: Optional[List[str]] = typer.Option(
            None, "--cc",
            help="Replace all Cc recipients (repeatable).",
        ),
        bcc: Optional[List[str]] = typer.Option(
            None, "--bcc",
            help="Replace all Bcc recipients (repeatable).",
        ),
        add_to: Optional[List[str]] = typer.Option(
            None, "--add-to",
            help="Add a To recipient without clearing existing ones (repeatable). Mutually exclusive with --to.",
        ),
        remove_to: Optional[List[str]] = typer.Option(
            None, "--remove-to",
            help="Remove a specific To recipient by address (repeatable).",
        ),
        attach: Optional[List[str]] = typer.Option(
            None, "--attach",
            help="Add an attachment by file path (repeatable).",
        ),
        remove_attach: Optional[List[str]] = typer.Option(
            None, "--remove-attach",
            help="Remove an attachment by filename (repeatable).",
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
        """Edit an existing Mail.app draft by message ID."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        # -- Validation: --body and --body-file are mutually exclusive ---------
        if body is not None and body_file is not None:
            render_error(
                "--body and --body-file are mutually exclusive. Specify one, not both.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Validation: --to and --add-to are mutually exclusive ---------------
        if to is not None and add_to is not None:
            render_error(
                "--to and --add-to cannot be combined. --to replaces all recipients; "
                "--add-to adds incrementally.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Resolve body from --body-file if given ----------------------------
        resolved_body = body
        if body_file is not None:
            path = Path(body_file)
            if not path.is_file():
                render_error(
                    f"--body-file '{body_file}' does not exist or is not a file.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)
            resolved_body = path.read_text()

        # -- Validate attachments exist ----------------------------------------
        attach_list = list(attach) if attach else None
        if attach_list:
            for file_path in attach_list:
                if not Path(file_path).is_file():
                    render_error(
                        f"Attachment '{file_path}' does not exist or is not a file.",
                        no_color=no_color,
                    )
                    raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Normalize optional lists ------------------------------------------
        to_list = list(to) if to else None
        cc_list = list(cc) if cc else None
        bcc_list = list(bcc) if bcc else None
        add_to_list = list(add_to) if add_to else None
        remove_to_list = list(remove_to) if remove_to else None
        remove_attach_list = list(remove_attach) if remove_attach else None

        # -- Validation: at least one edit option required --------------------
        has_edit = any([
            subject is not None,
            resolved_body is not None,
            to_list is not None,
            cc_list is not None,
            bcc_list is not None,
            add_to_list is not None,
            remove_to_list is not None,
            attach_list is not None,
            remove_attach_list is not None,
        ])
        if not has_edit:
            render_error(
                "At least one edit option is required: --subject, --body, --body-file, "
                "--to, --cc, --bcc, --add-to, --remove-to, --attach, or --remove-attach.",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        # -- Build changes dict for dry-run / JSON ----------------------------
        changes: dict[str, Any] = {}
        if subject is not None:
            changes["subject"] = subject
        if resolved_body is not None:
            changes["body"] = resolved_body
        if to_list is not None:
            changes["to"] = to_list
        if cc_list is not None:
            changes["cc"] = cc_list
        if bcc_list is not None:
            changes["bcc"] = bcc_list
        if add_to_list is not None:
            changes["add_to"] = add_to_list
        if remove_to_list is not None:
            changes["remove_to"] = remove_to_list
        if attach_list is not None:
            changes["attach"] = attach_list
        if remove_attach_list is not None:
            changes["remove_attach"] = remove_attach_list

        # -- Dry-run -----------------------------------------------------------
        if dry_run:
            if json_mode:
                dry_result = {
                    "action": "edited",
                    "dry_run": True,
                    "message_id": message_id,
                    "changes": changes,
                }
                sys.stdout.write(json.dumps(dry_result, indent=2) + "\n")
            else:
                _render_edit_dry_run(
                    message_id,
                    changes,
                    no_color=no_color,
                )
            raise typer.Exit(0)

        # -- Execute -----------------------------------------------------------
        try:
            result = perform_edit_draft(
                message_id=message_id,
                subject=subject,
                body=resolved_body,
                to=to_list,
                cc=cc_list,
                bcc=bcc_list,
                add_to=add_to_list,
                remove_to=remove_to_list,
                attach=attach_list,
                remove_attach=remove_attach_list,
            )
        except AppleScriptError as exc:
            exc_str = str(exc).lower()
            if "not found" in exc_str or "message not found" in exc_str:
                render_error(
                    f'Message "{message_id}" not found. '
                    f"Verify the message ID with 'mailctl messages list'.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_GENERAL_ERROR)
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        # -- Output ------------------------------------------------------------
        if json_mode:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _render_edit_human(result, no_color=no_color)
