"""Lazy index of the full LLM operation logs.

Each session writes one append-only JSONL file.  Scanning it line by line is
cheap compared with rendering it, so this module records byte ranges for the
few records the trajectory inspector can open and keeps only small summaries
in memory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RECORD_KIND_NEEDLES = (
    (b'"kind": "llm"', "llm"),
    (b'"kind":"llm"', "llm"),
    (b'"kind": "tool"', "tool"),
    (b'"kind":"tool"', "tool"),
)


@dataclass(frozen=True)
class RecordRef:
    path: Path
    offset: int
    length: int
    session_id: str

    def read(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                raw = stream.read(self.length)
            if not raw.endswith(b"\n"):
                return None
            value = json.loads(raw)
        except (OSError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None


def _record_kind(line: bytes) -> str | None:
    head = line[:800]
    for needle, kind in _RECORD_KIND_NEEDLES:
        if needle in head:
            return kind
    return None


def _short(value: Any, limit: int = 160) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _message_hash(message: Any) -> str:
    try:
        payload = json.dumps(
            message, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
    except (TypeError, ValueError):
        payload = str(message)
    return hashlib.blake2b(
        payload.encode("utf-8", "replace"), digest_size=8
    ).hexdigest()


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _argument_preview(value: Any) -> str:
    parsed = _arguments(value)
    if isinstance(parsed, dict):
        for key in (
            "command",
            "cmd",
            "path",
            "file_path",
            "query",
            "pattern",
            "url",
            "thought",
        ):
            text = parsed.get(key)
            if isinstance(text, str) and text.strip():
                return _short(text)
        return _short(json.dumps(parsed, ensure_ascii=False))
    return _short(parsed)


def _tool_call_summary(name: str, arguments: Any) -> str:
    preview = _argument_preview(arguments)
    return f"{name or 'tool'} · {preview}" if preview else (name or "tool")


def _content_summary(content: Any, reasoning: Any = None) -> str:
    text = _message_text({"content": content}).strip()
    if not text and isinstance(content, str):
        text = content.strip()
    if text:
        return _short(text)
    reasoning_text = str(reasoning or "").strip()
    if reasoning_text:
        return _short("reasoning: " + reasoning_text)
    return "(empty response)"


def _new_call(call_id: str, session_id: str, turn_id: int) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "model": "",
        "call_kind": "agent",
        "turn_kind": "",
        "route": "",
        "intents": {},
        "settled": {},
        "intent_meta": {},
        "settled_meta": {},
        "tools": [],
        "tool_by_id": {},
        "context_hashes": [],
        "system_hashes": [],
        "message_count": None,
        "system_count": None,
        "last_user_preview": "",
        "summary": "(no response)",
        "response_preview": "",
        "has_response": False,
        "has_context": False,
        "warnings": [],
    }


def _ensure_call(
    calls: dict[str, dict[str, Any]],
    session_id: str,
    correlation: dict[str, Any],
) -> dict[str, Any]:
    turn_id = int(correlation.get("turn_id") or 0)
    call_id = str(correlation.get("call_id") or f"{session_id}:llm:{turn_id}")
    call = calls.get(call_id)
    if call is None:
        call = _new_call(call_id, session_id, turn_id)
        calls[call_id] = call
    return call


def _apply_intent(
    call: dict[str, Any], payload: dict[str, Any], attempt: int, ref: RecordRef
) -> None:
    call["intents"][attempt] = ref
    model = str(payload.get("model") or "")
    if model:
        call["model"] = model
    call["call_kind"] = str(payload.get("call_kind") or call["call_kind"])
    call["turn_kind"] = str(payload.get("turn_kind") or call["turn_kind"])
    call["route"] = str(payload.get("route") or call["route"] or "")
    system_messages = payload.get("system_messages") or []
    provider_messages = payload.get("provider_messages") or []
    system_messages = system_messages if isinstance(system_messages, list) else []
    provider_messages = provider_messages if isinstance(provider_messages, list) else []
    call["system_count"] = len(system_messages)
    call["message_count"] = len(provider_messages)
    call["system_hashes"] = [_message_hash(message) for message in system_messages]
    call["context_hashes"] = [_message_hash(message) for message in provider_messages]
    call["has_context"] = True
    last_user = ""
    for message in provider_messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_text(message).strip()
            if text:
                last_user = text
    call["last_user_preview"] = _short(last_user, 220)
    call["intent_meta"][attempt] = {
        "model": model,
        "call_kind": call["call_kind"],
        "turn_kind": call["turn_kind"],
        "route": call["route"],
        "system_count": len(system_messages),
        "message_count": len(provider_messages),
        "last_user_preview": call["last_user_preview"],
    }


def _apply_settled(
    call: dict[str, Any], payload: dict[str, Any], attempt: int, ref: RecordRef
) -> None:
    call["settled"][attempt] = ref
    message = payload.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    tool_calls = message.get("tool_calls")
    tool_calls = tool_calls if isinstance(tool_calls, list) else []
    specs = []
    for tool in tool_calls:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        function = function if isinstance(function, dict) else {}
        name = str(function.get("name") or "")
        arguments = function.get("arguments")
        specs.append(
            {
                "id": str(tool.get("id") or ""),
                "name": name,
                "arguments": _arguments(arguments),
                "arguments_raw": arguments if isinstance(arguments, str) else "",
                "preview": _argument_preview(arguments),
            }
        )
    if specs:
        summary = _tool_call_summary(specs[0]["name"], specs[0]["arguments"])
        if len(specs) > 1:
            summary += f" · +{len(specs) - 1}"
    else:
        summary = _content_summary(content, reasoning)
    call["summary"] = summary
    call["response_preview"] = summary
    call["has_response"] = True
    call["settled_meta"][attempt] = {
        "finish_reason": str(payload.get("finish_reason") or ""),
        "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
        "content": content,
        "reasoning_content": reasoning,
        "message": message,
        "tool_specs": specs,
        "summary": summary,
    }
    for spec in specs:
        existing = call["tool_by_id"].get(spec["id"])
        if existing is None:
            existing = {
                "id": spec["id"],
                "name": spec["name"],
                "arguments": spec["arguments"],
                "arguments_raw": spec["arguments_raw"],
                "preview": spec["preview"],
                "status": "requested",
                "is_error": False,
                "blocked": False,
                "rendered": "",
                "message": None,
                "shell_execution": None,
                "raw_output_artifact": "",
                "intent": None,
                "settled": None,
            }
            call["tool_by_id"][spec["id"]] = existing
            call["tools"].append(existing)
        else:
            existing.update(
                {
                    "name": spec["name"],
                    "arguments": spec["arguments"],
                    "arguments_raw": spec["arguments_raw"],
                    "preview": spec["preview"],
                }
            )


def _tool_key(step_id: str, tool_call_id: str = "") -> str:
    if tool_call_id:
        return tool_call_id
    if step_id.startswith("tool:"):
        return step_id[5:]
    return step_id


def _apply_tool_intent(
    calls: dict[str, dict[str, Any]],
    session_id: str,
    payload: dict[str, Any],
    ref: RecordRef,
) -> None:
    correlation = payload.get("correlation")
    correlation = correlation if isinstance(correlation, dict) else {}
    call = _ensure_call(calls, session_id, correlation)
    step_id = str(payload.get("step_id") or "")
    tool_call_id = _tool_key(step_id)
    tool = call["tool_by_id"].get(tool_call_id)
    if tool is None:
        tool = {
            "id": tool_call_id,
            "name": str(payload.get("name") or ""),
            "arguments": None,
            "arguments_raw": "",
            "preview": "",
            "status": "requested",
            "is_error": False,
            "blocked": False,
            "rendered": "",
            "message": None,
            "shell_execution": None,
            "raw_output_artifact": "",
            "intent": ref,
            "settled": None,
        }
        call["tool_by_id"][tool_call_id] = tool
        call["tools"].append(tool)
    else:
        tool["intent"] = ref


def _apply_tool_settled(
    calls: dict[str, dict[str, Any]],
    session_id: str,
    payload: dict[str, Any],
    ref: RecordRef,
) -> None:
    correlation = payload.get("correlation")
    correlation = correlation if isinstance(correlation, dict) else {}
    call = _ensure_call(calls, session_id, correlation)
    message = payload.get("message")
    message = message if isinstance(message, dict) else {}
    tool_call_id = str(message.get("tool_call_id") or "")
    step_id = str(payload.get("step_id") or "")
    key = _tool_key(step_id, tool_call_id)
    tool = call["tool_by_id"].get(key)
    if tool is None:
        tool = {
            "id": key,
            "name": str(payload.get("name") or message.get("name") or ""),
            "arguments": None,
            "arguments_raw": "",
            "preview": "",
            "status": "settled",
            "is_error": False,
            "blocked": False,
            "rendered": "",
            "message": None,
            "shell_execution": None,
            "raw_output_artifact": "",
            "intent": None,
            "settled": None,
        }
        call["tool_by_id"][key] = tool
        call["tools"].append(tool)
    tool["settled"] = ref
    tool["is_error"] = bool(payload.get("is_error"))
    tool["blocked"] = bool(payload.get("blocked"))
    tool["rendered"] = str(payload.get("rendered") or "")
    tool["message"] = message
    tool["shell_execution"] = payload.get("shell_execution")
    tool["raw_output_artifact"] = str(payload.get("raw_output_artifact") or "")
    tool["status"] = "error" if tool["is_error"] or tool["blocked"] else "ok"
    if not tool["name"]:
        tool["name"] = str(message.get("name") or "")


def scan_sessions(
    operations_dir: str | Path,
    session_ids: list[str] | tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Scan session operation files and return call summaries plus file facts."""
    directory = Path(operations_dir)
    calls: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    for session_id in session_ids:
        path = directory / f"{session_id}.jsonl"
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            files[session_id] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except OSError:
            continue
        try:
            with path.open("rb") as stream:
                offset = 0
                for raw in stream:
                    length = len(raw)
                    if not raw.endswith(b"\n"):
                        offset += length
                        continue
                    kind = _record_kind(raw)
                    if kind is None:
                        offset += length
                        continue
                    try:
                        record = json.loads(raw)
                    except (ValueError, TypeError):
                        warnings.append(
                            f"{session_id}: malformed record at byte {offset}"
                        )
                        offset += length
                        continue
                    if not isinstance(record, dict):
                        offset += length
                        continue
                    ref = RecordRef(path, offset, length, session_id)
                    payload = record.get("payload")
                    payload = payload if isinstance(payload, dict) else {}
                    correlation = payload.get("correlation")
                    correlation = correlation if isinstance(correlation, dict) else {}
                    attempt = int(record.get("attempt") or 1)
                    if kind == "llm":
                        call = _ensure_call(calls, session_id, correlation)
                        phase = record.get("phase")
                        if phase == "intent":
                            _apply_intent(call, payload, attempt, ref)
                        elif phase == "settled":
                            _apply_settled(call, payload, attempt, ref)
                    elif kind == "tool":
                        phase = record.get("phase")
                        if phase == "intent":
                            _apply_tool_intent(calls, session_id, payload, ref)
                        elif phase == "settled":
                            _apply_tool_settled(calls, session_id, payload, ref)
                    offset += length
        except OSError as exc:
            warnings.append(f"{session_id}: {exc}")
    return calls, {"files": files, "warnings": warnings}


def tool_raw_candidates(
    log_dir: Path,
    stage_id: str,
    raw_id: str,
    raw_output_artifact: str = "",
) -> list[Path]:
    """Return plausible raw tool output files for one settled tool call."""
    candidates: list[Path] = []
    if raw_output_artifact:
        path = Path(raw_output_artifact)
        if not path.is_absolute():
            path = log_dir / path
        candidates.append(path)
    safe_id = Path(raw_id).name
    if safe_id:
        if stage_id:
            candidates.append(
                log_dir / "traj_interaction" / "tool_outputs" / f"{stage_id}_{safe_id}"
            )
        candidates.append(log_dir / "interaction" / "tool_outputs" / safe_id)
        candidates.append(log_dir / "traj_interaction" / "tool_outputs" / safe_id)
        candidates.append(log_dir / "tool_outputs" / safe_id)
    return candidates
