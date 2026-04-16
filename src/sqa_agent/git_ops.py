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

    The "stage everything" scope is deliberate and differs from
    :func:`sqa_agent.cli.cmd_commit` / :func:`sqa_agent.cli._commit_resolve_changes`,
    which both use tracked-only ``git add -u``.  Interactive-resolve
    sessions routinely produce new files (a helper module the agent
    extracted, a new test the agent wrote) and the ``/commit`` slash
    is shaped for a one-keystroke "capture what just happened" flow —
    tracked-only would silently drop those new files and break the
    ergonomics.  ``cmd_commit`` is the conservative counterpart for
    ad-hoc manual commits where sweeping in untracked scratch files
    would be surprising.  Keep the scope policies in sync with each
    other when touching either site.

    Shows ``git status --short`` of the staged set before committing so
    the user can see exactly what is about to land — matches
    ``cmd_commit``'s visibility without adding a confirmation prompt
    (the slash-command's value is one-keystroke speed).
    """
    from git import GitCommandError

    repo = _open_repo(console)
    if repo is None:
        return
    try:
        repo.git.add(".")
        # Show the staged set before committing.  ``diff --name-status
        # --staged`` — same command cmd_commit uses — lists exactly the
        # files going into the commit (no unstaged edits, no untracked
        # leftovers).
        staged = repo.git.diff("--name-status", "--staged").strip()
        if not staged:
            console.print("[dim]  Nothing staged — nothing to commit.[/dim]")
            return
        console.print("\n[bold]Staged for commit:[/bold]")
        # ``markup=False`` so filenames containing Rich markup syntax are
        # displayed literally and can't spoof the status output.
        console.print(staged, markup=False)
        repo.index.commit(message)
    except GitCommandError as exc:
        console.print(f"[red]  Commit failed:[/red] {escape(str(exc))}")
        return
    console.print(f"[green]  Committed: {escape(message)}[/green]")
