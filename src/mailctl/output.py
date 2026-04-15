"""Shared output formatting for mailctl commands.

Provides a unified interface for rendering data as Rich tables or JSON,
controlled by the ``--json`` flag. Both accounts and mailboxes commands
delegate to this module for consistent output behaviour.

Design decisions:
- Rich Console auto-detects TTY: colour is disabled when stdout is piped.
- The ``--no-color`` flag is an explicit override on top of TTY detection.
- JSON output bypasses Rich entirely — plain ``json.dumps`` to stdout.
- Error messages always go to stderr via a separate Console instance.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from mailctl.errors import EXIT_GENERAL_ERROR


# --------------------------------------------------------------------------- #
# Column definition for tables
# --------------------------------------------------------------------------- #

@dataclass
class ColumnDef:
    """Definition for a single table column."""

    header: str
    key: str
    justify: str = "left"
    max_width: int | None = 50
    no_wrap: bool = True


# --------------------------------------------------------------------------- #
# Core render functions
# --------------------------------------------------------------------------- #

def render_output(
    data: list[dict[str, Any]],
    columns: list[ColumnDef],
    *,
    json_mode: bool = False,
    no_color: bool = False,
    title: str | None = None,
) -> None:
    """Render *data* as either a Rich table or JSON to stdout.

    Parameters
    ----------
    data:
        List of dictionaries, each representing a row.
    columns:
        Column definitions used in table mode.
    json_mode:
        If ``True``, emit JSON instead of a table.
    no_color:
        If ``True``, disable Rich colour/style in table mode.
    title:
        Optional table title.
    """
    if json_mode:
        # JSON goes directly to stdout — no Rich involvement.
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
        return

    # Rich Console auto-detects whether stdout is a TTY and disables colour
    # when it is not (e.g., piped to a file or another process).  The
    # ``no_color`` flag is an explicit override on top of that detection.
    console = Console(no_color=no_color)

    table = Table(title=title, show_header=True, header_style="bold")

    for col in columns:
        table.add_column(
            col.header,
            justify=col.justify,
            max_width=col.max_width,
            no_wrap=col.no_wrap,
            overflow="ellipsis",
        )

    for row in data:
        values = [str(row.get(col.key, "")) for col in columns]
        table.add_row(*values)

    console.print(table)


# --------------------------------------------------------------------------- #
# Error rendering
# --------------------------------------------------------------------------- #

def render_error(message: str, *, no_color: bool = False) -> None:
    """Render an error message to stderr."""
    console = Console(stderr=True, no_color=no_color)
    console.print(f"Error: {message}", style="bold red")


def handle_mail_error(exc: Exception, *, no_color: bool = False) -> None:
    """Render *exc* to stderr and raise ``typer.Exit`` with a non-zero code.

    This is the shared error handler used by all commands so that
    AppleScript failures produce consistent, user-friendly output.
    """
    message = getattr(exc, "message", str(exc))
    render_error(message, no_color=no_color)
    raise typer.Exit(code=EXIT_GENERAL_ERROR)
