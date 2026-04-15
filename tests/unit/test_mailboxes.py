"""Unit tests for 'mailctl mailboxes list'.

Covers: multi-account mailboxes success, --account filter, --json output,
account-not-found error, batch osascript call assertion, table formatting,
stderr/stdout separation, and exit codes.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.mailboxes import (
    build_mailboxes_script,
    parse_mailboxes_output,
)

# Use Click's CliRunner with mix_stderr=False so we can test stdout/stderr
# separation (C-043).  We invoke the underlying Click app via get_command().
_click_app = typer.main.get_command(app)
runner = CliRunner()

# --------------------------------------------------------------------------- #
# Canned osascript output
# --------------------------------------------------------------------------- #

TWO_ACCOUNT_MAILBOXES = (
    "Work||INBOX||5||120\n"
    "Work||Sent||0||45\n"
    "Work||Drafts||2||3\n"
    "Personal||INBOX||12||300\n"
    "Personal||Sent||0||89\n"
    "Personal||Trash||0||15"
)

SINGLE_ACCOUNT_MAILBOXES = (
    "Main||INBOX||3||50\n"
    "Main||Archive||0||1000"
)


# --------------------------------------------------------------------------- #
# Success cases
# --------------------------------------------------------------------------- #


class TestMailboxesListSuccess:
    """Successful invocations of 'mailboxes list'."""

    def test_exit_code_zero(self, mock_osascript):
        """C-032: Command exits 0 with valid data."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert result.exit_code == 0

    def test_mailbox_names_visible(self, mock_osascript):
        """C-032: Mailbox names appear in output."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert "INBOX" in result.stdout
        assert "Sent" in result.stdout
        assert "Drafts" in result.stdout

    def test_table_shows_unread_count(self, mock_osascript):
        """C-033: Unread counts visible in table output."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        # Work INBOX has 5 unread, Personal INBOX has 12
        assert "5" in result.stdout
        assert "12" in result.stdout

    def test_table_shows_message_count(self, mock_osascript):
        """C-033: Message counts visible in table output."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert "120" in result.stdout
        assert "300" in result.stdout

    def test_table_shows_mailbox_name(self, mock_osascript):
        """C-033: Mailbox names visible in table."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert "INBOX" in result.stdout
        assert "Trash" in result.stdout


# --------------------------------------------------------------------------- #
# All accounts shown with attribution
# --------------------------------------------------------------------------- #


class TestMailboxesAllAccounts:
    """C-035: Without --account, mailboxes from all accounts are shown."""

    def test_both_accounts_present(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert "Work" in result.stdout
        assert "Personal" in result.stdout

    def test_account_attribution_per_row(self, mock_osascript):
        """Each mailbox row identifies its account."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--json"])
        data = json.loads(result.stdout)
        for mbox in data:
            assert "account" in mbox
            assert mbox["account"] in ("Work", "Personal")


# --------------------------------------------------------------------------- #
# --account filter
# --------------------------------------------------------------------------- #


class TestMailboxesAccountFilter:
    """C-034: --account filters to a specific account."""

    def test_filters_to_work(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--account", "Work"])
        assert result.exit_code == 0
        # Work mailboxes present
        assert "INBOX" in result.stdout
        assert "Drafts" in result.stdout

    def test_excludes_other_account(self, mock_osascript):
        """Personal mailboxes should not appear when filtering to Work."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--account", "Work", "--json"])
        data = json.loads(result.stdout)
        accounts = {m["account"] for m in data}
        assert accounts == {"Work"}
        # Trash is only in Personal
        names = {m["name"] for m in data}
        assert "Trash" not in names

    def test_filters_to_personal(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "Personal", "--json"]
        )
        data = json.loads(result.stdout)
        assert all(m["account"] == "Personal" for m in data)


# --------------------------------------------------------------------------- #
# JSON output
# --------------------------------------------------------------------------- #


class TestMailboxesJSON:
    """C-036: --json produces valid JSON with all required fields."""

    def test_json_flag_produces_valid_json(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_json_contains_all_fields(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 6
        for mbox in data:
            assert "name" in mbox
            assert "account" in mbox
            assert "unread_count" in mbox
            assert "message_count" in mbox

    def test_json_values_correct(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list", "--json"])
        data = json.loads(result.stdout)
        inbox = next(m for m in data if m["name"] == "INBOX" and m["account"] == "Work")
        assert inbox["unread_count"] == 5
        assert inbox["message_count"] == 120

    def test_global_json_flag(self, mock_osascript):
        """C-040: --json as a global option works for mailboxes too."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["--json", "mailboxes", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 6


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


class TestMailboxesErrors:
    """C-038: Account not found error."""

    def test_nonexistent_account_exit_code(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "NonExistent"]
        )
        assert result.exit_code != 0

    def test_nonexistent_account_error_message(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "NonExistent"]
        )
        assert "NonExistent" in result.stderr or "NonExistent" in result.stdout

    def test_mail_not_running(self, mock_osascript):
        """Mail.app not running produces a graceful error."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Batch osascript call
# --------------------------------------------------------------------------- #


class TestMailboxesBatch:
    """C-037: At most one osascript call per account (or fewer)."""

    def test_single_osascript_call_for_all(self, mock_osascript):
        """Our implementation uses exactly one call for all accounts."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        runner.invoke(_click_app, ["mailboxes", "list"])
        # We batch everything into one call — even better than per-account.
        assert len(mock_osascript.calls) == 1

    def test_single_call_with_account_filter(self, mock_osascript):
        """Even with --account, only one osascript call is made."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        runner.invoke(_click_app, ["mailboxes", "list", "--account", "Work"])
        assert len(mock_osascript.calls) == 1

    def test_call_count_at_most_num_accounts(self, mock_osascript):
        """C-037 threshold: at most one call per account."""
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        runner.invoke(_click_app, ["mailboxes", "list"])
        num_accounts = 2  # Work and Personal
        assert len(mock_osascript.calls) <= num_accounts


# --------------------------------------------------------------------------- #
# stdout / stderr separation
# --------------------------------------------------------------------------- #


class TestMailboxesStreamSeparation:
    """C-043: Data on stdout, errors on stderr."""

    def test_success_data_on_stdout(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(_click_app, ["mailboxes", "list"])
        assert "INBOX" in result.stdout

    def test_error_on_stderr(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNT_MAILBOXES)
        result = runner.invoke(
            _click_app, ["mailboxes", "list", "--account", "NonExistent"]
        )
        assert "Error" in result.stderr or "NonExistent" in result.stderr


# --------------------------------------------------------------------------- #
# Parsing unit tests
# --------------------------------------------------------------------------- #


class TestParseMailboxesOutput:
    """Unit tests for the parser function directly."""

    def test_empty_input(self):
        assert parse_mailboxes_output("") == []

    def test_whitespace_input(self):
        assert parse_mailboxes_output("  \n  ") == []

    def test_parses_two_account_data(self):
        result = parse_mailboxes_output(TWO_ACCOUNT_MAILBOXES)
        assert len(result) == 6

    def test_parses_account_name(self):
        result = parse_mailboxes_output(TWO_ACCOUNT_MAILBOXES)
        assert result[0]["account"] == "Work"
        assert result[3]["account"] == "Personal"

    def test_parses_unread_as_int(self):
        result = parse_mailboxes_output(TWO_ACCOUNT_MAILBOXES)
        assert isinstance(result[0]["unread_count"], int)
        assert result[0]["unread_count"] == 5

    def test_parses_message_count_as_int(self):
        result = parse_mailboxes_output(TWO_ACCOUNT_MAILBOXES)
        assert isinstance(result[0]["message_count"], int)
        assert result[0]["message_count"] == 120


class TestBuildMailboxesScript:
    """Verify the generated AppleScript is well-formed."""

    def test_script_contains_tell_mail(self):
        script = build_mailboxes_script()
        assert 'tell application "Mail"' in script

    def test_script_queries_mailbox_properties(self):
        script = build_mailboxes_script()
        assert "name of mbox" in script
        assert "unread count of mbox" in script
        assert "count of messages of mbox" in script
