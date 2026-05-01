"""Reusable UI primitives built on rich and prompt_toolkit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, PromptBase
from rich.status import Status

from sqa_agent.findings import Finding

console = Console()

_INTERACTIVE_HELP_PREFIX = """\
Here, you may have a multi-turn interactive conversation with the agent to resolve the given finding.

[bold]Input:[/bold]
  [dim]Enter[/dim]        — Submit your message
  [dim]Alt+Enter[/dim]   — Insert a newline (for multi-line messages)

[bold]Commands:[/bold] (take no arguments — type the slash-command alone on its own line)"""

# Result of :func:`prompt_triage`. Exported because ``cli.py`` consumes it
# via the return type of :func:`prompt_triage`.
#
# Semantics of each member:
#
# * ``"auto"`` / ``"interactive"`` / ``"ignore"`` — triage *decisions*;
#   the finding's ``triage`` field is updated accordingly.
# * ``"quit"`` — user asked to stop triaging and return to the main menu.
# * ``"forward"`` / ``"back"`` — *navigation* signals (jump to next /
#   previous finding) that do not change the current finding's triage.
#
# :func:`prompt_triage` returns ``None`` (not a member of this literal)
# to signify "skip" — i.e. leave the finding untriaged and advance to
# the next one.
TriageAction = Literal["auto", "interactive", "ignore", "quit", "forward", "back"]


class _TriageOption(NamedTuple):
    """One entry in :data:`_TRIAGE_OPTIONS` — the single source of truth
    for triage keys, prompt rendering, and legend rendering."""

    key: str  # single-character keypress
    action: TriageAction | None  # resulting triage action, or None for skip
    prompt_label: str  # e.g. ``(a)uto`` — shown on the triage prompt line
    plain_label: str  # e.g. ``auto`` — used for legend width alignment
    rendered_label: str  # e.g. ``[green]auto[/green]`` — used in the legend
    description: str  # right-hand column in the legend


# Single source of truth for every triage-key-related string in the UI.
# Extend this tuple to add or rename an option; the prompt, validity
# message, and :data:`TRIAGE_LEGEND` all derive from it.
_TRIAGE_OPTIONS: tuple[_TriageOption, ...] = (
    _TriageOption(
        "a",
        "auto",
        "(a)uto",
        "auto",
        "[green]auto[/green]",
        "Mark for autonomous agent resolution",
    ),
    _TriageOption(
        "n",
        "interactive",
        "i(n)teractive",
        "interactive",
        "i[yellow]n[/yellow]teractive",
        "Mark for interactive agent resolution",
    ),
    _TriageOption(
        "g",
        "ignore",
        "i(g)nore",
        "ignore",
        "i[dim]g[/dim]nore",
        "Dismiss as not applicable or not worth fixing",
    ),
    _TriageOption("s", None, "(s)kip", "skip", "skip", "Leave untriaged for now"),
    _TriageOption(
        "f",
        "forward",
        "(f)wd",
        "fwd",
        "[cyan]fwd[/cyan]",
        "Jump to next finding without changing triage",
    ),
    _TriageOption(
        "b",
        "back",
        "(b)ack",
        "back",
        "[cyan]back[/cyan]",
        "Jump to previous finding without changing triage",
    ),
    _TriageOption(
        "q", "quit", "(q)uit", "quit", "quit", "Stop triaging and return to the menu"
    ),
)

_TRIAGE_KEYS: dict[str, TriageAction | None] = {
    opt.key: opt.action for opt in _TRIAGE_OPTIONS
}
_TRIAGE_PROMPT = "  " + "  ".join(opt.prompt_label for opt in _TRIAGE_OPTIONS)
# Render the list of valid keys as ``a, b, c, or d`` — the trailing option
# gets the "or " prefix, so its position in :data:`_TRIAGE_OPTIONS` is
# observable in the error message. The unpacking below makes that coupling
# explicit (rather than hiding it behind ``[:-1]`` / ``[-1]`` slicing).
*_triage_rest, _triage_last = _TRIAGE_OPTIONS
_TRIAGE_VALID_KEYS_MSG = (
    "[red]  Please enter "
    + ", ".join(opt.key for opt in _triage_rest)
    + f", or {_triage_last.key}.[/red]"
)
del _triage_rest, _triage_last

DEFAULT_RESOLVE_HINT = (
    "Please go ahead and use your best judgment to resolve this issue."
)

# Width-aligned legend derived from :data:`_TRIAGE_OPTIONS` so the legend
# cannot drift from the canonical option list (labels, descriptions, or
# styling). Pad to the widest *plain* label so Rich markup doesn't affect
# the visible column.
_TRIAGE_LEGEND_COL = max(len(opt.plain_label) for opt in _TRIAGE_OPTIONS) + 2
TRIAGE_LEGEND = "[bold]Triage options:[/bold]\n" + "\n".join(
    f"  {opt.rendered_label}{' ' * (_TRIAGE_LEGEND_COL - len(opt.plain_label))}— "
    f"{opt.description}"
    for opt in _TRIAGE_OPTIONS
)


def choose_menu(
    title: str,
    choices: list[str],
    default_index: int | None = None,
    *,
    footer: str = "",
) -> str:
    """Display a numbered menu and return the selected label string.

    Parameters
    ----------
    title:
        Bold heading printed above the menu.
    choices:
        Labels to enumerate. Must be unique — callers (e.g.
        :func:`sqa_agent.cli._summarize_result_files`) use the returned
        label as a dict key, so duplicates would silently collide.
    default_index:
        If given, the 0-based index used as the default selection.
    footer:
        Optional dim text printed below the choices.
    """
    if not choices:
        raise ValueError("choices must not be empty")
    duplicates = {label for label in choices if choices.count(label) > 1}
    if duplicates:
        raise ValueError(
            f"choices must be unique; duplicate label(s): {sorted(duplicates)}"
        )
    if default_index is not None and not (0 <= default_index < len(choices)):
        raise ValueError(
            f"default_index {default_index} out of range for {len(choices)} choices"
        )
    console.print(f"\n[bold]{title}[/bold]\n")
    for i, label in enumerate(choices, 1):
        console.print(f"  {i}. {label}")
    if footer:
        console.print(f"\n[dim]{footer}[/dim]")
    console.print()

    ask_kwargs: dict = {"console": console}
    if default_index is not None:
        ask_kwargs["default"] = default_index + 1

    while True:
        value = IntPrompt.ask("Enter your choice", **ask_kwargs)
        if 1 <= value <= len(choices):
            return choices[value - 1]
        console.print(f"[red]Please enter a number between 1 and {len(choices)}.[/red]")


def display_finding(finding: Finding, index: int, total: int) -> None:
    """Display a finding's location, severity, message, and metadata."""
    # ``file:line`` only makes sense with both; when ``file`` is unknown
    # fall back to a bare ``line N`` (or ``?`` if neither is known) rather
    # than the misleading ``?:42``.
    if finding.file:
        loc = finding.file
        if finding.line is not None:
            loc += f":{finding.line}"
    elif finding.line is not None:
        loc = f"line {finding.line}"
    else:
        loc = "?"
    sev = f" ({finding.severity})" if finding.severity else ""

    console.print(f"\n[bold][{index}/{total}][/bold] {escape(loc)}{sev}")
    console.print(f"  [bold yellow]{escape(finding.message)}[/bold yellow]")
    if finding.code:
        console.print(f"  [dim]Code: {escape(finding.code)}[/dim]")
    if finding.source:
        console.print(f"  [dim]Source: {escape(finding.source)}[/dim]")


def prompt_triage(finding: Finding, index: int, total: int) -> TriageAction | None:
    """Display a finding and prompt for a triage decision.

    Returns ``"auto"``, ``"interactive"``, ``"ignore"``, ``"quit"``,
    ``"forward"``, ``"back"``, or ``None`` (skip).
    """
    display_finding(finding, index, total)

    if finding.triage is not None:
        console.print(f"  [dim]\\[current: {escape(finding.triage)}][/dim]")

    while True:
        answer = PromptBase.ask(
            _TRIAGE_PROMPT,
            default="s",
            console=console,
        )
        key = answer.strip().lower()
        if key in _TRIAGE_KEYS:
            return _TRIAGE_KEYS[key]
        console.print(_TRIAGE_VALID_KEYS_MSG)


def prompt_resolve_hint() -> str:
    """Prompt the user for resolution instructions when triaging as 'auto'.

    Returns the user's text, or ``DEFAULT_RESOLVE_HINT`` if they press Enter.
    """
    answer = PromptBase.ask(
        "  Resolution instructions (Enter for default)",
        default="",
        show_default=False,
        console=console,
    )
    return answer.strip() or DEFAULT_RESOLVE_HINT


# Shared command protocol between :func:`prompt_interactive_input` (producer)
# and the interactive resolve loop in ``agent_resolve.py`` (consumer). Using
# a Literal alias means a typo on either side is caught by the type checker
# rather than silently falling through the match.
InteractiveCommand = Literal["skip", "quit", "resolve"]


@dataclass(frozen=True, slots=True)
class InteractiveInput:
    """Discriminated result from :func:`prompt_interactive_input`.

    *kind* is ``"command"`` (skip / resolve / quit), ``"text"`` (free-form
    user message), or ``"reprompt"`` — the user invoked a read-only
    command (empty input, ``/help``, ``/diff``, ``/commit``) so the
    caller should loop for fresh input.

    When *kind* is ``"command"``, *value* is one of the literals in
    :data:`InteractiveCommand`. For ``"text"`` it is free-form user input.
    """

    kind: Literal["command", "text", "reprompt"]
    value: str = ""


_prompt_session: "PromptSession | None" = None


def _get_prompt_session() -> "PromptSession":
    """Return the module-level PromptSession, creating it on first use."""
    global _prompt_session
    if _prompt_session is None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event):
            """Enter submits the input."""
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _newline(event):
            """Alt+Enter inserts a newline."""
            event.current_buffer.insert_text("\n")

        _prompt_session = PromptSession(key_bindings=bindings)
    return _prompt_session


async def _multiline_prompt() -> str:
    """Read user input using prompt_toolkit.

    - **Enter** submits the input.
    - **Alt+Enter** (or **Option+Enter** on macOS) inserts a newline.
    - Pasted multi-line text is captured as a single input thanks to
      prompt_toolkit's bracket-paste support.

    A module-level :class:`~prompt_toolkit.PromptSession` is reused across
    calls so that input history (Up/Down arrow recall) persists within and
    across interactive resolve conversations.

    Uses ``prompt_async`` so it works inside an already-running event loop
    (e.g. when launched from the interactive menu via ``asyncio.run``).
    """
    from prompt_toolkit.formatted_text import HTML

    session = _get_prompt_session()
    return await session.prompt_async(HTML("<b>You</b>: "), multiline=True)


def _handle_gitdiff() -> None:
    """Thin UI wrapper around :func:`sqa_agent.git_ops.show_diff`.

    Shows only **unstaged** changes (plain ``git diff`` semantics —
    staged and untracked changes are excluded). Errors from git are
    surfaced to the console by ``show_diff`` (``GitCommandError`` and
    the "not inside a git repository" case both print a red message
    rather than silently swallowing the failure).
    """
    from sqa_agent.git_ops import show_diff

    show_diff(console)


def _handle_gitcommit() -> None:
    """Prompt for a commit message and hand off to :mod:`sqa_agent.git_ops`.

    The message prompt lives here (UI concern); the actual staging and
    commit live in :mod:`sqa_agent.git_ops` so all project git access
    shares one backend.
    """
    from sqa_agent.git_ops import stage_and_commit

    # Catch Ctrl-C / EOF locally so an interrupt at this sub-prompt aborts
    # just the commit, not the surrounding interactive-resolve session.
    try:
        message = PromptBase.ask("Commit message", console=console).strip()
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]  Commit aborted.[/dim]")
        return
    if not message:
        console.print("[dim]  Empty message — commit aborted.[/dim]")
        return
    stage_and_commit(console, message)


def _handle_gethelp() -> None:
    """Print the interactive-help block. Referenced as the ``/help`` handler."""
    console.print()
    console.print(INTERACTIVE_HELP)


# Single source of truth for interactive slash-commands. Each entry is
# ``(slash, style, help_text, kind, payload)``:
#
# * ``kind == "command"`` — *payload* is an :data:`InteractiveCommand`; the
#   prompt loop returns ``InteractiveInput("command", payload)``.
# * ``kind == "handler"`` — *payload* is a zero-arg callable invoked for
#   its side effects; the prompt loop then returns
#   ``InteractiveInput("reprompt")``.
#
# Mirrors the :data:`_TRIAGE_OPTIONS` pattern so adding a new command only
# requires one edit: both the ``/help`` block and the dispatch table in
# :func:`prompt_interactive_input` are derived from this tuple.
_InteractiveEntry = tuple[
    str,
    str,
    str,
    Literal["command", "handler"],
    "InteractiveCommand | Callable[[], None]",
]
_INTERACTIVE_COMMANDS: tuple[_InteractiveEntry, ...] = (
    (
        "/resolve",
        "green",
        "Mark this finding as resolved and move on to the next",
        "command",
        "resolve",
    ),
    ("/skip", "yellow", "Skip this finding for now", "command", "skip"),
    ("/quit", "red", "Stop resolving and return to the menu", "command", "quit"),
    ("/diff", "dim", "Show unstaged changes", "handler", _handle_gitdiff),
    (
        "/commit",
        "dim",
        "Stage all changes and commit (prompts for message)",
        "handler",
        _handle_gitcommit,
    ),
    ("/help", "dim", "Show this help message", "handler", _handle_gethelp),
)

# Pad slashes to a common visual width for the help block (Rich markup
# doesn't affect visible width, so we compute padding on the raw slash).
_SLASH_COL = max(len(slash) for slash, *_ in _INTERACTIVE_COMMANDS) + 4
INTERACTIVE_HELP = (
    _INTERACTIVE_HELP_PREFIX
    + "\n"
    + "\n".join(
        f"  [{style}]{slash}[/{style}]{' ' * (_SLASH_COL - len(slash))}— {help_text}"
        for slash, style, help_text, _, _ in _INTERACTIVE_COMMANDS
    )
)

# Dispatch table: slash → (kind, payload). Built once at import time so
# :func:`prompt_interactive_input` can do an O(1) lookup instead of a
# chain of ``if lower == "/..."`` branches.
_INTERACTIVE_DISPATCH: dict[
    str, tuple[Literal["command", "handler"], "InteractiveCommand | Callable[[], None]"]
] = {slash: (kind, payload) for slash, _, _, kind, payload in _INTERACTIVE_COMMANDS}


async def prompt_interactive_input() -> InteractiveInput:
    """Prompt for interactive-resolve input.

    Returns an :class:`InteractiveInput` whose *kind* distinguishes commands
    from user text and re-prompt signals.
    """
    try:
        answer = await _multiline_prompt()
    except (EOFError, KeyboardInterrupt):
        # ``"quit": InteractiveCommand`` from the dispatch table by design.
        return InteractiveInput("command", "quit")
    text = answer.strip()

    if not text:
        return InteractiveInput("reprompt")

    lower = text.lower()
    entry = _INTERACTIVE_DISPATCH.get(lower)
    if entry is not None:
        kind, payload = entry
        if kind == "command":
            # ``payload`` is an :data:`InteractiveCommand` literal here.
            return InteractiveInput("command", payload)  # type: ignore[arg-type]
        # ``kind == "handler"``: payload is a callable.
        payload()  # type: ignore[operator]
        return InteractiveInput("reprompt")

    if lower.startswith("/"):
        console.print(
            f"[red]  Unknown command: {escape(text)}. Type /help for available commands.[/red]"
        )
        return InteractiveInput("reprompt")

    return InteractiveInput("text", text)


def agent_status() -> Status:
    """Return a console status context manager showing a spinner."""
    return console.status("[bold blue]Agent is thinking...[/bold blue]", spinner="dots")


def display_agent_response(text: str) -> None:
    """Render agent text in a panel with markdown formatting."""
    console.print(Panel(Markdown(text), title="Agent", border_style="blue"))


def display_agent_tool_use(tool_name: str, brief: str) -> None:
    """Show a dim line indicating tool activity."""
    console.print(f"  [dim][{escape(tool_name)}] {escape(brief)}[/dim]")


def prompt_concurrency(total_files: int) -> int:
    """Ask the user how many simultaneous agents to run.

    Returns a value clamped to ``[1, total_files]``. When there is at most
    one file to review, returns ``1`` without prompting (the degenerate
    ``1-1`` prompt is skipped).
    """
    if total_files <= 1:
        return 1
    value = IntPrompt.ask(
        f"Simultaneous agents (1-{total_files})",
        default=1,
        console=console,
    )
    clamped = max(1, min(value, total_files))
    if clamped != value:
        console.print(f"[dim]Using {clamped} concurrent agent(s).[/dim]")
    return clamped


def confirm(message: str) -> bool:
    """Prompt for y/n confirmation (defaults to yes on bare Enter)."""
    return Confirm.ask(message, default=True, console=console)
