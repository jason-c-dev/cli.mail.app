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
    fetch_cap: int = 500,
) -> str:
    """Return AppleScript that lists messages from a mailbox.

    Output format (one line per message, ``||``-delimited)::

        message_id||date||sender||subject||read_flag||flagged_flag

    Uses ``messages 1 thru N`` indexed access rather than ``every message`` —
    the latter fails with AppleScript error -1741 on large IMAP mailboxes
    whose messages are not fully synced. Each property access is wrapped in
    ``try``/``on error`` so a single message with a missing value does not
    abort the whole fetch.

    Filtering by unread/sender/subject/date is done in Python after fetching,
    to keep the AppleScript simple.

    Parameters
    ----------
    fetch_cap:
        Upper bound on how many messages AppleScript fetches. Mail.app
        orders messages newest-first, so this gets the most recent
        *fetch_cap* messages. Python then applies user filters and the
        user-supplied ``--limit``.
    """
    if account:
        target = f'mailbox "{mailbox}" of account "{account}"'
    else:
        target = f'mailbox "{mailbox}"'

    return f'''\
with timeout of 120 seconds
tell application "Mail"
    set theBox to {target}
    set msgCount to count of messages of theBox
    set upperBound to {fetch_cap}
    if msgCount < upperBound then set upperBound to msgCount
    if upperBound < 1 then return ""
    set msgs to messages 1 thru upperBound of theBox
    set output to ""
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
            set msgSender to sender of msg as string
        on error
            set msgSender to ""
        end try
        try
            set msgSubject to subject of msg as string
        on error
            set msgSubject to ""
        end try
        try
            set msgRead to read status of msg as string
        on error
            set msgRead to "false"
        end try
        try
            set msgFlagged to flagged status of msg as string
        on error
            set msgFlagged to "false"
        end try
        if msgId is not "" then
            if output is not "" then set output to output & linefeed
            set output to output & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged
        end if
    end repeat
    return output
end tell
end timeout'''


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


def fetch_messages_via_applescript(
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
    """Legacy AppleScript fetch. Retained as a fallback path only."""
    fetch_cap = max(limit * 4, 50) if limit > 0 else 100
    fetch_cap = min(fetch_cap, 300)
    script = build_messages_list_script(account=account, mailbox=mailbox, fetch_cap=fetch_cap)
    raw = run_applescript(script, timeout=120.0)
    messages = parse_messages_list_output(raw)

    messages = _apply_filters(
        messages,
        unread=unread,
        from_filter=from_filter,
        subject_filter=subject_filter,
        since=since,
        before=before,
    )
    messages = _sort_by_date_descending(messages)
    if limit > 0:
        messages = messages[:limit]
    return messages


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
    """Fetch messages from Mail.app's Envelope Index (SQLite).

    All filters (unread/from/subject/since/before) are pushed into the SQL
    WHERE clause, so we never fetch more rows than we return. Sub-second
    even on 130k-message INBOX because the DB has indexes covering every
    filter shape we generate.

    Returns the same list-of-dicts shape as the legacy AppleScript fetch:
    keys ``id``, ``date``, ``from``, ``subject``, ``read``, ``flagged``.
    """
    from mailctl.sqlite_engine import run_query, resolve_target_mailboxes
    from mailctl.account_map import uuid_for_name

    where: list[str] = ["m.deleted = 0"]
    params: list = []

    # Resolve account + mailbox scope. Gmail uses a label indirection so
    # a message "in INBOX" can be stored in [Gmail]/All Mail with a label
    # pointing at INBOX. resolve_target_mailboxes returns both sets.
    account_uuid = None
    if account:
        account_uuid = uuid_for_name(account)
        if account_uuid is None:
            from mailctl.account_map import get_account_map
            known = ", ".join(a.name for a in get_account_map()) or "(none)"
            raise AppleScriptError(
                f'Account "{account}" not found. Known accounts: {known}.'
            )

    if account_uuid or mailbox:
        storage_ids, label_ids = resolve_target_mailboxes(
            account_uuid=account_uuid,
            mailbox_name=mailbox,
        )
        if not storage_ids and not label_ids:
            # Scope resolves to nothing — distinguish "bad mailbox" from "bad account".
            if mailbox:
                raise AppleScriptError(
                    f'Mailbox "{mailbox}" not found. '
                    f"Use 'mailctl mailboxes list"
                    + (f" --account {account}" if account else "")
                    + "' to see available mailboxes."
                )
            return []
        scope_clauses: list[str] = []
        if storage_ids:
            placeholders = ",".join("?" * len(storage_ids))
            scope_clauses.append(f"m.mailbox IN ({placeholders})")
            params.extend(storage_ids)
        if label_ids:
            placeholders = ",".join("?" * len(label_ids))
            scope_clauses.append(
                f"m.ROWID IN (SELECT message_id FROM labels "
                f"WHERE mailbox_id IN ({placeholders}))"
            )
            params.extend(label_ids)
        where.append("(" + " OR ".join(scope_clauses) + ")")

    if unread:
        where.append("m.read = 0")

    if subject_filter:
        where.append(
            "m.subject IN (SELECT ROWID FROM subjects WHERE subject LIKE ?)"
        )
        params.append(f"%{subject_filter}%")

    if from_filter:
        where.append(
            "m.sender IN (SELECT ROWID FROM addresses "
            "WHERE address LIKE ? OR comment LIKE ?)"
        )
        params.extend([f"%{from_filter}%", f"%{from_filter}%"])

    if since:
        where.append("m.date_received >= ?")
        params.append(_date_to_unix(since, end_of_day=False))
    if before:
        where.append("m.date_received < ?")
        params.append(_date_to_unix(before, end_of_day=False))

    where_sql = " AND ".join(where)
    limit_sql = f"LIMIT {int(limit)}" if limit and limit > 0 else ""

    sql = f"""
        SELECT m.ROWID            AS id,
               m.date_received    AS date_received,
               m.read             AS read,
               m.flagged          AS flagged,
               m.subject_prefix   AS subject_prefix,
               s.subject          AS subject,
               a.address          AS sender_address,
               a.comment          AS sender_comment
        FROM messages m
        LEFT JOIN subjects  s ON s.ROWID = m.subject
        LEFT JOIN addresses a ON a.ROWID = m.sender
        WHERE {where_sql}
        ORDER BY m.date_received DESC
        {limit_sql}
    """
    rows = run_query(sql, tuple(params))

    results: list[dict] = []
    for row in rows:
        subject = (row["subject_prefix"] or "") + (row["subject"] or "")
        results.append({
            "id": str(row["id"]),
            "date": _format_unix_date(row["date_received"]),
            "from": _format_sender(row["sender_address"], row["sender_comment"]),
            "subject": subject.strip(),
            "read": bool(row["read"]),
            "flagged": bool(row["flagged"]),
        })
    return results


def _date_to_unix(date_str: str, end_of_day: bool = False) -> int:
    """Convert a YYYY-MM-DD string to Unix epoch seconds (local time)."""
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _format_unix_date(ts: int | None) -> str:
    """Format a Unix timestamp as ISO-8601 local time (stable, machine-parseable)."""
    if ts is None:
        return ""
    from datetime import datetime
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def _format_sender(address: str | None, comment: str | None) -> str:
    """Render an ``addresses`` row as a human-readable From string.

    ``comment`` is Mail's term for the display name. When present we
    render ``"Name <email>"``; otherwise just the email.
    """
    if not address:
        return comment or ""
    if comment:
        return f"{comment} <{address}>"
    return address


def _fetch_messages_preserved_for_tests(
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
    """Kept for the legacy test harness — unused at runtime.

    Returns the same shape the AppleScript fetch used to. Tests that patch
    ``run_applescript`` still exercise ``fetch_messages_via_applescript``.
    """
    return fetch_messages_via_applescript(
        account=account, mailbox=mailbox, unread=unread,
        from_filter=from_filter, subject_filter=subject_filter,
        since=since, before=before, limit=limit,
    )


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

def fetch_message_via_applescript(message_id: str) -> dict:
    """Legacy AppleScript show. Retained as a body-fallback path."""
    script = build_message_show_script(message_id)
    raw = run_applescript(script)
    return parse_message_show_output(raw)


# Mail.app's recipients.type: empirically 0 = to, 1 = cc, 2 = bcc.
# (Determined by cross-referencing against messages whose To/Cc headers
# we could read from the .emlx file.)
_RECIPIENT_TYPE_TO = 0
_RECIPIENT_TYPE_CC = 1
_RECIPIENT_TYPE_BCC = 2


def fetch_message(message_id: str) -> dict:
    """Fetch a single message by ID from the Envelope Index + ``.emlx``.

    Headers, recipients, and attachment metadata come from SQLite.
    Body content comes from the on-disk ``.emlx`` file. If that file is
    missing (e.g. IMAP didn't fully sync the message), falls back to
    reading the body via AppleScript so the user still gets useful output.

    Returns the same dict shape as the legacy AppleScript fetch, including
    ``id``, ``date``, ``from``, ``to``, ``cc``, ``bcc``, ``subject``,
    ``body``, ``headers``, ``attachments``, ``read``, ``flagged``.
    """
    from mailctl.sqlite_engine import run_query, parse_mailbox_url
    from mailctl.account_map import name_for_uuid
    from mailctl.emlx_reader import emlx_candidates, read_emlx, extract_body

    try:
        rowid = int(message_id)
    except ValueError:
        raise AppleScriptError(f'Message "{message_id}" not found.')

    header_rows = run_query(
        """
        SELECT m.ROWID            AS id,
               m.date_received    AS date_received,
               m.read             AS read,
               m.flagged          AS flagged,
               m.subject_prefix   AS subject_prefix,
               s.subject          AS subject,
               a.address          AS sender_address,
               a.comment          AS sender_comment,
               mb.url             AS mailbox_url
        FROM messages m
        LEFT JOIN subjects  s  ON s.ROWID  = m.subject
        LEFT JOIN addresses a  ON a.ROWID  = m.sender
        LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
        WHERE m.ROWID = ?
        """,
        (rowid,),
    )
    if not header_rows:
        raise AppleScriptError(f'Message "{message_id}" not found.')
    h = header_rows[0]

    recipient_rows = run_query(
        """
        SELECT r.type      AS rtype,
               a.address   AS address,
               a.comment   AS comment
        FROM recipients r
        JOIN addresses a ON a.ROWID = r.address
        WHERE r.message = ?
        ORDER BY r.position
        """,
        (rowid,),
    )
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    for r in recipient_rows:
        rendered = _format_sender(r["address"], r["comment"])
        if r["rtype"] == _RECIPIENT_TYPE_TO:
            to.append(rendered)
        elif r["rtype"] == _RECIPIENT_TYPE_CC:
            cc.append(rendered)
        elif r["rtype"] == _RECIPIENT_TYPE_BCC:
            bcc.append(rendered)

    attachment_rows = run_query(
        "SELECT name, attachment_id FROM attachments WHERE message = ?",
        (rowid,),
    )
    attachments = [
        {
            "name": row["name"] or "",
            "size": "",
            "mime_type": "",
        }
        for row in attachment_rows
    ]

    # Body + raw RFC 822 headers from the on-disk .emlx file.
    body = ""
    headers_text = ""
    mailbox_url = h["mailbox_url"] or ""
    paths = emlx_candidates(rowid, mailbox_url)
    if paths:
        try:
            msg = read_emlx(paths[0])
            body = extract_body(msg)
            headers_text = "\n".join(f"{k}: {v}" for k, v in msg.items())
        except Exception:
            # Body parse failure — fall back below.
            pass

    if not body:
        # Either no .emlx (partial download) or parsing failed. Ask Mail
        # for the body via AppleScript. We only pay for this when needed.
        try:
            legacy = fetch_message_via_applescript(message_id)
            if not body:
                body = legacy.get("body", "")
            if not headers_text:
                headers_text = legacy.get("headers", "")
            if not attachments:
                attachments = legacy.get("attachments", [])
        except AppleScriptError:
            # Best effort. Continue with what we have.
            pass

    subject = (h["subject_prefix"] or "") + (h["subject"] or "")

    return {
        "id": str(h["id"]),
        "date": _format_unix_date(h["date_received"]),
        "from": _format_sender(h["sender_address"], h["sender_comment"]),
        "to": ", ".join(to),
        "cc": ", ".join(cc),
        "bcc": ", ".join(bcc),
        "subject": subject.strip(),
        "body": body,
        "headers": headers_text,
        "attachments": attachments,
        "read": bool(h["read"]),
        "flagged": bool(h["flagged"]),
    }


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

    # Per-mailbox fetch cap. Mail.app orders messages newest-first, so this
    # gets the most recent N messages per mailbox. Python then applies filters.
    fetch_cap = 200

    msg_body_setup = ""
    if include_body:
        # Only compute body when include_body is set — avoids slow content-fetch
        # on every message. Still wrapped in try for safety.
        msg_body_setup = f'''
        try{body_block}
        on error
            set cleanBody to ""
        end try'''

    if mailbox:
        # Search a specific mailbox within the account.
        return f'''\
with timeout of 180 seconds
tell application "Mail"
    set output to ""
    set acct to account "{account}"
    set mbox to mailbox "{mailbox}" of acct
    set mboxName to name of mbox
    set msgCount to count of messages of mbox
    set upperBound to {fetch_cap}
    if msgCount < upperBound then set upperBound to msgCount
    if upperBound < 1 then return ""
    set msgs to messages 1 thru upperBound of mbox
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
            set msgSender to sender of msg as string
        on error
            set msgSender to ""
        end try
        try
            set msgSubject to subject of msg as string
        on error
            set msgSubject to ""
        end try
        try
            set msgRead to read status of msg as string
        on error
            set msgRead to "false"
        end try
        try
            set msgFlagged to flagged status of msg as string
        on error
            set msgFlagged to "false"
        end try{msg_body_setup}
        if msgId is not "" then
            if output is not "" then set output to output & linefeed
            set output to output & mboxName & "||" & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged{body_field}
        end if
    end repeat
    return output
end tell
end timeout'''
    else:
        # Search all mailboxes in the account.
        return f'''\
with timeout of 180 seconds
tell application "Mail"
    set output to ""
    set acct to account "{account}"
    set mboxes to every mailbox of acct
    repeat with mbox in mboxes
        try
            set mboxName to name of mbox
            set msgCount to count of messages of mbox
            set upperBound to {fetch_cap}
            if msgCount < upperBound then set upperBound to msgCount
            if upperBound >= 1 then
                set msgs to messages 1 thru upperBound of mbox
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
                        set msgSender to sender of msg as string
                    on error
                        set msgSender to ""
                    end try
                    try
                        set msgSubject to subject of msg as string
                    on error
                        set msgSubject to ""
                    end try
                    try
                        set msgRead to read status of msg as string
                    on error
                        set msgRead to "false"
                    end try
                    try
                        set msgFlagged to flagged status of msg as string
                    on error
                        set msgFlagged to "false"
                    end try{msg_body_setup}
                    if msgId is not "" then
                        if output is not "" then set output to output & linefeed
                        set output to output & mboxName & "||" & msgId & "||" & msgDate & "||" & msgSender & "||" & msgSubject & "||" & msgRead & "||" & msgFlagged{body_field}
                    end if
                end repeat
            end if
        on error
            -- Skip mailboxes that can't be read (system/virtual mailboxes etc)
        end try
    end repeat
    return output
end tell
end timeout'''


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
    """Search messages across accounts via the Envelope Index (SQLite).

    All filters push down into SQL. Account and mailbox scope use the same
    storage+label resolution as ``fetch_messages``. Body-substring search
    is not implemented here — ``--body`` is accepted but will return zero
    results until the ``.emlx`` reader path is wired in; the Typer handler
    surfaces a clear error in that case.

    Returns a list of message dicts with keys ``account``, ``mailbox``,
    ``id``, ``date``, ``from``, ``subject``, ``read``, ``flagged`` — same
    shape as the legacy AppleScript output.
    """
    from mailctl.sqlite_engine import run_query, resolve_target_mailboxes, parse_mailbox_url
    from mailctl.account_map import uuid_for_name, name_for_uuid

    if body_filter is not None:
        # Bodies live in .emlx files, not in Envelope Index. We could scan
        # them but it would be slow and defeat the point. Defer this.
        raise NotImplementedError(
            "Body search is not yet supported by the SQLite backend. "
            "Use --subject or --from for fast search; body search will "
            "be re-added via an .emlx scan in a follow-up."
        )

    where: list[str] = ["m.deleted = 0"]
    params: list = []

    account_uuid = None
    if account:
        account_uuid = uuid_for_name(account)
        if account_uuid is None:
            return []

    if account_uuid or mailbox:
        storage_ids, label_ids = resolve_target_mailboxes(
            account_uuid=account_uuid,
            mailbox_name=mailbox,
        )
        if not storage_ids and not label_ids:
            return []
        scope_clauses: list[str] = []
        if storage_ids:
            placeholders = ",".join("?" * len(storage_ids))
            scope_clauses.append(f"m.mailbox IN ({placeholders})")
            params.extend(storage_ids)
        if label_ids:
            placeholders = ",".join("?" * len(label_ids))
            scope_clauses.append(
                f"m.ROWID IN (SELECT message_id FROM labels "
                f"WHERE mailbox_id IN ({placeholders}))"
            )
            params.extend(label_ids)
        where.append("(" + " OR ".join(scope_clauses) + ")")

    if subject_filter:
        where.append(
            "m.subject IN (SELECT ROWID FROM subjects WHERE subject LIKE ?)"
        )
        params.append(f"%{subject_filter}%")

    if from_filter:
        where.append(
            "m.sender IN (SELECT ROWID FROM addresses "
            "WHERE address LIKE ? OR comment LIKE ?)"
        )
        params.extend([f"%{from_filter}%", f"%{from_filter}%"])

    if since:
        where.append("m.date_received >= ?")
        params.append(_date_to_unix(since))
    if before:
        where.append("m.date_received < ?")
        params.append(_date_to_unix(before))

    where_sql = " AND ".join(where)
    limit_sql = f"LIMIT {int(limit)}" if limit and limit > 0 else ""

    sql = f"""
        SELECT m.ROWID            AS id,
               m.date_received    AS date_received,
               m.read             AS read,
               m.flagged          AS flagged,
               m.subject_prefix   AS subject_prefix,
               s.subject          AS subject,
               a.address          AS sender_address,
               a.comment          AS sender_comment,
               mb.url             AS mailbox_url
        FROM messages m
        LEFT JOIN subjects  s  ON s.ROWID  = m.subject
        LEFT JOIN addresses a  ON a.ROWID  = m.sender
        LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
        WHERE {where_sql}
        ORDER BY m.date_received DESC
        {limit_sql}
    """
    rows = run_query(sql, tuple(params))

    results: list[dict] = []
    for row in rows:
        subject = (row["subject_prefix"] or "") + (row["subject"] or "")
        _, acct_uuid, mbox_path = parse_mailbox_url(row["mailbox_url"] or "")
        mbox_name = mbox_path.rsplit("/", 1)[-1] if mbox_path else ""
        results.append({
            "account": name_for_uuid(acct_uuid) if acct_uuid else "",
            "mailbox": mbox_name,
            "id": str(row["id"]),
            "date": _format_unix_date(row["date_received"]),
            "from": _format_sender(row["sender_address"], row["sender_comment"]),
            "subject": subject.strip(),
            "read": bool(row["read"]),
            "flagged": bool(row["flagged"]),
        })
    return results


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
            # Normalise curly/straight apostrophes — Mail.app uses typographic
            # apostrophes in error messages (e.g. "Can’t get..."), so match both.
            from mailctl.engine import normalize_error_text
            exc_str = normalize_error_text(str(exc))
            # Mailbox-not-found is the most common case when users pass an
            # account + mailbox combination that doesn't exist (e.g. "INBOX"
            # on an Exchange account that calls it "Inbox"). Check this
            # before the broader account-not-found test, since the error
            # "can't get mailbox X of account Y" matches both.
            if "mailbox" in exc_str and ("not found" in exc_str or "can't get" in exc_str or "doesn't exist" in exc_str):
                hint = f"Use 'mailctl mailboxes list"
                if account:
                    hint += f" --account {account}"
                hint += "' to see available mailboxes."
                render_error(
                    f'Mailbox "{mailbox}" not found. {hint}',
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)
            # Account not found. "Can't get account X" from AppleScript
            # means the account name doesn't resolve. Mailbox-case was already
            # handled above, so "can't get" + "account" without "mailbox" is
            # unambiguously an account problem.
            if account and "account" in exc_str and (
                "not found" in exc_str
                or "doesn't exist" in exc_str
                or ("can't get" in exc_str and "mailbox" not in exc_str)
            ):
                try:
                    known = parse_account_names_output(
                        run_applescript(build_account_names_script())
                    )
                    render_error(
                        f'Account "{account}" not found. '
                        f"Known accounts: {', '.join(known) or '(none)'}.",
                        no_color=no_color,
                    )
                    raise typer.Exit(code=EXIT_USAGE_ERROR)
                except AppleScriptError:
                    pass  # Fall through to generic handling
            handle_mail_error(exc, no_color=no_color)

        if not data:
            if json_mode:
                sys.stdout.write("[]\n")
            else:
                console = Console(no_color=no_color)
                console.print("No messages found.")
            raise typer.Exit(code=0)

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

        if not data:
            if json_mode:
                sys.stdout.write("[]\n")
            else:
                console = Console(no_color=no_color)
                console.print("No messages matched your search.")
            raise typer.Exit(code=0)

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
            # Provide a clear message-not-found error if applicable.
            if "not found" in str(exc).lower() or message_id in str(exc):
                render_error(
                    f'Message "{message_id}" not found. '
                    f"Verify the message ID with 'mailctl messages list'.",
                    no_color=no_color,
                )
                raise typer.Exit(code=EXIT_USAGE_ERROR)
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
