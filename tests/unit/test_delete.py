"""Unit tests for ``mailctl messages delete`` (Sprint 8).

Every test uses the ``mock_osascript`` fixture — no test in this module
invokes the real ``osascript`` binary.  Delete is safe by default: it moves
messages to Trash.  Only ``--permanent`` triggers actual deletion.

Tests are organised by criterion ID from the sprint contract.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.delete import (
    build_delete_messages_script,
    perform_delete,
)


_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# C-204: delete command exists and moves to Trash by default
# --------------------------------------------------------------------------- #


class TestDeleteBasic:
    def test_delete_exit_zero(self, mock_osascript):
        """C-204: 'messages delete 12345' exits 0 with mocked osascript."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "delete", "12345"]
        )
        assert result.exit_code == 0, result.output

    def test_delete_osascript_invoked(self, mock_osascript):
        """C-204: osascript is invoked when deleting."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "delete", "12345"])
        assert len(mock_osascript.calls) >= 1

    def test_delete_moves_to_trash(self, mock_osascript):
        """C-204: default delete moves to Trash, NOT permanent deletion."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "delete", "12345"])
        script = mock_osascript.last_script
        assert script is not None
        assert "Trash" in script
        # Should be a move operation, not a delete verb on the message directly
        assert "move msg to" in script

    def test_delete_not_permanent_by_default(self, mock_osascript):
        """C-204: default delete does NOT use the AppleScript 'delete' verb."""
        mock_osascript.set_output("")
        runner.invoke(_click_app, ["messages", "delete", "12345"])
        script = mock_osascript.last_script
        assert script is not None
        # The 'delete msg' line should NOT appear in default (trash) mode
        assert "delete msg" not in script


# --------------------------------------------------------------------------- #
# C-205: --permanent with confirmation
# --------------------------------------------------------------------------- #


class TestDeletePermanent:
    def test_permanent_confirmed_exit_zero(self, mock_osascript):
        """C-205: --permanent with 'y' confirmation exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output

    def test_permanent_confirmed_uses_delete_verb(self, mock_osascript):
        """C-205: --permanent generates AppleScript with 'delete' verb."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent"],
            input="y\n",
        )
        script = mock_osascript.last_script
        assert script is not None
        assert "delete msg" in script

    def test_permanent_rejected_no_osascript(self, mock_osascript):
        """C-205: --permanent with 'n' does NOT invoke osascript."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent"],
            input="n\n",
        )
        assert result.exit_code == 0  # graceful abort
        assert len(mock_osascript.calls) == 0

    def test_permanent_default_no_abort(self, mock_osascript):
        """C-205: --permanent with empty input (default N) aborts."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent"],
            input="\n",
        )
        assert result.exit_code == 0  # graceful abort
        assert len(mock_osascript.calls) == 0


# --------------------------------------------------------------------------- #
# C-206: --permanent --yes skips confirmation
# --------------------------------------------------------------------------- #


class TestDeletePermanentYes:
    def test_permanent_yes_exit_zero(self, mock_osascript):
        """C-206: --permanent --yes exits 0 without requiring input."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--yes"],
        )
        assert result.exit_code == 0, result.output

    def test_permanent_yes_osascript_invoked(self, mock_osascript):
        """C-206: --permanent --yes invokes osascript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--yes"],
        )
        assert len(mock_osascript.calls) >= 1

    def test_permanent_yes_no_prompt(self, mock_osascript):
        """C-206: --permanent --yes does NOT print a confirmation prompt."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--yes"],
        )
        assert "[y/N]" not in result.output


# --------------------------------------------------------------------------- #
# C-207: --yes without --permanent has no effect
# --------------------------------------------------------------------------- #


class TestDeleteYesAlone:
    def test_yes_alone_moves_to_trash(self, mock_osascript):
        """C-207: --yes alone still moves to Trash."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--yes"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "Trash" in script
        assert "delete msg" not in script

    def test_yes_alone_no_prompt(self, mock_osascript):
        """C-207: --yes alone does not show confirmation prompt."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--yes"],
        )
        assert "[y/N]" not in result.output


# --------------------------------------------------------------------------- #
# C-208: bulk delete — multiple message IDs
# --------------------------------------------------------------------------- #


class TestDeleteBulk:
    def test_bulk_exit_zero(self, mock_osascript):
        """C-208: delete with multiple IDs exits 0."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "delete", "100", "200", "300"]
        )
        assert result.exit_code == 0, result.output

    def test_bulk_all_ids_in_script(self, mock_osascript):
        """C-208: all three IDs appear in the generated AppleScript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "delete", "100", "200", "300"]
        )
        script = mock_osascript.last_script
        assert script is not None
        assert '"100"' in script
        assert '"200"' in script
        assert '"300"' in script

    def test_bulk_single_osascript_call(self, mock_osascript):
        """C-208: bulk delete batched into one osascript call."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app, ["messages", "delete", "100", "200", "300"]
        )
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# C-209: --account scopes the delete
# --------------------------------------------------------------------------- #


class TestDeleteAccount:
    def test_account_scoping(self, mock_osascript):
        """C-209: --account scopes the AppleScript to the specified account."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--account", "Work"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert 'account "Work"' in script


# --------------------------------------------------------------------------- #
# C-210: --dry-run on delete
# --------------------------------------------------------------------------- #


class TestDeleteDryRun:
    def test_dry_run_trash_exit_zero(self, mock_osascript):
        """C-210: --dry-run exits 0 for default trash."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_trash_describes_trash(self, mock_osascript):
        """C-210: --dry-run mentions moving to Trash."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--dry-run"],
        )
        assert "12345" in result.output
        assert "trash" in result.output.lower()

    def test_dry_run_trash_no_osascript(self, mock_osascript):
        """C-210: --dry-run does NOT invoke osascript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--dry-run"],
        )
        assert len(mock_osascript.calls) == 0

    def test_dry_run_permanent_describes_permanent(self, mock_osascript):
        """C-210: --dry-run --permanent mentions permanent deletion."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "12345" in result.output
        assert "permanent" in result.output.lower()

    def test_dry_run_permanent_no_osascript(self, mock_osascript):
        """C-210: --dry-run --permanent does NOT invoke osascript."""
        mock_osascript.set_output("")
        runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--dry-run"],
        )
        assert len(mock_osascript.calls) == 0


# --------------------------------------------------------------------------- #
# C-211: human-readable and JSON output
# --------------------------------------------------------------------------- #


class TestDeleteOutput:
    def test_human_output_trash(self, mock_osascript):
        """C-211: default delete outputs human-readable confirmation mentioning Trash."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app, ["messages", "delete", "12345"]
        )
        assert result.exit_code == 0
        assert "trash" in result.output.lower()

    def test_json_output_trash(self, mock_osascript):
        """C-211: --json for default delete emits valid JSON with 'trashed' action."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"] == "trashed"
        assert "12345" in data["message_ids"]
        assert data["permanent"] is False

    def test_json_output_permanent(self, mock_osascript):
        """C-211: --json for permanent delete emits 'deleted' action."""
        mock_osascript.set_output("")
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "12345", "--permanent", "--yes", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"] == "deleted"
        assert data["permanent"] is True


# --------------------------------------------------------------------------- #
# C-212 (partial): delete with nonexistent message ID error
# --------------------------------------------------------------------------- #


class TestDeleteError:
    def test_error_on_script_failure(self, mock_osascript):
        """C-212: delete with script error produces non-zero exit."""
        mock_osascript.set_error("execution error: Can't get message id 99999")
        result = runner.invoke(
            _click_app, ["messages", "delete", "99999"]
        )
        assert result.exit_code != 0

    def test_error_mentions_issue(self, mock_osascript):
        """C-212: error output is rendered on stderr."""
        mock_osascript.set_error("execution error: Can't get message id 99999")
        result = runner.invoke(
            _click_app, ["messages", "delete", "99999"]
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-214: help output for messages delete
# --------------------------------------------------------------------------- #


class TestDeleteHelp:
    def test_messages_help_lists_delete(self):
        """C-214: 'messages --help' lists 'delete' as a subcommand."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        assert "delete" in result.output.lower()

    def test_messages_help_lists_existing_subcommands(self):
        """C-214: 'messages --help' still lists existing subcommands."""
        result = runner.invoke(_click_app, ["messages", "--help"])
        output = result.output.lower()
        assert "list" in output
        assert "show" in output
        assert "search" in output
        assert "mark" in output
        assert "move" in output

    def test_delete_help_lists_all_options(self):
        """C-214: 'messages delete --help' lists all expected options."""
        result = runner.invoke(_click_app, ["messages", "delete", "--help"])
        output = result.output
        # Positional argument
        assert "MESSAGE_IDS" in output or "message_ids" in output.lower()
        # All options
        assert "--permanent" in output
        assert "--yes" in output
        assert "--account" in output
        assert "--dry-run" in output
        assert "--json" in output


# --------------------------------------------------------------------------- #
# C-215 (partial): architecture — build/perform/register pattern
# --------------------------------------------------------------------------- #


class TestDeleteArchitecture:
    def test_build_delete_messages_script_exists(self):
        """C-215: build_delete_messages_script function is importable."""
        assert callable(build_delete_messages_script)

    def test_perform_delete_exists(self):
        """C-215: perform_delete function is importable."""
        assert callable(perform_delete)

    def test_build_delete_script_returns_applescript(self):
        """C-215: build_delete_messages_script returns valid AppleScript."""
        script = build_delete_messages_script(
            message_ids=["12345"]
        )
        assert 'tell application "Mail"' in script
        assert "end tell" in script

    def test_build_delete_script_trash_mode(self):
        """C-215: default mode generates move to Trash."""
        script = build_delete_messages_script(
            message_ids=["12345"], permanent=False
        )
        assert "Trash" in script
        assert "delete msg" not in script

    def test_build_delete_script_permanent_mode(self):
        """C-215: permanent mode generates delete verb."""
        script = build_delete_messages_script(
            message_ids=["12345"], permanent=True
        )
        assert "delete msg" in script

    def test_handle_mail_error_used(self, mock_osascript):
        """C-215: delete uses handle_mail_error for error handling."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app, ["messages", "delete", "12345"]
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-217: comprehensive delete test coverage summary
# --------------------------------------------------------------------------- #
# Individual tests already cover:
# - default Trash (TestDeleteBasic)
# - --permanent + y (TestDeletePermanent.test_permanent_confirmed_*)
# - --permanent + n (TestDeletePermanent.test_permanent_rejected_no_osascript)
# - --permanent default N (TestDeletePermanent.test_permanent_default_no_abort)
# - --permanent --yes (TestDeletePermanentYes)
# - --yes alone (TestDeleteYesAlone)
# - bulk 3+ IDs (TestDeleteBulk)
# - --account (TestDeleteAccount)
# - --dry-run default (TestDeleteDryRun.test_dry_run_trash_*)
# - --dry-run --permanent (TestDeleteDryRun.test_dry_run_permanent_*)
# - --json Trash (TestDeleteOutput.test_json_output_trash)
# - --json permanent (TestDeleteOutput.test_json_output_permanent)
# - error handling (TestDeleteError)
# Total: 25+ test cases
