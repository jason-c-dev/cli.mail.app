"""Unit tests for 'mailctl messages list' and 'mailctl messages show'.

Covers:
- messages list: success with multiple messages, each filter option (--unread,
  --from, --subject, --since, --before, --limit), --json output, default INBOX,
  --account and --mailbox targeting, batch call assertion, combined filters,
  date sorting.
- messages show: full message display, attachment metadata, --headers flag,
  --raw flag, --json output, message-not-found error, positional argument.
- Error handling: Mail.app not running, stderr/stdout separation.
- Help text: subcommand and option listings.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.messages import (
    build_messages_list_script,
    build_message_show_script,
    parse_messages_list_output,
    parse_message_show_output,
    DEFAULT_LIMIT,
)

_click_app = typer.main.get_command(app)
runner = CliRunner()

# --------------------------------------------------------------------------- #
# Canned osascript output — list messages
# --------------------------------------------------------------------------- #

# 5 messages with varied attributes for filtering tests
FIVE_MESSAGES_OUTPUT = (
    "101||Friday, January 10, 2025 at 9:30:00 AM||alice@example.com||Invoice #1234||false||true\n"
    "102||Thursday, January 9, 2025 at 2:15:00 PM||bob@example.com||Meeting Notes||true||false\n"
    "103||Wednesday, January 8, 2025 at 11:00:00 AM||alice@example.com||Re: Project Update||false||false\n"
    "104||Monday, December 15, 2024 at 8:45:00 AM||charlie@example.com||Holiday Plans||true||true\n"
    "105||Tuesday, February 4, 2025 at 3:20:00 PM||bob@example.com||Invoice Summary||false||false"
)

THREE_MESSAGES_OUTPUT = (
    "201||Friday, January 10, 2025 at 9:30:00 AM||sender1@test.com||Hello World||false||false\n"
    "202||Thursday, January 9, 2025 at 2:15:00 PM||sender2@test.com||Test Message||true||true\n"
    "203||Wednesday, January 8, 2025 at 11:00:00 AM||sender3@test.com||Greetings||false||true"
)

# Canned output for show message
FULL_MESSAGE_OUTPUT = (
    "12345||Friday, January 10, 2025 at 9:30:00 AM||alice@example.com||"
    "bob@example.com, carol@example.com||dave@example.com||||"
    "Invoice #1234||false||true\n"
    "@@BODY@@\n"
    "Hi Bob,\n\nPlease find the invoice attached.\n\nBest,\nAlice\n"
    "@@HEADERS@@\n"
    "Message-ID: <abc123@example.com>\n"
    "Date: Fri, 10 Jan 2025 09:30:00 -0500\n"
    "From: alice@example.com\n"
    "To: bob@example.com\n"
    "Subject: Invoice #1234\n"
    "Content-Type: multipart/mixed\n"
    "@@ATTACHMENTS@@\n"
    "invoice.pdf||45678||application/pdf\n"
    "receipt.png||12345||image/png"
)

MESSAGE_NO_ATTACHMENTS_OUTPUT = (
    "99999||Thursday, January 9, 2025 at 2:15:00 PM||bob@example.com||"
    "alice@example.com||||"
    "Meeting Notes||true||false\n"
    "@@BODY@@\n"
    "Let's meet at 3pm tomorrow.\n"
    "@@HEADERS@@\n"
    "Message-ID: <def456@example.com>\n"
    "Date: Thu, 9 Jan 2025 14:15:00 -0500\n"
    "From: bob@example.com\n"
    "To: alice@example.com\n"
    "Subject: Meeting Notes\n"
    "@@ATTACHMENTS@@\n"
)

# Message with HTML body for --raw testing
MESSAGE_HTML_BODY_OUTPUT = (
    "77777||Friday, January 10, 2025 at 9:30:00 AM||sender@example.com||"
    "recipient@example.com||||"
    "HTML Test||true||false\n"
    "@@BODY@@\n"
    "<html><body><h1>Hello</h1><p>This is <b>bold</b> text.</p></body></html>\n"
    "@@HEADERS@@\n"
    "Content-Type: text/html\n"
    "@@ATTACHMENTS@@\n"
)


# =========================================================================== #
# MESSAGES LIST TESTS
# =========================================================================== #


class TestMessagesListSuccess:
    """C-051: 'mailctl messages list' runs without error."""

    def test_exit_code_zero(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list"])
        assert result.exit_code == 0

    def test_subjects_visible(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        subjects = [m["subject"] for m in data]
        assert "Hello World" in subjects
        assert "Test Message" in subjects
        assert "Greetings" in subjects

    def test_senders_visible(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        senders = [m["from"] for m in data]
        assert "sender1@test.com" in senders
        assert "sender2@test.com" in senders


class TestMessagesListTableColumns:
    """C-052: Table shows date, from, subject, read/unread, flagged, message ID."""

    def test_all_columns_visible(self, mock_osascript):
        """Verify all six data columns via JSON (avoids Rich table truncation)."""
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        msg = data[0]
        # All six fields present
        assert "date" in msg and msg["date"]
        assert "from" in msg and msg["from"]
        assert "subject" in msg and msg["subject"]
        assert "read" in msg and isinstance(msg["read"], bool)
        assert "flagged" in msg and isinstance(msg["flagged"], bool)
        assert "id" in msg and msg["id"]

    def test_message_ids_present(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        ids = {m["id"] for m in data}
        assert "201" in ids
        assert "202" in ids
        assert "203" in ids


class TestMessagesListDefaultMailbox:
    """C-053: Default is INBOX; --mailbox overrides."""

    def test_default_targets_inbox(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        runner.invoke(_click_app, ["messages", "list"])
        script = mock_osascript.last_script
        assert script is not None
        assert "INBOX" in script

    def test_mailbox_override(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        runner.invoke(_click_app, ["messages", "list", "--mailbox", "Sent"])
        script = mock_osascript.last_script
        assert script is not None
        assert '"Sent"' in script

    def test_both_exit_zero(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        r1 = runner.invoke(_click_app, ["messages", "list"])
        assert r1.exit_code == 0
        r2 = runner.invoke(_click_app, ["messages", "list", "--mailbox", "Drafts"])
        assert r2.exit_code == 0


class TestMessagesListAccount:
    """C-054: --account scopes listing to a specific account."""

    def test_account_in_script(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        runner.invoke(_click_app, ["messages", "list", "--account", "Work"])
        script = mock_osascript.last_script
        assert script is not None
        assert '"Work"' in script
        assert "account" in script.lower()

    def test_account_exit_zero(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--account", "Work"])
        assert result.exit_code == 0


class TestMessagesListUnreadFilter:
    """C-055: --unread filters to unread messages only."""

    def test_unread_filter(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--unread", "--json"])
        data = json.loads(result.stdout)
        # Messages 101, 103, 105 are unread (read=false)
        assert all(not m["read"] for m in data)
        assert len(data) == 3

    def test_unread_excludes_read(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--unread", "--json"])
        data = json.loads(result.stdout)
        ids = {m["id"] for m in data}
        # 102 and 104 are read — should be excluded
        assert "102" not in ids
        assert "104" not in ids


class TestMessagesListFromFilter:
    """C-056: --from filters by sender (case-insensitive substring)."""

    def test_from_filter_alice(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        assert all("alice" in m["from"].lower() for m in data)
        assert len(data) == 2  # alice has messages 101, 103

    def test_from_filter_case_insensitive(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--from", "Alice", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2

    def test_from_filter_excludes_others(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        ids = {m["id"] for m in data}
        assert "102" not in ids  # bob
        assert "104" not in ids  # charlie


class TestMessagesListSubjectFilter:
    """C-057: --subject filters by subject (case-insensitive substring)."""

    def test_subject_filter(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--subject", "invoice", "--json"])
        data = json.loads(result.stdout)
        assert all("invoice" in m["subject"].lower() for m in data)
        assert len(data) == 2  # 101 (Invoice #1234) and 105 (Invoice Summary)

    def test_subject_filter_case_insensitive(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--subject", "INVOICE", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2


class TestMessagesListDateFilters:
    """C-058: --since and --before filter by date range."""

    def test_since_filter(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app, ["messages", "list", "--since", "2025-01-01", "--json"]
        )
        data = json.loads(result.stdout)
        # Only 2025 messages: 101, 102, 103, 105 (not 104 which is Dec 2024)
        ids = {m["id"] for m in data}
        assert "104" not in ids
        assert len(data) == 4

    def test_before_filter(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app, ["messages", "list", "--before", "2025-01-10", "--json"]
        )
        data = json.loads(result.stdout)
        # Before Jan 10: 102 (Jan 9), 103 (Jan 8), 104 (Dec 15)
        ids = {m["id"] for m in data}
        assert "101" not in ids  # Jan 10 is not before Jan 10
        assert "105" not in ids  # Feb 4 is not before Jan 10
        assert len(data) == 3

    def test_since_and_before_combined(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--since", "2025-01-01", "--before", "2025-02-01", "--json"],
        )
        data = json.loads(result.stdout)
        # Jan 2025 only: 101 (Jan 10), 102 (Jan 9), 103 (Jan 8)
        ids = {m["id"] for m in data}
        assert "104" not in ids  # Dec 2024
        assert "105" not in ids  # Feb 2025
        assert len(data) == 3

    def test_since_alone_no_upper_bound(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app, ["messages", "list", "--since", "2025-01-09", "--json"]
        )
        data = json.loads(result.stdout)
        # Jan 9 and later: 101 (Jan 10), 102 (Jan 9), 105 (Feb 4)
        ids = {m["id"] for m in data}
        assert len(data) == 3
        assert "103" not in ids  # Jan 8
        assert "104" not in ids  # Dec 15

    def test_before_alone_no_lower_bound(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app, ["messages", "list", "--before", "2025-01-01", "--json"]
        )
        data = json.loads(result.stdout)
        # Before Jan 1 2025: only 104 (Dec 15 2024)
        ids = {m["id"] for m in data}
        assert len(data) == 1
        assert "104" in ids


class TestMessagesListLimit:
    """C-059: --limit caps results; default is 25."""

    def test_limit_caps_output(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--limit", "2", "--json"])
        data = json.loads(result.stdout)
        assert len(data) <= 2

    def test_default_limit_is_25(self):
        assert DEFAULT_LIMIT == 25

    def test_no_limit_flag_uses_default(self, mock_osascript):
        # Generate >25 messages
        lines = []
        for i in range(30):
            lines.append(
                f"{i}||Friday, January 10, 2025 at 9:30:00 AM||test@example.com||Msg {i}||false||false"
            )
        mock_osascript.set_output("\n".join(lines))
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 25


class TestMessagesListSorting:
    """C-060: Messages sorted by date descending (newest first)."""

    def test_newest_first(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json", "--limit", "50"])
        data = json.loads(result.stdout)
        # Expected order: 105 (Feb 4), 101 (Jan 10), 102 (Jan 9), 103 (Jan 8), 104 (Dec 15)
        ids = [m["id"] for m in data]
        assert ids == ["105", "101", "102", "103", "104"]


class TestMessagesListJSON:
    """C-061: --json outputs valid JSON with required fields."""

    def test_json_is_valid(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_json_has_all_fields(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        for msg in data:
            assert "id" in msg
            assert "date" in msg
            assert "from" in msg
            assert "subject" in msg
            assert "read" in msg
            assert "flagged" in msg

    def test_json_read_is_boolean(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list", "--json"])
        data = json.loads(result.stdout)
        for msg in data:
            assert isinstance(msg["read"], bool)
            assert isinstance(msg["flagged"], bool)

    def test_global_json_flag(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["--json", "messages", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestMessagesListBatchCall:
    """C-062: Exactly one osascript call for message listing."""

    def test_single_call(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        runner.invoke(_click_app, ["messages", "list"])
        assert len(mock_osascript.calls) == 1

    def test_single_call_with_filters(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        runner.invoke(
            _click_app,
            ["messages", "list", "--unread", "--from", "alice", "--json"],
        )
        assert len(mock_osascript.calls) == 1

    def test_single_call_with_json(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        runner.invoke(_click_app, ["messages", "list", "--json"])
        assert len(mock_osascript.calls) == 1


class TestMessagesListCombinedFilters:
    """C-077: Multiple filters applied conjunctively."""

    def test_unread_and_from_and_limit(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--unread", "--from", "alice", "--limit", "5", "--json"],
        )
        data = json.loads(result.stdout)
        # Unread alice messages: 101, 103
        assert all(not m["read"] for m in data)
        assert all("alice" in m["from"].lower() for m in data)
        assert len(data) == 2

    def test_unread_and_from_and_limit_capped(self, mock_osascript):
        mock_osascript.set_output(FIVE_MESSAGES_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--unread", "--from", "alice", "--limit", "1", "--json"],
        )
        data = json.loads(result.stdout)
        assert len(data) == 1


# =========================================================================== #
# MESSAGES SHOW TESTS
# =========================================================================== #


class TestMessagesShowSuccess:
    """C-063: 'mailctl messages show <id>' displays a full message."""

    def test_exit_code_zero(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert result.exit_code == 0

    def test_from_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "alice@example.com" in result.stdout

    def test_to_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "bob@example.com" in result.stdout

    def test_cc_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "dave@example.com" in result.stdout

    def test_subject_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "Invoice #1234" in result.stdout

    def test_body_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "Please find the invoice attached" in result.stdout

    def test_date_visible(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "January" in result.stdout or "2025" in result.stdout


class TestMessagesShowAttachments:
    """C-064: Attachment metadata displayed."""

    def test_attachment_filenames(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "invoice.pdf" in result.stdout
        assert "receipt.png" in result.stdout

    def test_attachment_sizes(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "45678" in result.stdout
        assert "12345" in result.stdout

    def test_attachment_mime_types(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "application/pdf" in result.stdout
        assert "image/png" in result.stdout


class TestMessagesShowHeaders:
    """C-065: --headers displays all message headers."""

    def test_headers_flag(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345", "--headers"])
        assert result.exit_code == 0
        assert "Message-ID" in result.stdout
        assert "Content-Type" in result.stdout

    def test_headers_separate_from_body(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345", "--headers"])
        # Headers section is distinct
        assert "Headers" in result.stdout
        assert "Body" in result.stdout


class TestMessagesShowRaw:
    """C-066: --raw displays unprocessed body without formatting."""

    def test_raw_outputs_body(self, mock_osascript):
        mock_osascript.set_output(MESSAGE_HTML_BODY_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "77777", "--raw"])
        assert result.exit_code == 0
        # Raw output should contain the HTML as-is
        assert "<html>" in result.stdout
        assert "<h1>Hello</h1>" in result.stdout

    def test_raw_differs_from_default(self, mock_osascript):
        mock_osascript.set_output(MESSAGE_HTML_BODY_OUTPUT)
        raw_result = runner.invoke(_click_app, ["messages", "show", "77777", "--raw"])
        mock_osascript.set_output(MESSAGE_HTML_BODY_OUTPUT)
        default_result = runner.invoke(_click_app, ["messages", "show", "77777"])
        # Raw output should be body only, default has headers/metadata
        assert "From:" in default_result.stdout
        assert "Subject:" in default_result.stdout
        # Raw is just body
        assert raw_result.stdout.strip().startswith("<html>")


class TestMessagesShowJSON:
    """C-067: --json outputs valid JSON with all message fields."""

    def test_json_valid(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)

    def test_json_has_all_fields(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345", "--json"])
        data = json.loads(result.stdout)
        for field in ["id", "date", "from", "to", "cc", "bcc", "subject",
                       "body", "attachments", "read", "flagged"]:
            assert field in data, f"Missing field: {field}"

    def test_json_attachments_structure(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345", "--json"])
        data = json.loads(result.stdout)
        assert isinstance(data["attachments"], list)
        assert len(data["attachments"]) == 2
        for att in data["attachments"]:
            assert "name" in att
            assert "size" in att
            assert "mime_type" in att


class TestMessagesShowPositionalArg:
    """C-068: message-id is a positional argument."""

    def test_positional_works(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert result.exit_code == 0

    def test_id_passed_to_script(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        runner.invoke(_click_app, ["messages", "show", "12345"])
        script = mock_osascript.last_script
        assert script is not None
        assert "12345" in script

    def test_no_id_produces_usage_error(self, mock_osascript):
        result = runner.invoke(_click_app, ["messages", "show"])
        assert result.exit_code != 0


# =========================================================================== #
# ERROR HANDLING TESTS
# =========================================================================== #


class TestMessagesShowNotFound:
    """C-069: Clear error for nonexistent message ID."""

    def test_nonexistent_id_exit_code(self, mock_osascript):
        mock_osascript.set_error("Can't get message id 999999.")
        result = runner.invoke(_click_app, ["messages", "show", "999999"])
        assert result.exit_code != 0

    def test_nonexistent_id_error_message(self, mock_osascript):
        mock_osascript.set_error("Can't get message id 999999.")
        result = runner.invoke(_click_app, ["messages", "show", "999999"])
        # Error message should be visible somewhere
        combined = result.stdout + result.stderr
        assert "999999" in combined or "error" in combined.lower()


class TestMessagesListMailNotRunning:
    """C-070: Clear error when Mail.app is not running."""

    def test_exit_code_nonzero(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "list"])
        assert result.exit_code != 0

    def test_error_mentions_mail(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "list"])
        combined = result.stdout + result.stderr
        assert "mail" in combined.lower() or "Mail" in combined


class TestMessagesStreamSeparation:
    """C-071: Data on stdout, errors on stderr."""

    def test_success_data_on_stdout(self, mock_osascript):
        mock_osascript.set_output(THREE_MESSAGES_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "list"])
        assert "Hello World" in result.stdout

    def test_error_on_stderr(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "list"])
        assert "Error" in result.stderr or "error" in result.stderr.lower()

    def test_show_data_on_stdout(self, mock_osascript):
        mock_osascript.set_output(FULL_MESSAGE_OUTPUT)
        result = runner.invoke(_click_app, ["messages", "show", "12345"])
        assert "alice@example.com" in result.stdout


# =========================================================================== #
# HELP TEXT TESTS
# =========================================================================== #


class TestMessagesHelp:
    """C-078: Help text for messages commands."""

    def test_messages_help_lists_subcommands(self):
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "show" in result.stdout

    def test_messages_list_help_describes_options(self):
        result = runner.invoke(_click_app, ["messages", "list", "--help"])
        assert result.exit_code == 0
        for opt in ["--mailbox", "--account", "--unread", "--from", "--subject",
                     "--since", "--before", "--limit", "--json"]:
            assert opt in result.stdout, f"Missing option {opt} in help"

    def test_messages_show_help_describes_options(self):
        result = runner.invoke(_click_app, ["messages", "show", "--help"])
        assert result.exit_code == 0
        assert "--headers" in result.stdout
        assert "--raw" in result.stdout
        assert "--json" in result.stdout
        # Should mention the positional argument
        assert "MESSAGE_ID" in result.stdout or "message" in result.stdout.lower()


# =========================================================================== #
# PARSING UNIT TESTS
# =========================================================================== #


class TestParseMessagesListOutput:
    """Direct unit tests for parse_messages_list_output."""

    def test_empty_input(self):
        assert parse_messages_list_output("") == []

    def test_whitespace_input(self):
        assert parse_messages_list_output("  \n  ") == []

    def test_parses_three_messages(self):
        result = parse_messages_list_output(THREE_MESSAGES_OUTPUT)
        assert len(result) == 3

    def test_parses_id(self):
        result = parse_messages_list_output(THREE_MESSAGES_OUTPUT)
        assert result[0]["id"] == "201"

    def test_parses_from(self):
        result = parse_messages_list_output(THREE_MESSAGES_OUTPUT)
        assert result[0]["from"] == "sender1@test.com"

    def test_read_parsed_as_bool(self):
        result = parse_messages_list_output(THREE_MESSAGES_OUTPUT)
        assert result[0]["read"] is False
        assert result[1]["read"] is True


class TestParseMessageShowOutput:
    """Direct unit tests for parse_message_show_output."""

    def test_parses_full_message(self):
        result = parse_message_show_output(FULL_MESSAGE_OUTPUT)
        assert result["id"] == "12345"
        assert result["from"] == "alice@example.com"
        assert result["subject"] == "Invoice #1234"

    def test_parses_body(self):
        result = parse_message_show_output(FULL_MESSAGE_OUTPUT)
        assert "Please find the invoice attached" in result["body"]

    def test_parses_attachments(self):
        result = parse_message_show_output(FULL_MESSAGE_OUTPUT)
        assert len(result["attachments"]) == 2
        assert result["attachments"][0]["name"] == "invoice.pdf"
        assert result["attachments"][0]["mime_type"] == "application/pdf"

    def test_parses_headers(self):
        result = parse_message_show_output(FULL_MESSAGE_OUTPUT)
        assert "Message-ID" in result["headers"]


class TestBuildMessagesListScript:
    """Verify the generated AppleScript is well-formed."""

    def test_script_contains_tell_mail(self):
        script = build_messages_list_script()
        assert 'tell application "Mail"' in script

    def test_script_queries_inbox_by_default(self):
        script = build_messages_list_script()
        assert "INBOX" in script

    def test_script_targets_account(self):
        script = build_messages_list_script(account="Work")
        assert '"Work"' in script
        assert "account" in script.lower()

    def test_script_targets_mailbox(self):
        script = build_messages_list_script(mailbox="Sent")
        assert '"Sent"' in script


class TestBuildMessageShowScript:
    """Verify the generated show AppleScript is well-formed."""

    def test_script_contains_tell_mail(self):
        script = build_message_show_script("12345")
        assert 'tell application "Mail"' in script

    def test_script_contains_message_id(self):
        script = build_message_show_script("12345")
        assert "12345" in script
