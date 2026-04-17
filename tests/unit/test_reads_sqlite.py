"""Behaviour tests for SQLite-backed read commands.

These tests replace the AppleScript-mock tests in ``test_messages.py``,
``test_search.py``, and ``test_mailboxes.py``. They drive the CLI via
Click's test runner against a synthetic in-memory Envelope Index so the
real SQL queries are exercised end-to-end.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from tests.conftest import (
    TEST_ACCOUNT_ALICE_UUID,
    TEST_ACCOUNT_BOB_UUID,
)

_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# mailboxes list
# --------------------------------------------------------------------------- #


class TestMailboxesList:
    def test_lists_both_accounts(self, envelope_db):
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_mailbox(f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox")
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert result.exit_code == 0, result.stderr
        assert "Alice" in result.output
        assert "Bob" in result.output
        assert "INBOX" in result.output
        assert "Inbox" in result.output

    def test_account_filter(self, envelope_db):
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_mailbox(f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox")
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "Alice"]
        )
        assert result.exit_code == 0, result.stderr
        assert "Alice" in result.output
        assert "Bob" not in result.output

    def test_unknown_account_errors(self, envelope_db):
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "Nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()

    def test_unknown_account_exit_code_is_usage_error(self, envelope_db):
        """Issue #6: unknown account must exit 2 (usage error), not 1.
        Matches the contract used by `messages list --mailbox Nowhere`
        and `messages show <bad-id>`, and what the mailctl skill
        promises. See
        https://github.com/jason-c-dev/cli.mail.app/issues/6."""
        from mailctl.errors import EXIT_USAGE_ERROR
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "Nonexistent"]
        )
        assert result.exit_code == EXIT_USAGE_ERROR, (
            f"expected exit {EXIT_USAGE_ERROR} (usage error), got "
            f"{result.exit_code}. stderr={result.stderr!r}"
        )

    def test_json_output(self, envelope_db):
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--json"]
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(m["account"] == "Alice" and m["name"] == "INBOX" for m in data)


# --------------------------------------------------------------------------- #
# messages list
# --------------------------------------------------------------------------- #


class TestMessagesList:
    def test_basic_list(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_message(
            mailbox_rowid=mbox,
            subject="Weekly report",
            sender="alice@example.com",
            sender_name="Alice",
            date_received=1700000100,
        )
        # Assert on JSON so the test isn't sensitive to terminal-width truncation.
        result = runner.invoke(
            _click_app, ["messages", "list", "--account", "Alice", "--json"]
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["subject"] == "Weekly report"
        assert "Alice" in data[0]["from"]

    def test_unread_filter(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_message(
            mailbox_rowid=mbox, subject="Read", sender="a@b.c", read=True
        )
        envelope_db.add_message(
            mailbox_rowid=mbox, subject="Unread", sender="a@b.c", read=False
        )
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--unread", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["subject"] == "Unread"

    def test_subject_filter_case_insensitive(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_message(
            mailbox_rowid=mbox, subject="Your Receipt from Acme", sender="a@b.c"
        )
        envelope_db.add_message(
            mailbox_rowid=mbox, subject="Meeting notes", sender="a@b.c"
        )
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--subject", "receipt", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert "Receipt" in data[0]["subject"]

    def test_from_filter(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_message(mailbox_rowid=mbox, subject="A", sender="alice@example.com")
        envelope_db.add_message(mailbox_rowid=mbox, subject="B", sender="bob@example.com")
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--from", "bob", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["subject"] == "B"

    def test_date_filters(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        # 2024-01-10 at midnight UTC ≈ 1704844800; but we build in local time.
        import datetime
        jan_10 = int(datetime.datetime(2024, 1, 10, 9, 0).timestamp())
        feb_10 = int(datetime.datetime(2024, 2, 10, 9, 0).timestamp())
        mar_10 = int(datetime.datetime(2024, 3, 10, 9, 0).timestamp())
        envelope_db.add_message(mailbox_rowid=mbox, subject="Jan", sender="a@b.c", date_received=jan_10)
        envelope_db.add_message(mailbox_rowid=mbox, subject="Feb", sender="a@b.c", date_received=feb_10)
        envelope_db.add_message(mailbox_rowid=mbox, subject="Mar", sender="a@b.c", date_received=mar_10)

        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--since", "2024-02-01", "--before", "2024-03-01", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["subject"] == "Feb"

    def test_sorted_newest_first(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        envelope_db.add_message(mailbox_rowid=mbox, subject="Older", sender="a@b.c", date_received=1700000100)
        envelope_db.add_message(mailbox_rowid=mbox, subject="Newest", sender="a@b.c", date_received=1700000300)
        envelope_db.add_message(mailbox_rowid=mbox, subject="Middle", sender="a@b.c", date_received=1700000200)
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert [m["subject"] for m in data] == ["Newest", "Middle", "Older"]

    def test_limit(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        for i in range(10):
            envelope_db.add_message(
                mailbox_rowid=mbox, subject=f"msg {i}", sender="a@b.c",
                date_received=1700000000 + i,
            )
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--limit", "3", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 3

    def test_unknown_mailbox_errors(self, envelope_db):
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--mailbox", "Nonexistent"],
        )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()

    def test_unknown_account_errors(self, envelope_db):
        result = runner.invoke(
            _click_app, ["messages", "list", "--account", "Nowhere"]
        )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()

    def test_gmail_label_indirection(self, envelope_db):
        """Gmail's INBOX is a label over All Mail. Messages appear under both."""
        all_mail = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/%5BGmail%5D/All%20Mail"
        )
        inbox = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX", source=all_mail,
        )
        envelope_db.add_message(
            mailbox_rowid=all_mail,
            subject="Stored in All Mail but labelled INBOX",
            sender="a@b.c",
            labels=[inbox],
        )
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--account", "Alice", "--mailbox", "INBOX", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert "INBOX" in data[0].get("subject", "") or data[0].get("subject")


# --------------------------------------------------------------------------- #
# messages search
# --------------------------------------------------------------------------- #


class TestMessagesSearch:
    def test_cross_account_search(self, envelope_db):
        alice_inbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        bob_inbox = envelope_db.add_mailbox(f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox")
        envelope_db.add_message(
            mailbox_rowid=alice_inbox, subject="Receipt from Alice's vendor", sender="v1@x.y"
        )
        envelope_db.add_message(
            mailbox_rowid=bob_inbox, subject="Receipt from Bob's vendor", sender="v2@x.y"
        )
        envelope_db.add_message(
            mailbox_rowid=alice_inbox, subject="Unrelated email", sender="u@x.y"
        )
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--subject", "receipt", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 2
        accounts = {m["account"] for m in data}
        assert accounts == {"Alice", "Bob"}

    def test_scoped_to_account(self, envelope_db):
        alice_inbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        bob_inbox = envelope_db.add_mailbox(f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox")
        envelope_db.add_message(mailbox_rowid=alice_inbox, subject="Receipt A", sender="v@x.y")
        envelope_db.add_message(mailbox_rowid=bob_inbox, subject="Receipt B", sender="v@x.y")
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--account", "Alice", "--subject", "receipt", "--json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["account"] == "Alice"

    def test_body_filter_not_supported(self, envelope_db):
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--body", "anything"],
        )
        # Should fail with a clear message, not crash silently.
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# drafts list
# --------------------------------------------------------------------------- #


class TestDraftsList:
    def test_lists_drafts_across_accounts(self, envelope_db):
        alice_drafts = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/Drafts"
        )
        bob_drafts = envelope_db.add_mailbox(
            f"ews://{TEST_ACCOUNT_BOB_UUID}/Drafts"
        )
        envelope_db.add_message(
            mailbox_rowid=alice_drafts, subject="alice draft", sender="alice@x.y",
            to=[("bob@x.y", "Bob")],
        )
        envelope_db.add_message(
            mailbox_rowid=bob_drafts, subject="bob draft", sender="bob@x.y",
        )
        result = runner.invoke(_click_app, ["drafts", "list", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        accounts = {d["account"] for d in data}
        assert accounts == {"Alice", "Bob"}
        subjects = {d["subject"] for d in data}
        assert subjects == {"alice draft", "bob draft"}

    def test_filter_by_account(self, envelope_db):
        alice_drafts = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/Drafts"
        )
        bob_drafts = envelope_db.add_mailbox(
            f"ews://{TEST_ACCOUNT_BOB_UUID}/Drafts"
        )
        envelope_db.add_message(mailbox_rowid=alice_drafts, subject="A", sender="a@x.y")
        envelope_db.add_message(mailbox_rowid=bob_drafts, subject="B", sender="b@x.y")
        result = runner.invoke(
            _click_app, ["drafts", "list", "--account", "Alice", "--json"]
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["subject"] == "A"


# --------------------------------------------------------------------------- #
# messages show
# --------------------------------------------------------------------------- #


class TestMessagesShow:
    def test_unknown_id_errors(self, envelope_db):
        result = runner.invoke(_click_app, ["messages", "show", "99999"])
        assert result.exit_code != 0
        assert "99999" in result.stderr or "not found" in result.stderr.lower()

    def test_non_numeric_id_errors(self, envelope_db):
        result = runner.invoke(_click_app, ["messages", "show", "not-a-number"])
        assert result.exit_code != 0

    def test_shows_headers_and_recipients(self, envelope_db):
        mbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        msg_id = envelope_db.add_message(
            mailbox_rowid=mbox,
            subject="Important",
            sender="alice@example.com",
            sender_name="Alice Admin",
            to=[("bob@example.com", "Bob"), ("carol@example.com", "Carol")],
            cc=[("dan@example.com", "Dan")],
            attachments=["invoice.pdf"],
        )
        result = runner.invoke(_click_app, ["messages", "show", str(msg_id), "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.output)
        assert data["subject"] == "Important"
        assert "Alice Admin" in data["from"]
        assert "bob@example.com" in data["to"]
        assert "carol@example.com" in data["to"]
        assert "dan@example.com" in data["cc"]
        assert data["bcc"] == ""
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["name"] == "invoice.pdf"


# --------------------------------------------------------------------------- #
# Envelope Index parse helpers
# --------------------------------------------------------------------------- #


class TestUrlParsing:
    def test_parse_imap_url_with_brackets(self):
        from mailctl.sqlite_engine import parse_mailbox_url
        scheme, uuid, path = parse_mailbox_url(
            "imap://11111111-2222-3333-4444-555555555555/%5BGmail%5D/All%20Mail"
        )
        assert scheme == "imap"
        assert uuid == "11111111-2222-3333-4444-555555555555"
        assert path == "[Gmail]/All Mail"

    def test_parse_ews_url(self):
        from mailctl.sqlite_engine import parse_mailbox_url
        scheme, uuid, path = parse_mailbox_url(
            "ews://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/Inbox"
        )
        assert scheme == "ews"
        assert uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert path == "Inbox"

    def test_friendly_name_strips_prefix(self):
        from mailctl.sqlite_engine import friendly_mailbox_name
        assert friendly_mailbox_name(
            "imap://UUID/%5BGmail%5D/All%20Mail"
        ) == "All Mail"
        assert friendly_mailbox_name(
            "ews://UUID/Inbox"
        ) == "Inbox"


# --------------------------------------------------------------------------- #
# Engine-level tests
# --------------------------------------------------------------------------- #


class TestSqliteEngine:
    def test_check_schema_detects_missing_tables(self, tmp_path, monkeypatch):
        """If Apple changes the schema, check_schema should surface it."""
        import sqlite3
        db = tmp_path / "broken.sqlite"
        conn = sqlite3.connect(str(db))
        # Create a DB missing several required tables.
        conn.execute("CREATE TABLE messages (ROWID INTEGER)")
        conn.commit()
        conn.close()
        from pathlib import Path as _P
        monkeypatch.setattr(
            "mailctl.sqlite_engine.envelope_index_path", lambda: _P(str(db))
        )
        from mailctl.sqlite_engine import check_schema
        missing = check_schema()
        # At minimum these should be missing.
        assert "subjects" in missing
        assert "addresses" in missing

    def test_resolve_target_mailboxes_finds_by_account(self, envelope_db):
        a_inbox = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        b_inbox = envelope_db.add_mailbox(f"ews://{TEST_ACCOUNT_BOB_UUID}/Inbox")
        from mailctl.sqlite_engine import resolve_target_mailboxes
        storage, labels = resolve_target_mailboxes(account_uuid=TEST_ACCOUNT_ALICE_UUID)
        assert storage == [a_inbox]
        assert labels == []

    def test_resolve_target_mailboxes_separates_label_mailboxes(self, envelope_db):
        all_mail = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/%5BGmail%5D/All%20Mail"
        )
        inbox = envelope_db.add_mailbox(
            f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX", source=all_mail,
        )
        from mailctl.sqlite_engine import resolve_target_mailboxes
        storage, labels = resolve_target_mailboxes(mailbox_name="INBOX")
        assert storage == []
        assert labels == [inbox]
