"""Regression tests for the v0.4.0 iteration.

Covers three amendment milestones:

  * fix-codex-update-loses-before-state — the Codex apply_patch adapter now
    reconstructs BOTH the before-state (context + '-' lines) and the after-state
    (context + '+' lines) from an Update hunk, so removal claims can PASS and the
    pre-existing-symbol guard fires on Codex transcripts.
  * fix-extractor-basename-substring-false-target — the extractor's target-path
    resolution matches a basename only at a real path-token boundary, so
    ``config.py`` no longer matches inside ``reconfig.python``.
  * m7_ast_java — Java joins Python/TypeScript/Go/Rust for AST verdicts.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agentlie.codex import _edits_from_patch, parse_codex_session
from agentlie.extractor import (
    _scan_target_path,
    _sentence_mentions_base,
    extract_claims,
)
from agentlie.verifier import (
    ADD_INDICATORS,
    LANG_BY_EXT,
    _ast_delta,
    _lang_for,
    _try_tree_sitter,
    verify_session,
)
from agentlie.models import Verdict


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _run_codex(records: list[dict]):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        path = fh.name
    try:
        turns, tracker = parse_codex_session(path)
    finally:
        Path(path).unlink(missing_ok=True)
    pairs = extract_claims(turns)
    verify_session(pairs, turns, tracker)
    return pairs


# --------------------------------------------------------------------------- #
# fix-codex-update-loses-before-state
# --------------------------------------------------------------------------- #
def test_codex_update_reconstructs_before_state():
    """An Update hunk's '-' lines must survive into ``before_content``."""
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/app.py\n"
        "@@\n"
        " def helper():\n"
        "-    old_debug_call()\n"
        "+    pass\n"
        "*** End Patch"
    )
    edits = _edits_from_patch(patch)
    assert len(edits) == 1
    edit = edits[0]
    # before = context + '-' ; after = context + '+'
    assert "old_debug_call" in (edit.before_content or "")
    assert "old_debug_call" not in (edit.after_content or "")
    assert "pass" in (edit.after_content or "")


def test_codex_add_hunk_has_empty_before():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n"
        "+def brand_new():\n"
        "+    return 1\n"
        "*** End Patch"
    )
    edit = _edits_from_patch(patch)[0]
    assert edit.before_content == ""
    assert "brand_new" in (edit.after_content or "")


def test_codex_delete_hunk_keeps_before_empties_after():
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: src/gone.py\n"
        "-def doomed():\n"
        "-    return 0\n"
        "*** End Patch"
    )
    edit = _edits_from_patch(patch)[0]
    assert "doomed" in (edit.before_content or "")
    assert edit.after_content == ""


def test_codex_removal_claim_can_pass():
    """Before v0.4.0 a genuine Codex removal verdicted VAGUE (before_content=None);
    with the before-state reconstructed it PASSes via symbol_removed."""
    patch = (
        "*** Begin Patch\n"
        "*** Update File: app_module\n"
        "@@\n"
        " def helper():\n"
        "-    old_debug_call()\n"
        "+    pass\n"
        "*** End Patch"
    )
    pairs = _run_codex(
        [
            {"type": "message", "role": "assistant",
             "content": "I removed `old_debug_call`"},
            {"type": "function_call", "name": "apply_patch",
             "arguments": {"input": patch}},
        ]
    )
    removal = [p for p in pairs if p.claim.verb == "remove"]
    assert removal, "expected a remove claim"
    assert removal[0].verdict == Verdict.PASS
    assert any(r.code == "symbol_removed" for r in removal[0].evidence)


def test_codex_add_of_preexisting_symbol_not_pass():
    """The v0.3.0 pre-existing-symbol guard must now fire on Codex too: a symbol
    the patch's CONTEXT lines show already existed is not a valid 'add'."""
    patch = (
        "*** Begin Patch\n"
        "*** Update File: app_module\n"
        "@@\n"
        " def helper():\n"
        "     existing_symbol()\n"
        "+    x = 1\n"
        "*** End Patch"
    )
    pairs = _run_codex(
        [
            {"type": "message", "role": "assistant",
             "content": "I added `existing_symbol`"},
            {"type": "function_call", "name": "apply_patch",
             "arguments": {"input": patch}},
        ]
    )
    add = [p for p in pairs if p.claim.verb == "add"]
    assert add, "expected an add claim"
    assert add[0].verdict != Verdict.PASS
    assert any(r.code == "symbol_preexisting" for r in add[0].evidence)


def test_codex_add_of_new_symbol_passes():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: app_module\n"
        "@@\n"
        " def helper():\n"
        "+    brand_new()\n"
        "*** End Patch"
    )
    pairs = _run_codex(
        [
            {"type": "message", "role": "assistant",
             "content": "I added `brand_new`"},
            {"type": "function_call", "name": "apply_patch",
             "arguments": {"input": patch}},
        ]
    )
    add = [p for p in pairs if p.claim.verb == "add"]
    assert add and add[0].verdict == Verdict.PASS
    assert any(r.code == "symbol_introduced" for r in add[0].evidence)


# --------------------------------------------------------------------------- #
# fix-extractor-basename-substring-false-target
# --------------------------------------------------------------------------- #
def test_basename_boundary_predicate():
    # substring inside an unrelated word must NOT match
    assert _sentence_mentions_base("see reconfig.python notes", "config.py") is False
    # utils.py must not match inside test_utils.py / foo_test_utils.py
    assert _sentence_mentions_base("edited test_utils.py", "utils.py") is False
    assert _sentence_mentions_base("foo_test_utils.py touched", "utils.py") is False
    # legitimate whole-token mentions DO match
    assert _sentence_mentions_base("I fixed utils.py handling", "utils.py") is True
    assert _sentence_mentions_base("touched a/config.py now", "config.py") is True
    assert _sentence_mentions_base("edited src/app.py here", "src/app.py") is True
    assert _sentence_mentions_base("the __init__.py file", "__init__.py") is True


def test_scan_target_path_rejects_substring_false_match():
    # The candidate (edited) path must NOT be attributed when its basename only
    # appears as a substring of an unrelated token.
    #   'config.py' inside 'reconfig.python' -> no candidate match, and PATH_PATTERN's
    #   trailing guard stops it extracting a bogus 'reconfig.py' either.
    assert _scan_target_path("see reconfig.python notes", ["config.py"]) is None
    # The candidate 'utils.py' must NOT be attributed to a mention of test_utils.py;
    # the returned path (if any) must be the ACTUALLY-named file, never the candidate.
    got = _scan_target_path("edited test_utils.py", ["utils.py"])
    assert got != "utils.py"
    assert got in (None, "test_utils.py")


def test_scan_target_path_still_matches_real_mentions():
    assert _scan_target_path("I fixed utils.py handling", ["src/utils.py"]) == "src/utils.py"
    assert _scan_target_path("touched a/config.py now", ["config.py"]) == "config.py"
    assert _scan_target_path("edited src/app.py here", ["src/app.py"]) == "src/app.py"


# --------------------------------------------------------------------------- #
# m7_ast_java
# --------------------------------------------------------------------------- #
def test_java_registered_in_lang_map():
    assert LANG_BY_EXT.get(".java") == "java"
    assert _lang_for("src/main/java/Foo.java") == "java"


def test_java_add_method_is_structural():
    parser = _try_tree_sitter("java")
    if parser is None:
        import pytest

        pytest.skip("java grammar unavailable")
    before = "class A {\n  void m(){ int x = 1; }\n}\n"
    after = "class A {\n  void m(){ int x = 1; }\n  void n(){ int y = 2; }\n}\n"
    delta = _ast_delta(parser, before, after)
    added = sum(v for k, v in delta.items() if k in ADD_INDICATORS and v > 0)
    assert added > 0, f"expected a structural add in the Java delta, got {delta}"


def test_java_add_claim_pass_and_lie():
    """A truthful Java 'add method' claim PASSes; a bare no-diff add LIEs — the
    same AST-delta path the other four languages use."""
    parser = _try_tree_sitter("java")
    if parser is None:
        import pytest

        pytest.skip("java grammar unavailable")

    from agentlie.models import ActualEdit, ClaimEditPair, ClaimSpan
    from agentlie.parser import FileStateTracker
    from agentlie.verifier import verify_pair

    tracker = FileStateTracker()

    # PASS: a real new method added to Foo.java
    e_pass = ActualEdit(
        tool="Edit",
        path="Foo.java",
        before_content="class Foo {\n  void a(){}\n}\n",
        after_content="class Foo {\n  void a(){}\n  void b(){}\n}\n",
    )
    p_pass = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="added a method to Foo.java", verb="add",
                        target_path="Foo.java"),
        edits=[e_pass],
    )
    verify_pair(p_pass, tracker)
    assert p_pass.verdict == Verdict.PASS

    # LIE: an add claim on Foo.java with no structural change and no diff
    e_lie = ActualEdit(
        tool="Edit",
        path="Foo.java",
        before_content="class Foo {\n  void a(){}\n}\n",
        after_content="class Foo {\n  void a(){}\n}\n",
    )
    p_lie = ClaimEditPair(
        turn_id=2,
        claim=ClaimSpan(text="added a method to Foo.java", verb="add",
                        target_path="Foo.java"),
        edits=[e_lie],
    )
    verify_pair(p_lie, tracker)
    assert p_lie.verdict == Verdict.LIE
