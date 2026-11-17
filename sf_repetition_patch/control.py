"""Policy controller consumed from the wrapped ``should_continue`` hook.

Every round the hook calls :func:`maybe_handle_repetition_trip`.  Without a
pending trip it returns ``None`` (defer to the original hook).  With a trip it
decides: cooldown / low budget -> keep running; judge says not degraded ->
cooldown and keep running; degraded -> restart (full pending_estra when stages
exist, lite in-session compact otherwise) or, past the restart budget,
terminate the worker while keeping the best stage.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from inquirycraft.memory import Message

from . import judge as judge_mod
from .config import RepeatGuardConfig, load_config
from .guard import IdenticalToolCallRepeatGuard

logger = logging.getLogger("scienceflow")

GUARD_NAME = IdenticalToolCallRepeatGuard.name

# Fallback state bucket for agents without an LNR coordinator (REPL, data prep).
_module_state: dict[str, dict[str, Any]] = {}

_COMPACT_RESERVE_SEC = 120.0  # headroom for one mid-run compact near deadline


def _find_guard(host: Any) -> Any:
    manager = getattr(host, "_guard_manager", None)
    guards = getattr(manager, "_guards", None) if manager is not None else None
    if not guards:
        return None
    for guard in guards:
        if getattr(guard, "name", "") == GUARD_NAME:
            return guard
    return None


def dig_coordinator(host: Any) -> Any:
    """Recover the LNR coordinator from the agent's callback ports.

    ``stage_capture`` is installed as a bound coordinator method (optionally
    ``functools.partial``-wrapped), so its ``__self__`` is the LnrSolver.  The
    coordinator outlives agent replacements (``make_agent``), which is exactly
    where cross-run state (cooldown, restart count) must live to survive.
    """
    if hasattr(host, "_lnr_repetition_coordinator"):
        return host._lnr_repetition_coordinator
    coordinator = None
    try:
        ports = getattr(host, "callback_ports", None)
        port = getattr(ports, "stage_capture", None) if ports is not None else None
        if callable(port):
            fn = getattr(port, "func", port)  # unwrap functools.partial
            coordinator = getattr(fn, "__self__", None)
    except Exception:
        coordinator = None
    host._lnr_repetition_coordinator = coordinator
    return coordinator


def _state_bucket(host: Any, coordinator: Any) -> Any:
    if coordinator is not None:
        return coordinator
    key = str(getattr(host, "_workspace_dir", "") or "") or "default"
    return _module_state.setdefault(key, {})


def _state_get(bucket: Any, name: str, default: Any = None) -> Any:
    if isinstance(bucket, dict):
        return bucket.get(name, default)
    return getattr(bucket, name, default)


def _state_set(bucket: Any, name: str, value: Any) -> None:
    if isinstance(bucket, dict):
        bucket[name] = value
    else:
        setattr(bucket, name, value)


def _emit(coordinator: Any, event: str, payload: dict[str, Any]) -> None:
    record = {"event": event, **payload}
    writer = getattr(coordinator, "_jsonl", None) if coordinator is not None else None
    if callable(writer):
        try:
            writer("lhr_context_events.jsonl", record)
            return
        except Exception:
            logger.debug("[repetition-guard] event write failed", exc_info=True)
    logger.info("[repetition-guard] %s", record)


def _solution_sha(host: Any) -> str | None:
    try:
        return host._sha256_of_solution()
    except Exception:
        return None


async def maybe_handle_repetition_trip(host: Any) -> bool | None:
    """Consume a pending repetition trip; return a should_continue decision."""
    guard = _find_guard(host)
    card = guard.pending_fact_card if guard is not None else None
    if not card:
        return None
    guard.pending_fact_card = None  # consume regardless of what follows

    cfg = load_config()
    if not cfg.judge_enabled:
        return None  # observability-only: detect, never act

    coordinator = dig_coordinator(host)
    bucket = _state_bucket(host, coordinator)
    now = time.time()

    cooldown_until = float(_state_get(bucket, "repetition_judge_cooldown_until", 0.0) or 0.0)
    if now < cooldown_until:
        # Cooldown suppresses only the judge call; the guard keeps detecting.
        # The suppressed count persists in the bucket and flows into the next
        # trip's fact card at judge time.
        suppressed = int(_state_get(bucket, "repetition_suppressed_count", 0) or 0) + 1
        _state_set(bucket, "repetition_suppressed_count", suppressed)
        return True

    deadline = getattr(getattr(host, "_run_policy", None), "deadline_monotonic", None)
    if deadline is not None and deadline - time.monotonic() < (
        cfg.judge_timeout_sec + _COMPACT_RESERVE_SEC
    ):
        _emit(coordinator, "repetition_guard_judge_skipped_low_budget", {"streak": card.get("streak")})
        return True

    # Augment the card with workspace evidence the judge needs for criterion (a).
    current_sha = _solution_sha(host)
    initial_sha = getattr(host, "_initial_solution_sha", None)
    card["solution_sha_changed_since_run_start"] = bool(
        current_sha and initial_sha and current_sha != initial_sha
    )
    card["suppressed_repeat_count"] = int(
        _state_get(bucket, "repetition_suppressed_count", 0) or 0
    )

    verdict = await _run_judge(host, card, cfg)
    degraded = bool(verdict.get("degraded"))

    if not degraded:
        # Judge failed and judge said "fine" share one path (fail-open: a
        # destructive compact+restart must not fire on a flaky judge).
        _state_set(bucket, "repetition_judge_cooldown_until", now + cfg.cooldown_sec)
        _state_set(bucket, "repetition_suppressed_count", 0)
        _emit(
            coordinator,
            "repetition_guard_judged",
            {
                "degraded": False,
                "confidence": verdict.get("confidence"),
                "reason": verdict.get("reason"),
                "streak": card.get("streak"),
            },
        )
        return True

    restarts = int(_state_get(bucket, "repetition_guard_restart_count", 0) or 0)
    if restarts >= cfg.max_restarts:
        _emit(
            coordinator,
            "repetition_guard_restart_budget_exhausted",
            {"streak": card.get("streak"), "restarts": restarts},
        )
        has_stage = bool(getattr(coordinator, "stage_snapshots", None)) if coordinator else False
        host._scienceflow_worker_search_outcome = (
            "finalize_candidate" if has_stage else "stop_no_candidate"
        )
        host._lnr_repetition_guard_stop_reason = "repetition_guard_restart_exhausted"
        return False

    if coordinator is not None and getattr(coordinator, "stage_snapshots", None):
        if await _full_restart(host, coordinator, card, verdict):
            _state_set(bucket, "repetition_guard_restart_count", restarts + 1)
            _state_set(bucket, "repetition_suppressed_count", 0)
            _emit(
                coordinator,
                "repetition_guard_restarted",
                {
                    "streak": card.get("streak"),
                    "restarts": restarts + 1,
                    "trigger_source": "repetition_guard",
                },
            )
            host._lnr_repetition_guard_stop_reason = "repetition_guard_restart"
            return False
        # pending_estra could not be set (no ledger cards): fall through to lite.

    await _lite_restart(host, card, verdict)
    _state_set(bucket, "repetition_guard_restart_count", restarts + 1)
    _state_set(bucket, "repetition_suppressed_count", 0)
    _emit(
        coordinator,
        "repetition_guard_compact_inband",
        {"streak": card.get("streak"), "restarts": restarts + 1},
    )
    return True


async def _run_judge(host: Any, card: dict[str, Any], cfg: RepeatGuardConfig) -> dict[str, Any]:
    """Ask the clean-context judge; any failure degrades to not-degraded."""
    prompt = judge_mod.build_repetition_judge_prompt(card)
    try:
        text = await host.run_ephemeral_agentic_route_prompt(
            prompt,
            trigger="repetition_guard_judge",
            base_messages=[],
            system_messages=[Message.system_message(judge_mod.REPETITION_JUDGE_SYSTEM)],
            timeout=cfg.judge_timeout_sec,
            llm_role="code",
        )
    except Exception as exc:
        return {
            "degraded": False,
            "confidence": "low",
            "reason": f"judge_error:{type(exc).__name__}",
            "recommended_focus": "",
        }
    verdict, _block, error = judge_mod.parse_repetition_judge_response(text)
    if not verdict:
        # Unusable response (missing block): fail open. A rejected vague focus
        # is recoverable — the verdict stands and the fallback constraint
        # covers the missing recommendation.
        verdict = {
            "degraded": False,
            "confidence": "low",
            "reason": f"judge_parse:{error}",
            "recommended_focus": "",
        }
    return verdict


async def _full_restart(host: Any, coordinator: Any, card: dict[str, Any], verdict: dict[str, Any]) -> bool:
    """Set ``pending_estra`` (keep_current); the stock worker loop does the rest.

    Diagnosis rides the decision dict's first-class fields: ``reason`` flows
    into the state packet and ``redirect_focus`` into the keep_current compact
    prompt — without this, the fresh lineage would rebuild the same failing
    hypothesis and repeat the command, burning the restart budget.
    """
    focus = str(verdict.get("recommended_focus") or "").strip()
    reason = (
        f"repetition guard: {card.get('streak')}x identical {card.get('tool_name')} "
        f"calls with unchanged workspace; judge: {verdict.get('reason')}"
    )
    reason += (
        f"; next step must change: {focus}"
        if focus
        else "; do not repeat the exact command, change a concrete input, path, or parameter first"
    )
    decision = {
        "action": "keep_current",
        "compact": True,
        "trigger_source": "repetition_guard",
        "reason": reason[:600],
        "redirect_focus": focus[:240],
        "compact_strength": "",
    }
    setter = getattr(coordinator, "_set_pending_estra_from_decision", None)
    if not callable(setter):
        return False
    try:
        await setter(agent=host, target="", decision=dict(decision))
    except Exception:
        logger.warning("[repetition-guard] pending_estra set failed; lite fallback", exc_info=True)
        return False
    return bool(getattr(coordinator, "pending_estra", None))


async def _lite_restart(host: Any, card: dict[str, Any], verdict: dict[str, Any]) -> None:
    """In-session fallback: compact history, then append the diagnosis."""
    focus = str(verdict.get("recommended_focus") or "").strip()
    lines = [
        "[Guard] Repeated identical tool calls with no workspace change were judged a repetition loop.",
        f"Observed: {card.get('streak')}x identical {card.get('tool_name')} calls.",
        f"Diagnosis: {verdict.get('reason')}",
    ]
    lines.append(
        f"Next step must change: {focus}"
        if focus
        else "Do not repeat the exact command; change a concrete input, path, or parameter first."
    )
    try:
        await host.compact(mid_run=True)
    except Exception:
        logger.warning("[repetition-guard] mid-run compact failed", exc_info=True)
    try:
        host.memory.add_message(Message.user_message("\n".join(lines)))
    except Exception:
        logger.debug("[repetition-guard] diagnosis message append failed", exc_info=True)


__all__ = ["GUARD_NAME", "dig_coordinator", "maybe_handle_repetition_trip"]