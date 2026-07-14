#!/usr/bin/env python3
"""Turn a PatchRail ci-result JSON into a GitHub annotation, job summary and outputs.

Read-only: this only reads the local classification result and writes to the
GitHub Actions annotation stream, $GITHUB_STEP_SUMMARY and $GITHUB_OUTPUT. It
never posts a comment, opens a PR or sends data anywhere.
"""
from __future__ import annotations

import json
import os
import sys

FIX_GUIDE_BASE = "https://getpatchrail.com/fix"

# Shown whenever there is no log to classify. Both halves matter: without
# `2>&1` a tool that reports only on stderr leaves an empty log, and without
# pipefail (`shell: bash`) a failing command piped into `tee` exits 0, so the
# step goes green and `if: failure()` never fires.
CAPTURE_HINT = (
    "Capture the failing command like this: "
    "`shell: bash` + `your-command 2>&1 | tee build.log` "
    "(stderr included, and pipefail keeps the step red)."
)

# Failure classes with a dedicated /fix/<slug> remediation guide on getpatchrail.com.
# Unknown or unlisted classes link to the guide index instead, never to a 404.
# Every entry must be a real `patchrail ci classes` slug AND a published guide;
# tests/test_fix_guide_slugs.py checks both and fails if this list drifts.
FIX_GUIDE_SLUGS = frozenset(
    {
        "artifact-or-cache-failure",
        "browser-test-failure",
        "ci-job-timeout",
        "code-coverage-threshold",
        "cpp-build-failure",
        "docker-build-failure",
        "dotnet-build-failure",
        "git-checkout-failure",
        "git-merge-conflict",
        "github-actions-workflow",
        "go-lint",
        "go-test-failure",
        "java-build-failure",
        "javascript-lint",
        "network-transient-failure",
        "node-dependency-install",
        "node-test-failure",
        "php-composer-failure",
        "python-dependency-resolution",
        "python-lint",
        "python-test-failure",
        "python-type-check",
        "release-publish-failure",
        "ruby-bundle-failure",
        "runner-resource-exhaustion",
        "rust-lint",
        "rust-test-failure",
        "secrets-or-permissions-failure",
        "security-scan-failure",
        "terraform-iac-failure",
        "typescript-typecheck",
    }
)


def guide_url(failure_class: str) -> str:
    slug = str(failure_class or "").replace("_", "-")
    if slug and slug in FIX_GUIDE_SLUGS:
        return f"{FIX_GUIDE_BASE}/{slug}"
    return FIX_GUIDE_BASE


def write_kv(path_env: str, lines: list[str]) -> None:
    path = os.environ.get(path_env)
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def unclassified(reason: str) -> int:
    """Report that there was no log to classify, without failing the job.

    This runs under `if: failure()`, on a run that is already red. A second red
    step with a raw exit code buries the failure the user actually came to see,
    so surface the cause as a warning and leave the outputs empty.
    """
    reason = " ".join(str(reason or "no log to classify").split())
    print(f"::warning title=PatchRail CI Triage::No classification: {reason} {CAPTURE_HINT}")
    write_kv(
        "GITHUB_STEP_SUMMARY",
        [
            "## PatchRail CI Triage",
            "",
            f"- **No classification:** {reason}",
            f"- **How to fix the capture:** {CAPTURE_HINT}",
            "",
            "_Classified locally. No pull request, comment or external call was made._",
        ],
    )
    write_kv(
        "GITHUB_OUTPUT",
        ["failure-class=", "confidence=", f"guide-url={FIX_GUIDE_BASE}"],
    )
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--unclassified":
        return unclassified(argv[1] if len(argv) > 1 else "")

    result_path = argv[0] if argv else "patchrail-ci-result.json"
    with open(result_path, encoding="utf-8") as handle:
        result = json.load(handle)

    failure_class = str(result.get("failure_class") or "unknown")
    confidence = result.get("confidence")
    subsystem = result.get("likely_subsystem") or "unknown"
    repro = result.get("reproduction_command") or ""
    strategy = result.get("minimal_repair_strategy") or ""
    url = guide_url(failure_class)

    # GitHub annotation (shows up inline on the run).
    print(
        f"::warning title=PatchRail CI Triage::{failure_class} "
        f"(confidence {confidence}) — guide: {url}"
    )

    # Job summary.
    summary = [
        "## PatchRail CI Triage",
        "",
        f"- **Root cause:** `{failure_class}`",
        f"- **Confidence:** `{confidence}`",
        f"- **Subsystem:** {subsystem}",
    ]
    if repro:
        summary.append(f"- **Reproduce:** `{repro}`")
    if strategy:
        summary.append(f"- **Suggested action:** {strategy}")
    summary += [
        f"- **Remediation guide:** {url}",
        "",
        "_Classified locally. No pull request, comment or external call was made._",
    ]
    write_kv("GITHUB_STEP_SUMMARY", summary)

    # Step outputs.
    write_kv(
        "GITHUB_OUTPUT",
        [
            f"failure-class={failure_class}",
            f"confidence={confidence}",
            f"guide-url={url}",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
