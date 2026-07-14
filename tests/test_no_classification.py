"""The action must degrade gracefully when there is no log to classify.

It runs under `if: failure()`, on a run that is already red. A missing or empty
log used to kill the step with a raw `exit 2` (or a misleading "provide either
log-path or log-text"), stacking a second, meaningless failure on top of the one
the user is actually debugging. It now annotates the cause and stays green.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ANNOTATE = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
GUIDE_INDEX = "https://getpatchrail.com/fix"


@pytest.fixture()
def run_unclassified(tmp_path):
    def run(reason: str) -> tuple[subprocess.CompletedProcess, str, str]:
        output = tmp_path / "output"
        summary = tmp_path / "summary"
        output.touch()
        summary.touch()
        proc = subprocess.run(
            [sys.executable, str(ANNOTATE), "--unclassified", reason],
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


def test_it_does_not_fail_the_job(run_unclassified) -> None:
    proc, _, _ = run_unclassified("log-path 'build.log' does not exist on the runner.")
    assert proc.returncode == 0, proc.stderr


def test_it_annotates_the_reason_and_the_fix(run_unclassified) -> None:
    proc, _, _ = run_unclassified("log input is empty (checked --log build.log)")
    assert "::warning title=PatchRail CI Triage::" in proc.stdout
    assert "log input is empty" in proc.stdout
    # The hint is the whole point: it names both halves of the correct capture.
    assert "2>&1" in proc.stdout and "pipefail" in proc.stdout


def test_outputs_are_empty_and_the_url_is_the_index(run_unclassified) -> None:
    """Downstream steps must be able to tell "no classification" from a real one."""
    _, output, _ = run_unclassified("log input is empty")
    lines = output.strip().splitlines()
    assert "failure-class=" in lines
    assert "confidence=" in lines
    assert f"guide-url={GUIDE_INDEX}" in lines


def test_the_summary_explains_the_capture(run_unclassified) -> None:
    _, _, summary = run_unclassified("log input is empty")
    assert "## PatchRail CI Triage" in summary
    assert "No classification" in summary
    assert "2>&1" in summary


def test_a_multiline_reason_stays_on_one_annotation_line(run_unclassified) -> None:
    """GitHub reads one annotation per line; a raw stderr dump would truncate it."""
    proc, _, _ = run_unclassified("patchrail: log input is empty\n  traceback line\n")
    warnings = [ln for ln in proc.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 1
    assert "traceback line" in warnings[0]
