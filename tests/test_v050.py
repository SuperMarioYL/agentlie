"""Regression tests for the v0.5.0 iteration.

Covers three amendment milestones:

  * fix-remove-symbol-present-false-pass — a "remove" claim whose named symbol is
    STILL PRESENT after the edit must NOT verdict PASS. Crediting symbol_present_after
    for a remove verb false-PASSed a lie (the honesty engine's worst failure mode);
    only the genuine present-before / absent-after transition may PASS a remove+symbol
    claim. Inverse-sibling of the v0.3.0 add-symbol false-PASS fix.
  * fix-source-label-sticky-originalfile — edit.source is "originalFile" only for the
    edit that actually consumed the seeded originalFile (the first edit to a seeded
    path); a later edit whose before-state came from cumulative replay is "replay".
  * m8_ast_ruby — Ruby joins Python/TypeScript/Go/Rust/Java for AST verdicts, with no
    new dependency (grammar already in tree-sitter-language-pack).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agentlie.extractor import PATH_PATTERN, extract_claims
from agentlie.models import (
    ActualEdit,
    ClaimEditPair,
    ClaimSpan,
    Turn,
    Verdict,
)
from agentlie.parser import FileStateTracker, parse_session
from agentlie.verifier import (
    ADD_INDICATORS,
    LANG_BY_EXT,
    _lang_for,
    _try_tree_sitter,
    verify_pair,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _verify(verb, *, sym=None, path=None, before="", after="", tool="Edit", epath=None):
    """Build a single-edit ClaimEditPair and verify it; return the resolved pair."""
    edit = ActualEdit(
        tool=tool,
        path=epath or path or "src/a.py",
        before_content=before,
        after_content=after,
    )
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text=f"I {verb}d something", verb=verb, target_symbol=sym, target_path=path),
        edits=[edit],
        verdict=Verdict.VAGUE,
    )
    return verify_pair(pair, FileStateTracker())


def _write_jsonl(records: list[dict]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        return fh.name


# --------------------------------------------------------------------------- #
# fix-remove-symbol-present-false-pass
# --------------------------------------------------------------------------- #
def test_remove_symbol_still_present_with_diff_is_not_pass():
    """A remove claim whose symbol survives a real (unrelated) diff must not PASS."""
    pair = _verify(
        "remove",
        sym="old_debug_call",
        before="def x():\n    old_debug_call()\n    return 1\n",
        after="def x():\n    old_debug_call()\n    return 2\n",
    )
    assert pair.verdict != Verdict.PASS
    codes = [r.code for r in pair.evidence]
    assert "symbol_still_present" in codes
    assert "symbol_present_after" not in codes  # the false-PASS evidence must be gone


def test_remove_symbol_still_present_noop_is_lie():
    """A pure noop remove (symbol untouched, no diff) is a LIE, not PASS."""
    pair = _verify(
        "remove",
        sym="old_debug_call",
        before="old_debug_call()\nx = 1\n",
        after="old_debug_call()\nx = 1\n",
    )
    assert pair.verdict == Verdict.LIE
    assert "symbol_still_present" in [r.code for r in pair.evidence]


def test_remove_symbol_still_present_non_ast_file_is_not_pass():
    """On a non-AST file (no tree-sitter language), a still-present remove is VAGUE,
    never PASS — the string path alone must not carry a remove verdict."""
    pair = _verify(
        "remove",
        sym="old_debug_call",
        before="# doc\nold_debug_call here\n",
        after="# doc\nold_debug_call here still\n",
        path="notes.md",
        epath="notes.md",
    )
    assert pair.verdict != Verdict.PASS
    assert "symbol_still_present" in [r.code for r in pair.evidence]


def test_remove_symbol_genuinely_removed_still_passes():
    """Control: the honest removal (present before, absent after) still PASSes."""
    pair = _verify(
        "remove",
        sym="old_debug_call",
        before="def x():\n    old_debug_call()\n    return 1\n",
        after="def x():\n    return 1\n",
    )
    assert pair.verdict == Verdict.PASS
    assert "symbol_removed" in [r.code for r in pair.evidence]


def test_add_symbol_still_uses_introduced_guard():
    """Sibling regression: the v0.3.0 add-symbol guard is untouched by this fix."""
    # pre-existing symbol, only an unrelated edit -> not PASS on string evidence
    pair = _verify(
        "add",
        sym="helper",
        before="def helper():\n    pass\n\nx = 1\n",
        after="def helper():\n    pass\n\nx = 2\n",
    )
    assert "symbol_preexisting" in [r.code for r in pair.evidence]
    assert "symbol_introduced" not in [r.code for r in pair.evidence]


# --------------------------------------------------------------------------- #
# fix-source-label-sticky-originalfile
# --------------------------------------------------------------------------- #
def test_source_label_second_edit_to_seeded_path_is_replay():
    """The first edit to a path with a seeded originalFile is 'originalFile'; the
    second edit (before-state from cumulative replay) is 'replay', not the sticky
    'originalFile'."""
    records = [
        {
            "uuid": "a1",
            "parentUuid": None,
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I edited foo.py"},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "id": "t1",
                        "input": {"file_path": "foo.py", "old_string": "A", "new_string": "B"},
                    },
                ],
            },
        },
        {
            "uuid": "u1",
            "parentUuid": "a1",
            "type": "user",
            "toolUseResult": {"tool_use_id": "t1", "filePath": "foo.py", "originalFile": "A\nkeep\n"},
        },
        {
            "uuid": "a2",
            "parentUuid": "u1",
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I edited foo.py again"},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "id": "t2",
                        "input": {"file_path": "foo.py", "old_string": "keep", "new_string": "changed"},
                    },
                ],
            },
        },
    ]
    path = _write_jsonl(records)
    try:
        turns, _ = parse_session(path)
    finally:
        Path(path).unlink(missing_ok=True)

    edits = [e for t in turns for e in t.tool_calls if e.path == "foo.py"]
    assert len(edits) == 2
    assert edits[0].source == "originalFile"
    assert edits[0].before_content == "A\nkeep\n"  # the seeded ground truth
    assert edits[1].source == "replay"  # NOT the sticky 'originalFile'
    assert edits[1].before_content != "A\nkeep\n"  # replayed cumulative before


def test_source_label_tracker_helper():
    """Unit-level: is_original_before is True only for the seeded value."""
    tracker = FileStateTracker()
    tracker.seed_original("x.py", "ORIG")
    assert tracker.is_original_before("x.py", "ORIG") is True
    assert tracker.is_original_before("x.py", "REPLAYED") is False
    assert tracker.is_original_before("unseeded.py", "ORIG") is False


# --------------------------------------------------------------------------- #
# m8_ast_ruby
# --------------------------------------------------------------------------- #
def test_ruby_lang_mapping_and_grammar_load():
    assert LANG_BY_EXT[".rb"] == "ruby"
    assert _lang_for("app.rb") == "ruby"
    # Grammar ships in the installed tree-sitter-language-pack — no new dependency.
    assert _try_tree_sitter("ruby") is not None


def test_ruby_add_method_passes():
    """A Ruby 'add method' claim with a real method added → PASS via ast_add."""
    pair = _verify(
        "add",
        path="app.rb",
        epath="app.rb",
        before="class Foo\nend\n",
        after="class Foo\n  def bar\n    1\n  end\nend\n",
    )
    assert pair.verdict == Verdict.PASS
    assert "ast_add" in [r.code for r in pair.evidence]


def test_ruby_add_no_structural_change_no_diff_is_lie():
    """A Ruby 'add' claim with no structural change and no diff → LIE."""
    pair = _verify(
        "add",
        path="app.rb",
        epath="app.rb",
        before="x = 1\n",
        after="x = 1\n",
    )
    assert pair.verdict == Verdict.LIE


def test_ruby_node_types_in_add_indicators():
    for node_type in ("method", "singleton_method", "class", "module"):
        assert node_type in ADD_INDICATORS


def test_path_pattern_recognizes_rb():
    m = PATH_PATTERN.search("I edited lib/foo.rb here")
    assert m is not None
    assert m.group("path") == "lib/foo.rb"
    # extractor end-to-end: a Ruby path is extracted as a claim target.
    turn = Turn(
        turn_id=1,
        uuid="t1",
        assistant_text="I added a method to app.rb",
        tool_calls=[ActualEdit(tool="Edit", path="app.rb")],
    )
    pairs = extract_claims([turn])
    assert any(p.claim.target_path == "app.rb" for p in pairs)
