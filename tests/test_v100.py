"""Regression tests for the v0.10.0 iteration.

Covers one release-hygiene defect:

  * fix-version-drift-release-hygiene — the v0.9.0 tag was cut from a single
    rename-verifier commit that bumped the git tag but left every derived
    version surface stale, so the release mis-reported its own version.
    Reproduced against the shipped v0.9.0 tag: ``agentlie --version`` printed
    ``agentlie, version 0.8.0`` while the git tag was ``v0.9.0`` —
    ``pyproject.toml`` (``version = "0.8.0"``) and
    ``src/agentlie/__init__.py`` (``__version__ = "0.8.0"``) both lagged the
    tag (and therefore so did the Click ``--version`` option that reads
    ``__version__`` in ``src/agentlie/cli.py``), the CHANGELOG had no
    ``[0.9.0]`` entry, and ``web/site.json`` carried no ``content_version``
    field at all.

  Fix: every version surface now tracks the canonical ``pyproject.toml``
  version, and these tests assert they stay in lockstep so the next bump
  cannot silently drift the way v0.9.0 did.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from click.testing import CliRunner

import agentlie
from agentlie.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE_JSON = REPO_ROOT / "web" / "site.json"


def _pyproject_version() -> str:
    """The canonical version (hatchling builds from ``[project] version``)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "could not find [project] version in pyproject.toml"
    return m.group(1)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def test_release_self_reports_its_own_version():
    """The v0.9.0 drift reproduction. At the shipped v0.9.0 tag
    ``agentlie --version`` printed ``0.8.0`` while the tag was ``v0.9.0`` —
    ``__version__`` lagged the release. The self-report MUST now reach 0.10.0
    (closing the drift). At v0.9.0 ``__version__`` was ``0.8.0`` < ``0.10.0``
    so this fails there; after the bump it passes."""
    assert _version_tuple(agentlie.__version__) >= (0, 10, 0), (
        f"__version__ {agentlie.__version__!r} < 0.10.0 — the v0.9.0 version "
        f"drift (reporting 0.8.0 at the v0.9.0 tag) is not closed"
    )


def test_cli_version_matches_canonical():
    """The ``agentlie --version`` self-report MUST equal the canonical
    ``pyproject.toml`` version. The Click ``--version`` option reads
    ``__version__`` (``src/agentlie/cli.py``), so this binds the CLI surface to
    the canonical bump — a one-surface bump (bumping pyproject but forgetting
    ``__init__.__version__``) now fails CI, the exact failure mode of v0.9.0."""
    canonical = _pyproject_version()
    result = CliRunner().invoke(cli_main, ["--version"])
    assert result.exit_code == 0, result.output
    assert canonical in result.output, (
        f"agentlie --version did not report {canonical!r}: {result.output!r}"
    )
    assert agentlie.__version__ == canonical, (
        f"__version__ ({agentlie.__version__!r}) != pyproject version "
        f"({canonical!r}) — a version surface lagged the canonical bump"
    )


def test_site_content_version_matches_canonical():
    """``web/site.json``'s ``content_version`` MUST track the canonical
    version. The v0.9.0 release shipped a site.json with NO ``content_version``
    field at all, so the live site could not reflect the shipped version; this
    guard keeps the site's version surface in lockstep with the package so a
    refresh cannot ship a stale version."""
    canonical = _pyproject_version()
    site = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    assert site.get("content_version") == canonical, (
        f"web/site.json content_version ({site.get('content_version')!r}) != "
        f"pyproject version ({canonical!r})"
    )
