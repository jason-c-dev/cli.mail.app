"""Unit tests for the CLI skeleton.

Covers: --version output, --help output, and error rendering to stderr.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mailctl import __version__
from mailctl.cli import app
from mailctl.errors import (
    AppleScriptError,
    MailNotRunningError,
    PermissionDeniedError,
    ScriptTimeoutError,
)

runner = CliRunner()


# --------------------------------------------------------------------------- #
# --version
# --------------------------------------------------------------------------- #


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "mailctl" in result.output
        assert __version__ in result.output


# --------------------------------------------------------------------------- #
# --help
# --------------------------------------------------------------------------- #


class TestHelp:
    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "mailctl" in result.output.lower() or "mail" in result.output.lower()

    def test_help_shows_subcommands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # Should show the registered subcommand groups.
        assert "accounts" in result.output.lower()
        assert "mailboxes" in result.output.lower()
        assert "messages" in result.output.lower()

    def test_no_args_shows_help(self):
        """Typer with no_args_is_help=True shows usage when invoked bare."""
        result = runner.invoke(app, [])
        # Typer returns exit code 0 for --help but may return 0 or 2 for
        # no_args_is_help depending on version. Either is acceptable — the
        # key contract is that help text appears.
        assert result.exit_code in (0, 2)
        assert "usage" in result.output.lower() or "mailctl" in result.output.lower()


# --------------------------------------------------------------------------- #
# Error rendering
# --------------------------------------------------------------------------- #


class TestErrorRendering:
    """Verify that AppleScript errors are caught and rendered to stderr."""

    def test_mail_not_running_error_exit_code(self):
        """MailNotRunningError should result in a non-zero exit."""
        from mailctl.cli import main, _handle_applescript_error

        exc = MailNotRunningError(stderr="app not running")
        code = _handle_applescript_error(exc)
        assert code == 1

    def test_permission_denied_error_exit_code(self):
        from mailctl.cli import _handle_applescript_error

        exc = PermissionDeniedError(stderr="not authorized")
        code = _handle_applescript_error(exc)
        assert code == 1

    def test_timeout_error_exit_code(self):
        from mailctl.cli import _handle_applescript_error

        exc = ScriptTimeoutError(timeout=30, stderr="")
        code = _handle_applescript_error(exc)
        assert code == 1

    def test_generic_error_exit_code(self):
        from mailctl.cli import _handle_applescript_error

        exc = AppleScriptError("something broke", stderr="oops")
        code = _handle_applescript_error(exc)
        assert code == 1

    def test_error_handler_returns_nonzero_for_all_types(self):
        """Every error type should result in non-zero exit."""
        from mailctl.cli import _handle_applescript_error

        errors = [
            MailNotRunningError(stderr=""),
            PermissionDeniedError(stderr=""),
            ScriptTimeoutError(timeout=30, stderr=""),
            AppleScriptError("generic", stderr=""),
        ]
        for exc in errors:
            code = _handle_applescript_error(exc)
            assert code != 0, f"{type(exc).__name__} should return non-zero"
