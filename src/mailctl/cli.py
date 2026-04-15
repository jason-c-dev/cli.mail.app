"""mailctl CLI entry point.

Defines the Typer application with global options (--version, --json) and
sets up the subcommand group structure. Error handling at this level catches
AppleScript exceptions and renders them to stderr with appropriate exit codes.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console

from mailctl import __version__
from mailctl.errors import (
    AppleScriptError,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    MailNotRunningError,
    PermissionDeniedError,
    ScriptTimeoutError,
)

# Console for stderr — used exclusively for error messages.
err_console = Console(stderr=True)

# Console for stdout — used for data output.
out_console = Console()

# --------------------------------------------------------------------------- #
# Typer app
# --------------------------------------------------------------------------- #

app = typer.Typer(
    name="mailctl",
    help="A command-line interface for Apple Mail.app on macOS.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Subcommand groups — placeholders wired up as the sprint plan progresses.
# Each group is a Typer sub-application that will gain commands in later sprints.
accounts_app = typer.Typer(
    name="accounts",
    help="Manage Mail.app accounts.",
    no_args_is_help=True,
)

mailboxes_app = typer.Typer(
    name="mailboxes",
    help="Browse and manage mailboxes.",
    no_args_is_help=True,
)

messages_app = typer.Typer(
    name="messages",
    help="Read, search, and manage messages.",
    no_args_is_help=True,
)

app.add_typer(accounts_app, name="accounts")
app.add_typer(mailboxes_app, name="mailboxes")
app.add_typer(messages_app, name="messages")


# --------------------------------------------------------------------------- #
# Version callback
# --------------------------------------------------------------------------- #


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"mailctl {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    json_output: Optional[bool] = typer.Option(
        None,
        "--json",
        help="Output results as JSON.",
    ),
) -> None:
    """mailctl — command-line interface for Apple Mail.app."""
    pass


# --------------------------------------------------------------------------- #
# CLI error handler
# --------------------------------------------------------------------------- #


def _handle_applescript_error(exc: AppleScriptError) -> int:
    """Render an AppleScript error to stderr and return the exit code."""
    if isinstance(exc, MailNotRunningError):
        err_console.print(f"Error: {exc.message}", style="bold red")
        return EXIT_GENERAL_ERROR
    elif isinstance(exc, PermissionDeniedError):
        err_console.print(f"Error: {exc.message}", style="bold red")
        return EXIT_GENERAL_ERROR
    elif isinstance(exc, ScriptTimeoutError):
        err_console.print(f"Error: {exc.message}", style="bold red")
        return EXIT_GENERAL_ERROR
    else:
        err_console.print(f"Error: {exc.message}", style="bold red")
        return EXIT_GENERAL_ERROR


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    """Run the mailctl CLI with top-level error handling."""
    try:
        app()
    except AppleScriptError as exc:
        code = _handle_applescript_error(exc)
        raise SystemExit(code)
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}", style="bold red")
        raise SystemExit(EXIT_GENERAL_ERROR)
