"""Doctor command — diagnose Mail.app integration health.

Runs a series of checks to verify that mailctl can communicate with Mail.app:

1. osascript is available (Xcode command-line tools installed)
2. Mail.app is installed on the system
3. Mail.app is currently running
4. The terminal has automation permission to script Mail.app
5. At least one email account is configured in Mail.app

Each check runs independently — failures do not short-circuit later checks.
The command exits 0 when all checks pass, 1 when any check fails.

Architecture follows the project's register() pattern:
- Individual check functions return (status, message) tuples for testability.
- The Typer command handler orchestrates checks and renders output.
- Uses the shared output module (render_error) for error rendering.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console

from mailctl.errors import EXIT_GENERAL_ERROR, EXIT_SUCCESS
from mailctl.output import render_error


# --------------------------------------------------------------------------- #
# Check result type
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    """Result of a single doctor check."""

    name: str
    status: str  # "pass" or "fail"
    message: str


# --------------------------------------------------------------------------- #
# Individual checks — each is independently callable/mockable
# --------------------------------------------------------------------------- #


def check_osascript() -> CheckResult:
    """Check that osascript is available and functional.

    Runs ``osascript -e 'return "ok"'`` to verify the binary is installed
    and can execute trivial AppleScript.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", 'return "ok"'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return CheckResult(
                name="osascript",
                status="pass",
                message="osascript is available and functional.",
            )
        else:
            return CheckResult(
                name="osascript",
                status="fail",
                message=(
                    "osascript returned an error. "
                    "Install Xcode command-line tools: xcode-select --install"
                ),
            )
    except FileNotFoundError:
        return CheckResult(
            name="osascript",
            status="fail",
            message=(
                "osascript not found. "
                "Install Xcode command-line tools: xcode-select --install"
            ),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="osascript",
            status="fail",
            message=(
                "osascript timed out. "
                "Install Xcode command-line tools: xcode-select --install"
            ),
        )
    except Exception as exc:
        return CheckResult(
            name="osascript",
            status="fail",
            message=(
                f"osascript check failed: {exc}. "
                "Install Xcode command-line tools: xcode-select --install"
            ),
        )


def check_mail_installed() -> CheckResult:
    """Check that Mail.app is installed on the system.

    Checks for the existence of /System/Applications/Mail.app or
    /Applications/Mail.app.
    """
    paths = [
        "/System/Applications/Mail.app",
        "/Applications/Mail.app",
    ]
    for path in paths:
        if os.path.isdir(path):
            return CheckResult(
                name="mail_installed",
                status="pass",
                message=f"Mail.app is installed ({path}).",
            )
    return CheckResult(
        name="mail_installed",
        status="fail",
        message=(
            "Mail.app is not installed. "
            "Mail.app should be available on macOS by default — "
            "check /System/Applications/Mail.app."
        ),
    )


def check_mail_running() -> CheckResult:
    """Check that Mail.app is currently running.

    Uses osascript to query if Mail.app process is active. Falls back
    to a process-list check if osascript is unavailable.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to '
             '(name of processes) contains "Mail"'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and "true" in result.stdout.lower():
            return CheckResult(
                name="mail_running",
                status="pass",
                message="Mail.app is running.",
            )
        else:
            return CheckResult(
                name="mail_running",
                status="fail",
                message=(
                    "Mail.app is not running. "
                    "Launch Mail.app and try again: open -a Mail"
                ),
            )
    except Exception:
        return CheckResult(
            name="mail_running",
            status="fail",
            message=(
                "Could not determine if Mail.app is running. "
                "Launch Mail.app and try again: open -a Mail"
            ),
        )


def check_scriptable() -> CheckResult:
    """Check that the terminal has automation permission to script Mail.app.

    Runs a trivial read-only AppleScript against Mail.app. If automation
    permission has not been granted, macOS returns an error containing
    "not allowed" or similar.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "Mail" to name'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return CheckResult(
                name="scriptable",
                status="pass",
                message="Automation permission granted for Mail.app.",
            )
        else:
            stderr_lower = result.stderr.lower()
            if ("not allowed" in stderr_lower
                    or "not authorized" in stderr_lower
                    or "permission" in stderr_lower
                    or "1002" in stderr_lower):
                return CheckResult(
                    name="scriptable",
                    status="fail",
                    message=(
                        "Automation permission denied for Mail.app. "
                        "Grant access in System Settings > Privacy & Security > Automation."
                    ),
                )
            else:
                return CheckResult(
                    name="scriptable",
                    status="fail",
                    message=(
                        f"Could not script Mail.app: {result.stderr.strip()}. "
                        "Check System Settings > Privacy & Security > Automation."
                    ),
                )
    except Exception as exc:
        return CheckResult(
            name="scriptable",
            status="fail",
            message=(
                f"Automation check failed: {exc}. "
                "Check System Settings > Privacy & Security > Automation."
            ),
        )


def check_accounts() -> CheckResult:
    """Check that at least one email account is configured in Mail.app.

    Queries Mail.app for the count of configured accounts.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "Mail" to count of every account'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            count_str = result.stdout.strip()
            try:
                count = int(count_str)
            except ValueError:
                count = 0

            if count > 0:
                return CheckResult(
                    name="accounts",
                    status="pass",
                    message=f"{count} account(s) configured in Mail.app.",
                )
            else:
                return CheckResult(
                    name="accounts",
                    status="fail",
                    message=(
                        "No email accounts configured in Mail.app. "
                        "Add an account in Mail.app > Settings > Accounts."
                    ),
                )
        else:
            return CheckResult(
                name="accounts",
                status="fail",
                message=(
                    "Could not query Mail.app accounts. "
                    "Ensure Mail.app is running and accessible."
                ),
            )
    except Exception as exc:
        return CheckResult(
            name="accounts",
            status="fail",
            message=(
                f"Account check failed: {exc}. "
                "Ensure Mail.app is running and accessible."
            ),
        )


# --------------------------------------------------------------------------- #
# All checks, in order
# --------------------------------------------------------------------------- #

ALL_CHECKS = [
    check_osascript,
    check_mail_installed,
    check_mail_running,
    check_scriptable,
    check_accounts,
]


def run_all_checks(
    checks: list | None = None,
) -> list[CheckResult]:
    """Run all doctor checks and return the results.

    The *checks* parameter allows overriding the check list for testing.
    Each check is called independently — failures do not affect later checks.
    """
    check_fns = checks if checks is not None else ALL_CHECKS
    results: list[CheckResult] = []
    for check_fn in check_fns:
        results.append(check_fn())
    return results


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #

_PASS_ICON = "\u2714"  # checkmark
_FAIL_ICON = "\u2718"  # cross


def _render_human(
    results: list[CheckResult],
    *,
    no_color: bool = False,
) -> None:
    """Render check results as a human-readable list with pass/fail indicators."""
    console = Console(no_color=no_color)
    console.print()
    console.print("[bold]mailctl doctor[/bold] — checking Mail.app integration")
    console.print()

    for r in results:
        if r.status == "pass":
            icon = _PASS_ICON
            style = "green"
        else:
            icon = _FAIL_ICON
            style = "red"
        console.print(f"  [{style}]{icon}[/{style}] {r.message}")

    console.print()
    all_passed = all(r.status == "pass" for r in results)
    if all_passed:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        fail_count = sum(1 for r in results if r.status == "fail")
        console.print(
            f"[bold red]{fail_count} check(s) failed.[/bold red]"
        )


def _render_json(results: list[CheckResult]) -> None:
    """Render check results as JSON to stdout."""
    all_passed = all(r.status == "pass" for r in results)
    payload = {
        "all_passed": all_passed,
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "message": r.message,
            }
            for r in results
        ],
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# Typer command registration
# --------------------------------------------------------------------------- #


def register(app: typer.Typer) -> None:
    """Register the ``doctor`` command as a top-level command on *app*.

    The doctor command is read-only — it never creates, modifies, or sends
    anything. It queries system state to diagnose Mail.app integration.
    """

    @app.command(
        "doctor",
        help=(
            "Diagnose Mail.app integration. Checks that osascript is "
            "installed, Mail.app is present and running, automation "
            "permissions are granted, and accounts are configured."
        ),
    )
    def doctor(
        ctx: typer.Context,
        json_output: bool = typer.Option(
            False, "--json",
            help="Output results as JSON.",
        ),
    ) -> None:
        """Check Mail.app integration health and report issues."""
        ctx.ensure_object(dict)
        json_mode = json_output or ctx.obj.get("json", False)
        no_color = ctx.obj.get("no_color", False)

        results = run_all_checks()

        if json_mode:
            _render_json(results)
        else:
            _render_human(results, no_color=no_color)

        all_passed = all(r.status == "pass" for r in results)
        raise typer.Exit(code=EXIT_SUCCESS if all_passed else EXIT_GENERAL_ERROR)
