"""Tests for the ``mailctl doctor`` command.

Covers C-220 through C-229, C-238, C-239, and C-240.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.doctor import (
    CheckResult,
    check_accounts,
    check_mail_installed,
    check_mail_running,
    check_osascript,
    check_scriptable,
    run_all_checks,
)

_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helper: mock all checks to pass
# --------------------------------------------------------------------------- #

def _all_pass_checks():
    """Return a list of check functions that all pass."""
    def _pass_osascript():
        return CheckResult(name="osascript", status="pass", message="osascript is available and functional.")

    def _pass_mail_installed():
        return CheckResult(name="mail_installed", status="pass", message="Mail.app is installed (/System/Applications/Mail.app).")

    def _pass_mail_running():
        return CheckResult(name="mail_running", status="pass", message="Mail.app is running.")

    def _pass_scriptable():
        return CheckResult(name="scriptable", status="pass", message="Automation permission granted for Mail.app.")

    def _pass_accounts():
        return CheckResult(name="accounts", status="pass", message="2 account(s) configured in Mail.app.")

    return [_pass_osascript, _pass_mail_installed, _pass_mail_running, _pass_scriptable, _pass_accounts]


def _failing_check(name: str, message: str):
    """Return a check function that fails."""
    def _fail():
        return CheckResult(name=name, status="fail", message=message)
    return _fail


# --------------------------------------------------------------------------- #
# C-220: Doctor command exists as top-level command
# --------------------------------------------------------------------------- #

class TestDoctorCommandExists:
    """C-220: 'mailctl doctor' exists as a top-level command."""

    def test_doctor_listed_in_help(self):
        """'mailctl --help' lists doctor as a command."""
        result = runner.invoke(_click_app, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.output

    def test_doctor_exit_zero_all_pass(self):
        """Doctor exits 0 when all checks pass."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_shows_at_least_5_checks(self):
        """Doctor reports at least 5 check results."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor"])
        # Count pass indicators
        pass_count = result.output.count("\u2714")  # checkmark
        assert pass_count >= 5


# --------------------------------------------------------------------------- #
# C-221: osascript check
# --------------------------------------------------------------------------- #

class TestDoctorCheckOsascript:
    """C-221: Doctor checks osascript availability."""

    def test_osascript_pass(self):
        """osascript check passes when osascript is available."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript", "-e", 'return "ok"'],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_osascript()
        assert result.status == "pass"
        assert "osascript" in result.message.lower()

    def test_osascript_fail_not_found(self):
        """osascript check fails when osascript is missing."""
        with patch("mailctl.commands.doctor.subprocess.run", side_effect=FileNotFoundError):
            result = check_osascript()
        assert result.status == "fail"
        assert "xcode" in result.message.lower() or "xcode-select" in result.message.lower()

    def test_osascript_fail_nonzero_returncode(self):
        """osascript check fails on non-zero exit code."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript", "-e", 'return "ok"'],
            returncode=1,
            stdout="",
            stderr="error",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_osascript()
        assert result.status == "fail"
        assert "xcode" in result.message.lower() or "install" in result.message.lower()


# --------------------------------------------------------------------------- #
# C-222: Mail.app installed check
# --------------------------------------------------------------------------- #

class TestDoctorCheckMailInstalled:
    """C-222: Doctor checks Mail.app installation."""

    def test_mail_installed_pass(self):
        """Mail.app check passes when found."""
        with patch("mailctl.commands.doctor.os.path.isdir", return_value=True):
            result = check_mail_installed()
        assert result.status == "pass"
        assert "installed" in result.message.lower()

    def test_mail_installed_fail(self):
        """Mail.app check fails when not found."""
        with patch("mailctl.commands.doctor.os.path.isdir", return_value=False):
            result = check_mail_installed()
        assert result.status == "fail"
        assert "not installed" in result.message.lower()


# --------------------------------------------------------------------------- #
# C-223: Mail.app running check
# --------------------------------------------------------------------------- #

class TestDoctorCheckMailRunning:
    """C-223: Doctor checks Mail.app is running."""

    def test_mail_running_pass(self):
        """Running check passes when Mail.app is running."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="true\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_mail_running()
        assert result.status == "pass"
        assert "running" in result.message.lower()

    def test_mail_running_fail(self):
        """Running check fails when Mail.app is not running."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="false\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_mail_running()
        assert result.status == "fail"
        assert "launch mail" in result.message.lower() or "open -a mail" in result.message.lower()


# --------------------------------------------------------------------------- #
# C-224: Automation permission check
# --------------------------------------------------------------------------- #

class TestDoctorCheckScriptable:
    """C-224: Doctor checks automation permission."""

    def test_scriptable_pass(self):
        """Scriptable check passes when permission is granted."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="Mail\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_scriptable()
        assert result.status == "pass"
        assert "automation" in result.message.lower() or "permission" in result.message.lower()

    def test_scriptable_fail_permission_denied(self):
        """Scriptable check fails when permission is denied."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=1,
            stdout="",
            stderr="Not authorized to send Apple events to Mail.",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_scriptable()
        assert result.status == "fail"
        assert "system settings" in result.message.lower()
        assert "privacy" in result.message.lower() or "automation" in result.message.lower()


# --------------------------------------------------------------------------- #
# C-225: Accounts check
# --------------------------------------------------------------------------- #

class TestDoctorCheckAccounts:
    """C-225: Doctor checks that accounts are configured."""

    def test_accounts_pass(self):
        """Accounts check passes when accounts exist and shows count."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="2\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_accounts()
        assert result.status == "pass"
        assert "2" in result.message

    def test_accounts_fail_none_configured(self):
        """Accounts check fails when no accounts are configured."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="0\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_accounts()
        assert result.status == "fail"
        assert "no" in result.message.lower() or "account" in result.message.lower()


# --------------------------------------------------------------------------- #
# C-226: Output format — pass/fail indicators, no short-circuit
# --------------------------------------------------------------------------- #

class TestDoctorOutputFormat:
    """C-226: All checks listed with pass/fail indicators."""

    def test_all_pass_indicators(self):
        """All checks show pass indicators when all pass."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 0
        # Should have checkmarks for each passing check
        assert result.output.count("\u2714") >= 5

    def test_no_short_circuit_on_failure(self):
        """Multiple failing checks are all listed."""
        checks = [
            _failing_check("osascript", "osascript not found."),
            lambda: CheckResult(name="mail_installed", status="pass", message="Mail.app installed."),
            _failing_check("mail_running", "Mail.app not running."),
            lambda: CheckResult(name="scriptable", status="pass", message="Permission granted."),
            _failing_check("accounts", "No accounts."),
        ]
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 1
        # Should show both pass and fail indicators
        assert "\u2718" in result.output  # cross for failures
        assert "\u2714" in result.output  # checkmark for passes
        # Both failing messages should appear
        assert "osascript not found" in result.output
        assert "Mail.app not running" in result.output
        assert "No accounts" in result.output

    def test_passing_checks_alongside_failures(self):
        """Passing checks still appear alongside failing checks."""
        checks = [
            lambda: CheckResult(name="osascript", status="pass", message="osascript ok."),
            _failing_check("mail_running", "Mail.app not running."),
            lambda: CheckResult(name="scriptable", status="pass", message="Permission ok."),
            lambda: CheckResult(name="mail_installed", status="pass", message="Mail.app installed."),
            _failing_check("accounts", "No accounts."),
        ]
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor"])
        # All 5 checks reported
        assert result.output.count("\u2714") + result.output.count("\u2718") >= 5


# --------------------------------------------------------------------------- #
# C-227: Exit codes
# --------------------------------------------------------------------------- #

class TestDoctorExitCodes:
    """C-227: Exit 0 when all pass; exit 1 when any fail."""

    def test_exit_zero_all_pass(self):
        """Exit code 0 when all checks pass."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 0

    def test_exit_one_on_single_failure(self):
        """Exit code 1 when one check fails."""
        checks = list(_all_pass_checks())
        checks[2] = _failing_check("mail_running", "Not running")
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 1

    def test_exit_one_on_multiple_failures(self):
        """Exit code still 1 when multiple checks fail."""
        checks = [
            _failing_check("osascript", "Missing"),
            _failing_check("mail_installed", "Missing"),
            _failing_check("mail_running", "Not running"),
            _failing_check("scriptable", "Denied"),
            _failing_check("accounts", "None"),
        ]
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# C-228: JSON output
# --------------------------------------------------------------------------- #

class TestDoctorJSON:
    """C-228: --json output with checks array and all_passed boolean."""

    def test_json_all_pass(self):
        """JSON output when all checks pass."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_passed"] is True
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) >= 5
        for check in data["checks"]:
            assert "name" in check
            assert check["status"] == "pass"
            assert "message" in check

    def test_json_with_failure(self):
        """JSON output when some checks fail."""
        checks = list(_all_pass_checks())
        checks[2] = _failing_check("mail_running", "Not running.")
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["all_passed"] is False
        # Find the failing check
        failing = [c for c in data["checks"] if c["status"] == "fail"]
        assert len(failing) == 1
        assert failing[0]["name"] == "mail_running"

    def test_json_check_structure(self):
        """Each check in JSON has name, status, message."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor", "--json"])
        data = json.loads(result.output)
        for check in data["checks"]:
            assert isinstance(check["name"], str)
            assert check["status"] in ("pass", "fail")
            assert isinstance(check["message"], str)


# --------------------------------------------------------------------------- #
# C-229: Help text
# --------------------------------------------------------------------------- #

class TestDoctorHelp:
    """C-229: 'mailctl doctor --help' describes the command."""

    def test_help_mentions_diagnosis(self):
        """Help text describes diagnostic purpose."""
        result = runner.invoke(_click_app, ["doctor", "--help"])
        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "diagnos" in output_lower or "check" in output_lower

    def test_help_lists_json_option(self):
        """Help text lists --json as an option."""
        result = runner.invoke(_click_app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output


# --------------------------------------------------------------------------- #
# C-238: --no-color output
# --------------------------------------------------------------------------- #

class TestDoctorNoColor:
    """C-238: --no-color produces plain text without ANSI escape codes."""

    def test_no_color_no_ansi(self):
        """--no-color output contains no ANSI escape sequences."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["--no-color", "doctor"])
        assert result.exit_code == 0
        # Check for ANSI escape codes
        assert "\x1b[" not in result.output
        assert "\x1b" not in result.output

    def test_no_color_still_readable(self):
        """--no-color output still contains check results."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["--no-color", "doctor"])
        assert result.exit_code == 0
        # Should still have meaningful content
        assert "osascript" in result.output.lower() or "mail" in result.output.lower()
        # Pass indicators should still be present (unicode, not ANSI)
        assert "\u2714" in result.output


# --------------------------------------------------------------------------- #
# C-239: Architecture — register() pattern, separate module
# --------------------------------------------------------------------------- #

class TestDoctorArchitecture:
    """C-239: Doctor follows project patterns."""

    def test_module_exists(self):
        """src/mailctl/commands/doctor.py exists."""
        import mailctl.commands.doctor
        assert hasattr(mailctl.commands.doctor, "register")

    def test_register_is_callable(self):
        """register() function exists and is callable."""
        from mailctl.commands.doctor import register
        assert callable(register)

    def test_individual_checks_mockable(self):
        """Individual check functions are independently callable."""
        from mailctl.commands.doctor import (
            check_osascript,
            check_mail_installed,
            check_mail_running,
            check_scriptable,
            check_accounts,
        )
        assert callable(check_osascript)
        assert callable(check_mail_installed)
        assert callable(check_mail_running)
        assert callable(check_scriptable)
        assert callable(check_accounts)

    def test_doctor_is_top_level_command(self):
        """Doctor is registered as a top-level command, not in a subgroup."""
        result = runner.invoke(_click_app, ["--help"])
        assert "doctor" in result.output
        # It should NOT be under a subgroup
        result2 = runner.invoke(_click_app, ["doctor", "--help"])
        assert result2.exit_code == 0


# --------------------------------------------------------------------------- #
# Additional comprehensive tests for C-240
# --------------------------------------------------------------------------- #

class TestDoctorComprehensive:
    """C-240: Additional doctor test cases."""

    def test_multiple_simultaneous_failures(self):
        """Multiple failures are all reported."""
        checks = [
            _failing_check("osascript", "osascript not found."),
            _failing_check("mail_installed", "Mail.app not found."),
            lambda: CheckResult(name="mail_running", status="pass", message="Running."),
            _failing_check("scriptable", "Permission denied."),
            lambda: CheckResult(name="accounts", status="pass", message="1 account."),
        ]
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor"])
        assert result.exit_code == 1
        assert "osascript not found" in result.output
        assert "Mail.app not found" in result.output
        assert "Permission denied" in result.output

    def test_json_pass_all_passed_true(self):
        """JSON all_passed is true when everything passes."""
        with patch("mailctl.commands.doctor.ALL_CHECKS", _all_pass_checks()):
            result = runner.invoke(_click_app, ["doctor", "--json"])
        data = json.loads(result.output)
        assert data["all_passed"] is True

    def test_json_fail_all_passed_false(self):
        """JSON all_passed is false when any check fails."""
        checks = list(_all_pass_checks())
        checks[0] = _failing_check("osascript", "Missing")
        with patch("mailctl.commands.doctor.ALL_CHECKS", checks):
            result = runner.invoke(_click_app, ["doctor", "--json"])
        data = json.loads(result.output)
        assert data["all_passed"] is False

    def test_run_all_checks_returns_list(self):
        """run_all_checks returns a list of CheckResult objects."""
        results = run_all_checks(checks=_all_pass_checks())
        assert isinstance(results, list)
        assert len(results) == 5
        assert all(isinstance(r, CheckResult) for r in results)

    def test_accounts_check_shows_count_in_pass(self):
        """When accounts pass, the message mentions the count."""
        mock_result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="3\n",
            stderr="",
        )
        with patch("mailctl.commands.doctor.subprocess.run", return_value=mock_result):
            result = check_accounts()
        assert result.status == "pass"
        assert "3" in result.message
