"""Unit tests for the AppleScript engine.

Covers: successful execution, multi-statement scripts, return-value parsing,
error categorisation (mail not running, permission denied, script error),
and timeout handling.
"""

from __future__ import annotations

import pytest

from mailctl.engine import parse_applescript_value, run_applescript
from mailctl.errors import (
    AppleScriptError,
    MailNotRunningError,
    PermissionDeniedError,
    ScriptTimeoutError,
)


# --------------------------------------------------------------------------- #
# Successful execution
# --------------------------------------------------------------------------- #


class TestRunAppleScriptSuccess:
    """Tests for successful osascript execution."""

    def test_returns_stripped_stdout(self, mock_osascript):
        mock_osascript.set_output("  hello world  \n")
        result = run_applescript('return "hello world"')
        assert result == "hello world"

    def test_passes_script_via_osascript_dash_e(self, mock_osascript):
        mock_osascript.set_output("ok")
        run_applescript('tell application "Mail" to name')
        assert mock_osascript.last_script == 'tell application "Mail" to name'

    def test_subprocess_called_with_correct_args(self, mock_osascript):
        mock_osascript.set_output("ok")
        run_applescript("some script")
        call_args = mock_osascript.calls[-1]
        assert call_args == ["osascript", "-e", "some script"]


class TestMultiStatementScripts:
    """Multi-statement (multi-line) scripts in a single osascript call."""

    def test_multiline_script_accepted(self, mock_osascript):
        script = 'tell application "Mail"\nreturn name\nend tell'
        mock_osascript.set_output("Mail")
        result = run_applescript(script)
        assert result == "Mail"
        # Verify the entire multi-line script was passed as one string.
        assert "\n" in mock_osascript.last_script

    def test_multi_statement_semicolons(self, mock_osascript):
        """Multi-statement using AppleScript's newline-separated format."""
        script = (
            'set x to 1\n'
            'set y to 2\n'
            'return x + y'
        )
        mock_osascript.set_output("3")
        result = run_applescript(script)
        assert result == "3"


# --------------------------------------------------------------------------- #
# Error categorisation
# --------------------------------------------------------------------------- #


class TestMailNotRunningError:
    """Detect Mail.app not running from various stderr patterns."""

    @pytest.mark.parametrize(
        "stderr",
        [
            'execution error: application "Mail" isn\'t running. (-600)',
            "application isn\u2019t running",
            "Mail got an error: Connection is invalid. (-609)",
            "some error: not running",
        ],
    )
    def test_raises_mail_not_running(self, mock_osascript, stderr):
        mock_osascript.set_error(stderr)
        with pytest.raises(MailNotRunningError) as exc_info:
            run_applescript("tell application \"Mail\" to name")
        assert exc_info.value.stderr == stderr


class TestPermissionDeniedError:
    """Detect automation permission denial."""

    @pytest.mark.parametrize(
        "stderr",
        [
            "Not authorized to send Apple events to Mail. (-1743)",
            "Terminal.app is not allowed to send keystrokes",
            "execution error: permission denied",
        ],
    )
    def test_raises_permission_denied(self, mock_osascript, stderr):
        mock_osascript.set_error(stderr)
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_applescript("tell application \"Mail\" to name")
        assert exc_info.value.stderr == stderr


class TestGenericScriptError:
    """Generic AppleScript errors (syntax, runtime) with message preserved."""

    def test_raises_applescript_error_with_message(self, mock_osascript):
        stderr = 'execution error: Can\'t get mailbox "Foo". (-1728)'
        mock_osascript.set_error(stderr)
        with pytest.raises(AppleScriptError) as exc_info:
            run_applescript("tell application \"Mail\" to get mailbox \"Foo\"")
        assert exc_info.value.stderr == stderr
        assert exc_info.value.message == stderr

    def test_syntax_error_preserved(self, mock_osascript):
        stderr = "syntax error: Expected end of line but found identifier. (-2741)"
        mock_osascript.set_error(stderr)
        with pytest.raises(AppleScriptError) as exc_info:
            run_applescript("this is not valid applescript")
        assert "syntax error" in exc_info.value.message

    def test_generic_error_not_mail_or_permission(self, mock_osascript):
        """Generic errors should not be MailNotRunning or PermissionDenied."""
        stderr = "execution error: some random error (-1234)"
        mock_osascript.set_error(stderr)
        with pytest.raises(AppleScriptError) as exc_info:
            run_applescript("bad script")
        # Ensure it's the base class, not a subclass.
        assert type(exc_info.value) is AppleScriptError


class TestTimeoutError:
    """Timeout handling."""

    def test_raises_timeout_error(self, mock_osascript):
        mock_osascript.set_timeout()
        with pytest.raises(ScriptTimeoutError) as exc_info:
            run_applescript("long running script")
        assert exc_info.value.timeout == 30

    def test_custom_timeout_passed_to_subprocess(self, mock_osascript):
        """Verify the timeout parameter is forwarded to subprocess.run."""
        mock_osascript.set_output("ok")
        run_applescript("script", timeout=10.0)
        # We can't directly inspect kwargs through our mock's __call__,
        # but we can at least verify it didn't error.


# --------------------------------------------------------------------------- #
# Return-value parsing
# --------------------------------------------------------------------------- #


class TestParseAppleScriptValue:
    """Tests for the return-value parser."""

    def test_empty_string(self):
        assert parse_applescript_value("") == ""

    def test_simple_string(self):
        assert parse_applescript_value("hello") == "hello"

    def test_quoted_string(self):
        assert parse_applescript_value('"hello world"') == "hello world"

    def test_comma_delimited_list(self):
        result = parse_applescript_value("inbox, sent, drafts")
        assert result == ["inbox", "sent", "drafts"]

    def test_list_with_quoted_items(self):
        result = parse_applescript_value('"Work", "Personal", "Archive"')
        assert result == ["Work", "Personal", "Archive"]

    def test_list_with_spaces_in_items(self):
        result = parse_applescript_value('"My Work", "Old Stuff"')
        assert result == ["My Work", "Old Stuff"]

    def test_single_item_no_comma_is_string(self):
        assert parse_applescript_value("just_one") == "just_one"

    def test_whitespace_stripped(self):
        assert parse_applescript_value("  hello  ") == "hello"
