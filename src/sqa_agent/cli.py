"""SQA Agent - AI-powered software quality assurance agent.

Uses the Claude Agent SDK to perform structured, automated code quality
analysis on software projects.
"""

import argparse
import asyncio
import functools
import json
import logging
import shlex
import shutil
import sys
import tomllib
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, Protocol, cast
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo


from sqa_agent.agent import (
    FatalAPIError,
    ResolveResult,
    ReviewStats,
    format_duration,
    interactive_resolve_findings,
    resolve_findings,
    review_file_queue,
    review_general,
)
from sqa_agent.config import (
    AgentConfig,
    Config,
    ConfigMigrationError,
    RunToolsConfig,
    ToolConfig,
    load_config,
    TOOL_CATEGORIES,
)
from sqa_agent.file_status import (
    compute_hashes,
    load_file_status,
    mark_reviewed,
    reconcile,
    resolve_candidate_files,
    save_file_status,
)
from sqa_agent.findings import (
    Finding,
    assign_ids,
    load_result,
    make_result_path,
    write_result,
)
from sqa_agent.prompts import load_general_prompts, load_file_prompts
from sqa_agent.tools import ToolExecutionError, run_formatters, run_tool

# Note: symbols from :mod:`sqa_agent.ui` (``console``, ``choose_menu``,
# ``confirm``, ``INTERACTIVE_HELP``, etc.) are intentionally imported
# lazily inside the functions that need them. ``sqa_agent.ui`` pulls in
# Rich (and, via prompt_toolkit, a non-trivial startup cost), so
# deferring the import keeps non-interactive code paths — e.g. tests that
# exercise pure parsing/validation — fast and free of Rich's side
# effects. Do not hoist these imports to module scope without a reason.

logger = logging.getLogger("sqa-agent")

SQA_DIR_NAME = ".sqa-agent"
# Bounds the upward pyproject.toml search to avoid wandering out of the
# project; 5 levels is deep enough for any reasonable source layout while
# keeping the walk fast.
_MAX_PARENT_SEARCH_DEPTH = 5

# POSIX convention: processes terminated by signal N exit with 128+N.
# SIGINT is signal 2, so Ctrl-C → 130.
EXIT_KEYBOARD_INTERRUPT = 130  # POSIX 128 + SIGINT

# Permission bits for the generated ./review launcher script.
# Owner-only rwx (``rwx------``). The launcher is a user-level convenience
# script: on a shared / multi-tenant host it shouldn't be world-readable
# (leaks install path) or world-executable.
_LAUNCHER_SCRIPT_MODE = 0o700

# Human-friendly labels for tool categories (keyed by the raw names used
# by ``TOOL_CATEGORIES``/``FileTypeTools``). Single source of truth so every
# UI surface — menus, health-check output, logs — spells them the same way.
_CATEGORY_LABELS: dict[str, str] = {
    "formatter": "Formatter",
    "linter": "Linter",
    "type_checker": "Type-checker",
    "test": "Test",
}

DEFAULT_CONFIG = f"""\
# SQA Agent configuration
# This file controls how the SQA agent analyzes your project.

# Files in scope for review (glob patterns).
# [files]
# include = ["src/**/*.py"]
# exclude = ["src/**/*_test.py"]

# Tool configuration per file type, keyed by extension.
# Each tool has a 'command' to run and a 'parser' for its output.
# Available parsers: ruff, ruff_format, mypy, pyrefly, eslint, tsc, pytest, raw
#
# [tools.py.formatter]
# command = "uv run ruff format ."
# parser = "ruff_format"
#
# [tools.py.linter]
# command = "uv run ruff check --output-format json ."
# parser = "ruff"
#
# [tools.py.type_checker]
# command = "uv run pyrefly check --output-format json ."
# parser = "pyrefly"
#
# # Alternative: mypy
# # command = "uv run mypy --output json src/"
# # parser = "mypy"
#
# [tools.py.test]
# command = "uv run pytest --tb=short"
# parser = "pytest"
#
# # --- TypeScript ---
# [tools.ts.linter]
# command = "npx eslint --format json ."
# parser = "eslint"
#
# [tools.ts.type_checker]
# command = "npx tsc --noEmit --pretty false"
# parser = "tsc"

# Agent configuration for AI-powered review.
# All fields below are optional; defaults shown.
# [agent]
# review_model  = "{AgentConfig.review_model}"
# resolve_model = "{AgentConfig.resolve_model}"
# thinking      = "{AgentConfig.thinking}"  # "adaptive" or "disabled"
# effort        = "{AgentConfig.effort}"  # low | medium | high | xhigh | max
#
# Note: Opus 4.7 and Sonnet 4.6 have a 1M-token context window built in,
# so there is no separate setting to enable it.
"""


def _find_sqa_project_root() -> Path | None:
    """Locate the sqa-agent project root by searching upward for pyproject.toml.

    Returns ``None`` when the package is installed as a wheel (no source tree).
    """
    current = Path(__file__).resolve().parent
    for _ in range(_MAX_PARENT_SEARCH_DEPTH):
        if (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_sqa_dir() -> Path:
    """Return the path to the .sqa-agent directory in the current working directory."""
    return Path.cwd() / SQA_DIR_NAME


def find_sqa_dir_or_log() -> Path | None:
    """Return the .sqa-agent directory, or log an error and return None.

    Named ``_or_log`` (rather than the older ``require_*``) to signal at
    the call site that the happy path returns ``Path`` and the sad path
    returns ``None`` after logging — neither branch raises.
    """
    sqa_dir = find_sqa_dir()
    if not sqa_dir.exists():
        logger.error(
            f"No '{SQA_DIR_NAME}/' directory found. Run 'sqa-agent init' first."
        )
        return None
    return sqa_dir


def _find_repo_or_log(project_root: Path) -> Repo | None:
    """Return a ``Repo`` for *project_root*, or log an error and return ``None``.

    Named ``_or_log`` (rather than ``_require_*``) because the sad path
    returns ``None`` after logging rather than raising — callers need to
    handle the ``None``.
    """
    try:
        return Repo(project_root, search_parent_directories=True)
    except InvalidGitRepositoryError:
        logger.error("No git repository found. SQA Agent requires a git repository.")
        return None


def _load_config_or_none(config_path: Path) -> Config | None:
    """Load and return the project config, or log an error and return ``None``.

    Catches TOML syntax errors and validation errors so callers get a
    friendly message instead of a raw traceback.  Deprecated-option
    migrations surface via :class:`ConfigMigrationError` and are printed
    verbatim (no "Invalid config" prefix) so the guidance is readable.
    """
    try:
        return load_config(config_path)
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Invalid config.toml syntax: {e}")
        return None
    except ConfigMigrationError as e:
        # Render the multi-line migration guidance verbatim.
        sys.stderr.write("\n" + e.user_message + "\n\n")
        return None
    except ValueError as e:
        logger.error(f"Invalid config.toml value: {e}")
        return None


def cmd_init() -> int:
    """Initialize a new .sqa-agent/ directory in the current working directory."""
    sqa_dir = find_sqa_dir()

    if sqa_dir.exists():
        logger.error(
            f"'{SQA_DIR_NAME}/' already exists in this directory. "
            f"This project has already been initialized.",
        )
        return 1

    # Roll back a half-completed init on failure so the user isn't left
    # with an inconsistent ``.sqa-agent/`` (e.g. mkdir succeeded but
    # copytree failed due to permissions). ``rmtree(..., ignore_errors=True)``
    # because the cleanup itself shouldn't mask the original exception.
    sqa_dir.mkdir()
    try:
        # Owner-only (``rwx------``): ``.sqa-agent/`` holds review state
        # and finding messages that quote source code, so it's not safe
        # to leave world-readable on a shared host. Done before writing
        # any files so they're created inside an already-private dir.
        sqa_dir.chmod(0o700)

        config_path = sqa_dir / "config.toml"
        config_path.write_text(DEFAULT_CONFIG)

        # Use the shared helper so both sites agree on the file format
        # (any future change — key sorting, indent, trailing newline —
        # lives in ``save_file_status`` alone).
        save_file_status(sqa_dir, {})

        # Copy default prompt templates into the project.
        bundled_prompts = Path(__file__).parent / "prompts"
        target_prompts = sqa_dir / "prompts"
        shutil.copytree(bundled_prompts, target_prompts)
    except OSError:
        shutil.rmtree(sqa_dir, ignore_errors=True)
        logger.error(
            f"Init failed partway through; removed partial {SQA_DIR_NAME}/. "
            "Please retry."
        )
        raise

    # Generate a `review` launcher script.  When running from a source
    # checkout we can use `uv run --project` so uv resolves sqa-agent's
    # own environment.  When installed as a wheel there is no project root,
    # so we fall back to invoking sqa-agent directly.
    review_script = Path.cwd() / "review"
    if not review_script.exists():
        sqa_project_dir = _find_sqa_project_root()
        if sqa_project_dir is not None:
            # ``shlex.quote`` protects against install paths containing
            # shell metacharacters (``"``, ``$``, backticks, ``\``), which
            # would otherwise let arbitrary code run every time the
            # generated launcher is invoked.
            quoted_dir = shlex.quote(str(sqa_project_dir))
            review_script.write_text(
                f'#!/bin/bash\nuv run --project {quoted_dir} sqa-agent "$@"\n'
            )
        else:
            review_script.write_text('#!/bin/bash\nsqa-agent "$@"\n')
        review_script.chmod(_LAUNCHER_SCRIPT_MODE)
        logger.info("Created ./review launcher script")
    else:
        # Leave an existing launcher in place — the user may have customized
        # it. Warn so they know to delete it manually if they want it
        # regenerated (e.g. after switching between wheel install and source
        # checkout, which produce different launcher contents).
        logger.info(
            "Kept existing ./review launcher; delete it and re-run init to regenerate."
        )

    logger.info(f"Initialized {SQA_DIR_NAME}/ in {Path.cwd()}")
    logger.info(
        f"Edit {config_path} to configure tools and file patterns before running a review."
    )
    return 0


def cmd_reset() -> int:
    """Reset file_status.json to current on-disk hashes for all in-scope files."""
    sqa_dir = find_sqa_dir_or_log()
    if sqa_dir is None:
        return 1

    config = _load_config_or_none(sqa_dir / "config.toml")
    if config is None:
        return 1
    project_root = Path.cwd()
    repo = _find_repo_or_log(project_root)
    if repo is None:
        return 1

    candidates = resolve_candidate_files(config, project_root, repo)
    current_hashes = compute_hashes(repo, project_root, candidates)
    save_file_status(sqa_dir, current_hashes)

    logger.info(f"Reset file status for {len(current_hashes)} file(s).")
    return 0


def cmd_commit() -> int:
    """Stage and commit all current changes to the current branch.

    Scope is ``git add -u`` (tracked files only) — the conservative
    counterpart to :func:`sqa_agent.git_ops.stage_and_commit`, which
    backs the ``/commit`` slash-command inside interactive-resolve and
    uses ``git add .`` so agent-created files get captured.  The two
    policies are deliberately different; see ``stage_and_commit``'s
    docstring for why.  Keep them in sync when touching either site.
    """
    repo = _find_repo_or_log(Path.cwd())
    if repo is None:
        return 1

    # Tracked-file dirtiness only, matching the staging policy below
    # (``git add -u``) and ``_commit_resolve_changes``. Untracked files
    # are intentionally not considered "something to commit" by this
    # command, so we don't nag the user when the only changes are
    # untracked artefacts we're going to ignore anyway.
    if not repo.is_dirty():
        logger.info("Nothing to commit — worktree is clean.")
        return 0

    from sqa_agent.ui import console, confirm, PromptBase

    console.print("\n[bold]Current changes:[/bold]")
    # ``markup=False`` so filenames that happen to contain Rich markup
    # (``[red]evil.txt`` etc.) are displayed literally and can't spoof
    # the status output.
    console.print(repo.git.status("--short"), markup=False)
    console.print()

    message = PromptBase.ask("Commit message", console=console).strip()
    if not message:
        # Non-zero so shell callers can distinguish an explicit user abort
        # from a successful commit or a clean-worktree no-op.
        logger.info("Empty commit message — aborted, nothing committed.")
        return 1

    # Stage tracked-file modifications only (``-u``), matching the
    # resolve-commit policy in ``_commit_resolve_changes``. This avoids
    # accidentally sweeping in untracked temp/build artefacts that a
    # user hasn't explicitly ``git add``-ed.
    try:
        repo.git.add("-u")

        # Show the exact staged set (not just the pre-stage status) and
        # reconfirm — after a resolve session that ran with
        # bypassPermissions, unexpected tracked files may have been
        # modified, and the user deserves a chance to see them before
        # the commit lands.
        staged = repo.git.diff("--name-status", "--staged")
        if not staged.strip():
            logger.info("Nothing staged after ``git add -u`` — nothing to commit.")
            return 0
        console.print("\n[bold]Staged for commit:[/bold]")
        # ``markup=False`` so filenames containing Rich markup syntax
        # are displayed literally.
        console.print(staged, markup=False)
        console.print()
        if not confirm("Commit these files?"):
            logger.info("Aborted by user — nothing committed.")
            return 1

        repo.index.commit(message)
    except GitCommandError as e:
        # Surface a friendly message instead of the raw GitPython traceback
        # for common failures: merge conflicts in the index, pre-commit hook
        # rejections, missing user.email/user.name, etc.
        logger.error(f"Commit failed: {e}")
        return 1
    logger.info(f"Committed: {message}")
    return 0


RESULT_LEGEND = "a=auto  n=interactive  g=ignore  ?=untriaged"


def _count_triage_buckets(
    findings: list[Finding],
) -> dict[str, int]:
    """Count findings by triage bucket.

    Returns a dict with keys ``"auto"``, ``"interactive"``, ``"ignore"``,
    and ``"untriaged"``.
    """
    counts: dict[str, int] = {"auto": 0, "interactive": 0, "ignore": 0, "untriaged": 0}
    for f in findings:
        if f.triage is None:
            counts["untriaged"] += 1
        else:
            # Pre-seeded with all valid triage keys; a bare ``+= 1`` will
            # raise ``KeyError`` for any unexpected value, surfacing schema
            # violations rather than silently absorbing them.
            counts[f.triage] += 1
    return counts


def _summarize_result_files(sqa_dir: Path) -> list[tuple[Path, str]]:
    """Scan result files and return ``(path, label)`` pairs for files with open findings.

    Each label contains the filename and a bracketed triage-bucket summary,
    e.g. ``result_2025_01_01_120000.json  [2a, 1n, 0g, 5?]``.
    Files with zero open findings are omitted.
    """
    result_files = sorted(sqa_dir.glob("result_*.json"))
    summaries: list[tuple[Path, str]] = []
    for path in result_files:
        # Skip corrupt/unreadable files so one bad file doesn't abort the
        # whole menu render; the user can still work on the remaining files.
        try:
            findings = load_result(path)
        except (json.JSONDecodeError, ValueError, KeyError, OSError) as exc:
            logger.warning(f"Skipping corrupt result file {path.name}: {exc}")
            continue
        open_findings = [f for f in findings if f.status == "open"]
        if not open_findings:
            continue
        resolved_count = sum(1 for f in findings if f.status == "resolved")
        counts = _count_triage_buckets(open_findings)
        status_tag = f"{len(open_findings)} open, {resolved_count} resolved"
        triage_tag = (
            f"{counts['auto']}a, {counts['interactive']}n, "
            f"{counts['ignore']}g, {counts['untriaged']}?"
        )
        label = f"{path.name}  [{status_tag} | {triage_tag}]"
        summaries.append((path, label))
    return summaries


def _select_result_file(sqa_dir: Path) -> Path | None:
    """Present a menu to choose a result file. Returns the path or None.

    When only one result file has open findings it is selected automatically.
    """
    summaries = _summarize_result_files(sqa_dir)
    if not summaries:
        logger.info("No result files with open findings. Run 'sqa-agent review' first.")
        return None

    if len(summaries) == 1:
        path, label = summaries[0]
        logger.info(f"Using {label}")
        return path

    from sqa_agent.ui import choose_menu

    labels = [label for _, label in summaries]
    path_by_label = {label: path for path, label in summaries}
    default_index = len(labels) - 1
    chosen_label = choose_menu(
        "Select a result file", labels, default_index, footer=RESULT_LEGEND
    )
    return path_by_label[chosen_label]


def _next_untriaged(findings: list[Finding], after: int) -> int | None:
    """Return the index of the next untriaged finding after *after*, or ``None``."""
    for i in range(after + 1, len(findings)):
        if findings[i].triage is None:
            return i
    return None


def cmd_triage() -> int:
    """Walk through findings and assign triage status to each one."""
    sqa_dir = find_sqa_dir_or_log()
    if sqa_dir is None:
        return 1

    chosen_path = _select_result_file(sqa_dir)
    if chosen_path is None:
        return 0

    from sqa_agent.ui import console, prompt_resolve_hint, prompt_triage, TRIAGE_LEGEND

    findings = load_result(chosen_path)
    triaged_count = sum(1 for f in findings if f.triage is not None)
    untriaged_count = len(findings) - triaged_count

    logger.info(
        f"{len(findings)} finding(s), "
        f"{triaged_count} already triaged, "
        f"{untriaged_count} to triage"
    )

    console.print()
    console.print(TRIAGE_LEGEND)

    # Start at the first untriaged finding, or 0 if all are triaged.
    cursor: int | None = _next_untriaged(findings, -1)
    if cursor is None:
        cursor = 0

    while 0 <= cursor < len(findings):
        finding = findings[cursor]
        action = prompt_triage(finding, finding.id, len(findings))

        if action == "quit":
            break
        if action == "forward":
            cursor = min(cursor + 1, len(findings) - 1)
            continue
        if action == "back":
            cursor = max(cursor - 1, 0)
            continue

        # a, n, g — triage decision (None means skip)
        was_untriaged = finding.triage is None
        if action is not None:
            finding.triage = action
            if action == "auto":
                finding.resolve_hint = prompt_resolve_hint()
                finding.status = "open"
            elif action == "interactive":
                finding.status = "open"
            elif action == "ignore":
                finding.status = "resolved"
            write_result(chosen_path, findings)

        # Auto-advance strategy: if the user was triaging a fresh
        # (previously-untriaged) finding, jump to the next untriaged one
        # — the expected linear walk-through behaviour. If instead the
        # user navigated back to re-triage an already-decided finding,
        # advance linearly so we don't silently kick them out of the
        # session when there are no untriaged findings ahead.
        if was_untriaged:
            next_idx = _next_untriaged(findings, cursor)
            if next_idx is None:
                break
            cursor = next_idx
        else:
            cursor = min(cursor + 1, len(findings) - 1)

    # Final summary.
    counts = _count_triage_buckets(findings)

    logger.info(
        f"Triage complete: "
        f"{counts['auto']} auto, "
        f"{counts['interactive']} interactive, "
        f"{counts['ignore']} ignore, "
        f"{counts['untriaged']} untriaged"
    )
    return 0


def _update_resolved_hashes(
    sqa_dir: Path,
    findings: list[Finding],
    before_hashes: dict[str, str],
    after_hashes: dict[str, str],
    file_status: dict[str, str],
) -> None:
    """Update file_status hashes for files changed during resolution.

    Only updates files that have no remaining open findings, so that
    the next ``sqa-agent review`` won't flag them as needing re-review.
    Called after each finding is resolved so that progress is saved
    incrementally (survives Ctrl-C).
    """
    changed = {p for p, h in after_hashes.items() if before_hashes.get(p) != h}
    if not changed:
        return

    files_with_open = {f.file for f in findings if f.file and f.status == "open"}
    eligible = changed - files_with_open

    if not eligible:
        return

    for path in eligible:
        file_status[path] = after_hashes[path]

    save_file_status(sqa_dir, file_status)
    logger.debug(f"Updated file_status hashes for {len(eligible)} file(s).")


def _commit_resolve_changes(repo: Repo, resolved_count: int, mode: str) -> None:
    """Stage tracked-file changes and commit after a resolve pass.

    *mode* is ``"auto"`` or ``"interactive"``, used in the commit message.
    Uses ``-u`` (tracked files only) to avoid committing untracked files
    that may have been created as side-effects (temp files, build artefacts).
    Mirrors the staging policy in :func:`cmd_commit`; keep the two in sync
    so manual and post-resolve commits behave the same way.
    """
    if not repo.is_dirty():
        logger.debug("No changes to commit after resolve.")
        return

    repo.git.add("-u")
    message = f"sqa-agent: {mode}-resolve {resolved_count} finding(s)"
    repo.index.commit(message)
    logger.info(f"Committed resolve changes: {message}")


class ResolveFn(Protocol):
    """Structural type for the resolve coroutines accepted by ``_run_resolve``.

    Matches the shape of :func:`resolve_findings` and
    :func:`interactive_resolve_findings` (the wrapper in
    :func:`cmd_interactive_resolve` also conforms). Declaring this
    explicitly lets static checkers catch kwarg mismatches at the call
    site instead of hiding them behind ``Callable[..., Any]``.
    """

    async def __call__(
        self,
        findings: list[Finding],
        agent_config: AgentConfig,
        project_root: Path,
        stats: ReviewStats,
        *,
        on_resolved: Callable[[Finding], None] | None = None,
        on_verify: Callable[[], list[Finding]] | None = None,
    ) -> ResolveResult: ...


async def _run_resolve(
    triage_kind: Literal["auto", "interactive"],
    resolve_fn: ResolveFn,
    extra_summary: Callable[[ResolveResult], list[str]],
    pre_run: Callable[[], None] | None = None,
) -> int:
    """Shared scaffolding for auto-resolve and interactive-resolve commands.

    *triage_kind* is the triage bucket to filter on (``"auto"`` or
    ``"interactive"``).  *resolve_fn* is the coroutine that actually
    performs the resolution.  *extra_summary* receives the resolve result
    and returns additional log lines for the summary block.
    *pre_run* is an optional hook called before the resolve loop starts
    (e.g. to display help text).
    """
    sqa_dir_or_none = find_sqa_dir_or_log()
    if sqa_dir_or_none is None:
        return 1
    # Rebind to a non-Optional local so the nested closures below
    # capture a concrete ``Path`` instead of ``Path | None`` — avoids
    # ``assert ... is not None`` type-narrowing hacks that get stripped
    # under ``python -O``.  Same trick is used for ``chosen_path`` below.
    sqa_dir: Path = sqa_dir_or_none

    chosen_path_or_none = _select_result_file(sqa_dir)
    if chosen_path_or_none is None:
        return 0
    chosen_path: Path = chosen_path_or_none

    config_or_none = _load_config_or_none(sqa_dir / "config.toml")
    if config_or_none is None:
        return 1
    config: Config = config_or_none
    project_root = Path.cwd()

    findings = load_result(chosen_path)
    open_findings = [
        f for f in findings if f.triage == triage_kind and f.status == "open"
    ]

    if not open_findings:
        logger.info(f"No open findings triaged as '{triage_kind}'. Nothing to resolve.")
        return 0

    logger.info(f"{len(open_findings)} finding(s) triaged as '{triage_kind}' and open.")

    if pre_run is not None:
        pre_run()

    stats = ReviewStats()

    repo_or_none = _find_repo_or_log(project_root)
    if repo_or_none is None:
        return 1
    repo: Repo = repo_or_none
    # Only hash files that actually carry open findings — these are the
    # paths ``_update_resolved_hashes`` will look up via
    # ``before_hashes.get()``.  Files the agent dirties as a side effect
    # of a fix aren't in this set, but ``.get()`` returning ``None`` still
    # correctly marks them as changed.  Scales O(len(open_findings))
    # instead of O(tracked files in repo), which matters on large repos;
    # behavior-equivalent for the small ones we currently target.
    candidate_files = sorted({f.file for f in open_findings if f.file})
    before_hashes = compute_hashes(repo, project_root, candidate_files)
    file_status = load_file_status(sqa_dir)

    def on_resolved(finding: Finding) -> None:
        write_result(chosen_path, findings)
        # Only recompute hashes for files the agent actually changed,
        # rather than all tracked files (avoids O(T) per finding).
        dirty = repo.git.diff("--name-only").splitlines()
        dirty += repo.git.diff("--name-only", "--staged").splitlines()
        dirty_unique = list(set(dirty))
        if dirty_unique:
            after_hashes = compute_hashes(repo, project_root, dirty_unique)
            _update_resolved_hashes(
                sqa_dir, findings, before_hashes, after_hashes, file_status
            )

    resolve_run_tools: RunToolsConfig | None = None
    if triage_kind == "auto":
        resolve_run_tools = config.resolve.auto
    elif triage_kind == "interactive":
        resolve_run_tools = config.resolve.interactive

    def on_verify() -> list[Finding]:
        return _run_deterministic_tools(config, run_tools=resolve_run_tools)

    # Enable verification only when tools are configured and at least one
    # tool category is enabled for this resolve mode.
    has_enabled_tools = resolve_run_tools is not None and any(
        getattr(resolve_run_tools, cat) for cat in TOOL_CATEGORIES
    )
    try:
        result = await resolve_fn(
            open_findings,
            config.agent,
            project_root,
            stats,
            on_resolved=on_resolved,
            on_verify=on_verify if config.tools and has_enabled_tools else None,
        )
    except FatalAPIError as e:
        logger.error(f"\n{e.user_message}")
        write_result(chosen_path, findings)
        resolved_count = sum(1 for f in findings if f.status == "resolved")
        if resolved_count > 0:
            _commit_resolve_changes(repo, resolved_count, mode=triage_kind)
        logger.info(
            "Progress saved. Re-run the command to continue where you left off."
        )
        return 1

    # Final save.
    write_result(chosen_path, findings)

    logger.info(f"--- {triage_kind.capitalize()}-resolve summary ---")
    logger.info(f"Resolved: {result.resolved}")
    for line in extra_summary(result):
        logger.info(line)
    logger.info(f"Agent prompts: {stats.total_prompts}")
    logger.info(f"Agent cost: ${stats.total_cost_usd:.4f}")
    logger.info(f"Agent time: {format_duration(stats.total_duration_secs)}")

    if result.resolved > 0:
        _commit_resolve_changes(repo, result.resolved, mode=triage_kind)

    return 0


def _failed_summary_line(r: ResolveResult) -> list[str]:
    """Format the "Failed (verify)" summary line, or empty list if none."""
    return [f"Failed (verify): {r.failed}"] if r.failed else []


async def cmd_auto_resolve() -> int:
    """Autonomously resolve findings triaged as 'auto'."""
    return await _run_resolve(
        triage_kind="auto",
        resolve_fn=resolve_findings,
        extra_summary=_failed_summary_line,
    )


async def cmd_interactive_resolve() -> int:
    """Interactively resolve findings triaged as 'interactive'."""

    def _show_help() -> None:
        from sqa_agent.ui import console, INTERACTIVE_HELP

        console.print()
        console.print(INTERACTIVE_HELP)
        console.print()

    sqa_dir = find_sqa_dir_or_log()
    if sqa_dir is None:
        return 1
    config = _load_config_or_none(sqa_dir / "config.toml")

    def on_format() -> None:
        if config is not None:
            run_formatters(config)

    return await _run_resolve(
        triage_kind="interactive",
        resolve_fn=functools.partial(interactive_resolve_findings, on_format=on_format),
        extra_summary=lambda r: [f"Skipped:  {r.skipped}"] + _failed_summary_line(r),
        pre_run=_show_help,
    )


def _log_config(config: Config) -> None:
    """Log the active configuration summary."""
    a = config.agent
    logger.info("--- Config ---")
    logger.info(f"  Review model:      {a.review_model}")
    logger.info(f"  Resolve model:     {a.resolve_model}")
    logger.info(f"  Thinking:          {a.thinking}")
    logger.info(f"  Effort:            {a.effort}")
    if config.include:
        logger.info(f"  Include:           {', '.join(config.include)}")
    if config.exclude:
        logger.info(f"  Exclude:           {', '.join(config.exclude)}")
    if config.tools:
        for ext, ft in config.tools.items():
            active = [c for c in TOOL_CATEGORIES if getattr(ft, c) is not None]
            logger.info(f"  Tools (.{ext}):      {', '.join(active)}")


def _iter_runnable_tools(
    config: Config,
    run_tools: RunToolsConfig | None = None,
) -> Iterator[tuple[str, str, ToolConfig]]:
    """Yield ``(ext, category, tool_config)`` for each enabled, configured tool.

    When *run_tools* is provided, categories whose flag is ``False`` are
    skipped; when ``None``, every configured category is yielded. Categories
    that are unset on a given file-type (``tool_config is None``) are skipped
    in either case. Iteration order is outer-loop ``config.tools`` insertion
    order, inner-loop :data:`TOOL_CATEGORIES` order.
    """
    for ext, file_type_tools in config.tools.items():
        for category in TOOL_CATEGORIES:
            if run_tools is not None and not getattr(run_tools, category):
                continue
            tool_config = getattr(file_type_tools, category)
            if tool_config is None:
                continue
            yield ext, category, tool_config


def _run_deterministic_tools(
    config: Config,
    run_tools: RunToolsConfig | None = None,
) -> list[Finding]:
    """Run configured deterministic tools and return their findings.

    When *run_tools* is provided, only tool categories whose corresponding
    flag is ``True`` are executed.  When ``None``, all configured tools run.
    """
    findings: list[Finding] = []
    if config.tools:
        current_ext: str | None = None
        for ext, category, tool_config in _iter_runnable_tools(config, run_tools):
            if ext != current_ext:
                logger.debug(f"--- .{ext} tools ---")
                current_ext = ext
            # One misconfigured tool (missing binary, bad timeout, unknown
            # parser) must not abort the whole run — log and skip so the
            # remaining tools still contribute findings.
            try:
                findings.extend(run_tool(category, tool_config))
            except ToolExecutionError as exc:
                logger.warning(f"{category} ({tool_config.command}) failed: {exc}")

        # Freshly-parsed tool findings always have ``status == "open"``,
        # so no filter is needed here.
        logger.info(f"Tool findings: {len(findings)} finding(s).")
    else:
        logger.info("No tools configured. Skipping deterministic analysis.")
    return findings


def _run_tool_health_check(
    config: Config,
    run_tools: RunToolsConfig | None = None,
) -> None:
    """Display per-tool pass/fail status for all configured deterministic tools.

    When *run_tools* is provided, only tool categories whose corresponding
    flag is ``True`` are executed.  When ``None``, all configured tools run.
    """
    if not config.tools:
        return

    from sqa_agent.ui import console

    failures: list[tuple[str, str, int]] = []
    errors: list[tuple[str, str, str]] = []
    for _ext, category, tool_config in _iter_runnable_tools(config, run_tools):
        # A misconfigured tool must not abort the menu's startup render —
        # record the error and continue so the user sees health status for
        # the remaining tools.
        try:
            tool_findings = run_tool(category, tool_config)
        except ToolExecutionError as exc:
            logger.warning(f"{category} ({tool_config.command}) failed: {exc}")
            errors.append((category, tool_config.command, str(exc)))
            continue
        if tool_findings:
            failures.append((category, tool_config.command, len(tool_findings)))

    if not failures and not errors:
        console.print("[green]Code checks: all passing[/green]")
    else:
        if failures:
            console.print("[yellow]Code check failures detected:[/yellow]")
            for category, command, count in failures:
                noun = "finding" if count == 1 else "findings"
                label = _CATEGORY_LABELS.get(category, category)
                console.print(
                    f"  [yellow]{label} ({count} {noun}):[/yellow]  {command}"
                )
        if errors:
            console.print("[red]Code check errors (tool failed to run):[/red]")
            for category, command, message in errors:
                label = _CATEGORY_LABELS.get(category, category)
                console.print(f"  [red]{label}:[/red]  {command}  ({message})")
        console.print(
            "[dim]Resolve these before running a review — use the auto-resolve or\n"
            "interactive-resolve menu items, or fix them via another agent or\n"
            "the command line.[/dim]"
        )


class ReviewCandidates(NamedTuple):
    """Result of :func:`_prepare_review_state`.

    Returned as a :class:`NamedTuple` so callers can access fields by name
    (e.g. ``result.candidates``) rather than unpacking a 5-tuple by
    position, which silently breaks if the order ever changes.

    ``repo`` is ``None`` only in the "no git repository" short-circuit,
    which always comes with ``candidates == []``; downstream code that
    needs ``repo`` already gates on ``candidates`` / ``needs_review``
    being non-empty, so it never sees a ``None`` here.
    """

    repo: Repo | None
    candidates: list[str]
    needs_review: list[str]
    file_status: dict[str, str]
    current_hashes: dict[str, str]


def _prepare_review_state(
    config: Config,
    project_root: Path,
    sqa_dir: Path,
) -> ReviewCandidates:
    """Prepare the state needed to drive an agent review.

    Three separable steps, bundled together because ``cmd_review`` always
    needs all of them: (1) resolve candidate files from config + git,
    (2) compute current blob hashes for those candidates, and
    (3) reconcile against any saved file-status and persist the result.

    Both the "no git repository" and "no candidate files" short-circuits
    return an empty :class:`ReviewCandidates` so callers have a single
    "nothing to review" branch (``if not result.candidates``) rather than
    two. The no-repo error has already been logged by
    :func:`_find_repo_or_log`.
    """
    repo = _find_repo_or_log(project_root)
    if repo is None:
        return ReviewCandidates(None, [], [], {}, {})

    candidates = resolve_candidate_files(config, project_root, repo)
    if not candidates:
        return ReviewCandidates(repo, [], [], {}, {})

    current_hashes = compute_hashes(repo, project_root, candidates)
    file_status = load_file_status(sqa_dir)
    needs_review = reconcile(file_status, candidates, current_hashes)
    save_file_status(sqa_dir, file_status)

    return ReviewCandidates(
        repo=repo,
        candidates=candidates,
        needs_review=needs_review,
        file_status=file_status,
        current_hashes=current_hashes,
    )


async def _run_agent_reviews(
    sqa_dir: Path,
    config: Config,
    project_root: Path,
    repo: Repo,
    needs_review: list[str],
    file_status: dict[str, str],
    current_hashes: dict[str, str],
    max_agents: int,
    on_findings: Callable[[list[Finding]], None],
) -> ReviewStats:
    """Run prompt-driven agent reviews (general + per-file).

    Returns the accumulated :class:`ReviewStats`.
    """
    prompts_dir = sqa_dir / "prompts"
    general_sections = load_general_prompts(prompts_dir)
    file_sections = load_file_prompts(prompts_dir)

    stats = ReviewStats()

    # General review (once per review session).
    if general_sections:
        logger.info(f"--- General review ({len(general_sections)} prompt(s)) ---")
        await review_general(
            general_sections,
            config.agent,
            project_root,
            stats,
            on_section_findings=on_findings,
        )

    # Per-file review — workers pull files on demand from a shared queue.
    file_queue: asyncio.Queue[str] = asyncio.Queue()
    for path in needs_review:
        file_queue.put_nowait(path)

    num_workers = min(max_agents, len(needs_review))
    use_labels = num_workers > 1
    total_files = len(needs_review)
    files_started = 0

    async def worker(index: int) -> list[Finding]:
        nonlocal files_started
        label = f"Agent #{index}" if use_labels else None
        prefix = f"{label} " if label else ""
        logger.info(
            f"--- {prefix}Starting worker ({len(file_sections)} prompt(s) per file) ---"
        )

        def worker_file_start(file_path: str) -> None:
            nonlocal files_started
            files_started += 1
            logger.info(
                f"--- {prefix}[{files_started}/{total_files}] "
                f"Reviewing: {file_path} ({len(file_sections)} prompt(s)) ---"
            )

        def worker_file_complete(file_path: str, file_findings: list[Finding]) -> None:
            on_findings(file_findings)
            mark_reviewed(sqa_dir, file_status, file_path, current_hashes[file_path])
            logger.info(f"{prefix}Completed review of {file_path}")

        return await review_file_queue(
            file_sections,
            file_queue,
            config.agent,
            project_root,
            stats,
            on_file_start=worker_file_start,
            on_file_complete=worker_file_complete,
            agent_label=label,
        )

    await asyncio.gather(*(worker(i) for i in range(1, num_workers + 1)))

    return stats


def _persist_findings(
    result_path: Path,
    all_findings: list[Finding],
    log_message: str | None = None,
) -> None:
    """Assign sequential IDs to *all_findings* and write them to *result_path*.

    Single source of truth for the "IDs + write together" invariant so the
    two steps can't drift out of sync at different call sites. When
    *log_message* is provided it is emitted at INFO level after the write.

    Both ``assign_ids`` and ``write_result`` are idempotent, so calling this
    helper after findings have already been persisted (e.g. incrementally
    via the ``on_findings`` callback) is harmless.
    """
    assign_ids(all_findings)
    write_result(result_path, all_findings)
    if log_message:
        logger.info(log_message)


async def cmd_review() -> int:
    """Run all configured tools and collect findings."""
    sqa_dir = find_sqa_dir_or_log()
    if sqa_dir is None:
        return 1

    config = _load_config_or_none(sqa_dir / "config.toml")
    if config is None:
        return 1
    project_root = Path.cwd()

    # Show active configuration so the user can Ctrl-C early if needed.
    _log_config(config)

    all_findings: list[Finding] = []
    result_path = make_result_path(sqa_dir)

    def on_findings(new_findings: list[Finding]) -> None:
        """Callback invoked after each completed review unit — persist incrementally.

        Called per-section for the general review and per-file for batch
        file reviews.
        """
        all_findings.extend(new_findings)
        _persist_findings(result_path, all_findings)

    # --- Identify files for agent review ---

    result = _prepare_review_state(config, project_root, sqa_dir)

    if not result.candidates:
        # Covers both the no-git-repo case (error already logged by
        # ``_find_repo_or_log``) and the no-matching-files case.
        logger.info("No candidate files matched the config. Skipping agent review.")
        if all_findings:
            _persist_findings(
                result_path,
                all_findings,
                f"Results written to {result_path.name}",
            )
        return 0

    if not result.needs_review:
        logger.info(
            f"All {len(result.candidates)} candidate file(s) are up to date. "
            "Nothing to review."
        )
        if all_findings:
            _persist_findings(
                result_path,
                all_findings,
                f"Results written to {result_path.name}",
            )
        return 0

    logger.info(
        f"{len(result.needs_review)} of {len(result.candidates)} file(s) need review:"
    )
    for rel_path in result.needs_review:
        logger.info(f"  {rel_path}")

    from sqa_agent.ui import confirm, prompt_concurrency

    if not confirm("Proceed with agent review?"):
        logger.info("Aborted by user.")
        if all_findings:
            _persist_findings(
                result_path,
                all_findings,
                f"Results written to {result_path.name}",
            )
        return 0

    if len(result.needs_review) > 1:
        max_agents = prompt_concurrency(len(result.needs_review))
    else:
        max_agents = 1

    # --- Agent reviews ---

    # ``result.repo`` is typed ``Repo | None`` so the no-repo and
    # no-candidates short-circuits above share a return shape; by the
    # time ``needs_review`` is non-empty the repo is guaranteed real.
    # Narrow explicitly rather than using ``assert`` so the check isn't
    # stripped under ``python -O``.
    repo_or_none = result.repo
    if repo_or_none is None:
        logger.error("Internal error: needs_review non-empty but repo is None.")
        return 1
    repo: Repo = repo_or_none

    try:
        stats = await _run_agent_reviews(
            sqa_dir,
            config,
            project_root,
            repo,
            result.needs_review,
            result.file_status,
            result.current_hashes,
            max_agents,
            on_findings,
        )
    except FatalAPIError as e:
        logger.error(f"\n{e.user_message}")
        if all_findings:
            _persist_findings(
                result_path,
                all_findings,
                f"Progress saved ({len(all_findings)} finding(s)) to {result_path.name}. "
                "Re-run the review to continue where you left off.",
            )
        return 1
    except Exception:
        # Unexpected failure (disk full during a callback write, bug in a
        # helper, etc.) — attempt one last persist so partial progress isn't
        # lost, then re-raise so the real traceback still surfaces.
        # ``_persist_findings`` itself is idempotent and may fail too (e.g.
        # the same disk-full condition); swallow that so the original
        # exception isn't masked.
        if all_findings:
            try:
                _persist_findings(result_path, all_findings)
            except Exception:
                logger.exception("Failed to persist findings during error recovery.")
        raise

    # --- Summary ---

    logger.info("--- Review summary ---")
    logger.info(f"Total findings: {len(all_findings)}")
    logger.info(f"Agent prompts: {stats.total_prompts}")
    if stats.parse_failures:
        # Distinguish a degraded review from a genuinely clean one — each
        # failure already emitted a per-section warning during the run;
        # this surfaces the aggregate so it's not lost in a long log.
        logger.warning(
            f"Parse failures: {stats.parse_failures} "
            f"(sections whose agent response couldn't be parsed; check warnings above)"
        )
    logger.info(f"Agent cost: ${stats.total_cost_usd:.4f}")
    logger.info(f"Agent time: {format_duration(stats.total_duration_secs)}")
    logger.info(f"Results written to {result_path.name}")
    return 0


def cmd_check() -> int:
    """Run configured code-quality tools, with a sub-menu for selection."""
    from sqa_agent.ui import choose_menu, console

    sqa_dir_or_none = find_sqa_dir_or_log()
    if sqa_dir_or_none is None:
        return 1

    config = _load_config_or_none(sqa_dir_or_none / "config.toml")
    if config is None:
        return 1

    if not config.tools:
        console.print("[yellow]No tools configured in config.toml.[/yellow]")
        return 0

    # Build the list of configured tool categories (order-preserving dedup).
    seen: set[str] = set()
    configured: list[str] = []
    for file_type_tools in config.tools.values():
        for category in TOOL_CATEGORIES:
            if getattr(file_type_tools, category) is not None and category not in seen:
                seen.add(category)
                configured.append(category)

    if not configured:
        console.print("[yellow]No tools configured in config.toml.[/yellow]")
        return 0

    while True:
        choices = ["Run all"]
        for cat in configured:
            choices.append(_CATEGORY_LABELS.get(cat, cat))
        choices.append("Back")

        choice = choose_menu("Check — code-quality tools", choices)

        if choice == "Back":
            return 0

        if choice == "Run all":
            run_tools = None
        else:
            # Map the label back to a RunToolsConfig with only one flag set.
            label_to_cat = {_CATEGORY_LABELS.get(c, c): c for c in configured}
            selected_cat = label_to_cat[choice]
            kwargs = {cat: (cat == selected_cat) for cat in TOOL_CATEGORIES}
            run_tools = RunToolsConfig(**kwargs)

        _run_tool_health_check(config, run_tools=run_tools)


@dataclass(frozen=True)
class _CliCommand:
    """Declarative entry for a single CLI command.

    Single source of truth for the command catalogue: ``build_parser``
    (argparse help), ``_interactive_menu`` (label + description +
    dispatch), and ``main`` (dispatch + sync/async wrapping) all derive
    from :data:`_COMMANDS`.  Adding a new command = adding one entry
    here; nothing else needs to change to keep the three surfaces in
    sync.
    """

    name: str
    """CLI subcommand name as typed on the command line (e.g. ``"auto-resolve"``)."""

    label: str
    """Human-readable title shown in the interactive menu (e.g. ``"Auto-resolve"``)."""

    help: str
    """One-line help string shown by ``sqa-agent --help``."""

    menu_description: str
    """One-line description shown next to :attr:`label` in the menu."""

    handler: Callable[[], int] | Callable[[], Coroutine[Any, Any, int]]
    """Zero-argument callable implementing the command; returns the exit code."""

    is_async: bool
    """True when :attr:`handler` is a coroutine (main wraps it in ``asyncio.run``)."""

    in_menu: bool = True
    """False excludes the command from the interactive menu (e.g. ``init`` is bootstrap-only)."""


# Declared in menu/workflow order — build_parser adds subparsers in this
# order too (affects --help output layout only).  Add new commands here;
# all three dispatch surfaces update automatically.
_COMMANDS: tuple[_CliCommand, ...] = (
    _CliCommand(
        name="review",
        label="Review",
        help="Run configured tools and collect findings",
        menu_description="Run tools and AI-powered code analysis",
        handler=cmd_review,
        is_async=True,
    ),
    _CliCommand(
        name="triage",
        label="Triage",
        help="Triage and manage findings",
        menu_description="Walk through findings and assign auto/interactive/ignore",
        handler=cmd_triage,
        is_async=False,
    ),
    _CliCommand(
        name="auto-resolve",
        label="Auto-resolve",
        help="Autonomously resolve findings triaged as 'auto'",
        menu_description="Autonomously resolve findings triaged as 'auto'",
        handler=cmd_auto_resolve,
        is_async=True,
    ),
    _CliCommand(
        name="interactive-resolve",
        label="Interactive-resolve",
        help="Interactively resolve findings triaged as 'interactive'",
        menu_description="Interactively resolve findings triaged as 'interactive'",
        handler=cmd_interactive_resolve,
        is_async=True,
    ),
    _CliCommand(
        name="reset",
        label="Reset",
        help="Mark all files as reviewed at their current state",
        menu_description="Mark all files as reviewed at their current state",
        handler=cmd_reset,
        is_async=False,
    ),
    _CliCommand(
        name="commit",
        label="Commit",
        help="Commit all current changes to the current branch",
        menu_description="Commit all current changes to the current branch",
        handler=cmd_commit,
        is_async=False,
    ),
    _CliCommand(
        name="check",
        label="Check",
        help="Run code-quality tools (formatter, linter, type-checker, test)",
        menu_description="Run code-quality tools (formatter, linter, type-checker, test)",
        handler=cmd_check,
        is_async=False,
    ),
    _CliCommand(
        name="init",
        label="Init",
        help="Initialize a new project for SQA analysis",
        # Bootstrap-only: meaningless when .sqa-agent/ already exists and
        # the menu is being shown.  Excluded from the menu.
        menu_description="",
        handler=cmd_init,
        is_async=False,
        in_menu=False,
    ),
)


def _command_by_name(name: str) -> _CliCommand | None:
    """Look up a command by its CLI name, or return ``None`` if not found."""
    for cmd in _COMMANDS:
        if cmd.name == name:
            return cmd
    return None


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Subparsers are derived from :data:`_COMMANDS` in declaration order.
    """
    parser = argparse.ArgumentParser(
        prog="sqa-agent",
        description="AI-powered software quality assurance agent.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (debug-level) logging output.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for cmd in _COMMANDS:
        subparsers.add_parser(cmd.name, help=cmd.help)
    return parser


# Sentinel label used for the "exit the menu" option.  Lives outside
# :data:`_COMMANDS` because it isn't a real command (no handler, no
# argparse subcommand) — it just closes the interactive loop.
_MENU_QUIT_LABEL = "Quit"


def _interactive_menu() -> int:
    """Show an interactive menu and dispatch the selected command.

    Menu entries are derived from :data:`_COMMANDS` (filtered by
    ``in_menu``); dispatch looks the selected command up by name and
    wraps it in ``asyncio.run`` when ``is_async`` is set.
    """
    from sqa_agent.ui import choose_menu, confirm, console

    labels: list[str] = []
    label_to_name: dict[str, str | None] = {}
    for cmd in _COMMANDS:
        if not cmd.in_menu:
            continue
        label = f"{cmd.label}  \u2014  {cmd.menu_description}"
        labels.append(label)
        label_to_name[label] = cmd.name
    labels.append(_MENU_QUIT_LABEL)
    label_to_name[_MENU_QUIT_LABEL] = None

    try:
        while True:
            # The result-file scan and tool health check below are
            # intentionally re-run every iteration: the whole point of
            # the menu is to show fresh status after each command
            # (review, resolve, reset, commit all mutate this state).
            # Caching would only save work at the cost of stale display.
            #
            # Show open-findings summary if .sqa-agent/ exists.
            # ``is_dir`` (not ``exists``) so a stray file at the path
            # doesn't trip ``NotADirectoryError`` in the glob below.
            sqa_dir = find_sqa_dir()
            if sqa_dir.is_dir():
                summaries = _summarize_result_files(sqa_dir)
                if summaries:
                    console.print("\n[bold]Open findings:[/bold]")
                    for _, label in summaries:
                        console.print(f"  {label}")
                    console.print(f"  [dim]{RESULT_LEGEND}[/dim]")

                config = _load_config_or_none(sqa_dir / "config.toml")
                if config is None:
                    # The error (migration guidance, TOML syntax, etc.) has
                    # already been written to stderr.  Bail out of the menu
                    # rather than looping on a config that every command
                    # would re-reject.
                    return 1
                _run_tool_health_check(config, run_tools=config.menu)

            repo = _find_repo_or_log(Path.cwd())
            if repo is not None:
                if repo.is_dirty(untracked_files=True):
                    console.print("[yellow]Git worktree: uncommitted changes[/yellow]")
                    console.print(
                        "[dim]Commit changes using the commit menu item, "
                        "or via the command line or another agent.[/dim]"
                    )
                else:
                    console.print("[green]Git worktree: clean[/green]")

            choice = choose_menu("SQA Agent", labels)
            cmd_name = label_to_name[choice]

            if cmd_name is None:
                # Quit sentinel.
                return 0

            cmd = _command_by_name(cmd_name)
            # Registry-derived labels always resolve; an inconsistency
            # here would be a programming error.
            assert cmd is not None, f"menu label maps to unknown command {cmd_name!r}"

            if not confirm(f"Run [bold]{cmd.label}[/bold]?"):
                continue

            # Don't let a single command's exception drop the user out of
            # the interactive menu — log and keep looping so they can try
            # another action. KeyboardInterrupt is handled by the outer
            # except so Ctrl-C still exits cleanly.
            try:
                if cmd.is_async:
                    asyncio.run(cast(Coroutine[Any, Any, int], cmd.handler()))
                else:
                    cmd.handler()
            except Exception as exc:
                logger.error(f"Command failed: {exc}")
                continue
    except KeyboardInterrupt:
        return EXIT_KEYBOARD_INTERRUPT


def _setup_logging(verbose: bool) -> None:
    """Configure console and file logging.

    The console handler respects the user's verbosity setting (INFO or
    DEBUG).  If a ``.sqa-agent/`` directory exists in the current working
    directory, a persistent timestamped file handler is added:

    * ``logs/<timestamp>.log`` — always DEBUG level; accumulates across
      runs so users can review history.
    """
    from datetime import datetime

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — user-selected level, simple format.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    # Persistent timestamped log in .sqa-agent/logs/.
    # ``is_dir`` (not ``exists``) so a stray file at the path doesn't
    # trip ``NotADirectoryError`` when we try to create ``logs/``.
    sqa_dir = find_sqa_dir()
    if sqa_dir.is_dir():
        logs_dir = sqa_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"{timestamp}.log"
        file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        # Owner-only (``rw-------``): these DEBUG logs include tool
        # commands, file paths, and tool stdout/stderr that may contain
        # secrets — not safe to leave world-readable on a shared host.
        log_path.chmod(0o600)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(message)s")
        )
        root.addHandler(file_handler)


def main() -> int:
    """Entry point for the SQA Agent CLI."""
    parser = build_parser()
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.command is None:
        return _interactive_menu()

    cmd = _command_by_name(args.command)
    if cmd is None:
        # Unreachable under normal argparse flow (argparse rejects
        # unknown subcommands), but keep as a defensive fallback.
        parser.print_help()
        return 1

    if cmd.is_async:
        return asyncio.run(cast(Coroutine[Any, Any, int], cmd.handler()))
    return cast(int, cmd.handler())


if __name__ == "__main__":
    sys.exit(main())
