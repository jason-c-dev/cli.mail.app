"""Custom exception hierarchy for mailctl.

All AppleScript-related errors inherit from AppleScriptError, with specific
subclasses for categorised failure modes (Mail not running, permission denied,
timeout). This lets CLI error handlers match on type rather than parsing
error messages.
"""


class AppleScriptError(Exception):
    """Base exception for all AppleScript execution failures.

    Attributes:
        message: Human-readable error description.
        stderr: Raw stderr output from osascript, if available.
    """

    def __init__(self, message: str, stderr: str = "") -> None:
        self.message = message
        self.stderr = stderr
        super().__init__(message)


class MailNotRunningError(AppleScriptError):
    """Raised when Mail.app is not running or unreachable."""

    def __init__(self, stderr: str = "") -> None:
        super().__init__(
            "Mail.app is not running. Launch Mail.app and try again.",
            stderr=stderr,
        )


class PermissionDeniedError(AppleScriptError):
    """Raised when automation permission for Mail.app has not been granted."""

    def __init__(self, stderr: str = "") -> None:
        super().__init__(
            "Automation permission denied. Grant access in "
            "System Settings > Privacy & Security > Automation.",
            stderr=stderr,
        )


class ScriptTimeoutError(AppleScriptError):
    """Raised when an osascript call exceeds the configured timeout."""

    def __init__(self, timeout: float, stderr: str = "") -> None:
        self.timeout = timeout
        super().__init__(
            f"AppleScript timed out after {timeout}s. "
            f"Mail.app may be unresponsive — try restarting Mail.app or "
            f"check whether it is stuck on a dialog.",
            stderr=stderr,
        )


# Exit code constants — used across the CLI.
EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_USAGE_ERROR = 2
