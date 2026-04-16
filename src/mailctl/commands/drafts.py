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


def build_drafts_list_script(account: str | None = None) -> str:
    """Return AppleScript that enumerates drafts across accounts.

    Output format (one line per draft, ``||``-delimited)::

        account_name||draft_id||date||to||subject

    Uses indexed access and per-message try blocks to tolerate
    half-synced or missing-value messages (same pattern as messages list).
    """
    account_filter = ""
    if account:
        account_filter = f'if (name of acct) is not "{account}" then skipMe\n'

    return f'''\
tell application "Mail"
    set allAccts to every account
    set output to ""
    repeat with acct in allAccts
        set skipMe to false
        {account_filter}
        if not skipMe then
            try
                set dBox to mailbox "Drafts" of acct
                set acctName to name of acct
                set msgCount to count of messages of dBox
                set upperBound to 200
                if msgCount < upperBound then set upperBound to msgCount
                if upperBound >= 1 then
                    set msgs to messages 1 thru upperBound of dBox
                    repeat with msg in msgs
                        try
                            set msgId to id of msg as string
                        on error
                            set msgId to ""
                        end try
                        try
                            set msgDate to date received of msg as string
                        on error
                            set msgDate to ""
                        end try
                        try
                            set msgSubject to subject of msg as string
                        on error
                            set msgSubject to "(no subject)"
                        end try
                        set toList to ""
                        try
                            repeat with addr in (every to recipient of msg)
                                if toList is not "" then set toList to toList & ", "
                                try
                                    set toList to toList & (address of addr as string)
                                on error
                                    set toList to toList & "?"
                                end try
                            end repeat
                        on error
                            set toList to ""
                        end try
                        if msgId is not "" then
                            if output is not "" then set output to output & linefeed
                            set output to output & acctName & "||" & msgId & "||" & msgDate & "||" & toList & "||" & msgSubject
                        end if
                    end repeat
                end if
            on error
                -- Account has no Drafts mailbox; skip silently.
            end try
        end if
    end repeat
    return output
end tell'''


def parse_drafts_list_output(raw: str) -> list[dict]:
    """Parse the ``||``-delimited drafts output into structured data."""
    if not raw.strip():
        return []

    drafts: list[dict] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) >= 5:
            drafts.append({
                "account": parts[0].strip(),
                "id": parts[1].strip(),
                "date": parts[2].strip(),
                "to": parts[3].strip(),
                "subject": parts[4].strip(),
            })
    return drafts


def fetch_drafts_via_applescript(account: str | None = None) -> list[dict]:
    """Legacy AppleScript fetch. Retained as a fallback path."""
    script = build_drafts_list_script(account=account)
    raw = run_applescript(script, timeout=60.0)
    return parse_drafts_list_output(raw)


def fetch_drafts(account: str | None = None) -> list[dict]:
    """Fetch drafts from Mail.app's Envelope Index (SQLite).

    Finds every mailbox whose URL path ends in ``Drafts`` and lists the
    messages in it. Returns the same dict shape as the legacy fetch —
    keys ``account``, ``id``, ``date``, ``to``, ``subject``.
    """
    from mailctl.sqlite_engine import run_query, parse_mailbox_url
    from mailctl.account_map import uuid_for_name, name_for_uuid

    where = ["mb.url LIKE '%/Drafts'"]
    params: list = []

    if account:
        uuid = uuid_for_name(account)
        if uuid is None:
            return []
        where.append("(mb.url LIKE ? OR mb.url LIKE ? OR mb.url LIKE ?)")
        params.extend([
            f"imap://{uuid}/%",
            f"ews://{uuid}/%",
            f"local://{uuid}/%",
        ])

    sql = f"""
        SELECT m.ROWID          AS id,
               m.date_received  AS date_received,
               s.subject        AS subject,
               m.subject_prefix AS subject_prefix,
               mb.url           AS mailbox_url
        FROM messages m
        JOIN mailboxes mb ON mb.ROWID = m.mailbox
        LEFT JOIN subjects s ON s.ROWID = m.subject
        WHERE m.deleted = 0
          AND {' AND '.join(where)}
        ORDER BY m.date_received DESC
    """
    rows = run_query(sql, tuple(params))

    # Fetch To-recipients per draft in one batched query.
    draft_ids = [row["id"] for row in rows]
    to_map: dict[int, list[str]] = {}
    if draft_ids:
        placeholders = ",".join("?" * len(draft_ids))
        recipient_rows = run_query(
            f"""
            SELECT r.message  AS mid,
                   a.address  AS address,
                   a.comment  AS comment
            FROM recipients r
            JOIN addresses a ON a.ROWID = r.address
            WHERE r.type = 0
              AND r.message IN ({placeholders})
            ORDER BY r.message, r.position
            """,
            tuple(draft_ids),
        )
        for r in recipient_rows:
            rendered = r["comment"] + " <" + r["address"] + ">" if r["comment"] else r["address"]
            to_map.setdefault(int(r["mid"]), []).append(rendered)

    results: list[dict] = []
    for row in rows:
        _, acct_uuid, _ = parse_mailbox_url(row["mailbox_url"] or "")
        subject = (row["subject_prefix"] or "") + (row["subject"] or "")
        results.append({
            "account": name_for_uuid(acct_uuid) if acct_uuid else "",
            "id": str(row["id"]),
            "date": _format_unix_date_drafts(row["date_received"]),
            "to": ", ".join(to_map.get(int(row["id"]), [])),
            "subject": subject.strip(),
        })
    return results


def _format_unix_date_drafts(ts: int | None) -> str:
    if ts is None:
        return ""
    from datetime import datetime
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def register(drafts_app: typer.Typer) -> None:
    """Register the ``drafts list`` and ``drafts edit`` commands."""

    @drafts_app.command(
        "list",
        help="List drafts across accounts (or scoped to one with --account).",
    )
    def drafts_list(
        ctx: typer.Context,
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope to a specific account name.",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """List draft messages from Mail.app."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        try:
            data = fetch_drafts(account=account)
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        if json_mode:
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
            return

        if not data:
            Console(no_color=no_color).print("No drafts found.")
            return

        from mailctl.output import ColumnDef, render_output
        cols = [
            ColumnDef(header="Account", key="account", max_width=20),
            ColumnDef(header="ID", key="id", max_width=12),
            ColumnDef(header="Date", key="date", max_width=30),
            ColumnDef(header="To", key="to", max_width=35),
            ColumnDef(header="Subject", key="subject", max_width=50),
        ]
        render_output(
            data, cols,
            json_mode=False, no_color=no_color,
            title="Drafts",
        )

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
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
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
