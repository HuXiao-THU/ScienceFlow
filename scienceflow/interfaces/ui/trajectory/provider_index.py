"""Read the compact per-call provider audit written for every LLM request."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_COMPLETED = "completed"
_FAILED = "failed"


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _tokens(row: dict[str, Any]) -> dict[str, Any]:
    def value(name: str) -> int | None:
        raw = row.get(name)
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    input_tokens = value("tokens_input")
    cached = value("tokens_cached")
    output = value("tokens_output")
    return {
        "input": input_tokens,
        "output": output,
        "cached": cached,
        "cache_rate": row.get("cache_rate"),
        "known": row.get("usage_status") == "known",
    }


def read_provider_calls(path: str | Path) -> list[dict[str, Any]]:
    """Return completed/failed provider rows from one worker audit file."""
    source = Path(path)
    if not source.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with source.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("phase") not in {_COMPLETED, _FAILED}:
                    continue
                rows.append(row)
    except OSError:
        return []
    return rows


def _call_key(row: dict[str, Any]) -> str:
    call_id = str(row.get("call_id") or "").strip()
    if call_id:
        return call_id
    return f"{row.get('session_id') or '?'}:llm:{row.get('turn_id') or '?'}"


def _first(row: dict[str, Any], name: str, default: Any = None) -> Any:
    value = row.get(name)
    return default if value in (None, "") else value


def logical_calls(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse retry attempts into one logical call per ``call_id``."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        key = _call_key(row)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    calls: list[dict[str, Any]] = []
    for key in order:
        attempts = sorted(
            groups[key],
            key=lambda row: (
                int(row.get("attempt") or 1),
                _number(row.get("timestamp"), 0.0) or 0.0,
            ),
        )
        completed = [row for row in attempts if row.get("phase") == _COMPLETED]
        primary = completed[-1] if completed else attempts[-1]
        first = attempts[0]
        started = min(
            (
                _number(row.get("timestamp"))
                for row in attempts
                if _number(row.get("timestamp")) is not None
            ),
            default=None,
        )
        ended = max(
            (
                (_number(row.get("timestamp")) or 0.0)
                + max(0.0, _number(row.get("elapsed_sec"), 0.0) or 0.0)
                for row in attempts
                if _number(row.get("timestamp")) is not None
            ),
            default=None,
        )
        tokens = _tokens(primary)
        if not tokens["known"]:
            for row in reversed(completed or attempts):
                candidate = _tokens(row)
                if candidate["known"]:
                    tokens = candidate
                    break
        tool_names: list[str] = []
        for row in attempts:
            for name in row.get("tool_names") or ():
                text = str(name or "")
                if text and text not in tool_names:
                    tool_names.append(text)
        finished = any(row.get("phase") == _COMPLETED for row in attempts)
        attempt_numbers = sorted({int(row.get("attempt") or 1) for row in attempts})
        calls.append(
            {
                "call_id": key,
                "session_id": primary.get("session_id")
                or first.get("session_id")
                or "",
                "worker_id": primary.get("worker_id") or first.get("worker_id") or "",
                "run_id": primary.get("run_id") or first.get("run_id") or "",
                "lineage_id": _first(
                    primary, "lineage_id", _first(first, "lineage_id", "")
                ),
                "stage_id": _first(primary, "stage_id", _first(first, "stage_id", "")),
                "node_uid": _first(primary, "node_uid", _first(first, "node_uid", "")),
                "turn_id": int(primary.get("turn_id") or first.get("turn_id") or 0),
                "model": primary.get("model") or first.get("model") or "",
                "call_kind": _first(
                    primary, "call_kind", _first(first, "call_kind", "agent")
                ),
                "turn_kind": _first(
                    primary, "turn_kind", _first(first, "turn_kind", "")
                ),
                "route": _first(primary, "route", _first(first, "route", "")),
                "llm_role": primary.get("llm_role") or first.get("llm_role") or "",
                "started_at": started,
                "ended_at": ended,
                "elapsed": _number(primary.get("elapsed_sec")),
                "ttft": _number(primary.get("ttft_sec")),
                "tpot": _number(primary.get("tpot_sec")),
                "tokens": tokens,
                "finish_reason": primary.get("finish_reason") or "",
                "status": "completed" if finished else "failed",
                "attempt_count": len(attempt_numbers),
                "attempts": [
                    {
                        "attempt": int(row.get("attempt") or 1),
                        "phase": row.get("phase"),
                        "started_at": _number(row.get("timestamp")),
                        "elapsed": _number(row.get("elapsed_sec")),
                        "finish_reason": row.get("finish_reason") or "",
                        "usage_status": row.get("usage_status") or "",
                        "tokens": _tokens(row),
                    }
                    for row in attempts
                ],
                "tool_names": tool_names,
                "provider_message_count": primary.get("provider_message_count"),
                "provider_context_hash": primary.get("provider_context_hash") or "",
                "stable_prefix_hash": primary.get("stable_prefix_hash") or "",
                "compaction_generation": int(primary.get("compaction_generation") or 0),
                "provider_phase": primary.get("phase"),
                "attempt_errors": [],
                "_rows": attempts,
            }
        )
    calls.sort(
        key=lambda call: (
            _number(call.get("started_at"), 0.0) or 0.0,
            str(call.get("session_id") or ""),
            int(call.get("turn_id") or 0),
        )
    )
    return calls
