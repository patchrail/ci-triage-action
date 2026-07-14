# PatchRail CI Triage Action

Classify a failed CI log **locally** on the runner and surface the matching
[PatchRail `/fix` remediation guide](https://getpatchrail.com/fix) as a job
annotation and step summary. Read-only by design: it never opens a pull request,
posts a comment, claims funding, or sends your logs anywhere.

It wraps the [`patchrail`](https://pypi.org/project/patchrail/) CLI's offline
`patchrail ci explain` command (100+ failure signatures across Python, Node, Go,
Rust, Java, Ruby, PHP, .NET, Docker, GitHub Actions and more). PatchRail is open
source (Apache-2.0) — the CLI, the failure-signature zoo and the issue tracker
live at **[github.com/patchrail/patchrail](https://github.com/patchrail/patchrail)**.

## Usage

Install from the
[GitHub Marketplace listing](https://github.com/marketplace/actions/patchrail-ci-triage),
or copy the step below into any workflow that already captures its failed build
or test log.

```yaml
- name: Build
  id: build
  run: make build
- name: PatchRail CI triage
  if: failure()
  uses: patchrail/ci-triage-action@v1
  with:
    log-path: build.log
```

That's the whole thing: pipe your build/test output to a file, then add the
step guarded by `if: failure()`. On a red run you get an annotation like
`python-test-failure (confidence 0.89) — guide: getpatchrail.com/fix/...` plus a
job summary block.

### Which ref to pin

`@v1` is a moving tag: it points at the latest commit on `main` that passed the
test suite, and it will keep moving within the v1 line (no breaking changes to
inputs or outputs). If you would rather review every change yourself, pin the
full commit SHA instead:

```yaml
uses: patchrail/ci-triage-action@<commit-sha>
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `log-path` | `''` | Path to the CI log file to explain. |
| `log-text` | `''` | Raw log text, used when no file is available. |
| `redact` | `'true'` | Redact secrets, emails and home paths locally first. |
| `patchrail-version` | `''` | Pin a specific `patchrail` version from PyPI. |
| `python-version` | `'3.x'` | Python version used to run the classifier. |

Provide either `log-path` (preferred) or `log-text`.

## Outputs

| Output | Description |
| --- | --- |
| `failure-class` | Classified failure class, e.g. `python-test-failure`. |
| `confidence` | Classifier confidence between 0 and 1. |
| `guide-url` | PatchRail `/fix` remediation guide URL for the class. |

## Example: capture the log and triage on failure

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: pytest -q | tee test.log
      - name: PatchRail CI triage
        if: failure()
        uses: patchrail/ci-triage-action@v1
        with:
          log-path: test.log
```

## Permissions

The action needs no special token. It only reads the log file you point it at
and writes to the run's annotation stream and job summary:

```yaml
permissions:
  contents: read
```

## Privacy

Classification runs entirely on the runner. With `redact: true` (the default),
secrets, emails and home paths are stripped before the log is parsed. Nothing is
uploaded.

---

Part of the open-source [**PatchRail**](https://github.com/patchrail/patchrail)
project. Maintained by **Pablo Guillén · PatchRail · [getpatchrail.com](https://getpatchrail.com)**.

Licensed under the Apache License 2.0.
