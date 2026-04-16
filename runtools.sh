#!/usr/bin/env bash
#
# Local quality-check runner: formatter, linter, type-checker, tests.
#
# Each tool runs even if a previous one fails (so a single invocation can
# surface issues from multiple tools at once).  The script exits non-zero
# when any tool fails, so CI-equivalent pre-push use is safe, and prints
# an explicit final banner — critical because the scripts' last visible
# output otherwise tends to be pytest's "N passed" line, which can
# visually mask an earlier type-check or lint failure.

fail=0

echo "Running formatter..."
uv run ruff format src tests || fail=$((fail | 1))

echo "Running linter..."
uv run ruff check --output-format full src tests || fail=$((fail | 2))

echo "Running type checker..."
uv run pyrefly check --output-format full-text src tests || fail=$((fail | 4))

echo "Running unit tests..."
uv run pytest --tb=short || fail=$((fail | 8))

echo
if [ "$fail" -eq 0 ]; then
    echo "✓ All checks passed."
else
    # Bit-mask lets the caller tell *which* stage failed without rerunning:
    # 1=formatter, 2=linter, 4=type-checker, 8=tests.
    echo "✗ One or more checks failed (mask=$fail: formatter=1, linter=2, typecheck=4, tests=8)." >&2
    exit 1
fi
