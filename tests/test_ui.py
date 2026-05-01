"""Tests for the ui module."""

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, patch

from sqa_agent.findings import Finding
from sqa_agent.ui import (
    DEFAULT_RESOLVE_HINT,
    InteractiveInput,
    choose_menu,
    confirm,
    display_finding,
    prompt_concurrency,
    prompt_interactive_input,
    prompt_resolve_hint,
    prompt_triage,
)


class TestChooseMenu:
    def test_returns_correct_choice(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=2):
            result = choose_menu("Pick one", ["Alpha", "Beta", "Gamma"])
        assert result == "Beta"

    def test_reprompts_on_out_of_range(self):
        with patch("sqa_agent.ui.IntPrompt.ask", side_effect=[0, 5, 2]):
            result = choose_menu("Pick one", ["Alpha", "Beta", "Gamma"])
        assert result == "Beta"


class TestChooseMenuWithDefault:
    def test_explicit_choice(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=1):
            result = choose_menu("Pick one", ["Alpha", "Beta", "Gamma"], 2)
        assert result == "Alpha"

    def test_enter_selects_default(self):
        # When the user presses Enter, IntPrompt.ask returns the default value
        # (default_index + 1). With default_index=2, the default kwarg is 3.
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=3):
            result = choose_menu("Pick one", ["Alpha", "Beta", "Gamma"], 2)
        assert result == "Gamma"


class TestPromptTriage:
    def _make_finding(self, **overrides):
        defaults = dict(
            id=1,
            source="ruff",
            message="Unused import",
            file="src/foo.py",
            line=42,
            severity="warning",
        )
        defaults.update(overrides)
        return Finding(**defaults)  # pyrefly: ignore[bad-argument-type]

    def test_auto(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="a"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "auto"

    def test_interactive(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="n"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "interactive"

    def test_ignore(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="g"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "ignore"

    def test_skip_returns_none(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="s"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result is None

    def test_quit(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="q"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "quit"

    def test_forward(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="f"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "forward"

    def test_back(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="b"):
            result = prompt_triage(self._make_finding(), 1, 10)
        assert result == "back"

    def test_shows_current_triage_status(self):
        """When a finding is already triaged, prompt_triage displays its status."""
        f = self._make_finding(triage="auto")
        buf = StringIO()
        with (
            patch("sqa_agent.ui.PromptBase.ask", return_value="s"),
            patch("sqa_agent.ui.console") as mock_console,
        ):
            mock_console.print = lambda *a, **kw: buf.write(str(a[0]) + "\n")
            prompt_triage(f, 1, 10)
        output = buf.getvalue()
        assert "[current: auto]" in output


class TestPromptResolveHint:
    def test_custom_hint(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value="Remove the import."):
            result = prompt_resolve_hint()
        assert result == "Remove the import."

    def test_empty_returns_default(self):
        with patch("sqa_agent.ui.PromptBase.ask", return_value=""):
            result = prompt_resolve_hint()
        assert result == DEFAULT_RESOLVE_HINT


class TestDisplayFinding:
    def _make_finding(self, **overrides):
        defaults = dict(
            id=1,
            source="ruff",
            message="Unused import",
            file="src/foo.py",
            line=42,
            severity="warning",
        )
        defaults.update(overrides)
        return Finding(**defaults)  # pyrefly: ignore[bad-argument-type]

    def test_shows_location_and_message(self):
        f = self._make_finding()
        buf = StringIO()
        with patch("sqa_agent.ui.console") as mock_console:
            mock_console.print = lambda *a, **kw: buf.write(str(a[0]) + "\n")
            display_finding(f, 1, 10)
        output = buf.getvalue()
        assert "src/foo.py:42" in output
        assert "Unused import" in output

    def test_handles_missing_line(self):
        f = self._make_finding(line=None)
        buf = StringIO()
        with patch("sqa_agent.ui.console") as mock_console:
            mock_console.print = lambda *a, **kw: buf.write(str(a[0]) + "\n")
            display_finding(f, 1, 10)
        output = buf.getvalue()
        assert "src/foo.py" in output
        assert ":None" not in output


class TestPromptInteractiveInput:
    def test_skip(self):
        with patch(
            "sqa_agent.ui._multiline_prompt",
            new_callable=AsyncMock,
            return_value="/skip",
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "command", "skip"
            )

    def test_resolve(self):
        with patch(
            "sqa_agent.ui._multiline_prompt",
            new_callable=AsyncMock,
            return_value="/resolve",
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "command", "resolve"
            )

    def test_quit(self):
        with patch(
            "sqa_agent.ui._multiline_prompt",
            new_callable=AsyncMock,
            return_value="/quit",
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "command", "quit"
            )

    def test_help_returns_reprompt(self):
        with patch(
            "sqa_agent.ui._multiline_prompt",
            new_callable=AsyncMock,
            return_value="/help",
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "reprompt"
            )

    def test_empty_returns_reprompt(self):
        with patch(
            "sqa_agent.ui._multiline_prompt", new_callable=AsyncMock, return_value=""
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "reprompt"
            )

    def test_text_returned(self):
        with patch(
            "sqa_agent.ui._multiline_prompt",
            new_callable=AsyncMock,
            return_value="Fix the bug please",
        ):
            assert asyncio.run(prompt_interactive_input()) == InteractiveInput(
                "text", "Fix the bug please"
            )


class TestPromptConcurrency:
    def test_returns_user_input_in_range(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=3):
            assert prompt_concurrency(5) == 3

    def test_clamps_to_one_if_zero(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=0):
            assert prompt_concurrency(5) == 1

    def test_clamps_to_one_if_negative(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=-2):
            assert prompt_concurrency(5) == 1

    def test_clamps_to_total_files_if_too_large(self):
        with patch("sqa_agent.ui.IntPrompt.ask", return_value=100):
            assert prompt_concurrency(5) == 5


class TestConfirm:
    def test_returns_true(self):
        with patch("sqa_agent.ui.Confirm.ask", return_value=True):
            assert confirm("Continue?") is True

    def test_returns_false(self):
        with patch("sqa_agent.ui.Confirm.ask", return_value=False):
            assert confirm("Continue?") is False
