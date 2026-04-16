"""mailctl CLI entry point.

Defines the Typer application with global options (--version, --json, --no-color)
and sets up the subcommand group structure. Command implementations live in
``mailctl.commands.*`` modules; this file only handles wiring and top-level
error handling.
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

# Subcommand groups — each is a Typer sub-application with its own commands.
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

drafts_app = typer.Typer(
    name="drafts",
    help="Manage Mail.app drafts.",
    no_args_is_help=True,
)

app.add_typer(accounts_app, name="accounts")
app.add_typer(mailboxes_app, name="mailboxes")
app.add_typer(messages_app, name="messages")
app.add_typer(drafts_app, name="drafts")


# --------------------------------------------------------------------------- #
# Register commands from command modules
# --------------------------------------------------------------------------- #

from mailctl.commands.accounts import register as register_accounts
from mailctl.commands.compose import register as register_compose
from mailctl.commands.delete import register as register_delete
from mailctl.commands.doctor import register as register_doctor
from mailctl.commands.drafts import register as register_drafts
from mailctl.commands.mailboxes import register as register_mailboxes
from mailctl.commands.mark_move import register as register_mark_move
from mailctl.commands.messages import register as register_messages
from mailctl.commands.reply_forward import register as register_reply_forward

register_accounts(accounts_app)
register_mailboxes(mailboxes_app)
register_messages(messages_app)
register_mark_move(messages_app)
register_delete(messages_app)
register_compose(app)
register_reply_forward(app)
register_drafts(drafts_app)
register_doctor(app)


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
    ctx: typer.Context,
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
    no_color: Optional[bool] = typer.Option(
        None,
        "--no-color",
        help="Disable colour/style output.",
    ),
) -> None:
    """mailctl — command-line interface for Apple Mail.app."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output or False
    ctx.obj["no_color"] = no_color or False


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
