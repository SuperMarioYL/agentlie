"""Extract claim spans from assistant turn text.

Rule-based first (cheap, offline, deterministic). Optional LLM fallback
gated behind --llm-extract for the cases where natural-language phrasing
doesn't match our regex set.

A "claim" is a sentence-like span where the assistant asserts a code
change was made. We normalise to a verb in:

    fix | add | remove | rename | update

and try to capture a target — a file path or symbol name. Both are
optional; a claim with no target is allowed but will almost always
verdict as VAGUE downstream.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from agentlie.models import ClaimEditPair, ClaimSpan, Turn, Verdict

VERB_SYNONYMS: dict[str, str] = {
    "fix": "fix",
    "fixed": "fix",
    "fixes": "fix",
    "patch": "fix",
    "patched": "fix",
    "resolve": "fix",
    "resolved": "fix",
    "add": "add",
    "added": "add",
    "adds": "add",
    "introduce": "add",
    "introduced": "add",
    "create": "add",
    "created": "add",
    "implement": "add",
    "implemented": "add",
    "remove": "remove",
    "removed": "remove",
    "removes": "remove",
    "delete": "remove",
    "deleted": "remove",
    "drop": "remove",
    "dropped": "remove",
    "rename": "rename",
    "renamed": "rename",
    "renames": "rename",
    "update": "update",
    "updated": "update",
    "updates": "update",
    "refactor": "update",
    "refactored": "update",
    "modify": "update",
    "modified": "update",
}

VERB_PATTERN = re.compile(
    r"\b(?:I('?ve| have)?\s+)?(?P<verb>"
    + "|".join(sorted(VERB_SYNONYMS.keys(), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

PATH_PATTERN = re.compile(
    r"`?(?P<path>(?:[./~]?[\w./-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|md|yml|yaml|json|toml))`?"
)

SYMBOL_PATTERN = re.compile(r"`(?P<sym>[A-Za-z_][\w.]*)`")

# A "claim sentence" is roughly a sentence containing a recognised verb.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Return (sentence, start, end) tuples."""
    out: list[tuple[str, int, int]] = []
    cursor = 0
    parts = SENTENCE_SPLIT.split(text)
    for part in parts:
        if not part.strip():
            cursor += len(part) + 1
            continue
        start = text.find(part, cursor)
        if start == -1:
            start = cursor
        end = start + len(part)
        out.append((part.strip(), start, end))
        cursor = end
    return out


def _scan_target_path(sentence: str, candidate_paths: Iterable[str]) -> Optional[str]:
    """Find a file path in the sentence, preferring ones the agent actually edited."""
    cand_set = set(candidate_paths)
    # 1. exact mention of one of the edited paths
    for path in cand_set:
        if path and path in sentence:
            return path
    # 2. tail-of-path mention (basename match)
    for path in cand_set:
        if not path:
            continue
        base = path.rsplit("/", 1)[-1]
        if base and base in sentence:
            return path
    # 3. any path-looking token
    m = PATH_PATTERN.search(sentence)
    return m.group("path") if m else None


def _scan_target_symbol(sentence: str) -> Optional[str]:
    m = SYMBOL_PATTERN.search(sentence)
    return m.group("sym") if m else None


def extract_claims(turns: list[Turn]) -> list[ClaimEditPair]:
    """Walk every turn, extract claim spans, attach the turn's tool calls."""
    pairs: list[ClaimEditPair] = []
    for turn in turns:
        text = turn.assistant_text
        if not text:
            continue
        candidate_paths = [e.path for e in turn.tool_calls if e.path]
        for sentence, _, _ in _split_sentences(text):
            m = VERB_PATTERN.search(sentence)
            if not m:
                continue
            verb_raw = m.group("verb").lower()
            verb = VERB_SYNONYMS.get(verb_raw, verb_raw)
            target_path = _scan_target_path(sentence, candidate_paths)
            target_symbol = _scan_target_symbol(sentence) if not target_path else None
            claim = ClaimSpan(
                text=sentence,
                verb=verb,
                target_path=target_path,
                target_symbol=target_symbol,
            )
            # Edits scoped to this turn — verifier will weigh by path match.
            pairs.append(
                ClaimEditPair(
                    turn_id=turn.turn_id,
                    claim=claim,
                    edits=list(turn.tool_calls),
                    verdict=Verdict.VAGUE,
                )
            )
    return pairs
