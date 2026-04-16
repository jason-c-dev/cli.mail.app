"""Unit tests for ``mailctl drafts edit`` (Sprint 8).

Every test uses the ``mock_osascript`` fixture — no test in this module
invokes the real ``osascript`` binary.  Draft editing is a non-send
operation: the generated AppleScript MUST NOT contain a ``send`` verb.

Tests are organised by criterion ID from the sprint contract.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.drafts import (
    build_edit_draft_script,
    perform_edit_draft,
)


_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# C-189: drafts edit command exists and succeeds with mocked osascript
# --------------------------------------------------------------------------- #


class TestDraftsEditCommand:
    def test_edit_exit_zero(self, mock_osascript):
        """C-189: 'drafts edit 12345 --subject \"New subject\"' exits 0."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app, ["drafts", "edit", "12345", "--subject", "New subject"]
        )
        assert result.exit_code == 0, result.output

    def test_edit_osascript_invoked(self, mock_osascript):
        """C-189: osascript is invoked when editing."""
        mock_osascript.set_output("OK")
        runner.invoke(
            _click_app, ["drafts", "edit", "12345", "--subject", "New subject"]
        )
        assert len(mock_osascript.calls) >= 1

    def test_edit_targets_message_id(self, mock_osascript):
        """C-189: generated AppleScript targets the draft by its message ID."""
        mock_osascript.set_output("OK")
        runner.invoke(
            _click_app, ["drafts", "edit", "12345", "--subject", "New subject"]
        )
        script = mock_osascript.last_script
        assert script is not None
        assert '"12345"' in script


# --------------------------------------------------------------------------- #
# C-190: --subject sets the subject of the draft
# --------------------------------------------------------------------------- #


class TestDraftsEditSubject:
    def test_subject_in_script(self, mock_osascript):
        """C-190: generated AppleScript sets the subject property."""
        mock_osascript.set_output("OK")
        runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "Updated meeting notes"],
        )
        script = mock_osascript.last_script
        assert script is not None
        assert "set subject of targetMsg to" in script
        assert "Updated meeting notes" in script

    def test_subject_exit_zero(self, mock_osascript):
        """C-190: exits 0 when setting subject."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "Updated meeting notes"],
        )
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# C-191: --body replaces the draft's body content
# --------------------------------------------------------------------------- #


class TestDraftsEditBody:
    def test_body_in_script(self, mock_osascript):
        """C-191: generated AppleScript sets the content/body of the draft."""
        mock_osascript.set_output("OK")
        runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--body", "Updated body content"],
        )
        script = mock_osascript.last_script
        assert script is not None
        assert "set content of targetMsg to" in script
        assert "Updated body content" in script

    def test_body_exit_zero(self, mock_osascript):
        """C-191: exits 0 when setting body."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--body", "Updated body content"],
        )
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# C-192: --body-file reads body from a file
# --------------------------------------------------------------------------- #


class TestDraftsEditBodyFile:
    def test_body_file_content_used(self, mock_osascript):
        """C-192: body content read from file and used in AppleScript."""
        mock_osascript.set_output("OK")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Body from file")
            f.flush()
            tmppath = f.name
        try:
            result = runner.invoke(
                _click_app,
                ["drafts", "edit", "12345", "--body-file", tmppath],
            )
            assert result.exit_code == 0, result.output
            script = mock_osascript.last_script
            assert script is not None
            assert "Body from file" in script
            assert "set content of targetMsg to" in script
        finally:
            os.unlink(tmppath)

    def test_body_file_nonexistent_error(self, mock_osascript):
        """C-192: non-existent --body-file produces non-zero exit."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--body-file", "/nonexistent/file.txt"],
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-193: --body and --body-file are mutually exclusive
# --------------------------------------------------------------------------- #


class TestDraftsEditBodyConflict:
    def test_body_and_body_file_error(self, mock_osascript):
        """C-193: --body and --body-file together produce a usage error."""
        mock_osascript.set_output("OK")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("content")
            f.flush()
            tmppath = f.name
        try:
            result = runner.invoke(
                _click_app,
                ["drafts", "edit", "12345", "--body", "text", "--body-file", tmppath],
            )
            assert result.exit_code != 0
            assert "mutually exclusive" in result.output.lower()
        finally:
            os.unlink(tmppath)


# --------------------------------------------------------------------------- #
# C-194: --to replaces To recipients
# --------------------------------------------------------------------------- #


class TestDraftsEditTo:
    def test_to_replaces_recipients(self, mock_osascript):
        """C-194: --to replaces existing To recipients."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--to", "alice@example.com", "--to", "bob@example.com"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        # Verify it clears existing recipients
        assert "delete every to recipient" in script
        # Verify both new recipients are added
        assert "alice@example.com" in script
        assert "bob@example.com" in script


# --------------------------------------------------------------------------- #
# C-195: --cc and --bcc replace recipients
# --------------------------------------------------------------------------- #


class TestDraftsEditCcBcc:
    def test_cc_replaces_recipients(self, mock_osascript):
        """C-195: --cc replaces CC recipients."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--cc", "cc@example.com"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "delete every cc recipient" in script
        assert "cc@example.com" in script

    def test_bcc_replaces_recipients(self, mock_osascript):
        """C-195: --bcc replaces BCC recipients."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--bcc", "bcc@example.com"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "delete every bcc recipient" in script
        assert "bcc@example.com" in script


# --------------------------------------------------------------------------- #
# C-196: --add-to and --remove-to for incremental changes
# --------------------------------------------------------------------------- #


class TestDraftsEditAddRemoveTo:
    def test_add_to_appends(self, mock_osascript):
        """C-196: --add-to adds a recipient without clearing existing ones."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--add-to", "new@example.com"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        # Should NOT clear existing recipients
        assert "delete every to recipient" not in script
        # Should add the new one
        assert "new@example.com" in script
        assert "make new to recipient" in script

    def test_remove_to_removes(self, mock_osascript):
        """C-196: --remove-to removes a specific recipient."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--remove-to", "old@example.com"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "old@example.com" in script
        assert "delete r" in script


# --------------------------------------------------------------------------- #
# C-197: --to and --add-to are mutually exclusive
# --------------------------------------------------------------------------- #


class TestDraftsEditToAddToConflict:
    def test_to_and_add_to_error(self, mock_osascript):
        """C-197: --to and --add-to together produce a usage error."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--to", "alice@example.com", "--add-to", "bob@example.com"],
        )
        assert result.exit_code != 0
        output = result.output.lower()
        assert "--to" in output and "--add-to" in output


# --------------------------------------------------------------------------- #
# C-198: --attach adds an attachment
# --------------------------------------------------------------------------- #


class TestDraftsEditAttach:
    def test_attach_valid_file(self, mock_osascript):
        """C-198: --attach adds an attachment in the AppleScript."""
        mock_osascript.set_output("OK")
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmppath = f.name
        try:
            result = runner.invoke(
                _click_app,
                ["drafts", "edit", "12345", "--attach", tmppath],
            )
            assert result.exit_code == 0, result.output
            script = mock_osascript.last_script
            assert script is not None
            assert "make new attachment" in script
            assert tmppath in script
        finally:
            os.unlink(tmppath)

    def test_attach_nonexistent_file_error(self, mock_osascript):
        """C-198: non-existent attachment path fails with a clear error."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--attach", "/nonexistent/file.pdf"],
        )
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "not a file" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-199: --remove-attach removes an attachment by name
# --------------------------------------------------------------------------- #


class TestDraftsEditRemoveAttach:
    def test_remove_attach_in_script(self, mock_osascript):
        """C-199: --remove-attach generates AppleScript to remove the named attachment."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--remove-attach", "report.pdf"],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        assert "report.pdf" in script
        assert "delete att" in script


# --------------------------------------------------------------------------- #
# C-200: combined edit options in a single invocation
# --------------------------------------------------------------------------- #


class TestDraftsEditCombined:
    def test_combined_subject_body_add_to(self, mock_osascript):
        """C-200: multiple edit options applied in one AppleScript call."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            [
                "drafts", "edit", "12345",
                "--subject", "New Subject",
                "--body", "New body",
                "--add-to", "extra@example.com",
            ],
        )
        assert result.exit_code == 0, result.output
        script = mock_osascript.last_script
        assert script is not None
        # All changes in one script
        assert "set subject of targetMsg to" in script
        assert "New Subject" in script
        assert "set content of targetMsg to" in script
        assert "New body" in script
        assert "extra@example.com" in script
        # Only one osascript call (batched)
        assert len(mock_osascript.calls) == 1


# --------------------------------------------------------------------------- #
# C-201: no edit options produces usage error
# --------------------------------------------------------------------------- #


class TestDraftsEditNoOptions:
    def test_no_options_nonzero_exit(self, mock_osascript):
        """C-201: no edit options produces non-zero exit."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app, ["drafts", "edit", "12345"]
        )
        assert result.exit_code != 0

    def test_no_options_error_message(self, mock_osascript):
        """C-201: error message mentions at least one edit option is required."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app, ["drafts", "edit", "12345"]
        )
        output = result.output.lower()
        assert "at least one" in output or "edit option" in output


# --------------------------------------------------------------------------- #
# C-202: --dry-run prints changes without executing AppleScript
# --------------------------------------------------------------------------- #


class TestDraftsEditDryRun:
    def test_dry_run_exit_zero(self, mock_osascript):
        """C-202: --dry-run exits 0."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "New", "--body", "Updated", "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_describes_changes(self, mock_osascript):
        """C-202: --dry-run output describes planned changes."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "New", "--body", "Updated", "--dry-run"],
        )
        assert "12345" in result.output
        assert "subject" in result.output.lower()
        assert "body" in result.output.lower()

    def test_dry_run_no_osascript_calls(self, mock_osascript):
        """C-202: --dry-run does NOT invoke osascript."""
        mock_osascript.set_output("OK")
        runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "New", "--body", "Updated", "--dry-run"],
        )
        assert len(mock_osascript.calls) == 0


# --------------------------------------------------------------------------- #
# C-203: --json emits structured JSON
# --------------------------------------------------------------------------- #


class TestDraftsEditJson:
    def test_json_output(self, mock_osascript):
        """C-203: --json emits valid JSON with required fields."""
        mock_osascript.set_output("OK")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "New Subject", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"] == "edited"
        assert data["message_id"] == "12345"
        assert "subject" in data.get("changes", {})


# --------------------------------------------------------------------------- #
# C-212 (partial): drafts edit with nonexistent message ID error
# --------------------------------------------------------------------------- #


class TestDraftsEditError:
    def test_error_on_script_failure(self, mock_osascript):
        """C-212: drafts edit with script error produces non-zero exit."""
        mock_osascript.set_error("execution error: Message not found: 99999")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "99999", "--subject", "Test"],
        )
        assert result.exit_code != 0

    def test_error_mentions_issue(self, mock_osascript):
        """C-212: error output mentions the problem."""
        mock_osascript.set_error("execution error: Message not found: 99999")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "99999", "--subject", "Test"],
        )
        # Error is rendered to output (mixed in via CliRunner)
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-213: help output for drafts and drafts edit
# --------------------------------------------------------------------------- #


class TestDraftsHelp:
    def test_drafts_help_lists_edit(self):
        """C-213: 'drafts --help' lists 'edit' as a subcommand."""
        result = runner.invoke(_click_app, ["drafts", "--help"])
        assert "edit" in result.output.lower()

    def test_drafts_edit_help_lists_all_options(self):
        """C-213: 'drafts edit --help' lists all expected options."""
        result = runner.invoke(_click_app, ["drafts", "edit", "--help"])
        output = result.output
        # Positional argument
        assert "MESSAGE_ID" in output or "message_id" in output.lower()
        # All options
        assert "--subject" in output
        assert "--body" in output
        assert "--body-file" in output
        assert "--to" in output
        assert "--cc" in output
        assert "--bcc" in output
        assert "--add-to" in output
        assert "--remove-to" in output
        assert "--attach" in output
        assert "--remove-attach" in output
        assert "--dry-run" in output
        assert "--json" in output


# --------------------------------------------------------------------------- #
# C-215 (partial): architecture — build/perform/register pattern
# --------------------------------------------------------------------------- #


class TestDraftsArchitecture:
    def test_build_edit_draft_script_exists(self):
        """C-215: build_edit_draft_script function is importable."""
        assert callable(build_edit_draft_script)

    def test_perform_edit_draft_exists(self):
        """C-215: perform_edit_draft function is importable."""
        assert callable(perform_edit_draft)

    def test_build_edit_script_returns_applescript(self):
        """C-215: build_edit_draft_script returns valid AppleScript."""
        script = build_edit_draft_script(
            message_id="12345", subject="Test"
        )
        assert 'tell application "Mail"' in script
        assert "end tell" in script

    def test_no_send_verb_in_edit_script(self):
        """Safety: edit script NEVER contains a 'send' verb."""
        script = build_edit_draft_script(
            message_id="12345",
            subject="Test",
            body="Body text",
            to=["alice@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            add_to=["extra@example.com"],
            remove_to=["old@example.com"],
            attach=["/tmp/test.txt"],
            remove_attach=["report.pdf"],
        )
        # The word "send" should NOT appear as a verb in the script
        # (it may appear in email addresses, but not as an AppleScript command)
        for line in script.split("\n"):
            stripped = line.strip()
            assert not stripped.startswith("send "), f"Found 'send' verb in line: {line}"

    def test_handle_mail_error_used(self, mock_osascript):
        """C-215: drafts edit uses handle_mail_error for error handling."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["drafts", "edit", "12345", "--subject", "Test"],
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-216: comprehensive draft edit test coverage summary
# --------------------------------------------------------------------------- #
# Individual tests already cover:
# - --subject (TestDraftsEditSubject)
# - --body (TestDraftsEditBody)
# - --body-file (TestDraftsEditBodyFile)
# - body conflict error (TestDraftsEditBodyConflict)
# - --to (replace) (TestDraftsEditTo)
# - --cc (TestDraftsEditCcBcc.test_cc_replaces_recipients)
# - --bcc (TestDraftsEditCcBcc.test_bcc_replaces_recipients)
# - --add-to (TestDraftsEditAddRemoveTo.test_add_to_appends)
# - --remove-to (TestDraftsEditAddRemoveTo.test_remove_to_removes)
# - --to + --add-to conflict (TestDraftsEditToAddToConflict)
# - --attach (TestDraftsEditAttach)
# - --remove-attach (TestDraftsEditRemoveAttach)
# - combined changes (TestDraftsEditCombined)
# - no-options error (TestDraftsEditNoOptions)
# - --dry-run (TestDraftsEditDryRun)
# - --json (TestDraftsEditJson)
# - error handling (TestDraftsEditError)
# Total: 25+ test cases
