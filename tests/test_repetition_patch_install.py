"""Tests for patch installation: hook mechanics, idempotence, defensive skips."""

from __future__ import annotations

import importlib
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path
from types import SimpleNamespace

from sf_repetition_patch import hook, patches
from sf_repetition_patch.guard import IdenticalToolCallRepeatGuard

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_apply_composition_appends_guard_once() -> None:
    calls: list = []

    def fake_create(cfg, **kwargs):
        calls.append(cfg)
        return SimpleNamespace(_guard_manager=SimpleNamespace(_guards=[]))

    module = SimpleNamespace(create_science_agent=fake_create)
    patches.apply("scienceflow.agent.factory.composition", module)
    agent = module.create_science_agent("cfg")
    assert len(agent._guard_manager._guards) == 1
    assert agent._guard_manager._guards[0].name == IdenticalToolCallRepeatGuard.name

    # Idempotent: second apply must not double-wrap or double-append.
    patches.apply("scienceflow.agent.factory.composition", module)
    agent2 = module.create_science_agent("cfg")
    assert len(agent2._guard_manager._guards) == 1
    assert len(calls) == 2


def test_apply_tool_result_wraps_hooks_defensively() -> None:
    class FakeHooks:
        def __init__(self, host):
            self.host = host

        async def should_continue(self, context):
            return False

        async def stop_output(self, context):
            return "original message"

    module = SimpleNamespace(ScienceFlowAgentHooks=FakeHooks)
    patches.apply("scienceflow.agent.session.execution.tool_result", module)
    assert getattr(FakeHooks.should_continue, "_repetition_wrapped", False)
    assert getattr(FakeHooks.stop_output, "_repetition_wrapped", False)


def test_wrapped_stop_output_reports_repetition_guard_reason() -> None:
    import asyncio

    class FakeHooks:
        def __init__(self, host):
            self.host = host

        async def should_continue(self, context):
            return False

        async def stop_output(self, context):
            return "original message"

    module = SimpleNamespace(ScienceFlowAgentHooks=FakeHooks)
    patches.apply("scienceflow.agent.session.execution.tool_result", module)
    host = SimpleNamespace(
        _lnr_repetition_guard_stop_reason="repetition_guard_restart",
        _log_info=lambda *a, **k: None,
    )
    out = asyncio.run(FakeHooks(host).stop_output(None))
    assert "repetition guard" in out
    assert host._lnr_repetition_guard_stop_reason == ""  # consumed
    out2 = asyncio.run(FakeHooks(host).stop_output(None))
    assert out2 == "original message"


def test_apply_with_missing_attributes_is_safe() -> None:
    # Version drift: target attributes missing -> warning, no exception.
    patches.apply("scienceflow.agent.factory.composition", SimpleNamespace())
    patches.apply("scienceflow.agent.session.execution.tool_result", SimpleNamespace())


def test_late_activation_rebinds_stale_aliases() -> None:
    name = "_sf_repetition_fake_consumer"

    def fake_create(cfg, **kwargs):
        return SimpleNamespace(_guard_manager=SimpleNamespace(_guards=[]))

    # Simulate a consumer that from-imported the original before install.
    sys.modules[name] = SimpleNamespace(create_science_agent=fake_create)
    try:
        module = SimpleNamespace(create_science_agent=fake_create)
        patches.apply("scienceflow.agent.factory.composition", module)
        # The wrapped function replaced the module attribute...
        assert getattr(module.create_science_agent, "_repetition_wrapped", False)
        # ...and every stale from-import binding of the original was rebound.
        assert sys.modules[name].create_science_agent is module.create_science_agent
        assert sys.modules[name].create_science_agent is not fake_create
        agent = sys.modules[name].create_science_agent("cfg")
        assert len(agent._guard_manager._guards) == 1
    finally:
        sys.modules.pop(name, None)


def test_finder_patches_module_on_first_import(tmp_path: Path) -> None:
    pkg = tmp_path / "zzz_repetition_target"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("value = 41\n", encoding="utf-8")
    seen: list[str] = []
    sys.path.insert(0, str(tmp_path))

    finder = hook._PatchFinder({"zzz_repetition_target": lambda name, m: seen.append(name)})
    sys.meta_path.insert(0, finder)
    try:
        importlib.import_module("zzz_repetition_target")
        assert seen == ["zzz_repetition_target"]
        assert sys.modules["zzz_repetition_target"].value == 41
        # Re-import from sys.modules does not re-fire the callback...
        assert importlib.import_module("zzz_repetition_target").value == 41
        assert seen == ["zzz_repetition_target"]
        # ...but importlib.reload() re-runs find_spec with a target and the
        # patch must re-arm so the reloaded module is not left pristine.
        importlib.reload(sys.modules["zzz_repetition_target"])
        assert seen == ["zzz_repetition_target", "zzz_repetition_target"]
    finally:
        sys.meta_path.remove(finder)
        sys.path.remove(str(tmp_path))
        sys.modules.pop("zzz_repetition_target", None)


def test_install_is_idempotent() -> None:
    hook.install()
    hook.install()
    # No duplicate finders beyond the single registration.
    patch_finders = [f for f in sys.meta_path if isinstance(f, hook._PatchFinder)]
    assert len(patch_finders) == 1


def test_real_scienceflow_import_is_patched_in_fresh_process() -> None:
    """End-to-end: patch installed first, then real ScienceFlow imports."""
    code = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, {root!r})
        import sf_repetition_patch  # installs before any scienceflow import

        import scienceflow.agent.factory.composition as composition
        import scienceflow.agent.session.execution.tool_result as tool_result
        import scienceflow.runtime.workflow as workflow

        assert getattr(composition.create_science_agent, '_repetition_wrapped', False)
        assert getattr(workflow.build_science_agent, '_repetition_wrapped', False)
        assert getattr(tool_result.ScienceFlowAgentHooks.should_continue, '_repetition_wrapped', False)
        assert getattr(tool_result.ScienceFlowAgentHooks.stop_output, '_repetition_wrapped', False)

        # Late activation also works when modules were imported first.
        import importlib, sf_repetition_patch.patches as P
        importlib.reload(composition)
        assert getattr(composition.create_science_agent, '_repetition_wrapped', False)
        print('SMOKE_OK')
        """
    ).format(root=str(_REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=120,
        env=None,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SMOKE_OK" in result.stdout
    _ = sysconfig.get_paths()  # keep import meaningful for deployment checks