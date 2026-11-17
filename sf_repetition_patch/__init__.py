"""ScienceFlow repetition-guard patch: zero-modification runtime overlay.

Detects consecutive identical tool calls, asks a clean-context LLM judge
whether they indicate capability degradation, and — when they do — reuses
ScienceFlow's pending_estra machinery to compact context and restart the
lineage.  No file under ``scienceflow/`` is modified.

Activate with ``import sf_repetition_patch`` (auto-installs) or call
``sf_repetition_patch.install()`` explicitly.  See README for deployment.
"""

from __future__ import annotations

from .hook import install

__version__ = "0.1.0"
__all__ = ["install"]

install()  # auto-activate on import (idempotent; used by the .pth line)