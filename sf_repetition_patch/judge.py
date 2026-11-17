"""Clean-context LLM judge for repeated tool calls.

The judge sees only a compact fact card — no conversation history.  This avoids
baseline drift (repetitive output in a degraded context reads as normal),
self-justification bias (the model's own recent explanations anchor its
verdict), and the cost of shipping a full dialogue per judge call.
"""

from __future__ import annotations

import json
import re
from typing import Any

REPETITION_JUDGE_BEGIN = "===REPETITION_JUDGE_RESPONSE==="
REPETITION_JUDGE_END = "===/REPETITION_JUDGE_RESPONSE==="

REPETITION_JUDGE_SYSTEM = (
    "You judge whether one science agent is stuck in a degenerate repetition loop.\n"
    "You see ONLY a fact card; there is no conversation history. Judge strictly from the evidence.\n"
    "Criteria:\n"
    "- If the solution/workspace changed since the run started, or the outputs show new\n"
    "  information on each call, identical commands are legitimate re-runs: degraded=false.\n"
    "- If every output is identical, or only replays a known failure, AND the workspace did\n"
    "  not change: degraded=true.\n"
    "- When degraded=true, recommended_focus must be ONE concrete next action naming a file\n"
    "  to inspect, a column to check, or a parameter to change — never a vague instruction.\n"
    "Answer with exactly one response block; no tools, no markdown, no extra text."
)

_BLOCK_RE = re.compile(
    re.escape(REPETITION_JUDGE_BEGIN) + r"\s*(.*?)\s*" + re.escape(REPETITION_JUDGE_END),
    re.IGNORECASE | re.DOTALL,
)
_KEY_RE = re.compile(r"^([A-Za-z_]+)\s*:\s*(.*)$", re.MULTILINE)

_TRUTHY = {"true", "yes", "1"}
_FALSY = {"false", "no", "0"}
_NULLISH = {"", "null", "none", "n/a", "-"}


def build_repetition_judge_prompt(fact_card: dict[str, Any]) -> str:
    """Prompt asking for one judgment block over the fact card."""
    return "\n".join(
        [
            "Decide whether the repeated tool calls below indicate capability degradation.",
            "Fact card (JSON):",
            json.dumps(fact_card, ensure_ascii=True, sort_keys=True, default=str),
            "Return exactly one response block:",
            REPETITION_JUDGE_BEGIN,
            "degraded: true|false",
            "confidence: low|medium|high",
            "reason: <one compact sentence>",
            "recommended_focus: <one concrete next action, or null when degraded=false>",
            REPETITION_JUDGE_END,
        ]
    )


def parse_repetition_judge_response(text: str) -> tuple[dict[str, Any], str, str]:
    """Parse a judge response into ``(verdict, block_text, error)``."""
    raw = str(text or "")
    match = _BLOCK_RE.search(raw)
    if not match:
        return {}, "", "missing_judge_block"
    body = match.group(1).strip()
    block_text = f"{REPETITION_JUDGE_BEGIN}\n{body}\n{REPETITION_JUDGE_END}"

    data: dict[str, str] = {}
    for key, value in _KEY_RE.findall(body):
        if key not in data:
            data[key] = value.strip().strip('"').strip("'")

    error = ""
    degraded_raw = data.get("degraded", "").strip().lower()
    if degraded_raw in _TRUTHY:
        degraded = True
    elif degraded_raw in _FALSY:
        degraded = False
    else:
        degraded = False
        error = "invalid_degraded"

    focus = data.get("recommended_focus", "").strip()
    if focus.lower() in _NULLISH:
        focus = ""
    if degraded and focus and not validate_recommended_focus(focus):
        # A vague focus is worse than none: the fallback constraint is stronger.
        focus = ""
        error = (error + ";" if error else "") + "vague_focus_rejected"

    confidence = data.get("confidence", "").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"

    verdict = {
        "degraded": degraded,
        "confidence": confidence,
        "reason": data.get("reason", "")[:400],
        "recommended_focus": focus,
    }
    return verdict, block_text, error


def validate_recommended_focus(text: str) -> bool:
    """True when *text* is a concrete, executable next action.

    Concrete actions name a target: a path/file/symbol (``.``, ``/``, ``_``,
    ``-``) or an inspectable noun (column/param/flag/seed/...).  "Try a
    different approach" fails; "check dataset/train.csv for an id column" passes.
    """
    s = (text or "").strip()
    if len(s) < 12 or len(s.split()) < 4:
        return False
    return bool(
        re.search(r"[./_\-\"']", s)
        or re.search(
            r"\b(column|columns|param|parameter|flag|seed|epoch|feature|dtype|key)\b",
            s.lower(),
        )
    )


__all__ = [
    "REPETITION_JUDGE_BEGIN",
    "REPETITION_JUDGE_END",
    "REPETITION_JUDGE_SYSTEM",
    "build_repetition_judge_prompt",
    "parse_repetition_judge_response",
    "validate_recommended_focus",
]