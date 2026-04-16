"""Architectural safety-model tests (C-115, C-120).

The safety model is **architectural**: the ONLY way to generate AppleScript
containing the ``send`` verb is to pass the literal ``--dangerously-send``
CLI flag.  These tests encode that invariant as a parametrised matrix of
bypass attempts and as grep-style assertions over the source tree.

Structural note: these tests use the ``mock_osascript`` fixture, so they
never invoke real Mail.app.  No real email is ever sent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer.main
from click.testing import CliRunner

from mailctl.cli import app


_click_app = typer.main.get_command(app)
runner = CliRunner()

DRAFT_ID_OUTPUT = "99999"
ACCOUNTS_OUTPUT = "Work\nPersonal"


# --------------------------------------------------------------------------- #
# C-115: code-inspection — no env-var / config / alias bypass mechanism
# --------------------------------------------------------------------------- #


SRC_DIR = Path(__file__).resolve().parents[2] / "src"


class TestSafetyModelCodeInspection:
    """C-115: no backdoor can set --dangerously-send implicitly."""

    def _read_all_py_sources(self) -> dict[Path, str]:
        return {
            path: path.read_text()
            for path in SRC_DIR.rglob("*.py")
        }

    def test_no_envvar_reader_for_send_flag(self):
        """No os.environ / getenv reference to MAILCTL_*_SEND-style vars."""
        pattern = re.compile(
            r"(os\.environ|getenv|os\.getenv)[^\n]{0,200}(DANGEROUSLY[_ ]?SEND|MAILCTL[_ ]?SEND)",
            re.IGNORECASE,
        )
        for path, text in self._read_all_py_sources().items():
            assert not pattern.search(text), (
                f"Found env-var backdoor for dangerously-send in {path}"
            )

    def test_no_dangerously_send_environment_keys(self):
        """No bare DANGEROUSLY_SEND / MAILCTL_SEND literal in source."""
        # Allow the string in tests / comments, but not in the src tree.
        for path, text in self._read_all_py_sources().items():
            lower = text.lower()
            # Ban an environment-variable literal that could be read.
            assert "mailctl_dangerously_send" not in lower, (
                f"Reserved env var literal present in {path}"
            )

    def test_no_typer_envvar_on_dangerously_send(self):
        """The Typer Option for --dangerously-send has no envvar=... keyword."""
        compose_src = (SRC_DIR / "mailctl" / "commands" / "compose.py").read_text()
        # Find the block defining the dangerously_send option.
        idx = compose_src.find('"--dangerously-send"')
        assert idx != -1, "Could not locate --dangerously-send option in compose.py"
        # Inspect a generous window around the declaration.
        window_start = compose_src.rfind("typer.Option", 0, idx)
        assert window_start != -1
        window_end = compose_src.find(")", idx)
        assert window_end != -1
        window = compose_src[window_start:window_end]
        assert "envvar" not in window, (
            "Typer envvar= on --dangerously-send would be a safety-model bypass"
        )

    def test_no_config_file_reader_for_send_defaults(self):
        """No config-file loader that could set send defaults."""
        # Search for config-file reading imports/usage in the src tree.
        forbidden_modules = (
            "configparser",
            "tomllib",
            "yaml.safe_load",
            "yaml.load",
        )
        for path, text in self._read_all_py_sources().items():
            for mod in forbidden_modules:
                assert mod not in text, (
                    f"Forbidden config-loader {mod!r} found in {path}: "
                    "this could be a safety-model bypass."
                )
        # json.load is commonly used for non-config purposes, so we only
        # flag patterns that look like config-path loading around a
        # dangerously_send reference.
        for path, text in self._read_all_py_sources().items():
            if "dangerously_send" not in text:
                continue
            # In the file that references dangerously_send, json.load
            # must NOT appear.
            assert "json.load" not in text, (
                f"{path}: json.load near dangerously_send logic is suspicious"
            )

    def test_only_cli_flag_sets_dangerously_send(self):
        """The only place dangerously_send is bound is the Typer option."""
        compose_src = (SRC_DIR / "mailctl" / "commands" / "compose.py").read_text()
        # The parameter name `dangerously_send` must appear as a Typer
        # parameter and as function parameters, but never be assigned
        # from an external source (env, file, alias).
        assert "dangerously_send" in compose_src
        # Check: no line in compose.py reads `dangerously_send =` from
        # an external source (os.environ, getattr(config, ...)) — only
        # function parameter bindings or flow-through calls are allowed.
        for line in compose_src.splitlines():
            if "dangerously_send" not in line:
                continue
            # Allow parameter list, function arg, and the Typer option line.
            # Disallow assignments from outside sources.
            if "os.environ" in line or "getenv" in line:
                pytest.fail(
                    f"Line binds dangerously_send from env: {line.strip()}"
                )


# --------------------------------------------------------------------------- #
# C-120: parametrised bypass-attempt matrix
# --------------------------------------------------------------------------- #


# Each scenario describes a compose invocation that should NOT send.
_NO_SEND_SCENARIOS: list[tuple[str, list[str], dict]] = [
    # (label, argv, extra-kwargs-for-CliRunner.invoke)
    (
        "bare",
        ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        {},
    ),
    (
        "yes-only",
        ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B", "--yes"],
        {},
    ),
    (
        "dry-run",
        ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B", "--dry-run"],
        {},
    ),
    (
        "all-recipient-types",
        [
            "compose",
            "--to", "a@x.com",
            "--cc", "c@x.com",
            "--bcc", "b@x.com",
            "--subject", "S",
            "--body", "B",
        ],
        {},
    ),
    (
        "attach",
        # attachment path is parametrised in the test function below.
        ["compose", "--to", "a@x.com", "--subject", "S", "--body", "B"],
        {"_attach": True},
    ),
    (
        "stdin-body",
        ["compose", "--to", "a@x.com", "--subject", "S"],
        {"input": "piped body"},
    ),
    (
        "from-account",
        [
            "compose",
            "--to", "a@x.com",
            "--subject", "S",
            "--body", "B",
            "--from", "Work",
        ],
        {"_accounts": True},
    ),
]


@pytest.mark.parametrize(
    "label,argv,extras",
    _NO_SEND_SCENARIOS,
    ids=[s[0] for s in _NO_SEND_SCENARIOS],
)
def test_safety_model_no_send_verb(mock_osascript, tmp_path, label, argv, extras):
    """C-120: across a matrix of bypass attempts, 'send' never appears."""
    argv = list(argv)
    invoke_kwargs = {k: v for k, v in extras.items() if not k.startswith("_")}

    if extras.get("_attach"):
        p = tmp_path / "file.txt"
        p.write_text("x")
        argv.extend(["--attach", str(p)])

    if extras.get("_accounts"):
        # Two sequential osascript calls: account lookup, then compose.
        mock_osascript.set_outputs([ACCOUNTS_OUTPUT, DRAFT_ID_OUTPUT])
    else:
        mock_osascript.set_output(DRAFT_ID_OUTPUT)

    result = runner.invoke(_click_app, argv, **invoke_kwargs)
    assert result.exit_code == 0, (
        f"[{label}] unexpected exit {result.exit_code}: {result.output}"
    )

    for call in mock_osascript.calls:
        script = call[2] if len(call) >= 3 else ""
        lowered = script.lower()
        # Literal send constructs from AppleScript vocabulary.
        assert "send newmessage" not in lowered, (
            f"[{label}] generated script contained 'send newMessage':\n{script}"
        )
        assert "send outgoing message" not in lowered, (
            f"[{label}] generated script contained 'send outgoing message':\n{script}"
        )
        assert "send msg" not in lowered, (
            f"[{label}] generated script contained 'send msg':\n{script}"
        )


def test_safety_model_send_only_with_dangerously_send(mock_osascript):
    """C-120 complement: with --dangerously-send --yes, 'send' DOES appear."""
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
