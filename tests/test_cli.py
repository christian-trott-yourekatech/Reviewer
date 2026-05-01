"""Tests for CLI commands."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from sqa_agent.cli import (
    _next_untriaged,
    _select_result_file,
    _summarize_result_files,
    _update_resolved_hashes,
    build_parser,
    cmd_auto_resolve,
    cmd_interactive_resolve,
    cmd_reset,
    cmd_triage,
)


def test_cmd_triage_no_sqa_dir(tmp_path, monkeypatch):
    """cmd_triage returns 1 when .sqa-agent/ doesn't exist."""
    monkeypatch.chdir(tmp_path)
    assert cmd_triage() == 1


def test_verbose_flag_defaults_false():
    parser = build_parser()
    args = parser.parse_args(["review"])
    assert args.verbose is False


def test_verbose_flag_short():
    parser = build_parser()
    args = parser.parse_args(["-v", "review"])
    assert args.verbose is True


def test_verbose_flag_long():
    parser = build_parser()
    args = parser.parse_args(["--verbose", "review"])
    assert args.verbose is True


class TestSelectResultFile:
    """Tests for _select_result_file."""

    def test_returns_none_when_no_files(self, tmp_path):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        result = _select_result_file(sqa_dir)
        assert result is None

    def test_returns_chosen_path(self, tmp_path, monkeypatch):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        # Create two result files with open findings.
        finding_json = json.dumps(
            {
                "version": 1,
                "timestamp": "2025_01_01_100000",
                "total": 1,
                "findings": [
                    {"id": 1, "source": "ruff", "message": "msg", "status": "open"},
                ],
            }
        )
        for name in ("result_2025_01_01_100000.json", "result_2025_01_02_100000.json"):
            (sqa_dir / name).write_text(finding_json)

        monkeypatch.setattr(
            "sqa_agent.ui.choose_menu",
            lambda title, labels, default, **kw: labels[0],
        )
        result = _select_result_file(sqa_dir)
        assert result is not None
        assert result.name == "result_2025_01_01_100000.json"


def test_cmd_auto_resolve_no_sqa_dir(tmp_path, monkeypatch):
    """cmd_auto_resolve returns 1 when .sqa-agent/ doesn't exist."""
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(cmd_auto_resolve()) == 1


def test_auto_resolve_subcommand_exists():
    """Parser accepts the auto-resolve subcommand."""
    parser = build_parser()
    args = parser.parse_args(["auto-resolve"])
    assert args.command == "auto-resolve"


def test_auto_resolve_no_auto_findings(tmp_path, monkeypatch):
    """cmd_auto_resolve returns 0 when no auto-triaged findings exist."""
    monkeypatch.chdir(tmp_path)
    sqa_dir = tmp_path / ".sqa-agent"
    sqa_dir.mkdir()

    # Create a config.toml.
    (sqa_dir / "config.toml").write_text("")

    # Create a result file with no auto-triaged findings.
    result = {
        "version": 1,
        "timestamp": "2025_01_01_120000",
        "total": 1,
        "findings": [
            {
                "id": 1,
                "source": "ruff",
                "message": "Unused import",
                "file": "src/foo.py",
                "line": 10,
                "severity": "warning",
                "code": "F401",
                "status": "open",
                "triage": "ignore",
            }
        ],
    }
    result_path = sqa_dir / "result_2025_01_01_120000.json"
    result_path.write_text(json.dumps(result))

    monkeypatch.setattr(
        "sqa_agent.ui.choose_menu",
        lambda title, labels, default, **kw: labels[0],
    )

    assert asyncio.run(cmd_auto_resolve()) == 0


def test_interactive_resolve_subcommand_exists():
    """Parser accepts the interactive-resolve subcommand."""
    parser = build_parser()
    args = parser.parse_args(["interactive-resolve"])
    assert args.command == "interactive-resolve"


def test_cmd_interactive_resolve_no_sqa_dir(tmp_path, monkeypatch):
    """cmd_interactive_resolve returns 1 when .sqa-agent/ doesn't exist."""
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(cmd_interactive_resolve()) == 1


def test_reset_subcommand_exists():
    """Parser accepts the reset subcommand."""
    parser = build_parser()
    args = parser.parse_args(["reset"])
    assert args.command == "reset"


def test_cmd_reset_no_sqa_dir(tmp_path, monkeypatch):
    """cmd_reset returns 1 when .sqa-agent/ doesn't exist."""
    monkeypatch.chdir(tmp_path)
    assert cmd_reset() == 1


def test_cmd_reset_updates_file_status(tmp_path, monkeypatch):
    """cmd_reset writes current hashes for all in-scope files."""
    monkeypatch.chdir(tmp_path)

    # Set up a git repo with a tracked file.
    from git import Repo as GitRepo

    repo = GitRepo.init(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    hello = src_dir / "hello.py"
    hello.write_text("print('hello')\n")
    repo.index.add([str(hello)])
    repo.index.commit("initial")

    # Set up .sqa-agent with a config that includes src/**/*.py.
    sqa_dir = tmp_path / ".sqa-agent"
    sqa_dir.mkdir()
    (sqa_dir / "config.toml").write_text('[files]\ninclude = ["src/**/*.py"]\n')
    (sqa_dir / "file_status.json").write_text("{}\n")

    assert cmd_reset() == 0

    status = json.loads((sqa_dir / "file_status.json").read_text())
    assert "src/hello.py" in status
    # The hash should be a 40-char hex string (git blob hash).
    assert len(status["src/hello.py"]) == 40


def test_interactive_resolve_no_interactive_findings(tmp_path, monkeypatch):
    """cmd_interactive_resolve returns 0 when no interactive-triaged findings exist."""
    monkeypatch.chdir(tmp_path)
    sqa_dir = tmp_path / ".sqa-agent"
    sqa_dir.mkdir()

    # Create a config.toml.
    (sqa_dir / "config.toml").write_text("")

    # Create a result file with no interactive-triaged findings.
    result = {
        "version": 1,
        "timestamp": "2025_01_01_120000",
        "total": 1,
        "findings": [
            {
                "id": 1,
                "source": "ruff",
                "message": "Unused import",
                "file": "src/foo.py",
                "line": 10,
                "severity": "warning",
                "code": "F401",
                "status": "open",
                "triage": "auto",
            }
        ],
    }
    result_path = sqa_dir / "result_2025_01_01_120000.json"
    result_path.write_text(json.dumps(result))

    monkeypatch.setattr(
        "sqa_agent.ui.choose_menu",
        lambda title, labels, default, **kw: labels[0],
    )

    assert asyncio.run(cmd_interactive_resolve()) == 0


class TestNextUntriaged:
    """Tests for _next_untriaged helper."""

    def _finding(self, id: int, triage=None):
        from sqa_agent.findings import Finding

        return Finding(id=id, source="ruff", message="msg", triage=triage)

    def test_finds_next_untriaged(self):
        findings = [
            self._finding(1, "auto"),
            self._finding(2),
            self._finding(3, "ignore"),
        ]
        assert _next_untriaged(findings, 0) == 1

    def test_returns_none_when_all_triaged(self):
        findings = [self._finding(1, "auto"), self._finding(2, "ignore")]
        assert _next_untriaged(findings, 0) is None

    def test_skips_current_position(self):
        findings = [self._finding(1), self._finding(2), self._finding(3)]
        # Starting after index 0, should find index 1
        assert _next_untriaged(findings, 0) == 1


def test_cmd_triage_navigation(tmp_path, monkeypatch):
    """cmd_triage supports f/b navigation to revisit previously triaged findings."""
    monkeypatch.chdir(tmp_path)
    sqa_dir = tmp_path / ".sqa-agent"
    sqa_dir.mkdir()
    (sqa_dir / "config.toml").write_text("")

    # Three findings: first two untriaged, third already triaged as ignore.
    result = {
        "version": 1,
        "timestamp": "2025_01_01_120000",
        "total": 3,
        "findings": [
            {
                "id": 1,
                "source": "ruff",
                "message": "Finding 1",
                "file": "a.py",
                "line": 1,
                "severity": "warning",
                "code": "F401",
                "status": "open",
                "triage": None,
            },
            {
                "id": 2,
                "source": "ruff",
                "message": "Finding 2",
                "file": "b.py",
                "line": 2,
                "severity": "warning",
                "code": "F401",
                "status": "open",
                "triage": None,
            },
            {
                "id": 3,
                "source": "ruff",
                "message": "Finding 3",
                "file": "c.py",
                "line": 3,
                "severity": "warning",
                "code": "F401",
                "status": "open",
                "triage": "ignore",
            },
        ],
    }
    result_path = sqa_dir / "result_2025_01_01_120000.json"
    result_path.write_text(json.dumps(result))

    monkeypatch.setattr(
        "sqa_agent.ui.choose_menu",
        lambda title, labels, default, **kw: labels[0],
    )

    # Simulate user actions:
    # 1. Finding 1 (untriaged): triage as "ignore" → auto-advance to finding 2
    # 2. Finding 2 (untriaged): press "b" to go back to finding 1
    # 3. Finding 1 (now triaged ignore): re-triage as "auto" (needs resolve hint)
    #    → auto-advance to finding 2
    # 4. Finding 2 (untriaged): triage as "ignore" → all triaged, loop ends
    triage_responses = iter(["g", "b", "a", "g"])
    monkeypatch.setattr(
        "sqa_agent.ui.PromptBase.ask",
        lambda *a, **kw: next(triage_responses),
    )

    # When finding 1 is re-triaged as "auto", prompt_resolve_hint is called.
    monkeypatch.setattr(
        "sqa_agent.ui.prompt_resolve_hint",
        lambda: "Fix it",
    )

    assert cmd_triage() == 0

    # Verify: finding 1 was re-triaged to auto with hint, finding 2 to ignore.
    from sqa_agent.findings import load_result

    saved = load_result(result_path)
    assert saved[0].triage == "auto"
    assert saved[0].resolve_hint == "Fix it"
    assert saved[1].triage == "ignore"
    assert saved[2].triage == "ignore"  # unchanged


class TestUpdateResolvedHashes:
    """Tests for _update_resolved_hashes."""

    def _finding(self, file: str, status: "Literal['open', 'resolved']" = "open"):
        from sqa_agent.findings import Finding

        return Finding(id=1, source="agent", message="msg", file=file, status=status)

    def test_changed_file_no_open_findings_updates_hash(self, tmp_path):
        """A file changed during resolution with no remaining open findings gets updated."""
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        (sqa_dir / "file_status.json").write_text("{}\n")

        findings = [self._finding("src/foo.py", status="resolved")]
        before = {"src/foo.py": "aaa", "src/bar.py": "bbb"}
        after = {"src/foo.py": "aaa_new", "src/bar.py": "bbb"}
        file_status: dict[str, str] = {"src/foo.py": "aaa", "src/bar.py": "bbb"}

        _update_resolved_hashes(sqa_dir, findings, before, after, file_status)

        assert file_status["src/foo.py"] == "aaa_new"
        assert file_status["src/bar.py"] == "bbb"  # unchanged

        # Verify it was persisted to disk.
        on_disk = json.loads((sqa_dir / "file_status.json").read_text())
        assert on_disk["src/foo.py"] == "aaa_new"

    def test_changed_file_with_open_findings_not_updated(self, tmp_path):
        """A file that still has open findings should NOT have its hash updated."""
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        (sqa_dir / "file_status.json").write_text("{}\n")

        findings = [self._finding("src/foo.py", status="open")]
        before = {"src/foo.py": "aaa"}
        after = {"src/foo.py": "aaa_new"}
        file_status: dict[str, str] = {"src/foo.py": "aaa"}

        _update_resolved_hashes(sqa_dir, findings, before, after, file_status)

        assert file_status["src/foo.py"] == "aaa"  # unchanged

    def test_no_changes_is_noop(self, tmp_path):
        """When no files changed, file_status stays the same and nothing is written."""
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        original = '{"src/foo.py": "aaa"}\n'
        (sqa_dir / "file_status.json").write_text(original)

        findings = [self._finding("src/foo.py", status="resolved")]
        before = {"src/foo.py": "aaa"}
        after = {"src/foo.py": "aaa"}  # same hash
        file_status: dict[str, str] = {"src/foo.py": "aaa"}

        _update_resolved_hashes(sqa_dir, findings, before, after, file_status)

        assert file_status == {"src/foo.py": "aaa"}
        # File on disk should not have been rewritten (content unchanged).
        assert (sqa_dir / "file_status.json").read_text() == original


def _make_result_json(findings_data: list[dict]) -> str:
    """Helper to create a valid result JSON string."""
    return json.dumps(
        {
            "version": 1,
            "timestamp": "2025_01_01_120000",
            "total": len(findings_data),
            "findings": findings_data,
        }
    )


class TestSummarizeResultFiles:
    """Tests for _summarize_result_files."""

    def test_filters_out_files_with_no_findings(self, tmp_path):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()

        # File with all resolved findings — should be filtered out (no open).
        (sqa_dir / "result_2025_01_01_100000.json").write_text(
            _make_result_json(
                [
                    {"id": 1, "source": "ruff", "message": "msg", "status": "resolved"},
                ]
            )
        )

        # File with open findings — should be included.
        (sqa_dir / "result_2025_01_02_100000.json").write_text(
            _make_result_json(
                [
                    {"id": 1, "source": "ruff", "message": "msg", "status": "open"},
                ]
            )
        )

        # Empty file — should be filtered out.
        (sqa_dir / "result_2025_01_03_100000.json").write_text(_make_result_json([]))

        summaries = _summarize_result_files(sqa_dir)
        assert len(summaries) == 1
        names = [path.name for path, _ in summaries]
        assert "result_2025_01_02_100000.json" in names

    def test_labels_contain_correct_counts(self, tmp_path):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()

        (sqa_dir / "result_2025_01_01_100000.json").write_text(
            _make_result_json(
                [
                    {
                        "id": 1,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": "auto",
                    },
                    {
                        "id": 2,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": "auto",
                    },
                    {
                        "id": 3,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": "interactive",
                    },
                    {
                        "id": 4,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": "ignore",
                    },
                    {
                        "id": 5,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": None,
                    },
                    {
                        "id": 6,
                        "source": "r",
                        "message": "m",
                        "status": "open",
                        "triage": None,
                    },
                    # Resolved finding — should not be counted.
                    {
                        "id": 7,
                        "source": "r",
                        "message": "m",
                        "status": "resolved",
                        "triage": "auto",
                    },
                ]
            )
        )

        summaries = _summarize_result_files(sqa_dir)
        assert len(summaries) == 1
        _, label = summaries[0]
        assert "6 open, 1 resolved" in label
        assert "2a, 1n, 1g, 2?" in label

    def test_empty_dir_returns_empty(self, tmp_path):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()
        assert _summarize_result_files(sqa_dir) == []

    def test_select_result_file_returns_none_when_no_findings(self, tmp_path):
        sqa_dir = tmp_path / ".sqa-agent"
        sqa_dir.mkdir()

        (sqa_dir / "result_2025_01_01_100000.json").write_text(_make_result_json([]))

        result = _select_result_file(sqa_dir)
        assert result is None


class TestCommandRegistry:
    """The single declarative registry in cli.py replaces three previously-
    duplicated catalogues (argparse subparsers, menu options, sync/async
    dispatch dicts).  These tests pin the invariants the registry provides
    so a future refactor can't silently drop or desync them."""

    def test_every_command_has_unique_name(self):
        from sqa_agent.cli import _COMMANDS

        names = [c.name for c in _COMMANDS]
        assert len(names) == len(set(names)), (
            f"duplicate command names in registry: {names}"
        )

    def test_every_handler_is_callable(self):
        from sqa_agent.cli import _COMMANDS

        for c in _COMMANDS:
            assert callable(c.handler), f"{c.name}: handler is not callable"

    def test_parser_subcommands_match_registry(self):
        """build_parser adds exactly one subparser per registry entry."""
        from sqa_agent.cli import _COMMANDS, build_parser

        parser = build_parser()
        # Subparsers live in the ``_subparsers`` action; its ``choices``
        # dict exposes the registered names.
        subparsers_action = next(
            a
            for a in parser._actions
            if isinstance(a, __import__("argparse")._SubParsersAction)
        )
        registered = set(subparsers_action.choices.keys())
        expected = {c.name for c in _COMMANDS}
        assert registered == expected, (
            f"argparse subcommands out of sync with registry: "
            f"parser={registered}, registry={expected}"
        )

    def test_menu_entries_exclude_bootstrap_only_commands(self):
        """init is bootstrap-only and is excluded from the menu."""
        from sqa_agent.cli import _COMMANDS, _command_by_name

        init = _command_by_name("init")
        assert init is not None
        assert init.in_menu is False
        # Every other command IS in the menu — no accidental hides.
        for c in _COMMANDS:
            if c.name == "init":
                continue
            assert c.in_menu is True, f"{c.name!r} was unexpectedly hidden from menu"

    def test_async_commands_flagged_is_async(self):
        """Commands whose handler is a coroutine function have is_async=True
        (so main wraps them in asyncio.run) and vice versa."""
        import inspect

        from sqa_agent.cli import _COMMANDS

        for c in _COMMANDS:
            actually_async = inspect.iscoroutinefunction(c.handler)
            assert c.is_async == actually_async, (
                f"{c.name}: is_async={c.is_async} but handler "
                f"coroutine-function check is {actually_async}"
            )

    def test_command_by_name_lookup(self):
        from sqa_agent.cli import _command_by_name

        review = _command_by_name("review")
        assert review is not None
        assert review.name == "review"
        assert _command_by_name("no-such-command") is None
