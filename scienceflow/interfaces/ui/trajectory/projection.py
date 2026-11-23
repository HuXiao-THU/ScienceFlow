"""Project audit records into the worker/lineage/call models used by the UI."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

from .operation_index import RecordRef, tool_raw_candidates

_RAW_ID_RE = re.compile(r"raw_id=([^\]\s]+)")
_STAGE_COMMIT_WINDOW_FALLBACK_SEC = 1800.0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sort_key(value: Any) -> tuple[int, Any]:
    text = str(value or "")
    if len(text) > 1 and text[0].casefold() == "l" and text[1:].isdigit():
        return (0, int(text[1:]))
    if len(text) > 1 and text[0].casefold() == "s" and text[1:].isdigit():
        return (0, int(text[1:]))
    return (1, text.casefold())


def _call_start(call: dict[str, Any]) -> float:
    return _number(call.get("started_at")) or 0.0


def _tokens_sum(calls: list[dict[str, Any]]) -> dict[str, int]:
    total = {"input": 0, "output": 0, "cached": 0}
    for call in calls:
        tokens = call.get("tokens") or {}
        for key in total:
            value = tokens.get(key)
            if isinstance(value, (int, float)) and value > 0:
                total[key] += int(value)
    return total


def _time_range(calls: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    starts = [_number(call.get("started_at")) for call in calls]
    ends = [_number(call.get("ended_at")) for call in calls]
    start = min((value for value in starts if value is not None), default=None)
    end = max((value for value in ends if value is not None), default=None)
    return start, end


def _session_kind(session_calls: list[dict[str, Any]]) -> str:
    if any(str(call.get("call_kind")) == "agent" for call in session_calls):
        return "main"
    return "ephemeral"


def _session_route(session_calls: list[dict[str, Any]]) -> str:
    for call in session_calls:
        turn_kind = str(call.get("turn_kind") or "")
        if turn_kind:
            return turn_kind
    return ""


def _stage_commit_window(
    stamp: float | None,
    windows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if stamp is None:
        return None
    for window in windows:
        start = _number(window.get("start"))
        if start is None:
            continue
        if stamp < start - 0.001:
            continue
        end = _number(window.get("end"))
        if end is None:
            end = start + _STAGE_COMMIT_WINDOW_FALLBACK_SEC
        if stamp <= end + 0.001:
            return window
    return None


def _anno_by_call(events: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in events.get("estra") or ():
        call_id = str(item.get("call_id") or "")
        if call_id:
            result.setdefault(call_id, {})[str(item.get("kind") or "estra")] = item
    return result


def _failures_by_call(highlights: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in highlights.get("failures") or ():
        call_id = str(item.get("call_id") or "")
        if call_id:
            result.setdefault(call_id, []).append(item)
    return result


def _decisions_by_call(highlights: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in highlights.get("decisions") or ():
        call_id = str(item.get("call_id") or "")
        if call_id:
            result.setdefault(call_id, []).append(item)
    return result


def _compaction_for_call(
    call: dict[str, Any],
    compactions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    stamp = _number(call.get("started_at"))
    if stamp is None:
        return None
    for window in compactions:
        start = _number(window.get("start"))
        if start is None:
            continue
        end = _number(window.get("end")) or start + 3600
        if start - 5 <= stamp <= end + 30:
            return window
    return None


def _phase_label(value: str) -> str:
    return value.upper().replace("_", " ")


def annotate_call(
    call: dict[str, Any],
    events: dict[str, Any],
    highlights: dict[str, Any],
    *,
    estra_by_call: dict[str, dict[str, Any]] | None = None,
    failures_by_call: dict[str, list[dict[str, Any]]] | None = None,
    decisions_by_call: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    estra_by_call = (
        estra_by_call if estra_by_call is not None else _anno_by_call(events)
    )
    failures_by_call = (
        failures_by_call
        if failures_by_call is not None
        else _failures_by_call(highlights)
    )
    decisions_by_call = (
        decisions_by_call
        if decisions_by_call is not None
        else _decisions_by_call(highlights)
    )
    call_id = str(call.get("call_id") or "")
    kind = str(call.get("call_kind") or "agent")
    turn_kind = str(call.get("turn_kind") or "")
    annotations: list[dict[str, Any]] = []
    phase = "normal"
    label = "AGENT"

    if kind == "ephemeral":
        if turn_kind.startswith("stage_commit"):
            phase, label = "stage_commit", "STAGE COMMIT"
        elif turn_kind.startswith("compact"):
            phase, label = "compact", "COMPACT"
        elif turn_kind == "estra":
            phase, label = "estra", "ESTRA"
        elif turn_kind == "estra_archive_summary":
            phase, label = "archive", "ARCHIVE"
        elif turn_kind in {"text_only", "agentic_text_only", "route"}:
            phase, label = "text_route", "TEXT ROUTE"
        elif turn_kind.startswith("resource"):
            phase, label = "resource", "RESOURCE"
        elif turn_kind.startswith("metric"):
            phase, label = "metric", "METRIC"
        elif turn_kind in {"final", "finalize", "stage_end"}:
            phase, label = "final", "FINALIZE"
        elif turn_kind.startswith("report"):
            phase, label = "report", "REPORT"
        elif turn_kind == "global_merge":
            phase, label = "merge", "GLOBAL MERGE"
        else:
            phase = "ephemeral"
            label = _phase_label(turn_kind) if turn_kind else "EPHEMERAL"
        annotations.append({"kind": "ephemeral", "route": turn_kind})
        if phase == "compact":
            window = _compaction_for_call(call, events.get("compactions") or [])
            if window:
                annotations.append(
                    {
                        "kind": "compact",
                        "mode": window.get("mode"),
                        "reason": window.get("reason"),
                        "result": window.get("result"),
                        "omitted_before": window.get("omitted_before"),
                        "omitted_after": window.get("omitted_after"),
                        "summary_chars": window.get("summary_chars"),
                        "tokens_in": window.get("tokens_in"),
                        "tokens_out": window.get("tokens_out"),
                    }
                )
        if phase == "estra":
            item = (estra_by_call.get(call_id) or {}).get("decision")
            if item:
                annotations.append(
                    {
                        "kind": "estra",
                        "action": item.get("action"),
                        "intent": item.get("intent"),
                        "target_stage": item.get("target_stage"),
                        "latest_stage": item.get("latest_stage"),
                        "reason": item.get("reason"),
                        "summary": item.get("summary"),
                    }
                )
        if phase == "archive":
            item = (estra_by_call.get(call_id) or {}).get("archive")
            if item:
                annotations.append(
                    {
                        "kind": "archive",
                        "target_stage": item.get("target_stage"),
                        "terminal_stage": item.get("terminal_stage"),
                        "summary": item.get("summary"),
                        "summary_chars": item.get("summary_chars"),
                    }
                )
    else:
        windows = events.get("stage_commit_windows") or []
        stamps = [_number(call.get("started_at"))]
        for attempt in call.get("attempts") or ():
            stamps.append(_number(attempt.get("started_at")))
        window = None
        for stamp in stamps:
            window = _stage_commit_window(stamp, windows)
            if window is not None:
                break
        if window:
            phase, label = "stage_commit", "STAGE COMMIT"
            annotations.append(
                {
                    "kind": "stage_commit",
                    "stage": window.get("stage"),
                    "parse_retries": window.get("parse_retries"),
                    "fallback": window.get("fallback"),
                    "ok": window.get("ok"),
                    "judgment_source": window.get("judgment_source"),
                }
            )
        else:
            decisions = decisions_by_call.get(call_id) or []
            context_decision = next(
                (
                    item
                    for item in decisions
                    if str(item.get("route") or "") == "context"
                    or "context" in str(item.get("reason") or "")
                ),
                None,
            )
            if context_decision:
                phase, label = "context", "CONTEXT GUARD"
                annotations.append(
                    {
                        "kind": "context",
                        "route": context_decision.get("route"),
                        "reason": context_decision.get("reason"),
                        "action": context_decision.get("action"),
                    }
                )

    failures = failures_by_call.get(call_id) or []
    attempt_errors = []
    for item in failures:
        attempt_errors.append(
            {
                "attempt": item.get("attempt"),
                "error_type": item.get("error_type"),
                "error": item.get("error"),
            }
        )
        annotations.append(
            {
                "kind": "failure",
                "attempt": item.get("attempt"),
                "error_type": item.get("error_type"),
                "error": item.get("error"),
            }
        )
    if int(call.get("attempt_count") or 1) > 1:
        annotations.append({"kind": "retry", "attempts": call.get("attempt_count")})
    return {
        "phase": phase,
        "phase_label": label,
        "annotations": annotations,
        "attempt_errors": attempt_errors,
    }


def _captures_by(lineage: str, events: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for capture in events.get("stage_captures") or ():
        if str(capture.get("lineage") or "") != lineage:
            continue
        stage = str(capture.get("stage") or "")
        if stage:
            result[stage] = capture
    return result


def _best_metric(captures: list[dict[str, Any]]) -> dict[str, Any] | None:
    values = [
        item
        for item in captures
        if isinstance(item.get("metric_value"), (int, float))
        and not isinstance(item.get("metric_value"), bool)
        and math.isfinite(float(item["metric_value"]))
    ]
    if not values:
        return None
    direction = bool(values[-1].get("lower_is_better"))
    best = (
        min(values, key=lambda item: float(item["metric_value"]))
        if direction
        else max(values, key=lambda item: float(item["metric_value"]))
    )
    return {
        "name": best.get("metric_name") or "",
        "value": best.get("metric_value"),
        "stage": best.get("stage") or "",
        "lower_is_better": direction,
        "timestamp": best.get("timestamp"),
        "candidate_id": best.get("candidate_id") or "",
        "node_uid": best.get("node_uid") or "",
    }


def _stage_sequence(calls: list[dict[str, Any]]) -> list[str]:
    stages: list[str] = []
    for call in sorted(calls, key=_call_start):
        if str(call.get("call_kind") or "") != "agent":
            continue
        stage = str(call.get("stage_id") or "")
        if stage and stage not in {"draft"} and stage not in stages:
            stages.append(stage)
    if not stages:
        for call in sorted(calls, key=_call_start):
            stage = str(call.get("stage_id") or "")
            if stage and stage not in stages:
                stages.append(stage)
    return stages


def build_worker_index(
    worker_id: str,
    calls: list[dict[str, Any]],
    events: dict[str, Any],
    highlights: dict[str, Any],
) -> dict[str, Any]:
    estra_by_call = _anno_by_call(events)
    failures_by_call = _failures_by_call(highlights)
    decisions_by_call = _decisions_by_call(highlights)
    transitions = events.get("lineage_transitions") or []
    by_lineage: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        lineage = (
            str(call.get("lineage_id") or "") or f"session:{call.get('session_id')}"
        )
        by_lineage.setdefault(lineage, []).append(call)

    lineages: list[dict[str, Any]] = []
    for lineage, lineage_calls in by_lineage.items():
        lineage_calls.sort(key=_call_start)
        sessions: dict[str, list[dict[str, Any]]] = {}
        for call in lineage_calls:
            sessions.setdefault(str(call.get("session_id") or ""), []).append(call)
        captures = _captures_by(lineage, events)
        stages = _stage_sequence(lineage_calls)
        stage_metrics = []
        for stage in stages:
            capture = captures.get(stage)
            if not capture:
                continue
            stage_metrics.append(
                {
                    "stage": stage,
                    "metric_name": capture.get("metric_name") or "",
                    "metric_value": capture.get("metric_value"),
                    "lower_is_better": capture.get("lower_is_better"),
                    "candidate_id": capture.get("candidate_id") or "",
                    "node_uid": capture.get("node_uid") or "",
                    "timestamp": capture.get("timestamp"),
                }
            )
        phase_counts: dict[str, int] = {}
        ephemeral_count = 0
        for call in lineage_calls:
            annotated = annotate_call(
                call,
                events,
                highlights,
                estra_by_call=estra_by_call,
                failures_by_call=failures_by_call,
                decisions_by_call=decisions_by_call,
            )
            phase = str(annotated["phase"])
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
            if str(call.get("call_kind")) == "ephemeral":
                ephemeral_count += 1
        start, end = _time_range(lineage_calls)
        transition_in = next(
            (item for item in reversed(transitions) if str(item.get("to")) == lineage),
            None,
        )
        transition_out = next(
            (item for item in transitions if str(item.get("from")) == lineage),
            None,
        )
        session_rows = []
        for session_id, session_calls in sessions.items():
            session_calls.sort(key=_call_start)
            s_start, s_end = _time_range(session_calls)
            session_stages = []
            for call in session_calls:
                stage = str(call.get("stage_id") or "")
                if stage and stage not in session_stages:
                    session_stages.append(stage)
            session_rows.append(
                {
                    "id": session_id,
                    "kind": _session_kind(session_calls),
                    "route": _session_route(session_calls),
                    "call_count": len(session_calls),
                    "stages": session_stages,
                    "started_at": s_start,
                    "ended_at": s_end,
                    "tokens": _tokens_sum(session_calls),
                }
            )
        session_rows.sort(key=lambda item: _number(item.get("started_at")) or 0.0)
        main_sessions = [item for item in session_rows if item["kind"] == "main"]
        lineages.append(
            {
                "id": lineage,
                "stages": stages,
                "call_count": len(lineage_calls),
                "ephemeral_count": ephemeral_count,
                "main_session_count": len(main_sessions),
                "session_count": len(session_rows),
                "tokens": _tokens_sum(lineage_calls),
                "started_at": start,
                "ended_at": end,
                "best_metric": _best_metric(list(captures.values())),
                "stage_metrics": stage_metrics,
                "phase_counts": phase_counts,
                "transition_in": transition_in,
                "transition_out": transition_out,
                "sessions": session_rows,
            }
        )
    lineages.sort(key=lambda item: _sort_key(item.get("id")))
    start, end = _time_range(calls)
    return {
        "id": worker_id,
        "call_count": len(calls),
        "attempt_count": sum(int(call.get("attempt_count") or 1) for call in calls),
        "ephemeral_count": sum(
            1 for call in calls if call.get("call_kind") == "ephemeral"
        ),
        "lineage_count": len(lineages),
        "session_count": len({call.get("session_id") for call in calls}),
        "tokens": _tokens_sum(calls),
        "started_at": start,
        "ended_at": end,
        "lineages": lineages,
    }


def build_index(
    worker_indexes: list[dict[str, Any]], *, task_root: str, warnings: list[str]
) -> dict[str, Any]:
    return {
        "available": bool(worker_indexes),
        "task_root": task_root,
        "generated_at": time.time(),
        "totals": {
            "workers": len(worker_indexes),
            "calls": sum(
                int(worker.get("call_count") or 0) for worker in worker_indexes
            ),
            "attempts": sum(
                int(worker.get("attempt_count") or 0) for worker in worker_indexes
            ),
            "ephemeral": sum(
                int(worker.get("ephemeral_count") or 0) for worker in worker_indexes
            ),
            "lineages": sum(
                int(worker.get("lineage_count") or 0) for worker in worker_indexes
            ),
        },
        "workers": worker_indexes,
        "warnings": warnings,
    }


def _common_prefix(left: list[str], right: list[str]) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _context_change(
    call: dict[str, Any],
    previous: dict[str, Any] | None,
    op_call: dict[str, Any] | None,
) -> dict[str, Any]:
    hashes = list((op_call or {}).get("context_hashes") or [])
    message_count = (op_call or {}).get("message_count")
    system_count = (op_call or {}).get("system_count")
    if message_count is None:
        message_count = call.get("provider_message_count")
    if previous is None or not hashes:
        return {
            "messages": message_count,
            "system": system_count,
            "new": len(hashes) if hashes else None,
            "removed": 0 if hashes else None,
            "reset": False,
            "previous_call_id": None,
            "new_message_indices": list(range(len(hashes))),
        }
    previous_op = previous.get("_op_call")
    previous_hashes = list((previous_op or {}).get("context_hashes") or [])
    reset = previous.get("session_id") != call.get("session_id") or int(
        previous.get("compaction_generation") or 0
    ) != int(call.get("compaction_generation") or 0)
    if not previous_hashes:
        return {
            "messages": message_count,
            "system": system_count,
            "new": len(hashes),
            "removed": None,
            "reset": reset,
            "previous_call_id": previous.get("call_id"),
            "new_message_indices": list(range(len(hashes))),
        }
    common = 0 if reset else _common_prefix(previous_hashes, hashes)
    new_indices = list(range(common, len(hashes)))
    return {
        "messages": message_count,
        "system": system_count,
        "new": len(new_indices),
        "removed": max(0, len(previous_hashes) - common),
        "reset": reset,
        "previous_call_id": previous.get("call_id"),
        "new_message_indices": new_indices,
    }


def build_calls(
    lineage: str,
    calls: list[dict[str, Any]],
    op_calls: dict[str, dict[str, Any]],
    events: dict[str, Any],
    highlights: dict[str, Any],
) -> dict[str, Any]:
    estra_by_call = _anno_by_call(events)
    failures_by_call = _failures_by_call(highlights)
    decisions_by_call = _decisions_by_call(highlights)
    rows: list[dict[str, Any]] = []
    for call in sorted(calls, key=_call_start):
        op_call = op_calls.get(str(call.get("call_id") or ""))
        annotated = annotate_call(
            call,
            events,
            highlights,
            estra_by_call=estra_by_call,
            failures_by_call=failures_by_call,
            decisions_by_call=decisions_by_call,
        )
        ready = dict(call)
        ready.pop("_rows", None)
        ready["_op_call"] = op_call
        ready["stage"] = str(call.get("stage_id") or "")
        ready["lineage"] = str(call.get("lineage_id") or lineage)
        ready["session"] = str(call.get("session_id") or "")
        ready["session_id"] = str(call.get("session_id") or "")
        ready["turn"] = int(call.get("turn_id") or 0)
        ready["session_kind"] = (
            "ephemeral" if str(call.get("call_kind")) == "ephemeral" else "main"
        )
        ready["phase"] = annotated["phase"]
        ready["phase_label"] = annotated["phase_label"]
        ready["annotations"] = annotated["annotations"]
        ready["attempt_errors"] = annotated["attempt_errors"]
        tools = []
        for tool in (op_call or {}).get("tools") or ():
            tools.append(
                {
                    "id": tool.get("id") or "",
                    "name": tool.get("name") or "",
                    "preview": tool.get("preview") or "",
                    "status": tool.get("status") or "requested",
                    "is_error": bool(tool.get("is_error")),
                    "blocked": bool(tool.get("blocked")),
                    "result_preview": _short(tool.get("rendered"), 180),
                }
            )
        ready["tools"] = tools
        phase = str(annotated["phase"])
        stage = str(call.get("stage_id") or "")
        if phase == "stage_commit":
            summary = f"stage bookkeeping · {stage or '?'}"
            if int(call.get("attempt_count") or 1) > 1:
                summary += f" · {call.get('attempt_count')} attempts"
        elif phase == "compact":
            compact = next(
                (
                    item
                    for item in annotated["annotations"]
                    if item.get("kind") == "compact"
                ),
                None,
            )
            summary = (
                f"compact · {compact.get('result')}"
                if compact and compact.get("result")
                else f"compact · {stage or '?'}"
            )
        elif phase == "estra":
            estra = next(
                (
                    item
                    for item in annotated["annotations"]
                    if item.get("kind") == "estra"
                ),
                None,
            )
            if estra:
                summary = "ESTRA · " + " · ".join(
                    part
                    for part in (
                        str(estra.get("action") or ""),
                        str(estra.get("intent") or ""),
                        (
                            f"target {estra.get('target_stage')}"
                            if estra.get("target_stage")
                            else ""
                        ),
                    )
                    if part
                )
            else:
                summary = (op_call or {}).get("summary") or "ESTRA decision"
        elif phase == "archive":
            archive = next(
                (
                    item
                    for item in annotated["annotations"]
                    if item.get("kind") == "archive"
                ),
                None,
            )
            summary = (
                f"archive summary · {archive.get('target_stage') or stage or '?'}"
                if archive
                else "archive summary"
            )
        else:
            summary = (
                (op_call or {}).get("summary")
                if (op_call or {}).get("has_response")
                else (call.get("route") or call.get("turn_kind") or "call")
            )
        ready["summary"] = summary
        ready["has_detail"] = bool(op_call and op_call.get("has_context"))
        status = str(call.get("status") or "completed")
        if status == "completed" and int(call.get("attempt_count") or 1) > 1:
            status = "retried"
        ready["status"] = status
        rows.append(ready)
    previous: dict[str, Any] | None = None
    for index, call in enumerate(rows, start=1):
        call["index"] = index
        call["context"] = _context_change(call, previous, call.get("_op_call"))
        previous = call
    for call in rows:
        call.pop("_op_call", None)
    total = len(rows)
    return {
        "lineage": lineage,
        "total": total,
        "calls": rows,
        "stats": {
            "calls": total,
            "ephemeral": sum(
                1 for call in rows if call.get("call_kind") == "ephemeral"
            ),
            "retried": sum(1 for call in rows if call.get("status") == "retried"),
            "failed": sum(1 for call in rows if call.get("status") == "failed"),
            "tokens": _tokens_sum(rows),
        },
    }


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _raw_output_info(
    tool: dict[str, Any],
    log_dir: Path,
    stage_id: str,
) -> dict[str, Any] | None:
    message = tool.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    raw_id = ""
    if isinstance(content, str):
        match = _RAW_ID_RE.search(content)
        if match:
            raw_id = match.group(1)
    artifact = str(tool.get("raw_output_artifact") or "")
    if not raw_id and artifact:
        raw_id = Path(artifact).name
    if not raw_id:
        return None
    for candidate in tool_raw_candidates(log_dir, stage_id, raw_id, artifact):
        try:
            if candidate.is_file():
                return {
                    "id": raw_id,
                    "path": str(candidate),
                    "chars": candidate.stat().st_size,
                }
        except OSError:
            continue
    return {"id": raw_id, "path": "", "chars": None}


def build_call_detail(
    call: dict[str, Any],
    op_call: dict[str, Any] | None,
    provider_call: dict[str, Any] | None,
    events: dict[str, Any],
    highlights: dict[str, Any],
    *,
    log_dir: Path,
    stage_id: str,
) -> dict[str, Any]:
    estra_by_call = _anno_by_call(events)
    failures_by_call = _failures_by_call(highlights)
    decisions_by_call = _decisions_by_call(highlights)
    annotated = annotate_call(
        call,
        events,
        highlights,
        estra_by_call=estra_by_call,
        failures_by_call=failures_by_call,
        decisions_by_call=decisions_by_call,
    )
    failures = {
        int(item.get("attempt") or 1): item
        for item in failures_by_call.get(str(call.get("call_id")), [])
    }
    attempts: list[dict[str, Any]] = []
    if op_call:
        attempt_numbers = sorted(
            set(op_call.get("intents") or {}) | set(op_call.get("settled") or {})
        )
    else:
        attempt_numbers = []
    if not attempt_numbers:
        for item in (provider_call or {}).get("attempts") or ():
            attempt_numbers.append(int(item.get("attempt") or 1))
    attempt_meta = {
        int(item.get("attempt") or 1): item
        for item in (provider_call or {}).get("attempts") or ()
    }
    for attempt in attempt_numbers:
        meta = attempt_meta.get(attempt, {})
        intent_ref = (op_call or {}).get("intents", {}).get(attempt)
        settled_ref = (op_call or {}).get("settled", {}).get(attempt)
        intent = intent_ref.read() if isinstance(intent_ref, RecordRef) else None
        settled = settled_ref.read() if isinstance(settled_ref, RecordRef) else None
        intent_payload = (
            (intent or {}).get("payload") if isinstance(intent, dict) else {}
        )
        intent_payload = intent_payload if isinstance(intent_payload, dict) else {}
        settled_payload = (
            (settled or {}).get("payload") if isinstance(settled, dict) else {}
        )
        settled_payload = settled_payload if isinstance(settled_payload, dict) else {}
        error = failures.get(attempt)
        attempts.append(
            {
                "attempt": attempt,
                "status": "failed" if error and not settled else "completed",
                "started_at": meta.get("started_at"),
                "elapsed": meta.get("elapsed"),
                "tokens": meta.get("tokens"),
                "request": {
                    "model": intent_payload.get("model") or call.get("model") or "",
                    "call_kind": intent_payload.get("call_kind")
                    or call.get("call_kind"),
                    "turn_kind": intent_payload.get("turn_kind")
                    or call.get("turn_kind"),
                    "route": intent_payload.get("route") or call.get("route"),
                    "system_messages": intent_payload.get("system_messages") or [],
                    "provider_messages": intent_payload.get("provider_messages") or [],
                }
                if intent
                else None,
                "response": {
                    "finish_reason": settled_payload.get("finish_reason"),
                    "message": settled_payload.get("message"),
                    "usage": settled_payload.get("usage"),
                }
                if settled
                else None,
                "error": {
                    "type": (error or {}).get("error_type"),
                    "error_type": (error or {}).get("error_type"),
                    "message": (error or {}).get("error"),
                }
                if error
                else None,
            }
        )
    tools: list[dict[str, Any]] = []
    for tool in (op_call or {}).get("tools") or ():
        settled_ref = tool.get("settled")
        settled = settled_ref.read() if isinstance(settled_ref, RecordRef) else None
        payload = (settled or {}).get("payload") if isinstance(settled, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        raw_info = _raw_output_info(tool, Path(log_dir), stage_id)
        tools.append(
            {
                "id": tool.get("id") or "",
                "name": tool.get("name") or "",
                "arguments": tool.get("arguments"),
                "arguments_raw": tool.get("arguments_raw") or "",
                "preview": tool.get("preview") or "",
                "status": tool.get("status") or "requested",
                "is_error": bool(tool.get("is_error")),
                "blocked": bool(tool.get("blocked")),
                "rendered": payload.get("rendered")
                if settled
                else tool.get("rendered"),
                "message": payload.get("message") if settled else tool.get("message"),
                "shell_execution": payload.get("shell_execution")
                if settled
                else tool.get("shell_execution"),
                "raw_output": raw_info,
            }
        )
    detail_call = dict(call)
    detail_call["phase"] = annotated["phase"]
    detail_call["phase_label"] = annotated["phase_label"]
    detail_call["annotations"] = annotated["annotations"]
    detail_call["attempt_errors"] = annotated["attempt_errors"]
    detail_call["context"] = (
        _context_change(
            call,
            None,
            op_call,
        )
        if not call.get("context")
        else call.get("context")
    )
    return {
        "call": detail_call,
        "attempts": attempts,
        "tools": tools,
        "annotations": annotated["annotations"],
        "context_change": call.get("context") or {},
        "stage": stage_id,
        "lineage": str(call.get("lineage_id") or ""),
    }
