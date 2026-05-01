"""Finding data model and result file I/O for SQA Agent."""

import json
import logging
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Literal


logger = logging.getLogger("sqa-agent")

_RESULT_PREFIX = "result_"
_RESULT_FORMAT_VERSION = 1


@dataclass
class Finding:
    """A single finding produced by a tool or agent review.

    The ``id`` field defaults to 0 as an "unassigned" sentinel; the real
    sequential IDs are stamped onto each finding by :func:`assign_ids`
    (the authoritative source). Callers that produce findings in-flight
    can simply omit ``id`` and rely on :func:`assign_ids` to number them
    before persistence. ``id`` is declared last so the default stays
    compatible with dataclass field-ordering rules; all construction in
    this codebase uses keyword arguments, so position is not observable.

    ``source`` follows a two-prefix taxonomy:

    * ``"agent:<file>"`` — findings produced by an agent review. ``<file>``
      is either a project-relative file path (file-specific review) or the
      literal ``"general"`` for project-wide review prompts.
    * ``"<category>:<parser>"`` — findings produced by deterministic tools,
      built via :func:`sqa_agent.tools.make_source_id` (e.g. ``"lint:ruff"``,
      ``"typecheck:mypy"``). Callers should use that helper rather than
      hand-assembling the string to avoid typos.
    """

    source: str
    message: str
    file: str | None = None
    line: int | None = None
    severity: Literal["info", "warning", "error"] = "info"
    code: str | None = None
    status: Literal["open", "resolved"] = "open"
    triage: Literal["auto", "interactive", "ignore"] | None = None
    resolve_hint: str | None = None
    checklist_item: str | None = None
    id: int = 0


def assign_ids(findings: list[Finding]) -> None:
    """Assign sequential IDs (starting from 1) to a list of findings."""
    for i, finding in enumerate(findings, start=1):
        finding.id = i


def make_result_path(sqa_dir: Path) -> Path:
    """Create a datetime-stamped result file path for this review session."""
    # Second-level granularity is acceptable; sub-second collisions are not
    # expected in practice since reviews are human-initiated.
    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    return sqa_dir / f"{_RESULT_PREFIX}{timestamp}.json"


def write_result(result_path: Path, findings: list[Finding]) -> None:
    """Write findings to the result file, overwriting any previous content.

    ``result_path`` **must** have been produced by :func:`make_result_path`.
    The embedded timestamp is derived by stripping ``_RESULT_PREFIX`` from the
    file stem, so arbitrary filenames will produce a meaningless timestamp
    value.
    """
    if not result_path.stem.startswith(_RESULT_PREFIX):
        raise ValueError(
            f"result_path {result_path} was not produced by make_result_path"
        )
    timestamp = result_path.stem.removeprefix(_RESULT_PREFIX)

    payload = {
        "version": _RESULT_FORMAT_VERSION,
        "timestamp": timestamp,
        "total": len(findings),
        "findings": [asdict(f) for f in findings],
    }

    result_path.write_text(json.dumps(payload, indent=2) + "\n")


def load_result(result_path: Path) -> list[Finding]:
    """Read a result JSON file and return a list of Finding objects.

    Note: the ``version`` field in the payload is currently ignored.  If the
    result schema changes in a future version, this function will need to be
    updated to handle migration or to reject unsupported versions.

    Payload metadata (``version``, ``timestamp``, ``total``) is intentionally
    discarded — this API returns only the findings themselves. Consumers
    that need the metadata (e.g., to display "last reviewed at" or to gate
    on schema version) should read the JSON directly via :mod:`json`.
    """
    payload = json.loads(result_path.read_text())
    try:
        version = payload.get("version")
        if version != _RESULT_FORMAT_VERSION:
            logger.warning(
                "Unsupported result format version %s; attempting to read anyway",
                version,
            )
        known_keys = {f.name for f in fields(Finding)}
        allowed_severity = {"info", "warning", "error"}
        findings = []
        for entry in payload["findings"]:
            # Non-mutating filter:
            # * Strip unrecognised keys for forward-compatibility with newer
            #   result formats that may add fields this version doesn't know.
            # * Older result files may store severity as ``null``; drop it so
            #   the dataclass default applies instead of passing ``None``.
            filtered = {
                k: v
                for k, v in entry.items()
                if k in known_keys and not (k == "severity" and v is None)
            }
            # Minimal defensive validation: if severity isn't one of the
            # Literal members, warn and drop so the Finding default applies.
            severity = filtered.get("severity")
            if severity is not None and severity not in allowed_severity:
                logger.warning(
                    "Ignoring unknown severity %r in result file; using default",
                    severity,
                )
                filtered = {k: v for k, v in filtered.items() if k != "severity"}
            findings.append(Finding(**filtered))
    except (KeyError, TypeError) as e:
        raise ValueError(f"Malformed result file {result_path}: {e}") from e
    return findings
