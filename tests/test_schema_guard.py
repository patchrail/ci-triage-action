"""The action must not invent a classification when patchrail changes its contract.

`annotate.py` reads every field of the ci-result with `.get()`, so a renamed key
never raises. Before the guard, a patchrail release that bumped the ci-result
schema turned every consumer's annotation into `unknown (confidence None)` linking
to the bare guide index — on a run that is already red, under a `@v1` tag the user
pinned precisely so nothing would move under them. Worse, it was silent: the smoke
assertions (`test -n` on the class, a `fix*` glob on the URL) passed on that output.

patchrail ships breaking JSON contract changes in minor bumps (0.4.0 moved
`ci classes` to schema v2), so this is a question of when, not if.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ANNOTATE = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
GUIDE_INDEX = "https://getpatchrail.com/fix"

# Kept literal on purpose: importing the constant from annotate.py would make this
# test agree with any future edit to it, which is the drift it exists to catch.
SUPPORTED_SCHEMA = "patchrail.ci_result.v1"

# What patchrail emits today (verified against 0.3.1, 0.4.0 and 0.5.0).
V1_RESULT = {
    "schema_version": SUPPORTED_SCHEMA,
    "failure_class": "python_test_failure",
    "confidence": 0.89,
    "likely_subsystem": "Python tests",
    "reproduction_command": "python -m pytest -q",
    "minimal_repair_strategy": "Reproduce the failing test and patch the drift.",
}

# A future patchrail that regroups the classification fields, the way 0.4.0 did to
# `ci classes`. Every key annotate.py reads is gone, and none of them raise.
V2_RESULT = {
    "schema_version": "patchrail.ci_result.v2",
    "classification": {"class": "python_test_failure", "confidence": 0.89},
    "likely_subsystem": "Python tests",
    "reproduction_command": "python -m pytest -q",
}


@pytest.fixture()
def annotate(tmp_path):
    def run(result: dict) -> tuple[subprocess.CompletedProcess, str, str]:
        result_path = tmp_path / "patchrail-ci-result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        output = tmp_path / "output"
        summary = tmp_path / "summary"
        output.touch()
        summary.touch()
        proc = subprocess.run(
            [sys.executable, str(ANNOTATE), str(result_path)],
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
        )
        return proc, output.read_text(), summary.read_text()

    return run


def test_the_current_schema_is_annotated_normally(annotate) -> None:
    """The guard must not cost anything on the contract patchrail ships today."""
    proc, output, summary = annotate(V1_RESULT)
    assert proc.returncode == 0, proc.stderr
    assert "python_test_failure (confidence 0.89)" in proc.stdout
    assert f"guide-url={GUIDE_INDEX}/python-test-failure" in output
    assert "**Root cause:** `python_test_failure`" in summary


def test_an_unreadable_schema_is_never_dressed_up_as_a_classification(annotate) -> None:
    """The bug: `unknown (confidence None)` looks like triage and says nothing."""
    proc, output, _ = annotate(V2_RESULT)
    assert "unknown (confidence None)" not in proc.stdout
    assert "failure-class=unknown" not in output
    assert "confidence=None" not in output


def test_an_unreadable_schema_names_both_versions_and_the_way_out(annotate) -> None:
    proc, _, summary = annotate(V2_RESULT)
    assert "patchrail.ci_result.v2" in proc.stdout  # what the runner installed
    assert SUPPORTED_SCHEMA in proc.stdout  # what this action reads
    assert "patchrail-version" in proc.stdout  # how to get unstuck today
    assert "patchrail.ci_result.v2" in summary


def test_an_unreadable_schema_leaves_the_outputs_empty(annotate) -> None:
    """Downstream steps must be able to tell "no classification" from a real one."""
    _, output, _ = annotate(V2_RESULT)
    lines = output.strip().splitlines()
    assert "failure-class=" in lines
    assert "confidence=" in lines
    assert f"guide-url={GUIDE_INDEX}" in lines


def test_a_result_without_a_schema_version_is_not_trusted(annotate) -> None:
    """Covers a pre-schema patchrail, and any JSON that is not a ci-result at all."""
    proc, output, _ = annotate({"failure_class": "python_test_failure", "confidence": 0.89})
    assert "No classification" in proc.stdout
    assert "failure-class=python_test_failure" not in output


def test_an_unreadable_schema_still_does_not_fail_the_job(annotate) -> None:
    """It runs under `if: failure()`: a second red step buries the real failure."""
    proc, _, _ = annotate(V2_RESULT)
    assert proc.returncode == 0, proc.stderr


def test_the_annotation_stays_on_one_line(annotate) -> None:
    """GitHub reads one annotation per line; a wrapped message would be truncated."""
    proc, _, _ = annotate(V2_RESULT)
    warnings = [ln for ln in proc.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 1
