"""Review orchestration — file-specific and general review functions."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient

from sqa_agent.agent_common import (
    ReviewStats,
    build_review_options,
    build_section_prompt,
    create_client,
    log_section_cumulative,
    send_prompt_and_collect,
    setup_file_context,
)
from sqa_agent.config import AgentConfig
from sqa_agent.findings import Finding
from sqa_agent.prompts import Section

logger = logging.getLogger("sqa-agent")


def _batch_session_id(agent_label: str | None) -> str:
    """Return the session id used for a file-review batch.

    Single source of truth for the format so producers and any future
    consumers agree on the key used in :class:`ReviewStats` trackers.
    """
    return f"batch:{agent_label or ''}"


async def _run_sections(
    client: ClaudeSDKClient,
    sections: list[Section],
    source: str,
    stats: ReviewStats,
    session_id: str,
    *,
    file_path: str | None = None,
    agent_label: str | None = None,
    on_section_findings: Callable[[list[Finding]], None] | None = None,
) -> list[Finding]:
    """Execute *sections* sequentially and return the collected findings.

    Centralises the build-prompt → send → collect → log loop used by both
    ``review_file_queue`` and ``review_general``.
    """
    all_findings: list[Finding] = []
    for section in sections:
        if file_path is not None:
            prompt = build_section_prompt(section, file_path)
        else:
            prompt = build_section_prompt(section)
        checklist_item = f"{section.label} {section.title}"
        findings = await send_prompt_and_collect(
            client,
            prompt,
            source,
            stats,
            session_id=session_id,
            checklist_item=checklist_item,
        )
        all_findings.extend(findings)
        if on_section_findings:
            on_section_findings(findings)
        log_section_cumulative(section, len(findings), stats, agent_label)
    return all_findings


async def review_file_queue(
    sections: list[Section],
    queue: asyncio.Queue[str],
    agent_config: AgentConfig,
    project_root: Path,
    stats: ReviewStats,
    on_file_start: Callable[[str], None] | None = None,
    on_file_complete: Callable[[str, list[Finding]], None] | None = None,
    agent_label: str | None = None,
) -> list[Finding]:
    """Review files pulled on-demand from a shared *queue*.

    A single ``ClaudeSDKClient`` session is kept open for all files pulled
    from *queue*, so subsequent files benefit from prompt caching — the
    system prompt and any previously-read imports are already in context
    as cache hits. Pulling from a shared :class:`asyncio.Queue` lets
    multiple concurrent workers balance load dynamically when files have
    varying review times.

    Queue contract: the caller must fully enqueue all files before
    starting workers. Workers use ``queue.get_nowait()`` and exit the
    moment it raises :class:`asyncio.QueueEmpty`, so a producer still
    feeding the queue while workers drain would see workers exit early.
    For a producer/consumer pattern, switch to ``queue.get()`` with a
    sentinel for graceful shutdown.

    Findings are buffered per-file and only reported via *on_file_complete*
    once all section prompts for that file complete successfully — i.e. one
    call per file, not per section.

    Error handling: this function does not recover from exceptions. If
    ``_run_sections`` raises a ``FatalAPIError`` (or any other exception)
    mid-review, the exception propagates out of ``review_file_queue``; the
    current file's partial findings are discarded, its *on_file_complete*
    does not fire, and any files still in the queue are left unreviewed.
    Retry or recovery — e.g., opening a fresh session for the remainder —
    is the caller's responsibility.

    *on_file_start* is called when a file is about to be reviewed. Note
    that an exception may prevent the corresponding *on_file_complete*
    from firing — callers must not assume the two are always paired.

    *on_file_complete* is called with ``(file_path, file_findings)`` after
    each file finishes successfully, allowing the caller to persist results
    and mark files as reviewed.
    """
    if not sections:
        return []

    options = build_review_options(agent_config, project_root)

    all_findings: list[Finding] = []
    prefix = f"{agent_label} " if agent_label else ""
    session_id = _batch_session_id(agent_label)

    async with create_client(options) as client:
        session_started = False
        while True:
            try:
                file_under_review = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if not session_started:
                # session_id convention: 'batch:<agent_label>' for file
                # reviews, 'general' for review_general.
                stats.start_session(session_id)
                session_started = True

            if on_file_start:
                on_file_start(file_under_review)

            source = await setup_file_context(
                client,
                file_under_review,
                stats,
                session_id=session_id,
                log_prefix=prefix,
            )

            file_findings = await _run_sections(
                client,
                sections,
                source,
                stats,
                session_id,
                file_path=file_under_review,
                agent_label=agent_label,
            )

            all_findings.extend(file_findings)
            if on_file_complete:
                on_file_complete(file_under_review, file_findings)

    return all_findings


async def review_general(
    sections: list[Section],
    agent_config: AgentConfig,
    project_root: Path,
    stats: ReviewStats,
    on_section_findings: Callable[[list[Finding]], None] | None = None,
) -> list[Finding]:
    """Run general (non-file-specific) review prompts."""
    if not sections:
        return []

    options = build_review_options(agent_config, project_root)
    session_id = "general"

    async with create_client(options) as client:
        # session_id convention: 'batch:<agent_label>' for file reviews,
        # 'general' for review_general.
        stats.start_session(session_id)
        all_findings = await _run_sections(
            client,
            sections,
            "agent:general",
            stats,
            session_id,
            on_section_findings=on_section_findings,
        )

    return all_findings
