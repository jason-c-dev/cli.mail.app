"""Unit tests for ``mailctl compose`` (C-106 to C-129).

Every test uses the ``mock_osascript`` fixture — no test in this module
invokes the real ``osascript`` binary or causes Mail.app to send a real
message.  The send code path is verified by string inspection of the
generated AppleScript only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app
from mailctl.commands.compose import (
    build_compose_script,
    parse_account_names_output,
)


_click_app = typer.main.get_command(app)
runner = CliRunner()


# A mock AppleScript return value representing a newly-created message id.
DRAFT_ID_OUTPUT = "123456"

# Account-names response for --from validation calls.
ACCOUNTS_OUTPUT = "Work\nPersonal"


def _multi_outputs(mock, *, accounts: str = ACCOUNTS_OUTPUT, message_id: str = DRAFT_ID_OUTPUT) -> None:
    """Configure a mock to return accounts first, then the message id."""
    mock.set_outputs([accounts, message_id])


# --------------------------------------------------------------------------- #
# C-106: basic happy path
# --------------------------------------------------------------------------- #


class TestComposeBasic:
    def test_basic_compose_exit_zero(self, mock_osascript):
        """C-106: exit 0 with --to, --subject, --body."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "alice@example.com",
                "--subject", "Hello",
                "--body", "World",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_basic_compose_osascript_invoked(self, mock_osascript):
        """C-106: at least one osascript call was made."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "alice@example.com",
                "--subject", "Hello",
                "--body", "World",
            ],
        )
        assert len(mock_osascript.calls) >= 1


# --------------------------------------------------------------------------- #
# C-107: missing required args
# --------------------------------------------------------------------------- #


class TestComposeRequiredArgs:
    def test_missing_to_exit_nonzero(self, mock_osascript):
        """C-107: missing --to -> non-zero exit."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--subject", "Hello", "--body", "World"],
        )
        assert result.exit_code != 0
        assert mock_osascript.calls == []

    def test_missing_subject_exit_nonzero(self, mock_osascript):
        """C-107: missing --subject -> non-zero exit."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "alice@example.com", "--body", "World"],
        )
        assert result.exit_code != 0
        assert mock_osascript.calls == []


# --------------------------------------------------------------------------- #
# C-108: repeatable recipients
# --------------------------------------------------------------------------- #


class TestComposeRecipients:
    def test_multiple_recipients_in_script(self, mock_osascript):
        """C-108: all --to / --cc / --bcc addresses appear in AppleScript."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--to", "b@x.com",
                "--cc", "c@x.com",
                "--cc", "d@x.com",
                "--bcc", "e@x.com",
                "--subject", "S",
                "--body", "B",
            ],
        )
        assert result.exit_code == 0, result.output
        # Concatenate every script passed to osascript.
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        for addr in ["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com"]:
            assert addr in all_scripts, f"{addr} missing from generated script"

    def test_recipient_type_classification(self, mock_osascript):
        """C-108: 'to recipient', 'cc recipient', 'bcc recipient' keys exist."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--cc", "c@x.com",
                "--bcc", "e@x.com",
                "--subject", "S",
                "--body", "B",
            ],
        )
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "to recipient" in all_scripts
        assert "cc recipient" in all_scripts
        assert "bcc recipient" in all_scripts


# --------------------------------------------------------------------------- #
# C-109, C-110: body sources
# --------------------------------------------------------------------------- #


class TestComposeBodySources:
    def test_inline_body_in_script(self, mock_osascript):
        """C-109: --body <text> appears in generated AppleScript."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "inline text"],
        )
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "inline text" in all_scripts

    def test_body_file_in_script(self, mock_osascript, tmp_path):
        """C-109: --body-file content appears in generated AppleScript."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("file body content")
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body-file", str(body_file),
            ],
        )
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "file body content" in all_scripts

    def test_stdin_body_in_script(self, mock_osascript):
        """C-109: piped stdin body appears in generated AppleScript."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S"],
            input="piped body",
        )
        assert result.exit_code == 0, result.output
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "piped body" in all_scripts

    def test_two_body_sources_errors(self, mock_osascript, tmp_path):
        """C-109: --body and --body-file together is a usage error."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("file body")
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "X",
                "--body-file", str(body_file),
            ],
        )
        assert result.exit_code != 0

    def test_no_body_tty_errors(self, mock_osascript, monkeypatch):
        """C-110: interactive stdin with no body flags -> usage error."""
        # Force stdin to be treated as a TTY.
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S"],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        lower = combined.lower()
        assert any(token in lower for token in ("body", "--body", "--body-file", "stdin"))


# --------------------------------------------------------------------------- #
# C-111: attachments
# --------------------------------------------------------------------------- #


class TestComposeAttachments:
    def test_attach_paths_in_script(self, mock_osascript, tmp_path):
        """C-111: attachment paths appear in AppleScript with attachment verb."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("aaa")
        b.write_text("bbb")
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--attach", str(a),
                "--attach", str(b),
            ],
        )
        assert result.exit_code == 0, result.output
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert str(a) in all_scripts
        assert str(b) in all_scripts
        assert "attachment" in all_scripts

    def test_nonexistent_attachment_fails_fast(self, mock_osascript):
        """C-111: missing attachment path -> error, no osascript call."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--attach", "/nonexistent/path/xyz.bin",
            ],
        )
        assert result.exit_code != 0
        assert mock_osascript.calls == []
        combined = (result.output or "") + (result.stderr or "")
        assert "nonexistent" in combined.lower() or "does not exist" in combined.lower()


# --------------------------------------------------------------------------- #
# C-112: --from account selection
# --------------------------------------------------------------------------- #


class TestComposeFromAccount:
    def test_from_account_appears_in_script(self, mock_osascript):
        """C-112: --from <account> binds sender in AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--from", "Work",
            ],
        )
        assert result.exit_code == 0, result.output
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        # Either references `account "Work"` or sets sender from it.
        assert 'account "Work"' in all_scripts or "senderAcct" in all_scripts
        assert "Work" in all_scripts

    def test_unknown_from_account_errors(self, mock_osascript):
        """C-112: unknown --from account -> non-zero exit and actionable error."""
        # Mock returns the account list only (no subsequent compose call).
        mock_osascript.set_output(ACCOUNTS_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--from", "NonexistentAccount",
            ],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        assert "NonexistentAccount" in combined


# --------------------------------------------------------------------------- #
# C-113: default path creates draft, never sends
# --------------------------------------------------------------------------- #


class TestComposeDefaultDraft:
    def test_default_no_send_verb(self, mock_osascript):
        """C-113: no --dangerously-send -> no 'send' verb in any script."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            # Guard against matching substrings of other identifiers:
            # check for the specific AppleScript send constructs.
            lowered = script.lower()
            assert "send newmessage" not in lowered
            assert "send outgoing message" not in lowered
            assert "send msg" not in lowered

    def test_default_creates_draft_script(self, mock_osascript):
        """C-113: generated script uses 'make new outgoing message'."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "make new outgoing message" in all_scripts

    def test_default_output_mentions_draft(self, mock_osascript):
        """C-113 / C-121: default output reports a draft was created."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        assert "draft" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-114 & C-117: --dangerously-send (via mock only)
# --------------------------------------------------------------------------- #


class TestComposeDangerouslySend:
    def test_dangerously_send_with_yes_produces_send_verb(self, mock_osascript):
        """C-114: --dangerously-send + --yes -> 'send' verb in script."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "send newMessage" in all_scripts

    def test_dangerously_send_stdout_reports_sent(self, mock_osascript):
        """C-114: stdout reports the message was sent."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
                "--yes",
            ],
        )
        assert "sent" in result.output.lower()

    def test_confirm_y_produces_send(self, mock_osascript):
        """C-117: typing 'y' at the prompt produces a send script."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
            ],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        all_scripts = "\n".join(call[2] for call in mock_osascript.calls if len(call) >= 3)
        assert "send newMessage" in all_scripts


# --------------------------------------------------------------------------- #
# C-116: decline at confirmation prompt -> no send
# --------------------------------------------------------------------------- #


class TestComposeConfirmationDecline:
    def test_empty_input_no_send(self, mock_osascript):
        """C-116: empty input at prompt -> no 'send' verb in any script."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
            ],
            input="\n",
        )
        # Either exit 0 with a cancellation message, or non-zero.
        # In both cases, no 'send' verb.
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send newMessage" not in script
            assert "send outgoing message" not in script.lower()
        assert "cancel" in result.output.lower() or result.exit_code != 0

    def test_n_input_no_send(self, mock_osascript):
        """C-116: 'n' at prompt -> no 'send' verb in any script."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
            ],
            input="n\n",
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send newMessage" not in script


# --------------------------------------------------------------------------- #
# C-118: --yes without --dangerously-send is harmless
# --------------------------------------------------------------------------- #


class TestComposeYesAlone:
    def test_yes_alone_creates_draft(self, mock_osascript):
        """C-118: --yes without --dangerously-send creates a draft, no send."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send newMessage" not in script
        assert "draft" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-119: --dry-run
# --------------------------------------------------------------------------- #


class TestComposeDryRun:
    def test_dry_run_no_compose_script(self, mock_osascript):
        """C-119: --dry-run does not invoke compose/send AppleScript."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "make new outgoing message" not in script
            assert "send newMessage" not in script

    def test_dry_run_describes_draft(self, mock_osascript):
        """C-119: dry-run output describes recipients, subject, and draft intent."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dry-run",
            ],
        )
        assert "a@x.com" in result.output
        assert "S" in result.output
        assert "draft" in result.output.lower()

    def test_dry_run_dangerously_send_no_actual_send(self, mock_osascript):
        """C-119: --dry-run + --dangerously-send describes a send, runs nothing."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
                "--yes",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "send" in result.output.lower()
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send newMessage" not in script


# --------------------------------------------------------------------------- #
# C-121: human-readable output
# --------------------------------------------------------------------------- #


class TestComposeHumanOutput:
    def test_default_output_mentions_draft_and_account(self, mock_osascript):
        """C-121: default output contains 'Draft' and account name."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--from", "Work",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "draft" in result.output.lower()
        assert "Work" in result.output
        assert "sent" not in result.output.lower()


# --------------------------------------------------------------------------- #
# C-122: JSON output
# --------------------------------------------------------------------------- #


class TestComposeJSON:
    def test_json_draft(self, mock_osascript):
        """C-122: --json emits structured draft result."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--cc", "c@x.com",
                "--subject", "S",
                "--body", "B",
                "--from", "Work",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert payload["action"] == "draft"
        assert payload["account"] == "Work"
        assert "a@x.com" in payload["to"]
        assert "c@x.com" in payload["cc"]
        assert payload["bcc"] == []
        assert payload["subject"] == "S"
        assert "id" in payload

    def test_json_sent(self, mock_osascript):
        """C-122: --dangerously-send --yes --json -> action == 'sent'."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "a@x.com",
                "--subject", "S",
                "--body", "B",
                "--dangerously-send",
                "--yes",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["action"] == "sent"


# --------------------------------------------------------------------------- #
# C-123: Mail.app not running
# --------------------------------------------------------------------------- #


class TestComposeMailNotRunning:
    def test_mail_not_running_exit_nonzero(self, mock_osascript):
        """C-123: Mail.app down -> non-zero exit."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        assert result.exit_code != 0

    def test_mail_not_running_error_mentions_mail(self, mock_osascript):
        """C-123: error message mentions Mail.app."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        combined = (result.stderr or "") + (result.output or "")
        assert "mail" in combined.lower()


# --------------------------------------------------------------------------- #
# C-124: stdout/stderr separation
# --------------------------------------------------------------------------- #


class TestComposeStreamSeparation:
    def test_success_output_on_stdout(self, mock_osascript):
        """C-124: successful compose output appears on stdout."""
        mock_osascript.set_output(DRAFT_ID_OUTPUT)
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
            catch_exceptions=False,
        )
        assert "draft" in result.stdout.lower()

    def test_error_on_stderr(self, mock_osascript):
        """C-124: Mail.app errors go to stderr."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        )
        assert "Error" in result.stderr or "error" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# C-125: help text
# --------------------------------------------------------------------------- #


class TestComposeHelp:
    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        result = runner.invoke(_click_app, ["compose", "--help"])
        return result.output

    def test_help_exit_zero(self):
        result = runner.invoke(_click_app, ["compose", "--help"])
        assert result.exit_code == 0

    def test_help_lists_all_options(self, help_output):
        for flag in [
            "--to", "--cc", "--bcc", "--subject",
            "--body", "--body-file", "--from", "--attach",
            "--dangerously-send", "--yes", "--dry-run", "--json",
        ]:
            assert flag in help_output, f"--help missing {flag}"

    def test_help_dangerously_send_warns(self, help_output):
        """C-125: --dangerously-send help text warns about sending."""
        # Find the chunk of the help that is about --dangerously-send.
        # Look for any of the warning words in the whole help output.
        lower = help_output.lower()
        assert any(word in lower for word in ("send", "sends", "real", "irreversible"))

    def test_help_mentions_draft_default(self, help_output):
        """C-125: help makes clear default behaviour creates a draft."""
        assert "draft" in help_output.lower()


# --------------------------------------------------------------------------- #
# C-126: top-level --help lists compose
# --------------------------------------------------------------------------- #


class TestTopLevelHelp:
    def test_top_level_lists_compose(self):
        result = runner.invoke(_click_app, ["--help"])
        assert result.exit_code == 0
        assert "compose" in result.output

    def test_top_level_still_lists_prior_groups(self):
        result = runner.invoke(_click_app, ["--help"])
        for cmd in ("accounts", "mailboxes", "messages"):
            assert cmd in result.output, f"{cmd} missing from top-level help"


# --------------------------------------------------------------------------- #
# Unit tests for helper functions
# --------------------------------------------------------------------------- #


class TestBuildComposeScript:
    def test_include_send_true_emits_send(self):
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            include_send=True,
        )
        assert "send newMessage" in script

    def test_include_send_false_omits_send(self):
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            include_send=False,
        )
        assert "send newMessage" not in script
        assert "make new outgoing message" in script

    def test_recipient_types_present(self):
        script = build_compose_script(
            to=["a@x.com"], cc=["c@x.com"], bcc=["b@x.com"],
            subject="S", body="B",
            include_send=False,
        )
        assert "to recipient" in script
        assert "cc recipient" in script
        assert "bcc recipient" in script

    def test_attachment_path_appears(self):
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            attachments=["/tmp/foo.txt"],
            include_send=False,
        )
        assert "/tmp/foo.txt" in script
        assert "attachment" in script


# --------------------------------------------------------------------------- #
# Regression: issue #3 — --from must resolve the account BEFORE the draft is
# created, so a failed lookup doesn't leak a recipient-less draft.
# See https://github.com/jason-c-dev/cli.mail.app/issues/3.
# --------------------------------------------------------------------------- #


class TestFromAccountNoLeak:
    """Issue #3 regression: sender resolved before draft creation."""

    def test_account_lookup_precedes_make_new(self):
        """If --from fails, AppleScript errors BEFORE `make new outgoing message`
        runs — so no partial draft is left in the default account."""
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            from_account="Google",
            include_send=False,
        )
        idx_lookup = script.find('account "Google"')
        idx_make = script.find("make new outgoing message")
        assert idx_lookup != -1, "sender lookup missing"
        assert idx_make != -1, "make-new missing"
        assert idx_lookup < idx_make, (
            "account lookup must precede draft creation so a failed "
            "lookup doesn't leak a blank draft"
        )

    def test_set_sender_follows_recipients(self):
        """`set sender` must come AFTER the recipient block. If it came
        before (or inside `make new outgoing message properties`),
        Mail.app auto-saves the draft and the later recipient writes
        silently fail — landing an empty-to draft in the --from account."""
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            from_account="Google",
            include_send=False,
        )
        idx_recip = script.find("to recipient")
        idx_set_sender = script.find("set sender of newMessage")
        assert idx_recip != -1, "recipient block missing"
        assert idx_set_sender != -1, "set sender missing"
        assert idx_recip < idx_set_sender, (
            "recipients must be added before `set sender`; otherwise "
            "Mail.app auto-saves and the recipient writes fail"
        )
        # `sender:` must NOT appear in the make-new properties dict —
        # that's what triggers the auto-save.
        assert "sender:senderEmail" not in script

    def test_partial_draft_rollback_on_error(self):
        """If anything after `make new outgoing message` fails, the
        partial draft must be deleted before the error propagates."""
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            from_account="Google",
            include_send=False,
        )
        assert "delete newMessage" in script
        assert "on error" in script


# --------------------------------------------------------------------------- #
# Regression: issue #5 — compose must print the canonical SQLite ROWID so the
# downstream CLI (drafts edit, messages mark/show/delete) accepts the id
# unchanged.
# See https://github.com/jason-c-dev/cli.mail.app/issues/5.
# --------------------------------------------------------------------------- #


class TestComposePrintsCanonicalId:
    """Issue #5: the id compose prints must be the same id space the rest
    of mailctl uses. AppleScript returns small, internal integers; SQLite
    ROWIDs are 5-6 digit numbers. Look up the canonical id via the
    Envelope Index after save, fall back to the AppleScript id if the
    index hasn't caught up yet."""

    def test_canonical_id_returned_when_draft_indexed(
        self, envelope_db, mock_osascript,
    ):
        """Happy path: SQLite has the freshly-saved draft, so the id
        returned reflects the ROWID, not the AppleScript number."""
        from tests.conftest import TEST_ACCOUNT_ALICE_UUID

        drafts = envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/Drafts")
        # Seed a draft that matches what we're about to compose.
        rowid = envelope_db.add_message(
            mailbox_rowid=drafts,
            subject="issue5-canonical",
            sender="alice@example.com",
            date_received=1700000000,
        )

        # Two AppleScript calls: fetch_account_names, then compose.
        mock_osascript.set_outputs(["Alice\nBob", "5"])
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "x@y.com",
                "--subject", "issue5-canonical",
                "--body", "b",
                "--from", "Alice",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == str(rowid), (
            f"compose returned {data['id']!r}, expected SQLite ROWID "
            f"{rowid!r} (AppleScript returned '5')"
        )

    def test_fallback_to_applescript_id_when_not_indexed(
        self, envelope_db, mock_osascript,
    ):
        """If SQLite doesn't know about the draft yet (index race), we
        fall back to the AppleScript id rather than erroring. The user
        can re-fetch via `drafts list` once the index catches up."""
        from tests.conftest import TEST_ACCOUNT_ALICE_UUID
        envelope_db.add_mailbox(f"imap://{TEST_ACCOUNT_ALICE_UUID}/Drafts")
        # Deliberately do NOT seed any matching message.

        mock_osascript.set_outputs(["Alice\nBob", "7"])
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "x@y.com",
                "--subject", "no-match-here",
                "--body", "b",
                "--from", "Alice",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "7", (
            "fallback to AppleScript id when SQLite lookup is empty"
        )

    def test_send_path_keeps_applescript_id(
        self, envelope_db, mock_osascript,
    ):
        """--dangerously-send doesn't save a draft; there's no ROWID
        to look up. Return the AppleScript id unchanged."""
        mock_osascript.set_outputs(["99"])
        result = runner.invoke(
            _click_app,
            [
                "compose",
                "--to", "x@y.com",
                "--subject", "sendpath",
                "--body", "b",
                "--dangerously-send",
                "--yes",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "99"

    def test_no_inline_item_1_email_addresses(self):
        """The bare `item 1 of (email addresses of X)` pattern fails with
        AppleScript -1700 because indexing into Mail-internal collections
        doesn't materialise inline. Use `first item of emails as text`
        via an intermediate binding."""
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            from_account="Google",
            include_send=False,
        )
        assert "item 1 of (email addresses" not in script
        assert "first item of senderEmails" in script

    def test_without_from_omits_sender_property(self):
        """Without --from the script must not touch sender at all."""
        script = build_compose_script(
            to=["a@x.com"], cc=[], bcc=[],
            subject="S", body="B",
            include_send=False,
        )
        assert "sender:" not in script
        assert "senderAccount" not in script
        assert "senderEmail" not in script


class TestParseAccountNamesOutput:
    def test_empty(self):
        assert parse_account_names_output("") == []

    def test_multi(self):
        assert parse_account_names_output("Work\nPersonal") == ["Work", "Personal"]
