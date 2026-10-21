"""Regression tests for the v0.12.0 iteration.

Covers the four fix milestones from the amend-scan-2026-05-26-0206-v0.12.0
bug hunt (evidence: amendments/_grill_v0.12.0/_bug_findings.yaml and the
reproduction scripts under amendments/_grill_v0.12.0/repro/):

  * fix-add-remove-symbol-guard-bypassed-by-path — extractor.py nulled the
    backticked symbol for add/remove whenever the claim also named a file, so
    the verifier's symbol-level guard (verifier.py:410, the v0.3.0 add /
    v0.5.0 remove contracts) never fired for the commonest phrasing and a lying
    "I added `foo` to a.py" (foo pre-existed, an unrelated symbol added)
    false-PASSed on the symbol-agnostic AST count.

  * fix-target-selection-nondeterministic — _scan_target_path iterated
    ``set(candidate_paths)``, so a sentence naming more than one edited file
    resolved to a different target per process (PYTHONHASHSEED), making the
    verdict and the ``--fail-on-lie`` exit code non-reproducible.

  * fix-sentence-final-path-mention — _sentence_mentions_base rejected the
    sentence-final period, so a path ending its sentence was never recognised
    by the edited-path preference steps; for an extension outside PATH_PATTERN
    the claim lost its target entirely and verdicted VAGUE.

  * fix-project-urls-dead-owner — pyproject.toml [project.urls] pointed at
    github.com/supermario-leo/agentlie (HTTP 404); the canonical repository is
    github.com/SuperMarioYL/agentlie.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from agentlie.extractor import _sentence_mentions_base, extract_claims
from agentlie.models import ActualEdit, Turn, Verdict
from agentlie.parser import FileStateTracker
from agentlie.verifier import verify_pair

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _verify(text: str, edits: list[ActualEdit]) -> object:
    """extract + verify a single-turn session; return the first pair."""
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


# --------------------------------------------------------------------------- #
# 1. fix-add-remove-symbol-guard-bypassed-by-path
# --------------------------------------------------------------------------- #
ADD_BEFORE = "def foo():\n    return 1\n"
ADD_AFTER = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
REMOVE_BEFORE = "def legacy_token():\n    return 1\n\ndef other():\n    return 2\n"
REMOVE_AFTER = "def legacy_token():\n    return 1\n"


def test_lying_add_with_path_is_not_pass():
    """REPRO of the v0.12.0 defect: "I added `foo` to a.py." where `foo` was
    already present and an UNRELATED `bar` was added. Pre-fix the extractor
    dropped the backticked symbol (a path was named), so the claim PASSed on
    the symbol-agnostic ast_add count. The symbol-level guard must now apply."""
    pair = _verify("I added `foo` to a.py.", [_edit("a.py", ADD_BEFORE, ADD_AFTER)])
    assert pair.claim.target_symbol == "foo", (
        f"the backticked symbol must survive alongside the path, got {pair.claim.target_symbol!r}"
    )
    assert pair.verdict != Verdict.PASS, (
        f"a lying add (foo pre-existed, unrelated bar added) must not PASS, got {pair.verdict} ({_codes(pair)})"
    )
    assert "symbol_preexisting" in _codes(pair)


def test_lying_remove_with_path_is_not_pass():
    """Mirror REPRO: "I removed `legacy_token` from a.py." where the symbol
    SURVIVES and an unrelated `other` was removed. Pre-fix this false-PASSed on
    ast_remove — the exact hole the v0.5.0 remove contract declared closed."""
    pair = _verify(
        "I removed `legacy_token` from a.py.", [_edit("a.py", REMOVE_BEFORE, REMOVE_AFTER)]
    )
    assert pair.claim.target_symbol == "legacy_token"
    assert pair.verdict != Verdict.PASS, (
        f"a lying remove (symbol still present) must not PASS, got {pair.verdict} ({_codes(pair)})"
    )
    assert "symbol_still_present" in _codes(pair)


def test_truthful_add_of_new_symbol_with_path_still_passes():
    """The fix must not overcorrect: a truthful "I added `foo` to a.py" whose
    symbol is genuinely newly introduced still PASSes on the symbol-level
    transition (not on the bare AST count)."""
    pair = _verify(
        "I added `foo` to a.py.",
        [_edit("a.py", "def bar():\n    return 2\n", "def bar():\n    return 2\n\ndef foo():\n    return 1\n")],
    )
    assert pair.verdict == Verdict.PASS, f"got {pair.verdict} ({_codes(pair)})"
    assert "symbol_introduced" in _codes(pair)


def test_truthful_remove_of_named_symbol_with_path_still_passes():
    """Symmetric control: a truthful "I removed `legacy_token` from a.py" whose
    symbol really disappears still PASSes on symbol_removed."""
    pair = _verify(
        "I removed `legacy_token` from a.py.",
        [_edit("a.py", "def legacy_token():\n    return 1\n\ndef keep():\n    pass\n", "def keep():\n    pass\n")],
    )
    assert pair.verdict == Verdict.PASS, f"got {pair.verdict} ({_codes(pair)})"
    assert "symbol_removed" in _codes(pair)


# --------------------------------------------------------------------------- #
# 2. fix-target-selection-nondeterministic
# --------------------------------------------------------------------------- #
_DETERMINISM_SNIPPET = (
    "import sys; sys.path.insert(0, 'src');"
    "from agentlie.extractor import _scan_target_path;"
    "print(_scan_target_path('I fixed zeta.py and alpha.py and mid.py and beta.py and gamma.py.',"
    " ['zeta.py','alpha.py','mid.py','beta.py','gamma.py']))"
)


def test_multi_path_target_is_stable_across_hash_seeds():
    """REPRO of the v0.12.0 defect: the chosen target for a sentence naming
    several edited files depended on PYTHONHASHSEED (a set iteration order), so
    the same session classified differently per process. Two distinct hash
    seeds must now yield the identical target."""
    results = []
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_SNIPPET],
            cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=True,
        )
        results.append(out.stdout.strip())
    assert len(set(results)) == 1, f"target selection is not deterministic: {results}"
    assert results[0] != "None"


# --------------------------------------------------------------------------- #
# 3. fix-sentence-final-path-mention
# --------------------------------------------------------------------------- #
def test_sentence_final_path_is_recognised_as_a_mention():
    """REPRO of the v0.12.0 defect: the mention boundary rejected the
    sentence-final period, so an edited path ending its sentence was never
    matched by the preference steps."""
    assert _sentence_mentions_base("I fixed src/auth.py.", "src/auth.py") is True
    assert _sentence_mentions_base("I fixed the bug in auth.py.", "auth.py") is True


def test_sentence_final_path_without_path_pattern_extension_keeps_its_target():
    """The user-visible consequence: a truthful claim about a file whose
    extension is outside PATH_PATTERN loses its target when it ends the sentence
    and verdicits VAGUE(no_target). With the boundary fixed the target is
    recovered, which is a real verdict improvement for the AST-covered ``.pyi``
    extension (PASS instead of VAGUE), and for the remaining unsupported
    extensions (.sh/.c/.css) the claim is at least attributed to its file
    instead of reported as untargeted."""
    # `.pyi` is AST-covered but absent from PATH_PATTERN, so before the fix the
    # sentence-final mention was lost AND the fallback could not recover it:
    # target_path=None -> VAGUE(no_target). Now it resolves and PASSes.
    pair = _verify("I fixed the stub in types.pyi.", [_edit("types.pyi", "def f(): pass\n", "def f():\n    return 1\n")])
    assert pair.claim.target_path == "types.pyi", f"lost its target: {pair.claim.target_path!r}"
    assert pair.verdict == Verdict.PASS, f"got {pair.verdict} ({_codes(pair)})"

    # Unsupported extensions have no AST coverage, so the verdict stays VAGUE —
    # but the target must now be named rather than dropped as no_target.
    for claim, path in [
        ("I fixed the deploy script in deploy.sh.", "deploy.sh"),
        ("I fixed the bug in main.c.", "main.c"),
        ("I fixed the styles in app.css.", "app.css"),
    ]:
        pair = _verify(claim, [_edit(path, "a", "b")])
        assert pair.claim.target_path == path, (
            f"{claim!r} lost its target: got {pair.claim.target_path!r}"
        )
        assert "no_target" not in _codes(pair), f"{claim!r} -> {_codes(pair)}"


def test_dotted_word_boundary_still_rejects_substring_matches():
    """Negative case the boundary class exists for: `config.py` must NOT be
    treated as a mention inside the unrelated token `reconfig.python`, and
    `utils.py` must not match `utils.pyc`."""
    assert _sentence_mentions_base("see reconfig.python for details", "config.py") is False
    assert _sentence_mentions_base("open utils.pyc now", "utils.py") is False
    assert _sentence_mentions_base("I touched test_utils.py", "utils.py") is False


# --------------------------------------------------------------------------- #
# 4. fix-project-urls-dead-owner
# --------------------------------------------------------------------------- #
CANONICAL_REPO = "github.com/SuperMarioYL/agentlie"


def test_project_urls_point_at_the_canonical_repository():
    """REPRO of the v0.12.0 defect: [project.urls] advertised
    github.com/supermario-leo/agentlie, which returns HTTP 404, so every
    published wheel/sdist carried dead Homepage/Repository/Issues links."""
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"\[project\.urls\](.*?)(?=\n\[|\Z)", text, re.DOTALL)
    assert block, "[project.urls] not found in pyproject.toml"
    values = re.findall(r'=\s*"([^"]+)"', block.group(1))
    assert values, "no URLs declared under [project.urls]"
    for url in values:
        assert CANONICAL_REPO in url, f"project URL does not point at the canonical repo: {url}"
    assert "supermario-leo" not in block.group(1), (
        "stale supermario-leo owner still present under [project.urls]"
    )
