"""Shared pytest fixtures for mailctl tests.

The key fixture is :func:`mock_osascript`, which patches ``subprocess.run``
inside the engine module so that tests never call real ``osascript``. This
is the single mock seam described in the architecture.
"""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class OsascriptMock:
    """Programmable stand-in for osascript subprocess calls.

    Usage in tests::

        def test_something(mock_osascript):
            mock_osascript.set_output("some output")
            result = run_applescript("tell application \\"Mail\\" to name")
            assert result == "some output"

        def test_error(mock_osascript):
            mock_osascript.set_error("application isn't running", returncode=1)
            with pytest.raises(MailNotRunningError):
                run_applescript("tell application \\"Mail\\" to name")
    """

    def __init__(self) -> None:
        self._stdout: str = ""
        self._stderr: str = ""
        self._returncode: int = 0
        self._side_effect: Exception | None = None
        self._calls: list[list[str]] = []

    # -- Configuration helpers ------------------------------------------------

    def set_output(self, stdout: str, returncode: int = 0) -> None:
        """Configure a successful response."""
        self._stdout = stdout
        self._stderr = ""
        self._returncode = returncode
        self._side_effect = None

    def set_error(self, stderr: str, returncode: int = 1) -> None:
        """Configure a failed response with stderr."""
        self._stdout = ""
        self._stderr = stderr
        self._returncode = returncode
        self._side_effect = None

    def set_timeout(self) -> None:
        """Configure a timeout side-effect."""
        self._side_effect = subprocess.TimeoutExpired(
            cmd=["osascript", "-e", "..."],
            timeout=30,
        )

    # -- Inspection -----------------------------------------------------------

    @property
    def calls(self) -> list[list[str]]:
        """All calls made to subprocess.run, as argument lists."""
        return self._calls

    @property
    def last_script(self) -> str | None:
        """The AppleScript passed in the most recent call, or None."""
        if not self._calls:
            return None
        # Expect ['osascript', '-e', '<script>']
        args = self._calls[-1]
        if len(args) >= 3:
            return args[2]
        return None

    # -- Internal callback used by the patch ----------------------------------

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self._calls.append(list(args))

        if self._side_effect is not None:
            raise self._side_effect

        return subprocess.CompletedProcess(
            args=args,
            returncode=self._returncode,
            stdout=self._stdout,
            stderr=self._stderr,
        )


@pytest.fixture
def mock_osascript() -> OsascriptMock:
    """Patch ``subprocess.run`` in the engine module and return a controller.

    The returned :class:`OsascriptMock` lets tests configure return values
    and simulate errors without touching the real ``osascript`` binary.
    """
    mock = OsascriptMock()
    with patch("mailctl.engine.subprocess.run", side_effect=mock):
        yield mock
