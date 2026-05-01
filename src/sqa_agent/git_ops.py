"""Git operations invoked from the interactive-resolve UI.

Split out of :mod:`sqa_agent.ui` so that module stays scoped to "how to
render a menu / read a prompt". These helpers use :class:`git.Repo` —
matching the style in :mod:`sqa_agent.cli` and :mod:`sqa_agent.file_status`
— so all project git access shares one backend.

Output goes through a caller-provided :class:`rich.console.Console` so
this module doesn't depend on the UI module's module-level console
instance (which would create a circular import in practice).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

if TYPE_CHECKING:
    # Type-only import: ``git`` is imported lazily inside each helper below
    # so that a simple ``rich``-only consumer of this module doesn't pay
    # the gitpython startup cost.  This import makes ``Repo`` resolvable
    # to static type-checkers without running at import time.
    from git import Repo


def _open_repo(console: Console) -> Repo | None:
    """Open the git repository rooted at (or above) the current working dir.

    Returns ``None`` — with a user-visible message — when the cwd is not
    inside a git checkout, so the ``/diff`` and ``/commit`` handlers can
    surface a friendly error instead of crashing.
    """
    from git import InvalidGitRepositoryError, Repo

    try:
        return Repo(".", search_parent_directories=True)
    except InvalidGitRepositoryError:
        console.print("[red]  Not inside a git repository.[/red]")
        return None


def show_diff(console: Console) -> None:
    """Display the current unstaged diff inline on *console*."""
    from git import GitCommandError

    repo = _open_repo(console)
    if repo is None:
        return
    try:
        output = repo.git.diff().strip()
    except GitCommandError as exc:
        console.print(f"[red]  git diff failed:[/red] {escape(str(exc))}")
        return
    if not output:
        console.print("[dim]  No unstaged changes.[/dim]")
    else:
        # Render via :class:`rich.syntax.Syntax` rather than a Markdown
        # fenced block — a diffed file that itself contains ``` would
        # terminate a fenced block early and cause the remainder to be
        # rendered as Markdown headings/links.
        console.print()
        console.print(Syntax(output, "diff", background_color="default"))
        console.print()


def stage_and_commit(console: Console, message: str) -> None:
    """Stage all changes (``git add .``) and commit with *message*.

    Mirrors the legacy "stage everything" behaviour of the interactive
    ``/commit`` slash-command — *not* the tracked-only ``-u`` policy
    used by :func:`sqa_agent.cli.cmd_commit` /
    :func:`sqa_agent.cli._commit_resolve_changes`; interactive-resolve
    commits are expected to include files the agent just created.
    """
    from git import GitCommandError

    repo = _open_repo(console)
    if repo is None:
        return
    try:
        repo.git.add(".")
        repo.index.commit(message)
    except GitCommandError as exc:
        console.print(f"[red]  Commit failed:[/red] {escape(str(exc))}")
        return
    console.print(f"[green]  Committed: {escape(message)}[/green]")
