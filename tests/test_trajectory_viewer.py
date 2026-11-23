"""Trajectory index, call summaries and lazy payload reads."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from scienceflow.interfaces.ui.trajectory.discovery import discover_workers
from scienceflow.interfaces.ui.trajectory.store import TrajectoryStore


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _correlation(session: str, turn: int, stage: str = "S01", lineage: str = "L01"):
    return {
        "run_id": "demo-run",
        "worker_id": "W00",
        "session_id": session,
        "stage_id": stage,
        "lineage_id": lineage,
        "node_uid": f"W00:{lineage}:{stage}",
        "turn_id": turn,
        "call_id": f"{session}:llm:{turn}",
    }


def _llm(
    session,
    turn,
    *,
    attempt=1,
    phase="intent",
    messages=None,
    stage="S01",
    lineage="L01",
):
    correlation = _correlation(session, turn, stage, lineage)
    payload = {
        "model": "deepseek-flash",
        "system_messages": [{"role": "system", "content": "system prompt"}],
        "provider_messages": messages
        if messages is not None
        else [{"role": "user", "content": "task prompt"}],
        "call_kind": "agent",
        "turn_kind": "tool" if turn == 1 else "qa",
        "route": None,
        "correlation": correlation,
    }
    if phase == "settled":
        payload = {
            "finish_reason": "tool_calls" if turn == 1 else "stop",
            "correlation": correlation,
            "message": (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": '{"command": "echo hi"}',
                            },
                        }
                    ],
                }
                if turn == 1
                else {"role": "assistant", "content": "final answer"}
            ),
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    return {
        "schema_version": "2.0",
        "sequence": turn * 10 + (0 if phase == "intent" else 1),
        "timestamp": "2026-01-01T00:00:00.000Z",
        "operation_id": "demo-run",
        "kind": "llm",
        "phase": phase,
        "step_id": f"llm:{turn}",
        "attempt": attempt,
        "payload": payload,
    }


def _tool(session, turn, phase):
    correlation = _correlation(session, turn)
    payload = {"name": "bash", "correlation": correlation}
    if phase == "settled":
        payload.update(
            {
                "is_error": False,
                "blocked": False,
                "rendered": "hi",
                "raw_output_artifact": None,
                "shell_execution": None,
                "message": {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "[exit=0] hi [tool-output raw_id=tool_000001_bash.txt]",
                },
                "correlation": correlation,
            }
        )
    return {
        "schema_version": "2.0",
        "sequence": turn * 10 + (2 if phase == "intent" else 3),
        "timestamp": "2026-01-01T00:00:00.000Z",
        "operation_id": "demo-run",
        "kind": "tool",
        "phase": phase,
        "step_id": "tool:call_1",
        "attempt": 1,
        "payload": payload,
    }


def _event(event, timestamp, payload, worker="W00"):
    return {
        "event": event,
        "timestamp": timestamp,
        "timestamp_utc": "2026-01-01T00:00:00Z",
        "worker_id": worker,
        "payload": payload,
    }


def _provider_call(
    session,
    turn,
    *,
    attempt=1,
    phase="completed",
    stage="S01",
    lineage="L01",
    kind="agent",
    turn_kind="tool",
    timestamp=100.0,
    elapsed=2.0,
    tool_names=None,
):
    correlation = _correlation(session, turn, stage, lineage)
    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "call_seq": turn,
        "phase": phase,
        "session_id": session,
        "run_id": "demo-run",
        "worker_id": "W00",
        "stage_id": stage,
        "lineage_id": lineage,
        "node_uid": f"W00:{lineage}:{stage}",
        "call_id": f"{session}:llm:{turn}",
        "turn_id": turn,
        "model": "deepseek-flash",
        "attempt": attempt,
        "elapsed_sec": elapsed,
        "ttft_sec": 0.5,
        "tpot_sec": 0.01,
        "tokens_input": 10,
        "tokens_output": 5,
        "tokens_cached": 2,
        "cache_rate": 0.2,
        "usage_status": "known" if phase == "completed" else "unknown",
        "finish_reason": "stop" if phase == "completed" else "unknown",
        "tool_names": tool_names or [],
        "call_kind": kind,
        "turn_kind": turn_kind,
        "stable_prefix_hash": "prefix",
        "provider_context_hash": f"context-{turn}",
        "provider_message_count": 3,
        "compaction_generation": 0,
        "route": None,
        "correlation": correlation,
    }


def _fixture(root: Path) -> None:
    audit = root / "workers/w00/logs/agent_runtime_audit"
    operations = audit / "agent_runtime_operations"
    main = "demo-run-W00-main"
    estra = "demo-run-W00-estra-abcd"
    main_ops = [
        _llm(main, 1, phase="intent"),
        _llm(main, 1, phase="settled"),
        _tool(main, 1, "intent"),
        _tool(main, 1, "settled"),
        _llm(
            main,
            2,
            phase="intent",
            messages=[
                {"role": "user", "content": "task prompt"},
                {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "content": "hi"},
                {"role": "user", "content": "[LNR_STAGE_COMMIT] summarize"},
            ],
        ),
        _llm(
            main,
            2,
            attempt=2,
            phase="intent",
            messages=[
                {"role": "user", "content": "task prompt"},
                {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "content": "hi"},
                {"role": "user", "content": "[LNR_STAGE_COMMIT] summarize"},
            ],
        ),
        _llm(main, 2, attempt=2, phase="settled"),
    ]
    estra_ops = [
        _llm(
            estra,
            1,
            phase="intent",
            messages=[{"role": "user", "content": "choose next stage"}],
            stage="S02",
        ),
        _llm(estra, 1, phase="settled", stage="S02"),
    ]
    _write_jsonl(operations / f"{main}.jsonl", main_ops)
    _write_jsonl(operations / f"{estra}.jsonl", estra_ops)
    _write_jsonl(
        audit / "agent_provider_calls.jsonl",
        [
            _provider_call(main, 1, tool_names=["bash"]),
            _provider_call(
                main, 2, attempt=1, phase="failed", turn_kind="qa", timestamp=102.0
            ),
            _provider_call(
                main, 2, attempt=2, phase="completed", turn_kind="qa", timestamp=103.0
            ),
            _provider_call(
                estra,
                1,
                stage="S02",
                kind="ephemeral",
                turn_kind="estra",
                timestamp=105.0,
            ),
        ],
    )
    _write_jsonl(
        root / "workers/w00/logs/lhr_events.jsonl",
        [
            _event(
                "stage_captured",
                101.0,
                {
                    "stage_id": "S01",
                    "lineage_id": "L01",
                    "node_uid": "W00:L01:S01",
                    "metric_event": {
                        "metric_name": "test_f1",
                        "metric_value": 0.5,
                        "lower_is_better": False,
                    },
                },
            ),
            _event("stage_commit_text_requested", 102.5, {"stage_id": "S01"}),
            _event(
                "stage_commit_text_parse_retry",
                103.5,
                {"stage_id": "S01", "reason": "missing"},
            ),
            _event("stage_commit_ok", 104.0, {"stage_id": "S01"}),
            _event(
                "estra_decision",
                105.0,
                {
                    "action": "switch_stage",
                    "intent": "redirect",
                    "target_stage": "S02",
                    "llm_correlation": _correlation(estra, 1, "S02"),
                },
            ),
            _event(
                "estra_keep_current_compacted",
                106.0,
                {
                    "previous_lineage_id": "L01",
                    "new_lineage_id": "L02",
                    "target_node_uid": "W00:L01:S01",
                    "target_stage": "S01",
                },
            ),
        ],
    )
    _write_jsonl(
        audit / "agent_runtime_events.jsonl",
        [
            {
                "schema_version": "1.0",
                "type": "session.started",
                "timestamp": "2026-01-01T00:00:00Z",
                "session_id": main,
                "payload": {
                    "model": "deepseek-flash",
                    "correlation": _correlation(main, 1),
                },
            },
            {
                "schema_version": "1.0",
                "type": "agent.turn.decision",
                "timestamp": "2026-01-01T00:00:01Z",
                "session_id": main,
                "turn_id": 2,
                "payload": {
                    "route": "stage_commit_retry",
                    "reason": "scienceflow_stage_commit_retry",
                    "action": "continue",
                    "correlation": _correlation(main, 2),
                },
            },
            {
                "schema_version": "1.0",
                "type": "llm.attempt.failed",
                "timestamp": "2026-01-01T00:00:02Z",
                "session_id": main,
                "turn_id": 2,
                "payload": {
                    "attempt": 1,
                    "error_type": "BadRequestError",
                    "error": "bad reasoning replay",
                    "correlation": _correlation(main, 2),
                },
            },
        ],
    )
    raw = root / "workers/w00/logs/traj_interaction/tool_outputs"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "S01_tool_000001_bash.txt").write_text("[exit=0] hi\n", encoding="utf-8")


def test_discovers_workers_and_builds_lineage_index(tmp_path):
    _fixture(tmp_path)
    workers = discover_workers(tmp_path)
    assert [worker.worker_id for worker in workers] == ["W00"]

    store = TrajectoryStore()
    index = store.index(tmp_path)
    assert index["available"] is True
    assert index["totals"] == {
        "workers": 1,
        "calls": 3,
        "attempts": 4,
        "ephemeral": 1,
        "lineages": 1,
    }
    lineage = index["workers"][0]["lineages"][0]
    assert lineage["id"] == "L01"
    assert lineage["stages"] == ["S01"]
    assert lineage["call_count"] == 3
    assert lineage["ephemeral_count"] == 1
    assert lineage["best_metric"]["value"] == 0.5
    assert lineage["transition_out"]["to"] == "L02"
    assert lineage["phase_counts"]["stage_commit"] == 1
    assert lineage["phase_counts"]["estra"] == 1


def test_call_list_tags_stage_commit_and_estra(tmp_path):
    _fixture(tmp_path)
    store = TrajectoryStore()
    store.index(tmp_path)
    calls = store.calls(tmp_path, "W00", "L01")
    assert calls["total"] == 3
    by_turn = {
        call["turn"]: call for call in calls["calls"] if call["call_kind"] == "agent"
    }
    assert by_turn[2]["phase"] == "stage_commit"
    assert by_turn[2]["phase_label"] == "STAGE COMMIT"
    assert by_turn[2]["status"] == "retried"
    assert by_turn[1]["tools"][0]["name"] == "bash"
    estra = next(call for call in calls["calls"] if call["call_kind"] == "ephemeral")
    assert estra["phase"] == "estra"
    assert estra["stage"] == "S02"
    assert any(item["kind"] == "estra" for item in estra["annotations"])
    # The stage commit call sees the newly injected prompt.
    assert by_turn[2]["context"]["new"] == 3


def test_call_detail_reads_full_context_tools_and_retry_error(tmp_path):
    _fixture(tmp_path)
    store = TrajectoryStore()
    store.index(tmp_path)
    calls = store.calls(tmp_path, "W00", "L01")
    first = next(
        call
        for call in calls["calls"]
        if call["call_kind"] == "agent" and call["turn"] == 1
    )
    detail = store.detail(tmp_path, "W00", first["session"], first["call_id"])
    assert detail is not None
    assert (
        detail["attempts"][0]["request"]["provider_messages"][0]["content"]
        == "task prompt"
    )
    assert detail["tools"][0]["rendered"] == "hi"
    assert detail["tools"][0]["raw_output"]["id"] == "tool_000001_bash.txt"
    assert store.tool_raw(tmp_path, "W00", "S01", "tool_000001_bash.txt") is not None

    retry = next(call for call in calls["calls"] if call["status"] == "retried")
    retry_detail = store.detail(tmp_path, "W00", retry["session"], retry["call_id"])
    assert len(retry_detail["attempts"]) == 2
    assert retry_detail["attempts"][0]["status"] == "failed"
    assert retry_detail["attempts"][0]["error"]["error_type"] == "BadRequestError"
    assert retry_detail["attempts"][1]["status"] == "completed"


def test_tool_raw_rejects_unexpected_ids(tmp_path):
    _fixture(tmp_path)
    store = TrajectoryStore()
    store.index(tmp_path)
    assert store.tool_raw(tmp_path, "W00", "S01", "../secret") is None
    assert store.tool_raw(tmp_path, "W00", "S01", "missing.txt") is None
    assert store.tool_raw(tmp_path, "W00", "../S01", "tool_000001_bash.txt") is None


def test_malformed_operation_line_is_skipped(tmp_path):
    _fixture(tmp_path)
    operations = (
        tmp_path / "workers/w00/logs/agent_runtime_audit/agent_runtime_operations"
    )
    with (operations / "demo-run-W00-main.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"kind": "llm", broken\n')
    store = TrajectoryStore()
    index = store.index(tmp_path)
    assert index["available"] is True
    calls = store.calls(tmp_path, "W00", "L01")
    assert calls["total"] == 3
    assert calls["warnings"]


def _http_get(server, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            f"http://127.0.0.1:{server.server_port}/{path}", timeout=5
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_routes_serve_trajectory_index_calls_detail_and_tool(
    tmp_path, monkeypatch
):
    from scienceflow.interfaces.ui.web_monitor.server import MonitorServer

    _fixture(tmp_path)
    server = MonitorServer(("127.0.0.1", 0), tmp_path, "token")
    server.snapshot = {
        "tasks": [{"key": "1", "name": "Demo", "status": "completed"}],
        "updated_at": 1,
    }
    monkeypatch.setattr(server.monitor, "task_root", lambda key: tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _http_get(server, "token/task/1/trajectory")
        assert status == 200
        index = json.loads(body)
        assert index["totals"]["calls"] == 3

        query = urllib.parse.urlencode({"worker": "W00", "lineage": "L01"})
        status, body = _http_get(server, f"token/task/1/trajectory/calls?{query}")
        assert status == 200
        calls = json.loads(body)
        assert calls["total"] == 3
        first = next(call for call in calls["calls"] if call["turn"] == 1)

        query = urllib.parse.urlencode(
            {
                "worker": "W00",
                "session": first["session_id"],
                "call": first["call_id"],
            }
        )
        status, body = _http_get(server, f"token/task/1/trajectory/call?{query}")
        assert status == 200
        detail = json.loads(body)
        assert detail["attempts"][0]["request"]["provider_messages"]

        query = urllib.parse.urlencode(
            {"worker": "W00", "stage": "S01", "raw": "tool_000001_bash.txt"}
        )
        status, body = _http_get(server, f"token/task/1/trajectory/tool?{query}")
        assert status == 200
        assert b"[exit=0] hi" in body

        status, _ = _http_get(server, "token/task/9/trajectory")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
