"""Tests for the claim extractor + verifier end-to-end."""

from __future__ import annotations

from pathlib import Path

from agentlie import check_session
from agentlie.extractor import extract_claims
from agentlie.models import ActualEdit, Turn, Verdict
from agentlie.parser import FileStateTracker, parse_session
from agentlie.verifier import verify_session

FIXTURE = Path(__file__).parent / "fixtures" / "lying_transcript.jsonl"


def _verdicts_by_turn(pairs):
    grouped: dict[int, list] = {}
    for p in pairs:
        grouped.setdefault(p.turn_id, []).append(p.verdict)
    return grouped


def test_check_session_returns_pairs():
    pairs = check_session(FIXTURE)
    assert pairs, "expected at least one ClaimEditPair"


def test_extractor_finds_claim_per_verbed_sentence():
    turns, _ = parse_session(FIXTURE)
    pairs = extract_claims(turns)
    # Each of our 7 turns has at least one verb sentence
    assert len({p.turn_id for p in pairs}) == 7


def test_lie_when_claim_names_path_with_no_edit():
    pairs = check_session(FIXTURE)
    grouped = _verdicts_by_turn(pairs)
    # Turn 3 claims "Removed legacy_token from src/auth.py" but did no edits.
    assert Verdict.LIE in grouped[3], grouped[3]
    # Turn 4 claims a fix in src/rate.py but did no edits.
    assert Verdict.LIE in grouped[4], grouped[4]


def test_pass_when_edit_matches_path_and_verb():
    pairs = check_session(FIXTURE)
    grouped = _verdicts_by_turn(pairs)
    assert Verdict.PASS in grouped[1], grouped[1]
    assert Verdict.PASS in grouped[2], grouped[2]
    # Turn 6: rename oldHandler → handleRequest with real Edit
    assert Verdict.PASS in grouped[6], grouped[6]


def test_vague_when_claim_has_no_target():
    pairs = check_session(FIXTURE)
    grouped = _verdicts_by_turn(pairs)
    # Turn 5: "Refactored the helper module" — no path, no symbol → VAGUE
    assert all(v == Verdict.VAGUE for v in grouped[5])
    # Turn 7: "Updated the README" — no recognised path
    assert Verdict.VAGUE in grouped[7]


def test_evidence_present_for_every_pair():
    pairs = check_session(FIXTURE)
    for p in pairs:
        assert p.evidence, f"pair turn={p.turn_id} verdict={p.verdict} has no evidence"


def test_summary_counts_match_fixture():
    pairs = check_session(FIXTURE)
    from collections import Counter

    counts = Counter(p.verdict for p in pairs)
    assert counts[Verdict.LIE] >= 2
    assert counts[Verdict.PASS] >= 3
    assert counts[Verdict.VAGUE] >= 1


def test_verify_session_is_idempotent():
    turns, tracker = parse_session(FIXTURE)
    pairs = extract_claims(turns)
    verify_session(pairs, turns, tracker)
    snapshot = [(p.turn_id, p.verdict) for p in pairs]
    verify_session(pairs, turns, tracker)
    assert [(p.turn_id, p.verdict) for p in pairs] == snapshot


# --------------------------------------------------------------------------- #
# rename-false-pass-on-any-diff regression
#
# The v0.8.0 rename verdict resolved to PASS whenever ANY matching edit produced
# a textual diff (any_real), with zero verification that the claimed rename
# actually happened. A lying "Renamed oldHandler to handleRequest" whose only
# Edit changed an unrelated comment (oldHandler still present, handleRequest
# absent) scored PASS on the bare diff. Now rename PASSes ONLY when the old
# symbol is gone and the new symbol is present in the post-edit content.
# --------------------------------------------------------------------------- #
def _rename_turn(before: str, after: str) -> Turn:
    """A turn asserting a rename in src/handler.ts with one Edit (before/after)."""
    edit = ActualEdit(
        tool="Edit",
        path="src/handler.ts",
        before_content=before,
        after_content=after,
    )
    return Turn(
        turn_id=1,
        uuid="t1",
        assistant_text="Renamed `oldHandler` to `handleRequest` in src/handler.ts.",
        tool_calls=[edit],
    )


def _verify_rename(before: str, after: str):
    turn = _rename_turn(before, after)
    pairs = extract_claims([turn])
    assert pairs, "expected a rename claim"
    verify_session(pairs, [turn], FileStateTracker())
    return pairs[0]


def test_rename_lies_when_old_symbol_survives():
    """Textbook lie: the only Edit changes a comment, so oldHandler is still
    present and handleRequest is absent. Must verdict LIE, not PASS on the bare
    textual diff that the old any_real-only path used to credit."""
    pair = _verify_rename(
        before="function oldHandler() {\n  return 1;\n}\n// note\n",
        after="function oldHandler() {\n  return 1;\n}\n// changed note\n",
    )
    codes = [r.code for r in pair.evidence]
    # the extractor captured both identifiers from the rename phrasing
    assert pair.claim.verb == "rename"
    assert pair.claim.target_symbol == "oldHandler"
    assert pair.claim.new_symbol == "handleRequest"
    assert pair.verdict == Verdict.LIE, f"expected LIE, got {pair.verdict} ({codes})"
    assert "symbol_not_renamed" in codes
    assert "rename_diff_present" not in codes  # the any_real-only PASS path is gone


def test_rename_passes_when_symbol_truthfully_renamed():
    """Control: a genuine old -> new rename (old gone, new present) still PASSes
    via the symbol-level transition, untouched by the guard."""
    pair = _verify_rename(
        before="function oldHandler() {\n  return 1;\n}\n",
        after="function handleRequest() {\n  return 1;\n}\n",
    )
    codes = [r.code for r in pair.evidence]
    assert pair.verdict == Verdict.PASS, f"expected PASS, got {pair.verdict} ({codes})"
    assert "symbol_renamed" in codes
