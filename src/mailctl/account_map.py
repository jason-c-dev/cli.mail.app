"""Account identity bridge between AppleScript and SQLite.

The SQLite Envelope Index identifies accounts by UUID (embedded in
``mailboxes.url``), while AppleScript identifies them by user-friendly
name. Commands that query SQLite need to translate UUIDs back to
display names in both directions:

- ``--account Google`` on the command line → UUID for the SQL WHERE clause.
- ``mailboxes.url`` in a result row → "Google" in the rendered table.

The mapping comes from one AppleScript call per CLI invocation, cached
in module scope. This is the only AppleScript round-trip that happens
in a SQLite-backed read — it runs in ~200ms against any number of
accounts and the result is stable for the life of the process.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from mailctl.engine import run_applescript
from mailctl.errors import AppleScriptError


@dataclass(frozen=True)
class Account:
    """Minimal account identity — enough to map UUID↔name for queries."""
    uuid: str
    name: str


_BUILD_ACCOUNT_MAP_SCRIPT = '''\
tell application "Mail"
    set output to ""
    repeat with a in every account
        set output to output & (id of a as string) & "||" & (name of a as string) & linefeed
    end repeat
    return output
end tell'''


@functools.lru_cache(maxsize=1)
def get_account_map() -> tuple[Account, ...]:
    """Return all configured Mail.app accounts as (UUID, name) pairs.

    Cached for the lifetime of the process. If Mail.app configuration
    changes mid-invocation (rare — requires Settings UI action), callers
    must restart the CLI to pick it up.

    Raises :class:`AppleScriptError` if Mail.app is not running, lacks
    automation permission, or returns no accounts.
    """
    raw = run_applescript(_BUILD_ACCOUNT_MAP_SCRIPT, timeout=10.0)
    accounts: list[Account] = []
    for line in raw.strip().splitlines():
        if "||" not in line:
            continue
        uuid, name = line.split("||", 1)
        accounts.append(Account(uuid=uuid.strip(), name=name.strip()))
    return tuple(accounts)


def name_for_uuid(uuid: str) -> str:
    """Return the display name for *uuid*, or the UUID itself as a fallback."""
    for acct in get_account_map():
        if acct.uuid == uuid:
            return acct.name
    return uuid


def uuid_for_name(name: str) -> str | None:
    """Return the UUID for *name*, or None if not found (case-insensitive)."""
    lowered = name.lower()
    for acct in get_account_map():
        if acct.name.lower() == lowered:
            return acct.uuid
    return None


def clear_cache() -> None:
    """Reset the cached account map. Exposed for tests."""
    get_account_map.cache_clear()
