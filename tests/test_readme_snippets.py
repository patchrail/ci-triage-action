"""Guard the workflow snippets in the README against the log-capture footgun.

People copy the README verbatim into their own workflow, so a broken snippet
here breaks *their* CI, not ours. Two mistakes are easy to make and silent:

* `make build | tee build.log` under the default `run:` shell (`bash -e`, no
  pipefail) exits 0 when the build fails -- the step goes green, `if: failure()`
  never fires, and a red build is reported as passing;
* no `2>&1`, so a tool that reports only on stderr writes an empty log and there
  is nothing left to classify.

These tests parse every YAML snippet in the README and fail if a capture step
loses either protection, or if the file it writes is not the one handed to the
action.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

README = Path(__file__).resolve().parent.parent / "README.md"
_YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_TEE_TARGET = re.compile(r"\|\s*tee\s+(\S+)")


def _snippets() -> list[Any]:
    blocks = _YAML_BLOCK.findall(README.read_text(encoding="utf-8"))
    assert blocks, "no ```yaml blocks found in the README; did the docs move?"
    return [yaml.safe_load(block) for block in blocks]


def _walk(node: Any) -> Iterator[dict]:
    """Yield every mapping in a parsed snippet, at any depth."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _capture_steps() -> list[dict]:
    """Every README step that pipes a command into `tee`."""
    steps = [
        step
        for snippet in _snippets()
        for step in _walk(snippet)
        if isinstance(step.get("run"), str) and "tee" in step["run"]
    ]
    assert steps, "the README no longer shows how to capture a log; the snippets must"
    return steps


@pytest.mark.parametrize("step", _capture_steps(), ids=lambda s: str(s.get("name", "step")))
def test_capture_step_keeps_the_step_red_on_failure(step: dict) -> None:
    """Without pipefail, `cmd | tee log` swallows cmd's exit code and CI goes green."""
    has_pipefail = step.get("shell") == "bash" or "pipefail" in step["run"]
    assert has_pipefail, (
        f"README step {step.get('name')!r} pipes into `tee` without pipefail. "
        f"The default `run:` shell is `bash -e`, so `{step['run']}` exits 0 even when "
        f"the command fails: the step goes green and `if: failure()` never fires. "
        f"Add `shell: bash` (which is `bash -eo pipefail`) or `set -o pipefail`."
    )


@pytest.mark.parametrize("step", _capture_steps(), ids=lambda s: str(s.get("name", "step")))
def test_capture_step_records_stderr(step: dict) -> None:
    """Compilers, linters and type checkers report on stderr; an empty log explains nothing."""
    assert "2>&1" in step["run"], (
        f"README step {step.get('name')!r} captures stdout only. Tools that report "
        f"errors on stderr would leave an empty log and nothing to classify. "
        f"Use `2>&1 | tee <log>`."
    )


def test_captured_file_is_the_one_handed_to_the_action() -> None:
    """A snippet that tees to build.log but triages test.log would never classify anything."""
    for snippet in _snippets():
        mappings = list(_walk(snippet))
        captured = {
            match
            for step in mappings
            if isinstance(step.get("run"), str)
            for match in _TEE_TARGET.findall(step["run"])
        }
        triaged = {
            str(step["with"]["log-path"])
            for step in mappings
            if isinstance(step.get("with"), dict) and step["with"].get("log-path")
        }
        if not triaged:
            continue
        assert triaged <= captured, (
            f"README snippet triages {sorted(triaged - captured)} but never writes it "
            f"(it captures {sorted(captured)}). The action would find no log."
        )
