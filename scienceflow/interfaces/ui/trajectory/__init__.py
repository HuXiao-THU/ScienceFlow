"""Read-only projection of ScienceFlow agent runtime audit logs.

The trajectory viewer is intentionally independent from the live research
process: it discovers durable audit files under a task root, builds compact
indexes from them, and reads full LLM payloads only when a call is selected.
"""

from .store import TrajectoryStore

__all__ = ["TrajectoryStore"]
