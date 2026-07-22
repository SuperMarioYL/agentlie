"""Regression tests for the v0.7.0 iteration.

Covers two correctness bug-fixes surfaced by a source-inspection bug hunt:

  * fix-verifier-fix-branch-none-before-false-pass — the "fix" AST branch used a
    RAW ``before_content != after_content`` comparison, unlike the add/remove
    branches which use the normalized ``real_diff = (before_content or "") !=
    (after_content or "")``. When an Edit's old_string is not found on a path the
    tracker has never seen, the parser sets before_content=None and
    after_content="" (current="" -> no change); ``None != ""`` is True, so the fix
    branch credited a zero-diff no-op as a textual delta and the resolver PASSed
    a missed lie (the honesty tool's worst failure mode). Now the fix branch uses
    ``real_diff`` so None normalizes to "" and a no-op resolves to LIE, matching
    add/remove. A genuine fix with a real diff still PASSes; add/remove are
    untouched (already normalized).
  * fix-check-session-ignores-codex — the public ``check_session()`` convenience
    called ``parse_session`` (Claude-Code-only) directly, so a Codex log silently
    returned 0 turns / 0 claims. Now it dispatches through the same
    ``looks_like_codex -> parse_codex_session, else parse_session`` logic the CLI
    ``check`` uses (parse_any), so the library API honors Codex logs. A Claude
    Code JSONL session still parses identically.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agentlie import check_session
from agentlie.cli import parse_any
from agentlie.extractor import extract_claims
from agentlie.models import (
    ActualEdit,
    ClaimEditPair,
    ClaimSpan,
    Verdict,
)
from agentlie.parser import FileStateTracker, parse_session
from agentlie.verifier import verify_pair, verify_session


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _verify(verb, *, path=None, before=None, after="", tool="Edit"):
    """Build a single-edit path-targeted claim and verify it; return the pair.

    ``before=None, after=""`` reproduces the parser's apply_edit output for an
    Edit whose old_string is not found on a path the tracker has never seen
    (current="" -> no change) — the exact no-op state that used to false-PASS a
    fix claim.
    """
    edit = ActualEdit(
        tool=tool,
        path=path or "newfile.py",
        before_content=before,
        after_content=after,
    )
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(
            text=f"I {verb}ed the bug in {path or 'newfile.py'}",
            verb=verb,
            target_path=path or "newfile.py",
        ),
        edits=[edit],
        verdict=Verdict.VAGUE,
    )
    return verify_pair(pair, FileStateTracker())


def _write_jsonl(records: list[dict]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        return fh.name


def _cli_check(path: str):
    """Replicate the CLI ``check`` pipeline (parse_any auto -> extract -> verify),
    so a verdict can be compared against the library ``check_session`` API."""
    turns, tracker = parse_any(Path(path), "auto")
    pairs = extract_claims(turns)
    return verify_session(pairs, turns, tracker)


def _verdict_tuples(pairs):
    return [(p.turn_id, p.claim.verb, p.verdict) for p in pairs]


# --------------------------------------------------------------------------- #
# fix-verifier-fix-branch-none-before-false-pass
# --------------------------------------------------------------------------- #
def test_fix_noop_on_unseen_path_is_lie_not_pass():
    """An Edit whose old_string is not found on a path the tracker has never seen
    yields before_content=None / after_content="" (current="" -> no change). The
    fix branch must NOT credit this zero-diff no-op as a textual delta: a lying
    'I fixed the bug in newfile.py' must verdict LIE, not PASS."""
    pair = _verify("fix", path="newfile.py", before=None, after="")
    codes = [r.code for r in pair.evidence]
    assert pair.verdict == Verdict.LIE
    # the no-op is recorded as such; crucially fix_delta_present is NOT credited
    assert "noop_edit" in codes
    assert "fix_delta_present" not in codes


def test_fix_genuine_diff_still_passes():
    """Control: a fix claim backed by a real textual diff still PASSes (the
    genuine-fix PASS path is unchanged by the normalization)."""
    pair = _verify(
        "fix",
        path="src/app.py",
        before="def f():\n    return bug\n",
        after="def f():\n    return fixed\n",
    )
    assert pair.verdict == Verdict.PASS
    assert "fix_delta_present" in [r.code for r in pair.evidence]


def test_fix_noop_then_real_in_same_turn_still_passes():
    """The v0.2.0 order-independent fix is untouched by this change: a real fix
    plus a no-op edit to the same target must PASS regardless of iteration order.
    Here [noop, real] -> the noop's else-clause preserves a prior flag (None here)
    and the real edit sets ast_evidence=True, so the turn PASSes, not LIE."""
    edits = [
        ActualEdit(tool="Edit", path="src/app.py", before_content=None, after_content=""),
        ActualEdit(tool="Edit", path="src/app.py", before_content="bug\n", after_content="fixed\n"),
    ]
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="I fixed the bug in src/app.py", verb="fix", target_path="src/app.py"),
        edits=edits,
        verdict=Verdict.VAGUE,
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.PASS
    assert "fix_delta_present" in [r.code for r in pair.evidence]


def test_add_noop_on_unseen_path_still_lies():
    """Scope control: add/remove branches are already normalized via real_diff and
    are unaffected by the fix-only change — a zero-diff no-op add still LIEs."""
    pair = _verify("add", path="src/app.py", before=None, after="")
    assert pair.verdict == Verdict.LIE


def test_remove_noop_on_unseen_path_still_lies():
    """Scope control: the remove branch is likewise unaffected."""
    pair = _verify("remove", path="src/app.py", before=None, after="")
    assert pair.verdict == Verdict.LIE


# --------------------------------------------------------------------------- #
# fix-check-session-ignores-codex
# --------------------------------------------------------------------------- #
CODEX_REMOVAL_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: src/app.py\n"
    "@@\n"
    " def helper():\n"
    "-    old_debug_call()\n"
    "+    pass\n"
    "*** End Patch"
)


def _codex_removal_log() -> list[dict]:
    return [
        {"type": "message", "role": "assistant", "content": "I removed `old_debug_call`"},
        {"type": "function_call", "name": "apply_patch",
         "arguments": {"input": CODEX_REMOVAL_PATCH}},
    ]


def test_parse_session_on_codex_log_is_silently_empty():
    """Sanity: parse_session (Claude-Code-only) on a Codex log yields 0 turns —
    the exact defect check_session used to have and now routes around."""
    path = _write_jsonl(_codex_removal_log())
    try:
        turns, _ = parse_session(path)
    finally:
        Path(path).unlink(missing_ok=True)
    assert len(turns) == 0


def test_check_session_parses_codex_log_nonzero():
    """check_session on a Codex log with a real apply_patch removal hunk must
    return non-zero turns/claims (previously 0/0) and the removal verdicts PASS."""
    path = _write_jsonl(_codex_removal_log())
    try:
        pairs = check_session(path)
    finally:
        Path(path).unlink(missing_ok=True)
    assert pairs, "expected non-zero claims from a Codex log"
    removal = [p for p in pairs if p.claim.verb == "remove"]
    assert removal, "expected a remove claim"
    assert removal[0].verdict == Verdict.PASS
    assert any(r.code == "symbol_removed" for r in removal[0].evidence)


def test_check_session_codex_matches_cli_check_verdict():
    """check_session must produce the same verdict the CLI ``check`` produces on
    the same Codex log (both dispatch through looks_like_codex ->
    parse_codex_session)."""
    path = _write_jsonl(_codex_removal_log())
    try:
        lib_pairs = check_session(path)
        cli_pairs = _cli_check(path)
    finally:
        Path(path).unlink(missing_ok=True)
    assert _verdict_tuples(lib_pairs) == _verdict_tuples(cli_pairs)
    # And both are non-trivial — the defect was a silent 0-claim pass.
    assert lib_pairs


def _claude_code_session() -> list[dict]:
    return [
        {
            "uuid": "a1",
            "parentUuid": None,
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I fixed the bug in src/app.py"},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "id": "t1",
                        "input": {"file_path": "src/app.py", "old_string": "bug", "new_string": "fixed"},
                    },
                ],
            },
        },
        {
            "uuid": "u1",
            "parentUuid": "a1",
            "type": "user",
            "toolUseResult": {"tool_use_id": "t1", "filePath": "src/app.py", "originalFile": "bug\n"},
        },
    ]


def test_check_session_claude_code_jsonl_unchanged():
    """A Claude Code JSONL session still parses identically via check_session
    (routes to parse_session); verdicts match the direct parse_session pipeline
    and the fix claim backed by originalFile ground truth PASSes."""
    path = _write_jsonl(_claude_code_session())
    try:
        lib_pairs = check_session(path)
        turns, tracker = parse_session(path)
        direct_pairs = verify_session(extract_claims(turns), turns, tracker)
    finally:
        Path(path).unlink(missing_ok=True)
    assert lib_pairs, "expected claims from a Claude Code session"
    assert _verdict_tuples(lib_pairs) == _verdict_tuples(direct_pairs)
    assert any(p.verdict == Verdict.PASS for p in lib_pairs)
