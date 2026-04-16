"""Mailboxes command — list Mail.app mailboxes.

Generates AppleScript to query mailboxes across all accounts in a single
``osascript`` call, parses the delimited output, and renders via the shared
output module.

Architecture:
- ``build_mailboxes_script()`` — generates the AppleScript string
- ``parse_mailboxes_output()`` — turns raw osascript stdout into dicts
- ``fetch_mailboxes()`` — orchestrates script + engine + parse
- ``mailboxes_list()`` — Typer command handler (thin layer)
"""

from __future__ import annotations

import sys

import typer

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError, EXIT_USAGE_ERROR
from mailctl.output import (
    ColumnDef,
    handle_mail_error,
    render_error,
    render_output,
)


# --------------------------------------------------------------------------- #
# AppleScript generation
# --------------------------------------------------------------------------- #

def build_mailboxes_script() -> str:
    """Return AppleScript that lists all mailboxes across all accounts.

    Output format (one line per mailbox, ``||``-delimited)::

        AccountName||MailboxName||unread_count||message_count
    """
    return '''\
tell application "Mail"
    set output to ""
    set allAccts to every account
    repeat with acct in allAccts
        set acctName to name of acct
        set mboxes to every mailbox of acct
        repeat with mbox in mboxes
            set mboxName to name of mbox
            set mboxUnread to unread count of mbox
            set mboxCount to count of messages of mbox
            if output is not "" then set output to output & linefeed
            set output to output & acctName & "||" & mboxName & "||" & (mboxUnread as string) & "||" & (mboxCount as string)
        end repeat
    end repeat
    return output
end tell'''


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_mailboxes_output(raw: str) -> list[dict]:
    """Parse the ``||``-delimited mailbox output into structured data.

    Returns a list of dicts with keys:
    ``account``, ``name``, ``unread_count``, ``message_count``.
    """
    if not raw.strip():
        return []

    mailboxes: list[dict] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) >= 4:
            mailboxes.append({
                "account": parts[0].strip(),
                "name": parts[1].strip(),
                "unread_count": int(parts[2].strip()),
                "message_count": int(parts[3].strip()),
            })
    return mailboxes


# --------------------------------------------------------------------------- #
# Data fetching (engine integration)
# --------------------------------------------------------------------------- #

def fetch_mailboxes() -> list[dict]:
    """Fetch all mailboxes across all accounts via a single AppleScript call.

    Returns a list of mailbox dicts.  Raises :class:`AppleScriptError`
    (or a subclass) on failure.
    """
    script = build_mailboxes_script()
    raw = run_applescript(script)
    return parse_mailboxes_output(raw)


# --------------------------------------------------------------------------- #
# Table column definitions
# --------------------------------------------------------------------------- #

MAILBOXES_COLUMNS = [
    ColumnDef(header="Account", key="account", max_width=25),
    ColumnDef(header="Mailbox", key="name", max_width=30),
    ColumnDef(header="Unread", key="unread_count", justify="right", max_width=10),
    ColumnDef(header="Messages", key="message_count", justify="right", max_width=10),
]


# --------------------------------------------------------------------------- #
# Typer command handler
# --------------------------------------------------------------------------- #

def register(mailboxes_app: typer.Typer) -> None:
    """Register the ``mailboxes list`` command on *mailboxes_app*."""

    @mailboxes_app.command("list", help="List mailboxes across accounts.")
    def mailboxes_list(
        ctx: typer.Context,
        account: str | None = typer.Option(
            None, "--account", "-a", help="Filter to a specific account name."
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Output results as JSON."
        ),
    ) -> None:
        """List mailboxes with unread and message counts."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        try:
            data = fetch_mailboxes()
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        # Filter by account if requested.
        if account is not None:
            # Check the account actually exists in the data.
            known_accounts = {m["account"] for m in data}
            if account not in known_accounts:
                render_error(
                    f"Account '{account}' not found. "
                    f"Known accounts: {', '.join(sorted(known_accounts)) or '(none)'}",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)

            data = [m for m in data if m["account"] == account]

        render_output(
            data,
            MAILBOXES_COLUMNS,
            json_mode=json_mode,
            no_color=no_color,
            title="Mailboxes",
        )
