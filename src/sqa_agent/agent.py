"""Agent-powered code review using the Claude Agent SDK.

This module re-exports the public API from the three sub-modules that
implement the agent subsystem:

- ``agent_common``: shared constants, types, and helpers.
- ``agent_review``: review orchestration (file and general reviews).
- ``agent_resolve``: autonomous and interactive resolve functions.
"""

# --- Common: types and helpers ---
from sqa_agent.agent_common import (  # noqa: F401
    FatalAPIError,
    ResolveResult,
    ReviewStats,
    format_duration,
)

# --- Review orchestration ---
from sqa_agent.agent_review import (  # noqa: F401
    review_file_queue,
    review_general,
)

# --- Resolve (autonomous + interactive) ---
from sqa_agent.agent_resolve import (  # noqa: F401
    interactive_resolve_finding,
    interactive_resolve_findings,
    resolve_findings,
)

__all__ = [
    "FatalAPIError",
    "ResolveResult",
    "ReviewStats",
    "format_duration",
    "interactive_resolve_finding",
    "interactive_resolve_findings",
    "resolve_findings",
    "review_file_queue",
    "review_general",
]
