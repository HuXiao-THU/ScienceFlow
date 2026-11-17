"""Pure repetition detector: consecutive identical tool-call signatures.

The guard is deliberately dumb: it counts consecutive identical calls, keeps a
rolling output window, and refreshes a fact card on its own instance once the
streak reaches the threshold.  It never terminates the run (``should_terminate_run``
stays False), never emits coaching, and needs no host callbacks — all policy
lives in ``control.maybe_handle_repetition_trip``, which consumes the fact card
each round through the wrapped ``should_continue`` hook.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

from inquirycraft.tools import ToolGuard

_NOISE_FIELDS = frozenset({"thought"})
_OUTPUT_CLIP_CHARS = 400
_ARGS_CLIP_CHARS = 400


def repeat_signature(tool_name: str, args: dict[str, Any]) -> str:
    """Stable signature for one tool call, ignoring per-round noise fields."""
    clean = {k: v for k, v in (args or {}).items() if k not in _NOISE_FIELDS}
    try:
        payload = json.dumps(clean, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted((str(k), str(v)) for k, v in clean.items()))
    normalized = " ".join(payload.split())
    digest = hashlib.sha1(normalized.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{tool_name or ''!s}:{digest}"


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


class IdenticalToolCallRepeatGuard(ToolGuard):
    """Detect consecutive identical tool calls; refresh a fact card at threshold."""

    name = "identical_tool_call_repeat_guard"

    def __init__(self, *, threshold: int = 3, recent_window: int = 20) -> None:
        self._threshold = max(0, int(threshold))
        self._recent_window = max(1, int(recent_window))
        self.reset()

    @property
    def threshold(self) -> int:
        return self._threshold

    def reset(self) -> None:
        self._last_signature: str | None = None
        self._streak = 0
        self._first_repeat_call_index = 0
        self._total_calls = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=self._recent_window)
        self._distinct_window: list[str] = []
        self.pending_fact_card: dict[str, Any] | None = None

    def on_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> str | None:
        if self._threshold <= 0:
            return None
        args = dict(args or {})
        self._total_calls += 1
        signature = repeat_signature(str(tool_name or ""), args)
        self._recent.append(
            {
                "signature": signature,
                "error": bool(getattr(result, "error", None)),
                "output": _clip(getattr(result, "output", ""), _OUTPUT_CLIP_CHARS),
            }
        )
        self._distinct_window.append(signature)
        if len(self._distinct_window) > self._recent_window:
            self._distinct_window = self._distinct_window[-self._recent_window :]

        if signature == self._last_signature:
            self._streak += 1
        else:
            self._last_signature = signature
            self._streak = 1
            self._first_repeat_call_index = self._total_calls
            self.pending_fact_card = None  # a different call resets the trip

        if self._streak >= self._threshold:
            # Refresh on every further repeat so the controller always sees the
            # latest evidence (and so a new trip can fire after a cooldown).
            self.pending_fact_card = self._build_fact_card(tool_name, args)
        return None

    def _build_fact_card(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        recent = list(self._recent)[-6:]
        distinct = len(set(self._distinct_window))
        clean = {k: v for k, v in (args or {}).items() if k not in _NOISE_FIELDS}
        try:
            normalized_args = json.dumps(clean, sort_keys=True, default=str)
        except (TypeError, ValueError):
            normalized_args = repr(sorted((str(k), str(v)) for k, v in clean.items()))
        return {
            "tool_name": str(tool_name or ""),
            "normalized_args": _clip(normalized_args, _ARGS_CLIP_CHARS),
            "streak": self._streak,
            "total_calls_in_run": self._total_calls,
            "first_repeat_call_index": self._first_repeat_call_index,
            "recent_outputs": recent,
            "distinct_signatures_recent": distinct,
            # Backfilled by the controller while a judge cooldown suppresses
            # judge calls: repeats during cooldown are themselves strong evidence.
            "suppressed_repeat_count": 0,
        }


__all__ = ["IdenticalToolCallRepeatGuard", "repeat_signature"]