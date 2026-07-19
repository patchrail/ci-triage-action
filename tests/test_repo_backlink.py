"""Every job summary must carry a link back to the PatchRail repo — and only a link.

The annotation and job summary land on the runner of someone else's failing build.
That summary is the one place a maintainer reading the triage can find the project
behind it, so it carries a plain backlink to github.com/patchrail/patchrail. Two
things must hold on every path (classified, no-log, unreadable-schema):

* the backlink is present, so the surface is not a dead end;
* it is a link, never a "star this repo" ask. Soliciting stars from inside a CI
  run the action does not own is off-limits, and the backlink must not drift into one.

The backlink is text written to $GITHUB_STEP_SUMMARY, so it keeps the read-only
promise the rest of the action makes: no pull request, no comment, no network.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ANNOTATE = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
REPO_URL = "https://github.com/patchrail/patchrail"

# A star/upvote solicitation would turn the backlink into a campaign; none may
# appear in a summary. Word boundaries keep this off innocent words like "start".
STAR_SOLICITATION = re.compile(r"\bstars?\b|\bupvotes?\b|⭐|🌟", re.IGNORECASE)

CLASSIFIED_RESULT = {
    "schema_version": "patchrail.ci_result.v1",
    "failure_class": "python_test_failure",
    "confidence": 0.95,
    "likely_subsystem": "Python tests",
    "reproduction_command": "python -m pytest -q",
    "minimal_repair_strategy": "Reproduce the failing test and patch the drift.",
}
UNREADABLE_RESULT = {
    "schema_version": "patchrail.ci_result.v2",
    "classification": {"class": "python_test_failure", "confidence": 0.89},
}


def _summary_for(tmp_path, argv: list[str]) -> str:
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    output.touch()
    summary.touch()
    proc = subprocess.run(
        [sys.executable, str(ANNOTATE), *argv],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return summary.read_text()


def _classified(tmp_path) -> str:
    result_path = tmp_path / "patchrail-ci-result.json"
    result_path.write_text(json.dumps(CLASSIFIED_RESULT), encoding="utf-8")
    return _summary_for(tmp_path, [str(result_path)])


def _unclassified(tmp_path) -> str:
    return _summary_for(tmp_path, ["--unclassified", "log input is empty"])


def _unreadable_schema(tmp_path) -> str:
    result_path = tmp_path / "patchrail-ci-result.json"
    result_path.write_text(json.dumps(UNREADABLE_RESULT), encoding="utf-8")
    return _summary_for(tmp_path, [str(result_path)])


SUMMARY_BUILDERS = pytest.mark.parametrize(
    "build_summary",
    [_classified, _unclassified, _unreadable_schema],
    ids=["classified", "no-log", "unreadable-schema"],
)


@SUMMARY_BUILDERS
def test_every_summary_links_back_to_the_repo(build_summary, tmp_path) -> None:
    assert REPO_URL in build_summary(tmp_path)


@SUMMARY_BUILDERS
def test_the_backlink_is_never_a_star_solicitation(build_summary, tmp_path) -> None:
    assert not STAR_SOLICITATION.search(build_summary(tmp_path))
