# tst/unit/core/lifecycle/

Unit tests for `src/trading/core/lifecycle/`.

## Files

| File | What it tests |
|------|--------------|
| `test_component.py` | `Component` ABC state transitions (CREATED→STARTING→RUNNING→STOPPING→STOPPED) using `SpyComponent`, `FailingSetupComponent`, and `SlowTeardownComponent` test doubles; error propagation; cleanup timing |
| `test_runtime.py` | `Runtime` orchestration: ordered startup (each component's `_setup()` completes before the next begins), graceful shutdown in reverse order |
