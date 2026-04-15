"""Accounts command — list Mail.app accounts.

Generates AppleScript to query all accounts in a single ``osascript`` call,
parses the delimited output, and renders via the shared output module.

Architecture:
- ``build_accounts_script()`` — generates the AppleScript string
- ``parse_accounts_output()`` — turns raw osascript stdout into dicts
- ``fetch_accounts()`` — orchestrates script + engine + parse
- ``accounts_list()`` — Typer command handler (thin layer)
"""

from __future__ import annotations

import typer

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError
from mailctl.output import ColumnDef, handle_mail_error, render_output


# --------------------------------------------------------------------------- #
# AppleScript generation
# --------------------------------------------------------------------------- #

def build_accounts_script() -> str:
    """Return AppleScript that lists all Mail.app accounts in one call.

    Output format (one line per account, ``||``-delimited)::

        AccountName||email1;email2||account type||true
    """
    return '''\
tell application "Mail"
    set output to ""
    set allAccts to every account
    repeat with acct in allAccts
        set acctName to name of acct
        set acctType to account type of acct as string
        set acctEnabled to enabled of acct
        set acctEmails to email addresses of acct
        set emailStr to ""
        repeat with e in acctEmails
            if emailStr is not "" then set emailStr to emailStr & ";"
            set emailStr to emailStr & (e as string)
        end repeat
        if output is not "" then set output to output & linefeed
        set output to output & acctName & "||" & emailStr & "||" & acctType & "||" & (acctEnabled as string)
    end repeat
    return output
end tell'''


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_accounts_output(raw: str) -> list[dict]:
    """Parse the ``||``-delimited account output into structured data.

    Returns a list of dicts with keys: ``name``, ``email``, ``type``, ``enabled``.
    """
    if not raw.strip():
        return []

    accounts: list[dict] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) >= 4:
            accounts.append({
                "name": parts[0].strip(),
                "email": parts[1].strip(),
                "type": parts[2].strip(),
                "enabled": parts[3].strip().lower() == "true",
            })
    return accounts


# --------------------------------------------------------------------------- #
# Data fetching (engine integration)
# --------------------------------------------------------------------------- #

def fetch_accounts() -> list[dict]:
    """Fetch all Mail.app accounts via a single AppleScript call.

    Returns a list of account dicts.  Raises :class:`AppleScriptError`
    (or a subclass) on failure.
    """
    script = build_accounts_script()
    raw = run_applescript(script)
    return parse_accounts_output(raw)


# --------------------------------------------------------------------------- #
# Table column definitions
# --------------------------------------------------------------------------- #

ACCOUNTS_COLUMNS = [
    ColumnDef(header="Account", key="name", max_width=30),
    ColumnDef(header="Email", key="email", max_width=40),
    ColumnDef(header="Type", key="type", max_width=15),
    ColumnDef(header="Enabled", key="enabled", max_width=10),
]


# --------------------------------------------------------------------------- #
# Typer command handler
# --------------------------------------------------------------------------- #

def register(accounts_app: typer.Typer) -> None:
    """Register the ``accounts list`` command on *accounts_app*."""

    @accounts_app.command("list", help="List all configured Mail.app accounts.")
    def accounts_list(
        ctx: typer.Context,
        json_output: bool = typer.Option(
            False, "--json", help="Output results as JSON."
        ),
    ) -> None:
        """List all accounts configured in Mail.app."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        try:
            data = fetch_accounts()
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        render_output(
            data,
            ACCOUNTS_COLUMNS,
            json_mode=json_mode,
            no_color=no_color,
            title="Mail.app Accounts",
        )
