"""Locate agent runtime audit directories under a ScienceFlow task root."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_WORKER_DIR = re.compile(r"^[wW](\d+)$")


@dataclass(frozen=True)
class WorkerAudit:
    """One worker (or control actor) whose provider calls were audited."""

    worker_id: str
    log_dir: Path
    audit_dir: Path

    @property
    def provider_calls(self) -> Path:
        return self.audit_dir / "agent_provider_calls.jsonl"

    @property
    def events_path(self) -> Path:
        return self.audit_dir / "agent_runtime_events.jsonl"

    @property
    def operations_dir(self) -> Path:
        return self.audit_dir / "agent_runtime_operations"

    @property
    def lhr_events_path(self) -> Path:
        return self.log_dir / "lhr_events.jsonl"

    @property
    def has_data(self) -> bool:
        return self.provider_calls.is_file() or self.operations_dir.is_dir()


def _worker_id(audit_dir: Path) -> str:
    for part in reversed(audit_dir.parts):
        match = _WORKER_DIR.match(part)
        if match:
            return f"W{int(match.group(1)):02d}"
    parent = audit_dir.parent.name
    if parent and parent not in {"logs", "task_logs"}:
        return parent
    return "W00"


def _candidate_audit_dirs(task_root: Path) -> list[Path]:
    root = Path(task_root)
    candidates: list[Path] = []
    workers_dir = root / "workers"
    if workers_dir.is_dir():
        for worker in sorted(workers_dir.iterdir()):
            if not worker.is_dir():
                continue
            candidates.append(worker / "logs" / "agent_runtime_audit")
            candidates.append(worker / "workspace" / ".logs" / "agent_runtime_audit")
    for name in ("task_logs", "logs", ".logs"):
        candidates.append(root / name / "agent_runtime_audit")
    resource_dir = root / "resource" / "agent_runtime_audit"
    if resource_dir.is_dir():
        for actor in sorted(resource_dir.iterdir()):
            if actor.is_dir():
                candidates.append(actor)
    return candidates


def discover_workers(task_root: str | Path) -> list[WorkerAudit]:
    """Return every audited worker under *task_root*, deduplicated by path."""
    root = Path(task_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        return []
    seen: set[Path] = set()
    workers: list[WorkerAudit] = []
    for audit_dir in _candidate_audit_dirs(root):
        try:
            resolved = audit_dir.resolve(strict=False)
        except OSError:
            continue
        if resolved in seen or not audit_dir.is_dir():
            continue
        log_dir = audit_dir.parent
        worker = WorkerAudit(
            worker_id=_worker_id(audit_dir),
            log_dir=log_dir,
            audit_dir=audit_dir,
        )
        if not worker.has_data:
            continue
        seen.add(resolved)
        workers.append(worker)
    workers.sort(key=lambda item: item.worker_id)
    return workers
