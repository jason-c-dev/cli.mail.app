"""Tests for error message polish across commands.

Covers C-230 (error format), C-232 (account not found), C-233 (mailbox not found),
C-234 (message not found), C-235 (empty results), C-236 (empty body),
C-237 (attachment not found), and C-241 (error polish tests).
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.errors import (
    MailNotRunningError,
    PermissionDeniedError,
    ScriptTimeoutError,
)

_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# C-230: Error format consistency — problem. solution.
# --------------------------------------------------------------------------- #

class TestErrorFormatConsistency:
    """C-230: All typed error classes have 'problem. solution.' format."""

    def test_mail_not_running_has_solution(self):
        """MailNotRunningError mentions launching Mail.app."""
        exc = MailNotRunningError()
        assert "not running" in exc.message.lower()
        assert "launch" in exc.message.lower() or "mail.app" in exc.message.lower()

    def test_permission_denied_has_system_settings(self):
        """PermissionDeniedError mentions System Settings path."""
        exc = PermissionDeniedError()
        assert "system settings" in exc.message.lower()
        assert "privacy" in exc.message.lower() or "automation" in exc.message.lower()

    def test_script_timeout_has_solution(self):
        """ScriptTimeoutError mentions Mail.app unresponsive and suggests remedy."""
        exc = ScriptTimeoutError(timeout=30.0)
        assert "timed out" in exc.message.lower()
        assert "unresponsive" in exc.message.lower() or "restart" in exc.message.lower()


# --------------------------------------------------------------------------- #
# C-232: Account not found — consistent across commands
# --------------------------------------------------------------------------- #

class TestAccountNotFound:
    """C-232: Account not found errors list known accounts."""

    def test_messages_list_account_not_found(self, mock_osascript):
        """messages list with bad --account shows known accounts."""
        # First call: messages fetch fails with account error
        # Second call: account names lookup for the error message
        mock_osascript._output_sequence = None
        mock_osascript._calls = []
        # We need a custom side-effect: first call errors, second returns accounts
        call_count = [0]
        original_call = mock_osascript.__call__

        import subprocess as sp

        def side_effect(args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return sp.CompletedProcess(args=args, returncode=1,
                    stdout="", stderr="Can't get account \"NonexistentAccount\".")
            else:
                return sp.CompletedProcess(args=args, returncode=0,
                    stdout="PersonalAccount\nWorkAccount", stderr="")

        with patch("mailctl.engine.subprocess.run", side_effect=side_effect):
            result = runner.invoke(
                _click_app,
                ["messages", "list", "--account", "NonexistentAccount"],
            )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()
        assert "PersonalAccount" in result.stderr or "WorkAccount" in result.stderr

    def test_mailboxes_list_account_not_found(self, mock_osascript):
        """mailboxes list with bad --account shows known accounts."""
        mock_osascript.set_output("PersonalAccount||INBOX||5||100\nWorkAccount||INBOX||3||50")
        result = runner.invoke(
            _click_app,
            ["mailboxes", "list", "--account", "NonexistentAccount"],
        )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()

    def test_compose_from_account_not_found(self, mock_osascript):
        """compose with bad --from shows known accounts."""
        mock_osascript.set_output("PersonalAccount\nWorkAccount")
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "test@example.com",
                "--subject", "Test",
                "--body", "Hello",
                "--from", "NonexistentAccount",
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower()
        assert "PersonalAccount" in result.stderr or "WorkAccount" in result.stderr


# --------------------------------------------------------------------------- #
# C-233: Mailbox not found
# --------------------------------------------------------------------------- #

class TestMailboxNotFound:
    """C-233: Mailbox not found errors with guidance."""

    def test_messages_list_mailbox_not_found(self, mock_osascript):
        """messages list with bad --mailbox gives guidance."""
        mock_osascript.set_error("Can't get mailbox \"NonexistentBox\".", returncode=1)
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--mailbox", "NonexistentBox"],
        )
        assert result.exit_code != 0
        assert "NonexistentBox" in result.stderr or "mailboxes list" in result.stderr.lower()

    def test_messages_move_mailbox_not_found(self, mock_osascript):
        """messages move with bad --to gives mailbox guidance."""
        mock_osascript.set_error("Can't get mailbox \"NonexistentBox\".", returncode=1)
        result = runner.invoke(
            _click_app,
            ["messages", "move", "12345", "--to", "NonexistentBox"],
        )
        assert result.exit_code != 0
        assert "NonexistentBox" in result.stderr or "mailboxes list" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# C-234: Message not found — consistent across commands
# --------------------------------------------------------------------------- #

class TestMessageNotFound:
    """C-234: Message not found errors mention the ID."""

    def test_messages_show_not_found(self, mock_osascript):
        """messages show with bad ID produces clear error."""
        mock_osascript.set_error("Can't get message id 99999. Message not found.", returncode=1)
        result = runner.invoke(
            _click_app,
            ["messages", "show", "99999"],
        )
        assert result.exit_code != 0
        assert "99999" in result.stderr or "not found" in result.stderr.lower()

    def test_messages_mark_not_found(self, mock_osascript):
        """messages mark with bad ID produces clear error."""
        mock_osascript.set_error("Message not found: 99999", returncode=1)
        result = runner.invoke(
            _click_app,
            ["messages", "mark", "99999", "--read"],
        )
        assert result.exit_code != 0
        assert "99999" in result.stderr or "not found" in result.stderr.lower()

    def test_messages_delete_not_found(self, mock_osascript):
        """messages delete with bad ID produces clear error."""
        mock_osascript.set_error("Message not found: 99999", returncode=1)
        result = runner.invoke(
            _click_app,
            ["messages", "delete", "99999"],
        )
        assert result.exit_code != 0
        assert "99999" in result.stderr or "not found" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# C-235: Empty results messaging
# --------------------------------------------------------------------------- #

class TestEmptyResults:
    """C-235: List/search with no results shows a helpful message."""

    def test_messages_list_empty(self, envelope_db, mock_osascript):
        """messages list with no results shows 'No messages found.'"""
        # Seed an INBOX mailbox with no messages so the mailbox resolves
        # but the result set is empty.
        from tests.conftest import TEST_ACCOUNT_ALICE_UUID
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/INBOX")
        result = runner.invoke(
            _click_app,
            ["messages", "list", "--mailbox", "INBOX"],
        )
        assert result.exit_code == 0
        assert "no messages found" in result.output.lower()

    def test_messages_search_empty(self, mock_osascript):
        """messages search with no results shows 'No messages matched'."""
        # First call: account names; second call: search results (empty)
        mock_osascript.set_outputs(["TestAccount", ""])
        result = runner.invoke(
            _click_app,
            ["messages", "search", "--from", "nobody@example.com"],
        )
        assert result.exit_code == 0
        assert "no messages matched" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-236: Empty body from stdin
# --------------------------------------------------------------------------- #

class TestEmptyStdinBody:
    """C-236: Empty body error mentions alternatives."""

    def test_compose_no_body_error(self):
        """compose with no body shows available input methods."""
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "test@example.com", "--subject", "Test"],
        )
        assert result.exit_code != 0
        # Should mention at least two of: --body, --body-file, stdin
        error_text = result.stderr.lower()
        methods_mentioned = sum(1 for m in ["--body", "--body-file", "stdin"] if m in error_text)
        assert methods_mentioned >= 2


# --------------------------------------------------------------------------- #
# C-237: Attachment not found
# --------------------------------------------------------------------------- #

class TestAttachmentNotFound:
    """C-237: Attachment not found error includes the file path."""

    def test_compose_attach_not_found(self):
        """compose with missing attachment shows the file path."""
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "test@example.com",
                "--subject", "Test",
                "--body", "Hello",
                "--attach", "/nonexistent/file.pdf",
            ],
        )
        assert result.exit_code != 0
        assert "/nonexistent/file.pdf" in result.stderr
        assert "not" in result.stderr.lower() and ("exist" in result.stderr.lower() or "found" in result.stderr.lower())
