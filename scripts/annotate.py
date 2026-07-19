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

# The ci-result contract this action knows how to read. patchrail ships breaking
# JSON contract changes in minor bumps (0.4.0 moved `ci classes` to schema v2),
# and every field below is read with `.get()`, so a renamed key would not raise:
# it would quietly annotate `unknown (confidence None)` on a run that is already
# red. Refuse a schema we do not know instead of inventing a classification.
RESULT_SCHEMA = "patchrail.ci_result.v1"

# What one `unknown` verdict may drag into the annotation. patchrail already
# de-duplicates and caps `runner_errors`, but the content is log text: a matrix
# build emits one annotation per leg, and a single line can be a whole stack trace.
RUNNER_ERROR_LIMIT = 3
RUNNER_ERROR_MAX_CHARS = 200

# Shown whenever there is no log to classify. Both halves matter: without
# `2>&1` a tool that reports only on stderr leaves an empty log, and without
# pipefail (`shell: bash`) a failing command piped into `tee` exits 0, so the
# step goes green and `if: failure()` never fires.
CAPTURE_HINT = (
    "Capture the failing command like this: "
    "`shell: bash` + `your-command 2>&1 | tee build.log` "
    "(stderr included, and pipefail keeps the step red)."
)

# Closes every job summary, classified or not. The summary sits on the runner of
# someone else's red build, so it is the one surface where a maintainer reading the
# annotation can find the project behind it — carry a plain repo backlink. It stays
# inside the read-only promise: text written to $GITHUB_STEP_SUMMARY, no PR, no
# comment, no network. A link, never a "star us" ask; the run is not ours to campaign on.
SUMMARY_FOOTER = [
    "",
    "_Powered by [PatchRail](https://github.com/patchrail/patchrail) — "
    "open-source, local-first CI failure triage._",
    "_Classified locally. No pull request, comment or external call was made._",
]

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


def runner_errors(result: dict) -> list[str]:
    """The lines the runner itself flagged, as patchrail 0.6.0 reports them.

    Only present on an `unknown` result: when no rule matches, patchrail hands back
    the runner's own annotation for the failing step (`##[error]…`), so the job can
    say *where* it died even when PatchRail cannot say *why*. Without this, `unknown`
    annotates a red run with `inspect CI log and run the failing job locally` — the
    user learns nothing they would not have learned by never running the action.

    Absent on a classified result (its `signals` already explain it) and on any
    patchrail below 0.6.0, which the `patchrail-version` input still allows: an
    older release simply has no such key and this returns nothing.
    """
    reported = result.get("runner_errors")
    if not isinstance(reported, list):
        return []
    lines = []
    for entry in reported[:RUNNER_ERROR_LIMIT]:
        # Log text, so it arrives with whatever shape the failing build gave it.
        line = " ".join(str(entry).split())
        if not line:
            continue
        if len(line) > RUNNER_ERROR_MAX_CHARS:
            line = line[: RUNNER_ERROR_MAX_CHARS - 1].rstrip() + "…"
        lines.append(line)
    return lines


def annotation_safe(text: str) -> str:
    """Escape log text for a workflow command, `%` first.

    GitHub decodes `%0A` in a `::warning::` message back into a newline, and this
    text is a line from a build any PR author can write. Left raw, a log containing
    `%0A::error::<anything>` would close our annotation and forge a second one.
    """
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def summary_safe(text: str) -> str:
    """Keep log text inside its code span in the job summary, instead of formatting it."""
    return text.replace("`", "'")


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
            *SUMMARY_FOOTER,
        ],
    )
    write_kv(
        "GITHUB_OUTPUT",
        ["failure-class=", "confidence=", f"guide-url={FIX_GUIDE_BASE}"],
    )
    return 0


def incompatible_schema(found: str) -> int:
    """Report that the installed patchrail speaks a contract this action cannot read.

    Same contract as `unclassified`: one annotation line, empty outputs, the guide
    index, exit 0. Naming both versions is the point — the alternative is an
    `unknown (confidence None)` annotation that looks like a real classification
    and tells the user nothing about why their pinned action stopped working.
    """
    found = " ".join(str(found or "").split()) or "none"
    reason = (
        f"the installed patchrail emits ci-result schema '{found}', "
        f"and this action reads '{RESULT_SCHEMA}'. Pin a compatible release with "
        f"the `patchrail-version` input, or upgrade patchrail-ci-triage."
    )
    print(f"::warning title=PatchRail CI Triage::No classification: {reason}")
    write_kv(
        "GITHUB_STEP_SUMMARY",
        [
            "## PatchRail CI Triage",
            "",
            f"- **No classification:** {reason}",
            *SUMMARY_FOOTER,
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

    if result.get("schema_version") != RESULT_SCHEMA:
        return incompatible_schema(result.get("schema_version"))

    failure_class = str(result.get("failure_class") or "unknown")
    confidence = result.get("confidence")
    subsystem = result.get("likely_subsystem") or "unknown"
    repro = result.get("reproduction_command") or ""
    strategy = result.get("minimal_repair_strategy") or ""
    reported = runner_errors(result)
    url = guide_url(failure_class)

    # GitHub annotation (shows up inline on the run). One line, always: GitHub reads
    # one annotation per line, so the runner's line is escaped, never appended raw.
    headline = f"{failure_class} (confidence {confidence})"
    if reported:
        headline += f" — runner reported: {annotation_safe(reported[0])}"
    print(f"::warning title=PatchRail CI Triage::{headline} — guide: {url}")

    # Job summary.
    summary = [
        "## PatchRail CI Triage",
        "",
        f"- **Root cause:** `{failure_class}`",
        f"- **Confidence:** `{confidence}`",
        f"- **Subsystem:** {subsystem}",
    ]
    # On an `unknown` verdict this is the only line in the summary worth reading, so
    # it goes above the generic "reproduce it locally" advice, not below it.
    if reported:
        summary.append("- **Errors the runner reported:**")
        summary += [f"  - `{summary_safe(line)}`" for line in reported]
    if repro:
        summary.append(f"- **Reproduce:** `{repro}`")
    if strategy:
        summary.append(f"- **Suggested action:** {strategy}")
    summary += [
        f"- **Remediation guide:** {url}",
        *SUMMARY_FOOTER,
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
