"""Unit tests for 'mailctl messages search' — Cross-Account Search (Sprint 4).

Covers:
- C-081: search command exists and runs
- C-082: requires at least one search filter
- C-083: --from filter (case-insensitive substring)
- C-084: --subject filter (case-insensitive substring)
- C-085: --body filter (case-insensitive substring)
- C-086: --since / --before date range filters
- C-087: combined filters (AND logic)
- C-088: cross-account search (all accounts, account field in results)
- C-089: --account scoping
- C-090: --mailbox scoping
- C-091: mailbox field in results
- C-092: --limit and default of 25
- C-093: Rich table with expected columns
- C-094: --json output with required fields
- C-095: results sorted by date descending
- C-096: batch call efficiency (at most N+1 osascript calls)
- C-097: empty results (no error)
- C-098: clear error when Mail.app not running
- C-099: stdout for data, stderr for errors
- C-100: search --help lists all options
- C-101: messages --help lists search subcommand
- C-102: comprehensive test coverage (12+ tests)
- C-104: architecture — build/parse/fetch/render pattern
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="Legacy AppleScript-path tests; SQLite coverage in test_reads_sqlite.py"
)

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.messages import (
    build_account_names_script,
    build_search_script,
    parse_account_names_output,
    parse_search_output,
    DEFAULT_LIMIT,
)

_click_app = typer.main.get_command(app)
runner = CliRunner()

# --------------------------------------------------------------------------- #
# Canned osascript output — account names
# --------------------------------------------------------------------------- #

ACCOUNT_NAMES_OUTPUT = "Work\nPersonal"

# --------------------------------------------------------------------------- #
# Canned osascript output — search results per account
# --------------------------------------------------------------------------- #

# Work account: 3 messages across INBOX and Sent
WORK_SEARCH_OUTPUT = (
    "INBOX||301||Friday, January 10, 2025 at 9:30:00 AM||alice@example.com||Invoice #1234||false||true\n"
    "INBOX||302||Thursday, January 9, 2025 at 2:15:00 PM||bob@example.com||Meeting Notes||true||false\n"
    "Sent||303||Wednesday, January 8, 2025 at 11:00:00 AM||me@work.com||Re: Invoice #1234||true||false"
)

# Personal account: 2 messages in INBOX
PERSONAL_SEARCH_OUTPUT = (
    "INBOX||401||Tuesday, February 4, 2025 at 3:20:00 PM||alice@personal.com||Weekend Plans||false||false\n"
    "INBOX||402||Monday, December 15, 2024 at 8:45:00 AM||charlie@example.com||Holiday Greetings||true||true"
)

# Search results with body content for --body filter testing
WORK_SEARCH_WITH_BODY = (
    "INBOX||301||Friday, January 10, 2025 at 9:30:00 AM||alice@example.com||Invoice #1234||false||true||Please review this urgent invoice@@NL@@Thanks Alice\n"
    "INBOX||302||Thursday, January 9, 2025 at 2:15:00 PM||bob@example.com||Meeting Notes||true||false||See you at the meeting tomorrow\n"
    "Sent||303||Wednesday, January 8, 2025 at 11:00:00 AM||me@work.com||Re: Invoice #1234||true||false||Invoice acknowledged"
)

PERSONAL_SEARCH_WITH_BODY = (
    "INBOX||401||Tuesday, February 4, 2025 at 3:20:00 PM||alice@personal.com||Weekend Plans||false||false||Let's go hiking this weekend\n"
    "INBOX||402||Monday, December 15, 2024 at 8:45:00 AM||charlie@example.com||Holiday Greetings||true||true||This is an urgent reminder about the party"
)

# Empty search result
EMPTY_SEARCH_OUTPUT = ""

# Multiple messages for limit testing (8 messages in one account)
MANY_MESSAGES_OUTPUT = "\n".join(
    f"INBOX||{500+i}||Friday, January {10+i}, 2025 at 9:00:00 AM||test@example.com||Msg {i}||false||false"
    for i in range(8)
)


# =========================================================================== #
# C-081: SEARCH COMMAND EXISTS AND RUNS
# =========================================================================== #


class TestSearchCommandExists:
    """C-081: 'mailctl messages search' command exists and runs."""

    def test_exit_code_zero(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice"])
        assert result.exit_code == 0

    def test_output_contains_results(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        assert len(data) > 0

    def test_results_from_multiple_accounts(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        accounts = {m["account"] for m in data}
        assert "Work" in accounts
        assert "Personal" in accounts


# =========================================================================== #
# C-082: REQUIRES AT LEAST ONE SEARCH FILTER
# =========================================================================== #


class TestSearchRequiresFilter:
    """C-082: Running with no filters produces a usage error."""

    def test_no_filters_exit_code(self, mock_osascript):
        result = runner.invoke(_click_app, ["messages", "search"])
        assert result.exit_code != 0

    def test_no_filters_error_message(self, mock_osascript):
        result = runner.invoke(_click_app, ["messages", "search"])
        combined = result.stdout + result.stderr
        assert "search criterion" in combined.lower() or "at least one" in combined.lower()


# =========================================================================== #
# C-083: --from FILTER
# =========================================================================== #


class TestSearchFromFilter:
    """C-083: --from filters by sender (case-insensitive substring)."""

    def test_from_filter_matches(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        assert all("alice" in m["from"].lower() for m in data)
        assert len(data) == 2  # alice@example.com (Work) and alice@personal.com (Personal)

    def test_from_filter_excludes_non_matching(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        ids = {m["id"] for m in data}
        assert "302" not in ids  # bob
        assert "402" not in ids  # charlie

    def test_from_filter_case_insensitive(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "Alice", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2


# =========================================================================== #
# C-084: --subject FILTER
# =========================================================================== #


class TestSearchSubjectFilter:
    """C-084: --subject filters by subject (case-insensitive substring)."""

    def test_subject_filter_matches(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--subject", "invoice", "--json"])
        data = json.loads(result.stdout)
        assert all("invoice" in m["subject"].lower() for m in data)
        assert len(data) == 2  # Invoice #1234 and Re: Invoice #1234

    def test_subject_filter_case_insensitive(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--subject", "INVOICE", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2


# =========================================================================== #
# C-085: --body FILTER
# =========================================================================== #


class TestSearchBodyFilter:
    """C-085: --body filters by body content (case-insensitive substring)."""

    def test_body_filter_matches(self, mock_osascript):
        mock_osascript.set_outputs([
            ACCOUNT_NAMES_OUTPUT,
            WORK_SEARCH_WITH_BODY,
            PERSONAL_SEARCH_WITH_BODY,
        ])
        result = runner.invoke(_click_app, ["messages", "search", "--body", "urgent", "--json"])
        data = json.loads(result.stdout)
        # "urgent" appears in Work msg 301 body and Personal msg 402 body
        assert len(data) == 2
        ids = {m["id"] for m in data}
        assert "301" in ids
        assert "402" in ids

    def test_body_filter_case_insensitive(self, mock_osascript):
        mock_osascript.set_outputs([
            ACCOUNT_NAMES_OUTPUT,
            WORK_SEARCH_WITH_BODY,
            PERSONAL_SEARCH_WITH_BODY,
        ])
        result = runner.invoke(_click_app, ["messages", "search", "--body", "URGENT", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 2

    def test_body_filter_excludes_non_matching(self, mock_osascript):
        mock_osascript.set_outputs([
            ACCOUNT_NAMES_OUTPUT,
            WORK_SEARCH_WITH_BODY,
            PERSONAL_SEARCH_WITH_BODY,
        ])
        result = runner.invoke(_click_app, ["messages", "search", "--body", "hiking", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["id"] == "401"


# =========================================================================== #
# C-086: --since / --before DATE RANGE FILTERS
# =========================================================================== #


class TestSearchDateFilters:
    """C-086: --since and --before filter by date range."""

    def test_since_and_before_combined(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "test", "--since", "2025-01-01",
             "--before", "2025-02-01", "--json"],
        )
        # Passes --from test which won't match much, but let's use a broader filter
        # Actually let's test with a broader match
        pass

    def test_since_filter_alone(self, mock_osascript):
        """--since alone works (no upper bound)."""
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--since", "2025-01-01", "--json"],
        )
        data = json.loads(result.stdout)
        # Jan 8, Jan 9, Jan 10, Feb 4 (all 2025) — not Dec 15 2024
        ids = {m["id"] for m in data}
        assert "402" not in ids  # Dec 15 2024
        assert len(data) == 4

    def test_before_filter_alone(self, mock_osascript):
        """--before alone works (no lower bound)."""
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--before", "2025-01-01", "--json"],
        )
        data = json.loads(result.stdout)
        # Only Dec 15 2024 is before Jan 1 2025
        assert len(data) == 1
        assert data[0]["id"] == "402"

    def test_since_and_before_range(self, mock_osascript):
        """Both --since and --before constrain the range."""
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--since", "2025-01-01",
             "--before", "2025-02-01", "--json"],
        )
        data = json.loads(result.stdout)
        # Jan 8, 9, 10 (all 2025, before Feb) — not Dec 2024, not Feb 2025
        ids = {m["id"] for m in data}
        assert "402" not in ids  # Dec 2024
        assert "401" not in ids  # Feb 2025
        assert len(data) == 3


# =========================================================================== #
# C-087: COMBINED FILTERS (AND LOGIC)
# =========================================================================== #


class TestSearchCombinedFilters:
    """C-087: Multiple filters applied conjunctively."""

    def test_from_and_subject_combined(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "alice", "--subject", "invoice", "--json"],
        )
        data = json.loads(result.stdout)
        # Only alice + invoice: msg 301 (alice@example.com, Invoice #1234)
        assert len(data) == 1
        assert data[0]["id"] == "301"
        assert "alice" in data[0]["from"].lower()
        assert "invoice" in data[0]["subject"].lower()


# =========================================================================== #
# C-088: CROSS-ACCOUNT SEARCH (ALL ACCOUNTS)
# =========================================================================== #


class TestSearchCrossAccount:
    """C-088: Search spans all accounts; results include account field."""

    def test_results_from_all_accounts(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "a", "--json"])
        data = json.loads(result.stdout)
        accounts = {m["account"] for m in data}
        assert "Work" in accounts
        assert "Personal" in accounts

    def test_each_result_has_account_field(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "a", "--json"])
        data = json.loads(result.stdout)
        for msg in data:
            assert "account" in msg
            assert msg["account"] in ("Work", "Personal")


# =========================================================================== #
# C-089: --account SCOPING
# =========================================================================== #


class TestSearchAccountScoping:
    """C-089: --account scopes search to a single account."""

    def test_account_scoping(self, mock_osascript):
        # When --account is specified, only one search call is made (no account list call).
        mock_osascript.set_outputs([WORK_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "alice", "--account", "Work", "--json"],
        )
        data = json.loads(result.stdout)
        assert all(m["account"] == "Work" for m in data)

    def test_account_scoping_excludes_other_accounts(self, mock_osascript):
        mock_osascript.set_outputs([WORK_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "a", "--account", "Work", "--json"],
        )
        data = json.loads(result.stdout)
        for msg in data:
            assert msg["account"] == "Work"


# =========================================================================== #
# C-090: --mailbox SCOPING
# =========================================================================== #


class TestSearchMailboxScoping:
    """C-090: --mailbox scopes search to a specific mailbox."""

    def test_mailbox_in_script(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        runner.invoke(
            _click_app,
            ["messages", "search", "--from", "test", "--mailbox", "Sent"],
        )
        # Check that the search scripts target the Sent mailbox.
        # Calls: [0] = account names, [1] = Work search, [2] = Personal search
        assert len(mock_osascript.calls) >= 2
        # The search scripts (calls[1] and calls[2]) should mention "Sent"
        for call in mock_osascript.calls[1:]:
            script = call[2] if len(call) >= 3 else ""
            assert '"Sent"' in script


# =========================================================================== #
# C-091: MAILBOX FIELD IN RESULTS
# =========================================================================== #


class TestSearchMailboxField:
    """C-091: Each result includes a 'mailbox' field."""

    def test_mailbox_field_present(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "a", "--json"])
        data = json.loads(result.stdout)
        for msg in data:
            assert "mailbox" in msg
            assert msg["mailbox"]  # non-empty

    def test_mailbox_field_values(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "a", "--json"])
        data = json.loads(result.stdout)
        mailboxes = {m["mailbox"] for m in data}
        assert "INBOX" in mailboxes


# =========================================================================== #
# C-092: --limit AND DEFAULT
# =========================================================================== #


class TestSearchLimit:
    """C-092: --limit caps results; default is 25."""

    def test_limit_caps_output(self, mock_osascript):
        mock_osascript.set_outputs([
            "SingleAccount",
            MANY_MESSAGES_OUTPUT,
        ])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "test", "--limit", "2", "--json"],
        )
        data = json.loads(result.stdout)
        assert len(data) <= 2

    def test_default_limit_is_25(self):
        assert DEFAULT_LIMIT == 25

    def test_default_limit_applied(self, mock_osascript):
        # Generate >25 messages
        many_msgs = "\n".join(
            f"INBOX||{600+i}||Friday, January 10, 2025 at 9:00:00 AM||test@example.com||Msg {i}||false||false"
            for i in range(30)
        )
        mock_osascript.set_outputs(["TestAccount", many_msgs])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "test", "--json"])
        data = json.loads(result.stdout)
        assert len(data) == 25


# =========================================================================== #
# C-093: RICH TABLE OUTPUT
# =========================================================================== #


class TestSearchTableOutput:
    """C-093: Table shows account, mailbox, date, from, subject, and message ID."""

    def test_table_contains_account(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice"])
        # Table output should contain account info
        assert "Work" in result.stdout or "Personal" in result.stdout

    def test_table_contains_data_columns(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice"])
        # Should see account, mailbox, date, sender, subject, id in table headers or data
        assert "alice" in result.stdout.lower()
        assert "invoice" in result.stdout.lower() or "plans" in result.stdout.lower()


# =========================================================================== #
# C-094: --json OUTPUT
# =========================================================================== #


class TestSearchJSONOutput:
    """C-094: --json outputs valid JSON with required fields."""

    def test_json_is_valid_array(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_json_has_all_required_fields(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice", "--json"])
        data = json.loads(result.stdout)
        assert len(data) > 0
        for msg in data:
            assert "id" in msg
            assert "account" in msg
            assert "mailbox" in msg
            assert "date" in msg
            assert "from" in msg
            assert "subject" in msg
            assert "read" in msg and isinstance(msg["read"], bool)
            assert "flagged" in msg and isinstance(msg["flagged"], bool)

    def test_json_no_internal_body_field(self, mock_osascript):
        """Internal _body field should not appear in JSON output."""
        mock_osascript.set_outputs([
            ACCOUNT_NAMES_OUTPUT,
            WORK_SEARCH_WITH_BODY,
            PERSONAL_SEARCH_WITH_BODY,
        ])
        result = runner.invoke(_click_app, ["messages", "search", "--body", "urgent", "--json"])
        data = json.loads(result.stdout)
        for msg in data:
            assert "_body" not in msg


# =========================================================================== #
# C-095: SORTED BY DATE DESCENDING
# =========================================================================== #


class TestSearchSorting:
    """C-095: Results sorted newest-first, interleaved across accounts."""

    def test_newest_first(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "alice", "--json", "--limit", "50"],
        )
        data = json.loads(result.stdout)
        # Filtered by --from "alice": 301 (alice@example.com) and 401 (alice@personal.com)
        # Sorted by date descending: 401 (Feb 4) then 301 (Jan 10)
        ids = [m["id"] for m in data]
        assert ids == ["401", "301"]

    def test_interleaved_across_accounts(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "alice", "--json", "--limit", "50"],
        )
        data = json.loads(result.stdout)
        # Verify results are interleaved (not grouped by account).
        accounts_in_order = [m["account"] for m in data]
        # 401 is Personal (Feb 4), 301 is Work (Jan 10)
        assert accounts_in_order == ["Personal", "Work"]


# =========================================================================== #
# C-096: BATCH CALL EFFICIENCY
# =========================================================================== #


class TestSearchBatching:
    """C-096: At most N+1 osascript calls for N accounts."""

    def test_two_accounts_three_calls(self, mock_osascript):
        """2 accounts → at most 3 calls (1 for account list + 1 per account)."""
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        runner.invoke(_click_app, ["messages", "search", "--from", "test"])
        # 1 call for account names + 2 calls for searching = 3
        assert len(mock_osascript.calls) == 3

    def test_single_account_one_call(self, mock_osascript):
        """With --account, only 1 call (no account list needed)."""
        mock_osascript.set_outputs([WORK_SEARCH_OUTPUT])
        runner.invoke(_click_app, ["messages", "search", "--from", "test", "--account", "Work"])
        assert len(mock_osascript.calls) == 1

    def test_three_accounts_four_calls(self, mock_osascript):
        """3 accounts → 4 calls."""
        three_accounts = "Work\nPersonal\nSchool"
        mock_osascript.set_outputs([
            three_accounts,
            WORK_SEARCH_OUTPUT,
            PERSONAL_SEARCH_OUTPUT,
            EMPTY_SEARCH_OUTPUT,
        ])
        runner.invoke(_click_app, ["messages", "search", "--from", "test"])
        assert len(mock_osascript.calls) == 4


# =========================================================================== #
# C-097: EMPTY RESULTS
# =========================================================================== #


class TestSearchEmptyResults:
    """C-097: Empty results produce exit code 0, not an error."""

    def test_empty_json_is_empty_array(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, EMPTY_SEARCH_OUTPUT, EMPTY_SEARCH_OUTPUT])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "nonexistent", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == []

    def test_empty_table_exit_zero(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, EMPTY_SEARCH_OUTPUT, EMPTY_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "nonexistent"])
        assert result.exit_code == 0


# =========================================================================== #
# C-098: ERROR WHEN MAIL.APP NOT RUNNING
# =========================================================================== #


class TestSearchMailNotRunning:
    """C-098: Clear error when Mail.app is not running."""

    def test_exit_code_nonzero(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "search", "--from", "test"])
        assert result.exit_code != 0

    def test_error_mentions_mail(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "search", "--from", "test"])
        combined = result.stdout + result.stderr
        assert "mail" in combined.lower() or "Mail" in combined


# =========================================================================== #
# C-099: STDOUT FOR DATA, STDERR FOR ERRORS
# =========================================================================== #


class TestSearchStreamSeparation:
    """C-099: Data on stdout, errors on stderr."""

    def test_success_data_on_stdout(self, mock_osascript):
        mock_osascript.set_outputs([ACCOUNT_NAMES_OUTPUT, WORK_SEARCH_OUTPUT, PERSONAL_SEARCH_OUTPUT])
        result = runner.invoke(_click_app, ["messages", "search", "--from", "alice"])
        assert result.exit_code == 0
        # Data should be on stdout
        assert "alice" in result.stdout.lower() or "Search" in result.stdout

    def test_error_on_stderr(self, mock_osascript):
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(_click_app, ["messages", "search", "--from", "test"])
        assert "Error" in result.stderr or "error" in result.stderr.lower()


# =========================================================================== #
# C-100: HELP TEXT LISTS ALL OPTIONS
# =========================================================================== #


class TestSearchHelp:
    """C-100: 'mailctl messages search --help' describes all options."""

    def test_help_lists_all_options(self):
        result = runner.invoke(_click_app, ["messages", "search", "--help"])
        assert result.exit_code == 0
        for opt in ["--from", "--subject", "--body", "--since", "--before",
                     "--account", "--mailbox", "--limit", "--json"]:
            assert opt in result.stdout, f"Missing option {opt} in search help"


# =========================================================================== #
# C-101: MESSAGES --help LISTS SEARCH SUBCOMMAND
# =========================================================================== #


class TestMessagesHelpListsSearch:
    """C-101: 'mailctl messages --help' lists search alongside list and show."""

    def test_search_in_messages_help(self):
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert result.exit_code == 0
        assert "search" in result.stdout
        assert "list" in result.stdout
        assert "show" in result.stdout


# =========================================================================== #
# C-104: ARCHITECTURE — build/parse/fetch/render PATTERN
# =========================================================================== #


class TestSearchArchitecture:
    """C-104: Search follows build/parse/fetch/render pattern."""

    def test_build_search_script_exists(self):
        script = build_search_script(account="Test")
        assert 'tell application "Mail"' in script

    def test_build_search_script_targets_account(self):
        script = build_search_script(account="Work")
        assert '"Work"' in script

    def test_build_search_script_mailbox_scoping(self):
        script = build_search_script(account="Work", mailbox="Sent")
        assert '"Sent"' in script

    def test_build_search_script_include_body(self):
        script = build_search_script(account="Work", include_body=True)
        assert "content of msg" in script

    def test_parse_search_output_basic(self):
        raw = "INBOX||101||Jan 10||alice@test.com||Hello||true||false"
        result = parse_search_output(raw, "TestAccount")
        assert len(result) == 1
        assert result[0]["account"] == "TestAccount"
        assert result[0]["mailbox"] == "INBOX"
        assert result[0]["id"] == "101"

    def test_parse_search_output_empty(self):
        result = parse_search_output("", "TestAccount")
        assert result == []

    def test_build_account_names_script_exists(self):
        script = build_account_names_script()
        assert 'tell application "Mail"' in script

    def test_parse_account_names_output(self):
        names = parse_account_names_output("Work\nPersonal")
        assert names == ["Work", "Personal"]

    def test_parse_account_names_output_empty(self):
        names = parse_account_names_output("")
        assert names == []
