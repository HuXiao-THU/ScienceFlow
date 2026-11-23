"""Cached, thread-safe access to trajectory indexes and full call payloads."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from .discovery import WorkerAudit, discover_workers
from .events_index import extract_events, read_lhr_events, read_runtime_highlights
from .operation_index import scan_sessions, tool_raw_candidates
from .projection import build_call_detail, build_calls, build_index, build_worker_index
from .provider_index import logical_calls, read_provider_calls

_SAFE_RAW_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_STAGE = re.compile(r"^[A-Za-z0-9_.-]*$")


def _signature(paths: list[Path]) -> tuple[Any, ...]:
    result = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            result.append((str(path), None, None))
        else:
            result.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(result)


class TrajectoryStore:
    """Build small indexes from audit logs and read full payloads on demand."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._index_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
        self._provider_cache: dict[
            str, tuple[tuple[Any, ...], list[dict[str, Any]], str]
        ] = {}
        self._lineage_cache: dict[
            tuple[str, str, str], tuple[tuple[Any, ...], dict[str, Any]]
        ] = {}
        self._highlight_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}

    @staticmethod
    def _root(task_root: str | Path) -> str:
        return str(Path(task_root).expanduser().resolve(strict=False))

    @staticmethod
    def _workers(root: str) -> list[WorkerAudit]:
        return discover_workers(root)

    def index(self, task_root: str | Path) -> dict[str, Any]:
        root = self._root(task_root)
        with self._lock:
            workers = self._workers(root)
            paths: list[Path] = []
            for worker in workers:
                paths.extend(
                    [worker.provider_calls, worker.lhr_events_path, worker.events_path]
                )
            signature = _signature(paths) + tuple(
                (worker.worker_id, str(worker.audit_dir)) for worker in workers
            )
            cached = self._index_cache.get(root)
            if cached and cached[0] == signature:
                return cached[1]
            worker_indexes = []
            warnings: list[str] = []
            for worker in workers:
                provider_rows = read_provider_calls(worker.provider_calls)
                calls = logical_calls(provider_rows)
                if not calls:
                    warnings.append(f"{worker.worker_id}: no provider call records")
                    continue
                events = extract_events(read_lhr_events(worker.lhr_events_path))
                worker_indexes.append(
                    build_worker_index(worker.worker_id, calls, events, {})
                )
                self._provider_cache[f"{root}\0{worker.worker_id}"] = (
                    _signature([worker.provider_calls]),
                    calls,
                    worker.worker_id,
                )
            if not workers:
                warnings.append("no agent runtime audit directories found")
            data = build_index(worker_indexes, task_root=root, warnings=warnings)
            self._index_cache[root] = (signature, data)
            return data

    def _worker(self, root: str, worker_id: str) -> WorkerAudit | None:
        for worker in self._workers(root):
            if worker.worker_id == worker_id:
                return worker
        return None

    def _provider_calls(self, root: str, worker: WorkerAudit) -> list[dict[str, Any]]:
        key = f"{root}\0{worker.worker_id}"
        signature = _signature([worker.provider_calls])
        cached = self._provider_cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        calls = logical_calls(read_provider_calls(worker.provider_calls))
        self._provider_cache[key] = (signature, calls, worker.worker_id)
        return calls

    def _lineage(
        self,
        root: str,
        worker: WorkerAudit,
        lineage_id: str,
    ) -> dict[str, Any] | None:
        index = self.index(root)
        worker_row = next(
            (
                item
                for item in index.get("workers", [])
                if item.get("id") == worker.worker_id
            ),
            None,
        )
        if worker_row is None:
            return None
        lineage_row = next(
            (
                item
                for item in worker_row.get("lineages", [])
                if item.get("id") == lineage_id
            ),
            None,
        )
        if lineage_row is None:
            return None
        session_ids = [
            str(item.get("id") or "") for item in lineage_row.get("sessions") or []
        ]
        operation_paths = [
            worker.operations_dir / f"{session_id}.jsonl" for session_id in session_ids
        ]
        op_signature = _signature(
            operation_paths + [worker.provider_calls, worker.lhr_events_path]
        )
        cache_key = (root, worker.worker_id, lineage_id)
        cached = self._lineage_cache.get(cache_key)
        if cached and cached[0] == op_signature:
            return cached[1]
        calls = self._provider_calls(root, worker)
        provider_calls = [
            call for call in calls if str(call.get("lineage_id") or "") == lineage_id
        ]
        op_calls, op_facts = scan_sessions(worker.operations_dir, session_ids)
        events = extract_events(read_lhr_events(worker.lhr_events_path))
        response = build_calls(lineage_id, provider_calls, op_calls, events, {})
        response["warnings"] = list(op_facts.get("warnings") or ())
        data = {
            "provider_calls": provider_calls,
            "op_calls": op_calls,
            "events": events,
            "response": response,
        }
        self._lineage_cache[cache_key] = (op_signature, data)
        return data

    def calls(
        self, task_root: str | Path, worker_id: str, lineage_id: str
    ) -> dict[str, Any]:
        root = self._root(task_root)
        with self._lock:
            worker = self._worker(root, worker_id)
            if worker is None:
                return {
                    "lineage": lineage_id,
                    "total": 0,
                    "calls": [],
                    "stats": {},
                    "warnings": [f"worker {worker_id} not found"],
                }
            data = self._lineage(root, worker, lineage_id)
            if data is None:
                return {
                    "lineage": lineage_id,
                    "total": 0,
                    "calls": [],
                    "stats": {},
                    "warnings": [f"lineage {lineage_id} not found"],
                }
            return data["response"]

    def detail(
        self,
        task_root: str | Path,
        worker_id: str,
        session_id: str,
        call_id: str,
    ) -> dict[str, Any] | None:
        root = self._root(task_root)
        with self._lock:
            worker = self._worker(root, worker_id)
            if worker is None:
                return None
            calls = self._provider_calls(root, worker)
            provider_call = next(
                (call for call in calls if str(call.get("call_id")) == call_id), None
            )
            if provider_call is None:
                return None
            lineage_id = str(provider_call.get("lineage_id") or "")
            data = self._lineage(root, worker, lineage_id)
            if data is None:
                return None
            call_item = next(
                (
                    item
                    for item in data["response"].get("calls", [])
                    if str(item.get("call_id")) == call_id
                ),
                None,
            )
            if call_item is None:
                return None
            key = f"{root}\0{worker.worker_id}"
            highlight_signature = _signature([worker.events_path])
            cached_highlights = self._highlight_cache.get(key)
            if cached_highlights and cached_highlights[0] == highlight_signature:
                highlights = cached_highlights[1]
            else:
                highlights = read_runtime_highlights(worker.events_path)
                self._highlight_cache[key] = (highlight_signature, highlights)
            detail = build_call_detail(
                call_item,
                data["op_calls"].get(call_id),
                provider_call,
                data["events"],
                highlights,
                log_dir=worker.log_dir,
                stage_id=str(provider_call.get("stage_id") or ""),
            )
            detail["worker"] = worker_id
            detail["session"] = session_id
            return detail

    def tool_raw(
        self,
        task_root: str | Path,
        worker_id: str,
        stage_id: str,
        raw_id: str,
    ) -> Path | None:
        if not _SAFE_RAW_ID.match(str(raw_id or "")):
            return None
        if not _SAFE_STAGE.match(str(stage_id or "")):
            return None
        root = self._root(task_root)
        with self._lock:
            worker = self._worker(root, worker_id)
            if worker is None:
                return None
            for candidate in tool_raw_candidates(
                worker.log_dir, str(stage_id or ""), str(raw_id)
            ):
                try:
                    if candidate.is_file():
                        return candidate.resolve(strict=False)
                except OSError:
                    continue
        return None

    def invalidate(self, task_root: str | Path | None = None) -> None:
        with self._lock:
            if task_root is None:
                self._index_cache.clear()
                self._provider_cache.clear()
                self._lineage_cache.clear()
                self._highlight_cache.clear()
                return
            root = self._root(task_root)
            self._index_cache.pop(root, None)
            for key in list(self._provider_cache):
                if key.startswith(f"{root}\0"):
                    self._provider_cache.pop(key, None)
            for key in list(self._lineage_cache):
                if key[0] == root:
                    self._lineage_cache.pop(key, None)
            for key in list(self._highlight_cache):
                if key.startswith(f"{root}\0"):
                    self._highlight_cache.pop(key, None)
