"""Prompt parsing for SQA Agent.

Parses structured review prompts from markdown files. Each markdown heading
becomes a Section whose prompt_text is the content between that heading and
the next. Empty sections (headings with no body) are skipped.
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Section:
    """A single review prompt extracted from a markdown file."""

    label: str  # Hierarchical label, e.g. "1.2", "2.1.3"
    title: str  # The heading text, e.g. "DRY Check"
    full_title: str  # Breadcrumb path, e.g. "General Coding Principles > DRY Check"
    prompt_text: str  # The body text below the heading


_MAX_HEADING_DEPTH = 6  # Markdown supports h1–h6
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def parse_prompt_file(filepath: Path) -> list[Section]:
    """Parse a markdown file into a list of Sections.

    Headings are numbered hierarchically based on depth:
      # Heading      -> 1, 2, 3, ...
      ## Subheading  -> 1.1, 1.2, 2.1, ...
      ### Sub-sub    -> 1.1.1, 1.1.2, ...

    Sections whose body text is empty are omitted.
    """
    if not filepath.exists():
        return []

    lines = filepath.read_text().split("\n")

    # First pass: locate all headings with their line index, level, and title.
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))

    if not headings:
        return []

    # Second pass: build sections with hierarchical labels.
    counters = [0] * _MAX_HEADING_DEPTH
    title_stack = [""] * _MAX_HEADING_DEPTH
    sections: list[Section] = []

    for idx, (line_idx, level, title) in enumerate(headings):
        # Increment counter at this level, reset deeper levels.
        counters[level - 1] += 1
        title_stack[level - 1] = title
        for j in range(level, len(counters)):
            counters[j] = 0
            title_stack[j] = ""

        label = ".".join(str(counters[k]) for k in range(level))
        full_title = " > ".join(title_stack[k] for k in range(level) if title_stack[k])

        # Extract body text between this heading and the next.
        start = line_idx + 1
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        prompt_text = "\n".join(lines[start:end]).strip()

        if prompt_text:
            sections.append(
                Section(
                    label=label,
                    title=title,
                    full_title=full_title,
                    prompt_text=prompt_text,
                )
            )

    return sections


def load_general_prompts(prompts_dir: Path) -> list[Section]:
    """Load the general (once-per-review) prompt sections."""
    return parse_prompt_file(prompts_dir / "general_review_prompts.md")


def load_file_prompts(prompts_dir: Path) -> list[Section]:
    """Load the per-file prompt sections."""
    return parse_prompt_file(prompts_dir / "file_review_prompts.md")
