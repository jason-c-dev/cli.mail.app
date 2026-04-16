"""Messages commands — list, show, and search Mail.app messages.

Generates AppleScript to query messages in a mailbox with filters (unread,
sender, subject, date range), parses the delimited output, and renders via
the shared output module.

Architecture:
- ``build_messages_list_script()`` — generates AppleScript to list messages
- ``parse_messages_list_output()`` — turns raw osascript stdout into dicts
- ``fetch_messages()`` — orchestrates script + engine + parse + filtering
- ``build_message_show_script()`` — generates AppleScript to show one message
- ``parse_message_show_output()`` — turns raw osascript stdout into a dict
- ``fetch_message()`` — orchestrates script + engine + parse for show
- ``build_account_names_script()`` — generates AppleScript to list account names
- ``build_search_script()`` — generates AppleScript to search one account
- ``parse_search_output()`` — turns raw search output into dicts
- ``fetch_search_results()`` — orchestrates cross-account search
- ``register()`` — Typer command handlers (thin layer)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError, EXIT_USAGE_ERROR
from mailctl.output import (
    ColumnDef,
    handle_mail_error,
    render_error,
    render_output,
)


# --------------------------------------------------------------------------- #
# AppleScript generation — list messages
# --------------------------------------------------------------------------- #

def build_messages_list_script(
    *,
    account: str | None = None,
    mailbox: str = "INBOX",
) -> str:
    """Return AppleScript that lists messages from a mailbox.

    Output format (one line per message, ``||``-delimited)::

        message_id||date||sender||subject||read_flag||flagged_flag

    The script fetches metadata for all messages in a single osascript call.
    Filtering by unread/sender/subject/date is done in Python after fetching,
    to keep the AppleScript simple and the batch call pattern intact.
    """
    if account:
        target = f'mailbox "{mailbox}" of account "{account}"'
    else:
        target = f'mailbox "{mailbox}"'

    return f'''\
tell application "Mail"
    set output to ""
    set msgs to every message of {target}
    repeat with msg in msgs
        set msgId to id of msg as string
        set msgDate to date received of msg as string
        set msgSender to sender of msg
        set msgSubject to subject of msg
        set msgRead to read status of msg as string
        set msgFlagged to flagged status of msg as string
        if output is not "" then set output to output & linefeed
        set output to output & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged
    end repeat
    return output
end tell'''


# --------------------------------------------------------------------------- #
# Parsing — list messages
# --------------------------------------------------------------------------- #

def parse_messages_list_output(raw: str) -> list[dict]:
    """Parse the ``||``-delimited message list output into structured data.

    Returns a list of dicts with keys:
    ``id``, ``date``, ``from``, ``subject``, ``read``, ``flagged``.
    """
    if not raw.strip():
        return []

    messages: list[dict] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) >= 6:
            messages.append({
                "id": parts[0].strip(),
                "date": parts[1].strip(),
                "from": parts[2].strip(),
                "subject": parts[3].strip(),
                "read": parts[4].strip().lower() == "true",
                "flagged": parts[5].strip().lower() == "true",
            })
    return messages


def _parse_date(date_str: str) -> datetime | None:
    """Best-effort parse of an AppleScript date string.

    AppleScript dates come in various locale-dependent formats.
    We try common patterns and return None on failure.
    """
    patterns = [
        "%A, %B %d, %Y at %I:%M:%S %p",   # Friday, January 10, 2025 at 9:30:00 AM
        "%A, %d %B %Y at %H:%M:%S",        # Friday, 10 January 2025 at 09:30:00
        "%Y-%m-%dT%H:%M:%S",               # ISO-8601
        "%Y-%m-%d %H:%M:%S",               # Standard datetime
        "%m/%d/%Y %H:%M:%S",               # US format
        "%d/%m/%Y %H:%M:%S",               # UK format
    ]
    for pattern in patterns:
        try:
            return datetime.strptime(date_str, pattern)
        except ValueError:
            continue
    # Last resort: try to extract a date-like pattern
    # e.g. "date \"Friday, January 10, 2025 at 9:30:00 AM\""
    cleaned = re.sub(r'^date\s+"?|"?\s*$', '', date_str)
    if cleaned != date_str:
        return _parse_date(cleaned)
    return None


def _apply_filters(
    messages: list[dict],
    *,
    unread: bool = False,
    from_filter: str | None = None,
    subject_filter: str | None = None,
    since: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Apply post-fetch filters to messages.

    All filters are applied conjunctively (AND).
    """
    result = messages

    if unread:
        result = [m for m in result if not m["read"]]

    if from_filter:
        lower_from = from_filter.lower()
        result = [m for m in result if lower_from in m["from"].lower()]

    if subject_filter:
        lower_subject = subject_filter.lower()
        result = [m for m in result if lower_subject in m["subject"].lower()]

    if since:
        since_date = datetime.strptime(since, "%Y-%m-%d")
        result = [m for m in result if _is_on_or_after(m["date"], since_date)]

    if before:
        before_date = datetime.strptime(before, "%Y-%m-%d")
        result = [m for m in result if _is_before(m["date"], before_date)]

    return result


def _is_on_or_after(date_str: str, threshold: datetime) -> bool:
    """Check if a date string represents a date on or after the threshold."""
    parsed = _parse_date(date_str)
    if parsed is None:
        return True  # Can't parse — include by default
    return parsed >= threshold


def _is_before(date_str: str, threshold: datetime) -> bool:
    """Check if a date string represents a date before the threshold."""
    parsed = _parse_date(date_str)
    if parsed is None:
        return True  # Can't parse — include by default
    return parsed < threshold


def _sort_by_date_descending(messages: list[dict]) -> list[dict]:
    """Sort messages by date descending (newest first).

    Messages with unparseable dates are placed at the end.
    """
    def sort_key(m: dict) -> tuple[int, datetime]:
        parsed = _parse_date(m["date"])
        if parsed is None:
            return (1, datetime.min)
        return (0, parsed)

    return sorted(messages, key=sort_key, reverse=True)


# --------------------------------------------------------------------------- #
# Data fetching — list messages
# --------------------------------------------------------------------------- #

DEFAULT_LIMIT = 25


def fetch_messages(
    *,
    account: str | None = None,
    mailbox: str = "INBOX",
    unread: bool = False,
    from_filter: str | None = None,
    subject_filter: str | None = None,
    since: str | None = None,
    before: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Fetch messages from a mailbox via a single AppleScript call.

    Returns a list of message dicts, filtered and sorted (newest first),
    capped at *limit*. Raises :class:`AppleScriptError` on failure.
    """
    script = build_messages_list_script(account=account, mailbox=mailbox)
    raw = run_applescript(script)
    messages = parse_messages_list_output(raw)

    # Apply post-fetch filters
    messages = _apply_filters(
        messages,
        unread=unread,
        from_filter=from_filter,
        subject_filter=subject_filter,
        since=since,
        before=before,
    )

    # Sort by date descending (newest first)
    messages = _sort_by_date_descending(messages)

    # Apply limit
    if limit > 0:
        messages = messages[:limit]

    return messages


# --------------------------------------------------------------------------- #
# AppleScript generation — show single message
# --------------------------------------------------------------------------- #

def build_message_show_script(message_id: str) -> str:
    """Return AppleScript that fetches a single message by ID.

    Output format (``||``-delimited, multi-section with ``@@HEADERS@@``
    and ``@@ATTACHMENTS@@`` separators)::

        id||date||from||to||cc||bcc||subject||read||flagged
        @@BODY@@
        <message body text>
        @@HEADERS@@
        Header-Name: value
        ...
        @@ATTACHMENTS@@
        filename||size||mime_type
        ...
    """
    return f'''\
tell application "Mail"
    set targetMsg to first message of mailbox "INBOX" whose id is {message_id}

    -- Core fields
    set msgId to id of targetMsg as string
    set msgDate to date received of targetMsg as string
    set msgFrom to sender of targetMsg
    set msgSubject to subject of targetMsg
    set msgRead to read status of targetMsg as string
    set msgFlagged to flagged status of targetMsg as string

    -- Recipients
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

    set bccList to ""
    repeat with addr in (every bcc recipient of targetMsg)
        if bccList is not "" then set bccList to bccList & ", "
        set bccList to bccList & (address of addr as string)
    end repeat

    -- Body
    set msgBody to content of targetMsg

    -- Headers
    set msgHeaders to all headers of targetMsg

    -- Attachments
    set attachOutput to ""
    set attachList to every mail attachment of targetMsg
    repeat with att in attachList
        set attName to name of att
        set attSize to downloaded size of att as string
        set attMime to MIME type of att
        if attachOutput is not "" then set attachOutput to attachOutput & linefeed
        set attachOutput to attachOutput & attName & "||" & attSize & "||" & attMime
    end repeat

    -- Compose output
    set headerLine to msgId & "||" & msgDate & "||" & msgFrom & "||" & toList & "||" & ccList & "||" & bccList & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged
    set output to headerLine & linefeed & "@@BODY@@" & linefeed & msgBody & linefeed & "@@HEADERS@@" & linefeed & msgHeaders & linefeed & "@@ATTACHMENTS@@" & linefeed & attachOutput

    return output
end tell'''


# --------------------------------------------------------------------------- #
# Parsing — show single message
# --------------------------------------------------------------------------- #

def parse_message_show_output(raw: str) -> dict:
    """Parse the structured output for a single message into a dict.

    Returns a dict with keys: ``id``, ``date``, ``from``, ``to``, ``cc``,
    ``bcc``, ``subject``, ``body``, ``headers``, ``attachments``, ``read``,
    ``flagged``.
    """
    sections = raw.split("@@BODY@@")
    if len(sections) < 2:
        # Minimal parse if format is unexpected
        return {"id": "", "date": "", "from": "", "to": "", "cc": "",
                "bcc": "", "subject": "", "body": raw, "headers": "",
                "attachments": [], "read": False, "flagged": False}

    header_line = sections[0].strip()
    rest = sections[1]

    # Parse header fields
    parts = header_line.split("||")
    msg: dict[str, Any] = {
        "id": parts[0].strip() if len(parts) > 0 else "",
        "date": parts[1].strip() if len(parts) > 1 else "",
        "from": parts[2].strip() if len(parts) > 2 else "",
        "to": parts[3].strip() if len(parts) > 3 else "",
        "cc": parts[4].strip() if len(parts) > 4 else "",
        "bcc": parts[5].strip() if len(parts) > 5 else "",
        "subject": parts[6].strip() if len(parts) > 6 else "",
        "read": (parts[7].strip().lower() == "true") if len(parts) > 7 else False,
        "flagged": (parts[8].strip().lower() == "true") if len(parts) > 8 else False,
    }

    # Split body, headers, attachments
    header_sections = rest.split("@@HEADERS@@")
    body_text = header_sections[0].strip() if header_sections else ""

    headers_and_attachments = header_sections[1] if len(header_sections) > 1 else ""
    attach_sections = headers_and_attachments.split("@@ATTACHMENTS@@")
    headers_text = attach_sections[0].strip() if attach_sections else ""
    attachments_text = attach_sections[1].strip() if len(attach_sections) > 1 else ""

    msg["body"] = body_text
    msg["headers"] = headers_text

    # Parse attachments
    attachments: list[dict] = []
    if attachments_text:
        for line in attachments_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            att_parts = line.split("||")
            if len(att_parts) >= 3:
                attachments.append({
                    "name": att_parts[0].strip(),
                    "size": att_parts[1].strip(),
                    "mime_type": att_parts[2].strip(),
                })
    msg["attachments"] = attachments

    return msg


# --------------------------------------------------------------------------- #
# Data fetching — show single message
# --------------------------------------------------------------------------- #

def fetch_message(message_id: str) -> dict:
    """Fetch a single message by ID via AppleScript.

    Returns a message dict with all fields.
    Raises :class:`AppleScriptError` on failure (including message not found).
    """
    script = build_message_show_script(message_id)
    raw = run_applescript(script)
    return parse_message_show_output(raw)


# --------------------------------------------------------------------------- #
# AppleScript generation — account names (for cross-account search)
# --------------------------------------------------------------------------- #

def build_account_names_script() -> str:
    """Return AppleScript that lists all account names.

    Output format: one account name per line.
    """
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
    """Parse account names output into a list of strings."""
    if not raw.strip():
        return []
    return [line.strip() for line in raw.strip().split("\n") if line.strip()]


# --------------------------------------------------------------------------- #
# AppleScript generation — search messages across mailboxes of one account
# --------------------------------------------------------------------------- #

def build_search_script(
    *,
    account: str,
    mailbox: str | None = None,
    include_body: bool = False,
) -> str:
    """Return AppleScript that fetches messages across mailboxes of *account*.

    Output format (one line per message, ``||``-delimited)::

        mailbox_name||message_id||date||sender||subject||read_flag||flagged_flag

    When *include_body* is ``True``, body content is appended as an 8th field
    with newlines replaced by ``@@NL@@`` to keep one-line-per-message format.

    When *mailbox* is given, only that mailbox is searched.  Otherwise all
    mailboxes of the account are searched.
    """
    body_block = ""
    body_field = ""
    if include_body:
        body_block = '''
            set msgBody to content of msg
            set oldDelims to AppleScript's text item delimiters
            set AppleScript's text item delimiters to {return, linefeed, character id 10}
            set bodyParts to text items of msgBody
            set AppleScript's text item delimiters to "@@NL@@"
            set cleanBody to bodyParts as text
            set AppleScript's text item delimiters to oldDelims'''
        body_field = ' & "||" & cleanBody'

    if mailbox:
        # Search a specific mailbox within the account.
        return f'''\
tell application "Mail"
    set output to ""
    set acct to account "{account}"
    set mbox to mailbox "{mailbox}" of acct
    set mboxName to name of mbox
    set msgs to every message of mbox
    repeat with msg in msgs
        set msgId to id of msg as string
        set msgDate to date received of msg as string
        set msgSender to sender of msg
        set msgSubject to subject of msg
        set msgRead to read status of msg as string
        set msgFlagged to flagged status of msg as string{body_block}
        if output is not "" then set output to output & linefeed
        set output to output & mboxName & "||" & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged{body_field}
    end repeat
    return output
end tell'''
    else:
        # Search all mailboxes in the account.
        return f'''\
tell application "Mail"
    set output to ""
    set acct to account "{account}"
    set mboxes to every mailbox of acct
    repeat with mbox in mboxes
        set mboxName to name of mbox
        set msgs to every message of mbox
        repeat with msg in msgs
            set msgId to id of msg as string
            set msgDate to date received of msg as string
            set msgSender to sender of msg
            set msgSubject to subject of msg
            set msgRead to read status of msg as string
            set msgFlagged to flagged status of msg as string{body_block}
            if output is not "" then set output to output & linefeed
            set output to output & mboxName & "||" & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged{body_field}
        end repeat
    end repeat
    return output
end tell'''


# --------------------------------------------------------------------------- #
# Parsing — search results
# --------------------------------------------------------------------------- #

def parse_search_output(raw: str, account_name: str) -> list[dict]:
    """Parse ``||``-delimited search output into structured data.

    Each line has at least 7 fields:
    ``mailbox||id||date||from||subject||read||flagged[||body]``

    Returns a list of dicts with keys: ``account``, ``mailbox``, ``id``,
    ``date``, ``from``, ``subject``, ``read``, ``flagged``, and optionally
    ``_body`` (prefixed with underscore — internal, not exposed in output).
    """
    if not raw.strip():
        return []

    messages: list[dict] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Split into at most 8 parts so body (which may contain ||) stays intact.
        parts = line.split("||", 7)
        if len(parts) >= 7:
            msg: dict = {
                "account": account_name,
                "mailbox": parts[0].strip(),
                "id": parts[1].strip(),
                "date": parts[2].strip(),
                "from": parts[3].strip(),
                "subject": parts[4].strip(),
                "read": parts[5].strip().lower() == "true",
                "flagged": parts[6].strip().lower() == "true",
            }
            if len(parts) > 7:
                # Body content (with @@NL@@ markers for newlines).
                msg["_body"] = parts[7].replace("@@NL@@", "\n")
            messages.append(msg)
    return messages


# --------------------------------------------------------------------------- #
# Filtering — search results (extends existing _apply_filters)
# --------------------------------------------------------------------------- #

def _apply_search_filters(
    messages: list[dict],
    *,
    from_filter: str | None = None,
    subject_filter: str | None = None,
    body_filter: str | None = None,
    since: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Apply post-fetch filters to search results.

    All filters are applied conjunctively (AND).
    """
    result = messages

    if from_filter:
        lower_from = from_filter.lower()
        result = [m for m in result if lower_from in m["from"].lower()]

    if subject_filter:
        lower_subject = subject_filter.lower()
        result = [m for m in result if lower_subject in m["subject"].lower()]

    if body_filter:
        lower_body = body_filter.lower()
        result = [
            m for m in result
            if lower_body in m.get("_body", "").lower()
        ]

    if since:
        since_date = datetime.strptime(since, "%Y-%m-%d")
        result = [m for m in result if _is_on_or_after(m["date"], since_date)]

    if before:
        before_date = datetime.strptime(before, "%Y-%m-%d")
        result = [m for m in result if _is_before(m["date"], before_date)]

    return result


# --------------------------------------------------------------------------- #
# Data fetching — cross-account search
# --------------------------------------------------------------------------- #

def fetch_search_results(
    *,
    account: str | None = None,
    mailbox: str | None = None,
    from_filter: str | None = None,
    subject_filter: str | None = None,
    body_filter: str | None = None,
    since: str | None = None,
    before: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Search messages across accounts via AppleScript.

    Uses at most *N* + 1 osascript calls for *N* accounts: one call to list
    account names, then one per account to search.  When ``--account`` is
    given, only one search call is made (no account-list call).

    Returns a list of message dicts (with ``account`` and ``mailbox`` fields),
    filtered, sorted newest-first, and capped at *limit*.
    Raises :class:`AppleScriptError` on failure.
    """
    include_body = body_filter is not None

    # Determine which accounts to search.
    if account:
        account_names = [account]
    else:
        raw_accounts = run_applescript(build_account_names_script())
        account_names = parse_account_names_output(raw_accounts)

    # Search each account.
    all_messages: list[dict] = []
    for acct_name in account_names:
        script = build_search_script(
            account=acct_name,
            mailbox=mailbox,
            include_body=include_body,
        )
        raw = run_applescript(script)
        msgs = parse_search_output(raw, acct_name)
        all_messages.extend(msgs)

    # Apply filters.
    all_messages = _apply_search_filters(
        all_messages,
        from_filter=from_filter,
        subject_filter=subject_filter,
        body_filter=body_filter,
        since=since,
        before=before,
    )

    # Sort by date descending (newest first).
    all_messages = _sort_by_date_descending(all_messages)

    # Apply limit.
    if limit > 0:
        all_messages = all_messages[:limit]

    # Strip internal _body field before returning.
    for msg in all_messages:
        msg.pop("_body", None)

    return all_messages


# --------------------------------------------------------------------------- #
# Table column definitions
# --------------------------------------------------------------------------- #

MESSAGES_COLUMNS = [
    ColumnDef(header="Date", key="date", max_width=25),
    ColumnDef(header="From", key="from", max_width=30),
    ColumnDef(header="Subject", key="subject", max_width=40),
    ColumnDef(header="Read", key="read", max_width=8),
    ColumnDef(header="Flagged", key="flagged", max_width=8),
    ColumnDef(header="ID", key="id", max_width=15),
]

SEARCH_COLUMNS = [
    ColumnDef(header="Account", key="account", max_width=20),
    ColumnDef(header="Mailbox", key="mailbox", max_width=15),
    ColumnDef(header="Date", key="date", max_width=25),
    ColumnDef(header="From", key="from", max_width=25),
    ColumnDef(header="Subject", key="subject", max_width=35),
    ColumnDef(header="ID", key="id", max_width=15),
]


# --------------------------------------------------------------------------- #
# Typer command handlers
# --------------------------------------------------------------------------- #

def register(messages_app: typer.Typer) -> None:
    """Register the ``messages list`` and ``messages show`` commands."""

    @messages_app.command("list", help="List messages in a mailbox.")
    def messages_list(
        ctx: typer.Context,
        mailbox: str = typer.Option(
            "INBOX", "--mailbox", "-m",
            help="Target mailbox name (default: INBOX).",
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope to a specific account name.",
        ),
        unread: bool = typer.Option(
            False, "--unread", "-u",
            help="Show only unread messages.",
        ),
        from_filter: Optional[str] = typer.Option(
            None, "--from", "-f",
            help="Filter by sender (case-insensitive substring match).",
        ),
        subject_filter: Optional[str] = typer.Option(
            None, "--subject", "-s",
            help="Filter by subject (case-insensitive substring match).",
        ),
        since: Optional[str] = typer.Option(
            None, "--since",
            help="Show messages on or after this date (YYYY-MM-DD).",
        ),
        before: Optional[str] = typer.Option(
            None, "--before",
            help="Show messages before this date (YYYY-MM-DD).",
        ),
        limit: int = typer.Option(
            DEFAULT_LIMIT, "--limit", "-l",
            help=f"Maximum number of messages to return (default: {DEFAULT_LIMIT}).",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """List messages in a mailbox with optional filters."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        try:
            data = fetch_messages(
                account=account,
                mailbox=mailbox,
                unread=unread,
                from_filter=from_filter,
                subject_filter=subject_filter,
                since=since,
                before=before,
                limit=limit,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        render_output(
            data,
            MESSAGES_COLUMNS,
            json_mode=json_mode,
            no_color=no_color,
            title="Messages",
        )

    @messages_app.command(
        "search",
        help="Search messages across accounts with filters.",
    )
    def messages_search(
        ctx: typer.Context,
        from_filter: Optional[str] = typer.Option(
            None, "--from", "-f",
            help="Filter by sender (case-insensitive substring match).",
        ),
        subject_filter: Optional[str] = typer.Option(
            None, "--subject", "-s",
            help="Filter by subject (case-insensitive substring match).",
        ),
        body_filter: Optional[str] = typer.Option(
            None, "--body", "-b",
            help="Filter by body content (case-insensitive substring match).",
        ),
        since: Optional[str] = typer.Option(
            None, "--since",
            help="Show messages on or after this date (YYYY-MM-DD).",
        ),
        before: Optional[str] = typer.Option(
            None, "--before",
            help="Show messages before this date (YYYY-MM-DD).",
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a",
            help="Scope search to a specific account name.",
        ),
        mailbox: Optional[str] = typer.Option(
            None, "--mailbox", "-m",
            help="Scope search to a specific mailbox within the targeted account(s).",
        ),
        limit: int = typer.Option(
            DEFAULT_LIMIT, "--limit", "-l",
            help=f"Maximum number of results to return (default: {DEFAULT_LIMIT}).",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Search messages across all accounts with filters.

        At least one search filter (--from, --subject, --body, --since, or
        --before) is required.
        """
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        # Require at least one search filter.
        if not any([from_filter, subject_filter, body_filter, since, before]):
            render_error(
                "At least one search criterion is required "
                "(--from, --subject, --body, --since, or --before).",
                no_color=no_color,
            )
            raise typer.Exit(code=EXIT_USAGE_ERROR)

        try:
            data = fetch_search_results(
                account=account,
                mailbox=mailbox,
                from_filter=from_filter,
                subject_filter=subject_filter,
                body_filter=body_filter,
                since=since,
                before=before,
                limit=limit,
            )
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)

        render_output(
            data,
            SEARCH_COLUMNS,
            json_mode=json_mode,
            no_color=no_color,
            title="Search Results",
        )

    @messages_app.command("show", help="Show a single message by ID.")
    def messages_show(
        ctx: typer.Context,
        message_id: str = typer.Argument(
            ...,
            help="The message ID to display (from 'messages list' output).",
        ),
        headers: bool = typer.Option(
            False, "--headers", "-H",
            help="Display all message headers.",
        ),
        raw: bool = typer.Option(
            False, "--raw", "-r",
            help="Display the unprocessed message body without formatting.",
        ),
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Show a single message with full details."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        try:
            msg = fetch_message(message_id)
        except AppleScriptError as exc:
            handle_mail_error(exc, no_color=no_color)
            return  # unreachable but satisfies type checker

        if json_mode:
            sys.stdout.write(json.dumps(msg, indent=2, default=str) + "\n")
            return

        if raw:
            sys.stdout.write(msg.get("body", "") + "\n")
            return

        # Formatted output using Rich
        console = Console(no_color=no_color)

        # Header block
        console.print(f"[bold]From:[/bold]    {msg['from']}")
        console.print(f"[bold]To:[/bold]      {msg['to']}")
        if msg.get("cc"):
            console.print(f"[bold]Cc:[/bold]      {msg['cc']}")
        if msg.get("bcc"):
            console.print(f"[bold]Bcc:[/bold]     {msg['bcc']}")
        console.print(f"[bold]Date:[/bold]    {msg['date']}")
        console.print(f"[bold]Subject:[/bold] {msg['subject']}")
        console.print(f"[bold]ID:[/bold]      {msg['id']}")
        console.print(f"[bold]Read:[/bold]    {msg['read']}")
        console.print(f"[bold]Flagged:[/bold] {msg['flagged']}")
        console.print()

        # Headers section (if requested)
        if headers and msg.get("headers"):
            console.print("[bold]--- Headers ---[/bold]")
            console.print(msg["headers"])
            console.print()

        # Attachments
        if msg.get("attachments"):
            console.print("[bold]--- Attachments ---[/bold]")
            for att in msg["attachments"]:
                console.print(
                    f"  {att['name']}  ({att['size']} bytes, {att['mime_type']})"
                )
            console.print()

        # Body
        console.print("[bold]--- Body ---[/bold]")
        console.print(msg.get("body", "(empty)"))
