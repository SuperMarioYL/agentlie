"""Regression tests for the v0.8.0 iteration.

Covers one root-cause honesty-engine defect:

  * fix-add-remove-noop-poisons-ast-evidence — in a multi-edit turn where ONE edit
    is a no-op (Edit old_string not found -> before==after) and the OTHER is a real
    but NON-structural add/remove (a print/log/assignment/return/comment — changes no
    ADD/REMOVE_INDICATOR node), the verdict falsely became LIE on a truthful change,
    in BOTH iteration orders. Root cause in ``src/agentlie/verifier.py``:

      - the zero-diff branch (add ~L294-298 / remove ~L316-320) ran
        ``ast_evidence = ast_evidence if ast_evidence else False``, converting the
        initial ``None`` -> ``False`` mid-loop;
      - the subsequent real-diff non-structural branch ``ast_no_add_but_diff``
        (~L279-293) / ``ast_no_remove_but_diff`` (~L306-315) did NOT touch
        ``ast_evidence``, so the ``False`` stuck and the resolver (~L390
        ``elif ast_evidence is False``) emitted LIE.

    The ``fix`` verb was immune (its real-diff branch sets ``ast_evidence=True``).
    The v0.3.0 fix only covered the SINGLE-edit no-op case.

  Fix: defer the ``ast_evidence=False`` downgrade until AFTER the AST loop. Track
  whether any matching edit produced a positive signal (a structural
  ADD/REMOVE_INDICATOR delta OR a normalized real_diff). After the loop, downgrade
  to ``False`` (-> LIE) ONLY when no edit showed any positive signal; leave
  ``None`` (-> VAGUE) when a real non-structural diff is present; keep ``True`` when
  a structural delta appeared. The single-edit no-op -> LIE contract is preserved
  (a noop-only turn still resolves False -> LIE). Multi-edit turns are now
  order-independent for add/remove, mirroring how the ``fix`` branch already behaved.
"""

from __future__ import annotations

from agentlie.models import (
    ActualEdit,
    ClaimEditPair,
    ClaimSpan,
    Verdict,
)
from agentlie.parser import FileStateTracker
from agentlie.verifier import verify_pair


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
NOOP_EDIT = lambda path="src/app.py": ActualEdit(  # noqa: E731
    tool="Edit", path=path, before_content=None, after_content=""
)
# An Edit whose old_string is not found on a path the tracker has never seen
# yields before_content=None / after_content="" (current="" -> no change) — the
# exact no-op state, real_diff=False ("" == "").


def _real_print_add(path="src/app.py"):
    """A real but NON-structural add: appending a print statement changes no
    ADD_INDICATOR node (print is an expression_statement), yet real_diff is True."""
    return ActualEdit(
        tool="Edit",
        path=path,
        before_content="x = 1\n",
        after_content="x = 1\nprint('hi')\n",
    )


def _real_print_remove(path="src/app.py"):
    """Mirror: removing a print statement changes no REMOVE_INDICATOR node yet is a
    real textual diff."""
    return ActualEdit(
        tool="Edit",
        path=path,
        before_content="x = 1\nprint('hi')\n",
        after_content="x = 1\n",
    )


def _pair(verb, edits, path="src/app.py"):
    return ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(
            text=f"I {verb}ed a print in {path}",
            verb=verb,
            target_path=path,
        ),
        edits=edits,
        verdict=Verdict.VAGUE,
    )


def _verify(verb, edits):
    pair = _pair(verb, edits)
    return verify_pair(pair, FileStateTracker())


def _codes(pair):
    return [r.code for r in pair.evidence]


# --------------------------------------------------------------------------- #
# 1. multi-edit turn [noop, real_print_add] -> VAGUE (not LIE), both orders
# --------------------------------------------------------------------------- #
def test_multi_edit_noop_then_real_print_add_is_vague_not_lie():
    """[noop, real_print_add] in the same turn must NOT verdict LIE. The noop's
    zero-diff branch used to poison ast_evidence=False mid-loop; the real print's
    non-structural real-diff branch did not reset it, so the resolver emitted LIE on
    a truthful change. Now the False downgrade is deferred past the loop and only
    fires when NO edit showed a positive signal — so a real (if non-structural) diff
    resolves to VAGUE, never LIE."""
    pair = _verify("add", [NOOP_EDIT(), _real_print_add()])
    assert pair.verdict == Verdict.VAGUE, (
        f"expected VAGUE for a real non-structural add + noop, got {pair.verdict} "
        f"({_codes(pair)})"
    )
    # the real diff is recorded; crucially the noop did not drag it to LIE
    assert "ast_no_add_but_diff" in _codes(pair)
    assert "ast_no_add" in _codes(pair)  # the noop is still recorded as such


def test_multi_edit_real_print_add_then_noop_is_vague_not_lie():
    """Order independence: [real_print_add, noop] must yield the SAME VAGUE verdict
    as [noop, real_print_add]. Pre-fix this order also LIE'd (the noop ran LAST and
    set ast_evidence=False after the real edit left it None). The deferred
    downgrade makes the verdict independent of iteration order, mirroring the fix
    branch."""
    pair = _verify("add", [_real_print_add(), NOOP_EDIT()])
    assert pair.verdict == Verdict.VAGUE, (
        f"expected VAGUE (order-independent), got {pair.verdict} ({_codes(pair)})"
    )
    assert "ast_no_add_but_diff" in _codes(pair)


# --------------------------------------------------------------------------- #
# 2. single-edit no-op-only turn -> still LIE (preserve contract)
# --------------------------------------------------------------------------- #
def test_single_edit_noop_add_still_lies():
    """The v0.3.0 contract — a no-op-only add turn resolves to LIE — is PRESERVED.
    A noop-only turn shows no positive signal at all, so the post-loop downgrade
    still sets ast_evidence=False -> LIE. The fix only rescues turns that contain a
    REAL diff."""
    pair = _verify("add", [NOOP_EDIT()])
    assert pair.verdict == Verdict.LIE, (
        f"noop-only add must still LIE, got {pair.verdict} ({_codes(pair)})"
    )
    assert "noop_edit" in _codes(pair)


def test_single_edit_noop_remove_still_lies():
    """Symmetric contract preservation for the remove branch."""
    pair = _verify("remove", [NOOP_EDIT()])
    assert pair.verdict == Verdict.LIE
    assert "noop_edit" in _codes(pair)


# --------------------------------------------------------------------------- #
# 3. single-edit real-print add (control) -> VAGUE (unchanged)
# --------------------------------------------------------------------------- #
def test_single_edit_real_print_add_is_vague_control():
    """Control: a single-edit real non-structural add (no structural node added)
    already resolved to VAGUE before this fix and must remain VAGUE. This guards
    against the fix accidentally promoting a non-structural real diff to PASS (it
    must stay None -> VAGUE) or downgrading it to LIE (the original bug)."""
    pair = _verify("add", [_real_print_add()])
    assert pair.verdict == Verdict.VAGUE
    assert "ast_no_add_but_diff" in _codes(pair)


# --------------------------------------------------------------------------- #
# remove mirror: the fix touches the remove zero-diff branch too
# --------------------------------------------------------------------------- #
def test_multi_edit_noop_then_real_print_remove_is_vague_not_lie():
    """Mirror of the add case for the REMOVE branch: a real non-structural removal
    (deleting a print) + a noop must resolve VAGUE, not LIE, in both orders. The
    remove zero-diff branch had the identical mid-loop ``ast_evidence = ... else
    False`` poison and is fixed by the same deferred downgrade."""
    for order in ([NOOP_EDIT(), _real_print_remove()], [_real_print_remove(), NOOP_EDIT()]):
        pair = _verify("remove", order)
        assert pair.verdict == Verdict.VAGUE, (
            f"expected VAGUE for real non-structural remove + noop, got "
            f"{pair.verdict} ({_codes(pair)})"
        )
        assert "ast_no_remove_but_diff" in _codes(pair)
