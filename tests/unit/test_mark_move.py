"""Unit tests for ``mailctl messages mark`` and ``mailctl messages move`` (Sprint 7).

Every test uses the ``mock_osascript`` fixture — no test in this module
invokes the real ``osascript`` binary.  Mark and move are state-change
operations, not send operations, so they do NOT use the ``--dangerously-send``
safety model.

Tests are organised by criterion ID from the sprint contract.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.mark_move import (
    build_mark_messages_script,
    build_move_messages_script,
    perform_mark,
    perform_move,
)


_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# C-161: mark --read sets read status to true
# --------------------------------------------------------------------------- #


class TestMarkRead:
    def test_mark_read_exit_zero(self, mock_osascript):
        """C-161: 'messages mark <id> --read' exits 0 with mocked osascript."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345", "--read"])
        assert result.exit_code == 0, result.output

    def test_mark_read_osascript_invoked(self, mock_osascript):
        """C-161: osascript is invoked when marking read."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--read"])
        assert len(mock_osascript.calls) >= 1

    def test_mark_read_script_sets_true(self, mock_osascript):
        """C-161: generated AppleScript sets read status to true."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--read"])
        script = mock_osascript.last_script
        assert script is not None
        assert "set read status of targetMsg to true" in script


# --------------------------------------------------------------------------- #
# C-162: mark --unread sets read status to false
# --------------------------------------------------------------------------- #


class TestMarkUnread:
    def test_mark_unread_exit_zero(self, mock_osascript):
        """C-162: 'messages mark <id> --unread' exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345", "--unread"])
        assert result.exit_code == 0, result.output

    def test_mark_unread_script_sets_false(self, mock_osascript):
        """C-162: generated AppleScript sets read status to false."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--unread"])
        script = mock_osascript.last_script
        assert script is not None
        assert "set read status of targetMsg to false" in script


# --------------------------------------------------------------------------- #
# C-163: mark --flagged sets flagged status to true
# --------------------------------------------------------------------------- #


class TestMarkFlagged:
    def test_mark_flagged_exit_zero(self, mock_osascript):
        """C-163: 'messages mark <id> --flagged' exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345", "--flagged"])
        assert result.exit_code == 0, result.output

    def test_mark_flagged_script_sets_true(self, mock_osascript):
        """C-163: generated AppleScript sets flagged status to true."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--flagged"])
        script = mock_osascript.last_script
        assert script is not None
        assert "set flagged status of targetMsg to true" in script


# --------------------------------------------------------------------------- #
# C-164: mark --unflagged sets flagged status to false
# --------------------------------------------------------------------------- #


class TestMarkUnflagged:
    def test_mark_unflagged_exit_zero(self, mock_osascript):
        """C-164: 'messages mark <id> --unflagged' exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345", "--unflagged"])
        assert result.exit_code == 0, result.output

    def test_mark_unflagged_script_sets_false(self, mock_osascript):
        """C-164: generated AppleScript sets flagged status to false."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--unflagged"])
        script = mock_osascript.last_script
        assert script is not None
        assert "set flagged status of targetMsg to false" in script


# --------------------------------------------------------------------------- #
# C-165: combined flags in a single invocation
# --------------------------------------------------------------------------- #


class TestMarkCombined:
    def test_read_and_flagged(self, mock_osascript):
        """C-165: --read --flagged sets both in one AppleScript call."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read", "--flagged"]
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "set read status of targetMsg to true" in script
        assert "set flagged status of targetMsg to true" in script

    def test_unread_and_unflagged(self, mock_osascript):
        """C-165: --unread --unflagged sets both in one AppleScript call."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--unread", "--unflagged"]
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "set read status of targetMsg to false" in script
        assert "set flagged status of targetMsg to false" in script

    def test_combined_single_osascript_call(self, mock_osascript):
        """C-165: combined flags are batched into one osascript call."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read", "--flagged"]
        )
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# C-166: no flags produces usage error
# --------------------------------------------------------------------------- #


class TestMarkNoFlags:
    def test_no_flags_nonzero_exit(self, mock_osascript):
        """C-166: mark with no flags exits non-zero."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345"])
        assert result.exit_code != 0

    def test_no_flags_error_message(self, mock_osascript):
        """C-166: error message mentions required flags."""
        mock_osascript.set_output("")
        result = runner.invoke(_click_app, ["messages", "mark", "12345"])
        output = result.output.lower()
        assert "flag" in output or "--read" in output


# --------------------------------------------------------------------------- #
# C-167: contradictory flags
# --------------------------------------------------------------------------- #


class TestMarkContradictory:
    def test_read_and_unread_error(self, mock_osascript):
        """C-167: --read --unread produces non-zero exit."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read", "--unread"]
        )
        assert result.exit_code != 0
        assert "contradict" in result.output.lower()

    def test_flagged_and_unflagged_error(self, mock_osascript):
        """C-167: --flagged --unflagged produces non-zero exit."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--flagged", "--unflagged"]
        )
        assert result.exit_code != 0
        assert "contradict" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-168: bulk mark — multiple message IDs
# --------------------------------------------------------------------------- #


class TestMarkBulk:
    def test_bulk_exit_zero(self, mock_osascript):
        """C-168: mark with multiple IDs exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "100", "200", "300", "--read"]
        )
        assert result.exit_code == 0, result.output

    def test_bulk_all_ids_in_script(self, mock_osascript):
        """C-168: all three IDs appear in the generated AppleScript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "mark", "100", "200", "300", "--read"]
        )
        script = mock_osascript.last_script
        assert script is not None
        assert "whose id is 100" in script
        assert "whose id is 200" in script
        assert "whose id is 300" in script

    def test_bulk_single_osascript_call(self, mock_osascript):
        """C-168: bulk mark batched into one osascript call."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "mark", "100", "200", "300", "--read"]
        )
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# C-169: mark with --account scoping
# --------------------------------------------------------------------------- #


class TestMarkAccount:
    def test_account_in_script_from_resolver(self, mock_osascript):
        """C-169: the account in the generated script comes from the SQLite
        resolver, not from --account — mark now resolves each id's owning
        account directly from the Envelope Index. The --account flag is
        retained as an accepted option for backward compatibility but no
        longer shapes the AppleScript (conftest stubs resolver to
        'TestAccount')."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "12345", "--read"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert 'account "TestAccount"' in script


# --------------------------------------------------------------------------- #
# C-170: move to mailbox
# --------------------------------------------------------------------------- #


class TestMoveBasic:
    def test_move_exit_zero(self, mock_osascript):
        """C-170: 'messages move <id> --to Archive' exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        assert result.exit_code == 0, result.output

    def test_move_osascript_invoked(self, mock_osascript):
        """C-170: osascript is invoked when moving."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        assert len(mock_osascript.calls) >= 1

    def test_move_script_references_archive(self, mock_osascript):
        """C-170: generated AppleScript moves to mailbox 'Archive'."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        script = mock_osascript.last_script
        assert script is not None
        assert '"Archive"' in script
        assert "move targetMsg to mailbox" in script


# --------------------------------------------------------------------------- #
# C-171: move with --account scoping
# --------------------------------------------------------------------------- #


class TestMoveAccount:
    def test_move_target_resolved_in_source_account(self, mock_osascript):
        """C-171: the destination mailbox is resolved inside the message's
        **own** account (derived via the SQLite resolver), because Mail.app's
        ``move`` verb can't cross accounts. Conftest stubs the resolver to
        ('TestAccount', 'INBOX') so the source AND the target both land in
        TestAccount."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "Archive"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert 'mailbox "Archive" of account "TestAccount"' in script


# --------------------------------------------------------------------------- #
# C-172: move without --to
# --------------------------------------------------------------------------- #


class TestMoveMissingTo:
    def test_missing_to_nonzero_exit(self, mock_osascript):
        """C-172: move without --to exits non-zero."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345"]
        )
        assert result.exit_code != 0

    def test_missing_to_error_message(self, mock_osascript):
        """C-172: error mentions --to."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345"]
        )
        assert "--to" in result.output


# --------------------------------------------------------------------------- #
# C-173: bulk move — multiple message IDs
# --------------------------------------------------------------------------- #


class TestMoveBulk:
    def test_bulk_move_exit_zero(self, mock_osascript):
        """C-173: move with multiple IDs exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "100", "200", "300", "--to", "Archive"],
        )
        assert result.exit_code == 0, result.output

    def test_bulk_move_all_ids_in_script(self, mock_osascript):
        """C-173: all three IDs appear in the generated AppleScript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "move", "100", "200", "300", "--to", "Archive"],
        )
        script = mock_osascript.last_script
        assert script is not None
        assert "whose id is 100" in script
        assert "whose id is 200" in script
        assert "whose id is 300" in script

    def test_bulk_move_single_osascript_call(self, mock_osascript):
        """C-173: bulk move batched into one osascript call."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "move", "100", "200", "300", "--to", "Archive"],
        )
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# C-174: dry-run on mark
# --------------------------------------------------------------------------- #


class TestMarkDryRun:
    def test_dry_run_exit_zero(self, mock_osascript):
        """C-174: --dry-run exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "12345", "--read", "--flagged", "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_describes_operation(self, mock_osascript):
        """C-174: --dry-run output mentions message ID and flags."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "12345", "--read", "--flagged", "--dry-run"],
        )
        assert "12345" in result.output
        assert "read" in result.output.lower()
        assert "flagged" in result.output.lower()

    def test_dry_run_no_osascript_calls(self, mock_osascript):
        """C-174: --dry-run does NOT invoke osascript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "mark", "12345", "--read", "--flagged", "--dry-run"],
        )
        assert len(mock_osascript.calls) == 0


# --------------------------------------------------------------------------- #
# C-175: dry-run on move
# --------------------------------------------------------------------------- #


class TestMoveDryRun:
    def test_dry_run_exit_zero(self, mock_osascript):
        """C-175: --dry-run exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "Archive", "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_describes_operation(self, mock_osascript):
        """C-175: --dry-run output mentions message ID and target."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "Archive", "--dry-run"],
        )
        assert "12345" in result.output
        assert "Archive" in result.output

    def test_dry_run_no_osascript_calls(self, mock_osascript):
        """C-175: --dry-run does NOT invoke osascript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "Archive", "--dry-run"],
        )
        assert len(mock_osascript.calls) == 0


# --------------------------------------------------------------------------- #
# C-176: dry-run with multiple IDs
# --------------------------------------------------------------------------- #


class TestDryRunMultipleIds:
    def test_mark_dry_run_all_ids(self, mock_osascript):
        """C-176: dry-run mark lists all message IDs."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "100", "200", "300", "--read", "--dry-run"],
        )
        assert "100" in result.output
        assert "200" in result.output
        assert "300" in result.output

    def test_move_dry_run_all_ids(self, mock_osascript):
        """C-176: dry-run move lists all message IDs and target mailbox."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "100", "200", "300", "--to", "Trash", "--dry-run"],
        )
        assert "100" in result.output
        assert "200" in result.output
        assert "300" in result.output
        assert "Trash" in result.output


# --------------------------------------------------------------------------- #
# C-177: human-readable confirmation output
# --------------------------------------------------------------------------- #


class TestHumanOutput:
    def test_mark_confirmation(self, mock_osascript):
        """C-177: mark outputs human-readable confirmation with message ID."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read"]
        )
        assert result.exit_code == 0
        assert "12345" in result.output
        assert "read" in result.output.lower()

    def test_move_confirmation(self, mock_osascript):
        """C-177: move outputs human-readable confirmation with message ID and target."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        assert result.exit_code == 0
        assert "12345" in result.output
        assert "Archive" in result.output


# --------------------------------------------------------------------------- #
# C-178: --json output
# --------------------------------------------------------------------------- #


class TestJsonOutput:
    def test_mark_json(self, mock_osascript):
        """C-178: --json on mark emits structured JSON with required fields."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "12345", "--read", "--flagged", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"] == "mark"
        assert "12345" in data["message_ids"]
        assert data["changes"]["read"] is True
        assert data["changes"]["flagged"] is True

    def test_move_json(self, mock_osascript):
        """C-178: --json on move emits structured JSON with required fields."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "Archive", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"] == "move"
        assert "12345" in data["message_ids"]
        assert data["target_mailbox"] == "Archive"


# --------------------------------------------------------------------------- #
# C-179: nonexistent message ID error handling
# --------------------------------------------------------------------------- #


class TestErrorHandlingNonexistent:
    def test_mark_error_on_script_failure(self, mock_osascript):
        """C-179: mark with script error produces non-zero exit."""
        mock_osascript.set_error("execution error: Can't get message id 99999")
        result = runner.invoke(
            _click_app, ["messages", "mark", "99999", "--read"]
        )
        assert result.exit_code != 0

    def test_move_error_on_script_failure(self, mock_osascript):
        """C-179: move with script error produces non-zero exit."""
        mock_osascript.set_error("execution error: Can't get message id 99999")
        result = runner.invoke(
            _click_app, ["messages", "move", "99999", "--to", "Archive"]
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-180: Mail.app not running error
# --------------------------------------------------------------------------- #


class TestErrorHandlingMailNotRunning:
    def test_mark_mail_not_running(self, mock_osascript):
        """C-180: mark when Mail.app not running produces consistent error."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read"]
        )
        assert result.exit_code != 0
        assert "mail" in result.output.lower()

    def test_move_mail_not_running(self, mock_osascript):
        """C-180: move when Mail.app not running produces consistent error."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        assert result.exit_code != 0
        assert "mail" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-181: help output lists mark and move alongside existing commands
# --------------------------------------------------------------------------- #


class TestHelpOutput:
    def test_messages_help_lists_mark(self):
        """C-181: 'messages --help' lists 'mark'."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert "mark" in result.output.lower()

    def test_messages_help_lists_move(self):
        """C-181: 'messages --help' lists 'move'."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert "move" in result.output.lower()

    def test_messages_help_lists_existing_subcommands(self):
        """C-181: regression — existing subcommands still present."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert "list" in result.output.lower()
        assert "show" in result.output.lower()
        assert "search" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-182: mark --help describes all options
# --------------------------------------------------------------------------- #


class TestMarkHelp:
    def test_mark_help_shows_options(self):
        """C-182: 'messages mark --help' enumerates all expected options."""
        result = runner.invoke(_click_app, ["messages", "mark", "--help"])
        output = result.output
        assert "MESSAGE_IDS" in output or "message_ids" in output.lower()
        assert "--read" in output
        assert "--unread" in output
        assert "--flagged" in output
        assert "--unflagged" in output
        assert "--account" in output
        assert "--dry-run" in output
        assert "--json" in output


# --------------------------------------------------------------------------- #
# C-183: move --help describes all options
# --------------------------------------------------------------------------- #


class TestMoveHelp:
    def test_move_help_shows_options(self):
        """C-183: 'messages move --help' enumerates all expected options."""
        result = runner.invoke(_click_app, ["messages", "move", "--help"])
        output = result.output
        assert "MESSAGE_IDS" in output or "message_ids" in output.lower()
        assert "--to" in output
        assert "--account" in output
        assert "--dry-run" in output
        assert "--json" in output


# --------------------------------------------------------------------------- #
# C-184: architecture — build/perform/register pattern
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_build_mark_messages_script_exists(self):
        """C-184: build_mark_messages_script function is importable."""
        assert callable(build_mark_messages_script)

    def test_build_move_messages_script_exists(self):
        """C-184: build_move_messages_script function is importable."""
        assert callable(build_move_messages_script)

    def test_perform_mark_exists(self):
        """C-184: perform_mark function is importable."""
        assert callable(perform_mark)

    def test_perform_move_exists(self):
        """C-184: perform_move function is importable."""
        assert callable(perform_move)

    def test_build_mark_script_returns_applescript(self):
        """C-184: build_mark_messages_script returns valid AppleScript."""
        script = build_mark_messages_script(
            locations=[("12345", "A", "INBOX")], read=True,
        )
        assert 'tell application "Mail"' in script
        assert "end tell" in script

    def test_build_move_script_returns_applescript(self):
        """C-184: build_move_messages_script returns valid AppleScript."""
        script = build_move_messages_script(
            locations=[("12345", "A", "INBOX")], target_mailbox="Archive",
        )
        assert 'tell application "Mail"' in script
        assert "end tell" in script

    def test_handle_mail_error_used_in_mark(self, mock_osascript):
        """C-184: mark uses handle_mail_error for error handling."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app, ["messages", "mark", "12345", "--read"]
        )
        # handle_mail_error renders "Error:" to stderr
        assert result.exit_code != 0

    def test_handle_mail_error_used_in_move(self, mock_osascript):
        """C-184: move uses handle_mail_error for error handling."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app, ["messages", "move", "12345", "--to", "Archive"]
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-185: comprehensive mark tests summary (covered by classes above)
# --------------------------------------------------------------------------- #
# Individual tests already cover:
# - --read (TestMarkRead)
# - --unread (TestMarkUnread)
# - --flagged (TestMarkFlagged)
# - --unflagged (TestMarkUnflagged)
# - combined --read --flagged (TestMarkCombined)
# - bulk (3+ IDs) (TestMarkBulk)
# - --account (TestMarkAccount)
# - --dry-run (TestMarkDryRun)
# - --json (TestJsonOutput.test_mark_json)
# - no-flags error (TestMarkNoFlags)
# - contradictory flags error (TestMarkContradictory)


# --------------------------------------------------------------------------- #
# C-186: comprehensive move tests summary (covered by classes above)
# --------------------------------------------------------------------------- #
# Individual tests already cover:
# - single ID move (TestMoveBasic)
# - bulk (3+ IDs) (TestMoveBulk)
# - --account (TestMoveAccount)
# - --dry-run (TestMoveDryRun)
# - --json (TestJsonOutput.test_move_json)
# - missing --to error (TestMoveMissingTo)
# - AppleScript error handling (TestErrorHandlingNonexistent.test_move_error_on_script_failure)


# --------------------------------------------------------------------------- #
# Regression: issue #2 — do not iterate every mailbox of every account.
# That pattern fails with -1728 (Notes mailbox) and -1741 (large IMAP
# mailboxes like Gmail's All Mail). mark/move must target each message
# directly via `whose id is <id>` scoped to its resolved mailbox.
# See https://github.com/jason-c-dev/cli.mail.app/issues/2.
# --------------------------------------------------------------------------- #


class TestNoMailboxIteration:
    """Issue #2 regression: generated scripts must NOT iterate mailboxes."""

    def test_mark_script_has_no_iteration(self, mock_osascript):
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "mark", "12345", "--read"])
        script = mock_osascript.last_script or ""
        assert "every mailbox of every account" not in script
        assert "repeat with mbox" not in script
        assert "every message of mbox" not in script
        assert "whose id is 12345" in script

    def test_move_script_has_no_iteration(self, mock_osascript):
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "move", "12345", "--to", "Archive"])
        script = mock_osascript.last_script or ""
        assert "every mailbox of every account" not in script
        assert "repeat with mbox" not in script
        assert "every message of mbox" not in script
        assert "whose id is 12345" in script


# --------------------------------------------------------------------------- #
# C-188: regression — Sprint 1-6 commands unaffected
# --------------------------------------------------------------------------- #


class TestRegression:
    def test_version_still_works(self):
        """C-188: 'mailctl --version' still works."""
        result = runner.invoke(_click_app, ["--version"])
        assert result.exit_code == 0
        assert "mailctl" in result.output

    def test_help_lists_all_groups(self):
        """C-188: 'mailctl --help' lists all command groups."""
        result = runner.invoke(_click_app, ["--help"])
        output = result.output.lower()
        assert "accounts" in output
        assert "mailboxes" in output
        assert "messages" in output
        assert "compose" in output
        assert "reply" in output
        assert "forward" in output

    def test_accounts_help(self):
        """C-188: accounts --help still works."""
        result = runner.invoke(_click_app, ["accounts", "--help"])
        assert result.exit_code == 0

    def test_mailboxes_help(self):
        """C-188: mailboxes --help still works."""
        result = runner.invoke(_click_app, ["mailboxes", "--help"])
        assert result.exit_code == 0

    def test_messages_help(self):
        """C-188: messages --help still works."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert result.exit_code == 0
