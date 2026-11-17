# sf_repetition_patch

Zero-modification ScienceFlow overlay: detects consecutive identical tool
calls, asks a clean-context LLM judge whether they indicate capability
degradation, and — when they do — reuses ScienceFlow's existing
`pending_estra` machinery to compact context and restart the lineage.

No file under `scienceflow/` is touched; everything is runtime patching.

## Install

The patch activates with a single import.  Pick one:

**Option A — `.pth` file (recommended, no sitecustomize edit):**

```sh
cp -r sf_repetition_patch "$(python -c 'import site; print(site.getsitepackages()[0])')/"
echo 'import sf_repetition_patch' > "$(python -c 'import site; print(site.getsitepackages()[0])')/sf_repetition_patch.pth"
```

**Option B — sitecustomize:** append to your existing `sitecustomize.py`:

```python
import sf_repetition_patch  # auto-installs on import
```

(requires the package directory to be importable, e.g. via PYTHONPATH or a
copy into site-packages).

**Option C — explicit:** call `sf_repetition_patch.install()` before
`scienceflow` is first imported.

All forms are idempotent.  If ScienceFlow was already imported when the
patch installs, already-loaded target modules are patched late and stale
`from ... import` bindings are rebound automatically.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SF_REPEAT_GUARD_THRESHOLD` | `3` | Consecutive identical calls N that trips the detector. `<=0` disables. |
| `SF_REPEAT_GUARD_JUDGE_ENABLED` | `true` | `false` = observability only: detect and log, never act. |
| `SF_REPEAT_GUARD_JUDGE_TIMEOUT_SEC` | `60` | LLM judge timeout. |
| `SF_REPEAT_GUARD_COOLDOWN_SEC` | `120` | Cooldown after the judge fails or says "not degraded". |
| `SF_REPEAT_GUARD_MAX_RESTARTS` | `3` | Restart budget; exhausted -> worker terminates, best stage kept. |

## Behaviour

1. **Detect** (sync, free): a guard counts consecutive identical
   `(tool, args)` signatures — any different call resets the count.
2. **Judge** (async, clean context): at threshold, a fact card — recent
   outputs, streak, workspace-sha evidence — goes to an isolated LLM call
   with no conversation history.  A judge failure or "not degraded" arms the
   cooldown and the session continues seamlessly.
3. **Restart** (degraded): with stages present, sets `pending_estra`
   (`keep_current`) carrying the judge diagnosis; ScienceFlow's stock worker
   loop then compacts memory, starts a new lineage, and rebuilds the agent.
   Without stages, falls back to an in-session `compact()` plus an injected
   diagnosis message.  Past the restart budget, the worker terminates while
   `_result` keeps the best stage.

Events are written to `lhr_context_events.jsonl` (`repetition_guard_judged`,
`repetition_guard_restarted`, `repetition_guard_restart_budget_exhausted`, ...).

## Tests

```sh
python -m pytest tests/test_repetition_patch_guard.py \
                 tests/test_repetition_patch_judge.py \
                 tests/test_repetition_patch_control.py \
                 tests/test_repetition_patch_install.py -q
```
