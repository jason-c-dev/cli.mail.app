"""SQLite execution engine for read-only access to Mail.app's Envelope Index.

This is the **single execution seam** for all SQLite queries, mirroring the
role of ``engine.run_applescript`` for AppleScript. Tests mock this one
function to avoid depending on the user's real Mail.app index.

Architectural notes:
- Opens the database in read-only mode (``mode=ro`` URI) so Mail.app's
  WAL-based writes are never blocked and we cannot accidentally mutate
  the index. Mail.app is the authoritative writer.
- Resolves the versioned data directory (``V10``, ``V11``, ...) at
  runtime so we transparently follow Apple's schema migrations.
- Classifies OS-level errors (FileNotFoundError, PermissionError) into
  domain exceptions that the CLI surface renders with actionable hints.
"""

from __future__ import annotations

import glob
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from mailctl.errors import (
    EnvelopeIndexError,
    EnvelopeIndexMissingError,
    FullDiskAccessError,
)


# Tables we require. If any of these are missing the schema has shifted
# under us and queries won't work. doctor.py uses this to surface the issue.
REQUIRED_TABLES = frozenset({
    "messages",
    "mailboxes",
    "subjects",
    "addresses",
    "recipients",
    "attachments",
})


def envelope_index_path() -> Path:
    """Return the path to Mail.app's Envelope Index file.

    Globs ``~/Library/Mail/V*/MailData/Envelope Index`` and picks the
    highest-versioned directory. Raises :class:`EnvelopeIndexMissingError`
    if none exists.
    """
    home = Path.home()
    pattern = str(home / "Library" / "Mail" / "V*" / "MailData" / "Envelope Index")
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise EnvelopeIndexMissingError()
    # Sort puts V10 before V9 alphabetically — but versions stay single digit
    # up to V9 and then go double. Explicit numeric sort by the V prefix:
    def version_key(p: str) -> int:
        try:
            vdir = Path(p).parents[1].name  # "V10"
            return int(vdir.lstrip("V"))
        except (ValueError, IndexError):
            return 0
    matches.sort(key=version_key)
    return Path(matches[-1])


def _open_connection(path: Path) -> sqlite3.Connection:
    """Open the Envelope Index read-only. Translates low-level errors."""
    if not path.exists():
        raise EnvelopeIndexMissingError()

    # Probe read permission explicitly so we can raise the specific TCC hint.
    # sqlite3.connect doesn't always raise PermissionError for TCC denials;
    # it can surface as OperationalError("unable to open database file")
    # instead. An explicit open() catches the TCC case earlier with the
    # right error type.
    try:
        fd = os.open(str(path), os.O_RDONLY)
        os.close(fd)
    except PermissionError as exc:
        raise FullDiskAccessError(path=str(path)) from exc
    except FileNotFoundError as exc:
        raise EnvelopeIndexMissingError() from exc

    uri = f"file:{quote(str(path))}?mode=ro&immutable=0"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.OperationalError as exc:
        if "unable to open" in str(exc).lower():
            # Most likely TCC even if os.open succeeded (SIP-protected path
            # can have subtler semantics).
            raise FullDiskAccessError(path=str(path)) from exc
        raise EnvelopeIndexError(
            f"Could not open Envelope Index: {exc}"
        ) from exc
    conn.row_factory = sqlite3.Row
    return conn


def run_query(
    sql: str,
    params: tuple | list = (),
    *,
    db_path: Path | None = None,
) -> list[sqlite3.Row]:
    """Execute a read-only SELECT against the Envelope Index.

    Parameters
    ----------
    sql:
        The SELECT statement. Parameters MUST use ``?`` placeholders —
        never string-format user input into the SQL.
    params:
        Bound parameters for the ``?`` placeholders.
    db_path:
        Override the database path. Intended for tests; production callers
        should leave this as ``None`` so the standard Envelope Index is used.

    Returns
    -------
    list of sqlite3.Row
        Rows with column-name access (``row["subject"]`` or ``row[2]``).
    """
    path = db_path if db_path is not None else envelope_index_path()
    conn = _open_connection(path)
    try:
        cursor = conn.execute(sql, params)
        return list(cursor.fetchall())
    except sqlite3.OperationalError as exc:
        raise EnvelopeIndexError(f"SQLite query failed: {exc}") from exc
    finally:
        conn.close()


def check_schema(db_path: Path | None = None) -> set[str]:
    """Return the set of REQUIRED_TABLES that are missing from the index.

    An empty set means the schema has everything we need. Used by the
    doctor command to detect an unrecognised Mail.app version.
    """
    rows = run_query(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
        db_path=db_path,
    )
    present = {row["name"] for row in rows}
    return set(REQUIRED_TABLES) - present


# --------------------------------------------------------------------------- #
# Mailbox URL parsing
# --------------------------------------------------------------------------- #

# Mailbox URLs in the Envelope Index look like:
#   imap://{UUID}/INBOX
#   imap://{UUID}/%5BGmail%5D/All%20Mail
#   ews://{UUID}/Inbox
#   local://{UUID}/SendLater
# The UUID is the same identifier that AppleScript returns as `id of account`.


def parse_mailbox_url(url: str) -> tuple[str, str, str]:
    """Parse a mailbox URL into ``(scheme, account_uuid, mailbox_path)``.

    The mailbox path is URL-decoded and keeps any leading provider prefix
    (e.g. ``[Gmail]/All Mail``). Local accounts and IMAP/EWS accounts all
    flow through the same parse — callers that only want IMAP or EWS can
    filter by scheme.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    # For these URIs, the UUID lives in the netloc slot. An embedded
    # ``@host`` can appear in older IMAP URLs; strip it.
    account_uuid = parsed.netloc
    if "@" in account_uuid:
        account_uuid = account_uuid.split("@", 1)[-1]
    mailbox_path = unquote(parsed.path.lstrip("/"))
    return scheme, account_uuid, mailbox_path


def friendly_mailbox_name(url: str) -> str:
    """Return the last path segment of *url*, URL-decoded.

    ``imap://.../%5BGmail%5D/All%20Mail`` becomes ``All Mail``.
    ``ews://.../Inbox`` becomes ``Inbox``.
    """
    _, _, path = parse_mailbox_url(url)
    if not path:
        return ""
    # Keep last path segment; callers that want the provider prefix
    # (e.g. "[Gmail]/All Mail") can use parse_mailbox_url directly.
    return path.rsplit("/", 1)[-1]


def resolve_target_mailboxes(
    *,
    account_uuid: str | None = None,
    mailbox_name: str | None = None,
    db_path: Path | None = None,
) -> tuple[list[int], list[int]]:
    """Resolve user-facing account/mailbox scope into SQLite ROWID sets.

    Gmail (and some other IMAP servers) uses a label model: the visible
    ``INBOX`` is a virtual mailbox whose ``source`` column points at the
    canonical ``[Gmail]/All Mail`` storage. Messages are stored in the
    canonical mailbox and linked to virtual mailboxes via the ``labels``
    table.

    Callers that want to filter messages by scope need BOTH:
    - direct storage ROWIDs (``source IS NULL``) to match via ``messages.mailbox``
    - label ROWIDs (``source IS NOT NULL``) to match via the ``labels`` table

    Returns ``(storage_rowids, label_rowids)``. Either list may be empty.
    If both inputs are ``None``, returns empty tuples — caller should
    skip the scope WHERE clause entirely.
    """
    if account_uuid is None and mailbox_name is None:
        return [], []

    where: list[str] = []
    params: list = []

    if account_uuid:
        where.append(
            "(url LIKE ? OR url LIKE ? OR url LIKE ?)"
        )
        params.extend([
            f"imap://{account_uuid}/%",
            f"ews://{account_uuid}/%",
            f"local://{account_uuid}/%",
        ])

    if mailbox_name:
        encoded = (
            mailbox_name
            .replace("[", "%5B").replace("]", "%5D")
            .replace(" ", "%20")
        )
        where.append("(url LIKE ? OR url LIKE ?)")
        params.extend([f"%/{encoded}", f"%/{encoded}/%"])

    sql = (
        "SELECT ROWID, source FROM mailboxes "
        "WHERE " + " AND ".join(where)
    )
    rows = run_query(sql, tuple(params), db_path=db_path)

    storage: list[int] = []
    labels: list[int] = []
    for row in rows:
        if row["source"] is None:
            storage.append(int(row["ROWID"]))
        else:
            labels.append(int(row["ROWID"]))
    return storage, labels
