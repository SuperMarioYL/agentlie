"""Regression + feature tests for the v0.2.0 iteration.

Covers the three bug fixes and the three features folded into v0.2.0:

  fix-tree-sitter-languages-uninstallable — the AST verifier actually runs
  fix-walk-dag-recursion                  — long linear sessions don't RecursionError
  fix-verifier-fix-branch-order-dependent-lie — fix verdict is edit-order-independent
  m5_ast_go_rust                          — Go/Rust claims can reach AST evidence
  m6_codex_logs                           — Codex logs normalise into Turn/ActualEdit
  m4_llm_extract                          — LLM extractor degrades gracefully offline
"""

from __future__ import annotations

import json
from pathlib import Path

from agentlie.codex import looks_like_codex, parse_codex_session
from agentlie.extractor import extract_claims_llm
from agentlie.models import ActualEdit, ClaimEditPair, ClaimSpan, Verdict
from agentlie.parser import FileStateTracker, _walk_dag, parse_session
from agentlie.verifier import _try_tree_sitter, verify_pair


# --------------------------------------------------------------------------- #
# fix-tree-sitter-languages-uninstallable
# --------------------------------------------------------------------------- #
def test_tree_sitter_python_parser_loads():
    """The AST path is dead if the grammar provider can't be imported; this
    asserts get_parser('python') actually returns a parser."""
    assert _try_tree_sitter("python") is not None


# --------------------------------------------------------------------------- #
# m5_ast_go_rust
# --------------------------------------------------------------------------- #
def test_tree_sitter_go_and_rust_parsers_load():
    assert _try_tree_sitter("go") is not None
    assert _try_tree_sitter("rust") is not None


def test_go_add_claim_reaches_ast_pass():
    """A Go file gaining a function should verdict PASS on AST grounds, not
    degrade to VAGUE (the pre-v0.2 behavior since Go wasn't in LANG_BY_EXT)."""
    before = "package main\n\nfunc main() {}\n"
    after = "package main\n\nfunc main() {}\n\nfunc handle() error { return nil }\n"
    edit = ActualEdit(tool="Write", path="server.go", content=after,
                      before_content=before, after_content=after)
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="Added handle to server.go", verb="add", target_path="server.go"),
        edits=[edit],
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.PASS
    assert any("ast_add" in r.code for r in pair.evidence)


def test_rust_add_claim_reaches_ast_pass():
    before = "fn main() {}\n"
    after = "fn main() {}\n\nfn helper() -> u32 { 0 }\n"
    edit = ActualEdit(tool="Write", path="lib.rs", content=after,
                      before_content=before, after_content=after)
    pair = ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="Added helper to lib.rs", verb="add", target_path="lib.rs"),
        edits=[edit],
    )
    verify_pair(pair, FileStateTracker())
    assert pair.verdict == Verdict.PASS


# --------------------------------------------------------------------------- #
# fix-walk-dag-recursion
# --------------------------------------------------------------------------- #
def test_walk_dag_handles_long_linear_chain():
    """A >2000-record near-linear parentUuid chain must not RecursionError."""
    records = []
    prev = None
    n = 2500
    for i in range(n):
        uuid = f"u{i}"
        records.append({"uuid": uuid, "parentUuid": prev, "type": "user"})
        prev = uuid
    ordered = _walk_dag(records)
    assert len(ordered) == n
    # Order is preserved along the chain.
    assert [r["uuid"] for r in ordered[:3]] == ["u0", "u1", "u2"]


def test_parse_session_handles_long_chain(tmp_path):
    """End-to-end: a long synthetic Claude Code session parses without crashing."""
    path = tmp_path / "long.jsonl"
    lines = []
    prev = None
    for i in range(2200):
        uuid = f"a{i}"
        rec = {
            "uuid": uuid,
            "parentUuid": prev,
            "type": "assistant",
            "message": {"role": "assistant", "content": f"step {i}"},
        }
        lines.append(json.dumps(rec))
        prev = uuid
    path.write_text("\n".join(lines), encoding="utf-8")
    turns, _ = parse_session(path)
    assert len(turns) == 2200


# --------------------------------------------------------------------------- #
# fix-verifier-fix-branch-order-dependent-lie
# --------------------------------------------------------------------------- #
def _fix_pair(edits):
    return ClaimEditPair(
        turn_id=1,
        claim=ClaimSpan(text="Fixed the bug in app.py", verb="fix", target_path="app.py"),
        edits=edits,
    )


def test_fix_branch_is_order_independent():
    """A real fix plus a noop edit to the same target must PASS regardless of
    iteration order — pre-v0.2 the fix-branch flipped to LIE for [real, noop]."""
    real = ActualEdit(
        tool="Edit", path="app.py",
        before_content="def f():\n    return 1\n",
        after_content="def f():\n    return 2\n",
    )
    noop = ActualEdit(
        tool="Edit", path="app.py",
        before_content="def f():\n    return 2\n",
        after_content="def f():\n    return 2\n",
    )
    pair_real_first = _fix_pair([real, noop])
    pair_noop_first = _fix_pair([noop, real])
    verify_pair(pair_real_first, FileStateTracker())
    verify_pair(pair_noop_first, FileStateTracker())
    assert pair_real_first.verdict == pair_noop_first.verdict
    assert pair_real_first.verdict == Verdict.PASS


# --------------------------------------------------------------------------- #
# m6_codex_logs
# --------------------------------------------------------------------------- #
def _write_codex_log(tmp_path: Path) -> Path:
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
    path = tmp_path / "codex.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def test_looks_like_codex_detects_codex_log(tmp_path):
    path = _write_codex_log(tmp_path)
    assert looks_like_codex(path) is True


def test_looks_like_codex_rejects_claude_code(tmp_path):
    path = tmp_path / "cc.jsonl"
    path.write_text(json.dumps({
        "uuid": "a1", "parentUuid": None, "type": "assistant",
        "message": {"role": "assistant", "content": "hi"},
    }), encoding="utf-8")
    assert looks_like_codex(path) is False


def test_parse_codex_normalises_into_turns(tmp_path):
    path = _write_codex_log(tmp_path)
    turns, tracker = parse_codex_session(path)
    assert len(turns) == 1
    turn = turns[0]
    assert "null check" in turn.assistant_text
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].path == "src/auth.py"
    assert turn.tool_calls[0].tool == "Write"
    assert "if u is None" in (turn.tool_calls[0].after_content or "")


# --------------------------------------------------------------------------- #
# m4_llm_extract — graceful offline fallback
# --------------------------------------------------------------------------- #
def test_llm_extract_degrades_without_api_key(monkeypatch):
    """extract_claims_llm must return rule-based pairs + used_llm=False when no
    ANTHROPIC_API_KEY is available — the flag is additive, never load-bearing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fixture = Path(__file__).parent / "fixtures" / "lying_transcript.jsonl"
    turns, _ = parse_session(fixture)
    pairs, used_llm = extract_claims_llm(turns)
    assert used_llm is False
    assert pairs, "should still produce rule-based pairs"
