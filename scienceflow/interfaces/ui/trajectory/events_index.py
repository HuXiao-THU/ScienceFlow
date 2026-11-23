"""Extract stage, lineage, compaction and retry facts from runtime logs."""

from __future__ import annotations

import json
import math
import mmap
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_STAGE_COMMIT_EVENTS = frozenset(
    {
        "stage_commit_text_requested",
        "stage_commit_text_parse_retry",
        "stage_commit_text_fallback_applied",
        "stage_commit_appended_text_block",
        "stage_commit_appended_deterministic",
        "stage_commit_ok",
        "stage_commit_persist_to_memory",
        "stage_commit_persisted_to_memory",
        "stage_commit_fork_judgment_ok",
        "stage_commit_fork_judgment_error",
        "stage_commit_append_error",
        "stage_commit_append_rejected",
        "stage_commit_deterministic_append_error",
        "stage_commit_deterministic_append_rejected",
    }
)
_COMPACT_EVENTS = frozenset({"context_compact_started", "context_compact_finished"})
_LINEAGE_EVENTS = frozenset({"estra_keep_current_compacted", "estra_stage_switched"})
_ESTRA_EVENTS = frozenset({"estra_decision", "estra_invalid"})
_METRIC_EVENTS = frozenset({"evaluator_metric_event", "metric_validity_adjudicated"})
_INTERESTING = (
    _STAGE_COMMIT_EVENTS
    | _COMPACT_EVENTS
    | _LINEAGE_EVENTS
    | _ESTRA_EVENTS
    | _METRIC_EVENTS
    | frozenset({"stage_captured", "estra_archive_summary_synthesized"})
)

_RUNTIME_NEEDLES = (
    b'"type": "session.started"',
    b'"type":"session.started"',
    b'"type": "agent.turn.decision"',
    b'"type":"agent.turn.decision"',
    b'"type": "llm.attempt.failed"',
    b'"type":"llm.attempt.failed"',
    b'"type": "llm.retrying"',
    b'"type":"llm.retrying"',
)
_RUNTIME_MARKER = re.compile(
    rb'"type":\s*"(session\.started|agent\.turn\.decision|llm\.attempt\.failed|llm\.retrying)"'
)


def _timestamp(row: dict[str, Any]) -> float | None:
    for value in (row.get("timestamp"), row.get("timestamp_utc")):
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("payload")
    return value if isinstance(value, dict) else {}


def read_lhr_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        with source.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    continue
                if b'"event"' not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict) and row.get("event") in _INTERESTING:
                    events.append(row)
    except OSError:
        return []
    events.sort(key=lambda row: _timestamp(row) or 0.0)
    return events


def _stage_captures(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    captures: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "stage_captured":
            continue
        payload = _payload(event)
        metric = payload.get("metric_event")
        metric = metric if isinstance(metric, dict) else {}
        stage = str(payload.get("stage_id") or metric.get("stage_id") or "")
        if not stage:
            continue
        captures.append(
            {
                "stage": stage,
                "lineage": str(
                    payload.get("lineage_id") or metric.get("lineage_id") or ""
                ),
                "node_uid": str(
                    payload.get("node_uid") or metric.get("node_uid") or ""
                ),
                "timestamp": _timestamp(event),
                "metric_name": str(metric.get("metric_name") or ""),
                "metric_value": metric.get("metric_value"),
                "lower_is_better": metric.get("lower_is_better"),
                "candidate_id": str(metric.get("candidate_id") or ""),
                "candidate_ready": metric.get("candidate_ready"),
                "snapshot_id": str(payload.get("snapshot_id") or ""),
                "snapshot_path": str(payload.get("snapshot_path") or ""),
            }
        )
    return captures


def _stage_commit_windows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [event for event in events if event.get("event") in _STAGE_COMMIT_EVENTS]
    windows: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    for event in ordered:
        payload = _payload(event)
        name = str(event.get("event") or "")
        stage = str(payload.get("stage_id") or "")
        stamp = _timestamp(event)
        if name == "stage_commit_text_requested":
            if stage and stage in active:
                active[stage]["end"] = stamp
                windows.append(active.pop(stage))
            if stage:
                active[stage] = {
                    "stage": stage,
                    "start": stamp,
                    "end": None,
                    "parse_retries": 0,
                    "fallback": False,
                    "ok": False,
                }
            continue
        window = active.get(stage)
        if window is None:
            continue
        if name == "stage_commit_text_parse_retry":
            window["parse_retries"] += 1
            window["retry_reason"] = str(payload.get("reason") or "")
        elif name == "stage_commit_text_fallback_applied":
            window["fallback"] = True
        elif name == "stage_commit_appended_text_block":
            window["judgment_source"] = str(payload.get("judgment_source") or "")
            if "fallback" in window["judgment_source"]:
                window["fallback"] = True
        elif name == "stage_commit_ok":
            window["ok"] = True
            window["end"] = stamp
            active.pop(stage, None)
            windows.append(window)
        elif name in {
            "stage_commit_append_error",
            "stage_commit_append_rejected",
            "stage_commit_deterministic_append_error",
            "stage_commit_deterministic_append_rejected",
        }:
            window["error"] = str(payload.get("reason") or payload.get("error") or name)
    for stage, window in active.items():
        window["end"] = None
        windows.append(window)
    windows.sort(key=lambda item: item.get("start") or 0.0)
    return windows


def _compactions(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    active: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        if name not in _COMPACT_EVENTS:
            continue
        payload = _payload(event)
        key = (
            payload.get("round"),
            str(payload.get("mode") or ""),
            str(payload.get("stage_id") or ""),
        )
        if name == "context_compact_started":
            active[key] = {
                "round": payload.get("round"),
                "mode": str(payload.get("mode") or ""),
                "stage": str(payload.get("stage_id") or ""),
                "reason": str(payload.get("reason") or ""),
                "start": _timestamp(event),
                "end": None,
                "omitted_before": payload.get("omitted_before"),
                "omitted_after": payload.get("omitted_after"),
                "recent_keep": payload.get("recent_keep"),
                "result": "",
                "summary_chars": None,
                "tokens_in": None,
                "tokens_out": None,
                "tokens_cached": None,
                "status": "",
            }
            continue
        window = active.pop(key, None)
        if window is None:
            continue
        window["end"] = _timestamp(event)
        window["omitted_after"] = payload.get("omitted_after", window["omitted_after"])
        window["result"] = str(payload.get("result") or "")
        window["summary_chars"] = payload.get("summary_chars")
        window["tokens_in"] = payload.get("tokens_in")
        window["tokens_out"] = payload.get("tokens_out")
        window["tokens_cached"] = payload.get("tokens_cached")
        window["status"] = str(payload.get("status") or "")
        result.append(window)
    result.extend(active.values())
    result.sort(key=lambda item: item.get("start") or 0.0)
    return result


def _estra(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        payload = _payload(event)
        if name == "estra_decision":
            correlation = payload.get("llm_correlation")
            correlation = correlation if isinstance(correlation, dict) else {}
            result.append(
                {
                    "kind": "decision",
                    "timestamp": _timestamp(event),
                    "call_id": str(correlation.get("call_id") or ""),
                    "session_id": str(correlation.get("session_id") or ""),
                    "action": str(payload.get("action") or ""),
                    "intent": str(payload.get("intent") or ""),
                    "decision_kind": str(payload.get("decision_kind") or ""),
                    "target_stage": str(payload.get("target_stage") or ""),
                    "latest_stage": str(payload.get("latest_stage") or ""),
                    "compact": payload.get("compact"),
                    "reason": str(payload.get("decision_reason") or ""),
                    "bottleneck": str(payload.get("bottleneck") or ""),
                    "evidence": str(payload.get("evidence") or ""),
                    "summary": str(payload.get("exploration_summary") or ""),
                }
            )
        elif name == "estra_archive_summary_synthesized":
            correlation = payload.get("llm_correlation")
            correlation = correlation if isinstance(correlation, dict) else {}
            result.append(
                {
                    "kind": "archive",
                    "timestamp": _timestamp(event),
                    "call_id": str(correlation.get("call_id") or ""),
                    "session_id": str(correlation.get("session_id") or ""),
                    "target_stage": str(payload.get("target_stage") or ""),
                    "terminal_stage": str(payload.get("terminal_stage") or ""),
                    "summary": str(payload.get("summary_excerpt") or ""),
                    "summary_chars": payload.get("summary_chars"),
                }
            )
    result.sort(key=lambda item: item.get("timestamp") or 0.0)
    return result


def _lineage_transitions(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("event") or "")
        if name not in _LINEAGE_EVENTS:
            continue
        payload = _payload(event)
        new_lineage = str(payload.get("new_lineage_id") or "")
        if not new_lineage:
            continue
        result.append(
            {
                "kind": "restore" if name == "estra_stage_switched" else "redirect",
                "event": name,
                "timestamp": _timestamp(event),
                "from": str(payload.get("previous_lineage_id") or ""),
                "to": new_lineage,
                "target_node_uid": str(payload.get("target_node_uid") or ""),
                "target_stage": str(payload.get("target_stage") or ""),
                "action": str(payload.get("action") or ""),
                "compact_strength": str(payload.get("compact_strength") or ""),
                "snapshot_id": str(payload.get("snapshot_id") or ""),
                "tail_summary_chars": payload.get("tail_summary_chars"),
                "state_packet_chars": payload.get("state_packet_chars"),
            }
        )
    result.sort(key=lambda item: item.get("timestamp") or 0.0)
    return result


def _metric_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") not in _METRIC_EVENTS:
            continue
        payload = _payload(event)
        result.append(
            {
                "event": str(event.get("event") or ""),
                "timestamp": _timestamp(event),
                "stage": str(payload.get("stage_id") or ""),
                "metric_name": str(payload.get("metric_name") or ""),
                "metric_value": payload.get("metric_value"),
                "validity": str(
                    payload.get("metric_validity") or payload.get("validity") or ""
                ),
                "source": str(payload.get("metric_validity_source") or ""),
            }
        )
    result.sort(key=lambda item: item.get("timestamp") or 0.0)
    return result


def extract_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stage_captures": _stage_captures(events),
        "stage_commit_windows": _stage_commit_windows(events),
        "compactions": _compactions(events),
        "estra": _estra(events),
        "lineage_transitions": _lineage_transitions(events),
        "metric_events": _metric_events(events),
    }


def read_runtime_highlights(
    path: str | Path,
    call_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Extract a few decision/retry events without reading the full ledger.

    The runtime event file is hundreds of megabytes, almost entirely streaming
    ``message.delta`` rows.  Memory-mapping it and searching for the handful of
    event-type markers keeps first-open latency bounded.  When a specific call
    is being inspected, pass its ID to skip unrelated rows.
    """
    source = Path(path)
    sessions: dict[str, dict[str, Any]] = {}
    decisions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    if not source.is_file():
        return {
            "sessions": sessions,
            "decisions": decisions,
            "failures": failures,
            "retries": retries,
        }
    lines: list[bytes] = []
    try:
        with source.open("rb") as stream:
            try:
                mapped = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError):
                mapped = None
            if mapped is None:
                for raw in stream:
                    if any(needle in raw for needle in _RUNTIME_NEEDLES):
                        lines.append(raw)
            else:
                try:
                    seen_starts: set[int] = set()
                    for match in _RUNTIME_MARKER.finditer(mapped):
                        index = match.start()
                        line_start = mapped.rfind(b"\n", 0, index) + 1
                        line_end = mapped.find(b"\n", index)
                        if line_end < 0:
                            line_end = len(mapped)
                        if line_start not in seen_starts:
                            seen_starts.add(line_start)
                            lines.append(mapped[line_start:line_end])
                finally:
                    mapped.close()
    except OSError:
        pass
    for raw in lines:
        try:
            row = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("type") or "")
        payload = _payload(row)
        session_id = str(row.get("session_id") or "")
        correlation = payload.get("correlation")
        correlation = correlation if isinstance(correlation, dict) else {}
        call_id = str(correlation.get("call_id") or "")
        if call_ids is not None and call_id not in call_ids:
            continue
        base = {
            "session_id": session_id,
            "turn_id": row.get("turn_id"),
            "timestamp": _timestamp(row),
            "call_id": call_id,
            "stage": str(correlation.get("stage_id") or ""),
            "lineage": str(correlation.get("lineage_id") or ""),
        }
        if name == "session.started":
            sessions[session_id] = {
                **base,
                "model": str(payload.get("model") or ""),
            }
        elif name == "agent.turn.decision":
            decisions.append(
                {
                    **base,
                    "route": str(payload.get("route") or ""),
                    "reason": str(payload.get("reason") or ""),
                    "action": str(payload.get("action") or ""),
                }
            )
        elif name == "llm.attempt.failed":
            failures.append(
                {
                    **base,
                    "attempt": payload.get("attempt") or 1,
                    "error_type": str(payload.get("error_type") or ""),
                    "error": str(payload.get("error") or ""),
                    "finish_reason": str(payload.get("finish_reason") or ""),
                }
            )
        elif name == "llm.retrying":
            retries.append(
                {
                    **base,
                    "attempt": payload.get("attempt"),
                    "next_attempt": payload.get("next_attempt"),
                    "delay_sec": payload.get("delay_sec"),
                    "error_type": str(payload.get("error_type") or ""),
                    "error": str(payload.get("error") or ""),
                }
            )
    decisions.sort(key=lambda item: item.get("timestamp") or 0.0)
    failures.sort(key=lambda item: item.get("timestamp") or 0.0)
    retries.sort(key=lambda item: item.get("timestamp") or 0.0)
    return {
        "sessions": sessions,
        "decisions": decisions,
        "failures": failures,
        "retries": retries,
    }
