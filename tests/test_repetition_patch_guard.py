"""Unit tests for the pure repetition detector guard."""

from __future__ import annotations

from inquirycraft.tools import ToolResult

from sf_repetition_patch.guard import IdenticalToolCallRepeatGuard, repeat_signature


def _bash() -> dict:
    return {"command": "python3 solution.py"}


def test_trip_at_threshold_and_card_refresh() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    result = ToolResult(output="epoch 1 done")
    guard.on_tool_result("bash", _bash(), result)
    guard.on_tool_result("bash", _bash(), result)
    assert guard.pending_fact_card is None
    guard.on_tool_result("bash", _bash(), result)
    card = guard.pending_fact_card
    assert card is not None
    assert card["streak"] == 3
    assert card["tool_name"] == "bash"
    assert card["total_calls_in_run"] == 3
    guard.on_tool_result("bash", _bash(), result)
    assert guard.pending_fact_card["streak"] == 4


def test_signature_change_resets_trip() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    result = ToolResult(output="ok")
    for _ in range(3):
        guard.on_tool_result("bash", _bash(), result)
    assert guard.pending_fact_card is not None
    guard.on_tool_result("read", {"path": "a.py", "offset": 1}, result)
    assert guard.pending_fact_card is None


def test_intermittent_repetition_never_trips() -> None:
    # A-B-A-B-A: no two consecutive identical calls -> no trip. This is the
    # deliberate semantics: cumulative counting would kill legitimate
    # evaluate -> improve -> evaluate research loops.
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    result = ToolResult(output="ok")
    for _ in range(5):
        guard.on_tool_result("bash", _bash(), result)
        guard.on_tool_result("read", {"path": "a.py"}, result)
    assert guard.pending_fact_card is None


def test_different_tools_same_args_are_distinct() -> None:
    assert repeat_signature("bash", _bash()) != repeat_signature("read", _bash())


def test_thought_field_is_stripped() -> None:
    assert repeat_signature("bash", {"command": "ls", "thought": "a"}) == repeat_signature(
        "bash", {"command": "ls", "thought": "b"}
    )


def test_nonserializable_args_are_tolerated() -> None:
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    sig = repeat_signature("bash", {"command": Weird()})
    assert sig.startswith("bash:")
    guard = IdenticalToolCallRepeatGuard(threshold=2)
    guard.on_tool_result("bash", {"command": Weird()}, ToolResult(output="x"))
    guard.on_tool_result("bash", {"command": Weird()}, ToolResult(output="x"))
    # Same repr -> same signature -> trip still works through the fallback path.
    assert guard.pending_fact_card is not None


def test_guard_never_terminates_or_coaches() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=1)
    out = guard.on_tool_result("bash", _bash(), ToolResult(output="x"))
    assert out is None
    assert guard.pending_fact_card is not None
    assert guard.should_terminate_run() is False


def test_reset_clears_state() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=2)
    result = ToolResult(output="x")
    guard.on_tool_result("bash", _bash(), result)
    guard.on_tool_result("bash", _bash(), result)
    assert guard.pending_fact_card is not None
    guard.reset()
    assert guard.pending_fact_card is None
    guard.on_tool_result("bash", _bash(), result)
    assert guard.pending_fact_card is None


def test_threshold_zero_disables_detection() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=0)
    result = ToolResult(output="x")
    for _ in range(10):
        assert guard.on_tool_result("bash", _bash(), result) is None
    assert guard.pending_fact_card is None


def test_fact_card_evidence_fields() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=2)
    ok = ToolResult(output="all good")
    err = ToolResult(output="", error="non-zero exit code 1")
    guard.on_tool_result("bash", _bash(), ok)
    guard.on_tool_result("bash", _bash(), err)
    card = guard.pending_fact_card
    assert card["streak"] == 2
    assert card["recent_outputs"][0]["error"] is False
    assert card["recent_outputs"][1]["error"] is True
    assert "python3 solution.py" in card["normalized_args"]