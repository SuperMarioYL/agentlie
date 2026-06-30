"""Regression tests for the v0.3.0 iteration.

v0.3.0 is a fix-focused bump: five verified correctness defects found by a
source-inspection bug hunt (no external feature/bug-report demand had arrived
~10 days post-ship). Each test below locks in one fix.

  fix-add-remove-false-lie-nonstructural — a truthful add/remove of a
      non-structural statement (print/log/assignment/return/comment) must NOT
      verdict LIE; it resolves to PASS (string evidence) or VAGUE, never LIE.
  fix-add-symbol-false-pass-preexisting — an "I added X" claim where X already
      existed before the edit must NOT PASS on mere presence-after.
  fix-matching-edits-basename-false-match — the basename fallback must be
      path-segment-safe (utils.py must not match test_utils.py; __init__.py must
      not match a different package's __init__.py).
  fix-replace-all-not-modeled — an Edit with replace_all=True must replace every
      occurrence in the replayed after-content, not just the first.
  fix-parse-subcommand-ignores-codex — `agentlie parse` honors Codex logs
      (routes through parse_any) instead of silently yielding 0 turns.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agentlie.cli import main
from agentlie.models import ActualEdit, ClaimEditPair, ClaimSpan, Verdict
from agentlie.parser import FileStateTracker
from agentlie.verifier import _matching_edits, verify_pair


# --------------------------------------------------------------------------- #
# fix-add-remove-false-lie-nonstructural
# --------------------------------------------------------------------------- #
def _add_pair(edit: ActualEdit, text: str = "Added a debug print to app.py") -> ClaimEditPair:
    return ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text=text, verb="add", target_path="app.py"),
        edits=[edit],
    )


def test_add_nonstructural_print_is_not_lie():
    """Adding a real print statement changes no ADD_INDICATOR node, but the edit
    DID happen — verdict must not be LIE (the worst honesty-tool failure mode)."""
    before = "def f():\n    return 1\n"
    after = "def f():\n    print('debug')\n    return 1\n"
    edit = ActualEdit(
        tool="Edit", path="app.py", before_content=before, after_content=after,
    )
    pair = _add_pair(edit)
    verify_pair(pair, FileStateTracker())
    assert pair.verdict != Verdict.LIE, [r.code for r in pair.evidence]
    # A real diff with no structural add resolves to VAGUE (we refuse LIE without
    # ground truth that contradicts the verb).
    assert pair.verdict == Verdict.VAGUE
    assert any("ast_no_add_but_diff" in r.code for r in pair.evidence)


def test_remove_nonstructural_statement_is_not_lie():
    """Removing a real assignment line changes no REMOVE_INDICATOR node but is a
    genuine edit — must not LIE."""
    before = "def f():\n    x = 1\n    return 1\n"
    after = "def f():\n    return 1\n"
    edit = ActualEdit(
        tool="Edit", path="app.py", before_content=before, after_content=after,
    )
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="Removed the temp assignment from app.py", verb="remove", target_path="app.py"),
        edits=[edit],
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict != Verdict.LIE, [r.code for r in pair.evidence]
    assert any("ast_no_remove_but_diff" in r.code for r in pair.evidence)


def test_add_with_zero_diff_still_lies():
    """The fix must not over-correct: an 'add' claim whose only edit is a noop
    (no textual diff) is still a LIE."""
    same = "def f():\n    return 1\n"
    edit = ActualEdit(
        tool="Edit", path="app.py", before_content=same, after_content=same,
    )
    pair = _add_pair(edit)
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.LIE, [r.code for r in pair.evidence]


# --------------------------------------------------------------------------- #
# fix-add-symbol-false-pass-preexisting
# --------------------------------------------------------------------------- #
def test_add_symbol_preexisting_does_not_pass():
    """An 'I added helper' claim must NOT PASS when helper() already existed and
    the agent edited an unrelated line (a missed lie)."""
    before = "def helper():\n    return 1\n\ndef other():\n    return 2\n"
    after = "def helper():\n    return 1\n\ndef other():\n    return 3\n"
    edit = ActualEdit(
        tool="Edit", path="lib.py", before_content=before, after_content=after,
    )
    # No target_path match → claim names a symbol only.
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="I added helper", verb="add", target_symbol="helper"),
        edits=[edit],
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict != Verdict.PASS, [r.code for r in pair.evidence]
    assert any("symbol_preexisting" in r.code for r in pair.evidence)


def test_add_symbol_newly_introduced_passes():
    """The complement: a genuinely-new symbol still PASSes on string evidence."""
    before = "def other():\n    return 2\n"
    after = "def helper():\n    return 1\n\ndef other():\n    return 2\n"
    edit = ActualEdit(
        tool="Edit", path="lib.py", before_content=before, after_content=after,
    )
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="I added helper", verb="add", target_symbol="helper"),
        edits=[edit],
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.PASS
    assert any("symbol_introduced" in r.code for r in pair.evidence)


# --------------------------------------------------------------------------- #
# fix-matching-edits-basename-false-match
# --------------------------------------------------------------------------- #
def test_basename_fallback_does_not_match_substring_file():
    """target 'utils.py' must NOT match 'test/test_utils.py' on the basename
    fallback — that would let a different file's edit back the claim."""
    edit = ActualEdit(tool="Edit", path="test/test_utils.py",
                      before_content="a", after_content="b")
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="fixed utils.py", verb="fix", target_path="utils.py"),
        edits=[edit],
    )
    assert _matching_edits(pair) == []
    # And the end-to-end verdict is LIE (path untouched), not a false PASS.
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.LIE


def test_basename_fallback_does_not_cross_package_init():
    """target '__init__.py' must not match a different package's __init__.py."""
    edit = ActualEdit(tool="Edit", path="pkgb/__init__.py",
                      before_content="a", after_content="b")
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="fixed pkga/__init__.py", verb="fix",
                        target_path="pkga/__init__.py"),
        edits=[edit],
    )
    assert _matching_edits(pair) == []


def test_basename_fallback_still_matches_at_path_boundary():
    """Sanity: a real path-boundary basename match still works."""
    edit = ActualEdit(tool="Edit", path="src/pkg/utils.py",
                      before_content="a", after_content="b")
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="fixed utils.py", verb="fix", target_path="utils.py"),
        edits=[edit],
    )
    assert _matching_edits(pair) == [edit]


# --------------------------------------------------------------------------- #
# fix-replace-all-not-modeled
# --------------------------------------------------------------------------- #
def test_replace_all_replaces_every_occurrence():
    """An Edit with replace_all=True must replace all occurrences in the replayed
    after-content; default (False) replaces only the first."""
    tracker = FileStateTracker()
    tracker.seed_original("m.py", "foo()\nfoo()\nfoo()\n")
    edit = ActualEdit(tool="Edit", path="m.py", old_string="foo()",
                      new_string="bar()", replace_all=True)
    before, after = tracker.apply_edit(edit)
    assert after == "bar()\nbar()\nbar()\n", after
    assert "foo()" not in after


def test_first_occurrence_only_when_replace_all_false():
    tracker = FileStateTracker()
    tracker.seed_original("m.py", "foo()\nfoo()\nfoo()\n")
    edit = ActualEdit(tool="Edit", path="m.py", old_string="foo()",
                      new_string="bar()", replace_all=False)
    _, after = tracker.apply_edit(edit)
    assert after == "bar()\nfoo()\nfoo()\n", after


def test_replace_all_flag_parsed_from_tool_input(tmp_path):
    """End-to-end: the replace_all input flag is carried onto ActualEdit."""
    from agentlie.parser import parse_session

    rec = {
        "uuid": "a1",
        "parentUuid": None,
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Renamed foo to bar everywhere."},
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": "m.py",
                        "old_string": "foo",
                        "new_string": "bar",
                        "replace_all": True,
                    },
                },
            ],
        },
    }
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(rec), encoding="utf-8")
    turns, _ = parse_session(path)
    edits = [e for t in turns for e in t.tool_calls if e.tool == "Edit"]
    assert edits, "expected an Edit tool call"
    assert edits[0].replace_all is True


# --------------------------------------------------------------------------- #
# fix-parse-subcommand-ignores-codex
# --------------------------------------------------------------------------- #
def _write_codex_log(path: Path) -> None:
    events = [
        {"type": "message", "role": "assistant",
         "content": "I'll add a guard. Added a null check to src/auth.py."},
        {"type": "function_call", "name": "apply_patch",
         "arguments": {"input": (
             "*** Begin Patch\n"
             "*** Update File: src/auth.py\n"
             " def token(u):\n"
             "+    if u is None:\n"
             "+        return None\n"
             "     return u.legacy\n"
             "*** End Patch\n"
         )}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def test_parse_subcommand_honors_codex_logs(tmp_path):
    """`agentlie parse <codex.log>` must report the Codex turn, not '0 turns'."""
    log = tmp_path / "codex.jsonl"
    _write_codex_log(log)
    runner = CliRunner()
    result = runner.invoke(main, ["parse", str(log)])
    assert result.exit_code == 0, result.output
    assert "0 turns parsed" not in result.output, result.output
    assert "1 turns parsed" in result.output, result.output


def test_parse_subcommand_format_codex_flag(tmp_path):
    """The explicit --format codex option is accepted by `parse`."""
    log = tmp_path / "codex.jsonl"
    _write_codex_log(log)
    runner = CliRunner()
    result = runner.invoke(main, ["parse", "--format", "codex", str(log)])
    assert result.exit_code == 0, result.output
    assert "1 turns parsed" in result.output, result.output
