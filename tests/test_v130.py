"""Regression tests for the v0.13.0 iteration.

Covers the four defects reproduced from the shipped v0.12.0 source (evidence:
amendments/_grill_v0.13.0/bug_hunt.yaml, repro/repro_v013.py):

  * fix-backticked-symbol-tokenization — SYMBOL_PATTERN only matched a bare
    identifier inside backticks, so `foo()` lost the symbol (v0.12.0's
    add/remove symbol guard bypassed -> false PASS on a lying add) while
    `utils.py` was accepted AS a symbol (genuine add forced to VAGUE).

  * fix-negated-claims-extracted-as-assertions — "I did not modify a.py" was
    extracted as an ordinary update claim and verdicted LIE (path_untouched):
    a false accusation against a truthful denial.

  * fix-vague-verdict-without-evidence — a symbol-only claim in a turn with no
    Edit/Write resolved VAGUE with an empty evidence list.

  * fix-json-summary-drops-zero-verdicts — --json emitted only the verdict keys
    that occurred, so summary["LIE"] raised KeyError on a clean session.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentlie import __version__
from agentlie.extractor import extract_claims
from agentlie.models import ActualEdit, Turn, Verdict
from agentlie.parser import FileStateTracker
from agentlie.report import as_json
from agentlie.verifier import verify_pair

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE_JSON = REPO_ROOT / "web" / "site.json"


def _pair(text: str, edits: list[ActualEdit]):
    turns = [Turn(turn_id=1, uuid="t1", assistant_text=text, tool_calls=edits)]
    pairs = extract_claims(turns)
    assert pairs, "no claim extracted"
    verify_pair(pairs[0], FileStateTracker())
    return pairs[0]


def _codes(pair) -> list[str]:
    return [r.code for r in pair.evidence]


def _edit(path: str, before: str, after: str) -> ActualEdit:
    return ActualEdit(
        tool="Edit", path=path, old_string="x", new_string="y",
        before_content=before, after_content=after,
    )


FOO_BEFORE = "def foo():\n    return 1\n"
FOO_AFTER = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"


# --------------------------------------------------------------------------- #
# 1. fix-backticked-symbol-tokenization
# --------------------------------------------------------------------------- #
def test_call_form_symbol_keeps_guard():
    """REPRO: "I added `foo()` to a.py." where foo pre-existed and an unrelated
    bar was added. Pre-fix the call form yielded no symbol, so the claim PASSed
    on the symbol-agnostic ast_add count; the symbol guard must apply."""
    pair = _pair("I added `foo()` to a.py.", [_edit("a.py", FOO_BEFORE, FOO_AFTER)])

    assert pair.claim.target_symbol == "foo"
    assert pair.verdict != Verdict.PASS, _codes(pair)
    assert "symbol_preexisting" in _codes(pair)


def test_backticked_filename_is_not_symbol():
    """REPRO: "I added validation to `utils.py`." with a real structural add.
    Pre-fix the backticked file name became target_symbol, which forces
    ast_pass=False for symbol add/remove claims and degraded PASS to VAGUE."""
    before = "def main():\n    pass\n"
    after = "def main():\n    pass\n\ndef parse_args():\n    pass\n"
    pair = _pair("I added validation to `utils.py`.", [_edit("utils.py", before, after)])

    assert pair.claim.target_path == "utils.py"
    assert pair.claim.target_symbol is None
    assert pair.verdict == Verdict.PASS, _codes(pair)


def test_backticked_call_form_new_symbol_passes():
    """The guard must not over-correct: a truthful call-form add of a NEW symbol
    still PASSes on symbol_introduced."""
    before = "def other():\n    return 1\n"
    after = "def other():\n    return 1\n\ndef helper():\n    return 2\n"
    pair = _pair("I added `helper()` to a.py.", [_edit("a.py", before, after)])

    assert pair.claim.target_symbol == "helper"
    assert pair.verdict == Verdict.PASS, _codes(pair)
    assert "symbol_introduced" in _codes(pair)


# --------------------------------------------------------------------------- #
# 2. fix-negated-claims-extracted-as-assertions
# --------------------------------------------------------------------------- #
def test_negated_claim_is_not_extracted():
    """REPRO: a truthful denial that names an untouched file was extracted as a
    claim and verdicted LIE (path_untouched), flipping --fail-on-lie."""
    for text in ("I did not modify src/app.py.", "I didn't fix src/app.py.",
                 "I never updated src/app.py."):
        turns = [Turn(turn_id=1, uuid="t1", assistant_text=text, tool_calls=[])]
        assert extract_claims(turns) == [], text


def test_negation_guard_is_clause_local():
    """A denial in one clause must not hide a real claim in the next."""
    pair = _pair(
        "I did not modify src/app.py but I fixed src/other.py.",
        [_edit("src/other.py", "a = 1\n", "a = 2\n")],
    )

    assert pair.claim.verb == "fix"
    assert pair.claim.target_path == "src/other.py"
    assert pair.verdict == Verdict.PASS, _codes(pair)


# --------------------------------------------------------------------------- #
# 3. fix-vague-verdict-without-evidence
# --------------------------------------------------------------------------- #
def test_symbol_claim_without_edits_has_reason():
    """REPRO: a symbol-only claim in a zero-edit turn resolved VAGUE with an
    empty evidence list, so the report explained nothing."""
    pair = _pair("I added `helper` to the module.", [])

    assert pair.verdict == Verdict.VAGUE
    assert pair.evidence, "VAGUE must carry a reason"
    assert "no_matching_edits" in _codes(pair)


# --------------------------------------------------------------------------- #
# 4. fix-json-summary-drops-zero-verdicts
# --------------------------------------------------------------------------- #
def test_json_summary_has_all_verdict_keys():
    """REPRO: a PASS-only session serialised summary as {"PASS": 1}; consumers
    reading summary["LIE"] raised KeyError."""
    pair = _pair("I fixed a.py.", [_edit("a.py", "a = 1\n", "a = 2\n")])
    payload = json.loads(as_json([pair]))

    assert payload["summary"] == {"PASS": 1, "VAGUE": 0, "LIE": 0}


# --------------------------------------------------------------------------- #
# 5. version lockstep
# --------------------------------------------------------------------------- #
def test_version_lockstep():
    """Every shipped version surface names the same version."""
    py_version = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8")
    )
    assert py_version is not None
    assert py_version.group(1) == __version__

    for readme in ("README.md", "README.en.md"):
        text = (REPO_ROOT / readme).read_text(encoding="utf-8")
        assert f"v{__version__}" in text, readme

    site = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    assert site["content_version"] == __version__
    assert site["meta"]["content_version"] == __version__
