"""Tests for the JSONL parser + FileStateTracker."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlie.parser import FileStateTracker, parse_session
from agentlie.models import ActualEdit

FIXTURE = Path(__file__).parent / "fixtures" / "lying_transcript.jsonl"


def test_parse_session_returns_only_assistant_turns():
    turns, _ = parse_session(FIXTURE)
    assert len(turns) == 7
    assert all(t.uuid.startswith("a") for t in turns)


def test_parse_session_filters_queue_operation_records():
    turns, _ = parse_session(FIXTURE)
    assert not any(t.uuid == "q1" for t in turns)


def test_parser_groups_tool_use_into_turn():
    turns, _ = parse_session(FIXTURE)
    turn1 = turns[0]
    assert "null check" in turn1.assistant_text
    assert len(turn1.tool_calls) == 1
    assert turn1.tool_calls[0].tool == "Edit"
    assert turn1.tool_calls[0].path == "src/auth.py"


def test_filestate_tracker_uses_originalfile_when_present():
    turns, tracker = parse_session(FIXTURE)
    # auth.py was seeded from originalFile
    assert "legacy_token" in tracker.get("src/auth.py")
    assert "user is None" in tracker.get("src/auth.py")


def test_filestate_tracker_replay_falls_back_when_no_origin():
    tracker = FileStateTracker()
    # No originalFile seeded — apply two writes and check cumulative state.
    edit = ActualEdit(tool="Write", path="foo.py", content="A\n")
    tracker.apply_edit(edit)
    assert tracker.get("foo.py") == "A\n"
    edit2 = ActualEdit(tool="Edit", path="foo.py", old_string="A", new_string="B")
    tracker.apply_edit(edit2)
    assert tracker.get("foo.py") == "B\n"


def test_parse_session_handles_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_session(FIXTURE.parent / "does_not_exist.jsonl")


def test_parser_attaches_before_after_to_edits():
    turns, _ = parse_session(FIXTURE)
    edit = turns[0].tool_calls[0]
    assert edit.before_content is not None
    assert edit.after_content is not None
    assert edit.before_content != edit.after_content
    assert "user is None" in edit.after_content


def test_parser_records_tool_call_for_write():
    turns, _ = parse_session(FIXTURE)
    turn2 = turns[1]
    assert turn2.tool_calls[0].tool == "Write"
    assert turn2.tool_calls[0].path == "src/util.py"
    assert "import logging" in turn2.tool_calls[0].after_content


def test_dag_walk_preserves_parent_child_order():
    turns, _ = parse_session(FIXTURE)
    ids = [t.turn_id for t in turns]
    assert ids == list(range(1, len(turns) + 1))
