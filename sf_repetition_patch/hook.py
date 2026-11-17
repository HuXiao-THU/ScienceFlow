"""Import hook: patch target ScienceFlow modules right after they execute.

``install()`` registers a meta_path finder matched on exact module names.  When
a target module first executes, ``patches.apply`` runs immediately — before
any consumer can bind ``from ... import`` references to the unpatched
attribute.  Modules imported before installation are patched late via the
``sys.modules`` sweep, and stale ``from``-import bindings are rebound.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from collections.abc import Callable
from typing import Any

_installed = False


class _PatchLoader(importlib.abc.Loader):
    """Delegate to the real loader, then run the patch callback."""

    def __init__(self, inner: Any, on_exec: Callable[[Any], None]) -> None:
        self._inner = inner
        self._on_exec = on_exec

    def create_module(self, spec: Any) -> Any:
        return self._inner.create_module(spec)

    def exec_module(self, module: Any) -> None:
        self._inner.exec_module(module)
        try:
            self._on_exec(module)
        except Exception:  # patches.apply already logs; never break an import
            pass


class _PatchFinder(importlib.abc.MetaPathFinder):
    """Exact-name finder that patches target modules at import (and reload)."""

    def __init__(self, targets: dict[str, Callable[[str, Any], None]]) -> None:
        self._targets = targets
        self._done: set[str] = set()

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname not in self._targets:
            return None
        if fullname in self._done and target is None:
            return None  # already intercepted once; never twice for fresh imports
        # ``target`` is non-None only for importlib.reload(): re-arm so the
        # reloaded module is patched again instead of reverting to pristine.
        self._done.add(fullname)
        sys.meta_path.remove(self)  # avoid recursion into this finder
        try:
            spec = importlib.util.find_spec(fullname)
        except (ImportError, ValueError):
            return None
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return None
        if isinstance(spec.loader, _PatchLoader):
            # Reload of an already-patched module: reuse the existing patch
            # loader (its exec_module re-runs the callback exactly once)
            # instead of stacking a second wrapper.
            return spec
        on_exec = self._targets[fullname]
        spec.loader = _PatchLoader(
            spec.loader, lambda m, name=fullname: on_exec(name, m)
        )
        return spec


def install() -> None:
    """Activate the patch.  Idempotent; safe when ScienceFlow is absent."""
    global _installed
    if _installed:
        return
    _installed = True
    from . import patches

    targets = dict.fromkeys(patches.TARGET_MODULES, patches.apply)
    sys.meta_path.insert(0, _PatchFinder(targets))
    # Late activation: patch any target already imported before install().
    for name in patches.TARGET_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            patches.apply(name, module)


__all__ = ["install"]