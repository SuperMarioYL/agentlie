"""Parse a Claude Code session JSONL into reconstructed Turn objects.

The JSONL is a record stream, NOT a flat list of turns. Records form a
DAG via {uuid, parentUuid}. Each logical "turn" is an assistant message
plus the tool_use records that branch off it. Many records aren't messages
at all (queue-operation, last-prompt, ai-title, attachment) and must be
filtered out.

This module also exposes `FileStateTracker`, which maintains per-path
before/after state by:
  1. Preferring `toolUseResult.originalFile` + `structuredPatch` when
     present in the JSONL (ground-truth).
  2. Falling back to cumulative replay of Edit/Write tool inputs when
     originalFile is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from agentlie.models import ActualEdit, Turn

# Tool names emitted by Claude Code in 2026-Q2 logs (Edit + Write are the
# only ones we mutate state for; the rest are observed for context).
MUTATING_TOOLS = {"Edit", "Write"}
KNOWN_TOOLS = {"Agent", "Bash", "Edit", "Glob", "Grep", "Read", "TodoWrite", "ToolSearch", "Write"}

# Record types we ignore wholesale — they live in the same JSONL but
# describe scheduler / UI state, not the conversation.
NON_MESSAGE_TYPES = {"queue-operation", "last-prompt", "ai-title", "attachment"}


class FileStateTracker:
    """Tracks the apparent content of every file the agent touched.

    Two sources, in priority order:
      1. ``toolUseResult.originalFile`` for the before-state, applied
         deltas for the after-state. Authoritative when present.
      2. Replay: assume each Edit/Write is applied in DAG order on top of
         whatever the tracker currently believes the file says.

    Files that were never read or written are unknown — we return None.
    """

    def __init__(self) -> None:
        self._state: dict[str, str] = {}
        self._origin_seen: set[str] = set()

    def get(self, path: str) -> Optional[str]:
        return self._state.get(path)

    def seed_original(self, path: str, content: str) -> None:
        """Seed the before-state from toolUseResult.originalFile."""
        if path not in self._origin_seen:
            self._state.setdefault(path, content)
            self._origin_seen.add(path)

    def apply_edit(self, edit: ActualEdit) -> tuple[Optional[str], Optional[str]]:
        """Apply an edit; return (before, after) snapshots."""
        before = self._state.get(edit.path)
        if edit.tool == "Write":
            after = edit.content or ""
            self._state[edit.path] = after
            return before, after
        if edit.tool == "Edit":
            current = before if before is not None else ""
            old = edit.old_string or ""
            new = edit.new_string or ""
            if old and old in current:
                after = current.replace(old, new, 1)
            elif old == "":
                after = current + new
            else:
                # old_string not found — record degraded after-state as
                # the current state (no change) so the verifier still sees
                # a diff signal (or lack thereof).
                after = current
            self._state[edit.path] = after
            return before, after
        return before, before


def _iter_records(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines rather than abort the whole session.
                continue


def _is_message_record(rec: dict) -> bool:
    rec_type = rec.get("type")
    if rec_type in NON_MESSAGE_TYPES:
        return False
    # Messages have either a role or a message.role.
    if rec_type in {"user", "assistant"}:
        return True
    msg = rec.get("message") or {}
    if isinstance(msg, dict) and msg.get("role") in {"user", "assistant"}:
        return True
    return False


def _walk_dag(records: list[dict]) -> list[dict]:
    """Sort records by parentUuid chain (depth-first), then by index for ties."""
    by_uuid: dict[str, dict] = {r.get("uuid", f"_{i}"): r for i, r in enumerate(records)}
    children: dict[Optional[str], list[str]] = {}
    for r in records:
        parent = r.get("parentUuid")
        children.setdefault(parent, []).append(r.get("uuid", ""))

    ordered: list[dict] = []
    seen: set[str] = set()

    # Iterative pre-order DFS. A normal long Claude Code session is a near-linear
    # parentUuid chain (depth ≈ record count), so a recursive walk would blow
    # Python's ~1000-frame recursion limit on 1500+ record sessions — exactly the
    # long-session target use case. The explicit stack keeps depth unbounded.
    # Push each node's children in reverse so they pop in their listed order,
    # preserving the original pre-order traversal.
    stack: list[Optional[str]] = list(reversed(children.get(None, [])))
    while stack:
        child_uuid = stack.pop()
        if not child_uuid or child_uuid in seen or child_uuid not in by_uuid:
            continue
        seen.add(child_uuid)
        ordered.append(by_uuid[child_uuid])
        stack.extend(reversed(children.get(child_uuid, [])))
    # Append orphans (records whose parent isn't in the file) in input order.
    for r in records:
        uuid = r.get("uuid", "")
        if uuid not in seen:
            ordered.append(r)
            seen.add(uuid)
    return ordered


def _message_content(rec: dict) -> list[dict]:
    msg = rec.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            return content
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
    content = rec.get("content")
    if isinstance(content, list):
        return content
    return []


def _tool_use_edits(content_blocks: list[dict]) -> list[ActualEdit]:
    edits: list[ActualEdit] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        if name not in KNOWN_TOOLS:
            continue
        inp = block.get("input") or {}
        path = inp.get("file_path") or inp.get("path") or inp.get("filePath")
        if name == "Edit":
            edits.append(
                ActualEdit(
                    tool="Edit",
                    path=path or "",
                    old_string=inp.get("old_string"),
                    new_string=inp.get("new_string"),
                )
            )
        elif name == "Write":
            edits.append(
                ActualEdit(
                    tool="Write",
                    path=path or "",
                    content=inp.get("content"),
                )
            )
    return edits


def _assistant_text(content_blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            txt = block.get("text", "")
            if txt:
                parts.append(txt)
    return "\n\n".join(parts).strip()


def _extract_tool_results(rec: dict) -> dict[str, dict]:
    """Pull toolUseResult records keyed by their tool_use_id (if exposed)."""
    result_map: dict[str, dict] = {}
    tur = rec.get("toolUseResult")
    if isinstance(tur, dict):
        # Some logs key results inline by id; others associate by parent linkage.
        tid = tur.get("tool_use_id") or rec.get("uuid", "")
        result_map[tid] = tur
    # Also: user records with content[*].type == "tool_result"
    for block in _message_content(rec):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            tid = block.get("tool_use_id") or rec.get("uuid", "")
            payload = block.get("content")
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                inner = payload[0].get("text") or {}
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except json.JSONDecodeError:
                        inner = {}
                if isinstance(inner, dict):
                    result_map.setdefault(tid, inner)
            elif isinstance(payload, dict):
                result_map.setdefault(tid, payload)
    return result_map


def parse_session(path: str | Path) -> tuple[list[Turn], FileStateTracker]:
    """Parse the JSONL at `path` into Turn objects + a populated FileStateTracker."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"session JSONL not found: {path}")

    raw = list(_iter_records(path))
    ordered = _walk_dag(raw)
    tracker = FileStateTracker()

    # Pre-pass: harvest toolUseResult records carrying originalFile, so the
    # tracker has the ground-truth before-state available when we walk edits.
    tool_results: dict[str, dict] = {}
    for rec in ordered:
        tool_results.update(_extract_tool_results(rec))
    for tur in tool_results.values():
        if not isinstance(tur, dict):
            continue
        original = tur.get("originalFile")
        file_path = tur.get("filePath") or tur.get("file_path")
        if original is not None and file_path:
            tracker.seed_original(file_path, original)

    turns: list[Turn] = []
    turn_id = 0
    for rec in ordered:
        if not _is_message_record(rec):
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") if isinstance(msg, dict) else rec.get("type")
        if role != "assistant":
            continue
        blocks = _message_content(rec)
        edits = _tool_use_edits(blocks)
        text = _assistant_text(blocks)

        # Apply edits cumulatively so `before/after` reflect DAG order.
        for edit in edits:
            if not edit.path:
                continue
            before, after = tracker.apply_edit(edit)
            edit.before_content = before
            edit.after_content = after
            edit.source = "originalFile" if edit.path in tracker._origin_seen else "replay"

        if not text and not edits:
            continue

        turn_id += 1
        turns.append(
            Turn(
                turn_id=turn_id,
                uuid=rec.get("uuid", ""),
                parent_uuid=rec.get("parentUuid"),
                assistant_text=text,
                tool_calls=edits,
                timestamp=rec.get("timestamp"),
            )
        )
    return turns, tracker
