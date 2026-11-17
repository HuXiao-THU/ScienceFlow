"""Runtime wrappers bound to ScienceFlow modules by the import hook.

Only two stable boundaries are patched:

* ``composition.create_science_agent`` — append the detector guard to every
  built agent (the guard is callback-free; attachment is three lines).
* ``ScienceFlowAgentHooks.should_continue`` / ``stop_output`` — the controller
  consumes a pending trip before deferring to the original hook behaviour.

Every attribute access is defensive: on ScienceFlow version drift a missing
boundary logs a warning and disables that patch point instead of crashing.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from .config import load_config
from .control import maybe_handle_repetition_trip
from .guard import IdenticalToolCallRepeatGuard

logger = logging.getLogger("scienceflow")

_COMPOSITION = "scienceflow.agent.factory.composition"
_TOOL_RESULT = "scienceflow.agent.session.execution.tool_result"

TARGET_MODULES = (_COMPOSITION, _TOOL_RESULT)

# Attribute names that may hold a from-imported reference to the original
# creator function (late activation must rebind them too).
_CREATOR_ALIASES = ("create_science_agent", "build_science_agent")


def apply(module_name: str, module: Any) -> None:
    """Patch one freshly executed (or already loaded) target module."""
    try:
        if module_name == _COMPOSITION:
            _patch_composition(module)
        elif module_name == _TOOL_RESULT:
            _patch_tool_result(module)
    except Exception:
        logger.warning("[repetition-guard] patch %s failed", module_name, exc_info=True)


def _patch_composition(module: Any) -> None:
    original = getattr(module, "create_science_agent", None)
    if original is None:
        logger.warning("[repetition-guard] create_science_agent missing; guard disabled")
        return
    if getattr(original, "_repetition_wrapped", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        agent = original(*args, **kwargs)
        try:
            _attach_guard(agent)
        except Exception:
            logger.warning("[repetition-guard] guard attach failed", exc_info=True)
        return agent

    wrapped._repetition_wrapped = True
    wrapped.__name__ = getattr(original, "__name__", "create_science_agent")
    module.create_science_agent = wrapped
    _rebind_stale_aliases(original, wrapped)


def _rebind_stale_aliases(original: Any, wrapped: Any) -> None:
    """Late activation only: fix ``from ... import`` bindings made before install."""
    for mod in list(sys.modules.values()):
        for alias in _CREATOR_ALIASES:
            if getattr(mod, alias, None) is original:
                setattr(mod, alias, wrapped)


def _attach_guard(agent: Any) -> None:
    manager = getattr(agent, "_guard_manager", None)
    guards = getattr(manager, "_guards", None)
    if manager is None or guards is None:
        logger.warning("[repetition-guard] agent has no guard manager; skipped")
        return
    if any(getattr(g, "name", "") == IdenticalToolCallRepeatGuard.name for g in guards):
        return  # idempotent
    guards.append(IdenticalToolCallRepeatGuard(threshold=load_config().threshold))


def _patch_tool_result(module: Any) -> None:
    hooks_cls = getattr(module, "ScienceFlowAgentHooks", None)
    if hooks_cls is None:
        logger.warning("[repetition-guard] ScienceFlowAgentHooks missing; controller disabled")
        return

    original_should = getattr(hooks_cls, "should_continue", None)
    if original_should is None:
        logger.warning("[repetition-guard] should_continue missing; controller disabled")
    elif not getattr(original_should, "_repetition_wrapped", False):

        async def should_continue(self: Any, context: Any) -> bool:
            try:
                handled = await maybe_handle_repetition_trip(self.host)
            except Exception:
                logger.warning("[repetition-guard] controller failed; deferring", exc_info=True)
                handled = None
            if handled is not None:
                return bool(handled)
            return await original_should(self, context)

        should_continue._repetition_wrapped = True
        should_continue.__name__ = "should_continue"
        hooks_cls.should_continue = should_continue

    original_stop = getattr(hooks_cls, "stop_output", None)
    if original_stop is None or getattr(original_stop, "_repetition_wrapped", False):
        return

    async def stop_output(self: Any, context: Any) -> Any:
        reason = getattr(self.host, "_lnr_repetition_guard_stop_reason", "")
        if reason:
            self.host._lnr_repetition_guard_stop_reason = ""
            message = (
                "[ScienceAgent] Run paused by repetition guard: compact restart pending."
                if reason == "repetition_guard_restart"
                else "[ScienceAgent] Run stopped by repetition guard: restart budget exhausted."
            )
            try:
                self.host._log_info("[policy] %s", message)
            except Exception:
                pass
            return message
        return await original_stop(self, context)

    stop_output._repetition_wrapped = True
    stop_output.__name__ = "stop_output"
    hooks_cls.stop_output = stop_output


__all__ = ["TARGET_MODULES", "apply"]