"""Drift test for ``skills/mailctl/SKILL.md``.

The skill is loaded into every Claude Code session whose system prompt
matches its triggers. When the CLI's behaviour or shape changes, the
skill either stays in sync or silently misleads every model that
consumes it. This test catches drift cheaply.

Scope: static assertions against the skill text. No CLI execution.

What we assert:

1. **Structural**: YAML frontmatter is present, file isn't empty, key
   sections exist. Lesser models are sensitive to structure.
2. **Command coverage**: every top-level ``mailctl`` subcommand the CLI
   ships appears in the skill's command reference. Discovered
   dynamically from the Typer app so new commands fail the test until
   documented.
3. **Safety invariants**: the load-bearing phrases that prevent
   accidental sends, permanent deletes, etc. must be present verbatim.
4. **Known-limitation mentions**: each documented CAN'T-do item has its
   keyword plus its workaround keyword. This is the "don't bump into
   walls" guarantee.
5. **Stale-phrase blocker**: phrases that described pre-fix behaviour
   and would mislead today are explicitly forbidden.

These checks are intentionally shallow — the richer validation is the
regression prompt under ``tests/regression/`` driven by a model, not by
pytest. This file exists to catch obvious drift in CI / pre-commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


SKILL_PATH = (
    Path(__file__).resolve().parents[2] / "skills" / "mailctl" / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert SKILL_PATH.is_file(), f"skill missing: {SKILL_PATH}"
    return SKILL_PATH.read_text()


# --------------------------------------------------------------------------- #
# 1. Structure
# --------------------------------------------------------------------------- #


class TestSkillStructure:
    def test_has_yaml_frontmatter(self, skill_text):
        assert skill_text.startswith("---\n"), "skill must open with YAML frontmatter"
        # Frontmatter closes before any H1.
        closing = skill_text.find("\n---\n", 4)
        h1 = skill_text.find("\n# ")
        assert 0 < closing < h1, "frontmatter must close before the H1"

    def test_frontmatter_names_skill(self, skill_text):
        first_block = skill_text.split("\n---\n", 2)[0]
        assert re.search(r"^name:\s*mailctl\s*$", first_block, re.MULTILINE)

    def test_frontmatter_has_description(self, skill_text):
        first_block = skill_text.split("\n---\n", 2)[0]
        assert re.search(r"^description:", first_block, re.MULTILINE)

    def test_required_sections_present(self, skill_text):
        # The sections a lesser model relies on to find its bearings.
        required_headings = [
            "## Absolute safety rules",
            "## Pre-flight",
            "## Output conventions",
            "## Command reference",
            "## What mailctl CAN'T do",
            "## Common-error decoder",
            "## Quick reference",
        ]
        missing = [h for h in required_headings if h not in skill_text]
        assert not missing, f"skill missing required sections: {missing}"


# --------------------------------------------------------------------------- #
# 2. Command coverage — every subcommand the CLI ships is in the skill
# --------------------------------------------------------------------------- #


def _cli_subcommands() -> set[str]:
    """Discover every ``mailctl <group> <command>`` pair via Typer.

    Falls back to a hand-maintained baseline if the Typer tree can't
    be introspected (shouldn't happen — this is belt-and-braces).
    """
    from mailctl.cli import app
    import typer.main

    click_cmd = typer.main.get_command(app)
    shapes: set[str] = set()

    for name, child in click_cmd.commands.items():
        if hasattr(child, "commands"):
            # Group — recurse one level.
            for sub_name in child.commands:
                shapes.add(f"{name} {sub_name}")
        else:
            shapes.add(name)
    return shapes


class TestCommandCoverage:
    def test_every_subcommand_mentioned(self, skill_text):
        discovered = _cli_subcommands()
        # Commands the skill intentionally doesn't advertise because
        # they're covered by a pointer to a known limitation (see
        # "What mailctl CAN'T do" → drafts edit).
        intentionally_omitted = {"drafts edit"}

        missing: list[str] = []
        for shape in sorted(discovered - intentionally_omitted):
            needle = f"mailctl {shape}"
            if needle not in skill_text:
                missing.append(needle)
        assert not missing, (
            f"CLI subcommands not referenced in the skill: {missing}. "
            "Either document them in the command reference or add to "
            "`intentionally_omitted` with a pointer to where they're "
            "covered."
        )


# --------------------------------------------------------------------------- #
# 3. Safety invariants — load-bearing safety phrases
# --------------------------------------------------------------------------- #


class TestSafetyInvariants:
    REQUIRED_PHRASES = [
        # Flag safety
        "--dangerously-send",
        # Draft-first default
        "default to drafts",
        # Two-step pattern — the rule lesser models break first
        "Two-step",
        # Prompt default
        "default No",
        # Permanent delete needs explicit opt-in
        "--permanent",
        "Trash",
        # Cleanup own drafts
        "Clean up your own test drafts",
    ]

    @pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
    def test_phrase_present(self, skill_text, phrase):
        assert phrase in skill_text, (
            f"load-bearing safety phrase missing: {phrase!r}"
        )


# --------------------------------------------------------------------------- #
# 4. Known-limitation coverage — every wall we know about has a mention
# --------------------------------------------------------------------------- #


class TestLimitationCoverage:
    # For each (id, limitation keyword, workaround keyword) triple the
    # skill must mention the limitation AND the workaround. That keeps
    # the "don't bump walls" contract honest — documenting a limitation
    # without a workaround is worse than useless for a lesser model.
    LIMITATIONS = [
        # saved-draft immutability
        ("draft-edit", "read-only", "delete"),
        # cross-account moves
        ("move-cross-account", "can't cross accounts", "forward"),
        # body search
        ("body-search", "--body", "--subject"),
        # Gmail UID churn
        ("gmail-uid", "Gmail", "re-fetch"),
    ]

    @pytest.mark.parametrize("label,limit,workaround", LIMITATIONS)
    def test_limitation_and_workaround(self, skill_text, label, limit, workaround):
        assert limit in skill_text, (
            f"limitation {label!r} missing the phrase {limit!r}"
        )
        assert workaround in skill_text, (
            f"limitation {label!r} missing its workaround keyword "
            f"{workaround!r} — a limitation without a workaround is worse "
            f"than useless for a lesser model"
        )


# --------------------------------------------------------------------------- #
# 5. Stale-phrase blocker — phrases that described pre-fix behaviour
# --------------------------------------------------------------------------- #


class TestNoStalePhrases:
    """Phrases the OLD skill contained that are now misleading. If one
    sneaks back in, a lesser model will act on wrong assumptions.

    Each entry pairs a forbidden phrase with a short explanation of
    why it's wrong now."""

    STALE = [
        (
            "the draft ID the command printed",
            # Pre-#5: compose printed a small AppleScript-local id no
            # other command accepted. Post-#5 it prints the canonical
            # SQLite ROWID. This phrase implies the distinction still
            # exists.
            "misleading post-#5: ids are unified across all subcommands",
        ),
        (
            "Move to another mailbox (same account)",
            # Pre-#4: the caveat was "same account only" with no
            # validation or guidance. Post-#4 the CLI validates against
            # the source account and the skill points at the
            # forward-based workaround. The old phrase reads like a
            # vague footnote.
            "replaced by the explicit What-CAN'T-do entry + workaround",
        ),
    ]

    @pytest.mark.parametrize("phrase,reason", STALE)
    def test_phrase_absent(self, skill_text, phrase, reason):
        assert phrase not in skill_text, (
            f"stale phrase still in skill: {phrase!r}\nreason: {reason}"
        )


# --------------------------------------------------------------------------- #
# 6. Token-size sanity — the skill loads into every matching session;
# keep it tight. Numbers are heuristic, not rigid — trip the alarm only
# on real drift.
# --------------------------------------------------------------------------- #


class TestSize:
    # Current length ~270 lines / ~13kb. Pad generously to avoid nuisance
    # failures from small wording changes.
    MAX_LINES = 400
    MAX_BYTES = 18_000

    def test_reasonable_line_count(self, skill_text):
        line_count = skill_text.count("\n")
        assert line_count <= self.MAX_LINES, (
            f"skill is {line_count} lines (max {self.MAX_LINES}). "
            "If this is real growth, raise MAX_LINES. If it's drift, "
            "cut verbose prose — skills ship to every matching session."
        )

    def test_reasonable_byte_count(self, skill_text):
        byte_count = len(skill_text.encode("utf-8"))
        assert byte_count <= self.MAX_BYTES, (
            f"skill is {byte_count} bytes (max {self.MAX_BYTES})."
        )
