"""Environment-variable configuration for the repetition-guard patch.

The patch never touches ScienceFlow's config system (ToolConfig / loader /
yaml), so the same code works against any installed SF version.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_PREFIX = "SF_REPEAT_GUARD_"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(_PREFIX + name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(_PREFIX + name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(_PREFIX + name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RepeatGuardConfig:
    threshold: int = 3
    judge_enabled: bool = True
    judge_timeout_sec: float = 60.0
    cooldown_sec: float = 120.0
    max_restarts: int = 3


def load_config() -> RepeatGuardConfig:
    return RepeatGuardConfig(
        threshold=_env_int("THRESHOLD", 3),
        judge_enabled=_env_bool("JUDGE_ENABLED", True),
        judge_timeout_sec=_env_float("JUDGE_TIMEOUT_SEC", 60.0),
        cooldown_sec=_env_float("COOLDOWN_SEC", 120.0),
        max_restarts=_env_int("MAX_RESTARTS", 3),
    )


__all__ = ["RepeatGuardConfig", "load_config"]