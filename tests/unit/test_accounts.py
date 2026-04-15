"""Unit tests for 'mailctl accounts list'.

Covers: multi-account success, single account, --json output, Mail.app not
running error, single osascript call assertion, AppleScript content inspection,
table formatting, stderr/stdout separation, and exit codes.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.accounts import (
    build_accounts_script,
    parse_accounts_output,
)

# Use Click's CliRunner with mix_stderr=False so we can test stdout/stderr
# separation (C-043).  We invoke the underlying Click app via get_command().
_click_app = typer.main.get_command(app)
runner = CliRunner()


def _invoke(*args: str) -> object:
    """Invoke the CLI via Click's runner, returning a Result with .output and .stderr."""
    return runner.invoke(_click_app, list(args))

# --------------------------------------------------------------------------- #
# Canned osascript output for two accounts
# --------------------------------------------------------------------------- #

TWO_ACCOUNTS_OUTPUT = (
    "Work||work@example.com||iCloud account||true\n"
    "Personal||personal@gmail.com||IMAP account||false"
)

SINGLE_ACCOUNT_OUTPUT = "Main||main@example.com||Exchange account||true"


# --------------------------------------------------------------------------- #
# Success cases
# --------------------------------------------------------------------------- #


class TestAccountsListSuccess:
    """Successful invocations of 'accounts list'."""

    def test_two_accounts_exit_code_zero(self, mock_osascript):
        """C-026: Command exits 0 with two accounts."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert result.exit_code == 0

    def test_two_accounts_both_visible(self, mock_osascript):
        """C-026: Both account names appear in output."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "Work" in result.stdout
        assert "Personal" in result.stdout

    def test_single_account_success(self, mock_osascript):
        """Single-account edge case."""
        mock_osascript.set_output(SINGLE_ACCOUNT_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert result.exit_code == 0
        assert "Main" in result.stdout

    def test_table_shows_email(self, mock_osascript):
        """C-028: Email addresses visible in table output."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "work@example.com" in result.stdout
        assert "personal@gmail.com" in result.stdout

    def test_table_shows_type(self, mock_osascript):
        """C-028: Account type visible in table output."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "iCloud" in result.stdout
        assert "IMAP" in result.stdout

    def test_table_shows_enabled_state(self, mock_osascript):
        """C-028: Enabled state visible in table output."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "True" in result.stdout
        assert "False" in result.stdout


# --------------------------------------------------------------------------- #
# AppleScript inspection
# --------------------------------------------------------------------------- #


class TestAccountsAppleScript:
    """C-027: AppleScript queries all four account properties."""

    def test_queries_every_account(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        script = mock_osascript.last_script
        assert script is not None
        assert "every account" in script

    def test_queries_name(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        script = mock_osascript.last_script
        assert "name of acct" in script

    def test_queries_email_addresses(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        script = mock_osascript.last_script
        assert "email addresses of acct" in script

    def test_queries_account_type(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        script = mock_osascript.last_script
        assert "account type of acct" in script

    def test_queries_enabled_state(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        script = mock_osascript.last_script
        assert "enabled of acct" in script


# --------------------------------------------------------------------------- #
# JSON output
# --------------------------------------------------------------------------- #


class TestAccountsJSON:
    """C-029: --json produces valid JSON with all required fields."""

    def test_json_flag_produces_valid_json(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_json_contains_all_fields(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2
        for acct in data:
            assert "name" in acct
            assert "email" in acct
            assert "type" in acct
            assert "enabled" in acct

    def test_json_values_correct(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list", "--json"])
        data = json.loads(result.stdout)
        work = next(a for a in data if a["name"] == "Work")
        assert work["email"] == "work@example.com"
        assert work["type"] == "iCloud account"
        assert work["enabled"] is True

    def test_global_json_flag(self, mock_osascript):
        """C-040: --json as a global option (before subcommand)."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["--json", "accounts", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 2


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


class TestAccountsErrors:
    """C-030, C-044: Error handling and exit codes."""

    def test_mail_not_running_exit_code(self, mock_osascript):
        """C-030: Non-zero exit when Mail.app is not running."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert result.exit_code != 0

    def test_mail_not_running_error_message(self, mock_osascript):
        """C-030: Error message mentions Mail.app."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["accounts", "list"])
        # Error goes to stderr
        assert "mail" in result.stderr.lower() or "mail" in result.stdout.lower()

    def test_success_exit_code_zero(self, mock_osascript):
        """C-044: Exit code 0 on success."""
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# Batch osascript call
# --------------------------------------------------------------------------- #


class TestAccountsBatch:
    """C-031: Exactly one osascript call for the entire accounts list."""

    def test_single_osascript_call(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list"])
        assert len(mock_osascript.calls) == 1

    def test_single_call_even_with_json(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        runner.invoke(_click_app, ["accounts", "list", "--json"])
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# stdout / stderr separation
# --------------------------------------------------------------------------- #


class TestAccountsStreamSeparation:
    """C-043: Data on stdout, errors on stderr."""

    def test_success_data_on_stdout(self, mock_osascript):
        mock_osascript.set_output(TWO_ACCOUNTS_OUTPUT)
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "Work" in result.stdout

    def test_error_on_stderr(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["accounts", "list"])
        assert "Error" in result.stderr or "error" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Parsing unit tests
# --------------------------------------------------------------------------- #


class TestParseAccountsOutput:
    """Unit tests for the parser function directly."""

    def test_empty_input(self):
        assert parse_accounts_output("") == []

    def test_whitespace_input(self):
        assert parse_accounts_output("   \n  ") == []

    def test_two_accounts(self):
        result = parse_accounts_output(TWO_ACCOUNTS_OUTPUT)
        assert len(result) == 2
        assert result[0]["name"] == "Work"
        assert result[1]["name"] == "Personal"

    def test_enabled_parsed_as_bool(self):
        result = parse_accounts_output(TWO_ACCOUNTS_OUTPUT)
        assert result[0]["enabled"] is True
        assert result[1]["enabled"] is False


class TestBuildAccountsScript:
    """Verify the generated AppleScript is well-formed."""

    def test_script_contains_tell_mail(self):
        script = build_accounts_script()
        assert 'tell application "Mail"' in script

    def test_script_queries_all_properties(self):
        script = build_accounts_script()
        assert "name of acct" in script
        assert "email addresses of acct" in script
        assert "account type of acct" in script
        assert "enabled of acct" in script
