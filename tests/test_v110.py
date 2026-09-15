"""Regression tests for the v0.11.0 iteration.

Covers three fix milestones from the amend-scan-2026-05-26-0206-v0.11.0
bug hunt (evidence: amendments/_grill_v0.11.0/_signals.yaml):

  * fix-ci-ruff-lint-red — CI's `ruff check src tests` step failed with 47
    errors on every push since the v0.10.0 tag: the unpinned ``ruff>=0.6`` dev
    dep resolved to ruff 0.16.x, whose default lint set gained new rules
    (UP045, BLE001, RUF059, B033, SIM102/103, RUF022, RUF100). Fixed by
    clearing every violation AND pinning ``ruff>=0.6,<0.17``.

  * fix-rename-new-symbol-trailing-period-false-lie — RENAME_PATTERN's
    ``(?P<new>[\\w.]+)`` swallows the sentence-final period of an unbackticked
    "renamed X to Y." claim, so new_symbol became ``"new_handler."`` and the
    verifier's ``new_sym in after`` check false-LIE'd a TRUTHFUL rename (the
    honesty engine's worst failure mode). Fixed by stripping the trailing dot
    from both captured identifiers.

  * fix-demo-workflow-push-detached-head — demo.yml committed the rendered
    gif on the detached tag HEAD and ran a bare ``git push`` (exit 128), so
    the workflow failed on EVERY release tag v0.1.1..v0.10.0. Fixed by pushing
    to the default branch explicitly (``git push origin HEAD:main``); the
    static contract test below keeps the refspec from regressing to a bare
    push.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from agentlie.extractor import extract_claims
from agentlie.models import ActualEdit, Turn, Verdict
from agentlie.parser import FileStateTracker
from agentlie.verifier import verify_pair

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
DEMO_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "demo.yml"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _rename_turn(claim_text: str, before: str, after: str) -> Turn:
    """A one-turn session whose single edit carries explicit ground truth."""
    return Turn(
        turn_id=1,
        uuid="t1",
        assistant_text=claim_text,
        tool_calls=[
            ActualEdit(
                tool="Edit",
                path="src/handler.ts",
                old_string="old_handler",
                new_string="new_handler",
                before_content=before,
                after_content=after,
            )
        ],
    )


BEFORE = "export function old_handler() {\n  return 1;\n}\n"
AFTER = "export function new_handler() {\n  return 1;\n}\n"


def _rename_verdict(claim_text: str, before: str = BEFORE, after: str = AFTER):
    """extract + verify a single rename claim; return (pair, new_symbol)."""
    pairs = extract_claims([_rename_turn(claim_text, before, after)])
    assert pairs, "no claim extracted"
    verify_pair(pairs[0], FileStateTracker())
    return pairs[0], pairs[0].claim.new_symbol


def _codes(pair):
    return [r.code for r in pair.evidence]


# --------------------------------------------------------------------------- #
# 1. fix-rename-new-symbol-trailing-period-false-lie
# --------------------------------------------------------------------------- #
def test_unbackticked_sentence_final_truthful_rename_passes():
    """REPRO of the v0.11.0 defect: a truthful rename whose phrase ENDS the
    sentence, unbackticked. RENAME_PATTERN swallowed the sentence-final period
    into new_symbol ("new_handler."), so ``new_sym in after`` failed and the
    verdict was LIE (symbol_not_renamed) on a truthful rename. The strip fixes
    the capture; the verdict must now be PASS."""
    pair, new_symbol = _rename_verdict("Done — I renamed old_handler to new_handler.")
    assert new_symbol == "new_handler", (
        f"new_symbol must not carry the swallowed sentence period, got {new_symbol!r}"
    )
    assert pair.claim.target_symbol == "old_handler"
    assert pair.verdict == Verdict.PASS, (
        f"truthful sentence-final rename must PASS, got {pair.verdict} ({_codes(pair)})"
    )
    assert "symbol_renamed" in _codes(pair)


def test_backticked_rename_control_still_passes():
    """Control: the backticked phrasing was never broken (the optional `` `? ``
    terminates the group before the period). It must keep PASSing so the fix
    does not disturb the designed path."""
    pair, new_symbol = _rename_verdict("Done — I renamed `old_handler` to `new_handler`.")
    assert new_symbol == "new_handler"
    assert pair.verdict == Verdict.PASS
    assert "symbol_renamed" in _codes(pair)


def test_lying_rename_with_same_phrasing_still_lies():
    """The fix must not open a false-PASS: a rename claim where the old symbol
    SURVIVES (only an unrelated comment was tweaked) stays LIE. Pre-fix this
    case LIE'd too, but only by accident of the polluted symbol; post-fix it
    LIEs on the clean symbol transition check (old present, new absent)."""
    lying_after = "export function old_handler() {\n  return 1; // note2\n}\n"
    pair, new_symbol = _rename_verdict(
        "Done — I renamed old_handler to new_handler.",
        before="export function old_handler() {\n  return 1; // note\n}\n",
        after=lying_after,
    )
    assert new_symbol == "new_handler"
    assert pair.verdict == Verdict.LIE, (
        f"lying rename (old symbol survives) must stay LIE, got {pair.verdict} "
        f"({_codes(pair)})"
    )
    assert "symbol_not_renamed" in _codes(pair)


def test_rename_new_side_strips_to_empty_falls_back_vague():
    """Fail-soft: when the captured new side is all dots ("renamed `old` to
    ..."), the strip yields an empty identifier; the claim falls back to the
    symbol-scan path (no new_symbol) and the verifier refuses to PASS on diff
    alone (rename_no_symbols -> VAGUE), the same contract as a rename claim
    that never captured both identifiers."""
    pair, new_symbol = _rename_verdict("I renamed `old_handler` to ...")
    assert new_symbol is None
    assert pair.verdict == Verdict.VAGUE
    assert "rename_no_symbols" in _codes(pair)


# --------------------------------------------------------------------------- #
# 2. fix-ci-ruff-lint-red
# --------------------------------------------------------------------------- #
def _find_ruff() -> str | None:
    """Locate the ruff binary of the running environment (CI installs it via
    the `dev` extra); None when absent so the test can skip."""
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.exists():
        return str(candidate)
    return shutil.which("ruff")


def test_lint_clean_under_pinned_ruff():
    """The CI Lint step contract: ``ruff check src tests`` exits 0 under the
    ruff the dev extra resolves (0.16.x at the time of the fix). At v0.10.0
    this exited 1 with 47 errors on every CI run. Skipped only when ruff is
    genuinely unavailable in the environment (CI always installs it)."""
    ruff = _find_ruff()
    if ruff is None:
        import pytest

        pytest.skip("ruff binary not available in this environment")
    result = subprocess.run(
        [ruff, "check", "src", "tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,  # the returncode IS the assertion below
    )
    assert result.returncode == 0, (
        f"ruff check src tests failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_ruff_dev_dependency_is_upper_bounded():
    """Root-cause hygiene: the dev dependency must carry an upper bound so a
    future ruff ruleset promotion cannot silently redden CI (the exact v0.10.0
    failure mode — unpinned ``ruff>=0.6`` floated onto 0.16's expanded default
    ruleset). Bumping the bound is a conscious act that runs the new ruleset
    locally first."""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'"ruff(>=?[\w.]+)(,[<>][\w.]+)?"', text)
    assert m, "ruff dev dependency not found in pyproject.toml"
    assert m.group(2), (
        f"ruff dev dependency must be upper-bounded (e.g. ruff>=0.6,<0.17), "
        f"got un-pinned {m.group(0)!r}"
    )


# --------------------------------------------------------------------------- #
# 3. fix-demo-workflow-push-detached-head
# --------------------------------------------------------------------------- #
def test_demo_workflow_pushes_to_named_branch():
    """Static contract for the demo workflow fix: the gif commit must be pushed
    to an explicit ref (``origin HEAD:main``). A bare ``git push`` on the tag
    trigger's detached HEAD exits 128 — demo.yml failed on EVERY release tag
    v0.1.1..v0.10.0 that way, so the regression guard pins the refspec."""
    text = DEMO_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^\s*git push origin HEAD:main\s*$", text, re.MULTILINE), (
        "demo.yml must push the gif commit with an explicit refspec "
        "(git push origin HEAD:main)"
    )
    assert not re.search(r"^\s*git push\s*$", text, re.MULTILINE), (
        "demo.yml must not run a bare `git push` — on the tag trigger the "
        "checkout is a detached HEAD and the push exits 128"
    )


# --------------------------------------------------------------------------- #
# 4. version lockstep extension (v0.10.0 family, not duplicated)
# --------------------------------------------------------------------------- #
def test_changelog_latest_section_matches_canonical():
    """Extends the v0.10.0 lockstep family to the CHANGELOG: the newest
    ``## [X.Y.Z]`` section must equal the canonical pyproject version, so a
    release cannot ship without documenting itself (the v0.9.0 release shipped
    with no CHANGELOG entry at all)."""
    canonical = re.search(
        r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE
    ).group(1)
    sections = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)
    assert sections, "CHANGELOG.md has no versioned sections"
    assert sections[0] == canonical, (
        f"CHANGELOG latest section {sections[0]!r} != pyproject version {canonical!r}"
    )
