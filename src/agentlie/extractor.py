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

import json
import os
import re
from typing import Iterable, Optional

from agentlie.models import ClaimEditPair, ClaimSpan, Turn, Verdict

# Default Haiku model for the optional LLM extractor (m4). Overridable via env
# so the extractor keeps working when Anthropic renames the latest Haiku snapshot.
LLM_MODEL = os.environ.get("AGENTLIE_LLM_MODEL", "claude-3-5-haiku-latest")

_VALID_VERBS = {"fix", "add", "remove", "rename", "update"}

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

# The trailing ``(?!\w|\.\w)`` guard stops the extension from being matched inside a
# longer word — e.g. ``reconfig.python`` must NOT yield ``reconfig.py`` — while still
# allowing a path that ends a sentence (``main.py.`` → ``main.py``, since the trailing
# ``.`` is followed by a non-word char).
PATH_PATTERN = re.compile(
    r"`?(?P<path>(?:[./~]?[\w./-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|md|yml|yaml|json|toml))(?!\w|\.\w)`?"
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


def _sentence_mentions_base(sentence: str, base: str) -> bool:
    """True if ``base`` appears in ``sentence`` as a whole path token, not a bare
    substring.

    A bare ``base in sentence`` is not path-segment-safe: the basename ``config.py``
    would match inside the unrelated word ``reconfig.python`` — the same class of
    defect the verifier's ``_matching_edits`` basename fallback had to fix. We require
    the match to sit at a token boundary: the characters flanking the basename must not
    be part of a larger path/identifier token (word chars, ``.``, ``/``, ``-``). A
    leading ``/`` is fine (a real path segment boundary).
    """
    boundary = r"[^\w./\\-]"
    pattern = rf"(?:^|{boundary}|/)" + re.escape(base) + rf"(?={boundary}|$)"
    return re.search(pattern, sentence) is not None


def _scan_target_path(sentence: str, candidate_paths: Iterable[str]) -> Optional[str]:
    """Find a file path in the sentence, preferring ones the agent actually edited."""
    cand_set = set(candidate_paths)
    # 1. exact mention of one of the edited paths — but only at a real path-token
    #    boundary, so a bare-basename candidate like ``config.py`` can't match inside
    #    the unrelated word ``reconfig.python`` (a plain ``path in sentence`` substring
    #    test is not path-segment-safe; the tail of a full path like ``src/utils.py``
    #    still matches because its flanking chars are boundaries).
    for path in cand_set:
        if path and _sentence_mentions_base(sentence, path):
            return path
    # 2. tail-of-path mention (basename match) — same boundary discipline, so
    #    ``utils.py`` can't match ``test_utils.py``.
    for path in cand_set:
        if not path:
            continue
        base = path.rsplit("/", 1)[-1]
        if base and base != path and _sentence_mentions_base(sentence, base):
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


# --------------------------------------------------------------------------- #
# m4 — optional Claude-Haiku-backed extractor.
#
# The rule-based pass above is deterministic and offline but misses claims whose
# phrasing doesn't hit the regex set ("I went ahead and got rid of the dead
# import", "wired the handler so the 500 stops"). When --llm-extract is set AND
# an API key is present, we ask Haiku to enumerate change-claims as structured
# JSON, then merge any that the rule-based pass didn't already cover. Any
# failure (no key, no SDK, network/parse error) degrades silently to the
# rule-based result — the flag is additive, never load-bearing.
# --------------------------------------------------------------------------- #

_LLM_SYSTEM = (
    "You extract code-change claims from a coding agent's assistant message. "
    "A claim is a span where the agent asserts it changed code. For each claim, "
    "return a normalised verb (one of: fix, add, remove, rename, update), the "
    "verbatim claim text, and an optional target file path the agent named. "
    "Only include genuine change assertions — ignore questions, plans, and "
    "descriptions of what code already does. Respond with a single JSON object "
    '{"claims": [{"verb": "...", "text": "...", "target_path": "... or null"}]}.'
)


def _anthropic_client():
    """Return an Anthropic client, or None if the SDK or key is unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
    except Exception:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:
        return None


def _llm_extract_turn(client, turn: Turn) -> list[ClaimSpan]:
    """Ask Haiku for the change-claims in one turn's assistant text."""
    text = turn.assistant_text or ""
    if not text.strip():
        return []
    try:
        resp = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
    except Exception:
        return []
    # Tolerate fenced or prose-wrapped JSON.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except Exception:
        return []
    candidate_paths = [e.path for e in turn.tool_calls if e.path]
    spans: list[ClaimSpan] = []
    for item in data.get("claims", []):
        if not isinstance(item, dict):
            continue
        verb = str(item.get("verb", "")).lower().strip()
        verb = VERB_SYNONYMS.get(verb, verb)
        if verb not in _VALID_VERBS:
            continue
        ctext = str(item.get("text", "")).strip()
        if not ctext:
            continue
        target_path = item.get("target_path") or None
        if target_path and candidate_paths:
            # Prefer an actually-edited path when the model names a bare basename.
            target_path = _scan_target_path(str(target_path), candidate_paths) or str(target_path)
        spans.append(
            ClaimSpan(
                text=ctext,
                verb=verb,
                target_path=str(target_path) if target_path else None,
                target_symbol=_scan_target_symbol(ctext) if not target_path else None,
            )
        )
    return spans


def extract_claims_llm(turns: list[Turn]) -> tuple[list[ClaimEditPair], bool]:
    """Rule-based extraction augmented by a Haiku pass.

    Returns (pairs, used_llm). ``used_llm`` is False when the LLM path was
    unavailable (no key / no SDK), so the caller can warn that it fell back to
    rule-based extraction. New LLM-only claims are appended to the rule-based
    pairs; duplicates (same verb + overlapping text within a turn) are dropped.
    """
    pairs = extract_claims(turns)
    client = _anthropic_client()
    if client is None:
        return pairs, False

    seen: set[tuple[int, str, str]] = {
        (p.turn_id, p.claim.verb, _norm(p.claim.text)) for p in pairs
    }
    for turn in turns:
        for span in _llm_extract_turn(client, turn):
            key = (turn.turn_id, span.verb, _norm(span.text))
            if key in seen or _overlaps(turn.turn_id, span, seen):
                continue
            seen.add(key)
            pairs.append(
                ClaimEditPair(
                    turn_id=turn.turn_id,
                    claim=span,
                    edits=list(turn.tool_calls),
                    verdict=Verdict.VAGUE,
                )
            )
    pairs.sort(key=lambda p: p.turn_id)
    return pairs, True


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _overlaps(turn_id: int, span: ClaimSpan, seen: set[tuple[int, str, str]]) -> bool:
    """True if a rule-based claim in the same turn substantially covers this span."""
    norm = _norm(span.text)
    for tid, verb, text in seen:
        if tid != turn_id or verb != span.verb:
            continue
        if norm in text or text in norm:
            return True
    return False
