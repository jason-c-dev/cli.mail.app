"""Unit tests for ``mailctl reply`` and ``mailctl forward`` (C-131 to C-157).

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
from mailctl.commands.reply_forward import (
    build_fetch_message_script,
    build_forward_script,
    build_reply_script,
    parse_fetch_message_output,
    _build_quoted_body,
    _compute_reply_recipients,
)


_click_app = typer.main.get_command(app)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# Mock data constants
# --------------------------------------------------------------------------- #

# Simulated original message fetch output (sender||to||cc||subject||date||body)
ORIGINAL_MSG_OUTPUT = (
    "alice@example.com||"
    "user@example.com, bob@example.com||"
    "carol@example.com||"
    "Hello||"
    "Monday, April 14, 2025 at 10:00:00 AM||"
    "Original body text"
)

# Message id returned by the reply/forward compose script.
REPLY_ID_OUTPUT = "654321"


def _multi_outputs(
    mock,
    *,
    fetch: str = ORIGINAL_MSG_OUTPUT,
    compose_id: str = REPLY_ID_OUTPUT,
) -> None:
    """Configure mock to return fetch output first, then compose id."""
    mock.set_outputs([fetch, compose_id])


# --------------------------------------------------------------------------- #
# C-131: reply command basic happy path
# --------------------------------------------------------------------------- #


class TestReplyBasic:
    def test_reply_exit_zero(self, mock_osascript):
        """C-131: exit 0 with message-id and --body."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Thanks"],
        )
        assert result.exit_code == 0, result.output

    def test_reply_osascript_called_at_least_twice(self, mock_osascript):
        """C-131: at least two osascript calls (fetch + reply)."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Thanks"],
        )
        # One for fetch, one for reply
        assert len(mock_osascript.calls) >= 2


# --------------------------------------------------------------------------- #
# C-132: reply sets recipient to original sender only
# --------------------------------------------------------------------------- #


class TestReplyRecipients:
    def test_reply_to_sender_only(self, mock_osascript):
        """C-132: plain reply addresses original sender only."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Got it"],
        )
        assert result.exit_code == 0, result.output
        # The reply script is the second call (first is fetch)
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "alice@example.com" in reply_script
        # bob and carol should NOT be recipients
        assert "bob@example.com" not in reply_script
        assert "carol@example.com" not in reply_script


# --------------------------------------------------------------------------- #
# C-133: reply --all sets all recipients
# --------------------------------------------------------------------------- #


# User email addresses returned by the user-emails fetch call.
USER_EMAILS_OUTPUT = "user@example.com"


def _reply_all_outputs(
    mock,
    *,
    fetch: str = ORIGINAL_MSG_OUTPUT,
    user_emails: str = USER_EMAILS_OUTPUT,
    compose_id: str = REPLY_ID_OUTPUT,
) -> None:
    """Configure mock for reply-all: fetch msg, fetch user emails, reply."""
    mock.set_outputs([fetch, user_emails, compose_id])


class TestReplyAll:
    def test_reply_all_includes_all_recipients(self, mock_osascript):
        """C-133: reply --all addresses sender + To + Cc (minus user)."""
        _reply_all_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--all", "--body", "Agreed"],
        )
        assert result.exit_code == 0, result.output
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        # alice (sender) and bob (original To) should be in to recipients
        assert "alice@example.com" in reply_script
        assert "bob@example.com" in reply_script
        # carol (original Cc) should be in cc recipients
        assert "carol@example.com" in reply_script

    def test_reply_all_to_recipients(self, mock_osascript):
        """C-133: to recipients include sender and original To (minus user)."""
        _reply_all_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--all", "--body", "Agreed"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        # alice and bob should be 'to recipient'
        # carol should be 'cc recipient'
        lines = reply_script.split("\n")
        to_lines = [l for l in lines if "to recipient" in l and "cc recipient" not in l]
        cc_lines = [l for l in lines if "cc recipient" in l]
        to_text = " ".join(to_lines)
        cc_text = " ".join(cc_lines)
        assert "alice@example.com" in to_text
        assert "bob@example.com" in to_text
        assert "carol@example.com" in cc_text

    def test_reply_all_excludes_user_address(self, mock_osascript):
        """C-133: user's own address not duplicated in recipients."""
        _reply_all_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--all", "--body", "Agreed"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        # user@example.com should not be a recipient in the reply script
        # Count occurrences of "user@example.com" in recipient creation lines
        recip_lines = [l for l in reply_script.split("\n") if "recipient" in l and "delete" not in l]
        recip_text = " ".join(recip_lines)
        assert "user@example.com" not in recip_text


# --------------------------------------------------------------------------- #
# C-134: forward command basic happy path
# --------------------------------------------------------------------------- #


class TestForwardBasic:
    def test_forward_exit_zero(self, mock_osascript):
        """C-134: exit 0 with message-id, --to, and --body."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "dave@example.com", "--body", "FYI"],
        )
        assert result.exit_code == 0, result.output

    def test_forward_missing_to_errors(self, mock_osascript):
        """C-134: missing --to produces usage error."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--body", "FYI"],
        )
        assert result.exit_code != 0

    def test_forward_multiple_to(self, mock_osascript):
        """C-134: multiple --to addresses appear in generated AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--to", "b@x.com",
                "--body", "FYI",
            ],
        )
        assert result.exit_code == 0, result.output
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "a@x.com" in fwd_script
        assert "b@x.com" in fwd_script


# --------------------------------------------------------------------------- #
# C-135: original content included in reply and forward
# --------------------------------------------------------------------------- #


class TestOriginalContent:
    def test_reply_includes_original_body(self, mock_osascript):
        """C-135: reply body contains both new text and original content."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "My reply"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "My reply" in reply_script
        assert "Original body text" in reply_script

    def test_reply_includes_attribution(self, mock_osascript):
        """C-135: reply body includes attribution line with date and sender."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "My reply"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "alice@example.com" in reply_script
        assert "wrote" in reply_script.lower()

    def test_forward_includes_original_body(self, mock_osascript):
        """C-135: forward body contains user text + original content."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "d@x.com", "--body", "See below"],
        )
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "See below" in fwd_script
        assert "Original body text" in fwd_script

    def test_forward_includes_attribution(self, mock_osascript):
        """C-135: forward body includes attribution line."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "d@x.com", "--body", "See below"],
        )
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "alice@example.com" in fwd_script
        assert "wrote" in fwd_script.lower()


# --------------------------------------------------------------------------- #
# C-136: body sources (--body, --body-file, stdin)
# --------------------------------------------------------------------------- #


class TestBodySources:
    def test_reply_inline_body(self, mock_osascript):
        """C-136: --body <text> appears in reply AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "inline"],
        )
        assert result.exit_code == 0
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "inline" in reply_script

    def test_reply_body_file(self, mock_osascript, tmp_path):
        """C-136: --body-file content appears in reply AppleScript."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("file content")
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body-file", str(body_file)],
        )
        assert result.exit_code == 0
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "file content" in reply_script

    def test_reply_stdin(self, mock_osascript):
        """C-136: piped stdin body appears in reply AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345"],
            input="piped content",
        )
        assert result.exit_code == 0, result.output
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "piped content" in reply_script

    def test_reply_two_sources_errors(self, mock_osascript, tmp_path):
        """C-136: --body and --body-file together is a usage error."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("file body")
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "X", "--body-file", str(body_file)],
        )
        assert result.exit_code != 0

    def test_forward_body_file(self, mock_osascript, tmp_path):
        """C-136: --body-file works on forward too (parity)."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("fwd file content")
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body-file", str(body_file)],
        )
        assert result.exit_code == 0
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "fwd file content" in fwd_script


# --------------------------------------------------------------------------- #
# C-137: attachments on reply and forward
# --------------------------------------------------------------------------- #


class TestAttachments:
    def test_reply_attachments_in_script(self, mock_osascript, tmp_path):
        """C-137: attachment paths appear in reply AppleScript."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("aaa")
        b.write_text("bbb")
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "reply", "12345",
                "--body", "B",
                "--attach", str(a),
                "--attach", str(b),
            ],
        )
        assert result.exit_code == 0, result.output
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert str(a) in reply_script
        assert str(b) in reply_script

    def test_forward_attachments_in_script(self, mock_osascript, tmp_path):
        """C-137: attachment paths appear in forward AppleScript."""
        a = tmp_path / "a.txt"
        a.write_text("aaa")
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--body", "B",
                "--attach", str(a),
            ],
        )
        assert result.exit_code == 0, result.output
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert str(a) in fwd_script
        assert "attachment" in fwd_script

    def test_reply_nonexistent_attachment_fails(self, mock_osascript):
        """C-137: missing attachment path -> error before any osascript call."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--attach", "/nonexistent"],
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# C-138: default reply creates draft, no send verb
# --------------------------------------------------------------------------- #


class TestReplyDefaultDraft:
    def test_no_send_verb_in_reply(self, mock_osascript):
        """C-138: no 'send' verb when --dangerously-send absent."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Thanks"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            lowered = script.lower()
            assert "send replymsg" not in lowered
            assert "send fwdmsg" not in lowered
            assert "send newmessage" not in lowered
            assert "send outgoing" not in lowered

    def test_reply_output_mentions_draft(self, mock_osascript):
        """C-138: output reports a draft was created."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Thanks"],
        )
        assert "draft" in result.output.lower()

    def test_reply_creates_via_reply_verb(self, mock_osascript):
        """C-138: reply script uses Mail.app's reply construct."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "Thanks"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "reply" in reply_script.lower()


# --------------------------------------------------------------------------- #
# C-139: default forward creates draft, no send verb
# --------------------------------------------------------------------------- #


class TestForwardDefaultDraft:
    def test_no_send_verb_in_forward(self, mock_osascript):
        """C-139: no 'send' verb when --dangerously-send absent."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "FYI"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            lowered = script.lower()
            assert "send fwdmsg" not in lowered
            assert "send replymsg" not in lowered
            assert "send newmessage" not in lowered
            assert "send outgoing" not in lowered

    def test_forward_output_mentions_draft(self, mock_osascript):
        """C-139: output reports a draft was created."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "FYI"],
        )
        assert "draft" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-140: --dangerously-send --yes on reply triggers send (mock only)
# --------------------------------------------------------------------------- #


class TestReplyDangerouslySend:
    def test_reply_send_verb_present(self, mock_osascript):
        """C-140: --dangerously-send --yes produces 'send' verb in script."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dangerously-send", "--yes"],
        )
        assert result.exit_code == 0, result.output
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "send replyMsg" in reply_script

    def test_reply_send_output_reports_sent(self, mock_osascript):
        """C-140: output reports message was sent."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dangerously-send", "--yes"],
        )
        assert "sent" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-141: --dangerously-send --yes on forward triggers send (mock only)
# --------------------------------------------------------------------------- #


class TestForwardDangerouslySend:
    def test_forward_send_verb_present(self, mock_osascript):
        """C-141: --dangerously-send --yes produces 'send' verb in script."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--body", "B",
                "--dangerously-send",
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        fwd_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        assert "send fwdMsg" in fwd_script

    def test_forward_send_output_reports_sent(self, mock_osascript):
        """C-141: output reports message was sent."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--body", "B",
                "--dangerously-send",
                "--yes",
            ],
        )
        assert "sent" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-142: confirmation prompt declines -> no send
# --------------------------------------------------------------------------- #


class TestConfirmationDecline:
    def test_reply_empty_input_no_send(self, mock_osascript):
        """C-142: empty input at reply prompt -> no 'send' verb."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dangerously-send"],
            input="\n",
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script
            assert "send fwdMsg" not in script
        assert "cancel" in result.output.lower() or result.exit_code == 0

    def test_forward_n_input_no_send(self, mock_osascript):
        """C-142: 'n' at forward prompt -> no 'send' verb."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--body", "B",
                "--dangerously-send",
            ],
            input="n\n",
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script
            assert "send replyMsg" not in script


# --------------------------------------------------------------------------- #
# C-143: --yes alone without --dangerously-send never sends
# --------------------------------------------------------------------------- #


class TestYesAlone:
    def test_reply_yes_alone_drafts(self, mock_osascript):
        """C-143: --yes without --dangerously-send on reply creates draft."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--yes"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script
            assert "send fwdMsg" not in script
        assert "draft" in result.output.lower()

    def test_forward_yes_alone_drafts(self, mock_osascript):
        """C-143: --yes without --dangerously-send on forward creates draft."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B", "--yes"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script
            assert "send replyMsg" not in script
        assert "draft" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-145: --dry-run on reply
# --------------------------------------------------------------------------- #


class TestReplyDryRun:
    def test_dry_run_reply_no_compose_script(self, mock_osascript):
        """C-145: --dry-run does not execute reply compose AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        # The fetch call is acceptable (read-only). But no compose/send call.
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "make new outgoing message" not in script
            assert "send replyMsg" not in script
            assert "reply originalMsg" not in script
        assert "draft" in result.output.lower()

    def test_dry_run_reply_dangerously_send(self, mock_osascript):
        """C-145: --dry-run + --dangerously-send --yes describes send, runs nothing."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dangerously-send", "--yes", "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "send" in result.output.lower()
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script


# --------------------------------------------------------------------------- #
# C-146: --dry-run on forward
# --------------------------------------------------------------------------- #


class TestForwardDryRun:
    def test_dry_run_forward_no_compose_script(self, mock_osascript):
        """C-146: --dry-run does not execute forward compose AppleScript."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B", "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "make new outgoing message" not in script
            assert "send fwdMsg" not in script
            assert "forward originalMsg" not in script
        assert "draft" in result.output.lower()

    def test_dry_run_forward_dangerously_send(self, mock_osascript):
        """C-146: --dry-run + --dangerously-send --yes describes send, runs nothing."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            [
                "forward", "12345",
                "--to", "a@x.com",
                "--body", "B",
                "--dangerously-send", "--yes",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "send" in result.output.lower()
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script


# --------------------------------------------------------------------------- #
# C-147: reply threading — references original message
# --------------------------------------------------------------------------- #


class TestReplyThreading:
    def test_reply_script_references_original_message(self, mock_osascript):
        """C-147: reply AppleScript references the original message id."""
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B"],
        )
        reply_script = mock_osascript.calls[-1][2] if len(mock_osascript.calls) >= 2 else ""
        # The script should reference the original message id for threading
        assert "12345" in reply_script
        # And use Mail.app's reply construct
        assert "reply" in reply_script.lower()


# --------------------------------------------------------------------------- #
# C-148: human-readable output
# --------------------------------------------------------------------------- #


class TestHumanOutput:
    def test_reply_draft_output(self, mock_osascript):
        """C-148: reply draft output mentions 'draft' and subject."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B"],
        )
        assert "draft" in result.output.lower()
        # Subject should appear
        assert "hello" in result.output.lower() or "Hello" in result.output

    def test_forward_draft_output(self, mock_osascript):
        """C-148: forward draft output mentions 'draft' and subject."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B"],
        )
        assert "draft" in result.output.lower()

    def test_reply_sent_output(self, mock_osascript):
        """C-148: reply sent output mentions 'sent'."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--dangerously-send", "--yes"],
        )
        assert "sent" in result.output.lower()


# --------------------------------------------------------------------------- #
# C-149: JSON output
# --------------------------------------------------------------------------- #


class TestJSONOutput:
    def test_reply_json(self, mock_osascript):
        """C-149: reply --json emits valid JSON with required keys."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert payload["action"] == "draft"
        assert "to" in payload
        assert "subject" in payload
        assert "id" in payload

    def test_forward_json(self, mock_osascript):
        """C-149: forward --json emits valid JSON with required keys."""
        _multi_outputs(mock_osascript)
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert payload["action"] == "draft"
        assert "a@x.com" in payload["to"]
        assert "original_message_id" in payload
        assert payload["original_message_id"] == "12345"


# --------------------------------------------------------------------------- #
# C-150: nonexistent message id -> clear error
# --------------------------------------------------------------------------- #


class TestMessageNotFound:
    def test_reply_message_not_found(self, mock_osascript):
        """C-150: reply with nonexistent id -> non-zero exit, error message."""
        mock_osascript.set_error("Can't get message id 99999.")
        result = runner.invoke(
            _click_app,
            ["reply", "99999", "--body", "B"],
        )
        assert result.exit_code != 0
        combined = (result.stderr or "") + (result.output or "")
        assert "99999" in combined or "not found" in combined.lower() or "error" in combined.lower()

    def test_forward_message_not_found(self, mock_osascript):
        """C-150: forward with nonexistent id -> non-zero exit, error message."""
        mock_osascript.set_error("Can't get message id 99999.")
        result = runner.invoke(
            _click_app,
            ["forward", "99999", "--to", "a@x.com", "--body", "B"],
        )
        assert result.exit_code != 0
        combined = (result.stderr or "") + (result.output or "")
        assert "99999" in combined or "not found" in combined.lower() or "error" in combined.lower()


# --------------------------------------------------------------------------- #
# C-151: Mail.app not running -> consistent error
# --------------------------------------------------------------------------- #


class TestMailNotRunning:
    def test_reply_mail_not_running(self, mock_osascript):
        """C-151: reply when Mail.app not running -> non-zero exit."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B"],
        )
        assert result.exit_code != 0
        combined = (result.stderr or "") + (result.output or "")
        assert "mail" in combined.lower()

    def test_forward_mail_not_running(self, mock_osascript):
        """C-151: forward when Mail.app not running -> non-zero exit."""
        mock_osascript.set_error("application isn't running")
        result = runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B"],
        )
        assert result.exit_code != 0
        combined = (result.stderr or "") + (result.output or "")
        assert "mail" in combined.lower()


# --------------------------------------------------------------------------- #
# C-152: top-level --help lists reply and forward
# --------------------------------------------------------------------------- #


class TestTopLevelHelp:
    def test_top_level_lists_reply(self):
        """C-152: 'reply' listed in top-level --help."""
        result = runner.invoke(_click_app, ["--help"])
        assert "reply" in result.output

    def test_top_level_lists_forward(self):
        """C-152: 'forward' listed in top-level --help."""
        result = runner.invoke(_click_app, ["--help"])
        assert "forward" in result.output

    def test_top_level_still_lists_prior_commands(self):
        """C-152: regression - compose, accounts, mailboxes, messages still listed."""
        result = runner.invoke(_click_app, ["--help"])
        for cmd in ("compose", "accounts", "mailboxes", "messages"):
            assert cmd in result.output, f"{cmd} missing from top-level help"


# --------------------------------------------------------------------------- #
# C-153: reply/forward --help describe all options
# --------------------------------------------------------------------------- #


class TestCommandHelp:
    def test_reply_help_lists_all_options(self):
        """C-153: reply --help lists all expected options."""
        result = runner.invoke(_click_app, ["reply", "--help"])
        assert result.exit_code == 0
        for flag in ["--body", "--body-file", "--attach", "--dangerously-send",
                      "--yes", "--dry-run", "--json", "--all"]:
            assert flag in result.output, f"reply --help missing {flag}"

    def test_reply_help_warns_about_send(self):
        """C-153: reply --help warns about --dangerously-send."""
        result = runner.invoke(_click_app, ["reply", "--help"])
        lower = result.output.lower()
        assert any(w in lower for w in ("danger", "send", "irreversible"))

    def test_forward_help_lists_all_options(self):
        """C-153: forward --help lists all expected options."""
        result = runner.invoke(_click_app, ["forward", "--help"])
        assert result.exit_code == 0
        for flag in ["--to", "--body", "--body-file", "--attach",
                      "--dangerously-send", "--yes", "--dry-run", "--json"]:
            assert flag in result.output, f"forward --help missing {flag}"


# --------------------------------------------------------------------------- #
# C-154: parametrised safety-model tests
# --------------------------------------------------------------------------- #


_REPLY_SAFETY_SCENARIOS = [
    # (description, extra_args)
    ("bare reply", ["reply", "12345", "--body", "B"]),
    ("reply --yes", ["reply", "12345", "--body", "B", "--yes"]),
    ("reply --dry-run", ["reply", "12345", "--body", "B", "--dry-run"]),
    ("reply --all", None),  # handled separately (needs reply-all mock)
    ("reply with --attach", None),  # handled separately (needs tmp_path)
    ("reply with --body-file", None),  # handled separately
    ("reply --json", ["reply", "12345", "--body", "B", "--json"]),
    ("reply --all --yes", None),  # handled separately (needs reply-all mock)
]

_FORWARD_SAFETY_SCENARIOS = [
    ("bare forward", ["forward", "12345", "--to", "a@x.com", "--body", "B"]),
    ("forward --yes", ["forward", "12345", "--to", "a@x.com", "--body", "B", "--yes"]),
    ("forward --dry-run", ["forward", "12345", "--to", "a@x.com", "--body", "B", "--dry-run"]),
    ("forward multiple --to", ["forward", "12345", "--to", "a@x.com", "--to", "b@x.com", "--body", "B"]),
    ("forward with --attach", None),  # handled separately
    ("forward with --body-file", None),  # handled separately
    ("forward --json", ["forward", "12345", "--to", "a@x.com", "--body", "B", "--json"]),
    ("forward --yes --json", ["forward", "12345", "--to", "a@x.com", "--body", "B", "--yes", "--json"]),
]


class TestReplySafetyModel:
    """C-154: Parametrised safety tests for reply — at least 6 bypass scenarios."""

    @pytest.mark.parametrize(
        "desc,args",
        [(s[0], s[1]) for s in _REPLY_SAFETY_SCENARIOS if s[1] is not None],
        ids=[s[0] for s in _REPLY_SAFETY_SCENARIOS if s[1] is not None],
    )
    def test_no_send_verb_in_scenario(self, mock_osascript, desc, args):
        """No 'send' verb in any osascript call for this reply scenario."""
        _multi_outputs(mock_osascript)
        runner.invoke(_click_app, args)
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script, f"'send replyMsg' found in {desc}"
            assert "send fwdMsg" not in script, f"'send fwdMsg' found in {desc}"

    def test_no_send_with_attach(self, mock_osascript, tmp_path):
        """No 'send' when reply has --attach (without --dangerously-send)."""
        a = tmp_path / "a.txt"
        a.write_text("aaa")
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body", "B", "--attach", str(a)],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script

    def test_no_send_with_body_file(self, mock_osascript, tmp_path):
        """No 'send' when reply has --body-file (without --dangerously-send)."""
        bf = tmp_path / "body.txt"
        bf.write_text("file body")
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--body-file", str(bf)],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script

    def test_no_send_reply_all(self, mock_osascript):
        """No 'send' when reply --all (without --dangerously-send)."""
        _reply_all_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--all", "--body", "B"],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script

    def test_no_send_reply_all_yes(self, mock_osascript):
        """No 'send' when reply --all --yes (without --dangerously-send)."""
        _reply_all_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["reply", "12345", "--all", "--body", "B", "--yes"],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send replyMsg" not in script


class TestForwardSafetyModel:
    """C-154: Parametrised safety tests for forward — at least 6 bypass scenarios."""

    @pytest.mark.parametrize(
        "desc,args",
        [(s[0], s[1]) for s in _FORWARD_SAFETY_SCENARIOS if s[1] is not None],
        ids=[s[0] for s in _FORWARD_SAFETY_SCENARIOS if s[1] is not None],
    )
    def test_no_send_verb_in_scenario(self, mock_osascript, desc, args):
        """No 'send' verb in any osascript call for this forward scenario."""
        _multi_outputs(mock_osascript)
        runner.invoke(_click_app, args)
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script, f"'send fwdMsg' found in {desc}"
            assert "send replyMsg" not in script, f"'send replyMsg' found in {desc}"

    def test_no_send_with_attach(self, mock_osascript, tmp_path):
        """No 'send' when forward has --attach (without --dangerously-send)."""
        a = tmp_path / "a.txt"
        a.write_text("aaa")
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body", "B", "--attach", str(a)],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script

    def test_no_send_with_body_file(self, mock_osascript, tmp_path):
        """No 'send' when forward has --body-file (without --dangerously-send)."""
        bf = tmp_path / "body.txt"
        bf.write_text("file body")
        _multi_outputs(mock_osascript)
        runner.invoke(
            _click_app,
            ["forward", "12345", "--to", "a@x.com", "--body-file", str(bf)],
        )
        for call in mock_osascript.calls:
            script = call[2] if len(call) >= 3 else ""
            assert "send fwdMsg" not in script


# --------------------------------------------------------------------------- #
# Unit tests for helper functions
# --------------------------------------------------------------------------- #


class TestBuildReplyScript:
    def test_include_send_true_emits_send(self):
        script = build_reply_script(
            message_id="12345",
            to=["a@x.com"], cc=[], subject="S", body="B",
            include_send=True,
        )
        assert "send replyMsg" in script

    def test_include_send_false_omits_send(self):
        script = build_reply_script(
            message_id="12345",
            to=["a@x.com"], cc=[], subject="S", body="B",
            include_send=False,
        )
        assert "send replyMsg" not in script
        assert "save replyMsg" in script

    def test_script_references_original_id(self):
        script = build_reply_script(
            message_id="12345",
            to=["a@x.com"], cc=[], subject="S", body="B",
        )
        assert "12345" in script
        assert "reply" in script.lower()


class TestBuildForwardScript:
    def test_include_send_true_emits_send(self):
        script = build_forward_script(
            message_id="12345",
            to=["a@x.com"], subject="S", body="B",
            include_send=True,
        )
        assert "send fwdMsg" in script

    def test_include_send_false_omits_send(self):
        script = build_forward_script(
            message_id="12345",
            to=["a@x.com"], subject="S", body="B",
            include_send=False,
        )
        assert "send fwdMsg" not in script
        assert "save fwdMsg" in script

    def test_script_references_original_id(self):
        script = build_forward_script(
            message_id="12345",
            to=["a@x.com"], subject="S", body="B",
        )
        assert "12345" in script
        assert "forward" in script.lower()


class TestComputeReplyRecipients:
    def test_plain_reply(self):
        original = {"sender": "alice@example.com", "to": "user@example.com, bob@example.com", "cc": "carol@example.com"}
        to, cc = _compute_reply_recipients(original, reply_all=False)
        assert to == ["alice@example.com"]
        assert cc == []

    def test_reply_all(self):
        original = {"sender": "alice@example.com", "to": "user@example.com, bob@example.com", "cc": "carol@example.com"}
        to, cc = _compute_reply_recipients(original, reply_all=True, user_email="user@example.com")
        assert "alice@example.com" in to
        assert "bob@example.com" in to
        assert "user@example.com" not in to
        assert "carol@example.com" in cc

    def test_reply_all_no_duplicate_sender(self):
        original = {"sender": "alice@example.com", "to": "alice@example.com, bob@example.com", "cc": ""}
        to, cc = _compute_reply_recipients(original, reply_all=True)
        assert to.count("alice@example.com") == 1


class TestParseFetchMessageOutput:
    def test_parse_basic(self):
        raw = "sender@x.com||to@x.com||cc@x.com||Subject||Date||Body text"
        result = parse_fetch_message_output(raw)
        assert result["sender"] == "sender@x.com"
        assert result["to"] == "to@x.com"
        assert result["cc"] == "cc@x.com"
        assert result["subject"] == "Subject"
        assert result["date"] == "Date"
        assert result["body"] == "Body text"


class TestBuildQuotedBody:
    def test_quoted_body_format(self):
        original = {
            "sender": "alice@example.com",
            "date": "Monday, April 14, 2025 at 10:00:00 AM",
            "body": "Original body text",
        }
        result = _build_quoted_body(new_body="My reply", original=original)
        assert "My reply" in result
        assert "Original body text" in result
        assert "alice@example.com" in result
        assert "wrote" in result.lower()
        assert "> " in result  # Quoted lines
