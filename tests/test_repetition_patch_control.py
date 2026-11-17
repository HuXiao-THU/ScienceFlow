"""Unit tests for the repetition-guard policy controller (FakeHost/FakeCoordinator)."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sf_repetition_patch import control
from sf_repetition_patch.control import maybe_handle_repetition_trip
from sf_repetition_patch.guard import IdenticalToolCallRepeatGuard
from sf_repetition_patch.judge import REPETITION_JUDGE_BEGIN, REPETITION_JUDGE_END


def _judge_text(degraded: bool, reason: str = "loop", focus: str = "") -> str:
    focus_line = focus if focus else "null"
    return (
        f"{REPETITION_JUDGE_BEGIN}\n"
        f"degraded: {'true' if degraded else 'false'}\n"
        f"confidence: high\n"
        f"reason: {reason}\n"
        f"recommended_focus: {focus_line}\n"
        f"{REPETITION_JUDGE_END}\n"
    )


class FakeMemory:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add_message(self, message) -> None:
        self.messages.append(str(getattr(message, "content", message)))


class FakeCoordinator:
    def __init__(self, with_stage: bool = True) -> None:
        self.stage_snapshots = {"S01": object()} if with_stage else {}
        self.pending_estra = None
        self.events: list[tuple[str, dict]] = []
        self.last_decision: dict | None = None

    def _jsonl(self, name: str, record: dict) -> None:
        self.events.append((name, dict(record)))

    async def _set_pending_estra_from_decision(self, *, agent, target, decision) -> None:
        self.last_decision = dict(decision)
        # Stock behaviour: keep_current silently does nothing without ledger cards.
        if self.stage_snapshots:
            self.pending_estra = decision

    def stage_capture(self, **kwargs):  # bound-method shape for digging
        return None


class FakeHost:
    def __init__(
        self,
        guard: IdenticalToolCallRepeatGuard,
        coordinator: FakeCoordinator | None,
        judge_text: str = "",
        judge_error: bool = False,
    ) -> None:
        self._guard_manager = SimpleNamespace(_guards=[guard])
        self._workspace_dir = Path("/tmp/fake-ws")
        self._run_policy = SimpleNamespace(deadline_monotonic=time.monotonic() + 3600.0)
        self._initial_solution_sha = "sha0"
        self._sha_of_solution = "sha0"
        self.memory = FakeMemory()
        self.compact_called = False
        self.judge_calls: list[tuple[str, str]] = []
        self.judge_text = judge_text
        self.judge_error = judge_error
        self._scienceflow_worker_search_outcome = ""
        self._lnr_repetition_guard_stop_reason = ""
        if coordinator is not None:
            self.callback_ports = SimpleNamespace(stage_capture=coordinator.stage_capture)

    def _sha256_of_solution(self) -> str:
        return self._sha_of_solution

    async def run_ephemeral_agentic_route_prompt(self, prompt: str, **kwargs):
        self.judge_calls.append((prompt, str(kwargs.get("trigger"))))
        if self.judge_error:
            raise RuntimeError("judge unavailable")
        return self.judge_text

    async def compact(self, **kwargs) -> str:
        self.compact_called = True
        return "Compacted history (1 record)."

    def _log_info(self, *args, **kwargs) -> None:
        pass


def _trip(guard: IdenticalToolCallRepeatGuard, n: int = 3) -> None:
    from inquirycraft.tools import ToolResult

    result = ToolResult(output="same output")
    for _ in range(n):
        guard.on_tool_result("bash", {"command": "python3 solution.py"}, result)


@pytest.fixture(autouse=True)
def _clean_module_state():
    control._module_state.clear()
    yield
    control._module_state.clear()


async def test_no_trip_defers_to_original_hook() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    host = FakeHost(guard, coordinator=None)
    assert await maybe_handle_repetition_trip(host) is None


async def test_judge_false_sets_cooldown_and_continues_seamlessly() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    host = FakeHost(guard, coordinator, judge_text=_judge_text(False, "sha changed"))
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    # Session continues: no restart, no injected message, no outcome.
    assert coordinator.pending_estra is None
    assert host.memory.messages == []
    assert host._scienceflow_worker_search_outcome == ""
    # Cooldown armed on the coordinator (survives agent replacement).
    assert coordinator.repetition_judge_cooldown_until > time.time()
    # A second trip within the cooldown does not call the judge again.
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert len(host.judge_calls) == 1
    assert coordinator.repetition_suppressed_count == 1
    # Suppressed evidence flows into the next judged fact card.
    coordinator.repetition_judge_cooldown_until = 0.0
    _trip(guard)
    await maybe_handle_repetition_trip(host)
    assert len(host.judge_calls) == 2
    assert '"suppressed_repeat_count": 1' in host.judge_calls[1][0]


async def test_judge_error_fails_open() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    host = FakeHost(guard, coordinator, judge_error=True)
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert coordinator.pending_estra is None
    assert coordinator.repetition_judge_cooldown_until > time.time()
    assert host.compact_called is False


async def test_degraded_with_stage_sets_pending_estra_with_diagnosis() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    host = FakeHost(
        guard,
        coordinator,
        judge_text=_judge_text(True, "identical outputs", "check dataset/train.csv for an id column"),
    )
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is False
    decision = coordinator.last_decision
    assert decision["action"] == "keep_current"
    assert decision["compact"] is True
    assert decision["trigger_source"] == "repetition_guard"
    assert "3x identical" in decision["reason"]
    assert "id column" in decision["reason"]
    assert "id column" in decision["redirect_focus"]
    assert coordinator.pending_estra == coordinator.last_decision
    assert coordinator.repetition_guard_restart_count == 1
    # No cooldown after a successful restart: an immediate re-trip must be
    # judgeable right away so max_restarts converges fast.
    assert not hasattr(coordinator, "repetition_judge_cooldown_until") or (
        coordinator.repetition_judge_cooldown_until == 0.0
    )
    assert host._lnr_repetition_guard_stop_reason == "repetition_guard_restart"
    assert host.compact_called is False


async def test_degraded_without_stage_falls_back_to_lite_compact() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=False)
    host = FakeHost(
        guard,
        coordinator,
        judge_text=_judge_text(True, "stuck", "inspect the feature columns of train.csv"),
    )
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert coordinator.pending_estra is None
    assert host.compact_called is True
    assert len(host.memory.messages) == 1
    assert "inspect the feature columns of train.csv" in host.memory.messages[0]
    assert coordinator.repetition_guard_restart_count == 1


async def test_degraded_without_coordinator_uses_module_state_lite() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    host = FakeHost(guard, coordinator=None, judge_text=_judge_text(True, "stuck"))
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert host.compact_called is True
    bucket = control._module_state[str(host._workspace_dir)]
    assert bucket["repetition_guard_restart_count"] == 1


async def test_restart_budget_exhausted_terminates_worker() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    coordinator.repetition_guard_restart_count = 3
    host = FakeHost(guard, coordinator, judge_text=_judge_text(True, "stuck"))
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is False
    assert host._scienceflow_worker_search_outcome == "finalize_candidate"
    assert host._lnr_repetition_guard_stop_reason == "repetition_guard_restart_exhausted"
    assert coordinator.pending_estra is None
    assert coordinator.repetition_guard_restart_count == 3
    assert any(
        r.get("event") == "repetition_guard_restart_budget_exhausted"
        for _, r in coordinator.events
    )


async def test_restart_budget_exhausted_without_stage_labels_no_candidate() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=False)
    coordinator.repetition_guard_restart_count = 3
    host = FakeHost(guard, coordinator, judge_text=_judge_text(True, "stuck"))
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is False
    assert host._scienceflow_worker_search_outcome == "stop_no_candidate"


async def test_low_budget_skips_judge() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    host = FakeHost(guard, coordinator)
    host._run_policy = SimpleNamespace(deadline_monotonic=time.monotonic() + 60.0)
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert host.judge_calls == []
    assert coordinator.pending_estra is None


async def test_judge_disabled_is_observability_only() -> None:
    import os

    guard = IdenticalToolCallRepeatGuard(threshold=3)
    host = FakeHost(guard, coordinator=None)
    _trip(guard)
    os.environ["SF_REPEAT_GUARD_JUDGE_ENABLED"] = "false"
    try:
        # The trip is consumed but no action follows (deferred to original hook).
        assert await maybe_handle_repetition_trip(host) is None
        assert host.judge_calls == []
    finally:
        os.environ.pop("SF_REPEAT_GUARD_JUDGE_ENABLED", None)


async def test_workspace_sha_evidence_enters_fact_card() -> None:
    guard = IdenticalToolCallRepeatGuard(threshold=3)
    host = FakeHost(guard, coordinator=None, judge_text=_judge_text(False, "sha changed"))
    host._sha_of_solution = "sha1-different"
    _trip(guard)
    assert await maybe_handle_repetition_trip(host) is True
    assert '"solution_sha_changed_since_run_start": true' in host.judge_calls[0][0]
    assert host.judge_calls[0][1] == "repetition_guard_judge"


def test_coordinator_digging_via_partial_wrapper() -> None:
    import functools

    guard = IdenticalToolCallRepeatGuard(threshold=3)
    coordinator = FakeCoordinator(with_stage=True)
    host = FakeHost(guard, coordinator)
    host.callback_ports = SimpleNamespace(
        stage_capture=functools.partial(coordinator.stage_capture)
    )
    assert control.dig_coordinator(host) is coordinator
    # Cached after first dig.
    host.callback_ports = None
    assert control.dig_coordinator(host) is coordinator