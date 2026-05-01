"""Tests for findings I/O."""

from sqa_agent.findings import Finding, assign_ids, load_result, write_result


def test_load_result_round_trip(tmp_path):
    """Findings survive a write_result -> load_result round trip."""
    findings = [
        Finding(
            id=0,
            source="ruff",
            message="Unused import",
            file="src/foo.py",
            line=10,
            severity="warning",
            code="F401",
        ),
        Finding(
            id=0,
            source="mypy",
            message="Incompatible return type",
            file="src/bar.py",
            line=25,
            severity="error",
            triage="auto",
        ),
    ]
    assign_ids(findings)

    result_path = tmp_path / "result_2025_01_01_120000.json"
    write_result(result_path, findings)

    loaded = load_result(result_path)

    assert len(loaded) == len(findings)
    for original, restored in zip(findings, loaded):
        assert original == restored
