"""Tests for tool output parsers."""

import json

from sqa_agent.tools import ToolResult, parse_eslint, parse_ruff_format, parse_tsc


# ---------------------------------------------------------------------------
# parse_eslint
# ---------------------------------------------------------------------------

SOURCE = "linter:eslint"


def test_eslint_empty_stdout():
    result = ToolResult(exit_code=0, stdout="", stderr="")
    assert parse_eslint(result, SOURCE) == []


def test_eslint_valid_json():
    data = [
        {
            "filePath": "src/app.ts",
            "messages": [
                {
                    "ruleId": "no-unused-vars",
                    "severity": 1,
                    "message": "'x' is declared but never used.",
                    "line": 5,
                },
                {
                    "ruleId": "no-console",
                    "severity": 2,
                    "message": "Unexpected console statement.",
                    "line": 12,
                },
            ],
        },
        {
            "filePath": "src/utils.ts",
            "messages": [
                {
                    "ruleId": None,
                    "severity": 3,
                    "message": "Parsing error.",
                    "line": 1,
                },
            ],
        },
    ]
    result = ToolResult(exit_code=1, stdout=json.dumps(data), stderr="")
    findings = parse_eslint(result, SOURCE)

    assert len(findings) == 3

    assert findings[0].file == "src/app.ts"
    assert findings[0].line == 5
    assert findings[0].code == "no-unused-vars"
    assert findings[0].severity == "warning"
    assert findings[0].message == "'x' is declared but never used."

    assert findings[1].file == "src/app.ts"
    assert findings[1].line == 12
    assert findings[1].code == "no-console"
    assert findings[1].severity == "error"
    assert findings[1].message == "Unexpected console statement."

    assert findings[2].file == "src/utils.ts"
    assert findings[2].severity == "info"


def test_eslint_malformed_json():
    result = ToolResult(exit_code=1, stdout="NOT JSON{{{", stderr="")
    findings = parse_eslint(result, SOURCE)

    assert len(findings) == 1
    assert findings[0].message == "NOT JSON{{{"


# ---------------------------------------------------------------------------
# parse_tsc
# ---------------------------------------------------------------------------

TSC_SOURCE = "type_checker:tsc"


def test_tsc_empty_stdout():
    result = ToolResult(exit_code=0, stdout="", stderr="")
    assert parse_tsc(result, TSC_SOURCE) == []


def test_tsc_valid_diagnostics():
    lines = (
        "src/foo.ts(10,5): error TS2345: Argument of type 'string' is not "
        "assignable to parameter of type 'number'.\n"
        "src/bar.ts(3,1): error TS2304: Cannot find name 'foo'.\n"
    )
    result = ToolResult(exit_code=2, stdout=lines, stderr="")
    findings = parse_tsc(result, TSC_SOURCE)

    assert len(findings) == 2

    assert findings[0].file == "src/foo.ts"
    assert findings[0].line == 10
    assert findings[0].code == "TS2345"
    assert findings[0].severity == "error"
    assert "assignable" in findings[0].message

    assert findings[1].file == "src/bar.ts"
    assert findings[1].line == 3
    assert findings[1].code == "TS2304"
    assert findings[1].severity == "error"


def test_tsc_skips_non_matching_lines():
    stdout = (
        "\n"
        "Found 2 errors in 1 file.\n"
        "\n"
        "src/index.ts(1,1): error TS1005: ';' expected.\n"
    )
    result = ToolResult(exit_code=2, stdout=stdout, stderr="")
    findings = parse_tsc(result, TSC_SOURCE)

    assert len(findings) == 1
    assert findings[0].file == "src/index.ts"
    assert findings[0].code == "TS1005"


# ---------------------------------------------------------------------------
# parse_ruff_format
# ---------------------------------------------------------------------------

FMT_SOURCE = "formatter:ruff_format"


def test_ruff_format_clean():
    result = ToolResult(exit_code=0, stdout="", stderr="17 files already formatted\n")
    assert parse_ruff_format(result, FMT_SOURCE) == []


def test_ruff_format_would_reformat():
    stderr = (
        "Would reformat: src/sqa_agent/agent.py\n"
        "Would reformat: tests/test_agent.py\n"
        "2 files would be reformatted, 15 files already formatted\n"
    )
    result = ToolResult(exit_code=1, stdout="", stderr=stderr)
    findings = parse_ruff_format(result, FMT_SOURCE)

    assert len(findings) == 2
    assert findings[0].file == "src/sqa_agent/agent.py"
    assert findings[0].message == "File needs reformatting"
    assert findings[1].file == "tests/test_agent.py"


def test_ruff_format_unexpected_failure():
    result = ToolResult(exit_code=2, stdout="", stderr="error: something went wrong\n")
    findings = parse_ruff_format(result, FMT_SOURCE)

    assert len(findings) == 1
    assert "something went wrong" in findings[0].message
