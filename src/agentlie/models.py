"""Typed data primitives shared across parser / extractor / verifier / report."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    PASS = "PASS"
    VAGUE = "VAGUE"
    LIE = "LIE"


class ClaimSpan(BaseModel):
    """A span of assistant text that asserts a code change was made."""

    text: str
    verb: str
    target_path: Optional[str] = None
    target_symbol: Optional[str] = None
    span_start: int = 0
    span_end: int = 0


class ActualEdit(BaseModel):
    """A real tool-call mutation pulled from the transcript.

    Where possible, `before_content` / `after_content` come from
    `toolUseResult.originalFile` + `structuredPatch` (ground truth).
    Otherwise they are reconstructed by replaying prior edits.
    """

    tool: str
    path: str
    old_string: Optional[str] = None
    new_string: Optional[str] = None
    replace_all: bool = False
    content: Optional[str] = None
    before_content: Optional[str] = None
    after_content: Optional[str] = None
    ast_delta: dict = Field(default_factory=dict)
    source: str = "replay"


class Reason(BaseModel):
    code: str
    detail: str


class Turn(BaseModel):
    """A reconstructed logical turn — assistant text + colocated tool_use records.

    Walks the parentUuid DAG, groups by logical turn boundary
    (an assistant text record + any tool_use records branching off it).
    """

    turn_id: int
    uuid: str
    parent_uuid: Optional[str] = None
    assistant_text: str = ""
    tool_calls: list[ActualEdit] = Field(default_factory=list)
    timestamp: Optional[str] = None


class ClaimEditPair(BaseModel):
    turn_id: int
    claim: ClaimSpan
    edits: list[ActualEdit] = Field(default_factory=list)
    verdict: Verdict = Verdict.VAGUE
    evidence: list[Reason] = Field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "turn": self.turn_id,
            "verdict": self.verdict.value,
            "verb": self.claim.verb,
            "target": self.claim.target_path or self.claim.target_symbol or "—",
            "claim": _truncate(self.claim.text, 80),
            "edits": len(self.edits),
            "evidence": "; ".join(r.detail for r in self.evidence[:2]),
        }


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
