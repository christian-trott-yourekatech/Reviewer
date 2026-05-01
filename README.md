# SQA Agent

An AI-powered software quality assurance agent built on the Claude Agent SDK. SQA Agent performs structured, automated code quality analysis on software projects.

## Project Structure

```
src/sqa_agent/
  agent.py         # Re-export surface for the agent subsystem
  agent_common.py  # Shared agent helpers (session setup, tool config, prompts)
  agent_resolve.py # Auto-resolve and interactive-resolve logic
  agent_review.py  # File review and general review orchestration
  cli.py           # CLI entry point and interactive menu
  config.py        # Configuration loading and validation
  file_status.py   # Git hash tracking and change detection
  findings.py      # Finding data model and result file I/O
  git_ops.py       # Git helpers for the interactive-resolve UI
  prompts.py       # Markdown prompt parsing
  tools.py         # Tool execution and output parsing
  ui.py            # Rich-based UI (prompts, menus, status display)
  prompts/         # Default review prompt templates
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A git repository (for file change detection)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated (for agent review)

### Authentication

The agent review phase uses the Claude Agent SDK, which requires the Claude Code CLI. You have two options:

1. **Claude Code CLI** (recommended): Install Claude Code and run `claude` once to authenticate interactively. The SDK will use the CLI's session credentials.
2. **API key**: Set the `ANTHROPIC_API_KEY` environment variable with your Anthropic API key.

Deterministic tools (formatter, linter, type checker, tests) do not require authentication and will run regardless.

## Installation

Install with `uv tool install` directly:

```bash
uv tool install --with "sqa-agent[all]" git+https://github.com/christian-trott-yourekatech/Reviewer.git
```

Or use the bundled install script (a thin wrapper around the same command):

```bash
curl -fsSL https://raw.githubusercontent.com/christian-trott-yourekatech/Reviewer/main/install.sh | bash
```

The `[all]` extra pulls in optional dependencies the agent may invoke during review (`ruff`, `mypy`, `pyrefly`, `pytest`). Omit it to install only the core package.

### Upgrading

Once a project has been initialized (see [Initialize a project](#initialize-a-project) below), the generated `./review` launcher accepts an `--upgrade` flag that reinstalls the latest published version:

```bash
./review --upgrade
```

You can also re-run `install.sh` or the `uv tool install` command above (both use `--reinstall`).

### Per-project artifacts

`sqa-agent init` creates a `.sqa-agent/` directory in your project for review state and logs. We recommend adding the following to your `.gitignore`:

```
.sqa-agent/logs/
.sqa-agent/result*.json
```

The result files contain finding messages that may quote source code; the logs are timestamped DEBUG-level traces useful for local debugging but not worth committing.

## Usage

### Initialize a project

From the root of the project you want to analyze, run:

```bash
uv run review init
```

This creates a `.sqa-agent/` directory containing:

- **`config.toml`** - Configuration controlling how the agent analyzes your project.
- **`file_status.json`** - Tracks git blob hashes of reviewed files to detect changes.
- **`prompts/`** - Review prompt templates (customize these for your project).

### Configure tools and agent

Edit `.sqa-agent/config.toml` to define which files to review, which tools to run, and agent settings.

```toml
[files]
include = ["src/**/*.py"]
exclude = ["src/**/*_test.py"]

[tools.py.formatter]
command = "uv run ruff format src"
parser = "ruff_format"

[tools.py.linter]
command = "uv run ruff check --output-format json src"
parser = "ruff"

[tools.py.type_checker]
command = "uv run pyrefly check --output-format json src"
parser = "pyrefly"

# Alternative: mypy
# command = "uv run mypy --output json src"
# parser = "mypy"

[tools.py.test]
command = "uv run pytest --tb=short"
parser = "pytest"

# --- TypeScript ---
# [tools.ts.linter]
# command = "npx eslint --format json ."
# parser = "eslint"
#
# [tools.ts.type_checker]
# command = "npx tsc --noEmit --pretty false"
# parser = "tsc"

[agent]
review_model  = "claude-opus-4-7"
resolve_model = "claude-opus-4-7"
thinking      = "adaptive"    # "adaptive" or "disabled"
effort        = "xhigh"       # low | medium | high | xhigh | max

# Which deterministic tools to re-run as verification after the agent
# resolves a finding. Each flag defaults to false (no verification).
[resolve.auto]
formatter    = true
linter       = true
type_checker = true
test         = false

[resolve.interactive]
formatter    = true
linter       = false
type_checker = false
test         = false

# Which tools the interactive menu should health-check on every refresh.
# Defaults to false; enable per category to surface failures up-front.
[menu]
formatter    = false
linter       = true
type_checker = true
test         = false
```

The 1M-token context window is built in to Opus 4.7 and Sonnet 4.6, so no
configuration knob is required to enable it.

Tool categories: `formatter`, `linter`, `type_checker`, `test`.

Available parsers: `ruff`, `ruff_format`, `mypy`, `pyrefly`, `eslint`, `tsc`, `pytest`, `raw` (fallback that stores full output as a single finding).

### Commands

Running `uv run review` with no subcommand opens an interactive menu. The available commands are:

| Command | Description |
|---------|-------------|
| `init` | Initialize a new `.sqa-agent/` directory |
| `review` | Run deterministic tools and AI-powered code analysis |
| `triage` | Walk through findings and assign triage decisions |
| `auto-resolve` | Autonomously resolve findings triaged as "auto" |
| `interactive-resolve` | Interactively resolve findings triaged as "interactive" |
| `check` | Run deterministic code-quality tools (with a sub-menu for selection) |
| `commit` | Stage tracked-file changes (`git add -u`) and commit with a prompted message |
| `reset` | Mark all in-scope files as reviewed at their current state |

### Workflow

The typical workflow is: **review → triage → resolve**.

#### 1. Review

```bash
uv run review review
```

A review has three phases:

1. **Deterministic tools** run project-wide (formatter, linter, type checker, tests).

2. **Change detection** identifies which files need review. The `[files] include` globs are intersected with the list of git-tracked files (`git ls-files`), so untracked files (build artifacts, generated code, etc.) are never reviewed. The resulting candidates are then compared against `file_status.json` by git blob hash — only files that are new or changed since their last review are flagged. Renamed files are detected (when unambiguous) and entries for deleted files are pruned automatically.

3. **Agent review** uses the Claude Agent SDK to review each file that needs attention. For each file, a stateful `ClaudeSDKClient` session is created:
   - The agent first reads and understands the file.
   - Then each review prompt (from `file_review_prompts.md`) is sent as a follow-up message in the same conversation.
   - This enables **prompt caching** — the system prompt and accumulated context are reused across prompts, significantly reducing costs.
   - The agent has read-only access to the codebase (Read, Grep, Glob) but cannot edit files.
   - Findings are reported as structured JSON and collected into the result file.
   - Multiple files can be reviewed concurrently (you are prompted for the concurrency level).

   After all prompts complete for a file, its git hash is recorded so it won't be re-reviewed until it changes.

All findings (from both deterministic tools and agent review) are written to a single datetime-stamped result file. A cost summary is printed at the end.

#### 2. Triage

```bash
uv run review triage
```

Triage walks through each finding and lets you assign a disposition:

- **`auto`** — Can be resolved autonomously by the agent. You may optionally provide a resolve hint with guidance for the agent.
- **`interactive`** — Requires a human-in-the-loop conversation with the agent to resolve.
- **`ignore`** — Not worth fixing; skip this finding.

Findings start as **untriaged** until you assign a decision.

#### 3. Resolve

```bash
uv run review auto-resolve
uv run review interactive-resolve
```

- **Auto-resolve** feeds each "auto"-triaged finding (along with any resolve hint) to the agent, which edits the codebase autonomously. After the pass, tracked-file changes are staged with `git add -u` and committed automatically.
- **Interactive-resolve** opens a multi-turn conversation with the agent for each "interactive"-triaged finding. Inside a session you can:
  - `/resolve` — mark the finding resolved and move to the next.
  - `/skip` — leave it open and move on.
  - `/quit` — stop the resolve pass.
  - `/diff` — show the current unstaged diff.
  - `/commit` — stage **all** changes (`git add .`, including untracked files) and commit with a prompted message. Confirmation is required so a stray secret file can be aborted before it lands.
  - `/help` — show this list.

  Note that the in-session `/commit` and the post-pass automatic commit use deliberately different staging scopes (`git add .` vs `git add -u`). Interactive sessions routinely produce new files (a helper module, a new test) that the broader scope captures; the post-pass commit is more conservative to avoid sweeping in untracked artefacts.

### Customize prompts

The prompt files in `.sqa-agent/prompts/` use standard markdown. Each heading (`#`, `##`, etc.) defines a review section. The body text below a heading is the prompt sent to the agent. Empty sections (headings with no body) are skipped.

- `general_review_prompts.md` - Run once per review session (project-wide concerns).
- `file_review_prompts.md` - Run per file. Each section becomes a separate agent call.

You can add, remove, or edit sections to tailor the review to your project's needs.

> **Note on the two prompts directories.** `src/sqa_agent/prompts/` ships with
> the package as bundled defaults — these are copied into your project's
> `.sqa-agent/prompts/` on `sqa-agent init`. `.sqa-agent/prompts/` is the
> per-project editable copy that the agent actually reads at runtime. In this
> repository specifically, `.sqa-agent/prompts/` is also the customized copy
> used to self-review the tool, so it may diverge from the bundled defaults.

## Development

See [TODO.md](./TODO.md) for open work.

Install dependencies (including dev/CI tooling):

```bash
uv sync --all-extras
```

Run the full quality-check pass (formatter, linter, type-checker, tests) —
use this before committing or opening a PR:

```bash
./runtools.sh
```

To run individual tools:

```bash
uv run ruff format src tests       # format
uv run ruff check src tests        # lint
uv run pyrefly check src tests     # type-check
uv run pytest tests/ -v            # tests
```

`./runtools.sh` runs each tool sequentially and prints a per-tool pass/fail banner at the end so you can see at a glance which steps need attention.

## License

SQA Agent is released under the MIT License — see [LICENSE](./LICENSE) for the full text.
