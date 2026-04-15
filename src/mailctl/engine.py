"""AppleScript execution engine.

This module is the **single execution seam** for all osascript interaction.
Every AppleScript call in the codebase goes through :func:`run_applescript`.
Tests mock this one function to avoid touching Mail.app.

Design decisions:
- One subprocess call site — easy to mock, log, and time.
- Multi-statement scripts supported natively (newlines in the script string).
- Return-value parsing converts AppleScript's text output into Python types.
- Error classification maps osascript stderr patterns to typed exceptions.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from mailctl.errors import (
    AppleScriptError,
    MailNotRunningError,
    PermissionDeniedError,
    ScriptTimeoutError,
)

# Default timeout in seconds for osascript calls.
DEFAULT_TIMEOUT: float = 30.0

# --------------------------------------------------------------------------- #
# Patterns used to classify osascript stderr into specific error types.
# --------------------------------------------------------------------------- #

_MAIL_NOT_RUNNING_PATTERNS = [
    "isn\u2019t running",   # curly apostrophe (macOS default)
    "isn't running",        # straight apostrophe
    "connection is invalid",
    "not running",
]

_PERMISSION_DENIED_PATTERNS = [
    "not allowed assistive access",
    "not allowed to send keystrokes",
    "is not allowed to",
    "not authorized to send apple events",
    "permission",
    "assistive access",
    "1002",  # Common AppleScript permission error code
]


def run_applescript(script: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Execute an AppleScript string via ``osascript -e`` and return stdout.

    This is the **only** function in the codebase that calls ``subprocess.run``
    with ``osascript``. All higher-level helpers delegate here.

    Parameters
    ----------
    script:
        An AppleScript source string. May contain multiple statements separated
        by newlines — ``osascript -e`` handles them fine.
    timeout:
        Maximum seconds to wait for ``osascript`` to complete. Raises
        :class:`ScriptTimeoutError` if exceeded.

    Returns
    -------
    str
        The stripped stdout of the osascript process on success.

    Raises
    ------
    MailNotRunningError
        Mail.app is not running or unreachable.
    PermissionDeniedError
        The terminal does not have automation permission for Mail.app.
    ScriptTimeoutError
        osascript did not complete within *timeout* seconds.
    AppleScriptError
        Any other osascript failure (syntax errors, runtime errors, etc.).
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScriptTimeoutError(
            timeout=timeout,
            stderr=str(exc.stderr or ""),
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        _raise_classified_error(stderr)

    return result.stdout.strip()


def _raise_classified_error(stderr: str) -> None:
    """Inspect *stderr* and raise the most specific exception possible."""
    lower = stderr.lower()

    for pattern in _MAIL_NOT_RUNNING_PATTERNS:
        if pattern in lower:
            raise MailNotRunningError(stderr=stderr)

    for pattern in _PERMISSION_DENIED_PATTERNS:
        if pattern in lower:
            raise PermissionDeniedError(stderr=stderr)

    # Generic AppleScript error — preserve the original message.
    raise AppleScriptError(stderr or "Unknown AppleScript error", stderr=stderr)


# --------------------------------------------------------------------------- #
# Return-value parsers
# --------------------------------------------------------------------------- #

def parse_applescript_value(raw: str) -> Any:
    """Parse an AppleScript return value into a Python type.

    Handles:
    - Empty string → empty string
    - Comma-delimited lists (AppleScript renders lists as ``item1, item2, ...``)
    - Simple strings (strips surrounding quotes if present)

    This is deliberately conservative — it handles the common cases we need
    for mailctl (strings, lists of strings) without trying to be a full
    AppleScript parser.
    """
    if not raw:
        return ""

    # AppleScript sometimes wraps strings in quotes.
    stripped = raw.strip()

    # Check for list format: "item1, item2, item3"
    # AppleScript lists are rendered with comma separation.
    # We detect lists by the presence of commas outside of quoted strings.
    if _looks_like_list(stripped):
        return _parse_list(stripped)

    # Single value — strip outer quotes if present.
    return _strip_quotes(stripped)


def _looks_like_list(value: str) -> bool:
    """Return True if *value* looks like an AppleScript list rendering."""
    # A list has commas at the top level (outside quotes).
    depth = 0
    in_quotes = False
    for ch in value:
        if ch == '"' and depth == 0:
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch in "({":
                depth += 1
            elif ch in ")}":
                depth -= 1
            elif ch == "," and depth == 0:
                return True
    return False


def _parse_list(value: str) -> list[str]:
    """Split a comma-delimited AppleScript list into Python list items."""
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    depth = 0

    for ch in value:
        if ch == '"' and depth == 0:
            in_quotes = not in_quotes
            current.append(ch)
        elif not in_quotes:
            if ch in "({":
                depth += 1
                current.append(ch)
            elif ch in ")}":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                items.append(_strip_quotes("".join(current).strip()))
                current = []
            else:
                current.append(ch)
        else:
            current.append(ch)

    # Last item.
    trailing = "".join(current).strip()
    if trailing:
        items.append(_strip_quotes(trailing))

    return items


def _strip_quotes(value: str) -> str:
    """Remove surrounding double-quotes from a value, if present."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value
