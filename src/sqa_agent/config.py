"""Configuration loading and validation for SQA Agent."""

import re
import tomllib
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Literal, get_args

# Default Claude model used for both review and resolve sessions.
# Referenced by AgentConfig defaults and the migration message, so bumping
# this single constant keeps the user-facing guidance in sync.
_DEFAULT_MODEL = "claude-opus-4-7"

# Known Claude model families.  Update when new families are released.
_MODEL_FAMILIES = ("opus", "sonnet", "haiku")

# Matches Claude model IDs of the form
# ``claude-<family>-<version>[.-<version>...][-<yyyymmdd>]``.
# Examples: claude-sonnet-4-6, claude-opus-4-7, claude-haiku-4-5-20251001.
#
# The regex is intentionally permissive for validation purposes only — the
# greedy ``(?:[.-]\d+)*`` absorbs any number of ``.N`` or ``-N`` version
# segments, and the trailing 8-digit date group is ambiguous with another
# numeric version segment (``-4-5-20251001`` could match as either
# version=``4-5-20251001`` or version=``4-5`` + date=``20251001``). Both
# parses are acceptable for validating the overall shape, so we don't try
# to disambiguate; downstream code never inspects the captured groups.
_MODEL_PATTERN = re.compile(
    r"^claude-(" + "|".join(_MODEL_FAMILIES) + r")-\d+(?:[.-]\d+)*(?:-\d{8})?$"
)

ThinkingMode = Literal["adaptive", "disabled"]
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

_VALID_THINKING: tuple[str, ...] = get_args(ThinkingMode)
_VALID_EFFORT: tuple[str, ...] = get_args(EffortLevel)


class ConfigMigrationError(ValueError):
    """Raised when a config file uses a deprecated option and needs user action.

    Carries a multi-line ``user_message`` rendered verbatim by the CLI so the
    user sees actionable migration guidance instead of a one-line traceback.
    """

    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


def _validate_model(name: str, field_name: str) -> None:
    """Raise ValueError if *name* doesn't look like a valid Claude model ID."""
    if not _MODEL_PATTERN.match(name):
        raise ValueError(
            f"{field_name}={name!r} doesn't look like a valid Claude model ID. "
            f"Expected a name like 'claude-opus-4-7' or 'claude-sonnet-4-6'. "
            f"Check for typos in your config.toml [agent] section."
        )


@dataclass
class ToolConfig:
    """Configuration for a single tool (e.g. a linter or type-checker)."""

    command: str
    parser: str = "raw"

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command:
            raise ValueError("command must be a non-empty string")
        if not isinstance(self.parser, str) or not self.parser:
            raise ValueError("parser must be a non-empty string")


@dataclass
class FileTypeTools:
    """Tool configuration for a single file type (e.g. .py files)."""

    formatter: ToolConfig | None = None
    linter: ToolConfig | None = None
    type_checker: ToolConfig | None = None
    test: ToolConfig | None = None


TOOL_CATEGORIES = tuple(f.name for f in dataclass_fields(FileTypeTools))


@dataclass
class RunToolsConfig:
    """Controls which deterministic tools run in a given phase.

    Each field corresponds to a tool category in :class:`FileTypeTools`.
    When ``True`` the tool is executed; when ``False`` it is skipped.
    All default to ``False`` for backward compatibility.
    """

    formatter: bool = False
    linter: bool = False
    type_checker: bool = False
    test: bool = False


_RUN_TOOLS_FIELDS = {f.name for f in dataclass_fields(RunToolsConfig)}


@dataclass
class ResolveConfig:
    """Per-mode configuration for the resolve phase."""

    auto: RunToolsConfig = field(default_factory=RunToolsConfig)
    interactive: RunToolsConfig = field(default_factory=RunToolsConfig)


@dataclass
class AgentConfig:
    """Configuration for the AI agent review.

    ``thinking`` and ``effort`` apply uniformly to both review and resolve
    sessions.  (The 1M-token context window is automatic for Opus 4.7 and
    Sonnet 4.6, so no config knob is needed for it.)
    """

    review_model: str = _DEFAULT_MODEL
    resolve_model: str = _DEFAULT_MODEL
    thinking: ThinkingMode = "adaptive"
    effort: EffortLevel = "xhigh"

    def __post_init__(self) -> None:
        if not isinstance(self.review_model, str) or not self.review_model:
            raise ValueError("review_model must be a non-empty string")
        if not isinstance(self.resolve_model, str) or not self.resolve_model:
            raise ValueError("resolve_model must be a non-empty string")
        _validate_model(self.review_model, "review_model")
        _validate_model(self.resolve_model, "resolve_model")
        if self.thinking not in _VALID_THINKING:
            raise ValueError(
                f"thinking={self.thinking!r} is not valid. "
                f"Expected one of: {', '.join(_VALID_THINKING)}."
            )
        if self.effort not in _VALID_EFFORT:
            raise ValueError(
                f"effort={self.effort!r} is not valid. "
                f"Expected one of: {', '.join(_VALID_EFFORT)}."
            )


_AGENT_FIELDS = {f.name for f in dataclass_fields(AgentConfig)}
# Hardcoded rather than derived from a dataclass: ``include`` and ``exclude``
# live on Config itself (top-level fields) rather than a nested Files
# dataclass, so there is no single source to derive them from. Update both
# Config and this set together when adding new ``[files]`` keys.
_FILES_FIELDS = {"include", "exclude"}
_TOOL_ENTRY_FIELDS = {f.name for f in dataclass_fields(ToolConfig)}


_MAX_THINKING_TOKENS_MIGRATION_MSG = f"""\
Your config.toml uses 'max_thinking_tokens', which is no longer supported.

Claude Opus 4.7 replaces the fixed thinking-budget model with adaptive
thinking, where the model decides per-step how much to think. A fixed
budget can no longer be set.

Please update the [agent] section of your .sqa-agent/config.toml:

  Remove this line:
    max_thinking_tokens = <N>

  Add (or keep) these lines (values shown are the defaults):
    review_model  = "{_DEFAULT_MODEL}"
    resolve_model = "{_DEFAULT_MODEL}"
    thinking      = "adaptive"    # or "disabled"
    effort        = "xhigh"       # one of: low, medium, high, xhigh, max

All fields have sensible defaults, so you can simply delete
'max_thinking_tokens' and omit the others to accept the defaults above.

(Note: the 1M-token context window is built in to Opus 4.7 and
Sonnet 4.6, so no config knob is required for it.)"""


@dataclass
class Config:
    """Top-level SQA Agent configuration.

    Attributes:
        include: Glob patterns for files eligible for agent review and
            deterministic tool runs.
        exclude: Glob patterns excluded from agent review and deterministic
            tool runs.
        tools: Per-file-extension tool configurations keyed by extension (e.g. ".py").
        agent: AI agent settings (models, thinking mode, effort).
        resolve: Per-mode controls for which deterministic tools run during resolve.
        menu: Which deterministic tools the interactive menu health-checks at startup.
    """

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    tools: dict[str, FileTypeTools] = field(default_factory=dict)
    agent: AgentConfig = field(default_factory=AgentConfig)
    resolve: ResolveConfig = field(default_factory=ResolveConfig)
    menu: RunToolsConfig = field(default_factory=RunToolsConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.include, list) or not all(
            isinstance(s, str) for s in self.include
        ):
            raise ValueError("include must be a list of strings")
        if not isinstance(self.exclude, list) or not all(
            isinstance(s, str) for s in self.exclude
        ):
            raise ValueError("exclude must be a list of strings")
        if not isinstance(self.tools, dict):
            raise ValueError(f"tools must be a dict, got {type(self.tools).__name__}")
        if not isinstance(self.agent, AgentConfig):
            raise ValueError(
                f"agent must be an AgentConfig, got {type(self.agent).__name__}"
            )
        if not isinstance(self.resolve, ResolveConfig):
            raise ValueError(
                f"resolve must be a ResolveConfig, got {type(self.resolve).__name__}"
            )
        if not isinstance(self.menu, RunToolsConfig):
            raise ValueError(
                f"menu must be a RunToolsConfig, got {type(self.menu).__name__}"
            )


def _check_unknown_keys(section_name: str, raw: dict, allowed: set[str]) -> None:
    """Raise ValueError if *raw* contains any keys outside *allowed*.

    Single source for the ``"{section}: unknown keys: ..."`` message so all
    config sections stay consistent if the format ever evolves (e.g. to add
    typo hints).
    """
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{section_name}: unknown keys: {', '.join(sorted(unknown))}")


def _parse_file_type_tools(ext: str, raw: dict) -> FileTypeTools:
    """Parse tool definitions for a single file type from raw TOML data."""
    _check_unknown_keys(f"tools.{ext}", raw, set(TOOL_CATEGORIES))
    kwargs = {}
    for category in TOOL_CATEGORIES:
        if category in raw:
            entry = raw[category]
            if "command" not in entry:
                raise ValueError(
                    f"tools.{ext}.{category}: missing required key 'command'"
                )
            _check_unknown_keys(f"tools.{ext}.{category}", entry, _TOOL_ENTRY_FIELDS)
            # Build kwargs only from keys actually present so the dataclass
            # default is the single source of truth for omitted fields.
            kwargs[category] = ToolConfig(
                **{k: entry[k] for k in _TOOL_ENTRY_FIELDS if k in entry}
            )
    return FileTypeTools(**kwargs)


def _parse_run_tools_config(section_name: str, raw: dict) -> RunToolsConfig:
    """Parse a section containing per-tool booleans (e.g. ``[menu]``)."""
    _check_unknown_keys(section_name, raw, _RUN_TOOLS_FIELDS)
    for key, value in raw.items():
        if not isinstance(value, bool):
            raise ValueError(
                f"{section_name}.{key} must be a boolean, got {type(value).__name__}"
            )
    return RunToolsConfig(**raw)


_RESOLVE_FIELDS = {f.name for f in dataclass_fields(ResolveConfig)}


def _parse_resolve_config(raw: dict) -> ResolveConfig:
    """Parse the ``[resolve]`` section with ``auto`` and ``interactive`` sub-tables."""
    _check_unknown_keys("resolve", raw, _RESOLVE_FIELDS)
    auto = _parse_run_tools_config("resolve.auto", raw.get("auto", {}))
    interactive = _parse_run_tools_config(
        "resolve.interactive", raw.get("interactive", {})
    )
    return ResolveConfig(auto=auto, interactive=interactive)


def _parse_files_section(raw: dict) -> tuple[list[str], list[str]]:
    """Parse the ``[files]`` section into ``(include, exclude)`` lists."""
    _check_unknown_keys("files", raw, _FILES_FIELDS)
    include = raw.get("include", [])
    exclude = raw.get("exclude", [])
    return include, exclude


def _parse_agent_section(raw: dict) -> AgentConfig:
    """Parse the ``[agent]`` section, handling deprecated keys."""
    if "max_thinking_tokens" in raw:
        raise ConfigMigrationError(_MAX_THINKING_TOKENS_MIGRATION_MSG)
    _check_unknown_keys("agent", raw, _AGENT_FIELDS)
    return AgentConfig(**raw)


def _require_table(name: str, raw: object) -> dict:
    """Return *raw* as a dict or raise ValueError with a clear message."""
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a TOML table")
    return raw


def load_config(config_path: Path) -> Config:
    """Load and parse a TOML configuration file into a Config object."""
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    files_section = _require_table("files", raw.get("files", {}))
    tools_section = _require_table("tools", raw.get("tools", {}))
    agent_section = _require_table("agent", raw.get("agent", {}))
    resolve_section = _require_table("resolve", raw.get("resolve", {}))
    menu_section = _require_table("menu", raw.get("menu", {}))

    include, exclude = _parse_files_section(files_section)
    tools = {
        ext: _parse_file_type_tools(ext, _require_table(f"tools.{ext}", tool_defs))
        for ext, tool_defs in tools_section.items()
    }
    agent = _parse_agent_section(agent_section)
    resolve = _parse_resolve_config(resolve_section)
    menu = _parse_run_tools_config("menu", menu_section)

    return Config(
        include=include,
        exclude=exclude,
        tools=tools,
        agent=agent,
        resolve=resolve,
        menu=menu,
    )
