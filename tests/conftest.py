"""Shared pytest fixtures for mailctl tests.

Two seams:

- :func:`mock_osascript` patches ``subprocess.run`` inside ``engine`` so
  tests never shell out to real ``osascript``. Used by write-path tests
  (compose, reply, forward, draft edit, delete, mark/move) and legacy
  tests that assert on generated AppleScript.

- :func:`envelope_db` builds an in-memory SQLite database matching
  Mail.app's Envelope Index schema, seeds it with synthetic data, and
  monkey-patches ``mailctl.sqlite_engine`` + ``mailctl.account_map`` so
  read-path commands (``accounts``, ``mailboxes``, ``messages``, ``drafts``)
  exercise real SQL against realistic data. This closes the mock blind
  spot that let AppleScript-shape bugs pass silently.
"""

from __future__ import annotations

import sqlite3
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class OsascriptMock:
    """Programmable stand-in for osascript subprocess calls."""

    def __init__(self) -> None:
        self._stdout: str = ""
        self._stderr: str = ""
        self._returncode: int = 0
        self._side_effect: Exception | None = None
        self._calls: list[list[str]] = []
        self._output_sequence: list[str] | None = None
        self._sequence_index: int = 0

    def set_output(self, stdout: str, returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = ""
        self._returncode = returncode
        self._side_effect = None
        self._output_sequence = None

    def set_outputs(self, outputs: list[str]) -> None:
        self._output_sequence = list(outputs)
        self._sequence_index = 0
        self._stderr = ""
        self._returncode = 0
        self._side_effect = None

    def set_error(self, stderr: str, returncode: int = 1) -> None:
        self._stdout = ""
        self._stderr = stderr
        self._returncode = returncode
        self._side_effect = None
        self._output_sequence = None

    def set_timeout(self) -> None:
        self._side_effect = subprocess.TimeoutExpired(
            cmd=["osascript", "-e", "..."],
            timeout=30,
        )

    @property
    def calls(self) -> list[list[str]]:
        return self._calls

    @property
    def last_script(self) -> str | None:
        if not self._calls:
            return None
        args = self._calls[-1]
        if len(args) >= 3:
            return args[2]
        return None

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self._calls.append(list(args))
        if self._side_effect is not None:
            raise self._side_effect
        stdout = self._stdout
        if self._output_sequence is not None:
            idx = min(self._sequence_index, len(self._output_sequence) - 1)
            stdout = self._output_sequence[idx]
            self._sequence_index += 1
        return subprocess.CompletedProcess(
            args=args,
            returncode=self._returncode,
            stdout=stdout,
            stderr=self._stderr,
        )


@pytest.fixture
def mock_osascript() -> OsascriptMock:
    mock = OsascriptMock()
    with patch("mailctl.engine.subprocess.run", side_effect=mock):
        yield mock


# --------------------------------------------------------------------------- #
# Envelope Index (SQLite) test harness
# --------------------------------------------------------------------------- #

# Matches the real Mail.app V10 schema — only the columns mailctl queries.
# The full production schema has ~30 additional columns on `messages` that we
# don't touch; omitting them here keeps tests readable and the in-memory DB
# small without changing query behaviour.
_SCHEMA = """
CREATE TABLE mailboxes (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    unread_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    unread_count_adjusted_for_duplicates INTEGER NOT NULL DEFAULT 0,
    source INTEGER,
    UNIQUE(url)
);
CREATE TABLE subjects (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    UNIQUE(subject)
);
CREATE TABLE addresses (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    comment TEXT NOT NULL,
    UNIQUE(address, comment)
);
CREATE TABLE messages (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    sender INTEGER,
    subject_prefix TEXT,
    subject INTEGER NOT NULL,
    date_received INTEGER,
    mailbox INTEGER NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    type INTEGER DEFAULT 0
);
CREATE TABLE recipients (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    message INTEGER NOT NULL,
    address INTEGER NOT NULL,
    type INTEGER,
    position INTEGER
);
CREATE TABLE attachments (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    message INTEGER NOT NULL,
    attachment_id TEXT,
    name TEXT
);
CREATE TABLE labels (
    message_id INTEGER,
    mailbox_id INTEGER,
    PRIMARY KEY (message_id, mailbox_id)
) WITHOUT ROWID;
"""


class EnvelopeDB:
    """Helper for tests that need to seed the Envelope Index."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._address_ids: dict[tuple[str, str], int] = {}
        self._subject_ids: dict[str, int] = {}

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def add_mailbox(self, url: str, source: int | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO mailboxes (url, source) VALUES (?, ?)",
                (url, source),
            )
            return int(cur.lastrowid)

    def _get_or_create_subject(self, subject: str) -> int:
        if subject in self._subject_ids:
            return self._subject_ids[subject]
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO subjects (subject) VALUES (?)",
                (subject,),
            )
            if cur.lastrowid:
                sid = int(cur.lastrowid)
            else:
                row = c.execute(
                    "SELECT ROWID FROM subjects WHERE subject = ?",
                    (subject,),
                ).fetchone()
                sid = int(row["ROWID"])
            self._subject_ids[subject] = sid
            return sid

    def _get_or_create_address(self, address: str, comment: str = "") -> int:
        key = (address, comment)
        if key in self._address_ids:
            return self._address_ids[key]
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO addresses (address, comment) VALUES (?, ?)",
                (address, comment),
            )
            if cur.lastrowid:
                aid = int(cur.lastrowid)
            else:
                row = c.execute(
                    "SELECT ROWID FROM addresses WHERE address = ? AND comment = ?",
                    (address, comment),
                ).fetchone()
                aid = int(row["ROWID"])
            self._address_ids[key] = aid
            return aid

    def add_message(
        self,
        *,
        mailbox_rowid: int,
        subject: str,
        sender: str,
        sender_name: str = "",
        date_received: int = 1700000000,
        read: bool = False,
        flagged: bool = False,
        to: list[tuple[str, str]] | None = None,
        cc: list[tuple[str, str]] | None = None,
        bcc: list[tuple[str, str]] | None = None,
        labels: list[int] | None = None,
        attachments: list[str] | None = None,
        deleted: bool = False,
        subject_prefix: str | None = None,
    ) -> int:
        # Resolve all FK IDs up-front on their own connections, then do the
        # whole message insert in a single transaction. Avoids nested
        # "database is locked" when the outer INSERT transaction is still
        # open and the helpers try to open a second connection.
        subject_id = self._get_or_create_subject(subject)
        sender_id = self._get_or_create_address(sender, sender_name)
        recipient_triples: list[tuple[int, int, int]] = []  # (rtype, addr_id, position)
        for rtype, recipients in ((0, to or []), (1, cc or []), (2, bcc or [])):
            for pos, (addr, name) in enumerate(recipients):
                recipient_triples.append((rtype, self._get_or_create_address(addr, name), pos))

        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO messages
                (sender, subject_prefix, subject, date_received, mailbox,
                 read, flagged, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sender_id, subject_prefix, subject_id, date_received,
                 mailbox_rowid, int(read), int(flagged), int(deleted)),
            )
            msg_id = int(cur.lastrowid)
            for rtype, addr_id, pos in recipient_triples:
                c.execute(
                    "INSERT INTO recipients (message, address, type, position) "
                    "VALUES (?, ?, ?, ?)",
                    (msg_id, addr_id, rtype, pos),
                )
            for mbox_id in labels or []:
                c.execute(
                    "INSERT INTO labels (message_id, mailbox_id) VALUES (?, ?)",
                    (msg_id, mbox_id),
                )
            for attach in attachments or []:
                c.execute(
                    "INSERT INTO attachments (message, attachment_id, name) "
                    "VALUES (?, ?, ?)",
                    (msg_id, f"att-{msg_id}-{attach}", attach),
                )
            return msg_id


# Stable test UUIDs — callers can reference these by name.
TEST_ACCOUNT_ALICE_UUID = "00000000-0000-0000-0000-000000000001"
TEST_ACCOUNT_BOB_UUID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _default_empty_envelope_db(tmp_path, monkeypatch, request):
    """Auto-applied: point sqlite_engine at an empty DB unless a test opts in.

    Without this, a test that runs a read-path command would hit the user's
    real Mail.app Envelope Index. The fixture also pins a predictable
    account map so ``uuid_for_name`` doesn't call out to real Mail.app.

    Tests that need seeded data request the ``envelope_db`` fixture
    explicitly — that overrides this empty default.
    """
    # If the test explicitly asks for envelope_db or seeded_envelope_db,
    # those fixtures set up their own DB — skip this default.
    requested = request.fixturenames
    if "envelope_db" in requested or "seeded_envelope_db" in requested:
        yield
        return

    db_path = str(tmp_path / "empty-envelope-index.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    from pathlib import Path as _Path
    monkeypatch.setattr(
        "mailctl.sqlite_engine.envelope_index_path",
        lambda: _Path(db_path),
    )

    # Fake account map so fetch_messages / search don't call real AppleScript
    # for UUID↔name mapping. These names are stable for assertions.
    from mailctl.account_map import Account, get_account_map

    def _fake_map() -> tuple[Account, ...]:
        return (
            Account(uuid=TEST_ACCOUNT_ALICE_UUID, name="PersonalAccount"),
            Account(uuid=TEST_ACCOUNT_BOB_UUID, name="WorkAccount"),
        )

    get_account_map.cache_clear()
    monkeypatch.setattr("mailctl.account_map.get_account_map", _fake_map)
    yield


@pytest.fixture
def envelope_db(tmp_path, monkeypatch) -> EnvelopeDB:
    """Build an in-memory-ish Envelope Index and wire the engine to use it.

    Also patches :func:`mailctl.account_map.get_account_map` to return a
    deterministic mapping so SQLite-backed commands don't shell out to
    real Mail.app for account identity.
    """
    db_path = str(tmp_path / "EnvelopeIndex.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    # Point the engine's path resolver and connection opener at our DB.
    from pathlib import Path as _Path
    monkeypatch.setattr(
        "mailctl.sqlite_engine.envelope_index_path",
        lambda: _Path(db_path),
    )

    # Pin the account map. Individual tests can clear + re-pin with
    # different accounts by calling account_map.clear_cache().
    from mailctl.account_map import Account, get_account_map

    def _fake_map() -> tuple[Account, ...]:
        return (
            Account(uuid=TEST_ACCOUNT_ALICE_UUID, name="Alice"),
            Account(uuid=TEST_ACCOUNT_BOB_UUID, name="Bob"),
        )

    get_account_map.cache_clear()
    monkeypatch.setattr("mailctl.account_map.get_account_map", _fake_map)

    return EnvelopeDB(db_path)


@pytest.fixture
def seeded_envelope_db(envelope_db) -> EnvelopeDB:
    """Envelope DB pre-populated with a handful of messages across two accounts.

    Use this for tests that just need *some* realistic data. Tests that
    care about specific rows should start from ``envelope_db`` and seed
    exactly what they need.
    """
    inbox_alice = envelope_db.add_mailbox(
        f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX"
    )
    drafts_alice = envelope_db.add_mailbox(
        f"imap://{TEST_ACCOUNT_ALICE_UUID}/Drafts"
    )
    inbox_bob = envelope_db.add_mailbox(
        f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox"
    )

    # A few messages in Alice's INBOX with varied dates and senders.
    envelope_db.add_message(
        mailbox_rowid=inbox_alice,
        subject="Weekly report",
        sender="alice@example.com",
        sender_name="Alice Admin",
        date_received=1700000100,
        read=False,
        to=[("bob@example.com", "Bob")],
    )
    envelope_db.add_message(
        mailbox_rowid=inbox_alice,
        subject="Your receipt from Acme",
        sender="billing@acme.example",
        sender_name="Acme Billing",
        date_received=1700000200,
        read=True,
        attachments=["receipt.pdf"],
    )
    envelope_db.add_message(
        mailbox_rowid=inbox_bob,
        subject="Cross-account test",
        sender="someone@example.com",
        sender_name="Someone",
        date_received=1700000300,
        read=False,
    )
    envelope_db.add_message(
        mailbox_rowid=drafts_alice,
        subject="draft in progress",
        sender="alice@example.com",
        sender_name="Alice Admin",
        date_received=1700000400,
        to=[("bob@example.com", "Bob")],
    )

    return envelope_db
