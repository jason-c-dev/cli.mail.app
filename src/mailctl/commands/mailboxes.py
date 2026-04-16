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

def build_mailboxes_script(account: str | None = None) -> str:
    """Return AppleScript that lists mailboxes, optionally filtered to one account.

    Output format (one line per mailbox, ``||``-delimited)::

        AccountName||MailboxName||unread_count||message_count

    Individual mailbox reads are wrapped in ``try``/``on error`` so a single
    unavailable mailbox (common with Exchange virtual folders) does not abort
    the whole enumeration. When *account* is supplied, the AppleScript only
    enumerates that account — avoiding slow enumeration of other accounts.
    """
    account_filter = ""
    if account:
        account_filter = f'\n        if (name of acct) is not "{account}" then skipMe'

    return f'''\
with timeout of 180 seconds
tell application "Mail"
    set output to ""
    set allAccts to every account
    repeat with acct in allAccts
        set skipMe to false{account_filter}
        if not skipMe then
            try
                set acctName to name of acct
                set mboxes to every mailbox of acct
                repeat with mbox in mboxes
                    try
                        set mboxName to name of mbox
                        try
                            set mboxUnread to unread count of mbox
                        on error
                            set mboxUnread to 0
                        end try
                        try
                            set mboxCount to count of messages of mbox
                        on error
                            set mboxCount to 0
                        end try
                        if output is not "" then set output to output & linefeed
                        set output to output & acctName & "||" & mboxName & "||" & (mboxUnread as string) & "||" & (mboxCount as string)
                    on error
                        -- Skip mailboxes that can't be read.
                    end try
                end repeat
            on error
                -- Skip accounts that can't be enumerated.
            end try
        end if
    end repeat
    return output
end tell
end timeout'''


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

def fetch_mailboxes_via_applescript(account: str | None = None) -> list[dict]:
    """Legacy AppleScript fetch. Retained as a fallback and for tests."""
    script = build_mailboxes_script(account=account)
    raw = run_applescript(script, timeout=180.0)
    return parse_mailboxes_output(raw)


def fetch_mailboxes(account: str | None = None) -> list[dict]:
    """Fetch mailboxes from Mail.app's Envelope Index (SQLite).

    Returns one dict per mailbox with keys ``account``, ``name``,
    ``unread_count``, ``message_count`` — identical shape to the legacy
    AppleScript fetch, so callers and renderers are unchanged.

    When *account* is given, filters to that account's UUID. Unknown
    accounts return an empty list; the Typer handler converts that into
    a usage error with the list of known accounts.
    """
    from mailctl.sqlite_engine import run_query, parse_mailbox_url
    from mailctl.account_map import get_account_map, uuid_for_name

    target_uuid = uuid_for_name(account) if account else None
    if account and target_uuid is None:
        from mailctl.account_map import get_account_map
        known = ", ".join(a.name for a in get_account_map()) or "(none)"
        raise AppleScriptError(
            f'Account "{account}" not found. Known accounts: {known}.'
        )

    # Exclude the soft-deleted counts from the visible total (total_count
    # includes deleted messages Mail hasn't fully purged).
    rows = run_query(
        """
        SELECT url,
               total_count - deleted_count AS visible_count,
               unread_count_adjusted_for_duplicates AS unread
        FROM mailboxes
        """
    )

    uuid_to_name = {a.uuid: a.name for a in get_account_map()}
    results: list[dict] = []
    for row in rows:
        scheme, uuid, path = parse_mailbox_url(row["url"])
        if target_uuid and uuid != target_uuid:
            continue
        acct_name = uuid_to_name.get(uuid)
        if not acct_name:
            # Orphan mailbox whose account AppleScript didn't return —
            # could be a disabled account or a stale entry. Skip.
            continue
        # Keep full provider-prefixed path so Gmail users can tell
        # "All Mail" from "[Gmail]/All Mail" if that distinction matters.
        # The last segment is what Mail.app shows in its sidebar.
        name = path.rsplit("/", 1)[-1] if path else path
        results.append({
            "account": acct_name,
            "name": name,
            "unread_count": int(row["unread"]),
            "message_count": int(row["visible_count"]),
        })

    # Sort: account name, then mailbox name. Makes diff/eyeball easy.
    results.sort(key=lambda m: (m["account"], m["name"]))
    return results


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
            data = fetch_mailboxes(account=account)
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        # Defence-in-depth: even when AppleScript filters by account, also
        # apply the filter client-side. This keeps behaviour correct if a
        # mock returns unfiltered data, and preserves the pre-existing
        # contract of raising a usage error for unknown accounts.
        if account is not None:
            known_accounts = {m["account"] for m in data}
            if data and account not in known_accounts:
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
