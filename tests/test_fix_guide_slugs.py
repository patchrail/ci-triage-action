"""Guard the action's FIX_GUIDE_SLUGS against drift.

The action turns a failure class into a https://getpatchrail.com/fix/<slug>
link. That list is hand-maintained here, so it can rot in two directions:

* a slug that is not a real PatchRail failure class -> dead entry, and a
  renamed class silently loses its guide link;
* a slug with no published guide page -> the action sends users to a 404.

The two sources of truth are the CLI (`patchrail ci classes`) and the guide
index on getpatchrail.com. These tests derive both and fail on divergence.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

GUIDE_INDEX = "https://getpatchrail.com/fix"
_HREF = re.compile(r'href="/fix/([a-z0-9-]+)"')


def _load_annotate():
    path = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
    spec = importlib.util.spec_from_file_location("annotate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


annotate = _load_annotate()


def _slug(failure_class: str) -> str:
    return failure_class.replace("_", "-")


def _cli_failure_classes() -> set[str]:
    """Every failure class the installed patchrail CLI can emit, as slugs."""
    raw = subprocess.run(
        [sys.executable, "-m", "patchrail", "ci", "classes", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    payload = json.loads(raw)
    entries = payload["classes"] if isinstance(payload, dict) else payload
    names = {e["failure_class"] if isinstance(e, dict) else e for e in entries}
    return {_slug(name) for name in names if name != "unknown"}


def _published_guides() -> set[str]:
    """Slugs the guide index actually links to. Skips if the site is unreachable."""
    try:
        with urllib.request.urlopen(GUIDE_INDEX, timeout=20) as response:
            if response.status != 200:
                pytest.skip(f"guide index returned HTTP {response.status}")
            html = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"guide index unreachable: {exc}")
    slugs = set(_HREF.findall(html))
    if not slugs:
        pytest.skip("guide index exposed no /fix/<slug> links; markup may have changed")
    return slugs


def test_every_slug_is_a_real_failure_class() -> None:
    """A slug the CLI never emits is dead weight, and a rename must not go unnoticed."""
    unknown = annotate.FIX_GUIDE_SLUGS - _cli_failure_classes()
    assert not unknown, (
        f"FIX_GUIDE_SLUGS contains slugs that are not PatchRail failure classes: "
        f"{sorted(unknown)}. Remove them, or fix the spelling to match "
        f"`patchrail ci classes`."
    )


def test_known_slug_links_to_its_guide() -> None:
    assert (
        annotate.guide_url("python_dependency_resolution")
        == f"{GUIDE_INDEX}/python-dependency-resolution"
    )


@pytest.mark.parametrize("failure_class", ["unknown", "", "some_class_with_no_guide"])
def test_class_without_a_guide_falls_back_to_the_index(failure_class: str) -> None:
    """A class with no published guide must reach the index, never a 404."""
    assert annotate.guide_url(failure_class) == GUIDE_INDEX


@pytest.mark.network
def test_slugs_match_the_published_guides() -> None:
    """FIX_GUIDE_SLUGS must be exactly the guides published on getpatchrail.com."""
    published = _published_guides()
    missing_page = annotate.FIX_GUIDE_SLUGS - published
    assert not missing_page, (
        f"FIX_GUIDE_SLUGS points at guides that are not published: "
        f"{sorted(missing_page)}. The action would send users to a 404."
    )
    unlinked = published - annotate.FIX_GUIDE_SLUGS
    assert not unlinked, (
        f"These guides are published but missing from FIX_GUIDE_SLUGS: "
        f"{sorted(unlinked)}. The action sends those failures to the index "
        f"instead of the guide that exists."
    )
