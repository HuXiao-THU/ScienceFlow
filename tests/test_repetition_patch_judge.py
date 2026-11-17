"""Unit tests for the clean-context repetition judge."""

from __future__ import annotations

from sf_repetition_patch.judge import (
    REPETITION_JUDGE_BEGIN,
    REPETITION_JUDGE_END,
    REPETITION_JUDGE_SYSTEM,
    build_repetition_judge_prompt,
    parse_repetition_judge_response,
    validate_recommended_focus,
)


def _block(degraded: str, reason: str, focus: str = "", confidence: str = "high") -> str:
    focus_line = focus if focus else "null"
    return (
        f"{REPETITION_JUDGE_BEGIN}\n"
        f"degraded: {degraded}\n"
        f"confidence: {confidence}\n"
        f"reason: {reason}\n"
        f"recommended_focus: {focus_line}\n"
        f"{REPETITION_JUDGE_END}\n"
    )


def test_system_prompt_states_the_three_criteria() -> None:
    low = REPETITION_JUDGE_SYSTEM.lower()
    assert "degraded=false" in low or "degraded: false" in low
    assert "workspace" in low
    assert "concrete" in low
    assert "no conversation history" in low


def test_prompt_embeds_fact_card() -> None:
    prompt = build_repetition_judge_prompt(
        {"tool_name": "bash", "streak": 4, "normalized_args": "python3 solution.py"}
    )
    assert "python3 solution.py" in prompt
    assert '"streak": 4' in prompt
    assert REPETITION_JUDGE_BEGIN in prompt


def test_parse_valid_degraded_block() -> None:
    verdict, block, error = parse_repetition_judge_response(
        _block("true", "identical outputs, no file change", "check dataset/train.csv for an id column")
    )
    assert error == ""
    assert verdict["degraded"] is True
    assert verdict["confidence"] == "high"
    assert "id column" in verdict["recommended_focus"]
    assert block.startswith(REPETITION_JUDGE_BEGIN)


def test_parse_not_degraded_block() -> None:
    verdict, _block_text, error = parse_repetition_judge_response(
        _block("false", "solution.py sha changed between calls")
    )
    assert error == ""
    assert verdict["degraded"] is False
    assert verdict["recommended_focus"] == ""


def test_parse_missing_block_fails_cleanly() -> None:
    verdict, block, error = parse_repetition_judge_response("I think it is fine.")
    assert verdict == {}
    assert block == ""
    assert error == "missing_judge_block"


def test_parse_invalid_degraded_value() -> None:
    verdict, _block_text, error = parse_repetition_judge_response(
        _block("maybe", "unclear")
    )
    assert verdict["degraded"] is False
    assert error == "invalid_degraded"


def test_vague_focus_is_rejected() -> None:
    verdict, _block_text, error = parse_repetition_judge_response(
        _block("true", "loop confirmed", "try a different approach next time")
    )
    assert verdict["degraded"] is True
    assert verdict["recommended_focus"] == ""
    assert "vague_focus_rejected" in error


def test_validate_recommended_focus_concrete_vs_vague() -> None:
    assert validate_recommended_focus("check dataset/train.csv for an id column before joining")
    assert validate_recommended_focus("lower --epochs to 1 and rerun the smoke pass")
    assert not validate_recommended_focus("try a different approach")
    assert not validate_recommended_focus("improve the code")
    assert not validate_recommended_focus("retry")