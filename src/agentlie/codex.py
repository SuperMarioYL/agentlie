"""Parse Codex CLI session logs into the same Turn / ActualEdit model.

Claude Code stores a session as a parentUuid-linked JSONL DAG; Codex
(the OpenAI coding agent) stores a flat, time-ordered JSONL event log.
The two share nothing structurally, so ``agentlie check`` needs a second
front-end that normalises Codex logs into the exact ``Turn`` /
``ActualEdit`` shape the extractor + verifier already consume — after
this module runs, the rest of the pipeline can't tell which agent
produced the session.

Codex log shape (tolerant — fields drift across Codex versions):

    {"type": "message",  "role": "assistant", "content": "...text..."}
    {"type": "function_call", "name": "shell",
     "arguments": {"command": ["apply_patch", "*** Begin Patch ..."]}}
    {"type": "function_call", "name": "apply_patch",
     "arguments": {"input": "*** Begin Patch ..."}}
    {"type": "response_item", "payload": {... any of the above nested ...}}

Records may also wrap the real event under ``payload`` / ``item`` /
``msg``; we unwrap those before classifying. A line we can't classify is
skipped, never fatal — the same fail-soft contract as the Claude Code
parser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator, Optional

from agentlie.models import ActualEdit, Turn
from agentlie.parser import FileStateTracker

# Tool names Codex uses for file mutations.
_PATCH_TOOLS = {"apply_patch", "applypatch", "edit_file", "write_file", "create_file"}

# apply_patch envelope markers.
_PATCH_BEGIN = "*** Begin Patch"
_PATCH_FILE_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<path>.+?)\s*$", re.MULTILINE
)


def _iter_records(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def _unwrap(rec: dict) -> dict:
    """Peel common Codex envelope keys until we reach the real event."""
    for _ in range(4):
        for key in ("payload", "item", "msg", "data"):
            inner = rec.get(key)
            if isinstance(inner, dict) and (
                inner.get("type") or inner.get("role") or inner.get("name")
            ):
                rec = inner
                break
        else:
            break
    return rec


def looks_like_codex(path: str | Path) -> bool:
    """Heuristic: a Codex log has function_call / response_item records and no
    Claude-Code parentUuid chain."""
    path = Path(path)
    if not path.exists():
        return False
    saw_codex = False
    for i, rec in enumerate(_iter_records(path)):
        if i > 40:
            break
        if "parentUuid" in rec or "toolUseResult" in rec:
            return False
        ev = _unwrap(rec)
        t = ev.get("type")
        if t in {"function_call", "response_item", "function_call_output"}:
            saw_codex = True
        if ev.get("name") in _PATCH_TOOLS:
            saw_codex = True
    return saw_codex


def _assistant_text(ev: dict) -> str:
    """Pull assistant prose from a Codex message event."""
    if ev.get("role") != "assistant" and ev.get("type") not in {"message", "assistant"}:
        return ""
    content = ev.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # OpenAI-style {"type":"output_text"|"text","text":"..."}
                txt = block.get("text") or block.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _patch_input(ev: dict) -> Optional[str]:
    """Extract the apply_patch body from a Codex function_call event."""
    if ev.get("type") not in {"function_call", None} and "name" not in ev:
        return None
    if ev.get("name") not in _PATCH_TOOLS and ev.get("type") != "function_call":
        return None
    args = ev.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"input": args}
    if not isinstance(args, dict):
        return None
    # apply_patch may arrive as {"input": "..."} or {"command": ["apply_patch", "..."]}.
    raw = args.get("input") or args.get("patch") or args.get("content")
    if not raw:
        cmd = args.get("command")
        if isinstance(cmd, list):
            for token in cmd:
                if isinstance(token, str) and _PATCH_BEGIN in token:
                    raw = token
                    break
        elif isinstance(cmd, str) and _PATCH_BEGIN in cmd:
            raw = cmd
    if isinstance(raw, str) and _PATCH_BEGIN in raw:
        return raw
    # Direct file write: {"path": "...", "content": "..."}
    return None


def _edits_from_patch(patch: str) -> list[ActualEdit]:
    """Turn an apply_patch envelope into ActualEdit (Write/Edit) records.

    We model each touched file as a Write of its post-patch hunk text. This is
    a coarse but honest after-state: the verifier's string + AST checks operate
    on it the same way they do for Claude Code Write edits.
    """
    edits: list[ActualEdit] = []
    files = list(_PATCH_FILE_RE.finditer(patch))
    if not files:
        return edits
    for idx, match in enumerate(files):
        path = match.group("path").strip()
        start = match.end()
        end = files[idx + 1].start() if idx + 1 < len(files) else len(patch)
        body = patch[start:end]
        # Strip the trailing "*** End Patch" marker if it landed in the last hunk.
        body = body.split("*** End Patch", 1)[0]
        # Reconstruct added/context lines (drop the leading +/space markers).
        added: list[str] = []
        for line in body.splitlines():
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith(" "):
                added.append(line[1:])
            # '-' lines are removals; omit from the after-state.
        content = "\n".join(added).strip("\n")
        if "Delete File:" in patch[max(0, match.start() - 40):match.end()]:
            edits.append(ActualEdit(tool="Write", path=path, content=""))
        else:
            edits.append(ActualEdit(tool="Write", path=path, content=content))
    return edits


def _direct_write(ev: dict) -> list[ActualEdit]:
    """Handle write_file/create_file style events with explicit path+content."""
    if ev.get("name") not in {"write_file", "create_file", "edit_file"}:
        return []
    args = ev.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return []
    if not isinstance(args, dict):
        return []
    path = args.get("path") or args.get("file_path")
    if not path:
        return []
    if "old_string" in args or "new_string" in args:
        return [
            ActualEdit(
                tool="Edit",
                path=str(path),
                old_string=args.get("old_string"),
                new_string=args.get("new_string"),
            )
        ]
    content = args.get("content") or args.get("contents") or ""
    return [ActualEdit(tool="Write", path=str(path), content=str(content))]


def parse_codex_session(path: str | Path) -> tuple[list[Turn], FileStateTracker]:
    """Parse a Codex JSONL log into Turn objects + a populated FileStateTracker.

    Codex logs are flat and time-ordered (no DAG), so a "turn" is an assistant
    message together with any file-mutating tool calls that follow it before the
    next assistant message — mirroring the Claude Code grouping.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Codex session log not found: {path}")

    tracker = FileStateTracker()
    turns: list[Turn] = []
    turn_id = 0
    pending_text = ""
    pending_edits: list[ActualEdit] = []

    def flush() -> None:
        nonlocal turn_id, pending_text, pending_edits
        if not pending_text and not pending_edits:
            return
        for edit in pending_edits:
            if not edit.path:
                continue
            before, after = tracker.apply_edit(edit)
            edit.before_content = before
            edit.after_content = after
            edit.source = "replay"
        turn_id += 1
        turns.append(
            Turn(
                turn_id=turn_id,
                uuid=f"codex-{turn_id}",
                parent_uuid=None,
                assistant_text=pending_text,
                tool_calls=pending_edits,
            )
        )
        pending_text = ""
        pending_edits = []

    for rec in _iter_records(path):
        ev = _unwrap(rec)
        text = _assistant_text(ev)
        if text:
            # A new assistant message starts a new turn.
            flush()
            pending_text = text
            continue
        edits: list[ActualEdit] = []
        patch = _patch_input(ev)
        if patch:
            edits = _edits_from_patch(patch)
        if not edits:
            edits = _direct_write(ev)
        if edits:
            pending_edits.extend(edits)

    flush()
    return turns, tracker
