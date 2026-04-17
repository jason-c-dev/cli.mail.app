"""Shared message-ID resolver for commands that need to address a single
message via AppleScript.

Mail.app's AppleScript dictionary exposes ``first message of mailbox "X"
of account "Y" whose id is Z`` — a direct lookup scoped to a specific
mailbox. The older, iterate-everything pattern (``every mailbox of every
account`` → ``every message of mbox``) is unreliable: some system
mailboxes (``Notes``) raise ``-1728``, and large IMAP mailboxes such as
Gmail's ``[Gmail]/All Mail`` or ``INBOX`` raise ``-1741`` because
messages aren't fully materialised.

This module resolves a Mail.app message ROWID to
``(account_name, mailbox_path)`` using the Envelope Index (SQLite) so
callers can build a targeted AppleScript instead. Used by ``reply``,
``forward``, ``messages mark``, ``messages move``, ``messages delete``,
and ``drafts edit``.
"""

from __future__ import annotations

from mailctl.account_map import name_for_uuid
from mailctl.errors import AppleScriptError
from mailctl.sqlite_engine import parse_mailbox_url, run_query


def resolve_message_location(message_id: str) -> tuple[str, str]:
    """Resolve a message ROWID to the account + mailbox it lives in.

    Returns ``(account_name, mailbox_path)``. The mailbox path keeps any
    provider prefix Mail.app uses internally (e.g. ``[Gmail]/All Mail``)
    — that's what AppleScript expects. Raises :class:`AppleScriptError`
    with a "not found" message if no row matches.
    """
    try:
        rowid = int(message_id)
    except ValueError:
        raise AppleScriptError(f'Message "{message_id}" not found.')

    rows = run_query(
        """
        SELECT mb.url AS mailbox_url
        FROM messages m
        JOIN mailboxes mb ON mb.ROWID = m.mailbox
        WHERE m.ROWID = ?
        """,
        (rowid,),
    )
    if not rows:
        raise AppleScriptError(f'Message "{message_id}" not found.')

    _, account_uuid, mailbox_path = parse_mailbox_url(rows[0]["mailbox_url"] or "")
    account_name = name_for_uuid(account_uuid) if account_uuid else ""
    if not account_name or not mailbox_path:
        raise AppleScriptError(f'Message "{message_id}" not found.')
    return account_name, mailbox_path
