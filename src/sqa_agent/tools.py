"""Tool execution and output parsing for SQA Agent."""

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Literal, cast

from sqa_agent.config import Config, ToolConfig
from sqa_agent.findings import Finding

logger = logging.getLogger("sqa-agent")

# ESLint severity level constants
_ESLINT_SEVERITY_ERROR = 2
_ESLINT_SEVERITY_WARNING = 1

# Finding ID placeholder — matches ``Finding.id``'s default sentinel (0).
# Actual sequential IDs are assigned by :func:`findings.assign_ids` once
# all findings for a run have been collected; that is the authoritative
# source. Parsers pass this constant explicitly for clarity even though
# ``id`` can now be omitted entirely.
UNASSIGNED_ID = 0

# Default timeout for tool commands (in seconds).
_DEFAULT_TIMEOUT = 300  # 5 minutes


class ToolExecutionError(Exception):
    """Raised when a tool command fails to start or exceeds its timeout.

    Typical causes include the tool binary not being installed
    (:class:`FileNotFoundError`), lacking execute permissions
    (:class:`PermissionError`), or the command running longer than the
    allowed timeout (:class:`subprocess.TimeoutExpired`).

    Also raised from :func:`run_tool` as a configuration error when a
    :class:`~sqa_agent.config.ToolConfig` references an unknown parser
    name (i.e. one not present in :data:`PARSERS`). A single exception
    type covers both subprocess-start failures and this config case to
    keep callers simple; the message distinguishes them.
    """


@dataclass
class ToolResult:
    """Raw result from running a tool command."""

    exit_code: int
    stdout: str
    stderr: str


def _run_command(command: str, timeout: int = _DEFAULT_TIMEOUT) -> ToolResult:
    """Execute a command and capture its output.

    The command string is split into an argument list via :func:`shlex.split`
    and executed *without* a shell, avoiding shell-injection risks.

    A *timeout* (default 5 minutes) prevents a misbehaving tool from blocking
    the review process indefinitely.
    """
    # Explicit empty-argv guard so the contract ("all start-failures
    # surface as ``ToolExecutionError``") holds even for empty or
    # whitespace-only commands — otherwise ``subprocess.run([])`` would
    # raise a bare ``ValueError: Empty args`` that callers don't catch.
    argv = shlex.split(command)
    if not argv:
        raise ToolExecutionError(f"Empty command: {command!r}")
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolExecutionError(
            f"Command timed out after {timeout}s: {command}"
        ) from exc
    except FileNotFoundError as exc:
        raise ToolExecutionError(
            f"Tool binary not found for command: {command}"
        ) from exc
    except PermissionError as exc:
        raise ToolExecutionError(
            f"Permission denied when running command: {command}"
        ) from exc
    except OSError as exc:
        # Fallthrough for other ``OSError`` subclasses (``NotADirectoryError``,
        # ``IsADirectoryError``, and so on). Preserves the documented
        # contract that all start-failures surface as ``ToolExecutionError``
        # rather than leaking a raw ``OSError`` from ``subprocess``.
        raise ToolExecutionError(
            f"Failed to execute command: {command} ({type(exc).__name__}: {exc})"
        ) from exc
    return ToolResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


# ---------------------------------------------------------------------------
# Parsers: each takes a ToolResult and returns a list of (un-numbered) Findings.
# The 'source' field is filled in by the caller.
# ---------------------------------------------------------------------------


def parse_ruff(result: ToolResult, source: str) -> list[Finding]:
    """Parse JSON output from `ruff check --output-format json`."""
    if not result.stdout.strip():
        return []

    # NOTE: intentional inline try/except — each parser's post-parse logic
    # differs enough that a shared helper adds indirection without real benefit.
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse ruff JSON output; storing as raw.")
        return parse_raw(result, source)

    findings = []
    for entry in entries:
        location = entry.get("location", {})
        findings.append(
            Finding(
                id=UNASSIGNED_ID,
                source=source,
                file=entry.get("filename"),
                line=location.get("row"),
                code=entry.get("code"),
                message=entry.get("message") or entry.get("code") or "<no message>",
            )
        )
    return findings


_MYPY_SEVERITY_MAP: dict[str, Literal["error", "info", "warning"]] = {
    "error": "error",
    "warning": "warning",
    "note": "info",
}


def parse_mypy(result: ToolResult, source: str) -> list[Finding]:
    """Parse JSON output from `mypy --output json`."""
    if not result.stdout.strip():
        return []

    all_lines = result.stdout.strip().splitlines()
    findings = []
    failed_lines = 0
    for line in all_lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping unparseable mypy JSONL line: %s", line)
            failed_lines += 1
            continue
        findings.append(
            Finding(
                id=UNASSIGNED_ID,
                source=source,
                file=entry.get("file"),
                line=entry.get("line"),
                code=entry.get("code"),
                severity=_MYPY_SEVERITY_MAP.get(entry.get("severity"), "info"),
                message=entry.get("message") or entry.get("code") or "<no message>",
            )
        )
    if not findings:
        logger.warning("Failed to parse any mypy JSONL lines; storing as raw.")
        return parse_raw(result, source)
    # Partial-corruption signal: if at least half the lines failed to
    # parse, surface it — the user is likely looking at a format drift
    # (mypy started interleaving text) and a quietly-truncated result
    # is worse than a loud warning.
    if failed_lines > 0 and failed_lines >= len(all_lines) // 2:
        logger.warning(
            "mypy: %d of %d JSONL lines failed to parse; findings may be incomplete.",
            failed_lines,
            len(all_lines),
        )
    return findings


def parse_pyrefly(result: ToolResult, source: str) -> list[Finding]:
    """Parse JSON output from `pyrefly check --output-format json`."""
    if not result.stdout.strip():
        return []

    # NOTE: intentional inline try/except (see parse_ruff for rationale).
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse pyrefly JSON output; storing as raw.")
        return parse_raw(result, source)

    # Shape-check: pyrefly may change its output format (or ``--output-format``
    # might be missing) and emit a list/primitive. Fall back to raw so we
    # don't crash with ``AttributeError`` on ``data.get``.
    if not isinstance(data, dict):
        logger.warning(
            "Pyrefly JSON output was not a dict (got %s); storing as raw.",
            type(data).__name__,
        )
        return parse_raw(result, source)

    findings = []
    for entry in data.get("errors", []):
        findings.append(
            Finding(
                id=UNASSIGNED_ID,
                source=source,
                file=entry.get("path"),
                line=entry.get("line"),
                code=entry.get("name"),
                message=entry.get("description") or entry.get("name") or "<no message>",
            )
        )
    return findings


def parse_eslint(result: ToolResult, source: str) -> list[Finding]:
    """Parse JSON output from `eslint --format json`."""
    if not result.stdout.strip():
        return []

    # NOTE: intentional inline try/except (see parse_ruff for rationale).
    try:
        file_entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse eslint JSON output; storing as raw.")
        return parse_raw(result, source)

    # Shape-check: eslint's top-level JSON is always a list of file
    # entries. Anything else (a dict wrapping them, a primitive) means
    # the format has changed — fall back to raw rather than crashing in
    # the iteration below.
    if not isinstance(file_entries, list):
        logger.warning(
            "ESLint JSON output was not a list (got %s); storing as raw.",
            type(file_entries).__name__,
        )
        return parse_raw(result, source)

    severity_map: dict[int, Literal["error", "info", "warning"]] = {
        _ESLINT_SEVERITY_ERROR: "error",
        _ESLINT_SEVERITY_WARNING: "warning",
    }
    findings = []
    for file_entry in file_entries:
        file_path = file_entry.get("filePath")
        for msg in file_entry.get("messages", []):
            findings.append(
                Finding(
                    id=UNASSIGNED_ID,
                    source=source,
                    file=file_path,
                    line=msg.get("line"),
                    code=msg.get("ruleId"),
                    severity=severity_map.get(msg.get("severity"), "info"),
                    message=msg.get("message") or msg.get("ruleId") or "<no message>",
                )
            )
    return findings


_TSC_LINE_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),\d+\): (?P<severity>error|warning) (?P<code>TS\d+): (?P<message>.+)$"
)


def parse_tsc(result: ToolResult, source: str) -> list[Finding]:
    """Parse output from `tsc --noEmit --pretty false`."""
    if not result.stdout.strip():
        return []

    findings = []
    for line in result.stdout.splitlines():
        m = _TSC_LINE_RE.match(line)
        if not m:
            continue
        findings.append(
            Finding(
                id=UNASSIGNED_ID,
                source=source,
                file=m.group("file"),
                line=int(m.group("line")),
                code=m.group("code"),
                severity=cast(Literal["error", "info", "warning"], m.group("severity")),
                message=m.group("message"),
            )
        )
    return findings


def _combined_output(result: ToolResult) -> str:
    """Join stdout and stderr with a newline, skipping empty streams.

    Single-sources the composition used by fallback parsers that treat
    a tool's full output as one blob, so any future prefixing (e.g.
    ``"[stderr] "``) only has to change here.
    """
    return "\n".join(filter(None, [result.stdout, result.stderr]))


def parse_ruff_format(result: ToolResult, source: str) -> list[Finding]:
    """Parse output from `ruff format --check`.

    ruff format --check prints ``Would reformat: <path>`` on stderr for each
    file that needs reformatting and exits with code 1.
    """
    if result.exit_code == 0:
        return []

    findings = []
    for line in _combined_output(result).splitlines():
        line = line.strip()
        if line.startswith("Would reformat:"):
            path = line.removeprefix("Would reformat:").strip()
            findings.append(
                Finding(
                    id=UNASSIGNED_ID,
                    source=source,
                    file=path,
                    message="File needs reformatting",
                )
            )
    if not findings:
        return parse_raw(result, source)
    return findings


def parse_pytest(result: ToolResult, source: str) -> list[Finding]:
    """Parse pytest output. Falls back to raw since pytest structured output
    requires a plugin. Treats non-zero exit as a single bulk finding."""
    if result.exit_code == 0:
        return []
    return parse_raw(result, source)


def parse_raw(result: ToolResult, source: str) -> list[Finding]:
    """Fallback parser: store the full output as a single finding."""
    output = _combined_output(result).strip()
    if not output:
        return []
    return [
        Finding(
            id=UNASSIGNED_ID,
            source=source,
            message=output,
        )
    ]


# Registry mapping parser names to functions.
# IMPORTANT: Parser names must match those used in ToolConfig.
PARSERS = {
    "ruff": parse_ruff,
    "ruff_format": parse_ruff_format,
    "mypy": parse_mypy,
    "pyrefly": parse_pyrefly,
    "eslint": parse_eslint,
    "tsc": parse_tsc,
    "pytest": parse_pytest,
    "raw": parse_raw,
}


def run_formatters(config: Config) -> None:
    """Run all configured formatter tools for their side effects.

    Best-effort: ``ToolExecutionError`` from any individual formatter is
    caught and logged as a warning so the session continues. Contrast
    with :func:`run_tool`, which lets the exception propagate — formatters
    are a cosmetic pre-pass, so one failing formatter shouldn't block the
    whole review.
    """
    if not config.tools:
        return
    for ext, file_type_tools in config.tools.items():
        tool_cfg = file_type_tools.formatter
        if tool_cfg is None:
            continue
        try:
            logger.debug(f"Running formatter (.{ext}): {tool_cfg.command}")
            result = _run_command(tool_cfg.command)
        except ToolExecutionError as e:
            logger.warning(f"Formatter (.{ext}) failed: {e}")
            continue
        # Most formatters return 0 even when they reformat files, but
        # some (e.g. ``prettier --check``) signal "would change" via a
        # non-zero exit. Surface it so operators aren't surprised by a
        # silent formatter failure during interactive resolve.
        if result.exit_code != 0:
            stderr_snippet = result.stderr.strip().splitlines()[:3]
            detail = f" stderr: {' | '.join(stderr_snippet)}" if stderr_snippet else ""
            logger.warning(
                f"Formatter (.{ext}) exited with code {result.exit_code}.{detail}"
            )


def make_source_id(category: str, parser: str) -> str:
    """Build the source identifier used in findings from **deterministic tools**.

    Format: ``"<category>:<parser>"``, e.g. ``"lint:ruff"``.

    * *category* – the logical group the tool belongs to (e.g. ``"lint"``,
      ``"typecheck"``, ``"test"``).
    * *parser* – the parser name used to interpret the tool's output.

    Tool-specific scope only — agent reviews use a separate
    ``"agent:<file>"`` convention built manually in
    :mod:`sqa_agent.agent_common` (see the ``Finding.source`` docstring
    in :mod:`sqa_agent.findings` for the full taxonomy). The two formats
    share only the ``<kind>:<detail>`` shape; they are intentionally not
    single-sourced.
    """
    return f"{category}:{parser}"


def run_tool(category: str, tool_config: ToolConfig) -> list[Finding]:
    """Run a single tool and return parsed findings.

    Raises :class:`ToolExecutionError` on command failure (missing binary,
    permission denied, timeout) or an unknown parser. Callers that want
    best-effort semantics (don't abort the run on one misconfigured tool)
    must catch it themselves — see :func:`run_formatters` for the
    opposite convention.
    """
    source = make_source_id(category, tool_config.parser)

    parser_fn = PARSERS.get(tool_config.parser)
    if parser_fn is None:
        raise ToolExecutionError(
            f"Unknown parser '{tool_config.parser}'. "
            f"Available parsers: {', '.join(sorted(PARSERS))}"
        )

    logger.debug(f"Running {category} ({tool_config.parser}): {tool_config.command}")
    result = _run_command(tool_config.command)

    findings = parser_fn(result, source)

    if result.exit_code != 0 and not findings:
        logger.warning(
            f"{category} exited with code {result.exit_code} but produced no findings."
        )
        if result.stderr.strip():
            logger.warning(f"stderr: {result.stderr.strip()}")

    return findings
