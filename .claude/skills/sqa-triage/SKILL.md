---
name: sqa-triage
description: Triage sqa-agent code review findings into auto-resolve, interactive, or ignore categories. Use when the user asks to triage, review, or process sqa-agent results, or after running the sqa-agent review tool.
---

# SQA Triage

Triage sqa-agent review findings by reading each finding, evaluating it against the triage guidelines, and writing a triage decision back to the result file.

## When to Use

- When the user asks to triage, classify, or process review findings

## Result File Format

Result files are stored in `.sqa-agent/` with names like `result_YYYY_MM_DD_HHMMSS.json`. The structure is:

```json
{
  "findings": [
    {
      "id": 1,
      "file": "path/to/file.ts",
      "line": 42,
      "severity": "error|warning|info",
      "checklist_item": "1.3 Logic+Consistency",
      "source": "agent|tsc|eslint",
      "message": "Detailed description of the issue...",
      "triage": null,
      "resolve_hint": null
    }
  ]
}
```

## Triage Values

Each finding receives one of three triage values:

- **`"auto"`** — The fix is obvious, low-risk, and can be done without discussion. Must include a `resolve_hint` string explaining how to fix it.
- **`"interactive"`** — The finding is valid but needs discussion before acting. The fix may have multiple approaches, touch sensitive code, involve significant scope, or have architecture/UX implications.
- **`"ignore"`** — The finding is genuinely not applicable to this project.

See `triage-guidelines.md` in this skill directory for detailed criteria on how to categorize findings.

## Procedure

### Step 1: Locate the result file

Find the most recent result file in `.sqa-agent/`. If multiple exist, use the one the user specifies or the most recent by timestamp.

### Step 2: Read and understand findings

Read the full result file. For each finding, understand:
- What file and line it refers to
- What the reviewer is flagging
- The severity assigned by the reviewer
- Which checklist item it falls under

Read the actual source files as needed to understand context. For findings about interfaces, callers, or SSOT, read the relevant consuming/producing code too.

### Step 3: Apply triage guidelines

For each finding, apply the criteria in `triage-guidelines.md` to assign a triage value. Key principles:
- Read the guidelines file for the full decision criteria
- Consider project-specific context (date philosophy, product scope)
- **Difficulty is not a reason to ignore.** Complex but valuable fixes should be interactive.
- When in doubt between auto and interactive, choose interactive
- When in doubt between ignore and interactive, choose interactive

### Step 4: Write decisions to the result file

Write a script that applies all triage decisions to the result file. For each finding:
- Set `"triage"` to `"auto"`, `"interactive"`, or `"ignore"`
- For auto findings, set `"resolve_hint"` to a specific, actionable description of the fix
- For ignore findings, set `"resolve_hint"` to an explanation of why the issue was set to ignore.
- For interactive findings, set `"resolve_hint"` to an explanation of why the issue was set to interactive.

### Step 5: Produce a summary

After triaging, report:
- Count of auto / interactive / ignore findings
- The list of interactive findings with their IDs, files, and a brief description of why they need discussion
- Any themes or patterns observed across the findings

## Tips

- Process findings in file order (grouped by source file) to maintain context
- When the same issue appears in multiple files, triage the primary instance and mark duplicates as ignore with a note
- Auto resolve hints should be specific enough that a resolver agent can act on them without further clarification
