"""agentlie — Claude Code Agent honesty verifier.

Public entry points:
    from agentlie import check_session, ClaimEditPair, Verdict
"""

from agentlie.models import (
    ActualEdit,
    ClaimEditPair,
    ClaimSpan,
    Reason,
    Turn,
    Verdict,
)
from agentlie.parser import FileStateTracker, parse_session
from agentlie.codex import looks_like_codex, parse_codex_session
from agentlie.extractor import extract_claims
from agentlie.verifier import verify_pair, verify_session
from agentlie.report import render_report

__version__ = "0.6.0"

__all__ = [
    "ActualEdit",
    "ClaimEditPair",
    "ClaimSpan",
    "FileStateTracker",
    "Reason",
    "Turn",
    "Verdict",
    "check_session",
    "extract_claims",
    "parse_session",
    "render_report",
    "verify_pair",
    "verify_session",
    "__version__",
]


def check_session(path):
    """Convenience: parse → extract → verify a session in one call.

    Returns a list of ClaimEditPair with verdicts populated.

    Honors Codex logs the same way the CLI ``check`` does: auto-dispatch via
    ``looks_like_codex`` (Codex) else ``parse_session`` (Claude Code). Previously
    this called ``parse_session`` (Claude-Code-only) directly, so a Codex log
    silently returned 0 turns / 0 claims — the library-level analog of the
    v0.3.0 ``fix-parse-subcommand-ignores-codex`` defect.
    """
    if looks_like_codex(path):
        turns, tracker = parse_codex_session(path)
    else:
        turns, tracker = parse_session(path)
    claim_pairs = extract_claims(turns)
    return verify_session(claim_pairs, turns, tracker)
