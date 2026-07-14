"""An `unknown` verdict must hand back the line the runner flagged, and nothing else.

Before this, an unclassified run got the worst annotation the action can produce:
`unknown (confidence 0.15)`, subsystem `unknown`, and the advice to "inspect CI log
and run the failing job locally" — on a red run, from a tool the user installed
precisely so they would not have to. patchrail 0.6.0 reports the runner's own
`##[error]` line for the failing step in `runner_errors`, so the action can at least
say *where* the job died when it cannot say *why*. It was already in the JSON the
action reads; the action was dropping it on the floor.

The lines are log text from a build any PR author can write, so they are untrusted:
they reach a `::warning` workflow command, where GitHub decodes `%0A` back into a
newline, and a job summary, which is Markdown. Escaped on the way out, both times.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ANNOTATE = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
GUIDE_INDEX = "https://getpatchrail.com/fix"

# What patchrail 0.6.0 emits for a log no rule matches. The runner error is the real
# one from psf/requests run 29295524780, the run that motivated the patchrail fix.
UNKNOWN_RESULT = {
    "schema_version": "patchrail.ci_result.v1",
    "failure_class": "unknown",
    "confidence": 0.15,
    "likely_subsystem": "unknown",
    "reproduction_command": "inspect CI log and run the failing job locally",
    "minimal_repair_strategy": "Do not auto-repair until the failing subsystem is identified.",
    "runner_errors": ['"github-token" length must be less than or equal to 100 characters long'],
}

# A log that classifies: its `signals` explain it, and patchrail reports no
# `runner_errors` at all. Also what every patchrail below 0.6.0 emits.
CLASSIFIED_RESULT = {
    "schema_version": "patchrail.ci_result.v1",
    "failure_class": "python_test_failure",
    "confidence": 0.95,
    "likely_subsystem": "Python tests",
    "reproduction_command": "python -m pytest -q",
    "minimal_repair_strategy": "Reproduce the failing test and patch the drift.",
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


def with_errors(*errors: object) -> dict:
    return {**UNKNOWN_RESULT, "runner_errors": list(errors)}


def test_the_runner_line_reaches_the_annotation(annotate) -> None:
    """The whole point: the red run now names the line it died on."""
    proc, _, _ = annotate(UNKNOWN_RESULT)
    assert proc.returncode == 0, proc.stderr
    assert '"github-token" length must be less than or equal to 100' in proc.stdout


def test_the_runner_line_reaches_the_job_summary(annotate) -> None:
    _, _, summary = annotate(UNKNOWN_RESULT)
    assert "**Errors the runner reported:**" in summary
    assert '"github-token" length must be less than or equal to 100' in summary


def test_the_runner_line_outranks_the_generic_advice(annotate) -> None:
    """On `unknown` it is the only line worth reading, so it must not sit under
    "inspect CI log and run the failing job locally"."""
    _, _, summary = annotate(UNKNOWN_RESULT)
    assert summary.index("Errors the runner reported") < summary.index("Reproduce")


def test_an_unknown_verdict_is_still_unknown(annotate) -> None:
    """An annotation says where the job died, not why. Reporting it must not be
    allowed to look like a classification: patchrail does not know this log."""
    proc, output, _ = annotate(UNKNOWN_RESULT)
    assert "failure-class=unknown" in output
    assert "confidence=0.15" in output
    assert f"guide-url={GUIDE_INDEX}" in output.strip().splitlines()  # index, not a 404


def test_a_classified_result_is_untouched(annotate) -> None:
    """patchrail sends no `runner_errors` when a rule matched, and neither does any
    patchrail below 0.6.0, which `patchrail-version` still lets a user pin."""
    proc, output, summary = annotate(CLASSIFIED_RESULT)
    assert proc.returncode == 0, proc.stderr
    assert "runner reported" not in proc.stdout
    assert "Errors the runner reported" not in summary
    assert "python_test_failure (confidence 0.95)" in proc.stdout
    assert f"guide-url={GUIDE_INDEX}/python-test-failure" in output


def test_a_log_line_cannot_forge_a_second_annotation(annotate) -> None:
    """GitHub decodes `%0A` in a workflow command back into a newline. Raw, this log
    line would end our warning and open an `::error::` of the author's choosing."""
    proc, _, _ = annotate(with_errors("boom%0A::error::forged by the log"))
    commands = [ln for ln in proc.stdout.splitlines() if ln.startswith("::")]
    assert len(commands) == 1
    assert commands[0].startswith("::warning title=PatchRail CI Triage::")
    # The `%` is escaped, so GitHub renders `%0A` as those two literal characters
    # instead of decoding it into the newline that would start the forged command.
    assert "%250A" in commands[0]
    assert "%0A::error::" not in commands[0]


def test_the_annotation_stays_on_one_line(annotate) -> None:
    """GitHub reads one annotation per line; a wrapped message would be truncated."""
    proc, _, _ = annotate(with_errors("first line\nsecond line\r\nthird"))
    warnings = [ln for ln in proc.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 1
    assert "first line second line third" in warnings[0]


def test_a_log_line_cannot_break_out_of_its_code_span(annotate) -> None:
    """The job summary is Markdown, and this is text from someone else's build."""
    _, _, summary = annotate(with_errors("`# not a heading, and no <img> either"))
    reported = next(ln for ln in summary.splitlines() if "not a heading" in ln)
    assert reported.startswith("  - `")
    assert reported.endswith("`")
    assert reported.count("`") == 2


def test_a_matrix_build_cannot_flood_the_annotation(annotate) -> None:
    """One failing leg per matrix entry, each with its own annotation."""
    _, _, summary = annotate(with_errors(*[f"leg {i} died" for i in range(9)]))
    assert summary.count("leg ") == 3


def test_a_stack_trace_on_one_line_is_truncated(annotate) -> None:
    _, _, summary = annotate(with_errors("x" * 5000))
    reported = next(ln for ln in summary.splitlines() if "xxx" in ln)
    assert len(reported) < 260
    assert reported.endswith("…`")


def test_junk_in_the_field_is_ignored_rather_than_annotated(annotate) -> None:
    """`.get()` never raises, so a future patchrail that reshapes this field would
    otherwise annotate a red run with a stringified dict."""
    for junk in ["a bare string", {"error": "a dict"}, 17, None]:
        proc, _, summary = annotate(with_errors() | {"runner_errors": junk})
        assert proc.returncode == 0, proc.stderr
        assert "runner reported" not in proc.stdout
        assert "Errors the runner reported" not in summary


def test_empty_and_blank_entries_never_become_a_bullet(annotate) -> None:
    proc, _, summary = annotate(with_errors("", "   ", "\n"))
    assert "runner reported" not in proc.stdout
    assert "Errors the runner reported" not in summary
